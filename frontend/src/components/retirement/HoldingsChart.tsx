// Chart 3: what do you own along the way? Accounts stacked month by month; the
// gray bands (property, private investments) are owned but not spendable. The
// plan picked under "Compare with" draws as one thin line, so the difference is
// visible, not just stated.

import { useMemo, useState } from "react";
import {
  COLORS, HOLDING_ORDER, holdingValue, holdingsStory, mergeMilestones, type HoldingKey, type Plan,
} from "./model";
import { midClip, money, monthName, numberWord, wholeAge } from "./format";
import { DataTable, GridY, HoverLayer } from "./chartKit";
import { clamp, cursorKeys, niceScale, useWidth } from "./chartMath";
import MilestoneStrip from "./MilestoneStrip";
import { placeFlags, stripHeight } from "./flags";
import Readout, { type ReadoutRow } from "./Readout";

const NAMES: Record<HoldingKey, string> = {
  brk: "Brokerage", rrif: "RRIF", ira: "IRAs", cash: "Cash", home: "Property", priv: "Private investments",
};
const GRAY = new Set<HoldingKey>(["home", "priv"]);

export default function HoldingsChart({ plan, compare, cursor, setCursor }: {
  plan: Plan;
  compare: Plan | null;
  cursor: number;
  setCursor: (m: number) => void;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [table, setTable] = useState(false);
  const rows = plan.rows;
  const keys = useMemo(() => HOLDING_ORDER.filter((k) => k === "cash" || plan.present.has(k)), [plan.present]);
  const narrow = width < 560;

  // What 59½ opens, under the plan's IRA rule (projection.open_all_iras_after_59_5).
  const label59 = !plan.hasIraA || (plan.openAllIras && (plan.iraAOpenMonth ?? 0) <= plan.age59Month) ? "59½: IRAs open"
    : plan.openAllIras ? "59½: other IRAs open" : "59½: one IRA opens";
  const g = useMemo(() => {
    const W = Math.max(300, width - 12);
    const L = narrow ? 48 : 58, R = narrow ? 10 : 140, T = 22, plotH = narrow ? 240 : 300;
    const x0 = L, x1 = W - R, n = rows.length;
    const x = (i: number) => x0 + (n > 1 ? (i / (n - 1)) * (x1 - x0) : 0);
    const totals = rows.map((r) => keys.reduce((s, k) => s + holdingValue(r, k), 0));
    const cmpTotals = compare ? compare.rows.map((r) => r.total - Math.min(0, r.cash)) : [];
    const s = niceScale(Math.max(1, ...totals, ...cmpTotals) * 1.03, 4);
    const y = (v: number) => T + plotH - (v / s.max) * plotH;
    const cum = rows.map(() => 0);
    const bands: { k: HoldingKey; d: string; edge: string; mid: number; h: number; v: number }[] = [];
    for (const k of keys) {
      const lower = cum.slice();
      rows.forEach((r, i) => { cum[i] += holdingValue(r, k); });
      if (Math.max(...rows.map((r) => holdingValue(r, k))) < 1) continue;
      const top = cum.map((v, i) => `${x(i)},${y(v)}`);
      const bot = lower.map((v, i) => `${x(i)},${y(v)}`).reverse();
      bands.push({
        k, d: "M" + top.join(" L") + " L" + bot.join(" L") + " Z", edge: "M" + top.join(" L"),
        mid: y((lower[n - 1] + cum[n - 1]) / 2), h: y(lower[n - 1]) - y(cum[n - 1]), v: holdingValue(rows[n - 1], k),
      });
    }
    const span = (rows[n - 1]?.m ?? 0) / 12;
    const every = span > 25 ? 5 : span > 10 ? 2 : 1;
    const flags = plan.milestones.filter((m) => m.kind === "age" || m.kind === "warning" || m.label === "72(t) account opens");
    const sold = plan.milestones.filter((m) => m.kind === "sale");
    if (sold.length) {
      const lastSale = sold[sold.length - 1];
      flags.push({ ...lastSale, label: sold.length === 1 ? lastSale.label
        : sold.length === 2 ? "Both properties sold" : `All ${numberWord(sold.length)} properties sold` });
    }
    const merged = mergeMilestones(flags.sort((a, b) => a.month - b.month), 0);
    const placed = placeFlags(merged.map((f) => ({ m: f.m, text: f.text.replace(/^59½$/, label59), warn: f.warn })),
      (m) => x(clamp(m, 0, n - 1)), W - 4);
    const msY = T + plotH + 60;
    return { W, H: msY + stripHeight(placed), T, plotH, x0, x1, x, totals, cmpTotals, s, y, bands, every, placed, msY };
  }, [width, narrow, rows, keys, compare, plan.milestones, label59]);

  if (!rows.length) return null;
  const i = clamp(cursor, 0, rows.length - 1);
  const r = rows[i];
  const n = rows.length;

  // Right-edge labels for bands tall enough to own one, top band first.
  const labels: { k: HoldingKey; y: number; v: number }[] = [];
  let lastY = -1e9;
  for (const b of [...g.bands].reverse()) {
    if (b.h < 14) continue;
    const yy = Math.max(b.mid + 4, lastY + 30);
    labels.push({ k: b.k, y: yy, v: b.v });
    lastY = yy;
  }
  const cmpEnd = compare ? g.cmpTotals[g.cmpTotals.length - 1] : null;

  const readRows: ReadoutRow[] = [...keys].reverse().map((k) => ({
    color: COLORS[k], value: money(holdingValue(r, k)),
    name: NAMES[k] + (GRAY.has(k) ? ", not spendable" : ""), muted: holdingValue(r, k) < 1,
  }));
  readRows.push({ value: money(g.totals[i]), name: "Total", total: true });
  if (compare) {
    const cr = compare.rows[Math.min(i, compare.rows.length - 1)];
    if (cr) readRows.push({ key: "ring", value: money(cr.total - Math.min(0, cr.cash)), name: compare.name, muted: true });
  }

  return (
    <>
      <div className="rt-board">
        <div ref={ref} className="rt-plot" tabIndex={0}
          aria-label={`What you own, by account, until age ${plan.endAge}. Use the arrow keys to move through time.`}
          onKeyDown={cursorKeys(1, i, n - 1, setCursor)}>
          {table ? (
            <DataTable
              head={["Year end", "Age", ...keys.map((k) => NAMES[k]), "Total", "Spendable"]}
              rows={plan.years.map((t) => {
                const e = t.end;
                const tot = keys.reduce((s, k) => s + holdingValue(e, k), 0);
                return [t.y, wholeAge(e.a), ...keys.map((k) => holdingValue(e, k)), tot, tot - e.home - e.priv];
              })}
            />
          ) : (
            <svg width={g.W} height={g.H} role="img" aria-label={`What you own, by account, until age ${plan.endAge}`}>
              <GridY x0={g.x0} x1={g.x1} y={g.y} scale={g.s} />
              {g.bands.map((b) => (
                <g key={b.k}>
                  <path d={b.d} fill={COLORS[b.k]} fillOpacity={GRAY.has(b.k) ? 1 : 0.88} />
                  <path d={b.edge} fill="none" stroke="#f5f0e6" strokeWidth={1.5} />
                </g>
              ))}
              {compare && g.cmpTotals.length > 1 && (
                <>
                  <path
                    d={"M" + g.cmpTotals.map((v, k) => `${g.x(Math.min(k, n - 1))},${g.y(v)}`).join(" L")}
                    fill="none" stroke="#1c1b19" strokeWidth={1.5} strokeOpacity={0.6}
                  />
                  {!narrow && cmpEnd != null && (
                    <text x={g.x1 + 10} y={Math.min(g.y(cmpEnd), g.y(g.totals[n - 1])) - 22} className="lbl2">
                      {midClip(compare.name)} {money(cmpEnd)}
                    </text>
                  )}
                </>
              )}
              {!narrow && labels.map((l) => (
                <g key={l.k}>
                  <text x={g.x1 + 10} y={l.y - 7} className="lbl2">{NAMES[l.k]}</text>
                  <text x={g.x1 + 10} y={l.y + 8} className="lbl">{money(l.v)}</text>
                </g>
              ))}
              {rows.map((row, k) =>
                row.d.endsWith("-01") && Number(row.d.slice(0, 4)) % g.every === 0 && k > 6 && k < n - 6 ? (
                  <g key={row.m}>
                    <text x={g.x(k)} y={g.T + g.plotH + 18} className="ax" textAnchor="middle">{row.d.slice(0, 4)}</text>
                    <text x={g.x(k)} y={g.T + g.plotH + 32} className="ax2" textAnchor="middle">age {wholeAge(row.a)}</text>
                  </g>
                ) : null,
              )}
              <MilestoneStrip placed={g.placed} yTop={g.T + g.plotH} yBase={g.msY} onPick={setCursor} />
              <g pointerEvents="none">
                <line x1={g.x(i)} x2={g.x(i)} y1={g.T} y2={g.T + g.plotH} stroke="#1c1b19" strokeWidth={1} strokeOpacity={0.55} />
                <circle cx={g.x(i)} cy={g.y(g.totals[i])} r={4.5} fill="#1c1b19" stroke="#f5f0e6" strokeWidth={2} />
              </g>
              <HoverLayer x0={g.x0} x1={g.x1} top={g.T} bottom={g.T + g.plotH}
                toIndex={(px) => clamp(Math.round(((px - g.x0) / Math.max(1, g.x1 - g.x0)) * (n - 1)), 0, n - 1)}
                onIndex={setCursor} />
            </svg>
          )}
        </div>
        <Readout
          when={`${monthName(r.d)}, age ${wholeAge(r.a)}`}
          story={holdingsStory(plan, r)}
          rows={readRows}
          hint={plan.present.has("home") || plan.present.has("priv") ? "Gray bands are things you own but can't spend from." : undefined}
        />
      </div>
      <div className="rt-tools">
        <button type="button" className="rt-linkbtn" onClick={() => setTable((v) => !v)}>
          {table ? "Show as a chart" : "Show as a table"}
        </button>
      </div>
    </>
  );
}
