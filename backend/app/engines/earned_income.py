"""When earned income stops: one rule for every engine that has a retirement date.

Salary, bonus and side-hustle sources without an end_date are assumed to be the
job you retire FROM, so they stop at the retirement date. Earned income that
starts on or after that date is work done in retirement (consulting, part-time)
and runs to its own end_date, or for life without one. An explicit end_date
always wins.

Until fire-master#27 the timeline, Monte Carlo and the Runway cut every undated
earned source at retirement, so a side hustle that started after it never paid;
the withdrawal plan capped earned income at retirement even past an explicit
later end_date, and the Roth-conversion baseline skipped earned income outright.

Not used by project_wealth_pools (the Retirement page): it has no working phase
and skips earned income of every kind, as the page says ("This assumes you've
stopped working").
"""

from datetime import date

EARNED_INCOME_TYPES = frozenset({"salary", "bonus", "side_hustle"})


def is_earned(src) -> bool:
    return src.income_type.value in EARNED_INCOME_TYPES


def stops_at_retirement(src, retirement_date: date | None) -> bool:
    """True for working-years pay: earned income with no end_date that starts
    before the retirement date (an undated start has already started).

    Callers cut a source only once a retirement date exists; with none, an
    undated earned source still reads as working-years pay.
    """
    if not is_earned(src) or src.end_date is not None:
        return False
    if retirement_date is None or src.start_date is None:
        return True
    return src.start_date < retirement_date
