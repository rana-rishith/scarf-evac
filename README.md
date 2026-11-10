# SCARF — hospital ward evacuation planner

SCARF is a routing algorithm for a hospital ward in a fire. Its name is
Staff-Coupled Acuity-Ranked Flow. It plans who leaves, by which route, at
which time, and with which staff.

You cannot empty a hospital in the way that you empty an office. Most patients
do not walk out without help. One patient needs a nurse. Another needs two
porters and a bed. The alarm stops the lifts. Therefore bed patients move
sideways through fire doors into the next compartment.

Two resources become critical before distance does. Door capacity is the
first. A door that passes ten walking adults in one minute passes
approximately two beds. Staff are the second. An escort stays with a patient
for the full journey and must then walk back.

## What is new

**SCARF keeps the position of each escort inside the routing label.**

Nurses recirculate. They deliver a patient, walk back, and collect the next
one. The cost of a journey therefore includes the walk back of the staff who
make it. Routing reacts to where the staff are. Most planners fix the routes
first and give the staff to them afterwards. Those planners cannot make this
choice.

The effect is visible in the output below. Escorts 9 and 10 deliver a patient
to the south refuge at 02:00. They then collect a different patient at 02:30.

## Install and run

SCARF needs Python 3.10 or later. It uses only the standard library at run
time.

```bash
pip install -r requirements.txt
```

Plan one ward:

```bash
python -m scarf.cli data/ward_icu_12.json
```

```
phi             FEASIBLE
Theta           07:30 (15 slots) to clear
Lower bound     04:00, set by escort work

  group          cls  n  start arrive escorts  route
  pod4#k3#2        3  1  00:00  02:00    9,10  pod4 -> c3 -> rs
  pod1#k3#0        3  1  00:00  02:00     1,2  pod1 -> c1 -> rn
  pod3#k3#0        3  1  02:30  04:30    9,10  pod3 -> c3 -> rs

Bottleneck arcs (B) -- what the estates team should widen
  pod2 -> c1       door      1100 mm, 3 width units per slot
```

Add `--json` for machine-readable output.

The last block is the useful part for a building owner. It gives the doors
that limit the evacuation.

## Results

Run the benchmark yourself:

```bash
python -m bench.gap
```

An exact solver finds the true best clearance time on small instances. It
completes 22 of the 23 benchmark instances. **SCARF gets the optimum on all
22.** The lower bound is 88 per cent of the true optimum on average. It is
exactly tight on 9 instances.

Therefore a plan on a large ward carries a certified gap. Run
`python -m bench.gap --wards` for this table.

| Ward | Patients | Staff | Clears | Clearance | Certified gap | Limit | Time |
|---|---|---|---|---|---|---|---|
| 12-bed ICU | 12 | 14 | yes | 7.5 min | 88% | escort work | 7 ms |
| 28-bed ward | 28 | 5 | no, 1 left | 17.5 min | — | escort work | 42 ms |
| two-floor block | 20 | 6 | no, 1 left | 7.5 min | — | escort work | 5 ms |
| generated, 240 beds | 240 | 40 | yes | 81.5 min | 60% | refuge intake | 0.6 s |
| generated, 960 beds | 960 | 160 | yes | 312 min | 58% | refuge intake | 12.4 s |

The `Limit` column is the actionable one. On a 28-bed ward with five staff at
night, no corridor is the limit. Five people cannot escort eleven wheelchair
patients and nine bed patients more quickly. A wider door gives no
improvement. One more pair of hands does.

Two wards do not clear. Those results are facts about the buildings. Bay 1 of
the 28-bed ward holds 12 width units behind one 1100 mm door. The door stays
usable for three slots, so 9 units can leave. SCARF strands one patient, which
is the smallest possible loss, and it names the door.

## How it works

The algorithm runs in five phases.

0. Compute a deadline for each place from the hazard, and a trip table for
   each mobility class.
1. Reject the instance if a patient cannot reach safety. Compute a lower bound
   from four physical limits.
2. Route the groups one at a time, in priority order, over a network that
   expands only when the search needs it.
3. Apply a penalty to the arcs that set the clearance time. Then route again.
   Keep the better plan.
4. Make a new plan when the hazard moves.

A time-expanded network needs more than 200,000 nodes in memory for the
largest ward here. SCARF never builds one. The horizon costs time, not memory.
The 960-patient ward uses 31.5 MB.

`scarf/verify.py` checks each plan on its own. It derives all eight rules from
the move list and shares no data with the solver. It found a real capacity
fault at 1920 patients.

## Tests

```bash
python -m pytest -q
```

The suite has 146 tests. 143 pass. Three need more exact-solver budget than
the quick run gives, so they skip.

## Layout

```
scarf/       the planner. No run-time dependencies.
  model.py     domain model and input validation
  ledger.py    hazard deadlines, capacity ledger, escort pool
  solver.py    SCARF, phases 0 to 4
  verify.py    independent checker for a finished plan
  scenario.py  JSON load and dump
  cli.py       text and JSON output
data/        three published ward layouts and a ward generator
bench/       exact solver, 23 small instances, comparison tables
tests/       rule tests and 23 boundary cases
docs/        specification, algorithm notes, technology choices
```

## Limits

Read these before you trust a number.

- SCARF is a heuristic. The problem is NP-hard. We prove no bound on how far
  from optimal a plan can be.
- The method gets the optimum on all 22 instances that an exact solver can
  complete. Those instances are small. They are not a guarantee.
- One instance with eight patient groups has no answer at eight million search
  nodes. We know nothing about quality above seven groups.
- SCARF can report that a ward does not clear when a plan exists. It never
  emits a plan that breaks a rule.
- No drill has tested this work. Each clearance time depends on the published
  design figures in `docs/SPEC.md`. Use the tool to compare buildings and
  staffing levels. Do not use it to predict a real evacuation time.
