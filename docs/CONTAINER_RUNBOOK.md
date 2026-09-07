# Container Runbook — how the Docker path works, and how to rewind it

> **Audience: the owner (you), not end users.** The "what actually changed, how to run it,
> and how to undo it if it breaks" reference for the Docker setup.
>
> Written to be read when something is on fire and you don't remember the details.

---

## TL;DR — the two ways to run FIREMaster now

| | **Native (contributor) path** | **Docker (user) path** |
|---|---|---|
| Command | `./scripts/start.sh` | `docker compose up` |
| Backend runs | on your Mac (host) via `uv` | inside a container |
| Frontend runs | on your Mac via `npm` | inside a container |
| Postgres/Redis | Docker containers | same Docker containers |
| `.env` `DATABASE_URL` | `@localhost:5432` (used as-is) | overridden to `@postgres:5432` by compose |
| Who it's for | **you**, for fast edit-reload dev | **new users**, easiest install |

**Both paths share the same Postgres data** (the `firemaster-postgres-1` container / `firemaster_pgdata`
volume). Switching between them does not move or copy your data.

---

## The single most important safety rule

```
NEVER run `docker compose down -v` on the default project.
```

The `-v` flag **deletes the data volumes** — that is your real financial database
(`firemaster_pgdata`). `docker compose down` (no `-v`) is safe: it stops containers but keeps
data. Only ever use `-v` against the throwaway **`fmtest`** project (see Testing below).

---

## How to rewind (in order of severity)

### 1. Undo a single change
The work is committed in logical groups on the `containerize` branch (run `git log --oneline`:
the Docker stack, the setup module, the docs relabel, the runbook). To back one out:
```bash
git log --oneline           # find the commit
git revert <sha>            # makes a new commit that undoes it
```

### 2. Throw away ALL the container work, keep your app working
The native path (`./scripts/start.sh`) is untouched by any of this. To abandon the whole
effort and go back to exactly how things were:
```bash
git checkout main
git branch -D containerize   # optional: delete the branch entirely
./scripts/start.sh           # your normal workflow, unchanged
```
Your `backend/.env` (with `@localhost`) and your data are exactly as they were.

### 3. "I shut my servers down and want them back"
```bash
./scripts/start.sh           # relaunches backend :8000, frontend :5173, celery
```
`start.sh` detects that Postgres/Redis are already running and reuses them.

### 4. Nuke all containers and start clean — WITHOUT losing data
```bash
docker compose down          # stops + removes containers, KEEPS volumes (your data)
docker compose up            # rebuilds the world; data still there
```

---

## What changed, file by file (Changes 1–5)

> Filled in as each change lands. Each heading notes the commit and how to verify it.

### Change 1 — Networking fix + volume-shadow fix (`docker-compose.yml`)
- **Networking:** added an `environment:` block to `backend`, `celery-worker`, `celery-beat`
  overriding `DATABASE_URL`, `DATABASE_URL_SYNC`, `REDIS_URL` to use the **service names**
  (`@postgres:5432`, `redis:6379`) instead of `localhost`. Compose `environment:` wins over
  the `env_file`, and pydantic reads OS-env over its `.env` file — so your `backend/.env`
  stays `@localhost` and the **native path still works**.
- **Volume-shadow fix (not in the original plan — would have crashed the backend):** the
  compose file bind-mounts `./backend:/app/backend`, which overlays your Mac's
  `backend/.venv` (macOS arm64 Python) on top of the container's Linux `.venv`. Running
  macOS binaries in a Linux container = instant crash. Fix: an **anonymous volume**
  `- /app/backend/.venv` on each backend-image service, which preserves the image's Linux
  venv. Same idea applied to the frontend's `node_modules` in Change 2.
- **Verify:** `docker compose up backend` → logs show a successful DB connection, not
  `Connection refused`.

### Change 2 — Frontend container (`frontend/Dockerfile`, `docker-compose.yml`)
- New `frontend/Dockerfile` (node:22.22-slim): `npm ci`, copy app, run the Vite dev server
  with **`--host`** (binds `0.0.0.0` — without it `:5173` is unreachable from the host).
- New `frontend` compose service: publishes
  `127.0.0.1:${FRONTEND_HOST_PORT:-5173}:5173`, sets
  **`VITE_API_URL=http://backend:8000`** (the Vite proxy in `vite.config.ts` runs *inside* the
  container, so it must reach the backend by service name), and an anonymous
  `- /app/frontend/node_modules` volume (same shadow fix as the backend's `.venv`).
- **Verify:** `docker compose up` → open `http://localhost:5173`, dashboard loads, API calls work.
- **If HMR (hot reload) misbehaves on a remapped port:** add `server.hmr.clientPort` to
  `vite.config.ts`. Not needed on the default `:5173`.

### Change 3 — Auto-migrations (`docker-compose.yml`)
- New one-shot `migrate` service: runs `uv run alembic upgrade head`, then a first-run demo
  seed, then exits. `backend`, `celery-worker`, `celery-beat` now
  `depends_on: migrate: condition: service_completed_successfully`, so the schema (and demo
  data) always exist before they start.
- **First-run demo auto-seed:** after migrating, the step runs `seed_demo.py --if-empty`, which
  seeds the demo persona **only** when the DB has no accounts and `SEED_DEMO != false`. It is
  wrapped in `|| true` so a seed hiccup can never block startup, and a re-run on a populated DB
  is a no-op (idempotent guard). Start blank with `SEED_DEMO=false docker compose up`.
- **Chosen over the plan's entrypoint-script approach on purpose:** a single migrate service
  avoids three containers racing `alembic upgrade` at once, and avoids a shell entrypoint that
  CRLF line endings could break.
- Alembic reads `DATABASE_URL_SYNC` from the env ([backend/alembic/env.py](../backend/alembic/env.py)),
  which the migrate service sets to `@postgres:5432`.
- **Verify (fresh DB):** under `fmtest`, `docker compose ... up`; `migrate` logs show
  `Running upgrade … `, then the backend starts against a populated schema.

### Change 4 — Cross-platform setup (`backend/app/setup.py`, `backend/app/main.py`)
- New `python -m app.setup`: generates a JWT secret (`secrets.token_hex`) and a bcrypt admin
  hash, writes `backend/.env`. Pure Python + bcrypt (in the image) — no openssl/sed/read/bash,
  so it runs identically on Windows. Interactive password prompt, or non-interactive via
  `FIREMASTER_ADMIN_PASSWORD` (+ optional `FIREMASTER_ADMIN_USERNAME`, `FIREMASTER_FORCE_SETUP=1`).
- It writes `backend/.env` (the mounted file), so the secret persists on the host and is read by
  every service. It does **not** read `.env.example` (repo root isn't mounted) — the template is
  embedded in the module.
- `main.py` startup errors now point at this command instead of a manual bcrypt one-liner.
- **Run it once:** `docker compose run --rm backend uv run python -m app.setup`.

### Change 5 — Retire `start.sh` as the user path (`scripts/`, `README`)
- `scripts/start.sh` and `scripts/setup.sh` are **kept**, but their header comments now mark them
  as the *optional native/contributor* path. Nothing was deleted — the native workflow is intact.
- README Quick start now leads with `docker compose up`; the native path is a clearly-labeled
  "Contributor / native dev (optional)" subsection.

### Supporting — `.dockerignore`, `.gitattributes`
- `backend/.dockerignore` / `frontend/.dockerignore`: keep `.venv`/`node_modules`/secrets out of
  the image build context (the backend's `COPY . .` was otherwise baking `.env` into a layer).
- `.gitattributes`: `*.sh text eol=lf` — the CRLF guard.
- Also folded into Change 1: the compose `celery-worker` command was **missing**
  `-I app.tasks.sync_tasks` (required for task autodiscovery per CLAUDE.md) — added.

---

## Still open (future, not blocking)

- ~~**Multi-arch prebuilt images:**~~ **Done** (Jun 2026).
  `.github/workflows/docker-images.yml` builds amd64 + arm64 backend/frontend
  images and pushes them to GHCR on push-to-main (path-filtered) + manual dispatch. Both
  `docker-compose.yml` services now carry an `image:` alongside `build:`, so `docker compose up`
  **pulls** (`docker compose pull` to refresh) and `up --build` / offline falls back to a local
  build. See "Prebuilt images" below for the image-build restructure and the standalone compose.
- ~~**Windows + amd64 acceptance test:**~~ **Done** (Jun 2026). Validated on Azure
  `Standard_D4s_v7` / Windows Server 2025: Docker Desktop + WSL2 nested virt, amd64 images,
  demo auto-seed, scenarios — all passing. No bash/uv/node on the host.

---

## Prebuilt images (GHCR) and the standalone compose

> The point:
> decouple *shipping the product* from *shipping the code* — users pull images, they don't clone.

### The workflow — `.github/workflows/docker-images.yml`
- Two parallel jobs (backend, frontend) → `ghcr.io/gdb-mtx/firemaster-backend` and
  `…/firemaster-frontend`, each multi-arch (`linux/amd64` + `linux/arm64`).
- Tags: `latest` (default branch only), `sha-<7char>`, `YYYY.MM.DD`.
- Trigger: push to `main` (path-filtered to `backend/`, `frontend/`, `scripts/`, `config/`,
  the compose file, the workflow) + manual `workflow_dispatch`. Auth is the built-in
  `GITHUB_TOKEN` (needs `packages: write`) — no PAT.
- **One-time, post-first-run (manual — CI can't do these):** make BOTH GHCR packages **public**
  (GitHub profile → Packages → each → Settings → visibility → Public) so pulls need no login.

### Image-build restructure (backend only)
The backend image's build context is now the **repo root**, not `./backend`
(`build: { context: ., dockerfile: backend/Dockerfile }`; the workflow matches). Why: the
standalone compose can't bind-mount `scripts/` + `config/`, so the demo first-run seed
(`../scripts/seed_demo.py`) would have nothing to run. The Dockerfile now `COPY`s `backend/`,
`scripts`, and `config` into the image; a new **root `.dockerignore`** keeps everything else
(frontend, `.git`, venvs, **secrets**) out of the larger context. The dev compose still
bind-mounts `./scripts` + `./config` over the baked copies, so native edit-reload is unchanged.
- **Verify the bake:** `docker build -f backend/Dockerfile -t fm-test . && docker run --rm
  fm-test ls /app/scripts /app/config` → the seed scripts + config files are listed.
- **Gotcha:** never let `backend/.env` into the image — the root `.dockerignore` excludes
  `**/.env`; if you ever see login break on a *pulled* image, confirm that exclusion held.

### Standalone compose — `docker-compose.public.yml`
The file shipped in the install kit (firemaster.io): GHCR images only, **no `build:`**, **no
source bind-mounts**, no `scripts/`/`config/` on disk (they're in the image). Run it with
`-f docker-compose.public.yml`. Differences from the dev compose worth knowing:
- PostgreSQL and Redis have no host ports. The API and UI publish only on `127.0.0.1`.
- **`.env` is a single-file bind mount** (`./backend/.env:/app/backend/.env`), not `env_file`
  (the interpolation bug — see the troubleshooting table). A bind mount of a **non-existent**
  host file is created as a *directory*, which breaks `app.setup`, so the kit must ship an
  empty `backend/.env` placeholder that the setup step fills in.
- **No Monarch persistence** (no `.monarch_session` volume): the free tier is demo-only; live
  sync is the paid gate (Phase 3, not yet built).
- Pull/update: `docker compose -f docker-compose.public.yml pull && … up -d`.

---

## Testing safely (the `fmtest` isolated project)

All container testing runs under a **separate Docker Compose project** named `fmtest`, with
**remapped host ports**, so it cannot collide with your running services or touch your real
data volumes. A `fmtest` project gets its own network and its own EMPTY volumes
(`fmtest_pgdata`), entirely separate from the real `firemaster_*` volumes.

The loopback-only host ports are parameterized in `docker-compose.yml`
(`${POSTGRES_HOST_PORT:-5432}`, `${REDIS_HOST_PORT:-6379}`, `${BACKEND_HOST_PORT:-8000}`,
`${FRONTEND_HOST_PORT:-5173}`), so an isolated stack just sets them inline:

```bash
# bring up an isolated copy on non-conflicting ports
POSTGRES_HOST_PORT=5599 REDIS_HOST_PORT=6399 BACKEND_HOST_PORT=8020 FRONTEND_HOST_PORT=5190 \
  docker compose -p fmtest up

# tear it down INCLUDING its throwaway data (safe: only touches fmtest_* volumes)
docker compose -p fmtest down -v
```

(Those same env vars are the escape hatch for any **user** whose `:5432`/`:5173` is already
taken — no file edits, just set the var.)

### Source-only local images

Use `docker-compose.local-build.yml` when the checked-out source, rather than a mutable registry
tag, must be the source of truth. The overlay assigns distinct `:local` image names and sets
`pull_policy: never`; the Dockerfiles require their lockfiles and use frozen installs. It also
uses the production multi-stage frontend, so the running image contains nginx and compiled
static assets instead of the Node development server and toolchain.

```bash
docker compose -f docker-compose.public.yml -f docker-compose.local-build.yml pull postgres redis
docker compose -f docker-compose.public.yml -f docker-compose.local-build.yml build --pull
docker compose -f docker-compose.public.yml -f docker-compose.local-build.yml up --pull never
```

---

## Troubleshooting (the gotchas that actually bite)

| Symptom | Cause | Fix |
|---|---|---|
| Backend logs `Connection refused` to DB | `.env` points at `localhost` inside the container | Change 1 override missing/typo'd; confirm `environment:` block on the service |
| Backend exits with `bad interpreter` / `exec format error` | The `.venv` volume-shadow bug | confirm the `- /app/backend/.venv` anonymous volume is present |
| Frontend container starts but browser can't reach `:5173` | Vite bound to localhost inside the container | Dockerfile must run `vite --host` (binds `0.0.0.0`) |
| Frontend loads but all API calls 500/fail | Vite proxy still points at `localhost:8000` | `VITE_API_URL=http://backend:8000` must be set on the frontend service |
| `migrate` container shows "Exited (0)" | **Normal** — it's a one-shot that runs migrations then quits | nothing to fix |
| Backend crashes: `JWT_SECRET_KEY must be set…` | No valid `.env` yet | run the setup command (Change 4): `docker compose run --rm backend uv run python -m app.setup` |
| Login always fails with the *correct* password | **The env_file `$`-interpolation bug** (found during build): Compose interpolates `env_file` values, stripping `$…` sequences out of the bcrypt hash | the app services intentionally have **no** `env_file` — secrets load from the *mounted* `backend/.env` via pydantic. Do **not** re-add `env_file:` to backend/celery/migrate |
| `ModuleNotFoundError` running `docker compose … backend python …` | Bare `python` uses the system interpreter, not the uv venv | prefix with `uv run`: `… backend uv run python …` |
| Old Python package after changing deps | The anonymous `.venv` volume persists across rebuilds and can hold stale deps | rebuild, then `docker compose up --build --renew-anon-volumes` (renews the anon `.venv` only; the named data volume is kept) |
| Port already in use on `up` | Your native stack or sister project is running | remap with `BACKEND_HOST_PORT=…` etc., or stop the native app layer |
| Demo data vanished after connecting Monarch | **Expected** — the first real sync auto-clears the demo persona (marker-scoped: only demo rows, real data untouched) | keep it with `AUTO_CLEAR_DEMO=false`; the FIRE config stays for you to rebuild on /config |
| Need a demo that can NEVER pull real data (shared/public/scratch) | default install can sync if a Monarch session is present | `DEMO_MODE=true docker compose up` — hard-disables Monarch sync (the session is **never loaded**), hides the Sync button, and locks the app to seeded demo data. Mandatory for any hosted/public demo |
| Edited a `.sh` file on Windows, container won't start | CRLF line endings | `.gitattributes` forces `*.sh` to LF; re-checkout the file |

### Useful inspection commands
```bash
docker compose ps                      # what's running in this project
docker compose logs -f backend         # follow the backend logs
docker compose logs migrate            # see what the migration step did
docker ps -a                           # ALL containers (every project)
docker compose config                  # the fully-resolved compose file (after env substitution)
```

---

## Appendix — the running-services map (as of containerization work)

Three independent things run on this machine; don't confuse them:

- **FIRE quant sister project** — `:8001` backend (`api.main`), `:5174` vite. *Different project,
  leave alone.*
- **FIREMaster (yours)** — native: `:8000` backend (`app.main`), `:5173` vite; data in
  `firemaster-postgres-1` / `firemaster-redis-1`.
- **FIREMaster (demo)** — native: `:8010` backend, `:5180` vite; data in standalone `fmdemo-pg`
  (`:5499`).

Scoped shutdown of just the FIREMaster app layer (keeps data + sister project):
```bash
pkill -f 'uvicorn app.main:app'
pkill -f 'FIREMaster/backend/.venv/bin/celery'
pkill -f 'uv run celery -A app.tasks.celery_app'
pkill -f 'FIREMaster/frontend/node_modules/.bin/vite'
```
