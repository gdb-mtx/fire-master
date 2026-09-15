import { useState } from "react";
import type { Property, PropertyRule } from "../../types/property";
import {
  usePropertyRules,
  useUpsertRule,
  useDeleteRule,
  useUpdateProperty,
  useManualEntries,
  useCreateManualEntry,
  useDeleteManualEntry,
  usePropertyCategories,
} from "../../api/queries";
import { formatCurrency as fmt } from "../../utils/formatting";

type Tab = "rules" | "properties" | "manual";

const RULE_KIND_COLORS: Record<string, string> = {
  expense: "var(--blue)",
  income: "var(--green)",
  exclusion: "var(--red)",
};

export default function PropertyManagePanel({ properties }: { properties: Property[] }) {
  const [tab, setTab] = useState<Tab>("rules");
  const propName = (id: string | null) =>
    id ? properties.find((p) => p.id === id)?.name ?? "—" : "Global";

  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg">
      <div className="flex border-b border-[var(--border)]">
        {(["rules", "properties", "manual"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-5 py-3 text-xs font-medium uppercase tracking-wider transition-colors ${
              tab === t
                ? "text-[var(--green)] border-b-2 border-[var(--green)]"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t === "rules" ? "Classification rules" : t === "properties" ? "Property facts" : "Manual entries"}
          </button>
        ))}
      </div>
      <div className="p-5">
        {tab === "rules" && <RulesTab properties={properties} propName={propName} />}
        {tab === "properties" && <PropertiesTab properties={properties} />}
        {tab === "manual" && <ManualTab properties={properties} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Rules
// ---------------------------------------------------------------------------

function RulesTab({
  properties,
  propName,
}: {
  properties: Property[];
  propName: (id: string | null) => string;
}) {
  const { data: rules } = usePropertyRules();
  const { data: cats } = usePropertyCategories();
  const upsert = useUpsertRule();
  const del = useDeleteRule();

  const [draft, setDraft] = useState({
    property_id: properties[0]?.id ?? "",
    pattern: "",
    rule_kind: "expense" as PropertyRule["rule_kind"],
    expense_category: "",
    require_tx_category: "",
    priority: 50,
  });

  const add = () => {
    if (!draft.pattern.trim()) return;
    upsert.mutate({
      data: {
        property_id: draft.rule_kind === "exclusion" ? null : draft.property_id,
        pattern: draft.pattern.trim(),
        rule_kind: draft.rule_kind,
        expense_category:
          draft.rule_kind === "expense" && draft.expense_category
            ? draft.expense_category
            : null,
        require_tx_category: draft.require_tx_category.trim() || null,
        priority: draft.priority,
      },
    });
    setDraft({ ...draft, pattern: "", require_tx_category: "" });
  };

  return (
    <div className="space-y-4">
      <p className="text-xs text-[var(--text-secondary)]">
        Rules match a merchant by substring (case-insensitive), in ascending priority order.
        Exclusions win first; income rules match positive amounts; expense rules match charges.
        Editing rules then <span className="text-[var(--text-primary)]">Re-classify</span> restamps
        transactions — manual overrides are preserved.
      </p>

      {/* Add rule */}
      <div className="flex flex-wrap items-end gap-2 bg-[rgba(255,255,255,0.02)] border border-[var(--border)] rounded-lg p-3">
        <Field label="Kind">
          <select
            value={draft.rule_kind}
            onChange={(e) => setDraft({ ...draft, rule_kind: e.target.value as PropertyRule["rule_kind"] })}
            className="input"
          >
            <option value="expense">expense</option>
            <option value="income">income</option>
            <option value="exclusion">exclusion</option>
          </select>
        </Field>
        {draft.rule_kind !== "exclusion" && (
          <Field label="Property">
            <select
              value={draft.property_id}
              onChange={(e) => setDraft({ ...draft, property_id: e.target.value })}
              className="input"
            >
              {properties.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Merchant contains">
          <input
            value={draft.pattern}
            onChange={(e) => setDraft({ ...draft, pattern: e.target.value })}
            placeholder="e.g. Coastal Power"
            className="input w-44"
          />
        </Field>
        {draft.rule_kind === "expense" && (
          <Field label="Category">
            <select
              value={draft.expense_category}
              onChange={(e) => setDraft({ ...draft, expense_category: e.target.value })}
              className="input"
            >
              <option value="">(auto)</option>
              {cats?.expense_categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Req. tx cat">
          <input
            value={draft.require_tx_category}
            onChange={(e) => setDraft({ ...draft, require_tx_category: e.target.value })}
            placeholder="optional"
            className="input w-24"
          />
        </Field>
        <Field label="Priority">
          <input
            type="number"
            value={draft.priority}
            onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })}
            className="input w-16"
          />
        </Field>
        <button
          onClick={add}
          disabled={upsert.isPending}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--green)] text-white hover:opacity-90 disabled:opacity-50"
        >
          Add rule
        </button>
      </div>

      {/* Rules table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[var(--text-secondary)] text-[11px] uppercase tracking-wider text-left">
              <th className="px-3 py-2">Kind</th>
              <th className="px-3 py-2">Property</th>
              <th className="px-3 py-2">Merchant ~</th>
              <th className="px-3 py-2">Category</th>
              <th className="px-3 py-2">Req. cat</th>
              <th className="px-3 py-2 text-right">Prio</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {(rules ?? []).map((r) => (
              <tr
                key={r.id}
                className={`border-t border-[var(--border)] ${r.is_active ? "" : "opacity-40"}`}
              >
                <td className="px-3 py-2">
                  <span
                    className="text-[10px] uppercase font-semibold"
                    style={{ color: RULE_KIND_COLORS[r.rule_kind] }}
                  >
                    {r.rule_kind}
                  </span>
                </td>
                <td className="px-3 py-2 text-[var(--text-secondary)]">{propName(r.property_id)}</td>
                <td className="px-3 py-2 text-[var(--text-primary)]">{r.pattern}</td>
                <td className="px-3 py-2 text-[var(--text-secondary)]">{r.expense_category ?? "—"}</td>
                <td className="px-3 py-2 text-[var(--text-secondary)]">{r.require_tx_category ?? "—"}</td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--text-secondary)]">{r.priority}</td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  <button
                    onClick={() => upsert.mutate({ id: r.id, data: { is_active: !r.is_active } })}
                    className="text-[11px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] mr-3"
                  >
                    {r.is_active ? "disable" : "enable"}
                  </button>
                  <button
                    onClick={() => del.mutate(r.id)}
                    className="text-[11px] text-[var(--text-secondary)] hover:text-[var(--red)]"
                  >
                    delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Property facts
// ---------------------------------------------------------------------------

function PropertiesTab({ properties }: { properties: Property[] }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
      {properties.map((p) => (
        <PropertyEditCard key={p.id} property={p} />
      ))}
    </div>
  );
}

function PropertyEditCard({ property }: { property: Property }) {
  const update = useUpdateProperty();
  const [form, setForm] = useState({
    value: property.value,
    loan_balance: property.loan_balance,
    potential_monthly_rental: property.potential_monthly_rental ?? 0,
  });
  const dirty =
    form.value !== property.value ||
    form.loan_balance !== property.loan_balance ||
    (property.potential_monthly_rental ?? 0) !== form.potential_monthly_rental;

  return (
    <div className="border border-[var(--border)] rounded-lg p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="w-2.5 h-2.5 rounded-sm" style={{ background: property.color ?? "var(--blue)" }} />
        <span className="font-semibold text-[var(--text-primary)]">{property.name}</span>
        <span className="ml-auto text-xs text-[var(--text-secondary)]">
          equity {fmt(form.value - form.loan_balance)}
        </span>
      </div>
      <NumField label="Value" value={form.value} onChange={(v) => setForm({ ...form, value: v })} />
      <NumField label="Loan balance" value={form.loan_balance} onChange={(v) => setForm({ ...form, loan_balance: v })} />
      {Number(property.extra_data.mortgage_rate_pct ?? 0) > 0 && (
        <div className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          Mortgage: {Number(property.extra_data.mortgage_rate_pct).toFixed(2)}% ·{" "}
          {fmt(Number(property.extra_data.mortgage_monthly_payment ?? 0))}/month · final payment{" "}
          {new Date(`${String(property.extra_data.mortgage_payoff_date)}T12:00:00`).toLocaleDateString(
            "en-US", { month: "short", year: "numeric" },
          )}
          <div className="mt-1 text-[10px] opacity-70">Projection terms are editable in FIRE Settings.</div>
        </div>
      )}
      <NumField
        label="Potential rent / mo"
        value={form.potential_monthly_rental}
        onChange={(v) => setForm({ ...form, potential_monthly_rental: v })}
      />
      <button
        disabled={!dirty || update.isPending}
        onClick={() =>
          update.mutate({
            id: property.id,
            data: {
              value: form.value,
              loan_balance: form.loan_balance,
              potential_monthly_rental: form.potential_monthly_rental,
            },
          })
        }
        className="w-full px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--green)] text-white hover:opacity-90 disabled:opacity-40 disabled:bg-[var(--border)] disabled:text-[var(--text-secondary)]"
      >
        {update.isPending ? "Saving…" : dirty ? "Save" : "Saved"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Manual entries
// ---------------------------------------------------------------------------

function ManualTab({ properties }: { properties: Property[] }) {
  const { data: entries } = useManualEntries();
  const { data: cats } = usePropertyCategories();
  const create = useCreateManualEntry();
  const del = useDeleteManualEntry();

  const today = new Date().toISOString().slice(0, 10);
  const [draft, setDraft] = useState({
    property_id: properties[0]?.id ?? "",
    date: today,
    kind: "income" as "income" | "expense",
    amount: 0,
    property_category: "Rental Income",
    merchant: "",
  });

  const submit = () => {
    if (!draft.amount) return;
    const signed = draft.kind === "income" ? Math.abs(draft.amount) : -Math.abs(draft.amount);
    create.mutate({
      property_id: draft.property_id,
      date: draft.date,
      amount: signed,
      property_category: draft.kind === "income" ? "Rental Income" : draft.property_category,
      merchant: draft.merchant.trim() || null,
    });
    setDraft({ ...draft, amount: 0, merchant: "" });
  };

  return (
    <div className="space-y-4">
      <p className="text-xs text-[var(--text-secondary)]">
        Off-Monarch income or costs (e.g. rent paid into another account). These never sync from
        Monarch and stay pinned to the property.
      </p>
      <div className="flex flex-wrap items-end gap-2 bg-[rgba(255,255,255,0.02)] border border-[var(--border)] rounded-lg p-3">
        <Field label="Property">
          <select
            value={draft.property_id}
            onChange={(e) => setDraft({ ...draft, property_id: e.target.value })}
            className="input"
          >
            {properties.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Kind">
          <select
            value={draft.kind}
            onChange={(e) => setDraft({ ...draft, kind: e.target.value as "income" | "expense" })}
            className="input"
          >
            <option value="income">income</option>
            <option value="expense">expense</option>
          </select>
        </Field>
        {draft.kind === "expense" && (
          <Field label="Category">
            <select
              value={draft.property_category}
              onChange={(e) => setDraft({ ...draft, property_category: e.target.value })}
              className="input"
            >
              {cats?.expense_categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Date">
          <input
            type="date"
            value={draft.date}
            onChange={(e) => setDraft({ ...draft, date: e.target.value })}
            className="input"
          />
        </Field>
        <Field label="Amount $">
          <input
            type="number"
            value={draft.amount || ""}
            onChange={(e) => setDraft({ ...draft, amount: Number(e.target.value) })}
            className="input w-28"
          />
        </Field>
        <Field label="Memo">
          <input
            value={draft.merchant}
            onChange={(e) => setDraft({ ...draft, merchant: e.target.value })}
            placeholder="optional"
            className="input w-36"
          />
        </Field>
        <button
          onClick={submit}
          disabled={create.isPending}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--green)] text-white hover:opacity-90 disabled:opacity-50"
        >
          Add entry
        </button>
      </div>

      <table className="w-full text-sm">
        <tbody>
          {(entries ?? []).map((e) => {
            const prop = properties.find((p) => p.id === e.property_id);
            return (
              <tr key={e.id} className="border-t border-[var(--border)]">
                <td className="px-3 py-2 text-[var(--text-secondary)]">{e.date}</td>
                <td className="px-3 py-2 text-[var(--text-primary)]">{prop?.name ?? "—"}</td>
                <td className="px-3 py-2 text-[var(--text-secondary)]">{e.property_category}</td>
                <td className="px-3 py-2 text-[var(--text-secondary)]">{e.merchant ?? "—"}</td>
                <td
                  className="px-3 py-2 text-right tabular-nums"
                  style={{ color: e.amount >= 0 ? "var(--green)" : "var(--text-primary)" }}
                >
                  {fmt(e.amount)}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => del.mutate(e.id)}
                    className="text-[11px] text-[var(--text-secondary)] hover:text-[var(--red)]"
                  >
                    delete
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Small inputs
// ---------------------------------------------------------------------------

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">{label}</span>
      {children}
    </label>
  );
}

function NumField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-3">
      <span className="text-xs text-[var(--text-secondary)]">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="input w-32 text-right"
      />
    </label>
  );
}
