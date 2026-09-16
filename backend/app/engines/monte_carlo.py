"""Monte Carlo simulation engine for FIRE projections.

REAL-TERMS frame, consistent with project_wealth_pools: the portfolio
compounds at a stochastic REAL return, and spending/income/SS stay FLAT in
today's dollars (constant purchasing power; COLA offsets inflation).

Per-year draw:
  z_r, z_o ~ N(0,1) independent;  z_i = rho*z_r + sqrt(1-rho^2)*z_o
  r_nom = exp(ln(1+mu_nom) + sigma*z_r) - 1     (lognormal gross growth —
          median-calibrated: the median path compounds at exactly mu_nom)
  infl  = max(-0.99, mu_i + sigma_i*z_i)         (normal, floored)
  r_real = (1+r_nom)/(1+infl) - 1

rho defaults to -0.25 (high-inflation years lean low-return), which makes
real returns MORE volatile than nominal-only volatility would suggest —
that's the honest cost of inflation risk in a real-terms model.

History note: the previous implementation drew a REAL return but inflated
spending/income NOMINALLY — double-counting inflation and making every fan
chart too pessimistic. Fixed 2026-07-11.

Second history note (fire-master#5, fixed 2026-08-01): income used to be
pre-summed by income_type over all active sources — ignoring start/end
dates, growth_rate, per-source retirement cutoff, and the active scenario.
Income now comes from the shared _income_at_month() helper against the
EFFECTIVE config, same as project_lifetime.

Third history note (fire-master#16, fixed 2026-09-09): the engine never read
CashflowEvent, so a plan expressed as dated events (the only way to model two
earners claiming SS at different ages) simulated a zero-income household.
Events now flow through the shared build_cashflow_schedule(), collapsed to
net dollars per year. Conversion events (property-sale proceeds, vests) are
skipped: this single-pool model already holds those assets at book value.

Known limit: one undifferentiated portfolio — no 59½ gate, no taxable-first
waterfall, no SEPP — so it cannot measure sequence risk inside a bridge.
A pool-aware Monte Carlo is a separate piece of work.

Overrides via fire_config.custom_assumptions["monte_carlo"]:
  return_std (0.16), inflation_std (0.015), correlation (-0.25).
Nominal return mean and inflation mean come from the base config
(expected_annual_return / expected_inflation_rate).

Opt-in historical mode (2026-09-16, from fire-master#21 by Daniel Levine):
  simulation_method: "historical_blocks" replaces the independent lognormal
  draw with overlapping contiguous blocks of JOINT (S&P 500, 10y Treasury,
  CPI) annual observations 1928-2025 (app/data/historical_market_returns.py,
  Damodaran). Each observation is recentered in log space so the full-history
  portfolio CAGR equals expected_annual_return and inflation equals
  expected_inflation_rate; only the deviations are historical. This keeps
  inflation streaks and crash/recovery sequences the iid draw cannot produce.
  Keys: historical_block_years (7), stock_weight (1.0 = all S&P, the same
  asset the parametric sigma is calibrated to). The default method stays
  "parametric": an existing config produces the same fan chart as before.
"""

from __future__ import annotations

import logging
import math
import random
import uuid as uuid_mod
from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.historical_market_returns import HISTORICAL_MARKET_RETURNS
from app.engines.fire_projections import (
    _spending_multiplier,
    build_cashflow_schedule,
    cashflow_by_year,
)
from app.schemas.tax import MonteCarloResponse, PercentileCurvePoint

logger = logging.getLogger(__name__)

# Historical S&P 500 annual volatility (~16%, 1928-2024). Return MEAN comes
# from config (expected_annual_return, nominal).
DEFAULT_RETURN_STD = 0.16
DEFAULT_INFLATION_STD = 0.015
DEFAULT_CORRELATION = -0.25
DEFAULT_HISTORICAL_BLOCK_YEARS = 7
DEFAULT_HISTORICAL_STOCK_WEIGHT = 1.0
SIMULATION_METHODS = ("parametric", "historical_blocks")


class HistoricalBlocks:
    """Block bootstrap over the joint (S&P 500, 10y Treasury, CPI) annual history.

    A run is stitched from overlapping contiguous ``block_years``-long slices of
    HISTORICAL_MARKET_RETURNS, so within a block the stock/bond/inflation
    relationships and their ordering are exactly what happened. Each observation
    is then shifted in log space so the whole-history geometric mean of the
    portfolio equals the configured nominal return and the whole-history
    inflation equals the configured rate: the LEVEL is the user's assumption,
    only the deviations are historical.
    """

    def __init__(self, stock_weight: float = DEFAULT_HISTORICAL_STOCK_WEIGHT,
                 block_years: int = DEFAULT_HISTORICAL_BLOCK_YEARS):
        self.w = min(1.0, max(0.0, float(stock_weight)))
        self.block_years = max(1, int(block_years))
        n = len(HISTORICAL_MARKET_RETURNS)
        self._port_center = sum(math.log1p(self._portfolio(o)) for o in HISTORICAL_MARKET_RETURNS) / n
        self._infl_center = sum(math.log1p(o[3]) for o in HISTORICAL_MARKET_RETURNS) / n

    def _portfolio(self, obs: tuple[int, float, float, float]) -> float:
        _year, stocks, bonds, _cpi = obs
        return self.w * stocks + (1.0 - self.w) * bonds

    def path(self, rng: random.Random, years: int) -> list[tuple[int, float, float, float]]:
        """Concatenate random contiguous blocks (never wrapping past 2025) to cover `years`."""
        hist = HISTORICAL_MARKET_RETURNS
        block = min(self.block_years, len(hist), max(1, years))
        out: list[tuple[int, float, float, float]] = []
        while len(out) < years:
            i = rng.randrange(0, len(hist) - block + 1)
            out.extend(hist[i:i + block])
        return out[:years]

    def real_return(self, obs: tuple[int, float, float, float],
                    target_nominal: float, target_inflation: float) -> tuple[float, float]:
        """(real return, inflation) for one observation, recentered to the targets."""
        nominal = math.expm1(math.log1p(target_nominal) + math.log1p(self._portfolio(obs)) - self._port_center)
        inflation = math.expm1(math.log1p(target_inflation) + math.log1p(obs[3]) - self._infl_center)
        return (1.0 + nominal) / (1.0 + inflation) - 1.0, inflation


def _draw_year(
    rng: random.Random,
    mu_nom: float,
    sigma: float,
    mu_i: float,
    sigma_i: float,
    rho: float,
) -> tuple[float, float]:
    """One year's correlated (real_return, inflation) draw.

    Degenerate volatilities collapse to the deterministic means, so a
    zero-vol run reproduces plain compound growth exactly.
    """
    z_r = rng.gauss(0.0, 1.0)
    z_o = rng.gauss(0.0, 1.0)
    rho = max(-0.99, min(0.99, rho))
    z_i = rho * z_r + math.sqrt(1.0 - rho * rho) * z_o

    if sigma > 0:
        r_nom = math.exp(math.log(1.0 + mu_nom) + sigma * z_r) - 1.0
    else:
        r_nom = mu_nom
    if sigma_i > 0:
        infl = max(-0.99, mu_i + sigma_i * z_i)
    else:
        infl = mu_i

    r_real = (1.0 + r_nom) / (1.0 + infl) - 1.0
    return r_real, infl


@dataclass
class SimulationRun:
    """Result of a single Monte Carlo run."""
    final_net_worth: float
    money_lasted: bool
    yearly_net_worths: list[float]  # net worth at each year


class MonteCarloEngine:
    """Monte Carlo simulation wrapper around the FIRE projections engine."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def run_simulation(
        self,
        n_runs: int = 1000,
        seed: int | None = None,
        scenario_id: uuid_mod.UUID | None = None,
    ) -> MonteCarloResponse:
        """Run N Monte Carlo simulations with randomized annual returns.

        Each run uses the same config/spending/income but draws correlated
        (return, inflation) pairs per year — sequence-of-returns risk plus
        inflation risk, in real terms.
        """
        from app.engines.fire_projections import FireProjectionsEngine
        from app.engines.net_worth import NetWorthEngine

        fire_engine = FireProjectionsEngine(self.db)
        config = await fire_engine.get_effective_config(scenario_id)
        nw_engine = NetWorthEngine(self.db)
        nw = await nw_engine.calculate_current()

        current_nw = nw.net_worth  # dollars
        annual_spending_cents = await fire_engine._get_annual_spending(config)
        annual_spending = annual_spending_cents / 100  # flat REAL
        income_sources = await fire_engine._get_income_sources()
        cashflow_events = await fire_engine._get_cashflow_events()

        retirement_date = fire_engine._get_retirement_date(config)
        today = date.today()

        if config.date_of_birth:
            end_date = config.date_of_birth + relativedelta(years=config.life_expectancy)
        else:
            end_date = today + relativedelta(years=40)

        total_years = max(1, (end_date.year - today.year))
        years_to_retirement = 0
        if retirement_date and retirement_date > today:
            years_to_retirement = max(0, (retirement_date.year - today.year))

        # Stochastic parameters: nominal return mean + inflation mean from the
        # base config; volatilities/correlation overridable via
        # custom_assumptions.monte_carlo.
        mc_cfg = (config.custom_assumptions or {}).get("monte_carlo", {})
        mu_nom = config.expected_annual_return / 100
        mu_i = config.expected_inflation_rate / 100
        sigma = mc_cfg.get("return_std", DEFAULT_RETURN_STD)
        sigma_i = mc_cfg.get("inflation_std", DEFAULT_INFLATION_STD)
        rho = mc_cfg.get("correlation", DEFAULT_CORRELATION)
        # Opt-in historical block bootstrap (see module docstring). Default parametric.
        simulation_method = str(mc_cfg.get("simulation_method", "parametric")).lower()
        if simulation_method not in SIMULATION_METHODS:
            logger.warning("monte_carlo.simulation_method=%r unknown; using parametric", simulation_method)
            simulation_method = "parametric"
        historical_block_years = max(1, int(mc_cfg.get("historical_block_years", DEFAULT_HISTORICAL_BLOCK_YEARS)))
        stock_weight = max(0.0, min(1.0, float(mc_cfg.get("stock_weight", DEFAULT_HISTORICAL_STOCK_WEIGHT))))
        blocks = HistoricalBlocks(stock_weight, historical_block_years) if simulation_method == "historical_blocks" else None

        # Social Security and pension (annual dollars, FLAT REAL — COLA
        # offsets inflation, mirroring project_wealth_pools).
        ss_annual = 0.0
        if config.social_security_monthly:
            ss_annual = config.social_security_monthly * 12 / 100
        ss_start_year = 0
        if config.date_of_birth:
            ss_start_date = config.date_of_birth + relativedelta(years=config.social_security_start_age)
            ss_start_year = max(0, ss_start_date.year - today.year)

        pension_annual = 0.0
        if config.pension_monthly:
            pension_annual = config.pension_monthly * 12 / 100
        pension_start_year = 0
        if config.pension_start_age and config.date_of_birth:
            pension_start_date = config.date_of_birth + relativedelta(years=config.pension_start_age)
            pension_start_year = max(0, pension_start_date.year - today.year)

        # Pre-compute source income per year via the shared, scenario-aware
        # helper (date bounds, retirement cutoff, real growth compounding) —
        # deterministic, so it lives outside the run loop. Each year sums 12
        # monthly evaluations, matching project_lifetime's cadence so mid-year
        # retirement and source start/end dates land in the right year.
        inflation_pct = config.expected_inflation_rate
        income_by_year: list[float] = []
        for yr in range(total_years):
            year_cents = 0
            for m in range(12):
                month_idx = yr * 12 + m
                current = today + relativedelta(months=month_idx)
                year_cents += fire_engine._income_at_month(
                    income_sources, current, retirement_date,
                    month_idx / 12.0, inflation_pct,
                )
            income_by_year.append(year_cents / 100)

        # Cashflow events: same schedule the projection engines use, net
        # signed dollars per year (probability-weighted, flat real).
        cf_by_month, _ = build_cashflow_schedule(
            cashflow_events, today, total_years * 12,
            skip=fire_engine._single_pool_event_skip(config),
        )
        events_by_year = cashflow_by_year(cf_by_month, total_years)

        # Starting age for spending-phase lookup
        start_age = fire_engine._compute_age(config, today) if config.date_of_birth else 30

        rng = random.Random(seed)

        # Run simulations (all values in real dollars)
        runs: list[SimulationRun] = []
        for _ in range(n_runs):
            nw_val = current_nw
            yearly_nw: list[float] = [current_nw]
            money_lasted = True
            historical_path = blocks.path(rng, total_years) if blocks is not None else None

            for yr in range(total_years):
                if historical_path is not None:
                    r_real, _infl = blocks.real_return(historical_path[yr], mu_nom, mu_i)
                else:
                    r_real, _infl = _draw_year(rng, mu_nom, sigma, mu_i, sigma_i, rho)

                age = start_age + yr
                is_retired = yr >= years_to_retirement

                # Spending: constant purchasing power + retirement phase step-down
                yr_spending = annual_spending
                if is_retired:
                    yr_spending *= _spending_multiplier(age)

                # Income: flat real, from the shared per-year precompute
                yr_income = income_by_year[yr]
                if yr >= ss_start_year:
                    yr_income += ss_annual
                if yr >= pension_start_year:
                    yr_income += pension_annual

                net_cash = yr_income - yr_spending + events_by_year[yr]
                nw_val = nw_val * (1 + r_real) + net_cash
                yearly_nw.append(round(nw_val, 2))

                if nw_val < 0:
                    money_lasted = False
                    # Pad remaining years with the breach value
                    for _ in range(yr + 1, total_years):
                        yearly_nw.append(round(nw_val, 2))
                    break

            runs.append(SimulationRun(
                final_net_worth=round(nw_val, 2),
                money_lasted=money_lasted,
                yearly_net_worths=yearly_nw,
            ))

        # Compute results
        finals = sorted(r.final_net_worth for r in runs)
        success_count = sum(1 for r in runs if r.money_lasted)

        # Percentile curves (year-by-year percentiles across runs)
        curves: list[PercentileCurvePoint] = []
        for yr in range(total_years + 1):
            values = sorted(r.yearly_net_worths[yr] for r in runs if yr < len(r.yearly_net_worths))
            if not values:
                break
            n = len(values)
            curves.append(PercentileCurvePoint(
                age=round(start_age + yr, 1),
                p10=round(values[int(n * 0.10)], 2),
                p25=round(values[int(n * 0.25)], 2),
                p50=round(values[int(n * 0.50)], 2),
                p75=round(values[int(n * 0.75)], 2),
                p90=round(values[min(int(n * 0.90), n - 1)], 2),
            ))

        return MonteCarloResponse(
            success_rate=round(success_count / n_runs * 100, 1),
            percentile_10=round(finals[int(n_runs * 0.10)], 2),
            percentile_25=round(finals[int(n_runs * 0.25)], 2),
            percentile_50=round(finals[int(n_runs * 0.50)], 2),
            percentile_75=round(finals[int(n_runs * 0.75)], 2),
            percentile_90=round(finals[min(int(n_runs * 0.90), n_runs - 1)], 2),
            mean_final_nw=round(sum(finals) / n_runs, 2),
            total_runs=n_runs,
            percentile_curves=curves,
            worst_final_nw=round(finals[0], 2),
            best_final_nw=round(finals[-1], 2),
            assumptions={
                "frame": "real (today's dollars); spending/income/SS flat real",
                "simulation_method": simulation_method,
                "return_model": (
                    f"historical {historical_block_years}-year blocks 1928-2025 (Damodaran), "
                    f"{stock_weight:.0%} S&P 500 / {1 - stock_weight:.0%} 10y Treasury, "
                    f"recentered to {mu_nom:.2%} nominal CAGR"
                    if simulation_method == "historical_blocks"
                    else f"lognormal gross growth, median {mu_nom:.2%} nominal, sigma {sigma:.2%}"
                ),
                "inflation_model": (
                    f"joint historical blocks recentered to {mu_i:.2%} long-run inflation"
                    if simulation_method == "historical_blocks"
                    else f"normal, mean {mu_i:.2%}, sigma {sigma_i:.2%}, corr {rho:+.2f} with returns"
                ),
                "real_return": "(1+r_nom)/(1+inflation) - 1 per year",
                "cashflow_events": (
                    f"{len(cashflow_events)} planned/confirmed events applied "
                    "(probability-weighted; conversion events skipped — assets already in net worth)"
                ),
                "seed": seed,
            },
        )
