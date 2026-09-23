export interface CashflowEvent {
  id: string;
  name: string;
  event_type: "income" | "expense";
  amount_cents: number;
  amount: number;
  date: string;
  is_recurring: boolean;
  recurrence: string | null;
  end_date: string | null;
  probability: number;
  status: string;
  category: string | null;
  linked_account_id: string | null;
  notes: string | null;
  created_at: string | null;
}

export interface CashflowEventCreate {
  name: string;
  event_type: "income" | "expense";
  amount: number;
  date: string;
  is_recurring?: boolean;
  recurrence?: string | null;
  end_date?: string | null;
  probability?: number;
  status?: string;
  category?: string | null;
  notes?: string | null;
}

export interface MonthlyProjectionPoint {
  month: string;
  starting_cash: number;
  income: number;
  expenses: number;
  net: number;
  ending_cash: number;
  events: string[];
  /** Month 0 only: the row covers this day → month end (earlier days are in the balance). */
  from_day?: number | null;
}

/** A trailing-window payment left out of the burn because it paid a cashflow event. */
export interface BurnExclusion {
  event_name: string;
  amount: number;
  date: string;
  merchant?: string | null;
}

/** A pinned property sale from the active plan, as the Runway applies it. */
export interface ScenarioSale {
  key: string;
  month: string;
  month_index: number;
  net_proceeds: number;
  proceeds_to: string;
  burn_change: number;
  income_change: number;
}

export interface RunwayResponse {
  current_cash: number;
  monthly_burn: number;
  monthly_income: number;
  net_monthly: number;
  months_remaining: number | null;
  cash_zero_date: string | null;
  income_provenance: "override" | "modeled";
  trailing_burn: number;
  trailing_income: number;
  projection: MonthlyProjectionPoint[];
  scenario_name?: string | null;
  scenario_sales?: ScenarioSale[];
  /** Trailing 90-day average with nothing excluded (trailing_burn excludes event-matched payments). */
  trailing_burn_raw?: number;
  burn_exclusions?: BurnExclusion[];
}
