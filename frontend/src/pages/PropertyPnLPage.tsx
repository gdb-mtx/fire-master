import { useMemo, useRef, useState } from "react";
import Layout from "../components/Layout";
import {
  useProperties,
  usePropertyPnL,
  useReclassify,
} from "../api/queries";
import type { PropertyPnL } from "../types/property";
import { formatCurrency as fmt, fmtCompact } from "../utils/formatting";
import {
  Bar,
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
} from "recharts";
import { TOOLTIP_STYLE } from "../utils/theme";
import PropertyManagePanel from "../components/property/PropertyManagePanel";
import PropertyTransactionsTable from "../components/property/PropertyTransactionsTable";

const RENTAL = "Rental Income";

// Distinct, muted swatches for stacked expense categories (cohesive with CHART_COLORS).
const CATEGORY_COLORS: Record<string, string> = {
  Mortgage: "#3d6e9e",
  "HOA / Condo Fees": "#7a6aaa",
  Assessments: "#9e4a7a",
  Moorage: "#2e8494",
  Insurance: "#b06830",
  "Property Tax": "#a07d1a",
  Utilities: "#6a9e4a",
  "Repairs / Maintenance": "#b04a56",
  "Cleaning / Turnover": "#2e8a7a",
  Other: "#6a6e9e",
};

function monthLabel(m: string): string {
  return new Date(m + "-01T12:00:00").toLocaleDateString("en-US", {
    month: "short",
    year: "2-digit",
  });
}

type RangePreset = "all" | "ytd" | "12m";

function rangeDates(preset: RangePreset): { start?: string; end?: string } {
  const today = new Date();
  if (preset === "ytd") return { start: `${today.getFullYear()}-01-01` };
  if (preset === "12m") {
    const d = new Date(today);
    d.setMonth(d.getMonth() - 11);
    return { start: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01` };
  }
  return {};
}

export default function PropertyPnLPage() {
  const [range, setRange] = useState<RangePreset>("12m");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showManage, setShowManage] = useState(false);
  const [showTx, setShowTx] = useState(false);

  const { start, end } = rangeDates(range);
  const { data: properties } = useProperties();
  const { data: pnl, isLoading } = usePropertyPnL(start, end);
  const reclassify = useReclassify();

  const selected: PropertyPnL | undefined = useMemo(() => {
    if (!pnl) return undefined;
    return (
      pnl.properties.find((p) => p.property.id === selectedId) ??
      pnl.properties[0]
    );
  }, [pnl, selectedId]);

  // Portfolio totals across all properties.
  const portfolio = useMemo(() => {
    if (!pnl) return null;
    return pnl.properties.reduce(
      (acc, p) => {
        acc.cost += p.totals.total_cost;
        acc.rental += p.totals.rental_actual;
        acc.equity += p.property.equity;
        return acc;
      },
      { cost: 0, rental: 0, equity: 0 },
    );
  }, [pnl]);

  if (isLoading || !pnl || !properties) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-96 text-[var(--text-secondary)]">
          Loading property P&amp;L…
        </div>
      </Layout>
    );
  }

  // Empty state: no properties declared yet. Accounts synced from Monarch show
  // under Assets regardless — this module additionally needs properties +
  // merchant rules declared so transactions can classify into a per-property P&L.
  if (properties.length === 0) {
    return (
      <Layout>
        <div className="max-w-2xl mx-auto mt-12">
          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6 space-y-4">
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">
              No properties configured yet
            </h2>
            <p className="text-sm text-[var(--text-secondary)]">
              Your real-estate <em>accounts</em> (values, mortgages) already show under
              Assets — that comes from Monarch. This page is different: it builds a
              per-property P&amp;L from your <em>transactions</em>, and Monarch&apos;s
              merchant text doesn&apos;t know which charges belong to which property.
              You declare that mapping once:
            </p>
            <ol className="text-sm text-[var(--text-secondary)] space-y-2 list-decimal list-inside">
              <li>
                Copy{" "}
                <code className="font-mono text-[12px] bg-[var(--bg)] border border-[var(--border)] rounded px-1">
                  config/properties.example.json
                </code>{" "}
                to{" "}
                <code className="font-mono text-[12px] bg-[var(--bg)] border border-[var(--border)] rounded px-1">
                  config/properties.json
                </code>
              </li>
              <li>Edit it: your properties, merchant rules (lender, HOA, Airbnb…), exclusions</li>
              <li>
                Seed it:{" "}
                <code className="font-mono text-[12px] bg-[var(--bg)] border border-[var(--border)] rounded px-1">
                  docker compose exec backend uv run python ../scripts/seed_properties.py
                </code>
              </li>
            </ol>
            <p className="text-sm text-[var(--text-secondary)]">
              From then on every synced transaction classifies itself; one-offs are fixed
              inline on the Transactions page or by tagging the transaction with the
              property&apos;s name in Monarch.{" "}
              <a
                href="https://github.com/gdb-mtx/fire-master/blob/main/docs/MONARCH_SETUP.md#5-property-owners-classification-setup"
                target="_blank"
                rel="noreferrer"
                className="text-[var(--green)] hover:underline"
              >
                Full guide →
              </a>
            </p>
          </div>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="space-y-6">
        {/* ============================================================ */}
        {/* HEADER                                                        */}
        {/* ============================================================ */}
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <h2 className="text-sm uppercase tracking-[0.2em] text-[var(--text-secondary)] mb-1">
              Property P&amp;L
            </h2>
            <p className="text-2xl font-semibold text-[var(--text-primary)]">
              Real-estate income &amp; cost ledger
            </p>
            <p className="text-xs text-[var(--text-secondary)] mt-1 max-w-xl">
              Property transactions are pulled out of the Spending Tracker and reconciled
              here. They still count toward Runway &amp; burn.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-lg border border-[var(--border)] overflow-hidden">
              {(["all", "ytd", "12m"] as RangePreset[]).map((r) => (
                <button
                  key={r}
                  onClick={() => setRange(r)}
                  className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                    range === r
                      ? "bg-[rgba(0,212,170,0.12)] text-[var(--green)]"
                      : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  }`}
                >
                  {r === "all" ? "All time" : r === "ytd" ? "This year" : "12 mo"}
                </button>
              ))}
            </div>
            <button
              onClick={() => reclassify.mutate()}
              disabled={reclassify.isPending}
              className="px-3 py-1.5 text-xs font-medium rounded-lg border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--green)] transition-colors disabled:opacity-50"
            >
              {reclassify.isPending ? "Re-classifying…" : "Re-classify"}
            </button>
            <button
              onClick={() => setShowManage((s) => !s)}
              className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                showManage
                  ? "border-[var(--green)] text-[var(--green)]"
                  : "border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--green)]"
              }`}
            >
              Manage
            </button>
          </div>
        </div>

        {reclassify.isSuccess && reclassify.data && (
          <div className="text-xs text-[var(--text-secondary)] -mt-2">
            Re-classified · {reclassify.data.matched} matched ·{" "}
            {reclassify.data.skipped_manual} manual kept
          </div>
        )}

        {/* ============================================================ */}
        {/* PROPERTY CARDS                                                */}
        {/* ============================================================ */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {pnl.properties.map((p) => {
            const color = p.property.color ?? "var(--blue)";
            const isSel = selected?.property.id === p.property.id;
            const netPositive = p.totals.net_cost <= 0;
            return (
              <button
                key={p.property.id}
                onClick={() => setSelectedId(p.property.id)}
                className={`text-left bg-[var(--bg-card)] border rounded-xl p-5 relative overflow-hidden transition-all ${
                  isSel
                    ? "border-[var(--text-primary)]! shadow-lg"
                    : "border-[var(--border)] hover:border-[var(--text-secondary)]!"
                }`}
              >
                {/* Accent stripe */}
                <span
                  className="absolute left-0 top-0 bottom-0 w-1"
                  style={{ background: color }}
                />
                <div className="flex items-start justify-between mb-3 pl-2">
                  <div>
                    <div className="text-base font-semibold text-[var(--text-primary)]">
                      {p.property.name}
                    </div>
                    <div className="text-[11px] text-[var(--text-secondary)] truncate max-w-[200px]">
                      {p.property.address ?? "—"}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                      Equity
                    </div>
                    <div className="text-sm font-semibold text-[var(--text-primary)]">
                      {fmtCompact(p.property.equity)}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-2 pl-2">
                  <Metric label="Cost / mo" value={fmt(p.totals.monthly_cost)} />
                  <Metric
                    label="Rental / mo"
                    value={fmt(
                      p.property.potential_monthly_rental != null
                        ? rentalPerMonth(p)
                        : 0,
                    )}
                    sub={
                      p.property.potential_monthly_rental != null
                        ? `of ${fmt(p.property.potential_monthly_rental)}`
                        : undefined
                    }
                  />
                  <Metric
                    label="Net / yr"
                    value={fmt(Math.abs(p.totals.net_per_year))}
                    valueColor={netPositive ? "var(--green)" : "var(--red)"}
                    sub={netPositive ? "income" : "drain"}
                  />
                </div>

                {p.cost_per_equity != null && (
                  <div className="mt-3 pl-2 flex items-center gap-2">
                    <div className="flex-1 h-1.5 rounded-full bg-[rgba(255,255,255,0.06)] overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.min(p.cost_per_equity * 100, 100)}%`,
                          background: color,
                        }}
                      />
                    </div>
                    <span className="text-[10px] text-[var(--text-secondary)] whitespace-nowrap">
                      {(p.cost_per_equity * 100).toFixed(1)}¢ cost / $1 equity
                    </span>
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* Portfolio strip */}
        {portfolio && (
          <div className="flex flex-wrap gap-6 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-5 py-3 text-sm">
            <StripStat label="Portfolio cost" value={fmt(portfolio.cost)} />
            <StripStat label="Rental income" value={fmt(portfolio.rental)} color="var(--green)" />
            <StripStat
              label="Net carry"
              value={fmt(Math.abs(portfolio.cost - portfolio.rental))}
              color={portfolio.cost - portfolio.rental <= 0 ? "var(--green)" : "var(--red)"}
            />
            <StripStat label="Equity locked" value={fmtCompact(portfolio.equity)} />
          </div>
        )}

        {/* ============================================================ */}
        {/* MANAGE PANEL                                                  */}
        {/* ============================================================ */}
        {showManage && <PropertyManagePanel properties={properties} />}

        {/* ============================================================ */}
        {/* SELECTED PROPERTY DETAIL                                      */}
        {/* ============================================================ */}
        {selected && (
          <PropertyDetail
            data={selected}
            months={pnl.months}
            showTx={showTx}
            onToggleTx={() => setShowTx((s) => !s)}
          />
        )}
      </div>
    </Layout>
  );
}

function rentalPerMonth(p: PropertyPnL): number {
  // Actual rental income spread over the months that had any activity.
  const monthsWithData = new Set<string>();
  Object.values(p.categories).forEach((byMonth) =>
    Object.keys(byMonth).forEach((m) => monthsWithData.add(m)),
  );
  const n = monthsWithData.size || 1;
  return p.totals.rental_actual / n;
}

function Metric({
  label,
  value,
  sub,
  valueColor,
}: {
  label: string;
  value: string;
  sub?: string;
  valueColor?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
        {label}
      </div>
      <div
        className="text-sm font-semibold"
        style={{ color: valueColor ?? "var(--text-primary)" }}
      >
        {value}
      </div>
      {sub && <div className="text-[10px] text-[var(--text-secondary)]">{sub}</div>}
    </div>
  );
}

function StripStat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
        {label}
      </div>
      <div className="font-semibold" style={{ color: color ?? "var(--text-primary)" }}>
        {value}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Detail: month-by-month matrix + composed chart
// ---------------------------------------------------------------------------

function PropertyDetail({
  data,
  months,
  showTx,
  onToggleTx,
}: {
  data: PropertyPnL;
  months: string[];
  showTx: boolean;
  onToggleTx: () => void;
}) {
  const color = data.property.color ?? "var(--blue)";

  // Frozen-column depth cue: a soft gradient on the pinned Category column's
  // right edge, fading in only once the matrix is scrolled sideways. Done as a
  // pseudo-element (not box-shadow) because the table is border-collapse, which
  // suppresses box-shadow on cells.
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrolled, setScrolled] = useState(false);
  const stickyCol = `sticky left-0 bg-[var(--bg-card)] z-10 after:content-[''] after:pointer-events-none after:absolute after:top-0 after:right-0 after:h-full after:w-4 after:translate-x-full after:bg-gradient-to-r after:from-black/10 after:to-transparent after:transition-opacity after:duration-200 ${
    scrolled ? "after:opacity-100" : "after:opacity-0"
  }`;

  // Months that actually have data for this property, sorted.
  const activeMonths = useMemo(() => {
    const s = new Set<string>();
    Object.values(data.categories).forEach((byMonth) =>
      Object.keys(byMonth).forEach((m) => s.add(m)),
    );
    return months.filter((m) => s.has(m));
  }, [data, months]);

  const expenseCats = useMemo(
    () => Object.keys(data.categories).filter((c) => c !== RENTAL).sort(),
    [data],
  );

  // Chart rows: one per month, expense categories stacked + rental as line.
  const chartData = useMemo(
    () =>
      activeMonths.map((m) => {
        const row: Record<string, number | string> = { month: monthLabel(m) };
        expenseCats.forEach((c) => {
          row[c] = data.categories[c]?.[m] ?? 0;
        });
        row[RENTAL] = data.categories[RENTAL]?.[m] ?? 0;
        return row;
      }),
    [activeMonths, expenseCats, data],
  );

  const rowTotal = (cat: string) =>
    activeMonths.reduce((sum, m) => sum + (data.categories[cat]?.[m] ?? 0), 0);

  const colTotal = (m: string, cats: string[]) =>
    cats.reduce((sum, c) => sum + (data.categories[c]?.[m] ?? 0), 0);

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <span className="w-3 h-3 rounded-sm" style={{ background: color }} />
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">
          {data.property.name} · month by month
        </h3>
        <button
          onClick={onToggleTx}
          className="ml-auto text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline underline-offset-4"
        >
          {showTx ? "Hide transactions" : "View / reassign transactions"}
        </button>
      </div>

      {/* Chart */}
      <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
            <XAxis
              dataKey="month"
              tick={{ fill: "var(--text-secondary)", fontSize: 11 }}
              axisLine={{ stroke: "var(--border)" }}
              tickLine={false}
            />
            <YAxis
              tickFormatter={(v) => fmtCompact(Number(v))}
              tick={{ fill: "var(--text-secondary)", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={56}
            />
            <Tooltip {...TOOLTIP_STYLE} formatter={(v) => fmt(Number(v))} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {expenseCats.map((c) => (
              <Bar
                key={c}
                dataKey={c}
                stackId="cost"
                fill={CATEGORY_COLORS[c] ?? "#6a6e9e"}
                radius={[0, 0, 0, 0]}
              />
            ))}
            <Line
              type="monotone"
              dataKey={RENTAL}
              stroke="var(--green)"
              strokeWidth={2}
              dot={{ r: 2 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Matrix table */}
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const v = e.currentTarget.scrollLeft > 0;
          setScrolled((prev) => (prev === v ? prev : v));
        }}
        className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg overflow-x-auto overscroll-x-contain"
      >
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-[var(--text-secondary)] text-[11px] uppercase tracking-wider">
              <th className={`text-left px-4 py-3 ${stickyCol}`}>
                Category
              </th>
              {activeMonths.map((m) => (
                <th key={m} className="text-right px-3 py-3 whitespace-nowrap">
                  {monthLabel(m)}
                </th>
              ))}
              <th className="text-right px-4 py-3 whitespace-nowrap border-l border-[var(--border)]">
                Total
              </th>
            </tr>
          </thead>
          <tbody>
            {expenseCats.map((cat) => (
              <tr key={cat} className="border-t border-[var(--border)] hover:bg-[rgba(255,255,255,0.02)]">
                <td className={`px-4 py-2.5 text-[var(--text-primary)] whitespace-nowrap ${stickyCol}`}>
                  <span
                    className="inline-block w-2 h-2 rounded-sm mr-2 align-middle"
                    style={{ background: CATEGORY_COLORS[cat] ?? "#6a6e9e" }}
                  />
                  {cat}
                </td>
                {activeMonths.map((m) => {
                  const v = data.categories[cat]?.[m] ?? 0;
                  return (
                    <td
                      key={m}
                      className={`text-right px-3 py-2.5 tabular-nums ${
                        v ? "text-[var(--text-primary)]" : "text-[var(--text-secondary)] opacity-40"
                      }`}
                    >
                      {v ? fmt(v) : "·"}
                    </td>
                  );
                })}
                <td className="text-right px-4 py-2.5 tabular-nums font-semibold text-[var(--text-primary)] border-l border-[var(--border)]">
                  {fmt(rowTotal(cat))}
                </td>
              </tr>
            ))}

            {/* Total Cost */}
            <tr className="border-t-2 border-[var(--border)] bg-[rgba(255,255,255,0.02)]">
              <td className={`px-4 py-2.5 font-semibold text-[var(--text-primary)] ${stickyCol}`}>
                Total cost
              </td>
              {activeMonths.map((m) => (
                <td key={m} className="text-right px-3 py-2.5 tabular-nums font-semibold text-[var(--text-primary)]">
                  {fmt(colTotal(m, expenseCats))}
                </td>
              ))}
              <td className="text-right px-4 py-2.5 tabular-nums font-semibold text-[var(--text-primary)] border-l border-[var(--border)]">
                {fmt(data.totals.total_cost)}
              </td>
            </tr>

            {/* Rental Income */}
            <tr className="border-t border-[var(--border)]">
              <td className={`px-4 py-2.5 text-[var(--green)] ${stickyCol}`}>
                Rental income
              </td>
              {activeMonths.map((m) => {
                const v = data.categories[RENTAL]?.[m] ?? 0;
                return (
                  <td key={m} className="text-right px-3 py-2.5 tabular-nums" style={{ color: v ? "var(--green)" : undefined }}>
                    {v ? fmt(v) : "·"}
                  </td>
                );
              })}
              <td className="text-right px-4 py-2.5 tabular-nums font-semibold text-[var(--green)] border-l border-[var(--border)]">
                {fmt(data.totals.rental_actual)}
              </td>
            </tr>

            {/* Net */}
            <tr className="border-t border-[var(--border)] bg-[rgba(255,255,255,0.02)]">
              <td className={`px-4 py-2.5 font-semibold text-[var(--text-primary)] ${stickyCol}`}>
                Net {data.totals.net_cost <= 0 ? "income" : "drain"}
              </td>
              {activeMonths.map((m) => {
                const net = colTotal(m, expenseCats) - (data.categories[RENTAL]?.[m] ?? 0);
                return (
                  <td
                    key={m}
                    className="text-right px-3 py-2.5 tabular-nums"
                    style={{ color: net <= 0 ? "var(--green)" : "var(--red)" }}
                  >
                    {fmt(Math.abs(net))}
                  </td>
                );
              })}
              <td
                className="text-right px-4 py-2.5 tabular-nums font-semibold border-l border-[var(--border)]"
                style={{ color: data.totals.net_cost <= 0 ? "var(--green)" : "var(--red)" }}
              >
                {fmt(Math.abs(data.totals.net_cost))}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {showTx && <PropertyTransactionsTable propertyId={data.property.id} />}
    </div>
  );
}
