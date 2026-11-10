"""Run the whole SCARF-versus-optimum comparison and print the table.

    python -m bench.gap                 # default budget, a few seconds
    python -m bench.gap --budget 8000000  # chase the stubborn ones
    python -m bench.gap --wards          # the large wards, bound only

The test suite asserts the conclusions; this prints the evidence.
"""

from __future__ import annotations

import argparse
import statistics
import time

from bench.exact import Exact, Timeout
from bench.tiny import TINY
from scarf.solver import Scarf, solve
from scarf.verify import verify


def exact_table(budget: int) -> int:
    print(f"{'instance':<26}{'P':>3}{'grp':>4}{'LB':>4}"
          f"{'SCARF':>7}{'OPT':>6}{'tight':>7}{'nodes':>10}{'s':>7}  check")
    ratios: list[float] = []
    optimal = solved = tight = 0
    gaps: list[tuple[str, int, int]] = []
    unresolved: list[str] = []

    for name, build in TINY.items():
        inst = build()
        plan = solve(inst)
        groups = len(Scarf(inst).groups)
        started = time.perf_counter()
        try:
            result = Exact(inst, budget=budget).solve()
        except Timeout:
            result = None
        elapsed = time.perf_counter() - started

        if result is None or result.theta is None:
            unresolved.append(name)
            print(f"{name:<26}{inst.census:>3}{groups:>4}"
                  f"{plan.lower_bound:>4}{plan.theta:>7}{'--':>6}{'':>7}"
                  f"{'budget':>10}{elapsed:>7.1f}  not solved")
            continue

        solved += 1
        ratios.append(plan.lower_bound / result.theta)
        tight += plan.lower_bound == result.theta
        check = "ok" if verify(inst, result.plan).ok else "FAIL"
        if plan.theta == result.theta:
            optimal += 1
            note = ""
        else:
            over = 100 * (plan.theta - result.theta) / result.theta
            note = f"  <-- gap +{over:.0f}%"
            gaps.append((name, plan.theta, result.theta))
        assert plan.lower_bound <= result.theta, (
            f"{name}: bound {plan.lower_bound} exceeds optimum {result.theta}")
        print(f"{name:<26}{inst.census:>3}{groups:>4}{plan.lower_bound:>4}"
              f"{plan.theta:>7}{result.theta:>6}"
              f"{100 * plan.lower_bound / result.theta:>6.0f}%"
              f"{result.nodes:>10,}{elapsed:>7.1f}  {check}{note}")

    print()
    print(f"SCARF optimal on {optimal}/{solved} instances solved exactly")
    if ratios:
        print(f"lower bound averages {100 * statistics.mean(ratios):.0f}% of "
              f"the optimum, exactly tight on {tight}/{solved}")
    if gaps:
        print("gaps: " + ", ".join(
            f"{n} ({got} vs {want})" for n, got, want in gaps))
    if unresolved:
        print(f"beyond the {budget:,}-node budget: {', '.join(unresolved)}")
    return 0


def ward_table() -> int:
    from data.wards import WARDS, generate_ward

    rows = [(n, WARDS[n]()) for n in sorted(WARDS)]
    rows += [(f"gen {b}x6", generate_ward(b, 6, max(4, b), seed=5))
             for b in (10, 20, 40, 80, 160)]
    print(f"{'instance':<12}{'n':>5}{'m':>5}{'P':>6}{'staff':>6}"
          f"{'phi':>6}{'Theta':>7}{'LB':>5}{'within':>8}"
          f"{'ms':>8}  limited by")
    for name, inst in rows:
        started = time.perf_counter()
        plan = solve(inst)
        ms = (time.perf_counter() - started) * 1000
        assert verify(inst, plan).ok, name
        within = ("--" if not plan.phi else
                  f"{100 * (plan.theta - plan.lower_bound) / max(1, plan.lower_bound):.0f}%")
        print(f"{name:<12}{len(inst.vertices):>5}{len(inst.arcs):>5}"
              f"{inst.census:>6}{inst.total_staff:>6}"
              f"{'yes' if plan.phi else 'NO':>6}{plan.theta:>7}"
              f"{plan.lower_bound:>5}{within:>8}{ms:>8.0f}  {plan.binding}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.gap", description=__doc__)
    ap.add_argument("--budget", type=int, default=250_000,
                    help="exact-solver node budget (default 250000)")
    ap.add_argument("--wards", action="store_true",
                    help="print the large-ward table instead")
    args = ap.parse_args(argv)
    return ward_table() if args.wards else exact_table(args.budget)


if __name__ == "__main__":
    raise SystemExit(main())
