# Monarch Money Setup

FIREMaster syncs from [Monarch Money](https://www.monarchmoney.com) instead of connecting to
banks itself. **This is by design**: Monarch owns aggregation (bank connections, transaction
dedup, merchant cleanup, categories — a hard problem a dedicated product already solves),
FIREMaster owns analysis (projections, scenarios, property P&L, the AI-analyst layer). The
two-layer split means your bank credentials live in exactly one place — Monarch — and
FIREMaster only ever sees read-only financial data on your own machine.

You don't need Monarch to *evaluate* FIREMaster — the demo persona loads automatically on
first run, so the app is alive the moment you log in. Connect Monarch (~$8/month, free trial
available) when you're ready to run on your real data. Demo rows are manual-source and
coexist safely with a real sync — and
your **first real sync clears them automatically** (set `AUTO_CLEAR_DEMO=false` to keep them).

> **Running a locked demo?** Set `DEMO_MODE=true` and everything in this doc is hard-disabled —
> Monarch sync never runs, the session is never loaded, and the instance is limited to the
> seeded demo persona. Required for any shared or public demo.

## 1. One-time authentication

```bash
docker compose exec backend uv run python ../scripts/monarch_login.py
```

Enter your Monarch email, password, and MFA code if prompted. (`docker compose exec` gives the
interactive terminal the MFA prompt needs; run it against the already-running stack.) Nothing you
type at the password or MFA prompt is echoed. What gets written is `backend/.monarch_session`,
mode 0600, gitignored — it IS your login, guard it like one. Your password is never stored, and
the helper stops the library from also dropping its own copy under `.mm/`. If you logged in with
a version older than Sept 2026, delete the leftover `backend/.mm/mm_session.pickle` once you have
confirmed `backend/.monarch_session` is there.

FIREMaster uses the community-maintained `monarchmoneycommunity` client (the original
`monarchmoney` library is unmaintained and broke when Monarch rebranded domains).

**Sessions expire** every few weeks/months. If syncs start failing with auth errors, just
re-run `monarch_login.py`.

## 2. First sync

Click **Sync Now** on the Dashboard, or:

```bash
curl -X POST "http://localhost:8000/api/sync/monarch?full_history=true" \
  -H "Authorization: Bearer $TOKEN"
```

Use `full_history=true` for the **first** sync — it pulls your complete transaction history
and Monarch's full net-worth history. Subsequent syncs are incremental (a 45-day look-back
window) and run automatically **every 4 hours** via the Celery beat scheduler while the stack
is up; a daily net-worth snapshot is computed at 00:30.

What a sync brings over: accounts (type, institution, balance), per-account balance history,
transactions (with Monarch's categories and your Monarch tags), and aggregate net worth
history.

**If the stack was offline for more than 45 days**, run one `full_history=true` sync to
backfill the gap — the incremental window can't reach back further on its own.

## 3. Enrich your accounts (this drives the projections)

Monarch knows what an account *is*; FIREMaster needs to know what it's *for*. On the
**Assets** page, assign each account a **FIRE role** — the projection engine partitions your
net worth by these roles:

| Bucket | FIRE roles |
|---|---|
| Liquid (cash pool) | `cash_reserve`, `operating_account`, `speculative` |
| Retirement | `retirement_core`, `retirement_bridge`, `retirement_supplemental`, `tax_free_reserve` |
| Real estate | `primary_residence`, `sell_candidate`, `income_producing` (assets); `primary_mortgage`, `sell_with_property` (their loans) |
| Illiquid / private | `illiquid_private` |
| Other | anything else (vehicles, etc.) |

The bridge/supplemental split matters if you model a SEPP/72(t) plan: `retirement_bridge` is
the IRA you draw from before 59½, `retirement_supplemental` is the one that grows untouched.
The demo persona (seed_demo.py) shows a fully-enriched example of every role.

Enrichment is **never overwritten by sync** — roles, notes, tags, strategies, and targets all
survive every Monarch re-sync. Hiding/excluding accounts stays Monarch's job (use Monarch's
own hide flag; FIREMaster respects it via `include_in_net_worth`).

## 4. Income sources and cashflow events

Two things Monarch can't know, entered once in the app (or via the API):

- **Income sources** (Runway/Retirement): salary, severance, unemployment, rentals — with
  end dates where they apply.
- **Cashflow events** (Runway/Retirement): one-off or recurring future flows — a property
  sale, a tax refund, equity vesting — each with a probability weighting.

## 5. Property owners: classification setup

If you track rental/property P&L:

```bash
cp config/properties.example.json config/properties.json
# edit: your properties, merchant rules, exclusions
docker compose exec backend uv run python ../scripts/seed_properties.py
```

Transactions auto-classify to properties by merchant rules; fix one-offs inline on the
Transactions page. Classifications can also round-trip with Monarch as **tags** (tag name =
property name): outbound via `scripts/monarch_tag_writeback.py` (dry-run by default), inbound
automatically on every sync — tag a transaction in Monarch's app and it lands classified
here. Details and gotchas: [PROPERTY_MODULE.md](PROPERTY_MODULE.md).

## 6. Going fully live

**Nothing to clean up by hand.** Your first real Monarch sync (step 2) **auto-removes the demo
persona** — accounts, balance history, income sources, and cashflow events. It is marker-scoped,
so it only ever deletes demo rows; your synced data is never touched.

The one thing that stays is the **FIRE config** — that's your retirement *plan*, and only you
can write it. Rebuild it under **Settings → Plan** (the gear in the left sidebar) — projections
reflect the demo persona's plan until you replace it.

```bash
# Optional escape hatches:
#   keep the demo alongside real data →  AUTO_CLEAR_DEMO=false docker compose up
#   clear the demo manually any time  →  docker compose exec backend uv run python ../scripts/seed_demo.py --remove
```
