"""Setup status — the install-kit agent's first call.

Replaces prose state-detection in the kit CLAUDE.md: instead of inferring the
install lifecycle from several endpoints, one read-only call reports what's
configured, what the valid values are, and what to do next (in order).
Computes nothing, changes nothing.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.engines.fire_projections import (
    ILLIQUID_ROLES,
    EDUCATION_ROLES,
    LIQUID_ROLES,
    RE_ASSET_ROLES,
    RE_LIABILITY_ROLES,
    RETIREMENT_ROLES,
    SPECULATIVE_ROLES,
)
from app.models.account import Account
from app.models.fire_config import FireConfig
from app.models.fire_scenario import FireScenario
from app.models.income_source import IncomeSource
from app.models.property import Property
from app.models.property_rule import PropertyRule
from app.models.enums import DataSource
from app.schemas.setup import SetupStatusResponse

router = APIRouter(prefix="/api/setup", tags=["setup"])

VALID_FIRE_ROLES = {
    "liquid": sorted(LIQUID_ROLES),
    "retirement": sorted(RETIREMENT_ROLES),
    "real_estate_assets": sorted(RE_ASSET_ROLES),
    "real_estate_liabilities": sorted(RE_LIABILITY_ROLES),
    "illiquid": sorted(ILLIQUID_ROLES),
    "education": sorted(EDUCATION_ROLES),
    # Volatile assets (crypto etc.) — counted in net worth, deliberately NOT cash
    # and NOT bridge fuel (Jul 27 decision): inert in projections, like illiquid.
    "speculative": sorted(SPECULATIVE_ROLES),
    # Conventional-but-inert: counted in net worth "other", contributes nothing to
    # projections (vehicles etc.). The demo persona uses it — not a typo.
    "inert_other": ["depreciating"],
    "system": ["system"],
}
_ALL_ROLES = {r for group in VALID_FIRE_ROLES.values() for r in group}


def compute_setup_status(
    *,
    demo_mode: bool,
    demo_persona: bool,
    accounts_total: int,
    accounts_monarch: int,
    accounts_with_fire_role: int,
    roles_in_use: list[str],
    config_present: bool,
    config_updated_at: str | None,
    sepp_configured: bool,
    properties: int,
    property_rules: int,
    scenarios_total: int,
    active_scenario: str | None,
    income_sources_active: int,
) -> SetupStatusResponse:
    """Pure state/next-steps decision — the route just feeds it counts."""
    unknown = sorted({r for r in roles_in_use if r and r not in _ALL_ROLES})

    if accounts_total == 0:
        state = "empty"
    elif demo_persona and accounts_monarch == 0:
        state = "demo"
    elif accounts_with_fire_role == 0 or demo_persona:
        state = "half_onboarded"
    else:
        state = "onboarded"

    steps: list[str] = []
    if state == "empty":
        steps = [
            "No accounts exist. Connect Monarch (see CONNECT_MONARCH.md) or seed the demo persona.",
        ]
    elif state == "demo":
        steps = [
            "This is the seeded DEMO persona — every number is synthetic. Say so in any analysis.",
            "To go live: connect Monarch (CONNECT_MONARCH.md); the first real sync auto-clears demo data.",
        ]
    elif state == "half_onboarded":
        steps = [
            "Real accounts exist but setup is incomplete — do NOT present projections as findings yet.",
            f"Enrich fire_role on real accounts ({accounts_with_fire_role}/{accounts_total} done) via "
            "PATCH /api/accounts/{id}/enrichment — valid values are in valid_fire_roles.",
            "Rebuild the FIRE config with the user's real numbers — the demo persona's config "
            "deliberately survives as a template (demo_persona stays true until it's rebuilt).",
            "Set custom_assumptions.sepp balances: retirement money enters projections ONLY through "
            "the SEPP config block — account balances under retirement roles are ignored by the "
            "wealth-pool projection.",
        ]
    else:
        steps = [
            "Setup looks complete — normal operation.",
            "Standing check: diff live IRA balances against custom_assumptions.sepp — scenario "
            "projections run on config-side balances, which go stale against the market.",
        ]
    if unknown:
        steps.append(
            f"WARNING: unrecognized fire_role value(s) {unknown} — these map to no pool and the "
            "account contributes nothing to projections. Fix via the valid_fire_roles vocabulary."
        )
    if demo_mode:
        steps.append(
            "DEMO_MODE lock is on (hosted demo): Monarch sync is disabled by design."
        )

    return SetupStatusResponse(
        state=state,
        demo_mode=demo_mode,
        demo_persona=demo_persona,
        accounts_total=accounts_total,
        accounts_monarch=accounts_monarch,
        accounts_with_fire_role=accounts_with_fire_role,
        unknown_fire_roles=unknown,
        valid_fire_roles=VALID_FIRE_ROLES,
        config_present=config_present,
        config_updated_at=config_updated_at,
        sepp_configured=sepp_configured,
        properties=properties,
        property_rules=property_rules,
        scenarios_total=scenarios_total,
        active_scenario=active_scenario,
        income_sources_active=income_sources_active,
        next_steps=steps,
    )


@router.get("/status", response_model=SetupStatusResponse)
async def get_setup_status(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    accounts = (await db.execute(select(Account.fire_role, Account.source))).all()
    config = (await db.execute(select(FireConfig))).scalars().first()
    ca = (config.custom_assumptions or {}) if config else {}
    sepp = ca.get("sepp", {}) or {}
    active = (
        await db.execute(select(FireScenario.name).where(FireScenario.is_active == True))
    ).scalars().first()

    async def _count(q):
        return (await db.execute(q)).scalar() or 0

    return compute_setup_status(
        demo_mode=get_settings().DEMO_MODE,
        demo_persona=bool(ca.get("demo_persona")),
        accounts_total=len(accounts),
        accounts_monarch=sum(1 for a in accounts if a.source == DataSource.MONARCH),
        accounts_with_fire_role=sum(1 for a in accounts if a.fire_role),
        roles_in_use=[a.fire_role for a in accounts if a.fire_role],
        config_present=config is not None,
        config_updated_at=config.updated_at.isoformat() if config and config.updated_at else None,
        sepp_configured=bool(
            sepp.get("ira_a_balance") or sepp.get("ira_b_balance") or sepp.get("sepp_monthly")
        ),
        properties=await _count(select(func.count()).select_from(Property)),
        property_rules=await _count(select(func.count()).select_from(PropertyRule)),
        scenarios_total=await _count(select(func.count()).select_from(FireScenario)),
        active_scenario=active,
        income_sources_active=await _count(
            select(func.count()).select_from(IncomeSource).where(IncomeSource.is_active == True)
        ),
    )
