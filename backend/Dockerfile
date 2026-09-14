FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# Install system dependencies for asyncpg and psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Pin the build tool instead of executing whatever `latest` points to.
COPY --from=ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 /uv /uvx /bin/

WORKDIR /app/backend

# NOTE: this image is built with the REPO ROOT as the build context (see
# docker-compose.yml and .github/workflows/docker-images.yml), NOT ./backend. That lets
# the image bake in the repo-root scripts/ + config/ dirs, which the standalone
# (no-bind-mount) docker-compose.public.yml needs for its first-run demo seed.
# A root .dockerignore keeps the rest of the repo (frontend, .git, venvs, secrets) out.

# The lockfile is required: image builds must use the dependency graph reviewed
# and committed with this source tree.
COPY backend/pyproject.toml backend/uv.lock ./

# Never fall back to resolving a new dependency graph during an image build.
RUN uv sync --frozen --no-dev

# Application code into /app/backend, plus the seed scripts and config the demo seed
# reads. scripts/seed_*.py expect <repo>/backend as a sibling dir — here that's
# /app/backend, so `python ../scripts/seed_demo.py` from WORKDIR resolves correctly.
COPY backend/ ./
COPY scripts /app/scripts
COPY config /app/config

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
