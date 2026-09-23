import { useState, useMemo } from "react";
import {
  useTrackerSummary,
  useTrackerDaily,
  useTrackerTransactions,
  useTrackerCategories,
  useProperties,
  useReassignTransaction,
} from "../api/queries";
import Layout from "../components/Layout";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
  Cell,
} from "recharts";
import { formatCurrency as fmt, fmtCompact } from "../utils/formatting";
import { CHART_COLORS, TOOLTIP_STYLE } from "../utils/theme";
import { parseLocalDate } from "../utils/dates";
import type { PlannedExclusion } from "../types/spending";

const MONTHLY_TARGET = 5000;

function statusColor(status: string): string {
  if (status === "under_pace") return "var(--green)";
  if (status === "on_pace") return "var(--yellow)";
  return "var(--red)";
}

function statusLabel(status: string): string {
  if (status === "under_pace") return "Under budget";
  if (status === "on_pace") return "On track";
  return "Over pace";
}

/** Exclusions whose matched payment falls in the given "YYYY-MM" month. */
function exclusionsIn(all: PlannedExclusion[], month: string): PlannedExclusion[] {
  return all.filter((ex) => ex.matched_date.startsWith(month));
}

/**
 * What "Adjusted" removed: payments matched to planned expense events
 * (amount ±10%, date ±7 days), so one-offs budgeted as events don't count as
 * lifestyle spending. Matching runs once over the whole tracker window.
 */
function ExclusionList({ items, when }: { items: PlannedExclusion[]; when: string }) {
  if (items.length === 0) {
    return (
      <div className="mt-3 text-[11px] text-[var(--text-secondary)]">
        Adjusted: no planned-event payments excluded {when}.
      </div>
    );
  }
  return (
    <div className="mt-3 px-3 py-2 rounded-md bg-[rgba(0,212,170,0.06)] border border-[rgba(0,212,170,0.15)]">
      <div className="text-[11px] text-[var(--green)] font-medium mb-1">
        Excluding {items.length} planned-event payment{items.length > 1 ? "s" : ""} {when}:
      </div>
      {items.map((ex) => (
        <div key={ex.matched_transaction_id} className="text-[11px] text-[var(--text-secondary)] flex items-center gap-2">
          <span className="text-[var(--text-primary)]">{ex.event_name}</span>
          <span className="font-mono">{fmt(ex.matched_amount)}</span>
          <span className="opacity-60">
            {ex.matched_merchant && `(${ex.matched_merchant})`}{" "}
            {parseLocalDate(ex.matched_date).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function SpendingTrackerPage() {
  const [categoryFilter, setCategoryFilter] = useState<string | undefined>();
  const [selectedMonth, setSelectedMonth] = useState<string | undefined>();
  const [excludePlanned, setExcludePlanned] = useState(false);

  const { data: tracker, isLoading } = useTrackerSummary(MONTHLY_TARGET, excludePlanned);
  const { data: daily } = useTrackerDaily(selectedMonth, MONTHLY_TARGET, excludePlanned);
  const { data: txData } = useTrackerTransactions(
    categoryFilter,
    selectedMonth,
    100,
    0,
    excludePlanned,
  );
  const { data: monthCategories } = useTrackerCategories(
    selectedMonth,
    excludePlanned,
  );
  const { data: properties } = useProperties();
  const reassign = useReassignTransaction();

  // Progress percentage toward target
  const progressPct = tracker
    ? Math.min((tracker.spent_so_far / tracker.monthly_target) * 100, 100)
    : 0;

  // Progress bar color based on percentage — hex values for gradient opacity trick
  const progressBarColor = useMemo(() => {
    if (!tracker) return "#2e8b6e";
    const pct = tracker.spent_so_far / tracker.monthly_target;
    if (pct < 0.6) return "#2e8b6e";
    if (pct < 0.85) return "#a07d1a";
    return "#b04a56";
  }, [tracker]);

  // Daily chart data
  const dailyChartData = useMemo(() => {
    if (!daily) return [];
    return daily.days.map((d) => ({
      day: d.day,
      spent: d.cumulative,
      target: d.target_pace,
      daily: d.daily_amount,
    }));
  }, [daily]);

  // Category bar data for the selected (or current) month — falls back to
  // the summary's `categories` field while the dedicated endpoint loads.
  const categoryData = useMemo(() => {
    const source =
      monthCategories ?? (selectedMonth ? [] : tracker?.categories ?? []);
    return source.slice(0, 12).map((c) => ({
      name:
        c.category.length > 20 ? c.category.slice(0, 18) + "..." : c.category,
      fullName: c.category,
      amount: c.amount,
      pct: c.percentage,
      count: c.transaction_count,
      discretionary: c.is_discretionary,
    }));
  }, [monthCategories, tracker, selectedMonth]);

  if (isLoading || !tracker) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-96 text-[var(--text-secondary)]">
          Loading tracker...
        </div>
      </Layout>
    );
  }

  const currentMonthLabel = new Date(
    tracker.current_month + "-01T12:00:00",
  ).toLocaleDateString("en-US", { month: "long", year: "numeric" });

  return (
    <Layout>
      <div className="space-y-6">
        {/* ============================================================ */}
        {/* HERO SECTION — The number that matters                        */}
        {/* ============================================================ */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between mb-4">
            <div>
              <div className="flex items-center gap-3 mb-1">
                <h2 className="text-sm uppercase tracking-wider text-[var(--text-secondary)]">
                  Non-Housing Spending &middot; {currentMonthLabel}
                </h2>
                {/* Exclude planned toggle */}
                <button
                  onClick={() => setExcludePlanned(!excludePlanned)}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-medium uppercase tracking-wider transition-all ${
                    excludePlanned
                      ? "bg-[rgba(0,212,170,0.15)] text-[var(--green)] border border-[rgba(0,212,170,0.3)]"
                      : "bg-[var(--bg-secondary)] text-[var(--text-secondary)] border border-[var(--border)] hover:text-[var(--text-primary)] hover:border-[var(--green)]"
                  }`}
                >
                  <span className={`inline-block w-1.5 h-1.5 rounded-full ${excludePlanned ? "bg-[var(--green)]" : "bg-[var(--text-secondary)] opacity-40"}`} />
                  {excludePlanned ? "Adjusted" : "Raw"}
                </button>
              </div>
              <div className="flex items-baseline gap-3">
                <span
                  className="text-5xl font-bold font-mono tracking-tighter"
                  style={{ color: statusColor(tracker.status) }}
                >
                  {fmt(tracker.spent_so_far)}
                </span>
                <span className="text-lg text-[var(--text-secondary)]">
                  of {fmt(tracker.monthly_target)}
                </span>
              </div>
              <p className="text-sm mt-2" style={{ color: statusColor(tracker.status) }}>
                {statusLabel(tracker.status)} &middot; {tracker.days_remaining}{" "}
                days left
              </p>
            </div>

            {/* Savings vs old pace — the motivational stat */}
            {tracker.savings_vs_old > 0 && (
              <div className="sm:text-right">
                <div className="text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1">
                  Saving vs pre-layoff pace
                </div>
                <div className="text-2xl font-bold font-mono text-[var(--green)]">
                  {fmt(tracker.savings_vs_old)}
                  <span className="text-sm font-normal text-[var(--text-secondary)]">
                    /mo
                  </span>
                </div>
                {tracker.runway_days_added > 0 && (
                  <div className="text-xs text-[var(--green)] mt-1">
                    +{Math.round(tracker.runway_days_added)} days of runway
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Progress bar */}
          <div className="relative h-3 bg-[var(--bg-secondary)] rounded-full overflow-hidden">
            <div
              className="absolute inset-y-0 left-0 rounded-full transition-all duration-500"
              style={{
                width: `${progressPct}%`,
                background: `linear-gradient(90deg, ${progressBarColor}cc, ${progressBarColor})`,
              }}
            />
            {/* Target markers at 25%, 50%, 75% */}
            {[25, 50, 75].map((pct) => (
              <div
                key={pct}
                className="absolute inset-y-0 w-px bg-[var(--border)]"
                style={{ left: `${pct}%` }}
              />
            ))}
          </div>
          <div className="flex justify-between mt-1.5 text-[10px] text-[var(--text-secondary)] font-mono">
            <span>$0</span>
            <span>{fmt(tracker.monthly_target / 2)}</span>
            <span>{fmt(tracker.monthly_target)}</span>
          </div>
          {/* Exclusion info — the hero is the current month */}
          {tracker.exclude_planned && (
            <ExclusionList
              items={exclusionsIn(tracker.planned_exclusions, tracker.current_month)}
              when="this month"
            />
          )}
        </div>

        {/* ============================================================ */}
        {/* PACE CARDS — Daily average, projected, budget remaining        */}
        {/* ============================================================ */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <PaceCard
            label="Daily Average"
            value={`${fmt(tracker.daily_average)}/day`}
            sub={`Target: ${fmt(tracker.monthly_target / tracker.days_in_month)}/day`}
            color={statusColor(tracker.status)}
          />
          <PaceCard
            label="Projected Month-End"
            value={fmt(tracker.projected_total)}
            sub={
              tracker.projected_total <= tracker.monthly_target
                ? `${fmt(tracker.monthly_target - tracker.projected_total)} under target`
                : `${fmt(tracker.projected_total - tracker.monthly_target)} over target`
            }
            color={
              tracker.projected_total <= tracker.monthly_target
                ? "var(--green)"
                : "var(--red)"
            }
          />
          <PaceCard
            label="Budget Remaining"
            value={fmt(Math.max(tracker.budget_remaining, 0))}
            sub={`${fmt(Math.max(tracker.budget_remaining, 0) / Math.max(tracker.days_remaining, 1))}/day remaining`}
            color={
              tracker.budget_remaining > 0 ? "var(--green)" : "var(--red)"
            }
          />
          <PaceCard
            label="Pre-Layoff Pace"
            value={fmt(tracker.pre_layoff_avg)}
            sub="Jan-Mar 2026 avg (non-housing)"
            color="var(--text-secondary)"
          />
        </div>

        {/* ============================================================ */}
        {/* DAILY ACCUMULATION CHART — The signature viz                   */}
        {/* ============================================================ */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-[var(--text-secondary)]">
              Daily Spending Accumulation
            </h3>
            {/* Month selector from tracker months */}
            {tracker.months.length > 1 && (
              <div className="flex gap-1">
                {tracker.months.map((m) => {
                  const label = new Date(m.month + "-01T12:00:00").toLocaleDateString(
                    "en-US",
                    { month: "short" },
                  );
                  const isActive =
                    (selectedMonth ?? tracker.current_month) === m.month;
                  return (
                    <button
                      key={m.month}
                      onClick={() =>
                        setSelectedMonth(
                          m.month === tracker.current_month
                            ? undefined
                            : m.month,
                        )
                      }
                      className={`px-2.5 py-1 text-xs rounded transition-colors ${
                        isActive
                          ? "bg-[var(--blue)] text-white"
                          : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* A past month is selected: show what Adjusted removed from IT */}
          {tracker.exclude_planned && selectedMonth && (
            <div className="mb-3">
              <ExclusionList
                items={exclusionsIn(tracker.planned_exclusions, selectedMonth)}
                when={`in ${parseLocalDate(selectedMonth).toLocaleDateString("en-US", { month: "long" })}`}
              />
            </div>
          )}

          {dailyChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart
                data={dailyChartData}
                margin={{ left: 8, right: 8, top: 8, bottom: 4 }}
              >
                <defs>
                  <linearGradient id="spentGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#3d6e9e" stopOpacity={0.35} />
                    <stop
                      offset="100%"
                      stopColor="#3d6e9e"
                      stopOpacity={0.05}
                    />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="rgba(0,0,0,0.08)"
                  vertical={false}
                />
                <XAxis
                  dataKey="day"
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: "#5c5c6a", fontSize: 11 }}
                />
                <YAxis
                  tickFormatter={(v: number) => fmtCompact(v)}
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: "#5c5c6a", fontSize: 11 }}
                />
                <Tooltip
                  {...TOOLTIP_STYLE}
                  formatter={(value, name) => {
                    const v = Number(value);
                    if (name === "target") return [fmt(v), "Target Pace"];
                    if (name === "daily") return [fmt(v), "Spent Today"];
                    return [fmt(v), "Total Spent"];
                  }}
                  labelFormatter={(d) => `Day ${d}`}
                />
                {/* Target pace — dashed green line */}
                <Area
                  type="linear"
                  dataKey="target"
                  stroke="var(--green)"
                  strokeWidth={2}
                  strokeDasharray="6 3"
                  fill="none"
                  dot={false}
                />
                {/* Cumulative spending — solid blue area */}
                <Area
                  type="monotone"
                  dataKey="spent"
                  stroke="var(--blue)"
                  strokeWidth={2.5}
                  fill="url(#spentGrad)"
                  dot={false}
                />
                {/* Budget line */}
                <ReferenceLine
                  y={MONTHLY_TARGET}
                  stroke="var(--red)"
                  strokeDasharray="4 4"
                  strokeOpacity={0.6}
                  label={{
                    value: `$${(MONTHLY_TARGET / 1000).toFixed(0)}K Target`,
                    position: "right",
                    fill: "#5c5c6a",
                    fontSize: 10,
                  }}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-[280px] text-[var(--text-secondary)] text-sm">
              No data for this month
            </div>
          )}
        </div>

        {/* ============================================================ */}
        {/* BOTTOM ROW: Categories + Monthly Scorecard                    */}
        {/* ============================================================ */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          {/* Category Breakdown */}
          <div className="lg:col-span-3 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">
              Where It's Going &middot;{" "}
              {new Date(
                (selectedMonth ?? tracker.current_month) + "-01T12:00:00",
              ).toLocaleDateString("en-US", {
                month: "long",
                year: "numeric",
              })}
            </h3>
            {categoryData.length > 0 ? (
              <ResponsiveContainer
                width="100%"
                height={Math.max(categoryData.length * 32 + 20, 150)}
              >
                <BarChart
                  data={categoryData}
                  layout="vertical"
                  margin={{ left: 8, right: 24, top: 4, bottom: 4 }}
                  barCategoryGap="20%"
                >
                  <XAxis
                    type="number"
                    tickFormatter={(v: number) => fmtCompact(v)}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "#5c5c6a", fontSize: 11 }}
                  />
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={140}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "#1a1a1e", fontSize: 12 }}
                  />
                  <Tooltip
                    {...TOOLTIP_STYLE}
                    formatter={(value) => [fmt(Number(value)), "Spent"]}
                    labelFormatter={(label) => {
                      const item = categoryData.find(
                        (d) => d.name === String(label),
                      );
                      return item
                        ? `${item.fullName} (${item.pct.toFixed(1)}%)`
                        : String(label);
                    }}
                  />
                  <Bar
                    dataKey="amount"
                    radius={[0, 4, 4, 0]}
                    maxBarSize={24}
                    cursor="pointer"
                    onClick={(d) => {
                      const fullName = (d as unknown as { fullName?: string })
                        ?.fullName;
                      if (fullName) {
                        setCategoryFilter(
                          categoryFilter === fullName ? undefined : fullName,
                        );
                      }
                    }}
                  >
                    {categoryData.map((d, i) => {
                      // Each bar gets its own palette hue. Fixed (non-
                      // discretionary) categories read dimmer so the
                      // discretionary signal is preserved without all
                      // fixed bars collapsing to a single gray.
                      const fill = CHART_COLORS[i % CHART_COLORS.length];
                      const dimmed =
                        categoryFilter && d.fullName !== categoryFilter;
                      const baseOpacity = d.discretionary ? 0.85 : 0.5;
                      return (
                        <Cell
                          key={i}
                          fill={fill}
                          fillOpacity={dimmed ? 0.2 : baseOpacity}
                        />
                      );
                    })}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-[150px] text-[var(--text-secondary)] text-sm">
                No spending data yet
              </div>
            )}
            {categoryFilter && (
              <button
                onClick={() => setCategoryFilter(undefined)}
                className="mt-2 text-xs text-[var(--blue)] hover:underline"
              >
                Clear filter: {categoryFilter}
              </button>
            )}
          </div>

          {/* Monthly Scorecard */}
          <div className="lg:col-span-2 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">
              Monthly Scorecard
            </h3>
            <div className="space-y-3">
              {[...tracker.months].reverse().map((m) => {
                const label = new Date(m.month + "-01T12:00:00").toLocaleDateString(
                  "en-US",
                  { month: "short", year: "numeric" },
                );
                const isCurrent = m.month === tracker.current_month;
                const underBudget = m.delta >= 0;

                return (
                  <div
                    key={m.month}
                    className={`flex items-center justify-between p-3 rounded-lg border ${
                      isCurrent
                        ? "border-[var(--blue)] bg-[rgba(77,142,255,0.06)]"
                        : "border-[var(--border)] bg-[var(--bg-secondary)]"
                    }`}
                  >
                    <div>
                      <div className="text-sm font-medium text-[var(--text-primary)]">
                        {label}
                        {isCurrent && (
                          <span className="ml-2 text-[10px] text-[var(--blue)] uppercase">
                            current
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-[var(--text-secondary)] mt-0.5">
                        {fmt(m.daily_average)}/day avg
                      </div>
                    </div>
                    <div className="text-right">
                      <div
                        className="text-lg font-bold font-mono"
                        style={{ color: statusColor(m.status) }}
                      >
                        {fmt(m.total)}
                      </div>
                      <div
                        className="text-xs font-mono"
                        style={{
                          color: underBudget ? "var(--green)" : "var(--red)",
                        }}
                      >
                        {underBudget ? "" : "+"}
                        {fmt(Math.abs(m.delta))}{" "}
                        {underBudget ? "under" : "over"}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* ============================================================ */}
        {/* TRANSACTION FEED — Every dollar, visible                      */}
        {/* ============================================================ */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-[var(--text-secondary)]">
              Every Expense{" "}
              {categoryFilter && (
                <span className="text-[var(--blue)]">
                  &middot; {categoryFilter}
                </span>
              )}
            </h3>
            {txData && (
              <span className="text-xs text-[var(--text-secondary)]">
                {txData.total_count} transactions &middot;{" "}
                {fmt(txData.total_amount)}
              </span>
            )}
          </div>

          {txData && txData.transactions.length > 0 ? (
            <div className="space-y-0.5 max-h-[480px] overflow-y-auto pr-1">
              {txData.transactions.map((tx) => (
                <div
                  key={tx.id}
                  className="group flex items-center justify-between py-2 px-3 rounded hover:bg-[var(--bg-secondary)] transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    {/* Date */}
                    <span className="text-xs text-[var(--text-secondary)] font-mono w-[68px] shrink-0">
                      {new Date(tx.date + "T00:00:00").toLocaleDateString(
                        "en-US",
                        { month: "short", day: "numeric" },
                      )}
                    </span>
                    {/* Merchant */}
                    <span className="text-sm text-[var(--text-primary)] truncate max-w-[240px]">
                      {tx.merchant || "—"}
                    </span>
                    {/* Category badge */}
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded shrink-0 ${
                        tx.is_discretionary
                          ? "bg-[rgba(255,192,77,0.15)] text-[var(--yellow)]"
                          : "bg-[rgba(136,136,160,0.15)] text-[var(--text-secondary)]"
                      }`}
                    >
                      {tx.category}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0 ml-3">
                    {/* Move to property — pulls a leaked property charge out of the Tracker */}
                    {properties && properties.length > 0 && (
                      <select
                        value=""
                        title="Assign to a property (removes it from the Tracker)"
                        onChange={(e) => {
                          if (!e.target.value) return;
                          reassign.mutate({
                            txId: tx.id,
                            propertyId: e.target.value,
                            category: tx.amount >= 0 ? "Rental Income" : "Other",
                          });
                        }}
                        className="input opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity text-[11px]"
                      >
                        <option value="">→ property…</option>
                        {properties.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                      </select>
                    )}
                    {/* Amount */}
                    <span className="text-sm font-mono font-medium text-[var(--text-primary)]">
                      {fmt(tx.amount)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex items-center justify-center h-[120px] text-[var(--text-secondary)] text-sm">
              {txData ? "No transactions found" : "Loading..."}
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
}

/* ------------------------------------------------------------------ */
/*  Small reusable components                                          */
/* ------------------------------------------------------------------ */

function PaceCard({
  label,
  value,
  color,
  sub,
}: {
  label: string;
  value: string;
  color: string;
  sub?: string;
}) {
  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4 flex flex-col gap-1">
      <span className="text-xs uppercase tracking-wider text-[var(--text-secondary)]">
        {label}
      </span>
      <span
        className="text-xl font-bold font-mono tracking-tight"
        style={{ color }}
      >
        {value}
      </span>
      {sub && (
        <span className="text-xs text-[var(--text-secondary)]">{sub}</span>
      )}
    </div>
  );
}
