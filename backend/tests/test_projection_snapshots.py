"""Projection engine snapshot tests — validates known-good outputs for all 3 scenarios.

Mocks all DB access (4 async methods + 2 direct select() calls) and freezes
date.today() to 2026-04-14 for deterministic results. This is the safety net
that unblocks engine refactoring.

Known-good values are snapshots of the synthetic test persona:
  - Keep Both — Optimized
  - Primary 5-Year Exit (keep the income property)
  - Sell Income Property + Pay Down Primary
"""

from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engines.fire_projections import FireProjectionsEngine
from app.models.enums import IncomeType
from app.models.fire_config import FireConfig
from app.schemas.fire import NetWorthBreakdown
from tests.conftest import (
    FROZEN_TODAY,
    SCENARIO_KEEP_BOTH,
    SCENARIO_PRIMARY_5YR,
    SCENARIO_SELL_ALL_THREE,
    SCENARIO_SELL_INCOME_PROPERTY,
    _apply_scenario,
    _make_fire_config,
)


# ---------------------------------------------------------------------------
# Helper to build a fully-mocked engine
# ---------------------------------------------------------------------------

def _make_engine(
    config: FireConfig,
    breakdown: NetWorthBreakdown,
    accounts: list,
    cashflow_events: list,
    income_sources: list | None = None,
) -> FireProjectionsEngine:
    """Create a FireProjectionsEngine with all DB access mocked."""
    mock_db = AsyncMock()

    engine = FireProjectionsEngine(mock_db)

    # Mock the 4 async helper methods
    engine.get_effective_config = AsyncMock(return_value=config)
    engine._compute_net_worth_breakdown = AsyncMock(return_value=breakdown)
    engine._get_income_sources = AsyncMock(return_value=income_sources or [])
    engine._get_annual_spending = AsyncMock(return_value=config.target_annual_spending)

    # Mock the 2 direct db.execute() calls:
    # 1st call: select(Account) for RE equity partition
    # 2nd call: select(CashflowEvent) for cashflow injection
    account_result = MagicMock()
    account_result.scalars.return_value.all.return_value = accounts

    cf_result = MagicMock()
    cf_result.scalars.return_value.all.return_value = cashflow_events

    mock_db.execute = AsyncMock(side_effect=[account_result, cf_result])

    return engine


def _account(role: str, dollars: float, *, is_asset: bool = True):
    account = MagicMock()
    account.fire_role = role
    account.current_balance = round(dollars * 100)
    account.is_asset = is_asset
    account.include_in_net_worth = True
    return account


class TestProjectionIncome:
    @pytest.mark.asyncio
    async def test_social_security_uses_main_amount_and_age_once(self, frozen_today):
        config = _make_fire_config(target_annual_spending=0)
        config.social_security_monthly = 100_000
        config.social_security_start_age = 53
        config.custom_assumptions = {
            "projection": {"ss_claim_age": 62, "ss_early_reduction": 0.10},
        }
        breakdown = NetWorthBreakdown(
            liquid=20_000, retirement=0, real_estate_equity=0,
            illiquid_private=0, other=0,
        )
        engine = _make_engine(config, breakdown, [], [])

        result = await engine.project_wealth_pools(end_age=54)

        assert any(point.age >= 53 and point.income == 1_000 for point in result.points)
        assert all(point.income != 100 for point in result.points)

    @pytest.mark.asyncio
    async def test_salary_flows_until_configured_retirement_date(self, frozen_today):
        config = _make_fire_config(target_annual_spending=12_000_000)
        config.target_retirement_age = 53
        config.healthcare_monthly_cost = None
        config.social_security_monthly = None
        config.custom_assumptions = {}
        salary = MagicMock()
        salary.name = "Household take-home pay"
        salary.income_type = IncomeType.SALARY
        salary.annual_amount = 12_000_000
        salary.start_date = None
        salary.end_date = None
        salary.growth_rate = None
        breakdown = NetWorthBreakdown(
            liquid=20_000, retirement=0, real_estate_equity=0,
            illiquid_private=0, other=0,
        )
        engine = _make_engine(config, breakdown, [], [], [salary])

        result = await engine.project_wealth_pools(end_age=54, bridge_months=12)

        assert result.points[0].income == 10_000
        assert any(point.age >= 53 and point.income == 0 for point in result.points)

    @pytest.mark.asyncio
    async def test_primary_mortgage_amortizes_and_payment_ends(self, frozen_today):
        config = _make_fire_config(target_annual_spending=12_000_000)
        config.healthcare_monthly_cost = None
        config.social_security_monthly = None
        config.custom_assumptions = {
            "projection": {
                "re_appreciation_rate": 0.0,
                "primary_property_mortgage_pi": 5_689.74,
                "primary_property_mortgage_rate": 0.025,
                "primary_property_mortgage_payoff_date": "2051-03-01",
                "spending_phase_slow_age": 100,
                "spending_phase_floor_age": 110,
            },
        }
        accounts = [
            _account("primary_residence", 2_000_000),
            _account("primary_mortgage", 1_249_905.13, is_asset=False),
        ]
        breakdown = NetWorthBreakdown(
            liquid=10_000_000, retirement=0, real_estate_equity=750_094.87,
            illiquid_private=0, other=0,
        )
        engine = _make_engine(config, breakdown, accounts, [])

        result = await engine.project_wealth_pools(end_age=79, bridge_months=400)
        before = next(p for p in result.points if p.date.startswith("2051-02"))
        after = next(p for p in result.points if p.date.startswith("2051-03"))

        assert before.expenses == 10_000
        assert after.expenses == pytest.approx(4_310, abs=1)
        assert after.real_estate == pytest.approx(2_000_000, abs=1)
        assert any(e["label"] == "Home mortgage paid off" for e in result.events)


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------

# Tolerance: $100K — accounts for minor shifts from date math precision
TOLERANCE = 100_000


class TestKeepBothOptimized:
    @pytest.fixture
    def engine(self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources):
        config = _make_fire_config()
        _apply_scenario(config, SCENARIO_KEEP_BOTH)
        return _make_engine(config, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)

    @pytest.mark.asyncio
    async def test_final_wealth(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        assert abs(result.total_at_end - 2_265_000) < TOLERANCE, (
            f"Keep Both total_at_end={result.total_at_end:.0f}, expected ~2,265,000"
        )

    @pytest.mark.asyncio
    async def test_cash_crisis_in_bridge(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        assert result.cash_zero_month is not None and result.cash_zero_month < 72, (
            f"Keep Both should hit cash zero in bridge after the late-windfall downgrade; got {result.cash_zero_month}"
        )

    @pytest.mark.asyncio
    async def test_primary_sale_event(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        # The legacy miami_sale path emits its fixed legacy marker label
        sale_events = [e for e in result.events if "Sell Miami" in e.get("label", "")]
        assert len(sale_events) > 0, "Should have a primary-property sale event"

    @pytest.mark.asyncio
    async def test_sepp_starts(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        sepp_events = [e for e in result.events if "SEPP" in e.get("label", "")]
        assert len(sepp_events) > 0, "Should have SEPP start event"

    @pytest.mark.asyncio
    async def test_sepp_does_not_deplete(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        assert result.sepp_depletes_age is None or result.sepp_depletes_age > 82


class TestPrimaryExit5Year:
    @pytest.fixture
    def engine(self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources):
        config = _make_fire_config()
        _apply_scenario(config, SCENARIO_PRIMARY_5YR)
        return _make_engine(config, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)

    @pytest.mark.asyncio
    async def test_final_wealth(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        assert abs(result.total_at_end - 2_240_000) < TOLERANCE, (
            f"Primary 5yr total_at_end={result.total_at_end:.0f}, expected ~2,240,000"
        )

    @pytest.mark.asyncio
    async def test_cash_crisis_in_bridge(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        assert result.cash_zero_month is not None and result.cash_zero_month < 72, (
            f"Primary 5yr should hit cash zero in bridge after the late-windfall downgrade; got {result.cash_zero_month}"
        )

    @pytest.mark.asyncio
    async def test_primary_sells_at_month_60(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        for pt in result.points:
            # The legacy miami_sale path emits its fixed legacy marker label
            if pt.month == 60 and pt.event and "Sell Miami" in pt.event:
                return
        pytest.fail("Primary property should sell at month 60")


class TestSellIncomeProperty:
    @pytest.fixture
    def engine(self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources):
        config = _make_fire_config()
        _apply_scenario(config, SCENARIO_SELL_INCOME_PROPERTY)
        return _make_engine(config, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)

    @pytest.mark.asyncio
    async def test_final_wealth(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        assert abs(result.total_at_end - 2_270_000) < TOLERANCE, (
            f"Sell income property total_at_end={result.total_at_end:.0f}, expected ~2,270,000"
        )

    @pytest.mark.asyncio
    async def test_no_cash_crisis(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        assert result.cash_zero_month is None, "Selling the income property should leave no cash crisis"

    @pytest.mark.asyncio
    async def test_income_property_sale_event(self, engine, frozen_today):
        result = await engine.project_wealth_pools(end_age=82)
        # The legacy sauvie_sale path emits its fixed legacy marker label
        sale_events = [e for e in result.events if "Sauvie" in e.get("label", "")]
        assert len(sale_events) > 0, "Should have an income-property sale event"


# ---------------------------------------------------------------------------
# Rate sensitivity — RE appreciation affects Miami sale proceeds
# ---------------------------------------------------------------------------

class TestRateSensitivity:
    """Verify that changing re_appreciation_rate actually moves the numbers."""

    @pytest.mark.asyncio
    async def test_re_appreciation_affects_proceeds(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        # 0% appreciation
        config_0 = _make_fire_config()
        _apply_scenario(config_0, SCENARIO_KEEP_BOTH)
        config_0.custom_assumptions["projection"]["re_appreciation_rate"] = 0.0
        engine_0 = _make_engine(config_0, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)
        result_0 = await engine_0.project_wealth_pools(end_age=82)

        # 2% appreciation
        config_2 = _make_fire_config()
        _apply_scenario(config_2, SCENARIO_KEEP_BOTH)
        config_2.custom_assumptions["projection"]["re_appreciation_rate"] = 0.02
        engine_2 = _make_engine(config_2, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)
        result_2 = await engine_2.project_wealth_pools(end_age=82)

        # 2% should produce more wealth than 0%
        assert result_2.total_at_end > result_0.total_at_end, (
            f"2% RE ({result_2.total_at_end:.0f}) should exceed 0% RE ({result_0.total_at_end:.0f})"
        )
        # Difference should be meaningful (>$50K)
        diff = result_2.total_at_end - result_0.total_at_end
        assert diff > 50_000, f"RE rate difference should be >$50K, got ${diff:,.0f}"


class TestSpendingOverride:
    """Verify that spending_override_cents parameter works correctly."""

    @pytest.mark.asyncio
    async def test_lower_spending_higher_wealth(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        config = _make_fire_config()
        _apply_scenario(config, SCENARIO_KEEP_BOTH)

        # $11K/mo spending
        engine_low = _make_engine(config, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)
        result_low = await engine_low.project_wealth_pools(
            end_age=82, spending_override_cents=11_000 * 12 * 100,
        )

        # $15K/mo spending
        engine_high = _make_engine(config, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)
        result_high = await engine_high.project_wealth_pools(
            end_age=82, spending_override_cents=15_000 * 12 * 100,
        )

        assert result_low.total_at_end > result_high.total_at_end, (
            f"$11K/mo ({result_low.total_at_end:.0f}) should exceed $15K/mo ({result_high.total_at_end:.0f})"
        )
        # Difference should be massive (~$1M+ for $4K/mo over 29 years)
        diff = result_low.total_at_end - result_high.total_at_end
        assert diff > 500_000, f"$4K/mo spending difference should be >$500K, got ${diff:,.0f}"

    @pytest.mark.asyncio
    async def test_override_does_not_change_default(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """Running with override should not affect a subsequent default run."""
        config = _make_fire_config()
        _apply_scenario(config, SCENARIO_KEEP_BOTH)

        # Run with override
        engine_1 = _make_engine(config, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)
        await engine_1.project_wealth_pools(end_age=82, spending_override_cents=20_000 * 12 * 100)

        # Run without override — should use config's target_annual_spending
        engine_2 = _make_engine(config, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)
        result_default = await engine_2.project_wealth_pools(end_age=82)

        assert abs(result_default.total_at_end - 2_265_000) < TOLERANCE, (
            f"Default run after override: {result_default.total_at_end:.0f}, expected ~2,265,000"
        )


# ---------------------------------------------------------------------------
# Generic property_sales mechanism + taxable brokerage pool
# ---------------------------------------------------------------------------

class TestGradualPropertyExit:
    """The 'Gradual Property Exit' scenario: sell all 3 properties on a timeline,
    route net proceeds into a taxable brokerage pool, and test whether 72(t) is still
    needed. Also exercises the generic-sale refactor mechanics + back-compat."""

    def _engine(self, overrides, nwb, accts, cfs, inc):
        config = _make_fire_config()
        _apply_scenario(config, deepcopy(overrides))
        return _make_engine(config, nwb, accts, cfs, inc)

    @pytest.mark.asyncio
    async def test_generic_cash_sale_matches_legacy_primary(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """A generic primary-property sale routed to CASH must reproduce the legacy
        miami_sale path to the dollar — proves the refactor is behavior-preserving."""
        common = {
            "sepp": {"ira_a_balance": 402_000, "ira_b_balance": 165_000, "sepp_monthly": 2_100,
                     "sepp_start_month": 12, "ira_growth_rate": 0.06},
            "rrsp": {"monthly_net": 1_900, "start_month": 12, "total_available": 205_000},
            "rental_occupancy_rate": 0.70,
        }
        legacy = {**common, "miami_sale": {
            "sale_month": 13, "mortgage_balance_at_sale": 270_000, "monthly_cost": 6_150,
            "post_sale_rent": 2_500, "rental_income_lost": 340}}
        generic = {**common, "property_sales": [{
            "key": "coastal_condo", "re_bucket": "primary", "sale_month": 13, "value": 510_000,
            "cost_basis": 510_000, "agent_fee_pct": 0.06, "ltcg_rate": 0.15, "state_tax_rate": 0.0,
            "mortgage_balance_at_sale": 270_000, "monthly_cost": 6_150, "in_base_burn": True,
            "post_sale_rent": 2_500, "monthly_income": 340, "proceeds_to": "cash"}]}

        r_legacy = await self._engine(legacy, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)
        r_generic = await self._engine(generic, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)

        assert abs(r_generic.total_at_end - r_legacy.total_at_end) < 1_000, (
            f"generic cash-routed primary sale ({r_generic.total_at_end:.0f}) should match legacy "
            f"({r_legacy.total_at_end:.0f})")
        assert r_generic.cash_zero_month == r_legacy.cash_zero_month

    @pytest.mark.asyncio
    async def test_section_121_exclusion_reduces_tax(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """§121 exclusion shelters the gain → higher net proceeds → more final wealth."""
        base = {
            "sepp": {"ira_a_balance": 402_000, "ira_b_balance": 165_000, "sepp_monthly": 2_100,
                     "sepp_start_month": 12, "ira_growth_rate": 0.06},
            "rrsp": {"monthly_net": 1_900, "start_month": 12, "total_available": 205_000},
            "taxable_pool": {"starting_balance": 0, "return_rate": 0.065},
        }
        river = {"key": "river_house", "re_bucket": "income", "sale_month": 13, "value": 396_000,
                 "cost_basis": 220_000, "agent_fee_pct": 0.06, "ltcg_rate": 0.15, "state_tax_rate": 0.06,
                 "current_mortgage_balance": 0, "monthly_cost": 1_050, "in_base_burn": True,
                 "monthly_income": 3_300, "proceeds_to": "taxable"}
        excluded = {**base, "property_sales": [{**river, "section_121_exclusion": 250_000}]}
        taxed = {**base, "property_sales": [{**river, "section_121_exclusion": 0}]}

        r_excl = await self._engine(excluded, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)
        r_tax = await self._engine(taxed, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)

        assert r_excl.total_at_end > r_tax.total_at_end
        diff = r_excl.total_at_end - r_tax.total_at_end
        assert diff > 30_000, f"§121 on a ~$150K gain should save >$30K, got ${diff:,.0f}"

    @pytest.mark.asyncio
    async def test_proceeds_routing_taxable_vs_cash(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """proceeds_to='taxable' parks proceeds in the brokerage pool; 'cash' leaves it empty."""
        base = {
            "sepp": {"ira_a_balance": 402_000, "ira_b_balance": 165_000, "sepp_monthly": 2_100,
                     "sepp_start_month": 12, "ira_growth_rate": 0.06},
            "rrsp": {"monthly_net": 1_900, "start_month": 12, "total_available": 205_000},
            "taxable_pool": {"starting_balance": 0, "return_rate": 0.065},
        }
        condo = {"key": "coastal_condo", "re_bucket": "primary", "sale_month": 13, "value": 510_000,
                 "cost_basis": 510_000, "agent_fee_pct": 0.06, "ltcg_rate": 0.15, "state_tax_rate": 0.0,
                 "mortgage_balance_at_sale": 270_000, "monthly_cost": 6_150, "in_base_burn": True,
                 "post_sale_rent": 2_500, "monthly_income": 340}
        to_tax = {**base, "property_sales": [{**condo, "proceeds_to": "taxable"}]}
        to_cash = {**base, "property_sales": [{**condo, "proceeds_to": "cash"}]}

        r_tax = await self._engine(to_tax, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82, bridge_months=60)
        r_cash = await self._engine(to_cash, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82, bridge_months=60)

        assert max(p.taxable for p in r_tax.points) > 100_000, "taxable route should park proceeds in the pool"
        # Scoped to pre-RMD ages: this invariant is about sale PROCEEDS. From
        # rmd_start_age forced RMD excess legitimately lands in the taxable pool
        # from an unrelated source (fire-master#14).
        rmd_age = 73  # persona config.rmd_start_age
        assert max(p.taxable for p in r_cash.points if p.age < rmd_age) == 0, (
            "cash route must never populate the taxable pool (before RMDs)")

    @pytest.mark.asyncio
    async def test_taxable_growth_rate_matters(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """A higher taxable real return compounds to more final wealth."""
        slow = deepcopy(SCENARIO_SELL_ALL_THREE)
        slow["taxable_pool"] = {"starting_balance": 0, "return_rate": 0.0}
        fast = deepcopy(SCENARIO_SELL_ALL_THREE)
        fast["taxable_pool"] = {"starting_balance": 0, "return_rate": 0.065}

        r_slow = await self._engine(slow, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)
        r_fast = await self._engine(fast, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)

        assert r_fast.total_at_end > r_slow.total_at_end
        assert r_fast.total_at_end - r_slow.total_at_end > 50_000

    @pytest.mark.asyncio
    async def test_taxable_bridges_without_sepp(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """The 72(t) question: with sepp_monthly=0, IRA-A is never touched and the taxable
        brokerage is drawn to bridge cash gaps instead."""
        with_sepp = deepcopy(SCENARIO_SELL_ALL_THREE)
        no_sepp = deepcopy(SCENARIO_SELL_ALL_THREE)
        no_sepp["sepp"]["sepp_monthly"] = 0

        r_on = await self._engine(with_sepp, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82, bridge_months=60)
        r_off = await self._engine(no_sepp, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82, bridge_months=60)

        assert r_off.sepp_depletes_age is None, "IRA-A must never deplete when SEPP is off"
        assert r_off.points[-1].ira_sepp > r_on.points[-1].ira_sepp, (
            "IRA-A should end larger with SEPP off (untouched, growing)")
        assert any((p.taxable_draw or 0) > 0 for p in r_off.points), (
            "taxable brokerage should be drawn to bridge gaps when SEPP is off")

    @pytest.mark.asyncio
    async def test_secondary_carrying_cost_bites(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """The secondary home is held until m25 with in_base_burn=False, so its $2,400/mo
        carrying cost must reduce wealth vs. a run that wrongly ignores it."""
        with_cost = deepcopy(SCENARIO_SELL_ALL_THREE)
        no_cost = deepcopy(SCENARIO_SELL_ALL_THREE)
        for s in no_cost["property_sales"]:
            if s["key"] == "mountain_house":
                s["monthly_cost"] = 0

        r_with = await self._engine(with_cost, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)
        r_without = await self._engine(no_cost, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)

        assert r_without.total_at_end > r_with.total_at_end
        assert r_without.total_at_end - r_with.total_at_end > 30_000, (
            "secondary held 25 months @ $2,400 should bite by tens of thousands")

    @pytest.mark.asyncio
    async def test_secondary_cashflow_event_suppressed(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """suppress_cashflow_match prevents the planned 'Mountain House Sale Proceeds' event
        from double-counting with the generic secondary-home sale."""
        suppressed = deepcopy(SCENARIO_SELL_ALL_THREE)
        not_suppressed = deepcopy(SCENARIO_SELL_ALL_THREE)
        for s in not_suppressed["property_sales"]:
            s.pop("suppress_cashflow_match", None)

        r_sup = await self._engine(suppressed, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)
        r_unsup = await self._engine(not_suppressed, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)

        assert r_unsup.total_at_end > r_sup.total_at_end, "double-count inflates the un-suppressed run"
        assert r_unsup.total_at_end - r_sup.total_at_end > 50_000

    @pytest.mark.asyncio
    async def test_property_sales_supersedes_legacy_keys(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """When property_sales is present, legacy miami_sale/sauvie_sale keys are ignored
        (no double-sell) — proves the precedence rule."""
        generic_only = deepcopy(SCENARIO_SELL_ALL_THREE)
        with_legacy = deepcopy(SCENARIO_SELL_ALL_THREE)
        with_legacy["miami_sale"] = {"sale_month": 50, "mortgage_balance_at_sale": 100_000,
                                      "monthly_cost": 6_150, "post_sale_rent": 2_500, "rental_income_lost": 340}
        with_legacy["sauvie_sale"] = {"enabled": True, "sale_month": 5, "net_proceeds": 999_999}

        r_a = await self._engine(generic_only, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)
        r_b = await self._engine(with_legacy, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82)

        assert r_a.total_at_end == r_b.total_at_end, (
            "legacy sale keys must be ignored when property_sales is present")

    @pytest.mark.asyncio
    async def test_suppressed_sale_event_leaves_no_phantom_marker(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """suppress_cashflow_match must silence the event's LABEL too, not just
        its money — a suppressed "… Sale Proceeds" event used to still render
        a chart marker at its event month, implying income that never lands,
        right next to the generic sale's own marker (spotted as apparent
        double-counting on the bridge chart)."""
        r = await self._engine(
            SCENARIO_SELL_ALL_THREE, net_worth_breakdown, mock_accounts,
            mock_cashflow_events, mock_income_sources,
        ).project_wealth_pools(end_age=82, bridge_months=60)
        labels = "; ".join(p.event for p in r.points if p.event)
        assert "Mountain House Sale Proceeds" not in labels, (
            "phantom marker rendered for a sale-suppressed event")
        assert "Sell Mountain House" in labels, (
            "the generic sale's own marker must still render")

        # Control: without generic sales, the event is real and keeps its label
        r_ctl = await self._engine(
            SCENARIO_KEEP_BOTH, net_worth_breakdown, mock_accounts,
            mock_cashflow_events, mock_income_sources,
        ).project_wealth_pools(end_age=82, bridge_months=60)
        ctl_labels = "; ".join(p.event for p in r_ctl.points if p.event)
        assert "Mountain House Sale Proceeds" in ctl_labels

    @pytest.mark.asyncio
    async def test_cash_repairs_to_zero_when_taxable_funded(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """Negative cash is repaired back to zero from the taxable pool once it
        exists — cash may only go negative while NO drawable pool is funded
        (pre-rescue stress stays visible; the 44-month frozen negative scar the
        owner spotted does not). High burn + an early sale forces the path."""
        overrides = deepcopy(SCENARIO_SELL_ALL_THREE)
        overrides["property_sales"][0]["sale_month"] = 3  # fund taxable early

        config = _make_fire_config(target_annual_spending=30_000_000)  # $300K/yr burn
        _apply_scenario(config, overrides)
        engine = _make_engine(
            config, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources)
        r = await engine.project_wealth_pools(end_age=82, bridge_months=60)

        # Invariant: never negative cash while the taxable pool has money
        violations = [p for p in r.points if p.cash < 0 and p.taxable > 1]
        assert not violations, f"frozen negative cash beside a funded pool: {violations[:3]}"
        # The repair actually fired: months resting exactly on the floor while
        # taxable draws cover the burn
        floor_months = [p for p in r.points if p.cash == 0 and (p.taxable_draw or 0) > 0]
        assert len(floor_months) >= 3, "expected cash pinned at zero by repair draws"


# ---------------------------------------------------------------------------
# Drawdown waterfall: taxable gate (#12), Roth pool (#13), RMDs (#14)
# ---------------------------------------------------------------------------

class TestWaterfallPools:
    """fire-master#12/#13/#14 — the waterfall is one path with no property-sale
    gate, a tax-free pool is drawn last, and RMDs are forced from rmd_start_age."""

    # No property sales; a configured brokerage must still be spendable.
    BROKERAGE_ONLY = {
        "sepp": {"ira_a_balance": 402_000, "ira_b_balance": 165_000, "sepp_monthly": 2_100,
                 "sepp_start_month": 12, "ira_growth_rate": 0.06},
        "rrsp": {"monthly_net": 1_900, "start_month": 12, "total_available": 205_000},
        "taxable_pool": {"starting_balance": 600_000, "return_rate": 0.06},
        "projection": {"enforce_rmd": False},
    }

    def _engine(self, overrides, nwb, accts, cfs, inc, **cfg_overrides):
        config = _make_fire_config(**cfg_overrides)
        _apply_scenario(config, deepcopy(overrides))
        return _make_engine(config, nwb, accts, cfs, inc)

    # --- #12 -------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_taxable_pool_is_drawn_without_property_sales(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """A configured taxable_pool with NO property_sales used to compound untouched
        while cash ran arbitrarily negative (use_generic_sales gated the waterfall)."""
        r = await self._engine(
            self.BROKERAGE_ONLY, net_worth_breakdown, mock_accounts, mock_cashflow_events,
            mock_income_sources, target_annual_spending=30_000_000,  # $300K/yr burn
        ).project_wealth_pools(end_age=82, bridge_months=60)

        assert any((p.taxable_draw or 0) > 0 for p in r.points), "brokerage never drawn"
        stranded = [p for p in r.points if p.cash < 0 and p.taxable > 1]
        assert not stranded, f"negative cash beside a funded brokerage: {stranded[:3]}"
        assert r.points[-1].taxable < 600_000, "pool should be spent down, not compound forever"

    @pytest.mark.asyncio
    async def test_no_taxable_pool_still_matches_legacy_path(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """With an empty pool the unified waterfall reduces to the legacy IRA-B-only
        formula: taxable stays 0 and never draws (behavior-preserving)."""
        overrides = deepcopy(self.BROKERAGE_ONLY)
        del overrides["taxable_pool"]
        r = await self._engine(
            overrides, net_worth_breakdown, mock_accounts, mock_cashflow_events,
            mock_income_sources, target_annual_spending=30_000_000,
        ).project_wealth_pools(end_age=82, bridge_months=60)
        assert all(p.taxable == 0 and (p.taxable_draw or 0) == 0 for p in r.points)
        assert any(p.age >= 59.5 and p.ira_draw > 2_100 for p in r.points), "IRA-B still drawn after 59½"

    # --- #13 -------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_roth_pool_is_projected_and_drawn(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        overrides = deepcopy(self.BROKERAGE_ONLY)
        overrides["taxable_pool"] = {"starting_balance": 100_000, "return_rate": 0.06}
        overrides["roth_pool"] = {"starting_balance": 400_000, "return_rate": 0.06}
        r = await self._engine(
            overrides, net_worth_breakdown, mock_accounts, mock_cashflow_events,
            mock_income_sources, target_annual_spending=30_000_000,
        ).project_wealth_pools(end_age=82, bridge_months=60)

        assert r.points[0].roth > 400_000, "Roth should compound at its real rate"
        assert any((p.roth_draw or 0) > 0 for p in r.points), "Roth never drawn"
        assert r.points[-1].roth < r.points[0].roth
        # Pools feed the total
        p0 = r.points[0]
        assert abs(p0.total - (max(0, p0.cash) + p0.ira_sepp + p0.ira_growth + p0.rrsp
                               + p0.real_estate + p0.illiquid + p0.taxable + p0.roth)) < 2

    @pytest.mark.asyncio
    async def test_roth_is_drawn_strictly_last(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """A tax-free dollar is the most valuable dollar: Roth is touched only once
        taxable is exhausted, and (after 59½) only once IRA-B is exhausted too."""
        overrides = deepcopy(self.BROKERAGE_ONLY)
        overrides["taxable_pool"] = {"starting_balance": 150_000, "return_rate": 0.06}
        overrides["roth_pool"] = {"starting_balance": 400_000, "return_rate": 0.06}
        r = await self._engine(
            overrides, net_worth_breakdown, mock_accounts, mock_cashflow_events,
            mock_income_sources, target_annual_spending=30_000_000,
        ).project_wealth_pools(end_age=82, bridge_months=60)

        drawn = [p for p in r.points if (p.roth_draw or 0) > 0]
        assert drawn, "expected Roth draws under a $300K burn"
        assert all(p.taxable == 0 for p in drawn), "Roth drawn while taxable still funded"
        assert all(p.ira_growth == 0 for p in drawn if p.age >= 59.5), "Roth drawn while IRA-B still funded"

    @pytest.mark.asyncio
    async def test_absent_roth_pool_is_behavior_preserving(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """No roth_pool key (or an empty one) reproduces the existing projection
        to the dollar; the new point fields sit at zero."""
        base = deepcopy(SCENARIO_SELL_ALL_THREE)
        empty = {**deepcopy(SCENARIO_SELL_ALL_THREE), "roth_pool": {}}
        r_a = await self._engine(base, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82, bridge_months=60)
        r_b = await self._engine(empty, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=82, bridge_months=60)
        assert r_a.total_at_end == r_b.total_at_end
        assert [p.model_dump() for p in r_a.points] == [p.model_dump() for p in r_b.points]
        assert all(p.roth == 0 and (p.roth_draw or 0) == 0 for p in r_a.points)

    # --- #14 -------------------------------------------------------------

    RMD_HEAVY = {
        # Spending comfortably covered elsewhere; a large IRA-B nobody needs to touch.
        "sepp": {"ira_a_balance": 0, "ira_b_balance": 3_000_000, "sepp_monthly": 0,
                 "sepp_start_month": 0, "ira_growth_rate": 0.06},
        "rrsp": {"monthly_net": 0, "start_month": 0, "total_available": 0},
        "taxable_pool": {"starting_balance": 1_000_000, "return_rate": 0.06},
    }

    @pytest.mark.asyncio
    async def test_rmd_forces_minimum_ira_draw(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """From rmd_start_age every recorded month's IRA draw covers at least
        balance / Uniform Lifetime divisor / 12 — even when the spending gap is zero."""
        from app.engines.tax_engine import _get_rmd_divisor
        r = await self._engine(
            self.RMD_HEAVY, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources,
        ).project_wealth_pools(end_age=90)

        post = [p for p in r.points if p.age >= 73 and (p.ira_growth + p.ira_sepp) > 0]
        assert len(post) >= 10, "persona should carry IRA money well past 73"
        for p in post:
            required = (p.ira_growth + p.ira_sepp) / _get_rmd_divisor(int(p.age))
            assert p.ira_draw * 12 >= required * 0.98, (
                f"age {p.age}: drew {p.ira_draw * 12:,.0f}/yr against required {required:,.0f}")
            assert (p.rmd_redeposit or 0) > 0, f"age {p.age}: forced excess should be redeposited"
        pre = [p for p in r.points if p.age < 73]
        assert all((p.rmd_redeposit or 0) == 0 for p in pre)
        assert any("RMDs at 73" in e["label"] for e in r.events)

    @pytest.mark.asyncio
    async def test_rmd_opt_out_reproduces_under_draw(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """projection.enforce_rmd=false restores the old behavior: $0 drawn against a
        multi-million IRA-B past 73 (the symptom in the issue)."""
        overrides = {**deepcopy(self.RMD_HEAVY), "projection": {"enforce_rmd": False}}
        r = await self._engine(
            overrides, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources,
        ).project_wealth_pools(end_age=90)
        post = [p for p in r.points if p.age >= 73]
        assert any(p.ira_draw == 0 and p.ira_growth > 1_000_000 for p in post)
        assert all((p.rmd_redeposit or 0) == 0 for p in r.points)
        assert not any("RMD" in e["label"] for e in r.events)

    @pytest.mark.asyncio
    async def test_rmd_redeposit_is_not_double_counted(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """The forced excess moves IRA → taxable; it is not spending and not cash.
        With both pools at the same real rate total wealth is (nearly) unchanged —
        a double count would add every redeposit to cash AND taxable."""
        on = deepcopy(self.RMD_HEAVY)
        off = {**deepcopy(self.RMD_HEAVY), "projection": {"enforce_rmd": False}}
        r_on = await self._engine(on, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=90)
        r_off = await self._engine(off, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources).project_wealth_pools(end_age=90)

        total_redeposited = sum((p.rmd_redeposit or 0) * 12 for p in r_on.points)
        assert total_redeposited > 500_000, "test needs material forced RMDs to be meaningful"
        assert abs(r_on.total_at_end - r_off.total_at_end) < 0.01 * r_off.total_at_end, (
            f"RMD on={r_on.total_at_end:,.0f} vs off={r_off.total_at_end:,.0f}")
        # Cash never receives the redeposit: net cash flow is income + draws to cash - expenses
        for p in r_on.points:
            assert (p.rmd_redeposit or 0) <= p.ira_draw

    @pytest.mark.asyncio
    async def test_rmd_redeposit_is_drawable_without_taxable_pool_config(
        self, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources, frozen_today,
    ):
        """A config that never declared taxable_pool still gets a reachable pool once
        RMDs fund it (the gate is 'pool has money', not 'pool was configured')."""
        overrides = deepcopy(self.RMD_HEAVY)
        del overrides["taxable_pool"]
        r = await self._engine(
            overrides, net_worth_breakdown, mock_accounts, mock_cashflow_events,
            mock_income_sources, target_annual_spending=24_000_000,  # $240K/yr
        ).project_wealth_pools(end_age=90)
        post = [p for p in r.points if p.age >= 74]
        assert any(p.taxable > 0 for p in post), "redeposits should populate the pool"
        assert any((p.taxable_draw or 0) > 0 for p in post), "the redeposit-funded pool must be drawable"
        assert not [p for p in post if p.cash < 0 and p.taxable > 1]
