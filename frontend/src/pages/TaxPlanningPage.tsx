import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import {
  useBracketAnalysis,
  useWithdrawalPlan,
  useRothConversionPlan,
  useMonteCarlo,
  useSeppCalculator,
  useFireConfig,
  useUpdateFireConfig,
} from "../api/queries";
import type { SeppParams } from "../api/queries";
import Layout from "../components/Layout";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import type { BracketDetail } from "../types/tax";
import { formatCurrency as fmt, fmtCompact, fmtAxis, fmtPct } from "../utils/formatting";
import { TOOLTIP_STYLE } from "../utils/theme";

// ---------------------------------------------------------------------------
// Shared components
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// SEPP / 72(t) calculator card
// ---------------------------------------------------------------------------

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

const SEPP_INPUT_CLASS =
  "w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-1.5 text-sm font-mono text-[var(--text-primary)] focus:outline-none focus:border-[var(--blue)]";

function SeppCalculatorCard({ deferredBalance }: { deferredBalance?: number }) {
  const { data: fireConfig } = useFireConfig();
  const updateConfig = useUpdateFireConfig();

  const [balanceStr, setBalanceStr] = useState("");
  const [ageStr, setAgeStr] = useState("");
  const [rateStr, setRateStr] = useState("5");
  const [targetStr, setTargetStr] = useState("");
  const [confirming, setConfirming] = useState(false);

  // Prefill once per field: tax-deferred balance + the age the owner turns
  // this year (Notice 2022-6: age on your birthday in the first distribution
  // year). Separate flags — the two data sources load at different times.
  const balanceSeeded = useRef(false);
  const ageSeeded = useRef(false);
  useEffect(() => {
    if (!balanceSeeded.current && deferredBalance != null && deferredBalance > 0) {
      setBalanceStr(String(Math.round(deferredBalance)));
      balanceSeeded.current = true;
    }
    if (!ageSeeded.current && fireConfig?.date_of_birth) {
      const birthYear = new Date(fireConfig.date_of_birth).getFullYear();
      setAgeStr(String(new Date().getFullYear() - birthYear));
      ageSeeded.current = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deferredBalance, fireConfig]);

  const params = useMemo<SeppParams | null>(() => {
    const balance = parseFloat(balanceStr);
    const age = parseInt(ageStr, 10);
    const rate = parseFloat(rateStr) / 100;
    if (!(balance > 0) || !(age >= 21 && age <= 80) || !(rate >= 0)) return null;
    const target = parseFloat(targetStr);
    return {
      balance,
      age,
      rate,
      targetMonthly: target > 0 ? target : undefined,
    };
  }, [balanceStr, ageStr, rateStr, targetStr]);

  const debouncedParams = useDebounced(params, 400);
  const { data: sepp, error } = useSeppCalculator(debouncedParams);

  // What "Apply to plan" writes: reverse mode when a target is set.
  const applyValues = useMemo(() => {
    if (!sepp) return null;
    if (sepp.reverse) {
      return {
        monthly: Math.round(sepp.reverse.target_monthly),
        iraA: Math.round(sepp.reverse.required_balance),
      };
    }
    return {
      monthly: Math.round(sepp.amortization.monthly),
      iraA: Math.round(sepp.balance),
    };
  }, [sepp]);

  useEffect(() => setConfirming(false), [applyValues?.monthly, applyValues?.iraA]);

  const apply = () => {
    if (!applyValues || !fireConfig) return;
    if (!confirming) {
      setConfirming(true);
      return;
    }
    // Config PATCH merges custom_assumptions (RFC 7386, fire-master#9) — send
    // ONLY the two managed fields; siblings and other blocks survive untouched.
    // Resending a stale full spread here would clobber concurrent edits.
    updateConfig.mutate(
      {
        custom_assumptions: {
          sepp: {
            sepp_monthly: applyValues.monthly,
            ira_a_balance: applyValues.iraA,
          },
        },
      },
      { onSuccess: () => setConfirming(false) },
    );
  };

  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-medium text-[var(--text-secondary)]">
            SEPP / 72(t) Calculator
          </h3>
          <span className="text-xs text-[var(--text-secondary)]">
            Penalty-free withdrawals before 59½ &middot; Rev. Rul. 2002-62, Notice 2022-6
          </span>
        </div>
        {sepp && (
          <div className="text-right">
            <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] block">
              Life Expectancy
            </span>
            <span className="text-sm font-mono font-bold text-[var(--text-primary)]">
              {sepp.life_expectancy} yrs
            </span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Inputs */}
        <div className="space-y-3">
          <div>
            <label className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] block mb-1">
              IRA Balance ($)
            </label>
            <input
              type="number"
              value={balanceStr}
              onChange={(e) => setBalanceStr(e.target.value)}
              className={SEPP_INPUT_CLASS}
              placeholder="400000"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] block mb-1">
                Age
              </label>
              <input
                type="number"
                value={ageStr}
                onChange={(e) => setAgeStr(e.target.value)}
                className={SEPP_INPUT_CLASS}
                placeholder="54"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] block mb-1">
                Rate (%)
              </label>
              <input
                type="number"
                step="0.1"
                value={rateStr}
                onChange={(e) => setRateStr(e.target.value)}
                className={SEPP_INPUT_CLASS}
                placeholder="5"
              />
            </div>
          </div>
          <p className="text-[10px] text-[var(--text-secondary)] leading-relaxed">
            Age = your age on your birthday in the first distribution year.
            IRS rate cap: the greater of 5% or 120% of the federal mid-term
            rate — 5% is always allowed.
          </p>
          {error && (
            <p className="text-[11px] text-[var(--red)]">{(error as Error).message}</p>
          )}
        </div>

        {/* Method comparison */}
        <div className="space-y-2">
          <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] block">
            Annual Payment by IRS Method
          </span>
          {sepp ? (
            <>
              <div className="flex items-baseline justify-between px-3 py-2 rounded border border-[rgba(46,139,110,0.35)] bg-[rgba(46,139,110,0.06)]">
                <div>
                  <span className="text-xs font-medium text-[var(--text-primary)] block">
                    Fixed Amortization
                  </span>
                  <span className="text-[10px] text-[var(--text-secondary)]">
                    highest payment, fixed for the series
                  </span>
                </div>
                <div className="text-right">
                  <span className="text-sm font-mono font-bold text-[var(--green)] block">
                    {fmt(sepp.amortization.monthly)}/mo
                  </span>
                  <span className="text-[10px] font-mono text-[var(--text-secondary)]">
                    {fmt(sepp.amortization.annual)}/yr
                  </span>
                </div>
              </div>
              <div className="flex items-baseline justify-between px-3 py-2 rounded border border-[var(--border)]">
                <div>
                  <span className="text-xs font-medium text-[var(--text-primary)] block">
                    RMD Method
                  </span>
                  <span className="text-[10px] text-[var(--text-secondary)]">
                    recalculated annually, varies with balance
                  </span>
                </div>
                <div className="text-right">
                  <span className="text-sm font-mono font-bold text-[var(--text-primary)] block">
                    {fmt(sepp.rmd_method.monthly)}/mo
                  </span>
                  <span className="text-[10px] font-mono text-[var(--text-secondary)]">
                    {fmt(sepp.rmd_method.annual)}/yr
                  </span>
                </div>
              </div>
            </>
          ) : (
            <p className="text-xs text-[var(--text-secondary)]">
              Enter a balance, age, and rate.
            </p>
          )}
        </div>

        {/* Reverse solver + apply */}
        <div className="space-y-3">
          <div>
            <label className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] block mb-1">
              Reverse: I need ($/mo)
            </label>
            <input
              type="number"
              value={targetStr}
              onChange={(e) => setTargetStr(e.target.value)}
              className={SEPP_INPUT_CLASS}
              placeholder="2800"
            />
          </div>
          {sepp?.reverse && (
            <div className="px-3 py-2 rounded border border-[rgba(61,110,158,0.35)] bg-[rgba(61,110,158,0.06)]">
              <span className="text-xs text-[var(--text-primary)] block">
                Fund IRA-A with{" "}
                <span className="font-mono font-bold text-[var(--blue)]">
                  {fmt(sepp.reverse.required_balance)}
                </span>
              </span>
              <span className="text-[10px] text-[var(--text-secondary)]">
                The dual-IRA split: IRA-A pays your SEPP; the rest stays in
                IRA-B compounding untouched.
              </span>
            </div>
          )}
          {applyValues && (
            <button
              onClick={apply}
              disabled={updateConfig.isPending}
              className={`w-full px-3 py-2 rounded text-xs font-medium border transition-colors ${
                confirming
                  ? "border-[var(--yellow)] text-[var(--yellow)] bg-[rgba(160,125,26,0.08)]"
                  : "border-[var(--border)] text-[var(--text-primary)] hover:border-[var(--green)]"
              }`}
            >
              {updateConfig.isPending
                ? "Applying…"
                : confirming
                  ? `Confirm: write ${fmt(applyValues.monthly)}/mo + ${fmtCompact(applyValues.iraA)} IRA-A`
                  : "Apply to plan"}
            </button>
          )}
          <p className="text-[10px] text-[var(--text-secondary)]">
            Writes sepp_monthly + ira_a_balance to the BASE config (scenarios
            keep their own overrides). Projections refresh automatically.
          </p>
          {updateConfig.isSuccess && !confirming && (
            <p className="text-[11px] text-[var(--green)]">Applied — SEPP plan updated.</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bracket bar visualization
// ---------------------------------------------------------------------------

const BRACKET_COLORS = [
  "var(--green)", // 10% — green (low tax)
  "#4de8c4", // 12%
  "var(--blue)", // 22% — blue
  "var(--yellow)", // 24% — yellow
  "#ff8a4d", // 32% — orange
  "var(--red)", // 35% — red
  "#e03060", // 37% — deep red
];

function BracketBar({ brackets, room }: { brackets: BracketDetail[]; room: { current_rate: number; next_rate: number | null; room_dollars: number } }) {
  const totalIncome = brackets.reduce((s, b) => s + b.income_in_bracket, 0);
  if (totalIncome <= 0) return null;

  return (
    <div className="space-y-3">
      {/* The stacked bar */}
      <div className="flex h-8 rounded overflow-hidden">
        {brackets.map((b, i) => {
          const widthPct = (b.income_in_bracket / totalIncome) * 100;
          if (widthPct < 0.5) return null;
          return (
            <div
              key={i}
              className="relative group flex items-center justify-center transition-all duration-300 hover:brightness-125"
              style={{
                width: `${widthPct}%`,
                background: BRACKET_COLORS[i] || BRACKET_COLORS[BRACKET_COLORS.length - 1],
                opacity: 0.85,
              }}
            >
              {widthPct > 8 && (
                <span className="text-[10px] font-mono font-bold text-[#0a0a0f] drop-shadow-sm">
                  {(b.rate * 100).toFixed(0)}%
                </span>
              )}
              {/* Hover tooltip */}
              <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 hidden group-hover:block z-10 whitespace-nowrap">
                <div className="bg-[#0a0a0f] border border-[var(--border)] rounded px-2 py-1 text-[10px] font-mono text-[var(--text-primary)] shadow-lg">
                  {(b.rate * 100).toFixed(0)}% bracket: {fmt(b.income_in_bracket)} → {fmt(b.tax_in_bracket)} tax
                </div>
              </div>
            </div>
          );
        })}
        {/* Room indicator */}
        {room.room_dollars > 0 && (
          <div
            className="flex items-center justify-center border-l-2 border-dashed border-[var(--text-secondary)]"
            style={{
              width: `${Math.min(25, (room.room_dollars / totalIncome) * 100)}%`,
              background: "rgba(77,142,255,0.08)",
            }}
          >
            <span className="text-[9px] font-mono text-[var(--blue)] opacity-80">
              room
            </span>
          </div>
        )}
      </div>

      {/* Bracket legend row */}
      <div className="flex items-center gap-4 flex-wrap">
        {brackets.map((b, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <div
              className="w-2 h-2 rounded-sm"
              style={{ background: BRACKET_COLORS[i] || BRACKET_COLORS[BRACKET_COLORS.length - 1] }}
            />
            <span className="text-[10px] font-mono text-[var(--text-secondary)]">
              {(b.rate * 100).toFixed(0)}%: {fmtCompact(b.income_in_bracket)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function TaxPlanningPage() {
  const { data: brackets, isLoading: loadingBrackets } = useBracketAnalysis();
  const { data: withdrawal } = useWithdrawalPlan(30);
  const { data: roth } = useRothConversionPlan(0.22);
  const { data: monteCarlo, isLoading: loadingMC } = useMonteCarlo(1000);

  const isLoading = loadingBrackets || loadingMC;

  // Monte Carlo fan chart data — transform percentile curves for Recharts
  const fanChartData = useMemo(() => {
    if (!monteCarlo?.percentile_curves) return [];
    return monteCarlo.percentile_curves.map((p) => ({
      age: Math.round(p.age),
      p10: p.p10,
      p25: p.p25,
      p50: p.p50,
      p75: p.p75,
      p90: p.p90,
      // Bands for stacking: each band = difference between adjacent percentiles
      band_10_25: Math.max(0, p.p25 - p.p10),
      band_25_50: Math.max(0, p.p50 - p.p25),
      band_50_75: Math.max(0, p.p75 - p.p50),
      band_75_90: Math.max(0, p.p90 - p.p75),
    }));
  }, [monteCarlo]);

  if (isLoading || !brackets) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-96 text-[var(--text-secondary)]">
          Loading...
        </div>
      </Layout>
    );
  }

  const successColor =
    (monteCarlo?.success_rate ?? 0) >= 85
      ? "var(--green)"
      : (monteCarlo?.success_rate ?? 0) >= 70
        ? "var(--yellow)"
        : "var(--red)";

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-xl font-semibold text-[var(--text-primary)] tracking-tight">
              Tax Planning
            </h2>
            <p className="text-sm text-[var(--text-secondary)] mt-1">
              Withdrawal strategy & Roth conversions &middot; projections in today&rsquo;s dollars
            </p>
          </div>
          <Link
            to="/settings"
            className="px-3 py-1.5 text-xs bg-[var(--bg-secondary)] border border-[var(--border)] rounded hover:border-[var(--blue)] transition-colors"
          >
            Configure
          </Link>
        </div>

        {/* Stat Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            label="Total Tax"
            value={fmt(brackets.total_tax)}
            color="var(--yellow)"
            sub={`Federal ${fmt(brackets.federal_tax)} + State ${fmt(brackets.state_tax)}`}
          />
          <StatCard
            label="Effective Rate"
            value={fmtPct(brackets.overall_effective_rate)}
            color="var(--text-primary)"
            sub={`Marginal ${fmtPct(brackets.federal_marginal_rate)}`}
          />
          <StatCard
            label="Bracket Room"
            value={fmtCompact(brackets.bracket_room.room_dollars)}
            color="var(--blue)"
            sub={`Until ${brackets.bracket_room.next_rate ? fmtPct(brackets.bracket_room.next_rate) : "top"} bracket`}
          />
          <StatCard
            label="Monte Carlo"
            value={monteCarlo ? `${monteCarlo.success_rate}%` : "—"}
            color={successColor}
            sub={monteCarlo ? `${monteCarlo.total_runs.toLocaleString()} simulations` : "Loading..."}
          />
        </div>

        {/* Two-column: Monte Carlo + Brackets */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Monte Carlo Fan Chart */}
          <div className="lg:col-span-2 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                Monte Carlo Simulation
              </h3>
              {monteCarlo && (
                <div className="flex items-center gap-2">
                  <span
                    className="text-lg font-bold font-mono"
                    style={{ color: successColor }}
                  >
                    {monteCarlo.success_rate}%
                  </span>
                  <span className="text-xs text-[var(--text-secondary)]">success</span>
                </div>
              )}
            </div>

            {fanChartData.length > 0 ? (
              <>
                <ResponsiveContainer width="100%" height={380}>
                  <AreaChart
                    data={fanChartData}
                    margin={{ top: 10, right: 10, left: 10, bottom: 0 }}
                  >
                    <defs>
                      <linearGradient id="gradBand10" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#b04a56" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="#b04a56" stopOpacity={0.05} />
                      </linearGradient>
                      <linearGradient id="gradBand25" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#a07d1a" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="#a07d1a" stopOpacity={0.05} />
                      </linearGradient>
                      <linearGradient id="gradBand50" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#2e8b6e" stopOpacity={0.3} />
                        <stop offset="100%" stopColor="#2e8b6e" stopOpacity={0.05} />
                      </linearGradient>
                      <linearGradient id="gradBand75" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#3d6e9e" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="#3d6e9e" stopOpacity={0.05} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(42,42,62,0.5)" />
                    <XAxis
                      dataKey="age"
                      stroke="#5c5c6a"
                      tick={{ fontSize: 11, fill: "#5c5c6a" }}
                      tickFormatter={(v) => `${v}`}
                    />
                    <YAxis
                      stroke="#5c5c6a"
                      tick={{ fontSize: 11, fill: "#5c5c6a" }}
                      tickFormatter={fmtAxis}
                      width={65}
                    />
                    <Tooltip
                      {...TOOLTIP_STYLE}
                      labelFormatter={(age) => `Age ${age}`}
                      formatter={(value, name) => {
                        const labels: Record<string, string> = {
                          p10: "10th %ile",
                          p25: "25th %ile",
                          p50: "Median",
                          p75: "75th %ile",
                          p90: "90th %ile",
                        };
                        return [fmtCompact(Number(value)), labels[String(name)] || String(name)];
                      }}
                    />
                    <ReferenceLine y={0} stroke="var(--red)" strokeDasharray="4 4" strokeOpacity={0.6} />

                    {/* Stacked bands: base = p10, then bands on top */}
                    <Area type="monotone" dataKey="p10" stackId="fan" stroke="none" fill="url(#gradBand10)" />
                    <Area type="monotone" dataKey="band_10_25" stackId="fan" stroke="none" fill="url(#gradBand10)" />
                    <Area type="monotone" dataKey="band_25_50" stackId="fan" stroke="none" fill="url(#gradBand25)" />
                    <Area type="monotone" dataKey="band_50_75" stackId="fan" stroke="none" fill="url(#gradBand50)" />
                    <Area type="monotone" dataKey="band_75_90" stackId="fan" stroke="none" fill="url(#gradBand75)" />

                    {/* Median line */}
                    <Area type="monotone" dataKey="p50" stackId="none" stroke="var(--green)" strokeWidth={2} fill="none" dot={false} />
                  </AreaChart>
                </ResponsiveContainer>

                {/* Percentile summary row */}
                {monteCarlo && (
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-4 pt-4 border-t border-[var(--border)]">
                    {[
                      { label: "Worst 10%", value: monteCarlo.percentile_10, color: "var(--red)" },
                      { label: "25th %ile", value: monteCarlo.percentile_25, color: "var(--yellow)" },
                      { label: "Median", value: monteCarlo.percentile_50, color: "var(--green)" },
                      { label: "75th %ile", value: monteCarlo.percentile_75, color: "var(--blue)" },
                      { label: "Best 10%", value: monteCarlo.percentile_90, color: "var(--blue)" },
                    ].map((p) => (
                      <div key={p.label} className="text-center">
                        <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                          {p.label}
                        </span>
                        <div className="text-sm font-mono font-bold mt-0.5" style={{ color: p.color }}>
                          {fmtCompact(p.value)}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="flex items-center justify-center h-[380px] text-[var(--text-secondary)] text-sm">
                {loadingMC ? "Running simulations..." : "Configure FIRE settings to run Monte Carlo."}
              </div>
            )}
          </div>

          {/* Bracket Visualization + ACA */}
          <div className="space-y-6">
            <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">
                Federal Tax Brackets
              </h3>

              <BracketBar brackets={brackets.federal_brackets} room={brackets.bracket_room} />

              {/* Rate comparison */}
              <div className="grid grid-cols-2 gap-3 mt-4 pt-3 border-t border-[var(--border)]">
                <div>
                  <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                    Marginal Rate
                  </span>
                  <div className="text-lg font-bold font-mono text-[var(--yellow)]">
                    {fmtPct(brackets.federal_marginal_rate)}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                    Effective Rate
                  </span>
                  <div className="text-lg font-bold font-mono text-[var(--green)]">
                    {fmtPct(brackets.federal_effective_rate)}
                  </div>
                </div>
              </div>

              {/* Income summary */}
              <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--text-secondary)]">Gross Income</span>
                  <span className="font-mono text-[var(--text-primary)]">{fmt(brackets.gross_income)}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--text-secondary)]">Standard Deduction</span>
                  <span className="font-mono text-[var(--green)]">-{fmt(brackets.standard_deduction)}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--text-secondary)]">Taxable Income</span>
                  <span className="font-mono text-[var(--text-primary)]">{fmt(brackets.taxable_income)}</span>
                </div>
              </div>
            </div>

            {/* Account Balances by Tax Treatment */}
            <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">
                Accounts by Tax Treatment
              </h3>
              <div className="space-y-2">
                {[
                  { label: "Tax-Deferred", value: brackets.account_balances.tax_deferred, color: "var(--yellow)", accounts: brackets.account_balances.tax_deferred_accounts },
                  { label: "Tax-Free", value: brackets.account_balances.tax_free, color: "var(--green)", accounts: brackets.account_balances.tax_free_accounts },
                  { label: "Taxable", value: brackets.account_balances.taxable, color: "var(--blue)", accounts: brackets.account_balances.taxable_accounts },
                ].map((bucket) => (
                  <div key={bucket.label}>
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-[var(--text-secondary)]">{bucket.label}</span>
                      <span className="text-xs font-mono font-bold" style={{ color: bucket.color }}>
                        {fmtCompact(bucket.value)}
                      </span>
                    </div>
                    {bucket.accounts.length > 0 && (
                      <div className="ml-3 mt-0.5 space-y-0.5">
                        {bucket.accounts.map((a) => (
                          <div key={a.name} className="flex justify-between text-[10px] text-[var(--text-secondary)]">
                            <span className="truncate mr-2">{a.name}</span>
                            <span className="font-mono shrink-0">{fmtCompact(a.balance)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* ACA Subsidy Indicator */}
            <div
              className="bg-[var(--bg-card)] border rounded-lg p-4"
              style={{
                borderColor: brackets.aca.cliff_warning
                  ? "rgba(255,192,77,0.5)"
                  : "var(--border)",
              }}
            >
              <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">
                ACA Healthcare
              </h3>
              <div className="space-y-2">
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--text-secondary)]">MAGI</span>
                  <span className="font-mono text-[var(--text-primary)]">{fmt(brackets.aca.magi)}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--text-secondary)]">FPL %</span>
                  <span className="font-mono text-[var(--text-primary)]">{brackets.aca.fpl_percentage}%</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--text-secondary)]">Monthly Subsidy</span>
                  <span className="font-mono" style={{ color: brackets.aca.monthly_subsidy > 0 ? "var(--green)" : "var(--text-secondary)" }}>
                    {fmt(brackets.aca.monthly_subsidy)}
                  </span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--text-secondary)]">Net Premium</span>
                  <span className="font-mono text-[var(--yellow)]">{fmt(brackets.aca.net_monthly_cost)}/mo</span>
                </div>
                {brackets.aca.cliff_warning && (
                  <div className="mt-2 px-2 py-1.5 rounded bg-[rgba(255,192,77,0.08)] border border-[rgba(255,192,77,0.2)]">
                    <span className="text-[10px] text-[var(--yellow)]">
                      Warning: {fmt(brackets.aca.cliff_distance)} from subsidy cliff
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* SEPP / 72(t) Calculator */}
        <SeppCalculatorCard deferredBalance={brackets?.account_balances.tax_deferred} />

        {/* Roth Conversion Ladder */}
        {roth && roth.years.length > 0 && (
          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                  Roth Conversion Ladder
                </h3>
                <span className="text-xs text-[var(--text-secondary)]">
                  Window: {roth.conversion_window} &middot; Target: {fmtPct(roth.target_bracket_rate)} bracket
                </span>
              </div>
              <div className="flex items-center gap-4">
                <div className="text-right">
                  <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] block">
                    Tax Saved
                  </span>
                  <span className="text-sm font-mono font-bold text-[var(--green)]">
                    {fmtCompact(roth.estimated_tax_saved)}
                  </span>
                  <span className="text-[10px] text-[var(--text-secondary)] block">
                    vs {fmtPct(roth.assumed_rmd_marginal_rate)} at RMDs
                  </span>
                </div>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[var(--border)]">
                    {["Year", "Age", "Baseline", "Conversion", "Tax Cost", "Cumulative", "MAGI"].map((h) => (
                      <th
                        key={h}
                        className="py-2 px-3 text-left font-medium text-[var(--text-secondary)] uppercase tracking-wider text-[10px]"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {roth.years.map((yr) => (
                    <tr
                      key={yr.year}
                      className="border-b border-[rgba(42,42,62,0.3)] hover:bg-[rgba(255,255,255,0.02)] transition-colors"
                    >
                      <td className="py-2 px-3 font-mono text-[var(--text-primary)]">{yr.year}</td>
                      <td className="py-2 px-3 font-mono text-[var(--text-secondary)]">{yr.age}</td>
                      <td className="py-2 px-3 font-mono text-[var(--text-secondary)]">{fmt(yr.baseline_income)}</td>
                      <td className="py-2 px-3 font-mono text-[var(--green)]">{fmt(yr.conversion_amount)}</td>
                      <td className="py-2 px-3 font-mono text-[var(--red)]">{fmt(yr.tax_on_conversion)}</td>
                      <td className="py-2 px-3 font-mono text-[var(--text-primary)]">{fmtCompact(yr.cumulative_converted)}</td>
                      <td className="py-2 px-3 font-mono text-[var(--text-secondary)]">{fmt(yr.magi_after_conversion)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-[var(--border)]">
                    <td colSpan={3} className="py-2 px-3 text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                      Totals
                    </td>
                    <td className="py-2 px-3 font-mono font-bold text-[var(--green)]">{fmtCompact(roth.total_converted)}</td>
                    <td className="py-2 px-3 font-mono font-bold text-[var(--red)]">{fmtCompact(roth.total_tax_paid)}</td>
                    <td colSpan={2} className="py-2 px-3 font-mono font-bold text-[var(--green)]">
                      saves {fmtCompact(roth.estimated_tax_saved)}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>
        )}

        {/* Withdrawal Sequence */}
        {withdrawal && withdrawal.years.length > 0 && (
          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                Withdrawal Sequence
              </h3>
              <div className="flex items-center gap-4">
                <div className="text-right">
                  <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] block">
                    Avg Effective Rate
                  </span>
                  <span className="text-sm font-mono font-bold text-[var(--text-primary)]">
                    {fmtPct(withdrawal.average_effective_rate)}
                  </span>
                </div>
                <div className="text-right">
                  <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] block">
                    Total Tax
                  </span>
                  <span className="text-sm font-mono font-bold text-[var(--yellow)]">
                    {fmtCompact(withdrawal.total_tax_paid)}
                  </span>
                </div>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[var(--border)]">
                    {["Year", "Age", "Taxable", "Deferred", "Roth", "Cash", "Total Tax", "Eff. Rate", "After-Tax"].map((h) => (
                      <th
                        key={h}
                        className="py-2 px-3 text-left font-medium text-[var(--text-secondary)] uppercase tracking-wider text-[10px]"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {withdrawal.years.map((yr) => (
                    <tr
                      key={yr.year}
                      className="border-b border-[rgba(42,42,62,0.3)] hover:bg-[rgba(255,255,255,0.02)] transition-colors"
                    >
                      <td className="py-2 px-3 font-mono text-[var(--text-primary)]">{yr.year}</td>
                      <td className="py-2 px-3 font-mono text-[var(--text-secondary)]">{yr.age}</td>
                      <td className="py-2 px-3 font-mono" style={{ color: yr.from_taxable > 0 ? "var(--blue)" : "var(--text-secondary)" }}>
                        {yr.from_taxable > 0 ? fmt(yr.from_taxable) : "—"}
                      </td>
                      <td className="py-2 px-3 font-mono" style={{ color: yr.from_deferred > 0 ? "var(--yellow)" : "var(--text-secondary)" }}>
                        {yr.from_deferred > 0 ? fmt(yr.from_deferred) : "—"}
                      </td>
                      <td className="py-2 px-3 font-mono" style={{ color: yr.from_roth > 0 ? "var(--green)" : "var(--text-secondary)" }}>
                        {yr.from_roth > 0 ? fmt(yr.from_roth) : "—"}
                      </td>
                      <td className="py-2 px-3 font-mono" style={{ color: yr.from_cash > 0 ? "var(--purple, #7a6aaa)" : "var(--text-secondary)" }}>
                        {yr.from_cash > 0 ? fmt(yr.from_cash) : "—"}
                      </td>
                      <td className="py-2 px-3 font-mono text-[var(--red)]">
                        {yr.total_tax > 0 ? fmt(yr.total_tax) : "—"}
                      </td>
                      <td className="py-2 px-3 font-mono text-[var(--text-secondary)]">
                        {yr.effective_rate > 0 ? fmtPct(yr.effective_rate) : "—"}
                      </td>
                      <td className="py-2 px-3 font-mono text-[var(--text-primary)]">
                        {yr.after_tax_income > 0 ? fmt(yr.after_tax_income) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-[var(--border)]">
                    <td colSpan={6} className="py-2 px-3 text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                      {withdrawal.years.length}-Year Total
                    </td>
                    <td className="py-2 px-3 font-mono font-bold text-[var(--red)]">{fmtCompact(withdrawal.total_tax_paid)}</td>
                    <td className="py-2 px-3 font-mono font-bold text-[var(--text-secondary)]">{fmtPct(withdrawal.average_effective_rate)}</td>
                    <td className="py-2 px-3 font-mono font-bold text-[var(--text-primary)]">{fmtCompact(withdrawal.total_withdrawn - withdrawal.total_tax_paid)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
}
