#!/bin/bash
# OPTIONAL native/contributor dev path — NOT the documented user install.
# Most users should run `docker compose up` (see README "Quick start").
# This runs Postgres/Redis in Docker but the backend, Celery worker, and frontend
# directly on the host via uv/npm, for fast hot-reload during development.
# Requires: uv, Node 22.22+, bash (macOS/Linux/WSL2). Usage: ./scripts/start.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV="$HOME/.local/bin/uv"

cd "$PROJECT_DIR"

# Verify .env exists
if [ ! -f backend/.env ]; then
    echo "ERROR: No .env file found. Run scripts/setup.sh first."
    exit 1
fi

# Kill any stale processes on our ports
echo "Cleaning up stale processes..."
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
lsof -ti:5173 | xargs kill -9 2>/dev/null || true
sleep 1

# Verify Docker daemon is reachable before any compose calls
if ! docker info > /dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running."
  echo "       Start Docker Desktop (open -a Docker), wait for it to finish launching, then re-run this script."
  exit 1
fi

# Start infrastructure (postgres + redis) — skip wait if already running
PG_RUNNING=$(docker compose ps --status running postgres --format '{{.Name}}' 2>/dev/null)
REDIS_RUNNING=$(docker compose ps --status running redis --format '{{.Name}}' 2>/dev/null)

if [ -n "$PG_RUNNING" ] && [ -n "$REDIS_RUNNING" ]; then
  echo "PostgreSQL and Redis already running."
else
  echo "Starting PostgreSQL and Redis..."
  docker compose up -d postgres redis
  sleep 2
fi

# Apply migrations
echo "Applying database migrations..."
cd "$PROJECT_DIR/backend"
$UV run alembic upgrade head

# Start backend
echo "Starting backend on :8000..."
$UV run uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

# Ensure cleanup on exit
cleanup() {
  echo ""
  echo "Shutting down..."
  kill $BACKEND_PID 2>/dev/null
  kill $CELERY_PID 2>/dev/null
  kill $FRONTEND_PID 2>/dev/null
  exit 0
}
trap cleanup INT TERM

# Wait for backend to be ready
echo "Waiting for backend..."
BACKEND_READY=false
for i in $(seq 1 30); do
  if curl -s --max-time 2 http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "Backend ready."
    BACKEND_READY=true
    break
  fi
  if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "ERROR: Backend process exited unexpectedly."
    exit 1
  fi
  sleep 1
done

if [ "$BACKEND_READY" = false ]; then
  echo "ERROR: Backend did not become ready within 30 seconds."
  kill $BACKEND_PID 2>/dev/null
  exit 1
fi

# Start Celery worker
echo "Starting Celery worker..."
$UV run celery -A app.tasks.celery_app worker --loglevel=info -I app.tasks.sync_tasks &
CELERY_PID=$!

# Start frontend
echo "Starting frontend on :5173..."
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
cd "$PROJECT_DIR/frontend"
if [ ! -d node_modules ]; then
    echo "Installing frontend dependencies (first run)..."
    npm install
fi
npm run dev &
FRONTEND_PID=$!

cd "$PROJECT_DIR"

echo ""
echo "FIREMaster is running:"
echo "  Dashboard: http://localhost:5173"
echo "  API:       http://localhost:8000/api/health"
echo "  API Docs:  http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services."

# Wait for any to exit
wait
