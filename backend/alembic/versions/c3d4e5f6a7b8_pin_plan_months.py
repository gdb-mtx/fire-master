"""Pin stored plan-month offsets to calendar months

Plan months (property sale, SEPP / RRSP draw start, cash-rate cutover, car-loan
payoff) were stored as offsets from TODAY, so every unedited plan slid a month
later each month. Convert every stored offset, once, to the calendar month it
points to on the day this migration runs — no projection moves on that day, and
nothing drifts after it. From here on the API pins offsets on write
(app/engines/plan_months.py). fire_config / fire_scenarios history triggers
archive the pre-migration rows, so the offsets remain recoverable.

The converter is a frozen copy of plan_months.pin_plan_months (migrations must
not follow later app-code changes). Legacy single-property blocks are left as
they are, as the engine does.

Revision ID: c3d4e5f6a7b8
Revises: f8a9b0c1d2e3
"""
import copy
import json
from datetime import date
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from dateutil.relativedelta import relativedelta

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLAN_MONTH_KEYS = (
    ("sepp", "sepp_start_month", "start_date"),
    ("rrsp", "start_month", "start_date"),
    ("projection", "cash_savings_cutover_month", "cash_savings_cutover_date"),
    ("debt_paydown", "car_loan_payoff_month", "car_loan_payoff_date"),
)


def _month(today: date, offset) -> str:
    return (today.replace(day=1) + relativedelta(months=int(offset))).strftime("%Y-%m")


def _pin_container(container: dict, rel: str, pinned: str, today: date) -> bool:
    if rel not in container:
        return False
    val = container.pop(rel)
    if val is not None and not container.get(pinned):
        container[pinned] = _month(today, val)
    return True


def pin(ca, today: date):
    """(pinned copy, changed?) — offsets become calendar months; pins win."""
    if not isinstance(ca, dict):
        return ca, False
    out = copy.deepcopy(ca)
    changed = False
    for section, rel, pinned in PLAN_MONTH_KEYS:
        container = out.get(section)
        if isinstance(container, dict):
            changed |= _pin_container(container, rel, pinned, today)
    for sale in out.get("property_sales") or []:
        if isinstance(sale, dict):
            changed |= _pin_container(sale, "sale_month", "sale_date", today)
    return out, changed


def upgrade() -> None:
    conn = op.get_bind()
    today = date.today()

    for row in conn.execute(sa.text("SELECT id, custom_assumptions FROM fire_config")).mappings():
        new, changed = pin(row["custom_assumptions"], today)
        if changed:
            conn.execute(
                sa.text("UPDATE fire_config SET custom_assumptions = CAST(:ca AS jsonb) WHERE id = :id"),
                {"ca": json.dumps(new), "id": row["id"]},
            )

    for row in conn.execute(sa.text("SELECT id, overrides FROM fire_scenarios")).mappings():
        overrides = row["overrides"]
        if not isinstance(overrides, dict):
            continue
        new_ca, changed = pin(overrides.get("custom_assumptions"), today)
        if changed:
            conn.execute(
                sa.text("UPDATE fire_scenarios SET overrides = CAST(:ov AS jsonb) WHERE id = :id"),
                {"ov": json.dumps({**overrides, "custom_assumptions": new_ca}), "id": row["id"]},
            )


def downgrade() -> None:
    # The offsets are in fire_config_history / fire_scenario_history; restoring a
    # sliding plan month is not a rollback anyone wants.
    pass
