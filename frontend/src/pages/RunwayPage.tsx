import { useState, useMemo, useEffect, useRef } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  BarChart,
  Bar,
} from "recharts";
import Layout from "../components/Layout";
import {
  useRunway,
  useCashflowEvents,
  useCreateCashflowEvent,
  useDeleteCashflowEvent,
  useUpdateCashflowEvent,
  useFireConfig,
} from "../api/queries";
import { formatCurrency, fmtAxis } from "../utils/formatting";
import { TOOLTIP_STYLE } from "../utils/theme";
import { useEventMarkers } from "../components/charts/EventMarkers";
import type { EventMarkerGroup } from "../components/charts/EventMarkers";
import type { CashflowEvent, CashflowEventCreate } from "../types/cashflow";

/* ------------------------------------------------------------------ */
/*  Stat Card                                                          */
/* ------------------------------------------------------------------ */
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

/* ------------------------------------------------------------------ */
/*  Event Modal — handles both create and edit. Pass `initial` to edit. */
/* ------------------------------------------------------------------ */
function EventModal({
  initial,
  onClose,
  onSubmit,
}: {
  initial?: CashflowEvent;
  onClose: () => void;
  onSubmit: (data: CashflowEventCreate) => void;
}) {
  const isEdit = !!initial;
  const [form, setForm] = useState<CashflowEventCreate>(
    initial
      ? {
          name: initial.name,
          event_type: initial.event_type,
          amount: initial.amount,
          date: initial.date,
          is_recurring: initial.is_recurring,
          recurrence: initial.recurrence,
          end_date: initial.end_date,
          probability: initial.probability,
          status: initial.status,
          category: initial.category,
          notes: initial.notes,
        }
      : {
          name: "",
          event_type: "expense",
          amount: 0,
          date: new Date().toISOString().slice(0, 10),
          is_recurring: false,
          recurrence: null,
          end_date: null,
          probability: 1.0,
          status: "planned",
          category: null,
          notes: null,
        },
  );

  // Probability is edited as raw text and parsed on save. Coercing on every
  // keystroke (`parseFloat(v) || 1`) snapped a blank field and a typed "0"
  // back to 1, so no value starting with 0 could ever be entered.
  const [probabilityDraft, setProbabilityDraft] = useState<string>(
    String(form.probability),
  );
  const submit = () => {
    const p = parseFloat(probabilityDraft);
    const probability = isNaN(p) ? 1 : Math.min(1, Math.max(0, p));
    onSubmit({ ...form, probability });
  };

  const inputCls =
    "w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus:border-[var(--blue)] transition-colors";
  const labelCls =
    "block text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6 w-full max-w-md space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">
          {isEdit ? "Edit Cash Flow Event" : "Add Cash Flow Event"}
        </h3>

        <div>
          <label className={labelCls}>Name</label>
          <input
            className={inputCls}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="e.g., Roof Replacement"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>Type</label>
            <select
              className={inputCls}
              value={form.event_type}
              onChange={(e) =>
                setForm({
                  ...form,
                  event_type: e.target.value as "income" | "expense",
                })
              }
            >
              <option value="expense">Expense</option>
              <option value="income">Income</option>
            </select>
          </div>
          <div>
            <label className={labelCls}>Amount ($)</label>
            <input
              type="number"
              className={inputCls}
              value={form.amount || ""}
              onChange={(e) =>
                setForm({ ...form, amount: parseFloat(e.target.value) || 0 })
              }
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>Date</label>
            <input
              type="date"
              className={inputCls}
              value={form.date}
              onChange={(e) => setForm({ ...form, date: e.target.value })}
            />
          </div>
          <div>
            <label className={labelCls}>Status</label>
            <select
              className={inputCls}
              value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value })}
            >
              <option value="planned">Planned</option>
              <option value="confirmed">Confirmed</option>
            </select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>Recurring?</label>
            <select
              className={inputCls}
              value={form.is_recurring ? "yes" : "no"}
              onChange={(e) =>
                setForm({
                  ...form,
                  is_recurring: e.target.value === "yes",
                  recurrence:
                    e.target.value === "yes" ? "monthly" : null,
                })
              }
            >
              <option value="no">One-time</option>
              <option value="yes">Recurring</option>
            </select>
          </div>
          {form.is_recurring && (
            <div>
              <label className={labelCls}>Frequency</label>
              <select
                className={inputCls}
                value={form.recurrence || "monthly"}
                onChange={(e) =>
                  setForm({ ...form, recurrence: e.target.value })
                }
              >
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="annual">Annual</option>
              </select>
            </div>
          )}
        </div>

        {form.is_recurring && (
          <div>
            <label className={labelCls}>End Date</label>
            <input
              type="date"
              className={inputCls}
              value={form.end_date || ""}
              onChange={(e) =>
                setForm({ ...form, end_date: e.target.value || null })
              }
            />
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>Probability (0-1)</label>
            <input
              type="number"
              step="0.1"
              min="0"
              max="1"
              className={inputCls}
              value={probabilityDraft}
              onChange={(e) => setProbabilityDraft(e.target.value)}
              placeholder="1.0"
            />
          </div>
          <div>
            <label className={labelCls}>Category</label>
            <input
              className={inputCls}
              value={form.category || ""}
              onChange={(e) =>
                setForm({ ...form, category: e.target.value || null })
              }
              placeholder="Optional"
            />
          </div>
        </div>

        <div>
          <label className={labelCls}>Notes</label>
          <input
            className={inputCls}
            value={form.notes || ""}
            onChange={(e) =>
              setForm({ ...form, notes: e.target.value || null })
            }
            placeholder="Optional"
          />
        </div>

        <div className="flex gap-3 pt-2">
          <button
            className="flex-1 px-4 py-2 text-sm rounded bg-[var(--green)] text-[#0a0a0f] font-medium hover:opacity-90 transition-opacity"
            onClick={submit}
          >
            {isEdit ? "Save Changes" : "Add Event"}
          </button>
          <button
            className="flex-1 px-4 py-2 text-sm rounded border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--text-secondary)] transition-colors"
            onClick={onClose}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Events List — splits into upcoming + past, dims the past block.    */
/* ------------------------------------------------------------------ */
function isEventRealized(evt: CashflowEvent, today: string): boolean {
  // Recurring events count as past only when their entire run has ended.
  // (No end_date = ongoing → never realized.)
  if (evt.is_recurring) {
    return !!evt.end_date && evt.end_date < today;
  }
  return evt.date < today;
}

function EventRow({
  evt,
  realized,
  onEdit,
  onDelete,
}: {
  evt: CashflowEvent;
  realized: boolean;
  onEdit: (evt: CashflowEvent) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div
      className="flex flex-wrap items-center justify-between gap-y-1.5 p-3 rounded border border-[rgba(42,42,62,0.3)] hover:bg-[rgba(255,255,255,0.02)] transition-colors"
      style={realized ? { opacity: 0.45, filter: "saturate(0.6)" } : undefined}
    >
      <div className="flex items-center gap-3 min-w-0 basis-full sm:basis-auto">
        <span
          className="inline-block px-2 py-0.5 rounded text-[10px] uppercase tracking-wider font-medium"
          style={{
            background:
              evt.event_type === "income"
                ? "rgba(0,212,170,0.15)"
                : "rgba(255,77,106,0.15)",
            color: evt.event_type === "income" ? "var(--green)" : "var(--red)",
          }}
        >
          {evt.event_type}
        </span>
        <div className="min-w-0">
          <div className="text-sm text-[var(--text-primary)] truncate">
            {evt.name}
          </div>
          <div className="text-[10px] text-[var(--text-secondary)]">
            {evt.date}
            {evt.is_recurring &&
              ` / ${evt.recurrence} → ${evt.end_date ?? "ongoing"}`}
            {evt.probability < 1 &&
              ` / ${(evt.probability * 100).toFixed(0)}% likely`}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3 ml-auto">
        <span
          className="font-mono text-sm font-bold"
          style={{
            color: evt.event_type === "income" ? "var(--green)" : "var(--red)",
          }}
        >
          {evt.event_type === "income" ? "+" : "-"}
          {formatCurrency(evt.amount)}
        </span>
        <span
          className="inline-block px-1.5 py-0.5 rounded text-[9px] uppercase tracking-wider"
          style={{
            background: realized
              ? "rgba(120,120,140,0.15)"
              : evt.status === "confirmed"
              ? "rgba(0,212,170,0.1)"
              : "rgba(77,142,255,0.1)",
            color: realized
              ? "var(--text-secondary)"
              : evt.status === "confirmed"
              ? "var(--green)"
              : "var(--blue)",
          }}
        >
          {realized ? "realized" : evt.status}
        </span>
        <button
          className="text-[var(--text-secondary)] hover:text-[var(--blue)] transition-colors text-sm"
          onClick={() => onEdit(evt)}
          title="Edit event"
        >
          ✎
        </button>
        <button
          className="text-[var(--text-secondary)] hover:text-[var(--red)] transition-colors text-xs"
          onClick={() => onDelete(evt.id)}
          title="Delete event"
        >
          x
        </button>
      </div>
    </div>
  );
}

function EventsList({
  events,
  onEdit,
  onDelete,
}: {
  events: CashflowEvent[];
  onEdit: (evt: CashflowEvent) => void;
  onDelete: (id: string) => void;
}) {
  const [showPast, setShowPast] = useState(false);

  const { upcoming, past } = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const up: CashflowEvent[] = [];
    const ps: CashflowEvent[] = [];
    for (const evt of events) {
      (isEventRealized(evt, today) ? ps : up).push(evt);
    }
    // Upcoming sorted asc by date, past sorted desc (most recent first).
    up.sort((a, b) => a.date.localeCompare(b.date));
    ps.sort((a, b) => b.date.localeCompare(a.date));
    return { upcoming: up, past: ps };
  }, [events]);

  return (
    <div className="lg:col-span-3 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-[var(--text-secondary)]">
          Cash Flow Events
        </h3>
        <span className="text-xs text-[var(--text-secondary)]">
          {upcoming.length} upcoming
          {past.length > 0 && ` / ${past.length} past`}
        </span>
      </div>

      <div className="space-y-2">
        {upcoming.map((evt) => (
          <EventRow
            key={evt.id}
            evt={evt}
            realized={false}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        ))}
        {events.length === 0 && (
          <div className="text-center text-sm text-[var(--text-secondary)] py-6">
            No events yet. Add your first cash flow event.
          </div>
        )}
      </div>

      {past.length > 0 && (
        <div className="mt-4 pt-3 border-t border-[var(--border)]">
          <button
            className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors mb-2"
            onClick={() => setShowPast((v) => !v)}
          >
            {showPast ? "▾" : "▸"} Past events ({past.length})
          </button>
          {showPast && (
            <div className="space-y-2">
              {past.map((evt) => (
                <EventRow
                  key={evt.id}
                  evt={evt}
                  realized={true}
                  onEdit={onEdit}
                  onDelete={onDelete}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page                                                          */
/* ------------------------------------------------------------------ */
export default function RunwayPage() {
  const { data: fireConfig } = useFireConfig();

  // Committed overrides — these drive the API query. Undefined = let the
  // backend derive income/burn from actual transaction data.
  const [incomeOverride, setIncomeOverride] = useState<number | undefined>(
    undefined,
  );
  const [burnOverride, setBurnOverride] = useState<number | undefined>(
    undefined,
  );
  // Draft text in the input fields — only committed on blur/Enter
  const [incomeDraft, setIncomeDraft] = useState<string>("");
  const [burnDraft, setBurnDraft] = useState<string>("");

  // Seed income/burn once from custom_assumptions.runway (if set),
  // falling back to target_annual_spending for burn.
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current || !fireConfig) return;
    const runway = (fireConfig.custom_assumptions?.runway ?? {}) as Record<string, number>;
    const seedIncome = runway.monthly_income;
    // Seed burn to MATCH the Retirement page's monthly total: spending + healthcare
    // (the pages disagreed by exactly the healthcare line before).
    const seedBurn = runway.monthly_burn
      ?? (fireConfig.target_annual_spending
        ? Math.round(fireConfig.target_annual_spending / 12 / 100)
          + Math.round((fireConfig.healthcare_monthly_cost ?? 0) / 100)
        : undefined);
    if (seedIncome != null && seedIncome > 0) {
      setIncomeDraft(String(seedIncome));
      setIncomeOverride(seedIncome);
    }
    if (seedBurn != null && seedBurn > 0) {
      setBurnDraft(String(seedBurn));
      setBurnOverride(seedBurn);
    }
    seeded.current = true;
  }, [fireConfig]);

  const [showAddModal, setShowAddModal] = useState(false);
  const [editingEvent, setEditingEvent] = useState<CashflowEvent | null>(null);

  const commitIncome = () => {
    const v = parseFloat(incomeDraft);
    setIncomeOverride(isNaN(v) || incomeDraft === "" ? undefined : v);
  };
  const commitBurn = () => {
    const v = parseFloat(burnDraft);
    setBurnOverride(isNaN(v) || burnDraft === "" ? undefined : v);
  };

  // Queries
  const {
    data: runway,
    isLoading: runwayLoading,
  } = useRunway(24, incomeOverride, burnOverride);
  const { data: events, isLoading: eventsLoading } = useCashflowEvents();
  const createEvent = useCreateCashflowEvent();
  const updateEvent = useUpdateCashflowEvent();
  const deleteEvent = useDeleteCashflowEvent();

  // Chart data — anchor each point at the START of the month so the
  // first tick reflects today's cash before any of this month's events apply.
  //
  // Event labels are shifted forward by one tick: an event that occurs in
  // month X manifests as a slope between X-tick (pre-event start) and
  // (X+1)-tick (post-event start, equal to X's ending cash). Plotting the
  // marker on (X+1) makes it land on the visible spike/dip — same way the
  // Retirement bridge chart aligns markers with peaks. Events in the final
  // month have no next tick to land on, so they're dropped from markers
  // (still visible in the table below).
  const chartData = useMemo(() => {
    if (!runway?.projection) return [];
    const points = runway.projection.map((p) => ({
      month: p.month,
      cash: p.starting_cash,
      income: p.income,
      expenses: -p.expenses,
      events: [] as string[],
    }));
    runway.projection.forEach((p, i) => {
      if (p.events.length > 0 && i + 1 < points.length) {
        points[i + 1].events = p.events;
      }
    });
    return points;
  }, [runway]);

  const eventPoints = useMemo(
    () => chartData.filter((p) => p.events.length > 0),
    [chartData],
  );

  // Marker groups for the chart: enrich the projection's bare event names
  // with amount/date/probability by joining against the full event objects.
  const markerGroups = useMemo<EventMarkerGroup[]>(() => {
    const byName = new Map((events ?? []).map((e) => [e.name, e]));
    return eventPoints.map((p) => ({
      x: p.month,
      y: p.cash,
      events: p.events.map((name) => {
        const ev = byName.get(name);
        return {
          label: name,
          amount: ev ? (ev.event_type === "expense" ? -ev.amount : ev.amount) : undefined,
          date: ev?.date,
          probability: ev?.probability,
        };
      }),
    }));
  }, [eventPoints, events]);

  const { markers: eventMarkers, overlay: eventOverlay, wrapperProps: chartWrapperProps } =
    useEventMarkers(markerGroups, {
      defaultColor: "var(--yellow)",
      xLabel: (m) => {
        const d = new Date(`${m}-01`);
        return isNaN(d.getTime())
          ? String(m)
          : d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
      },
    });

  // Y-axis position of the zero line as a fraction from the top (0..1).
  // Used to split the area + stroke gradients into a green region above
  // zero and a red region below. Falls back to 1 (all green) when cash
  // never goes negative across the projection.
  const zeroOffset = useMemo(() => {
    if (chartData.length === 0) return 1;
    const values = chartData.map((d) => d.cash);
    const yMax = Math.max(0, ...values);
    const yMin = Math.min(0, ...values);
    if (yMax === yMin) return 1;
    return yMax / (yMax - yMin);
  }, [chartData]);

  // Negative-cash window summary for the alert banner
  const negativeWindow = useMemo(() => {
    if (!runway?.projection) return null;
    const negs = runway.projection.filter((p) => p.starting_cash < 0 || p.ending_cash < 0);
    if (negs.length === 0) return null;
    const trough = Math.min(
      ...runway.projection.map((p) => Math.min(p.starting_cash, p.ending_cash)),
    );
    return {
      first: negs[0].month,
      last: negs[negs.length - 1].month,
      trough,
      months: negs.length,
    };
  }, [runway]);

  // Income/expense bar data for current month view
  const barData = useMemo(() => {
    if (!runway?.projection || runway.projection.length < 2) return [];
    // Show next 6 months of income vs expenses
    const monthNames = [
      "Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    return runway.projection.slice(0, 6).map((p) => ({
      month: monthNames[parseInt(p.month.slice(5), 10) - 1],
      income: p.income,
      expenses: p.expenses,
    }));
  }, [runway]);

  if (runwayLoading || eventsLoading) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-64 text-[var(--text-secondary)]">
          Loading runway projection...
        </div>
      </Layout>
    );
  }

  if (!runway) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-64 text-[var(--red)]">
          Failed to load runway data
        </div>
      </Layout>
    );
  }

  const zeroDate = runway.cash_zero_date
    ? new Date(runway.cash_zero_date).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : null;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-xl font-semibold text-[var(--text-primary)] tracking-tight">
              Cash Runway
            </h2>
            <p className="text-sm text-[var(--text-secondary)] mt-1">
              Monthly cash projection with known events
            </p>
          </div>
          <button
            className="px-3 py-1.5 text-xs bg-[var(--bg-secondary)] border border-[var(--border)] rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--green)] transition-colors"
            onClick={() => setShowAddModal(true)}
          >
            + Add Event
          </button>
        </div>

        {/* Hero Stats */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
          <StatCard
            label="Liquid Cash"
            value={formatCurrency(runway.current_cash)}
            color="var(--text-primary)"
          />
          <StatCard
            label="Cash Zero"
            value={zeroDate ?? "Never"}
            color={zeroDate ? "var(--red)" : "var(--green)"}
            sub={
              runway.cash_zero_date
                ? `${Math.round((new Date(runway.cash_zero_date).getTime() - Date.now()) / (30.44 * 24 * 60 * 60 * 1000))} months`
                : runway.net_monthly >= 0
                  ? "Positive cash flow"
                  : "Holds through 24 mo — planned events cover the gap"
            }
          />
          <StatCard
            label="Net Monthly"
            value={formatCurrency(runway.net_monthly)}
            color={runway.net_monthly >= 0 ? "var(--green)" : "var(--red)"}
          />
          <StatCard
            label="Monthly Income"
            value={formatCurrency(runway.monthly_income)}
            color="var(--green)"
            sub={`${runway.income_provenance === "override" ? "Your override" : "From income sources"} · 90d actual: ${formatCurrency(runway.trailing_income)}/mo`}
          />
          <StatCard
            label="Monthly Burn"
            value={formatCurrency(runway.monthly_burn)}
            color="var(--red)"
            sub={`Trailing: ${formatCurrency(runway.trailing_burn)}`}
          />
        </div>

        {/* Baseline Controls */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
          <h3 className="text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-3">
            Baseline Overrides
          </h3>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 items-end">
            <div>
              <label className="block text-xs text-[var(--text-secondary)] mb-1">
                Monthly Income ($)
              </label>
              <input
                type="number"
                className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] font-mono focus:outline-none focus:border-[var(--blue)] transition-colors"
                value={incomeDraft}
                onChange={(e) => setIncomeDraft(e.target.value)}
                onBlur={commitIncome}
                onKeyDown={(e) => e.key === "Enter" && commitIncome()}
                placeholder="From income sources"
              />
              <span className="text-[10px] text-[var(--text-secondary)] mt-0.5 block whitespace-nowrap">
                {runway.income_provenance === "override"
                  ? `Sources: ${formatCurrency(runway.trailing_income)}/mo received 90d`
                  : `Sources: ${formatCurrency(runway.monthly_income)}/mo`}
              </span>
            </div>
            <div>
              <label className="block text-xs text-[var(--text-secondary)] mb-1">
                Monthly Burn ($)
              </label>
              <input
                type="number"
                className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] font-mono focus:outline-none focus:border-[var(--blue)] transition-colors"
                value={burnDraft}
                onChange={(e) => setBurnDraft(e.target.value)}
                onBlur={commitBurn}
                onKeyDown={(e) => e.key === "Enter" && commitBurn()}
                placeholder="Use trailing average"
              />
              <span className="text-[10px] text-[var(--text-secondary)] mt-0.5 block whitespace-nowrap">
                Trailing: {formatCurrency(runway.trailing_burn)}/mo
              </span>
            </div>
            <div>
              <button
                className="px-4 py-2 text-xs rounded border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--blue)] transition-colors"
                onClick={() => {
                  setIncomeOverride(undefined);
                  setBurnOverride(undefined);
                  setIncomeDraft("");
                  setBurnDraft("");
                }}
              >
                Clear Overrides
              </button>
              {/* invisible spacer matching the inputs' hint line so items-end aligns the button with the input row */}
              <span className="text-[10px] mt-0.5 block invisible" aria-hidden="true">.</span>
            </div>
          </div>
        </div>

        {/* Cash Flow Projection Chart */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-[var(--text-secondary)]">
              Cash Balance Projection
            </h3>
            {negativeWindow && (
              <div
                className="text-[11px] px-2.5 py-1 rounded border font-mono"
                style={{
                  background: "rgba(176,74,86,0.08)",
                  borderColor: "rgba(176,74,86,0.3)",
                  color: "var(--red)",
                }}
                title="Months where projected cash drops below zero"
              >
                Negative window: {negativeWindow.first} → {negativeWindow.last}
                {" / "}
                trough {formatCurrency(negativeWindow.trough)}
                {" / "}
                {negativeWindow.months} mo
              </div>
            )}
          </div>
          <div {...chartWrapperProps}>
          <ResponsiveContainer width="100%" height={340}>
            <AreaChart
              data={chartData}
              margin={{ top: 30, right: 10, left: 10, bottom: 0 }}
            >
              <defs>
                {/* Split fill: green above zero, red below. zeroOffset is
                    the relative Y position of the zero line (0=top, 1=bottom). */}
                <linearGradient id="runwayFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#2e8b6e" stopOpacity={0.35} />
                  <stop offset={zeroOffset} stopColor="#2e8b6e" stopOpacity={0.05} />
                  <stop offset={zeroOffset} stopColor="#b04a56" stopOpacity={0.08} />
                  <stop offset="100%" stopColor="#b04a56" stopOpacity={0.4} />
                </linearGradient>
                {/* Hard split for the line stroke at the zero crossing */}
                <linearGradient id="runwayStroke" x1="0" y1="0" x2="0" y2="1">
                  <stop offset={zeroOffset} stopColor="var(--green)" />
                  <stop offset={zeroOffset} stopColor="var(--red)" />
                </linearGradient>
              </defs>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="rgba(42,42,62,0.5)"
              />
              <XAxis
                dataKey="month"
                stroke="#5c5c6a"
                tick={{ fontSize: 11, fill: "#5c5c6a" }}
                tickFormatter={(v: string, index: number) => {
                  const [y, m] = v.split("-");
                  const monthNames = [
                    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                  ];
                  const name = monthNames[parseInt(m, 10) - 1];
                  // Show year on the first tick and at every January.
                  return index === 0 || m === "01"
                    ? `${name} '${y.slice(2)}`
                    : name;
                }}
              />
              <YAxis
                stroke="#5c5c6a"
                tickFormatter={fmtAxis}
                width={65}
                tick={{ fontSize: 11, fill: "#5c5c6a" }}
              />
              <Tooltip
                {...TOOLTIP_STYLE}
                labelFormatter={(month) => {
                  const d = new Date(String(month) + "-01");
                  return `Start of ${d.toLocaleDateString("en-US", {
                    month: "long",
                    year: "numeric",
                  })}`;
                }}
                formatter={(value, name) => [
                  formatCurrency(Number(value)),
                  name === "cash" ? "Starting Cash" : String(name),
                ]}
              />
              <ReferenceLine
                y={0}
                stroke="var(--red)"
                strokeWidth={1.5}
                strokeDasharray="6 3"
                label={{
                  value: "ZERO",
                  position: "right",
                  fill: "var(--red)",
                  fontSize: 10,
                }}
              />
              {eventMarkers}
              <Area
                type="monotone"
                dataKey="cash"
                stroke="url(#runwayStroke)"
                strokeWidth={2}
                fill="url(#runwayFill)"
                baseValue={0}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
          {eventOverlay}
          </div>
        </div>

        {/* Two-column: Income/Expense Bars + Events */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          {/* Income vs Expenses (next 6 months) */}
          <div className="lg:col-span-2 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
            <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">
              Income vs Expenses (6 mo)
            </h3>
            <ResponsiveContainer width="100%" height={320}>
              <BarChart
                data={barData}
                margin={{ top: 5, right: 5, left: 5, bottom: 5 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="rgba(42,42,62,0.3)"
                  vertical={false}
                />
                <XAxis
                  dataKey="month"
                  stroke="#5c5c6a"
                  tick={{ fontSize: 10, fill: "#5c5c6a" }}
                />
                <YAxis
                  stroke="#5c5c6a"
                  tickFormatter={fmtAxis}
                  tick={{ fontSize: 10, fill: "#5c5c6a" }}
                  width={50}
                />
                <Tooltip
                  {...TOOLTIP_STYLE}
                  formatter={(value) => formatCurrency(Number(value))}
                />
                <Bar dataKey="income" fill="var(--green)" fillOpacity={0.7} radius={[3, 3, 0, 0]} />
                <Bar dataKey="expenses" fill="var(--red)" fillOpacity={0.7} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Events List */}
          <EventsList
            events={events ?? []}
            onEdit={(evt) => setEditingEvent(evt)}
            onDelete={(id) => deleteEvent.mutate(id)}
          />
        </div>

        {/* Monthly Projection Table */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">
            Monthly Projection
          </h3>
          <div className="max-h-[400px] overflow-y-auto overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--border)]">
                  {[
                    "Month",
                    "Starting Cash",
                    "Income",
                    "Expenses",
                    "Net",
                    "Ending Cash",
                    "Events",
                  ].map((h) => (
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
                {runway.projection.map((row) => {
                  const isNeg = row.ending_cash < 0;
                  return (
                    <tr
                      key={row.month}
                      className="border-b border-[rgba(42,42,62,0.3)] hover:bg-[rgba(255,255,255,0.02)] transition-colors"
                      style={{
                        background: isNeg
                          ? "rgba(255,77,106,0.04)"
                          : undefined,
                      }}
                    >
                      <td className="py-2 px-3 font-mono text-[var(--text-primary)]">
                        {row.month}
                      </td>
                      <td className="py-2 px-3 font-mono text-[var(--text-primary)]">
                        {formatCurrency(row.starting_cash)}
                      </td>
                      <td className="py-2 px-3 font-mono text-[var(--green)]">
                        {formatCurrency(row.income)}
                      </td>
                      <td className="py-2 px-3 font-mono text-[var(--red)]">
                        {formatCurrency(row.expenses)}
                      </td>
                      <td
                        className="py-2 px-3 font-mono font-bold"
                        style={{
                          color: row.net >= 0 ? "var(--green)" : "var(--red)",
                        }}
                      >
                        {row.net >= 0 ? "+" : ""}
                        {formatCurrency(row.net)}
                      </td>
                      <td
                        className="py-2 px-3 font-mono font-bold"
                        style={{
                          color: row.ending_cash >= 0 ? "var(--green)" : "var(--red)",
                        }}
                      >
                        {formatCurrency(row.ending_cash)}
                      </td>
                      <td className="py-2 px-3">
                        <div className="flex flex-wrap gap-1">
                          {row.events.map((evt, i) => (
                            <span
                              key={i}
                              className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-[rgba(77,142,255,0.1)] text-[var(--blue)] truncate max-w-[140px]"
                            >
                              {evt}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Event Modal — handles both add and edit */}
      {(showAddModal || editingEvent) && (
        <EventModal
          initial={editingEvent ?? undefined}
          onClose={() => {
            setShowAddModal(false);
            setEditingEvent(null);
          }}
          onSubmit={(data) => {
            if (editingEvent) {
              updateEvent.mutate({ id: editingEvent.id, data });
            } else {
              createEvent.mutate(data);
            }
            setShowAddModal(false);
            setEditingEvent(null);
          }}
        />
      )}
    </Layout>
  );
}
