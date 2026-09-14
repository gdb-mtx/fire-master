"""Seed a starter FIRE config so a fresh install renders projections immediately.

Generic persona: age ~50, $10K/mo target spending, 4% SWR, retire at 55, $2,500/mo
Social Security at 67. Replace every value on the /fire/config page afterwards —
this exists so the Retirement dashboard isn't empty on first run.

Idempotent and non-clobbering: if a config row already has a date_of_birth set,
the script assumes you've configured the app and exits without touching anything.

Usage:
    cd backend && uv run python ../scripts/seed_config.py
"""

import asyncio
import sys
from datetime import date
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select
from app.core.database import async_session_factory
from app.models.fire_config import FireConfig

STARTER = dict(
    date_of_birth=date(1976, 1, 1),
    target_retirement_age=55,
    life_expectancy=90,
    fire_variant="regular",
    safe_withdrawal_rate=4.0,
    expected_annual_return=7.0,
    expected_inflation_rate=3.0,
    target_annual_spending=12_000_000,  # $120,000/yr = $10K/mo (cents)
    social_security_monthly=250_000,  # $2,500/mo (cents)
    social_security_start_age=67,
    healthcare_monthly_cost=80_000,  # $800/mo pre-Medicare (cents)
    post_medicare_healthcare_monthly_cost=70_000,  # $700/mo Medicare + supplements (cents)
    medicare_start_age=65,
    rmd_start_age=73,
    custom_assumptions={
        "tax": {"filing_status": "single", "household_size": 1},
    },
)


async def main():
    async with async_session_factory() as session:
        result = await session.execute(select(FireConfig).limit(1))
        config = result.scalar_one_or_none()

        if config is not None and config.date_of_birth is not None:
            print("FIRE config already populated (date_of_birth is set) — nothing to do.")
            return

        if config is None:
            config = FireConfig()
            session.add(config)
            print("  CREATE fire_config row")

        for field, value in STARTER.items():
            setattr(config, field, value)
            print(f"  SET {field} = {value}")

        await session.commit()
        print("\nDone. Open /fire/config and replace the starter values with your own.")


if __name__ == "__main__":
    asyncio.run(main())
