"""Investments group -> transfer flags

A "Buy" in a brokerage account is money changing shape, not money spent, yet
rows under Monarch's Investments parent were flagged as spending. Flip the
existing category_mappings once here; classify_flags() applies the same rule
to everything synced from now on.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE category_mappings SET is_transfer = true, is_income = false "
        "WHERE parent_category = 'Investments' "
        "OR normalized_category IN ('Investments', 'Buy')"
    )


def downgrade() -> None:
    # Nothing to undo: putting the wrong spending flags back is not a rollback anyone wants.
    pass
