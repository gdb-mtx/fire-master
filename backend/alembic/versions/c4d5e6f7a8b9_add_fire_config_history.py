"""add fire_config_history table + capture trigger

fire-master#10: before this, a destructive write to fire_config (e.g. the #9
merge bug) was permanently unrecoverable — no history table, no undo, nothing.
This adds an append-only history table populated by a Postgres trigger on
UPDATE/DELETE of fire_config, capturing the full prior row as JSONB. Trigger
(not app code) so every write path is covered: API, seed scripts, psql.

DDL constants are imported from the model module so integration tests
exercise the identical SQL.

Revision ID: c4d5e6f7a8b9
Revises: 093aad7d52f1
Create Date: 2026-08-05 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.fire_config_history import ALL_TRIGGER_DDL, HISTORY_TRIGGER_DROP_DDL

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = '093aad7d52f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fire_config_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("config_id", UUID(as_uuid=True), nullable=True),
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
        "ix_fire_config_history_changed_at", "fire_config_history", ["changed_at"]
    )
    for ddl in ALL_TRIGGER_DDL:
        op.execute(ddl)


def downgrade() -> None:
    op.execute(HISTORY_TRIGGER_DROP_DDL)
    op.drop_index("ix_fire_config_history_changed_at", table_name="fire_config_history")
    op.drop_table("fire_config_history")
