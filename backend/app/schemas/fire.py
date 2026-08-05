import datetime as _dt
from uuid import UUID

from pydantic import BaseModel, computed_field

from app.models.enums import GoalStatus, GoalType, IncomeType


# --- FIRE Config Schemas ---


class FireConfigResponse(BaseModel):
    id: UUID
    date_of_birth: _dt.date | None = None
    target_retirement_age: int | None = None
    target_retirement_date: _dt.date | None = None
    life_expectancy: int = 90
    fire_variant: str = "regular"
    safe_withdrawal_rate: float = 4.0
    expected_annual_return: float = 7.0
    expected_inflation_rate: float = 3.0
    target_annual_spending: int | None = None
    social_security_monthly: int | None = None
    social_security_start_age: int = 67
    pension_monthly: int | None = None
    pension_start_age: int | None = None
    healthcare_monthly_cost: int | None = None
    medicare_start_age: int = 65
    rmd_start_age: int = 73
    state_tax_rate: float | None = None
    federal_marginal_rate: float | None = None
    target_legacy: int = 0
    notes: str | None = None
    custom_assumptions: dict | None = None
    updated_at: _dt.datetime | None = None

    model_config = {"from_attributes": True}


class FireConfigUpdate(BaseModel):
    date_of_birth: _dt.date | None = None
    target_retirement_age: int | None = None
    target_retirement_date: _dt.date | None = None
    life_expectancy: int | None = None
    fire_variant: str | None = None
    safe_withdrawal_rate: float | None = None
    expected_annual_return: float | None = None
    expected_inflation_rate: float | None = None
    target_annual_spending: int | None = None
    social_security_monthly: int | None = None
    social_security_start_age: int | None = None
    pension_monthly: int | None = None
    pension_start_age: int | None = None
    healthcare_monthly_cost: int | None = None
    medicare_start_age: int | None = None
    rmd_start_age: int | None = None
    target_legacy: int | None = None
    state_tax_rate: float | None = None
    federal_marginal_rate: float | None = None
    notes: str | None = None
    custom_assumptions: dict | None = None


class FireConfigHistoryEntry(BaseModel):
    """One prior state of fire_config, captured by the DB trigger (fire-master#10)."""

    id: int
    config_id: UUID | None = None
    op: str
    changed_at: _dt.datetime
    data: dict

    model_config = {"from_attributes": True}


# --- Income Source Schemas ---


class IncomeSourceCreate(BaseModel):
    name: str
    income_type: IncomeType
    annual_amount: int
    frequency: str = "monthly"
    start_date: _dt.date | None = None
    end_date: _dt.date | None = None
    growth_rate: float | None = None
    is_taxable: bool = True
    tax_treatment: str | None = None
    linked_account_id: UUID | None = None
    notes: str | None = None
    custom_data: dict | None = None


class IncomeSourceUpdate(BaseModel):
    name: str | None = None
    income_type: IncomeType | None = None
    annual_amount: int | None = None
    frequency: str | None = None
    start_date: _dt.date | None = None
    end_date: _dt.date | None = None
    growth_rate: float | None = None
    is_taxable: bool | None = None
    tax_treatment: str | None = None
    linked_account_id: UUID | None = None
    notes: str | None = None
    custom_data: dict | None = None


class IncomeSourceResponse(BaseModel):
    id: UUID
    name: str
    income_type: IncomeType
    annual_amount_cents: int
    frequency: str
    start_date: _dt.date | None = None
    end_date: _dt.date | None = None
    growth_rate: float | None = None
    is_taxable: bool
    tax_treatment: str | None = None
    is_active: bool
    linked_account_id: UUID | None = None
    notes: str | None = None
    custom_data: dict | None = None

    @computed_field
    @property
    def annual_amount(self) -> float:
        return self.annual_amount_cents / 100

    @computed_field
    @property
    def monthly_amount(self) -> float:
        return self.annual_amount_cents / 100 / 12

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, src) -> "IncomeSourceResponse":
        return cls(
            id=src.id,
            name=src.name,
            income_type=src.income_type,
            annual_amount_cents=src.annual_amount,
            frequency=src.frequency,
            start_date=src.start_date,
            end_date=src.end_date,
            growth_rate=src.growth_rate,
            is_taxable=src.is_taxable,
            tax_treatment=src.tax_treatment,
            is_active=src.is_active,
            linked_account_id=src.linked_account_id,
            notes=src.notes,
            custom_data=src.custom_data,
        )


class IncomeSummaryResponse(BaseModel):
    sources: list[IncomeSourceResponse]
    total_annual_cents: int
    total_monthly_cents: int
    by_type: dict[str, float]

    @computed_field
    @property
    def total_annual(self) -> float:
        return self.total_annual_cents / 100


# --- Goal Schemas ---


class GoalCreate(BaseModel):
    name: str
    goal_type: GoalType
    target_value: int | None = None
    target_percentage: float | None = None
    target_date: _dt.date | None = None
    metric_query: dict | None = None
    notes: str | None = None


class GoalUpdate(BaseModel):
    name: str | None = None
    target_value: int | None = None
    target_percentage: float | None = None
    target_date: _dt.date | None = None
    status: GoalStatus | None = None
    metric_query: dict | None = None
    notes: str | None = None


class GoalResponse(BaseModel):
    id: UUID
    name: str
    goal_type: GoalType
    target_value: int | None = None
    target_percentage: float | None = None
    target_date: _dt.date | None = None
    current_value: int | None = None
    current_percentage: float | None = None
    progress_pct: float | None = None
    status: GoalStatus
    metric_query: dict | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


# --- Projection Schemas ---


class NetWorthBreakdown(BaseModel):
    liquid: float  # cash, checking, crypto
    retirement: float  # 401k, IRAs, RRSP, Roth
    real_estate_equity: float  # property values net of mortgages
    illiquid_private: float  # locked private investments
    other: float  # vehicles, depreciating assets


class FireNumberResponse(BaseModel):
    fire_number: float
    annual_spending: float
    safe_withdrawal_rate: float
    current_net_worth: float
    gap: float
    progress_pct: float
    # Accessible net worth: excludes illiquid, RE equity, depreciating
    accessible_net_worth: float | None = None
    accessible_progress_pct: float | None = None
    net_worth_breakdown: NetWorthBreakdown | None = None


class WealthPoolPoint(BaseModel):
    date: str
    age: float
    cash: float  # liquid pool (bridge funds)
    ira_sepp: float  # IRA-A: SEPP source, depleting
    ira_growth: float  # IRA-B: untouched until 59½, growing
    rrsp: float  # RRSP/RRIF drawdown pool
    real_estate: float  # RE equity (net of mortgages)
    illiquid: float  # private investments (vesting over time)
    taxable: float = 0  # taxable brokerage pool (e.g. property-sale proceeds compounding at market rate)
    taxable_draw: float = 0  # monthly withdrawal from taxable brokerage to cover cash gap
    total: float  # all pools combined
    income: float  # monthly income (non-IRA)
    expenses: float  # monthly expenses
    ira_draw: float  # monthly IRA withdrawal
    rrsp_draw: float = 0  # monthly RRIF withdrawal
    cash_interest: float = 0  # monthly interest earned
    month: int | None = None  # month offset from start
    event: str | None = None  # event marker label


class WealthPoolProjection(BaseModel):
    points: list[WealthPoolPoint]
    events: list[dict]  # [{month, label, color}]
    cash_zero_month: int | None  # month when cash hits zero (if ever)
    total_at_end: float
    sepp_monthly: float  # SEPP payment amount
    sepp_depletes_age: float | None  # age when IRA-A hits zero
    demo_persona: bool = False  # config still carries the seeded persona — data is synthetic


class SpendingSensitivityPoint(BaseModel):
    monthly_spending: float  # dollars
    total_at_end: float
    cash_zero_month: int | None


class SpendingBreakdown(BaseModel):
    """What's inside the total monthly budget."""
    primary_property_all_in: float  # P&I + HOA + insurance + utilities
    primary_property_pi: float  # just the mortgage P&I portion
    income_property_cost: float  # income-property costs saved when it sells
    secondary_property_cost: float  # secondary-property costs removed when it sells
    non_housing: float  # remainder — groceries, transport, insurance, discretionary
    post_sale_rent: float  # replaces primary-property costs after sale


class SpendingSensitivityResponse(BaseModel):
    current_monthly: float  # dollars (from config)
    base_monthly: float  # dollars (base spending, excludes healthcare)
    healthcare_monthly: float  # dollars
    breakdown: SpendingBreakdown  # what's inside the total
    points: list[SpendingSensitivityPoint]


class ProjectionPoint(BaseModel):
    date: str
    age: float
    net_worth: float
    income: float
    spending: float
    savings: float
    phase: str  # "accumulation" or "retirement"
    # Tax-aware fields (populated when tax_aware=True)
    tax_owed: float | None = None
    effective_rate: float | None = None
    roth_conversion: float | None = None
    magi: float | None = None


class LifetimeProjectionResponse(BaseModel):
    points: list[ProjectionPoint]
    scenario: str
    retirement_date: str | None
    retirement_age: float | None
    net_worth_at_retirement: float
    net_worth_at_end: float
    lowest_net_worth: float
    money_lasts: bool


class RetirementTimelineResponse(BaseModel):
    conservative: LifetimeProjectionResponse
    moderate: LifetimeProjectionResponse
    optimistic: LifetimeProjectionResponse
    projected_retirement_date: str | None
    months_remaining: int | None
    on_track: bool


class ScenarioInput(BaseModel):
    savings_rate_change: float | None = None
    income_change: int | None = None
    spending_change: int | None = None
    spending_category_changes: dict[str, int] | None = None
    return_rate_override: float | None = None
    one_time_windfall: int | None = None
    one_time_expense: int | None = None
    retirement_age_override: int | None = None
    life_expectancy_override: int | None = None
    swr_override: float | None = None


class ScenarioComparison(BaseModel):
    base: LifetimeProjectionResponse
    scenario: LifetimeProjectionResponse
    retirement_date_delta_days: int | None
    net_worth_at_retirement_delta: float
    net_worth_at_end_delta: float
    money_lasts_change: str  # "unchanged", "now_lasts", "no_longer_lasts"


class IncomeStream(BaseModel):
    label: str
    monthly: float  # dollars/mo
    color: str


class UpcomingEvent(BaseModel):
    name: str
    date: str
    amount: float
    event_type: str  # "income" or "expense"


class BridgeStatus(BaseModel):
    cash_balance: float
    monthly_burn: float
    monthly_income_total: float  # non-IRA recurring
    monthly_ira_total: float  # SEPP + RRSP
    monthly_deficit: float  # burn - income - ira (positive = shortfall)
    cash_runway_months: int | None  # how long cash lasts at current deficit
    income_streams: list[IncomeStream]
    # Post-sale comparison (after selling the primary property)
    post_sale_burn: float
    post_sale_income_total: float
    post_sale_deficit: float
    # Upcoming events
    upcoming_events: list[UpcomingEvent]


class ReadinessBreakdown(BaseModel):
    savings_progress: float
    savings_rate: float
    income_stability: float
    expense_stability: float
    healthcare_plan: float
    emergency_fund: float
    goal_progress: float


class ReadinessResponse(BaseModel):
    score: float
    breakdown: ReadinessBreakdown
    fire_number: float
    current_net_worth: float
    progress_pct: float


class Milestone(BaseModel):
    age: float
    date: str
    label: str
    description: str
    financial_impact: str  # e.g., "$837K accessible" or "$4,600/mo income"
    status: str  # "past", "upcoming", "distant"


class MilestonesResponse(BaseModel):
    milestones: list[Milestone]
    current_age: float
    bridge_end_age: float  # 59.5 — penalty-free access
    bridge_months_remaining: int


class FireMetricsResponse(BaseModel):
    fire_number: FireNumberResponse
    readiness: ReadinessResponse
    timeline: RetirementTimelineResponse | None
    income_summary: IncomeSummaryResponse
    savings_rate_current: float | None
    savings_rate_average: float | None
    net_worth_growth_30d: float | None
    net_worth_growth_1y: float | None
    goals: list[GoalResponse]


# --- FIRE Scenarios ---


class FireScenarioCreate(BaseModel):
    name: str
    description: str | None = None
    overrides: dict | None = None
    is_active: bool = False


class FireScenarioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    overrides: dict | None = None


class FireScenarioResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    is_active: bool
    overrides: dict | None = None
    created_at: _dt.datetime
    updated_at: _dt.datetime

    model_config = {"from_attributes": True}
