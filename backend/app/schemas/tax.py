"""Tax engine Pydantic schemas for API responses."""

from pydantic import BaseModel


# --- Bracket Analysis ---


class BracketDetail(BaseModel):
    rate: float
    income_in_bracket: float
    tax_in_bracket: float
    bracket_floor: float
    bracket_ceiling: float | None = None


class BracketRoomResponse(BaseModel):
    current_rate: float
    next_rate: float | None = None
    room_dollars: float


class AccountBalanceDetail(BaseModel):
    name: str
    balance: float


class AccountBalanceSummary(BaseModel):
    tax_deferred: float
    tax_free: float
    taxable: float
    already_taxed: float
    tax_deferred_accounts: list[AccountBalanceDetail] = []
    tax_free_accounts: list[AccountBalanceDetail] = []
    taxable_accounts: list[AccountBalanceDetail] = []


class ACASnapshot(BaseModel):
    magi: float
    fpl_percentage: float
    subsidy_eligible: bool
    monthly_premium: float
    monthly_subsidy: float
    net_monthly_cost: float
    cliff_distance: float
    cliff_warning: bool


class BracketAnalysisResponse(BaseModel):
    filing_status: str
    gross_income: float
    standard_deduction: float
    federal_deduction_method: str = "standard"
    taxable_income: float
    federal_tax: float
    federal_brackets: list[BracketDetail]
    federal_effective_rate: float
    federal_marginal_rate: float
    state_tax: float
    state: str = ""
    state_gross_income: float = 0
    state_rate: float
    state_tax_method: str = "flat_rate"
    state_taxable_income: float = 0
    state_standard_deduction: float = 0
    state_deduction_method: str = "standard"
    state_income_tax: float = 0
    state_payroll_tax: float = 0
    state_effective_rate: float = 0
    state_marginal_rate: float = 0
    fica_tax: float
    total_tax: float
    overall_effective_rate: float
    bracket_room: BracketRoomResponse
    account_balances: AccountBalanceSummary
    aca: ACASnapshot


# --- Withdrawal Plan ---


class WithdrawalYearResponse(BaseModel):
    year: int
    age: float
    spending_need: float = 0.0
    taxes_funded: float = 0.0
    net_spendable: float = 0.0
    from_taxable: float
    from_deferred: float
    from_roth: float
    from_cash: float = 0.0
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


class WithdrawalPlanResponse(BaseModel):
    years: list[WithdrawalYearResponse]
    total_tax_paid: float
    average_effective_rate: float
    total_withdrawn: float


# --- Roth Conversion Plan ---


class RothConversionYearResponse(BaseModel):
    year: int
    age: float
    baseline_income: float
    conversion_amount: float
    total_taxable_income: float
    tax_on_conversion: float
    cumulative_converted: float
    magi_after_conversion: float
    bracket_filled_to: float


class RothConversionPlanResponse(BaseModel):
    years: list[RothConversionYearResponse]
    total_converted: float
    total_tax_paid: float
    estimated_tax_saved: float
    target_bracket_rate: float
    conversion_window: str
    # Savings = converted × this assumed future-RMD marginal rate − tax paid.
    # Config-driven (custom_assumptions.tax.assumed_rmd_marginal_rate).
    assumed_rmd_marginal_rate: float = 0.24


# --- ACA Analysis ---


class ACAAnalysisResponse(BaseModel):
    magi: float
    household_size: int
    fpl: float
    fpl_percentage: float
    subsidy_eligible: bool
    estimated_monthly_premium: float
    estimated_monthly_subsidy: float
    estimated_net_monthly_cost: float
    cliff_distance: float
    cliff_warning: bool


# --- Tax Scenario ---


class TaxSituationSnapshot(BaseModel):
    gross_income: float
    taxable_income: float
    total_tax: float
    effective_rate: float


class TaxScenarioSnapshot(BaseModel):
    gross_income: float
    taxable_income: float
    total_tax: float
    effective_rate: float
    roth_conversion: float
    extra_income: float
    extra_deduction: float


class TaxScenarioDelta(BaseModel):
    additional_tax: float
    marginal_rate_on_new_income: float


class TaxScenarioResponse(BaseModel):
    base: TaxSituationSnapshot
    scenario: TaxScenarioSnapshot
    delta: TaxScenarioDelta


class TaxScenarioInput(BaseModel):
    roth_conversion: float = 0
    extra_income: float = 0
    extra_deduction: float = 0


# --- Monte Carlo ---


class PercentileCurvePoint(BaseModel):
    age: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float


class MonteCarloResponse(BaseModel):
    success_rate: float
    percentile_10: float
    percentile_25: float
    percentile_50: float
    percentile_75: float
    percentile_90: float
    mean_final_nw: float
    total_runs: int
    percentile_curves: list[PercentileCurvePoint]
    worst_final_nw: float
    best_final_nw: float
    # Model disclosure (additive; frontend tolerates absence)
    assumptions: dict | None = None


# --- SEPP / 72(t) ---


class SEPPMethodResult(BaseModel):
    annual: float
    monthly: float


class SEPPReverseResult(BaseModel):
    target_monthly: float
    required_balance: float  # IRA-A size that yields the target (amortization)


class SEPPResponse(BaseModel):
    balance: float
    age: int
    rate: float
    max_rate: float  # max(5%, 120% of mid-term AFR) — Notice 2022-6
    life_expectancy: float  # Single Life Table divisor used
    amortization: SEPPMethodResult
    rmd_method: SEPPMethodResult  # year-1 value; redetermined annually by design
    annuitization: SEPPMethodResult | None = None  # not implemented in v1
    reverse: SEPPReverseResult | None = None
    assumptions: dict
