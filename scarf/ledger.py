"""Reservation bookkeeping: hazard deadlines, capacity ledger, escort pool.

Nothing here materialises the time-expanded graph. Reservations are stored
sparsely, keyed on (place, slot), so memory grows with the number of moves
actually planned rather than with n * |T|.
"""

from __future__ import annotations

import heapq
import math

from .model import Arc, Instance, Kind

INF = float("inf")


# --------------------------------------------------------------------------
# Hazard field -> one deadline per vertex
# --------------------------------------------------------------------------


#: How much each kind of opening slows the hazard front relative to an open
#: corridor. A closed fire door on a compartment line is the whole reason a
#: refuge in the next compartment counts as somewhere safe to put a bed.
RESISTANCE: dict[str, float] = {
    "corridor": 1.0,
    "ramp": 1.0,
    "door": 10.0,
    "stair": 5.0,
    "lift": 20.0,
}


def hazard_deadlines(inst: Instance) -> dict[str, int]:
    """Slot from which each vertex is unusable, i.e. h(v, t) >= theta.

    The hazard is monotone: fire does not un-burn. That lets the whole field
    h : V x T -> [0, 1] collapse to a single scalar per vertex, which is all
    the router needs, and it is what keeps the search out of the time-expanded
    graph. The front crosses an arc in tau(e) * spread_factor * resistance
    seconds and the plan must stay ``safety_margin`` slots clear of it.

    shortcut: monotone spreading hazards only. A hazard that recedes (a leak
    that is contained) needs the full h(v, t) field and a time-indexed check.
    """
    never = inst.horizon + 1
    reach: dict[str, int] = {v: never for v in inst.vertices}
    if not inst.hazard:
        return reach

    # Fire ignores pi(e, k): it passes a 900 mm door a bed cannot, so spread
    # is computed on the undirected graph, resisted only by what it burns
    # through.
    nbr: dict[str, list[tuple[str, float]]] = {v: [] for v in inst.vertices}
    for a in inst.arcs:
        cost = a.tau * RESISTANCE.get(a.kind, 1.0)
        nbr[a.u].append((a.v, cost))
        nbr[a.v].append((a.u, cost))

    # Each source carries its own spread rate along with its front.
    best: dict[str, float] = {}
    heap: list[tuple[float, str, float]] = []
    for h in inst.hazard:
        start = float(h.start)
        if start < best.get(h.vertex, INF):
            best[h.vertex] = start
            heapq.heappush(heap, (start, h.vertex, h.spread_factor))

    settled: set[str] = set()
    while heap:
        t, u, factor = heapq.heappop(heap)
        if u in settled:
            continue
        settled.add(u)
        for w, cost in nbr[u]:
            if w in settled:
                continue
            at = t + (cost * factor) / inst.dt
            if at < best.get(w, INF):
                best[w] = at
                heapq.heappush(heap, (at, w, factor))

    for v, t in best.items():
        lost = int(math.floor(t)) - inst.safety_margin
        reach[v] = max(0, min(never, lost))
    # Refuges sit in an adjacent fire compartment and exits are open ground;
    # they are only lost if the hazard physically reaches them.
    return reach


def arc_deadline(a: Arc, dl: dict[str, int]) -> int:
    return min(dl[a.u], dl[a.v])


# --------------------------------------------------------------------------
# Capacity ledger
# --------------------------------------------------------------------------


class Ledger:
    """Sparse record of what is already reserved on corridors and vertices."""

    def __init__(self, inst: Instance, max_wait: int | None = None) -> None:
        self.inst = inst
        # How long a group may queue for an arc. Capping it bounds the per-arc
        # scan in Scarf._hop, which is the only place |T| enters the running
        # time at all; leaving it at the horizon costs time but never strands
        # anyone who could have waited. On a 240-patient ward the far bays
        # genuinely queue for over twenty minutes.
        self.max_wait = inst.horizon if max_wait is None else max_wait
        self.corridor: dict[tuple[str, int], int] = {}
        self.vertex: dict[tuple[str, int], int] = {}
        self.refuge_fill: dict[str, int] = {}

    # -- arcs ---------------------------------------------------------------

    @staticmethod
    def span_of(units: int, cap: int) -> tuple[int, int]:
        """How long a move of ``units`` blocks an arc of capacity ``cap``.

        A bed is five width units; a 1.1 m door passes two per slot. The bed
        still fits through the door, it just holds the door for three slots.
        That is the trolley-in-the-doorway case from the problem statement.
        """
        span = max(1, math.ceil(units / cap))
        per = math.ceil(units / span)
        return span, per

    def arc_free(self, cid: str, cap: int, s: int, span: int, per: int) -> bool:
        for t in range(s, s + span):
            if self.corridor.get((cid, t), 0) + per > cap:
                return False
        return True

    def reserve_arc(self, cid: str, s: int, span: int, per: int) -> None:
        for t in range(s, s + span):
            key = (cid, t)
            self.corridor[key] = self.corridor.get(key, 0) + per

    # -- vertices -----------------------------------------------------------

    def vertex_free(self, v: str, t0: int, t1: int, heads: int) -> bool:
        """Can ``heads`` people stand at v over the closed interval [t0, t1]?"""
        node = self.inst.vertices[v]
        cap = node.capacity
        if cap == INF:
            return True
        if node.kind in (Kind.REFUGE, Kind.EXIT):
            # A refuge's capacity counts the patients who stay, which
            # refuge_has_room enforces permanently and for good. Escorts hand
            # over and walk back out, so charging them standing room here as
            # well would double-count the patient and make a refuge sized for
            # its intended load unusable by any escorted patient at all: a
            # one-place refuge would reject a bed plus its two porters.
            return True
        for t in range(t0, t1 + 1):
            if self.vertex.get((v, t), 0) + heads > cap:
                return False
        return True

    def reserve_vertex(self, v: str, t0: int, t1: int, heads: int) -> None:
        for t in range(t0, t1 + 1):
            key = (v, t)
            self.vertex[key] = self.vertex.get(key, 0) + heads

    # -- refuges ------------------------------------------------------------

    def refuge_has_room(self, v: str, patients: int) -> bool:
        """Refuge occupants stay put, so their capacity is consumed for good."""
        node = self.inst.vertices[v]
        if node.kind is Kind.EXIT or node.capacity == INF:
            return True
        return self.refuge_fill.get(v, 0) + patients <= node.capacity

    def fill_refuge(self, v: str, patients: int) -> None:
        self.refuge_fill[v] = self.refuge_fill.get(v, 0) + patients


# --------------------------------------------------------------------------
# Escort pool with recirculation
# --------------------------------------------------------------------------


class EscortPool:
    """Staff as a mobile renewable resource.

    This is the part the published models leave out. An escort is not a unit
    of an abstract pool: it is a person standing somewhere. After a hand-over
    at a refuge the escort is at the refuge, and the next patient who needs
    two pairs of hands waits for someone to walk back. The time cost of that
    walk back is what actually sets the clearance time on a staff-poor ward.
    """

    def __init__(self, inst: Instance) -> None:
        self.loc: list[str] = []
        self.free_at: list[int] = []
        for v in inst.vertices.values():
            for _ in range(v.staff):
                self.loc.append(v.id)
                self.free_at.append(0)
        self.total = len(self.loc)

    def source(
        self,
        at: str,
        earliest: int,
        count: int,
        walk: dict[str, int],
        deadline: int,
    ) -> tuple[int, list[int]] | None:
        """Claim ``count`` escorts for a pickup at ``at``.

        ``walk`` gives the walking time in slots from any vertex to ``at`` for
        an unencumbered adult. Escorts are chosen greedily by how soon they
        can be standing at the pickup point, which is optimal for a single
        pickup: any other choice of the same size arrives no earlier.

        shortcut: an escort walking back is not charged against corridor
        capacity. Two nurses walking against a stream of beds is a rounding
        error next to the beds themselves. Upgrade this if a scenario ever
        puts more staff than patients in one corridor.
        """
        if count == 0:
            return max(earliest, 0), []
        if count > self.total:
            return None
        arrivals: list[tuple[int, int]] = []
        for i, where in enumerate(self.loc):
            step = walk.get(where)
            if step is None:
                continue                      # cannot reach the pickup at all
            arrivals.append((self.free_at[i] + step, i))
        if len(arrivals) < count:
            return None
        arrivals.sort()
        chosen = arrivals[:count]
        ready = max(earliest, chosen[-1][0])
        if ready >= deadline:
            # The hands cannot get here before this bay is lost.
            return None
        return ready, [i for _, i in chosen]

    def commit(self, who: list[int], dest: str, when: int) -> None:
        for i in who:
            self.loc[i] = dest
            self.free_at[i] = when
