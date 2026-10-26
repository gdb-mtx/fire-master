"""fire-master#11: income/transfer flags derive from Monarch's parent group, not a 5-name list;
readiness savings-rate component is window-averaged and clamped to 0-100."""
import pytest

from app.engines.fire_projections import savings_rate_component
from app.ingestion.category_sync import classify_flags


@pytest.mark.parametrize("name,parent,expected", [
    ("Paychecks", "Income", (True, False)),
    ("Alice - Paycheck", "Income", (True, False)),       # the reported case
    ("Bob - Paycheck", "Income", (True, False)),
    ("Side Hustle", "Income", (True, False)),
    ("Interest", "Other", (True, False)),                # name fallback when parent is Mint-era
    ("Groceries", "Food & Dining", (False, False)),
    ("Transfer", "Transfers", (False, True)),
    ("Brokerage Sweep", "Transfers", (False, True)),     # custom transfer under Monarch's group
    ("Buy", "Investments", (False, True)),               # a purchase changes shape, not wealth
    ("Brokerage Activity", "Investments", (False, True)),  # anything under the Investments parent
    ("Investments", "Other", (False, True)),             # no usable parent: name-set fallback
    ("Buy", None, (False, True)),                         # no parent at all: name-set fallback
    ("Credit Card Payment", "Other", (False, True)),     # name fallback
    ("Paychecks", None, (True, False)),
    # Income-named categories outside Monarch's Income group (Track 3 #7)
    ("Rental Income", "Other", (True, False)),
    ("Interest Income", "Other", (True, False)),
    ("Income", "Other", (True, False)),
    ("Consulting Income", "Business", (True, False)),
    ("Dividend & Cap Gains", "Other", (True, False)),
    ("Income Tax", "Taxes", (False, False)),             # doesn't END in the word
    ("Employee Wages & Contract Labor", "Business", (False, False)),
    ("Fixed Income", "Investments", (False, True)),      # an Investments parent stays a transfer
])
def test_classify_flags(name, parent, expected):
    assert classify_flags(name, parent) == expected


def test_flags_never_both():
    # A transfer-named category someone filed under Income is a transfer, not income.
    assert classify_flags("Transfer", "Income") == (False, True)
    assert classify_flags("Paychecks", "Investments") == (False, True)


@pytest.mark.parametrize("rate,expected", [
    (None, 0.0),
    (-693_000.0, 0.0),   # the observed field value
    (-5.0, 0.0),
    (0.0, 0.0),
    (15.0, 50.0),
    (30.0, 100.0),
    (80.0, 100.0),
])
def test_savings_rate_component_clamped(rate, expected):
    assert savings_rate_component(rate) == expected
