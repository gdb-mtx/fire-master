"""fire-master#26 against real Postgres: a brokerage buy Monarch files as
"Investment" (Financial group) is a transfer, so the Runway burn drops it.

classify_flags() is unit-tested; this pins the two layers a mock cannot see:
the category sync's ON CONFLICT rewrite of a row stored before the fix, and
the burn query's join on the stored flag.

Runs only when TEST_DATABASE_URL is set (CI provides a service container;
locally: create a scratch DB and export TEST_DATABASE_URL — NEVER point
this at a real database, tables are created and dropped).
"""

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.engines.cashflow import CashflowEngine
from app.ingestion.category_sync import CategorySyncService
from app.models.account import Account
from app.models.category_mapping import CategoryMapping
from app.models.enums import AccountType, DataSource
from app.models.transaction import Transaction

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL not set (integration tests run in CI or against a scratch DB)",
)


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


class _FakeMonarch:
    """Monarch's taxonomy as the API returns it: Investment sits in Financial."""

    async def get_transaction_category_groups(self):
        return [{"id": "g-fin", "name": "Financial"}, {"id": "g-food", "name": "Food & Dining"}]

    async def get_transaction_categories(self):
        return [
            {"id": "c-inv", "name": "Investment", "group": {"id": "g-fin"}},
            {"id": "c-fees", "name": "Financial Fees", "group": {"id": "g-fin"}},
            {"id": "c-groc", "name": "Groceries", "group": {"id": "g-food"}},
        ]


async def _flags(db, raw: str) -> tuple[bool, bool]:
    row = (await db.execute(
        select(CategoryMapping).where(CategoryMapping.raw_category == raw)
    )).scalar_one()
    return row.is_income, row.is_transfer


async def test_sync_rewrites_a_row_stored_before_the_fix(db):
    # The row as an install synced before the fix holds it: counted as spending.
    db.add(CategoryMapping(raw_category="Investment", normalized_category="Investment",
                           parent_category="Financial", is_income=False, is_transfer=False))
    await db.flush()

    await CategorySyncService(db, _FakeMonarch()).sync_from_monarch()
    db.expire_all()

    assert await _flags(db, "Investment") == (False, True)
    assert await _flags(db, "Financial Fees") == (False, False)  # a fee is still spending


async def test_runway_burn_drops_the_buy(db):
    await CategorySyncService(db, _FakeMonarch()).sync_from_monarch()
    brokerage = Account(name="Brokerage", account_type=AccountType.TAXABLE,
                        source=DataSource.MANUAL, current_balance=0)
    db.add(brokerage)
    await db.flush()
    for cents, category, merchant in [
        (-80_000_00, "Investment", "Money Fund Purchase"),   # the sweep
        (-25_00, "Financial Fees", "Advisory fee"),
        (-300_00, "Groceries", "Market"),
    ]:
        db.add(Transaction(id=uuid.uuid4(), account_id=brokerage.id, date=date(2026, 10, 15),
                           amount=cents, category=category, merchant=merchant,
                           source=DataSource.MANUAL))
    await db.flush()

    outflows = await CashflowEngine(db)._outflows(date(2026, 8, 1), date(2026, 10, 31))

    assert sorted(c.merchant for c in outflows) == ["Advisory fee", "Market"]
    assert sum(c.amount_cents for c in outflows) == 325_00
