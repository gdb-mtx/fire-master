"""add fire_scenario_history table + capture trigger

The fire-master#10 trigger pattern applied to fire_scenarios: scenarios are
many rows with a real delete button, so an accidental delete (or a bad PUT
overwriting overrides) was just as unrecoverable as the config wipe was.
Append-only history via BEFORE UPDATE/DELETE trigger; DDL constants imported
from the model module so integration tests exercise the identical SQL.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.fire_scenario_history import (
    ALL_SCENARIO_TRIGGER_DDL,
    SCENARIO_TRIGGER_DROP_DDL,
)

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fire_scenario_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("scenario_id", UUID(as_uuid=True), nullable=True),
        sa.Column("op", sa.String(16), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("data", JSONB(), nullable=False),
    )
    op.create_index(
        "ix_fire_scenario_history_changed_at", "fire_scenario_history", ["changed_at"]
    )
    for ddl in ALL_SCENARIO_TRIGGER_DDL:
        op.execute(ddl)


def downgrade() -> None:
    op.execute(SCENARIO_TRIGGER_DROP_DDL)
    op.drop_index("ix_fire_scenario_history_changed_at", table_name="fire_scenario_history")
    op.drop_table("fire_scenario_history")
