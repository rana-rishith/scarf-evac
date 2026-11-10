# Specification: rules, conditions, constraints

This is the contract the planner is held to. Every rule here is checked
independently by `scarf/verify.py` against the emitted plan, not merely
assumed by the solver. Section 4 is the part worth reading twice: the cases
that do not show up on a happy path.

## 1. Scope

In scope: the routing and scheduling decision. Given a ward graph, a census by
mobility class, the staff on duty and a hazard, produce a movement plan.

Out of scope, by decision: device integration. No fire panel, no RFID bed
tags, no nurse-call feed, no PA system. The planner is given a complete and
truthful snapshot and is assumed to keep it. Concretely we assume:

- every sensor and tag that would feed the snapshot has already been read;
- those devices stay responsive for the whole evacuation, so no input goes
  stale or missing mid-plan;
- a patient's mobility class, a corridor's clear width and a door's state are
  known exactly;
- staff do what the plan says.

The one dynamic input we do model is the hazard, because a plan that ignores
a spreading fire is not a plan. See `Scarf.replan`.

## 2. The instance

`I = (G, p, s, tau, rho, pi, c, kappa, alpha, h, theta, T)`, as
`scarf.model.Instance`.

### 2.1 Graph

`G = (V, E)` directed. A vertex is a real place: a bay, a nurses' station, a
corridor junction, a stair landing, a lift lobby. An arc is **one passable
direction** of one corridor, door, ramp or stair. Both directions are stored
separately so the hazard can block one side while the other stays open.

`V` is partitioned into occupied, transit, refuge and exit vertices. A refuge
is in an adjacent fire compartment; an exit is open ground.

### 2.2 Time

Time is integer **slots** of `dt` seconds, default **30 s**. That default is
not arbitrary. The problem statement says a ward corridor passes about ten
walking adults a minute and about two beds. Both are whole numbers of
patients per 30 s, and their ratio fixes the width cost of a bed at five
walkers. Every other quantity follows from that choice.

### 2.3 Mobility classes

`K = {1, 2, 3}` = ambulant, wheelchair or assisted, bed or stretcher.

| | `rho(k)` speed penalty | `alpha(k)` escorts | width units | min clear width | group size |
|---|---|---|---|---|---|
| 1 ambulant | 1.0 | 0 | 1 | 700 mm | up to 8 |
| 2 assisted | 1.6 | 1 | 2 | 850 mm | 1 |
| 3 bed | 2.5 | 2 | 5 | 1100 mm | 1 |

Escorted patients move one at a time because each one holds its own pair of
hands. Walkers move in batches.

### 2.4 Capacity from clear width

`c(e) = max(1, round(width_m * 0.0775 * dt))` width units per slot.

The coefficient is **0.0775 persons per metre per second**, which is far below
the SFPE free-flow figure of about 1.3. That is deliberate: ward evacuation is
escorted and orderly, not a panic flow, and 0.0775 is the value implied by the
problem statement's own numbers. It gives:

| opening | clear width | `c(e)` | meaning |
|---|---|---|---|
| bed-moving corridor | 2150 mm | 5 | 10 walkers or 2 beds a minute |
| double-leaf fire door | 1800 mm | 4 | |
| escape stair | 1200 mm | 3 | |
| bed-access door leaf | 1100 mm | 3 | a bed holds it for two slots |
| wheelchair door | 850 mm | 2 | no bed fits |

### 2.5 Passability `pi(e, k)`

Derived from width and kind, and overridable per arc:

- **lift: nothing fits.** The alarm shuts the lifts down. The lift arc is kept
  in the graph on purpose, so a planner that forgets this is caught.
- **stair:** class 1 yes; class 2 only at 1000 mm or wider, and then with one
  extra pair of hands for the evacuation chair; class 3 never.
- **corridor, door, ramp:** by the minimum clear width in the table above.

### 2.6 Hazard

Monotone: fire does not un-burn. The front leaves each source at its `start`
slot and crosses an arc in `tau(e) * spread_factor * resistance` seconds.
`spread_factor` defaults to 60, putting the front at about 1.2 m per minute,
a growing compartment fire. Resistance is 1 for a corridor, 5 for a stair,
**10 for a door** and 20 for a lift shaft — the resistance of the compartment
line is the whole reason a refuge counts as somewhere safe to put a bed.

Because the hazard is monotone, the field `h : V x T -> [0, 1]` with threshold
`theta` collapses to one scalar per vertex: `deadline[v]`, the first slot at
which `v` is unusable, minus a `safety_margin` of whole slots. This collapse
is what keeps the router out of the time-expanded graph.

## 3. The rules a plan must obey

A plan `O = (phi, R, A, f, Theta, B)` is valid only if all of these hold.
The numbering matches `scarf/verify.py`.

1. **Arc flow within capacity.** For every physical opening and slot,
   `sum over k of f(e, k, t) <= c(e)`, counted in width units. Both
   directions of one corridor are charged to **one shared budget**: storing
   the directions separately must not let a plan push `2c` patients through a
   corridor rated `c`.
2. **Standing room.** No transit vertex or bay holds more than `kappa(v)`
   people at any slot, counting patients and their escorts. A group waiting at
   a junction occupies it for the whole wait. Refuges and exits are **not**
   governed by this rule — see rule 6.
3. **Conservation.** Patients are neither created nor lost. Every patient is
   either delivered to a refuge or an exit, or named in `unplaced`. `phi` is
   TRUE only if `unplaced` is empty.
4. **Hazard and fit.** Nobody is at `v` at or after `deadline[v]`, nobody is
   on an arc past its deadline **for the whole crossing** (not merely at the
   endpoints), and nobody uses an arc with `pi(e, k) = 0`.
5. **Staff.** A class-`k` group holds `alpha(k)` escorts from pickup to
   hand-over. An arc demanding extra hands demands them for the whole
   journey, taken as the maximum over the path. Staff committed never exceed
   staff on duty.
6. **Refuge capacity.** A refuge's `kappa` counts the **patients who stay**,
   and their places are consumed permanently. Escorts hand over and walk back
   out, so they are not charged standing room at the refuge, and neither is
   the patient charged twice. Applying rule 2 here as well would double-count
   the patient and make a refuge sized for its intended load unusable: a
   one-place refuge would reject a bed plus its two porters, which is a bug
   the exact solver in `bench/` caught.
7. **Escort continuity.** An escort is in one place at a time, and cannot be
   collected for the next pickup before walking there from the last
   hand-over. This is the rule the published models do not have.
8. **Release times.** A patient a doctor has not cleared does not move before
   `defer_until`.

Objective: minimise `Theta`, the last arrival, breaking ties on
acuity-weighted arrival `sum (1 + acuity(v)) * arrival * size`. The tie-break
matters clinically: two plans that finish together are not equally good if one
leaves the ICU beds until last.

When no plan clears everyone, the planner reports the one that **strands the
fewest patients**, and only then minimises `Theta`. Ordering on `Theta` alone
would prefer a plan that abandons two patients because it finishes earlier.

## 4. Hidden cases

These are the ones that bite. Each has a test named after it in
`tests/test_hidden_cases.py`, or in `bench/test_gap.py` for the two the exact
solver uncovered.

### Staffing

1. **The escort has to walk back.** After a hand-over the escort is standing
   at the refuge. The next bed patient waits for someone to walk back, and on
   a staff-poor ward that walk, not the corridor, sets the clearance time.
   Modelling staff as an abstract counter overstates how many patients a given
   roster can move.
2. **One nurse cannot move a bed patient.** `alpha(3) = 2`. If the whole ward
   has one member of staff, the answer is a clear infeasibility with a reason,
   not a loop or a crash.
3. **Escort starvation.** Every escort can end up at a far refuge while
   patients remain in a bay about to be lost. If the hands cannot arrive
   before `deadline[v]`, that group is reported unplaced rather than given an
   impossible journey.
4. **A ward of walkers needs no staff at all.** `alpha(1) = 0` must not force
   a spurious staffing requirement.
5. **Stairs cost an extra pair of hands.** A wheelchair user on a stair needs
   an evacuation chair. Whether to pull a third person off the floor to open
   the stair, or send the group the long way to a refuge, is a real decision,
   so the planner **tries both** and keeps the better one.

### Geometry

6. **A doorway a visitor walks through without noticing stops a trolley.** A
   bed is five width units; a 1100 mm door passes three per slot. The bed
   still fits, it just holds the door for two slots, and nobody else uses it
   meanwhile.
7. **Both directions of one corridor share one width.** See rule 1.
8. **The lift is dead.** A graph that includes lift arcs will yield a
   wonderful plan that cannot happen.
9. **A bed bay whose only egress is a stair.** `pi(e, 3) = 0` on every way
   out means those patients can only reach a horizontal refuge. If there is
   none, say so before routing anything.
10. **A one-way corridor.** The hazard blocks one side; the other still works.
11. **A refuge that is full is full.** Later patients must be sent to another
    one, and if every refuge is full the remainder are unplaced.
12. **A narrow junction does not overfill.** `kappa` at a stair landing or a
    lift lobby binds long before the corridor does.
13. **A refuge is sized for patients, not for porters.** Charging a refuge
    instantaneous standing room for the patient *and* both escorts makes a
    refuge sized for one bed reject that bed. The planner was stricter than
    its own specification here and silently routed everyone to a distant
    refuge instead; see rule 6.
14. **Who gets the near refuge.** Filling the nearest refuge first is not
    optimal when it is small: the first bed to arrive takes the one good
    place and a later bed with no alternative is sent the long way. The
    optimum gives the early departure slots to the *far* refuge, because
    early groups have the slack to absorb a longer trip, and leaves the near
    one for whoever departs last. Refuge places are therefore claimed in
    reverse dispatch order.

### Timing

15. **Caught in transit.** A corridor lost while a group is halfway along it
    is not usable. Checking only the endpoints is not enough.
16. **The bay where the fire starts.** This is the case that produces the
    sharpest finding and it is arithmetic, not a solver failure. Bay 1 of the
    28-bed medical ward is untenable three slots after the alarm, so its one
    1100 mm door passes `3 x 3 = 9` width units. Three walkers, two
    wheelchair users and one bed demand `3 + 4 + 5 = 12`. Someone is left.
    Leaving the bed costs one patient; leaving the two wheelchair users costs
    two. SCARF leaves the bed, which is the smallest possible loss.
17. **Heaviest-first is not always right.** Starting the beds first minimises
    clearance time, because they are slow and hold two staff each. Under a
    tight deadline it strands the lighter patients queued behind them. The
    planner tries both orders.
18. **Wait in your bay, not in the corridor.** An earliest-arrival route
    leaves groups parked at junctions for as long as the corridor ahead is
    busy, and those parked people consume standing room that starves everyone
    behind them. Every departure is slid as late as it will go without
    delaying any arrival, which moves the waiting back into the patients' own
    bay where they are already standing.
19. **A horizon shorter than the journey** is reported as infeasible within
    the horizon, which is a different statement from infeasible.
20. **Patients already in a refuge** are not counted in the census and not
    moved.
21. **A patient a doctor has not released.** `defer_until` is a hard release
    time.

### Input

22. **Malformed scenarios are rejected with a reason** at the trust boundary:
    unknown vertex kinds, duplicate ids, arcs to vertices that do not exist,
    non-positive crossing times, negative censuses, patients parked on a
    transit vertex, unknown mobility classes.
23. **An unbounded exit.** Open ground has no capacity; `null` means infinite
    and must not become zero.

## 5. Stated limitations

Honest list of what the model does not do.

- **Escort walk-back is not charged against corridor capacity.** Two nurses
  walking against a stream of beds is a rounding error next to the beds. It
  would matter only in a scenario with more staff than patients in one
  corridor.
- **One label per vertex in the search.** Exact whenever waiting at a vertex
  is never blocked by `kappa`, a heuristic otherwise. Keeping the `k` best
  labels per vertex would close the gap at `k` times the cost.
- **Monotone hazards only.** A leak that is contained and recedes needs the
  full `h(v, t)` field rather than one deadline per vertex.
- **Patients do not choose.** Everyone follows the plan. No milling, no
  families refusing to be separated, no staff improvising.
- **Flow is an entry-rate constraint**, as in the problem statement: `c(e)` is
  patients per unit time, not a count of bodies simultaneously in a corridor.
- **SCARF is a heuristic.** It can fail to find a feasible plan when one
  exists. It never emits an infeasible one: that is what `verify` is for.
- **Destination assignment is a heuristic**, not a matching. Claiming refuge
  places in reverse dispatch order is optimal on every instance checked
  against the exact solver, but it is a rule of thumb rather than a solved
  assignment problem, and a ward with many small refuges could still beat it.
- **How far from optimal is now measured, not guessed.** Against an exact
  solver on twenty-two instances of up to seven groups, SCARF reaches the
  optimum on every one. The lower bound averages 88% of the true optimum, so
  on large wards the plans are certified within 53-88% of optimal. See
  `docs/ALGORITHM.md` sections 3 and 5. Nothing is known above seven groups:
  one eight-group instance remains unresolved.
- **Escorts are sourced, not scheduled.** Each group takes the escorts who
  can reach it soonest, which is optimal for that group but not necessarily
  across groups. No instance currently penalises it, but it remains a greedy
  choice.
- **The bound models no standing room at junctions.** It is loosest exactly
  where that binds, around 67-75% of the optimum.
