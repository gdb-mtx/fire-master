// Property P&L module types. All money values are dollars (API converts from cents).

export interface Property {
  id: string;
  key: string;
  name: string;
  address: string | null;
  color: string | null;
  value: number;
  loan_balance: number;
  equity: number;
  purchase_price: number;
  purchase_date: string | null;
  potential_monthly_rental: number | null;
  potential_monthly_rental_full: number | null;
  display_order: number;
  is_active: boolean;
  notes: string[];
  extra_data: Record<string, unknown>;
}

export interface PropertyPnLTotals {
  total_cost: number;
  rental_actual: number;
  rental_potential: number;
  net_cost: number;
  monthly_cost: number;
  net_per_year: number;
  /** Calendar months the per-month figures divide by, counted from first_month. */
  months: number;
  /** "YYYY-MM": the later of the period start and the property's first transaction. */
  first_month: string | null;
}

export interface PropertyPnL {
  property: Property;
  // category -> "YYYY-MM" -> dollars
  categories: Record<string, Record<string, number>>;
  totals: PropertyPnLTotals;
  cost_per_equity: number | null;
}

export interface PnLResponse {
  months: string[];
  properties: PropertyPnL[];
}

export interface PropertyRule {
  id: string;
  property_id: string | null;
  match_type: string;
  pattern: string;
  expense_category: string | null;
  require_tx_category: string | null;
  rule_kind: "expense" | "income" | "exclusion";
  priority: number;
  is_active: boolean;
  notes: string | null;
}

export interface PropertyTransaction {
  id: string;
  date: string;
  merchant: string | null;
  category: string | null;
  amount: number;
  property_id: string | null;
  property_category: string | null;
  property_source: "rule" | "manual" | "monarch_tag" | null;
}

export interface PropertyTransactionsResponse {
  transactions: PropertyTransaction[];
  total: number;
}

export interface ReclassifyResult {
  matched: number;
  unmatched: number;
  skipped_manual: number;
}

export interface CategoriesResponse {
  expense_categories: string[];
  income_category: string;
}

export interface ManualEntryInput {
  property_id: string;
  date: string;
  amount: number; // signed dollars
  property_category: string;
  merchant?: string | null;
  notes?: string | null;
}
