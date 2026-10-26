import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import {
  useBridgeStatus,
  useFireNumber,
  useFireReadiness,
  useMilestones,
  useRetirementProjection,
  useScenarios,
  useSpendingSensitivity,
} from "../api/queries";
import Layout from "../components/Layout";
import { formatCurrency as fmt, fmtCompact } from "../utils/formatting";
import type { Milestone, SpendingSensitivity } from "../types/fire";
import { fmtMonthYear } from "../utils/dates";
import "../components/retirement/retirement.css";
import { bridgeAnswer, holdingsAnswer, sourcesAnswer, toPlan, verdictWords } from "../components/retirement/model";
import Verdict from "../components/retirement/Verdict";
import BeforeYouRely from "../components/retirement/BeforeYouRely";
import BridgeCashChart from "../components/retirement/BridgeCashChart";
import MoneySourcesChart from "../components/retirement/MoneySourcesChart";
import HoldingsChart from "../components/retirement/HoldingsChart";
import HowItWorks from "../components/retirement/HowItWorks";
import { ComparePicker, ScenarioSelector } from "../components/retirement/PlanControls";
import { money } from "../components/retirement/format";

// Retirement (Oct 2026 redesign): answer first, one question per chart, every
// sentence computed from the projection. The cards that predate the redesign
// sit under "More detail", unchanged.

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
                    {fmtMonthYear(m.date)}
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
  const isModified = baseOverride != null;
  // property_sales configs: per-property lines, some possibly carried ON TOP of
  // the budget while held (in_base_burn: false) — the modeled monthly total
  // includes them. Legacy configs keep the fixed three-bucket breakdown.
  const lines = b?.properties ?? [];
  const inBudget = lines.filter((l) => l.in_budget);
  const onTop = lines.filter((l) => !l.in_budget);
  const outside = b?.outside_budget_monthly ?? 0;
  const total = displayBase + healthcare + outside;

  // Recompute non-housing when user adjusts total budget
  const nonHousing = !b
    ? 0
    : lines.length
      ? displayBase - inBudget.reduce((sum, l) => sum + l.monthly_cost, 0)
      : displayBase - b.primary_property_all_in - b.income_property_cost - b.secondary_property_cost;

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
            {outside > 0 && ` + $${outside.toLocaleString()} unsold-property carry`}
          </span>
          <span className="text-xs font-mono font-bold ml-auto" style={{ color: isModified ? "var(--blue)" : "var(--text-primary)" }}>
            = ${total.toLocaleString()}/mo
          </span>
        </div>
      </div>

      {/* Breakdown (property_sales): each property as the engine treats it */}
      {b && lines.length > 0 && (
        <div className="mb-4 py-3 border-t border-[var(--border)]">
          <div className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2">
            What's in the ${displayBase.toLocaleString()}
          </div>
          <div className="space-y-1.5 text-xs">
            {inBudget.map((l) => (
              <div key={l.label}>
                <div className="flex justify-between">
                  <span className="text-[var(--text-secondary)]">{l.label} housing</span>
                  <span className="font-mono text-[var(--text-primary)]">${l.monthly_cost.toLocaleString()}</span>
                </div>
                {l.mortgage_pi > 0 && (
                  <div className="flex justify-between pl-3">
                    <span className="text-[var(--text-secondary)] opacity-60">P&I ${l.mortgage_pi.toLocaleString()} + other ${(l.monthly_cost - l.mortgage_pi).toLocaleString()}</span>
                  </div>
                )}
              </div>
            ))}
            <div className="flex justify-between pt-1 border-t border-[var(--border)]/50">
              <span className="text-[var(--text-secondary)] font-medium">Non-housing living</span>
              <span className="font-mono font-bold" style={{ color: nonHousing < 2000 ? "var(--red)" : "var(--text-primary)" }}>
                ${nonHousing.toLocaleString()}
              </span>
            </div>
          </div>
          {onTop.length > 0 && (
            <>
              <div className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mt-3 mb-2">
                On top of the budget{onTop.every((l) => l.held) ? "" : " until sold"}
              </div>
              <div className="space-y-1.5 text-xs">
                {onTop.map((l) => (
                  <div key={l.label} className="flex justify-between">
                    <span className="text-[var(--text-secondary)]">{l.label} carrying cost</span>
                    <span className="font-mono text-[var(--text-primary)]">+${l.monthly_cost.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </>
          )}
          <div className="mt-3 text-[10px] text-[var(--text-secondary)] opacity-60 leading-relaxed">
            {lines.map((l) => (
              <span key={l.label}>
                {l.held ? (
                  <>{l.label}: held (no sale planned).{" "}</>
                ) : (
                  <>
                    After {l.label} sells: −${l.monthly_cost.toLocaleString()}
                    {l.post_sale_rent > 0 && ` +$${l.post_sale_rent.toLocaleString()} rent`}.{" "}
                  </>
                )}
              </span>
            ))}
            Healthcare ${healthcare.toLocaleString()}/mo added pre-65, drops at Medicare.
            Spending phases: 85% at 70, 75% at 80.
          </div>
        </div>
      )}

      {/* Breakdown (legacy keys): what's inside the budget */}
      {b && lines.length === 0 && (
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
              <span className="text-[var(--text-secondary)] opacity-60">P&I ${b.primary_property_pi.toLocaleString()} + other ${(b.primary_property_all_in - b.primary_property_pi).toLocaleString()}</span>
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

const COMPARE_KEY = "rt.compare";
const readCompare = () => { try { return localStorage.getItem(COMPARE_KEY) ?? ""; } catch { return ""; } };
const writeCompare = (v: string) => { try { localStorage.setItem(COMPARE_KEY, v); } catch { /* private mode */ } };

function Skeleton() {
  return (
    <div aria-busy="true" aria-label="Loading your projection">
      <div className="rt-skel" style={{ height: 44, width: "56%", marginTop: 44 }} />
      <div className="rt-skel" style={{ height: 22, width: "72%", marginTop: 16 }} />
      <div className="rt-skel" style={{ height: 96, marginTop: 34 }} />
      <div className="rt-skel" style={{ height: 380, marginTop: 64 }} />
    </div>
  );
}

export default function RetirementPage() {
  const { data: fireNum } = useFireNumber();
  const { data: readiness } = useFireReadiness();
  const { data: milestones } = useMilestones();
  const { data: bridge } = useBridgeStatus();
  const { data: sensitivity } = useSpendingSensitivity();
  const { data: scenarios } = useScenarios();

  // Spending what-if from the card under "More detail" — ephemeral, never saved.
  const [baseOverride, setBaseOverride] = useState<number | null>(null);
  const [debouncedOverride, setDebouncedOverride] = useState<number | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setDebouncedOverride(baseOverride), 500);
    return () => clearTimeout(t);
  }, [baseOverride]);
  const spendingOverrideCents = debouncedOverride != null ? Math.round(debouncedOverride * 12 * 100) : undefined;

  const { data: projection, isLoading } = useRetirementProjection("active", spendingOverrideCents);

  // Compare with: another plan's line on the holdings chart (remembered per browser).
  const [compareId, setCompareId] = useState<string>(readCompare);
  const active = scenarios?.find((s) => s.is_active);
  const compareValid = compareId !== "" && !!scenarios &&
    (compareId === "base" ? !!active : scenarios.some((s) => s.id === compareId && !s.is_active));
  const compareName = !compareValid ? "" : compareId === "base" ? "Base plan" : scenarios!.find((s) => s.id === compareId)!.name;
  const { data: compareProjection } = useRetirementProjection(compareValid ? compareId : "active", undefined, compareValid);
  const pickCompare = (v: string) => { setCompareId(v); writeCompare(v); };

  const plan = useMemo(
    () => (projection && projection.points.length ? toPlan(active?.name ?? "Base plan", projection) : null),
    [projection, active?.name],
  );
  const compare = useMemo(
    () => (compareValid && compareProjection && compareProjection.points.length ? toPlan(compareName, compareProjection) : null),
    [compareValid, compareProjection, compareName],
  );

  // One time cursor shared by the three charts. It opens on the first moment
  // that moves money (a sale or one-time money) in the next five years.
  const [cursor, setCursor] = useState<number | null>(null);
  const firstMoment = plan?.milestones.find((x) => (x.kind === "sale" || x.kind === "money") && x.month < 60)?.month ?? 0;
  const at = cursor ?? firstMoment;

  const words = useMemo(() => (plan ? verdictWords(plan, compare) : null), [plan, compare]);
  const bridgeA = useMemo(() => (plan ? bridgeAnswer(plan) : null), [plan]);

  const breakdown = fireNum?.net_worth_breakdown;
  const accessibleNW = fireNum?.accessible_net_worth;
  const accessiblePct = fireNum?.accessible_progress_pct;
  const bridgeProjection = projection; // Cash Runway reads the same run's cash_zero_month

  return (
    <Layout>
      <div className="rt">
        <div className="rt-top">
          <p className="rt-crumb"><b>Retirement</b>, in today&rsquo;s dollars</p>
          <div className="rt-controls">
            {scenarios && <ScenarioSelector scenarios={scenarios} />}
            {scenarios && <ComparePicker scenarios={scenarios} value={compareValid ? compareId : ""} onChange={pickCompare} />}
          </div>
        </div>

        {debouncedOverride != null && (
          <div className="rt-note" role="status">
            <span>Showing this plan at <b className="num">{money(debouncedOverride)}</b> a month of spending, not your saved number.</span>
            <button type="button" onClick={() => setBaseOverride(null)}>Back to the saved plan</button>
          </div>
        )}

        {isLoading && <Skeleton />}

        {!isLoading && !plan && (
          <header className="rt-verdict">
            <h1>Add your date of birth to see your plan.</h1>
            <p>The projection runs month by month from today to {projection?.end_age ?? 82}, so it needs your age. <Link to="/settings" style={{ color: "var(--rt-navy)" }}>Open Configure</Link></p>
          </header>
        )}

        {plan && words && bridgeA && (
          <>
            <Verdict words={words} />
            <BeforeYouRely />

            <section className="rt-q" aria-labelledby="q-bridge">
              <h2 id="q-bridge">Does cash hold through the next five years?</h2>
              <p className={"answer" + (bridgeA.bad ? " bad" : "")}>{bridgeA.text}</p>
              <BridgeCashChart plan={plan} cursor={at} setCursor={setCursor} />
            </section>

            <section className="rt-q" aria-labelledby="q-sources">
              <h2 id="q-sources">Where does the money come from each year?</h2>
              <p className="answer">{sourcesAnswer(plan)}</p>
              <MoneySourcesChart plan={plan} cursor={at} setCursor={setCursor} />
            </section>

            <section className="rt-q" aria-labelledby="q-own">
              <h2 id="q-own">What do you own along the way?</h2>
              <p className="answer">{holdingsAnswer(plan)}</p>
              <HoldingsChart plan={plan} compare={compare} cursor={at} setCursor={setCursor} />
            </section>

            <HowItWorks items={projection?.assumptions ?? []} />
          </>
        )}

        <section className="rt-more" aria-labelledby="rt-more-h">
          <h2 id="rt-more-h">More detail</h2>
          <p>The cards from before the redesign, unchanged. Editing the monthly budget below reruns everything above.</p>
          <div className="space-y-6">
            <SpendingSensitivityCard
              baseOverride={baseOverride}
              onBaseChange={setBaseOverride}
              sensitivity={sensitivity}
            />

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
                      <span className="text-[11px] font-bold text-[var(--text-primary)]">
                        Monthly gap
                        {bridge.income_streams.some((st) => st.label.endsWith("(temp)")) && (
                          <span className="font-normal text-[var(--text-secondary)]"> (excl. temp income)</span>
                        )}
                      </span>
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
                            {fmtMonthYear(ev.date)}
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

            {milestones && milestones.milestones.length > 0 && (
              <MilestoneTimeline milestones={milestones.milestones} currentAge={milestones.current_age} />
            )}

            {fireNum && readiness && (
              <>
                {/* Stat Cards — 5 columns with Cash Runway */}
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
                  {/* Cash Runway = the projection's own cash-zero month (the Bridge chart
                      below, and the Runway page's method): dated events, sales and pool
                      draws included. The old flat "cash ÷ today's deficit" ignored every
                      one of them and disagreed with both. */}
                  <StatCard
                    label="Cash Runway"
                    value={
                      !bridgeProjection
                        ? "—"
                        : bridgeProjection.cash_zero_month == null
                          ? "Never"
                          : `${bridgeProjection.cash_zero_month}mo`
                    }
                    color={
                      bridgeProjection?.cash_zero_month == null
                        ? "var(--green)"
                        : bridgeProjection.cash_zero_month > 12
                          ? "var(--green)"
                          : bridgeProjection.cash_zero_month > 6
                            ? "var(--yellow)"
                            : "var(--red)"
                    }
                    sub={bridge ? `projected · gap now ${fmtCompact(bridge.monthly_deficit)}/mo` : undefined}
                  />
                  <StatCard
                    label="FIRE Number"
                    value={fmtCompact(fireNum.fire_number)}
                    color="var(--text-primary)"
                    sub={`${fireNum.safe_withdrawal_rate}% SWR`}
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
                        { label: "Private Venture", value: breakdown.illiquid_private, color: "var(--orange, #b06830)" },
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

              </>
            )}
          </div>
        </section>
      </div>
    </Layout>
  );
}
