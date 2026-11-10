# Tech stack

## Summary

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.10+ (developed on 3.14.3) | Readable algorithm code; the graph work is pointer-chasing, not number-crunching |
| Runtime dependencies | **none** | Everything the solver needs is in the standard library |
| Test framework | pytest 9.1 | Parametrised cases map cleanly onto the rule table |
| Interface | `argparse` CLI + importable library | A planner is used from a script far more often than from a prompt |
| Data format | JSON | A scenario is a list of places and a list of openings; nothing needs a binary format |

Install and run:

```bash
python -m pytest tests -q
```

```bash
python -m scarf.cli --ward medical28 --verify
```

## Standard library only, and why that is deliberate

The whole solver imports `heapq`, `math`, `dataclasses`, `enum`, `json`,
`argparse`, `copy`, `random` and `pathlib`. Nothing else. `pip install` is not
required to run it.

Three libraries were considered and deliberately not used.

**networkx** was the obvious candidate and does not fit. The core operation is
not "shortest path on a weighted graph", it is "earliest feasible crossing of
this arc given what is already reserved in each 30-second slot, who is
standing at the far end, and when the fire gets here". That predicate lives in
`Scarf._hop` and consults a sparse reservation ledger; no library shortest-path
call can express it. Using networkx would mean wrapping a graph we would then
have to bypass for every real decision, and it would add a dependency to save
roughly thirty lines of Dijkstra.

**numpy** buys nothing here. The reservation ledger is deliberately sparse —
dictionaries keyed on `(place, slot)` — because that is what avoids the
time-expanded blow-up a dense array would reintroduce. A dense
`arcs x classes x slots` array is exactly the `O((n+m)|T|)` memory the
algorithm is designed to avoid.

**An LP or MILP solver** (PuLP, OR-Tools, Gurobi) is the standard way to solve
quickest-flow exactly, on a time-expanded network. It is not used because the
exact problem is NP-hard once escorts are held for a journey's duration (see
`docs/ALGORITHM.md` section 2), so the solver would not give optimality in
reasonable time on realistic instances anyway, and the combinatorial heuristic
runs in milliseconds where a MILP would take minutes. A small MILP is still
the right tool for *measuring* the optimality gap on five-patient instances,
which is listed as the first next step.

## Layout

```
scarf/                 the library, no third-party imports
  model.py             Instance, Vertex, Arc, Mobility, Kind; physical
                       parameters; capacity and pi(e,k) derived from clear
                       width; input validation
  ledger.py            hazard deadlines, the sparse capacity ledger, and the
                       escort pool with recirculation
  solver.py            SCARF itself: phases 0-4
  verify.py            independent re-check of all eight rules
  scenario.py          JSON load, dump and round-trip
  cli.py               command line front end
data/
  wards.py             three hand-encoded wards and a parametric generator
  *.json               the same wards exported, as a schema reference
tests/
  conftest.py          small purpose-built instances
  test_rules.py        the rule table, 38 tests
  test_hidden_cases.py the cases that bite, 37 tests
bench/                 research tools, not part of the planner
  tiny.py              23 instances small enough to solve exactly
  exact.py             exact solver by iterative deepening, for the real
                       optimality gap
  gap.py               prints the comparison tables (python -m bench.gap)
  test_gap.py          asserts the bound is admissible, tight enough to
                       quote, and that SCARF stays optimal wherever it is
docs/
  SPEC.md              rules, conditions, constraints, hidden cases
  ALGORITHM.md         the algorithm, complexity, novelty review, results
  TECH_STACK.md        this file
```

## Design choices worth defending

**Integer time slots, not floats.** Every time is an integer count of 30 s
slots. Capacity is per slot, so a float clock would need interval arithmetic
on reservations and would make the ledger ambiguous at boundaries. The slot
length is a parameter; 30 s is the default because the problem statement's own
figures — ten walkers or two beds a minute down a 2.15 m corridor — are whole
numbers of patients per 30 s.

**Width units, not patient counts.** Arc capacity is in width units per slot,
a walker costs one and a bed costs five. This is what lets one number, `c(e)`,
derived from clear width, correctly carry mixed traffic, and it is what makes
"a bed holds a 1100 mm door for two slots" fall out rather than be special-cased.

**Capacity and passability derived, not declared.** A scenario file states the
clear width in millimetres and the arc kind. `c(e)` and `pi(e, k)` are computed
in `Arc.derive`. A scenario should never have to state a capacity that its
author would have to guess at, and a floor plan gives widths directly.

**Dataclasses over dicts** for the domain model, so a typo in a field name is
an error rather than a silently missing key, and so the model reads as the
specification.

**The verifier is separate code.** `verify.py` re-derives every constraint
from the move list alone and shares no bookkeeping with the solver. A
heuristic is only trustworthy if its output is checked rather than assumed; if
the two ever disagree, the test suite says so. This caught a genuine capacity
violation at 1920 patients that no amount of reading the solver would have
found.

**No device layer.** Integration is assumed done and the devices assumed
responsive, per the project scope. The planner takes a snapshot and returns a
plan. That keeps the whole thing a pure function of its input, which is why it
is testable at all.
