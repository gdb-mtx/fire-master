"""Plan months: calendar dates, never offsets that slide with today.

A plan month — a property sale, the SEPP / RRSP draw start, the cash-rate
cutover, a loan payoff — used to be stored as an offset from TODAY
("sale_month: 13"), so an unedited plan drifted a month later every month.
Each such key now has a pinned twin holding a calendar month ("2027-07"):

  - reads go through resolve_month_offset(): the pinned twin wins, the legacy
    offset is honored only when no pin exists;
  - writes go through pin_plan_months(): the config PATCH, scenario
    create/update and history restores convert any offset they receive into
    its pinned twin at the month it points to on the day it is written, so
    nothing stored drifts. (Migration c3d4e5f6a7b8 did the same, once, for
    everything already stored.)

The legacy single-property blocks are deliberately not covered — they are
author back-compat only and are not extended.

Leaf module: no app imports.
"""

import copy
import logging
from datetime import date

from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

# (custom_assumptions sub-dict, legacy offset key, pinned calendar-month key)
PLAN_MONTH_KEYS: tuple[tuple[str, str, str], ...] = (
    ("sepp", "sepp_start_month", "start_date"),
    ("rrsp", "start_month", "start_date"),
    ("projection", "cash_savings_cutover_month", "cash_savings_cutover_date"),
    ("debt_paydown", "car_loan_payoff_month", "car_loan_payoff_date"),
)
# property_sales[] entries: sale_month → sale_date
SALE_KEYS = ("sale_month", "sale_date")


def month_from_offset(today: date, offset: int) -> str:
    """The calendar month `offset` months after today's month, as "YYYY-MM"."""
    return (today.replace(day=1) + relativedelta(months=int(offset))).strftime("%Y-%m")


def resolve_month_offset(
    cfg: dict, date_key: str, month_key: str, today: date, default: int | None = 0,
) -> int | None:
    """A plan month as an offset from today's month.

    `date_key` holds an absolute calendar month — "2027-07" (a day, if given, is
    ignored) — and wins. `month_key` is the legacy offset counted FROM TODAY.
    Calendar offsets match build_cashflow_schedule, so a pinned sale and a
    cashflow event dated the same month land on the same month. A pinned month
    already past resolves to 0 (now); a malformed one falls back to the offset.
    """
    raw = cfg.get(date_key)
    if raw:
        try:
            year, month = int(str(raw)[0:4]), int(str(raw)[5:7])
            if not 1 <= month <= 12:
                raise ValueError(raw)
            return max(0, (year - today.year) * 12 + (month - today.month))
        except (TypeError, ValueError):
            logger.warning("Ignoring malformed %s=%r; using %s", date_key, raw, month_key)
    val = cfg.get(month_key, default)
    return int(val) if val is not None else default


def is_held(sale: dict) -> bool:
    """A property_sales entry with no sale date is a property KEPT for good."""
    return not sale.get("sale_date") and sale.get("sale_month") is None


def sale_offset(sale: dict, today: date) -> int | None:
    """Months until a property_sales entry sells, or None when it is HELD.

    Held = neither sale_date nor sale_month. The property never converts to cash;
    its carrying cost keeps applying (added on top of the budget while
    in_base_burn is false) and its rental income keeps flowing. This is how a
    plan says "keep it and keep paying for it" — before, an entry without a date
    resolved to month 0 and sold immediately.
    """
    if is_held(sale):
        return None
    return resolve_month_offset(sale, "sale_date", "sale_month", today, default=None)


def _pin(container: dict, rel: str, pinned: str, today: date, as_patch: bool) -> None:
    if rel not in container:
        return
    val = container[rel]
    if val is None:
        if not as_patch:
            del container[rel]  # a patch's null is a delete instruction — keep it
        return
    if not container.get(pinned):
        container[pinned] = month_from_offset(today, val)
    if as_patch:
        container[rel] = None  # JSON Merge Patch: delete the stored offset
    else:
        del container[rel]


def pin_plan_months(ca: dict | None, today: date, *, as_patch: bool = False) -> dict | None:
    """Copy of custom_assumptions with every plan-month offset pinned to the
    calendar month it points to today. An existing pin wins over an offset.

    as_patch=True for an RFC 7386 merge-patch body: the offset key is sent as
    null so the merge deletes the stored one (lists — property_sales — replace
    wholesale, so their entries just drop the key).
    """
    if not isinstance(ca, dict):
        return ca
    out = copy.deepcopy(ca)
    for section, rel, pinned in PLAN_MONTH_KEYS:
        container = out.get(section)
        if isinstance(container, dict):
            _pin(container, rel, pinned, today, as_patch)
    sales = out.get("property_sales")
    if isinstance(sales, list):
        for sale in sales:
            if isinstance(sale, dict):
                _pin(sale, SALE_KEYS[0], SALE_KEYS[1], today, as_patch=False)
    return out
