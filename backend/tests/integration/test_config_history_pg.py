"""Postgres-backed integration tests for the fire_config history trigger.

fire-master#10: history rows are written by a DB trigger, which SQLAlchemy's
metadata create_all does NOT create — so these tests execute the exact DDL
constants the alembic migration uses (imported from the model module), then
verify capture and restore against real Postgres.

Runs only when TEST_DATABASE_URL is set (CI provides a service container;
locally: create a scratch DB and export TEST_DATABASE_URL — NEVER point
this at a real database, tables are created and dropped).
"""

import os
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.fire_config import FireConfig
from app.models.fire_config_history import ALL_TRIGGER_DDL, FireConfigHistory
from app.schemas.fire import FireConfigUpdate

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL not set (integration tests run in CI or against a scratch DB)",
)


@pytest.fixture
async def db():
    """Engine + schema + the history trigger on the scratch database."""
    from app.core.database import Base

    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in ALL_TRIGGER_DDL:
            await conn.execute(text(ddl))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP FUNCTION IF EXISTS fire_config_capture_history()"))
    await engine.dispose()


async def _make_config(db) -> FireConfig:
    config = FireConfig(
        date_of_birth=date(1973, 7, 1),
        target_annual_spending=15_300_000,
        custom_assumptions={
            "sepp": {"sepp_monthly": 2_100, "ira_a_balance": 402_000},
            "taxable_pool": {"return_rate": 0.065},
        },
    )
    db.add(config)
    await db.flush()
    return config


async def _history(db) -> list[FireConfigHistory]:
    res = await db.execute(select(FireConfigHistory).order_by(FireConfigHistory.id))
    return list(res.scalars().all())


class TestHistoryTrigger:
    async def test_update_captures_prior_state(self, db):
        config = await _make_config(db)
        config.custom_assumptions = {"sepp": {"sepp_monthly": 2_500}}
        config.target_annual_spending = 14_000_000
        await db.flush()

        rows = await _history(db)
        assert len(rows) == 1
        row = rows[0]
        assert row.op == "UPDATE"
        assert str(row.config_id) == str(config.id)
        # The snapshot is the PRIOR state, not the new one
        assert row.data["target_annual_spending"] == 15_300_000
        assert row.data["custom_assumptions"]["sepp"]["ira_a_balance"] == 402_000
        assert row.data["custom_assumptions"]["taxable_pool"] == {"return_rate": 0.065}

    async def test_noop_update_captures_nothing(self, db):
        """WHEN (OLD.* IS DISTINCT FROM NEW.*) — same-value writes don't spam history."""
        config = await _make_config(db)
        await db.execute(
            text("UPDATE fire_config SET target_annual_spending = target_annual_spending")
        )
        assert await _history(db) == []

    async def test_delete_captures_prior_state(self, db):
        config = await _make_config(db)
        cid = config.id
        await db.delete(config)
        await db.flush()

        rows = await _history(db)
        assert len(rows) == 1
        assert rows[0].op == "DELETE"
        assert str(rows[0].config_id) == str(cid)
        assert rows[0].data["target_annual_spending"] == 15_300_000

    async def test_raw_sql_write_is_captured(self, db):
        """The point of a trigger: writes that bypass the API entirely still
        get archived (seed scripts, psql sessions, future bugs)."""
        await _make_config(db)
        await db.execute(text("UPDATE fire_config SET custom_assumptions = '{}'::jsonb"))
        rows = await _history(db)
        assert len(rows) == 1
        assert rows[0].data["custom_assumptions"]["sepp"]["sepp_monthly"] == 2_100

    async def test_restore_round_trip(self, db):
        """Replicates the restore endpoint's coercion loop: JSONB snapshot ->
        FireConfigUpdate (ISO strings -> dates) -> setattr, back to the exact
        pre-wipe state — including a full custom_assumptions REPLACE."""
        config = await _make_config(db)
        original_ca = dict(config.custom_assumptions)

        # The #9-style disaster: wholesale wipe
        config.custom_assumptions = {"oops": True}
        config.date_of_birth = date(1999, 1, 1)
        await db.flush()

        snapshot_row = (await _history(db))[0]
        snapshot = FireConfigUpdate(
            **{
                k: v
                for k, v in snapshot_row.data.items()
                if k in FireConfigUpdate.model_fields
            }
        )
        for field, value in snapshot.model_dump(exclude_unset=True).items():
            setattr(config, field, value)
        await db.flush()

        cid = config.id
        db.expire_all()
        res = await db.execute(select(FireConfig).where(FireConfig.id == cid))
        restored = res.scalar_one()
        assert restored.custom_assumptions == original_ca
        assert restored.date_of_birth == date(1973, 7, 1)
        # ...and the restore itself was captured, so it can be undone too
        assert len(await _history(db)) == 2
