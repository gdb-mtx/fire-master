# Scenario Event Overrides — Design Note

*Working design note for giving scenarios a way to vary **cashflow events**, which live in their own table and are therefore outside everything a scenario can reach today. Raised upstream as [fire-master#18](https://github.com/gdb-mtx/fire-master/issues/18) by an external collaborator. **Status: proposal. Nothing here is implemented.** Relevant code: `backend/app/engines/fire_projections.py`, `backend/app/models/fire_scenario.py`, `backend/app/models/cashflow_event.py`.*

---

## The problem in one paragraph

Scenarios deep-merge a **config object**: `fire_config` columns plus the `custom_assumptions` JSONB. They have no mechanism to vary **rows in another table**. Cashflow events are rows in another table. So there is nowhere in a scenario to say *"assume that 2040 expense is bigger"* — not because the scenario system is simplistic, but because it operates on one config row and events aren't in it.

> **Mental model:** the boundary is **config row vs. separate table**, not simple vs. complex. A scenario can already express a fully-modelled property sale with amortised mortgage payoff and capital-gains treatment — because that lives in `custom_assumptions`. It cannot change a single dollar of a cashflow event — because that lives in `cashflow_events`.

---

## What scenarios can already do (important context)

It would be wrong to describe the current what-if surface as limited. A single `custom_assumptions.property_sales` entry models appreciated value, agent fees, cost basis, §121 exclusion, LTCG + state tax, mortgage payoff via amortisation, burn deltas, occupancy-adjusted rental-income cancellation, proceeds routing into the taxable pool, and cashflow-event suppression. Scenarios override all of it. The same holds for `taxable_pool`, `roth_pool`, `sepp`, `monte_carlo`, `projection` and `debt_paydown`.

`property_sales` is scenario-varyable **because it was put inside `custom_assumptions` rather than in a table of its own.** That decision also bought it config history/undo for free. Cashflow events went the other way — own table, own API, own UI, a status workflow — and landed outside what scenarios reach.

So the app already has two homes for future dated flows, with opposite trade-offs:

| | `custom_assumptions.property_sales` | `cashflow_events` |
|---|---|---|
| Scenario-varyable | yes, for free | **no** |
| Real UI | no — raw JSON | yes |
| History / undo | yes (fire-master#10 triggers) | **none** |
| Read backward-looking | no | yes (spending reconciliation) |

### Sharp edge: the merge is one level, and lists replace

`_apply_overrides` merges `custom_assumptions` **one level deep**, and anything that isn't a dict replaces wholesale:

```python
if isinstance(subval, dict) and isinstance(base_ca.get(subkey), dict):
    base_ca[subkey] = {**base_ca[subkey], **subval}   # one level only
else:
    base_ca[subkey] = subval                          # lists land here
```

So a scenario varying one sale in a three-sale `property_sales` array must restate all three, and anything nested two levels inside `projection` replaces its parent. This is existing behaviour, not a proposal — but any event-override format has to decide whether it follows the same rule or a different one.

---

## Why it's total, not partial, for some plans

`fire_config` is single-person by construction. Every age-like column is singular:

| Column | Singular? |
|--------|-----------|
| `date_of_birth` | yes |
| `social_security_start_age` | yes |
| `medicare_start_age` | yes |
| `pension_start_age` | yes |

A two-earner household reaching Medicare at different times, claiming Social Security on different clocks, and carrying time-boxed obligations (a mortgage that ends, children leaving home, cover that steps as each person ages) **cannot express any of that in settings**. It all has to become cashflow events.

So that shape of plan lives almost entirely in the half scenarios can't reach, and has no working what-if at all. The only way to ask a hypothetical is:

1. edit the real event in the live plan
2. read the chart
3. edit it back

That is a destructive write to answer a read-only question. If step 3 doesn't happen — interrupted, tab closed, error — the plan is quietly wrong with nothing flagging it. Note that `fire_config` and `fire_scenarios` both have history/undo (Postgres triggers, fire-master#10). **`cashflow_events` has none.** There is nothing to restore from.

---

## Verified current behaviour

Everything in this section was checked against the code, not assumed.

- **Scenario overrides reach config only.** `FireProjectionsEngine._apply_overrides()` (`fire_projections.py:246`) copies `fire_config` columns, applies top-level keys, and one-level deep-merges `custom_assumptions`. That is the entire surface.
- **Unknown override keys silently no-op.** `_apply_overrides` only applies a key when `hasattr(merged, key)` is true. Putting `{"events": {...}}` in a scenario today does nothing and reports success.
- **Events are loaded with no scenario awareness.** `_get_cashflow_events()` (`fire_projections.py:452`) queries planned+confirmed rows directly. It takes no scenario parameter, so every engine sees the live rows regardless of which scenario is active — including `GET /api/fire/scenarios/{id}/preview`.
- **Five forward engines consume events**, all via that one loader:
  - `project_lifetime` (`fire_projections.py:582`)
  - `compute_readiness` (`fire_projections.py:850`)
  - `project_wealth_pools` (`fire_projections.py:1284`)
  - `compute_bridge_status` (`fire_projections.py:1766`)
  - `MonteCarloEngine` (`monte_carlo.py:142`, reaching into the projections engine's private method)

  This is exactly the set fire-master#16/#17 were about.
- **Events already have stable IDs.** `CashflowEvent.id` is a UUID primary key with a `gen_random_uuid()` server default. No migration is needed to reference events by ID.
- **`CashflowEvent.name` has no unique constraint.** Two events may share a name.
- **The scenario UI is a raw JSON textarea.** `ScenariosSection` in `frontend/src/pages/FireConfigPage.tsx` validates with `JSON.parse` and nothing else. There is no structured scenario editor.

---

## What was proposed upstream

fire-master#18 adds an `events` key to `overrides`, matching events **by name**:

```json
{"events": {"add":    [{"name": "...", "event_type": "expense", "amount_cents": 30000,
                        "date": "2040-12-01", "end_date": "2043-12-01"}],
            "modify": [{"name": "<an existing event>", "amount_cents": 45000}],
            "remove": ["<an existing event>"]}}
```

It returns a new detached event list; nothing is persisted. Names must resolve to exactly one live event, and a name that is both modified and removed is rejected rather than resolved by ordering. Roughly 700 lines including 25 tests, backend only, never exposed to a user.

The author explicitly framed it as raw material rather than a design, and asked for a yes / no / reshape answer.

---

## Scope first: `add` is separable from `modify` / `remove`

The upstream proposal treats add / modify / remove as one feature. They have very different costs.

**`add` needs no identity at all.** *"What if I also had a $45k expense in 2040"* points at nothing — no rename problem, no deletion problem, no failure mode, no pointer to go stale. It could live in `custom_assumptions` exactly the way `property_sales` does, work with today's merge machinery, and inherit config history for free.

**`modify` and `remove` need a pointer into `cashflow_events`** — and carry every hard problem in §2 and §3 below.

| | Needs event identity | Needs new machinery | Hard problems |
|---|---|---|---|
| `add` | no | little — mirrors `property_sales` | none |
| `modify` | yes | yes | identity, stale references, failure mode |
| `remove` | yes | yes | same |

If `add` covers most of the what-ifs people actually want, the expensive half may not be worth building. **Worth asking the upstream author which of the three his household actually uses** — a cheap question that could shrink this substantially.

Note that `add` alone does *not* close the gap #18 describes: its motivating example is varying an existing obligation, which is `modify`. But it may close enough of it to be the right first step.

---

## Proposed design

### 1. Deltas are stored; results are not

The **instruction** ("assume the 2040 expense is $45k") lives in `fire_scenarios.overrides`, a persisted JSONB column. Saved, named, editable, revisited.

The **result** — a modified event — is assembled in memory at projection time and discarded.

This mirrors what config scenarios already do: `get_effective_config()` returns a detached `FireConfig` copy that is explicitly *"never written back to DB."*

Rejected alternative: materialising modified events into `cashflow_events` when a scenario activates. That requires un-writing them on deactivate and creates two sources of truth for the same plan.

**Bonus:** because scenario writes are archived by the fire-master#10 triggers, event deltas stored this way inherit undo for free.

### 2. Reference events by ID, carry the name for diagnostics

*Applies to `modify` / `remove` only — `add` references nothing.*

Match on `CashflowEvent.id`. Store the name alongside it, unused for matching, so a broken reference can say *"the event 'Healthcare' referenced by this scenario no longer exists"* rather than showing a bare UUID.

Name-matching is rejected: names aren't unique, and a rename silently breaks the reference.

Open trade-off: IDs are hostile to hand-editing, and the only scenario editor today is a JSON textarea. See open questions.

### 3. Decide the failure mode explicitly

*Applies to `modify` / `remove` only.*

When a referenced event is missing (deleted, or ID not found), there are three options:

| Option | Behaviour | Verdict |
|--------|-----------|---------|
| Hard fail | Preview refuses to render | Honest, annoying |
| Silent skip | Chart renders without that delta | **Unacceptable** |
| Warn and proceed | Chart renders, response carries a warning | Preferred |

Silent skip is what you get for free from a lookup that finds nothing and moves on. In a retirement planner a confidently wrong chart is worse than no chart — the user believes they are looking at the $45k scenario and is actually looking at $30k.

**Decision: warn and proceed.** The projection response needs a field for unresolved overrides, and the frontend needs to surface it.

### 4. The engine carries the scenario — don't pass it around

All five forward engines must see the same events. Wiring scenario events into some but not all reproduces fire-master#16/#17 exactly: the Retirement page would render a chart built on scenario events beside a readiness number built on the live plan, with nothing on screen indicating disagreement.

Two ways to do it:

- **(a)** thread `scenario_id` through all five call sites — forgettable, and Monte Carlo currently reaches into a private method
- **(b)** construct the engine with the scenario: `FireProjectionsEngine(db, scenario_id=...)`

**Prefer (b).** Config and events then resolve from the same stored scenario automatically, everywhere. You cannot forget to pass a parameter that isn't a parameter. It also removes the cross-module private call.

This is the bulk of the real work — mechanical, but it touches the engines that fire-master#12–#17 just stabilised.

### 5. Scope: forward projections only

`app/engines/spending.py:504` also queries `CashflowEvent`, for a **backward**-looking purpose — reconciling planned expenses against actual spending.

A scenario must never touch that. "What if healthcare cost more in 2040" must not alter the record of what was spent last month. The boundary is *forward projections*, not *everywhere events are read*.

### 6. `modify` patches, it does not replace

If a scenario says *change Healthcare to $45,000* and the real event's **date** is later edited, the scenario version picks up the new date. A scenario holds one delta, not a frozen copy.

Worth stating explicitly because the codebase already runs two deliberately different merge contracts (RFC 7386 for config PATCH in `app/core/merge.py`; one-level merge for scenarios in `_apply_overrides`) with a standing note not to unify them. Add/modify/remove on a list is a third semantic — decide it once, document it here.

---

## Open questions

1. **Which of add / modify / remove is actually needed?** See the scope section above. Unanswered, and it changes the size of this by a lot. Ask upstream before designing further.
2. **How do IDs get into the JSON box?** The only scenario editor is a raw textarea. Nobody will hand-type UUIDs. Options: accept name *or* ID and resolve names to IDs on save; add a minimal event-picker UI; or defer until there's a real scenario editor. **Unresolved — the weakest part of the design, and it only applies to `modify`/`remove`.**
3. **Should an event override follow the existing merge rule?** `custom_assumptions` merges one level and replaces lists wholesale. An add/modify/remove verb list is a different semantic in the same blob. Either is defensible; it must be decided once and written down, because the codebase already runs two deliberately different merge contracts (`app/core/merge.py` RFC 7386 for config PATCH, one-level for scenarios) with a standing note not to unify them.
4. **Should unknown override keys start erroring?** Today they silently no-op. Tightening that is correct but is a behaviour change to existing stored scenarios.
5. **What validates the blob?** Config overrides land on typed columns. An events blob has no type checking unless deliberately routed through the same Pydantic schemas the real event API uses (`CashflowEventCreate` / `CashflowEventUpdate`). Without that, a typo'd date surfaces as a crash deep inside a projection, far from where it was typed.
6. **Should base events get their own history table?** Arguably the other half of the upstream complaint — the destructive-edit risk exists independently of scenarios. Smaller, separable, defensible on its own merits, same trigger pattern as fire-master#10.
7. **Is per-person config the better fix for the motivating case?** Adding spouse/partner DOB and per-person claim/Medicare ages would collapse much of the event usage back into settings, which scenarios already override. It does not subsume events — time-boxed obligations are genuinely event-shaped — but it addresses the *why* rather than the *what*.
8. **Does `add` need a stable identity after all?** An added event exists only inside the scenario. Fine until someone wants to modify an added event, or two scenarios want to share one.

---

## Explicitly not decided

- Whether to build this at all. The motivating use case is not one this app's owner currently has.
- Whether to accept the upstream patch, rewrite it, or decline. The author asked for a straight answer and is carrying it locally either way.
- Any UI beyond the existing JSON textarea.

---

## Effort read

| Piece | Size | Needed for |
|-------|------|-----------|
| UI | Near zero — the JSON escape hatch already exists | all |
| Backend plumbing (engine carries scenario, 5 call sites) | Mechanical but invasive; touches recently-stabilised engines | all |
| Override apply logic — `add` | Small; mirrors `property_sales` | `add` |
| Override apply logic — `modify` / `remove` | Small in code, large in design | `modify`/`remove` |
| Identity + stale-reference handling (§2/§3) | **Where the thinking is** | `modify`/`remove` only |
| Engine-carries-scenario consistency (§4) | The other real decision | all |
| Validation + warning plumbing | Small-to-medium, easy to skip and regret | all |

**`add`-only is a materially smaller project than the full proposal** — it skips the identity work entirely and needs the §4 plumbing regardless.

---

## References

- [fire-master#18](https://github.com/gdb-mtx/fire-master/issues/18) — upstream proposal
- fire-master#16 / #17 — events reaching every forward engine; the failure mode §4 guards against
- fire-master#10 — config/scenario history triggers; why §1 gets undo for free
- `ARCHITECTURE.md` — Projection Engine
- `CLAUDE.md` — "Cashflow events reach EVERY forward engine via `build_cashflow_schedule()`… Never expand events inline in an engine again."
