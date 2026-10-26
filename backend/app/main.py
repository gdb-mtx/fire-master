import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.accounts import router as accounts_router
from app.api.advisor import router as advisor_router
from app.api.auth import router as auth_router
from app.api.setup import router as setup_router
from app.api.cashflow import router as cashflow_router
from app.api.categories import router as categories_router
from app.api.dashboard import router as dashboard_router
from app.api.fire import router as fire_router
from app.api.net_worth import router as net_worth_router
from app.api.properties import router as properties_router
from app.api.spending import router as spending_router
from app.api.sync import router as sync_router
from app.api.tax import router as tax_router
from app.api.transactions import router as transactions_router
from app.core.config import get_settings
from app.engines.fire_projections import ScenarioNotFoundError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup validation
    settings = get_settings()
    if settings.JWT_SECRET_KEY in ("change-me", ""):
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Run first-run setup:\n"
            "  docker compose run --rm backend uv run python -m app.setup   (Docker)\n"
            "  uv run python -m app.setup                             (native dev, from backend/)"
        )
    if not settings.AUTH_PASSWORD_HASH or not settings.AUTH_PASSWORD_HASH.startswith("$2"):
        raise RuntimeError(
            "AUTH_PASSWORD_HASH is not a valid bcrypt hash. Run first-run setup:\n"
            "  docker compose run --rm backend uv run python -m app.setup   (Docker)\n"
            "  uv run python -m app.setup                             (native dev, from backend/)"
        )
    yield
    # Shutdown


app = FastAPI(
    title="FIREMaster",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(ScenarioNotFoundError)
async def _scenario_not_found(_request: Request, exc: ScenarioNotFoundError) -> JSONResponse:
    # Any endpoint taking ?scenario_id= gets a 404 for an unknown id, instead of
    # the base plan returned under the scenario's name.
    return JSONResponse(status_code=404, content={"detail": str(exc)})

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _settings.CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(advisor_router)
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(cashflow_router)
app.include_router(categories_router)
app.include_router(dashboard_router)
app.include_router(fire_router)
app.include_router(net_worth_router)
app.include_router(properties_router)
app.include_router(setup_router)
app.include_router(spending_router)
app.include_router(sync_router)
app.include_router(tax_router)
app.include_router(transactions_router)


@app.get("/api/health")
async def health():
    settings = get_settings()
    return {"status": "ok", "version": "0.1.0", "demo_mode": settings.DEMO_MODE}
