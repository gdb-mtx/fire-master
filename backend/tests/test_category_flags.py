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
    ("Buy", "Investments", (False, True)),               # brokerage purchase, not spending
    # Any custom category under Monarch's Investments group is also neutral.
    ("Brokerage Activity", "Investments", (False, True)),
    ("Investments", "Other", (False, True)),             # transaction-only fallback
    ("Buy", None, (False, True)),                         # transaction-only fallback
    ("Credit Card Payment", "Other", (False, True)),     # name fallback
    ("Paychecks", None, (True, False)),
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
