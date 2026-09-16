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

from .conftest import FROZEN_TODAY, _make_fire_config


@pytest.fixture
def frozen_today_mc():
    """Patch date.today() in the Monte Carlo engine to 2026-04-14."""
    with patch("app.engines.monte_carlo.date") as mock_date:
        mock_date.today.return_value = FROZEN_TODAY
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        yield mock_date


@contextmanager
def _mc_env(config, *, net_worth=1_500_000.0, spending_cents=15_300_000, income_sources=(), events=()):
    """Patch every DB touchpoint the MC engine reaches through its sub-engines."""
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

    @staticmethod
    def _hand_loop(annual_spending: float, start_nw: float, dob: date) -> float:
        """Independent replica of the zero-vol path: compound at the real
        rate with flat-real flows; stop at the first breach like the engine."""
        r_real = 1.07 / 1.03 - 1
        start_age = (FROZEN_TODAY - dob).days / 365.25
        nw = start_nw
        for yr in range(TOTAL_YEARS):
            # Retired from year 0 (retirement date is mid-2026, same year)
            yr_spending = annual_spending * _spending_multiplier(start_age + yr)
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

    def test_lognormal_median_calibration(self):
        """Median gross growth factor equals 1 + mu_nom (z=0 → exactly mu)."""
        rng = random.Random(9)
        draws = sorted(
            _draw_year(rng, 0.07, 0.16, 0.03, 0.0, 0.0)[0] for _ in range(10_001)
        )
        median_real = draws[5_000]
        expected_real = 1.07 / 1.03 - 1
        assert median_real == pytest.approx(expected_real, abs=0.01)


class TestDepletion:
    async def test_depletion_pads_and_fails(self, base_fire_config, frozen_today_mc):
        """A starving plan fails every run; curves still span the horizon."""
        engine = MonteCarloEngine(db=None)
        with _mc_env(base_fire_config, net_worth=100_000.0, spending_cents=30_000_000):
            result = await engine.run_simulation(n_runs=100, seed=5)
        assert result.success_rate == 0.0
        assert result.best_final_nw < 0
        assert len(result.percentile_curves) == TOTAL_YEARS + 1


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


class TestHistoricalBlocks:
    """Opt-in block bootstrap (fire-master#21 idea, landed 2026-09-16 as opt-in only)."""

    def test_dataset_is_contiguous_and_plausible(self):
        years = [row[0] for row in HISTORICAL_MARKET_RETURNS]
        assert years == list(range(1928, 2026))
        by_year = {row[0]: row for row in HISTORICAL_MARKET_RETURNS}
        assert by_year[1931][1] < -0.40   # Depression crash
        assert by_year[2008][1] < -0.30   # GFC
        assert by_year[1979][3] > 0.12    # inflation spike
        assert all(-0.6 < r < 0.6 for _, r, _, _ in HISTORICAL_MARKET_RETURNS)

    def test_recentering_hits_configured_cagr_over_full_history(self):
        """Log-recentering: the geometric mean over ALL observations equals the target."""
        mu_nom, mu_i = 0.07, 0.03
        nominal_logs, infl_logs = [], []
        for obs in HISTORICAL_MARKET_RETURNS:
            r_real, infl = _historical_real_return(obs, 1.0, mu_nom, mu_i)
            nominal_logs.append(math.log1p((1 + r_real) * (1 + infl) - 1))
            infl_logs.append(math.log1p(infl))
        n = len(HISTORICAL_MARKET_RETURNS)
        assert abs(math.exp(sum(nominal_logs) / n) - 1 - mu_nom) < 1e-9
        assert abs(math.exp(sum(infl_logs) / n) - 1 - mu_i) < 1e-9

    def test_blocks_are_contiguous_history(self):
        rng = random.Random(7)
        path = _sample_historical_path(rng, years=20, block_years=7)
        assert len(path) == 20
        # Within each 7-year block the years are consecutive.
        for i in range(0, 14, 7):
            block_years = [row[0] for row in path[i:i + 7]]
            assert block_years == list(range(block_years[0], block_years[0] + 7))

    @pytest.mark.asyncio
    async def test_default_method_is_parametric_and_unchanged(self, base_fire_config, frozen_today_mc):
        base_fire_config.custom_assumptions = {}
        with _mc_env(base_fire_config):
            r = await MonteCarloEngine(MagicMock()).run_simulation(n_runs=50, seed=1)
        assert r.assumptions["simulation_method"] == "parametric"
        assert r.assumptions["return_model"].startswith("lognormal")

    @pytest.mark.asyncio
    async def test_opt_in_is_deterministic_and_labelled(self, base_fire_config, frozen_today_mc):
        base_fire_config.custom_assumptions = {"monte_carlo": {"simulation_method": "historical_blocks"}}
        with _mc_env(base_fire_config):
            a = await MonteCarloEngine(MagicMock()).run_simulation(n_runs=50, seed=3)
            b = await MonteCarloEngine(MagicMock()).run_simulation(n_runs=50, seed=3)
        assert a.assumptions["simulation_method"] == "historical_blocks"
        assert "1928-2025" in a.assumptions["return_model"]
        assert a.success_rate == b.success_rate
        assert a.percentile_50 == b.percentile_50
