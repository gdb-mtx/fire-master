import { useState, useMemo } from "react";
import {
  useSpendingAnalysis,
  useSpendingTrends,
} from "../api/queries";
import Layout from "../components/Layout";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  CartesianGrid,
  Cell,
} from "recharts";
import { formatCurrency as fmt, fmtCompact } from "../utils/formatting";
import { CHART_COLORS as CATEGORY_COLORS, TOOLTIP_STYLE } from "../utils/theme";
import { fmtMonthYear } from "../utils/dates";

const RANGE_OPTIONS = [
  { label: "Month", value: "month" },
  { label: "3M", value: "3m" },
  { label: "6M", value: "6m" },
  { label: "YTD", value: "ytd" },
  { label: "1Y", value: "1y" },
  { label: "All", value: "all" },
] as const;

function StatCard({
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
        className="text-2xl font-bold font-mono tracking-tight"
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

export default function SpendingPage() {
  const [range, setRange] = useState("1y");
  const [groupByParent, setGroupByParent] = useState(false);
  const { data: analysis, isLoading } = useSpendingAnalysis(range, groupByParent);
  const { data: trends } = useSpendingTrends(12, 8);

  // Transform trends data for multi-line chart: pivot to { month, cat1, cat2, ... }
  const trendChartData = useMemo(() => {
    if (!trends) return [] as Record<string, string | number>[];
    const monthMap = new Map<string, Record<string, string | number>>();

    for (const pt of trends.total_trend) {
      monthMap.set(pt.month, { month: pt.month, Total: pt.amount });
    }
    for (const cat of trends.categories) {
      for (const pt of cat.points) {
        const entry = monthMap.get(pt.month) || { month: pt.month };
        entry[cat.category] = pt.amount;
        monthMap.set(pt.month, entry);
      }
    }
    return Array.from(monthMap.values()).sort((a, b) =>
      String(a.month).localeCompare(String(b.month)),
    );
  }, [trends]);

  const trendCategories = useMemo(
    () => trends?.categories.map((c) => c.category) ?? [],
    [trends],
  );

  if (isLoading || !analysis) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-96 text-[var(--text-secondary)]">
          Loading...
        </div>
      </Layout>
    );
  }

  // Bar chart data — show top 15 categories + "Other" for the rest
  const TOP_N = 30;
  const topCategories = analysis.categories.slice(0, TOP_N).map((c) => ({
    name: c.category.length > 22 ? c.category.slice(0, 20) + "..." : c.category,
    fullName: c.category,
    amount: c.amount,
    pct: c.percentage,
    count: c.transaction_count,
  }));
  const remaining = analysis.categories.slice(TOP_N);
  const barData = remaining.length > 0
    ? [
        ...topCategories,
        {
          name: `Other (${remaining.length})`,
          fullName: `Other (${remaining.length} categories)`,
          amount: Math.round(remaining.reduce((sum, c) => sum + c.amount, 0) * 100) / 100,
          pct: Math.round(remaining.reduce((sum, c) => sum + c.percentage, 0) * 10) / 10,
          count: remaining.reduce((sum, c) => sum + c.transaction_count, 0),
        },
      ]
    : topCategories;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">
            Spending Analysis
          </h2>
          <p className="text-sm text-[var(--text-secondary)] mt-1">
            {fmtMonthYear(analysis.start_date)}{" "}
            &ndash;{" "}
            {fmtMonthYear(analysis.end_date)}
          </p>
        </div>

        {/* Summary Hero Cards */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <StatCard
            label="Total Spending"
            value={fmt(analysis.total_spending)}
            color="var(--red)"
          />
          <StatCard
            label="Total Income"
            value={fmt(analysis.total_income)}
            color="var(--green)"
          />
          <StatCard
            label="Net Savings"
            value={fmt(analysis.net_savings)}
            color={analysis.net_savings >= 0 ? "var(--green)" : "var(--red)"}
          />
        </div>

        {/* Category Breakdown */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
          <div className="flex flex-wrap items-center justify-between gap-y-2 mb-4">
            <h3 className="text-sm font-medium text-[var(--text-secondary)]">
              Spending by Category
            </h3>
            <div className="flex flex-wrap items-center gap-3">
              {/* Parent / Individual toggle */}
              <button
                onClick={() => setGroupByParent(!groupByParent)}
                className={`px-2.5 py-1 text-xs rounded border transition-colors ${
                  groupByParent
                    ? "border-[var(--blue)] text-[var(--blue)]"
                    : "border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--blue)]"
                }`}
              >
                {groupByParent ? "By Group" : "By Category"}
              </button>
              {/* Range selector */}
              <div className="flex gap-1">
                {RANGE_OPTIONS.map((r) => (
                  <button
                    key={r.value}
                    onClick={() => setRange(r.value)}
                    className={`px-2.5 py-1 text-xs rounded transition-colors ${
                      range === r.value
                        ? "bg-[var(--blue)] text-white"
                        : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                    }`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {barData.length > 0 ? (
            <ResponsiveContainer width="100%" height={Math.max(barData.length * 32 + 20, 200)}>
              <BarChart
                data={barData}
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
                  width={160}
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: "#1a1a1e", fontSize: 12 }}
                />
                <Tooltip
                  {...TOOLTIP_STYLE}
                  formatter={(value) => [fmt(Number(value)), "Spent"]}
                  labelFormatter={(label) => {
                    const l = String(label);
                    const item = barData.find((d) => d.name === l);
                    return item ? `${item.fullName} (${item.pct.toFixed(1)}%)` : l;
                  }}
                />
                <Bar dataKey="amount" radius={[0, 4, 4, 0]} maxBarSize={24}>
                  {barData.map((_, i) => (
                    <Cell
                      key={i}
                      fill={CATEGORY_COLORS[i % CATEGORY_COLORS.length]}
                      fillOpacity={0.85}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-[200px] text-[var(--text-secondary)] text-sm">
              No spending data for this period
            </div>
          )}
        </div>

        {/* Spending Trends */}
        <div>
          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                Monthly Spending Trends
              </h3>
              {/* Legend */}
              <div className="flex flex-wrap gap-x-3 gap-y-1 max-w-[360px] justify-end">
                {trendCategories.map((cat, i) => (
                  <span key={cat} className="flex items-center gap-1 text-[10px] text-[var(--text-secondary)]">
                    <span
                      className="w-2 h-2 rounded-full inline-block"
                      style={{ background: CATEGORY_COLORS[i % CATEGORY_COLORS.length] }}
                    />
                    {cat}
                  </span>
                ))}
              </div>
            </div>

            {trendChartData.length > 0 ? (
              <ResponsiveContainer width="100%" height={320}>
                <LineChart
                  data={trendChartData}
                  margin={{ left: 8, right: 8, top: 8, bottom: 4 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="rgba(42,42,62,0.5)"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="month"
                    tickFormatter={(m: string) => {
                      const [y, mo] = m.split("-");
                      return `${["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][parseInt(mo, 10) - 1]} '${y.slice(2)}`;
                    }}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "#5c5c6a", fontSize: 10 }}
                  />
                  <YAxis
                    tickFormatter={(v: number) => fmtCompact(v)}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "#5c5c6a", fontSize: 10 }}
                  />
                  <Tooltip
                    {...TOOLTIP_STYLE}
                    formatter={(value) => fmt(Number(value))}
                    labelFormatter={(label) => {
                      return fmtMonthYear(String(label), "long");
                    }}
                  />
                  {/* Category lines */}
                  {trendCategories.map((cat, i) => (
                    <Line
                      key={cat}
                      type="monotone"
                      dataKey={cat}
                      stroke={CATEGORY_COLORS[i % CATEGORY_COLORS.length]}
                      strokeWidth={1.5}
                      dot={false}
                      connectNulls
                    />
                  ))}
                  {/* Total line — thicker, dashed */}
                  <Line
                    type="monotone"
                    dataKey="Total"
                    stroke="var(--text-primary)"
                    strokeWidth={2}
                    strokeDasharray="6 3"
                    dot={false}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-[320px] text-[var(--text-secondary)] text-sm">
                No trend data yet
              </div>
            )}
          </div>
        </div>
      </div>
    </Layout>
  );
}

