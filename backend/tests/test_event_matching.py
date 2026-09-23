"""Event occurrences and event → transaction matching (pure, no DB)."""

from datetime import date
from types import SimpleNamespace

from app.engines.event_calendar import event_occurrence_dates
from app.engines.event_matching import Candidate, expense_occurrences, match_occurrences


def _ev(name, amount_cents, d, *, recurring=False, recurrence=None, end=None, etype="expense"):
    return SimpleNamespace(
        name=name, amount_cents=amount_cents, date=d, event_type=etype,
        is_recurring=recurring, recurrence=recurrence, end_date=end,
    )


# --- occurrences ------------------------------------------------------------------------

def test_one_off_occurs_once_inside_the_range():
    ev = _ev("Roof", 500_000, date(2026, 8, 3))
    assert event_occurrence_dates(ev, date(2026, 8, 1), date(2026, 8, 31)) == [date(2026, 8, 3)]
    assert event_occurrence_dates(ev, date(2026, 9, 1), date(2026, 9, 30)) == []


def test_recurring_steps_by_month_and_stops_after_end_month():
    ev = _ev("Assessment", 440_000, date(2026, 4, 14), recurring=True,
             recurrence="monthly", end=date(2026, 7, 1))
    assert event_occurrence_dates(ev, date(2026, 1, 1), date(2026, 12, 31)) == [
        date(2026, 4, 14), date(2026, 5, 14), date(2026, 6, 14), date(2026, 7, 14),
    ]


def test_month_end_start_clamps_without_drifting():
    ev = _ev("Rent", 100_000, date(2026, 1, 31), recurring=True, recurrence="monthly")
    assert event_occurrence_dates(ev, date(2026, 1, 1), date(2026, 4, 30)) == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30),
    ]


def test_quarterly_keeps_phase():
    ev = _ev("Tax", 300_000, date(2026, 1, 15), recurring=True, recurrence="quarterly")
    assert event_occurrence_dates(ev, date(2026, 3, 1), date(2026, 12, 31)) == [
        date(2026, 4, 15), date(2026, 7, 15), date(2026, 10, 15),
    ]


def test_only_expense_events_produce_occurrences():
    occ = expense_occurrences(
        [_ev("Payout", 100_000, date(2026, 8, 1), etype="income"),
         _ev("Bill", 100_000, date(2026, 8, 2))],
        date(2026, 8, 1), date(2026, 8, 31),
    )
    assert [(e.name, d) for e, d in occ] == [("Bill", date(2026, 8, 2))]


# --- matching ---------------------------------------------------------------------------

def test_closest_amount_wins_and_each_transaction_is_used_once():
    events = [_ev("Support lump", 168_200, date(2026, 8, 3))]
    cands = [
        Candidate("other", date(2026, 7, 27), 175_000, "P2P"),  # inside ±10%, ±7d
        Candidate("real", date(2026, 8, 3), 168_150, "P2P"),
    ]
    occ = expense_occurrences(events, date(2026, 7, 1), date(2026, 9, 30))
    [m] = match_occurrences(occ, cands)
    assert m.txn.id == "real"


def test_matching_over_the_full_window_never_steals_across_a_month_edge():
    # The July-view bug: a range that ends Jul 31 hides the real Aug 3 payment,
    # so a Jul 27 payment of a similar size was taken instead. Matching once over
    # the whole window (then filtering) leaves the July payment alone.
    events = [_ev("Support lump", 168_200, date(2026, 8, 3))]
    cands = [
        Candidate("jul-payment", date(2026, 7, 27), 175_000, "P2P"),
        Candidate("aug-payment", date(2026, 8, 3), 168_150, "P2P"),
    ]
    occ = expense_occurrences(events, date(2026, 3, 1), date(2026, 9, 22))
    matched_ids = {m.txn.id for m in match_occurrences(occ, cands)}
    assert matched_ids == {"aug-payment"}


def test_outside_tolerance_is_not_matched():
    events = [_ev("Bill", 100_000, date(2026, 8, 10))]
    cands = [
        Candidate("too-big", date(2026, 8, 10), 111_000),    # +11%
        Candidate("too-late", date(2026, 8, 18), 100_000),   # +8 days
    ]
    occ = expense_occurrences(events, date(2026, 8, 1), date(2026, 8, 31))
    assert match_occurrences(occ, cands) == []


def test_each_recurring_occurrence_can_match_its_own_payment():
    events = [_ev("Assessment", 440_000, date(2026, 6, 14), recurring=True,
                  recurrence="monthly", end=date(2026, 8, 31))]
    cands = [
        Candidate("jun", date(2026, 6, 12), 442_904),
        Candidate("jul", date(2026, 7, 14), 442_904),
        Candidate("aug", date(2026, 8, 11), 442_904),
    ]
    occ = expense_occurrences(events, date(2026, 6, 1), date(2026, 9, 22))
    assert [m.txn.id for m in match_occurrences(occ, cands)] == ["jun", "jul", "aug"]
