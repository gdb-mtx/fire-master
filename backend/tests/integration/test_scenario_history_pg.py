"""Postgres-backed integration tests for the fire_scenarios history trigger.

The fire-master#10 pattern applied to scenarios. The case that matters most
here (and that fire_config never had): DELETE — scenarios have a real delete
button in the UI, and before this an accidental delete was unrecoverable.

Runs only when TEST_DATABASE_URL is set (CI provides a service container;
locally: create a scratch DB and export TEST_DATABASE_URL — NEVER point
this at a real database, tables are created and dropped).
"""

import os
from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.fire_scenario import FireScenario
from app.models.fire_scenario_history import (
    ALL_SCENARIO_TRIGGER_DDL,
    FireScenarioHistory,
)

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL not set (integration tests run in CI or against a scratch DB)",
)

OVERRIDES = {
    "rental_occupancy_rate": 0.70,
    "custom_assumptions": {"sepp": {"sepp_monthly": 2_100}},
}


@pytest.fixture
async def db():
    """Engine + schema + the scenario history trigger on the scratch database."""
    from app.core.database import Base

    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in ALL_SCENARIO_TRIGGER_DDL:
            await conn.execute(text(ddl))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(
            text("DROP FUNCTION IF EXISTS fire_scenario_capture_history()")
        )
    await engine.dispose()


async def _make_scenario(db, name="Sell River House") -> FireScenario:
    scenario = FireScenario(name=name, description="test", overrides=dict(OVERRIDES))
    db.add(scenario)
    await db.flush()
    return scenario


async def _history(db) -> list[FireScenarioHistory]:
    res = await db.execute(
        select(FireScenarioHistory).order_by(FireScenarioHistory.id)
    )
    return list(res.scalars().all())


class TestScenarioHistoryTrigger:
    async def test_update_captures_prior_state(self, db):
        scenario = await _make_scenario(db)
        scenario.overrides = {"clobbered": True}
        await db.flush()

        rows = await _history(db)
        assert len(rows) == 1
        assert rows[0].op == "UPDATE"
        assert str(rows[0].scenario_id) == str(scenario.id)
        assert rows[0].data["overrides"] == OVERRIDES
        assert rows[0].data["name"] == "Sell River House"

    async def test_noop_update_captures_nothing(self, db):
        await _make_scenario(db)
        await db.execute(text("UPDATE fire_scenarios SET name = name"))
        assert await _history(db) == []

    async def test_delete_captures_prior_state(self, db):
        scenario = await _make_scenario(db)
        sid = scenario.id
        await db.delete(scenario)
        await db.flush()

        rows = await _history(db)
        assert len(rows) == 1
        assert rows[0].op == "DELETE"
        assert str(rows[0].scenario_id) == str(sid)
        assert rows[0].data["overrides"] == OVERRIDES

    async def test_delete_then_restore_recreates_scenario(self, db):
        """The headline case: accidental delete -> re-created from the DELETE
        snapshot under the original id, inactive. Replicates the restore
        endpoint's re-insert branch."""
        scenario = await _make_scenario(db)
        scenario.is_active = True
        await db.flush()
        sid = scenario.id
        await db.delete(scenario)
        await db.flush()

        snap = (await _history(db))[-1]
        assert snap.op == "DELETE"
        data = snap.data
        restored = FireScenario(
            id=UUID(data["id"]),
            name=data["name"],
            description=data.get("description"),
            overrides=data.get("overrides"),
            is_active=False,
            created_at=datetime.fromisoformat(data["created_at"]),
        )
        db.add(restored)
        await db.flush()

        db.expire_all()
        res = await db.execute(select(FireScenario).where(FireScenario.id == sid))
        row = res.scalar_one()
        assert row.overrides == OVERRIDES
        assert row.name == "Sell River House"
        assert row.is_active is False  # restore never resurrects the active flag

    async def test_bad_put_then_restore_content(self, db):
        """The #9-shaped accident on scenarios: a PUT that overwrites overrides.
        Replicates the restore endpoint's update branch — content restored,
        is_active untouched."""
        scenario = await _make_scenario(db)
        scenario.is_active = True
        await db.flush()

        scenario.overrides = {}  # fat-fingered wipe
        await db.flush()

        snap = (await _history(db))[-1]
        data = snap.data
        scenario.name = data["name"]
        scenario.description = data.get("description")
        scenario.overrides = data.get("overrides")
        await db.flush()

        sid = scenario.id
        db.expire_all()
        res = await db.execute(select(FireScenario).where(FireScenario.id == sid))
        row = res.scalar_one()
        assert row.overrides == OVERRIDES
        assert row.is_active is True  # selection pointer survives restore
