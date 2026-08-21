"""reclassify is_income/is_transfer from parent_category (fire-master#11)

Custom-named income categories under Monarch's "Income" group (e.g. "Alice - Paycheck")
were stored with is_income=false because the sync exact-matched five hardcoded names.
The engine now derives the flags from the parent group; this heals rows already stored
so the fix lands on upgrade without waiting for the next Monarch sync.

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, Sequence[str], None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE category_mappings SET is_transfer = true "
        "WHERE parent_category = 'Transfers' AND is_transfer = false"
    )
    op.execute(
        "UPDATE category_mappings SET is_income = true "
        "WHERE parent_category = 'Income' AND is_transfer = false AND is_income = false"
    )


def downgrade() -> None:
    # Data-only fix; the old exact-match flags are not worth restoring.
    pass
