export interface FireConfig {
  id: string;
  date_of_birth: string | null;
  target_retirement_age: number | null;
  target_retirement_date: string | null;
  life_expectancy: number;
  fire_variant: string;
  safe_withdrawal_rate: number;
  expected_annual_return: number;
  expected_inflation_rate: number;
  target_annual_spending: number | null;
  social_security_monthly: number | null;
  social_security_start_age: number;
  pension_monthly: number | null;
  pension_start_age: number | null;
  healthcare_monthly_cost: number | null;
  medicare_start_age: number;
  rmd_start_age: number;
  state_tax_rate: number | null;
  federal_marginal_rate: number | null;
  notes: string | null;
  custom_assumptions: Record<string, unknown> | null;
  updated_at: string | null;
}

export interface IncomeSource {
  id: string;
  name: string;
  income_type: string;
  annual_amount_cents: number;
  annual_amount: number;
  monthly_amount: number;
  frequency: string;
  start_date: string | null;
  end_date: string | null;
  growth_rate: number | null;
  is_taxable: boolean;
  tax_treatment: string | null;
  is_active: boolean;
  linked_account_id: string | null;
  notes: string | null;
  custom_data: Record<string, unknown> | null;
}

export interface IncomeSummary {
  sources: IncomeSource[];
  total_annual_cents: number;
  total_annual: number;
  total_monthly_cents: number;
  by_type: Record<string, number>;
}

export interface Goal {
  id: string;
  name: string;
  goal_type: string;
  target_value: number | null;
  target_percentage: number | null;
  target_date: string | null;
  current_value: number | null;
  current_percentage: number | null;
  progress_pct: number | null;
  status: string;
  metric_query: Record<string, unknown> | null;
  notes: string | null;
}

export interface NetWorthBreakdown {
  liquid: number;
  retirement: number;
  real_estate_equity: number;
  illiquid_private: number;
  other: number;
}

export interface FireNumber {
  fire_number: number;
  annual_spending: number;
  safe_withdrawal_rate: number;
  current_net_worth: number;
  gap: number;
  progress_pct: number;
  accessible_net_worth: number | null;
  accessible_progress_pct: number | null;
  net_worth_breakdown: NetWorthBreakdown | null;
}

export interface ProjectionPoint {
  date: string;
  age: number;
  net_worth: number;
  income: number;
  spending: number;
  savings: number;
  phase: "accumulation" | "retirement";
}

export interface LifetimeProjection {
  points: ProjectionPoint[];
  scenario: string;
  retirement_date: string | null;
  retirement_age: number | null;
  net_worth_at_retirement: number;
  net_worth_at_end: number;
  lowest_net_worth: number;
  money_lasts: boolean;
}

export interface RetirementTimeline {
  conservative: LifetimeProjection;
  moderate: LifetimeProjection;
  optimistic: LifetimeProjection;
  projected_retirement_date: string | null;
  months_remaining: number | null;
  on_track: boolean;
}

export interface SpendingSensitivityPoint {
  monthly_spending: number;
  total_at_end: number;
  cash_zero_month: number | null;
}

export interface SpendingBreakdown {
  primary_property_all_in: number;
  primary_property_pi: number;
  income_property_cost: number;
  secondary_property_cost: number;
  non_housing: number;
  post_sale_rent: number;
}

export interface SpendingSensitivity {
  current_monthly: number;
  base_monthly: number;
  healthcare_monthly: number;
  breakdown: SpendingBreakdown;
  points: SpendingSensitivityPoint[];
}

export interface ReadinessBreakdown {
  savings_progress: number;
  savings_rate: number;
  income_stability: number;
  expense_stability: number;
  healthcare_plan: number;
  emergency_fund: number;
  goal_progress: number;
}

export interface Readiness {
  score: number;
  breakdown: ReadinessBreakdown;
  fire_number: number;
  current_net_worth: number;
  progress_pct: number;
}

export interface ScenarioInput {
  savings_rate_change?: number;
  income_change?: number;
  spending_change?: number;
  spending_category_changes?: Record<string, number>;
  return_rate_override?: number;
  one_time_windfall?: number;
  one_time_expense?: number;
  retirement_age_override?: number;
  life_expectancy_override?: number;
  swr_override?: number;
}

export interface ScenarioComparison {
  base: LifetimeProjection;
  scenario: LifetimeProjection;
  retirement_date_delta_days: number | null;
  net_worth_at_retirement_delta: number;
  net_worth_at_end_delta: number;
  money_lasts_change: "unchanged" | "now_lasts" | "no_longer_lasts";
}

export interface WealthPoolPoint {
  date: string;
  age: number;
  cash: number;
  ira_sepp: number;
  ira_growth: number;
  rrsp: number;
  real_estate: number;
  illiquid: number;
  taxable: number;
  taxable_draw?: number;
  roth?: number; // Roth / tax-free pool (custom_assumptions.roth_pool), drawn last
  roth_draw?: number;
  total: number;
  income: number;
  expenses: number;
  ira_draw: number; // SEPP + IRA-B gap draw + any forced RMD
  rmd_redeposit?: number; // part of ira_draw redeposited to taxable by an RMD (never reaches cash)
  rrsp_draw: number;
  cash_interest: number;
  month: number | null;
  event: string | null;
}

export interface WealthPoolProjection {
  points: WealthPoolPoint[];
  events: { month: number; age: number; label: string; color: string }[];
  cash_zero_month: number | null;
  total_at_end: number;
  sepp_monthly: number;
  sepp_depletes_age: number | null;
}

export interface Milestone {
  age: number;
  date: string;
  label: string;
  description: string;
  financial_impact: string;
  status: "past" | "upcoming" | "distant";
}

export interface MilestonesResponse {
  milestones: Milestone[];
  current_age: number;
  bridge_end_age: number;
  bridge_months_remaining: number;
}

export interface IncomeStream {
  label: string;
  monthly: number;
  color: string;
}

export interface UpcomingEvent {
  name: string;
  date: string;
  amount: number;
  event_type: "income" | "expense";
}

export interface BridgeStatus {
  cash_balance: number;
  monthly_burn: number;
  monthly_income_total: number;
  monthly_ira_total: number;
  monthly_deficit: number;
  cash_runway_months: number | null;
  income_streams: IncomeStream[];
  post_sale_burn: number;
  post_sale_income_total: number;
  post_sale_deficit: number;
  upcoming_events: UpcomingEvent[];
}

export interface FireMetrics {
  fire_number: FireNumber;
  readiness: Readiness;
  timeline: RetirementTimeline | null;
  income_summary: IncomeSummary;
  savings_rate_current: number | null;
  savings_rate_average: number | null;
  net_worth_growth_30d: number | null;
  net_worth_growth_1y: number | null;
  goals: Goal[];
}

export interface FireScenario {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  overrides: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}
