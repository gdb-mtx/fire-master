"""add post-Medicare healthcare cost

Revision ID: c9d0e1f2a3b4
Revises: f8a9b0c1d2e3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fire_config",
        sa.Column("post_medicare_healthcare_monthly_cost", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fire_config", "post_medicare_healthcare_monthly_cost")
