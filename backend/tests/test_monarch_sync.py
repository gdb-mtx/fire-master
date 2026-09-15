"""Monarch sync unit tests — account-type mapping and snapshot sign convention.

Regression coverage for fire-master#6 (subtype fall-through mislabeled
cd/cash_management as CHECKING and bare roth as TAXABLE) and fire-master#7
(the today-snapshot wrote liabilities positive, breaking the history API's
liabilities-negative sign convention and spiking every liability chart).
"""

import pytest

from app.ingestion.monarch_sync import (
    _account_upsert_stmt,
    _holding_basis_summary,
    _map_account_type,
    _snapshot_balance,
)
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
        # fire-master#8: explicit vehicle-loan subtypes (generic "loan" subtype
        # still maps OTHER — that's what the manual override is for)
        ("loan", "car", AccountType.VEHICLE),
        ("loan", "auto", AccountType.VEHICLE),
        ("loan", "auto_loan", AccountType.VEHICLE),
        ("loan", "vehicle_loan", AccountType.VEHICLE),
        ("loan", "loan", AccountType.OTHER),
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


class TestHoldingBasisSummary:
    def test_non_mapping_response_is_empty(self):
        assert _holding_basis_summary([])["holdings_count"] == 0

    def test_aggregates_basis_without_storing_security_details(self):
        payload = {
            "portfolio": {
                "aggregateHoldings": {
                    "edges": [
                        {"node": {"ticker": "ONE", "totalValue": 700, "basis": 500}},
                        {"node": {"ticker": "TWO", "totalValue": 300, "basis": 200}},
                    ],
                },
            },
        }
        assert _holding_basis_summary(payload) == {
            "holdings_value_cents": 100_000,
            "basis_covered_value_cents": 100_000,
            "cost_basis_cents": 70_000,
            "holdings_count": 2,
            "basis_known_count": 2,
        }

    def test_zero_basis_cash_is_basis_but_unknown_security_uses_fallback(self):
        payload = {
            "portfolio": {
                "aggregateHoldings": {
                    "edges": [
                        {"node": {"totalValue": 100, "basis": 0, "security": {"type": "cash"}}},
                        {"node": {"totalValue": 50, "basis": None}},
                    ],
                },
            },
        }
        result = _holding_basis_summary(payload)
        assert result["holdings_value_cents"] == 15_000
        assert result["basis_covered_value_cents"] == 10_000
        assert result["cost_basis_cents"] == 10_000

    def test_named_money_market_fund_is_basis_dollar_for_dollar(self):
        payload = {
            "portfolio": {
                "aggregateHoldings": {
                    "edges": [{
                        "node": {
                            "totalValue": 250,
                            "basis": 0,
                            "security": {
                                "type": "mutual_fund",
                                "name": "Example Municipal Money Market Fund",
                            },
                        },
                    }],
                },
            },
        }
        result = _holding_basis_summary(payload)
        assert result["basis_covered_value_cents"] == 25_000
        assert result["cost_basis_cents"] == 25_000


class TestAccountUpsertStmt:
    """The upsert's self-heal CASE must bind account_type through the Enum type.

    Regression: `else_=account_type` (the raw str-enum member) bound as a plain
    string — the lowercase .value ("roth_ira") — which Postgres rejected because
    the native enum labels are the member NAMES ("ROTH_IRA"). One bad bind
    aborted the whole sync transaction and every downstream step cascaded with
    InFailedSQLTransactionError. The fix routes else_ through excluded.account_type.
    """

    RAW = {
        "id": 166721573173020858,
        "displayName": "Roth IRA",
        "type": {"name": "brokerage"},
        "subtype": {"name": "roth"},
        "displayBalance": 100.0,
        "isAsset": True,
    }

    def _compiled(self):
        from sqlalchemy.dialects import postgresql

        return _account_upsert_stmt(self.RAW).compile(dialect=postgresql.dialect())

    def test_self_heal_uses_excluded_not_literal(self):
        # ELSE must reference the insert's excluded row, never a fresh bind param
        assert "ELSE excluded.account_type" in str(self._compiled())

    def test_no_bind_carries_raw_enum_value(self):
        compiled = self._compiled()
        for name, value in compiled.construct_params().items():
            proc = compiled.binds[name].type.dialect_impl(compiled.dialect).bind_processor(compiled.dialect)
            sent = proc(value) if proc else value
            assert sent != "roth_ira", f"param {name} binds the lowercase .value, not the PG label"

    def test_insert_values_bind_enum_name(self):
        compiled = self._compiled()
        bind = compiled.binds["account_type"]
        proc = bind.type.dialect_impl(compiled.dialect).bind_processor(compiled.dialect)
        assert proc(bind.value) == "ROTH_IRA"

    def test_manual_override_guard_present(self):
        compiled = self._compiled()
        # the JSON key rides in as a bind param, not literal SQL text
        assert "account_type_manual" in compiled.construct_params().values()
        assert "THEN accounts.account_type" in str(compiled)  # WHEN manual: keep the existing row's type
