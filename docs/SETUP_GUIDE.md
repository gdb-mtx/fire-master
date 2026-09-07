# Setup Guide

Complete walkthrough from clone to a living dashboard. Expect **~10 minutes** end to end —
mostly the prebuilt-image pull and Docker Desktop install. The short version is in the
[README](../README.md#quick-start); this guide adds detail, expected output, and the
post-install configuration steps.

## 1. Prerequisites

| Tool | Why | Check |
|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | runs the **whole** stack in containers — Postgres 16, Redis, Python 3.12, Node | `docker compose version` |

That's it — **no Python, uv, Node, or shell tooling on your machine.** They all live inside
the containers. The repo is public, so cloning needs no GitHub account or authentication —
plain `git` does it (`git` ships with macOS; on Windows it's installed separately — see
below). No git at all? Download the source as a zip from the
[repo page](https://github.com/gdb-mtx/fire-master) (green **Code** button → Download ZIP)
and unzip it — updating later is just re-downloading.

- **macOS / Linux**: install [Homebrew](https://brew.sh/) if you don't have it yet
  (`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`),
  then:
  ```bash
  brew install --cask docker    # Docker Desktop
  ```
- **Windows**: open PowerShell and run both:
  ```powershell
  winget install --id Docker.DockerDesktop -e --accept-package-agreements --accept-source-agreements
  winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
  ```
  Docker Desktop sets up WSL2 for you (**one reboot**). You do **not** install or manage an
  Ubuntu distro, and you never touch bash.
- **Prefer to develop natively** (run the backend/frontend on the host for fast hot-reload)?
  That path needs `uv` + Node 18+ and a bash shell — see the **Contributor / native dev**
  section in the [README](../README.md#contributor--native-dev-optional). This guide uses the
  Docker path throughout.

## 2. Clone and configure

```bash
git clone https://github.com/gdb-mtx/fire-master.git firemaster && cd firemaster
docker compose run --rm backend uv run python -m app.setup
```

(No login, no account — the repo is public.)

`app.setup` creates `backend/.env`: it generates a random JWT secret and asks you to choose an
**admin password** (stored as a bcrypt hash — the plaintext is never written anywhere).
Username is `admin`. It runs entirely inside the container (Python + bcrypt), so there are no
host dependencies and it works identically on Windows.

- **Non-interactive** (CI / scripted): set the password via an env var instead of the prompt:
  `docker compose run --rm -e FIREMASTER_ADMIN_PASSWORD=yourpass backend uv run python -m app.setup`
- **Change it later**: re-run with `--force` (it refuses to overwrite otherwise):
  `docker compose run --rm backend uv run python -m app.setup --force`

## 3. Start the stack

```bash
docker compose up
```

First run **pulls the prebuilt images** from GHCR (~30–60s) plus Postgres/Redis. (No prebuilt
image available, or offline? `docker compose up --build` builds them locally instead — a few
minutes.) Then, every start:

1. **postgres** and **redis** come up and pass health checks,
2. a one-shot **migrate** service runs `alembic upgrade head` — on a fresh database this builds
   the full schema and then **auto-seeds the demo persona** (set `SEED_DEMO=false` to skip) —
   then **exits** (a `migrate ... Exited (0)` line in the output is normal),
3. the **backend** (FastAPI :8000), **Celery worker** + **beat** (background sync jobs), and the
   **frontend** (Vite :5173) start.

The published ports bind to `127.0.0.1`, so they are reachable only from this computer. The
standalone install does not publish PostgreSQL or Redis at all. Do not expose these services
directly to a LAN or the internet.

Leave this terminal running; `Ctrl+C` stops everything. Once the stack is up, you can also
press **d** in the terminal menu to detach (keeps containers running, frees your terminal).
Or start detached from the beginning with `docker compose up -d`.

Sanity checks: `http://localhost:8000/api/health` returns ok, `http://localhost:5173` shows the
login screen. (If a published port is already taken, set e.g.
`BACKEND_HOST_PORT=8001 FRONTEND_HOST_PORT=5174 docker compose up`.)

## 4. Demo data (loaded automatically)

On a **fresh** database the **demo persona** is seeded automatically by the migrate step, so the
app is alive the moment you log in — there's nothing to run. (To start blank instead, set the environment variable `SEED_DEMO=false` before running
`docker compose up`.) Add the example what-if scenarios whenever
you like, in a second terminal:

```bash
docker compose exec backend uv run python ../scripts/seed_scenarios.py   # optional: example what-if scenarios
```

The demo persona is a 52-year-old just past a layoff: severance and unemployment running out,
three properties (a mortgaged primary, a secondary home under contract to sell, a rented-out
income property), a 401(k)/IRA stack, startup equity, ~2 years of net-worth history, and a
SEPP/72(t) bridge plan to 59½. The Retirement page shows the whole story — including the cash
pool brushing zero at ~57 before the planned downsize rescues it. That's the point: this is
what catching a bridge problem *years in advance* looks like.

Demo mechanics worth knowing:

- **Safe**: it refuses to run against a database that already has Monarch-synced data.
- **Safe to re-run**: running it again just refreshes the dates to today and updates in place — no duplicates.
- **Removable**: `docker compose exec backend uv run python ../scripts/seed_demo.py --remove`
  deletes every demo row. Demo rows are manual-source, so they coexist safely with a later real
  Monarch sync until you remove them.

If you started blank (`SEED_DEMO=false`) but still want the Retirement page to project
something, seed just a starter FIRE config:
`docker compose exec backend uv run python ../scripts/seed_config.py`.

## 5. Log in and tour

Open **http://localhost:5173**, log in (`admin` / your password). Suggested order with demo
data: **Dashboard** (net worth + history) → **Retirement** (the projection — hover the event
markers) → **Runway** (cash months remaining) → **Settings → Plan** (every assumption driving
what you just saw — the gear in the sidebar).

## 6. Go live: connect Monarch

When you're ready for your real data, follow [MONARCH_SETUP.md](MONARCH_SETUP.md):
authenticate once, hit **Sync Now**, and enrich your accounts with FIRE roles. Your first sync
**auto-clears the demo persona** (only the FIRE config stays, for you to rebuild under
**Settings → Plan**). Property owners: also see the property import section there and
[PROPERTY_MODULE.md](PROPERTY_MODULE.md).

## 7. Make the config yours

Everything the projections assume lives in one place: **Settings → Plan** (base plan) plus
**scenarios** (named override sets compared on the Retirement page).

- Base config: date of birth, target spending, Social Security, healthcare, withdrawal
  strategy, and the `custom_assumptions` blocks (SEPP plan, property sales, tax profile,
  projection rates — all projections are in **today's dollars**, so $80K/year
  spending stays at $80K purchasing power throughout).
- Scenarios store only their *differences* from the base, so editing the base updates every
  scenario that doesn't override that field.
- Property sales are modeled with `property_sales` entries (any sale month, dynamic value,
  capital gains incl. §121, mortgage payoff via amortization, proceeds to a taxable pool).
  The seeded examples in `scripts/seed_scenarios.py` show the full key set.

## 8. Staying up to date

```bash
docker compose pull               # fetch the latest prebuilt images from GHCR
docker compose up -d              # restart on them; migrate auto-applies any new migrations
```

Contributors developing against the source tree update with a local rebuild instead:

```bash
git pull
docker compose up -d --build      # rebuilds images for any code/dep changes; migrate auto-applies
```

Migrations are always safe to re-run. If a pull changed Python/JS dependencies and a container
errors about a missing package, refresh the cached dependency volumes:
`docker compose up -d --build --renew-anon-volumes` (renews the anonymous `.venv` /
`node_modules` volumes only — your **named data volume is kept**).

## 9. Troubleshooting

Start with the [README's troubleshooting table](../README.md#troubleshooting) and the
[Container Runbook](CONTAINER_RUNBOOK.md). Additional detail:

- **Login fails with the correct password** — `AUTH_PASSWORD_HASH` in `backend/.env` must be a
  bcrypt hash (starts with `$2`). Regenerate by re-running setup with `--force` (step 2). Note:
  the app reads the *mounted* `backend/.env` directly — do not add `env_file:` to the backend
  service in compose (Compose interpolates env_file values and would corrupt the `$` in the
  hash). See the runbook.
- **`migrate` container shows `Exited (0)`** — normal; it ran migrations and quit.
- **Migrations fail on an existing database** — you likely have an older schema. For the demo /
  a throwaway project you can start clean with `docker compose down -v` — **but `-v` DESTROYS
  the data volume**, so never run it against a project holding real data.
- **Celery logs show no tasks registered** — the worker must run with `-I app.tasks.sync_tasks`
  (the compose `celery-worker` command includes it).
- **Port already in use** — remap with `BACKEND_HOST_PORT` / `FRONTEND_HOST_PORT` /
  `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT`, e.g. `BACKEND_HOST_PORT=8001 docker compose up`.
