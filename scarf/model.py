"""Domain model for a hospital evacuation instance.

All times are integer *slots*. The slot length ``dt`` defaults to 30 s because
that is the granularity at which the physical constraints in the problem
statement line up: a 2.15 m ward corridor passes about ten walking adults per
minute and about two beds per minute, so one 30 s slot carries five "width
units", a walking adult costs one unit and a bed costs five.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum

# --------------------------------------------------------------------------
# Classes and their physical parameters
# --------------------------------------------------------------------------


class Mobility(IntEnum):
    """K = {1, 2, 3} from the problem statement."""

    AMBULANT = 1
    ASSISTED = 2
    BED = 3


class Kind(IntEnum):
    """The partition V = Vp u Vt u Vr u Vx."""

    OCCUPIED = 0
    TRANSIT = 1
    REFUGE = 2
    EXIT = 3


#: rho(k): a class-k patient crosses an arc in tau(e) * rho(k).
RHO: dict[int, float] = {1: 1.0, 2: 1.6, 3: 2.5}

#: alpha(k): staff held per class-k patient for the whole journey.
ALPHA: dict[int, int] = {1: 0, 2: 1, 3: 2}

#: Width units one class-k patient consumes on an arc. A bed is five walkers.
WIDTH_UNITS: dict[int, int] = {1: 1, 2: 2, 3: 5}

#: Largest group dispatched as one unit. Escorted patients move singly.
MAX_GROUP: dict[int, int] = {1: 8, 2: 1, 3: 1}

#: Minimum clear width in mm for a class to pass at all (pi(e, k)).
MIN_WIDTH_MM: dict[int, int] = {1: 700, 2: 850, 3: 1100}

#: Controlled assisted-movement specific flow, persons per metre per second.
#: Far below the SFPE free-flow figure of about 1.3 p/m/s: ward evacuation is
#: escorted and orderly, and the problem statement numbers imply about 0.08.
FLOW_COEFF = 0.0775


@dataclass
class Vertex:
    """A real place: a bay, a nurses station, a junction, a stair landing."""

    id: str
    kind: Kind
    capacity: float               # kappa(v): people who fit at once
    staff: int = 0                # s(v): on-duty staff standing here
    patients: dict[int, int] = field(default_factory=dict)   # p(v, k)
    acuity: float = 0.0           # 0..1, higher means sicker
    defer_until: int = 0          # slots; nobody here may move before this
    floor: int = 0
    label: str = ""

    @property
    def census(self) -> int:
        return sum(self.patients.values())


@dataclass
class Arc:
    """One passable direction of one corridor, door, ramp or stair."""

    u: str
    v: str
    tau: float                    # seconds, free flow for one ambulant adult
    clear_width_mm: int = 2150
    kind: str = "corridor"        # corridor | door | stair | ramp | lift
    corridor: str | None = None   # shared physical width id, see note below
    extra_escorts: int = 0        # extra hands this arc demands (stairs)
    fits: dict[int, bool] = field(default_factory=dict)      # pi(e, k)
    capacity: int = 0             # c(e): width units per slot
    id: int = -1

    def derive(self, dt: int) -> None:
        """Fill in pi(e, k) and c(e) from the physical width and arc kind.

        Lifts are dead: the alarm shuts them down, so no class may use them.
        Stairs take walkers, take a wheelchair user only in an evacuation chair
        with one extra pair of hands, and never take a bed.
        """
        if self.kind == "lift":
            derived = {1: False, 2: False, 3: False}
        elif self.kind == "stair":
            derived = {1: True, 2: self.clear_width_mm >= 1000, 3: False}
            if derived[2] and self.extra_escorts == 0:
                self.extra_escorts = 1
        else:
            derived = {k: self.clear_width_mm >= w
                       for k, w in MIN_WIDTH_MM.items()}
        # An explicit fits= given by the caller wins over the derivation.
        for k, val in derived.items():
            self.fits.setdefault(k, val)
        if self.capacity == 0:
            width_m = self.clear_width_mm / 1000.0
            self.capacity = max(1, round(width_m * FLOW_COEFF * dt))
        if self.corridor is None:
            # Both directions of one physical corridor share its width, so the
            # capacity ledger must key on the pair, not on the arc. Without
            # this a plan can push 2c patients through a corridor rated c.
            self.corridor = "|".join(sorted((self.u, self.v)))


@dataclass
class HazardSource:
    """A fire or leak that starts at one vertex and spreads along the graph."""

    vertex: str
    start: int = 0                # slot at which it becomes dangerous
    spread_factor: float = 60.0   # front crosses an arc in tau * this
    #: 60 puts the front at walking_speed / 60, about 1.2 m per minute,
    #: which is a growing compartment fire. Lower it for a fast-developing
    #: fire, raise it for a slow smoke migration.


@dataclass
class Instance:
    """I = (G, p, s, tau, rho, pi, c, kappa, alpha, h, theta, T)."""

    vertices: dict[str, Vertex]
    arcs: list[Arc]
    dt: int = 30                              # slot length in seconds
    horizon: int = 120                        # |T|, in slots
    hazard: list[HazardSource] = field(default_factory=list)
    safety_margin: int = 1                    # slots to clear ahead of front
    rho: dict[int, float] = field(default_factory=lambda: dict(RHO))
    alpha: dict[int, int] = field(default_factory=lambda: dict(ALPHA))
    width_units: dict[int, int] = field(
        default_factory=lambda: dict(WIDTH_UNITS))
    max_group: dict[int, int] = field(
        default_factory=lambda: dict(MAX_GROUP))
    name: str = "instance"

    def __post_init__(self) -> None:
        self.out: dict[str, list[Arc]] = {v: [] for v in self.vertices}
        for i, a in enumerate(self.arcs):
            a.id = i
            a.derive(self.dt)
            if a.u in self.out:
                self.out[a.u].append(a)

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        """Reject malformed instances at the trust boundary."""
        if self.dt <= 0:
            raise ValueError("dt must be positive")
        if self.horizon <= 0:
            raise ValueError("horizon must be positive")
        for a in self.arcs:
            if a.u not in self.vertices or a.v not in self.vertices:
                raise ValueError(
                    f"arc {a.u}->{a.v} references an unknown vertex")
            if a.tau <= 0:
                raise ValueError(f"arc {a.u}->{a.v} has non-positive tau")
            if a.capacity < 1:
                raise ValueError(f"arc {a.u}->{a.v} has zero capacity")
        for v in self.vertices.values():
            if v.capacity < 0 or v.staff < 0 or v.defer_until < 0:
                raise ValueError(f"vertex {v.id} has a negative quantity")
            for k, n in v.patients.items():
                if k not in (1, 2, 3):
                    raise ValueError(
                        f"vertex {v.id} has unknown mobility class {k}")
                if n < 0:
                    raise ValueError(f"vertex {v.id} has a negative census")
            if v.census and v.kind is Kind.TRANSIT:
                raise ValueError(
                    f"vertex {v.id} holds patients but is a transit vertex")
        for h in self.hazard:
            if h.vertex not in self.vertices:
                raise ValueError(f"hazard at unknown vertex {h.vertex}")

    # -- derived quantities -------------------------------------------------

    @property
    def census(self) -> int:
        """P, the total number of patients needing to move."""
        return sum(
            v.census for v in self.vertices.values()
            if v.kind not in (Kind.REFUGE, Kind.EXIT)
        )

    @property
    def total_staff(self) -> int:
        return sum(v.staff for v in self.vertices.values())

    def slots(self, seconds: float) -> int:
        """Round a duration in seconds up to whole slots, never below one."""
        return max(1, math.ceil(seconds / self.dt))

    def safe_vertices(self) -> set[str]:
        return {v.id for v in self.vertices.values()
                if v.kind in (Kind.REFUGE, Kind.EXIT)}
