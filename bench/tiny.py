"""Instances small enough to solve exactly.

Each one isolates a mechanism SCARF has to get right. They are deliberately
tiny -- two to five groups, three to seven vertices, short horizons -- because
the exact solver enumerates schedules and only tiny instances finish.
"""

from __future__ import annotations

from scarf.model import Arc, HazardSource, Instance, Kind, Vertex

INF = float("inf")

CORRIDOR = 2150
BED_DOOR = 1100
FIRE_DOOR = 1800
STAIR = 1200


def _link(arcs: list[Arc], u: str, v: str, metres: float,
          width: int = CORRIDOR, kind: str = "corridor") -> None:
    cid = f"{min(u, v)}|{max(u, v)}"
    for a, b in ((u, v), (v, u)):
        arcs.append(Arc(u=a, v=b, tau=round(metres / 1.2, 1),
                        clear_width_mm=width, kind=kind, corridor=cid))


def _bay(vid, patients, staff=0, capacity=10, acuity=0.5, defer=0):
    return Vertex(id=vid, kind=Kind.OCCUPIED, capacity=capacity, staff=staff,
                  patients=dict(patients), acuity=acuity,
                  defer_until=defer, label=vid)


def _transit(vid, capacity=10, staff=0):
    return Vertex(id=vid, kind=Kind.TRANSIT, capacity=capacity, staff=staff,
                  label=vid)


def _refuge(vid, capacity=20):
    return Vertex(id=vid, kind=Kind.REFUGE, capacity=capacity, label=vid)


def _inst(nodes, arcs, **kw) -> Instance:
    inst = Instance(vertices={n.id: n for n in nodes}, arcs=arcs, **kw)
    inst.validate()
    return inst


# --------------------------------------------------------------------------


def walkers_only() -> Instance:
    """Two walker groups, no escorts. Pure capacity."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "b", "c", 12)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {1: 3}), _bay("b", {1: 3}), _transit("c"),
                  _refuge("r")], arcs, horizon=14, name="walkers_only")


def one_bed() -> Instance:
    """A single bed patient with exactly enough hands. Sanity case."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {3: 1}, staff=2), _transit("c"), _refuge("r")],
                 arcs, horizon=14, name="one_bed")


def two_beds_two_staff() -> Instance:
    """Two beds, one pair of hands. The second waits for the walk back."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 24)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {3: 2}, staff=2), _transit("c"), _refuge("r")],
                 arcs, horizon=20, name="two_beds_two_staff")


def recirculate_across_bays() -> Instance:
    """One nurse, two wheelchair patients at opposite ends."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 24)
    _link(arcs, "b", "c", 24)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {2: 1}, staff=1), _bay("b", {2: 1}),
                  _transit("c"), _refuge("r")], arcs, horizon=24,
                 name="recirculate_across_bays")


def mixed_bay() -> Instance:
    """One bed and one wheelchair sharing a bay and a pair of hands."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {2: 1, 3: 1}, staff=2), _transit("c"),
                  _refuge("r")], arcs, horizon=20, name="mixed_bay")


def narrow_door() -> Instance:
    """A bed holds the only door for two slots; walkers queue behind it."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 6, BED_DOOR, "door")
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {1: 4, 3: 1}, staff=2), _transit("c"),
                  _refuge("r")], arcs, horizon=18, name="narrow_door")


def two_refuges() -> Instance:
    """A real destination choice: near refuge is small, far one is big."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "c", "r1", 4, FIRE_DOOR, "door")
    _link(arcs, "c", "r2", 36, FIRE_DOOR, "door")
    return _inst([_bay("a", {3: 2}, staff=4), _transit("c"),
                  _refuge("r1", capacity=1), _refuge("r2", capacity=8)],
                 arcs, horizon=22, name="two_refuges")


def tight_junction() -> Instance:
    """Standing room at the junction binds before the corridor does."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "b", "c", 12)
    _link(arcs, "c", "r", 24, FIRE_DOOR, "door")
    return _inst([_bay("a", {2: 2}, staff=2), _bay("b", {2: 1}, staff=1),
                  _transit("c", capacity=4), _refuge("r")], arcs,
                 horizon=24, name="tight_junction")


def under_fire() -> Instance:
    """A deadline on the bay forces the order."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {1: 2, 2: 1}, staff=1), _transit("c"),
                  _refuge("r")], arcs, horizon=16,
                 hazard=[HazardSource(vertex="a", start=9,
                                      spread_factor=60.0)],
                 name="under_fire")


def stair_choice() -> Instance:
    """Grab a third pair of hands for the stair, or take the long way."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 6, BED_DOOR, "door")
    _link(arcs, "c", "land", 5, FIRE_DOOR, "door")
    _link(arcs, "land", "x", 24, STAIR, "stair")
    _link(arcs, "c", "r", 60, FIRE_DOOR, "door")
    return _inst([_bay("a", {2: 2}, staff=3), _transit("c"),
                  _transit("land", capacity=6), _refuge("r"),
                  Vertex(id="x", kind=Kind.EXIT, capacity=INF, label="x")],
                 arcs, horizon=26, name="stair_choice")


def deferred_patient() -> Instance:
    """A patient a doctor has not cleared yet."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {3: 1}, staff=2, defer=4),
                  _bay("b", {1: 2}), _transit("c"), _refuge("r")],
                 arcs + _b_link(), horizon=18, name="deferred_patient")


def _b_link() -> list[Arc]:
    arcs: list[Arc] = []
    _link(arcs, "b", "c", 12)
    return arcs


# -- harder cases, to probe for a gap the easy ones would not show ---------


def three_bays_one_nurse() -> Instance:
    """One nurse, three wheelchair patients at three different distances."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "b", "c", 24)
    _link(arcs, "d", "c", 36)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {2: 1}, staff=1), _bay("b", {2: 1}),
                  _bay("d", {2: 1}), _transit("c"), _refuge("r")],
                 arcs, horizon=30, name="three_bays_one_nurse")


def competing_bays() -> Instance:
    """Two bays of beds sharing one corridor that passes one bed per slot."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "b", "c", 12)
    _link(arcs, "c", "r", 12, FIRE_DOOR, "door")
    return _inst([_bay("a", {3: 2}, staff=2), _bay("b", {3: 2}, staff=2),
                  _transit("c"), _refuge("r")], arcs, horizon=26,
                 name="competing_bays")


def queue_at_door() -> Instance:
    """Beds and walkers out of one bay through one 1100 mm door."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 6, BED_DOOR, "door")
    _link(arcs, "c", "r", 12, FIRE_DOOR, "door")
    return _inst([_bay("a", {1: 3, 2: 1, 3: 2}, staff=4), _transit("c"),
                  _refuge("r")], arcs, horizon=26, name="queue_at_door")


def deadline_forces_order() -> Instance:
    """A bay about to be lost, holding a bed and two wheelchair patients.

    The width arithmetic does not let everyone out, so the choice of who to
    leave is the whole decision. SCARF should strand the bed, not the pair.
    """
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 6, BED_DOOR, "door")
    _link(arcs, "b", "c", 12)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {2: 2, 3: 1}, staff=4), _bay("b", {1: 2}),
                  _transit("c"), _refuge("r")], arcs, horizon=20,
                 hazard=[HazardSource(vertex="a", start=6,
                                      spread_factor=60.0)],
                 name="deadline_forces_order")


def split_destinations() -> Instance:
    """Two refuges of two places each, four escorted patients."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12)
    _link(arcs, "c", "r1", 6, FIRE_DOOR, "door")
    _link(arcs, "c", "r2", 30, FIRE_DOOR, "door")
    return _inst([_bay("a", {2: 4}, staff=4), _transit("c"),
                  _refuge("r1", capacity=2), _refuge("r2", capacity=4)],
                 arcs, horizon=26, name="split_destinations")


def long_wing() -> Instance:
    """A bed and a walker sharing a wide door at the end of a long wing.

    Egress is cheap -- six width units through a corridor passing five a slot
    -- and staff are plentiful, so neither the bay term nor the escort term
    bites. What sets the clearance time is simply how long the bed takes to
    travel, which is the one situation where the travel term binds alone.
    """
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 60)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {1: 1, 3: 1}, staff=8), _transit("c"),
                  _refuge("r")], arcs, horizon=24, name="long_wing")


# -- larger cases, to push the exact solver past four groups ---------------


def five_bays_two_nurses() -> Instance:
    """Five wheelchair patients down one corridor, two nurses."""
    arcs: list[Arc] = []
    for i, dist in enumerate((12, 18, 24, 30, 36), start=1):
        _link(arcs, f"b{i}", "c", dist)
    _link(arcs, "c", "r", 4, FIRE_DOOR, "door")
    nodes = [_bay(f"b{i}", {2: 1}, staff=(2 if i == 1 else 0))
             for i in range(1, 6)]
    nodes += [_transit("c"), _refuge("r")]
    return _inst(nodes, arcs, horizon=34, name="five_bays_two_nurses")


def six_beds_two_pairs() -> Instance:
    """Six beds out of one bay with four staff: two pairs, recirculating."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12, BED_DOOR, "door")
    _link(arcs, "c", "r", 8, FIRE_DOOR, "door")
    return _inst([_bay("a", {3: 6}, staff=4, capacity=14), _transit("c"),
                  _refuge("r", capacity=8)], arcs, horizon=40,
                 name="six_beds_two_pairs")


def mixed_two_bays() -> Instance:
    """Two bays, each a walker group, a wheelchair and a bed: six groups."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c", 12, BED_DOOR, "door")
    _link(arcs, "b", "c", 18, BED_DOOR, "door")
    _link(arcs, "c", "r", 6, FIRE_DOOR, "door")
    return _inst([_bay("a", {1: 2, 2: 1, 3: 1}, staff=3),
                  _bay("b", {1: 2, 2: 1, 3: 1}, staff=1),
                  _transit("c"), _refuge("r")], arcs, horizon=34,
                 name="mixed_two_bays")


def seven_groups_two_refuges() -> Instance:
    """Seven groups and a genuine destination choice at each end."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c1", 12, BED_DOOR, "door")
    _link(arcs, "b", "c2", 12, BED_DOOR, "door")
    _link(arcs, "c1", "c2", 18)
    _link(arcs, "c1", "r1", 4, FIRE_DOOR, "door")
    _link(arcs, "c2", "r2", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {2: 2, 3: 2}, staff=4),
                  _bay("b", {1: 3, 2: 1}, staff=2),
                  _transit("c1"), _transit("c2"),
                  _refuge("r1", capacity=3), _refuge("r2", capacity=6)],
                 arcs, horizon=34, name="seven_groups_two_refuges")


def eight_bays_three_nurses() -> Instance:
    """Eight wheelchair patients, three nurses, one spine."""
    arcs: list[Arc] = []
    for i in range(1, 9):
        junction = "c1" if i <= 4 else "c2"
        _link(arcs, f"b{i}", junction, 10 + 4 * i)
    _link(arcs, "c1", "c2", 16)
    _link(arcs, "c1", "r", 4, FIRE_DOOR, "door")
    nodes = [_bay(f"b{i}", {2: 1}, staff=(3 if i == 1 else 0))
             for i in range(1, 9)]
    nodes += [_transit("c1"), _transit("c2"), _refuge("r")]
    return _inst(nodes, arcs, horizon=46, name="eight_bays_three_nurses")


def nine_groups_under_fire() -> Instance:
    """Nine groups, a spreading fire and two refuges."""
    arcs: list[Arc] = []
    _link(arcs, "a", "c1", 10, BED_DOOR, "door")
    _link(arcs, "b", "c1", 14, BED_DOOR, "door")
    _link(arcs, "d", "c2", 14, BED_DOOR, "door")
    _link(arcs, "c1", "c2", 16)
    _link(arcs, "c1", "r1", 4, FIRE_DOOR, "door")
    _link(arcs, "c2", "r2", 4, FIRE_DOOR, "door")
    return _inst([_bay("a", {1: 2, 2: 1, 3: 1}, staff=3, acuity=0.9),
                  _bay("b", {1: 2, 2: 1}, staff=2, acuity=0.5),
                  _bay("d", {2: 1, 3: 1}, staff=2, acuity=0.7),
                  _transit("c1"), _transit("c2"),
                  _refuge("r1", capacity=5), _refuge("r2", capacity=6)],
                 arcs, horizon=40,
                 hazard=[HazardSource(vertex="a", start=14,
                                      spread_factor=60.0)],
                 name="nine_groups_under_fire")


TINY = {
    "walkers_only": walkers_only,
    "one_bed": one_bed,
    "two_beds_two_staff": two_beds_two_staff,
    "recirculate_across_bays": recirculate_across_bays,
    "mixed_bay": mixed_bay,
    "narrow_door": narrow_door,
    "two_refuges": two_refuges,
    "tight_junction": tight_junction,
    "under_fire": under_fire,
    "stair_choice": stair_choice,
    "deferred_patient": deferred_patient,
    "three_bays_one_nurse": three_bays_one_nurse,
    "competing_bays": competing_bays,
    "queue_at_door": queue_at_door,
    "deadline_forces_order": deadline_forces_order,
    "split_destinations": split_destinations,
    "long_wing": long_wing,
    "five_bays_two_nurses": five_bays_two_nurses,
    "six_beds_two_pairs": six_beds_two_pairs,
    "mixed_two_bays": mixed_two_bays,
    "seven_groups_two_refuges": seven_groups_two_refuges,
    "eight_bays_three_nurses": eight_bays_three_nurses,
    "nine_groups_under_fire": nine_groups_under_fire,
}
