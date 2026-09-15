"""Shared fixtures for FIREMaster tests.

Provides deterministic FireConfig, account, income source, and cashflow event
fixtures for a synthetic test persona (frozen at April 14, 2026). All monetary
values in cents (matching PostgreSQL BIGINT storage).

The persona owns three properties:
  - "Coastal Condo"  — primary residence with a mortgage (fire_role primary_residence)
  - "Mountain House" — secondary home headed for sale (fire_role sell_candidate)
  - "River House"    — income property, rented out (fire_role income_producing)

Legacy single-property config blocks (miami_sale / park_city / sauvie_sale /
str_income / mortgage_recast) are exercised deliberately: the engine retains
them for author back-compat, and these fixtures opt in explicitly because the
engine's own defaults are neutral (zero/disabled).
"""

import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engines.tax_engine import TaxEngine
from app.models.fire_config import FireConfig
from app.models.cashflow_event import CashflowEvent
from app.models.enums import IncomeType
from app.models.income_source import IncomeSource
from app.schemas.fire import NetWorthBreakdown


# ---------------------------------------------------------------------------
# Frozen date — deterministic age calculations
# ---------------------------------------------------------------------------

FROZEN_TODAY = date(2026, 4, 14)


@pytest.fixture
def frozen_today():
    """Patch date.today() in the projections engine to 2026-04-14."""
    with patch("app.engines.fire_projections.date") as mock_date:
        mock_date.today.return_value = FROZEN_TODAY
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        yield mock_date


# ---------------------------------------------------------------------------
# Tax engine (no DB needed)
# ---------------------------------------------------------------------------

@pytest.fixture
def tax_engine():
    return TaxEngine(db=None)


# ---------------------------------------------------------------------------
# Base fire config (synthetic persona)
# ---------------------------------------------------------------------------

BASE_CUSTOM_ASSUMPTIONS = {
    "scenario": "baseline",
    "sepp_bridge": True,
    "bridge_end_age": 59.5,
    "penalty_free_age": 59.5,
    "rule_of_55_eligible": False,
    "rental_occupancy_rate": 1.0,
    # Occupancy haircut applies only to the income property's rental source
    "occupancy_source_match": ["river house"],
    "tax": {
        "filing_status": "single",
        "household_size": 1,
        "cost_basis_pct": 0.6,
        "state": "CO",
        "state_tax_rate": 4.4,
    },
    # The engine's sepp/rrsp defaults are zero — fixtures opt in explicitly.
    "sepp": {
        "ira_a_balance": 402_000,
        "ira_b_balance": 165_000,
        "sepp_monthly": 2_100,
        "sepp_start_month": 12,
        "ira_growth_rate": 0.06,
    },
    "rrsp": {
        "monthly_net": 1_900,
        "start_month": 12,
        "total_available": 205_000,
    },
    # LEGACY single-property blocks — exercised for author back-compat.
    "miami_sale": {
        "sale_month": 60,
        "monthly_cost": 6_150,
        "post_sale_rent": 2_500,
        "rental_income_lost": 340,
    },
    "park_city": {
        "monthly_cost": 2_400,
    },
    "projection": {
        "surplus_investment_rate": 0.04,
        "cash_reserve_months": 12,
        "cash_savings_rate_early": 0.01,
        "cash_savings_rate_late": 0.0,
        "cash_savings_cutover_month": 60,
        "re_appreciation_rate": 0.01,
        "primary_property_purchase_price": 510_000,
        "primary_property_agent_fee_pct": 0.06,
        "primary_property_mortgage_pi": 3_100,
        "primary_property_cost_basis": 510_000,
        "primary_property_ltcg_rate": 0.15,
        # The secondary home's sale arrives as a cashflow event with this label
        "sell_event_label_match": "mountain house",
        "ss_early_reduction": 0.70,
        "ss_claim_age": 62,
        "spending_phase_slow": 0.85,
        "spending_phase_floor": 0.75,
        "spending_phase_slow_age": 70,
        "spending_phase_floor_age": 80,
        "ira_b_draw_threshold_months": 12,
    },
}


def _make_fire_config(**overrides) -> FireConfig:
    """Create a FireConfig with persona defaults, optionally overridden."""
    config = FireConfig()
    config.id = uuid.uuid4()
    config.date_of_birth = date(1973, 7, 1)
    config.target_retirement_age = 53
    config.life_expectancy = 90
    config.fire_variant = "regular"
    config.safe_withdrawal_rate = 4.0
    config.expected_annual_return = 7.0
    config.expected_inflation_rate = 3.0
    config.target_annual_spending = 15_300_000  # $153,000/yr = $12,750/mo
    config.social_security_monthly = 465_000  # $4,650/mo
    config.social_security_start_age = 67
    config.pension_monthly = None
    config.pension_start_age = None
    config.healthcare_monthly_cost = 60_000  # $600/mo
    config.post_medicare_healthcare_monthly_cost = None
    config.medicare_start_age = 65
    config.rmd_start_age = 73
    config.target_legacy = 0
    config.notes = None
    config.custom_assumptions = deepcopy(BASE_CUSTOM_ASSUMPTIONS)
    config.created_at = datetime(2026, 3, 15, tzinfo=timezone.utc)
    config.updated_at = datetime(2026, 4, 14, tzinfo=timezone.utc)
    for k, v in overrides.items():
        setattr(config, k, v)
    return config


@pytest.fixture
def base_fire_config():
    return _make_fire_config()


# ---------------------------------------------------------------------------
# Scenario overrides (mirror the shapes scripts/seed_scenarios.py produces)
# ---------------------------------------------------------------------------

SCENARIO_KEEP_BOTH = {
    "rental_occupancy_rate": 0.70,
    "sepp": {
        "ira_a_balance": 402_000,
        "ira_b_balance": 165_000,
        "sepp_monthly": 2_100,
        "sepp_start_month": 12,
        "ira_growth_rate": 0.06,
    },
    "miami_sale": {
        "sale_month": 42,
        "mortgage_balance_at_sale": 225_000,
        "monthly_cost": 6_150,
        "post_sale_rent": 2_500,
        "rental_income_lost": 340,
    },
    "rrsp": {
        "monthly_net": 1_900,
        "start_month": 12,
        "total_available": 205_000,
    },
    "str_income": {
        "enabled": True,
        "miami_monthly_averaged": 1_250,
        "park_city_monthly_averaged": 1_300,
        "start_month": 6,
    },
    "mortgage_recast": {
        "enabled": True,
        "month": 6,
        "paydown_amount": 60_000,
        "new_monthly_pi": 2_540,
    },
    "projection": {
        "primary_property_mortgage_pi": 3_100,
    },
}

SCENARIO_PRIMARY_5YR = {
    "rental_occupancy_rate": 0.70,
    "sepp": {
        "ira_a_balance": 402_000,
        "ira_b_balance": 165_000,
        "sepp_monthly": 2_100,
        "sepp_start_month": 12,
        "ira_growth_rate": 0.06,
    },
    "miami_sale": {
        "sale_month": 60,
        "mortgage_balance_at_sale": 270_000,
        "monthly_cost": 6_150,
        "post_sale_rent": 2_500,
        "rental_income_lost": 340,
    },
    "rrsp": {
        "monthly_net": 1_900,
        "start_month": 12,
        "total_available": 205_000,
    },
    "str_income": {
        "enabled": True,
        "miami_monthly_averaged": 1_200,
        "park_city_monthly_averaged": 1_300,
        "start_month": 6,
    },
}

SCENARIO_SELL_INCOME_PROPERTY = {
    "sepp": {
        "ira_a_balance": 402_000,
        "ira_b_balance": 165_000,
        "sepp_monthly": 2_100,
        "sepp_start_month": 12,
        "ira_growth_rate": 0.06,
    },
    "miami_sale": {
        "sale_month": 60,
        "monthly_cost": 6_150,
        "post_sale_rent": 2_500,
        "rental_income_lost": 340,
    },
    "rrsp": {
        "monthly_net": 1_900,
        "start_month": 12,
        "total_available": 205_000,
    },
    "str_income": {
        "enabled": True,
        "miami_monthly_averaged": 1_200,
        "park_city_monthly_averaged": 1_300,
        "start_month": 6,
    },
    "projection": {
        "primary_property_ltcg_rate": 0.15,
        "primary_property_cost_basis": 510_000,
    },
    "sauvie_sale": {
        "enabled": True,
        "sale_month": 3,
        "net_proceeds": 385_000,
        "monthly_cost_saved": 1_050,
        "monthly_income_lost": 3_300,
        "miami_mortgage_paydown": 325_000,
        "park_city_loan_paydown": 0,
    },
}

# Generic property_sales scenario: sell all 3 properties on a timeline, route every
# sale's net proceeds into a taxable brokerage pool compounding at 6.5% real. Uses the
# new config-driven sale mechanism (NOT the legacy single-property keys). Mortgage
# rates/P&I here are TEST values.
SCENARIO_SELL_ALL_THREE = {
    "taxable_pool": {"starting_balance": 0, "return_rate": 0.065},
    "rental_occupancy_rate": 0.70,
    "sepp": {
        "ira_a_balance": 402_000,
        "ira_b_balance": 165_000,
        "sepp_monthly": 2_100,
        "sepp_start_month": 12,
        "ira_growth_rate": 0.06,
    },
    "rrsp": {"monthly_net": 1_900, "start_month": 12, "total_available": 205_000},
    "property_sales": [
        {"key": "coastal_condo", "re_bucket": "primary", "sale_month": 13, "value": 510_000,
         "cost_basis": 510_000, "agent_fee_pct": 0.06, "ltcg_rate": 0.15,
         "state_tax_rate": 0.0, "section_121_exclusion": 0,
         "current_mortgage_balance": 325_000, "mortgage_rate": 0.065, "mortgage_pi": 3_100,
         "monthly_cost": 6_150, "in_base_burn": True, "post_sale_rent": 2_500,
         "monthly_income": 340, "proceeds_to": "taxable"},
        {"key": "mountain_house", "re_bucket": "secondary", "sale_month": 25, "value": 398_000,
         "cost_basis": 385_000, "agent_fee_pct": 0.06, "ltcg_rate": 0.15,
         "state_tax_rate": 0.05, "section_121_exclusion": 0,
         "current_mortgage_balance": 267_000, "mortgage_rate": 0.07, "mortgage_pi": 1_800,
         "monthly_cost": 2_400, "in_base_burn": False, "post_sale_rent": 0,
         "monthly_income": 0, "proceeds_to": "taxable",
         "suppress_cashflow_match": "mountain house"},
        {"key": "river_house", "re_bucket": "income", "sale_month": 39, "value": 396_000,
         "cost_basis": 220_000, "agent_fee_pct": 0.06, "ltcg_rate": 0.15,
         "state_tax_rate": 0.06, "section_121_exclusion": 0,
         "current_mortgage_balance": 0, "mortgage_rate": 0, "mortgage_pi": 0,
         "monthly_cost": 1_050, "in_base_burn": True, "post_sale_rent": 0,
         "monthly_income": 3_300, "occupancy_adjusted": True, "proceeds_to": "taxable"},
    ],
}


def _apply_scenario(config: FireConfig, overrides: dict) -> FireConfig:
    """Deep-merge scenario overrides into a config's custom_assumptions."""
    from copy import deepcopy
    ca = deepcopy(config.custom_assumptions or {})
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(ca.get(key), dict):
            ca[key] = {**ca[key], **val}
        else:
            ca[key] = val
    config.custom_assumptions = ca
    return config


@pytest.fixture
def keep_both_config():
    config = _make_fire_config()
    return _apply_scenario(config, SCENARIO_KEEP_BOTH)


@pytest.fixture
def primary_5yr_config():
    config = _make_fire_config()
    return _apply_scenario(config, SCENARIO_PRIMARY_5YR)


@pytest.fixture
def sell_income_property_config():
    config = _make_fire_config()
    return _apply_scenario(config, SCENARIO_SELL_INCOME_PROPERTY)


# ---------------------------------------------------------------------------
# Net worth breakdown (persona accounts, Apr 14 2026)
# ---------------------------------------------------------------------------

@pytest.fixture
def net_worth_breakdown():
    """Persona net worth breakdown by fire_role grouping."""
    return NetWorthBreakdown(
        liquid=930.00 * 100,  # cash_reserve + operating + tax_free - revolving - short_term
        retirement=8700.00 * 100,  # retirement_core + bridge + supplemental
        real_estate_equity=7200.00 * 100,  # primary_res + primary_mort + sell_cand + sell_with + income_prod
        illiquid_private=1280.00 * 100,  # illiquid_private
        other=650.00 * 100,  # depreciating + speculative
    )


# ---------------------------------------------------------------------------
# Cashflow events (persona, Apr 14 2026)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_cashflow_events():
    """Persona cashflow events as model-like objects."""
    events = []
    raw = [
        ("Coastal Condo Special Assessment", "expense", 450_000, date(2026, 4, 1), 1.0, True, "monthly", date(2026, 8, 31)),
        ("Boat Payment", "expense", 500_000, date(2026, 4, 1), 1.0, False, None, None),
        ("State Unemployment", "income", 120_000, date(2026, 5, 1), 0.9, True, "monthly", date(2026, 7, 31)),
        ("Remaining Severance", "income", 3_000_000, date(2026, 4, 30), 1.0, False, None, None),
        ("2025 STR Tax Refund", "income", 4_000_000, date(2026, 7, 1), 0.9, False, None, None),
        ("Mountain House Sale Proceeds", "income", 10_400_000, date(2026, 10, 15), 0.85, False, None, None),
        ("Overseas Payout", "income", 11_400_000, date(2027, 8, 1), 0.5, False, None, None),
        ("Auto loan payoff savings", "income", 87_000, date(2027, 7, 1), 1.0, True, "monthly", None),
        ("Startup A vests (1.5x)", "income", 7_800_000, date(2031, 4, 1), 0.7, False, None, None),
        ("Startup B vests (1.5x)", "income", 2_200_000, date(2032, 4, 1), 0.6, False, None, None),
        ("Startup C vests (2x)", "income", 4_500_000, date(2033, 4, 1), 0.5, False, None, None),
    ]
    for name, etype, amount, dt, prob, recurring, recurrence, end_date in raw:
        e = MagicMock(spec=CashflowEvent)
        e.name = name
        e.event_type = etype
        e.amount_cents = amount
        e.date = dt
        e.probability = prob
        e.is_recurring = recurring
        e.recurrence = recurrence
        e.end_date = end_date
        e.status = "planned"
        events.append(e)
    return events


# ---------------------------------------------------------------------------
# Account mocks (for RE equity partition in project_wealth_pools)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_accounts():
    """Persona accounts with fire_role for RE equity partitioning."""
    accounts = []
    # (fire_role, current_balance_cents, is_asset, include_in_net_worth)
    raw = [
        ("sell_candidate", 39_800_000, True, True),        # Mountain House (secondary)
        ("sell_with_property", 26_700_000, False, True),    # Mountain House mortgage (liability)
        ("primary_residence", 51_000_000, True, True),      # Coastal Condo (primary)
        ("primary_mortgage", 32_500_000, False, True),      # Coastal Condo mortgage (liability)
        ("income_producing", 39_600_000, True, True),       # River House (income property)
    ]
    for role, balance, is_asset, include in raw:
        a = MagicMock()
        a.fire_role = role
        a.current_balance = balance
        a.is_asset = is_asset
        a.include_in_net_worth = include
        accounts.append(a)
    return accounts


# ---------------------------------------------------------------------------
# Income sources (persona, Apr 14 2026)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_income_sources():
    """Persona active income sources as model-like objects."""
    sources = []
    # (name, income_type, annual_amount_cents, start_date, end_date, is_active)
    raw = [
        ("Employer Salary (ended)", IncomeType.SALARY, 25_000_000, None, date(2026, 3, 24), True),
        ("Employer Severance", IncomeType.OTHER, 8_200_000, None, date(2026, 4, 30), True),
        ("State Unemployment", IncomeType.OTHER, 1_440_000, None, date(2026, 6, 30), True),
        ("Coastal Condo Rental", IncomeType.RENTAL, 410_000, None, None, True),
        ("Mountain House Rental", IncomeType.RENTAL, 870_000, None, date(2026, 6, 30), True),
        ("River House Rental", IncomeType.RENTAL, 3_960_000, None, None, True),
    ]
    for name, itype, amount, start, end, active in raw:
        s = MagicMock(spec=IncomeSource)
        s.name = name
        s.income_type = itype
        s.annual_amount = amount
        s.frequency = "monthly"
        s.start_date = start
        s.end_date = end
        s.is_active = active
        s.growth_rate = None
        s.is_taxable = True
        sources.append(s)
    return sources
