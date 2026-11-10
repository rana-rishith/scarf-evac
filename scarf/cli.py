"""Command line front end.

    python -m scarf.cli --ward medical28
    python -m scarf.cli my_ward.json --verify
    python -m scarf.cli --ward icu12 --json > plan.json
"""

from __future__ import annotations

import argparse
import json
import sys

from .model import Instance, Kind
from .scenario import load
from .solver import Plan, Scarf
from .verify import verify


def _clock(slot: int, dt: int) -> str:
    total = slot * dt
    return f"{total // 60:02d}:{total % 60:02d}"


def render(inst: Instance, plan: Plan, show_verify: bool = False) -> str:
    out: list[str] = []
    w = out.append
    w(f"Ward            {inst.name}")
    w(f"Graph           n={len(inst.vertices)} vertices, "
      f"m={len(inst.arcs)} arcs, slot={inst.dt}s, "
      f"horizon={_clock(inst.horizon, inst.dt)}")
    counts: dict[int, int] = {}
    for v in inst.vertices.values():
        if v.kind in (Kind.REFUGE, Kind.EXIT):
            continue
        for k, n in v.patients.items():
            counts[k] = counts.get(k, 0) + n
    census = ", ".join(f"{counts.get(k, 0)} class-{k}" for k in (1, 2, 3))
    w(f"Census          P={inst.census} ({census})")
    w(f"Staff on duty   {inst.total_staff}")
    if inst.hazard:
        where = ", ".join(f"{h.vertex} from {_clock(h.start, inst.dt)}"
                          for h in inst.hazard)
        w(f"Hazard          {where}")
    w("")

    verdict = "FEASIBLE" if plan.phi else "NOT FEASIBLE"
    w(f"phi             {verdict}")
    if plan.reason:
        w(f"                {plan.reason}")
    w(f"Theta           {_clock(plan.theta, inst.dt)} "
      f"({plan.theta} slots) to clear")
    # Reported with the term that binds rather than as a percentage gap: the
    # bound is admissible but not tight, so the ratio overstates the real
    # distance from optimal, which bench/ measures at zero on 21 of the 22
    # instances it can solve exactly. Which obstruction binds is the useful
    # part.
    w(f"Lower bound     {_clock(plan.lower_bound, inst.dt)}"
      f"{', set by ' + plan.binding if plan.binding else ''}")
    w(f"Rounds used     {plan.rounds_used}")

    if plan.unplaced:
        w("")
        w(f"LEFT BEHIND     {len(plan.unplaced)} group(s): "
          f"{', '.join(plan.unplaced)}")

    if plan.moves:
        w("")
        w("Movement plan (R and A), in order of departure")
        w(f"  {'group':<14} {'cls':>3} {'n':>2} {'start':>6} "
          f"{'arrive':>6} {'escorts':>7}  route")
        for m in sorted(plan.moves, key=lambda m: (m.start, m.arrival)):
            escorts = ",".join(str(i) for i in m.escorts) or "-"
            w(f"  {m.group:<14} {m.cls:>3} {m.size:>2} "
              f"{_clock(m.start, inst.dt):>6} "
              f"{_clock(m.arrival, inst.dt):>6} {escorts:>7}  "
              f"{' -> '.join(m.path)}")

        dest: dict[str, int] = {}
        for m in plan.moves:
            dest[m.destination] = dest.get(m.destination, 0) + m.size
        w("")
        w("Where they end up")
        for v, n in sorted(dest.items(), key=lambda kv: -kv[1]):
            node = inst.vertices[v]
            cap = "unlimited" if node.capacity == float("inf") else int(node.capacity)
            w(f"  {v:<10} {n:>3} patients  (kappa {cap})  {node.label}")

    if plan.bottlenecks:
        w("")
        w("Bottleneck arcs (B) -- what the estates team should widen")
        for a in plan.bottlenecks:
            w(f"  {a.u} -> {a.v:<8} {a.kind:<9} {a.clear_width_mm} mm, "
              f"{a.capacity} width units per slot")

    if show_verify:
        rep = verify(inst, plan)
        w("")
        w(f"Verification    {'PASS' if rep.ok else 'FAIL'}")
        if not rep.ok:
            w(str(rep))
    return "\n".join(out)


def as_json(inst: Instance, plan: Plan) -> dict:
    return {
        "ward": inst.name,
        "phi": plan.phi,
        "theta_slots": plan.theta,
        "theta_seconds": plan.clearance_seconds,
        "lower_bound_slots": plan.lower_bound,
        "lower_bound_set_by": plan.binding,
        "reason": plan.reason,
        "unplaced": plan.unplaced,
        "routes": [
            {
                "group": m.group, "class": m.cls, "size": m.size,
                "origin": m.origin, "destination": m.destination,
                "path": m.path, "start": m.start, "arrival": m.arrival,
                "escorts": m.escorts,
            }
            for m in sorted(plan.moves, key=lambda m: m.start)
        ],
        "flow": [
            {"arc": arc, "class": k, "slot": t, "patients": n}
            for (arc, k, t), n in sorted(plan.flow.items())
        ],
        "bottlenecks": [
            {"u": a.u, "v": a.v, "kind": a.kind,
             "clear_width_mm": a.clear_width_mm, "capacity": a.capacity}
            for a in plan.bottlenecks
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scarf",
        description="Plan a hospital ward evacuation with SCARF.")
    ap.add_argument("scenario", nargs="?",
                    help="path to a scenario JSON file")
    ap.add_argument("--ward", help="use a built-in ward instead")
    ap.add_argument("--list", action="store_true",
                    help="list the built-in wards and exit")
    ap.add_argument("--rounds", type=int, default=3,
                    help="critical-arc rounds (default 3)")
    ap.add_argument("--max-wait", type=int, default=None,
                    help="cap how long a group may queue for an arc, in "
                         "slots (default: the horizon)")
    ap.add_argument("--verify", action="store_true",
                    help="re-check the plan against every rule")
    ap.add_argument("--json", action="store_true",
                    help="emit the plan as JSON")
    args = ap.parse_args(argv)

    try:
        from data.wards import WARDS
    except ImportError:
        WARDS = {}

    if args.list:
        for name in sorted(WARDS):
            print(name)
        return 0

    if args.scenario:
        inst = load(args.scenario)
    elif args.ward:
        if args.ward not in WARDS:
            ap.error(f"unknown ward {args.ward!r}; "
                     f"try one of {', '.join(sorted(WARDS))}")
        inst = WARDS[args.ward]()
    else:
        ap.error("give a scenario file or --ward")

    plan = Scarf(inst, max_wait=args.max_wait).solve(rounds=args.rounds)

    if args.json:
        print(json.dumps(as_json(inst, plan), indent=2))
    else:
        print(render(inst, plan, show_verify=args.verify))
    return 0 if plan.phi else 1


if __name__ == "__main__":
    sys.exit(main())
