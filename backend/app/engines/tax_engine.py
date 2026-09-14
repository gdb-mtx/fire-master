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
from app.models.income_source import IncomeSource, projection_annual_amount_cents

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

# California Franchise Tax Board 2025 published schedules. The projection is
# expressed in today's dollars, so these thresholds stay fixed just like the
# federal brackets above. California taxes capital gains as ordinary income.
CA_BRACKETS_2025: dict[str, list[dict]] = {
    "single": [
        {"rate": 0.01, "up_to": 11_079},
        {"rate": 0.02, "up_to": 26_264},
        {"rate": 0.04, "up_to": 41_452},
        {"rate": 0.06, "up_to": 57_542},
        {"rate": 0.08, "up_to": 72_724},
        {"rate": 0.093, "up_to": 371_479},
        {"rate": 0.103, "up_to": 445_771},
        {"rate": 0.113, "up_to": 742_953},
        {"rate": 0.123, "up_to": float("inf")},
    ],
    "married_filing_jointly": [
        {"rate": 0.01, "up_to": 22_158},
        {"rate": 0.02, "up_to": 52_528},
        {"rate": 0.04, "up_to": 82_904},
        {"rate": 0.06, "up_to": 115_084},
        {"rate": 0.08, "up_to": 145_448},
        {"rate": 0.093, "up_to": 742_958},
        {"rate": 0.103, "up_to": 891_542},
        {"rate": 0.113, "up_to": 1_485_906},
        {"rate": 0.123, "up_to": float("inf")},
    ],
}

CA_STANDARD_DEDUCTION_2025 = {
    "single": 5_706,
    "married_filing_jointly": 11_412,
}
CA_BEHAVIORAL_HEALTH_SURTAX_THRESHOLD = 1_000_000
CA_BEHAVIORAL_HEALTH_SURTAX_RATE = 0.01
CA_SDI_RATE_2026 = 0.013
CA_ITEMIZED_LIMIT_THRESHOLDS_2025 = {
    "single": 252_203,
    "married_filing_jointly": 504_411,
}
FEDERAL_SALT_BASE_CAP_2025 = 40_000
FEDERAL_SALT_PHASEOUT_AGI_2025 = 500_000
FEDERAL_SALT_FLOOR = 10_000

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


def _prorated_projection_for_year(source, year: int, end_override: date | None = None) -> float:
    """Spendable cash contribution for a source, respecting its date window."""
    end = source.end_date
    if end_override is not None and (end is None or end_override < end):
        end = end_override
    return (
        projection_annual_amount_cents(source) / 100
        * _year_fraction(year, source.start_date, end)
    )


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
class StateTaxBreakdown:
    """State income/payroll tax details for the configured residence."""
    taxable_income: float
    income_tax: float
    payroll_tax: float
    total_tax: float
    effective_rate: float
    marginal_rate: float
    standard_deduction: float
    deduction_method: str
    method: str


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
    spending_need: float
    taxes_funded: float
    net_spendable: float
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
            # Optional state-specific overrides. California automatically uses
            # the published progressive schedule when state is CA.
            "state_brackets": None,
            "state_standard_deduction": None,
            "state_deduction_method": "standard",
            "state_itemized_deduction": None,
            "state_surtax_threshold": None,
            "state_surtax_rate": None,
            "state_payroll_tax_rate": None,
            "brackets": None,  # will use DEFAULT_BRACKETS
            "standard_deduction": None,  # will use DEFAULT_STANDARD_DEDUCTION
            "federal_deduction_method": "standard",
            "federal_itemized_deduction": None,
            "household_size": 1,
            "cost_basis_pct": 0.60,  # estimated % of taxable balance that is cost basis (not taxed)
            # Assumed marginal rate on future RMDs, used to estimate Roth
            # conversion savings (at 73+ with SS income, 22-24% is typical).
            "assumed_rmd_marginal_rate": 0.24,
            # REAL yield on the cash bucket in the withdrawal plan. Default 0:
            # cash holds purchasing power (HYSA ≈ inflation). Positive only if
            # cash is parked meaningfully above inflation.
            "cash_yield_rate": 0.0,
            # When enabled, iteratively gross up withdrawals until spending
            # plus the resulting tax bill is fully funded.
            "fund_taxes_from_withdrawals": False,
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

    def _get_federal_standard_deduction(self, tax_config: dict) -> float:
        """Get the federal standard deduction before itemization."""
        status = tax_config.get("filing_status", "single")
        configured_standard = tax_config.get("standard_deduction")
        return (
            float(configured_standard)
            if configured_standard is not None
            else DEFAULT_STANDARD_DEDUCTION.get(
                status, DEFAULT_STANDARD_DEDUCTION["single"],
            )
        )

    def _get_standard_deduction(self, tax_config: dict) -> float:
        """Get the configured federal deduction (legacy name retained)."""
        standard = self._get_federal_standard_deduction(tax_config)
        itemized = tax_config.get("federal_itemized_deduction")
        method = tax_config.get("federal_deduction_method", "standard")
        return self._select_deduction(
            method,
            standard,
            float(itemized) if itemized is not None else None,
        )[0]

    def _get_federal_deduction_method(self, tax_config: dict) -> str:
        """Describe which configured federal deduction is actually in use."""
        method = tax_config.get("federal_deduction_method", "standard")
        itemized = tax_config.get("federal_itemized_deduction")
        if itemized is not None and method == "greater_of":
            standard = self._get_federal_standard_deduction(tax_config)
            return "itemized" if float(itemized) > standard else "standard"
        if itemized is not None and method == "itemized":
            return "itemized"
        return "standard"

    @staticmethod
    def _is_california(tax_config: dict) -> bool:
        state = str(tax_config.get("state") or "").strip().upper()
        return state in {"CA", "CALIFORNIA"}

    @staticmethod
    def _itemized_config(tax_config: dict) -> dict:
        value = tax_config.get("itemized_deductions") or {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _select_deduction(
        method: str,
        standard_deduction: float,
        itemized_deduction: float | None,
    ) -> tuple[float, str]:
        if itemized_deduction is None:
            return standard_deduction, "standard"
        if method == "itemized":
            return max(0.0, itemized_deduction), "itemized"
        if method == "greater_of":
            if itemized_deduction > standard_deduction:
                return itemized_deduction, "itemized"
            return standard_deduction, "standard"
        return standard_deduction, "standard"

    @staticmethod
    def _federal_salt_cap(year: int, magi: float, filing_status: str) -> float:
        """Current-law SALT cap, including the temporary 2025-2029 expansion."""
        if not 2025 <= year <= 2029:
            return FEDERAL_SALT_FLOOR / (2 if filing_status == "married_filing_separately" else 1)

        growth = 1.01 ** (year - 2025)
        base_cap = FEDERAL_SALT_BASE_CAP_2025 * growth
        phaseout_start = FEDERAL_SALT_PHASEOUT_AGI_2025 * growth
        floor = FEDERAL_SALT_FLOOR
        if filing_status == "married_filing_separately":
            base_cap /= 2
            phaseout_start /= 2
            floor /= 2
        return max(floor, base_cap - 0.30 * max(0.0, magi - phaseout_start))

    def _state_itemized_deduction(
        self,
        state_agi: float,
        tax_config: dict,
        mortgage_interest: float,
        *,
        year: int | None = None,
        inflation_rate: float = 0.0,
    ) -> tuple[float | None, float, float]:
        """Return usable CA itemized deduction, pre-limit total, and limitation."""
        itemized = self._itemized_config(tax_config)
        if not itemized.get("enabled", False) or not self._is_california(tax_config):
            return None, 0.0, 0.0

        property_tax = self._projected_property_tax(
            itemized, year=year, inflation_rate=inflation_rate,
        )
        charitable = float(itemized.get("annual_charitable_gifts") or 0.0)
        other_limited = float(itemized.get("annual_other_california") or 0.0)
        unlimited = float(itemized.get("annual_california_unlimited") or 0.0)
        limited = mortgage_interest + property_tax + charitable + other_limited
        before_limit = limited + unlimited

        status = tax_config.get("filing_status", "single")
        threshold = float(
            itemized.get("california_limitation_threshold")
            or CA_ITEMIZED_LIMIT_THRESHOLDS_2025.get(
                status, CA_ITEMIZED_LIMIT_THRESHOLDS_2025["single"],
            )
        )
        limitation = min(
            limited * 0.80,
            max(0.0, state_agi - threshold) * 0.06,
        )
        return max(0.0, before_limit - limitation), before_limit, limitation

    def _federal_itemized_deduction(
        self,
        year: int,
        federal_agi: float,
        state_tax_paid: float,
        tax_config: dict,
        mortgage_interest: float,
        *,
        inflation_rate: float = 0.0,
    ) -> tuple[float | None, float]:
        """Return federal itemized deductions and the allowed SALT component."""
        itemized = self._itemized_config(tax_config)
        if not itemized.get("enabled", False):
            return None, 0.0

        property_tax = self._projected_property_tax(
            itemized, year=year, inflation_rate=inflation_rate,
        )
        charitable = float(itemized.get("annual_charitable_gifts") or 0.0)
        other = float(itemized.get("annual_other_federal") or 0.0)
        salt_paid = property_tax + max(0.0, state_tax_paid)
        salt_allowed = min(
            salt_paid,
            self._federal_salt_cap(
                year, federal_agi, tax_config.get("filing_status", "single"),
            ),
        )
        return mortgage_interest + salt_allowed + charitable + other, salt_allowed

    @staticmethod
    def _projected_property_tax(
        itemized_config: dict,
        *,
        year: int | None,
        inflation_rate: float,
    ) -> float:
        """Project a nominal property-tax increase in the model's real dollars."""
        base_amount = float(itemized_config.get("annual_property_tax") or 0.0)
        if year is None:
            return base_amount
        base_year = int(itemized_config.get("property_tax_base_year") or date.today().year)
        years_elapsed = max(0, year - base_year)
        nominal_growth = float(itemized_config.get("property_tax_growth_rate") or 0.0)
        real_growth = (1 + nominal_growth) / (1 + inflation_rate) - 1
        return base_amount * ((1 + real_growth) ** years_elapsed)

    async def _primary_mortgage_balance(self) -> float:
        if self.db is None:
            return 0.0
        result = await self.db.execute(
            select(Account).where(Account.fire_role == "primary_mortgage")
        )
        return sum(
            abs(account.current_balance) for account in result.scalars().all()
            if not account.is_asset
        ) / 100

    @staticmethod
    def _mortgage_interest_schedule(
        config: FireConfig,
        current_balance: float,
        start_year: int,
        years: int,
    ) -> dict[int, tuple[float, float]]:
        """Annual deductible federal/CA interest from the live amortizing loan."""
        itemized = ((config.custom_assumptions or {}).get("tax", {}) or {}).get(
            "itemized_deductions", {},
        ) or {}
        projection = (config.custom_assumptions or {}).get("projection", {}) or {}
        payment = float(projection.get("primary_property_mortgage_pi") or 0.0)
        rate = float(projection.get("primary_property_mortgage_rate") or 0.0)
        payoff_raw = projection.get("primary_property_mortgage_payoff_date")
        try:
            payoff = date.fromisoformat(payoff_raw) if isinstance(payoff_raw, str) else payoff_raw
        except ValueError:
            payoff = None
        if current_balance <= 0 or payment <= 0 or rate <= 0:
            return {}

        monthly_rate = rate / 12
        balance = current_balance
        # Reconstruct an approximate January balance from the latest synced
        # balance so the current calendar year's interest is not understated.
        today = date.today()
        for _ in range(max(0, today.month - 1)):
            balance = (balance + payment) / (1 + monthly_rate)

        federal_limit = float(itemized.get("federal_mortgage_debt_limit") or 750_000)
        california_limit = float(itemized.get("california_mortgage_debt_limit") or 1_000_000)
        schedule: dict[int, tuple[float, float]] = {}
        for year in range(start_year, start_year + years):
            federal_interest = 0.0
            california_interest = 0.0
            for month in range(1, 13):
                month_date = date(year, month, 1)
                if payoff and month_date >= payoff:
                    balance = 0.0
                    continue
                if balance <= 0:
                    continue
                interest = balance * monthly_rate
                federal_interest += interest * min(1.0, federal_limit / balance)
                california_interest += interest * min(1.0, california_limit / balance)
                principal = min(balance, max(0.0, payment - interest))
                balance -= principal
            schedule[year] = (federal_interest, california_interest)
        return schedule

    async def _mortgage_interest_by_year(
        self,
        config: FireConfig,
        tax_config: dict,
        start_year: int,
        years: int,
    ) -> dict[int, tuple[float, float]]:
        itemized = self._itemized_config(tax_config)
        if not itemized.get("enabled", False):
            return {}
        balance = await self._primary_mortgage_balance()
        return self._mortgage_interest_schedule(config, balance, start_year, years)

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
        """Compute a flat state tax for states without a modeled schedule."""
        return round(max(0, taxable_income) * state_rate / 100, 2)

    def compute_configured_state_tax(
        self,
        state_agi: float,
        tax_config: dict,
        *,
        earned_income: float = 0.0,
        extra_deduction: float = 0.0,
        deduction_override: float | None = None,
        deduction_method_override: str | None = None,
    ) -> StateTaxBreakdown:
        """Compute state tax using a progressive CA model or flat fallback.

        California uses its own standard deduction, taxes capital gains as
        ordinary income, excludes Social Security before this method is called,
        and adds the 1% Behavioral Health Services Tax above $1 million of
        California taxable income. Employee SDI is included for earned income.
        """
        filing_status = tax_config.get("filing_status", "single")

        if self._is_california(tax_config):
            brackets = tax_config.get("state_brackets") or CA_BRACKETS_2025.get(
                filing_status, CA_BRACKETS_2025["single"],
            )
            configured_standard = tax_config.get("state_standard_deduction")
            standard_deduction = (
                float(configured_standard)
                if configured_standard is not None
                else CA_STANDARD_DEDUCTION_2025.get(
                    filing_status, CA_STANDARD_DEDUCTION_2025["single"],
                )
            )
            if deduction_override is not None:
                deduction = max(0.0, deduction_override)
                deduction_method = deduction_method_override or "itemized"
            else:
                itemized = tax_config.get("state_itemized_deduction")
                deduction, deduction_method = self._select_deduction(
                    tax_config.get("state_deduction_method", "standard"),
                    standard_deduction,
                    float(itemized) if itemized is not None else None,
                )
            taxable_income = max(
                0.0, state_agi - deduction - extra_deduction,
            )

            income_tax = 0.0
            marginal_rate = brackets[0]["rate"] if brackets else 0.0
            previous_ceiling = 0.0
            remaining = taxable_income
            for bracket in brackets:
                bracket_width = bracket["up_to"] - previous_ceiling
                income_in_bracket = min(remaining, bracket_width)
                if income_in_bracket > 0:
                    income_tax += income_in_bracket * bracket["rate"]
                    marginal_rate = bracket["rate"]
                remaining -= income_in_bracket
                previous_ceiling = bracket["up_to"]
                if remaining <= 0:
                    break

            configured_threshold = tax_config.get("state_surtax_threshold")
            surtax_threshold = (
                float(configured_threshold)
                if configured_threshold is not None
                else CA_BEHAVIORAL_HEALTH_SURTAX_THRESHOLD
            )
            configured_surtax_rate = tax_config.get("state_surtax_rate")
            surtax_rate = (
                float(configured_surtax_rate)
                if configured_surtax_rate is not None
                else CA_BEHAVIORAL_HEALTH_SURTAX_RATE
            )
            surtax = max(0.0, taxable_income - surtax_threshold) * surtax_rate
            if taxable_income > surtax_threshold:
                marginal_rate += surtax_rate

            configured_payroll_rate = tax_config.get("state_payroll_tax_rate")
            payroll_rate = (
                float(configured_payroll_rate)
                if configured_payroll_rate is not None
                else CA_SDI_RATE_2026
            )
            payroll_tax = max(0.0, earned_income) * payroll_rate
            income_tax = round(income_tax + surtax, 2)
            payroll_tax = round(payroll_tax, 2)
            total_tax = round(income_tax + payroll_tax, 2)
            return StateTaxBreakdown(
                taxable_income=round(taxable_income, 2),
                income_tax=income_tax,
                payroll_tax=payroll_tax,
                total_tax=total_tax,
                effective_rate=round(total_tax / state_agi, 4) if state_agi > 0 else 0.0,
                marginal_rate=round(marginal_rate, 4),
                standard_deduction=deduction,
                deduction_method=deduction_method,
                method="california_progressive_2025",
            )

        standard_deduction = self._get_standard_deduction(tax_config)
        if deduction_override is not None:
            deduction = max(0.0, deduction_override)
            deduction_method = deduction_method_override or "itemized"
        else:
            itemized = tax_config.get("state_itemized_deduction")
            deduction, deduction_method = self._select_deduction(
                tax_config.get("state_deduction_method", "standard"),
                standard_deduction,
                float(itemized) if itemized is not None else None,
            )
        taxable_income = max(0.0, state_agi - deduction - extra_deduction)
        state_rate = float(tax_config.get("state_tax_rate") or 0.0)
        total_tax = self.compute_state_tax(taxable_income, state_rate)
        return StateTaxBreakdown(
            taxable_income=round(taxable_income, 2),
            income_tax=total_tax,
            payroll_tax=0.0,
            total_tax=total_tax,
            effective_rate=round(total_tax / state_agi, 4) if state_agi > 0 else 0.0,
            marginal_rate=round(state_rate / 100, 4) if taxable_income > 0 else 0.0,
            standard_deduction=deduction,
            deduction_method=deduction_method,
            method="flat_rate",
        )

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

    @staticmethod
    def estimate_taxable_cost_basis(
        accounts: list[Account], fallback_pct: float,
    ) -> dict:
        """Estimate taxable basis from per-account synced data.

        Monarch-reported basis is used for covered holdings, uninvested cash is
        basis dollar-for-dollar, and the configured percentage applies only to
        holdings whose basis is unavailable. A manual account-level basis in
        ``custom_data.taxable_cost_basis_cents`` takes precedence.
        """
        fallback_pct = max(0.0, min(1.0, float(fallback_pct)))
        total_balance = 0.0
        total_basis = 0.0
        covered_value = 0.0
        details: list[dict] = []

        for account in accounts:
            balance = max(0.0, account.current_balance / 100)
            total_balance += balance
            custom = account.custom_data or {}
            manual_cents = custom.get("taxable_cost_basis_cents")
            source = "fallback"

            if manual_cents is not None:
                try:
                    basis = max(0.0, float(manual_cents) / 100)
                    exact_value = balance
                    source = "manual"
                except (TypeError, ValueError):
                    basis = balance * fallback_pct
                    exact_value = 0.0
            else:
                synced = (account.extra_data or {}).get("taxable_basis") or {}
                try:
                    holdings_value = max(
                        0.0, float(synced.get("holdings_value_cents") or 0) / 100,
                    )
                    known_value = max(
                        0.0, float(synced.get("basis_covered_value_cents") or 0) / 100,
                    )
                    known_basis = max(
                        0.0, float(synced.get("cost_basis_cents") or 0) / 100,
                    )
                except (TypeError, ValueError):
                    holdings_value = known_value = known_basis = 0.0

                if holdings_value > 0:
                    # Reconcile small timing differences between the holdings
                    # total and the account's newer headline balance.
                    scale = min(1.0, balance / holdings_value)
                    holdings_value *= scale
                    known_value = min(holdings_value, known_value * scale)
                    known_basis *= scale
                    cash_value = max(0.0, balance - holdings_value)
                    unknown_value = max(0.0, holdings_value - known_value)
                    basis = known_basis + cash_value + unknown_value * fallback_pct
                    exact_value = known_value + cash_value
                    source = "synced" if unknown_value <= 0.01 else "synced + fallback"
                else:
                    basis = balance * fallback_pct
                    exact_value = 0.0

            # Losses can make actual basis exceed market value. The tax model
            # conservatively treats the gain portion as zero; it does not book
            # an assumed capital-loss benefit.
            taxable_basis = min(balance, basis)
            total_basis += taxable_basis
            covered_value += min(balance, exact_value)
            details.append({
                "name": account.name,
                "balance": balance,
                "cost_basis": round(taxable_basis, 2),
                "cost_basis_pct": round(taxable_basis / balance, 4) if balance else 0.0,
                "basis_source": source,
            })

        return {
            "cost_basis": round(total_basis, 2),
            "cost_basis_pct": total_basis / total_balance if total_balance else fallback_pct,
            "coverage_pct": round(covered_value / total_balance, 4) if total_balance else 0.0,
            "accounts": details,
        }

    # -----------------------------------------------------------------------
    # Withdrawal sequencing optimizer
    # -----------------------------------------------------------------------

    async def optimize_withdrawal_sequence(
        self,
        years: int = 10,
        target_bracket_rate: float = 0.22,
        roth_conversions_enabled: bool = True,
        scenario_id: uuid_mod.UUID | None = None,
        config_override: FireConfig | None = None,
    ) -> WithdrawalPlan:
        """Produce a year-by-year tax-aware withdrawal plan.

        Withdrawals follow a fixed order each year:
        1. Cash above the configured operating reserve
        2. Taxable accounts (only the gains portion taxed, at LTCG rates)
        3. Accessible tax-deferred accounts (ordinary income)
        4. Tax-free Roth accounts
        5. The remaining cash reserve as a last resort

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

        When tax.fund_taxes_from_withdrawals is enabled, withdrawals are
        iteratively grossed up until both the desired spending and the tax bill
        produced by those withdrawals are funded. Taxes already embedded in a
        source's explicit net cash-flow amount are not charged twice.

        IncomeSource.is_taxable gates the tax computation: a source flagged
        non-taxable (or entered net-of-tax — flag it False) still offsets
        spending need but contributes nothing to ordinary income, FICA, or
        MAGI. Enter gross amounts with is_taxable=True to have the engine
        tax them.

        Config comes from get_effective_config (active scenario merged, or
        an explicit scenario_id) — same as the projection engines.
        """
        from app.engines.fire_projections import (
            FireProjectionsEngine,
            _annual_spending_with_mortgage,
            _healthcare_monthly_cents_at_age,
        )

        fire_engine = FireProjectionsEngine(self.db)
        config = config_override or await fire_engine.get_effective_config(scenario_id)
        tax_config = self._get_tax_config(config)
        filing_status = tax_config["filing_status"]
        std_deduction = self._get_standard_deduction(tax_config)
        brackets = self._get_brackets(tax_config)
        fallback_cost_basis_pct = tax_config.get("cost_basis_pct", 0.60)
        fund_taxes = bool(tax_config.get("fund_taxes_from_withdrawals", False))

        accounts = await self.get_accounts_by_tax_treatment()
        basis_estimate = self.estimate_taxable_cost_basis(
            accounts.taxable, fallback_cost_basis_pct,
        )
        income_sources = await self._get_active_income_sources()
        retirement_date = self._get_retirement_date(config)

        annual_spending_cents = config.target_annual_spending or 12_000_000  # default $120K
        annual_need = annual_spending_cents / 100
        assumptions = config.custom_assumptions or {}
        projection_config = assumptions.get("projection", {}) or {}
        penalty_free_age = float(assumptions.get("penalty_free_age", 59.5))
        rule_of_55_eligible = bool(assumptions.get("rule_of_55_eligible", False))
        sepp_monthly = float((assumptions.get("sepp", {}) or {}).get("sepp_monthly", 0) or 0)
        cash_reserve_months = max(
            0.0, float(projection_config.get("cash_reserve_months", 12) or 0),
        )
        # REAL yield on cash — default 0 (cash holds purchasing power at
        # best; set positive for HYSA-parked cash, negative for checking).
        cash_yield = tax_config.get("cash_yield_rate", 0.0)

        today = date.today()
        mortgage_interest_by_year = await self._mortgage_interest_by_year(
            config, tax_config, today.year, years,
        )
        year_plans: list[WithdrawalYearPlan] = []
        total_tax = 0.0
        total_withdrawn = 0.0
        total_gross_all_years = 0.0

        # Track account balances across years
        deferred_balance = accounts.tax_deferred_balance
        roth_balance = accounts.tax_free_balance
        taxable_balance = accounts.taxable_balance
        taxable_basis_balance = basis_estimate["cost_basis"]
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
            spending_need = _annual_spending_with_mortgage(
                annual_need, 1.0, config, current_year,
            )
            if is_retired:
                spending_need += (
                    _healthcare_monthly_cents_at_age(config, age) * 12 / 100
                )

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
            taxable_state_wages = 0.0
            gross_non_withdrawal_income = 0.0
            prepaid_withholding = 0.0

            for src in income_sources:
                # Day-prorated contribution for this calendar year (an ended
                # salary counts its partial year, not the full annual amount).
                # Earned income additionally stops at the retirement date even
                # without an explicit end_date.
                end_override = None
                if src.income_type.value in ("salary", "bonus", "side_hustle"):
                    end_override = retirement_date
                annual = _prorated_annual_for_year(src, current_year, end_override)
                annual_cash = _prorated_projection_for_year(
                    src, current_year, end_override,
                )
                if annual <= 0:
                    continue
                if src.growth_rate and yr > 0:
                    # growth_rate is a NOMINAL raise — deflate to real
                    real_growth = (1 + src.growth_rate / 100) / (1 + inflation) - 1
                    annual *= (1 + real_growth) ** yr
                    annual_cash *= (1 + real_growth) ** yr

                gross_non_withdrawal_income += annual
                custom_data = getattr(src, "custom_data", None)
                if (
                    src.is_taxable
                    and isinstance(custom_data, dict)
                    and custom_data.get("net_annual_amount") is not None
                ):
                    # The gross-minus-projected-cash difference is the amount
                    # already withheld from this source. The former boolean
                    # treatment credited the household with its ENTIRE tax
                    # bill whenever even one source had a net-cash override,
                    # including taxes attributable to overlapping gross-only
                    # sources. That made mixed income phases far too rosy.
                    prepaid_withholding += max(0.0, annual - annual_cash)

                if src.income_type.value == "social_security":
                    ss_income += annual_cash
                    taxable_ss += annual if src.is_taxable else 0.0
                elif src.income_type.value == "pension":
                    pension_income += annual_cash
                    taxable_pension += annual if src.is_taxable else 0.0
                elif src.income_type.value in ("salary", "bonus", "side_hustle"):
                    earned_income += annual_cash
                    taxable_earned += annual if src.is_taxable else 0.0
                    if src.income_type.value in ("salary", "bonus") and src.is_taxable:
                        taxable_state_wages += annual
                else:
                    other_income += annual_cash
                    taxable_other += annual if src.is_taxable else 0.0

            # Social Security from config (if not in income sources) —
            # prorated in its first year (a mid-year start is half a year)
            if config.social_security_monthly and config.date_of_birth and ss_income == 0:
                ss_start = config.date_of_birth + relativedelta(years=config.social_security_start_age)
                ss_income = (config.social_security_monthly * 12 / 100) * _year_fraction(
                    current_year, start=ss_start)
                taxable_ss = ss_income
                gross_non_withdrawal_income += ss_income

            # Pension from config — same first-year proration
            if config.pension_monthly and config.pension_start_age and config.date_of_birth and pension_income == 0:
                pension_start = config.date_of_birth + relativedelta(years=config.pension_start_age)
                pension_income = (config.pension_monthly * 12 / 100) * _year_fraction(
                    current_year, start=pension_start)
                taxable_pension = pension_income
                gross_non_withdrawal_income += pension_income

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
            traditional_accessible = (
                age >= penalty_free_age
                or (rule_of_55_eligible and age >= 55)
                or sepp_monthly > 0
            )
            cash_reserve = withdrawal_needed / 12 * cash_reserve_months

            if remaining_need > 0:
                # Spend cash above the configured operating reserve before selling
                # investments. This also makes accumulated working-year surplus
                # available to bridge an early retirement.
                cash_above_reserve = max(0.0, cash_balance - cash_reserve)
                if cash_above_reserve > 0:
                    draw = min(remaining_need, cash_above_reserve)
                    from_cash += draw
                    cash_balance -= draw
                    remaining_need -= draw

                # Then use taxable brokerage (preferential capital-gains rates).
                if taxable_balance > 0 and remaining_need > 0:
                    basis_ratio = min(1.0, taxable_basis_balance / taxable_balance)
                    draw = min(remaining_need, taxable_balance)
                    from_taxable = draw
                    basis_used = draw * basis_ratio
                    capital_gains += draw - basis_used
                    taxable_basis_balance = max(0.0, taxable_basis_balance - basis_used)
                    taxable_balance -= draw
                    remaining_need -= draw

                # Traditional accounts are unavailable before 59½ unless the
                # household explicitly configured Rule of 55 or a SEPP plan.
                if traditional_accessible and deferred_balance > 0 and remaining_need > 0:
                    draw = min(remaining_need, deferred_balance)
                    from_deferred = draw
                    deferred_balance -= draw
                    remaining_need -= draw

                # Roth is the last invested pool.
                if roth_balance > 0 and remaining_need > 0:
                    draw = min(remaining_need, roth_balance)
                    from_roth = draw
                    roth_balance -= draw
                    remaining_need -= draw

                # Finally permit the operating reserve itself to be depleted.
                if cash_balance > 0 and remaining_need > 0:
                    draw = min(remaining_need, cash_balance)
                    from_cash += draw
                    cash_balance -= draw
                    remaining_need -= draw

            # RMD check (age 73+): force minimum deferred withdrawal
            roth_conversion = 0.0
            rmd_redeposit = 0.0
            if age >= config.rmd_start_age and deferred_balance > 0:
                rmd_pct = 1 / _get_rmd_divisor(int(age))
                rmd_amount = deferred_balance * rmd_pct
                if rmd_amount > from_deferred:
                    extra_rmd = rmd_amount - from_deferred
                    from_deferred += extra_rmd
                    deferred_balance -= extra_rmd
                    # The forced excess isn't spent — it lands in taxable
                    # until spending and any tax bill need it.
                    taxable_balance += extra_rmd
                    taxable_basis_balance += extra_rmd
                    rmd_redeposit = extra_rmd

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

            def calculate_year_tax() -> tuple[float, float, float, float, float]:
                """Return ordinary income and federal/state/FICA/total tax."""
                ordinary = (
                    taxable_earned + taxable_ss * 0.85 + taxable_pension
                    + from_deferred + roth_conversion + taxable_other
                )
                state_agi = ordinary + capital_gains
                if self._is_california(tax_config):
                    # California excludes Social Security benefits entirely.
                    state_agi -= taxable_ss * 0.85
                federal_mortgage_interest, state_mortgage_interest = (
                    mortgage_interest_by_year.get(current_year, (0.0, 0.0))
                )
                state_itemized, _, _ = self._state_itemized_deduction(
                    state_agi,
                    tax_config,
                    state_mortgage_interest,
                    year=current_year,
                    inflation_rate=inflation,
                )
                state_deduction = None
                state_deduction_method = None
                if state_itemized is not None:
                    state_standard = (
                        float(tax_config["state_standard_deduction"])
                        if tax_config.get("state_standard_deduction") is not None
                        else CA_STANDARD_DEDUCTION_2025.get(
                            filing_status, CA_STANDARD_DEDUCTION_2025["single"],
                        )
                    )
                    state_deduction, state_deduction_method = self._select_deduction(
                        tax_config.get("state_deduction_method", "greater_of"),
                        state_standard,
                        state_itemized,
                    )
                state_breakdown = self.compute_configured_state_tax(
                    state_agi,
                    tax_config,
                    earned_income=taxable_state_wages,
                    deduction_override=state_deduction,
                    deduction_method_override=state_deduction_method,
                )
                state = state_breakdown.total_tax

                federal_itemized, _ = self._federal_itemized_deduction(
                    current_year,
                    ordinary + capital_gains,
                    state,
                    tax_config,
                    federal_mortgage_interest,
                    inflation_rate=inflation,
                )
                federal_deduction = std_deduction
                if federal_itemized is not None:
                    federal_deduction = self._select_deduction(
                        tax_config.get("federal_deduction_method", "greater_of"),
                        self._get_federal_standard_deduction(tax_config),
                        federal_itemized,
                    )[0]
                taxable_total = max(0, ordinary - federal_deduction)
                unused_deduction = max(0.0, federal_deduction - ordinary)
                taxable_capital_gains = max(0.0, capital_gains - unused_deduction)
                federal_breakdown = self.compute_federal_tax(
                    taxable_total, filing_status, brackets,
                )
                gains_tax = self.compute_capital_gains_tax(
                    taxable_capital_gains, taxable_total, filing_status,
                )
                federal = federal_breakdown.total_federal_tax + gains_tax
                fica = self.compute_fica(taxable_earned, filing_status)
                return ordinary, federal, state, fica, federal + state + fica

            # An explicit projected-cash amount is net of withholding. Credit
            # only the dollars actually removed from that source, rather than
            # declaring the household's whole computed liability prepaid.
            # Any calculated liability above the implied withholding remains
            # an outflow; excess withholding is conservatively not refunded.
            prepaid_tax = prepaid_withholding

            # Fixed-point gross-up: taxes on an extra traditional withdrawal
            # themselves require another (smaller) withdrawal. Iterate until
            # the remaining after-tax cash gap is immaterial or assets run out.
            for _ in range(12):
                (
                    ordinary_income,
                    federal_tax,
                    state_tax,
                    fica_tax,
                    total_tax_year,
                ) = calculate_year_tax()
                taxes_funded = max(0.0, total_tax_year - prepaid_tax) if fund_taxes else 0.0
                spendable_cash = (
                    total_non_withdrawal_income + from_taxable + from_deferred
                    + from_roth + from_cash - rmd_redeposit
                )
                extra_needed = max(0.0, spending_need + taxes_funded - spendable_cash)
                if extra_needed <= 0.01 or not fund_taxes:
                    break

                drawn = 0.0
                cash_above_reserve = max(0.0, cash_balance - cash_reserve)
                if cash_above_reserve > 0 and extra_needed > 0:
                    amount = min(extra_needed, cash_above_reserve)
                    cash_balance -= amount
                    from_cash += amount
                    extra_needed -= amount
                    drawn += amount
                if taxable_balance > 0 and extra_needed > 0:
                    basis_ratio = min(1.0, taxable_basis_balance / taxable_balance)
                    amount = min(extra_needed, taxable_balance)
                    basis_used = amount * basis_ratio
                    taxable_balance -= amount
                    taxable_basis_balance = max(0.0, taxable_basis_balance - basis_used)
                    from_taxable += amount
                    capital_gains += amount - basis_used
                    extra_needed -= amount
                    drawn += amount
                if traditional_accessible and deferred_balance > 0 and extra_needed > 0:
                    amount = min(extra_needed, deferred_balance)
                    deferred_balance -= amount
                    from_deferred += amount
                    extra_needed -= amount
                    drawn += amount
                if roth_balance > 0 and extra_needed > 0:
                    amount = min(extra_needed, roth_balance)
                    roth_balance -= amount
                    from_roth += amount
                    extra_needed -= amount
                    drawn += amount
                if cash_balance > 0 and extra_needed > 0:
                    amount = min(extra_needed, cash_balance)
                    cash_balance -= amount
                    from_cash += amount
                    extra_needed -= amount
                    drawn += amount

                if drawn <= 0.01:
                    break

            # Recompute once with the final grossed-up withdrawal amounts.
            (
                ordinary_income,
                federal_tax,
                state_tax,
                fica_tax,
                total_tax_year,
            ) = calculate_year_tax()
            taxes_funded = max(0.0, total_tax_year - prepaid_tax) if fund_taxes else 0.0

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
                gross_non_withdrawal_income + from_taxable + from_deferred
                + from_roth + from_cash
            )
            effective = total_tax_year / total_gross if total_gross > 0 else 0
            net_spendable = (
                total_non_withdrawal_income + from_taxable + from_deferred
                + from_roth + from_cash - rmd_redeposit - taxes_funded
            )

            year_plans.append(WithdrawalYearPlan(
                year=current_year,
                age=round(age, 1),
                spending_need=round(spending_need, 2),
                taxes_funded=round(taxes_funded, 2),
                net_spendable=round(net_spendable, 2),
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

            # Working-year surplus is future bridge cash, not money that
            # disappears from the plan after paying this year's expenses.
            cash_balance += max(0.0, net_spendable - spending_need)

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
        dynamic_itemized = self._itemized_config(tax_config).get("enabled", False)
        inflation = config.expected_inflation_rate / 100
        window_years = max(1, window_end.year - window_start.year + 1)
        mortgage_interest_by_year = await self._mortgage_interest_by_year(
            config, tax_config, window_start.year, window_years,
        )

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

            def tax_at_income(income: float) -> tuple[float, float, float]:
                """Federal taxable income, federal tax, and CA tax."""
                federal_mortgage_interest, state_mortgage_interest = (
                    mortgage_interest_by_year.get(current.year, (0.0, 0.0))
                )
                state_itemized, _, _ = self._state_itemized_deduction(
                    income,
                    tax_config,
                    state_mortgage_interest,
                    year=current.year,
                    inflation_rate=inflation,
                )
                state_deduction = None
                state_method = None
                if state_itemized is not None:
                    state_standard = (
                        float(tax_config["state_standard_deduction"])
                        if tax_config.get("state_standard_deduction") is not None
                        else CA_STANDARD_DEDUCTION_2025.get(
                            filing_status, CA_STANDARD_DEDUCTION_2025["single"],
                        )
                    )
                    state_deduction, state_method = self._select_deduction(
                        tax_config.get("state_deduction_method", "greater_of"),
                        state_standard,
                        state_itemized,
                    )
                state_tax = self.compute_configured_state_tax(
                    income,
                    tax_config,
                    deduction_override=state_deduction,
                    deduction_method_override=state_method,
                ).total_tax
                federal_itemized, _ = self._federal_itemized_deduction(
                    current.year,
                    income,
                    state_tax,
                    tax_config,
                    federal_mortgage_interest,
                    inflation_rate=inflation,
                )
                federal_deduction = std_deduction
                if federal_itemized is not None:
                    federal_deduction = self._select_deduction(
                        tax_config.get("federal_deduction_method", "greater_of"),
                        self._get_federal_standard_deduction(tax_config),
                        federal_itemized,
                    )[0]
                taxable = max(0.0, income - federal_deduction)
                federal_tax = self.compute_federal_tax(
                    taxable, filing_status, brackets,
                ).total_federal_tax
                return taxable, federal_tax, state_tax

            # Room to fill so POST-DEDUCTION taxable income lands at the
            # target bracket ceiling. With dynamic itemization, solve again as
            # the SALT and California limitations respond to the conversion.
            taxable_baseline, baseline_federal_tax, baseline_state_tax = tax_at_income(
                baseline_income,
            )
            room_to_target = max(0, target_ceiling + std_deduction - baseline_income)
            if dynamic_itemized:
                conversion_guess = room_to_target
                for _ in range(8):
                    taxable_guess, _, _ = tax_at_income(
                        baseline_income + conversion_guess,
                    )
                    conversion_guess = max(
                        0.0, conversion_guess + target_ceiling - taxable_guess,
                    )
                room_to_target = conversion_guess

            # Convert up to the room available (don't exceed deferred balance)
            conversion = min(room_to_target, deferred_balance)
            if conversion <= 0:
                current += relativedelta(years=1)
                continue

            # Tax on conversion, including income-dependent itemized deductions.
            total_taxable, federal_with, state_with = tax_at_income(
                baseline_income + conversion,
            )
            conversion_tax = federal_with - baseline_federal_tax
            if self._is_california(tax_config) or dynamic_itemized:
                conversion_tax += state_with - baseline_state_tax
            else:
                conversion_tax += self.compute_state_tax(
                    conversion, tax_config["state_tax_rate"],
                )

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
        state_wage_income = sum(
            _prorated_annual_for_year(s, current_year)
            for s in income_sources
            if s.income_type.value in ("salary", "bonus")
            and s.is_active and s.is_taxable
        )

        state_agi = total_income
        if self._is_california(tax_config):
            state_agi -= sum(
                _prorated_annual_for_year(s, current_year)
                for s in income_sources
                if s.income_type.value == "social_security"
                and s.is_active and s.is_taxable
            )
        mortgage_schedule = await self._mortgage_interest_by_year(
            config, tax_config, current_year, 1,
        )
        federal_mortgage_interest, state_mortgage_interest = (
            mortgage_schedule.get(current_year, (0.0, 0.0))
        )
        state_itemized, state_itemized_before_limit, state_itemized_limitation = (
            self._state_itemized_deduction(
                state_agi,
                tax_config,
                state_mortgage_interest,
                year=current_year,
                inflation_rate=config.expected_inflation_rate / 100,
            )
        )
        state_deduction = None
        state_deduction_method = None
        if state_itemized is not None:
            state_standard = (
                float(tax_config["state_standard_deduction"])
                if tax_config.get("state_standard_deduction") is not None
                else CA_STANDARD_DEDUCTION_2025.get(
                    filing_status, CA_STANDARD_DEDUCTION_2025["single"],
                )
            )
            state_deduction, state_deduction_method = self._select_deduction(
                tax_config.get("state_deduction_method", "greater_of"),
                state_standard,
                state_itemized,
            )
        state_breakdown = self.compute_configured_state_tax(
            state_agi,
            tax_config,
            earned_income=state_wage_income,
            deduction_override=state_deduction,
            deduction_method_override=state_deduction_method,
        )
        state_tax = state_breakdown.total_tax
        federal_itemized, salt_deduction = self._federal_itemized_deduction(
            current_year,
            total_income,
            state_tax,
            tax_config,
            federal_mortgage_interest,
            inflation_rate=config.expected_inflation_rate / 100,
        )
        federal_deduction = std_deduction
        federal_deduction_method = self._get_federal_deduction_method(tax_config)
        if federal_itemized is not None:
            federal_deduction, federal_deduction_method = self._select_deduction(
                tax_config.get("federal_deduction_method", "greater_of"),
                self._get_federal_standard_deduction(tax_config),
                federal_itemized,
            )
        taxable_income = max(0, total_income - federal_deduction)
        federal = self.compute_federal_tax(taxable_income, filing_status, brackets)
        fica = self.compute_fica(earned_income, filing_status)
        room = self.get_bracket_room(taxable_income, filing_status, brackets)
        effective = self.compute_effective_rate(total_income, federal.total_federal_tax, state_tax, fica)

        # Account balances by tax treatment
        accounts = await self.get_accounts_by_tax_treatment()
        basis_estimate = self.estimate_taxable_cost_basis(
            accounts.taxable, tax_config.get("cost_basis_pct", 0.60),
        )

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
            "standard_deduction": round(federal_deduction, 2),
            "federal_deduction_method": federal_deduction_method,
            "federal_itemized_deduction": round(federal_itemized or 0.0, 2),
            "federal_salt_deduction": round(salt_deduction, 2),
            "federal_mortgage_interest": round(federal_mortgage_interest, 2),
            "taxable_income": round(taxable_income, 2),
            "federal_tax": federal.total_federal_tax,
            "federal_brackets": federal.brackets,
            "federal_effective_rate": federal.effective_rate,
            "federal_marginal_rate": federal.marginal_rate,
            "state_tax": state_tax,
            "state": str(tax_config.get("state") or "").upper(),
            "state_gross_income": round(state_agi, 2),
            "state_rate": round(state_breakdown.marginal_rate * 100, 2),
            "state_tax_method": state_breakdown.method,
            "state_taxable_income": state_breakdown.taxable_income,
            "state_standard_deduction": state_breakdown.standard_deduction,
            "state_deduction_method": state_breakdown.deduction_method,
            "state_itemized_before_limit": round(state_itemized_before_limit, 2),
            "state_itemized_limitation": round(state_itemized_limitation, 2),
            "state_mortgage_interest": round(state_mortgage_interest, 2),
            "state_income_tax": state_breakdown.income_tax,
            "state_payroll_tax": state_breakdown.payroll_tax,
            "state_effective_rate": state_breakdown.effective_rate,
            "state_marginal_rate": state_breakdown.marginal_rate,
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
                "taxable_accounts": basis_estimate["accounts"],
                "taxable_cost_basis": basis_estimate["cost_basis"],
                "taxable_cost_basis_pct": round(basis_estimate["cost_basis_pct"], 4),
                "taxable_basis_coverage_pct": basis_estimate["coverage_pct"],
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
        std_deduction = self._get_standard_deduction(tax_config)
        brackets = self._get_brackets(tax_config)

        scenario_income = base["gross_income"] + extra_income + roth_conversion
        scenario_state_agi = (
            base["state_gross_income"] + extra_income + roth_conversion
        )
        scenario_state_itemized, _, _ = self._state_itemized_deduction(
            scenario_state_agi,
            tax_config,
            base["state_mortgage_interest"],
            year=date.today().year,
            inflation_rate=config.expected_inflation_rate / 100,
        )
        scenario_state_deduction = None
        scenario_state_method = None
        if scenario_state_itemized is not None:
            state_standard = (
                float(tax_config["state_standard_deduction"])
                if tax_config.get("state_standard_deduction") is not None
                else CA_STANDARD_DEDUCTION_2025.get(
                    filing_status, CA_STANDARD_DEDUCTION_2025["single"],
                )
            )
            scenario_state_deduction, scenario_state_method = self._select_deduction(
                tax_config.get("state_deduction_method", "greater_of"),
                state_standard,
                scenario_state_itemized,
            )
        scenario_state_breakdown = self.compute_configured_state_tax(
            scenario_state_agi,
            tax_config,
            extra_deduction=extra_deduction,
            deduction_override=scenario_state_deduction,
            deduction_method_override=scenario_state_method,
        )
        scenario_state = scenario_state_breakdown.income_tax + base["state_payroll_tax"]

        scenario_federal_itemized, _ = self._federal_itemized_deduction(
            date.today().year,
            scenario_income,
            scenario_state,
            tax_config,
            base["federal_mortgage_interest"],
            inflation_rate=config.expected_inflation_rate / 100,
        )
        scenario_federal_deduction = std_deduction
        if scenario_federal_itemized is not None:
            scenario_federal_deduction = self._select_deduction(
                tax_config.get("federal_deduction_method", "greater_of"),
                self._get_federal_standard_deduction(tax_config),
                scenario_federal_itemized,
            )[0]
        scenario_deductions = scenario_federal_deduction + extra_deduction
        scenario_taxable = max(0, scenario_income - scenario_deductions)
        scenario_federal = self.compute_federal_tax(
            scenario_taxable, filing_status, brackets,
        )
        # Roth conversions and the generic extra-income input are not treated
        # as wages, so current FICA/SDI carry through rather than increasing.
        scenario_total = (
            scenario_federal.total_federal_tax + scenario_state + base["fica_tax"]
        )

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
