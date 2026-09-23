"""Cashflow event payload validation."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.cashflow import CashflowEventCreate, CashflowEventUpdate


def _create(**kw):
    base = dict(name="Roof", event_type="expense", amount=1000.0, date=date(2027, 1, 1))
    return CashflowEventCreate(**{**base, **kw})


def test_probability_zero_is_accepted():
    # A parked event (probability 0) is legitimate; the old form could not send it.
    assert _create(probability=0.0).probability == 0.0
    assert CashflowEventUpdate(probability=0.0).probability == 0.0


def test_probability_defaults_to_one():
    assert _create().probability == 1.0


@pytest.mark.parametrize("bad", [-0.1, 1.5, 10.5])
def test_probability_outside_unit_interval_rejected(bad):
    with pytest.raises(ValidationError):
        _create(probability=bad)
    with pytest.raises(ValidationError):
        CashflowEventUpdate(probability=bad)
