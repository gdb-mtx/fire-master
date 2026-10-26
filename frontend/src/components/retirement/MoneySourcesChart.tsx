// Chart 2: where does the money come from each year? One bar per calendar year,
// stacked by source; the black tick is that year's spending. A lone windfall
// year is clipped (with a break mark and its true total) so it can't flatten
// every other bar.

import { useMemo, useState } from "react";
import { COLORS, SOURCE_ORDER, sourceValue, sourceWord, sourcesStory, type Plan, type SourceKey } from "./model";
import { money, wholeAge } from "./format";
import { DataTable, GridY, HoverLayer } from "./chartKit";
import { clamp, cursorKeys, niceScale, useWidth } from "./chartMath";
import Readout, { type ReadoutRow } from "./Readout";

const NAMES: Record<SourceKey, string> = {
  income: "Income", ss: "Social Security", ira: "From the IRAs", rrif: "From the RRIF",
  brk: "From the brokerage", cash: "From cash", one: "One-time money",
};

export default function MoneySourcesChart({ plan, cursor, setCursor }: {
  plan: Plan;
  cursor: number;
  setCursor: (m: number) => void;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [table, setTable] = useState(false);
  const ys = plan.years;
  const keys = useMemo(() => SOURCE_ORDER.filter((k) => k === "cash" || plan.present.has(k)), [plan.present]);
  const narrow = width < 560;

  const g = useMemo(() => {
    const W = Math.max(300, width - 12);
    const L = narrow ? 48 : 58, R = 16, T = 22, plotH = narrow ? 240 : 300;
    const x0 = L, x1 = W - R, step = (x1 - x0) / Math.max(1, ys.length);
    const xc = (i: number) => x0 + (i + 0.5) * step;
    const tot = ys.map((t) => keys.reduce((s, k) => s + sourceValue(t, k), 0));
    const ranked = [...tot, ...ys.map((t) => t.spend)].sort((a, b) => b - a);
    const clip = ranked.length > 2 && ranked[0] > 1.5 * ranked[1];
    const s = niceScale(Math.max(1, clip ? ranked[1] * 1.08 : ranked[0] * 1.02), 4);
    const y = (v: number) => T + plotH - (Math.min(v, s.max) / s.max) * plotH;
    const everyN = ys.length > 20 ? 5 : ys.length > 10 ? 2 : 1;
    return { W, H: T + plotH + 46, T, plotH, x0, x1, step, xc, tot, s, y, everyN };
  }, [width, narrow, ys, keys]);

  if (!ys.length) return null;
  const cy = Number((plan.rows[clamp(cursor, 0, plan.rows.length - 1)]?.d ?? "").slice(0, 4));
  let i = ys.findIndex((t) => t.y === cy);
  if (i < 0) i = cy < ys[0].y ? 0 : ys.length - 1;
  const t = ys[i];
  const bw = Math.max(3, g.step * 0.66);
  const lastMonth = plan.rows[plan.rows.length - 1].m;

  const readRows: ReadoutRow[] = [...keys].reverse().map((k) => ({
    color: COLORS[k],
    value: money(sourceValue(t, k)),
    name: k === "ira" && t.sepp > 0 ? `From the IRAs, ${money(t.sepp)} of it 72(t)` : k === "income" ? capitalizeFirst(sourceWord(plan, "income")) : NAMES[k],
    muted: sourceValue(t, k) <= 50,
  }));
  readRows.push({ key: "tick", value: money(t.spend), name: "Spending" });
  if (t.invested > 500) readRows.push({ value: money(t.invested), name: "Put into the brokerage", total: true, muted: true });
  if (t.toCash > 500) readRows.push({ value: money(t.toCash), name: "Added to cash", total: true, muted: true });

  return (
    <>
      <div className="rt-board">
        <div ref={ref} className="rt-plot" tabIndex={0}
          aria-label="Where the spending money comes from, year by year. Use the arrow keys to move between years."
          onKeyDown={cursorKeys(12, cursor, lastMonth, setCursor)}>
          {table ? (
            <DataTable
              head={["Year", "Age", "Spending", ...[...keys].map((k) => (k === "income" ? capitalizeFirst(sourceWord(plan, "income")) : NAMES[k].replace(/^From the |^From /, "").replace(/^./, (c) => c.toUpperCase()))), "Into brokerage", "Added to cash"]}
              rows={ys.map((y) => [y.y, wholeAge(y.a), y.spend, ...keys.map((k) => sourceValue(y, k)), y.invested, y.toCash])}
            />
          ) : (
            <svg width={g.W} height={g.H} role="img" aria-label="Sources of spending money by year">
              <GridY x0={g.x0} x1={g.x1} y={g.y} scale={g.s} />
              {ys.map((yr, k) => {
                let base = 0;
                const over = g.tot[k] > g.s.max;
                return (
                  <g key={yr.y} className={"bar" + (k === i ? " on" : "")}>
                    {keys.map((key) => {
                      const v = sourceValue(yr, key);
                      if (v <= 0 || base >= g.s.max) { base += Math.max(0, v); return null; }
                      const ya = g.y(base + v), yb = g.y(base);
                      base += v;
                      return <rect key={key} x={g.xc(k) - bw / 2} y={ya + 1} width={bw} height={Math.max(0.5, yb - ya - 1)} fill={COLORS[key]} rx={1.5} />;
                    })}
                    {over && (
                      <>
                        <path d={`M${g.xc(k) - bw / 2 - 2},${g.y(g.s.max) + 13} L${g.xc(k) + bw / 2 + 2},${g.y(g.s.max) + 7} L${g.xc(k) + bw / 2 + 2},${g.y(g.s.max) + 11} L${g.xc(k) - bw / 2 - 2},${g.y(g.s.max) + 17} Z`} fill="#f5f0e6" />
                        <text x={g.xc(k)} y={g.y(g.s.max) - 6} className="lbl" textAnchor="middle">{money(g.tot[k])}</text>
                      </>
                    )}
                    <rect x={g.xc(k) - bw / 2 - 4} y={g.y(yr.spend) - 1.25} width={bw + 8} height={2.5} fill="#1c1b19" rx={1.25} />
                  </g>
                );
              })}
              {ys.map((yr, k) =>
                k === 0 || k === ys.length - 1 || (yr.y % g.everyN === 0 && k > 1 && k < ys.length - 2) ? (
                  <g key={yr.y}>
                    <text x={g.xc(k)} y={g.T + g.plotH + 18} className="ax" textAnchor="middle">{yr.y}</text>
                    <text x={g.xc(k)} y={g.T + g.plotH + 32} className="ax2" textAnchor="middle">age {wholeAge(yr.a)}</text>
                  </g>
                ) : null,
              )}
              <rect x={g.xc(i) - g.step / 2} y={g.T - 6} width={g.step} height={g.plotH + 6} fill="#1c1b19" fillOpacity={0.05} rx={4} pointerEvents="none" />
              <HoverLayer x0={g.x0} x1={g.x1} top={g.T} bottom={g.T + g.plotH}
                toIndex={(px) => clamp(Math.floor((px - g.x0) / g.step), 0, ys.length - 1)}
                onIndex={(k) => setCursor(ys[k].m0 + Math.min(6, ys[k].n - 1))} />
            </svg>
          )}
        </div>
        <Readout
          when={`${t.y}, age ${wholeAge(t.a)}`}
          story={sourcesStory(plan, t)}
          rows={readRows}
          hint="Each bar adds up to what came in that year. Taller than the black line means money was left over."
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

const capitalizeFirst = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
