"""Tax-planning behavior tests: MAGI, ACA, Roth conversion ladder, withdrawal
sequencing.

These cover the higher-order planners that sit above the bracket primitives
(which test_tax_engine.py already covers). All DB access is mocked; balances
are given in DOLLARS in the helper for readability and converted to cents on
the account mocks.

Documented simplifications (asserted here as intended behavior, not bugs):
- ACA below 100% FPL still gets max subsidy (no Medicaid-gap modeling), and
  cliff_warning only fires on the *approaching* side of the 400% FPL cliff.
- The withdrawal sequencer runs in REAL terms: spending flat, balances at
  the real return ((1.07/1.03)−1 for the persona), brackets frozen at
  today's levels (≈ IRS inflation indexing). By default, taxes are reported
  but not deducted from balances; the source-aware gross-up tests explicitly
  enable funding taxes from withdrawals. Shortfalls draw through the
  waterfall pre-retirement too.
- total_withdrawn excludes cash draws (cash is already-taxed money).
  average_effective_rate = total tax / total GROSS income (income sources +
  all draws), matching each year's effective_rate — fire-master#4 killed the
  old withdrawals-only denominator that could exceed 100%. Per-year
  total_income/after_tax_income DO include cash draws.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engines.tax_engine import (
    DEFAULT_STANDARD_DEDUCTION,
    AccountsByTaxTreatment,
    TaxEngine,
)
from app.models.enums import IncomeType

from .conftest import FROZEN_TODAY, _make_fire_config

STD_DED = DEFAULT_STANDARD_DEDUCTION["single"]  # 15,700
CEILING_22 = 103_350  # single-filer 22% bracket ceiling (2026 estimates)
STATE_RATE = 4.4  # persona tax config (CO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def frozen_today_tax():
    """Patch date.today() in the tax engine to 2026-04-14."""
    with patch("app.engines.tax_engine.date") as mock_date:
        mock_date.today.return_value = FROZEN_TODAY
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        yield mock_date


def _acct(dollars: float) -> MagicMock:
    a = MagicMock()
    a.current_balance = round(dollars * 100)
    return a


def _income_source(name: str, itype: IncomeType, annual_dollars: float,
                   start=None, end=None, taxable=True) -> MagicMock:
    s = MagicMock()
    s.name = name
    s.income_type = itype
    s.annual_amount = round(annual_dollars * 100)
    s.start_date = start
    s.end_date = end
    s.is_active = True
    s.growth_rate = None
    s.is_taxable = taxable
    return s


def _make_planning_engine(
    *, deferred=0.0, roth=0.0, taxable=0.0, cash=0.0, income_sources=(),
) -> TaxEngine:
    """TaxEngine with all DB access mocked (balances in DOLLARS)."""
    engine = TaxEngine(db=None)
    grouped = AccountsByTaxTreatment(
        tax_deferred=[_acct(deferred)] if deferred else [],
        tax_free=[_acct(roth)] if roth else [],
        taxable=[_acct(taxable)] if taxable else [],
        already_taxed=[_acct(cash)] if cash else [],
    )
    engine.get_accounts_by_tax_treatment = AsyncMock(return_value=grouped)
    engine._get_active_income_sources = AsyncMock(return_value=list(income_sources))
    return engine


def _patch_config(config):
    """TaxEngine constructs a FireProjectionsEngine internally for config.

    Patches get_effective_config — the engine layer reads the scenario-merged
    config (fire-master#5 follow-up), not the base one.
    """
    return patch(
        "app.engines.fire_projections.FireProjectionsEngine.get_effective_config",
        AsyncMock(return_value=config),
    )


# ---------------------------------------------------------------------------
# MAGI
# ---------------------------------------------------------------------------

class TestComputeMagi:
    def test_sums_all_components(self, tax_engine):
        magi = tax_engine.compute_magi(
            earned_income=50_000, pension=10_000, deferred_withdrawals=20_000,
            roth_conversions=30_000, capital_gains=5_000, rental_income=12_000,
            dividend_income=3_000,
        )
        assert magi == 130_000

    def test_ss_85pct_haircut(self, tax_engine):
        assert tax_engine.compute_magi(social_security=40_000) == pytest.approx(34_000)

    def test_zero_inputs_zero(self, tax_engine):
        assert tax_engine.compute_magi() == 0


# ---------------------------------------------------------------------------
# ACA subsidy eligibility
# ---------------------------------------------------------------------------

class TestACAEligibility:
    def test_household_size_scales_fpl(self, tax_engine):
        # FPL = 15,060 + 5,380 × (n − 1)
        assert tax_engine.compute_aca_eligibility(30_000, household_size=1).fpl == 15_060
        assert tax_engine.compute_aca_eligibility(30_000, household_size=4).fpl == 31_200

    def test_below_150_fpl_max_subsidy(self, tax_engine):
        r = tax_engine.compute_aca_eligibility(20_000, household_size=1, age=55)
        assert r.fpl_percentage < 150
        assert r.estimated_monthly_subsidy == pytest.approx(r.estimated_monthly_premium * 0.95)

    def test_subsidy_tiers_slide(self, tax_engine):
        # Same premium (fixed age), rising MAGI → monotonically non-increasing subsidy
        fpl = 15_060
        subsidies = [
            tax_engine.compute_aca_eligibility(fpl * pct / 100, household_size=1, age=55)
            .estimated_monthly_subsidy
            for pct in (140, 190, 240, 290, 390, 450)
        ]
        assert subsidies == sorted(subsidies, reverse=True)
        assert subsidies[-1] == 0  # above 400% FPL

    def test_at_400_fpl_still_eligible(self, tax_engine):
        r = tax_engine.compute_aca_eligibility(15_060 * 4, household_size=1)
        assert r.subsidy_eligible is True

    def test_above_400_fpl_not_eligible(self, tax_engine):
        r = tax_engine.compute_aca_eligibility(15_060 * 4 + 1, household_size=1)
        assert r.subsidy_eligible is False
        assert r.estimated_monthly_subsidy == 0

    def test_cliff_warning_approaching_side_only(self, tax_engine):
        cliff = 15_060 * 4
        # Just under the cliff → warn
        assert tax_engine.compute_aca_eligibility(cliff - 3_000).cliff_warning is True
        # Over the cliff → no warning (already fallen off)
        assert tax_engine.compute_aca_eligibility(cliff + 1_000).cliff_warning is False
        # Far below → no warning
        assert tax_engine.compute_aca_eligibility(20_000).cliff_warning is False

    def test_premium_age_clamps(self, tax_engine):
        assert tax_engine.compute_aca_eligibility(30_000, age=10).estimated_monthly_premium == 300
        assert tax_engine.compute_aca_eligibility(30_000, age=110).estimated_monthly_premium == 1_200


# ---------------------------------------------------------------------------
# Roth conversion ladder
# ---------------------------------------------------------------------------

class TestRothConversionPlan:
    async def test_no_dob_returns_empty_plan(self, frozen_today_tax):
        config = _make_fire_config(date_of_birth=None)
        engine = _make_planning_engine(deferred=500_000)
        with _patch_config(config):
            plan = await engine.plan_roth_conversions()
        assert plan.years == []
        assert plan.total_converted == 0
        assert "N/A" in plan.conversion_window

    async def test_window_closed_after_ss_start(self, frozen_today_tax):
        # DOB 1953 → SS start (67) was 2020, long before the frozen today.
        config = _make_fire_config(date_of_birth=date(1953, 6, 1))
        engine = _make_planning_engine(deferred=500_000)
        with _patch_config(config):
            plan = await engine.plan_roth_conversions()
        assert plan.years == []
        assert "No conversion window" in plan.conversion_window

    async def test_fills_to_bracket_ceiling(self, base_fire_config, frozen_today_tax):
        """With zero baseline income the conversion absorbs the unused standard
        deduction: converted = ceiling + std_ded, post-deduction taxable lands
        exactly at the 22% ceiling."""
        engine = _make_planning_engine(deferred=2_000_000)
        with _patch_config(base_fire_config):
            plan = await engine.plan_roth_conversions(target_bracket_rate=0.22)
        # Window: retirement (2026-07) → SS start (2040-07) = 14 annual rungs
        assert len(plan.years) == 14
        first = plan.years[0]
        assert first.conversion_amount == pytest.approx(CEILING_22 + STD_DED)
        assert first.total_taxable_income == pytest.approx(CEILING_22)

    async def test_respects_deferred_balance(self, base_fire_config, frozen_today_tax):
        engine = _make_planning_engine(deferred=150_000)
        with _patch_config(base_fire_config):
            plan = await engine.plan_roth_conversions()
        assert plan.total_converted == pytest.approx(150_000)
        assert len(plan.years) == 2  # 119,050 then the 30,950 remainder

    async def test_zero_room_years_skipped(self, base_fire_config, frozen_today_tax):
        # Baseline (rental) income above ceiling + deduction → no room, ever.
        big_rental = _income_source("Rental", IncomeType.RENTAL, 150_000)
        engine = _make_planning_engine(deferred=500_000, income_sources=[big_rental])
        with _patch_config(base_fire_config):
            plan = await engine.plan_roth_conversions(target_bracket_rate=0.22)
        assert plan.years == []
        assert plan.total_converted == 0

    async def test_per_year_tax_is_marginal_delta(self, base_fire_config, frozen_today_tax, tax_engine):
        """Year tax = federal(baseline+conversion−ded) − federal(baseline−ded)
        + state on the conversion, recomputed via the tested primitives."""
        rental = _income_source("Rental", IncomeType.RENTAL, 30_000)
        engine = _make_planning_engine(deferred=2_000_000, income_sources=[rental])
        with _patch_config(base_fire_config):
            plan = await engine.plan_roth_conversions(target_bracket_rate=0.22)
        yr = plan.years[0]
        assert yr.conversion_amount == pytest.approx(CEILING_22 + STD_DED - 30_000)
        fed_with = tax_engine.compute_federal_tax(max(0, 30_000 + yr.conversion_amount - STD_DED)).total_federal_tax
        fed_without = tax_engine.compute_federal_tax(max(0, 30_000 - STD_DED)).total_federal_tax
        expected = fed_with - fed_without + tax_engine.compute_state_tax(yr.conversion_amount, STATE_RATE)
        assert yr.tax_on_conversion == pytest.approx(expected, abs=0.01)

    async def test_savings_uses_config_rmd_rate(self, frozen_today_tax):
        config = _make_fire_config()
        config.custom_assumptions["tax"] = {
            **config.custom_assumptions["tax"],
            "assumed_rmd_marginal_rate": 0.32,
        }
        engine = _make_planning_engine(deferred=300_000)
        with _patch_config(config):
            plan = await engine.plan_roth_conversions()
        assert plan.assumed_rmd_marginal_rate == 0.32
        expected = round(max(0, plan.total_converted * 0.32 - plan.total_tax_paid), 2)
        assert plan.estimated_tax_saved == pytest.approx(expected, abs=0.02)


# ---------------------------------------------------------------------------
# Withdrawal sequencing
# ---------------------------------------------------------------------------

class TestWithdrawalSequence:
    async def test_tax_deferred_withdrawals_are_grossed_up_to_fund_tax(
        self, base_fire_config, frozen_today_tax,
    ):
        base_fire_config.healthcare_monthly_cost = None
        base_fire_config.custom_assumptions["tax"]["fund_taxes_from_withdrawals"] = True
        engine = _make_planning_engine(deferred=1_000_000)

        with _patch_config(base_fire_config):
            plan = await engine.optimize_withdrawal_sequence(
                years=1, roth_conversions_enabled=False,
            )

        year = plan.years[0]
        assert year.from_deferred > year.spending_need
        assert year.taxes_funded == pytest.approx(year.total_tax, abs=0.02)
        assert year.net_spendable == pytest.approx(year.spending_need, abs=0.05)

    async def test_net_salary_withholding_is_not_funded_twice(
        self, base_fire_config, frozen_today_tax,
    ):
        base_fire_config.healthcare_monthly_cost = None
        base_fire_config.target_annual_spending = 12_000_000
        base_fire_config.target_retirement_age = 60
        base_fire_config.custom_assumptions["tax"]["fund_taxes_from_withdrawals"] = True
        salary = _income_source("Gross salary", IncomeType.SALARY, 200_000)
        salary.custom_data = {"net_annual_amount": 15_000_000}
        engine = _make_planning_engine(deferred=1_000_000, income_sources=[salary])

        with _patch_config(base_fire_config):
            plan = await engine.optimize_withdrawal_sequence(
                years=1, roth_conversions_enabled=False,
            )

        year = plan.years[0]
        assert year.from_deferred == 0
        assert year.taxes_funded == 0
        assert year.net_spendable == pytest.approx(150_000)

    async def test_waterfall_order_taxable_first(self, base_fire_config, frozen_today_tax):
        base_fire_config.healthcare_monthly_cost = None
        engine = _make_planning_engine(taxable=800_000, deferred=400_000, roth=200_000)
        with _patch_config(base_fire_config):
            plan = await engine.optimize_withdrawal_sequence(
                years=3, roth_conversions_enabled=False)
        # Year 0 (2026): retirement is 2026-07 but the shortfall (no income
        # mocked) draws through the waterfall anyway — a pre-retirement gap
        # is a drawdown too (pre-fix, year 0 silently took nothing).
        y0 = plan.years[0]
        assert y0.from_taxable == pytest.approx(153_000, rel=1e-6)
        # Year 1: spending is FLAT in real terms; taxable still covers it.
        y1 = plan.years[1]
        assert y1.from_taxable == pytest.approx(153_000, rel=1e-6)
        assert y1.from_deferred == 0
        assert y1.from_roth == 0
        assert y1.capital_gains_income == pytest.approx(y1.from_taxable * 0.4)

    async def test_depletion_cascades(self, base_fire_config, frozen_today_tax):
        base_fire_config.healthcare_monthly_cost = None
        engine = _make_planning_engine(taxable=150_000, deferred=300_000, roth=500_000)
        with _patch_config(base_fire_config):
            plan = await engine.optimize_withdrawal_sequence(
                years=3, roth_conversions_enabled=False)
        y0, y1 = plan.years[0], plan.years[1]
        # Year 0 drains taxable (150k < 153k need), deferred covers the rest.
        assert y0.from_taxable == pytest.approx(150_000, rel=1e-6)
        assert y0.from_deferred == pytest.approx(153_000 - 150_000, rel=1e-6)
        # Year 1: taxable is empty — everything from deferred (flat real need).
        assert y1.from_taxable == 0
        assert y1.from_deferred == pytest.approx(153_000, rel=1e-6)

    async def test_rmd_forced_at_73_excess_lands_in_taxable(self, frozen_today_tax):
        """Age 73+: the full RMD comes out of deferred even when spending needs
        far less, and the unspent excess is redeposited into taxable — visible
        as taxable draws in the following year (pre-fix, taxable stayed 0 and
        the excess RMD simply vanished from the model)."""
        config = _make_fire_config(
            date_of_birth=date(1953, 6, 1),  # age 73.6 in plan year 2027
            target_annual_spending=3_000_000,  # $30K — far below the RMD
            social_security_monthly=None,
        )
        engine = _make_planning_engine(deferred=2_000_000)
        with _patch_config(config):
            plan = await engine.optimize_withdrawal_sequence(
                years=3, roth_conversions_enabled=False)
        y0, y1, y2 = plan.years[0], plan.years[1], plan.years[2]
        # 2026 (age 72.6): below RMD age → draws only what spending needs.
        assert y0.from_deferred == pytest.approx(30_000)
        # 2027 (73.6): RMD divisor 26.5 forces the minimum on the post-draw
        # balance (model simplification vs the IRS prior-Dec-31 basis).
        # Balances grow at the REAL return (1.07/1.03); spending flat real.
        real_growth = 1.07 / 1.03
        deferred_after_draw = (2_000_000 - 30_000) * real_growth - 30_000
        assert y1.from_deferred == pytest.approx(deferred_after_draw / 26.5, rel=1e-6)
        assert y1.from_deferred > 30_000
        # 2028: spending is drawn from taxable — the redeposited RMD excess.
        assert y2.from_taxable == pytest.approx(30_000, rel=1e-6)

    async def test_roth_conversion_fills_bracket_in_window(self, base_fire_config, frozen_today_tax):
        engine = _make_planning_engine(taxable=800_000, deferred=400_000)
        with _patch_config(base_fire_config):
            plan = await engine.optimize_withdrawal_sequence(years=3)
        y1 = plan.years[1]  # 2027: retired, pre-SS (2040), pre-RMD
        # Zero other ordinary income → conversion = ceiling + std deduction,
        # landing post-deduction taxable exactly at the 22% ceiling.
        assert y1.roth_conversion == pytest.approx(CEILING_22 + STD_DED)
        assert y1.ordinary_income - STD_DED == pytest.approx(CEILING_22)
        # Toggle off → no conversions anywhere.
        with _patch_config(base_fire_config):
            plan_off = await engine.optimize_withdrawal_sequence(
                years=3, roth_conversions_enabled=False)
        assert all(y.roth_conversion == 0 for y in plan_off.years)

    async def test_year_tax_matches_primitives(self, base_fire_config, frozen_today_tax, tax_engine):
        engine = _make_planning_engine(taxable=800_000, deferred=400_000)
        with _patch_config(base_fire_config):
            plan = await engine.optimize_withdrawal_sequence(years=2)
        y1 = plan.years[1]
        taxable_income = max(0, y1.ordinary_income - STD_DED)
        fed = tax_engine.compute_federal_tax(taxable_income).total_federal_tax
        cg = tax_engine.compute_capital_gains_tax(y1.capital_gains_income, taxable_income)
        state = tax_engine.compute_state_tax(
            y1.ordinary_income + y1.capital_gains_income - STD_DED, STATE_RATE)
        assert y1.federal_tax == pytest.approx(fed + cg, abs=0.02)
        assert y1.state_tax == pytest.approx(state, abs=0.02)
        assert y1.fica_tax == 0  # no earned income in retirement
        assert y1.total_tax == pytest.approx(fed + cg + state, abs=0.05)

    async def test_totals_consistent(self, base_fire_config, frozen_today_tax):
        engine = _make_planning_engine(taxable=500_000, deferred=300_000, roth=200_000)
        with _patch_config(base_fire_config):
            plan = await engine.optimize_withdrawal_sequence(years=5)
        total_drawn = sum(y.from_taxable + y.from_deferred + y.from_roth for y in plan.years)
        total_tax = sum(y.total_tax for y in plan.years)
        assert plan.total_withdrawn == pytest.approx(total_drawn, abs=0.05)
        assert plan.total_tax_paid == pytest.approx(total_tax, abs=0.05)
        if total_drawn > 0:
            assert plan.average_effective_rate == pytest.approx(total_tax / total_drawn, abs=1e-4)

    async def test_cash_draws_visible_and_real_yield(self, frozen_today_tax):
        """Cash draws are recorded per year (previously invisible in the
        response). In real terms cash is FLAT by default (holds purchasing
        power, earns nothing above inflation); a configured positive REAL
        yield compounds. Cash-only plan, $170K vs $60K flat spending —
        year 2's partial draw pins the behavior exactly."""
        # Default: real yield 0 → cash balance just depletes
        config = _make_fire_config(
            target_annual_spending=6_000_000,
            healthcare_monthly_cost=None,
        )  # $60K
        engine = _make_planning_engine(cash=170_000)
        with _patch_config(config):
            plan = await engine.optimize_withdrawal_sequence(
                years=3, roth_conversions_enabled=False)
        y0, y1, y2 = plan.years
        assert y0.from_cash == pytest.approx(60_000)
        assert y1.from_cash == pytest.approx(60_000)
        assert y2.from_cash == pytest.approx(170_000 - 120_000)  # the flat remainder
        # Cash draws count as spendable income in the year rows
        assert y0.total_income == pytest.approx(y0.from_cash)
        assert y0.after_tax_income == pytest.approx(y0.from_cash - y0.total_tax)

        # Configured +2% REAL yield (HYSA above inflation) → compounds
        config2 = _make_fire_config(
            target_annual_spending=6_000_000,
            healthcare_monthly_cost=None,
        )
        config2.custom_assumptions["tax"] = {
            **config2.custom_assumptions["tax"], "cash_yield_rate": 0.02,
        }
        with _patch_config(config2):
            plan2 = await engine.optimize_withdrawal_sequence(
                years=3, roth_conversions_enabled=False)
        # ((170k − 60k)·1.02 − 60k)·1.02 = 53,244 — year 2 drains it exactly
        expected_y2 = ((170_000 - 60_000) * 1.02 - 60_000) * 1.02
        assert plan2.years[2].from_cash == pytest.approx(expected_y2, abs=1.0)


# ---------------------------------------------------------------------------
# Scenario awareness (fire-master#5 follow-up: the tax planners read the
# EFFECTIVE config and forward an explicit scenario_id, like monte-carlo)
# ---------------------------------------------------------------------------

class TestScenarioAwareness:
    async def test_withdrawal_plan_forwards_scenario_id(self, base_fire_config, frozen_today_tax):
        import uuid

        sid = uuid.uuid4()
        engine = _make_planning_engine(cash=100_000)
        with _patch_config(base_fire_config) as eff:
            await engine.optimize_withdrawal_sequence(
                years=1, roth_conversions_enabled=False, scenario_id=sid)
        eff.assert_awaited_once_with(sid)

    async def test_roth_plan_forwards_scenario_id(self, base_fire_config, frozen_today_tax):
        import uuid

        sid = uuid.uuid4()
        engine = _make_planning_engine(deferred=100_000)
        with _patch_config(base_fire_config) as eff:
            await engine.plan_roth_conversions(scenario_id=sid)
        eff.assert_awaited_once_with(sid)

    async def test_scenario_spending_changes_the_plan(self, frozen_today_tax):
        """A scenario that overrides target_annual_spending must change the
        withdrawal sequence — pre-fix, scenarios had zero effect here."""
        base = _make_fire_config(target_annual_spending=6_000_000)   # $60K
        merged = _make_fire_config(target_annual_spending=12_000_000)  # $120K override
        engine = _make_planning_engine(taxable=500_000)
        with _patch_config(base):
            plan_base = await engine.optimize_withdrawal_sequence(
                years=1, roth_conversions_enabled=False)
        engine2 = _make_planning_engine(taxable=500_000)
        with _patch_config(merged):
            plan_scenario = await engine2.optimize_withdrawal_sequence(
                years=1, roth_conversions_enabled=False)
        assert plan_base.years[0].from_taxable == pytest.approx(60_000)
        assert plan_scenario.years[0].from_taxable == pytest.approx(120_000)


# ---------------------------------------------------------------------------
# is_taxable gating (fire-master#4)
# ---------------------------------------------------------------------------

class TestIsTaxable:
    """Regression tests for fire-master#4: IncomeSource.is_taxable was
    stored and echoed by the API but never read by the engine layer — every
    active source was taxed by income_type alone."""

    async def test_non_taxable_source_pays_zero_tax(self, frozen_today_tax):
        """A non-taxable source covering all spending → zero tax, zero
        withdrawals, zero effective rate. The pre-fix engine taxed it as
        ordinary income."""
        config = _make_fire_config(target_annual_spending=6_000_000)  # $60K
        benefit = _income_source("VA benefit", IncomeType.OTHER, 80_000,
                                 taxable=False)
        engine = _make_planning_engine(deferred=0, income_sources=[benefit])
        with _patch_config(config):
            plan = await engine.optimize_withdrawal_sequence(
                years=3, roth_conversions_enabled=False)
        assert plan.total_withdrawn == 0
        assert plan.total_tax_paid == 0
        assert plan.average_effective_rate == 0
        for y in plan.years:
            assert y.total_tax == 0
            assert y.magi == 0

    async def test_taxable_flag_still_taxed(self, frozen_today_tax):
        """Differential pair: the same source flagged taxable IS taxed —
        the gate reads the flag, it doesn't blanket-exempt income."""
        config = _make_fire_config(target_annual_spending=6_000_000)
        gross = _income_source("Gross pension-ish", IncomeType.OTHER, 80_000,
                               taxable=True)
        engine = _make_planning_engine(deferred=0, income_sources=[gross])
        with _patch_config(config):
            plan = await engine.optimize_withdrawal_sequence(
                years=3, roth_conversions_enabled=False)
        assert plan.total_tax_paid > 0

    async def test_non_taxable_salary_pays_no_fica(self, frozen_today_tax):
        """A net-of-tax salary flagged non-taxable skips FICA too (it was
        already withheld from the net amount)."""
        config = _make_fire_config(
            target_annual_spending=6_000_000, target_retirement_age=60)
        net_salary = _income_source("Salary (net)", IncomeType.SALARY, 100_000,
                                    taxable=False)
        engine = _make_planning_engine(deferred=0, income_sources=[net_salary])
        with _patch_config(config):
            plan = await engine.optimize_withdrawal_sequence(
                years=2, roth_conversions_enabled=False)
        for y in plan.years:
            assert y.fica_tax == 0
            assert y.total_tax == 0

    async def test_effective_rate_never_exceeds_100pct(self, frozen_today_tax):
        """timswett's repro: taxable salary + non-taxable benefit, spending
        covered by income (no withdrawals) over multiple years. The old
        tax/withdrawals denominator produced >100% effective rates; the rate
        is now tax over gross income."""
        config = _make_fire_config(
            target_annual_spending=9_000_000, target_retirement_age=60)  # $90K
        salary = _income_source("Salary", IncomeType.SALARY, 100_000, taxable=True)
        benefit = _income_source("Benefit", IncomeType.OTHER, 10_000, taxable=False)
        engine = _make_planning_engine(
            deferred=200_000, income_sources=[salary, benefit])
        with _patch_config(config):
            plan = await engine.optimize_withdrawal_sequence(
                years=3, roth_conversions_enabled=False)
        assert 0 < plan.average_effective_rate < 1

    async def test_non_taxable_income_leaves_conversion_room_open(
        self, frozen_today_tax,
    ):
        """Golden-window bracket-fill: a non-taxable source doesn't consume
        bracket room, so the Roth conversion is larger by exactly its amount
        vs the same source flagged taxable."""
        def run(taxable_flag):
            config = _make_fire_config(target_annual_spending=3_000_000)  # $30K
            src = _income_source("Rental", IncomeType.RENTAL, 30_000,
                                 taxable=taxable_flag)
            return config, _make_planning_engine(
                deferred=1_000_000, income_sources=[src])

        config_t, engine_t = run(True)
        with _patch_config(config_t):
            plan_taxable = await engine_t.optimize_withdrawal_sequence(years=2)
        config_n, engine_n = run(False)
        with _patch_config(config_n):
            plan_non_taxable = await engine_n.optimize_withdrawal_sequence(years=2)

        # 2027 is the first retired year (persona retires Jul 2026)
        conv_t = plan_taxable.years[1].roth_conversion
        conv_n = plan_non_taxable.years[1].roth_conversion
        assert conv_n - conv_t == pytest.approx(30_000, abs=1.0)

    async def test_bracket_analysis_excludes_non_taxable(
        self, base_fire_config, frozen_today_tax,
    ):
        """analyze_brackets had the same hole: non-taxable sources inflated
        gross income, federal tax, FICA, and MAGI."""
        salary = _income_source("Salary (net)", IncomeType.SALARY, 100_000,
                                taxable=False)
        rental = _income_source("Rental", IncomeType.RENTAL, 36_000, taxable=True)
        engine = _make_planning_engine(
            deferred=50_000, income_sources=[salary, rental])
        with _patch_config(base_fire_config):
            result = await engine.analyze_brackets()
        assert result["gross_income"] == pytest.approx(36_000)
        assert result["fica_tax"] == 0
        assert result["aca"]["magi"] == pytest.approx(36_000)


# ---------------------------------------------------------------------------
# Bracket analysis (current-year income proration + MAGI)
# ---------------------------------------------------------------------------

class TestBracketAnalysis:
    async def test_prorates_ended_and_future_sources(self, base_fire_config, frozen_today_tax):
        """Calendar-year day-proration: a salary that ended Mar 31 contributes
        90/365 of its annual amount (not the full year — the old behavior
        overstated headline income); a source starting Nov 1 contributes
        61/365; a dateless source counts in full. MAGI now includes
        NON-earned income too (previously earned-only)."""
        salary = _income_source("Salary (ended)", IncomeType.SALARY, 120_000,
                                end=date(2026, 3, 31))
        rental = _income_source("Rental", IncomeType.RENTAL, 12_000)
        consulting = _income_source("Consulting (starts Nov)", IncomeType.SALARY, 120_000,
                                    start=date(2026, 11, 1))
        engine = _make_planning_engine(
            deferred=100_000, income_sources=[salary, rental, consulting])
        with _patch_config(base_fire_config):
            result = await engine.analyze_brackets()

        expected_salary = 120_000 * 90 / 365   # Jan 1 – Mar 31
        expected_consulting = 120_000 * 61 / 365  # Nov 1 – Dec 31
        expected_gross = expected_salary + 12_000 + expected_consulting
        assert result["gross_income"] == pytest.approx(expected_gross, abs=0.5)
        # FICA only on the prorated EARNED income
        expected_fica = engine.compute_fica(expected_salary + expected_consulting)
        assert result["fica_tax"] == pytest.approx(expected_fica, abs=1.0)
        # MAGI includes the rental (non-earned) income
        assert result["aca"]["magi"] == pytest.approx(expected_gross, abs=0.5)

    async def test_dateless_sources_count_in_full(self, base_fire_config, frozen_today_tax):
        rental = _income_source("Rental", IncomeType.RENTAL, 36_000)
        engine = _make_planning_engine(deferred=50_000, income_sources=[rental])
        with _patch_config(base_fire_config):
            result = await engine.analyze_brackets()
        assert result["gross_income"] == pytest.approx(36_000)
        assert result["fica_tax"] == 0  # nothing earned
