import { useMemo, useState, useRef, useEffect } from "react";
import { Link } from "react-router";
import {
  useActivateScenario,
  useBridgeStatus,
  useFireNumber,
  useFireReadiness,
  useFireTimeline,
  useIncomeSources,
  useMilestones,
  useMonteCarlo,
  useRetirementAgeAnalysis,
  useScenarios,
  useSpendingSensitivity,
  useWealthProjection,
} from "../api/queries";
import Layout from "../components/Layout";
import { formatCurrency as fmt, fmtCompact, fmtAxis } from "../utils/formatting";
import { useEventMarkers } from "../components/charts/EventMarkers";
import { useIsMobile } from "../hooks/useIsMobile";
import type { EventMarkerGroup } from "../components/charts/EventMarkers";
import { TOOLTIP_STYLE } from "../utils/theme";
import type { Milestone, SpendingSensitivity } from "../types/fire";
import {
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  CartesianGrid,
  Line,
  ComposedChart,
} from "recharts";

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

const MILESTONE_COLORS: Record<string, string> = {
  past: "#6b7280",
  upcoming: "var(--green)",
  distant: "var(--blue)",
};

function MilestoneTimeline({ milestones, currentAge }: { milestones: Milestone[]; currentAge: number }) {
  if (milestones.length === 0) return null;

  const minAge = Math.min(currentAge, milestones[0]?.age ?? currentAge);
  const maxAge = Math.max(...milestones.map((m) => m.age)) + 2;
  const range = maxAge - minAge;

  const currentPct = ((currentAge - minAge) / range) * 100;

  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-[var(--text-secondary)]">
          Financial Milestones
        </h3>
        <span className="text-xs font-mono text-[var(--text-secondary)]">
          Age {currentAge.toFixed(1)} today
        </span>
      </div>

      {/* Timeline bar */}
      <div className="relative h-16 mb-2">
        {/* Track */}
        <div className="absolute top-6 left-0 right-0 h-[3px] bg-[var(--bg-secondary)] rounded-full" />

        {/* Filled portion up to current age */}
        <div
          className="absolute top-6 left-0 h-[3px] rounded-full"
          style={{
            width: `${Math.min(100, currentPct)}%`,
            background: "linear-gradient(90deg, var(--green), rgba(0,212,170,0.3))",
          }}
        />

        {/* Current age marker */}
        <div
          className="absolute top-[18px] -translate-x-1/2"
          style={{ left: `${Math.min(98, Math.max(2, currentPct))}%` }}
        >
          <div className="w-2.5 h-2.5 rounded-full bg-[var(--green)] ring-2 ring-[var(--bg-card)] shadow-[0_0_8px_rgba(0,212,170,0.5)]" />
          <span className="absolute -top-5 left-1/2 -translate-x-1/2 text-[10px] font-mono text-[var(--green)] whitespace-nowrap font-medium">
            NOW
          </span>
        </div>

        {/* Milestone markers */}
        {milestones.map((m) => {
          const pct = ((m.age - minAge) / range) * 100;
          const color = MILESTONE_COLORS[m.status] ?? "var(--blue)";
          return (
            <div
              key={m.age}
              className="absolute -translate-x-1/2 group"
              style={{ left: `${Math.min(98, Math.max(2, pct))}%`, top: 0 }}
            >
              {/* Dot */}
              <div
                className="absolute top-[18px] left-1/2 -translate-x-1/2 w-3 h-3 rounded-full border-2 border-[var(--bg-card)]"
                style={{ backgroundColor: color }}
              />
              {/* Label below */}
              <div className="absolute top-[34px] left-1/2 -translate-x-1/2 flex flex-col items-center">
                <span className="text-[10px] font-mono font-medium whitespace-nowrap" style={{ color }}>
                  {m.age === 59.5 ? "59\u00BD" : m.age}
                </span>
              </div>

              {/* Tooltip on hover */}
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-8 hidden group-hover:block z-10">
                <div className="bg-[#1a1a2e] border border-[#2a2a3e] rounded-lg p-3 shadow-[0_8px_32px_rgba(0,0,0,0.6)] min-w-[180px]">
                  <div className="text-xs font-medium text-[var(--text-primary)] mb-1">{m.label}</div>
                  <div className="text-[10px] text-[var(--text-secondary)] mb-1.5">{m.description}</div>
                  <div className="text-xs font-mono font-medium" style={{ color }}>{m.financial_impact}</div>
                  <div className="text-[10px] text-[var(--text-secondary)] mt-1">
                    {new Date(m.date).toLocaleDateString("en-US", { month: "short", year: "numeric" })}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Milestone cards row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-6 pt-3 border-t border-[var(--border)]">
        {milestones.map((m) => {
          const color = MILESTONE_COLORS[m.status] ?? "var(--blue)";
          const yearsAway = m.age - currentAge;
          return (
            <div key={m.age} className="flex flex-col gap-0.5">
              <div className="flex items-center gap-1.5">
                <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: color }} />
                <span className="text-xs font-medium text-[var(--text-primary)]">
                  {m.label}
                </span>
              </div>
              <span className="text-[10px] font-mono" style={{ color }}>
                {m.financial_impact}
              </span>
              <span className="text-[10px] text-[var(--text-secondary)]">
                {yearsAway <= 0
                  ? "Reached"
                  : `${yearsAway.toFixed(1)}yr away`}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ScenarioSelector() {
  const { data: scenarios } = useScenarios();
  const activate = useActivateScenario();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const active = scenarios?.find((s) => s.is_active);
  const label = active ? active.name : "No Scenario";

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="px-3 py-1.5 text-xs bg-[var(--bg-secondary)] border border-[var(--border)] rounded hover:border-[var(--blue)] transition-colors flex items-center gap-1.5"
      >
        <span
          className="w-2 h-2 rounded-full"
          style={{ background: active ? "var(--green)" : "var(--text-secondary)" }}
        />
        {label}
        <svg className="w-3 h-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && scenarios && (
        <div className="absolute right-0 top-full mt-1 w-64 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-xl z-50 py-1">
          {scenarios.map((s) => (
            <button
              key={s.id}
              onClick={() => { activate.mutate(s.id); setOpen(false); }}
              className={`w-full text-left px-3 py-2 text-xs hover:bg-[var(--bg-hover)] flex items-center gap-2 ${s.is_active ? "text-[var(--green)]" : "text-[var(--text-primary)]"}`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${s.is_active ? "bg-[var(--green)]" : "bg-transparent border border-[var(--text-secondary)]"}`} />
              <div className="flex-1 min-w-0">
                <div className="truncate">{s.name}</div>
                {s.description && (
                  <div className="text-[10px] text-[var(--text-secondary)] truncate">{s.description}</div>
                )}
              </div>
            </button>
          ))}
          {scenarios.length === 0 && (
            <div className="px-3 py-2 text-[10px] text-[var(--text-secondary)]">
              No scenarios saved. Create one in Configure.
            </div>
          )}
          <div className="h-px bg-[var(--border)] my-1" />
          <Link
            to="/settings"
            onClick={() => setOpen(false)}
            className="block px-3 py-2 text-[10px] text-[var(--blue)] hover:bg-[var(--bg-hover)]"
          >
            Manage Scenarios...
          </Link>
        </div>
      )}
    </div>
  );
}

function SpendingSensitivityCard({
  baseOverride,
  onBaseChange,
  sensitivity,
}: {
  baseOverride: number | null;
  onBaseChange: (v: number | null) => void;
  sensitivity: SpendingSensitivity | undefined;
}) {
  const currentBase = sensitivity?.base_monthly ?? 0;
  const healthcare = sensitivity?.healthcare_monthly ?? 0;
  const b = sensitivity?.breakdown;
  const displayBase = baseOverride ?? currentBase;
  const total = displayBase + healthcare;
  const isModified = baseOverride != null;

  // Recompute non-housing when user adjusts total budget
  const nonHousing = b ? displayBase - b.primary_property_all_in - b.income_property_cost - b.secondary_property_cost : 0;
  const mortgagePayoffLabel = b?.primary_property_mortgage_payoff_date
    ? new Date(`${b.primary_property_mortgage_payoff_date}T12:00:00`).toLocaleDateString(
        "en-US", { month: "short", year: "numeric" },
      )
    : null;

  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-[var(--text-secondary)]">
          Monthly Spending
        </h3>
        {isModified && (
          <button
            onClick={() => onBaseChange(null)}
            className="text-xs px-2 py-0.5 rounded border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--blue)] hover:border-[var(--blue)] transition-colors"
          >
            Reset
          </button>
        )}
      </div>

      {/* Total budget input */}
      <div className="mb-4">
        <label className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-1.5 block">
          Total Monthly Budget
          <span className="normal-case tracking-normal ml-1 opacity-60">(incl. housing)</span>
        </label>
        <div className="flex items-baseline gap-1">
          <span className="text-sm text-[var(--text-secondary)]">$</span>
          <input
            type="number"
            value={displayBase}
            onChange={(e) => {
              const v = parseFloat(e.target.value);
              onBaseChange(isNaN(v) ? null : v);
            }}
            className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-2 py-1.5 text-sm font-mono font-bold text-[var(--text-primary)] w-28 focus:border-[var(--blue)] focus:outline-none transition-colors"
          />
          <span className="text-xs text-[var(--text-secondary)]">/mo</span>
          <span className="text-xs text-[var(--text-secondary)] ml-2">
            + ${healthcare.toLocaleString()} healthcare
          </span>
          <span className="text-xs font-mono font-bold ml-auto" style={{ color: isModified ? "var(--blue)" : "var(--text-primary)" }}>
            = ${total.toLocaleString()}/mo
          </span>
        </div>
      </div>

      {/* Breakdown: what's inside the budget */}
      {b && (
        <div className="mb-4 py-3 border-t border-[var(--border)]">
          <div className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2">
            What's in the ${displayBase.toLocaleString()}
          </div>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Primary property housing</span>
              <span className="font-mono text-[var(--text-primary)]">${b.primary_property_all_in.toLocaleString()}</span>
            </div>
            <div className="flex justify-between pl-3">
              <span className="text-[var(--text-secondary)] opacity-60">
                P&amp;I ${b.primary_property_pi.toLocaleString()}
                {b.primary_property_mortgage_rate_pct > 0 && ` · ${b.primary_property_mortgage_rate_pct}%`}
                {mortgagePayoffLabel && ` · final payment ${mortgagePayoffLabel}`}
                {b.primary_property_all_in > b.primary_property_pi && ` + other ${(b.primary_property_all_in - b.primary_property_pi).toLocaleString()}`}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Income property</span>
              <span className="font-mono text-[var(--text-primary)]">${b.income_property_cost.toLocaleString()}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Secondary property</span>
              <span className="font-mono text-[var(--text-primary)]">${b.secondary_property_cost.toLocaleString()}</span>
            </div>
            <div className="flex justify-between pt-1 border-t border-[var(--border)]/50">
              <span className="text-[var(--text-secondary)] font-medium">Non-housing living</span>
              <span className="font-mono font-bold" style={{ color: nonHousing < 2000 ? "var(--red)" : "var(--text-primary)" }}>
                ${nonHousing.toLocaleString()}
              </span>
            </div>
          </div>
          <div className="mt-3 text-[10px] text-[var(--text-secondary)] opacity-60 leading-relaxed">
            {mortgagePayoffLabel && <>Mortgage P&amp;I drops from spending after {mortgagePayoffLabel}. </>}
            After primary property sale: −${b.primary_property_all_in.toLocaleString()} +${b.post_sale_rent.toLocaleString()} rent.
            After secondary sells: −${b.secondary_property_cost.toLocaleString()}.
            Healthcare ${healthcare.toLocaleString()}/mo added pre-65, drops at Medicare.
            Spending phases: 85% at 70, 75% at 80.
          </div>
        </div>
      )}

      {/* Sensitivity strip */}
      {sensitivity?.points && sensitivity.points.length > 0 && (
        <div className="pt-4 border-t border-[var(--border)]">
          <div className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-3">
            Impact on Wealth at 82
          </div>
          <div className="grid grid-cols-3 md:grid-cols-5 gap-1.5">
            {sensitivity.points.map((pt, i) => {
              const isCurrent = Math.abs(pt.monthly_spending - currentBase) < 50;
              const isSelected = baseOverride != null && Math.abs(pt.monthly_spending - baseOverride) < 50;
              const hasCrisis = pt.cash_zero_month != null;
              return (
                <button
                  key={i}
                  onClick={() =>
                    onBaseChange(isCurrent ? null : pt.monthly_spending)
                  }
                  className={`rounded-lg py-2.5 px-1 text-center transition-all ${
                    isSelected
                      ? "bg-[var(--blue)]/15 ring-1 ring-[var(--blue)]/40"
                      : isCurrent
                        ? "bg-[var(--bg-secondary)] ring-1 ring-[var(--text-secondary)]/20"
                        : "bg-[var(--bg-secondary)] hover:bg-[var(--bg-secondary)]/80"
                  }`}
                >
                  <div className="text-[11px] font-mono text-[var(--text-secondary)]">
                    ${(pt.monthly_spending / 1000).toFixed(1)}K
                  </div>
                  <div
                    className="text-sm font-bold font-mono mt-1"
                    style={{ color: hasCrisis ? "var(--red)" : "var(--green)" }}
                  >
                    {fmtCompact(pt.total_at_end)}
                  </div>
                  {isCurrent && !isSelected && (
                    <div className="text-[9px] uppercase tracking-wider text-[var(--text-secondary)] mt-1">
                      current
                    </div>
                  )}
                  {hasCrisis && (
                    <div className="text-[9px] text-[var(--red)] mt-1">
                      cash crisis
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function RetirementPage() {
  const isMobile = useIsMobile();
  const { data: fireNum, isLoading: loadingNum } = useFireNumber();
  const { data: readiness, isLoading: loadingReady } = useFireReadiness();
  const { data: timeline } = useFireTimeline();
  const { data: milestones } = useMilestones();
  const { data: bridge } = useBridgeStatus();
  const { data: sensitivity } = useSpendingSensitivity();
  const { data: incomeSources } = useIncomeSources();
  const { data: monteCarlo, isLoading: loadingMC } = useMonteCarlo(1000);
  const { data: retirementAges, isLoading: loadingRetirementAges } =
    useRetirementAgeAnalysis(1000, 42, 85);
  const todayIso = new Date().toISOString().slice(0, 10);
  const currentEmploymentSources = (incomeSources ?? []).filter(
    (source) =>
      ["salary", "bonus", "side_hustle"].includes(source.income_type)
      && (!source.start_date || source.start_date <= todayIso)
      && (!source.end_date || source.end_date >= todayIso),
  );
  const grossEmploymentIncome = currentEmploymentSources
    .reduce((total, source) => total + source.annual_amount, 0);
  const projectedEmploymentIncome = currentEmploymentSources
    .reduce((total, source) => total + source.projection_annual_amount, 0);

  // Spending sensitivity state — ephemeral, not persisted
  const [baseOverride, setBaseOverride] = useState<number | null>(null);
  const [debouncedOverride, setDebouncedOverride] = useState<number | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedOverride(baseOverride), 500);
    return () => clearTimeout(t);
  }, [baseOverride]);

  const spendingOverrideCents = debouncedOverride != null
    ? Math.round(debouncedOverride * 12 * 100)
    : undefined;

  const { data: wealthProjection, isLoading: loadingWealth } = useWealthProjection(spendingOverrideCents);
  const hasSeppPlan = (wealthProjection?.sepp_monthly ?? 0) > 0;

  // Wealth chart data, hoisted so the milestone markers can snap to the
  // chart's own x values (category axis requires exact matches — the old
  // Math.round(ev.age) only matched by luck).
  const wealthChartData = useMemo(
    () =>
      (wealthProjection?.points ?? []).map((p) => ({
        ...p,
        cash: Math.max(0, p.cash), // clamp for stacked areas
        taxable: Math.max(0, p.taxable ?? 0), // clamp for stacked areas
        roth: Math.max(0, p.roth ?? 0),
        annual_outflow: p.expenses * 12,
        annual_taxes: (p.modeled_taxes ?? 0) * 12,
      })),
    [wealthProjection],
  );

  const fanChartData = useMemo(() => {
    if (!monteCarlo?.percentile_curves) return [];
    return monteCarlo.percentile_curves.map((p) => ({
      age: Math.round(p.age),
      p10: p.p10,
      p25: p.p25,
      p50: p.p50,
      p75: p.p75,
      p90: p.p90,
      band_10_25: Math.max(0, p.p25 - p.p10),
      band_25_50: Math.max(0, p.p50 - p.p25),
      band_50_75: Math.max(0, p.p75 - p.p50),
      band_75_90: Math.max(0, p.p90 - p.p75),
    }));
  }, [monteCarlo]);

  const selectedRetirementPoint = useMemo(() => {
    const selectedAge = timeline?.moderate.retirement_age;
    if (selectedAge == null || fanChartData.length === 0) return null;
    return fanChartData.reduce((closest, point) =>
      Math.abs(point.age - selectedAge) < Math.abs(closest.age - selectedAge)
        ? point
        : closest,
    );
  }, [fanChartData, timeline]);

  const wealthMarkerGroups = useMemo<EventMarkerGroup[]>(() => {
    if (!wealthProjection || wealthChartData.length === 0) return [];
    const byX = new Map<number, EventMarkerGroup>();
    for (const ev of wealthProjection.events) {
      const pt =
        wealthChartData.find((p) => p.month === ev.month) ??
        // Fallback: nearest point by age (events should always carry an
        // in-range month, but never drop a milestone silently).
        wealthChartData.reduce((best, p) =>
          Math.abs(p.age - ev.age) < Math.abs(best.age - ev.age) ? p : best,
        );
      const existing = byX.get(pt.age);
      const detail = { label: ev.label, color: ev.color };
      if (existing) existing.events.push(detail);
      else byX.set(pt.age, { x: pt.age, y: pt.total, events: [detail] });
    }
    return [...byX.values()];
  }, [wealthProjection, wealthChartData]);

  const { markers: wealthMarkers, overlay: wealthOverlay, wrapperProps: wealthWrapperProps } =
    useEventMarkers(wealthMarkerGroups, {
      defaultColor: "var(--yellow)",
      xLabel: (age) => `Age ${Number(age).toFixed(1)}`,
    });

  const isLoading = loadingNum || loadingReady;

  if (isLoading || !fireNum || !readiness) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-96 text-[var(--text-secondary)]">
          Loading...
        </div>
      </Layout>
    );
  }

  const breakdown = fireNum.net_worth_breakdown;
  const accessibleNW = fireNum.accessible_net_worth;
  const accessiblePct = fireNum.accessible_progress_pct;
  const confidence90 = retirementAges?.confidence_ages.find(
    (point) => point.confidence === 90,
  );
  const selectedAge = timeline?.moderate.retirement_age;
  const selectedMedian = selectedRetirementPoint?.p50;
  const selectedGap = selectedMedian != null ? fireNum.fire_number - selectedMedian : null;
  const successColor =
    (monteCarlo?.success_rate ?? 0) >= 90
      ? "var(--green)"
      : (monteCarlo?.success_rate ?? 0) >= 75
        ? "var(--yellow)"
        : "var(--red)";

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-xl font-semibold text-[var(--text-primary)] tracking-tight">
              Retirement
            </h2>
            <p className="text-sm text-[var(--text-secondary)] mt-1">
              FIRE projections & lifetime planning &middot; all amounts in today&rsquo;s dollars
            </p>
          </div>
          <div className="flex items-center gap-2">
            <ScenarioSelector />
            <Link
              to="/settings"
              className="px-3 py-1.5 text-xs bg-[var(--bg-secondary)] border border-[var(--border)] rounded hover:border-[var(--blue)] transition-colors"
            >
              Configure
            </Link>
          </div>
        </div>

        {/* Calculated retirement readiness + selected what-if */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div
            className="bg-[var(--bg-card)] border rounded-lg p-6 text-center card-hero"
            style={{
              borderColor: confidence90?.earliest_age != null
                ? "rgba(0,212,170,0.3)"
                : "rgba(255,192,77,0.3)",
            }}
          >
            <p className="text-xs uppercase tracking-widest text-[var(--text-secondary)] mb-2">
              Earliest Modeled FIRE Age
            </p>
            {confidence90?.earliest_age != null ? (
              <div
                className="text-5xl font-bold font-mono tracking-tight mb-2 glow-green"
                style={{ color: "var(--green)" }}
              >
                Age {confidence90.earliest_age}
              </div>
            ) : loadingRetirementAges ? (
              <div className="text-2xl font-bold font-mono tracking-tight mb-2 text-[var(--text-secondary)]">
                Calculating…
              </div>
            ) : (
              <div className="text-3xl font-bold font-mono tracking-tight mb-2 text-[var(--yellow)]">
                After age {retirementAges?.max_tested_age ?? 85}
              </div>
            )}
            <p className="text-sm text-[var(--text-secondary)]">
              First whole-year age with at least 90% modeled success
            </p>
            <div className="grid grid-cols-3 gap-2 mt-5 pt-4 border-t border-[var(--border)]">
              {[80, 90, 95].map((confidence) => {
                const point = retirementAges?.confidence_ages.find(
                  (item) => item.confidence === confidence,
                );
                return (
                  <div key={confidence}>
                    <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                      {confidence}% success
                    </div>
                    <div className="text-lg font-mono font-bold text-[var(--text-primary)] mt-1">
                      {loadingRetirementAges
                        ? "…"
                        : point?.earliest_age != null
                          ? `Age ${point.earliest_age}`
                          : `>${retirementAges?.max_tested_age ?? 85}`}
                    </div>
                  </div>
                );
              })}
            </div>
            <p className="text-[10px] text-[var(--text-secondary)] mt-3">
              Calculated by rerunning the income, tax, spending, healthcare, and account-access
              plan at each candidate age; {retirementAges?.runs_per_age ?? 1000} paths per age.
            </p>
          </div>

          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6">
            <p className="text-xs uppercase tracking-widest text-[var(--text-secondary)] mb-2">
              Your Selected What-If
            </p>
            <div className="flex items-end justify-between gap-4 mb-5">
              <div>
                <div className="text-3xl font-bold font-mono text-[var(--text-primary)]">
                  {selectedAge != null ? `Retire at ${Math.round(selectedAge)}` : "No age selected"}
                </div>
                {timeline?.projected_retirement_date && (
                  <div className="text-xs text-[var(--text-secondary)] mt-1">
                    {new Date(timeline.projected_retirement_date).toLocaleDateString(
                      "en-US",
                      { month: "long", year: "numeric" },
                    )}
                  </div>
                )}
              </div>
              <div className="text-right">
                <div className="text-2xl font-bold font-mono" style={{ color: successColor }}>
                  {monteCarlo ? `${monteCarlo.success_rate}%` : "—"}
                </div>
                <div className="text-[10px] text-[var(--text-secondary)]">modeled success</div>
              </div>
            </div>
            <div className="space-y-2 pt-4 border-t border-[var(--border)]">
              <div className="flex justify-between text-sm">
                <span className="text-[var(--text-secondary)]">Median spendable assets at retirement</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {selectedMedian != null ? fmtCompact(selectedMedian) : "—"}
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[var(--text-secondary)]">Portfolio target</span>
                <span className="font-mono text-[var(--text-primary)]">{fmtCompact(fireNum.fire_number)}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[var(--text-secondary)]">Median gap at retirement</span>
                <span
                  className="font-mono"
                  style={{ color: (selectedGap ?? 0) <= 0 ? "var(--green)" : "var(--yellow)" }}
                >
                  {selectedGap != null ? fmtCompact(Math.max(0, selectedGap)) : "—"}
                </span>
              </div>
            </div>
            <p className="text-[10px] text-[var(--text-secondary)] mt-4">
              This is a scenario you selected, not the app&rsquo;s recommendation.
            </p>
          </div>
        </div>

        {/* Stat Cards — 5 columns with Cash Runway */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
          <StatCard
            label="Cash Runway"
            value={bridge?.cash_runway_months != null ? `${bridge.cash_runway_months}mo` : "—"}
            color={(bridge?.cash_runway_months ?? 0) > 12 ? "var(--green)" : (bridge?.cash_runway_months ?? 0) > 6 ? "var(--yellow)" : "var(--red)"}
            sub={bridge ? `if work stopped · ${fmtCompact(bridge.monthly_deficit)}/mo` : undefined}
          />
          <StatCard
            label="Portfolio Target"
            value={fmtCompact(fireNum.fire_number)}
            color="var(--text-primary)"
            sub={`${fireNum.safe_withdrawal_rate}% withdrawal rate · ${fmtCompact(fireNum.annual_spending)}/yr`}
          />
          <StatCard
            label="Accessible Net Worth"
            value={accessibleNW != null ? fmtCompact(accessibleNW) : fmtCompact(fireNum.current_net_worth)}
            color="var(--green)"
            sub={`${fmt(fireNum.current_net_worth)} total`}
          />
          <StatCard
            label="Gap"
            value={accessibleNW != null
              ? fmtCompact(fireNum.fire_number - accessibleNW)
              : fmtCompact(fireNum.gap)}
            color={
              (accessibleNW != null ? fireNum.fire_number - accessibleNW : fireNum.gap) <= 0
                ? "var(--green)"
                : "var(--yellow)"
            }
            sub="accessible vs FIRE number"
          />
          <StatCard
            label="Accessible Progress"
            value={`${accessiblePct ?? fireNum.progress_pct}%`}
            color={
              (accessiblePct ?? fireNum.progress_pct) >= 100
                ? "var(--green)"
                : (accessiblePct ?? fireNum.progress_pct) >= 50
                  ? "var(--yellow)"
                  : "var(--red)"
            }
            sub={`${fireNum.progress_pct}% gross`}
          />
        </div>

        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4 text-xs text-[var(--text-secondary)] space-y-1">
          <p>
            <span className="text-[var(--text-primary)] font-medium">How the target works:</span>{" "}
            {fmt(fireNum.annual_spending)} gross annual portfolio outflow ÷ {fireNum.safe_withdrawal_rate}%.
            This does not credit future Social Security or assume you spend the portfolio to zero.
          </p>
          {fireNum.taxes_included && (
            <p>
              Gross outflow includes {fmt(fireNum.base_annual_spending ?? 0)} after-tax spending,
              {" "}{fmt(fireNum.healthcare_annual)} pre-Medicare healthcare, and approximately{" "}
              {fmt(fireNum.estimated_annual_taxes)} of first-full-retirement-year withdrawal taxes.
            </p>
          )}
          <p>
            Current employment phase: {fmt(grossEmploymentIncome)} gross. The projection receives{" "}
            {fmt(projectedEmploymentIncome)}/year before any additional modeled tax outflow. RSU
            compensation is included once, and each dated income phase stops as configured or at
            retirement.
          </p>
          {fireNum.lifetime_spend_down_number != null && (
            <p>
              The lower spend-down estimate is {fmt(fireNum.lifetime_spend_down_number)}; it assumes
              Social Security, declining spending after 70, and principal depletion by life expectancy.
            </p>
          )}
          <p className="text-[var(--yellow)]">
            Withdrawal taxes are grossed up according to the projected source of funds. Actual taxes
            will vary with future law, deductions, cost basis, and the withdrawal sequence.
          </p>
        </div>

        {/* FIRE Progress Bar — Accessible */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs uppercase tracking-wider text-[var(--text-secondary)]">
              Accessible Progress to FIRE
            </span>
            <span className="text-xs font-mono text-[var(--text-secondary)]">
              {fmt(accessibleNW ?? fireNum.current_net_worth)} / {fmt(fireNum.fire_number)}
            </span>
          </div>
          <div className="relative h-3 rounded-full bg-[var(--bg-secondary)] overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-1000"
              style={{
                width: `${Math.min(100, accessiblePct ?? fireNum.progress_pct)}%`,
                background:
                  (accessiblePct ?? fireNum.progress_pct) >= 100
                    ? "var(--green)"
                    : `linear-gradient(90deg, var(--blue), var(--green))`,
              }}
            />
          </div>

          {/* Net Worth Breakdown */}
          {breakdown && (
            <div className="flex items-center gap-4 mt-3 pt-3 border-t border-[var(--border)]">
              {[
                { label: "Liquid", value: breakdown.liquid, color: "var(--green)" },
                { label: "Retirement", value: breakdown.retirement, color: "var(--blue)" },
                { label: "Real Estate", value: breakdown.real_estate_equity, color: "var(--yellow)" },
                { label: "Education / 529", value: breakdown.education ?? 0, color: "var(--orange, #b06830)" },
                { label: "Private / Illiquid", value: breakdown.illiquid_private, color: "#a855f7" },
                { label: "Other", value: breakdown.other, color: "#6b7280" },
              ]
                .filter((b) => Math.abs(b.value) >= 100)
                .map((b) => (
                  <div key={b.label} className="flex items-center gap-1.5">
                    <div
                      className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: b.color }}
                    />
                    <span className="text-[10px] text-[var(--text-secondary)]">
                      {b.label}
                    </span>
                    <span className="text-[10px] font-mono font-medium" style={{ color: b.color }}>
                      {fmtCompact(b.value)}
                    </span>
                  </div>
                ))}
            </div>
          )}
        </div>

        {/* Spending Sensitivity */}
        <SpendingSensitivityCard
          baseOverride={baseOverride}
          onBaseChange={setBaseOverride}
          sensitivity={sensitivity}
        />

        {/* Wealth Projection — Full Width */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                Wealth Projection
              </h3>
              {wealthProjection && (
                <div className="flex items-center gap-2">
                  <span
                    className={`text-xl font-bold font-mono ${wealthProjection.total_at_end > 0 ? "glow-green" : "glow-red"}`}
                    style={{ color: wealthProjection.total_at_end > 0 ? "var(--green)" : "var(--red)" }}
                  >
                    {fmtCompact(wealthProjection.total_at_end)}
                  </span>
                  <span className="text-sm text-[var(--text-secondary)]">at 82</span>
                </div>
              )}
            </div>
            <p className="text-[11px] text-[var(--text-secondary)] mb-3">
              Employment income stops automatically at your retirement date. Total wealth includes
              home equity and can rise while cash or brokerage balances are being spent whenever
              investment growth and mortgage paydown exceed withdrawals. The red outflow line includes
              living costs and modeled taxes; the orange line isolates those taxes.
            </p>
            {wealthProjection && wealthChartData.length > 0 ? (
              <>
                <div {...wealthWrapperProps}>
                <ResponsiveContainer width="100%" height={400}>
                  <ComposedChart
                    data={wealthChartData}
                    margin={{ top: 32, right: isMobile ? 8 : 60, left: isMobile ? 0 : 10, bottom: 0 }}
                  >
                    <defs>
                      <linearGradient id="gradIraB" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#3d6e9e" stopOpacity={0.9} />
                        <stop offset="100%" stopColor="#3d6e9e" stopOpacity={0.5} />
                      </linearGradient>
                      <linearGradient id="gradIraA" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#7a6aaa" stopOpacity={0.9} />
                        <stop offset="100%" stopColor="#7a6aaa" stopOpacity={0.5} />
                      </linearGradient>
                      <linearGradient id="gradCash" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#2e8b6e" stopOpacity={0.9} />
                        <stop offset="100%" stopColor="#2e8b6e" stopOpacity={0.5} />
                      </linearGradient>
                      <linearGradient id="gradRE" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#a07d1a" stopOpacity={0.85} />
                        <stop offset="100%" stopColor="#a07d1a" stopOpacity={0.4} />
                      </linearGradient>
                      <linearGradient id="gradIlliquid" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#b06830" stopOpacity={0.85} />
                        <stop offset="100%" stopColor="#b06830" stopOpacity={0.4} />
                      </linearGradient>
                      <linearGradient id="gradRRSP" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#9e4a7a" stopOpacity={0.85} />
                        <stop offset="100%" stopColor="#9e4a7a" stopOpacity={0.4} />
                      </linearGradient>
                      <linearGradient id="gradTaxable" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#2aa6b8" stopOpacity={0.9} />
                        <stop offset="100%" stopColor="#2aa6b8" stopOpacity={0.5} />
                      </linearGradient>
                      <linearGradient id="gradRoth" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#5b8c3a" stopOpacity={0.9} />
                        <stop offset="100%" stopColor="#5b8c3a" stopOpacity={0.5} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(42,42,62,0.5)" />
                    <XAxis
                      dataKey="age"
                      stroke="#5c5c6a"
                      tick={{ fontSize: 11, fill: "#5c5c6a" }}
                      tickFormatter={(v) => `${Math.round(v)}`}
                    />
                    <YAxis
                      stroke="#5c5c6a"
                      tick={{ fontSize: 11, fill: "#5c5c6a" }}
                      tickFormatter={fmtAxis}
                      width={isMobile ? 48 : 65}
                    />
                    <Tooltip
                      {...TOOLTIP_STYLE}
                      labelFormatter={(age) => `Age ${age}`}
                      formatter={(value, name) => {
                        const labels: Record<string, string> = {
                          real_estate: "Real Estate",
                          illiquid: "Private Venture",
                          ira_growth: "Traditional retirement",
                          rrsp: "RRSP/RRIF",
                          ira_sepp: "SEPP IRA (optional)",
                          cash: "Cash (bridge)",
                          taxable: "Taxable brokerage",
                          roth: "Roth (tax-free)",
                          total: "Total",
                          annual_outflow: "Total portfolio outflow",
                          annual_taxes: "Modeled taxes",
                        };
                        return [fmtCompact(Number(value)), labels[String(name)] || String(name)];
                      }}
                    />

                    <ReferenceLine y={0} stroke="var(--red)" strokeDasharray="4 4" strokeOpacity={0.6} />

                    {/* Milestone markers */}
                    {wealthMarkers}

                    {/* Stacked areas: IRA-B + Taxable + Roth (bottom, stable), IRA-A, RRSP, Cash, Illiquid, RE (top, has discontinuities) */}
                    <Area type="monotone" dataKey="ira_growth" stackId="wealth" stroke="var(--blue)" strokeWidth={0} fill="url(#gradIraB)" />
                    <Area type="monotone" dataKey="taxable" stackId="wealth" stroke="#2aa6b8" strokeWidth={0} fill="url(#gradTaxable)" />
                    <Area type="monotone" dataKey="roth" stackId="wealth" stroke="#5b8c3a" strokeWidth={0} fill="url(#gradRoth)" />
                    {hasSeppPlan && <Area type="monotone" dataKey="ira_sepp" stackId="wealth" stroke="var(--purple, #7a6aaa)" strokeWidth={0} fill="url(#gradIraA)" />}
                    <Area type="monotone" dataKey="rrsp" stackId="wealth" stroke="var(--pink, #9e4a7a)" strokeWidth={0} fill="url(#gradRRSP)" />
                    <Area type="monotone" dataKey="illiquid" stackId="wealth" stroke="var(--orange, #b06830)" strokeWidth={0} fill="url(#gradIlliquid)" />
                    <Area type="monotone" dataKey="real_estate" stackId="wealth" stroke="var(--yellow)" strokeWidth={0} fill="url(#gradRE)" />
                    <Area type="monotone" dataKey="cash" stackId="wealth" stroke="var(--green)" strokeWidth={0} fill="url(#gradCash)" />

                    {/* Total line on top */}
                    <Line type="monotone" dataKey="total" stroke="#1a1a1e" strokeWidth={2} dot={false} strokeOpacity={0.8} />

                    {/* Annual portfolio outflow and its withdrawal-tax component (right axis) */}
                    <YAxis
                      yAxisId="spending"
                      orientation="right"
                      stroke="var(--red)"
                      tick={{ fontSize: 10, fill: "var(--red)" }}
                      tickFormatter={fmtAxis}
                      width={55}
                      domain={[0, 200000]}
                      hide={isMobile}
                    />
                    <Line yAxisId="spending" type="monotone" dataKey="annual_outflow" stroke="var(--red)" strokeWidth={1.5} strokeDasharray="6 3" dot={false} strokeOpacity={0.7} />
                    <Line yAxisId="spending" type="monotone" dataKey="annual_taxes" stroke="var(--orange, #b06830)" strokeWidth={1.25} strokeDasharray="2 3" dot={false} strokeOpacity={0.85} />
                  </ComposedChart>
                </ResponsiveContainer>
                {wealthOverlay}
                </div>

                {/* Legend + summary row */}
                <div className="flex items-center justify-between mt-4 pt-4 border-t border-[var(--border)]">
                  <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                    {[
                      { label: "Real Estate", color: "var(--yellow)" },
                      { label: "Private Venture", color: "var(--orange, #b06830)" },
                      { label: "Cash (bridge)", color: "var(--green)" },
                      { label: "RRSP/RRIF", color: "var(--pink, #9e4a7a)" },
                      ...(hasSeppPlan ? [{ label: "SEPP IRA", color: "var(--purple, #7a6aaa)" }] : []),
                      { label: "Traditional retirement", color: "var(--blue)" },
                      { label: "Taxable", color: "#2aa6b8" },
                      { label: "Roth", color: "#5b8c3a" },
                      { label: "Total", color: "#1a1a1e", dashed: true },
                      { label: "Total outflow/yr", color: "var(--red)", dashed: true },
                      { label: "Modeled taxes/yr", color: "var(--orange, #b06830)", dashed: true },
                    ].map((l) => (
                      <div key={l.label} className="flex items-center gap-1.5">
                        <div
                          className="w-3 h-[3px] rounded-full"
                          style={{
                            backgroundColor: l.color,
                            opacity: l.dashed ? 0.8 : 0.6,
                            borderBottom: l.dashed ? `1px dashed ${l.color}` : undefined,
                          }}
                        />
                        <span className="text-[10px] text-[var(--text-secondary)]">{l.label}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            ) : (
              <div className="flex items-center justify-center h-[400px] text-[var(--text-secondary)] text-sm">
                {loadingWealth ? "Computing wealth projection..." : "Configure FIRE settings to see the projection."}
              </div>
            )}
          </div>

        {/* Pool-aware Monte Carlo */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
          <div className="flex items-start justify-between gap-4 mb-2">
            <div>
              <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                Retirement Monte Carlo
              </h3>
              <p className="text-[11px] text-[var(--text-secondary)] mt-1 max-w-3xl">
                Success means cash, taxable investments, and age-accessible retirement accounts
                fund every modeled year. Home equity, 529s, private investments, and speculative
                assets are excluded unless a dated sale or vest makes them spendable.
              </p>
            </div>
            {monteCarlo && (
              <div className="text-right shrink-0">
                <div className="text-xl font-bold font-mono" style={{ color: successColor }}>
                  {monteCarlo.success_rate}%
                </div>
                <div className="text-[10px] text-[var(--text-secondary)]">
                  of {monteCarlo.total_runs.toLocaleString()} simulations
                </div>
              </div>
            )}
          </div>

          {fanChartData.length > 0 ? (
            <>
              <ResponsiveContainer width="100%" height={360}>
                <ComposedChart data={fanChartData} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(42,42,62,0.5)" />
                  <XAxis dataKey="age" stroke="#5c5c6a" tick={{ fontSize: 11, fill: "#5c5c6a" }} />
                  <YAxis stroke="#5c5c6a" tick={{ fontSize: 11, fill: "#5c5c6a" }} tickFormatter={fmtAxis} width={65} />
                  <Tooltip
                    {...TOOLTIP_STYLE}
                    labelFormatter={(age) => `Age ${age}`}
                    formatter={(value, name) => {
                      const labels: Record<string, string> = {
                        p10: "10th percentile",
                        p50: "Median",
                        p90: "90th percentile",
                      };
                      return [fmtCompact(Number(value)), labels[String(name)] || String(name)];
                    }}
                  />
                  <ReferenceLine y={0} stroke="var(--red)" strokeDasharray="4 4" strokeOpacity={0.6} />
                  <Line type="monotone" dataKey="p10" stroke="var(--red)" strokeWidth={1.5} dot={false} />
                  <Line type="monotone" dataKey="p50" stroke="var(--green)" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="p90" stroke="var(--blue)" strokeWidth={1.5} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
              {monteCarlo && (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-4 pt-4 border-t border-[var(--border)]">
                    {[
                      { label: "Starting spendable", value: monteCarlo.starting_spendable_assets, color: "var(--text-primary)" },
                      { label: "Excluded net assets", value: monteCarlo.excluded_non_spendable_assets, color: "var(--text-secondary)" },
                      { label: "10th percentile", value: monteCarlo.percentile_10, color: "var(--red)" },
                      { label: "Median ending", value: monteCarlo.percentile_50, color: "var(--green)" },
                      { label: "90th percentile", value: monteCarlo.percentile_90, color: "var(--blue)" },
                    ].map((item) => (
                      <div key={item.label} className="text-center">
                        <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">{item.label}</span>
                        <div className="text-sm font-mono font-bold mt-0.5" style={{ color: item.color }}>
                          {fmtCompact(item.value)}
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="text-[10px] text-[var(--text-secondary)] mt-3">
                    The configured return is the long-run arithmetic mean, not the median outcome.
                    A fixed set of random paths is reused so settings comparisons do not bounce
                    around. Projected federal and state taxes are included, using the expected-path
                    withdrawal schedule rather than recalculating taxes inside every random path.
                  </p>
                </>
              )}
            </>
          ) : (
            <div className="flex items-center justify-center h-[360px] text-[var(--text-secondary)] text-sm">
              {loadingMC ? "Running retirement simulations..." : "Configure FIRE settings to run Monte Carlo."}
            </div>
          )}
        </div>

        {/* Milestone Timeline */}
        {milestones && milestones.milestones.length > 0 && (
          <MilestoneTimeline
            milestones={milestones.milestones}
            currentAge={milestones.current_age}
          />
        )}

        {/* Bridge Status — Two Column */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Monthly Cash Flow */}
          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">Monthly Cash Flow</h3>
            {bridge ? (
              <div className="space-y-2.5">
                {bridge.income_streams.map((s) => (
                  <div key={s.label} className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: s.color }} />
                      <span className="text-[11px] text-[var(--text-secondary)]">{s.label}</span>
                    </div>
                    <span className="text-[11px] font-mono" style={{ color: s.color }}>
                      +${s.monthly.toLocaleString()}
                    </span>
                  </div>
                ))}
                <div className="flex items-center justify-between pt-1.5 border-t border-[var(--border)]">
                  <span className="text-[11px] font-medium text-[var(--text-primary)]">Income</span>
                  <span className="text-[11px] font-mono text-[var(--green)]">
                    +${(bridge.monthly_income_total + bridge.monthly_ira_total).toLocaleString()}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-medium text-[var(--text-primary)]">Expenses</span>
                  <span className="text-[11px] font-mono text-[var(--red)]">
                    -${bridge.monthly_burn.toLocaleString()}
                  </span>
                </div>
                <div className="flex items-center justify-between pt-1.5 border-t border-[var(--border)]">
                  <span className="text-[11px] font-bold text-[var(--text-primary)]">Monthly gap</span>
                  <span className="text-[11px] font-mono font-bold" style={{ color: bridge.monthly_deficit > 0 ? "var(--red)" : "var(--green)" }}>
                    {bridge.monthly_deficit > 0 ? "-" : "+"}${Math.abs(bridge.monthly_deficit).toLocaleString()}
                  </span>
                </div>
                <div className="mt-2 pt-2 border-t border-[var(--border)]">
                  <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">After primary property sale</span>
                  <div className="flex items-center justify-between mt-1">
                    <span className="text-[11px] text-[var(--text-secondary)]">Gap drops to</span>
                    <span className="text-[11px] font-mono font-bold" style={{ color: bridge.post_sale_deficit > 0 ? "var(--yellow)" : "var(--green)" }}>
                      {bridge.post_sale_deficit > 0 ? "-" : "+"}${Math.abs(bridge.post_sale_deficit).toLocaleString()}/mo
                    </span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-xs text-[var(--text-secondary)]">Loading...</div>
            )}
          </div>

          {/* Upcoming Events */}
          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">Upcoming Events</h3>
            {bridge?.upcoming_events.length ? (
              <div className="space-y-2">
                {bridge.upcoming_events.map((ev) => (
                  <div key={ev.name} className="flex items-center justify-between">
                    <div className="flex flex-col">
                      <span className="text-[11px] text-[var(--text-primary)]">{ev.name}</span>
                      <span className="text-[10px] text-[var(--text-secondary)]">
                        {new Date(ev.date).toLocaleDateString("en-US", { month: "short", year: "numeric" })}
                      </span>
                    </div>
                    <span className="text-[11px] font-mono font-medium" style={{ color: ev.amount >= 0 ? "var(--green)" : "var(--red)" }}>
                      {ev.amount >= 0 ? "+" : ""}{fmtCompact(ev.amount)}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-[var(--text-secondary)]">No upcoming events</div>
            )}
          </div>
        </div>
      </div>
    </Layout>
  );
}
