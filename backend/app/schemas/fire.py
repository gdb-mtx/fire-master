import datetime as _dt
from uuid import UUID

from pydantic import BaseModel, computed_field, field_validator

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
    roth: float = 0  # Roth / tax-free pool (custom_assumptions.roth_pool), drawn last
    roth_draw: float = 0  # monthly withdrawal from the Roth pool to cover cash gap
    total: float  # all pools combined
    income: float  # monthly income (non-IRA)
    expenses: float  # monthly expenses
    ira_draw: float  # monthly IRA withdrawal (SEPP + IRA-B gap draw + any forced RMD)
    rmd_redeposit: float = 0  # part of ira_draw forced by an RMD and redeposited to taxable (not cash)
    rrsp_draw: float = 0  # monthly RRIF withdrawal
    cash_interest: float = 0  # monthly interest earned
    month: int | None = None  # month offset from start
    event: str | None = None  # event marker label
    # Reporting split of the month (Oct 2026 Retirement redesign). Additive: none
    # of these feed the legacy math. Display identity, per month:
    #   expenses + event_expense + Δcash + cash_invested
    #     = (income − ss_income − event_income + event_expense)   regular income
    #       + (event_income − one_time_income)                    recurring events
    #       + cash_interest + ss_income + one_time_income
    #       + (ira_draw − rmd_redeposit) + roth_draw + rrsp_draw + taxable_draw
    ss_income: float = 0  # Social Security inside `income`
    event_income: float = 0  # positive cashflow events + one-off lumps into cash (inside `income`)
    event_expense: float = 0  # negative cashflow events + one-off lumps out of cash, as a positive number
    one_time_income: float = 0  # the non-recurring part of event_income
    sepp_draw: float = 0  # the 72(t) payment inside ira_draw
    cash_cushion: float = 0  # cash target this month (projection.cash_cushion_months); 0 when unset
    cash_invested: float = 0  # cash above the cushion moved to the brokerage (invest_cash_above_cushion)


class ProjectionMilestone(BaseModel):
    """A dated moment in the plan, in plain words, computed from the data."""
    month: int  # month offset from today
    age: float
    kind: str  # sale | draw | age | money | warning
    label: str  # "Coastal Condo sold", "Social Security starts", an event's own name
    amount: float | None = None  # signed $: sale net proceeds, one-off event, monthly payment
    destination: str | None = None  # sale proceeds: "brokerage" | "cash"


class ProjectionAssumption(BaseModel):
    """One rule the projection rests on, as the effective config sets it."""
    key: str
    label: str
    detail: str  # one plain sentence (or two)
    value: str  # display value: "6.5% a year", "6 months", "not counted"
    kind: str  # setting (from the plan) | rule (how the engine works) | limit (not modeled yet)
    is_default: bool = False  # a setting the user never set: the engine default is in use


class CashRule(BaseModel):
    """How the projection decides when savings refill cash."""
    mode: str  # "cushion" (projection.cash_cushion_months set) | "threshold" (legacy)
    cushion_months: float | None = None
    cushion_floor: float = 0
    invest_above_cushion: bool = False
    threshold_months: float = 12  # legacy rule: pools pay once cash < N months of the gap
    # Which IRA money can refill cash (projection.open_all_iras_after_59_5).
    open_all_iras: bool = False  # on: the 72(t) account pays too once open; IRAs repair cash from 59½
    age_59_5_month: int = 0  # month offset of 59½ (0 once past): IRA money is out of reach before it
    ira_a_open_month: int | None = None  # the 72(t) account takes extra withdrawals from here; None = never


class WealthPoolProjection(BaseModel):
    points: list[WealthPoolPoint]
    events: list[dict]  # [{month, label, color}]
    cash_zero_month: int | None  # month when cash hits zero (if ever)
    total_at_end: float
    sepp_monthly: float  # SEPP payment amount
    sepp_depletes_age: float | None  # age when IRA-A hits zero
    demo_persona: bool = False  # config still carries the seeded persona — data is synthetic
    # Oct 2026 Retirement redesign (additive)
    starting_cash: float = 0  # cash at month 0, before the first month's flows
    end_age: int | None = None
    money_runs_out_month: int | None = None  # cash ≤ 0 with every spendable pool empty
    cash_rule: CashRule | None = None
    milestones: list[ProjectionMilestone] = []
    assumptions: list[ProjectionAssumption] = []


class SpendingSensitivityPoint(BaseModel):
    monthly_spending: float  # dollars
    total_at_end: float
    cash_zero_month: int | None


class PropertyCostLine(BaseModel):
    """One property's carrying cost, as the pool engine treats it (property_sales)."""
    label: str
    monthly_cost: float  # all-in carrying cost while held
    mortgage_pi: float = 0
    in_budget: bool  # True: inside target spending, removed at sale. False: added ON TOP while held.
    post_sale_rent: float = 0
    held: bool = False  # no sale date: kept for good, carrying cost never ends


class SpendingBreakdown(BaseModel):
    """What's inside the total monthly budget."""
    primary_property_all_in: float  # P&I + HOA + insurance + utilities
    primary_property_pi: float  # just the mortgage P&I portion
    income_property_cost: float  # income-property costs saved when it sells
    secondary_property_cost: float  # secondary-property costs removed when it sells
    non_housing: float  # remainder — groceries, transport, insurance, discretionary
    post_sale_rent: float  # replaces primary-property costs after sale
    # Generic path (custom_assumptions.property_sales): one line per property with its
    # in_base_burn treatment. Empty for legacy configs, which use the fields above.
    properties: list[PropertyCostLine] = []
    outside_budget_monthly: float = 0  # held costs the engine adds on top of the budget


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


def _validate_scenario_overrides(v: dict | None) -> dict | None:
    """Reject override blobs that would silently do nothing or crash projections.

    Top-level keys are fire_config settings; per-plan assumptions live under
    custom_assumptions. An unknown key used to be accepted and then ignored, so
    the scenario reported success while changing nothing; custom_assumptions:
    null was accepted and then crashed every projection while active.
    """
    if v is None:
        return v
    from sqlalchemy import Date  # local: keep schemas free of model imports at load

    from app.models.fire_config import SCENARIO_LOCKED_COLUMNS, FireConfig

    columns = FireConfig.__table__.columns
    if "custom_assumptions" in v and not isinstance(v["custom_assumptions"], dict):
        raise ValueError("custom_assumptions must be an object (send {} for none)")
    unknown = sorted(
        k for k in v
        if k != "custom_assumptions" and (k not in columns or k in SCENARIO_LOCKED_COLUMNS)
    )
    if unknown:
        raise ValueError(
            f"not fire_config settings: {', '.join(unknown)} — "
            "per-plan assumptions belong under custom_assumptions"
        )
    for key, value in v.items():
        col = columns.get(key)
        if col is not None and isinstance(value, str) and isinstance(col.type, Date):
            try:
                _dt.date.fromisoformat(value[:10])
            except ValueError:
                raise ValueError(f"{key} must be an ISO date (YYYY-MM-DD), got {value!r}")
    return v


class FireScenarioCreate(BaseModel):
    name: str
    description: str | None = None
    overrides: dict | None = None
    is_active: bool = False

    @field_validator("overrides")
    @classmethod
    def check_overrides(cls, v: dict | None) -> dict | None:
        return _validate_scenario_overrides(v)


class FireScenarioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    overrides: dict | None = None

    @field_validator("overrides")
    @classmethod
    def check_overrides(cls, v: dict | None) -> dict | None:
        return _validate_scenario_overrides(v)


class FireScenarioResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    is_active: bool
    overrides: dict | None = None
    created_at: _dt.datetime
    updated_at: _dt.datetime

    model_config = {"from_attributes": True}


class FireScenarioHistoryEntry(BaseModel):
    """One prior state of a fire_scenarios row, captured by the DB trigger."""

    id: int
    scenario_id: UUID | None = None
    op: str
    changed_at: _dt.datetime
    data: dict

    model_config = {"from_attributes": True}
