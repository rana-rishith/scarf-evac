"""SCARF: Staff-Coupled Acuity-Ranked Flow.

A heuristic planner for hospital ward evacuation. The thing it does that the
published methods do not is treat escorts as a *mobile* resource whose current
position is part of the routing decision, so the walk back for the next
patient is priced into the plan rather than assumed away.

Five phases:

  0  Precompute       hazard deadlines, per-class egress, escort walk tables
  1  Pre-screen       union-find screen then exact reverse reachability
  2  Dispatch         deadline-first groups, staff-coupled earliest arrival
  3  Critical-arc     re-dispatch under penalties on the binding arcs
  4  Re-plan          residual instance when the hazard picture changes

See docs/ALGORITHM.md for the derivation, complexity and the novelty review.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from .ledger import EscortPool, Ledger, arc_deadline, hazard_deadlines
from .model import Arc, Instance, Kind

INF = float("inf")


# --------------------------------------------------------------------------
# Output records: O = (phi, R, A, f, Theta, B)
# --------------------------------------------------------------------------


@dataclass
class Hop:
    arc: Arc
    depart: int
    span: int
    per: int
    arrive: int


@dataclass
class Move:
    """One entry of R, plus its escort assignment from A."""

    group: str
    cls: int
    size: int
    origin: str
    acuity: float
    hops: list[Hop]
    start: int
    arrival: int
    escorts: list[int]

    @property
    def path(self) -> list[str]:
        if not self.hops:
            return [self.origin]
        return [self.origin] + [h.arc.v for h in self.hops]

    @property
    def destination(self) -> str:
        return self.path[-1]


@dataclass
class Plan:
    phi: bool
    theta: int = 0
    moves: list[Move] = field(default_factory=list)
    bottlenecks: list[Arc] = field(default_factory=list)
    flow: dict[tuple[int, int, int], int] = field(default_factory=dict)
    weighted_arrival: float = 0.0
    lower_bound: int = 0
    binding: str = ""          # which obstruction set the lower bound
    claim_mode: bool = True    # were refuge places claimed in reverse order
    heavy_mode: bool = True    # was the dispatch order longest-journey-first
    promoted: tuple[str, ...] = ()   # groups moved to the front of the order
    reason: str = ""
    unplaced: list[str] = field(default_factory=list)
    rounds_used: int = 0

    @property
    def clearance_seconds(self) -> int:
        return self.theta * self._dt

    _dt: int = 30

    def sort_key(self) -> tuple:
        """Fewest people left behind, then minimise Theta, then acuity.

        Stranded patients come first and Theta second on purpose: a plan that
        abandons two patients finishes early, and comparing on Theta alone
        would prefer it to a slower plan that gets everyone out.
        """
        return (len(self.unplaced), self.theta, self.weighted_arrival)


@dataclass
class Group:
    """A patient group g of class k waiting at one vertex."""

    id: str
    cls: int
    size: int
    vertex: str
    acuity: float
    defer_until: int


# --------------------------------------------------------------------------
# Union-find, used for the cheap connectivity screen
# --------------------------------------------------------------------------


class DisjointSet:
    def __init__(self, items) -> None:
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:          # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


# --------------------------------------------------------------------------
# The planner
# --------------------------------------------------------------------------


class Scarf:
    def __init__(self, inst: Instance, max_wait: int | None = None) -> None:
        inst.validate()
        self.inst = inst
        self.max_wait = max_wait
        self.deadlines = hazard_deadlines(inst)
        self.safe = inst.safe_vertices()
        self._trips: dict[int, dict[str, int]] = {}
        self._order: dict[int, dict[str, float]] = {}
        self.groups = self._make_groups()
        self.walk = {g.vertex: self._walk_table(g.vertex) for g in self.groups}

    # -- phase 0: precompute -----------------------------------------------

    def _make_groups(self) -> list[Group]:
        """Split the census into dispatchable groups.

        Escorted patients travel one at a time because each one holds its own
        pair of hands; walkers travel in batches capped by max_group.
        """
        out: list[Group] = []
        for v in self.inst.vertices.values():
            if v.kind in (Kind.REFUGE, Kind.EXIT):
                continue                      # already somewhere safe
            for k in sorted(v.patients, reverse=True):
                remaining = v.patients[k]
                cap = max(1, self.inst.max_group.get(k, 1))
                idx = 0
                while remaining > 0:
                    size = min(cap, remaining)
                    out.append(Group(
                        id=f"{v.id}#k{k}#{idx}", cls=k, size=size,
                        vertex=v.id, acuity=v.acuity,
                        defer_until=v.defer_until,
                    ))
                    remaining -= size
                    idx += 1
        return out

    def _walk_table(self, target: str) -> dict[str, int]:
        """Walking time in slots from every vertex to ``target``.

        Reverse Dijkstra on the arcs an unencumbered adult can use. This is
        what prices an escort's walk back, so it is computed once per pickup
        point rather than per dispatch.
        """
        rev: dict[str, list[Arc]] = {v: [] for v in self.inst.vertices}
        for a in self.inst.arcs:
            if a.fits.get(1):
                rev[a.v].append(a)
        dist: dict[str, float] = {target: 0.0}
        heap: list[tuple[float, str]] = [(0.0, target)]
        seen: set[str] = set()
        while heap:
            d, u = heapq.heappop(heap)
            if u in seen:
                continue
            seen.add(u)
            for a in rev[u]:
                nd = d + a.tau
                if nd < dist.get(a.u, INF):
                    dist[a.u] = nd
                    heapq.heappush(heap, (nd, a.u))
        return {v: self.inst.slots(d) if d > 0 else 0 for v, d in dist.items()}

    # -- phase 1: pre-screen -----------------------------------------------

    def prescreen(self) -> tuple[bool, str, list[str]]:
        """Cheap necessary checks before any routing work is attempted."""
        inst = self.inst
        if inst.census == 0:
            return True, "nothing to move", []
        if not self.safe:
            return False, "no refuge or exit in the graph", []

        # A class-3 patient needs two pairs of hands; one nurse is not enough.
        for g in self.groups:
            need = inst.alpha[g.cls] * g.size
            if need > inst.total_staff:
                return (False,
                        f"class {g.cls} needs {need} escorts, "
                        f"ward has {inst.total_staff} staff", [g.id])

        stranded: list[str] = []
        for k in {g.cls for g in self.groups}:
            usable = [a for a in inst.arcs if a.fits.get(k)]
            # O(m a(n)) screen on the underlying undirected graph: a necessary
            # condition, and it fails fast on the common case of a bed-bound
            # bay whose only egress is a staircase.
            dsu = DisjointSet(inst.vertices)
            for a in usable:
                dsu.union(a.u, a.v)
            roots = {dsu.find(s) for s in self.safe}
            # Exact check: reverse BFS honours arc direction.
            reach = self._reverse_reachable(usable)
            for g in self.groups:
                if g.cls != k:
                    continue
                if dsu.find(g.vertex) not in roots or g.vertex not in reach:
                    stranded.append(g.id)
                elif self.deadlines[g.vertex] <= 0:
                    stranded.append(g.id)
        if stranded:
            return (False,
                    "no class-feasible route to a refuge or exit for "
                    f"{len(stranded)} group(s)", sorted(set(stranded)))
        return True, "", []

    def _reverse_reachable(self, usable: list[Arc]) -> set[str]:
        rev: dict[str, list[str]] = {v: [] for v in self.inst.vertices}
        for a in usable:
            rev[a.v].append(a.u)
        seen = set(self.safe)
        stack = list(self.safe)
        while stack:
            u = stack.pop()
            for w in rev[u]:
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        return seen

    def _hop_floor(self, a: Arc, k: int) -> int:
        """Fewest slots a lone class-k patient can cross this arc in.

        Walking speed is only half of it. A bed is five width units and a
        1100 mm door passes three per slot, so the bed holds that door for two
        slots however fast the porters walk. Taking the larger of the two is
        what makes the bound notice that a ward of beds behind narrow doors is
        span-limited, not speed-limited: in the ICU it turns a claimed journey
        of one slot into the four it really takes.
        """
        units = self.inst.width_units[k]
        span = max(1, math.ceil(units / a.capacity))
        return max(self.inst.slots(a.tau * self.inst.rho[k]), span)

    def _trip_table(self, k: int) -> dict[str, int]:
        """Fewest slots from every vertex to its nearest refuge or exit.

        Multi-source Dijkstra from the safe set over reversed arcs a class-k
        patient fits through, which gives the *forward* journey out. Doing it
        per class and in the correct direction matters: reusing the escort
        walk tables instead would measure the journey backwards, and on a
        one-way corridor that is not the same number.
        """
        rev: dict[str, list[tuple[str, int]]] = {v: [] for v in
                                                 self.inst.vertices}
        for a in self.inst.arcs:
            if a.fits.get(k):
                rev[a.v].append((a.u, self._hop_floor(a, k)))
        dist: dict[str, int] = {s: 0 for s in self.safe}
        heap: list[tuple[int, str]] = [(0, s) for s in self.safe]
        heapq.heapify(heap)
        seen: set[str] = set()
        while heap:
            d, u = heapq.heappop(heap)
            if u in seen:
                continue
            seen.add(u)
            for w, cost in rev[u]:
                nd = d + cost
                if nd < dist.get(w, INF):
                    dist[w] = nd
                    heapq.heappush(heap, (nd, w))
        return dist

    def _trip_to(self, target: str, k: int) -> dict[str, int]:
        """Fewest slots from every vertex to one specific refuge or exit."""
        rev: dict[str, list[tuple[str, int]]] = {v: [] for v in
                                                 self.inst.vertices}
        for a in self.inst.arcs:
            if a.fits.get(k):
                rev[a.v].append((a.u, self._hop_floor(a, k)))
        dist: dict[str, int] = {target: 0}
        heap: list[tuple[int, str]] = [(0, target)]
        seen: set[str] = set()
        while heap:
            d, u = heapq.heappop(heap)
            if u in seen:
                continue
            seen.add(u)
            for w, cost in rev[u]:
                nd = d + cost
                if nd < dist.get(w, INF):
                    dist[w] = nd
                    heapq.heappush(heap, (nd, w))
        return dist

    def _claim_refuges(self, order: list[Group]) -> dict[str, str]:
        """Hand out refuge places in *reverse* dispatch order.

        Dispatching forwards and letting each group take the nearest refuge
        with a place left is first-come-first-served, and it is beatable
        whenever a near refuge is small: the first bed to arrive takes the one
        good place, and a later bed that had no alternative is sent the long
        way. Measured against the exact solver that cost 33% on an instance
        where swapping the destinations is optimal.

        Claiming in reverse fixes it in one pass. The group dispatched last
        has the least slack, so it should be left the shortest journey;
        equivalently the early groups, which have slack to absorb a longer
        trip, take the far refuges. It is the destination-assignment form of
        giving the long jobs the early start times.

        Returns a preferred destination per group. It is a preference, not a
        constraint: if the claimed refuge turns out to be unreachable for that
        group once capacity and the hazard are accounted for, the router falls
        back to a free choice.
        """
        inst = self.inst
        free: dict[str, float] = {}
        for v in inst.vertices.values():
            if v.kind is Kind.EXIT:
                free[v.id] = INF
            elif v.kind is Kind.REFUGE:
                free[v.id] = INF if v.capacity == INF else float(v.capacity)
        if len(free) < 2:
            return {}                    # one destination, nothing to assign

        classes = {g.cls for g in order}
        dist = {(r, k): self._trip_to(r, k) for r in free for k in classes}

        claim: dict[str, str] = {}
        for g in reversed(order):
            options = []
            for r, places in free.items():
                if places < g.size:
                    continue
                reach = dist[(r, g.cls)].get(g.vertex)
                if reach is not None:
                    options.append((reach, r))
            if not options:
                continue
            _, chosen = min(options)
            claim[g.id] = chosen
            if free[chosen] != INF:
                free[chosen] -= g.size
        return claim

    @staticmethod
    def _throughput(arcs) -> int:
        """Width units per slot across a set of arcs, counted once per corridor.

        Both directions of one corridor share its width, so the capacity is
        counted per physical corridor and not per arc. Arcs a given class
        cannot fit through are counted anyway, which overstates the capacity
        and therefore understates the time: safe for a lower bound.
        """
        return sum({a.corridor: a.capacity for a in arcs}.values())

    def lower_bound(self) -> int:
        """An admissible lower bound on Theta, the largest of four obstructions.

        1. **Travel.** The furthest patient still has to make the journey,
           and cannot start before a doctor releases them.
        2. **Escort work.** The escort-slots demanded cannot be supplied
           faster than the roster supplies them: the area bound for rigid jobs
           on parallel machines. Each delivery is counted one way, plus the
           walks back that are unavoidable -- every escort's *last* delivery
           needs no return and at most ``|S|`` deliveries can be somebody's
           last, so at least ``sum alpha - |S|`` of the (escort, delivery)
           pairs are followed by a walk back of at least one slot.
        3. **Bay egress.** A bay empties no faster than its own doorways pass
           people. Six beds behind one 1100 mm door is a queue before it is
           anything else, and this is the term that reads a ward's geometry
           rather than its distances.
        4. **Refuge intake.** Everyone has to get *in* somewhere, and every
           refuge and exit is reached through a known set of doors whose total
           width is fixed.

        Terms 3 and 4 both work the same way: if a set of doorways passes at
        most ``C`` width units per slot and ``D`` units must cross them, they
        are busy for at least ``ceil(D / C)`` slots, so the last crossing
        starts no earlier than ``ceil(D / C) - 1``, and the journey either
        side of it still has to happen.

        Every term understates where it is unsure -- capacities are summed
        without checking fit, spans are rounded down, the earliest crossing is
        assumed to be slot zero. ``bench/test_gap.py`` asserts the result
        never exceeds the true optimum on every instance solved exactly, which
        is how an earlier version charging a return walk for *every* delivery
        was caught claiming 4 slots on a ward that clears in 3.
        """
        return max(self.bound_terms().values(), default=0)

    def bound_terms(self) -> dict[str, int]:
        """The four obstructions of ``lower_bound``, separately.

        Worth having apart from their maximum: whichever one binds is the
        answer to "what is actually limiting this ward", which is a more
        useful thing to tell an estates team than a single number. On the
        built-in wards escort work binds, on door-constrained bays it is bay
        egress, and on the largest generated wards it is refuge intake.
        """
        inst = self.inst
        classes = {g.cls for g in self.groups}
        if not classes:
            return {}
        trip = {}
        for k in classes:
            if k not in self._trips:
                self._trips[k] = self._trip_table(k)
            trip[k] = self._trips[k]

        # -- 1 and 2: travel, and escort work
        travel = 0
        work = 0.0
        pairs = 0
        for g in self.groups:
            reach = trip[g.cls].get(g.vertex)
            if reach is None:
                continue                     # unreachable; phase 1 reports it
            travel = max(travel, reach + g.defer_until)
            escorts = inst.alpha[g.cls] * g.size
            work += escorts * reach
            pairs += escorts
        work += max(0, pairs - inst.total_staff)   # the unavoidable walks back
        escort_bound = math.ceil(work / max(1, inst.total_staff))

        # -- 3: every bay is throttled by its own doorways
        bay_bound = 0
        for v in inst.vertices.values():
            if v.kind in (Kind.REFUGE, Kind.EXIT) or not v.census:
                continue
            demand = sum(n * inst.width_units[k] for k, n in v.patients.items())
            out = self._throughput(inst.out[v.id])
            if not out or not demand:
                continue
            soonest = min((trip[k][v.id] for k in v.patients
                           if k in trip and v.id in trip[k]), default=None)
            if soonest is None:
                continue
            last_out = v.defer_until + math.ceil(demand / out) - 1
            bay_bound = max(bay_bound, last_out + soonest)

        # -- 4: everyone has to get in through the refuge and exit doors
        intake = [a for a in inst.arcs
                  if inst.vertices[a.v].kind in (Kind.REFUGE, Kind.EXIT)]
        intake_bound = 0
        capacity_in = self._throughput(intake)
        if capacity_in and intake:
            demand = sum(
                n * inst.width_units[k]
                for v in inst.vertices.values()
                if v.kind not in (Kind.REFUGE, Kind.EXIT)
                for k, n in v.patients.items())
            # No intake arc fits any class present means nobody can get in at
            # all; phase 1 reports that, so this term just stands aside.
            final_hop = min((self._hop_floor(a, k) for a in intake
                             for k in classes if a.fits.get(k)), default=0)
            if final_hop and demand:
                intake_bound = (math.ceil(demand / capacity_in) - 1
                                + final_hop)

        return {
            "travel": max(0, travel),
            "escort work": max(0, escort_bound),
            "bay egress": max(0, bay_bound),
            "refuge intake": max(0, intake_bound),
        }

    # -- phase 2: dispatch --------------------------------------------------

    def _order_table(self, k: int) -> dict[str, float]:
        """Travel *seconds* from every vertex to its nearest refuge or exit.

        Deliberately not the slot-rounded ``_trip_table``. That one exists to
        be admissible, so it rounds each hop up to a whole slot, and at 30 s
        a bay 24 m away and one 36 m away come out identical. For ranking
        groups that resolution is useless: five wheelchair patients at five
        different distances all tie, and the order falls back to the bay name.
        Seconds separate them, and nothing here needs to be a bound.
        """
        rev: dict[str, list[tuple[str, float]]] = {v: [] for v in
                                                   self.inst.vertices}
        for a in self.inst.arcs:
            if a.fits.get(k):
                rev[a.v].append((a.u, a.tau * self.inst.rho[k]))
        dist: dict[str, float] = {s: 0.0 for s in self.safe}
        heap: list[tuple[float, str]] = [(0.0, s) for s in self.safe]
        heapq.heapify(heap)
        seen: set[str] = set()
        while heap:
            d, u = heapq.heappop(heap)
            if u in seen:
                continue
            seen.add(u)
            for w, cost in rev[u]:
                nd = d + cost
                if nd < dist.get(w, INF):
                    dist[w] = nd
                    heapq.heappush(heap, (nd, w))
        return dist

    def trip_of(self, g: Group) -> float:
        """How long this group's journey takes, in seconds, cached."""
        if g.cls not in self._order:
            self._order[g.cls] = self._order_table(g.cls)
        return self._order[g.cls].get(g.vertex, 0.0)

    def _priority(self, g: Group, heavy_first: bool = True) -> tuple:
        """Deadline first, then sickest, then by journey length.

        Longest-journey-first is the makespan instinct, and the journey length
        is the right measure of it because it already carries both reasons a
        group is slow: a bed crosses an arc at 2.5 times walking pace and
        holds a narrow door for several slots, and a far bay is simply far.
        Ranking on the mobility class alone misses the second half of that.
        It ordered five equally sick wheelchair patients by bay name, nearest
        first, and came out 22% above optimal on a two-nurse ward where the
        furthest patient should have gone while both nurses were still
        standing next to her.

        It is the wrong instinct under a tight deadline. A bed holds a 1.1 m
        bay door for a full minute, and anyone queued behind it in a bay that
        is about to become untenable does not get out at all. Shortest-first
        clears more people. Which one wins depends on the instance, so phase 3
        tries both and keeps the better plan.
        """
        length = self.trip_of(g)
        return (self.deadlines[g.vertex], -g.acuity,
                -length if heavy_first else length, g.cls, g.id)

    def _search(
        self,
        g: Group,
        t_start: int,
        heads: int,
        ledger: Ledger,
        allow_extra: bool,
        penalty: dict[int, int],
        target: str | None = None,
    ) -> tuple[list[Hop], int] | None:
        """Earliest arrival at any refuge or exit, over a lazy time expansion.

        Labels are (vertex, slot) pairs but no time-expanded copy of the graph
        is ever built: the slot is carried on the label and feasibility is
        asked of the sparse ledger as each arc is relaxed. The heap is keyed
        on arrival plus accumulated arc penalties, while the true arrival
        times ride alongside, so phase 3 can steer the search away from the
        binding arcs without corrupting the schedule it reports.

        shortcut: one label is kept per vertex, its earliest feasible arrival.
        That is exact whenever waiting at a vertex is never blocked by kappa,
        and a heuristic otherwise. Keeping the k best labels per vertex would
        close the gap at k times the cost.
        """
        inst = self.inst
        k = g.cls
        units = g.size * inst.width_units[k]
        src = g.vertex

        true_t: dict[str, int] = {src: t_start}
        key_best: dict[str, float] = {src: float(t_start)}
        parent: dict[str, tuple[str, Hop]] = {}
        settled: set[str] = set()
        heap: list[tuple[float, str]] = [(float(t_start), src)]

        while heap:
            key, u = heapq.heappop(heap)
            if u in settled:
                continue
            settled.add(u)
            t = true_t[u]

            node = inst.vertices[u]
            if u != src and node.kind in (Kind.REFUGE, Kind.EXIT):
                if target is not None and u != target:
                    continue        # not the refuge this group was given
                if ledger.refuge_has_room(u, g.size):
                    hops: list[Hop] = []
                    cur = u
                    while cur in parent:
                        prev, hop = parent[cur]
                        hops.append(hop)
                        cur = prev
                    hops.reverse()
                    return hops, t
                continue          # refuge is full, keep looking further out

            for a in inst.out[u]:
                if not a.fits.get(k) or a.v in settled:
                    continue
                if a.extra_escorts and not allow_extra:
                    continue
                hop = self._hop(a, k, u, t, src, units, heads, ledger)
                if hop is None:
                    continue
                arrive = hop.arrive
                nkey = key + (arrive - t) + penalty.get(a.id, 0)
                if nkey < key_best.get(a.v, INF):
                    key_best[a.v] = nkey
                    true_t[a.v] = arrive
                    parent[a.v] = (u, hop)
                    heapq.heappush(heap, (nkey, a.v))
        return None

    def _hop(
        self,
        a: Arc,
        k: int,
        u: str,
        t: int,
        src: str,
        units: int,
        heads: int,
        ledger: Ledger,
    ) -> Hop | None:
        """Earliest feasible crossing of one arc, leaving u no sooner than t.

        Every reason a departure can be refused is handled in one sweep, so a
        group held up by a crowded junction waits for it instead of giving up
        on the arc: corridor width, standing room while waiting on this side,
        standing room on arrival, the horizon, and the hazard front.
        """
        inst = self.inst
        span, per = Ledger.span_of(units, a.capacity)
        dur = max(inst.slots(a.tau * inst.rho[k]), span)
        limit = min(self.deadlines[u], arc_deadline(a, self.deadlines))
        last = min(t + ledger.max_wait, limit - span, inst.horizon - dur,
                   self.deadlines[a.v] - dur - 1)
        for s in range(t, last + 1):
            if not ledger.arc_free(a.corridor, a.capacity, s, span, per):
                continue
            # Waiting at u costs standing room, except in the patients own
            # bay where they are already standing.
            if u != src and not ledger.vertex_free(u, t, s, heads):
                continue
            if not ledger.vertex_free(a.v, s + dur, s + dur, heads):
                continue
            return Hop(a, s, span, per, s + dur)
        return None

    def _compress(
        self,
        hops: list[Hop],
        t_start: int,
        heads: int,
        ledger: Ledger,
    ) -> list[Hop]:
        """Slide every departure as late as it will go, arrival unchanged.

        The search returns the earliest arrival, which leaves a group parked
        at a junction for as long as the corridor ahead of it is busy. A
        junction holds ten people, those parked people hold that standing room
        for the whole wait, and on a large ward they starve everyone behind
        them -- bays next to a refuge end up unable to move at all.

        Sliding each hop as late as it will go pushes the waiting back into
        the patients own bay, where they are already standing and cost nobody
        anything. Nobody arrives any later: the last hop is pinned, and each
        earlier hop is bounded by the departure of the one after it.

        The slide is all or nothing. A hop that cannot move keeps its old
        times while the hop after it has already moved later, which widens the
        wait between them into slots nobody has checked, so the whole revised
        schedule is validated before it is adopted and the search's own
        schedule is kept otherwise.
        """
        sched = [(h.depart, h.arrive) for h in hops]
        latest = sched[-1][1]
        for i in range(len(hops) - 1, -1, -1):
            hop = hops[i]
            a = hop.arc
            depart, arrive = sched[i]
            dur = arrive - depart
            lower = t_start if i == 0 else sched[i - 1][1]
            limit = min(self.deadlines[a.u], arc_deadline(a, self.deadlines))
            hi = min(latest - dur, limit - hop.span,
                     self.deadlines[a.v] - dur - 1)
            chosen = depart
            for cand in range(hi, depart - 1, -1):
                if cand < lower:
                    break
                if not ledger.arc_free(a.corridor, a.capacity, cand,
                                       hop.span, hop.per):
                    continue
                # The whole wait at the far end, not just the arrival instant.
                if not ledger.vertex_free(a.v, cand + dur, latest, heads):
                    continue
                chosen = cand
                break
            sched[i] = (chosen, chosen + dur)
            latest = chosen

        if self._schedule_ok(hops, sched, t_start, heads, ledger):
            for hop, (depart, arrive) in zip(hops, sched):
                hop.depart, hop.arrive = depart, arrive
        return hops

    def _schedule_ok(
        self,
        hops: list[Hop],
        sched: list[tuple[int, int]],
        t_start: int,
        heads: int,
        ledger: Ledger,
    ) -> bool:
        """Re-check a revised schedule against every rule before adopting it."""
        inst = self.inst
        if sched[-1][1] != hops[-1].arrive:
            return False                      # must not delay the arrival
        prev_arrive = t_start
        for i, hop in enumerate(hops):
            a = hop.arc
            depart, arrive = sched[i]
            if depart < prev_arrive:
                return False
            limit = min(self.deadlines[a.u], arc_deadline(a, self.deadlines))
            if depart + hop.span > limit:
                return False
            if arrive >= self.deadlines[a.v] or arrive > inst.horizon:
                return False
            if not ledger.arc_free(a.corridor, a.capacity, depart,
                                   hop.span, hop.per):
                return False
            if i + 1 < len(hops):
                # Standing room at the junction for the whole wait. The wait
                # at a.u is the same window, checked on the previous hop.
                if not ledger.vertex_free(a.v, arrive, sched[i + 1][0], heads):
                    return False
            prev_arrive = arrive
        return True

    def _route(
        self,
        g: Group,
        ledger: Ledger,
        pool: EscortPool,
        penalty: dict[int, int],
        target: str | None = None,
    ) -> Move | None:
        """Source escorts and route one group, jointly.

        Two attempts, because whether to grab an extra pair of hands is a real
        decision on a ward: without them the staircase is shut to a wheelchair
        and the group must go the long way to a refuge, with them the stairs
        open but the pool is thinner for everyone behind.
        """
        inst = self.inst
        k = g.cls
        walk = self.walk[g.vertex]
        deadline = self.deadlines[g.vertex]
        base = inst.alpha[k] * g.size
        t_ready = max(0, g.defer_until)

        attempts: list[Move] = []
        plans = [(base, False)]
        extra = max((a.extra_escorts for a in inst.arcs if a.fits.get(k)),
                    default=0)
        if extra:
            plans.append((base + extra * g.size, True))

        for need, allow_extra in plans:
            got = pool.source(g.vertex, t_ready, need, walk, deadline)
            if got is None:
                continue
            start, who = got
            # The claimed refuge first, then a free choice if it cannot be
            # reached. Never the other way round: the whole point of the
            # claim is to accept a longer trip for this group so that a later
            # one finishes sooner, so a better arrival is not a reason to
            # abandon it.
            found = None
            for attempt in ([target, None] if target else [None]):
                found = self._search(g, start, g.size + need, ledger,
                                     allow_extra, penalty, attempt)
                if found is not None:
                    break
            if found is None:
                continue
            hops, arrival = found
            hops = self._compress(hops, start, g.size + need, ledger)
            attempts.append(Move(
                group=g.id, cls=k, size=g.size, origin=g.vertex,
                acuity=g.acuity, hops=hops, start=max(start, hops[0].depart),
                arrival=arrival, escorts=who,
            ))
        if not attempts:
            return None
        return min(attempts, key=lambda m: (m.arrival, len(m.escorts)))

    def _commit(self, mv: Move, ledger: Ledger, pool: EscortPool) -> None:
        heads = mv.size + len(mv.escorts)
        for i, hop in enumerate(mv.hops):
            ledger.reserve_arc(hop.arc.corridor, hop.depart, hop.span, hop.per)
            if i + 1 < len(mv.hops):
                nxt = mv.hops[i + 1]
                ledger.reserve_vertex(hop.arc.v, hop.arrive, nxt.depart, heads)
        dest = mv.destination
        if dest != mv.origin:
            ledger.fill_refuge(dest, mv.size)
        pool.commit(mv.escorts, dest, mv.arrival)

    def dispatch(
        self,
        penalty: dict[int, int] | None = None,
        heavy_first: bool = True,
        claim_refuges: bool = True,
        promote: tuple[str, ...] = (),
    ) -> Plan:
        """One full pass: route every group in priority order.

        ``promote`` names groups to move to the front of that order, in the
        sequence given. Phase 3 uses it to repair an order no static key
        produces: see ``_repair``.
        """
        inst = self.inst
        penalty = penalty or {}
        ledger = Ledger(inst, max_wait=self.max_wait)
        pool = EscortPool(inst)
        order = sorted(self.groups, key=lambda g: self._priority(g, heavy_first))
        if promote:
            rank = {gid: i for i, gid in enumerate(promote)}
            order.sort(key=lambda g: rank.get(g.id, len(rank)))
        claim = self._claim_refuges(order) if claim_refuges else {}

        moves: list[Move] = []
        failed: list[str] = []
        for g in order:
            mv = self._route(g, ledger, pool, penalty, claim.get(g.id))
            if mv is None:
                failed.append(g.id)
                continue
            self._commit(mv, ledger, pool)
            moves.append(mv)

        plan = Plan(phi=not failed, moves=moves, unplaced=failed,
                    claim_mode=claim_refuges, heavy_mode=heavy_first,
                    promoted=tuple(promote), _dt=inst.dt)
        if failed:
            plan.reason = (f"{len(failed)} group(s) could not be routed "
                           f"within the horizon under the capacity, staffing "
                           f"and hazard rules")
        plan.theta = max((m.arrival for m in moves), default=0)
        plan.weighted_arrival = sum(
            (1.0 + m.acuity) * m.arrival * m.size for m in moves)
        plan.flow = self._flow(moves)
        plan.bottlenecks = self._critical(moves, plan.theta, ledger)
        return plan

    # -- phase 3: critical-arc descent -------------------------------------

    @staticmethod
    def _flow(moves: list[Move]) -> dict[tuple[int, int, int], int]:
        """f(e, k, t): class-k patients entering arc e at slot t."""
        f: dict[tuple[int, int, int], int] = {}
        for m in moves:
            for hop in m.hops:
                key = (hop.arc.id, m.cls, hop.depart)
                f[key] = f.get(key, 0) + m.size
        return f

    def _critical(
        self, moves: list[Move], theta: int, ledger: Ledger
    ) -> list[Arc]:
        """B: arcs saturated during the interval that fixes Theta."""
        if not moves:
            return []
        window = max(1, theta // 10)
        tail = theta - window
        hit: dict[int, Arc] = {}
        for m in moves:
            if m.arrival < tail:
                continue
            for hop in m.hops:
                a = hop.arc
                for t in range(hop.depart, hop.depart + hop.span):
                    load = ledger.corridor.get((a.corridor, t), 0)
                    if load >= a.capacity:
                        hit[a.id] = a
                        break
        return [hit[i] for i in sorted(hit)]

    def _repair(
        self,
        plan: Plan,
        penalty: dict[int, int],
        claim: bool,
    ) -> Plan:
        """Move whoever finished last to the front of the order and retry.

        The group that sets the clearance time is, by definition, the one that
        should have gone first. No static priority key produces that order:
        on a two-nurse ward with five wheelchair patients at five distances,
        both nearest-first and furthest-first clear in 11 slots, and the
        optimum of 9 comes from an order that is neither -- the furthest
        patient first, then the rest in their natural order, so the two
        remaining long trips end up running side by side instead of one nurse
        making the last one alone.

        One promotion per round, accumulating, which is a bounded local search
        on the dispatch order rather than an exhaustive one. Costs one extra
        pass and is kept only if it helps.
        """
        if not plan.moves:
            return plan
        theta = plan.theta
        last = min((m.group for m in plan.moves if m.arrival == theta),
                   default=None)
        if last is None or last in plan.promoted:
            return plan
        trial = self.dispatch(penalty, plan.heavy_mode, claim,
                              plan.promoted + (last,))
        return trial if trial.sort_key() < plan.sort_key() else plan

    def solve(self, rounds: int = 3) -> Plan:
        """Phases 0-3. Returns the best plan found."""
        ok, reason, stranded = self.prescreen()
        terms = self.bound_terms()
        bound = max(terms.values(), default=0)
        binding = max(terms, key=lambda k: terms[k]) if terms else ""
        if not ok:
            return Plan(phi=False, reason=reason, unplaced=stranded,
                        lower_bound=bound, binding=binding,
                        _dt=self.inst.dt)

        # Two things are instance dependent: which dispatch order wins (see
        # _priority) and whether claiming refuge places in reverse helps (see
        # _claim_refuges). The claim mode is the structural choice, so each
        # mode gets its own descent with its own arc penalties, and the better
        # of the two finished plans is returned.
        #
        # Sharing one penalty dict between the modes was tried and dropped.
        # The penalties are updated from whichever variant is winning, so the
        # losing mode never receives the penalties that would have let it
        # improve: on a 960-patient ward that cost 5%.
        best: Plan | None = None
        for claim in (True, False):
            penalty: dict[int, int] = {}
            mode_best: Plan | None = None
            promote: tuple[str, ...] = ()
            for r in range(max(1, rounds)):
                trials = [self.dispatch(penalty, hf, claim, promote)
                          for hf in (True, False)]
                plan = min(trials, key=lambda p: p.sort_key())
                plan = self._repair(plan, penalty, claim)
                promote = plan.promoted
                plan.rounds_used = r + 1
                if mode_best is None or plan.sort_key() < mode_best.sort_key():
                    mode_best = plan
                if plan.phi and plan.theta <= bound:
                    break                    # provably optimal, stop early
                if not plan.bottlenecks:
                    break                    # nothing left to steer away from
                for a in plan.bottlenecks:
                    penalty[a.id] = penalty.get(a.id, 0) + 1
            assert mode_best is not None
            if best is None or mode_best.sort_key() < best.sort_key():
                best = mode_best
            if best.phi and best.theta <= bound:
                break                        # no point trying the other mode
        assert best is not None
        best.lower_bound = bound
        best.binding = binding
        return best

    # -- phase 4: incremental re-plan --------------------------------------

    def replan(self, elapsed: int, hazard) -> Plan:
        """Re-solve for whoever has not yet reached safety.

        Groups whose journey finished before ``elapsed`` are left alone. Every
        other patient is returned to their origin and re-planned against the
        new hazard picture. Only the residual instance is solved, so a fire
        spreading into one wing does not cost a full re-solve of the ward.
        """
        plan = self.solve()
        keep = [m for m in plan.moves if m.arrival <= elapsed]

        import copy
        nxt = copy.deepcopy(self.inst)
        nxt.hazard = list(hazard)
        for v in nxt.vertices.values():
            v.patients = {}
        for m in plan.moves:
            if m.arrival > elapsed:
                node = nxt.vertices[m.origin]
                node.patients[m.cls] = node.patients.get(m.cls, 0) + m.size
        for gid in plan.unplaced:
            origin = gid.split("#")[0]
            cls = int(gid.split("#k")[1].split("#")[0])
            node = nxt.vertices[origin]
            node.patients[cls] = node.patients.get(cls, 0) + 1
        # Escorts are wherever the completed moves left them.
        for v in nxt.vertices.values():
            v.staff = 0
        pool = EscortPool(self.inst)
        for m in keep:
            pool.commit(m.escorts, m.destination, m.arrival)
        for where in pool.loc:
            nxt.vertices[where].staff += 1

        later = Scarf(nxt, max_wait=self.max_wait).solve()
        later.moves = keep + later.moves
        later.theta = max((m.arrival for m in later.moves), default=0)
        return later


def solve(inst: Instance, rounds: int = 3,
          max_wait: int | None = None) -> Plan:
    """Plan an evacuation of ``inst`` with SCARF."""
    return Scarf(inst, max_wait=max_wait).solve(rounds=rounds)
