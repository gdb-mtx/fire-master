"""Append-only history of fire_config writes (fire-master#10).

Rows are inserted by a Postgres trigger, NOT application code — the whole
point is that any write path (API, seed scripts, a psql session, a future
bug) gets captured at the database boundary, before the prior state is gone.
The app only ever reads this table (history endpoint) and restores from it.

The trigger DDL lives here as constants so the alembic migration and the
Postgres integration tests execute the exact same SQL.
"""

from datetime import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, Identity, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FireConfigHistory(Base):
    __tablename__ = "fire_config_history"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Plain UUID, deliberately no FK: history must outlive the config row.
    config_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    op: Mapped[str] = mapped_column(String(16), nullable=False)  # UPDATE | DELETE
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), index=True
    )
    # Full prior row, as Postgres saw it (to_jsonb(OLD))
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)


HISTORY_TRIGGER_FUNCTION_DDL = """
CREATE OR REPLACE FUNCTION fire_config_capture_history() RETURNS trigger AS $$
BEGIN
    INSERT INTO fire_config_history (config_id, op, data)
    VALUES (OLD.id, TG_OP, to_jsonb(OLD));
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

HISTORY_TRIGGER_UPDATE_DDL = """
CREATE TRIGGER fire_config_history_on_update
BEFORE UPDATE ON fire_config
FOR EACH ROW
WHEN (OLD.* IS DISTINCT FROM NEW.*)
EXECUTE FUNCTION fire_config_capture_history();
"""

HISTORY_TRIGGER_DELETE_DDL = """
CREATE TRIGGER fire_config_history_on_delete
BEFORE DELETE ON fire_config
FOR EACH ROW
EXECUTE FUNCTION fire_config_capture_history();
"""

HISTORY_TRIGGER_DROP_DDL = """
DROP TRIGGER IF EXISTS fire_config_history_on_update ON fire_config;
DROP TRIGGER IF EXISTS fire_config_history_on_delete ON fire_config;
DROP FUNCTION IF EXISTS fire_config_capture_history();
"""

ALL_TRIGGER_DDL = (
    HISTORY_TRIGGER_FUNCTION_DDL,
    HISTORY_TRIGGER_UPDATE_DDL,
    HISTORY_TRIGGER_DELETE_DDL,
)
