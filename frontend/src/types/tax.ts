// --- Bracket Analysis ---

export interface BracketDetail {
  rate: number;
  income_in_bracket: number;
  tax_in_bracket: number;
  bracket_floor: number;
  bracket_ceiling: number | null;
}

export interface BracketRoomInfo {
  current_rate: number;
  next_rate: number | null;
  room_dollars: number;
}

export interface AccountBalanceDetail {
  name: string;
  balance: number;
  cost_basis?: number | null;
  cost_basis_pct?: number | null;
  basis_source?: string | null;
}

export interface AccountBalanceSummary {
  tax_deferred: number;
  tax_free: number;
  taxable: number;
  already_taxed: number;
  tax_deferred_accounts: AccountBalanceDetail[];
  tax_free_accounts: AccountBalanceDetail[];
  taxable_accounts: AccountBalanceDetail[];
  taxable_cost_basis: number;
  taxable_cost_basis_pct: number;
  taxable_basis_coverage_pct: number;
}

export interface ACASnapshot {
  magi: number;
  fpl_percentage: number;
  subsidy_eligible: boolean;
  monthly_premium: number;
  monthly_subsidy: number;
  net_monthly_cost: number;
  cliff_distance: number;
  cliff_warning: boolean;
}

export interface BracketAnalysis {
  filing_status: string;
  gross_income: number;
  standard_deduction: number;
  federal_deduction_method: string;
  federal_itemized_deduction: number;
  federal_salt_deduction: number;
  federal_mortgage_interest: number;
  taxable_income: number;
  federal_tax: number;
  federal_brackets: BracketDetail[];
  federal_effective_rate: number;
  federal_marginal_rate: number;
  state_tax: number;
  state: string;
  state_gross_income: number;
  state_rate: number;
  state_tax_method: string;
  state_taxable_income: number;
  state_standard_deduction: number;
  state_deduction_method: string;
  state_itemized_before_limit: number;
  state_itemized_limitation: number;
  state_mortgage_interest: number;
  state_income_tax: number;
  state_payroll_tax: number;
  state_effective_rate: number;
  state_marginal_rate: number;
  fica_tax: number;
  total_tax: number;
  overall_effective_rate: number;
  bracket_room: BracketRoomInfo;
  account_balances: AccountBalanceSummary;
  aca: ACASnapshot;
}

// --- Withdrawal Plan ---

export interface WithdrawalYear {
  year: number;
  age: number;
  spending_need: number;
  taxes_funded: number;
  net_spendable: number;
  from_taxable: number;
  from_deferred: number;
  from_roth: number;
  from_cash: number;
  roth_conversion: number;
  total_income: number;
  ordinary_income: number;
  capital_gains_income: number;
  federal_tax: number;
  state_tax: number;
  fica_tax: number;
  total_tax: number;
  effective_rate: number;
  after_tax_income: number;
  magi: number;
}

export interface WithdrawalPlan {
  years: WithdrawalYear[];
  total_tax_paid: number;
  average_effective_rate: number;
  total_withdrawn: number;
}

// --- Roth Conversion Plan ---

export interface RothConversionYear {
  year: number;
  age: number;
  baseline_income: number;
  conversion_amount: number;
  total_taxable_income: number;
  tax_on_conversion: number;
  cumulative_converted: number;
  magi_after_conversion: number;
  bracket_filled_to: number;
}

export interface RothConversionPlan {
  years: RothConversionYear[];
  total_converted: number;
  total_tax_paid: number;
  estimated_tax_saved: number;
  target_bracket_rate: number;
  conversion_window: string;
  /** Assumed future-RMD marginal rate behind estimated_tax_saved. */
  assumed_rmd_marginal_rate: number;
}

// --- SEPP / 72(t) ---

export interface SEPPMethodResult {
  annual: number;
  monthly: number;
}

export interface SEPPReverseResult {
  target_monthly: number;
  /** IRA-A size that yields the target under fixed amortization. */
  required_balance: number;
}

export interface SEPPResponse {
  balance: number;
  age: number;
  rate: number;
  /** max(5%, 120% of mid-term AFR) — Notice 2022-6 */
  max_rate: number;
  life_expectancy: number;
  amortization: SEPPMethodResult;
  /** Year-1 value; redetermined annually by design. */
  rmd_method: SEPPMethodResult;
  annuitization: SEPPMethodResult | null;
  reverse: SEPPReverseResult | null;
  assumptions: Record<string, string>;
}

// --- Tax Scenario ---

export interface TaxScenarioInput {
  roth_conversion?: number;
  extra_income?: number;
  extra_deduction?: number;
}

export interface TaxScenarioResponse {
  base: {
    gross_income: number;
    taxable_income: number;
    total_tax: number;
    effective_rate: number;
  };
  scenario: {
    gross_income: number;
    taxable_income: number;
    total_tax: number;
    effective_rate: number;
    roth_conversion: number;
    extra_income: number;
    extra_deduction: number;
  };
  delta: {
    additional_tax: number;
    marginal_rate_on_new_income: number;
  };
}

// --- Monte Carlo ---

export interface PercentileCurvePoint {
  age: number;
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
}

export interface MonteCarloResult {
  success_rate: number;
  percentile_10: number;
  percentile_25: number;
  percentile_50: number;
  percentile_75: number;
  percentile_90: number;
  mean_final_nw: number;
  total_runs: number;
  percentile_curves: PercentileCurvePoint[];
  worst_final_nw: number;
  best_final_nw: number;
  starting_spendable_assets: number;
  excluded_non_spendable_assets: number;
  assumptions?: Record<string, unknown> | null;
}

export interface RetirementConfidenceAge {
  confidence: number;
  earliest_age: number | null;
  success_rate: number | null;
  prior_age_success_rate: number | null;
}

export interface RetirementAgeAnalysis {
  configured_retirement_age: number | null;
  current_age: number;
  max_tested_age: number;
  runs_per_age: number;
  confidence_ages: RetirementConfidenceAge[];
}
