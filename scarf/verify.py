"""Independent checker for a plan.

This re-derives every constraint from the move list alone, using none of the
solver's bookkeeping. If the solver and this module ever disagree, one of them
is wrong and the test suite says so. It is the reason to trust a heuristic:
the plan it emits is checked against the rules rather than assumed to obey
them.

The five rules from the problem statement, plus the ones a real ward adds:

  1  sum f(e, k, t) <= c(e)          arc flow within capacity
  2  occupancy at v <= kappa(v)      nobody crushed at a junction
  3  conservation                    patients neither created nor lost
  4  h(v, t) < theta and pi(e, k)    nobody in the fire, nothing through a
                                     door it does not fit
  5  staff committed <= staff        no over-commitment
  6  refuge fill <= kappa            a refuge that is full is full
  7  escort continuity               an escort cannot be in two places, and
                                     cannot teleport back for the next pickup
  8  release times                   defer_until respected
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ledger import EscortPool, arc_deadline, hazard_deadlines
from .model import Instance, Kind
from .solver import Plan

INF = float("inf")


@dataclass
class Report:
    ok: bool = True
    errors: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        if self.ok:
            return "plan satisfies every rule"
        return "\n".join(f"  - {e}" for e in self.errors)


def verify(inst: Instance, plan: Plan) -> Report:
    """Check a plan against the instance. Returns a Report, never raises."""
    rep = Report()
    deadlines = hazard_deadlines(inst)
    arcs_by_pair: dict[tuple[str, str], list] = {}
    for a in inst.arcs:
        arcs_by_pair.setdefault((a.u, a.v), []).append(a)

    # -- 3. conservation ---------------------------------------------------
    moved: dict[tuple[str, int], int] = {}
    for m in plan.moves:
        moved[(m.origin, m.cls)] = moved.get((m.origin, m.cls), 0) + m.size
    stranded = len(plan.unplaced)
    for v in inst.vertices.values():
        if v.kind in (Kind.REFUGE, Kind.EXIT):
            continue
        for k, n in v.patients.items():
            got = moved.get((v.id, k), 0)
            if got > n:
                rep.fail(f"rule 3: {got} class-{k} patients moved from "
                         f"{v.id} but only {n} were there")
            if got < n and not stranded:
                rep.fail(f"rule 3: {n - got} class-{k} patients left at "
                         f"{v.id} but the plan claims nobody was stranded")
    if plan.phi and stranded:
        rep.fail("rule 3: phi is TRUE but the plan lists unplaced groups")

    # -- per-move structural checks ---------------------------------------
    corridor_load: dict[tuple[str, int], int] = {}
    vertex_load: dict[tuple[str, int], int] = {}
    refuge_fill: dict[str, int] = {}

    for m in plan.moves:
        heads = m.size + len(m.escorts)
        if not m.hops:
            rep.fail(f"{m.group}: empty route")
            continue
        if m.hops[0].arc.u != m.origin:
            rep.fail(f"{m.group}: route does not start at {m.origin}")
        if m.hops[-1].arrive != m.arrival:
            rep.fail(f"{m.group}: arrival {m.arrival} disagrees with the "
                     f"last hop at {m.hops[-1].arrive}")
        dest = inst.vertices[m.destination]
        if dest.kind not in (Kind.REFUGE, Kind.EXIT):
            rep.fail(f"{m.group}: route ends at {dest.id}, which is neither "
                     f"a refuge nor an exit")

        # -- 8. release time
        origin = inst.vertices[m.origin]
        if m.start < origin.defer_until:
            rep.fail(f"rule 8: {m.group} starts at {m.start} but {m.origin} "
                     f"is held until {origin.defer_until}")

        prev_arrive = m.start
        for i, hop in enumerate(hop for hop in m.hops):
            a = hop.arc

            # the hop is a real arc of G, traversed in the stored direction
            if (a.u, a.v) not in arcs_by_pair:
                rep.fail(f"{m.group}: hop {a.u}->{a.v} is not an arc of G")
            if i > 0 and m.hops[i - 1].arc.v != a.u:
                rep.fail(f"{m.group}: route breaks between "
                         f"{m.hops[i-1].arc.v} and {a.u}")

            # -- 4. pi(e, k)
            if not a.fits.get(m.cls):
                rep.fail(f"rule 4: {m.group} is class {m.cls} and cannot "
                         f"pass {a.u}->{a.v} ({a.kind}, "
                         f"{a.clear_width_mm} mm)")
            if a.kind == "lift":
                rep.fail(f"rule 4: {m.group} uses lift {a.u}->{a.v}; lifts "
                         f"are shut down on alarm")

            # -- timing
            if hop.depart < prev_arrive:
                rep.fail(f"{m.group}: departs {a.u} at {hop.depart} before "
                         f"arriving at {prev_arrive}")
            units = m.size * inst.width_units[m.cls]
            want_span = max(1, -(-units // a.capacity))
            if hop.span < want_span:
                rep.fail(f"{m.group}: {units} width units need {want_span} "
                         f"slots on {a.u}->{a.v} but the plan books "
                         f"{hop.span}")
            want_dur = max(inst.slots(a.tau * inst.rho[m.cls]), hop.span)
            if hop.arrive - hop.depart < want_dur:
                rep.fail(f"{m.group}: crossing {a.u}->{a.v} takes "
                         f"{want_dur} slots, plan allows "
                         f"{hop.arrive - hop.depart}")
            if hop.arrive > inst.horizon:
                rep.fail(f"{m.group}: arrives at {hop.arrive}, past the "
                         f"horizon {inst.horizon}")

            # -- 4. hazard, over the whole traversal and the whole wait
            limit = min(deadlines[a.u], arc_deadline(a, deadlines))
            if hop.depart + hop.span > limit:
                rep.fail(f"rule 4: {m.group} is on {a.u}->{a.v} until "
                         f"{hop.depart + hop.span} but it is lost at {limit}")
            if hop.arrive >= deadlines[a.v]:
                rep.fail(f"rule 4: {m.group} arrives at {a.v} at "
                         f"{hop.arrive} but it is lost at {deadlines[a.v]}")
            if a.u != m.origin and prev_arrive >= deadlines[a.u]:
                rep.fail(f"rule 4: {m.group} waits at {a.u} from "
                         f"{prev_arrive}, lost at {deadlines[a.u]}")

            # -- 1. arc capacity, accumulated for the sweep below
            for t in range(hop.depart, hop.depart + hop.span):
                key = (a.corridor, t)
                corridor_load[key] = corridor_load.get(key, 0) + hop.per

            # -- 2. vertex occupancy while waiting mid-route
            if i + 1 < len(m.hops):
                for t in range(hop.arrive, m.hops[i + 1].depart + 1):
                    key = (a.v, t)
                    vertex_load[key] = vertex_load.get(key, 0) + heads
            prev_arrive = hop.arrive

        refuge_fill[m.destination] = refuge_fill.get(m.destination, 0) + m.size

        # -- 5. escorts
        need = inst.alpha[m.cls] * m.size
        if len(m.escorts) < need:
            rep.fail(f"rule 5: {m.group} is class {m.cls} and needs {need} "
                     f"escorts, plan assigns {len(m.escorts)}")
        extra = max((h.arc.extra_escorts for h in m.hops), default=0)
        if extra and len(m.escorts) < need + extra * m.size:
            rep.fail(f"rule 5: {m.group} uses an arc demanding {extra} extra "
                     f"escort(s) but only {len(m.escorts)} are assigned")

    # -- 1. arc capacity sweep --------------------------------------------
    caps = {a.corridor: a.capacity for a in inst.arcs}
    for (cid, t), load in corridor_load.items():
        cap = caps.get(cid, 0)
        if load > cap:
            rep.fail(f"rule 1: corridor {cid} carries {load} width units at "
                     f"slot {t}, capacity {cap}")

    # -- 2. vertex capacity sweep -----------------------------------------
    for (v, t), load in vertex_load.items():
        cap = inst.vertices[v].capacity
        if cap != INF and load > cap:
            rep.fail(f"rule 2: {load} people at {v} in slot {t}, "
                     f"kappa is {cap}")

    # -- 6. refuge capacity ------------------------------------------------
    for v, n in refuge_fill.items():
        node = inst.vertices[v]
        if node.kind is Kind.REFUGE and node.capacity != INF and n > node.capacity:
            rep.fail(f"rule 6: {n} patients sent to refuge {v}, "
                     f"kappa is {node.capacity}")

    # -- 7. escort continuity ---------------------------------------------
    pool = EscortPool(inst)
    if any(i >= pool.total for m in plan.moves for i in m.escorts):
        rep.fail(f"rule 7: plan names an escort outside the roster of "
                 f"{pool.total}")
    else:
        where = list(pool.loc)
        free = [0] * pool.total
        for m in sorted(plan.moves, key=lambda m: m.start):
            for i in m.escorts:
                walk = _walk_slots(inst, where[i], m.origin)
                if walk is None:
                    rep.fail(f"rule 7: escort {i} cannot walk from "
                             f"{where[i]} to {m.origin} for {m.group}")
                    continue
                if m.start < free[i] + walk:
                    rep.fail(f"rule 7: escort {i} is at {where[i]} free at "
                             f"{free[i]}, needs {walk} slots to reach "
                             f"{m.origin}, but {m.group} starts at {m.start}")
                where[i] = m.destination
                free[i] = m.arrival

    # -- Theta -------------------------------------------------------------
    theta = max((m.arrival for m in plan.moves), default=0)
    if plan.theta != theta:
        rep.fail(f"Theta is reported as {plan.theta} but the last arrival "
                 f"is {theta}")
    return rep


def _walk_slots(inst: Instance, src: str, dst: str) -> int | None:
    """Walking time in slots for an unencumbered adult, recomputed here."""
    import heapq
    if src == dst:
        return 0
    dist: dict[str, float] = {src: 0.0}
    heap: list[tuple[float, str]] = [(0.0, src)]
    seen: set[str] = set()
    while heap:
        d, u = heapq.heappop(heap)
        if u in seen:
            continue
        seen.add(u)
        if u == dst:
            return inst.slots(d)
        for a in inst.out[u]:
            if not a.fits.get(1):
                continue
            nd = d + a.tau
            if nd < dist.get(a.v, INF):
                dist[a.v] = nd
                heapq.heappush(heap, (nd, a.v))
    return None
