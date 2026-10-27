# Upgrading FIREMaster

Newest release first. Each section says what changes when you update, what moves in
your numbers, and what — if anything — you need to decide.

**Updating**

```bash
docker compose pull && docker compose up -d        # Docker install
git pull && ./scripts/start.sh                     # native/dev install
```

Database migrations run automatically on start. Your data stays in place, and every
change to your FIRE config and scenarios is archived in their history tables, so you can
restore any previous version.

**After every update**, run the upgrade check. It is read-only: it lists what the update
changed in *your* data and marks anything that needs a decision with REVIEW.

```bash
docker compose exec backend uv run python ../scripts/upgrade_check.py
# native:  cd backend && uv run python ../scripts/upgrade_check.py
```

> **For AI agents (Claude Code):** when this checkout has just been updated, read the
> newest section below, run the upgrade check, and walk the user through every REVIEW
> item before relying on projections. Don't change plan dates or events without asking;
> the check shows options, and the user decides.

---

## October 2026 — optional: let every IRA pay the bills after 59½

**In short:**
- A new, **optional** rule: after 59½, every IRA can pay the bills, including the account
  set aside for 72(t) once its series allows. Off unless you set it.
- The Retirement page names the date locked IRA money becomes reachable, and whenever cash
  sits at $0 it says how much savings pay each month and from where.

No migration runs and **no projection number moves unless you turn the rule on**. Your
stored data is untouched.

### What moves in your numbers

| Where | Before | After |
|---|---|---|
| **Retirement projection** | (unchanged) | Identical, month by month, for every plan that doesn't set `open_all_iras_after_59_5` |
| **Retirement page: short or run-out plans** | "…is still in IRAs; see which IRA money this projection draws" | Names the unlock: "$642K sits in IRAs you can't reach without a penalty until Jun 2034"; a stretch that ends at 59½ says so, with the money it opens |
| **Retirement page: "Money runs out"** | The engine's month when every pool is empty | The start of a shortfall that never recovers, when that comes first |
| **Retirement page: cash at $0** | A flat line at $0 | The cash chart labels it with the monthly draw ("Cash at $0: savings pay $8,632 a month"); readouts name every source; one big payback month no longer flattens the withdrawals panel |

### Decide these

- **Open every IRA after 59½?** Today, after 59½ only the IRA *not* set aside for 72(t)
  pays the bills; the 72(t) account pays only its 72(t) payments and required withdrawals
  from 73. And under the default cash rule, a cash shortfall that opened before 59½ is never
  paid back, even with money sitting in the IRAs. In `custom_assumptions.projection`
  (merge-patch): `open_all_iras_after_59_5: true`. Then:
  - The 72(t) account opens at the later of 59½ and five years after its first payment
    (earlier, an extra withdrawal would bring back the 10% penalty on every payment so far).
  - From 59½ the IRAs pay back a cash shortfall, after the brokerage and before the Roth.
  - **Expect:** no change while your brokerage never runs dry before required withdrawals
    begin. Where cash used to sit short beside IRA money, the shortfall closes, and wealth at
    82 can come out *lower*: today's projection leaves those bills unpaid at no cost while the
    IRAs keep growing. The lower number is the one where the bills are paid.

### For agents: API changes

All additive; nothing was removed or renamed.

- **`cash_rule`** gains `open_all_iras`, `age_59_5_month` (0 once past) and
  `ira_a_open_month` (null when the rule is off or there is no 72(t) account).
- **Milestone** "72(t) account opens" (`kind: draw`) when that month differs from 59½.
- **Assumption** `ira_access` names the dates when the rule is on.
- **Order with the rule on:** threshold mode draws the brokerage, IRA-B (59½), the open
  72(t) account, required withdrawals, then the Roth, and repairs a cash hole in the same
  order (IRAs from 59½); cushion mode refills from required withdrawals, the brokerage, IRA-B,
  the open 72(t) account, then the Roth.

### Undo

Send `open_all_iras_after_59_5: null` in a config merge-patch. Config edits are archived in
`fire_config_history` as always.

---

## October 2026 — a new Retirement page; an optional cash cushion

**In short:**
- The Retirement page is rebuilt around three questions: does cash hold through the next
  five years, where does the money come from each year, and what do you own along the way.
  It opens with a one-sentence verdict, every sentence is computed from your plan, and a
  "How this projection works" list shows every rule the numbers rest on, tagging engine
  defaults you never set.
- A new, **optional** cash rule: keep a cushion of N months of what your income doesn't
  cover, refilled from savings. Off unless you set it.
- The Property P&L page says what each number is and for which period; its Net is now the
  real total for the period you pick (it was a yearly estimate).

No migration runs and **no projection number moves unless you turn the cushion on**. Your
stored data is untouched.

### What moves in your numbers

| Where | Before | After |
|---|---|---|
| **Retirement projection** | (unchanged) | Identical, month by month, for every plan that doesn't set `cash_cushion_months` |
| **Retirement page** | Bridge Period and Wealth Projection charts, FIRE-number tiles on top | Verdict, three question charts, rules list. The older cards (budget, cash flow, upcoming events, milestones, FIRE number) sit under "More detail", unchanged |
| **Property P&L: Net** | "Net / yr", annualized, the same whatever period you picked | Rent minus costs for the period you pick; Costs and Rent are period totals with a monthly average |

### Decide these

- **Turn on the cash cushion?** Today, savings start paying the bills once cash falls below
  12 months of the shortfall, and cash then holds wherever it was at that moment (often well
  above what you need, earning the cash rate). The cushion instead keeps cash at a target and
  refills it every month. In `custom_assumptions.projection` (Configure, or a merge-patch):
  - `cash_cushion_months: 6`: keep 6 months of what income doesn't cover. Cashflow events
    don't size it, so a windfall month can't shrink it to zero.
  - `cash_cushion_floor: 25000`: never less than this many dollars (optional).
  - `invest_cash_above_cushion: true`: move cash above the cushion into the brokerage each
    month (optional).

  Less idle cash usually means more money at 82. Set it in the base config to cover every
  scenario (scenarios merge `projection` keys over the base).

### For agents: API changes

All additive; nothing was removed or renamed.

- **`GET /api/fire/wealth-projection`** takes `scenario_id` (preview a plan without
  activating it) and `base=true` (the base plan while a scenario is active). The Retirement
  page reads it with `bridge_months=1200`, monthly to `end_age`.
- **Per point:** `ss_income`, `event_income`, `event_expense`, `one_time_income`,
  `sepp_draw`, `cash_cushion`, `cash_invested`. Display identity, every month:
  `expenses + event_expense + Δcash + cash_invested = (income − ss_income − event_income +
  event_expense) + (event_income − one_time_income) + cash_interest + ss_income +
  one_time_income + (ira_draw − rmd_redeposit) + roth_draw + rrsp_draw + taxable_draw`.
- **Response:** `assumptions` (every rule the run rests on, built from the EFFECTIVE config:
  `key`, `label`, `detail`, `value`, `kind` = setting / rule / limit, `is_default`),
  `milestones` (dated moments in plain words, with amounts), `cash_rule`, `starting_cash`,
  `end_age`, `money_runs_out_month`.
- **Cushion refill order:** required IRA withdrawals first (they leave the IRAs anyway),
  then the brokerage, then IRA-B from 59½, then the Roth. Cash above the cushion pays first.
- **Before you rely on it:** taxes on IRA, 72(t) and RRIF withdrawals are not counted, and
  paychecks are not modeled (add work income as an income source of type Other, with dates).

### Undo

Turning the cushion off is deleting the keys: send them as `null` in a config merge-patch.
Config edits are archived in `fire_config_history` as always.

---

## October 2026 — a strengthening pass: events, sold properties, scenarios

**In short:**
- A sold property can be marked `closed`, so its proceeds are never counted twice.
- The Runway's event list says what the projection does with each event, and an event can
  now be marked completed or cancelled from the form.
- A few name and status rules that quietly gave wrong numbers now hold everywhere.
- Scenario saves reject settings that never did anything; unknown scenario ids return 404.
- Smaller fixes: the account page suggests the recognized FIRE roles and says when a role
  isn't one (the account then does nothing in projections); after logging in you land on
  the page you asked for; Runway and Retirement refresh after you switch scenarios or edit
  events.

No migration runs. Your stored data is untouched; the upgrade check lists anything in it
that this update affects.

### What moves in your numbers

| Where | Before | After |
|---|---|---|
| **Retirement: sale and vest name matchers** | An EXPENSE event whose name contained `sell_event_label_match` (e.g. "Mountain House Special Assessment") removed that property's equity in its month; one containing a vest word drained the illiquid pool | Only income events trigger them, like every other name matcher. Numbers move only if one of your expense events matched |
| **Runway: default burn** | Marking a paid expense event *completed* put its payment back into the 90-day average | Completed events still clean the burn; they just never project forward |
| **Runway: completed income** | Marking a received inflow completed was not possible from the form | Completed events never project forward: the money is already in your balance |
| **Savings rate, readiness, spending** | A category named "… Income" (e.g. "Rental Income", "Interest Income") outside Monarch's Income group counted as spending | It counts as income from the next category sync. Moves your numbers only if you have such a category |
| **Property sales** | A sale whose date had passed kept landing its proceeds "now", every month, in the Runway and Retirement | Unchanged until you mark it `closed: true` (below) |

### Decide these

- **Events whose date passed but aren't marked done.** Projections stop counting a planned or
  confirmed event once its date is gone, whether or not the money arrived; the Runway list
  used to call these "realized". They now read **date passed**. For each one: if it
  happened, edit it and set **Status: Completed**; if it slipped, move the date.
- **A property you have actually sold.** Add `"closed": true` to its `property_sales` entry
  (in the scenario that models it). It then counts as sold from now with no proceeds, because
  they are already in your accounts, and its carrying cost and rent changes apply. Don't
  delete the entry instead: its carrying cost would come back into the plan.

### For agents: API changes

- **`property_sales[].closed: true`.** Sold as of month 0 in every engine, zero proceeds,
  burn/income deltas applied, `suppress_cashflow_match` still applies. Runway
  `scenario_sales[]` gains `closed`.
- **Scenario overrides are validated on create/update.** A top-level key must be a fire_config
  setting (`id`, `created_at`, `updated_at` excluded), `custom_assumptions` must be an
  object, and date settings must be ISO dates. Anything else returns 422 with the reason.
  Stored scenarios are read tolerantly: a bad entry is skipped with a server-log warning.
- **Unknown `scenario_id` returns 404** on every endpoint that takes one
  (`/api/tax/withdrawal-plan`, `/roth-conversion-plan`, `/monte-carlo`). It used to answer
  with the base plan.
- **Cashflow event status** `completed` and `cancelled` are now offered by the UI. Runway burn
  matching reads planned, confirmed and completed events; only planned and confirmed
  project forward.
- **`GET /api/cashflow/runway`** parameter descriptions corrected: without overrides, income
  is declared income (sources + events) and burn is the cleaned 90-day average.

### Undo

Nothing in your data was rewritten. Scenario and config edits you make while acting on
this update are archived as usual (see the September section's Undo).

---

## September 2026 — plan dates stop drifting; Runway and Retirement agree

**In short:**
- Every plan month is now a calendar date that stays put.
- The Runway shows the property sales your active plan models.
- The Runway's default burn is your real recent spending.
- Several numbers that disagreed between pages now match.

### What happens automatically when you update

- **Plan dates are pinned.** Some settings used to be stored as "N months from today":
  property sale months, SEPP and RRSP start months, the cash-rate cutover and the car-loan
  payoff. Those slid one month later every month you didn't edit them. The migration turns
  each one into the calendar month it pointed to **on the day you update**. No Retirement
  number changes that day; the dates just stop moving. The old values stay in history.

### What moves in your numbers

| Where | Before | After |
|---|---|---|
| **Runway: default burn** | Your target spending + healthcare (unless you'd typed an override) | Your last 90 days of spending, minus payments matched to planned events (a finished special assessment is no longer projected forward). "Save as default" keeps any number you prefer. |
| **Runway: property sales** | Not shown; only hand-made "… Sale Proceeds" events counted | The active plan's dated sales appear: net proceeds in the sale month, carrying costs and rent adjusted after it |
| **Runway: first month** | A full month of spending, on top of a balance that already paid part of it | Only the rest of the month, so the cash-zero date moves a few weeks later |
| **Retirement: Cash Runway card** | Today's cash ÷ today's gap, ignoring every dated event | The projection's own cash-zero month, the same as the Bridge chart |
| **Retirement: Monthly Cash Flow** | Target spending only | Also pre-Medicare healthcare and the carrying cost of properties held outside the budget, matching the engine |
| **All projections** | An event dated earlier this month counted again | Already in your balance, so it isn't counted again |
| **Retirement: cash top-up** | Could sell brokerage to cover a hole that month's own inflow already covered | Measured at month end |
| **Tracker "Adjusted"** | Could exclude the wrong payment near a month boundary | Matched once over the whole tracker period |
| **Property P&L (Monarch tags)** | A tagged loan-principal line or card refund counted as rental income | Loan-account tags are ignored; a tagged card refund reduces the expense it refunds |

### If you use scenarios, decide these

The upgrade check lists which of these apply to you.

1. **Pinned dates.** Dates are pinned at *update day*. If you wrote a scenario months ago,
   the "months from today" had already slid, and the check shows the date you probably
   meant (counted from when you last saved the scenario). If that's the one you want,
   change it:
   - Scenario: `PUT /api/fire/scenarios/{id}` with the full `overrides`, setting
     `property_sales[].sale_date` / `sepp.start_date` / `rrsp.start_date` to `"YYYY-MM"`.
   - Base config: `PATCH /api/fire/config` with
     `{"custom_assumptions": {"sepp": {"start_date": "2027-06"}}}`.
   - The Configure page shows each as "Jul 2027 (in 10 mo)".
2. **Hand-made sale events.** A "… Sale Proceeds" income event can model the same sale as a
   `property_sales` entry. If so, it now counts twice on the Runway (it already did in
   Retirement). Fix it one of two ways:
   - Add `"suppress_cashflow_match": "<text in the event name>"` to that sale entry. The
     plan then owns the sale and the event is ignored.
   - Or delete the event.
3. **Expense events named like a sold property.** `suppress_cashflow_match` used to also
   drop *expense* events whose name matched (a special assessment on the property being
   sold). Those now count, because they are real money out. Delete or rename one only if
   it really shouldn't count.
4. **Sale entries without a date.** A `property_sales` entry with no `sale_date` (or old
   `sale_month`) used to sell **immediately**. It now means **held**: you keep it, its
   carrying cost and rent continue, and it never sells. Add a `sale_date` if you meant to
   sell it.
5. **Runway burn.** To keep the old plan-based number, type it into Monthly Burn on the
   Runway page and click **Save as default**. **Remove** clears it.
6. **Events with a minus sign.** An expense entered with a negative amount projected as
   *income*. The form and the API now reject negative amounts, since the type sets the
   direction. Existing ones are listed by the check; edit them to a positive amount.

### For agents: API changes

- **Plan months are calendar months.**
  - Write `"YYYY-MM"` to `property_sales[].sale_date`, `sepp.start_date`,
    `rrsp.start_date`, `projection.cash_savings_cutover_date` and
    `debt_paydown.car_loan_payoff_date`.
  - Offset keys (`sale_month`, `sepp_start_month`, `start_month`,
    `cash_savings_cutover_month`, `car_loan_payoff_month`) are still accepted but converted
    to dates when saved. Reads return dates.
- **`property_sales` entry with no date = held.** It never sells; carry and rent continue.
  `sale_month: 0` still means "sell now".
- **`suppress_cashflow_match` drops income events only,** in every engine.
- **Cashflow events.** `amount` must be ≥ 0 (`event_type` sets the direction) and
  `probability` must be between 0 and 1. Anything else returns 422.
- **`GET /api/cashflow/runway`.**
  - `trailing_burn` now excludes event-matched payments.
  - New fields: `trailing_burn_raw`, `burn_exclusions[]`, `scenario_name`,
    `scenario_sales[]`, `projection[].from_day`.
  - Saved defaults live in `custom_assumptions.runway.{monthly_income, monthly_burn}`;
    `null` means use actuals.
- **`GET /api/fire/bridge-status`.** `monthly_burn` / `monthly_deficit` now include
  pre-Medicare healthcare and held off-budget carrying costs. `cash_runway_months` is
  still cash ÷ current gap; for the projected date, use `cash_zero_month` from
  `/api/fire/wealth-projection`.
- **`GET /api/fire/wealth-projection`.** `events` gains `"RRIF starts"`.
- **`GET /api/fire/spending-sensitivity`.** `breakdown.properties[]` (`in_budget`, `held`)
  and `breakdown.outside_budget_monthly` are new.

### Undo

- Config: `GET /api/fire/config/history`, then `POST /api/fire/config/history/{id}/restore`.
- Scenarios: `GET /api/fire/scenarios/history`, then
  `POST /api/fire/scenarios/history/{id}/restore`.

Restoring an old version pins its offsets again as of the restore day.
