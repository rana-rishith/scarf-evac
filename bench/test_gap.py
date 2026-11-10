"""Measure SCARF against the exact optimum, and keep it honest.

These tests are the reason the optimality claims in the docs can be stated at
all. They are slower than the main suite because the exact solver enumerates
schedules, so they live here rather than in ``tests/``.

Run everything:      python -m pytest -q
Run just these:      python -m pytest bench -q
"""

from __future__ import annotations

import pytest

from bench.exact import Exact, Timeout
from bench.tiny import TINY
from scarf.solver import Scarf, solve
from scarf.verify import verify

#: Node budget per exact solve. Low enough that the whole suite stays quick:
#: an instance that needs more is skipped rather than slowing every run, and
#: `python -m bench.gap` re-runs the whole comparison with a bigger one.
BUDGET = 250_000

#: Instances where SCARF is known to be suboptimal, with the true optimum.
#: An entry here is a documented limitation, not a passing grade, and a gap
#: appearing anywhere else fails the suite. So does an entry left behind after
#: SCARF closes it, which is how both of the entries below came to be removed.
#:
#: Currently empty: SCARF reaches the optimum on every instance the exact
#: solver finishes.
#:
#:   split_destinations    the optimum fills the far refuge first, because the
#:                         early groups have the slack for a longer trip.
#:                         Closed by claiming refuge places in reverse
#:                         dispatch order.
#:   five_bays_two_nurses  a two-nurse ward where the optimum runs the two
#:                         long trips side by side at the end. Closed by
#:                         promoting whoever finishes last to the front of the
#:                         dispatch order and retrying.
KNOWN_GAPS: dict[str, int] = {}


#: One exact solve per instance per session. Four tests ask about the same
#: instance, and re-solving it four times was most of the suite's runtime.
_SOLVED: dict[str, object] = {}


def _solve_exact(name: str):
    if name not in _SOLVED:
        try:
            _SOLVED[name] = Exact(TINY[name](), budget=BUDGET).solve()
        except Timeout:
            _SOLVED[name] = None
    return _SOLVED[name]


def _exact(name: str):
    result = _solve_exact(name)
    if result is None:
        pytest.skip(f"exact solver exceeded {BUDGET} nodes")
    return result


@pytest.mark.parametrize("name", sorted(TINY))
def test_the_exact_solver_emits_a_valid_plan(name):
    """Whatever the exact solver claims is optimal must obey every rule."""
    inst = TINY[name]()
    result = _exact(name)
    if result.theta is None:
        pytest.skip("instance is infeasible, nothing to check")
    report = verify(inst, result.plan)
    assert report.ok, str(report)
    assert result.plan.theta == result.theta


@pytest.mark.parametrize("name", sorted(TINY))
def test_the_lower_bound_never_exceeds_the_true_optimum(name):
    """Admissibility, checked against the optimum rather than assumed.

    This is the test that caught the original bound charging a return walk
    for every delivery, including each escort's last one.
    """
    inst = TINY[name]()
    result = _exact(name)
    if result.theta is None:
        pytest.skip("instance is infeasible")
    bound = Scarf(inst).lower_bound()
    assert bound <= result.theta, (
        f"{name}: lower bound {bound} exceeds the optimum {result.theta}, "
        f"so it is not admissible")


@pytest.mark.parametrize("name", sorted(TINY))
def test_scarf_matches_the_optimum_except_where_documented(name):
    inst = TINY[name]()
    result = _exact(name)
    if result.theta is None:
        pytest.skip("instance is infeasible")
    plan = solve(inst)
    assert verify(inst, plan).ok
    assert plan.phi, plan.reason

    if name in KNOWN_GAPS:
        assert result.theta == KNOWN_GAPS[name], (
            f"{name}: the true optimum moved to {result.theta}; "
            f"KNOWN_GAPS says {KNOWN_GAPS[name]}")
        assert plan.theta > result.theta, (
            f"{name} is listed in KNOWN_GAPS but SCARF now reaches the "
            f"optimum. Delete the entry and take the win.")
        return

    assert plan.theta == result.theta, (
        f"{name}: SCARF gets {plan.theta}, optimum is {result.theta}. "
        f"Either this is a regression or it belongs in KNOWN_GAPS "
        f"with an explanation.")


def test_scarf_is_never_better_than_the_optimum():
    """A sanity check on the exact solver itself.

    If SCARF ever beats the claimed optimum, the exact solver is the thing
    that is wrong -- which is exactly how the inadmissible lower bound and
    the refuge standing-room bug were both found.
    """
    worse = []
    for name in TINY:
        result = _solve_exact(name)
        if result is None or result.theta is None:
            continue
        plan = solve(TINY[name]())
        if plan.phi and plan.theta < result.theta:
            worse.append((name, plan.theta, result.theta))
    assert not worse, (
        f"SCARF beat the 'exact' optimum on {worse}; the exact solver is "
        f"missing feasible plans")


def test_the_order_repair_earns_its_place():
    """Promoting the last-arriving group must still improve real plans.

    It was added to close the one remaining gap on `five_bays_two_nurses`,
    but it turned out to be a general improvement: the 12-bed ICU dropped
    from 17 slots to 15, the two-floor block likewise, and three generated
    wards improved too. If it ever stops helping anywhere, it is costing a
    dispatch pass per round for nothing and should be deleted.
    """
    from data.wards import WARDS

    original = Scarf._repair
    Scarf._repair = lambda self, plan, penalty, claim: plan
    try:
        without = {name: solve(WARDS[name]()).theta for name in WARDS}
    finally:
        Scarf._repair = original

    with_repair = {name: solve(WARDS[name]()).theta for name in WARDS}

    better = [n for n in WARDS if with_repair[n] < without[n]]
    worse = [n for n in WARDS if with_repair[n] > without[n]]
    assert not worse, f"order repair made these worse: {worse}"
    assert better, (
        f"order repair no longer improves any ward plan; delete it. "
        f"with={with_repair} without={without}")
