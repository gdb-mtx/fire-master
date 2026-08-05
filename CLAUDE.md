# CLAUDE.md

Read ARCHITECTURE.md for design philosophy and the two-interface architecture (frontend = cockpit, Claude Code = co-pilot). That document is the key to using this app well: the backend APIs are the real product, and Claude Code pointed at them is the analysis layer.

Read docs/PROPERTY_MODULE.md before touching property classification.

If a `CLAUDE.local.md` exists here, read it too — it carries the owner's machine-/data-specific notes and is never committed.

## Module map

| Module | Entry point |
|--------|-------------|
| Monarch sync + Net Worth | `app/ingestion/monarch_sync.py` |
| Spending Analyzer | `app/engines/spending.py` |
| AI Advisor (de-prioritized — Claude Code is the preferred analysis tool) | `app/advisor/manager.py` |
| Asset Hub + Enrichment | `app/engines/asset_hub.py` |
| FIRE Projections + Scenarios | `app/engines/fire_projections.py` |
| Tax Engine + Monte Carlo | `app/engines/tax_engine.py`, `app/engines/monte_carlo.py` |
| Spending Tracker | `/api/spending/tracker`, `/tracker` page |
| Scenario System | `fire_scenarios` table, `get_effective_config()` |
| Property P&L | `app/engines/property_pnl.py`, `/api/properties`, `/properties` page |
| Transactions Ledger | `app/api/transactions.py`, `/api/transactions`, `/transactions` page |
| Monarch tag sync (bidirectional) | `scripts/monarch_tag_writeback.py` (out), `reclassify()` tag map (in) |

Test suite: **200 unit tests, <1s** (`cd backend && uv run pytest -v`) + **9 Postgres integration tests** (skipped unless `TEST_DATABASE_URL` points at a scratch DB; CI runs them). Run after engine, config, tax, or scenario changes.

**Property P&L gotchas:**
- Transactions are classified to a property by DB-stored merchant rules (`property_rules`), stamped onto `transactions.property_id/property_category/property_source` by `PropertyPnLEngine.reclassify()` (runs post-sync + on `POST /api/properties/reclassify`).
- **Rental income can't land on a credit card.** A positive Airbnb-style amount on a credit-card account is a *guest refund* (a trip the owner booked), NOT a host payout — host payouts hit checking. `classify_transaction(..., is_credit_card=)` skips the income branch for credit-card rows; `reclassify`/`clear_override` join `accounts` to supply it. The guard is income-only — property expenses on a credit card still classify.
- `property_source='manual'` = user override; reclassify NEVER touches those (clobber guard). Like account enrichment, the property_* columns survive Monarch re-sync.
- P2P payments (Venmo/Zelle) never get auto-rules — the merchant carries no property signal. Classify per-transaction (manual override or Monarch tag).
- Tracker excludes any `property_id IS NOT NULL` row (one predicate in `_tracker_base_query()`); Runway/FIRE burn deliberately do NOT — they still count property spend.
- Manual/off-Monarch entries are `Transaction(source=MANUAL)` on the synthetic "Manual / Off-Monarch" account, `external_id=NULL` so the reconciler ignores them.
- Seed/refresh from config: `cd backend && uv run python ../scripts/seed_properties.py` (imports `config/properties.json` → `properties` + `property_rules`, then reclassifies). Start from `config/properties.example.json`.

**Transactions Ledger (`/transactions`):**
- Monarch-like browser over ALL transactions (`GET /api/transactions`, filters: `q`/`type`/`classification`/`property_id`/`account_id`/dates, paginated). Read-only; classify actions reuse the Property override endpoints.
- Exists because the Tracker is expense-only and Properties only lists already-assigned rows — so unclassified **income** (e.g. a P2P rental deposit) had no UI surface anywhere. The ledger is the general classification surface; inline "assign to property" reuses `set_override`.

**Monarch tag sync — bidirectional (read before touching tags or `reclassify`):**
- Property classification is two-way with Monarch via **tags** (NOT categories — a tag is multi-valued/orthogonal, so "which property" rides alongside the existing category; categories would force a combinatorial explosion). Tag name = property name, color = `Property.color`.
- **Outbound** (our classification → Monarch tag): `scripts/monarch_tag_writeback.py` — dry-run default, `--live`, `--undo`, `--property`. Merge-don't-clobber (`set_transaction_tags` REPLACES, so it reads current tags and writes the union), paginated reads, throttled writes, idempotent. Write key = `transactions.external_id` (the Monarch txn id).
- **Inbound** (Monarch tag → our P&L): `reclassify()` maps a tag back to its property via `match_tag_to_property()` + `resolve_with_tag()`. **Precedence: manual override > Monarch tag (`property_source='monarch_tag'`) > merchant rule.** A tag agreeing with a matched rule keeps `'rule'` (no churn); a disagreeing/gap-filling tag wins as `'monarch_tag'` with category defaulted (income→Rental Income, expense→`_fallback_category`). Removing the tag in Monarch un-assigns on next reclassify. So `property_source` ∈ {`rule`, `manual`, `monarch_tag`, NULL}; ledger shows a green "tag" badge.
- Celery runs `reclassify` post-sync but does NOT hot-reload — **restart the worker after engine edits** or the auto path runs stale code (the API/`POST /api/properties/reclassify` is current via uvicorn `--reload`).

## Tech stack

- **Backend**: Python 3.12 / FastAPI / SQLAlchemy 2.0 async / PostgreSQL 16 / Celery + Redis
- **Frontend**: React 18 + TypeScript / Vite / Tailwind CSS / lightweight-charts + Recharts / TanStack Query
- **Package management**: uv (pyproject.toml) for Python, npm for frontend
- **Infrastructure**: Docker Compose (postgres, redis, backend, celery-worker, celery-beat)

## Key commands

```bash
# Tests — RUN AFTER ENGINE/CONFIG/TAX/SCENARIO CHANGES
cd backend && uv run pytest -v

# Full stack
./scripts/start.sh               # postgres, redis, backend, celery, frontend

# Backend (from backend/)
uv sync                          # Install Python deps
uv run uvicorn app.main:app --reload  # Dev server :8000
uv run alembic upgrade head      # Apply migrations
uv run celery -A app.tasks.celery_app worker --loglevel=info -I app.tasks.sync_tasks
uv run celery -A app.tasks.celery_app beat --loglevel=info

# Frontend (from frontend/)
npm install && npm run dev       # Dev server :5173

# First-run seeds (from backend/)
uv run python ../scripts/seed_demo.py         # full demo persona: accounts, history, income, events, config (refuses on Monarch DBs; --remove to clear)
uv run python ../scripts/seed_config.py       # starter FIRE config persona (blank-slate alternative to the demo)
uv run python ../scripts/seed_scenarios.py    # example scenarios
uv run python ../scripts/seed_properties.py   # properties + rules from config/properties.json
uv run python ../scripts/monarch_login.py     # Monarch auth (one-time)

# Reports
cd backend && uv run python ../scripts/report_expense_scrub.py [YEAR]
```

## Architecture gotchas

Things that will cause bugs if you don't know them.

**Data layer:**
- All monetary values: BIGINT cents in PostgreSQL
- Use `displayBalance` (not `currentBalance`) for account balances
- Account enrichment fields survive Monarch sync (not overwritten)
- Demo-seed rows (`scripts/seed_demo.py`) are `source=MANUAL`, `external_id=NULL`, marked `custom_data.demo_seed=true` — invisible to sync/reconcile; `--remove` deletes exactly those
- Uses `monarchmoneycommunity` fork (original `monarchmoney` unmaintained, domain rebrand broke it)
- Uses `bcrypt` directly (not `passlib` — incompatible with bcrypt 5.x)
- **Enum columns in raw SQL expressions** (`case()`, on-conflict `set_`): never pass a bare enum member as a literal — it binds the lowercase `.value`, but the PG enum labels are member NAMES, and one bad bind aborts the whole transaction (the Aug 4 sync outage). Route through `stmt.excluded.<col>` or an explicitly typed bind, and cover the path in `tests/integration/` — mock tests structurally cannot catch this class.
- Celery worker needs `-I app.tasks.sync_tasks` for task autodiscovery
- `get_settings()` uses `@lru_cache` — restart backend after .env changes
- Expense reconciliation vs Monarch intentionally runs higher: tax refunds are not netted against spending (conservative burn rate)
- Incremental sync look-back = `INCREMENTAL_SYNC_DAYS` (45 days) in `monarch_sync.py`, used by both `sync_transactions` and `reconcile_transactions`. Was 7 days, which silently + permanently dropped any txn that posted >7 days after its date or fell in a >7-day sync gap (no incremental pass ever looks back further). If the stack is offline >45 days, run a backfill (`run_full_sync(full_history=True)`, or `sync_transactions(start_date=...)` for a pure upsert with no delete). Reconcile-delete is scoped to `source=MONARCH` rows only — never touches manual entries.
- Monarch assigns a NEW `external_id` when a pending txn posts (and often refines the merchant name), so the pending row becomes an orphan. `reconcile_transactions` deletes orphans (DB MONARCH rows absent from Monarch's current set for the window) — that's how pending/posted dups get cleaned. **A pure-upsert backfill leaves orphans behind**, so always follow `sync_transactions(start_date=...)` with `reconcile_transactions(start_date=...)`. NB: external_id uniqueness does NOT prevent these dups (two IDs, one real charge); to detect, diff our `external_id`s against Monarch's current set, not by merchant name (spelling drifts).

**Projection engine (read before touching):**
- EVERYTHING is real-terms as of Jul 2026 — `project_lifetime()` (single-pool, drives `/timeline` + scenario what-ifs) and `project_wealth_pools()` (pool-aware, the trusted engine) now share the frame; they differ in STRUCTURE. Do NOT copy mechanics between them, and never inflate a flow with `(1+i)**yr`.
- `project_wealth_pools()` is the trusted model. All rates REAL (after-inflation). Spending flat = constant purchasing power. SS flat = COLA offsets inflation.
- IRA-A earns its configured real growth rate; SEPP payments follow the IRS amortization calc — different things (investment return vs tax calc). Don't change financial assumptions without solid reasoning.
- ALL rates in `custom_assumptions.projection` — zero hardcoded values. Engine defaults are neutral (zero/disabled); everything meaningful comes from config.
- `custom_assumptions.property_sales` is the ONE documented property-sale mechanism: generic, per-property entries (dynamic value, cap gains incl. §121 + state tax, mortgage payoff via amortization, proceeds routed to a `taxable_pool` drawn before IRA-B). **Full mechanism + config keys: ARCHITECTURE.md (Projection Engine).**
- LEGACY blocks (`miami_sale`, `sauvie_sale`, `str_income`, `mortgage_recast`, `park_city`) are retained for author back-compat only and are clearly banner-marked in the engine — do not use them in new configs, do not extend them. `property_sales` supersedes them whenever present.
- `re_bucket` tokens: `primary | secondary | income` (legacy property-name tokens still alias for back-compat).
- Configurable matchers (all under `custom_assumptions`): `occupancy_source_match` (top-level; rental-source name substrings the occupancy haircut applies to; empty = all rentals), `projection.sell_event_label_match` (cashflow-event substring that drops secondary-property equity; None = disabled), `projection.illiquid_vest_event_match` (substrings that reduce the illiquid pool; default `["vest"]`).
- Mortgage recast (legacy): `miami_sale.monthly_cost` must be the ORIGINAL pre-recast value (engine adjusts it).
- Cash can go negative ONLY while no drawable pool exists (pre-rescue bridge stress; frontend clamps display at 0). Once the taxable pool is funded, a repair draw tops cash back to exactly $0 — a resting-at-zero cash line means "living off pool draws, no buffer."
- One-off cashflow events dated before today are dropped (not clamped to month 0).
- Monte Carlo (`monte_carlo.py`) shares the real-terms frame: lognormal nominal returns + correlated stochastic inflation (ρ=−0.25) → real return per year; spending/income/SS flat real. Vol/correlation overrides live in `custom_assumptions.monte_carlo`. Do NOT reintroduce nominal `(1+i)**yr` multipliers on flows — that was the pre-Jul-2026 double-count bug.
- The withdrawal plan (`optimize_withdrawal_sequence`) is also real-terms: spending flat, balances at the real return, tax brackets/deduction FROZEN at today's levels (correct — the IRS inflation-indexes them, so frozen-in-real ≈ indexed-in-nominal). Cash earns a REAL yield (`tax.cash_yield_rate`, default 0 = holds purchasing power).

**Config & scenarios:**
- FIRE config is single-row table. Config page saves to BASE config, not active scenario overrides.
- Scenarios store overrides-only JSONB, deep-merged via `get_effective_config(scenario_id)`.
- Config PATCH uses `flag_modified` + `db.refresh` for JSONB dirty-tracking.
- `PATCH /api/fire/config` merges `custom_assumptions` server-side as an RFC 7386 JSON Merge Patch (`app/core/merge.py`): omitted keys survive, dicts merge recursively, arrays/scalars replace, explicit `null` DELETES a key (fire-master#9). Clients (config page, tax planner, Claude Code) send ONLY the keys they manage — never resend a full spread (a stale spread clobbers concurrent edits), and clear a field by sending `null`, not by omitting it. Scenario `_apply_overrides` keeps its own one-level merge — different contract, do NOT unify them.
- Every `fire_config` UPDATE/DELETE is archived to `fire_config_history` by a Postgres trigger (fire-master#10; DDL in `app/models/fire_config_history.py`, shared by the migration and the PG integration tests). `GET /api/fire/config/history`, `POST /api/fire/config/history/{id}/restore` (restore replaces `custom_assumptions` wholesale and is itself archived). The trigger fires on ALL write paths incl. seed scripts and psql — don't add app-level history code.
- Seed scripts upsert (update existing, don't duplicate).

**Frontend:**
- Theme: warm-cream light UI (#f0ebe0 bg) with dark sidebar; accents `--green` #2e8b6e family — CSS vars in `index.css :root`
- Native fetch wrapper (no axios).
- Advisor bubble disabled in Layout.tsx.
- Workflow: enter enrichment + config in the frontend → backend stores → Claude Code queries APIs for analysis.
