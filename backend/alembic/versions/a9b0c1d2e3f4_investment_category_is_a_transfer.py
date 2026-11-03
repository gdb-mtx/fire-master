"""Investment category -> transfer flags

Monarch files some brokerage buys (a money-market fund purchase) as
"Investment" under its Financial group, so the Investments-group rule in
f8a9b0c1d2e3 never caught them and the Runway burn counted the buys as
spending (fire-master#26). classify_flags() now matches the name; flip the
rows already stored once here, including any the Monarch category sync does
not rewrite.

Revision ID: a9b0c1d2e3f4
Revises: c3d4e5f6a7b8
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE category_mappings SET is_transfer = true, is_income = false "
        "WHERE normalized_category = 'Investment'"
    )


def downgrade() -> None:
    # Nothing to undo: counting brokerage buys as spending again is not a rollback anyone wants.
    pass
