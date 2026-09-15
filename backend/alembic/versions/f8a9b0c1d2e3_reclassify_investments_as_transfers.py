"""reclassify investment activity as neutral transfers

Monarch groups brokerage purchases such as "Buy" under "Investments". These
move cash into an asset rather than consuming it, so they must not contribute to
spending. Heal existing mappings on upgrade; future syncs use the same rule.

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
    # Data-only correction; restoring known-bad spending flags would be harmful.
    pass
