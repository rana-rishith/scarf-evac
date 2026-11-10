"""The cases that are easy to get wrong and never show up in a happy path.

Each test is named for the thing a careless planner gets away with until a
real ward exposes it.
"""

from __future__ import annotations

import pytest

from data.wards import WARDS, generate_ward, ward_medical_28
from scarf.model import Arc, HazardSource, Instance
from scarf.scenario import from_dict, loads, to_dict
from scarf.solver import Scarf, solve
from scarf.verify import verify

from .conftest import bay, build, link, refuge, transit

INF = float("inf")


# --------------------------------------------------------------------------
# H1  The escort has to walk back, and that walk is on the clock
# --------------------------------------------------------------------------


def _two_far_bays(staff: int) -> Instance:
    """Two wheelchair patients 60 m apart, sharing ``staff`` escorts."""
    arcs: list[Arc] = []
    link(arcs, "a", "c", 60.0)
    link(arcs, "b", "c", 60.0)
    link(arcs, "c", "r", 4.0, 1800, "door")
    return build([bay("a", {2: 1}, staff=staff), bay("b", {2: 1}),
                  transit("c"), refuge("r")], arcs, horizon=200,
                 name=f"two_far_bays_{staff}")


def test_the_second_patient_waits_for_the_escort_to_walk_back():
    inst = _two_far_bays(staff=1)
    plan = solve(inst)
    assert plan.phi
    first, second = sorted(plan.moves, key=lambda m: m.start)
    assert first.escorts == second.escorts == [0]
    # r -> c -> b is 64 m at 1.2 m/s, which is three 30 s slots.
    assert second.start >= first.arrival + 3, (
        f"escort teleported: delivered at {first.arrival}, "
        f"collected again at {second.start}")
    assert verify(inst, plan).ok


def test_a_second_nurse_removes_the_walk_back_entirely():
    one = solve(_two_far_bays(staff=1))
    two = solve(_two_far_bays(staff=2))
    assert one.phi and two.phi
    assert two.theta < one.theta, (
        "staffing must change the clearance time, otherwise the escort pool "
        "is not really being held")


def test_an_escort_left_at_a_far_refuge_is_not_free_immediately():
    inst = _two_far_bays(staff=1)
    scarf = Scarf(inst)
    plan = scarf.solve()
    report = verify(inst, plan)
    assert report.ok, str(report)


# --------------------------------------------------------------------------
# H2  Degenerate instances must answer, not crash or loop
# --------------------------------------------------------------------------


def test_an_empty_ward_is_trivially_clear():
    arcs: list[Arc] = []
    link(arcs, "a", "r", 10.0, 1800, "door")
    inst = build([bay("a", {}), refuge("r")], arcs, name="empty")
    plan = solve(inst)
    assert plan.phi and plan.theta == 0 and plan.moves == []


def test_a_ward_with_nowhere_to_go_is_infeasible():
    arcs: list[Arc] = []
    link(arcs, "a", "c", 10.0)
    inst = build([bay("a", {1: 2}), transit("c")], arcs, name="no_exit")
    plan = solve(inst)
    assert not plan.phi
    assert "no refuge or exit" in plan.reason


def test_a_bay_with_no_route_out_names_the_stranded_group():
    arcs: list[Arc] = []
    link(arcs, "a", "r", 10.0, 1800, "door")
    inst = build([bay("a", {1: 1}), bay("island", {1: 1}), refuge("r")],
                 arcs, name="island")
    plan = solve(inst)
    assert not plan.phi
    assert plan.unplaced == ["island#k1#0"]


def test_a_one_way_corridor_is_respected():
    """The fire blocks one side of a corridor; the other side still works."""
    arcs: list[Arc] = []
    link(arcs, "a", "c", 10.0, both=False)      # a -> c only
    link(arcs, "c", "r", 4.0, 1800, "door", both=False)
    inst = build([bay("a", {1: 2}), transit("c"), refuge("r")], arcs,
                 name="one_way")
    plan = solve(inst)
    assert plan.phi
    # Nothing can come back the other way, so no plan may use c -> a.
    assert all(h.arc.u != "c" or h.arc.v != "a"
               for m in plan.moves for h in m.hops)


def test_a_horizon_shorter_than_the_journey_is_infeasible():
    arcs: list[Arc] = []
    link(arcs, "a", "r", 300.0, 1800, "door")
    inst = build([bay("a", {3: 1}, staff=2), refuge("r")], arcs,
                 horizon=2, name="tiny_horizon")
    plan = solve(inst)
    assert not plan.phi


def test_patients_already_in_a_refuge_are_left_alone():
    arcs: list[Arc] = []
    link(arcs, "a", "r", 10.0, 1800, "door")
    safe = refuge("r")
    safe.patients = {3: 2}
    inst = build([bay("a", {1: 1}), safe], arcs, name="already_safe")
    assert inst.census == 1
    plan = solve(inst)
    assert plan.phi and len(plan.moves) == 1


def test_a_ward_of_walkers_needs_no_staff_at_all():
    arcs: list[Arc] = []
    link(arcs, "a", "r", 20.0, 1800, "door")
    inst = build([bay("a", {1: 8}, staff=0), refuge("r")], arcs,
                 name="no_staff")
    plan = solve(inst)
    assert plan.phi
    assert all(m.escorts == [] for m in plan.moves)


# --------------------------------------------------------------------------
# H3  A doorway a visitor walks through without noticing stops a trolley
# --------------------------------------------------------------------------


def test_a_bed_holds_a_narrow_door_for_more_than_one_slot():
    arcs: list[Arc] = []
    link(arcs, "a", "r", 3.0, 1100, "door")
    inst = build([bay("a", {3: 1}, staff=2), refuge("r")], arcs,
                 name="narrow_door")
    plan = solve(inst)
    assert plan.phi
    hop = plan.moves[0].hops[0]
    # Five width units through a door that passes three per slot.
    assert hop.span == 2
    assert hop.arrive - hop.depart >= 2


def test_the_door_a_bed_is_holding_is_closed_to_everyone_else():
    arcs: list[Arc] = []
    link(arcs, "a", "r", 3.0, 1100, "door")
    inst = build([bay("a", {3: 1, 1: 3}, staff=2), refuge("r")], arcs,
                 name="shared_door")
    plan = solve(inst)
    assert plan.phi
    load: dict[int, int] = {}
    for m in plan.moves:
        for h in m.hops:
            for t in range(h.depart, h.depart + h.span):
                load[t] = load.get(t, 0) + h.per
    assert max(load.values()) <= 3
    assert verify(inst, plan).ok


def test_a_single_door_bay_in_a_fire_cannot_save_everyone():
    """The headline finding, and it is arithmetic, not a solver failure.

    Bay 1 of the medical ward is lost three slots after the alarm, so its one
    1.1 m door passes 3 x 3 = 9 width units. Three walkers, two wheelchair
    users and one bed demand 1x3 + 2x2 + 5 = 12. Leaving the bed behind costs
    one patient; leaving the wheelchair users behind costs two. SCARF leaves
    the bed.
    """
    inst = ward_medical_28()
    scarf = Scarf(inst)
    assert scarf.deadlines["b1"] == 3
    plan = scarf.solve()
    assert not plan.phi
    assert plan.unplaced == ["b1#k3#0"]
    assert verify(inst, plan).ok


def test_giving_that_bay_more_time_saves_the_bed():
    inst = ward_medical_28()
    inst.hazard = [HazardSource(vertex="b1", start=12, spread_factor=60.0)]
    plan = solve(inst)
    assert plan.phi, plan.reason
    assert verify(inst, plan).ok


# --------------------------------------------------------------------------
# H4  Acuity is the tie-break, and it has to actually bite
# --------------------------------------------------------------------------


def test_the_sicker_patient_leaves_first():
    arcs: list[Arc] = []
    link(arcs, "sick", "c", 20.0, 1100, "door")
    link(arcs, "well", "c", 20.0, 1100, "door")
    link(arcs, "c", "r", 4.0, 1800, "door")
    inst = build([bay("sick", {2: 1}, acuity=0.95),
                  bay("well", {2: 1}, acuity=0.10),
                  transit("c", staff=1), refuge("r")], arcs, horizon=200,
                 name="acuity")
    plan = solve(inst)
    assert plan.phi
    order = [m.origin for m in sorted(plan.moves, key=lambda m: m.start)]
    assert order[0] == "sick"


def test_the_tie_break_is_reported_so_two_equal_plans_can_be_compared():
    inst = WARDS["icu12"]()
    plan = solve(inst)
    assert plan.weighted_arrival > 0
    # Every ICU patient has acuity 1.0, so the weight doubles each arrival.
    expected = sum(2.0 * m.arrival * m.size for m in plan.moves)
    assert plan.weighted_arrival == pytest.approx(expected)


# --------------------------------------------------------------------------
# H5  Hazard timing, including being caught in transit
# --------------------------------------------------------------------------


def test_a_corridor_lost_mid_crossing_is_not_used():
    arcs: list[Arc] = []
    link(arcs, "a", "long", 240.0)          # eight slots for a walker
    link(arcs, "long", "r", 4.0, 1800, "door")
    link(arcs, "a", "r2", 60.0, 1800, "door")
    inst = build([bay("a", {1: 2}), transit("long"), refuge("r"),
                  refuge("r2")], arcs,
                 hazard=[HazardSource(vertex="long", start=2)],
                 name="caught_in_transit")
    scarf = Scarf(inst)
    assert scarf.deadlines["long"] <= 2
    plan = scarf.solve()
    assert plan.phi
    assert all("long" not in m.path for m in plan.moves)
    assert verify(inst, plan).ok


def test_a_fire_door_slows_the_front_enough_to_make_a_refuge_worth_having():
    """A refuge is only safe because the compartment line resists the fire."""
    arcs: list[Arc] = []
    link(arcs, "a", "c", 10.0)
    link(arcs, "c", "r", 4.0, 1800, "door")
    inst = build([bay("a", {1: 2}), transit("c"), refuge("r")], arcs,
                 hazard=[HazardSource(vertex="a", start=0)], name="compartment")
    dl = Scarf(inst).deadlines
    assert dl["r"] > dl["c"] > dl["a"]


def test_the_safety_margin_keeps_people_clear_of_the_front():
    arcs: list[Arc] = []
    link(arcs, "a", "c", 10.0)
    link(arcs, "c", "r", 4.0, 1800, "door")
    nodes = [bay("a", {1: 2}), transit("c"), refuge("r")]
    lax = build(nodes, list(arcs), hazard=[HazardSource("a", 0)],
                safety_margin=0, name="lax")
    strict = build([bay("a", {1: 2}), transit("c"), refuge("r")], list(arcs),
                   hazard=[HazardSource("a", 0)], safety_margin=4,
                   name="strict")
    assert Scarf(strict).deadlines["c"] < Scarf(lax).deadlines["c"]


# --------------------------------------------------------------------------
# H6  Re-planning when the fire picture changes
# --------------------------------------------------------------------------


def test_a_new_fire_mid_evacuation_reroutes_whoever_is_still_moving():
    inst = WARDS["icu12"]()
    scarf = Scarf(inst)
    first = scarf.solve()
    assert first.phi
    later = scarf.replan(elapsed=4, hazard=[
        HazardSource(vertex="c2", start=6, spread_factor=60.0),
        HazardSource(vertex="c3", start=8, spread_factor=60.0),
    ])
    assert later.theta >= 4
    # Everyone who had already reached safety keeps their original journey.
    done = {m.group for m in first.moves if m.arrival <= 4}
    assert done <= {m.group for m in later.moves}


def test_replanning_with_no_new_hazard_does_not_lose_anyone():
    inst = WARDS["icu12"]()
    scarf = Scarf(inst)
    first = scarf.solve()
    later = scarf.replan(elapsed=0, hazard=inst.hazard)
    assert len(later.moves) >= len(first.moves) - len(first.unplaced)


# --------------------------------------------------------------------------
# H7  Input handling at the trust boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize("doc, message", [
    ({"arcs": []}, "missing 'vertices'"),
    ({"vertices": []}, "missing 'arcs'"),
    ({"vertices": [{"id": "a", "kind": "nonsense"}], "arcs": []},
     "unknown vertex kind"),
    ({"vertices": [{"id": "a", "kind": "exit"}, {"id": "a", "kind": "exit"}],
      "arcs": []}, "duplicate vertex id"),
    ({"vertices": [{"id": "a", "kind": "exit"}],
      "arcs": [{"u": "a", "v": "ghost", "tau": 5}]}, "unknown vertex"),
    ({"vertices": [{"id": "a", "kind": "occupied", "capacity": 4},
                   {"id": "r", "kind": "refuge", "capacity": 4}],
      "arcs": [{"u": "a", "v": "r", "tau": 0}]}, "non-positive tau"),
    ({"vertices": [{"id": "a", "kind": "occupied", "capacity": -1},
                   {"id": "r", "kind": "refuge", "capacity": 4}],
      "arcs": [{"u": "a", "v": "r", "tau": 5}]}, "negative quantity"),
    ({"vertices": [{"id": "a", "kind": "transit", "capacity": 4,
                    "patients": {"1": 2}},
                   {"id": "r", "kind": "refuge", "capacity": 4}],
      "arcs": [{"u": "a", "v": "r", "tau": 5}]}, "transit vertex"),
    ({"vertices": [{"id": "a", "kind": "occupied", "capacity": 4,
                    "patients": {"9": 1}},
                   {"id": "r", "kind": "refuge", "capacity": 4}],
      "arcs": [{"u": "a", "v": "r", "tau": 5}]}, "unknown mobility class"),
])
def test_malformed_scenarios_are_rejected_with_a_reason(doc, message):
    with pytest.raises(ValueError, match=message):
        from_dict(doc)


def test_a_scenario_survives_a_round_trip():
    import json
    inst = WARDS["medical28"]()
    again = loads(json.dumps(to_dict(inst)))
    assert again.census == inst.census
    assert again.total_staff == inst.total_staff
    assert len(again.vertices) == len(inst.vertices)
    assert len(again.arcs) == len(inst.arcs)
    assert solve(again).theta == solve(inst).theta


def test_an_unlimited_capacity_exit_is_accepted():
    doc = {
        "vertices": [
            {"id": "a", "kind": "occupied", "capacity": 4,
             "patients": {"1": 2}},
            {"id": "x", "kind": "exit", "capacity": None},
        ],
        "arcs": [{"u": "a", "v": "x", "tau": 10, "clear_width_mm": 1800}],
    }
    inst = from_dict(doc)
    assert inst.vertices["x"].capacity == INF
    assert solve(inst).phi


# --------------------------------------------------------------------------
# H8  Determinism and scale
# --------------------------------------------------------------------------


def test_the_same_ward_always_gets_the_same_plan():
    first = solve(WARDS["medical28"]())
    second = solve(WARDS["medical28"]())
    assert first.theta == second.theta
    assert [(m.group, m.start, m.path) for m in first.moves] == \
           [(m.group, m.start, m.path) for m in second.moves]


def test_more_rounds_never_make_the_plan_worse():
    inst = WARDS["medical28"]()
    one = Scarf(inst).solve(rounds=1)
    many = Scarf(inst).solve(rounds=5)
    assert many.sort_key() <= one.sort_key()


@pytest.mark.parametrize("bays", [40, 80])
def test_a_large_ward_still_plans(bays):
    inst = generate_ward(bays, 6, bays, seed=5)
    plan = solve(inst)
    assert verify(inst, plan).ok
    assert plan.phi, plan.reason


def test_the_infeasible_wards_strand_the_fewest_patients_possible():
    """The docs call both strandings provably minimal. Hold them to it.

    Both wards lose patients to the same arithmetic: the bay the fire starts
    in has one 1100 mm door passing three width units per slot, and goes
    untenable three slots after the alarm, so nine units get out of it. More
    than nine units are inside, so width has to be shed, and the fewest
    patients that shed enough is what SCARF must lose -- one, in both wards.
    """
    from scarf.solver import Scarf

    for name in ("medical28", "twofloor"):
        inst = WARDS[name]()
        plan = solve(inst)
        assert not plan.phi, f"{name} is now feasible; the docs say otherwise"

        # The bay the fire is in, not the widest one: it is the occupied
        # vertex that goes untenable first.
        deadlines = Scarf(inst).deadlines
        bay = min((v for v in inst.vertices.values() if v.patients),
                  key=lambda v: (deadlines[v.id], v.id))
        assert deadlines[bay.id] == 3, (
            f"{name}: {bay.id} now has {deadlines[bay.id]} slots, not 3; the "
            f"nine-width-unit figure in the docs assumes three")

        inside = sum(inst.width_units[k] * n for k, n in bay.patients.items())
        shed = inside - 3 * 3            # three slots at three units each
        assert shed > 0, f"{name}: {bay.id} now fits through its own door"

        # Fewest patients that shed that much width: widest first.
        widths = sorted((inst.width_units[k]
                         for k, n in bay.patients.items() for _ in range(n)),
                        reverse=True)
        fewest = got = 0
        while got < shed:
            got += widths[fewest]
            fewest += 1

        assert len(plan.unplaced) == fewest == 1, (
            f"{name}: {len(plan.unplaced)} stranded, {fewest} is the width "
            f"arithmetic minimum ({inside} units in {bay.id}, 9 can leave)")
