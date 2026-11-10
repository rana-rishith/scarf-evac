"""The five rules from the problem statement, and the model that feeds them."""

from __future__ import annotations

import pytest

from data.wards import WARDS, generate_ward
from scarf.ledger import Ledger
from scarf.model import Arc, Kind, Vertex
from scarf.solver import Scarf, solve
from scarf.verify import verify

from .conftest import bay, build, exit_node, link, refuge, transit

INF = float("inf")


# --------------------------------------------------------------------------
# Capacity derived from clear width
# --------------------------------------------------------------------------


@pytest.mark.parametrize("width_mm, expected", [
    (2150, 5),      # bed-moving corridor: ten walkers or two beds a minute
    (1800, 4),      # double-leaf fire door
    (1200, 3),      # escape stair
    (1100, 3),      # bed-access door leaf
    (850, 2),       # wheelchair door
])
def test_capacity_follows_clear_width(width_mm, expected):
    a = Arc(u="p", v="q", tau=10, clear_width_mm=width_mm)
    a.derive(dt=30)
    assert a.capacity == expected


def test_corridor_capacity_matches_the_problem_statement():
    """Five width units per 30 s is ten walkers a minute and two beds."""
    a = Arc(u="p", v="q", tau=10, clear_width_mm=2150)
    a.derive(dt=30)
    walkers_per_min = 2 * a.capacity // 1
    beds_per_min = 2 * (a.capacity // 5)
    assert (walkers_per_min, beds_per_min) == (10, 2)


@pytest.mark.parametrize("width_mm, kind, fits", [
    (2150, "corridor", {1: True, 2: True, 3: True}),
    (1100, "door", {1: True, 2: True, 3: True}),
    (900, "door", {1: True, 2: True, 3: False}),    # a bed will not pass
    (700, "door", {1: True, 2: False, 3: False}),
    (1200, "stair", {1: True, 2: True, 3: False}),  # never a bed
    (1400, "lift", {1: False, 2: False, 3: False}),  # dead on alarm
])
def test_pi_follows_width_and_kind(width_mm, kind, fits):
    a = Arc(u="p", v="q", tau=10, clear_width_mm=width_mm, kind=kind)
    a.derive(dt=30)
    assert {k: a.fits[k] for k in (1, 2, 3)} == fits


def test_stair_demands_an_extra_pair_of_hands_for_a_wheelchair():
    a = Arc(u="p", v="q", tau=20, clear_width_mm=1200, kind="stair")
    a.derive(dt=30)
    assert a.fits[2] and a.extra_escorts == 1


# --------------------------------------------------------------------------
# Rule 1: shared corridor width
# --------------------------------------------------------------------------


def test_both_directions_of_a_corridor_share_one_width():
    """Storing directions separately must not double the corridor capacity."""
    arcs: list[Arc] = []
    link(arcs, "a", "b", 10.0)
    assert arcs[0].corridor == arcs[1].corridor
    assert arcs[0].u == arcs[1].v and arcs[0].v == arcs[1].u


def test_ledger_charges_both_directions_to_the_same_budget(straight):
    led = Ledger(straight)
    a, b = straight.arcs[0], straight.arcs[1]
    assert a.corridor == b.corridor
    led.reserve_arc(a.corridor, 0, 1, a.capacity)
    assert not led.arc_free(b.corridor, b.capacity, 0, 1, 1)


def test_span_spreads_a_wide_load_over_several_slots():
    """A bed is five width units; a 1.1 m door passes three a slot."""
    span, per = Ledger.span_of(units=5, cap=3)
    assert span == 2 and per == 3


# --------------------------------------------------------------------------
# Every rule at once, on every ward
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WARDS))
def test_built_in_wards_produce_verifiable_plans(name):
    inst = WARDS[name]()
    plan = solve(inst)
    report = verify(inst, plan)
    assert report.ok, str(report)


@pytest.mark.parametrize("bays, beds, staff, seed", [
    (2, 2, 2, 0), (5, 4, 4, 1), (12, 6, 10, 2), (20, 6, 16, 3),
])
def test_generated_wards_produce_verifiable_plans(bays, beds, staff, seed):
    inst = generate_ward(bays, beds, staff, seed=seed)
    plan = solve(inst)
    report = verify(inst, plan)
    assert report.ok, str(report)
    assert plan.phi, plan.reason


def test_generated_ward_with_a_fire_still_verifies():
    inst = generate_ward(10, 6, 12, seed=7, hazard_at="b5")
    plan = solve(inst)
    assert verify(inst, plan).ok


# --------------------------------------------------------------------------
# Rule 4: nobody in the fire, nothing through an opening it does not fit
# --------------------------------------------------------------------------


def test_a_bed_is_never_routed_down_a_stair():
    arcs: list[Arc] = []
    link(arcs, "a", "c", 3.0, 1100, "door")
    link(arcs, "c", "land", 5.0, 1800, "door")
    link(arcs, "land", "x", 24.0, 1200, "stair")
    link(arcs, "c", "r", 4.0, 1800, "door")
    inst = build([bay("a", {3: 2}, staff=4), transit("c"), transit("land"),
                  refuge("r"), exit_node("x")], arcs, name="stairs")
    plan = solve(inst)
    assert plan.phi
    for move in plan.moves:
        assert all(h.arc.kind != "stair" for h in move.hops)
        assert move.destination == "r"


def test_a_bed_bay_whose_only_way_out_is_a_stair_is_declared_infeasible():
    arcs: list[Arc] = []
    link(arcs, "a", "land", 3.0, 1100, "door")
    link(arcs, "land", "x", 24.0, 1200, "stair")
    inst = build([bay("a", {3: 1}, staff=4), transit("land"), exit_node("x")],
                 arcs, name="stair_only")
    plan = solve(inst)
    assert not plan.phi
    assert "class-feasible route" in plan.reason
    assert plan.unplaced == ["a#k3#0"]


def test_the_lift_is_never_used():
    arcs: list[Arc] = []
    link(arcs, "a", "lob", 3.0, 1100, "door")
    link(arcs, "lob", "lift", 3.0, 1400, "lift")
    link(arcs, "a", "r", 40.0, 1800, "door")
    inst = build([bay("a", {1: 4}), transit("lob"),
                  Vertex(id="lift", kind=Kind.EXIT, capacity=INF),
                  refuge("r")], arcs, name="lift")
    plan = solve(inst)
    assert plan.phi
    assert all(m.destination == "r" for m in plan.moves)
    assert verify(inst, plan).ok


def test_nobody_is_routed_into_a_vertex_the_fire_has_taken():
    from scarf.model import HazardSource
    arcs: list[Arc] = []
    link(arcs, "a", "c1", 3.0, 1100, "door")
    link(arcs, "c1", "c2", 60.0)
    link(arcs, "c2", "r", 4.0, 1800, "door")
    link(arcs, "a", "r2", 20.0, 1800, "door")
    inst = build([bay("a", {1: 2}), transit("c1"), transit("c2"),
                  refuge("r"), refuge("r2")], arcs,
                 hazard=[HazardSource(vertex="c2", start=0)], name="fire")
    scarf = Scarf(inst)
    assert scarf.deadlines["c2"] == 0
    plan = scarf.solve()
    assert plan.phi
    for move in plan.moves:
        assert "c2" not in move.path
    assert verify(inst, plan).ok


# --------------------------------------------------------------------------
# Rule 5: staff are finite
# --------------------------------------------------------------------------


def test_a_bed_patient_needs_two_pairs_of_hands(straight):
    plan = solve(straight)
    beds = [m for m in plan.moves if m.cls == 3]
    assert beds and all(len(m.escorts) == 2 for m in beds)
    chairs = [m for m in plan.moves if m.cls == 2]
    assert chairs and all(len(m.escorts) == 1 for m in chairs)
    walkers = [m for m in plan.moves if m.cls == 1]
    assert walkers and all(m.escorts == [] for m in walkers)


def test_one_nurse_cannot_move_a_bed_patient():
    arcs: list[Arc] = []
    link(arcs, "a", "r", 10.0, 1800, "door")
    inst = build([bay("a", {3: 1}, staff=1), refuge("r")], arcs,
                 name="short_staffed")
    plan = solve(inst)
    assert not plan.phi
    assert "needs 2 escorts" in plan.reason
    assert "ward has 1 staff" in plan.reason


def test_no_escort_is_ever_committed_to_two_groups_at_once():
    inst = generate_ward(8, 6, 6, seed=11)
    plan = solve(inst)
    # verify() checks escort continuity; assert it independently here too.
    assert verify(inst, plan).ok
    busy: dict[int, list[tuple[int, int]]] = {}
    for m in plan.moves:
        for i in m.escorts:
            busy.setdefault(i, []).append((m.start, m.arrival))
    for i, windows in busy.items():
        windows.sort()
        for (s1, e1), (s2, _) in zip(windows, windows[1:]):
            assert s2 >= e1, f"escort {i} double-booked: {windows}"


# --------------------------------------------------------------------------
# Rules 2 and 6: standing room, and a refuge that is full
# --------------------------------------------------------------------------


def test_a_full_refuge_pushes_patients_to_the_next_one():
    arcs: list[Arc] = []
    link(arcs, "a", "c", 3.0, 1100, "door")
    link(arcs, "c", "r1", 4.0, 1800, "door")
    link(arcs, "c", "r2", 30.0, 1800, "door")
    inst = build([bay("a", {1: 4}), transit("c"),
                  refuge("r1", capacity=2), refuge("r2", capacity=10)],
                 arcs, name="full_refuge")
    plan = solve(inst)
    assert plan.phi
    landed: dict[str, int] = {}
    for m in plan.moves:
        landed[m.destination] = landed.get(m.destination, 0) + m.size
    assert landed.get("r1", 0) <= 2
    assert sum(landed.values()) == 4
    assert verify(inst, plan).ok


def test_a_narrow_junction_does_not_overfill():
    arcs: list[Arc] = []
    for b in ("a1", "a2", "a3"):
        link(arcs, b, "c", 3.0, 1100, "door")
    link(arcs, "c", "r", 40.0, 1800, "door")
    nodes = [bay(b, {1: 6}) for b in ("a1", "a2", "a3")]
    nodes += [transit("c", capacity=4), refuge("r")]
    inst = build(nodes, arcs, name="tight_junction")
    plan = solve(inst)
    assert verify(inst, plan).ok


# --------------------------------------------------------------------------
# Rule 8 and the reported outputs
# --------------------------------------------------------------------------


def test_a_patient_held_by_a_doctor_does_not_move_early():
    arcs: list[Arc] = []
    link(arcs, "a", "r", 10.0, 1800, "door")
    inst = build([bay("a", {3: 1}, staff=4, defer_until=6), refuge("r")],
                 arcs, name="deferred")
    plan = solve(inst)
    assert plan.phi
    assert all(m.start >= 6 for m in plan.moves)
    assert verify(inst, plan).ok


def test_theta_is_the_last_arrival(straight):
    plan = solve(straight)
    assert plan.theta == max(m.arrival for m in plan.moves)
    assert plan.clearance_seconds == plan.theta * straight.dt


def test_flow_records_every_hop(straight):
    plan = solve(straight)
    total = sum(plan.flow.values())
    hops = sum(len(m.hops) * m.size for m in plan.moves)
    assert total == hops


def test_bottlenecks_name_real_arcs():
    inst = WARDS["icu12"]()
    plan = solve(inst)
    pairs = {(a.u, a.v) for a in inst.arcs}
    assert plan.bottlenecks
    assert all((a.u, a.v) in pairs for a in plan.bottlenecks)


def test_theta_never_beats_the_lower_bound():
    for name in sorted(WARDS):
        inst = WARDS[name]()
        plan = solve(inst)
        if plan.phi:
            assert plan.theta >= plan.lower_bound, name
