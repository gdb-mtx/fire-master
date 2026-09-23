"""Seed what-if scenarios for the DEMO persona (scripts/seed_demo.py).

The generic examples in seed_scenarios.py are written for the blank-slate
starter persona and don't match the demo persona's properties — scenario
overrides REPLACE the custom_assumptions.property_sales list wholesale, so a
mismatched list would gut the persona's baseline plan. These four are built on
the demo persona's actual story (retire at ~52, SEPP bridge, sell the Desert
Casita at month 6, downsize out of the City Loft at month 60):

1. "Baseline — Sell Casita, Downsize at Year 5"  — no overrides; the plan as configured.
2. "Keep the Desert Casita"                       — hold it 20 years instead: its sale
   moves to month 240, so the projection carries its $2,250/mo cost and shows what
   keeping the getaway really costs.
3. "Downsize the Loft Now"                        — City Loft sale moves from month 60
   to month 12: equity freed 4 years sooner, renting at $2,400/mo from year 1.
4. "Trim Spending to $11K/mo"                     — pure spending-sensitivity what-if;
   no property changes.

The Baseline is activated by default (only when no scenario is already active —
a restart never stomps a visitor's mid-session selection), so the Retirement
page's scenario selector shows an engaged default instead of "No Scenario".

Guard: refuses to run unless the FIRE config carries the demo_persona sentinel
(so it can never write demo scenarios into a real dataset). Idempotent: upserts
by scenario name.

Used by the hosted public demo (docker-compose.demo.yml migrate step). Zip-kit
users exploring demo mode can also run it manually:
    cd backend && uv run python ../scripts/seed_demo_scenarios.py
"""

import asyncio
from datetime import date
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select
from app.core.database import async_session_factory
from app.models.fire_config import FireConfig
from app.models.fire_scenario import FireScenario
from app.engines.plan_months import pin_plan_months


def _pinned(overrides: dict) -> dict:
    """Plan-month offsets (sale_month, ...) pinned to calendar months at seed time."""
    ca = overrides.get("custom_assumptions")
    return {**overrides, "custom_assumptions": pin_plan_months(ca, date.today())} if ca else overrides


# The demo persona's two property_sales entries (must match seed_demo.py —
# overriding the list replaces it wholesale, so scenarios that touch either
# property restate BOTH entries).
CASITA_SALE = {
    "key": "desert_casita",
    "re_bucket": "secondary",
    "sale_month": 6,
    "value": 386_000,
    "cost_basis": 361_000,
    "agent_fee_pct": 0.06,
    "ltcg_rate": 0.15,
    "state_tax_rate": 0.045,
    "section_121_exclusion": 0,
    "current_mortgage_balance": 238_000,
    "mortgage_rate": 0.0675,
    "mortgage_pi": 1_650,
    "monthly_cost": 2_250,
    "in_base_burn": False,
    "post_sale_rent": 0,
    "monthly_income": 0,
    "proceeds_to": "taxable",
    "suppress_cashflow_match": "desert casita",
}

LOFT_SALE = {
    "key": "city_loft",
    "re_bucket": "primary",
    "sale_month": 60,
    "value": 468_000,
    "cost_basis": 468_000,
    "agent_fee_pct": 0.06,
    "ltcg_rate": 0.15,
    "state_tax_rate": 0.0,
    "section_121_exclusion": 500_000,
    "current_mortgage_balance": 296_000,
    "mortgage_rate": 0.062,
    "mortgage_pi": 2_850,
    "monthly_cost": 5_900,
    "in_base_burn": True,
    "post_sale_rent": 2_400,
    "monthly_income": 360,
    "proceeds_to": "taxable",
}


SCENARIOS = [
    {
        "name": "Baseline — Sell Casita, Downsize at Year 5",
        "description": (
            "The plan as configured: sell the Desert Casita in 6 months, downsize "
            "out of the City Loft at year 5, bridge to 59½ on severance + "
            "SEPP + the RRSP drawdown. Every other scenario is compared against this."
        ),
        "is_active": False,
        "overrides": {},
    },
    {
        "name": "Keep the Desert Casita",
        "description": (
            "Hold the getaway for 20 more years instead of selling this summer. "
            "The projection keeps paying its $2,250/mo carry cost (mortgage, HOA, "
            "utilities) the whole time — the honest price of keeping the place you love."
        ),
        "is_active": False,
        "overrides": {
            "custom_assumptions": {
                "property_sales": [
                    {**CASITA_SALE, "sale_month": 240, "suppress_cashflow_match": "desert casita"},
                    LOFT_SALE,
                ],
            },
        },
    },
    {
        "name": "Downsize the Loft Now",
        "description": (
            "Sell the City Loft at month 12 instead of year 5: ~$170K of equity "
            "compounds in the taxable pool four years sooner, traded against "
            "renting at $2,400/mo from year one."
        ),
        "is_active": False,
        "overrides": {
            "custom_assumptions": {
                "property_sales": [
                    CASITA_SALE,
                    {**LOFT_SALE, "sale_month": 12},
                ],
            },
        },
    },
    {
        "name": "Trim Spending to $11K/mo",
        "description": (
            "Pure spending sensitivity: cut the target from $13,000/mo to "
            "$11,000/mo with no property changes. Watch what a $2K/mo trim does "
            "to the bridge years and the terminal wealth."
        ),
        "is_active": False,
        # Top-level key overrides the fire_config column (cents): $132,000/yr
        "overrides": {"target_annual_spending": 13_200_000},
    },
]


async def main():
    async with async_session_factory() as session:
        # Guard: demo persona only — never write demo scenarios into a real dataset
        config = (await session.execute(select(FireConfig))).scalars().first()
        sentinel = bool((config.custom_assumptions or {}).get("demo_persona")) if config else False
        if not sentinel:
            print("REFUSED: FIRE config is not the demo persona (no demo_persona sentinel).")
            print("This script seeds scenarios for the seed_demo.py persona only.")
            sys.exit(1)

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

        # Default the Baseline to active so the scenario UI starts engaged —
        # but never steal focus from a scenario someone already activated.
        result = await session.execute(select(FireScenario))
        all_scenarios = result.scalars().all()
        if not any(s.is_active for s in all_scenarios):
            for s in all_scenarios:
                if s.name.startswith("Baseline"):
                    s.is_active = True
                    print(f"  ACTIVATE '{s.name}' (default)")
                    break

        await session.commit()
        print(f"Done: {created} created, {updated} updated.")


if __name__ == "__main__":
    asyncio.run(main())
