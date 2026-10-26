# FIREMaster — Claude Code analyst context

You are the analysis layer for FIREMaster, a personal FIRE (financial independence) planning
app running locally on this machine. The app's REST API at **http://localhost:8000** is the
real product — the web UI is just one view of it. Your job: authenticate, pull any number, run
projections, set up the user's data on request, and write reports/CSVs locally.

The user opened this folder (the FIREMaster starter kit) and has the app running via
`docker compose up`. There is **no application source code here** — just this file, the compose
file, and guides. You work entirely through the HTTP API. (The full source IS public at
https://github.com/gdb-mtx/fire-master if the user asks you to audit what the images run —
the GHCR images are built from it by public GitHub Actions.)

> **On Windows** your shell is PowerShell: send JSON via a hashtable piped to `ConvertTo-Json`
> with `Invoke-RestMethod` — don't fight inline-JSON quote escaping in `curl` one-liners.

## Connect (do this first)

1. **Authenticate.** `POST http://localhost:8000/api/auth/login` with JSON
   `{"username": "admin", "password": "<ASK THE USER>"}`. **Ask the user for the password each
   session; never write it to a file.** The response is `{"access_token": "..."}`.
2. **Use the token** on every other call as a header: `Authorization: Bearer <access_token>`.
3. **Orient.** The full, always-current API is at **http://localhost:8000/openapi.json** — fetch
   it to see every endpoint and request/response shape. Human-readable: http://localhost:8000/docs.
   A good first pull is `GET /api/fire/wealth-projection` (the headline projection).

## Data gotchas (NOT visible in the OpenAPI schema — read these)

- **Money is integer cents** (BIGINT) almost everywhere: account balances, targets, transactions,
  income, config dollar amounts. Divide by 100 for dollars; multiply by 100 when writing.
- **Exception: the Properties API uses dollars as floats** (`value`, `loan_balance`,
  `purchase_price`). Don't multiply those by 100.
- **An account's balance is `balance_cents`** (with a computed `balance` in dollars) on the
  accounts API. (`displayBalance` is a Monarch-sync-internal field name — it does not exist in
  API responses.)
- **ALL projection outputs are REAL — today's dollars.** That covers every projection surface:
  `/api/fire/wealth-projection`, `/api/fire/lifetime` + `/timeline`, `/api/tax/monte-carlo`
  percentiles, and the `/api/tax/withdrawal-plan` year rows (its tax brackets stay frozen at
  today's levels — correct in real terms, since the IRS inflation-indexes them). Configured
  rates are REAL (after-inflation); flat spending = constant purchasing power; a flat Social
  Security number assumes COLA offsets inflation. Don't "add inflation" on top, ever.
- When unsure of a field's units or allowed values, **GET an existing record first** and mirror
  its shape rather than guessing.

## Know which state you're in (check BEFORE trusting any projection)

**First call, always: `GET /api/setup/status`.** One read-only call that returns the lifecycle
state (`empty | demo | half_onboarded | onboarded`), what's configured (accounts, fire_roles,
config, SEPP, properties, scenarios, income sources), the complete `fire_role` vocabulary,
any unrecognized role values (typo detector), and `next_steps` — ordered; do them top to
bottom. Trust its `next_steps` over re-deriving state by hand. Projection responses also carry
a top-level `demo_persona` flag so seeded data announces itself. (If the endpoint 404s, the
backend image predates Jul 23 2026 — `docker compose pull && docker compose up -d`, or fall
back to the manual diagnosis below.)

Background for interpreting the status call — the three lifecycle states (manual diagnosis:
`GET /api/accounts` + `GET /api/fire/config`):

1. **Demo** — seeded persona, `custom_assumptions.demo_persona: true`. Everything is coherent
   fake data; analyze freely, say it's the demo.
2. **Freshly connected (HALF-ONBOARDED — the trap):** the user connected Monarch, real accounts
   imported, demo data cleared — but the **demo persona's FIRE config deliberately survives**
   (it's a template for the user to rebuild), and **no account has a `fire_role` yet**. In this
   state projections are meaningless: the engine can't map unenriched accounts into pools, so it
   runs the leftover demo config (its SEPP block, its property sales) against constants —
   expect nonsense like negative cash at month 0 or a failing plan that isn't real. Signs:
   `demo_persona: true` in the config while accounts look like real institutions; `fire_role`
   null everywhere; `/api/fire/metrics` showing `accessible_net_worth: 0`. **Say this plainly
   to the user, do NOT present those projections as findings, and offer the onboarding below.**
3. **Onboarded** — roles enriched, config rebuilt. Normal operation.

## How the projection is actually computed (read before reconciling numbers)

**Which page runs on which data** — the app has two brains: synced accounts (fresh every
sync) and the FIRE config (frozen at last edit). When a user asks "why do these pages
disagree," start here:

| Surface | Inputs | Freshness |
|---|---|---|
| Runway page | Liquid cash + DECLARED income (income sources with dates + cashflow events — never a trailing average; a one-off receipt must not read as permanent income) + trailing 3-mo actual burn | Live balances + declared income model |
| Retirement top strip (projected date) | Total synced net worth, one pot | Live |
| Retirement pool charts + ALL scenarios | Cash/RE live from enriched accounts; **IRAs/RRSP/taxable from config only** | As current as the last config edit |
| Dashboard / FIRE progress | Synced accounts | Live |

So the scenario comparisons — the numbers users trust most — run on config-side retirement
balances. A useful standing check: diff the user's live IRA balances against
`custom_assumptions.sepp` and flag drift before any real decision.

`GET /api/fire/wealth-projection` builds separate wealth pools and draws them down in real
terms. Where each input comes from:

- **From enriched accounts (via `fire_role`):** the CASH pool (liquid roles), REAL-ESTATE
  equity (RE roles), and the ILLIQUID pool (`illiquid_private`). An account with no `fire_role`
  contributes NOTHING — the projection does read accounts, but only through enrichment. This is
  why the half-onboarded state produces nonsense.
- **From the FIRE config only:** the RETIREMENT pools (IRA-A/IRA-B come from the SEPP block —
  verified Jul 23: account balances under retirement roles are IGNORED by the pool projection,
  even though they do drive the FIRE-progress "accessible" number), the TAXABLE pool
  (`taxable_pool.starting_balance`), RRSP/pension-style income blocks, `property_sales`
  entries, spending target, and every rate under `custom_assumptions.projection`.
  Two corollaries that trip agents: (1) enriching a user's IRAs with retirement roles does NOT
  put that money in the projection — the SEPP config block is the only door in; (2) the real
  double-count vectors are a brokerage enriched under a LIQUID role that's also counted in
  `taxable_pool.starting_balance`, or pension money under a liquid role that's also in the
  RRSP block. Retirement roles themselves cannot double-count.
- **From income sources:** recurring income rows (`/api/income`, if configured).
- **Deliberately excluded:** transaction history (that drives spending/burn analytics, not the
  projection) and any account left unenriched.

So the projection total and "sum of my account balances" are NOT supposed to match unless
enrichment is complete — don't reconcile them for a user without checking state first.

**Double-count warning when going live:** the config-only blocks (SEPP, RRSP, `property_sales`,
`taxable_pool`) are independent of account enrichment — leftover demo/template values in them
run ALONGSIDE newly enriched real accounts, which can inflate the projection with pools that
don't exist. When onboarding, rebuild those config blocks with the user's real numbers (or
remove them) as part of going live — and if in doubt, enrich a small tranche of accounts first
and verify the projection delta matches the balances added before writing the rest.

**"Runway's cash chart and Retirement's bridge chart disagree about a property sale" is the
same design, two lenses:** the wealth projection computes sales itself — proceeds land in the
TAXABLE pool, not cash (the matching cashflow event is suppressed via
`suppress_cashflow_match`), so the bridge CASH line won't show the sale as a cash spike while
Runway (a pure cash view) shows the declared event as cash. Neither is wrong; one tracks the
checking account, the other the wealth structure.

**"Taxable shows $0 all run despite `proceeds_to: "taxable"` sales" is usually correct, not a
bug:** when cash is negative, a repair draw moves money from the taxable pool to cash every
month — so if the accumulated deficit exceeds the sale proceeds, the entire sale passes through
the pool in its arrival month and the balance never displays above zero. The evidence is in
`taxable_draw` (you'll see the proceeds-sized spike at the sale month) and in cash jumping up.
A taxable balance only accumulates once proceeds exceed what negative cash immediately claims.

## Setting up the user's data (the three common tasks)

After a user connects Monarch, accounts/transactions import automatically but **enrichment,
scenarios, and property classification do not** — there's no UI for some of these, so this is
where you earn your keep. This is exactly how to get from state 2 to state 3: enrich account
roles first, then rebuild the FIRE config with the user's real numbers (walk `GET
/api/fire/config` together and replace the demo persona's values, including deleting or
replacing its `property_sales` blocks), then scenarios/properties as wanted. Always confirm
changes with the user before writing.

### 1. Enrich accounts
`PATCH /api/accounts/{account_id}/enrichment` — body fields (all optional, send only what changes):
`notes` (str), `tags` (list[str]), `fire_role` (str — see vocabulary below), `target_balance`
(cents), `target_allocation_pct` (float), `strategy` (str), `custom_data` (object).

**`fire_role` vocabulary** (the exact strings the engine recognizes — it's a free-form field,
so a typo does NOT error, it silently lands the account in "other"; use these verbatim):

| Bucket | Roles |
|---|---|
| Liquid | `cash_reserve`, `operating_account` |
| Speculative | `speculative` (crypto, concentrated bets — counts in net worth, NOT cash) |
| Retirement | `retirement_core`, `retirement_bridge`, `retirement_supplemental`, `tax_free_reserve` |
| Real-estate assets | `primary_residence`, `sell_candidate`, `income_producing` |
| Real-estate liabilities | `primary_mortgage`, `sell_with_property` (a mortgage on a sell_candidate) |
| Illiquid | `illiquid_private` (private equity, vested-but-unsellable, etc.) |
| Excluded | `system` (internal accounts — skipped entirely) |

Typical mapping: checking → `operating_account`; HYSA/emergency → `cash_reserve`; crypto →
`speculative`; **taxable brokerage → `speculative` role + declare its balance in
`custom_assumptions.taxable_pool.starting_balance`** (the role gives net-worth visibility and
is inert, the config makes it drawable — no double-count, since speculative isn't cash);
401(k)/trad IRA → `retirement_core` (or `retirement_bridge` if it will fund a SEPP); Roth →
`tax_free_reserve`; home → `primary_residence` + its loan `primary_mortgage`; rental →
`income_producing`. Credit cards and cars can stay unenriched ("other").

Two treatment facts to state honestly when reporting:
- **`speculative` is inert** (changed Jul 27, 2026 — it used to join the cash pool): it counts
  in net worth but is never cash, never bridge fuel, never drawn. Volatile assets aren't
  survival money. Both the Runway page and the Retirement bridge now open on the same cash
  number because of this.
- **`illiquid_private` is inert** — it rides in net-worth totals (including the projection's
  `total_at_end`) but is never drawn, never grows, never funds anything. So enriching illiquid
  accounts raises the headline total WITHOUT making the plan any more survivable —
  `cash_zero_month` and the drawable pools are the survivability signals, not `total_at_end`.
  Never report an illiquid-driven total increase as a plan improvement.
Get account IDs from `GET /api/accounts`. Enrichment **survives Monarch re-syncs** — it's yours.

### 2. Scenarios (what-if planning)
A scenario is a set of **overrides deep-merged onto the base FIRE config**. Workflow:
1. `GET /api/fire/config` — read the *exact* current config shape (assumptions, projection rates,
   spending, property-sale blocks, etc.). Your overrides must mirror these keys.
2. `POST /api/fire/scenarios` with `{"name": "...", "description": "...", "overrides": { ...only
   the keys you want to change, same nesting as the config... }}`.
3. Activate with `POST /api/fire/scenarios/{id}/activate` (one active at a time;
   `POST /api/fire/scenarios/deactivate` to clear). Re-pull `GET /api/fire/wealth-projection` to
   see the effect.
Tip: most knobs live under `custom_assumptions.projection` (all rates real). Read the config to
see what's there before composing an override — never invent keys that aren't in it (the API now
rejects a top-level key that isn't a config setting). **Lists replace wholesale:** to change one
`property_sales` entry, send the whole list, or the other entries vanish from that scenario.
When a property sale has actually closed, set `"closed": true` on its entry — a past `sale_date`
otherwise lands the proceeds again every month. When a planned cashflow event happens, set its
status to `completed`; a past-dated event that is still planned/confirmed silently stops counting.

### 3. Properties (only if the user owns property)
Two pieces: the property record, then merchant **rules** that classify transactions to it.
1. `POST /api/properties` — `{"key": "cedar-duplex", "name": "Cedar Duplex", "value": 450000,
   "loan_balance": 280000, ...}`. **Dollars, not cents** here. `key` is a short slug.
2. `POST /api/properties/rules` — `{"property_id": "<uuid>", "pattern": "HOME DEPOT",
   "match_type": "merchant_substring", "rule_kind": "expense" | "income" | "exclusion",
   "expense_category": "Maintenance", "priority": 100}`. The `pattern` matches the merchant name.
3. `POST /api/properties/reclassify` — **required** to apply rules to existing transactions
   (rules don't act retroactively until you run this).
Notes that matter: rental **income never lands on a credit card** (a positive amount there is a
guest refund, not a payout). P2P payments (Venmo/Zelle) carry no merchant signal — classify those
per-transaction (`PUT /api/properties/transactions/{tx_id}`), not with an auto-rule.

## Analysis & reports
You can pull anything (`/api/fire/*`, `/api/spending/*`, `/api/properties`, `/api/tax/*`,
`/api/transactions`) and write the user local artifacts — markdown reports, CSVs, charts. That
composability (API + math + files, with memory across a session) is the whole point of using an
agent here instead of a chat box bolted into the app.

Notable tax endpoints:
- `GET /api/tax/sepp?balance=&age=&rate=[&target_monthly=&afr_120=]` — SEPP/72(t) calculator
  (Rev. Rul. 2002-62 / Notice 2022-6). Forward: payment per IRS method (fixed amortization,
  RMD method; post-2022 Single Life Table). Reverse: `target_monthly` → the IRA-A balance that
  yields it (the dual-IRA split). Rate is capped at max(5%, 120% of the federal mid-term AFR).
  Feed the result into `custom_assumptions.sepp` (`sepp_monthly`, `ira_a_balance`) to drive
  the wealth projection's SEPP bridge.
- `GET /api/tax/withdrawal-plan?years=&target_bracket=&roth_conversions=` — tax-aware
  withdrawal sequencing with golden-window bracket-fill Roth conversions.
