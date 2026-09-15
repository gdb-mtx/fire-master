"""Monte Carlo engine tests — real-terms frame, correlated draws, determinism.

The headline regression here is the inflation double-count fixed 2026-07-11:
the old engine compounded the portfolio at a REAL rate while inflating
spending/income NOMINALLY, so results depended on the nominal/inflation
split even when the real return was identical.
test_real_frame_invariant_to_nominal_split pins the fix: two configs with
the same real return but different nominal splits must produce identical
results (the old code diverged wildly).
"""

import math
import random
from contextlib import ExitStack, contextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engines.fire_projections import FireProjectionsEngine, _spending_multiplier
from app.data.historical_market_returns import HISTORICAL_MARKET_RETURNS
from app.engines.monte_carlo import (
    MonteCarloEngine,
    _draw_year,
    _historical_real_return,
    _sample_historical_path,
)
from app.engines.net_worth import NetWorthEngine
from app.engines.tax_engine import AccountsByTaxTreatment, TaxEngine

from .conftest import FROZEN_TODAY, _make_fire_config


@pytest.fixture
def frozen_today_mc():
    """Patch date.today() in the Monte Carlo engine to 2026-04-14."""
    with patch("app.engines.monte_carlo.date") as mock_date:
        mock_date.today.return_value = FROZEN_TODAY
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        yield mock_date


@contextmanager
def _mc_env(
    config, *, net_worth=1_500_000.0, spending_cents=15_300_000,
    income_sources=(), events=(), accounts=None, tax_funding_by_year=None,
):
    """Patch every DB touchpoint the MC engine reaches through its sub-engines."""
    if accounts is None:
        taxable_account = MagicMock(
            current_balance=round(net_worth * 100),
            name="Taxable",
            extra_data={},
            custom_data={},
        )
        accounts = AccountsByTaxTreatment(taxable=[taxable_account])
    with ExitStack() as stack:
        stack.enter_context(patch.object(
            FireProjectionsEngine, "_get_cashflow_events",
            AsyncMock(return_value=list(events))))
        stack.enter_context(patch.object(
            FireProjectionsEngine, "get_effective_config",
            AsyncMock(return_value=config)))
        stack.enter_context(patch.object(
            FireProjectionsEngine, "_get_annual_spending",
            AsyncMock(return_value=spending_cents)))
        stack.enter_context(patch.object(
            FireProjectionsEngine, "_get_income_sources",
            AsyncMock(return_value=list(income_sources))))
        stack.enter_context(patch.object(
            NetWorthEngine, "calculate_current",
            AsyncMock(return_value=MagicMock(net_worth=net_worth))))
        stack.enter_context(patch.object(
            TaxEngine, "get_accounts_by_tax_treatment",
            AsyncMock(return_value=accounts)))
        if tax_funding_by_year is not None:
            stack.enter_context(patch.object(
                FireProjectionsEngine, "_get_tax_funding_by_year",
                AsyncMock(return_value=tax_funding_by_year)))
        yield


# Persona horizon facts (DOB 1973-07-01, life expectancy 90, frozen 2026-04-14)
TOTAL_YEARS = 37  # 2063 - 2026
SS_START_YEAR = 14  # (1973 + 67) - 2026


class TestDeterminism:
    async def test_fixed_seed_reproducible(self, base_fire_config, frozen_today_mc):
        engine = MonteCarloEngine(db=None)
        with _mc_env(base_fire_config):
            r1 = await engine.run_simulation(n_runs=300, seed=42)
            r2 = await engine.run_simulation(n_runs=300, seed=42)
            r3 = await engine.run_simulation(n_runs=300, seed=7)
        assert r1.success_rate == r2.success_rate
        assert r1.percentile_50 == r2.percentile_50
        assert r1.percentile_curves[-1] == r2.percentile_curves[-1]
        assert r1.assumptions["seed"] == 42
        # A different seed actually changes the draw
        assert r3.percentile_50 != r1.percentile_50

    async def test_retirement_age_override_reaches_tax_schedule(
        self, base_fire_config, frozen_today_mc,
    ):
        engine = MonteCarloEngine(db=None)
        seen_ages = []

        async def capture_tax_config(config, years, scenario_id=None):
            seen_ages.append(config.target_retirement_age)
            return {}

        with _mc_env(base_fire_config), patch.object(
            FireProjectionsEngine,
            "_get_tax_funding_by_year",
            side_effect=capture_tax_config,
        ):
            await engine.run_simulation(
                n_runs=10, seed=1, retirement_age_override=60,
            )

        assert seen_ages == [60]

    async def test_retirement_age_analysis_finds_confidence_boundaries(
        self, base_fire_config, frozen_today_mc,
    ):
        engine = MonteCarloEngine(db=None)

        async def result_for_age(*, retirement_age_override, **_kwargs):
            return MagicMock(success_rate=min(100.0, (retirement_age_override - 50) * 5.0))

        with patch.object(
            FireProjectionsEngine,
            "get_effective_config",
            AsyncMock(return_value=base_fire_config),
        ), patch.object(engine, "run_simulation", side_effect=result_for_age):
            result = await engine.analyze_retirement_ages(
                targets=(80, 90, 95), n_runs=100, seed=1, max_age=75,
            )

        assert [point.earliest_age for point in result.confidence_ages] == [66, 68, 69]
        assert [point.success_rate for point in result.confidence_ages] == [80, 90, 95]
        assert result.runs_per_age == 100

    @staticmethod
    def _hand_loop(annual_spending: float, start_nw: float, dob: date) -> float:
        """Independent replica of the zero-vol path: compound at the real
        rate with flat-real flows; stop at the first breach like the engine."""
        r_real = 1.07 / 1.03 - 1
        start_age = (FROZEN_TODAY - dob).days / 365.25
        nw = start_nw
        for yr in range(TOTAL_YEARS):
            # Retired from year 0 (retirement date is mid-2026, same year)
            age = start_age + yr
            yr_spending = annual_spending * _spending_multiplier(age)
            # Fixture carries $600/mo of extra pre-Medicare healthcare.
            if age < 65:
                yr_spending += 7_200
            yr_income = 55_800.0 if yr >= SS_START_YEAR else 0.0  # SS flat real
            nw = nw * (1 + r_real) + yr_income - yr_spending
            if nw < 0:
                break  # engine stops (and pads) at depletion
        return nw

    async def test_zero_volatility_matches_hand_computed_loop(
        self, base_fire_config, frozen_today_mc,
    ):
        """With both sigmas at 0 every run collapses to plain compound growth
        at the real rate (1.07/1.03 − 1) with flat-real flows — replicated
        independently, both for a failing plan (persona spending, breaches
        mid-horizon) and a surviving one (reduced spending, full 37 years)."""
        config = base_fire_config
        config.custom_assumptions = {
            **config.custom_assumptions,
            "monte_carlo": {"return_std": 0, "inflation_std": 0},
        }
        engine = MonteCarloEngine(db=None)

        # Failing path: persona spending breaches — engine reports the breach value
        with _mc_env(config):
            failing = await engine.run_simulation(n_runs=50, seed=1)
        assert failing.worst_final_nw == failing.best_final_nw  # all runs identical
        assert failing.success_rate == 0.0
        expected_fail = self._hand_loop(153_000.0, 1_500_000.0, config.date_of_birth)
        assert failing.percentile_50 == pytest.approx(expected_fail, abs=1.0)

        # Surviving path: $60K spending compounds through the full horizon
        with _mc_env(config, spending_cents=6_000_000):
            surviving = await engine.run_simulation(n_runs=50, seed=1)
        assert surviving.success_rate == 100.0
        expected_ok = self._hand_loop(60_000.0, 1_500_000.0, config.date_of_birth)
        assert surviving.percentile_50 == pytest.approx(expected_ok, abs=1.0)

    async def test_real_frame_invariant_to_nominal_split(self, frozen_today_mc):
        """Same REAL return, different nominal/inflation splits → identical
        results. Fails on the pre-fix engine (nominal spending inflation
        compounded against a real portfolio rate)."""
        mc_block = {"monte_carlo": {"return_std": 0, "inflation_std": 0}}

        c1 = _make_fire_config()  # 7% nominal / 3% inflation
        c1.custom_assumptions = {**c1.custom_assumptions, **mc_block}

        # (1 + r2)/(1.06) == 1.07/1.03  →  same real return at 6% inflation
        r2_nominal_pct = ((1.07 / 1.03) * 1.06 - 1) * 100
        c2 = _make_fire_config(
            expected_annual_return=r2_nominal_pct,
            expected_inflation_rate=6.0,
        )
        c2.custom_assumptions = {**c2.custom_assumptions, **mc_block}

        engine = MonteCarloEngine(db=None)
        with _mc_env(c1):
            r1 = await engine.run_simulation(n_runs=10, seed=3)
        with _mc_env(c2):
            r2 = await engine.run_simulation(n_runs=10, seed=3)

        assert r1.percentile_50 == pytest.approx(r2.percentile_50, abs=1.0)
        assert r1.success_rate == r2.success_rate


class TestDrawModel:
    def test_historical_blocks_preserve_contiguous_joint_years(self):
        path = _sample_historical_path(random.Random(4), years=21, block_years=7)
        assert len(path) == 21
        historical_rows = set(HISTORICAL_MARKET_RETURNS)
        assert all(observation in historical_rows for observation in path)
        for start in range(0, len(path), 7):
            years = [observation[0] for observation in path[start:start + 7]]
            assert years == list(range(years[0], years[0] + len(years)))

    def test_historical_recenter_matches_configured_geometric_means(self):
        nominal_logs = []
        inflation_logs = []
        for observation in HISTORICAL_MARKET_RETURNS:
            real_return, inflation = _historical_real_return(
                observation, 0.80, 0.07, 0.03,
            )
            nominal_return = (1 + real_return) * (1 + inflation) - 1
            nominal_logs.append(math.log1p(nominal_return))
            inflation_logs.append(math.log1p(inflation))
        assert sum(nominal_logs) / len(nominal_logs) == pytest.approx(
            math.log1p(0.07), abs=1e-10,
        )
        assert sum(inflation_logs) / len(inflation_logs) == pytest.approx(
            math.log1p(0.03), abs=1e-10,
        )

    def test_correlation_near_target(self):
        """Recover the standard-normal drivers from 10K draws; their Pearson
        correlation must sit near rho = −0.25."""
        rng = random.Random(123)
        mu_nom, sigma, mu_i, sigma_i, rho = 0.07, 0.16, 0.03, 0.015, -0.25
        zr, zi = [], []
        for _ in range(10_000):
            r_real, infl = _draw_year(rng, mu_nom, sigma, mu_i, sigma_i, rho)
            r_nom = (1 + r_real) * (1 + infl) - 1
            zr.append((math.log(1 + r_nom) - math.log(1 + mu_nom)) / sigma)
            zi.append((infl - mu_i) / sigma_i)
        n = len(zr)
        mr, mi = sum(zr) / n, sum(zi) / n
        cov = sum((a - mr) * (b - mi) for a, b in zip(zr, zi)) / n
        sr = math.sqrt(sum((a - mr) ** 2 for a in zr) / n)
        si = math.sqrt(sum((b - mi) ** 2 for b in zi) / n)
        assert cov / (sr * si) == pytest.approx(rho, abs=0.05)

    def test_lognormal_mean_calibration(self):
        """The configured return is the arithmetic mean, not a rosier median."""
        rng = random.Random(9)
        nominal_draws = []
        for _ in range(100_000):
            real_return, inflation = _draw_year(rng, 0.07, 0.16, 0.03, 0.0, 0.0)
            nominal_draws.append((1 + real_return) * (1 + inflation) - 1)
        assert sum(nominal_draws) / len(nominal_draws) == pytest.approx(0.07, abs=0.002)

    def test_geometric_mode_calibrates_compounded_return(self):
        rng = random.Random(19)
        log_growth = []
        for _ in range(100_000):
            real_return, inflation = _draw_year(
                rng, 0.07, 0.13, 0.03, 0.0, 0.0,
                mean_type="geometric",
            )
            nominal_return = (1 + real_return) * (1 + inflation) - 1
            log_growth.append(math.log1p(nominal_return))
        assert sum(log_growth) / len(log_growth) == pytest.approx(
            math.log1p(0.07), abs=0.001,
        )


class TestDepletion:
    async def test_depletion_pads_and_fails(self, base_fire_config, frozen_today_mc):
        """A starving plan fails every run; curves still span the horizon."""
        engine = MonteCarloEngine(db=None)
        with _mc_env(base_fire_config, net_worth=100_000.0, spending_cents=30_000_000):
            result = await engine.run_simulation(n_runs=100, seed=5)
        assert result.success_rate == 0.0
        assert result.best_final_nw < 0
        assert len(result.percentile_curves) == TOTAL_YEARS + 1

    async def test_non_spendable_net_worth_does_not_fund_retirement(
        self, base_fire_config, frozen_today_mc,
    ):
        empty_accounts = AccountsByTaxTreatment()
        engine = MonteCarloEngine(db=None)
        with _mc_env(
            base_fire_config,
            net_worth=2_000_000,
            spending_cents=3_000_000,
            accounts=empty_accounts,
        ):
            result = await engine.run_simulation(n_runs=10, seed=5)

        assert result.starting_spendable_assets == 0
        assert result.excluded_non_spendable_assets == 2_000_000
        assert result.success_rate == 0

    async def test_locked_deferred_assets_cannot_cover_early_bridge(
        self, base_fire_config, frozen_today_mc,
    ):
        base_fire_config.custom_assumptions["sepp"] = {"sepp_monthly": 0}
        deferred = MagicMock(current_balance=100_000_000)
        accounts = AccountsByTaxTreatment(tax_deferred=[deferred])
        engine = MonteCarloEngine(db=None)
        with _mc_env(base_fire_config, net_worth=1_000_000, accounts=accounts):
            result = await engine.run_simulation(n_runs=10, seed=5)

        assert result.success_rate == 0
        assert result.percentile_curves[1].p50 < 0

    async def test_calendar_year_taxes_are_aligned_to_rolling_projection_year(
        self, frozen_today_mc,
    ):
        config = _make_fire_config(
            social_security_monthly=0,
            healthcare_monthly_cost=None,
            expected_annual_return=0,
            expected_inflation_rate=0,
            custom_assumptions={
                "monte_carlo": {"return_std": 0, "inflation_std": 0},
                "sepp": {"sepp_monthly": 0},
            },
        )
        engine = MonteCarloEngine(db=None)
        with _mc_env(
            config,
            net_worth=100_000,
            spending_cents=0,
            tax_funding_by_year={2026: 120_000, 2027: 0},
        ):
            result = await engine.run_simulation(n_runs=10, seed=5)

        # Apr-Dec is nine of the twelve months in calendar 2026. Jan-Mar 2027
        # carries no tax, so the first rolling projection year funds $90K.
        assert result.percentile_curves[1].p50 == pytest.approx(10_000, abs=1)

    async def test_sepp_only_unlocks_configured_payment(
        self, base_fire_config, frozen_today_mc,
    ):
        base_fire_config.custom_assumptions["sepp"] = {"sepp_monthly": 1_000}
        deferred = MagicMock(current_balance=100_000_000)
        accounts = AccountsByTaxTreatment(tax_deferred=[deferred])
        engine = MonteCarloEngine(db=None)
        with _mc_env(
            base_fire_config,
            net_worth=1_000_000,
            spending_cents=3_000_000,
            accounts=accounts,
        ):
            result = await engine.run_simulation(n_runs=10, seed=5)

        assert result.success_rate == 0
        assert result.percentile_curves[1].p50 == pytest.approx(-25_200, abs=1)

    async def test_roth_is_conservatively_locked_before_59_5(
        self, base_fire_config, frozen_today_mc,
    ):
        roth = MagicMock(current_balance=100_000_000)
        accounts = AccountsByTaxTreatment(tax_free=[roth])
        engine = MonteCarloEngine(db=None)
        with _mc_env(
            base_fire_config,
            net_worth=1_000_000,
            spending_cents=3_000_000,
            accounts=accounts,
        ):
            result = await engine.run_simulation(n_runs=10, seed=5)

        assert result.success_rate == 0
        assert result.percentile_curves[1].p50 < 0

    async def test_configured_roth_basis_is_accessible_before_59_5(
        self, base_fire_config, frozen_today_mc,
    ):
        base_fire_config.custom_assumptions["retirement_contributions"] = {
            "worker_count": 0,
            "starting_roth_contribution_basis": 100_000,
        }
        roth = MagicMock(current_balance=100_000_00)
        accounts = AccountsByTaxTreatment(tax_free=[roth])
        engine = MonteCarloEngine(db=None)
        with _mc_env(
            base_fire_config,
            net_worth=100_000,
            spending_cents=3_000_000,
            accounts=accounts,
            tax_funding_by_year={},
        ):
            result = await engine.run_simulation(n_runs=10, seed=5)

        assert result.percentile_curves[1].p50 > 0

    async def test_matured_roth_ladder_bridges_to_penalty_free_age(
        self, frozen_today_mc,
    ):
        config = _make_fire_config(
            life_expectancy=62,
            target_annual_spending=3_000_000,
            healthcare_monthly_cost=None,
            social_security_monthly=None,
            expected_annual_return=0,
            expected_inflation_rate=0,
        )
        config.custom_assumptions = {
            "monte_carlo": {"return_std": 0, "inflation_std": 0},
            "sepp": {"sepp_monthly": 0},
            "retirement_contributions": {"worker_count": 0},
            "roth_conversion_ladder": {
                "enabled": True,
                "wait_years": 5,
                "annual_conversion": 30_000,
            },
        }
        cash = MagicMock(current_balance=180_000_00)
        deferred = MagicMock(current_balance=1_000_000_00)
        accounts = AccountsByTaxTreatment(
            already_taxed=[cash], tax_deferred=[deferred],
        )
        engine = MonteCarloEngine(db=None)

        with _mc_env(
            config, net_worth=1_180_000, spending_cents=3_000_000,
            accounts=accounts, tax_funding_by_year={},
        ):
            with_ladder = await engine.run_simulation(n_runs=10, seed=5)
        config.custom_assumptions["roth_conversion_ladder"]["enabled"] = False
        with _mc_env(
            config, net_worth=1_180_000, spending_cents=3_000_000,
            accounts=accounts, tax_funding_by_year={},
        ):
            without_ladder = await engine.run_simulation(n_runs=10, seed=5)

        assert with_ladder.success_rate == 100
        assert without_ladder.success_rate == 0


def _make_source(income_type, annual_cents, *, start=None, end=None, growth=None):
    from unittest.mock import MagicMock

    from app.models.income_source import IncomeSource

    s = MagicMock(spec=IncomeSource)
    s.name = "test source"
    s.income_type = income_type
    s.annual_amount = annual_cents
    s.frequency = "monthly"
    s.start_date = start
    s.end_date = end
    s.is_active = True
    s.growth_rate = growth
    s.is_taxable = True
    return s


class TestIncomeTiming:
    """Regression tests for fire-master#5: run_simulation must reuse
    _income_at_month (date bounds, growth_rate, retirement cutoff by
    per-source end_date) and the scenario-aware effective config, instead
    of pre-summing annual_amount over all active sources."""

    ZERO_VOL = {"monte_carlo": {"return_std": 0, "inflation_std": 0}}

    def _config(self, base):
        base.custom_assumptions = {**base.custom_assumptions, **self.ZERO_VOL}
        return base

    async def test_uses_effective_config_and_forwards_scenario_id(
        self, base_fire_config, frozen_today_mc,
    ):
        """The engine resolves config through get_effective_config with the
        caller's scenario_id — the pre-fix engine read the base config and
        silently ignored scenarios."""
        import uuid

        engine = MonteCarloEngine(db=None)
        sid = uuid.uuid4()
        with ExitStack() as stack:
            eff = AsyncMock(return_value=base_fire_config)
            stack.enter_context(patch.object(
                FireProjectionsEngine, "get_effective_config", eff))
            stack.enter_context(patch.object(
                FireProjectionsEngine, "_get_annual_spending",
                AsyncMock(return_value=15_300_000)))
            stack.enter_context(patch.object(
                FireProjectionsEngine, "_get_income_sources",
                AsyncMock(return_value=[])))
            stack.enter_context(patch.object(
                FireProjectionsEngine, "_get_cashflow_events",
                AsyncMock(return_value=[])))
            stack.enter_context(patch.object(
                NetWorthEngine, "calculate_current",
                AsyncMock(return_value=MagicMock(net_worth=1_500_000.0))))
            taxable_account = MagicMock(
                current_balance=150_000_000,
                name="Taxable",
                extra_data={},
                custom_data={},
            )
            stack.enter_context(patch.object(
                TaxEngine, "get_accounts_by_tax_treatment",
                AsyncMock(return_value=AccountsByTaxTreatment(taxable=[taxable_account]))))
            await engine.run_simulation(n_runs=10, seed=1, scenario_id=sid)
        eff.assert_awaited_once_with(sid)

    async def test_ended_and_future_sources_contribute_nothing(
        self, base_fire_config, frozen_today_mc,
    ):
        """A salary that ended before today and a source starting after the
        horizon must be identical to having no sources at all. The pre-fix
        engine summed both as perpetually active."""
        from datetime import date as real_date

        from app.models.enums import IncomeType

        config = self._config(base_fire_config)
        engine = MonteCarloEngine(db=None)

        dead_sources = [
            _make_source(IncomeType.SALARY, 25_000_000, end=real_date(2026, 3, 24)),
            _make_source(IncomeType.RENTAL, 1_200_000, start=real_date(2090, 1, 1)),
        ]
        with _mc_env(config, income_sources=dead_sources):
            with_dead = await engine.run_simulation(n_runs=20, seed=2)
        with _mc_env(config, income_sources=[]):
            without = await engine.run_simulation(n_runs=20, seed=2)

        assert with_dead.percentile_50 == without.percentile_50
        assert with_dead.success_rate == without.success_rate

    async def test_growth_rate_compounds_real(self, base_fire_config, frozen_today_mc):
        """growth_rate is a nominal raise deflated to real: growth at the
        inflation rate is flat real (same result as no growth); growth above
        inflation strictly improves the outcome. The pre-fix engine dropped
        growth_rate entirely."""
        from app.models.enums import IncomeType

        config = self._config(base_fire_config)
        engine = MonteCarloEngine(db=None)

        def rental(growth):
            return [_make_source(IncomeType.RENTAL, 3_960_000, growth=growth)]

        with _mc_env(config, income_sources=rental(None)):
            flat = await engine.run_simulation(n_runs=20, seed=3)
        with _mc_env(config, income_sources=rental(3.0)):  # = inflation → flat real
            at_inflation = await engine.run_simulation(n_runs=20, seed=3)
        with _mc_env(config, income_sources=rental(6.0)):  # above inflation
            real_raise = await engine.run_simulation(n_runs=20, seed=3)

        assert at_inflation.percentile_50 == pytest.approx(flat.percentile_50, rel=1e-4)
        assert real_raise.percentile_50 > flat.percentile_50

    async def test_salary_with_explicit_end_date_survives_retirement(
        self, base_fire_config, frozen_today_mc,
    ):
        """A salary whose own end_date is later than the config retirement
        date keeps paying until that end_date (staggered household
        retirement). The pre-fix engine cut ALL earned income at the single
        retirement year."""
        from datetime import date as real_date

        from app.models.enums import IncomeType

        config = self._config(base_fire_config)  # retires Jul 2026 (age 53)
        engine = MonteCarloEngine(db=None)

        with _mc_env(config, income_sources=[
            _make_source(IncomeType.SALARY, 12_000_000),  # cut at retirement
        ]):
            cut_at_retirement = await engine.run_simulation(n_runs=20, seed=4)
        with _mc_env(config, income_sources=[
            _make_source(IncomeType.SALARY, 12_000_000, end=real_date(2031, 7, 1)),
        ]):
            spouse_works_5_more = await engine.run_simulation(n_runs=20, seed=4)

        assert spouse_works_5_more.percentile_50 > cut_at_retirement.percentile_50
