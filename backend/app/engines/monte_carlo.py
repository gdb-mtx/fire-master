"""Monte Carlo simulation engine for FIRE projections.

REAL-TERMS frame, consistent with project_wealth_pools: the portfolio
compounds at a stochastic REAL return, and spending/income/SS stay FLAT in
today's dollars (constant purchasing power; COLA offsets inflation).

Per-year market draw:
  z_r, z_o ~ N(0,1) independent;  z_i = rho*z_r + sqrt(1-rho^2)*z_o
  r_nom = exp(ln(1+mu_nom) - sigma^2/2 + sigma*z_r) - 1
          (lognormal gross growth calibrated so its arithmetic mean is mu_nom)
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

The simulation is pool-aware: success is funded only by cash, taxable,
traditional-retirement, Roth, and HSA balances. Home equity, 529s, private
assets, and speculative holdings do not silently fund spending. Traditional
accounts are age-gated unless Rule of 55 or SEPP is configured. Positive
cash-flow events can make an excluded asset spendable when a planned sale or
vest actually occurs.

Overrides via fire_config.custom_assumptions["monte_carlo"]:
  return_std (0.16), inflation_std (0.015), correlation (-0.25).
Nominal return mean and inflation mean come from the base config
(expected_annual_return / expected_inflation_rate).
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

from app.engines.fire_projections import (
    _annual_spending_with_mortgage,
    _healthcare_monthly_cents_at_age,
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
        # Calibrate the lognormal so E[r_nom] == mu_nom. Centering log returns
        # directly on log(1+mu_nom) makes the arithmetic mean too high by the
        # volatility drag term and materially overstates long horizons.
        log_mean = math.log(1.0 + mu_nom) - (sigma ** 2) / 2
        r_nom = math.exp(log_mean + sigma * z_r) - 1.0
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
        from app.engines.tax_engine import TaxEngine

        fire_engine = FireProjectionsEngine(self.db)
        config = await fire_engine.get_effective_config(scenario_id)
        nw_engine = NetWorthEngine(self.db)
        nw = await nw_engine.calculate_current()
        tax_engine = TaxEngine(self.db)
        accounts = await tax_engine.get_accounts_by_tax_treatment()

        starting_cash = accounts.already_taxed_balance
        starting_taxable = accounts.taxable_balance
        starting_deferred = accounts.tax_deferred_balance
        starting_roth = accounts.tax_free_balance
        starting_spendable = (
            starting_cash + starting_taxable + starting_deferred + starting_roth
        )
        excluded_non_spendable = max(0.0, nw.net_worth - starting_spendable)
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
        tax_funding_by_year = await fire_engine._get_tax_funding_by_year(
            config, total_years, scenario_id,
        )
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
        assumptions = config.custom_assumptions or {}
        projection_cfg = assumptions.get("projection", {}) or {}
        tax_cfg = tax_engine._get_tax_config(config)
        cash_real_yield = float(tax_cfg.get("cash_yield_rate", 0.0) or 0.0)
        cash_reserve_months = max(
            0.0, float(projection_cfg.get("cash_reserve_months", 12) or 0),
        )
        penalty_free_age = float(assumptions.get("penalty_free_age", 59.5))
        rule_of_55_eligible = bool(assumptions.get("rule_of_55_eligible", False))
        sepp_monthly = float(
            (assumptions.get("sepp", {}) or {}).get("sepp_monthly", 0) or 0,
        )

        # Social Security and pension (annual dollars, FLAT REAL — COLA
        # offsets inflation, mirroring project_wealth_pools).
        has_ss_source = any(
            source.income_type.value == "social_security" for source in income_sources
        )
        ss_annual = 0.0
        if config.social_security_monthly and not has_ss_source:
            ss_annual = config.social_security_monthly * 12 / 100
        ss_start_year = 0
        if config.date_of_birth:
            ss_start_date = config.date_of_birth + relativedelta(years=config.social_security_start_age)
            ss_start_year = max(0, ss_start_date.year - today.year)

        has_pension_source = any(
            source.income_type.value == "pension" for source in income_sources
        )
        pension_annual = 0.0
        if config.pension_monthly and not has_pension_source:
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
        # signed dollars per year (probability-weighted, flat real). Unlike the
        # old total-net-worth model, asset-conversion events are kept: home or
        # private equity is excluded initially and only becomes spendable when
        # the configured sale/vest occurs.
        cf_by_month, _ = build_cashflow_schedule(
            cashflow_events, today, total_years * 12,
        )
        events_by_year = cashflow_by_year(cf_by_month, total_years)

        # The simulation runs in rolling 12-month periods beginning today,
        # while the tax plan is keyed to calendar years. Allocate each annual
        # tax bill across its months before collapsing it into projection
        # years. Applying all of the current calendar year's tax to year 0
        # front-loaded a full tax bill even when only a few months remained.
        tax_funding_by_projection_year: list[float] = []
        for yr in range(total_years):
            projected_tax = 0.0
            for m in range(12):
                current = today + relativedelta(months=yr * 12 + m)
                projected_tax += tax_funding_by_year.get(current.year, 0.0) / 12
            tax_funding_by_projection_year.append(projected_tax)

        # Starting age for spending-phase lookup
        start_age = fire_engine._compute_age(config, today) if config.date_of_birth else 30

        rng = random.Random(seed)

        # Run simulations (all values in real dollars)
        runs: list[SimulationRun] = []
        for _ in range(n_runs):
            cash = starting_cash
            taxable = starting_taxable
            deferred = starting_deferred
            roth = starting_roth
            yearly_nw: list[float] = [starting_spendable]
            money_lasted = True

            for yr in range(total_years):
                r_real, _infl = _draw_year(rng, mu_nom, sigma, mu_i, sigma_i, rho)

                # Only invested, spendable accounts receive the stochastic
                # market return. Home equity, 529s, private assets, and other
                # non-spendable balances are not part of this solvency test.
                taxable = max(0.0, taxable * (1 + r_real))
                deferred = max(0.0, deferred * (1 + r_real))
                roth = max(0.0, roth * (1 + r_real))
                cash = max(0.0, cash * (1 + cash_real_yield))

                age = start_age + yr
                is_retired = yr >= years_to_retirement

                # Spending: constant purchasing power + retirement phase step-down
                spending_mult = _spending_multiplier(age) if is_retired else 1.0
                yr_spending = _annual_spending_with_mortgage(
                    annual_spending,
                    spending_mult,
                    config,
                    today.year + yr,
                )
                if is_retired:
                    yr_spending += (
                        _healthcare_monthly_cents_at_age(config, age) * 12 / 100
                    )
                yr_spending += tax_funding_by_projection_year[yr]

                # Income: flat real, from the shared per-year precompute
                yr_income = income_by_year[yr]
                if yr >= ss_start_year:
                    yr_income += ss_annual
                if yr >= pension_start_year:
                    yr_income += pension_annual

                event_cashflow = events_by_year[yr]
                net_cash = yr_income - yr_spending + event_cashflow
                if net_cash >= 0:
                    cash += net_cash
                    # Keep the configured operating reserve in cash; invest
                    # additional savings into the taxable bridge pool so they
                    # experience the same sequence risk as the portfolio.
                    reserve = max(
                        0.0,
                        max(0.0, yr_spending - yr_income - max(0.0, event_cashflow))
                        * cash_reserve_months / 12,
                    )
                    if cash > reserve:
                        taxable += cash - reserve
                        cash = reserve
                    remaining_need = 0.0
                else:
                    remaining_need = -net_cash
                    reserve = remaining_need * cash_reserve_months / 12

                    cash_above_reserve = max(0.0, cash - reserve)
                    draw = min(remaining_need, cash_above_reserve)
                    cash -= draw
                    remaining_need -= draw

                    draw = min(remaining_need, taxable)
                    taxable -= draw
                    remaining_need -= draw

                    traditional_accessible = age >= penalty_free_age or (
                        rule_of_55_eligible and age >= 55
                    )
                    # A 72(t)/SEPP election makes only its scheduled payment
                    # available, not the household's entire deferred balance.
                    deferred_limit = (
                        deferred if traditional_accessible else sepp_monthly * 12
                    )
                    if deferred_limit > 0:
                        draw = min(remaining_need, deferred, deferred_limit)
                        deferred -= draw
                        remaining_need -= draw

                    # Roth contribution basis is not tracked separately. Treat
                    # the aggregate Roth/HSA bucket as locked before 59½ rather
                    # than optimistically assuming every dollar is accessible.
                    if age >= penalty_free_age:
                        draw = min(remaining_need, roth)
                        roth -= draw
                        remaining_need -= draw

                    draw = min(remaining_need, cash)
                    cash -= draw
                    remaining_need -= draw

                portfolio_value = cash + taxable + deferred + roth
                if remaining_need > 0.01:
                    money_lasted = False
                    # A negative point represents the unfunded annual need. In
                    # a bridge failure, locked retirement assets may still
                    # exist but cannot legally fund spending in that year.
                    portfolio_value = -remaining_need
                    yearly_nw.append(round(portfolio_value, 2))
                    for _ in range(yr + 1, total_years):
                        yearly_nw.append(round(portfolio_value, 2))
                    break
                yearly_nw.append(round(portfolio_value, 2))

            runs.append(SimulationRun(
                final_net_worth=round(yearly_nw[-1], 2),
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
            starting_spendable_assets=round(starting_spendable, 2),
            excluded_non_spendable_assets=round(excluded_non_spendable, 2),
            assumptions={
                "frame": "real (today's dollars); spending/income/SS flat real",
                "return_model": f"lognormal gross growth, mean {mu_nom:.2%} nominal, sigma {sigma:.2%}",
                "inflation_model": f"normal, mean {mu_i:.2%}, sigma {sigma_i:.2%}, corr {rho:+.2f} with returns",
                "real_return": "(1+r_nom)/(1+inflation) - 1 per year",
                "success": "cash, taxable, and age-accessible retirement assets fund every modeled year",
                "early_access": "traditional accounts are age-gated; SEPP access is capped at the configured annual payment; Roth/HSA is conservatively locked before 59.5 because contribution basis is unavailable",
                "excluded_assets": "home equity, 529s, private investments, and speculative assets unless a dated conversion event makes them spendable",
                "taxes": (
                    "projected federal and state taxes included from the expected-path withdrawal schedule; taxes are not recalculated within each random path"
                    if tax_cfg.get("fund_taxes_from_withdrawals", False)
                    else "withdrawal taxes are not added to spending"
                ),
                "cashflow_events": (
                    f"{len(cashflow_events)} planned/confirmed events applied "
                    "(probability-weighted, including configured asset conversions)"
                ),
                "seed": seed,
            },
        )
