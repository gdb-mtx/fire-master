"""Cashflow-event calendar: when a (possibly recurring) event occurs.

Leaf module (no app imports) so both the projection engines and the
event-to-transaction matcher can use it without an import cycle.
"""

from datetime import date

from dateutil.relativedelta import relativedelta

RECURRENCE_STEP_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}


def event_occurrence_dates(cf, start: date, end: date) -> list[date]:
    """Dates a cashflow event occurs on within [start, end] (inclusive).

    One-off: its date. Recurring: the start date stepped by whole months
    (monthly / quarterly / annual, day clamped to month end), stopping after the
    end_date's MONTH (inclusive, same rule as build_cashflow_schedule).
    """
    if not cf.is_recurring:
        return [cf.date] if start <= cf.date <= end else []
    step = RECURRENCE_STEP_MONTHS.get(cf.recurrence or "monthly", 1)
    last_month = (cf.end_date.year, cf.end_date.month) if cf.end_date else None
    out: list[date] = []
    k = 0
    while True:
        d = cf.date + relativedelta(months=k * step)
        if d > end or (last_month and (d.year, d.month) > last_month):
            return out
        if d >= start:
            out.append(d)
        k += 1
