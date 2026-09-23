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
