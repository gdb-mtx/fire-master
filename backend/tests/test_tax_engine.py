"""Tax engine unit tests — pure math, zero DB dependency.

Tests federal and California brackets, LTCG, FICA, and bracket room
calculations against published or estimated tax tables.
"""

from app.engines.tax_engine import TaxEngine


# ---------------------------------------------------------------------------
# Federal income tax
# ---------------------------------------------------------------------------

class TestFederalTax:
    def test_zero_income(self, tax_engine: TaxEngine):
        result = tax_engine.compute_federal_tax(0, "single")
        assert result.total_federal_tax == 0
        assert result.effective_rate == 0

    def test_first_bracket_single(self, tax_engine: TaxEngine):
        # $10,000 taxable income — entirely in 10% bracket
        result = tax_engine.compute_federal_tax(10_000, "single")
        assert result.total_federal_tax == 1_000.0
        assert result.marginal_rate == 0.10

    def test_crosses_into_12pct_bracket(self, tax_engine: TaxEngine):
        # $30,000 taxable: $11,925 @ 10% + $18,075 @ 12%
        result = tax_engine.compute_federal_tax(30_000, "single")
        expected = 11_925 * 0.10 + 18_075 * 0.12  # 1192.50 + 2169.00 = 3361.50
        assert result.total_federal_tax == round(expected, 2)
        assert result.marginal_rate == 0.12

    def test_50k_single(self, tax_engine: TaxEngine):
        # $50,000 taxable: 10% on $11,925 + 12% on $36,550 + 22% on $1,525
        result = tax_engine.compute_federal_tax(50_000, "single")
        expected = 11_925 * 0.10 + 36_550 * 0.12 + 1_525 * 0.22
        assert result.total_federal_tax == round(expected, 2)
        assert result.marginal_rate == 0.22
        assert 0.11 < result.effective_rate < 0.13

    def test_married_filing_jointly(self, tax_engine: TaxEngine):
        # $50,000 MFJ: $23,850 @ 10% + $26,150 @ 12%
        result = tax_engine.compute_federal_tax(50_000, "married_filing_jointly")
        expected = 23_850 * 0.10 + 26_150 * 0.12
        assert result.total_federal_tax == round(expected, 2)
        assert result.marginal_rate == 0.12

    def test_high_income_single(self, tax_engine: TaxEngine):
        # $300,000 — should reach into the 32% or 35% bracket
        result = tax_engine.compute_federal_tax(300_000, "single")
        assert result.marginal_rate == 0.35
        assert result.total_federal_tax > 70_000


# ---------------------------------------------------------------------------
# California income tax
# ---------------------------------------------------------------------------

class TestCaliforniaTax:
    def test_mfj_progressive_brackets_and_state_deduction(self, tax_engine: TaxEngine):
        result = tax_engine.compute_configured_state_tax(
            250_000,
            {"state": "CA", "filing_status": "married_filing_jointly"},
        )

        assert result.method == "california_progressive_2025"
        assert result.standard_deduction == 11_412
        assert result.taxable_income == 238_588
        assert result.income_tax == 15_065.96
        assert result.payroll_tax == 0
        assert result.marginal_rate == 0.093

    def test_millionaire_surtax_and_uncapped_sdi(self, tax_engine: TaxEngine):
        result = tax_engine.compute_configured_state_tax(
            1_200_000,
            {"state": "California", "filing_status": "married_filing_jointly"},
            earned_income=1_200_000,
        )

        assert result.income_tax == 112_728.60
        assert result.payroll_tax == 15_600
        assert result.total_tax == 128_328.60
        assert result.marginal_rate == 0.123  # 11.3% bracket + 1% surtax

    def test_separate_federal_and_california_itemized_deductions(self, tax_engine: TaxEngine):
        config = {
            "state": "CA",
            "filing_status": "married_filing_jointly",
            "federal_deduction_method": "itemized",
            "federal_itemized_deduction": 75_000,
            "state_deduction_method": "itemized",
            "state_itemized_deduction": 50_000,
        }

        assert tax_engine._get_standard_deduction(config) == 75_000
        state = tax_engine.compute_configured_state_tax(250_000, config)
        assert state.deduction_method == "itemized"
        assert state.standard_deduction == 50_000
        assert state.taxable_income == 200_000

    def test_non_california_preserves_flat_rate_fallback(self, tax_engine: TaxEngine):
        result = tax_engine.compute_configured_state_tax(
            100_000,
            {
                "state": "UT",
                "state_tax_rate": 4.4,
                "filing_status": "single",
            },
        )

        assert result.method == "flat_rate"
        assert result.taxable_income == 84_300
        assert result.total_tax == 3_709.20


# ---------------------------------------------------------------------------
# Long-term capital gains
# ---------------------------------------------------------------------------

class TestCapitalGains:
    def test_ltcg_zero_bracket(self, tax_engine: TaxEngine):
        # $30K LTCG with $10K ordinary income — all gains in 0% bracket (up to $48,350)
        tax = tax_engine.compute_capital_gains_tax(30_000, 10_000, "single")
        assert tax == 0.0

    def test_ltcg_straddles_0_and_15(self, tax_engine: TaxEngine):
        # $30K LTCG with $40K ordinary income (single)
        # 0% bracket up to $48,350 → $8,350 at 0%, $21,650 at 15%
        tax = tax_engine.compute_capital_gains_tax(30_000, 40_000, "single")
        expected = 8_350 * 0.0 + 21_650 * 0.15  # $3,247.50
        assert tax == round(expected, 2)

    def test_ltcg_fully_in_15pct(self, tax_engine: TaxEngine):
        # $20K LTCG with $100K ordinary income — entirely in 15% bracket
        tax = tax_engine.compute_capital_gains_tax(20_000, 100_000, "single")
        assert tax == 20_000 * 0.15

    def test_short_term_taxed_as_ordinary(self, tax_engine: TaxEngine):
        # Short-term gains are taxed as ordinary income (delta method)
        tax = tax_engine.compute_capital_gains_tax(10_000, 50_000, "single", is_long_term=False)
        full = tax_engine.compute_federal_tax(60_000, "single")
        base = tax_engine.compute_federal_tax(50_000, "single")
        expected = full.total_federal_tax - base.total_federal_tax
        assert tax == round(expected, 2)


# ---------------------------------------------------------------------------
# FICA
# ---------------------------------------------------------------------------

class TestFICA:
    def test_zero_income(self, tax_engine: TaxEngine):
        assert tax_engine.compute_fica(0) == 0.0

    def test_below_ss_wage_base(self, tax_engine: TaxEngine):
        # $100K: SS = $100K * 6.2% = $6,200, Medicare = $100K * 1.45% = $1,450
        tax = tax_engine.compute_fica(100_000)
        assert tax == round(6_200 + 1_450, 2)

    def test_above_ss_wage_base(self, tax_engine: TaxEngine):
        # $200K: SS capped at $176,100 * 6.2%, Medicare on full $200K
        tax = tax_engine.compute_fica(200_000, "single")
        ss = 176_100 * 0.062
        medicare = 200_000 * 0.0145
        assert tax == round(ss + medicare, 2)

    def test_medicare_surtax(self, tax_engine: TaxEngine):
        # $250K single: above $200K threshold → 0.9% surtax on $50K
        tax = tax_engine.compute_fica(250_000, "single")
        ss = 176_100 * 0.062
        medicare = 250_000 * 0.0145
        surtax = 50_000 * 0.009
        assert tax == round(ss + medicare + surtax, 2)


# ---------------------------------------------------------------------------
# Bracket room
# ---------------------------------------------------------------------------

class TestBracketRoom:
    def test_mid_bracket(self, tax_engine: TaxEngine):
        # $30K taxable (single) — in 12% bracket, ceiling at $48,475
        room = tax_engine.get_bracket_room(30_000, "single")
        assert room.current_bracket_rate == 0.12
        assert room.next_bracket_rate == 0.22
        assert room.room_in_current == 48_475 - 30_000

    def test_at_bracket_boundary(self, tax_engine: TaxEngine):
        # Exactly at $48,475 — still in 12% bracket (up_to is exclusive boundary)
        room = tax_engine.get_bracket_room(48_475, "single")
        assert room.current_bracket_rate == 0.12
        assert room.room_in_current == 0  # at the ceiling

    def test_top_bracket(self, tax_engine: TaxEngine):
        # $700K — in top 37% bracket
        room = tax_engine.get_bracket_room(700_000, "single")
        assert room.current_bracket_rate == 0.37
        assert room.next_bracket_rate is None
        assert room.room_in_current == 0
