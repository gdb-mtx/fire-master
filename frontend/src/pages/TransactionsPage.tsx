import { useEffect, useMemo, useState } from "react";
import Layout from "../components/Layout";
import {
  useLedgerTransactions,
  useProperties,
  usePropertyCategories,
  useAccounts,
  useReassignTransaction,
} from "../api/queries";
import { formatCurrency as fmt } from "../utils/formatting";
import type {
  LedgerType,
  LedgerClassification,
} from "../types/transaction";

const PAGE = 50;

/** Small segmented control built from the shared token palette. */
function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-[var(--border)] overflow-hidden">
      {options.map((o, i) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={`px-3 py-1.5 text-xs transition-colors ${
            i > 0 ? "border-l border-[var(--border)]" : ""
          } ${
            value === o.value
              ? "bg-[var(--blue)] text-white"
              : "bg-[var(--bg-card)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export default function TransactionsPage() {
  // ---- filter state ---------------------------------------------------------
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [type, setTypeRaw] = useState<LedgerType>("all");
  const [classification, setClassificationRaw] =
    useState<LedgerClassification>("all");
  const [propertyId, setPropertyIdRaw] = useState<string | null>(null);
  const [accountId, setAccountIdRaw] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  // Any filter change resets pagination to the first page.
  const reset = () => setOffset(0);
  const setType = (v: LedgerType) => { setTypeRaw(v); reset(); };
  const setClassification = (v: LedgerClassification) => { setClassificationRaw(v); reset(); };
  const setPropertyId = (v: string | null) => { setPropertyIdRaw(v); reset(); };
  const setAccountId = (v: string | null) => { setAccountIdRaw(v); reset(); };

  // Debounce the merchant search so we don't refetch on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      setQ(qInput);
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [qInput]);

  // ---- data -----------------------------------------------------------------
  const { data, isFetching } = useLedgerTransactions({
    q,
    type,
    classification,
    propertyId,
    accountId,
    limit: PAGE,
    offset,
  });
  const { data: properties } = useProperties();
  const { data: cats } = usePropertyCategories();
  // Income category first, then the expense vocabulary. The API has always
  // returned income_category; the table simply never offered it.
  const propertyCategories = cats
    ? [cats.income_category, ...cats.expense_categories]
    : [];
  const { data: accounts } = useAccounts();
  const reassign = useReassignTransaction();

  const propsById = useMemo(
    () => Object.fromEntries((properties ?? []).map((p) => [p.id, p])),
    [properties],
  );

  const total = data?.total_count ?? 0;
  const net = (data?.total_income ?? 0) + (data?.total_expense ?? 0);
  const showingFrom = total === 0 ? 0 : offset + 1;
  const showingTo = Math.min(offset + PAGE, total);

  return (
    <Layout>
      <div className="space-y-5">
        {/* ── Header + summary ────────────────────────────────────────── */}
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-[var(--text-primary)]">
              Transactions
            </h1>
            <p className="text-sm text-[var(--text-secondary)] mt-0.5">
              Every transaction, in one place. Assign what belongs to a property —
              it leaves the Tracker and lands in that property&apos;s P&amp;L.
            </p>
          </div>
          <div className="flex items-stretch gap-2">
            <SummaryChip label="Transactions" value={total.toLocaleString()} />
            <SummaryChip
              label="Income"
              value={fmt(data?.total_income ?? 0)}
              tone="green"
            />
            <SummaryChip
              label="Expense"
              value={fmt(Math.abs(data?.total_expense ?? 0))}
              tone="red"
            />
            <SummaryChip
              label="Net"
              value={fmt(net)}
              tone={net >= 0 ? "green" : "red"}
            />
          </div>
        </div>

        {/* ── Filter bar ──────────────────────────────────────────────── */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-3 flex flex-wrap items-center gap-3">
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Search merchant…"
            className="input w-56"
          />
          <Segmented
            value={type}
            onChange={setType}
            options={[
              { value: "all", label: "All" },
              { value: "income", label: "Income" },
              { value: "expense", label: "Expense" },
            ]}
          />
          <Segmented
            value={classification}
            onChange={setClassification}
            options={[
              { value: "all", label: "Any status" },
              { value: "unclassified", label: "Unassigned" },
              { value: "classified", label: "On a property" },
            ]}
          />
          <select
            value={propertyId ?? ""}
            onChange={(e) => setPropertyId(e.target.value || null)}
            className="input"
          >
            <option value="">All properties</option>
            {properties?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <select
            value={accountId ?? ""}
            onChange={(e) => setAccountId(e.target.value || null)}
            className="input max-w-[200px]"
          >
            <option value="">All accounts</option>
            {accounts?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          {isFetching && (
            <span className="text-[11px] text-[var(--text-secondary)] ml-auto">
              updating…
            </span>
          )}
        </div>

        {/* ── Ledger table ────────────────────────────────────────────── */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[var(--text-secondary)] text-[11px] uppercase tracking-wider text-left border-b border-[var(--border)]">
                  <th className="px-4 py-2.5 font-medium">Date</th>
                  <th className="px-3 py-2.5 font-medium">Merchant</th>
                  <th className="px-3 py-2.5 font-medium">Account</th>
                  <th className="px-3 py-2.5 font-medium">Category</th>
                  <th className="px-3 py-2.5 font-medium text-right">Amount</th>
                  <th className="px-3 py-2.5 font-medium w-[320px]">Property</th>
                </tr>
              </thead>
              <tbody>
                {data?.transactions.map((tx) => {
                  const assigned = !!tx.property_id;
                  const prop = tx.property_id ? propsById[tx.property_id] : null;
                  const isIncome = tx.amount >= 0;
                  return (
                    <tr
                      key={tx.id}
                      className="border-b border-[var(--border-subtle)] last:border-0 hover:bg-[var(--bg-secondary)] transition-colors"
                    >
                      <td className="px-4 py-2 text-[var(--text-secondary)] font-mono text-xs whitespace-nowrap">
                        {tx.date}
                      </td>
                      <td className="px-3 py-2 text-[var(--text-primary)] max-w-[240px] truncate">
                        {tx.merchant || "—"}
                      </td>
                      <td className="px-3 py-2 text-[var(--text-secondary)] text-xs max-w-[160px] truncate">
                        {tx.account_name || "—"}
                      </td>
                      <td className="px-3 py-2">
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-secondary)] text-[var(--text-secondary)]">
                          {tx.category || "Uncategorized"}
                        </span>
                      </td>
                      <td
                        className={`px-3 py-2 text-right font-mono font-medium whitespace-nowrap ${
                          isIncome ? "text-[var(--green)]" : "text-[var(--text-primary)]"
                        }`}
                      >
                        {isIncome ? "+" : ""}
                        {fmt(tx.amount)}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          {/* color dot for the assigned property */}
                          <span
                            className="w-2 h-2 rounded-full shrink-0"
                            style={{
                              background: assigned
                                ? prop?.color || "var(--blue)"
                                : "var(--border-bright)",
                            }}
                          />
                          {/* property assignment */}
                          <select
                            value={tx.property_id ?? ""}
                            onChange={(e) =>
                              reassign.mutate({
                                txId: tx.id,
                                propertyId: e.target.value || null,
                                // Sign picks a sensible default only — the
                                // dropdown below can override it in either
                                // direction (a deposit refund is negative
                                // Rental Income).
                                category: e.target.value
                                  ? isIncome
                                    ? "Rental Income"
                                    : "Other"
                                  : null,
                              })
                            }
                            className="input py-1"
                          >
                            <option value="">— none —</option>
                            {properties?.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name}
                              </option>
                            ))}
                          </select>
                          {/* Category refinement — shown for EVERY assigned row,
                              income and expense alike, with the full vocabulary.
                              Gating this on !isIncome meant a negative amount could
                              only ever be an expense: a returned security deposit
                              (negative Rental Income) had no selectable label, and a
                              refunded property expense (positive) got no dropdown at
                              all. Sign is a default, not a constraint. */}
                          {assigned && (
                            <select
                              value={
                                tx.property_category ??
                                (isIncome ? "Rental Income" : "Other")
                              }
                              onChange={(e) =>
                                reassign.mutate({
                                  txId: tx.id,
                                  propertyId: tx.property_id,
                                  category: e.target.value,
                                })
                              }
                              className="input py-1"
                            >
                              {propertyCategories.map((c) => (
                                <option key={c} value={c}>
                                  {c}
                                </option>
                              ))}
                            </select>
                          )}
                          {/* source badge: manual override / Monarch tag / auto rule */}
                          {assigned &&
                            (() => {
                              const src =
                                tx.property_source === "manual"
                                  ? { label: "manual", title: "Manually assigned — survives re-sync", cls: "bg-[var(--blue-glow)] text-[var(--blue)]" }
                                  : tx.property_source === "monarch_tag"
                                    ? { label: "tag", title: "Classified from a Monarch tag", cls: "bg-[var(--green-glow)] text-[var(--green)]" }
                                    : { label: "auto", title: "Auto-classified by a merchant rule", cls: "bg-[var(--bg-secondary)] text-[var(--text-secondary)]" };
                              return (
                                <span
                                  title={src.title}
                                  className={`text-[9px] px-1 py-0.5 rounded uppercase tracking-wide shrink-0 ${src.cls}`}
                                >
                                  {src.label}
                                </span>
                              );
                            })()}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {data && data.transactions.length === 0 && (
            <div className="flex items-center justify-center h-[160px] text-[var(--text-secondary)] text-sm">
              No transactions match these filters.
            </div>
          )}
          {!data && (
            <div className="flex items-center justify-center h-[160px] text-[var(--text-secondary)] text-sm">
              Loading…
            </div>
          )}

          {/* ── Pagination footer ─────────────────────────────────────── */}
          <div className="flex items-center justify-between px-4 py-2.5 border-t border-[var(--border)] text-xs text-[var(--text-secondary)]">
            <span>
              {showingFrom.toLocaleString()}–{showingTo.toLocaleString()} of{" "}
              {total.toLocaleString()}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
                disabled={offset === 0}
                className="px-2.5 py-1 rounded border border-[var(--border)] disabled:opacity-40 hover:bg-[var(--bg-secondary)] transition-colors"
              >
                ← Prev
              </button>
              <button
                onClick={() => setOffset(offset + PAGE)}
                disabled={showingTo >= total}
                className="px-2.5 py-1 rounded border border-[var(--border)] disabled:opacity-40 hover:bg-[var(--bg-secondary)] transition-colors"
              >
                Next →
              </button>
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
}

function SummaryChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "green" | "red";
}) {
  const color =
    tone === "green"
      ? "var(--green)"
      : tone === "red"
        ? "var(--red)"
        : "var(--text-primary)";
  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-1.5 min-w-[92px]">
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
        {label}
      </div>
      <div className="text-sm font-mono font-medium" style={{ color }}>
        {value}
      </div>
    </div>
  );
}
