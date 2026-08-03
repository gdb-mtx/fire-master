"""Monarch sync unit tests — account-type mapping and snapshot sign convention.

Regression coverage for fire-master#6 (subtype fall-through mislabeled
cd/cash_management as CHECKING and bare roth as TAXABLE) and fire-master#7
(the today-snapshot wrote liabilities positive, breaking the history API's
liabilities-negative sign convention and spiking every liability chart).
"""

import pytest

from app.ingestion.monarch_sync import _map_account_type, _snapshot_balance
from app.models.enums import AccountType


class TestMapAccountType:
    @pytest.mark.parametrize("mtype,subtype,expected", [
        # fire-master#6: previously fell through to the depository parent (CHECKING)
        ("depository", "cd", AccountType.SAVINGS),
        ("depository", "cash_management", AccountType.SAVINGS),
        ("depository", "money_market", AccountType.SAVINGS),
        # fire-master#6: bare roth previously missed both ira checks → TAXABLE
        ("brokerage", "roth", AccountType.ROTH_IRA),
        ("investment", "roth", AccountType.ROTH_IRA),
        # Existing behavior that must not regress
        ("investment", "roth_ira", AccountType.ROTH_IRA),
        ("investment", "ira", AccountType.IRA),
        ("investment", "traditional_ira", AccountType.IRA),
        ("investment", "401k", AccountType.FOUR_OH_ONE_K),
        ("investment", "roth_401k", AccountType.FOUR_OH_ONE_K),  # 401 wins over roth
        ("investment", "hsa", AccountType.HSA),
        ("depository", "checking", AccountType.CHECKING),
        ("depository", "savings", AccountType.SAVINGS),
        ("investment", "brokerage", AccountType.TAXABLE),
        ("credit", None, AccountType.CREDIT_CARD),
        ("loan", None, AccountType.OTHER),
        ("mortgage", None, AccountType.REAL_ESTATE),
        ("depository", None, AccountType.CHECKING),
        ("vehicle", None, AccountType.VEHICLE),
        ("cryptocurrency", None, AccountType.CRYPTO),
    ])
    def test_mapping(self, mtype, subtype, expected):
        assert _map_account_type(mtype, subtype) == expected

    def test_unknown_type_falls_to_other(self):
        assert _map_account_type("someday_new_type", None) == AccountType.OTHER
        assert _map_account_type("", None) == AccountType.OTHER
        assert _map_account_type(None, None) == AccountType.OTHER

    def test_unknown_subtype_falls_to_parent(self):
        assert _map_account_type("depository", "novel_subtype") == AccountType.CHECKING

    def test_subtype_spaces_normalized(self):
        assert _map_account_type("depository", "Cash Management") == AccountType.SAVINGS


class TestSnapshotBalance:
    def test_asset_passes_through(self):
        assert _snapshot_balance(1_234_500, True) == 1_234_500
        assert _snapshot_balance(0, True) == 0

    def test_liability_negated(self):
        # fire-master#7: Monarch's account API reports loans as positive owed;
        # the history convention is liabilities negative
        assert _snapshot_balance(12_354_018, False) == -12_354_018

    def test_liability_already_negative_stays_negative(self):
        # -abs is idempotent: a source that already reports negative doesn't flip
        assert _snapshot_balance(-12_354_018, False) == -12_354_018

    def test_liability_zero(self):
        assert _snapshot_balance(0, False) == 0
