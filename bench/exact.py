"""An exact solver, for measuring how far off SCARF actually is.

This is a research tool, not part of the planner. It finds the true minimum
clearance time by iterative deepening: for each target Theta from the lower
bound upwards, ask whether any plan clears everyone by then, and return the
first target that answers yes.

"Any plan" is meant literally. For each group it enumerates every simple route
to a refuge or exit, and for each route every schedule of per-hop departure
slots -- so deliberate waiting part-way along a route is included, which is
the thing a restricted policy would miss. Escort assignment is then solved
exactly over the chosen schedules, with interchangeable staff canonicalised so
the same situation is not explored twice.

It reuses ``Scarf`` for the hazard deadlines, walk tables and group splitting,
so both solvers are judged on identical inputs, and the plan it returns is run
through ``scarf.verify`` like any other. Only tiny instances finish; a node
budget turns an overrun into an honest TIMEOUT rather than a wrong number.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from scarf.ledger import Ledger, arc_deadline
from scarf.model import Arc, Instance, Kind
from scarf.solver import Hop, Move, Plan, Scarf

BIG = 10 ** 9


class Timeout(Exception):
    """The node budget ran out before the search finished."""


@dataclass(frozen=True)
class Cand:
    """One fully timed candidate journey for one group."""

    gi: int
    hops: tuple[tuple[Arc, int, int, int, int], ...]
    need: int
    heads: int

    @property
    def start(self) -> int:
        return self.hops[0][1]

    @property
    def arrival(self) -> int:
        return self.hops[-1][4]

    @property
    def dest(self) -> str:
        return self.hops[-1][0].v


@dataclass
class Result:
    theta: int | None
    plan: Plan | None
    nodes: int
    targets: int


class Exact:
    def __init__(self, inst: Instance, budget: int = 400_000,
                 max_hops: int = 5) -> None:
        self.inst = inst
        self.budget = budget
        self.max_hops = max_hops
        self.scarf = Scarf(inst)
        self.deadlines = self.scarf.deadlines
        self.groups = self.scarf.groups
        self.walk = self.scarf.walk
        self.nodes = 0
        self._paths: dict[tuple[str, int], list[tuple[Arc, ...]]] = {}
        self.initial: list[str] = []
        for v in inst.vertices.values():
            self.initial.extend([v.id] * v.staff)

    # -- route enumeration -------------------------------------------------

    def routes(self, src: str, k: int) -> list[tuple[Arc, ...]]:
        """Every simple route from src to a refuge or exit usable by class k.

        A route stops at the first refuge or exit it reaches, which is what
        SCARF's search does too, so neither solver may pass through one.
        """
        key = (src, k)
        if key in self._paths:
            return self._paths[key]
        found: list[tuple[Arc, ...]] = []

        def walk(u: str, seen: frozenset[str], acc: list[Arc]) -> None:
            if len(acc) >= self.max_hops:
                return
            for a in self.inst.out[u]:
                if not a.fits.get(k) or a.v in seen:
                    continue
                node = self.inst.vertices[a.v]
                if node.kind in (Kind.REFUGE, Kind.EXIT):
                    found.append(tuple(acc + [a]))
                else:
                    walk(a.v, seen | {a.v}, acc + [a])

        walk(src, frozenset({src}), [])
        self._paths[key] = found
        return found

    # -- candidate enumeration ---------------------------------------------

    def earliest_hands(self, origin: str, need: int) -> int:
        """Soonest ``need`` escorts could possibly be standing at origin."""
        if need == 0:
            return 0
        table = self.walk[origin]
        arrivals = sorted(table.get(loc, BIG) for loc in self.initial)
        if len(arrivals) < need or arrivals[need - 1] >= BIG:
            return BIG
        return arrivals[need - 1]

    def candidates(self, gi: int, target: int) -> list[Cand]:
        """Every timed journey for group ``gi`` arriving by ``target``."""
        inst = self.inst
        g = self.groups[gi]
        k = g.cls
        units = g.size * inst.width_units[k]
        out: list[Cand] = []

        for route in self.routes(g.vertex, k):
            extra = max((a.extra_escorts for a in route), default=0)
            need = inst.alpha[k] * g.size + extra * g.size
            if need > len(self.initial):
                continue
            t_min = max(g.defer_until, self.earliest_hands(g.vertex, need))
            if t_min >= BIG:
                continue

            specs = []
            for a in route:
                span, per = Ledger.span_of(units, a.capacity)
                dur = max(inst.slots(a.tau * inst.rho[k]), span)
                specs.append((a, span, per, dur))
            # Durations still to come, so a hop is not tried at a slot that
            # cannot possibly leave room for the rest of the route.
            suffix = [0] * (len(specs) + 1)
            for i in range(len(specs) - 1, -1, -1):
                suffix[i] = suffix[i + 1] + specs[i][3]

            heads = g.size + need
            acc: list[tuple[Arc, int, int, int, int]] = []

            def rec(i: int, t_prev: int) -> None:
                if i == len(specs):
                    out.append(Cand(gi, tuple(acc), need, heads))
                    return
                a, span, per, dur = specs[i]
                limit = min(self.deadlines[a.u],
                            arc_deadline(a, self.deadlines))
                hi = min(limit - span,
                         target - suffix[i],
                         inst.horizon - suffix[i],
                         self.deadlines[a.v] - dur - 1)
                for d in range(t_prev, hi + 1):
                    acc.append((a, d, span, per, d + dur))
                    rec(i + 1, d + dur)
                    acc.pop()

            rec(0, t_min)

        out.sort(key=lambda c: (c.arrival, c.start))
        return out

    # -- ledger placement --------------------------------------------------

    def check(self, cand: Cand, led: Ledger) -> bool:
        """Would this candidate fit, without changing anything?

        Split out from ``commit`` so forward checking can probe an unassigned
        group's whole domain without a commit and rollback for each probe.
        """
        hops = cand.hops
        for i, (a, depart, span, per, arrive) in enumerate(hops):
            if not led.arc_free(a.corridor, a.capacity, depart, span, per):
                return False
            if i + 1 < len(hops):
                if not led.vertex_free(a.v, arrive, hops[i + 1][1],
                                       cand.heads):
                    return False
        return led.refuge_has_room(cand.dest, self.groups[cand.gi].size)

    def commit(self, cand: Cand, led: Ledger) -> None:
        hops = cand.hops
        for i, (a, depart, span, per, arrive) in enumerate(hops):
            led.reserve_arc(a.corridor, depart, span, per)
            if i + 1 < len(hops):
                led.reserve_vertex(a.v, arrive, hops[i + 1][1], cand.heads)
        led.fill_refuge(cand.dest, self.groups[cand.gi].size)

    def place(self, cand: Cand, led: Ledger) -> bool:
        """Reserve a candidate, or leave the ledger untouched and say no."""
        if not self.check(cand, led):
            return False
        self.commit(cand, led)
        return True

    def unplace(self, cand: Cand, led: Ledger) -> None:
        hops = cand.hops
        for i, (a, depart, span, per, arrive) in enumerate(hops):
            for t in range(depart, depart + span):
                led.corridor[(a.corridor, t)] -= per
            if i + 1 < len(hops):
                for t in range(arrive, hops[i + 1][1] + 1):
                    led.vertex[(a.v, t)] -= cand.heads
        led.refuge_fill[cand.dest] -= self.groups[cand.gi].size

    # -- escort feasibility ------------------------------------------------

    def escorts_for(
        self, chosen: list[Cand]
    ) -> dict[int, list[int]] | None:
        """Assign real escorts to the chosen journeys, or report impossible.

        Jobs are taken in order of departure. Staff are interchangeable, so
        the memo key sorts their states: two different ways of reaching the
        same multiset of (where, free from) are the same situation.
        """
        jobs = sorted((c for c in chosen if c.need > 0),
                      key=lambda c: (c.start, c.gi))
        if not jobs:
            return {}
        state0 = tuple((loc, 0) for loc in self.initial)
        dead: set[tuple[int, tuple]] = set()

        def rec(i: int, state: tuple) -> list[tuple[int, ...]] | None:
            if i == len(jobs):
                return []
            key = (i, tuple(sorted(state)))
            if key in dead:
                return None
            job = jobs[i]
            origin = self.groups[job.gi].vertex
            table = self.walk[origin]
            free = [j for j, (loc, when) in enumerate(state)
                    if when + table.get(loc, BIG) <= job.start]
            if len(free) >= job.need:
                for combo in itertools.combinations(free, job.need):
                    nxt = list(state)
                    for j in combo:
                        nxt[j] = (job.dest, job.arrival)
                    rest = rec(i + 1, tuple(nxt))
                    if rest is not None:
                        return [combo] + rest
            dead.add(key)
            return None

        plan = rec(0, state0)
        if plan is None:
            return None
        return {job.gi: list(combo) for job, combo in zip(jobs, plan)}

    # -- the search --------------------------------------------------------

    def _signature(self, gi: int) -> tuple:
        """What makes two groups interchangeable."""
        g = self.groups[gi]
        return (g.vertex, g.cls, g.size, g.acuity, g.defer_until)

    def feasible(self, target: int) -> list[Cand] | None:
        """Is there a plan clearing everyone by ``target``?

        Branch and bound over one candidate journey per group. Three things
        keep it inside the node budget, none of which gives up exactness:

        **Symmetry breaking.** Four bed patients in one bay are
        interchangeable, so assigning them journeys A, B, C, D explores the
        same plan as B, A, C, D -- 24 times over. Groups with the same
        signature are kept in a fixed relative order and the later one is
        required to take a candidate no earlier in the domain than the
        earlier one did. That collapses ``m!`` orderings of ``m`` identical
        groups to one.

        **Forward checking with fail-first ordering.** After each assignment,
        every unassigned group is probed for at least one candidate that still
        fits. A group with none means this branch is already dead, and the
        counts come for free, so the next group to assign is the one with the
        fewest remaining choices -- fail early, where the tree is narrow.

        **Incremental escort pruning.** Escort feasibility used to be checked
        only at the leaf, so escort-starved branches were explored to full
        depth first. Checking it on the groups assigned so far is a valid
        necessary condition, because taking jobs away from a roster that
        cannot cope never makes it cope less.
        """
        doms: list[list[Cand]] = []
        for gi in range(len(self.groups)):
            cands = self.candidates(gi, target)
            if not cands:
                return None
            doms.append(cands)

        rank = [{id(c): i for i, c in enumerate(d)} for d in doms]
        twin: dict[int, int] = {}
        seen: dict[tuple, int] = {}
        for gi in range(len(self.groups)):
            sig = self._signature(gi)
            if sig in seen:
                twin[gi] = seen[sig]
            seen[sig] = gi

        led = Ledger(self.inst)
        chosen: dict[int, Cand] = {}

        def probe(gi: int) -> int:
            """How many of this group's candidates still fit (capped at 2)."""
            floor = -1
            mate = twin.get(gi)
            if mate is not None and mate in chosen:
                floor = rank[mate][id(chosen[mate])]
            count = 0
            for cand in doms[gi]:
                if rank[gi][id(cand)] < floor:
                    continue
                if self.check(cand, led):
                    count += 1
                    if count == 2:
                        break
            return count

        def rec() -> bool:
            self.nodes += 1
            if self.nodes > self.budget:
                raise Timeout
            if len(chosen) == len(doms):
                return self.escorts_for(list(chosen.values())) is not None
            if self.escorts_for(list(chosen.values())) is None:
                return False

            # Fail-first: the eligible group with the fewest live candidates.
            pick, fewest = None, None
            for gi in range(len(doms)):
                if gi in chosen:
                    continue
                mate = twin.get(gi)
                if mate is not None and mate not in chosen:
                    continue            # keep identical groups in order
                live = probe(gi)
                if live == 0:
                    return False        # dead branch, stop here
                if fewest is None or live < fewest:
                    pick, fewest = gi, live
                    if live == 1:
                        break
            if pick is None:
                return False

            floor = -1
            mate = twin.get(pick)
            if mate is not None and mate in chosen:
                floor = rank[mate][id(chosen[mate])]
            for cand in doms[pick]:
                if rank[pick][id(cand)] < floor:
                    continue
                if not self.place(cand, led):
                    continue
                chosen[pick] = cand
                if rec():
                    return True
                del chosen[pick]
                self.unplace(cand, led)
            return False

        return list(chosen.values()) if rec() else None

    def solve(self) -> Result:
        ok, _, _ = self.scarf.prescreen()
        if not ok:
            return Result(None, None, self.nodes, 0)
        lower = self.scarf.lower_bound()
        targets = 0
        for target in range(lower, self.inst.horizon + 1):
            targets += 1
            found = self.feasible(target)
            if found is not None:
                return Result(target, self.build(found), self.nodes, targets)
        return Result(None, None, self.nodes, targets)

    # -- hand back a plan the verifier can check ---------------------------

    def build(self, chosen: list[Cand]) -> Plan:
        escorts = self.escorts_for(chosen) or {}
        moves: list[Move] = []
        for cand in chosen:
            g = self.groups[cand.gi]
            hops = [Hop(a, depart, span, per, arrive)
                    for a, depart, span, per, arrive in cand.hops]
            moves.append(Move(
                group=g.id, cls=g.cls, size=g.size, origin=g.vertex,
                acuity=g.acuity, hops=hops, start=cand.start,
                arrival=cand.arrival, escorts=escorts.get(cand.gi, []),
            ))
        plan = Plan(phi=True, moves=moves, _dt=self.inst.dt)
        plan.theta = max(m.arrival for m in moves)
        plan.weighted_arrival = sum(
            (1.0 + m.acuity) * m.arrival * m.size for m in moves)
        plan.flow = Scarf._flow(moves)
        plan.lower_bound = self.scarf.lower_bound()
        return plan


def solve_exact(inst: Instance, budget: int = 400_000) -> Result:
    return Exact(inst, budget=budget).solve()
