"""Append-only history of fire_scenarios writes — the fire-master#10 trigger
pattern applied to scenarios.

Unlike fire_config (single row, edits only), scenarios are many rows with a
real delete button in the UI — so the DELETE capture matters most here: it is
what makes "bring back the scenario I deleted" possible at all.

Rows are inserted by a Postgres trigger, not application code, so every write
path is covered (API, seed scripts, psql, future bugs). DDL constants live
here so the alembic migration and the Postgres integration tests execute the
exact same SQL.
"""

from datetime import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, Identity, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FireScenarioHistory(Base):
    __tablename__ = "fire_scenario_history"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Plain UUID, deliberately no FK: history must outlive the scenario row.
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    op: Mapped[str] = mapped_column(String(16), nullable=False)  # UPDATE | DELETE
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), index=True
    )
    # Full prior row, as Postgres saw it (to_jsonb(OLD))
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)


SCENARIO_TRIGGER_FUNCTION_DDL = """
CREATE OR REPLACE FUNCTION fire_scenario_capture_history() RETURNS trigger AS $$
BEGIN
    INSERT INTO fire_scenario_history (scenario_id, op, data)
    VALUES (OLD.id, TG_OP, to_jsonb(OLD));
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

SCENARIO_TRIGGER_UPDATE_DDL = """
CREATE TRIGGER fire_scenario_history_on_update
BEFORE UPDATE ON fire_scenarios
FOR EACH ROW
WHEN (OLD.* IS DISTINCT FROM NEW.*)
EXECUTE FUNCTION fire_scenario_capture_history();
"""

SCENARIO_TRIGGER_DELETE_DDL = """
CREATE TRIGGER fire_scenario_history_on_delete
BEFORE DELETE ON fire_scenarios
FOR EACH ROW
EXECUTE FUNCTION fire_scenario_capture_history();
"""

SCENARIO_TRIGGER_DROP_DDL = """
DROP TRIGGER IF EXISTS fire_scenario_history_on_update ON fire_scenarios;
DROP TRIGGER IF EXISTS fire_scenario_history_on_delete ON fire_scenarios;
DROP FUNCTION IF EXISTS fire_scenario_capture_history();
"""

ALL_SCENARIO_TRIGGER_DDL = (
    SCENARIO_TRIGGER_FUNCTION_DDL,
    SCENARIO_TRIGGER_UPDATE_DDL,
    SCENARIO_TRIGGER_DELETE_DDL,
)
