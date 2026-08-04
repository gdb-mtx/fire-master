import { useState, useEffect, useCallback } from "react";
import { useParams, Link } from "react-router";
import { useAccountDetail, useUpdateAccountEnrichment } from "../api/queries";
import LightweightChart from "../charts/LightweightChart";
import Layout from "../components/Layout";
import { formatCurrency } from "../utils/formatting";
import { accountTypeLabel, accountTypeColor } from "../utils/accountTypes";


interface EnrichmentForm {
  account_type: string;
  fire_role: string;
  notes: string;
  strategy: string;
  tags: string[];
  target_balance: string;
  target_allocation_pct: string;
}

export default function AccountDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: account, isLoading } = useAccountDetail(id ?? null);
  const enrichmentMutation = useUpdateAccountEnrichment();

  const [form, setForm] = useState<EnrichmentForm>({
    account_type: "",
    fire_role: "",
    notes: "",
    strategy: "",
    tags: [],
    target_balance: "",
    target_allocation_pct: "",
  });
  const [tagInput, setTagInput] = useState("");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saved">("idle");
  const [initialized, setInitialized] = useState(false);

  // Initialize form from account data
  useEffect(() => {
    if (account && !initialized) {
      setForm({
        account_type: account.account_type ?? "",
        fire_role: account.fire_role ?? "",
        notes: account.notes ?? "",
        strategy: account.strategy ?? "",
        tags: account.tags ?? [],
        target_balance: account.target_balance
          ? String(account.target_balance / 100)
          : "",
        target_allocation_pct: account.target_allocation_pct
          ? String(account.target_allocation_pct)
          : "",
      });
      setInitialized(true);
    }
  }, [account, initialized]);

  const handleSave = useCallback(() => {
    if (!id) return;
    const data: Record<string, unknown> = {};

    if (form.account_type !== (account?.account_type ?? ""))
      data.account_type = form.account_type || null; // null = clear override, re-auto on next sync
    if (form.fire_role !== (account?.fire_role ?? ""))
      data.fire_role = form.fire_role || null;
    if (form.notes !== (account?.notes ?? ""))
      data.notes = form.notes || null;
    if (form.strategy !== (account?.strategy ?? ""))
      data.strategy = form.strategy || null;
    if (JSON.stringify(form.tags) !== JSON.stringify(account?.tags ?? []))
      data.tags = form.tags.length > 0 ? form.tags : null;

    const targetBalCents = form.target_balance
      ? Math.round(parseFloat(form.target_balance) * 100)
      : null;
    if (targetBalCents !== (account?.target_balance ?? null))
      data.target_balance = targetBalCents;

    const targetPct = form.target_allocation_pct
      ? parseFloat(form.target_allocation_pct)
      : null;
    if (targetPct !== (account?.target_allocation_pct ?? null))
      data.target_allocation_pct = targetPct;

    if (Object.keys(data).length === 0) return;

    enrichmentMutation.mutate(
      { id, data },
      {
        onSuccess: () => {
          setSaveStatus("saved");
          setTimeout(() => setSaveStatus("idle"), 2000);
        },
      }
    );
  }, [id, form, account, enrichmentMutation]);

  const addTag = useCallback(() => {
    const tag = tagInput.trim().toLowerCase();
    if (tag && !form.tags.includes(tag)) {
      setForm((f) => ({ ...f, tags: [...f.tags, tag] }));
    }
    setTagInput("");
  }, [tagInput, form.tags]);

  const removeTag = useCallback((tag: string) => {
    setForm((f) => ({ ...f, tags: f.tags.filter((t) => t !== tag) }));
  }, []);

  if (isLoading || !account) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-96 text-[var(--text-secondary)]">
          Loading...
        </div>
      </Layout>
    );
  }

  const typeLabel = accountTypeLabel(account.account_type);
  const typeColor = accountTypeColor(account.account_type);

  const chartData = account.balance_history.map((p) => ({
    time: p.date,
    value: p.balance,
  }));

  return (
    <Layout>
      <div className="space-y-6">
        {/* Back link */}
        <Link
          to="/assets"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
        >
          <span className="text-xs">&larr;</span> Asset Hub
        </Link>

        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <span
                className="text-[10px] font-semibold uppercase tracking-widest px-2 py-0.5 rounded"
                style={{
                  color: typeColor,
                  background: `${typeColor}15`,
                  border: `1px solid ${typeColor}30`,
                }}
              >
                {typeLabel}
              </span>
              <span
                className={`w-2 h-2 rounded-full ${
                  account.is_asset ? "bg-[var(--green)]" : "bg-[var(--red)]"
                }`}
              />
              <span className="text-xs text-[var(--text-secondary)]">
                {account.is_asset ? "Asset" : "Liability"}
              </span>
            </div>
            <h2 className="text-2xl font-semibold text-[var(--text-primary)] tracking-tight">
              {account.name}
            </h2>
            {account.institution && (
              <p className="text-sm text-[var(--text-secondary)] mt-0.5">
                {account.institution}
              </p>
            )}
          </div>
          <div className="text-right">
            <p className="text-sm text-[var(--text-secondary)] mb-1">
              Current Balance
            </p>
            <p
              className="text-3xl font-bold font-mono tracking-tight"
              style={{
                color: account.is_asset
                  ? "var(--text-primary)"
                  : "var(--red)",
              }}
            >
              {account.is_asset ? "" : "-"}
              {formatCurrency(Math.abs(account.balance))}
            </p>
          </div>
        </div>

        {/* Two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left column — chart + enrichment */}
          <div className="lg:col-span-2 space-y-6">
            {/* Balance chart */}
            <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">
                Balance History
              </h3>
              {chartData.length > 0 ? (
                <LightweightChart data={chartData} height={280} disableScroll />
              ) : (
                <div className="flex items-center justify-center h-[280px] text-[var(--text-secondary)] text-sm">
                  No balance history available.
                </div>
              )}
            </div>

            {/* Enrichment form */}
            <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
              <div className="flex items-center justify-between mb-5">
                <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                  FIRE Strategy & Notes
                </h3>
                <div className="flex items-center gap-3">
                  {saveStatus === "saved" && (
                    <span className="text-xs text-[var(--green)]">
                      Saved
                    </span>
                  )}
                  <button
                    onClick={handleSave}
                    disabled={enrichmentMutation.isPending}
                    className="px-4 py-1.5 text-xs font-medium bg-[var(--blue)] text-white rounded hover:brightness-110 transition-all disabled:opacity-50"
                  >
                    {enrichmentMutation.isPending ? "Saving..." : "Save"}
                  </button>
                </div>
              </div>

              <div className="space-y-5">
                {/* Account Type (manual override — survives Monarch sync) */}
                <div>
                  <label className="block text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
                    Account Type
                  </label>
                  <select
                    value={form.account_type}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, account_type: e.target.value }))
                    }
                    className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--blue)] transition-colors"
                  >
                    {["checking", "savings", "401k", "ira", "roth_ira", "taxable", "hsa", "real_estate", "crypto", "CREDIT_CARD", "VEHICLE", "PRIVATE", "other"].map((t) => (
                      <option key={t} value={t}>{t.replace(/_/g, " ").toLowerCase()}</option>
                    ))}
                  </select>
                  <span className="text-[10px] text-[var(--text-secondary)] mt-1 block">
                    Changing this overrides Monarch's type and survives future syncs
                  </span>
                </div>

                {/* FIRE Role */}
                <div>
                  <label className="block text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
                    FIRE Role
                  </label>
                  <input
                    type="text"
                    value={form.fire_role}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, fire_role: e.target.value }))
                    }
                    placeholder="e.g. core retirement, bridge account, emergency fund"
                    className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] placeholder:opacity-50 focus:outline-none focus:border-[var(--blue)] transition-colors"
                  />
                </div>

                {/* Notes */}
                <div>
                  <label className="block text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
                    Notes
                  </label>
                  <textarea
                    value={form.notes}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, notes: e.target.value }))
                    }
                    placeholder="Add notes about this account — employer match, vesting schedule, rules, anything you want to remember..."
                    rows={4}
                    className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] placeholder:opacity-50 focus:outline-none focus:border-[var(--blue)] transition-colors resize-y"
                  />
                </div>

                {/* Strategy */}
                <div>
                  <label className="block text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
                    Strategy
                  </label>
                  <textarea
                    value={form.strategy}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, strategy: e.target.value }))
                    }
                    placeholder="Investment strategy, contribution plan, rebalancing notes..."
                    rows={3}
                    className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] placeholder:opacity-50 focus:outline-none focus:border-[var(--blue)] transition-colors resize-y"
                  />
                </div>

                {/* Tags */}
                <div>
                  <label className="block text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
                    Tags
                  </label>
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {form.tags.map((tag) => (
                      <span
                        key={tag}
                        className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-primary)]"
                      >
                        {tag}
                        <button
                          onClick={() => removeTag(tag)}
                          className="text-[var(--text-secondary)] hover:text-[var(--red)] transition-colors ml-0.5"
                        >
                          &times;
                        </button>
                      </span>
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addTag();
                        }
                      }}
                      placeholder="Add tag and press Enter"
                      className="flex-1 bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-1.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] placeholder:opacity-50 focus:outline-none focus:border-[var(--blue)] transition-colors"
                    />
                    <button
                      onClick={addTag}
                      className="px-3 py-1.5 text-xs bg-[var(--bg-secondary)] border border-[var(--border)] rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--blue)] transition-colors"
                    >
                      Add
                    </button>
                  </div>
                </div>

                {/* Targets row */}
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
                      Target Balance
                    </label>
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-[var(--text-secondary)]">
                        $
                      </span>
                      <input
                        type="number"
                        value={form.target_balance}
                        onChange={(e) =>
                          setForm((f) => ({
                            ...f,
                            target_balance: e.target.value,
                          }))
                        }
                        placeholder="0"
                        className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded pl-7 pr-3 py-2 text-sm font-mono text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] placeholder:opacity-50 focus:outline-none focus:border-[var(--blue)] transition-colors"
                      />
                    </div>
                    {form.target_balance && account.balance > 0 && (
                      <div className="mt-1.5">
                        <div className="h-1 rounded-full bg-[var(--bg-secondary)] overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{
                              width: `${Math.min(100, (account.balance / (parseFloat(form.target_balance) || 1)) * 100)}%`,
                              background:
                                account.balance >=
                                parseFloat(form.target_balance)
                                  ? "var(--green)"
                                  : "var(--blue)",
                            }}
                          />
                        </div>
                        <p className="text-[10px] text-[var(--text-secondary)] mt-0.5">
                          {Math.round(
                            (account.balance /
                              (parseFloat(form.target_balance) || 1)) *
                              100
                          )}
                          % of target
                        </p>
                      </div>
                    )}
                  </div>
                  <div>
                    <label className="block text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
                      Target Allocation
                    </label>
                    <div className="relative">
                      <input
                        type="number"
                        value={form.target_allocation_pct}
                        onChange={(e) =>
                          setForm((f) => ({
                            ...f,
                            target_allocation_pct: e.target.value,
                          }))
                        }
                        placeholder="0"
                        step="0.1"
                        className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 pr-7 py-2 text-sm font-mono text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] placeholder:opacity-50 focus:outline-none focus:border-[var(--blue)] transition-colors"
                      />
                      <span className="absolute right-3 top-1/2 -translate-y-1/2 text-sm text-[var(--text-secondary)]">
                        %
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Right column — metadata + transactions */}
          <div className="space-y-6">
            {/* Account info */}
            <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">
                Account Info
              </h3>
              <div className="space-y-2.5 text-sm">
                <div className="flex justify-between">
                  <span className="text-[var(--text-secondary)]">Type</span>
                  <span className="text-[var(--text-primary)]">
                    {typeLabel}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[var(--text-secondary)]">Source</span>
                  <span className="text-[var(--text-primary)] capitalize">
                    {account.source}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[var(--text-secondary)]">
                    Net Worth
                  </span>
                  <span className="text-[var(--text-primary)]">
                    {account.include_in_net_worth ? "Included" : "Excluded"}
                  </span>
                </div>
                {account.last_synced_at && (
                  <div className="flex justify-between">
                    <span className="text-[var(--text-secondary)]">
                      Last Sync
                    </span>
                    <span className="text-[var(--text-primary)] text-xs">
                      {new Date(account.last_synced_at).toLocaleDateString()}
                    </span>
                  </div>
                )}
              </div>
            </div>

            {/* Recent transactions */}
            <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">
                Recent Transactions
                <span className="ml-1.5 text-xs font-normal">
                  ({account.recent_transactions.length})
                </span>
              </h3>
              {account.recent_transactions.length > 0 ? (
                <div className="space-y-0.5 max-h-[480px] overflow-y-auto">
                  {account.recent_transactions.map((tx) => (
                    <div
                      key={tx.id}
                      className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-[var(--bg-secondary)] transition-colors"
                    >
                      <div className="min-w-0 flex-1 mr-3">
                        <p className="text-xs text-[var(--text-primary)] truncate">
                          {tx.merchant ?? tx.category ?? "Unknown"}
                        </p>
                        <p className="text-[10px] text-[var(--text-secondary)]">
                          {tx.date}
                          {tx.category && tx.merchant && (
                            <span> &middot; {tx.category}</span>
                          )}
                        </p>
                      </div>
                      <span
                        className="text-xs font-mono shrink-0"
                        style={{
                          color:
                            tx.amount >= 0 ? "var(--green)" : "var(--red)",
                        }}
                      >
                        {tx.amount >= 0 ? "+" : ""}
                        {formatCurrency(Math.abs(tx.amount))}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-[var(--text-secondary)] text-center py-6">
                  No recent transactions.
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
}
