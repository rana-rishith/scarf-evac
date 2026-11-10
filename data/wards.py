"""Test wards.

Provenance, stated plainly. There is no openly licensed hospital dataset that
carries indoor corridor topology *and* clear widths *and* an egress ground
truth. FloorPlanCAD contains hospital drawings but is CC-BY-NC and its
maintainers do not hold copyright in the drawings; OpenStreetMap has building
footprints but almost no indoor detail. So these wards are hand-encoded: the
topology follows the published ward layouts these designs are built from (a
racetrack acute medical ward, an open ICU, a two-storey block), and every
dimension is a published design figure rather than an invention:

  corridor clear width   2150 mm   bed-moving hospital corridor
  bay / side-room door   1100 mm   minimum leaf for bed access
  fire door, double leaf 1800 mm   compartment line
  stair clear width      1200 mm   escape stair
  walking speed          1.2 m/s   horizontal, unaided adult

``generate_ward`` builds arbitrarily large wards from the same vocabulary for
the scaling tests. Load a real plan instead with ``scarf.scenario.load``.
"""

from __future__ import annotations

import random

from scarf.model import Arc, HazardSource, Instance, Kind, Vertex

INF = float("inf")

CORRIDOR = 2150
BED_DOOR = 1100
FIRE_DOOR = 1800
STAIR = 1200
WALK = 1.2                                  # m/s


def _tau(metres: float) -> float:
    return round(metres / WALK, 1)


def _link(arcs: list[Arc], u: str, v: str, metres: float,
          width: int = CORRIDOR, kind: str = "corridor") -> None:
    """Add both directions of one physical opening.

    Both arcs inherit the same ``corridor`` id, so the ledger charges them
    against one shared width. Storing the directions separately is what lets
    the hazard block one side while the other stays open.
    """
    cid = f"{min(u, v)}|{max(u, v)}"
    for a, b in ((u, v), (v, u)):
        arcs.append(Arc(u=a, v=b, tau=_tau(metres), clear_width_mm=width,
                        kind=kind, corridor=cid))


# --------------------------------------------------------------------------
# Ward A: 28-bed acute medical ward, racetrack corridor, night staffing
# --------------------------------------------------------------------------


def ward_medical_28() -> Instance:
    v: dict[str, Vertex] = {}
    arcs: list[Arc] = []

    def add(node: Vertex) -> None:
        v[node.id] = node

    # Corridor spine, five junctions about 14 m apart.
    for i in range(1, 6):
        add(Vertex(id=f"c{i}", kind=Kind.TRANSIT, capacity=10, floor=2,
                   label=f"Corridor junction {i}"))
    for i in range(1, 5):
        _link(arcs, f"c{i}", f"c{i+1}", 14)

    # Four six-bed bays off the spine.
    bays = [
        ("b1", "c2", {1: 3, 2: 2, 3: 1}, 0.45),
        ("b2", "c2", {1: 2, 2: 3, 3: 1}, 0.50),
        ("b3", "c4", {1: 2, 2: 2, 3: 2}, 0.60),
        ("b4", "c4", {1: 1, 2: 3, 3: 2}, 0.65),
    ]
    for bid, junction, census, acuity in bays:
        add(Vertex(id=bid, kind=Kind.OCCUPIED, capacity=12, staff=0,
                   patients=dict(census), acuity=acuity, floor=2,
                   label=f"Six-bed bay {bid[-1]}"))
        _link(arcs, bid, junction, 2.5, BED_DOOR, "door")

    # Four single side rooms, the sickest patients on the ward.
    side = [
        ("sr1", "c1", {3: 1}, 0.90),
        ("sr2", "c3", {3: 1}, 0.95),
        ("sr3", "c3", {2: 1}, 0.70),
        ("sr4", "c5", {3: 1}, 0.85),
    ]
    for sid, junction, census, acuity in side:
        add(Vertex(id=sid, kind=Kind.OCCUPIED, capacity=4, staff=0,
                   patients=dict(census), acuity=acuity, floor=2,
                   label=f"Side room {sid[-1]}"))
        _link(arcs, sid, junction, 2.5, BED_DOOR, "door")

    # Nurses station: where the staff are standing when the alarm goes.
    add(Vertex(id="ns", kind=Kind.TRANSIT, capacity=8, staff=5, floor=2,
               label="Nurses station"))
    _link(arcs, "ns", "c3", 5)

    # Lift lobby. The lift is dead from the moment the alarm sounds; the arc
    # is present precisely so a planner that forgets that is caught out.
    add(Vertex(id="lob", kind=Kind.TRANSIT, capacity=6, floor=2,
               label="Lift lobby"))
    add(Vertex(id="lift", kind=Kind.EXIT, capacity=INF, floor=0,
               label="Lift car (disabled on alarm)"))
    _link(arcs, "lob", "c3", 6)
    _link(arcs, "lob", "lift", 3, 1400, "lift")

    # Two horizontal refuges across the compartment line, and one stair.
    add(Vertex(id="rw", kind=Kind.REFUGE, capacity=14, floor=2,
               label="West compartment refuge"))
    add(Vertex(id="re", kind=Kind.REFUGE, capacity=14, floor=2,
               label="East compartment refuge"))
    _link(arcs, "c1", "rw", 4, FIRE_DOOR, "door")
    _link(arcs, "c5", "re", 4, FIRE_DOOR, "door")

    add(Vertex(id="land", kind=Kind.TRANSIT, capacity=6, floor=2,
               label="Stair landing"))
    add(Vertex(id="ground", kind=Kind.EXIT, capacity=INF, floor=0,
               label="Final exit, open ground"))
    _link(arcs, "c5", "land", 5, FIRE_DOOR, "door")
    _link(arcs, "land", "ground", 24, STAIR, "stair")

    return Instance(
        vertices=v, arcs=arcs, dt=30, horizon=120,
        hazard=[HazardSource(vertex="b1", start=4, spread_factor=60.0)],
        name="ward_medical_28",
    )


# --------------------------------------------------------------------------
# Ward B: 12-bed ICU. Every patient is a bed case, so the stair is useless.
# --------------------------------------------------------------------------


def ward_icu_12() -> Instance:
    v: dict[str, Vertex] = {}
    arcs: list[Arc] = []

    for i in range(1, 4):
        v[f"c{i}"] = Vertex(id=f"c{i}", kind=Kind.TRANSIT, capacity=12,
                            floor=3, label=f"ICU corridor {i}")
    _link(arcs, "c1", "c2", 12)
    _link(arcs, "c2", "c3", 12)

    # Twelve ventilated beds in four pods of three.
    for i, junction in enumerate(("c1", "c1", "c3", "c3"), start=1):
        pid = f"pod{i}"
        v[pid] = Vertex(id=pid, kind=Kind.OCCUPIED, capacity=10, staff=3,
                        patients={3: 3}, acuity=1.0, floor=3,
                        label=f"ICU pod {i}")
        _link(arcs, pid, junction, 3, BED_DOOR, "door")

    v["ns"] = Vertex(id="ns", kind=Kind.TRANSIT, capacity=8, staff=2, floor=3,
                     label="ICU central station")
    _link(arcs, "ns", "c2", 4)

    v["rn"] = Vertex(id="rn", kind=Kind.REFUGE, capacity=8, floor=3,
                     label="North compartment refuge")
    v["rs"] = Vertex(id="rs", kind=Kind.REFUGE, capacity=8, floor=3,
                     label="South compartment refuge")
    _link(arcs, "c1", "rn", 4, FIRE_DOOR, "door")
    _link(arcs, "c3", "rs", 4, FIRE_DOOR, "door")

    # A stair exists. No bed will ever use it; pi(e, 3) = 0.
    v["land"] = Vertex(id="land", kind=Kind.TRANSIT, capacity=6, floor=3,
                       label="Stair landing")
    v["ground"] = Vertex(id="ground", kind=Kind.EXIT, capacity=INF, floor=0,
                         label="Final exit")
    _link(arcs, "c2", "land", 5, FIRE_DOOR, "door")
    _link(arcs, "land", "ground", 48, STAIR, "stair")

    return Instance(
        vertices=v, arcs=arcs, dt=30, horizon=160,
        hazard=[HazardSource(vertex="c2", start=6, spread_factor=60.0)],
        name="ward_icu_12",
    )


# --------------------------------------------------------------------------
# Ward C: two storeys. Walkers take the stair, beds cannot.
# --------------------------------------------------------------------------


def two_floor_block() -> Instance:
    v: dict[str, Vertex] = {}
    arcs: list[Arc] = []

    for floor in (1, 2):
        for i in (1, 2):
            vid = f"f{floor}c{i}"
            v[vid] = Vertex(id=vid, kind=Kind.TRANSIT, capacity=10,
                            floor=floor, label=f"Floor {floor} corridor {i}")
        _link(arcs, f"f{floor}c1", f"f{floor}c2", 16)

        for i in (1, 2):
            bid = f"f{floor}b{i}"
            v[bid] = Vertex(
                id=bid, kind=Kind.OCCUPIED, capacity=10,
                staff=2 if i == 1 else 1,
                patients={1: 2, 2: 2, 3: 1}, acuity=0.4 + 0.1 * floor,
                floor=floor, label=f"Floor {floor} bay {i}")
            _link(arcs, bid, f"f{floor}c{i}", 2.5, BED_DOOR, "door")

        rid = f"f{floor}r"
        v[rid] = Vertex(id=rid, kind=Kind.REFUGE, capacity=10, floor=floor,
                        label=f"Floor {floor} refuge")
        _link(arcs, f"f{floor}c2", rid, 4, FIRE_DOOR, "door")

        lid = f"f{floor}land"
        v[lid] = Vertex(id=lid, kind=Kind.TRANSIT, capacity=6, floor=floor,
                        label=f"Floor {floor} stair landing")
        _link(arcs, f"f{floor}c1", lid, 5, FIRE_DOOR, "door")

    _link(arcs, "f2land", "f1land", 24, STAIR, "stair")
    v["ground"] = Vertex(id="ground", kind=Kind.EXIT, capacity=INF, floor=0,
                         label="Final exit")
    _link(arcs, "f1land", "ground", 12, STAIR, "stair")

    return Instance(
        vertices=v, arcs=arcs, dt=30, horizon=160,
        hazard=[HazardSource(vertex="f2b1", start=4, spread_factor=60.0)],
        name="two_floor_block",
    )


# --------------------------------------------------------------------------
# Parametric generator, for the scaling tests
# --------------------------------------------------------------------------


def generate_ward(
    bays: int = 10,
    beds_per_bay: int = 6,
    staff: int = 8,
    seed: int = 0,
    hazard_at: str | None = None,
) -> Instance:
    """A straight-spine ward with ``bays`` bays hung off it."""
    if bays < 1 or beds_per_bay < 1:
        raise ValueError("bays and beds_per_bay must be positive")
    rng = random.Random(seed)
    v: dict[str, Vertex] = {}
    arcs: list[Arc] = []
    junctions = max(2, (bays + 1) // 2)

    for i in range(1, junctions + 1):
        v[f"c{i}"] = Vertex(id=f"c{i}", kind=Kind.TRANSIT, capacity=10,
                            floor=2, label=f"Corridor {i}")
    for i in range(1, junctions):
        _link(arcs, f"c{i}", f"c{i+1}", 14)

    for b in range(1, bays + 1):
        junction = f"c{(b + 1) // 2}"
        census: dict[int, int] = {}
        left = beds_per_bay
        for k in (3, 2, 1):
            take = rng.randint(0, left) if k != 1 else left
            if take:
                census[k] = take
            left -= take
        v[f"b{b}"] = Vertex(
            id=f"b{b}", kind=Kind.OCCUPIED, capacity=beds_per_bay * 2,
            patients=census, acuity=round(rng.random(), 2), floor=2,
            label=f"Bay {b}")
        _link(arcs, f"b{b}", junction, 2.5, BED_DOOR, "door")

    v["ns"] = Vertex(id="ns", kind=Kind.TRANSIT, capacity=8, staff=staff,
                     floor=2, label="Nurses station")
    _link(arcs, "ns", f"c{(junctions + 1) // 2}", 5)

    v["rw"] = Vertex(id="rw", kind=Kind.REFUGE, capacity=bays * beds_per_bay,
                     floor=2, label="West refuge")
    v["re"] = Vertex(id="re", kind=Kind.REFUGE, capacity=bays * beds_per_bay,
                     floor=2, label="East refuge")
    _link(arcs, "c1", "rw", 4, FIRE_DOOR, "door")
    _link(arcs, f"c{junctions}", "re", 4, FIRE_DOOR, "door")

    hazard = ([HazardSource(vertex=hazard_at, start=4, spread_factor=60.0)]
              if hazard_at else [])
    # The horizon has to grow with the ward, or a big instance is infeasible
    # by construction rather than by anything the planner did.
    horizon = max(200, 6 * bays + 2 * beds_per_bay * bays // max(1, staff))
    return Instance(vertices=v, arcs=arcs, dt=30, horizon=horizon,
                    hazard=hazard,
                    name=f"generated_{bays}x{beds_per_bay}")


WARDS = {
    "medical28": ward_medical_28,
    "icu12": ward_icu_12,
    "twofloor": two_floor_block,
}
