"""fire-master#25 against real Postgres: Property P&L per-month figures divide
by calendar months, not by the months that happened to have a transaction.

months_in_period() is unit-tested; this pins get_pnl end to end, where the
month grouping (to_char) and the first-transaction lookup are SQL.

Runs only when TEST_DATABASE_URL is set (CI provides a service container;
locally: create a scratch DB and export TEST_DATABASE_URL — NEVER point
this at a real database, tables are created and dropped).
"""

import os
import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.engines.property_pnl import PropertyPnLEngine
from app.models.account import Account
from app.models.enums import AccountType, DataSource
from app.models.property import Property
from app.models.transaction import Transaction

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL not set (integration tests run in CI or against a scratch DB)",
)

TODAY = date(2026, 11, 3)
LAST_12 = date(2025, 12, 1)  # what the page sends for "Last 12 months" on TODAY


@pytest.fixture
async def db():
    """Engine + schema on the scratch database; dropped afterwards."""
    from app.core.database import Base

    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed(db, prop: Property, rows: list[tuple[date, int, str]]) -> None:
    acct = Account(name=f"Checking {prop.key}", account_type=AccountType.CHECKING,
                   source=DataSource.MANUAL, current_balance=0)
    db.add_all([prop, acct])
    await db.flush()
    for d, cents, category in rows:
        db.add(Transaction(id=uuid.uuid4(), account_id=acct.id, date=d, amount=cents,
                           merchant="Bill", category="Mortgage", source=DataSource.MANUAL,
                           property_id=prop.id, property_category=category,
                           property_source="manual"))
    await db.flush()


async def _totals(db, key: str, **window) -> dict:
    pnl = await PropertyPnLEngine(db).get_pnl(today=TODAY, **window)
    [entry] = [e for e in pnl["properties"] if e["property"]["key"] == key]
    return entry["totals"]


async def test_lumpy_bills_spread_over_the_window(db):
    # The issue's repro: $9,000 a quarter + $6,000 twice a year, never in the
    # same month: 6 months with activity, $48,000 over 12 months.
    prop = Property(key="lumpy", name="Lumpy", potential_monthly_rental_cents=2_000_00)
    await _seed(db, prop, [
        (date(2024, 5, 1), -100_00, "Repairs / Maintenance"),   # held long before the window
        (date(2025, 12, 5), -9_000_00, "Mortgage"),
        (date(2026, 3, 5), -9_000_00, "Mortgage"),
        (date(2026, 6, 5), -9_000_00, "Mortgage"),
        (date(2026, 9, 5), -9_000_00, "Mortgage"),
        (date(2026, 2, 10), -6_000_00, "Property Tax"),
        (date(2026, 8, 10), -6_000_00, "Property Tax"),
    ])

    t = await _totals(db, "lumpy", start=LAST_12)

    assert t["total_cost_cents"] == 48_000_00
    assert (t["months"], t["first_month"]) == (12, "2025-12")
    assert t["monthly_cost_cents"] == 4_000_00          # was 8,000: $48,000 / 6 active months
    assert t["net_per_year_cents"] == 48_000_00
    assert t["rental_potential_cents"] == 12 * 2_000_00


async def test_property_bought_mid_window_counts_from_its_first_month(db):
    prop = Property(key="new", name="New", potential_monthly_rental_cents=1_500_00)
    await _seed(db, prop, [
        (date(2026, 6, 10), -3_000_00, "HOA / Condo Fees"),
        (date(2026, 9, 10), -3_000_00, "HOA / Condo Fees"),
        (date(2026, 10, 1), 900_00, "Rental Income"),
    ])

    t = await _totals(db, "new", start=LAST_12)

    assert (t["months"], t["first_month"]) == (6, "2026-06")   # Jun through Nov
    assert t["monthly_cost_cents"] == 1_000_00                  # was 2,000: 3 active months
    assert t["rental_potential_cents"] == 6 * 1_500_00
    assert t["net_per_year_cents"] == round((6_000_00 - 900_00) * 12 / 6)


async def test_all_time_runs_from_the_first_transaction(db):
    prop = Property(key="old", name="Old")
    await _seed(db, prop, [
        (date(2024, 1, 20), -1_200_00, "Insurance"),
        (date(2025, 1, 20), -1_200_00, "Insurance"),
        (date(2026, 1, 20), -1_200_00, "Insurance"),
    ])

    t = await _totals(db, "old")

    assert (t["months"], t["first_month"]) == (35, "2024-01")   # Jan 2024 through Nov 2026
    assert t["monthly_cost_cents"] == round(3_600_00 / 35)
