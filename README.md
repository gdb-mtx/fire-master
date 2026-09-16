# FIREMaster

A self-hosted FIRE (Financial Independence Retire Early) planning cockpit. Your real accounts, your real spending, your real
retirement math — running on your own machine, with an AI analyst that can read all of it.

FIREMaster answers the questions generic retirement calculators can't:

- *I just left my job at 52. Does the bridge to 59½ actually hold?*
- *What happens if I sell the rental in 18 months instead of carrying it?*
- *Where does cash run out — and which lever (spending, a property sale, a 72(t) plan) fixes it?*
- *What did this property really cost me last year, all-in?*

It does this with month-by-month wealth-pool projections (cash, taxable, IRAs, real estate,
private equity — each with its own rules), scenario comparison, SEPP/72(t) bridge modeling,
per-property P&L, and a spending tracker — all driven by data that syncs automatically from
your real accounts.

> **Not financial advice.** FIREMaster is a modeling tool you run yourself. Sanity-check the
> assumptions, and make decisions with a professional where it matters.

## The two-interface architecture

FIREMaster is deliberately built as two layers (see [ARCHITECTURE.md](ARCHITECTURE.md)):

1. **The cockpit** — a React dashboard for entering your plan and seeing state at a glance:
   net worth, runway, projections, properties, spending.
2. **The copilot** — every number in the app is served by a local FastAPI backend
   (`http://localhost:8000/docs`). Point [Claude Code](https://claude.com/claude-code) at it
   and you have a financial analyst with full access to *your* data: ad-hoc questions,
   scenario stress-tests, spending audits, tax-year prep. This is the feature the dashboard
   is just the front end for. See [docs/CLAUDE_CODE_USAGE.md](docs/CLAUDE_CODE_USAGE.md).

> **Claude is optional — but it's where the strength lives.** The app is complete without any
> AI: every page, projection, and scenario works standalone, and nothing calls out to any AI
> service. Add Claude Code (any Opus- or Fable-class model) and the same app becomes a
> financial analyst you can interrogate in plain English. Required: no. The reason this app
> exists: yes.

## Monarch by design

FIREMaster does **not** connect to your banks. [Monarch Money](https://www.monarchmoney.com)
(~$8/month) owns aggregation — bank connections, transaction dedup, merchant cleanup,
categories — because that's a hard, thankless problem that a dedicated product already solves
well. FIREMaster syncs from Monarch and owns everything Monarch doesn't: FIRE projections,
scenario math, property P&L, bridge planning, and the AI-analyst layer.

This is a deliberate two-layer architecture, not a missing feature. It also means you don't
need Monarch to try the app:

- **Day one (no Monarch):** seed the built-in demo persona and every page renders alive —
  a 52-year-old fresh off a layoff, three properties, a SEPP bridge plan, and a cash crunch
  the projections catch before it happens.
- **When you're ready:** connect your Monarch account and sync your real data. Your first
  sync automatically clears the demo persona — no manual cleanup needed.

## Quick start

Prerequisites: **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** — that's
it (the repo is public; plain `git clone` needs no account, and there's a no-git zip on the
repo page under **Code → Download ZIP**). For the full AI-analyst experience (the
reason this app exists), add **[Claude Code](https://claude.com/claude-code)** with a
[Pro or Max](https://claude.ai/) subscription — see
[docs/CLAUDE_CODE_USAGE.md](docs/CLAUDE_CODE_USAGE.md). No Python, Node, or shell tooling on
your machine. Works on macOS, Windows, and Linux. (On Windows, Docker Desktop installs WSL2
itself — one reboot, then the commands below.)

```bash
git clone https://github.com/gdb-mtx/fire-master.git firemaster && cd firemaster

docker compose run --rm backend uv run python -m app.setup   # one-time: JWT secret + your admin password
docker compose up                                     # pulls prebuilt images + starts everything; migrations + demo data load automatically
```

Open **http://localhost:5173** and log in as `admin` with the password you chose — the **demo
persona is already loaded**, so every page (Dashboard, Retirement, Runway, Config) is alive on
first launch.

> **First run pulls prebuilt multi-arch images from GHCR (~30–60s)**; subsequent `docker compose up` is faster still.
> (No prebuilt image yet, or offline? `docker compose up --build` builds locally instead.) To update later: `docker compose pull && docker compose up -d`.
> Every published port is bound to `127.0.0.1`. Nothing — not Postgres, not Redis, not the API
> or the UI — answers to other machines on your network. Leave that as it is, and never put
> this stack straight on the internet.
> A `migrate` container that shows `Exited (0)` is normal — it applied migrations + seeded the demo, then quit.
> If `:5432`/`:6379`/`:8000`/`:5173` are already taken, set e.g.
> `BACKEND_HOST_PORT=8001 FRONTEND_HOST_PORT=5174` before the command. Operational details,
> and how to undo any of this, are in [docs/CONTAINER_RUNBOOK.md](docs/CONTAINER_RUNBOOK.md).

The demo seeds automatically on a fresh database — to start **blank** instead, run
`SEED_DEMO=false docker compose up`. Optional extras, in a second terminal:

```bash
docker compose exec backend uv run python ../scripts/seed_scenarios.py   # example what-if scenarios
```

The demo is safe to explore or re-seed, and **clears itself on your first real Monarch sync**
(or `seed_demo.py --remove` any time).

### Going live with your data

```bash
docker compose exec backend uv run python ../scripts/monarch_login.py    # one-time Monarch auth (email/password/MFA)
```

Then hit **Sync Now** on the Dashboard — your first sync **auto-clears the demo persona**,
leaving just your data (rebuild your plan under **Settings → Plan** in the sidebar). Full walkthrough — including
account enrichment, property rules, and your first FIRE config — in
[docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) and [docs/MONARCH_SETUP.md](docs/MONARCH_SETUP.md).

### Contributor / native dev (optional)

Prefer to run the backend and frontend **directly on your machine** for fast hot-reload? That
path still exists. Beyond Docker it needs [uv](https://docs.astral.sh/uv/) and Node 22.22 or newer
(which still provides Postgres/Redis), and a bash shell (macOS/Linux/WSL2):

```bash
./scripts/setup.sh      # bash: generates backend/.env (JWT secret + admin password)
./scripts/start.sh      # postgres+redis in Docker; backend, worker, frontend on the host
```

Both paths share the same database, so you can switch between them freely.

### Run only what you can read

If you would rather not run an image from a registry, the local-build overlay builds both app
images from the checkout in front of you: local-only image names, `pull_policy: never` so a
registry tag can never be substituted at runtime, lockfile-frozen installs, and a frontend that
ships as static files behind nginx with no Node toolchain or source inside the container.

```bash
touch backend/.env
docker compose -f docker-compose.public.yml -f docker-compose.local-build.yml pull postgres redis
docker compose -f docker-compose.public.yml -f docker-compose.local-build.yml build --pull
docker compose -f docker-compose.public.yml -f docker-compose.local-build.yml run --rm --no-deps backend uv run python -m app.setup
docker compose -f docker-compose.public.yml -f docker-compose.local-build.yml up --pull never
```

The `touch` and the setup step are first-install only; after that the last command is enough,
plus a rebuild whenever you pull and read an update. In this standalone form Postgres and Redis
have no host ports at all, and the UI and API are on localhost only.

## What's inside

| Page | What it does |
|---|---|
| Dashboard | Net worth, asset/liability allocation, history, one-click Monarch sync |
| Runway | Cash runway: months of burn covered, income vs. spend, upcoming cashflow events |
| Retirement | Wealth-pool projection to age 90+, scenario compare, SEPP bridge, FIRE number |
| Assets | Asset hub with enrichment: FIRE roles, strategies, notes per account |
| Spending | Spending analyzer by category/merchant over time |
| Tracker | Monthly non-property spending vs. target, category drill-down |
| Transactions | Full ledger browser: filter, classify, assign to properties |
| Properties | Per-property P&L from real transactions (rules + overrides + Monarch tags) |
| Tax Planning | Effective tax modeling for retirement drawdown *(early development — disabled in current release)* |

Under the hood: Python 3.12 / FastAPI / SQLAlchemy 2.0 async / PostgreSQL 16 / Celery + Redis,
React 18 + TypeScript / Vite / Tailwind. All money is integer cents; all projections are in
today's dollars. The test suite covers the projection engine, tax math, scenario merging, and property
classification (`docker compose exec backend uv run pytest`, or `cd backend && uv run pytest`
on the native path).

## Troubleshooting

- **`Docker daemon is not running`** — start Docker Desktop first and wait for it to finish launching.
- **Port already in use** — the dev stack takes 5432, 6379, 8000 and 5173, all on `127.0.0.1`.
  Move any of them with the
  `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` / `BACKEND_HOST_PORT` / `FRONTEND_HOST_PORT` env vars,
  e.g. `BACKEND_HOST_PORT=8001 docker compose up`.
- **`migrate` container shows `Exited (0)`** — that's normal; it ran migrations and quit. See
  [docs/CONTAINER_RUNBOOK.md](docs/CONTAINER_RUNBOOK.md) for the full container troubleshooting table.
- **Login fails with a startup error about `JWT_SECRET_KEY`/`AUTH_PASSWORD_HASH`** — you skipped
  first-run setup: `docker compose run --rm backend uv run python -m app.setup`.
- **Changed `backend/.env` but nothing happened** — settings are cached at process start;
  `docker compose restart backend celery-worker` (or restart the host processes on the native path).
- **Edited engine code but Celery behaves old** — the worker doesn't hot-reload;
  `docker compose restart celery-worker`.
- **Stack was offline for weeks** — incremental sync looks back 45 days. For longer gaps, run a
  backfill: see "Monarch sync" in [docs/MONARCH_SETUP.md](docs/MONARCH_SETUP.md).

## Why this exists

I spent twenty-five years building technology for movies, e-commerce, and games — the kind of career you don't plan an early exit from, until a layoff plans it for you. At 53, with a household that
runs on real estate as much as index funds, every retirement calculator I tried gave me a
polite shrug: none of them could model a severance runway, a 72(t) bridge, a rental that pays
for itself, or the one question that actually mattered — *which year does cash go negative,
and what fixes it?* So I built the tool I needed, on top of the data I already had. FIREMaster
is that tool, cleaned up so you can run it on yours.

## License — source-published, free for personal use

This repo is public so you can **audit everything that touches your financial data**: read
the source, watch every update as a diff, and verify the chain end-to-end — the GHCR images
you run are built from this code by [public GitHub Actions](.github/workflows/), so what you
read is what you pull.

**Free to self-host for personal, noncommercial use — yours forever.** This is *not* open
source: commercial use of any kind (offering it as a service, using it with clients,
deploying it inside a company, selling forks) requires a separate license from the author.
Full terms: [LICENSE.md](LICENSE.md) (PolyForm Noncommercial 1.0.0 — about one page, plain
English).

Bug reports are welcome ([CONTRIBUTING.md](CONTRIBUTING.md)); security issues go to a
private channel ([SECURITY.md](SECURITY.md)).

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — design philosophy, the two-interface model, projection engine internals
- [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) — full installation walkthrough
- [docs/CONTAINER_RUNBOOK.md](docs/CONTAINER_RUNBOOK.md) — Docker run modes, troubleshooting, and how to rewind
- [docs/MONARCH_SETUP.md](docs/MONARCH_SETUP.md) — connecting and syncing Monarch Money
- [docs/CLAUDE_CODE_USAGE.md](docs/CLAUDE_CODE_USAGE.md) — the AI-analyst workflow, with example prompts
- [docs/PROPERTY_MODULE.md](docs/PROPERTY_MODULE.md) — property P&L classification internals
- [CLAUDE.md](CLAUDE.md) — repo guide for Claude Code sessions
