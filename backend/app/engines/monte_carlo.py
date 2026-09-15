"""Monte Carlo simulation engine for FIRE projections.

REAL-TERMS frame, consistent with project_wealth_pools: the portfolio
compounds at a stochastic REAL return, and spending/income/SS stay FLAT in
today's dollars (constant purchasing power; COLA offsets inflation).

Per-year market draw:
  z_r, z_o ~ N(0,1) independent;  z_i = rho*z_r + sqrt(1-rho^2)*z_o
  r_nom = exp(log_center + sigma*z_r) - 1
          where log_center is ln(1+mu_nom) for CAGR mode, or subtracts
          sigma^2/2 when the configured return is an arithmetic mean
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
  simulation_method ("historical_blocks"), historical_block_years (7),
  accumulation_stock_weight (0.80), retirement_stock_weight (0.70),
  return_mean_type ("geometric"), accumulation_return_std (0.13),
  retirement_return_std (0.12), inflation_std (0.015), correlation (-0.25).
Nominal return mean and inflation mean come from the base config
(expected_annual_return / expected_inflation_rate).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import uuid as uuid_mod
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.historical_market_returns import HISTORICAL_MARKET_RETURNS
from app.engines.fire_projections import (
    _annual_spending_with_mortgage,
    _contribution_policy_active,
    _healthcare_monthly_cents_at_date,
    _household_projection_end_date,
    _household_social_security_monthly_cents,
    _household_survivor_spending_multiplier,
    _retirement_savings_settings,
    _spending_multiplier,
    build_cashflow_schedule,
    cashflow_by_year,
)
from app.schemas.tax import (
    MonteCarloResponse,
    PercentileCurvePoint,
    RetirementAgeAnalysisResponse,
    RetirementConfidenceAge,
)

logger = logging.getLogger(__name__)

# Legacy all-stock volatility remains exported for the deterministic FIRE
# safety-margin calculation. Monte Carlo itself defaults to a diversified
# 13% working / 12% retirement allocation.
DEFAULT_RETURN_STD = 0.16
DEFAULT_ACCUMULATION_RETURN_STD = 0.13
DEFAULT_RETIREMENT_RETURN_STD = 0.12
DEFAULT_INFLATION_STD = 0.015
DEFAULT_CORRELATION = -0.25
DEFAULT_HISTORICAL_BLOCK_YEARS = 7
DEFAULT_ACCUMULATION_STOCK_WEIGHT = 0.80
DEFAULT_RETIREMENT_STOCK_WEIGHT = 0.70


def _draw_year(
    rng: random.Random,
    mu_nom: float,
    sigma: float,
    mu_i: float,
    sigma_i: float,
    rho: float,
    mean_type: str = "arithmetic",
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
        if mean_type == "geometric":
            # Household planning inputs are normally understood as long-run
            # compounded returns (CAGR). Keep the median log-growth path at
            # that rate; volatility then widens outcomes around it.
            log_mean = math.log(1.0 + mu_nom)
        else:
            # Arithmetic-capital-market mode: E[r_nom] == mu_nom.
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


@lru_cache(maxsize=32)
def _historical_log_centers(stock_weight: float) -> tuple[float, float]:
    """Geometric centers for a rebalanced stock/bond portfolio and inflation."""
    weight = max(0.0, min(1.0, stock_weight))
    portfolio_logs = []
    inflation_logs = []
    for _year, stock_return, bond_return, inflation in HISTORICAL_MARKET_RETURNS:
        portfolio_return = weight * stock_return + (1 - weight) * bond_return
        portfolio_logs.append(math.log1p(portfolio_return))
        inflation_logs.append(math.log1p(inflation))
    return (
        sum(portfolio_logs) / len(portfolio_logs),
        sum(inflation_logs) / len(inflation_logs),
    )


def _sample_historical_path(
    rng: random.Random,
    years: int,
    block_years: int = DEFAULT_HISTORICAL_BLOCK_YEARS,
) -> list[tuple[int, float, float, float]]:
    """Sample overlapping contiguous historical blocks without year wrapping."""
    history = HISTORICAL_MARKET_RETURNS
    block = max(1, min(int(block_years), len(history), max(1, years)))
    path: list[tuple[int, float, float, float]] = []
    while len(path) < years:
        start = rng.randrange(0, len(history) - block + 1)
        path.extend(history[start:start + block])
    return path[:years]


def _historical_real_return(
    observation: tuple[int, float, float, float],
    stock_weight: float,
    target_nominal_cagr: float,
    target_inflation: float,
) -> tuple[float, float]:
    """Recenter one joint historical observation to configured long-run means.

    Log deviations from the full-history geometric mean are retained, so crash,
    recovery, bond, and inflation relationships remain exactly historical. The
    level is shifted so the full-history portfolio CAGR matches the user's
    expected-return input and inflation matches the configured long-run rate.
    """
    _year, stock_return, bond_return, inflation = observation
    weight = max(0.0, min(1.0, stock_weight))
    portfolio_return = weight * stock_return + (1 - weight) * bond_return
    portfolio_center, inflation_center = _historical_log_centers(weight)
    nominal_log = (
        math.log1p(target_nominal_cagr)
        + math.log1p(portfolio_return)
        - portfolio_center
    )
    inflation_log = (
        math.log1p(target_inflation)
        + math.log1p(inflation)
        - inflation_center
    )
    nominal_return = math.exp(nominal_log) - 1
    adjusted_inflation = math.exp(inflation_log) - 1
    real_return = (1 + nominal_return) / (1 + adjusted_inflation) - 1
    return real_return, adjusted_inflation


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
        retirement_age_override: int | None = None,
    ) -> MonteCarloResponse:
        """Run N Monte Carlo simulations with randomized annual returns.

        Each run uses the same config/spending/income but draws correlated
        (return, inflation) pairs per year — sequence-of-returns risk plus
        inflation risk, in real terms.
        """
        from app.engines.fire_projections import FireProjectionsEngine
        from app.engines.net_worth import NetWorthEngine
        from app.engines.tax_engine import TaxEngine, _get_rmd_divisor

        fire_engine = FireProjectionsEngine(self.db)
        config = await fire_engine.get_effective_config(scenario_id)
        if retirement_age_override is not None:
            config = fire_engine._apply_overrides(config, {
                "target_retirement_age": retirement_age_override,
                "target_retirement_date": None,
            })
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

        end_date = _household_projection_end_date(
            config, today + relativedelta(years=40),
        )

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
        configured_method = mc_cfg.get("simulation_method")
        if configured_method is None:
            # Test fixtures and older saved profiles used return_std as an
            # explicit request for the parametric model.
            configured_method = (
                "parametric"
                if "return_std" in mc_cfg or "inflation_std" in mc_cfg
                else "historical_blocks"
            )
        simulation_method = str(configured_method).lower()
        if simulation_method not in {"historical_blocks", "parametric"}:
            simulation_method = "historical_blocks"
        historical_block_years = max(
            1, int(mc_cfg.get("historical_block_years", DEFAULT_HISTORICAL_BLOCK_YEARS)),
        )
        accumulation_stock_weight = max(0.0, min(
            1.0,
            float(mc_cfg.get(
                "accumulation_stock_weight", DEFAULT_ACCUMULATION_STOCK_WEIGHT,
            )),
        ))
        retirement_stock_weight = max(0.0, min(
            1.0,
            float(mc_cfg.get(
                "retirement_stock_weight", DEFAULT_RETIREMENT_STOCK_WEIGHT,
            )),
        ))
        mean_type = str(mc_cfg.get("return_mean_type", "geometric")).lower()
        if mean_type not in {"geometric", "arithmetic"}:
            mean_type = "geometric"
        legacy_sigma = mc_cfg.get("return_std")
        accumulation_sigma = float(
            mc_cfg.get(
                "accumulation_return_std",
                legacy_sigma if legacy_sigma is not None else DEFAULT_ACCUMULATION_RETURN_STD,
            ),
        )
        retirement_sigma = float(
            mc_cfg.get(
                "retirement_return_std",
                legacy_sigma if legacy_sigma is not None else DEFAULT_RETIREMENT_RETURN_STD,
            ),
        )
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
        savings_settings = _retirement_savings_settings(config)
        enforce_rmd = bool(projection_cfg.get("enforce_rmd", True))
        rmd_start_age = int(config.rmd_start_age or 73)

        # Social Security and pension (annual dollars, FLAT REAL — COLA
        # offsets inflation, mirroring project_wealth_pools).
        has_ss_source = any(
            source.income_type.value == "social_security" for source in income_sources
        )
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
        employment_income_by_year: list[float] = []
        contribution_phase_by_year: list[bool] = []
        employment_sources = [
            source for source in income_sources
            if source.income_type.value in ("salary", "bonus", "side_hustle")
        ]
        for yr in range(total_years):
            year_cents = 0
            employment_cents = 0
            contribution_phase_active = False
            for m in range(12):
                month_idx = yr * 12 + m
                current = today + relativedelta(months=month_idx)
                year_cents += fire_engine._income_at_month(
                    income_sources, current, retirement_date,
                    month_idx / 12.0, inflation_pct,
                )
                month_employment_cents = fire_engine._income_at_month(
                    employment_sources, current, retirement_date,
                    month_idx / 12.0, inflation_pct,
                )
                employment_cents += month_employment_cents
                if (
                    month_employment_cents > 0
                    and _contribution_policy_active(savings_settings, current)
                ):
                    contribution_phase_active = True
            income_by_year.append(year_cents / 100)
            employment_income_by_year.append(employment_cents / 100)
            contribution_phase_by_year.append(contribution_phase_active)

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
        for run_index in range(n_runs):
            # The simulation is CPU-heavy but runs inside an async request.
            # Yield in small batches so health checks and the page's quick
            # summary requests are not blocked for the full MC runtime.
            if run_index and run_index % 25 == 0:
                await asyncio.sleep(0)
            historical_path = (
                _sample_historical_path(rng, total_years, historical_block_years)
                if simulation_method == "historical_blocks" else None
            )
            cash = starting_cash
            taxable = starting_taxable
            deferred = starting_deferred
            roth = starting_roth
            roth_accessible_basis = min(
                roth, float(savings_settings["starting_roth_basis"]),
            )
            hsa_qualified_balance = min(
                max(0.0, roth - roth_accessible_basis),
                float(savings_settings["starting_hsa_qualified_balance"]),
            )
            roth_conversion_vintages: list[tuple[int, float]] = []
            yearly_nw: list[float] = [starting_spendable]
            money_lasted = True

            for yr in range(total_years):
                matured = sum(
                    amount for available_year, amount in roth_conversion_vintages
                    if available_year <= yr
                )
                roth_accessible_basis = min(
                    max(0.0, roth - hsa_qualified_balance),
                    roth_accessible_basis + matured,
                )
                roth_conversion_vintages = [
                    (available_year, amount)
                    for available_year, amount in roth_conversion_vintages
                    if available_year > yr
                ]

                age = start_age + yr
                is_retired = yr >= years_to_retirement
                if historical_path is not None:
                    stock_weight = (
                        retirement_stock_weight if is_retired
                        else accumulation_stock_weight
                    )
                    r_real, _infl = _historical_real_return(
                        historical_path[yr], stock_weight, mu_nom, mu_i,
                    )
                else:
                    sigma = retirement_sigma if is_retired else accumulation_sigma
                    r_real, _infl = _draw_year(
                        rng, mu_nom, sigma, mu_i, sigma_i, rho, mean_type,
                    )

                # Only invested, spendable accounts receive the stochastic
                # market return. Home equity, 529s, private assets, and other
                # non-spendable balances are not part of this solvency test.
                taxable = max(0.0, taxable * (1 + r_real))
                deferred = max(0.0, deferred * (1 + r_real))
                roth = max(0.0, roth * (1 + r_real))
                roth_accessible_basis = min(roth, roth_accessible_basis)
                cash = max(0.0, cash * (1 + cash_real_yield))

                # RMDs are a transfer from deferred to already-taxed assets,
                # not spending. The expected-path tax schedule includes the
                # resulting ordinary income, so the random path must also
                # retain the distributed principal instead of charging tax
                # while leaving the same dollars stranded in the IRA.
                if enforce_rmd and age >= rmd_start_age and deferred > 0:
                    rmd_amount = min(
                        deferred, deferred / _get_rmd_divisor(int(age)),
                    )
                    deferred -= rmd_amount
                    taxable += rmd_amount

                # Spending: constant purchasing power + retirement phase step-down
                current_year_date = today + relativedelta(years=yr)
                spending_mult = _spending_multiplier(age) if is_retired else 1.0
                spending_mult *= _household_survivor_spending_multiplier(
                    config, current_year_date,
                )
                living_spending = _annual_spending_with_mortgage(
                    annual_spending,
                    spending_mult,
                    config,
                    today.year + yr,
                )
                if is_retired:
                    living_spending += (
                        _healthcare_monthly_cents_at_date(
                            config, current_year_date,
                        ) * 12 / 100
                    )
                qualified_healthcare_spending = (
                    _healthcare_monthly_cents_at_date(
                        config, current_year_date,
                    ) * 12 / 100
                    if is_retired else 0.0
                )
                hsa_eligible_remaining = min(
                    hsa_qualified_balance, qualified_healthcare_spending,
                )
                yr_spending = living_spending + tax_funding_by_projection_year[yr]

                # Income: flat real, from the shared per-year precompute
                yr_income = income_by_year[yr]
                if not has_ss_source:
                    yr_income += (
                        _household_social_security_monthly_cents(
                            # Preserve the engine's existing annual convention:
                            # a benefit beginning anywhere in a calendar year
                            # is counted for that projection year.
                            config, date(current_year_date.year, 12, 31),
                        ) * 12 / 100
                    )
                if yr >= pension_start_year:
                    yr_income += pension_annual

                employment_income = employment_income_by_year[yr]
                annual_401k_contribution = 0.0
                annual_roth_contribution = 0.0
                if employment_income > 0.01 and contribution_phase_by_year[yr]:
                    annual_401k_contribution = min(
                        float(savings_settings["annual_401k"]), employment_income,
                    )
                    annual_roth_contribution = min(
                        float(savings_settings["annual_roth"]),
                        max(0.0, employment_income - annual_401k_contribution),
                    )
                deferred += annual_401k_contribution
                roth += annual_roth_contribution
                roth_accessible_basis += annual_roth_contribution

                event_cashflow = events_by_year[yr]
                net_cash = (
                    yr_income - yr_spending + event_cashflow
                    - annual_401k_contribution - annual_roth_contribution
                )
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

                    roth_limit = (
                        roth if age >= penalty_free_age
                        else min(
                            roth,
                            roth_accessible_basis + hsa_eligible_remaining,
                        )
                    )
                    if roth_limit > 0:
                        draw = min(remaining_need, roth_limit)
                        roth -= draw
                        remaining_need -= draw
                        if age < penalty_free_age:
                            hsa_draw = min(draw, hsa_eligible_remaining)
                            hsa_eligible_remaining -= hsa_draw
                            hsa_qualified_balance -= hsa_draw
                            roth_accessible_basis -= draw - hsa_draw

                    draw = min(remaining_need, cash)
                    cash -= draw
                    remaining_need -= draw

                # Build the ladder only while a conversion can mature before
                # normal penalty-free access. It is a transfer between pools,
                # so total wealth does not change; its tax cost is already in
                # the expected-path tax schedule above.
                if (
                    bool(savings_settings["ladder_enabled"])
                    and is_retired
                    and age + int(savings_settings["ladder_wait_years"]) < penalty_free_age
                    and deferred > 0
                ):
                    nonemployment_income = (
                        yr_income - employment_income
                    )
                    configured_conversion = float(
                        savings_settings["ladder_annual_conversion"],
                    )
                    conversion = min(
                        configured_conversion
                        or max(0.0, living_spending - nonemployment_income),
                        deferred,
                    )
                    if conversion > 0:
                        deferred -= conversion
                        roth += conversion
                        roth_conversion_vintages.append((
                            yr + int(savings_settings["ladder_wait_years"]),
                            conversion,
                        ))

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
                "return_model": (
                    f"overlapping {historical_block_years}-year blocks from 1928-2025, "
                    f"{accumulation_stock_weight:.0%} stocks while working / "
                    f"{retirement_stock_weight:.0%} in retirement, recentered to "
                    f"{mu_nom:.2%} nominal CAGR"
                    if simulation_method == "historical_blocks"
                    else (
                        f"independent lognormal growth, {mu_nom:.2%} nominal {mean_type} return; "
                        f"sigma {accumulation_sigma:.2%} while working and "
                        f"{retirement_sigma:.2%} in retirement"
                    )
                ),
                "inflation_model": (
                    f"joint historical blocks recentered to {mu_i:.2%} long-run inflation"
                    if simulation_method == "historical_blocks"
                    else f"normal, mean {mu_i:.2%}, sigma {sigma_i:.2%}, corr {rho:+.2f} with returns"
                ),
                "simulation_method": simulation_method,
                "real_return": "(1+r_nom)/(1+inflation) - 1 per year",
                "success": "cash, taxable, and age-accessible retirement assets fund every modeled year",
                "early_access": "traditional accounts are age-gated; SEPP is capped; Roth contribution basis and five-year-matured conversions are available before 59.5; the configured HSA reserve is limited to modeled qualified healthcare costs",
                "working_savings": (
                    f"{int(savings_settings['worker_count'])} workers; "
                    f"${float(savings_settings['annual_401k']):,.0f}/yr to traditional 401(k)s "
                    f"and ${float(savings_settings['annual_roth']):,.0f}/yr to Roth IRAs during the configured contribution phase"
                ),
                "roth_ladder": (
                    f"enabled with {int(savings_settings['ladder_wait_years'])}-year conversion seasoning"
                    if savings_settings["ladder_enabled"] else "disabled"
                ),
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

    async def analyze_retirement_ages(
        self,
        targets: tuple[float, ...] = (80.0, 90.0, 95.0),
        n_runs: int = 1000,
        seed: int = 42,
        max_age: int = 85,
        scenario_id: uuid_mod.UUID | None = None,
    ) -> RetirementAgeAnalysisResponse:
        """Find the earliest integer retirement age meeting each confidence.

        Each candidate reuses the same random seed, so moving the retirement
        age changes the plan rather than the sampled market paths. Success is
        expected to be monotonic as work continues longer. The targets share
        one adaptive search: every sampled age tightens the brackets for all
        confidence levels, instead of running three mostly separate searches.
        """
        from app.engines.fire_projections import FireProjectionsEngine

        fire_engine = FireProjectionsEngine(self.db)
        config = await fire_engine.get_effective_config(scenario_id)
        today = date.today()
        if not config.date_of_birth:
            return RetirementAgeAnalysisResponse(
                configured_retirement_age=None,
                current_age=0.0,
                max_tested_age=max_age,
                runs_per_age=n_runs,
                confidence_ages=[
                    RetirementConfidenceAge(confidence=target)
                    for target in sorted(set(targets))
                ],
            )
        current_age = (
            fire_engine._compute_age(config, today)
        )
        retirement_date = fire_engine._get_retirement_date(config)
        configured_age = (
            fire_engine._compute_age(config, retirement_date)
            if retirement_date and config.date_of_birth
            else None
        )
        first_age = max(18, math.ceil(current_age))
        last_age = min(max_age, max(first_age, int(config.life_expectancy) - 1))
        cache: dict[int, float] = {}

        async def success_at(age: int) -> float:
            if age not in cache:
                result = await self.run_simulation(
                    n_runs=n_runs,
                    seed=seed,
                    scenario_id=scenario_id,
                    retirement_age_override=age,
                )
                cache[age] = result.success_rate
            return cache[age]

        ordered_targets = sorted(set(targets))
        first_success = await success_at(first_age)
        last_success = await success_at(last_age)

        # A bracket is [known failure, known success]. An already-ready target
        # resolves to first_age; an unreachable target has no bracket.
        brackets: dict[float, list[int]] = {}
        resolved: dict[float, int | None] = {}
        for target in ordered_targets:
            if first_success >= target:
                resolved[target] = first_age
            elif last_success < target:
                resolved[target] = None
            else:
                brackets[target] = [first_age, last_age]

        while any(hi - lo > 1 for lo, hi in brackets.values()):
            # Choose the age with the best worst-case reduction across every
            # possible result bucket (<80, 80-90, 90-95, >=95). This is the
            # multi-threshold equivalent of a binary-search midpoint.
            candidate_ages = sorted({
                age
                for lo, hi in brackets.values()
                for age in range(lo + 1, hi)
            })

            def search_score(candidate_age: int) -> tuple[int, int, int]:
                outcome_scores: list[int] = []
                for met_count in range(len(ordered_targets) + 1):
                    remaining = 0
                    for target_index, target in enumerate(ordered_targets):
                        if target not in brackets:
                            continue
                        lo, hi = brackets[target]
                        if lo < candidate_age < hi:
                            if target_index < met_count:
                                hi = candidate_age
                            else:
                                lo = candidate_age
                        remaining += max(0, hi - lo - 1)
                    outcome_scores.append(remaining)
                return (
                    max(outcome_scores),
                    sum(outcome_scores),
                    candidate_age,
                )

            if len(cache) < 4:
                # Two interior midpoint-style probes establish the rough
                # shape before trusting interpolation on a potentially steep
                # or flat success curve.
                candidate = min(candidate_ages, key=search_score)
            else:
                estimates: list[int] = []
                for target, (lo, hi) in brackets.items():
                    if hi - lo <= 1:
                        continue
                    lo_success = cache[lo]
                    hi_success = cache[hi]
                    if hi_success > lo_success:
                        raw = lo + (
                            (target - lo_success)
                            / (hi_success - lo_success)
                            * (hi - lo)
                        )
                        estimate = math.ceil(raw)
                    else:
                        estimate = (lo + hi) // 2
                    estimates.append(max(lo + 1, min(hi - 1, estimate)))
                estimates.sort()
                candidate = estimates[(len(estimates) - 1) // 2]
            candidate_success = await success_at(candidate)
            for target, bounds in brackets.items():
                lo, hi = bounds
                if not lo < candidate < hi:
                    continue
                if candidate_success >= target:
                    bounds[1] = candidate
                else:
                    bounds[0] = candidate

        for target, (_lo, hi) in brackets.items():
            resolved[target] = hi

        confidence_ages: list[RetirementConfidenceAge] = []
        for target in ordered_targets:
            found = resolved[target]
            if found is None:
                confidence_ages.append(RetirementConfidenceAge(
                    confidence=target,
                    earliest_age=None,
                    success_rate=None,
                    prior_age_success_rate=last_success,
                ))
                continue
            prior = cache[found - 1] if found > first_age else None
            confidence_ages.append(RetirementConfidenceAge(
                confidence=target,
                earliest_age=found,
                success_rate=cache[found],
                prior_age_success_rate=prior,
            ))

        return RetirementAgeAnalysisResponse(
            configured_retirement_age=(
                round(configured_age, 1) if configured_age is not None else None
            ),
            current_age=round(current_age, 1),
            max_tested_age=last_age,
            runs_per_age=n_runs,
            confidence_ages=confidence_ages,
        )
