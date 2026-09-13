# Scenario Event Overrides — Design Note

*Working design note for giving scenarios a way to vary **cashflow events**, not just config numbers. Raised upstream as [fire-master#18](https://github.com/gdb-mtx/fire-master/issues/18) by an external collaborator. **Status: proposal. Nothing here is implemented.** Relevant code: `backend/app/engines/fire_projections.py`, `backend/app/models/fire_scenario.py`, `backend/app/models/cashflow_event.py`.*

---

## The problem in one paragraph

A plan can be described two ways: **settings** (single numbers — spending, retirement age, return rate, date of birth) and **events** (dated flows — "$40k in March 2031", "extra $2k/month from 2035 to 2040"). Scenarios — the what-if mechanism — can only vary settings. There is nowhere in a scenario to say *"assume that 2040 expense is bigger."* For a plan expressed mainly as settings this is invisible. For a plan expressed mainly as events, the entire what-if surface is unavailable.

> **Mental model:** a scenario override points at a *column*. A column can't be renamed or deleted by a user. An event override would point at a *row* — which users rename and delete. That difference is the whole design problem.

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

## Proposed design

### 1. Deltas are stored; results are not

The **instruction** ("assume the 2040 expense is $45k") lives in `fire_scenarios.overrides`, a persisted JSONB column. Saved, named, editable, revisited.

The **result** — a modified event — is assembled in memory at projection time and discarded.

This mirrors what config scenarios already do: `get_effective_config()` returns a detached `FireConfig` copy that is explicitly *"never written back to DB."*

Rejected alternative: materialising modified events into `cashflow_events` when a scenario activates. That requires un-writing them on deactivate and creates two sources of truth for the same plan.

**Bonus:** because scenario writes are archived by the fire-master#10 triggers, event deltas stored this way inherit undo for free.

### 2. Reference events by ID, carry the name for diagnostics

Match on `CashflowEvent.id`. Store the name alongside it, unused for matching, so a broken reference can say *"the event 'Healthcare' referenced by this scenario no longer exists"* rather than showing a bare UUID.

Name-matching is rejected: names aren't unique, and a rename silently breaks the reference.

Open trade-off: IDs are hostile to hand-editing, and the only scenario editor today is a JSON textarea. See open questions.

### 3. Decide the failure mode explicitly

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

1. **How do IDs get into the JSON box?** The only scenario editor is a raw textarea. Nobody will hand-type UUIDs. Options: accept name *or* ID and resolve names to IDs on save; add a minimal event-picker UI; or defer until there's a real scenario editor. **Unresolved — this is the weakest part of the design.**
2. **Should unknown override keys start erroring?** Today they silently no-op. Tightening that is correct but is a behaviour change to existing scenarios, and may break stored overrides that already carry stray keys.
3. **What validates the blob?** Config overrides land on typed columns. An events blob has no type checking unless deliberately routed through the same Pydantic schema the real event API uses (`CashflowEventCreate` / `CashflowEventUpdate`). Without that, a typo'd date surfaces as a crash deep inside a projection, far from where it was typed.
4. **Should base events get their own history table?** Arguably the other half of the upstream complaint — the destructive-edit risk exists independently of scenarios. Smaller, separable, and defensible on its own merits. Same trigger pattern as fire-master#10.
5. **Is per-person config the better fix for the motivating case?** Adding spouse/partner DOB and per-person claim/Medicare ages would collapse much of the event usage back into settings, which scenarios already override. It does not subsume events — time-boxed obligations are genuinely event-shaped — but it addresses the *why* rather than the *what*. Possibly both, possibly this first.
6. **Does `add` need a stable identity too?** An added event exists only inside the scenario. Fine until someone wants to modify an added event, or two scenarios want to share one.

---

## Explicitly not decided

- Whether to build this at all. The motivating use case is not one this app's owner currently has.
- Whether to accept the upstream patch, rewrite it, or decline. The author asked for a straight answer and is carrying it locally either way.
- Any UI beyond the existing JSON textarea.

---

## Effort read

| Piece | Size |
|-------|------|
| UI | Near zero — the JSON escape hatch already exists |
| Backend plumbing (engine carries scenario, 5 call sites) | Mechanical but invasive; touches recently-stabilised engines |
| Override apply logic | Small |
| Validation + warning plumbing | Small-to-medium, easy to skip and regret |
| The two real decisions (§2/§3 identity, §4 consistency) | Where the thinking is |

---

## References

- [fire-master#18](https://github.com/gdb-mtx/fire-master/issues/18) — upstream proposal
- fire-master#16 / #17 — events reaching every forward engine; the failure mode §4 guards against
- fire-master#10 — config/scenario history triggers; why §1 gets undo for free
- `ARCHITECTURE.md` — Projection Engine
- `CLAUDE.md` — "Cashflow events reach EVERY forward engine via `build_cashflow_schedule()`… Never expand events inline in an engine again."
