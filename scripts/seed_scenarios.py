"""Seed example FIRE scenarios into the database.

Creates three starter scenarios that work against the persona from
scripts/seed_config.py (none is activated — pick one in the UI):

1. "Baseline"                          — no overrides; the base config as-is.
2. "Sell Rental Property at Month 24"  — generic property_sales mechanism: sell an
   income property two years out, route net proceeds into a taxable brokerage pool.
3. "Downsize + Relocate"               — sell the primary home at month 36, switch
   to renting, proceeds to the taxable pool.

Scenarios store overrides-only JSONB; the engine deep-merges them onto the base
config (custom_assumptions subkeys merge, everything else replaces). The
property_sales entries below show the full key set — replace values with your own.
re_bucket tokens: primary | secondary | income (which fire_role equity bucket the
sale empties).

Idempotent: upserts by scenario name.

Usage:
    cd backend && uv run python ../scripts/seed_scenarios.py
"""

import asyncio
from datetime import date
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select
from app.core.database import async_session_factory
from app.models.fire_scenario import FireScenario
from app.engines.plan_months import pin_plan_months


def _pinned(overrides: dict) -> dict:
    """Plan-month offsets (sale_month, ...) pinned to calendar months at seed time."""
    ca = overrides.get("custom_assumptions")
    return {**overrides, "custom_assumptions": pin_plan_months(ca, date.today())} if ca else overrides


SCENARIOS = [
    {
        "name": "Baseline",
        "description": "Base config with no overrides — the reference point every other scenario is compared against.",
        "is_active": False,
        "overrides": {},
    },
    {
        "name": "Sell Rental Property at Month 24",
        "description": "Sell the income property two years from now; net proceeds compound in a taxable brokerage at 6% real, drawn before retirement accounts.",
        "is_active": False,
        "overrides": {
            "custom_assumptions": {
                "taxable_pool": {"starting_balance": 0, "return_rate": 0.06},
                "property_sales": [
                    {
                        "key": "rental_condo",
                        "re_bucket": "income",
                        "sale_month": 24,
                        "value": 400_000,
                        "cost_basis": 250_000,
                        "agent_fee_pct": 0.06,
                        "ltcg_rate": 0.15,
                        "state_tax_rate": 0.05,
                        "section_121_exclusion": 0,
                        "current_mortgage_balance": 0,
                        "monthly_cost": 1_000,
                        "in_base_burn": True,
                        "post_sale_rent": 0,
                        "monthly_income": 2_400,
                        "proceeds_to": "taxable",
                    },
                ],
            },
        },
    },
    {
        "name": "Downsize + Relocate",
        "description": "Sell the primary home at month 36 and switch to renting; proceeds to the taxable pool. Mortgage payoff amortizes the live balance forward.",
        "is_active": False,
        "overrides": {
            "custom_assumptions": {
                "taxable_pool": {"starting_balance": 0, "return_rate": 0.06},
                "property_sales": [
                    {
                        "key": "primary_home",
                        "re_bucket": "primary",
                        "sale_month": 36,
                        "value": 500_000,
                        "cost_basis": 400_000,
                        "agent_fee_pct": 0.06,
                        "ltcg_rate": 0.15,
                        "state_tax_rate": 0.05,
                        "section_121_exclusion": 250_000,
                        "current_mortgage_balance": 300_000,
                        "mortgage_rate": 0.065,
                        "mortgage_pi": 2_500,
                        "monthly_cost": 4_000,
                        "in_base_burn": True,
                        "post_sale_rent": 2_200,
                        "monthly_income": 0,
                        "proceeds_to": "taxable",
                    },
                ],
            },
        },
    },
]


async def main():
    async with async_session_factory() as session:
        result = await session.execute(select(FireScenario))
        existing = {s.name: s for s in result.scalars().all()}

        created = 0
        updated = 0
        for s_data in SCENARIOS:
            if s_data["name"] in existing:
                scenario = existing[s_data["name"]]
                scenario.description = s_data["description"]
                scenario.overrides = _pinned(s_data["overrides"])
                updated += 1
                print(f"  UPDATE scenario '{s_data['name']}'")
            else:
                scenario = FireScenario(
                    name=s_data["name"],
                    description=s_data["description"],
                    overrides=_pinned(s_data["overrides"]),
                    is_active=s_data["is_active"],
                )
                session.add(scenario)
                created += 1
                print(f"  CREATE scenario '{s_data['name']}'")

        await session.commit()
        print(f"\nDone. Created {created}, updated {updated} scenario(s).")


if __name__ == "__main__":
    asyncio.run(main())
