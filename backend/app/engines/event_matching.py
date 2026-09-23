"""Match past cashflow-event occurrences to the transactions that paid them.

A planned expense (a special assessment, a boat payment) is modeled as a
cashflow event; when it happens it also shows up as a real transaction. Any
view that ALSO derives a baseline from transactions must drop those rows, or
it counts the event twice: the Tracker's "Adjusted" mode strips them from the
lifestyle total, and the Runway strips them from its trailing burn (which
otherwise projects a finished one-off forward as if it recurred).

Pure functions — the callers fetch events and candidate transactions with
their own filters and pass plain rows in.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

from app.engines.event_calendar import event_occurrence_dates

# Amount within ±10% of the event, date within ±7 days of the occurrence.
# (The original matcher — ±30%, no date bound — matched a car payment against
# a child-support event on amount alone.)
AMOUNT_TOLERANCE = 0.10
DATE_WINDOW_DAYS = 7


@dataclass(frozen=True)
class Candidate:
    """A transaction that could have paid an expense event."""

    id: Any
    date: date
    amount_cents: int  # outflow magnitude, positive
    merchant: str | None = None


@dataclass(frozen=True)
class EventMatch:
    event_name: str
    event_amount_cents: int  # the event's face amount (not probability-weighted)
    occurrence_date: date
    txn: Candidate


def expense_occurrences(events: Iterable, start: date, end: date) -> list[tuple[Any, date]]:
    """(event, occurrence date) for every expense-event occurrence in [start, end], by date."""
    out = []
    for ev in events:
        if ev.event_type != "expense":
            continue
        out.extend((ev, d) for d in event_occurrence_dates(ev, start, end))
    out.sort(key=lambda pair: (pair[1], pair[0].name or ""))
    return out


def match_occurrences(
    occurrences: list[tuple[Any, date]],
    candidates: Iterable[Candidate],
    tolerance: float = AMOUNT_TOLERANCE,
    window_days: int = DATE_WINDOW_DAYS,
) -> list[EventMatch]:
    """Greedy, deterministic: each occurrence (earliest first) takes the unused
    candidate closest in amount (then in date) within the tolerances. A
    transaction pays at most one occurrence."""
    pool = list(candidates)
    used: set = set()
    window = timedelta(days=window_days)
    matches: list[EventMatch] = []
    for ev, occ in occurrences:
        target = abs(int(ev.amount_cents))
        low, high = target * (1 - tolerance), target * (1 + tolerance)
        best = None
        for c in pool:
            if c.id in used or not (occ - window <= c.date <= occ + window):
                continue
            if not (low <= c.amount_cents <= high):
                continue
            key = (abs(c.amount_cents - target), abs((c.date - occ).days))
            if best is None or key < best[0]:
                best = (key, c)
        if best is not None:
            used.add(best[1].id)
            matches.append(EventMatch(ev.name, target, occ, best[1]))
    return matches
