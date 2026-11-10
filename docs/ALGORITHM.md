# SCARF: Staff-Coupled Acuity-Ranked Flow

A heuristic planner for hospital ward evacuation, built for this project.

## 1. What is new here

The one-sentence claim: **SCARF treats escorts as a mobile resource whose
current position is part of the routing decision, so the walk back for the
next patient is priced into the plan rather than assumed away.**

Everything else in SCARF is assembled from known ideas. That one thing is not
in the literature, and it is the thing that changes the answers.

### Why it matters

Published evacuation models treat helpers as a counter: a pool of `S`
assistants, decrement on assignment, increment on release. But an escort is
not a unit of an abstract pool. After handing a bed patient over at the east
refuge, two porters are *standing at the east refuge*. The next bed patient in
a west bay waits for someone to walk the length of the ward. On a ward with
five staff and eight bed patients, that walk, not the corridor width, sets the
clearance time.

`tests/test_hidden_cases.py` pins the behaviour. Two wheelchair patients 60 m
apart sharing one escort: the second cannot start until the escort has walked
`r -> c -> b`, three slots after the first hand-over. Add a second nurse and
the clearance time drops. A counter-based pool shows no difference.

### Review of prior work

Searched across the quickest-flow, capacity-constrained-routing, scheduling
and simulation literatures. Four families exist, and the gap between them is
where SCARF sits.

| Family | Representative work | What it gives | What it lacks |
|---|---|---|---|
| Flows over time | Ford & Fulkerson; Hoppe & Tardos (quickest transshipment); Baumann & Skutella (earliest arrival, multiple sources) | Optimal, polynomial for the single-commodity case | One commodity, no resources, no hazard. Needs a time-expanded network of at least `(n+1)T` nodes |
| Capacity-constrained routing | Lu, George & Shekhar (CCRP, SSTD 2005); Shekhar et al. (IJGIS 2012); SSEP | Scales; capacity as a time series; avoids full time expansion | One evacuee class, no escorts, static hazard, nearest-shelter logic can overfill a shelter |
| Resource-constrained scheduling | Poppenborg & Knust, *Modeling and optimizing the evacuation of hospitals based on the MRCPSP with resource transfers* | Hospital evacuation with assistants and aids as resources; closest prior art | **Decomposes** scheduling from routing and solves them as separate subproblems; no corridor flow capacity as a time series; no spreading hazard |
| Simulation | HEPTAD (Joyce 2021); FDS+Evac; multi-hazard agent-based hospital models; BB_Evac | Behavioural realism, validated against drills | Descriptive, not optimisation. Answers "how long did that take", not "what should we do" |

The two pieces SCARF needs come from different literatures that do not cite
each other: quickest flow has the capacity-and-time machinery but no notion of
a helper, and the MRCPSP line has helpers but hands routing to a separate
subproblem. Searches for the specific mechanism — an escort returning to
collect the next patient, escort position inside a routing label — returned
nothing. The nearest thing found was a staged multi-zone Dijkstra that
updates each exit's occupancy time as zones expand, which is a dispatch loop
but is not about escorts.

**Honest scope of the novelty claim.** This is a literature review, not a
proof of absence. The claim is specific and falsifiable: no published
evacuation algorithm found couples escort position into the routing label so
that the return journey is scheduled. The surrounding components — lazy time
expansion, deadline-first priority, critical-arc re-dispatch — are each
recognisable; their combination for this problem is new, and the staff
coupling is the part that is genuinely unlike prior work.

## 2. Complexity of the exact problem

The exact problem is NP-hard, which is why SCARF is a heuristic.

**Sketch.** Strip the geometry: one bay, one refuge, one corridor of
unbounded width, no hazard, no deadlines. Each class-`k` patient becomes a job
of duration `tau * rho(k)` requiring `alpha(k)` escorts *simultaneously* for
its whole duration. Minimising `Theta` is then exactly minimising makespan for
rigid parallel-machine scheduling, `P|size_j|C_max`, which is strongly
NP-hard. Restoring corridor capacity and multiple classes additionally embeds
multi-commodity flow over time, itself NP-hard. So a polynomial exact
algorithm would be surprising, and the useful questions are how good the
heuristic is and how fast.

## 3. The algorithm

Five phases. Code in `scarf/solver.py`.

### Phase 0 — Precompute

```
deadline[]  <- Dijkstra from every hazard source on the undirected graph,
               arc cost tau(e) * spread_factor * resistance(kind),
               minus safety_margin                   O((n+m) log n)
walk[v]     <- reverse Dijkstra from each pickup vertex v over arcs with
               pi(e,1), giving the walk time to v from anywhere
                                                     O(|Vp| (n+m) log n)
groups      <- split the census per vertex per class into dispatchable
               groups; escorted patients singly, walkers in batches   O(P)
```

Collapsing the hazard field to one `deadline[v]` per vertex is what lets the
router stay out of the time-expanded graph. It is valid because the hazard is
monotone. `walk[]` is computed once per pickup point rather than per dispatch,
because it is consulted every time an escort is sourced.

### Phase 1 — Pre-screen

Cheap necessary conditions, so an impossible instance fails fast with a reason
instead of after a full routing attempt.

```
if no refuge and no exit                -> infeasible
if alpha(k) * size > total staff         -> infeasible, name the group
for each class k present:
    union-find over arcs with pi(e,k)    -> O(m a(n)) necessary screen
    reverse BFS from refuges and exits   -> O(n+m) exact check
    any occupied vertex not reaching safety, or already lost -> stranded
```

Union-find gives connectivity in the underlying undirected graph: a necessary
condition that fails fast on the common case of a bed-bound bay whose only way
out is a staircase. The reverse BFS then honours arc direction, which
union-find cannot.

**Lower bound** on `Theta`, reported with every plan, is the largest of four
independent obstructions. It is admissible — `bench/test_gap.py` asserts it
against the true optimum rather than arguing for it — and it averages 88% of
that optimum, exactly tight on 9 of the 22 instances solved exactly.

1. **Travel.** The furthest patient still has to make the journey, and cannot
   start before a doctor releases them.

   ```
   Theta >= max over groups of ( defer_until(v) + trip(k, v) )
   ```

   `trip(k, v)` is the fewest slots a lone class-k patient can get from `v` to
   any refuge or exit, over arcs it fits through, where crossing one arc costs

   ```
   max( ceil(tau(e) * rho(k) / dt),  ceil(width_units(k) / c(e)) )
   ```

   The second half of that is what matters. Walking speed is only half the
   story: a bed is five width units and a 1100 mm door passes three per slot,
   so the bed holds that door for two slots however fast the porters walk. An
   earlier bound used walking speed alone and claimed a one-slot journey for
   an ICU bed that really takes four, which is most of why it was useless.

2. **Escort work.** The escort-slots demanded cannot be supplied faster than
   the roster supplies them: the area bound for rigid jobs on parallel
   machines. Each delivery counts one way, plus the walks back that are
   unavoidable — every escort's *last* delivery needs no return, and at most
   `|S|` deliveries can be somebody's last.

   ```
   Theta >= ceil( ( sum alpha(k)*size*trip + max(0, sum alpha - |S|) ) / |S| )
   ```

3. **Bay egress.** A bay empties no faster than its own doorways pass people.
   Six beds behind one 1100 mm door is a queue before it is anything else.

   ```
   Theta >= max over bays of
            ( defer_until(v) + ceil(D(v) / C(v)) - 1 + min_k trip(k, v) )
   ```

4. **Refuge intake.** Everyone has to get *in* somewhere, and every refuge and
   exit is reached through a known set of doors whose total width is fixed.

   ```
   Theta >= ceil(D_total / C_in) - 1 + min final hop
   ```

Terms 3 and 4 share one argument: if a set of doorways passes at most `C`
width units per slot and `D` units must cross them, they are busy for at least
`ceil(D / C)` slots, so the last crossing starts no earlier than
`ceil(D / C) - 1`, and the journey either side of it still has to happen.

Every term understates wherever it is unsure, which is what keeps the whole
thing admissible: capacities are summed per physical corridor without checking
which classes fit, `D` is counted in bare width units so span rounding is
ignored, and the earliest crossing is assumed to be slot zero.

All four earn their place — each is the sole binding obstruction on at least
one instance, which `bench/test_gap.py` also asserts, so a term that stops
mattering gets deleted rather than carried. Which one binds is the useful
output: it is the answer to "what is actually limiting this ward", and the CLI
prints it. Escort work binds on the staff-poor built-in wards, bay egress
where a bay has one narrow door, refuge intake on the largest generated wards,
and travel on long wings where nothing else bites.

### Phase 2 — Dispatch

```
for each group in priority order:
    need    <- alpha(k) * size
    ready, escorts <- source the need-earliest-arriving free staff at the
                      pickup point, using walk[]            O(|S| log |S|)
    hops    <- staff-coupled earliest-arrival search from (vertex, ready)
    hops    <- slide every departure as late as it will go
    commit: reserve corridor slots, junction standing room, refuge places,
            and move the escorts to the destination, free from arrival
```

**Priority** is `(deadline[v], -acuity, weight, id)`: the bay about to be lost
first, then the sickest, then by weight. The weight direction is tried both
ways (see phase 3).

**Staff sourcing** picks the `need` escorts who can be standing at the pickup
point soonest, which is optimal for a single pickup: any other set of the same
size arrives no earlier. If the `need`-th earliest arrival is after
`deadline[v]`, the hands cannot get there in time and the group is reported
rather than given an impossible journey.

**Two attempts per group.** First with the base escorts, with extra-escort
arcs (stairs) forbidden. Then, if any class-feasible arc demands extra hands,
again with those hands sourced and the stairs open. Whether to pull a third
person off the floor to open the staircase, or send the group the long way to
a refuge, is a real ward decision; the planner costs out both and takes the
better.

#### The search

Labels are `(vertex, slot)` pairs, but **no time-expanded copy of the graph is
ever built**. The slot rides on the label and feasibility is asked of a sparse
reservation ledger as each arc is relaxed. One label is kept per vertex, its
earliest feasible arrival.

Each arc relaxation (`Scarf._hop`) finds the earliest feasible crossing by one
sweep over departure slots, handling every reason a departure can be refused
in one place: corridor width, standing room while waiting on this side,
standing room on arrival, the horizon, and the hazard front. Handling them
together matters — an earlier version advanced the departure slot for a full
corridor but not for a full junction, so a group held up by a crowded junction
abandoned the arc instead of waiting for it, and bays next to a refuge ended
up unable to move at all.

The heap is keyed on arrival **plus accumulated arc penalties**, with the true
arrival times carried alongside, so phase 3 can steer the search away from the
binding arcs without corrupting the schedule it reports. With no penalties the
key is the true arrival and the search is ordinary Dijkstra.

#### Waiting in the right place

An earliest-arrival route leaves a group parked at a junction for as long as
the corridor ahead is busy. A junction holds ten people; parked groups consume
that standing room for the whole wait and starve everyone behind them.

So after the search, every departure is slid as late as it will go
(`Scarf._compress`), bounded by the departure of the hop after it. Nobody
arrives later — the last hop is pinned — and the waiting moves back into the
patients' own bay, where they are already standing and cost nobody anything.

The slide is all or nothing, validated as a whole before being adopted. A hop
that cannot move while the hop after it has already moved widens the wait
between them into slots nobody checked; that was a real capacity violation
caught by `verify` at 1920 patients.

#### Who gets which refuge

Before any group is routed, refuge places are claimed in **reverse** dispatch
order (`_claim_refuges`).

Dispatching forwards and letting each group take the nearest refuge with a
place left is first-come-first-served, and it is beatable whenever a near
refuge is small: the first bed to arrive takes the one good place, and a later
bed that had no alternative is sent the long way. Measured against the exact
solver that cost 33% on an instance where simply swapping the destinations is
optimal.

Claiming in reverse fixes it in one pass. The group dispatched last has the
least slack, so it should be left the shortest journey; equivalently the early
groups, which have slack to absorb a longer trip, take the far refuges. It is
the destination-assignment form of giving the long jobs the early start times.

The claim is a preference, not a constraint. The router tries the claimed
refuge first and falls back to a free choice only if that refuge cannot be
reached once capacity and the hazard are accounted for — never because a free
choice would arrive sooner, since accepting a longer trip for this group so a
later one finishes earlier is the entire point.

### Phase 3 — Critical-arc re-dispatch

```
for claim in (reverse-claim, first-come):        # independent descents
    penalties <- {}
    for r in 1..R:
        plan <- best of dispatch(penalties, heavy-first),
                        dispatch(penalties, light-first)
        plan <- repair(plan)                     # promote whoever finished last
        keep plan if better than this mode's best
        stop if phi and Theta <= lower bound     (provably optimal)
        B <- arcs saturated during the interval that fixes Theta
        stop if B is empty
        penalties[a] += 1 for each a in B
return the better of the two modes' plans
```

`B` is the bottleneck set the estates team asked for, and it comes out of the
plan for free rather than being computed separately. Penalising it and
re-dispatching is a descent: the best plan is kept, so quality never regresses
with more rounds.

Both dispatch orders are tried because which one wins is instance dependent.
Heaviest-first is the makespan instinct — beds are slow and hold two staff, so
they set the clearance time, and starting them while every nurse is still
nearby is longest-processing-time-first. It is the wrong instinct under a tight
deadline: a bed holds a 1100 mm bay door for a full minute, and anyone queued
behind it in a bay about to be lost does not get out at all.

Each claim mode gets its **own** descent with its own penalties. Sharing one
penalty dictionary between them was tried and dropped: the penalties are
updated from whichever variant is currently winning, so the losing mode never
receives the penalties that would have let it improve, which cost 5% on a
960-patient ward. The two descents are independent searches and the better
finished plan is returned.

#### The order repair

A dispatch order is a single ranking, and one ranking cannot express *pair
these two and send them together*. On a five-bay two-nurse ward the optimum
does exactly that -- the two furthest patients travel at the same time at the
end -- and neither heaviest-first nor lightest-first reaches it, so SCARF was
22% above optimal there however the priority was tuned.

The repair is three lines. Take the group whose arrival *is* `Theta`, move it
to the front of the dispatch order, and re-dispatch:

```
repair(plan):
    last <- the group arriving at Theta
    trial <- dispatch(penalties, same order, promote=[last] + already promoted)
    return the better of trial and plan
```

Promoted groups accumulate across rounds, so a plan needing two groups moved
forward is reached in two rounds. It is a descent like the penalty loop: the
trial is adopted only if it compares better, so quality cannot regress.

The reasoning is that whoever finishes last is the group that most needed to
start earlier. That is not always true -- a group may finish last because its
journey is long, not because it started late -- which is why the result is
checked rather than assumed.

It closed the last measured gap, and then turned out to be a general
improvement rather than a fix for one instance: the 12-bed ICU dropped from
17 slots to 15, the two-floor block likewise, and three generated wards
improved by one to two slots. The cost is one extra dispatch pass per round,
about 1.5x the runtime. `test_the_order_repair_earns_its_place` asserts it
still improves at least one real ward and makes none worse, so it gets
deleted the day it stops paying.

Plans are compared on **patients stranded first, then `Theta`, then
acuity-weighted arrival**. Comparing on `Theta` alone would prefer a plan that
abandons two patients because it finishes earlier.

### Phase 4 — Re-plan

When the hazard picture changes, journeys already completed are kept, everyone
else is returned to their origin, escorts are placed wherever the completed
journeys left them, and the residual instance is solved. A fire spreading into
one wing does not cost a full re-solve of the ward.

## 4. Complexity

With `n` vertices, `m` arcs, `P` patients in `G` groups, `S` staff, `|Vp|`
occupied vertices, `W` the longest a group may queue (default: the horizon),
and `R` rounds:

| Phase | Time |
|---|---|
| 0 deadlines | `O((n + m) log n)` |
| 0 walk tables | `O(|Vp| (n + m) log n)` |
| 1 pre-screen | `O(m a(n) + n + G)` |
| 2 one search | `O(m W + n log n)` |
| 2 one dispatch pass | `O(G (S log S + m W + n log n))` |
| 3 whole solve | `O(|Vp|(n+m) log n + R G (m W + n log n))` |

Space: `O(n + m)` for the graph plus `O(G L)` sparse reservation entries for
`L`-hop paths.

**The point of the design.** A time-expanded formulation needs at least
`(n+1)|T|` nodes in memory before it starts. SCARF never materialises one:
`|T|` enters only as the bound `W` on a per-arc slot scan, and only in time.
Capping `W` trades plan quality for speed and strands nobody who could not
have waited anyway.

## 5. Guarantees, and how far from optimal

**What holds.**

- Every plan SCARF emits satisfies all eight rules. This is not assumed:
  `scarf/verify.py` re-derives every constraint from the move list alone,
  using none of the solver's bookkeeping, and it is asserted on every test
  instance.
- The lower bound is admissible, and useful. Not by argument —
  `bench/test_gap.py` asserts it against the true optimum on every instance
  solved exactly. It averages 88% of that optimum and is exactly tight on 9
  of 22, which is what lets the large-ward plans be certified as within
  53-88% of optimal rather than left unquantified.
- Infeasibility conclusions from phase 1 are sound, because they are
  necessary conditions.
- More rounds never produce a worse plan.
- Deterministic: the same instance gives the same plan.

**What does not hold.**

- **Not complete.** SCARF can report infeasible when a plan exists. Three
  reasons: the greedy priority order, one label per vertex in the search, and
  a finite `W`. It never emits an *infeasible* plan, which is the direction
  that matters.
- **Not optimal in general** -- the problem is NP-hard and this is a
  heuristic. It reaches the optimum on **all twenty-two** instances that can
  currently be solved exactly, which is a measurement rather than a
  guarantee: one eight-group instance is still unresolved. See the
  measurement below.

### The exact solver

`bench/exact.py` finds the true minimum clearance time by iterative
deepening: for each target `Theta` from the lower bound upwards, ask whether
any plan clears everyone by then, and return the first target that answers
yes.

"Any plan" is meant literally. For each group it enumerates every simple route
to a refuge or exit, and for each route every schedule of per-hop departure
slots — so deliberate waiting part-way along a route is included, which a
restricted policy would miss. Escort assignment is then solved exactly over
the chosen schedules, with interchangeable staff canonicalised so the same
situation is never explored twice. Pruning comes from four places: the target itself,
since a hop is never tried at a slot that cannot leave room for the rest of
the route; symmetry breaking between interchangeable groups; forward checking
with fail-first variable ordering; and escort feasibility checked on the
groups assigned so far rather than only at the leaf. Together those take the
reachable size from four groups to seven and cut node counts by an order of
magnitude -- see section 6.

It reuses `Scarf` for the hazard deadlines, walk tables and group splitting,
so both solvers are judged on identical inputs, and its plans go through
`scarf.verify` like any others. Only small instances finish; a node budget
turns an overrun into an honest skip rather than a wrong number, and one
eight-group instance is reported unsolved rather than guessed at.

### What it found

Three real defects, none of which reading the solver would have revealed.

1. **The lower bound was not admissible.** It charged a return walk for every
   escorted delivery, but each escort's *last* delivery needs no walk back. On
   a one-patient ward it claimed 4 slots where 3 is achievable — the "exact"
   solver reporting a worse answer than SCARF is how the bug announced itself.
   The corrected bound counts deliveries one way plus only the returns that
   are unavoidable: at most `|S|` deliveries can be somebody's last, so at
   least `sum alpha - |S|` of the (escort, delivery) pairs are followed by a
   walk back.
2. **A refuge was charged standing room for its own escorts.** `_hop` applied
   rule 2 at the destination, counting the patient plus both porters against a
   refuge capacity that means *patients who stay*. A one-place refuge
   therefore rejected a bed, and SCARF silently sent everyone to a distant
   refuge instead. The solver was stricter than its own specification, and
   `verify` had never caught it because rule 6 is what governs refuges. See
   SPEC rule 6.
3. **Filling the nearest refuge first is not optimal when it is small.** The
   first bed to arrive takes the one good place and a later bed is sent the
   long way, which cost 33% on `split_destinations` and 25% on `two_refuges`.
   Fixed structurally by claiming refuge places in reverse dispatch order,
   which also improved the 28-bed ward from 37 slots to 35 and balanced its
   two refuges at 14 and 13 instead of filling one and overflowing.

   An arc penalty on refuges that had filled up was tried first. It fixed
   `two_refuges` but not the general case, and once reverse claiming was in
   place it stopped changing any answer at all, so it was deleted. Both the
   keeping and the deleting were decided by measurement: the suite asserts a
   mechanism still earns its place, and that one stopped earning it.
4. **Ranking groups by mobility class misses half the story.** Among five
   equally sick wheelchair patients the class tie-break left the order to the
   bay name, nearest first, and that came out 22% above optimal on a
   two-nurse ward. Ranking on journey length instead -- which carries both
   why a group is slow, its class and its distance -- fixed it where a
   dispatch order can fix it, and independently improved an eight-bay ward
   from 15 slots to 13 and a 240-patient ward by one slot. The part of that
   instance a dispatch order cannot fix became finding 5.
5. **A single dispatch ranking cannot say "send these two together".** What
   was left of the two-nurse ward needed the two furthest patients travelling
   at the same time, which no ranking of five groups produces. Promoting the
   group that arrives at `Theta` to the front of the order and re-dispatching
   does produce it, and that turned out to improve five of the eight
   benchmark wards as well -- see the order repair in section 3.
6. **The lower bound was far too weak to certify anything.** Having a true
   optimum to compare against showed it averaging about half of it, so on
   large wards it sat 236-467% below the plan and said nothing. Three terms
   were missing or wrong: journey times ignored that a bed through a narrow
   door is span-limited rather than speed-limited, and nothing accounted for
   a bay being throttled by its own doorway or for every patient having to
   enter through a known set of refuge doors. With those added it averages
   88% of the optimum. This is the one case where the exact solver did not
   find a bug so much as reveal that a number being reported was not worth
   reporting.

## 6. Results

`python -m pytest -q` runs 146 tests in about 10 s, skipping the three that
want more exact-solver budget than the suite allows. Every plan from either
solver passes independent verification.

### Measured against the exact optimum

Twenty-three instances, twenty-two of which the exact solver finishes, up to
**seven patient groups**. **SCARF reaches the optimum on every one of them.**

| instance | P | groups | LB | SCARF | optimum | tight | nodes |
|---|---|---|---|---|---|---|---|
| walkers_only | 6 | 2 | 2 | 3 | 3 | 67% | 5 |
| one_bed | 1 | 1 | 3 | 3 | 3 | **100%** | 2 |
| two_beds_two_staff | 2 | 2 | 9 | 9 | 9 | **100%** | 16 |
| recirculate_across_bays | 2 | 2 | 7 | 7 | 7 | **100%** | 8 |
| mixed_bay | 2 | 2 | 5 | 6 | 6 | 83% | 36 |
| narrow_door | 5 | 2 | 4 | 5 | 5 | 80% | 5 |
| two_refuges | 2 | 2 | 4 | 4 | 4 | **100%** | 5 |
| tight_junction | 3 | 3 | 3 | 4 | 4 | 75% | 7 |
| under_fire | 3 | 2 | 2 | 2 | 2 | **100%** | 3 |
| stair_choice | 2 | 2 | 5 | 5 | 5 | **100%** | 4 |
| deferred_patient | 3 | 2 | 7 | 7 | 7 | **100%** | 3 |
| three_bays_one_nurse | 3 | 3 | 10 | 11 | 11 | 91% | 3,547 |
| competing_bays | 4 | 4 | 7 | 9 | 9 | 78% | 456 |
| queue_at_door | 6 | 4 | 6 | 8 | 8 | 75% | 36 |
| deadline_forces_order | 5 | 4 | 4 | 5 | 5 | 80% | 7 |
| split_destinations | 4 | 4 | 3 | 3 | 3 | **100%** | 16 |
| long_wing | 2 | 2 | 7 | 7 | 7 | **100%** | 3 |
| five_bays_two_nurses | 5 | 5 | 8 | 9 | 9 | 89% | 25,877 |
| six_beds_two_pairs | 6 | 6 | 14 | 16 | 16 | 88% | 21,734 |
| mixed_two_bays | 8 | 6 | 6 | 8 | 8 | 75% | 391 |
| seven_groups_two_refuges | 8 | 6 | 6 | 7 | 7 | 86% | 17 |
| eight_bays_three_nurses | 8 | 8 | 10 | 13 | *unsolved* | — | budget |
| nine_groups_under_fire | 9 | 7 | 4 | 5 | 5 | 80% | 10 |

The bound averages **88% of the true optimum** and is exactly tight on nine.
`python -m bench.gap` regenerates the table; `--budget` chases the stubborn
instances.

`eight_bays_three_nurses` is unresolved. SCARF plans 13 slots against a bound
of 10, and proving whether 10, 11 or 12 is reachable needs more than eight
million nodes, which is where eight groups currently sits. That is the honest
limit of the optimality claim.

#### The last gap, and closing it

`five_bays_two_nurses` was the one instance SCARF got wrong once the solver
reached five groups: five wheelchair patients at five different distances, two
nurses. Each nurse delivers, walks back and collects the next, so this is
two-machine scheduling with sequence-dependent setup times.

```
before   b1, b2, b3, b4, b5                     -> 11 slots
optimum  b1, b3, b2, then b4 and b5 together    ->  9 slots
```

The optimum interleaves near and far bays so that the two furthest patients
travel *at the same time* at the end. SCARF finished with one nurse making a
long trip alone. Neither longest-journey-first nor shortest-first reached it,
because both are dispatch orders and no single ranking produces that pairing.

The fix was not to solve the escort schedule, which is what this looked like
it needed. It was to notice that the group arriving at `Theta` is the one that
most needed to start earlier, promote it to the front of the order and
re-dispatch -- the order repair in Phase 3. That reaches 9 slots, and the
pairing falls out of the promotion rather than being constructed.

This gap was invisible until the exact solver was extended past four groups,
which is the whole argument for having extended it, and it is the fifth of
six defects found by measurement rather than by reading the code.

#### What extending the solver cost and bought

Three techniques, none giving up exactness, cut the node counts by an order of
magnitude and took the reachable size from four groups to seven.

- **Symmetry breaking.** Four bed patients in one bay are interchangeable, so
  assigning them journeys A, B, C, D explores the same plan as B, A, C, D --
  twenty-four times over. Groups with the same signature are kept in a fixed
  relative order and the later one must take a candidate no earlier in the
  domain than the earlier one did.
- **Forward checking with fail-first ordering.** After each assignment every
  unassigned group is probed for at least one candidate that still fits; none
  means the branch is already dead. The counts come free, so the next group
  assigned is the one with the fewest choices left.
- **Incremental escort pruning.** Escort feasibility used to be checked only
  at the leaf, so escort-starved branches were explored to full depth first.
  Checking it on the groups assigned so far is a valid necessary condition,
  because taking jobs away from a roster that cannot cope never makes it cope
  less.

`three_bays_one_nurse` went from 54,650 nodes and 410 ms to 3,547 nodes and
28 ms; `two_beds_two_staff` from 150 to 16.

### Scaling

`within` is how far above the lower bound the plan sits, so it is an upper
bound on how far from optimal SCARF could possibly be. It is left blank where
no plan clears everyone, since the bound is for a full evacuation.

| instance | n | m | P | staff | phi | Theta | LB | within | limited by | time |
|---|---|---|---|---|---|---|---|---|---|---|
| icu12 | 12 | 22 | 12 | 14 | yes | 15 (7.5 min) | 8 | 88% | escort work | 8 ms |
| medical28 | 20 | 38 | 28 | 5 | **no** | 35 (17.5 min) | 30 | — | escort work | 42 ms |
| twofloor | 13 | 24 | 20 | 6 | **no** | 15 (7.5 min) | 12 | — | escort work | 5 ms |
| gen 10x6 | 18 | 34 | 60 | 10 | yes | 72 | 47 | 53% | escort work | 56 ms |
| gen 20x6 | 33 | 64 | 120 | 20 | yes | 103 | 55 | 87% | escort work | 175 ms |
| gen 40x6 | 63 | 124 | 240 | 40 | yes | 163 | 102 | 60% | refuge intake | 632 ms |
| gen 80x6 | 123 | 244 | 480 | 80 | yes | 317 | 206 | 54% | refuge intake | 2.8 s |
| gen 160x6 | 243 | 484 | 960 | 160 | yes | 624 | 396 | 58% | refuge intake | 12.4 s |

Runtime grows about fourfold per doubling of ward size, consistent with the
`G * m * W` term: both `G` and `m` double. Two constant factors sit on top of
that: two independent claim-mode descents, which bought the destination
assignment, and the order repair, which costs a further dispatch pass per
round and bought the last two slots on five of these eight wards.

These `within` figures were 236–467% before the bound was tightened, which
certified nothing. At 53–88% they are worth quoting, and since the bound
still averages 88% rather than 100% of the optimum where the optimum is known,
the true distance from optimal is smaller than the column says. It remains an
upper bound on the gap, not the gap.

The binding column is the actionable part. On a 28-bed ward with five staff
overnight, what limits the evacuation is not any corridor — it is that five
people cannot escort eleven wheelchair patients and nine bed patients any
faster. Widening a door would not help; a second pair of hands would. On the
largest generated wards it flips to refuge intake, where more ways into the
refuges genuinely is the fix.

### The infeasible wards are findings, not failures

**medical28** strands exactly one patient: the bed case in the bay where the
fire starts. Bay 1 is untenable three slots after the alarm, so its single
1100 mm door passes `3 x 3 = 9` width units. Three walkers, two wheelchair
users and one bed demand `3 + 4 + 5 = 12`. Someone is left behind, and leaving
the bed costs one patient where leaving the wheelchair users costs two. SCARF
leaves the bed, which is the smallest possible loss. Give that bay six minutes
instead of ninety seconds and everyone gets out.

The design conclusion is about the building, not the algorithm: a six-bed bay
with one 1100 mm door cannot be cleared under fire in the bay. The reported
bottleneck set names that door.

**twofloor** strands one patient for the same reason, and it is worth stating
separately because the table reports it and the arithmetic is not the same
building. Floor-2 bay 1 holds two walkers, two wheelchair users and one bed:
`2 + 4 + 5 = 11` width units behind one 1100 mm door, untenable three slots
after the alarm, so `3 x 3 = 9` units get out. Two units must be shed. The
cheapest ways to shed two units are one wheelchair user or the bed, both
costing exactly one patient, so one stranded patient is the provable minimum
and SCARF reaches it. A walker is only one unit, so leaving walkers behind
would take two of them.

**gen 320x6** is a 1920-patient ward on one 2.15 m spine with a refuge at each
end. Nine groups of walkers strand in the outer half of the spine, bays 187 to 300
of 320, because it is saturated for the whole horizon. That is a true capacity result about an
absurd building. The tested envelope is up to about 960 patients; beyond it
the single-spine topology binds, not the planner.

### What the ICU plan shows

Escort recirculation, visible in the output: escorts 9 and 10 deliver a pod-4
patient to the south refuge at 02:00, walk back, and collect a pod-3 patient
at 02:30. Twelve ventilated patients, all bed cases, cleared in 7.5 minutes by
fourteen staff, with no bed ever routed down the stair — `pi(e, 3) = 0` — so
every one goes to a horizontal refuge across a compartment line. The beds
split six and six between the two refuges, and the two 1100 mm pod doors
come back as the bottleneck set.

## 7. Next steps, in priority order

1. **Push the exact solver past seven groups.** `eight_bays_three_nurses` is
   unresolved at eight million nodes, and the optimality claim is only as
   broad as the solver reaches. The next levers are a better branching
   heuristic and dominance between candidate schedules that occupy the same
   slots.
2. **Tighten the bound where it is loosest** -- `walkers_only` at 67% and the
   junction and door cases around 75%. All three are instances where standing
   room at a junction binds, which is the one physical constraint none of the
   four obstructions models.
3. `k` labels per vertex in the search, to recover exactness where `kappa`
   blocks a wait. This is the remaining known source of incompleteness.
4. Charge escort walk-back against corridor capacity, and check whether it
   ever changes an answer.
5. Validate against a real drill. Everything here is internally consistent and
   checked against its own rules; none of it has been checked against a
   stopwatch on a real ward.

## 8. Sources

Flows over time and quickest flow:
[Generalizations of flows over time with applications in evacuation optimization](https://depositonce.tu-berlin.de/items/8144cd8c-64b6-4f6f-9c4c-a83e16907991),
[Evacuation by earliest arrival flows](https://www.researchgate.net/publication/28357220_Evacuation_by_earliest_arrival_flows),
[Solving Evacuation Problems Efficiently: Earliest Arrival Flows with Multiple Sources](https://www.researchgate.net/publication/224678304_Solving_Evacuation_Problems_Efficiently--Earliest_Arrival_Flows_with_Multiple_Sources),
[Optimal Evacuation Flows on Dynamic Paths with General Edge Capacities](https://arxiv.org/pdf/1606.07208).

Capacity-constrained route planning:
[Capacity Constrained Routing Algorithms for Evacuation Planning: A Summary of Results](https://conservancy.umn.edu/items/f5fbf849-e484-426d-ab55-5a4aa5318289),
[Experiences with evacuation route planning algorithms](https://www-users.cse.umn.edu/~shekhar/talk/evac/ijgis.12.2012.pdf),
[Improved Algorithms for the Evacuation Route Planning Problem](https://arxiv.org/pdf/1605.00065),
[Capacity Constrained Routing Algorithms for Evacuation Route Planning](https://drops.dagstuhl.de/entities/document/10.4230/DagSemProc.10121.3).

Hospital evacuation as resource-constrained scheduling:
[Modeling and optimizing the evacuation of hospitals based on the MRCPSP with resource transfers](https://www.sciencedirect.com/science/article/abs/pii/S219244062100068X),
[the RCPSP variant](https://dokumente.ub.tu-clausthal.de/receive/clausthal_mods_00000058).

Hospital evacuation simulation:
[Hospital Evacuation Planning Tool for Assistance Devices (HEPTAD)](https://onlinelibrary.wiley.com/doi/full/10.1002/fam.2856),
[Multi-hazard hospital evacuation planning during disease outbreaks using agent-based modeling](https://pmc.ncbi.nlm.nih.gov/articles/PMC8507583/),
[Modeling Seismic Resilience and Hospital Evacuation](https://www.mdpi.com/2075-5309/16/8/1538),
[BB_Evac: Fast Location-Sensitive Behavior-Based Building Evacuation](https://arxiv.org/pdf/2002.08114).

Building-level routing:
[Comparative analysis of Bellman-Ford and Dijkstra's algorithms for optimal evacuation route planning in multi-floor buildings](https://www.tandfonline.com/doi/full/10.1080/23311916.2024.2319394),
[A Multi-Zone Staged Indoor Emergency Evacuation Algorithm Based on Time Equalization](https://doi.org/10.3390/ijgi10080499).
