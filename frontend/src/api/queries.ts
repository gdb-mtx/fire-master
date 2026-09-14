import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchJSON, patchJSON, postJSON, putJSON, deleteJSON } from "./client";
import type {
  DashboardSummary,
  NetWorthHistory,
  Account,
  SyncStatus,
  SyncTriggerResponse,
  TokenResponse,
} from "../types/api";
import type {
  SpendingAnalysis,
  SpendingTrends,
  RecurringExpensesData,
  HeatmapData,
  TrackerSummary,
  TrackerDailyData,
  TrackerTransactionsData,
  TrackerCategoryItem,
} from "../types/spending";
import type { ConversationListResponse, ConversationDetail } from "../types/advisor";
import type {
  Property,
  PnLResponse,
  PropertyRule,
  PropertyTransactionsResponse,
  PropertyTransaction,
  ReclassifyResult,
  CategoriesResponse,
  ManualEntryInput,
} from "../types/property";
import type { AssetHubData, AccountDetail, AccountEnrichment } from "../types/assets";
import type {
  FireConfig,
  FireMetrics,
  FireNumber,
  IncomeSource,
  Goal,
  BridgeStatus,
  MilestonesResponse,
  Readiness,
  WealthPoolProjection,
  RetirementTimeline,
  LifetimeProjection,
  ScenarioInput,
  ScenarioComparison,
  FireScenario,
  SpendingSensitivity,
} from "../types/fire";
import type {
  BracketAnalysis,
  WithdrawalPlan,
  RothConversionPlan,
  MonteCarloResult,
  SEPPResponse,
  TaxScenarioInput,
  TaxScenarioResponse,
} from "../types/tax";
import type {
  CashflowEvent,
  CashflowEventCreate,
  RunwayResponse,
} from "../types/cashflow";
import type { LedgerResponse, LedgerFilters } from "../types/transaction";

const BASE = "/api";

// Auth
export function useLogin() {
  return useMutation({
    mutationFn: (creds: { username: string; password: string }) =>
      postJSON<TokenResponse>(`${BASE}/auth/login`, creds),
  });
}

// Dashboard
export function useDashboardSummary() {
  return useQuery({
    queryKey: ["dashboard", "summary"],
    queryFn: () => fetchJSON<DashboardSummary>(`${BASE}/dashboard/summary`),
  });
}

// Net Worth
export function useNetWorthHistory(range: string = "1y") {
  return useQuery({
    queryKey: ["net-worth", "history", range],
    queryFn: () =>
      fetchJSON<NetWorthHistory>(
        `${BASE}/net-worth/history?range=${range}&interval=daily`,
      ),
  });
}

// Accounts
export function useAccounts() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: () => fetchJSON<Account[]>(`${BASE}/accounts`),
  });
}

// Sync
export function useSyncStatus() {
  return useQuery({
    queryKey: ["sync", "status"],
    queryFn: () => fetchJSON<SyncStatus>(`${BASE}/sync/status`),
    refetchInterval: (query) =>
      query.state.data?.status === "syncing" ? 3000 : false,
  });
}

export function useTriggerSync() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      postJSON<SyncTriggerResponse>(`${BASE}/sync/monarch`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sync", "status"] });
    },
  });
}

// Spending
export function useSpendingAnalysis(range: string = "1y", groupByParent: boolean = false) {
  return useQuery({
    queryKey: ["spending", "analysis", range, groupByParent],
    queryFn: () =>
      fetchJSON<SpendingAnalysis>(
        `${BASE}/spending/analysis?range=${range}&group_by_parent=${groupByParent}`,
      ),
  });
}

export function useSpendingTrends(months: number = 12, topN: number = 8) {
  return useQuery({
    queryKey: ["spending", "trends", months, topN],
    queryFn: () =>
      fetchJSON<SpendingTrends>(
        `${BASE}/spending/trends?months=${months}&top_n=${topN}`,
      ),
  });
}

export function useRecurringExpenses() {
  return useQuery({
    queryKey: ["spending", "recurring"],
    queryFn: () =>
      fetchJSON<RecurringExpensesData>(`${BASE}/spending/recurring`),
  });
}

export function useSpendingHeatmap(months: number = 12, topN: number = 15) {
  return useQuery({
    queryKey: ["spending", "heatmap", months, topN],
    queryFn: () =>
      fetchJSON<HeatmapData>(
        `${BASE}/spending/heatmap?months=${months}&top_n=${topN}`,
      ),
  });
}

// Spending Tracker
export function useTrackerSummary(monthlyTarget: number = 5000, excludePlanned: boolean = false) {
  return useQuery({
    queryKey: ["spending", "tracker", monthlyTarget, excludePlanned],
    queryFn: () =>
      fetchJSON<TrackerSummary>(
        `${BASE}/spending/tracker?monthly_target=${monthlyTarget}&exclude_planned=${excludePlanned}`,
      ),
    staleTime: 60_000,
  });
}

export function useTrackerDaily(month?: string, monthlyTarget: number = 5000, excludePlanned: boolean = false) {
  return useQuery({
    queryKey: ["spending", "tracker-daily", month, monthlyTarget, excludePlanned],
    queryFn: () =>
      fetchJSON<TrackerDailyData>(
        `${BASE}/spending/tracker/daily?monthly_target=${monthlyTarget}&exclude_planned=${excludePlanned}${month ? `&month=${month}` : ""}`,
      ),
    staleTime: 60_000,
  });
}

export function useTrackerCategories(
  month?: string,
  excludePlanned: boolean = false,
) {
  return useQuery({
    queryKey: ["spending", "tracker-categories", month, excludePlanned],
    queryFn: () => {
      const params = new URLSearchParams();
      if (month) params.set("month", month);
      params.set("exclude_planned", String(excludePlanned));
      return fetchJSON<TrackerCategoryItem[]>(
        `${BASE}/spending/tracker/categories?${params}`,
      );
    },
    staleTime: 60_000,
  });
}

export function useTrackerTransactions(
  category?: string,
  month?: string,
  limit: number = 50,
  offset: number = 0,
  excludePlanned: boolean = false,
) {
  return useQuery({
    queryKey: ["spending", "tracker-tx", category, month, limit, offset, excludePlanned],
    queryFn: () => {
      const params = new URLSearchParams();
      if (category) params.set("category", category);
      if (month) params.set("month", month);
      params.set("limit", String(limit));
      params.set("offset", String(offset));
      params.set("exclude_planned", String(excludePlanned));
      return fetchJSON<TrackerTransactionsData>(
        `${BASE}/spending/tracker/transactions?${params}`,
      );
    },
    staleTime: 60_000,
  });
}

// Asset Hub
export function useAssetHub() {
  return useQuery({
    queryKey: ["asset-hub"],
    queryFn: () => fetchJSON<AssetHubData>(`${BASE}/accounts/asset-hub`),
  });
}

export function useAccountDetail(id: string | null) {
  return useQuery({
    queryKey: ["account", "detail", id],
    queryFn: () => fetchJSON<AccountDetail>(`${BASE}/accounts/${id}/detail`),
    enabled: !!id,
  });
}

export function useUpdateAccountEnrichment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<AccountEnrichment> }) =>
      patchJSON(`${BASE}/accounts/${id}/enrichment`, data),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["account", "detail", variables.id] });
      queryClient.invalidateQueries({ queryKey: ["asset-hub"] });
    },
  });
}

// FIRE Engine
export function useFireConfig() {
  return useQuery({
    queryKey: ["fire", "config"],
    queryFn: () => fetchJSON<FireConfig>(`${BASE}/fire/config`),
  });
}

export function useUpdateFireConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<FireConfig>) =>
      patchJSON<FireConfig>(`${BASE}/fire/config`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fire"] });
    },
  });
}

export function useFireNumber() {
  return useQuery({
    queryKey: ["fire", "number"],
    queryFn: () => fetchJSON<FireNumber>(`${BASE}/fire/number`),
  });
}

export function useFireReadiness() {
  return useQuery({
    queryKey: ["fire", "readiness"],
    queryFn: () => fetchJSON<Readiness>(`${BASE}/fire/readiness`),
  });
}

export function useFireTimeline() {
  return useQuery({
    queryKey: ["fire", "timeline"],
    queryFn: () => fetchJSON<RetirementTimeline>(`${BASE}/fire/timeline`),
  });
}

export function useBridgeStatus() {
  return useQuery({
    queryKey: ["fire", "bridge-status"],
    queryFn: () => fetchJSON<BridgeStatus>(`${BASE}/fire/bridge-status`),
  });
}

export function useWealthProjection(spendingOverride?: number) {
  const params = new URLSearchParams();
  if (spendingOverride != null) params.set("spending_override", String(spendingOverride));
  const qs = params.toString();
  return useQuery({
    queryKey: ["fire", "wealth-projection", spendingOverride ?? "default"],
    queryFn: () =>
      fetchJSON<WealthPoolProjection>(`${BASE}/fire/wealth-projection${qs ? `?${qs}` : ""}`),
  });
}

export function useBridgeProjection(spendingOverride?: number) {
  const params = new URLSearchParams({ bridge_months: "60" });
  if (spendingOverride != null) params.set("spending_override", String(spendingOverride));
  return useQuery({
    queryKey: ["fire", "bridge-projection", spendingOverride ?? "default"],
    queryFn: () =>
      fetchJSON<WealthPoolProjection>(`${BASE}/fire/wealth-projection?${params.toString()}`),
  });
}

export function useSpendingSensitivity() {
  return useQuery({
    queryKey: ["fire", "spending-sensitivity"],
    queryFn: () =>
      fetchJSON<SpendingSensitivity>(`${BASE}/fire/spending-sensitivity`),
  });
}

export function useMilestones() {
  return useQuery({
    queryKey: ["fire", "milestones"],
    queryFn: () => fetchJSON<MilestonesResponse>(`${BASE}/fire/milestones`),
  });
}

export function useFireLifetime(scenario: string = "moderate") {
  return useQuery({
    queryKey: ["fire", "lifetime", scenario],
    queryFn: () =>
      fetchJSON<LifetimeProjection>(`${BASE}/fire/lifetime?scenario=${scenario}`),
  });
}

export function useFireMetrics() {
  return useQuery({
    queryKey: ["fire", "metrics"],
    queryFn: () => fetchJSON<FireMetrics>(`${BASE}/fire/metrics`),
  });
}

export function useIncomeSources() {
  return useQuery({
    queryKey: ["fire", "income"],
    queryFn: () => fetchJSON<IncomeSource[]>(`${BASE}/fire/income`),
  });
}

export function useCreateIncomeSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<IncomeSource>) =>
      postJSON<IncomeSource>(`${BASE}/fire/income`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fire"] });
    },
  });
}

export function useUpdateIncomeSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<IncomeSource> }) =>
      putJSON<IncomeSource>(`${BASE}/fire/income/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fire"] });
    },
  });
}

export function useDeleteIncomeSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetchJSON(`${BASE}/fire/income/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fire"] });
    },
  });
}

export function useGoals() {
  return useQuery({
    queryKey: ["fire", "goals"],
    queryFn: () => fetchJSON<Goal[]>(`${BASE}/fire/goals`),
  });
}

export function useCreateGoal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Goal>) =>
      postJSON<Goal>(`${BASE}/fire/goals`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fire", "goals"] });
    },
  });
}

export function useRunScenario() {
  return useMutation({
    mutationFn: (input: ScenarioInput) =>
      postJSON<ScenarioComparison>(`${BASE}/fire/scenario`, input),
  });
}

// Named Scenarios

export function useScenarios() {
  return useQuery({
    queryKey: ["fire", "scenarios"],
    queryFn: () => fetchJSON<FireScenario[]>(`${BASE}/fire/scenarios`),
  });
}

export function useCreateScenario() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      name: string;
      description?: string;
      overrides?: Record<string, unknown>;
      is_active?: boolean;
    }) => postJSON<FireScenario>(`${BASE}/fire/scenarios`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fire"] });
    },
  });
}

export function useUpdateScenario() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...data
    }: {
      id: string;
      name?: string;
      description?: string;
      overrides?: Record<string, unknown>;
    }) => putJSON<FireScenario>(`${BASE}/fire/scenarios/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fire", "scenarios"] });
    },
  });
}

export function useDeleteScenario() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      deleteJSON(`${BASE}/fire/scenarios/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fire"] });
    },
  });
}

export function useActivateScenario() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      postJSON<FireScenario>(`${BASE}/fire/scenarios/${id}/activate`),
    onSuccess: () => {
      // Invalidate all fire queries so dashboard refreshes with new scenario
      queryClient.invalidateQueries({ queryKey: ["fire"] });
    },
  });
}

export function useDeactivateScenario() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      postJSON<{ ok: boolean }>(`${BASE}/fire/scenarios/deactivate`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fire"] });
    },
  });
}

// Tax Planning
export function useBracketAnalysis() {
  return useQuery({
    queryKey: ["tax", "brackets"],
    queryFn: () => fetchJSON<BracketAnalysis>(`${BASE}/tax/bracket-analysis`),
  });
}

export function useWithdrawalPlan(years: number = 10) {
  return useQuery({
    queryKey: ["tax", "withdrawal", years],
    queryFn: () =>
      fetchJSON<WithdrawalPlan>(`${BASE}/tax/withdrawal-plan?years=${years}`),
  });
}

export function useRothConversionPlan(targetBracket: number = 0.22) {
  return useQuery({
    queryKey: ["tax", "roth", targetBracket],
    queryFn: () =>
      fetchJSON<RothConversionPlan>(
        `${BASE}/tax/roth-conversion-plan?target_bracket=${targetBracket}`,
      ),
  });
}

export function useMonteCarlo(runs: number = 1000, seed: number = 42) {
  return useQuery({
    queryKey: ["retirement", "monte-carlo", runs, seed],
    queryFn: () =>
      fetchJSON<MonteCarloResult>(`${BASE}/tax/monte-carlo?runs=${runs}&seed=${seed}`),
  });
}

export interface SeppParams {
  balance: number;
  age: number;
  rate: number; // decimal, e.g. 0.05
  targetMonthly?: number;
}

export function useSeppCalculator(params: SeppParams | null) {
  return useQuery({
    queryKey: ["tax", "sepp", params],
    queryFn: () => {
      const q = new URLSearchParams({
        balance: String(params!.balance),
        age: String(params!.age),
        rate: String(params!.rate),
      });
      if (params!.targetMonthly) q.set("target_monthly", String(params!.targetMonthly));
      return fetchJSON<SEPPResponse>(`${BASE}/tax/sepp?${q.toString()}`);
    },
    enabled: params != null,
    retry: false, // a 422 (rate above IRS cap) should surface, not retry
    placeholderData: (prev) => prev, // keep last result visible while typing
  });
}

export function useRunTaxScenario() {
  return useMutation({
    mutationFn: (input: TaxScenarioInput) =>
      postJSON<TaxScenarioResponse>(`${BASE}/tax/scenario`, input),
  });
}

// Cash Flow & Runway
export function useRunway(
  months: number = 24,
  incomeOverride?: number,
  burnOverride?: number,
) {
  const params = new URLSearchParams({ months: String(months) });
  if (incomeOverride !== undefined)
    params.set("income_override", String(incomeOverride));
  if (burnOverride !== undefined)
    params.set("burn_override", String(burnOverride));
  return useQuery({
    queryKey: ["cashflow", "runway", months, incomeOverride, burnOverride],
    queryFn: () =>
      fetchJSON<RunwayResponse>(`${BASE}/cashflow/runway?${params}`),
  });
}

export function useCashflowEvents() {
  return useQuery({
    queryKey: ["cashflow", "events"],
    queryFn: () => fetchJSON<CashflowEvent[]>(`${BASE}/cashflow/events`),
  });
}

export function useCreateCashflowEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CashflowEventCreate) =>
      postJSON<CashflowEvent>(`${BASE}/cashflow/events`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cashflow"] });
    },
  });
}

export function useUpdateCashflowEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<CashflowEventCreate> }) =>
      putJSON<CashflowEvent>(`${BASE}/cashflow/events/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cashflow"] });
    },
  });
}

export function useDeleteCashflowEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetchJSON(`${BASE}/cashflow/events/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cashflow"] });
    },
  });
}

// Advisor conversations
export function useConversations() {
  return useQuery({
    queryKey: ["advisor", "conversations"],
    queryFn: () =>
      fetchJSON<ConversationListResponse>(`${BASE}/advisor/conversations`),
  });
}

export function useConversation(id: string | null) {
  return useQuery({
    queryKey: ["advisor", "conversation", id],
    queryFn: () =>
      fetchJSON<ConversationDetail>(`${BASE}/advisor/conversations/${id}`),
    enabled: !!id,
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetchJSON(`${BASE}/advisor/conversations/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["advisor", "conversations"] });
    },
  });
}

// ---------------------------------------------------------------------------
//  Property P&L
// ---------------------------------------------------------------------------

const PROP_KEY = ["properties"];

export function useProperties(includeInactive = false) {
  return useQuery({
    queryKey: [...PROP_KEY, "list", includeInactive],
    queryFn: () =>
      fetchJSON<Property[]>(`${BASE}/properties?include_inactive=${includeInactive}`),
  });
}

export function usePropertyPnL(start?: string, end?: string) {
  const qs = new URLSearchParams();
  if (start) qs.set("start", start);
  if (end) qs.set("end", end);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return useQuery({
    queryKey: [...PROP_KEY, "pnl", start ?? "", end ?? ""],
    queryFn: () => fetchJSON<PnLResponse>(`${BASE}/properties/pnl${suffix}`),
  });
}

export function usePropertyRules(propertyId?: string) {
  return useQuery({
    queryKey: [...PROP_KEY, "rules", propertyId ?? "all"],
    queryFn: () =>
      fetchJSON<PropertyRule[]>(
        `${BASE}/properties/rules${propertyId ? `?property_id=${propertyId}` : ""}`,
      ),
  });
}

export function usePropertyCategories() {
  return useQuery({
    queryKey: [...PROP_KEY, "categories"],
    queryFn: () => fetchJSON<CategoriesResponse>(`${BASE}/properties/categories`),
    staleTime: Infinity,
  });
}

export function usePropertyTransactions(
  propertyId: string | null,
  month?: string,
  limit = 100,
) {
  return useQuery({
    queryKey: [...PROP_KEY, "transactions", propertyId, month ?? "", limit],
    queryFn: () => {
      const qs = new URLSearchParams({ limit: String(limit) });
      if (month) qs.set("month", month);
      return fetchJSON<PropertyTransactionsResponse>(
        `${BASE}/properties/${propertyId}/transactions?${qs.toString()}`,
      );
    },
    enabled: !!propertyId,
  });
}

export function useManualEntries() {
  return useQuery({
    queryKey: [...PROP_KEY, "manual-entries"],
    queryFn: () =>
      fetchJSON<PropertyTransaction[]>(`${BASE}/properties/manual-entries`),
  });
}

function invalidateProperties(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: PROP_KEY });
  // Tracker reflects property exclusion — refresh it too (tracker lives under "spending").
  qc.invalidateQueries({ queryKey: ["spending"] });
  // The ledger shows the property assignment inline — refresh it after any reclassify.
  qc.invalidateQueries({ queryKey: ["ledger"] });
}

// ---------------------------------------------------------------------------
//  Transactions ledger (Monarch-like all-transactions browser)
// ---------------------------------------------------------------------------

export function useLedgerTransactions(filters: LedgerFilters) {
  const {
    q = "",
    type = "all",
    classification = "all",
    propertyId = null,
    accountId = null,
    dateFrom = null,
    dateTo = null,
    limit = 50,
    offset = 0,
  } = filters;
  return useQuery({
    queryKey: [
      "ledger",
      q,
      type,
      classification,
      propertyId,
      accountId,
      dateFrom,
      dateTo,
      limit,
      offset,
    ],
    queryFn: () => {
      const qs = new URLSearchParams({
        type,
        classification,
        limit: String(limit),
        offset: String(offset),
      });
      if (q) qs.set("q", q);
      if (propertyId) qs.set("property_id", propertyId);
      if (accountId) qs.set("account_id", accountId);
      if (dateFrom) qs.set("date_from", dateFrom);
      if (dateTo) qs.set("date_to", dateTo);
      return fetchJSON<LedgerResponse>(`${BASE}/transactions?${qs.toString()}`);
    },
    placeholderData: (prev) => prev, // keep prior page visible while paginating/filtering
  });
}

export function useUpdateProperty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Property> }) =>
      patchJSON<Property>(`${BASE}/properties/${id}`, data),
    onSuccess: () => invalidateProperties(qc),
  });
}

export function useReclassify() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => postJSON<ReclassifyResult>(`${BASE}/properties/reclassify`),
    onSuccess: () => invalidateProperties(qc),
  });
}

export function useUpsertRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id?: string; data: Partial<PropertyRule> }) =>
      id
        ? patchJSON<PropertyRule>(`${BASE}/properties/rules/${id}`, data)
        : postJSON<PropertyRule>(`${BASE}/properties/rules`, data),
    onSuccess: () => invalidateProperties(qc),
  });
}

export function useDeleteRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteJSON(`${BASE}/properties/rules/${id}`),
    onSuccess: () => invalidateProperties(qc),
  });
}

export function useReassignTransaction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      txId,
      propertyId,
      category,
    }: {
      txId: string;
      propertyId: string | null;
      category: string | null;
    }) =>
      putJSON<PropertyTransaction>(`${BASE}/properties/transactions/${txId}`, {
        property_id: propertyId,
        property_category: category,
      }),
    onSuccess: () => invalidateProperties(qc),
  });
}

export function useClearTransactionOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (txId: string) =>
      deleteJSON(`${BASE}/properties/transactions/${txId}/override`),
    onSuccess: () => invalidateProperties(qc),
  });
}

export function useCreateManualEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ManualEntryInput) =>
      postJSON<PropertyTransaction>(`${BASE}/properties/manual-entries`, data),
    onSuccess: () => invalidateProperties(qc),
  });
}

export function useDeleteManualEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (txId: string) =>
      deleteJSON(`${BASE}/properties/manual-entries/${txId}`),
    onSuccess: () => invalidateProperties(qc),
  });
}
