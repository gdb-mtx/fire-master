"""Tax-Aware FIRE Engine — brackets, withdrawal sequencing, Roth conversions, ACA."""

from __future__ import annotations

import logging
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.enums import AccountType
from app.models.fire_config import FireConfig
from app.models.income_source import IncomeSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — 2026 estimated federal brackets (adjustable via config)
# ---------------------------------------------------------------------------

class FilingStatus(str, Enum):
    SINGLE = "single"
    MARRIED_FILING_JOINTLY = "married_filing_jointly"


class TaxTreatment(str, Enum):
    TAX_DEFERRED = "tax_deferred"        # 401k, traditional IRA
    TAX_FREE = "tax_free"                # Roth IRA, Roth 401k, HSA (medical)
    TAXABLE = "taxable"                  # Brokerage, crypto
    ALREADY_TAXED = "already_taxed"      # Checking, savings (principal)


# 2026 estimated brackets — user can override via fire_config.custom_assumptions
DEFAULT_BRACKETS: dict[str, list[dict]] = {
    "single": [
        {"rate": 0.10, "up_to": 11_925},
        {"rate": 0.12, "up_to": 48_475},
        {"rate": 0.22, "up_to": 103_350},
        {"rate": 0.24, "up_to": 197_300},
        {"rate": 0.32, "up_to": 250_525},
        {"rate": 0.35, "up_to": 626_350},
        {"rate": 0.37, "up_to": float("inf")},
    ],
    "married_filing_jointly": [
        {"rate": 0.10, "up_to": 23_850},
        {"rate": 0.12, "up_to": 96_950},
        {"rate": 0.22, "up_to": 206_700},
        {"rate": 0.24, "up_to": 394_600},
        {"rate": 0.32, "up_to": 501_050},
        {"rate": 0.35, "up_to": 751_600},
        {"rate": 0.37, "up_to": float("inf")},
    ],
}

DEFAULT_STANDARD_DEDUCTION = {
    "single": 15_700,
    "married_filing_jointly": 31_400,
}

# Long-term capital gains brackets (2026 estimates)
LTCG_BRACKETS: dict[str, list[dict]] = {
    "single": [
        {"rate": 0.00, "up_to": 48_350},
        {"rate": 0.15, "up_to": 533_400},
        {"rate": 0.20, "up_to": float("inf")},
    ],
    "married_filing_jointly": [
        {"rate": 0.00, "up_to": 96_700},
        {"rate": 0.15, "up_to": 600_050},
        {"rate": 0.20, "up_to": float("inf")},
    ],
}

# FICA (2026 estimates)
SOCIAL_SECURITY_RATE = 0.062
SOCIAL_SECURITY_WAGE_BASE = 176_100
MEDICARE_RATE = 0.0145
MEDICARE_SURTAX_RATE = 0.009  # additional on income > $200K single / $250K MFJ
MEDICARE_SURTAX_THRESHOLD = {"single": 200_000, "married_filing_jointly": 250_000}

# ACA Federal Poverty Level (2026 estimates, continental US)
FPL_BASE = 15_060  # 1-person household
FPL_PER_PERSON = 5_380

# NIIT (Net Investment Income Tax)
NIIT_RATE = 0.038
NIIT_THRESHOLD = {"single": 200_000, "married_filing_jointly": 250_000}

# IRS Uniform Lifetime Table for RMD divisors (updated for SECURE 2.0, effective 2024+)
# Source: IRS Publication 590-B, Table III
_RMD_DIVISORS: dict[int, float] = {
    72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9,
    78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7,
    84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9,
    90: 12.2, 91: 11.5, 92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9,
    96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4, 101: 6.0,
    102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3, 107: 4.1,
    108: 3.9, 109: 3.7, 110: 3.5, 111: 3.4, 112: 3.3, 113: 3.1,
    114: 3.0, 115: 2.9, 116: 2.8, 117: 2.7, 118: 2.5, 119: 2.3,
    120: 2.0,
}


def _get_rmd_divisor(age: int) -> float:
    """Return the IRS Uniform Lifetime Table divisor for the given age."""
    if age < 72:
        return 27.4  # shouldn't be called, but safe fallback
    if age > 120:
        return 2.0
    return _RMD_DIVISORS.get(age, max(2.0, 27.4 - (age - 72) * 0.5))


def _year_fraction(year: int, start: date | None = None, end: date | None = None) -> float:
    """Fraction of a calendar year covered by [start, end] (inclusive, day-based)."""
    year_start, year_end = date(year, 1, 1), date(year, 12, 31)
    s = max(start or year_start, year_start)
    e = min(end or year_end, year_end)
    if s > e:
        return 0.0
    return ((e - s).days + 1) / ((year_end - year_start).days + 1)


def _prorated_annual_for_year(source, year: int, end_override: date | None = None) -> float:
    """A source's dollar contribution to one calendar year, day-prorated.

    A salary that ended March 24 contributed ~3 months of income this year —
    not its full annual amount (the old behavior, which overstated headline
    income), and not zero (which would understate it). Sources without
    start/end dates count in full. `end_override` caps the window further
    (e.g. earned income stops at the retirement date).
    """
    end = source.end_date
    if end_override is not None and (end is None or end_override < end):
        end = end_override
    return (source.annual_amount / 100) * _year_fraction(year, source.start_date, end)


# ---------------------------------------------------------------------------
# Data classes for results
# ---------------------------------------------------------------------------

@dataclass
class TaxBreakdown:
    """Bracket-by-bracket federal tax detail."""
    taxable_income: float
    brackets: list[dict]  # [{rate, income_in_bracket, tax_in_bracket}]
    total_federal_tax: float
    effective_rate: float
    marginal_rate: float


@dataclass
class BracketRoom:
    """How much room before hitting the next bracket."""
    current_bracket_rate: float
    next_bracket_rate: float | None
    room_in_current: float  # dollars until next bracket
    room_to_fill_bracket: float  # same as room_in_current (alias for clarity)
    standard_deduction: float


@dataclass
class AccountsByTaxTreatment:
    """Accounts grouped by tax treatment."""
    tax_deferred: list[Account] = field(default_factory=list)  # 401k, IRA
    tax_free: list[Account] = field(default_factory=list)      # Roth, HSA
    taxable: list[Account] = field(default_factory=list)       # Brokerage, crypto
    already_taxed: list[Account] = field(default_factory=list) # Checking, savings

    @property
    def tax_deferred_balance(self) -> float:
        return sum(a.current_balance for a in self.tax_deferred) / 100

    @property
    def tax_free_balance(self) -> float:
        return sum(a.current_balance for a in self.tax_free) / 100

    @property
    def taxable_balance(self) -> float:
        return sum(a.current_balance for a in self.taxable) / 100

    @property
    def already_taxed_balance(self) -> float:
        return sum(a.current_balance for a in self.already_taxed) / 100


@dataclass
class WithdrawalYearPlan:
    """One year of the withdrawal sequence."""
    year: int
    age: float
    from_taxable: float
    from_deferred: float
    from_roth: float
    from_cash: float
    roth_conversion: float
    total_income: float
    ordinary_income: float
    capital_gains_income: float
    federal_tax: float
    state_tax: float
    fica_tax: float
    total_tax: float
    effective_rate: float
    after_tax_income: float
    magi: float


@dataclass
class WithdrawalPlan:
    """Multi-year optimized withdrawal sequence."""
    years: list[WithdrawalYearPlan]
    total_tax_paid: float
    average_effective_rate: float
    total_withdrawn: float


@dataclass
class RothConversionYear:
    """One year of the Roth conversion schedule."""
    year: int
    age: float
    baseline_income: float
    conversion_amount: float
    total_taxable_income: float
    tax_on_conversion: float
    cumulative_converted: float
    magi_after_conversion: float
    bracket_filled_to: float  # the marginal rate we're filling up to


@dataclass
class RothConversionPlan:
    """Full Roth conversion ladder recommendation."""
    years: list[RothConversionYear]
    total_converted: float
    total_tax_paid: float
    estimated_tax_saved: float  # vs. withdrawing at assumed_rmd_marginal_rate later
    target_bracket_rate: float
    conversion_window: str  # e.g., "age 52-67"
    # The savings estimate is converted × this rate − tax paid; surfaced so
    # the UI can disclose the assumption (config: tax.assumed_rmd_marginal_rate).
    assumed_rmd_marginal_rate: float = 0.24


@dataclass
class ACAResult:
    """ACA subsidy eligibility analysis."""
    magi: float
    household_size: int
    fpl: float
    fpl_percentage: float
    subsidy_eligible: bool
    estimated_monthly_premium: float
    estimated_monthly_subsidy: float
    estimated_net_monthly_cost: float
    cliff_distance: float  # dollars until subsidy cliff
    cliff_warning: bool


# ---------------------------------------------------------------------------
# Tax Engine
# ---------------------------------------------------------------------------

class TaxEngine:
    """Tax calculation, withdrawal sequencing, and Roth conversion planning."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # -----------------------------------------------------------------------
    # Tax config helpers
    # -----------------------------------------------------------------------

    def _get_tax_config(self, config: FireConfig) -> dict:
        """Extract tax configuration from fire_config.custom_assumptions."""
        defaults = {
            "filing_status": "single",
            "state": "UT",
            "state_tax_rate": config.state_tax_rate or 4.65,
            "brackets": None,  # will use DEFAULT_BRACKETS
            "standard_deduction": None,  # will use DEFAULT_STANDARD_DEDUCTION
            "household_size": 1,
            "cost_basis_pct": 0.60,  # estimated % of taxable balance that is cost basis (not taxed)
            # Assumed marginal rate on future RMDs, used to estimate Roth
            # conversion savings (at 73+ with SS income, 22-24% is typical).
            "assumed_rmd_marginal_rate": 0.24,
            # REAL yield on the cash bucket in the withdrawal plan. Default 0:
            # cash holds purchasing power (HYSA ≈ inflation). Positive only if
            # cash is parked meaningfully above inflation.
            "cash_yield_rate": 0.0,
        }
        if config.custom_assumptions and "tax" in config.custom_assumptions:
            defaults.update(config.custom_assumptions["tax"])
        return defaults

    def _get_brackets(self, tax_config: dict) -> list[dict]:
        """Get federal brackets, from config override or defaults."""
        if tax_config.get("brackets"):
            return tax_config["brackets"]
        status = tax_config.get("filing_status", "single")
        return DEFAULT_BRACKETS.get(status, DEFAULT_BRACKETS["single"])

    def _get_standard_deduction(self, tax_config: dict) -> float:
        """Get standard deduction for filing status."""
        if tax_config.get("standard_deduction"):
            return tax_config["standard_deduction"]
        status = tax_config.get("filing_status", "single")
        return DEFAULT_STANDARD_DEDUCTION.get(status, DEFAULT_STANDARD_DEDUCTION["single"])

    # -----------------------------------------------------------------------
    # Core tax computations (pure functions, no DB)
    # -----------------------------------------------------------------------

    def compute_federal_tax(
        self, taxable_income: float, filing_status: str = "single",
        brackets: list[dict] | None = None,
    ) -> TaxBreakdown:
        """Compute federal income tax bracket-by-bracket."""
        if brackets is None:
            brackets = DEFAULT_BRACKETS.get(filing_status, DEFAULT_BRACKETS["single"])

        result_brackets = []
        remaining = max(0, taxable_income)
        total_tax = 0.0
        prev_up_to = 0
        marginal_rate = brackets[0]["rate"]

        for bracket in brackets:
            bracket_width = bracket["up_to"] - prev_up_to
            income_in_bracket = min(remaining, bracket_width)

            if income_in_bracket > 0:
                tax_in_bracket = income_in_bracket * bracket["rate"]
                result_brackets.append({
                    "rate": bracket["rate"],
                    "income_in_bracket": round(income_in_bracket, 2),
                    "tax_in_bracket": round(tax_in_bracket, 2),
                    "bracket_floor": prev_up_to,
                    "bracket_ceiling": bracket["up_to"] if bracket["up_to"] != float("inf") else None,
                })
                total_tax += tax_in_bracket
                marginal_rate = bracket["rate"]

            remaining -= income_in_bracket
            prev_up_to = bracket["up_to"]
            if remaining <= 0:
                break

        effective = total_tax / taxable_income if taxable_income > 0 else 0

        return TaxBreakdown(
            taxable_income=round(taxable_income, 2),
            brackets=result_brackets,
            total_federal_tax=round(total_tax, 2),
            effective_rate=round(effective, 4),
            marginal_rate=marginal_rate,
        )

    def compute_state_tax(self, taxable_income: float, state_rate: float) -> float:
        """Compute state income tax (flat rate, e.g., Utah 4.65%)."""
        return round(max(0, taxable_income) * state_rate / 100, 2)

    def compute_effective_rate(
        self, gross_income: float, federal_tax: float, state_tax: float,
        fica_tax: float = 0,
    ) -> float:
        """Total tax / gross income."""
        if gross_income <= 0:
            return 0.0
        return round((federal_tax + state_tax + fica_tax) / gross_income, 4)

    def get_bracket_room(
        self, taxable_income: float, filing_status: str = "single",
        brackets: list[dict] | None = None,
    ) -> BracketRoom:
        """How much room before hitting the next bracket (key for Roth conversion)."""
        if brackets is None:
            brackets = DEFAULT_BRACKETS.get(filing_status, DEFAULT_BRACKETS["single"])

        std_ded = DEFAULT_STANDARD_DEDUCTION.get(filing_status, DEFAULT_STANDARD_DEDUCTION["single"])
        prev_up_to = 0
        current_rate = brackets[0]["rate"]
        next_rate = brackets[1]["rate"] if len(brackets) > 1 else None

        for i, bracket in enumerate(brackets):
            if taxable_income <= bracket["up_to"]:
                current_rate = bracket["rate"]
                room = bracket["up_to"] - taxable_income
                if bracket["up_to"] == float("inf"):
                    room = 0  # already in top bracket
                next_rate = brackets[i + 1]["rate"] if i + 1 < len(brackets) else None
                return BracketRoom(
                    current_bracket_rate=current_rate,
                    next_bracket_rate=next_rate,
                    room_in_current=round(room, 2),
                    room_to_fill_bracket=round(room, 2),
                    standard_deduction=std_ded,
                )
            prev_up_to = bracket["up_to"]

        # Top bracket
        return BracketRoom(
            current_bracket_rate=brackets[-1]["rate"],
            next_bracket_rate=None,
            room_in_current=0,
            room_to_fill_bracket=0,
            standard_deduction=std_ded,
        )

    def compute_capital_gains_tax(
        self, gains: float, ordinary_income: float,
        filing_status: str = "single", is_long_term: bool = True,
    ) -> float:
        """Compute capital gains tax. LTCG rates depend on total income."""
        if not is_long_term:
            # Short-term = ordinary income rates
            breakdown = self.compute_federal_tax(ordinary_income + gains, filing_status)
            base = self.compute_federal_tax(ordinary_income, filing_status)
            return round(breakdown.total_federal_tax - base.total_federal_tax, 2)

        # Long-term capital gains use special brackets based on total income
        ltcg_brackets = LTCG_BRACKETS.get(filing_status, LTCG_BRACKETS["single"])
        total_income = ordinary_income + gains

        # Find where gains start in the LTCG bracket system
        tax = 0.0
        remaining_gains = gains
        prev_up_to = 0

        for bracket in ltcg_brackets:
            if ordinary_income >= bracket["up_to"]:
                prev_up_to = bracket["up_to"]
                continue

            # How much of this bracket is available after ordinary income
            bracket_start = max(prev_up_to, ordinary_income)
            bracket_room = bracket["up_to"] - bracket_start
            gains_in_bracket = min(remaining_gains, bracket_room)

            if gains_in_bracket > 0:
                tax += gains_in_bracket * bracket["rate"]
                remaining_gains -= gains_in_bracket

            prev_up_to = bracket["up_to"]
            if remaining_gains <= 0:
                break

        return round(tax, 2)

    def compute_fica(
        self, earned_income: float, filing_status: str = "single",
    ) -> float:
        """Social Security + Medicare tax (only on earned income, not withdrawals)."""
        if earned_income <= 0:
            return 0.0

        # Social Security: 6.2% up to wage base
        ss_taxable = min(earned_income, SOCIAL_SECURITY_WAGE_BASE)
        ss_tax = ss_taxable * SOCIAL_SECURITY_RATE

        # Medicare: 1.45% on all, plus 0.9% surtax above threshold
        medicare_tax = earned_income * MEDICARE_RATE
        surtax_threshold = MEDICARE_SURTAX_THRESHOLD.get(filing_status, 200_000)
        if earned_income > surtax_threshold:
            medicare_tax += (earned_income - surtax_threshold) * MEDICARE_SURTAX_RATE

        return round(ss_tax + medicare_tax, 2)

    def compute_magi(
        self,
        earned_income: float = 0,
        social_security: float = 0,
        pension: float = 0,
        deferred_withdrawals: float = 0,
        roth_conversions: float = 0,
        capital_gains: float = 0,
        rental_income: float = 0,
        dividend_income: float = 0,
    ) -> float:
        """Compute Modified Adjusted Gross Income for ACA/IRMAA.

        MAGI = AGI + tax-exempt interest + excluded foreign income
        For most people: MAGI ≈ all income except Roth withdrawals.
        """
        # Taxable portion of Social Security (simplified: 85% if above threshold)
        ss_taxable = social_security * 0.85 if social_security > 0 else 0

        return (
            earned_income + ss_taxable + pension + deferred_withdrawals
            + roth_conversions + capital_gains + rental_income + dividend_income
        )

    # -----------------------------------------------------------------------
    # ACA Subsidy Calculator
    # -----------------------------------------------------------------------

    def compute_aca_eligibility(
        self, magi: float, household_size: int = 1, age: float = 55,
    ) -> ACAResult:
        """Compute ACA marketplace subsidy eligibility."""
        fpl = FPL_BASE + FPL_PER_PERSON * max(0, household_size - 1)
        fpl_pct = magi / fpl * 100 if fpl > 0 else 999

        # Subsidy eligibility thresholds
        # Below 150% FPL: maximum subsidies
        # 150-400% FPL: sliding scale
        # Above 400% FPL: limited subsidies (ARP extended, but reduced)
        subsidy_eligible = fpl_pct <= 400

        # Rough premium estimate (varies hugely by state/age/plan)
        # Benchmark: Silver plan, 55-year-old in Utah ~$650/mo (2026 est)
        base_premium = 550 + (age - 40) * 10  # rough age adjustment
        base_premium = max(300, min(base_premium, 1200))

        if fpl_pct <= 150:
            # Nearly free coverage
            subsidy = base_premium * 0.95
        elif fpl_pct <= 200:
            subsidy = base_premium * 0.80
        elif fpl_pct <= 250:
            subsidy = base_premium * 0.65
        elif fpl_pct <= 300:
            subsidy = base_premium * 0.50
        elif fpl_pct <= 400:
            subsidy = base_premium * 0.30
        else:
            subsidy = 0

        net_cost = base_premium - subsidy

        # Cliff: 400% FPL
        cliff_income = fpl * 4
        cliff_distance = cliff_income - magi

        return ACAResult(
            magi=round(magi, 2),
            household_size=household_size,
            fpl=fpl,
            fpl_percentage=round(fpl_pct, 1),
            subsidy_eligible=subsidy_eligible,
            estimated_monthly_premium=round(base_premium, 2),
            estimated_monthly_subsidy=round(subsidy, 2),
            estimated_net_monthly_cost=round(net_cost, 2),
            cliff_distance=round(cliff_distance, 2),
            cliff_warning=0 < cliff_distance < 5_000,
        )

    # -----------------------------------------------------------------------
    # Account tax classification
    # -----------------------------------------------------------------------

    @staticmethod
    def classify_account(account: Account) -> TaxTreatment:
        """Map an account's type to its tax treatment."""
        # Check for override in custom_data
        if account.custom_data and "tax_treatment" in account.custom_data:
            try:
                return TaxTreatment(account.custom_data["tax_treatment"])
            except ValueError:
                pass

        mapping = {
            AccountType.FOUR_OH_ONE_K: TaxTreatment.TAX_DEFERRED,
            AccountType.IRA: TaxTreatment.TAX_DEFERRED,
            AccountType.ROTH_IRA: TaxTreatment.TAX_FREE,
            AccountType.HSA: TaxTreatment.TAX_FREE,
            AccountType.TAXABLE: TaxTreatment.TAXABLE,
            AccountType.CRYPTO: TaxTreatment.TAXABLE,
            AccountType.REAL_ESTATE: TaxTreatment.TAXABLE,
            AccountType.PRIVATE: TaxTreatment.TAXABLE,
            AccountType.CHECKING: TaxTreatment.ALREADY_TAXED,
            AccountType.SAVINGS: TaxTreatment.ALREADY_TAXED,
            AccountType.CREDIT_CARD: TaxTreatment.ALREADY_TAXED,
            AccountType.VEHICLE: TaxTreatment.ALREADY_TAXED,
            AccountType.OTHER: TaxTreatment.ALREADY_TAXED,
        }
        return mapping.get(account.account_type, TaxTreatment.ALREADY_TAXED)

    async def get_accounts_by_tax_treatment(self) -> AccountsByTaxTreatment:
        """Group all accounts by tax treatment."""
        result = await self.db.execute(
            select(Account).where(
                Account.include_in_net_worth == True,
                Account.is_asset == True,
            )
        )
        accounts = result.scalars().all()

        grouped = AccountsByTaxTreatment()
        for acct in accounts:
            treatment = self.classify_account(acct)
            if treatment == TaxTreatment.TAX_DEFERRED:
                grouped.tax_deferred.append(acct)
            elif treatment == TaxTreatment.TAX_FREE:
                grouped.tax_free.append(acct)
            elif treatment == TaxTreatment.TAXABLE:
                grouped.taxable.append(acct)
            else:
                grouped.already_taxed.append(acct)

        return grouped

    # -----------------------------------------------------------------------
    # Withdrawal sequencing optimizer
    # -----------------------------------------------------------------------

    async def optimize_withdrawal_sequence(
        self,
        years: int = 10,
        target_bracket_rate: float = 0.22,
        roth_conversions_enabled: bool = True,
        scenario_id: uuid_mod.UUID | None = None,
    ) -> WithdrawalPlan:
        """Produce a year-by-year tax-aware withdrawal plan.

        Withdrawals follow a fixed order each year:
        1. Taxable accounts first (only the gains portion taxed, at LTCG rates)
        2. Tax-deferred next (ordinary income)
        3. Tax-free (Roth) last (preserve tax-free growth)
        4. Cash as absolute last resort

        On top of the fixed order, two tax-aware adjustments per year:
        - RMDs force a minimum deferred withdrawal at rmd_start_age+; any
          excess above the spending need is redeposited into taxable.
        - During the golden window (retired, pre-SS, pre-RMD), tax-deferred
          dollars are Roth-converted up to the target bracket ceiling
          (bracket-fill), controlled by roth_conversions_enabled.

        REAL-TERMS frame (today's dollars), consistent with the projection
        engine and Monte Carlo: spending stays flat (constant purchasing
        power), balances compound at the REAL return, and the tax brackets /
        standard deduction stay frozen at today's levels — which is the
        correct real-terms model because the IRS indexes them to inflation
        annually (a nominal simulation against frozen brackets manufactures
        phantom bracket creep).

        Simplification: taxes are reported but not deducted from balances
        (the plan shows the tax cost of the sequence, not net-of-tax wealth).

        IncomeSource.is_taxable gates the tax computation: a source flagged
        non-taxable (or entered net-of-tax — flag it False) still offsets
        spending need but contributes nothing to ordinary income, FICA, or
        MAGI. Enter gross amounts with is_taxable=True to have the engine
        tax them.

        Config comes from get_effective_config (active scenario merged, or
        an explicit scenario_id) — same as the projection engines.
        """
        from app.engines.fire_projections import FireProjectionsEngine

        fire_engine = FireProjectionsEngine(self.db)
        config = await fire_engine.get_effective_config(scenario_id)
        tax_config = self._get_tax_config(config)
        filing_status = tax_config["filing_status"]
        state_rate = tax_config["state_tax_rate"]
        std_deduction = self._get_standard_deduction(tax_config)
        brackets = self._get_brackets(tax_config)
        cost_basis_pct = tax_config.get("cost_basis_pct", 0.60)

        accounts = await self.get_accounts_by_tax_treatment()
        income_sources = await self._get_active_income_sources()
        retirement_date = self._get_retirement_date(config)

        annual_spending_cents = config.target_annual_spending or 12_000_000  # default $120K
        annual_need = annual_spending_cents / 100
        # REAL yield on cash — default 0 (cash holds purchasing power at
        # best; set positive for HYSA-parked cash, negative for checking).
        cash_yield = tax_config.get("cash_yield_rate", 0.0)

        today = date.today()
        year_plans: list[WithdrawalYearPlan] = []
        total_tax = 0.0
        total_withdrawn = 0.0
        total_gross_all_years = 0.0

        # Track account balances across years
        deferred_balance = accounts.tax_deferred_balance
        roth_balance = accounts.tax_free_balance
        taxable_balance = accounts.taxable_balance
        cash_balance = accounts.already_taxed_balance

        # REAL growth rate for invested balances: (1+nominal)/(1+inflation) − 1
        inflation = config.expected_inflation_rate / 100
        annual_return = (1 + config.expected_annual_return / 100) / (1 + inflation) - 1

        for yr in range(years):
            current_year = today.year + yr
            current_date = date(current_year, 1, 1)
            age = self._compute_age(config, current_date)
            is_retired = retirement_date and current_date >= retirement_date

            # Spending: flat in real terms (constant purchasing power)
            spending_need = annual_need
            if (
                is_retired
                and config.healthcare_monthly_cost
                and age < (config.medicare_start_age or 65)
            ):
                spending_need += config.healthcare_monthly_cost * 12 / 100

            # Income from sources (SS, pension, rental, etc.). Each type
            # bucket tracks ALL income (it offsets spending need either way)
            # plus a taxable portion gated by is_taxable — a source flagged
            # non-taxable (or entered net-of-tax) covers spending but never
            # enters the tax computation.
            earned_income = 0.0
            ss_income = 0.0
            pension_income = 0.0
            other_income = 0.0
            taxable_earned = 0.0
            taxable_ss = 0.0
            taxable_pension = 0.0
            taxable_other = 0.0

            for src in income_sources:
                # Day-prorated contribution for this calendar year (an ended
                # salary counts its partial year, not the full annual amount).
                # Earned income additionally stops at the retirement date even
                # without an explicit end_date.
                end_override = None
                if src.income_type.value in ("salary", "bonus", "side_hustle"):
                    end_override = retirement_date
                annual = _prorated_annual_for_year(src, current_year, end_override)
                if annual <= 0:
                    continue
                if src.growth_rate and yr > 0:
                    # growth_rate is a NOMINAL raise — deflate to real
                    real_growth = (1 + src.growth_rate / 100) / (1 + inflation) - 1
                    annual *= (1 + real_growth) ** yr

                if src.income_type.value == "social_security":
                    ss_income += annual
                    taxable_ss += annual if src.is_taxable else 0.0
                elif src.income_type.value == "pension":
                    pension_income += annual
                    taxable_pension += annual if src.is_taxable else 0.0
                elif src.income_type.value in ("salary", "bonus", "side_hustle"):
                    earned_income += annual
                    taxable_earned += annual if src.is_taxable else 0.0
                else:
                    other_income += annual
                    taxable_other += annual if src.is_taxable else 0.0

            # Social Security from config (if not in income sources) —
            # prorated in its first year (a mid-year start is half a year)
            if config.social_security_monthly and config.date_of_birth and ss_income == 0:
                ss_start = config.date_of_birth + relativedelta(years=config.social_security_start_age)
                ss_income = (config.social_security_monthly * 12 / 100) * _year_fraction(
                    current_year, start=ss_start)
                taxable_ss = ss_income

            # Pension from config — same first-year proration
            if config.pension_monthly and config.pension_start_age and config.date_of_birth and pension_income == 0:
                pension_start = config.date_of_birth + relativedelta(years=config.pension_start_age)
                pension_income = (config.pension_monthly * 12 / 100) * _year_fraction(
                    current_year, start=pension_start)
                taxable_pension = pension_income

            total_non_withdrawal_income = earned_income + ss_income + pension_income + other_income
            withdrawal_needed = max(0, spending_need - total_non_withdrawal_income)

            # Withdrawal sequencing — runs whenever spending exceeds income,
            # pre-retirement included (a shortfall is a drawdown either way;
            # previously pre-retirement years silently took no withdrawals).
            from_taxable = 0.0
            from_deferred = 0.0
            from_roth = 0.0
            from_cash = 0.0
            capital_gains = 0.0
            remaining_need = withdrawal_needed

            if remaining_need > 0:
                # Step 1: Draw from taxable first (preferential capital gains rates)
                if taxable_balance > 0 and remaining_need > 0:
                    draw = min(remaining_need, taxable_balance)
                    from_taxable = draw
                    # Only the gains portion is taxable
                    capital_gains = draw * (1 - cost_basis_pct)
                    taxable_balance -= draw
                    remaining_need -= draw

                # Step 2: Draw from tax-deferred (ordinary income, fill lower brackets)
                if deferred_balance > 0 and remaining_need > 0:
                    draw = min(remaining_need, deferred_balance)
                    from_deferred = draw
                    deferred_balance -= draw
                    remaining_need -= draw

                # Step 3: Draw from Roth (tax-free, last resort)
                if roth_balance > 0 and remaining_need > 0:
                    draw = min(remaining_need, roth_balance)
                    from_roth = draw
                    roth_balance -= draw
                    remaining_need -= draw

                # Step 4: Cash/savings as absolute last resort
                if cash_balance > 0 and remaining_need > 0:
                    draw = min(remaining_need, cash_balance)
                    from_cash = draw
                    cash_balance -= draw
                    remaining_need -= draw

            # RMD check (age 73+): force minimum deferred withdrawal
            roth_conversion = 0.0
            if age >= config.rmd_start_age and deferred_balance > 0:
                rmd_pct = 1 / _get_rmd_divisor(int(age))
                rmd_amount = deferred_balance * rmd_pct
                if rmd_amount > from_deferred:
                    extra_rmd = rmd_amount - from_deferred
                    from_deferred += extra_rmd
                    deferred_balance -= extra_rmd
                    # The forced excess isn't spent — it lands in taxable
                    # (gross redeposit; taxes are reported, not deducted).
                    taxable_balance += extra_rmd

            # Golden-window Roth conversion: retired, pre-SS, pre-RMD — fill
            # ordinary income up to the target bracket ceiling. Conversion is
            # taxed as ordinary income this year but moves dollars out of the
            # future-RMD pool into tax-free growth.
            if (
                roth_conversions_enabled
                and is_retired
                and ss_income == 0
                and age < config.rmd_start_age
                and deferred_balance > 0
            ):
                target_ceiling = 0.0
                for bracket in brackets:
                    if bracket["rate"] <= target_bracket_rate:
                        target_ceiling = bracket["up_to"]
                ordinary_so_far = (
                    taxable_earned + taxable_ss * 0.85 + taxable_pension
                    + from_deferred + taxable_other
                )
                # Fill so POST-DEDUCTION taxable income lands exactly at the
                # bracket ceiling — the conversion also absorbs any unused
                # standard deduction.
                room = max(0.0, target_ceiling + std_deduction - ordinary_so_far)
                roth_conversion = min(room, deferred_balance)
                deferred_balance -= roth_conversion
                roth_balance += roth_conversion

            # Compute taxes (Roth conversion counts as ordinary income) —
            # only the is_taxable portion of each income bucket enters.
            ordinary_income = taxable_earned + taxable_ss * 0.85 + taxable_pension + from_deferred + roth_conversion + taxable_other
            taxable_income = max(0, ordinary_income - std_deduction)

            federal_breakdown = self.compute_federal_tax(taxable_income, filing_status, brackets)
            cg_tax = self.compute_capital_gains_tax(capital_gains, taxable_income, filing_status)
            federal_tax = federal_breakdown.total_federal_tax + cg_tax
            state_tax = self.compute_state_tax(ordinary_income + capital_gains - std_deduction, state_rate)
            fica_tax = self.compute_fica(taxable_earned, filing_status)
            total_tax_year = federal_tax + state_tax + fica_tax

            magi = self.compute_magi(
                earned_income=taxable_earned,
                social_security=taxable_ss,
                pension=taxable_pension,
                deferred_withdrawals=from_deferred,
                roth_conversions=roth_conversion,
                capital_gains=capital_gains,
                dividend_income=taxable_other,
            )

            total_gross = (
                total_non_withdrawal_income + from_taxable + from_deferred
                + from_roth + from_cash
            )
            effective = total_tax_year / total_gross if total_gross > 0 else 0

            year_plans.append(WithdrawalYearPlan(
                year=current_year,
                age=round(age, 1),
                from_taxable=round(from_taxable, 2),
                from_deferred=round(from_deferred, 2),
                from_roth=round(from_roth, 2),
                from_cash=round(from_cash, 2),
                roth_conversion=round(roth_conversion, 2),
                total_income=round(total_gross, 2),
                ordinary_income=round(ordinary_income, 2),
                capital_gains_income=round(capital_gains, 2),
                federal_tax=round(federal_tax, 2),
                state_tax=round(state_tax, 2),
                fica_tax=round(fica_tax, 2),
                total_tax=round(total_tax_year, 2),
                effective_rate=round(effective, 4),
                after_tax_income=round(total_gross - total_tax_year, 2),
                magi=round(magi, 2),
            ))

            total_tax += total_tax_year
            total_withdrawn += from_taxable + from_deferred + from_roth
            total_gross_all_years += total_gross

            # Grow remaining balances (cash at its own yield, not market return)
            deferred_balance *= (1 + annual_return)
            roth_balance *= (1 + annual_return)
            taxable_balance *= (1 + annual_return)
            cash_balance *= (1 + cash_yield)

        # Effective rate over GROSS income (same denominator as each year's
        # effective_rate) — dividing by withdrawals alone let income-year tax
        # produce impossible >100% rates (fire-master#4).
        avg_rate = total_tax / total_gross_all_years if total_gross_all_years > 0 else 0

        return WithdrawalPlan(
            years=year_plans,
            total_tax_paid=round(total_tax, 2),
            average_effective_rate=round(avg_rate, 4),
            total_withdrawn=round(total_withdrawn, 2),
        )

    # -----------------------------------------------------------------------
    # Roth conversion ladder planner
    # -----------------------------------------------------------------------

    async def plan_roth_conversions(
        self,
        target_bracket_rate: float = 0.22,
        scenario_id: uuid_mod.UUID | None = None,
    ) -> RothConversionPlan:
        """Plan a Roth conversion ladder during the early retirement window.

        The golden window: between retirement and Social Security start,
        income is low. Convert traditional → Roth, paying low rates now
        to avoid higher rates later (especially when RMDs hit at 73).
        """
        from app.engines.fire_projections import FireProjectionsEngine

        fire_engine = FireProjectionsEngine(self.db)
        config = await fire_engine.get_effective_config(scenario_id)
        tax_config = self._get_tax_config(config)
        filing_status = tax_config["filing_status"]
        state_rate = tax_config["state_tax_rate"]
        std_deduction = self._get_standard_deduction(tax_config)
        brackets = self._get_brackets(tax_config)

        accounts = await self.get_accounts_by_tax_treatment()
        income_sources = await self._get_active_income_sources()
        retirement_date = self._get_retirement_date(config)

        if not retirement_date or not config.date_of_birth:
            return RothConversionPlan(
                years=[], total_converted=0, total_tax_paid=0,
                estimated_tax_saved=0, target_bracket_rate=target_bracket_rate,
                conversion_window="N/A — retirement date or DOB not set",
                assumed_rmd_marginal_rate=tax_config.get("assumed_rmd_marginal_rate", 0.24),
            )

        # Conversion window: retirement → SS start
        ss_start_date = config.date_of_birth + relativedelta(years=config.social_security_start_age)
        window_start = max(retirement_date, date.today())
        window_end = ss_start_date

        if window_start >= window_end:
            return RothConversionPlan(
                years=[], total_converted=0, total_tax_paid=0,
                estimated_tax_saved=0, target_bracket_rate=target_bracket_rate,
                conversion_window="No conversion window — already past SS start age",
                assumed_rmd_marginal_rate=tax_config.get("assumed_rmd_marginal_rate", 0.24),
            )

        # Find the bracket ceiling for the target rate
        target_ceiling = 0
        for bracket in brackets:
            if bracket["rate"] <= target_bracket_rate:
                target_ceiling = bracket["up_to"]

        conversion_years: list[RothConversionYear] = []
        cumulative_converted = 0.0
        total_tax = 0.0
        deferred_balance = accounts.tax_deferred_balance
        current = window_start

        while current < window_end and deferred_balance > 0:
            age = self._compute_age(config, current)

            # Baseline TAXABLE income during retirement (non-earned) — a
            # non-taxable source doesn't fill brackets, so it leaves the
            # full conversion room open (fire-master#4).
            baseline_income = 0.0
            for src in income_sources:
                if not src.is_taxable:
                    continue
                if src.start_date and current < src.start_date:
                    continue
                if src.end_date and current > src.end_date:
                    continue
                if src.income_type.value in ("salary", "bonus", "side_hustle"):
                    continue  # earned income stops at retirement
                if src.income_type.value == "social_security":
                    continue  # not yet started
                baseline_income += src.annual_amount / 100

            # Room to fill so POST-DEDUCTION taxable income lands exactly at
            # the target bracket ceiling. When baseline income is below the
            # standard deduction, the conversion absorbs the unused deduction
            # (converting $X with zero income leaves taxable at X − std_ded).
            taxable_baseline = max(0, baseline_income - std_deduction)
            room_to_target = max(0, target_ceiling + std_deduction - baseline_income)

            # Convert up to the room available (don't exceed deferred balance)
            conversion = min(room_to_target, deferred_balance)
            if conversion <= 0:
                current += relativedelta(years=1)
                continue

            # Tax on conversion (ordinary income, after standard deduction)
            total_taxable = max(0, baseline_income + conversion - std_deduction)
            tax_with = self.compute_federal_tax(total_taxable, filing_status, brackets)
            tax_without = self.compute_federal_tax(taxable_baseline, filing_status, brackets)
            conversion_tax = tax_with.total_federal_tax - tax_without.total_federal_tax
            conversion_tax += self.compute_state_tax(conversion, state_rate)

            cumulative_converted += conversion
            total_tax += conversion_tax
            deferred_balance -= conversion

            magi = self.compute_magi(
                deferred_withdrawals=0,
                roth_conversions=conversion,
                rental_income=baseline_income,
            )

            conversion_years.append(RothConversionYear(
                year=current.year,
                age=round(age, 1),
                baseline_income=round(baseline_income, 2),
                conversion_amount=round(conversion, 2),
                total_taxable_income=round(total_taxable, 2),
                tax_on_conversion=round(conversion_tax, 2),
                cumulative_converted=round(cumulative_converted, 2),
                magi_after_conversion=round(magi, 2),
                bracket_filled_to=target_bracket_rate,
            ))

            current += relativedelta(years=1)

        # Estimate tax savings: without conversion, these dollars come out
        # later as RMDs taxed at the assumed marginal rate (config-driven,
        # tax.assumed_rmd_marginal_rate — a disclosed estimate, not a full
        # lifetime tax simulation).
        estimated_rmd_rate = tax_config.get("assumed_rmd_marginal_rate", 0.24)
        tax_if_rmd = cumulative_converted * estimated_rmd_rate
        tax_saved = tax_if_rmd - total_tax

        start_age = self._compute_age(config, window_start)
        end_age = self._compute_age(config, window_end)

        return RothConversionPlan(
            years=conversion_years,
            total_converted=round(cumulative_converted, 2),
            total_tax_paid=round(total_tax, 2),
            estimated_tax_saved=round(max(0, tax_saved), 2),
            target_bracket_rate=target_bracket_rate,
            conversion_window=f"age {int(start_age)}-{int(end_age)}",
            assumed_rmd_marginal_rate=estimated_rmd_rate,
        )

    # -----------------------------------------------------------------------
    # Bracket analysis (high-level endpoint)
    # -----------------------------------------------------------------------

    async def analyze_brackets(self) -> dict:
        """Full bracket analysis for current year — the power endpoint."""
        from app.engines.fire_projections import FireProjectionsEngine

        fire_engine = FireProjectionsEngine(self.db)
        config = await fire_engine.get_effective_config()
        tax_config = self._get_tax_config(config)
        filing_status = tax_config["filing_status"]
        state_rate = tax_config["state_tax_rate"]
        std_deduction = self._get_standard_deduction(tax_config)
        brackets = self._get_brackets(tax_config)
        household_size = tax_config.get("household_size", 1)

        # Current CALENDAR-YEAR income, day-prorated by each source's active
        # window — an ended salary contributes its partial-year amount, not
        # its full annual value.
        income_sources = await self._get_active_income_sources()
        current_year = date.today().year
        # Only is_taxable sources enter the bracket/FICA/MAGI math — a
        # non-taxable (or net-of-tax) source is cash flow, not taxable
        # income (fire-master#4).
        total_income = sum(
            _prorated_annual_for_year(s, current_year)
            for s in income_sources if s.is_active and s.is_taxable
        )
        earned_income = sum(
            _prorated_annual_for_year(s, current_year)
            for s in income_sources
            if s.income_type.value in ("salary", "bonus", "side_hustle")
            and s.is_active and s.is_taxable
        )

        taxable_income = max(0, total_income - std_deduction)
        federal = self.compute_federal_tax(taxable_income, filing_status, brackets)
        state_tax = self.compute_state_tax(taxable_income, state_rate)
        fica = self.compute_fica(earned_income, filing_status)
        room = self.get_bracket_room(taxable_income, filing_status, brackets)
        effective = self.compute_effective_rate(total_income, federal.total_federal_tax, state_tax, fica)

        # Account balances by tax treatment
        accounts = await self.get_accounts_by_tax_treatment()

        # ACA analysis — MAGI includes NON-earned income too (rental,
        # severance, dividends); previously only earned income was counted,
        # understating MAGI whenever other income existed.
        magi = self.compute_magi(
            earned_income=earned_income,
            rental_income=max(0.0, total_income - earned_income),
        )
        age = 0.0
        if config.date_of_birth:
            age = self._compute_age(config, date.today())
        aca = self.compute_aca_eligibility(magi, household_size, age)

        return {
            "filing_status": filing_status,
            "gross_income": round(total_income, 2),
            "standard_deduction": std_deduction,
            "taxable_income": round(taxable_income, 2),
            "federal_tax": federal.total_federal_tax,
            "federal_brackets": federal.brackets,
            "federal_effective_rate": federal.effective_rate,
            "federal_marginal_rate": federal.marginal_rate,
            "state_tax": state_tax,
            "state_rate": state_rate,
            "fica_tax": fica,
            "total_tax": round(federal.total_federal_tax + state_tax + fica, 2),
            "overall_effective_rate": effective,
            "bracket_room": {
                "current_rate": room.current_bracket_rate,
                "next_rate": room.next_bracket_rate,
                "room_dollars": room.room_in_current,
            },
            "account_balances": {
                "tax_deferred": round(accounts.tax_deferred_balance, 2),
                "tax_free": round(accounts.tax_free_balance, 2),
                "taxable": round(accounts.taxable_balance, 2),
                "already_taxed": round(accounts.already_taxed_balance, 2),
                "tax_deferred_accounts": [
                    {"name": a.name, "balance": a.current_balance / 100}
                    for a in accounts.tax_deferred
                ],
                "tax_free_accounts": [
                    {"name": a.name, "balance": a.current_balance / 100}
                    for a in accounts.tax_free
                ],
                "taxable_accounts": [
                    {"name": a.name, "balance": a.current_balance / 100}
                    for a in accounts.taxable
                ],
            },
            "aca": {
                "magi": aca.magi,
                "fpl_percentage": aca.fpl_percentage,
                "subsidy_eligible": aca.subsidy_eligible,
                "monthly_premium": aca.estimated_monthly_premium,
                "monthly_subsidy": aca.estimated_monthly_subsidy,
                "net_monthly_cost": aca.estimated_net_monthly_cost,
                "cliff_distance": aca.cliff_distance,
                "cliff_warning": aca.cliff_warning,
            },
        }

    # -----------------------------------------------------------------------
    # Tax scenario (what-if)
    # -----------------------------------------------------------------------

    async def run_tax_scenario(
        self,
        roth_conversion: float = 0,
        extra_income: float = 0,
        extra_deduction: float = 0,
    ) -> dict:
        """What-if: compare current vs hypothetical tax situation."""
        base = await self.analyze_brackets()

        # Compute scenario with adjustments
        from app.engines.fire_projections import FireProjectionsEngine
        fire_engine = FireProjectionsEngine(self.db)
        config = await fire_engine.get_effective_config()
        tax_config = self._get_tax_config(config)
        filing_status = tax_config["filing_status"]
        state_rate = tax_config["state_tax_rate"]
        std_deduction = self._get_standard_deduction(tax_config)
        brackets = self._get_brackets(tax_config)

        scenario_income = base["gross_income"] + extra_income + roth_conversion
        scenario_deductions = std_deduction + extra_deduction
        scenario_taxable = max(0, scenario_income - scenario_deductions)

        scenario_federal = self.compute_federal_tax(scenario_taxable, filing_status, brackets)
        scenario_state = self.compute_state_tax(scenario_taxable, state_rate)
        scenario_total = scenario_federal.total_federal_tax + scenario_state

        base_total = base["total_tax"]

        return {
            "base": {
                "gross_income": base["gross_income"],
                "taxable_income": base["taxable_income"],
                "total_tax": base_total,
                "effective_rate": base["overall_effective_rate"],
            },
            "scenario": {
                "gross_income": round(scenario_income, 2),
                "taxable_income": round(scenario_taxable, 2),
                "total_tax": round(scenario_total, 2),
                "effective_rate": round(scenario_total / scenario_income if scenario_income > 0 else 0, 4),
                "roth_conversion": roth_conversion,
                "extra_income": extra_income,
                "extra_deduction": extra_deduction,
            },
            "delta": {
                "additional_tax": round(scenario_total - base_total, 2),
                "marginal_rate_on_new_income": round(
                    (scenario_total - base_total) / (extra_income + roth_conversion)
                    if (extra_income + roth_conversion) > 0 else 0, 4
                ),
            },
        }

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    async def _get_active_income_sources(self) -> list[IncomeSource]:
        result = await self.db.execute(
            select(IncomeSource).where(IncomeSource.is_active == True)
        )
        return list(result.scalars().all())

    def _get_retirement_date(self, config: FireConfig) -> date | None:
        if config.target_retirement_date:
            return config.target_retirement_date
        if config.target_retirement_age and config.date_of_birth:
            return config.date_of_birth + relativedelta(years=config.target_retirement_age)
        return None

    def _compute_age(self, config: FireConfig, target_date: date) -> float:
        if not config.date_of_birth:
            return 0
        delta = target_date - config.date_of_birth
        return delta.days / 365.25
