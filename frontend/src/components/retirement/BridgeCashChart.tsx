// Chart 1: does cash hold through the next five years? Cash (and the cushion,
// when the plan keeps one) on top; money taken out of savings each month below,
// with money put into the brokerage hanging under the baseline.

import { useId, useMemo } from "react";
import { BAD, COLORS, flagWorthy, mergeMilestones, bridgeStory, zeroSpells, type Plan } from "./model";
import { money, monthName, wholeAge } from "./format";
import { GridY, HoverLayer } from "./chartKit";
import { clamp, cursorKeys, niceScale, useWidth } from "./chartMath";
import MilestoneStrip from "./MilestoneStrip";
import { placeFlags, stripHeight } from "./flags";
import Readout, { type ReadoutRow } from "./Readout";

const MONTHS = 60;

export default function BridgeCashChart({ plan, cursor, setCursor }: {
  plan: Plan;
  cursor: number;
  setCursor: (m: number) => void;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const uid = useId().replace(/:/g, "");
  const rows = useMemo(() => plan.rows.slice(0, MONTHS), [plan.rows]);
  const narrow = width < 560;

  const g = useMemo(() => {
    const W = Math.max(300, width - 12);
    const L = narrow ? 48 : 58, R = narrow ? 10 : 118, T = 22, topH = narrow ? 180 : 214, gap = 46;
    const x0 = L, x1 = W - R, step = (x1 - x0) / Math.max(1, rows.length);
    const xc = (i: number) => x0 + (i + 0.5) * step;
    const cushion = rows.map((r) => (plan.cushionOn ? r.cushion : 0));
    const minCash = Math.min(0, ...rows.map((r) => r.cash));
    const sTop = niceScale(Math.max(1, ...rows.map((r) => r.cash), ...cushion) * 1.04, 4, minCash * 1.04);
    const yT = (v: number) => T + topH - ((v - sTop.min) / (sTop.max - sTop.min)) * topH;
    const out = rows.map((r) => r.fBrk + r.fRrif + r.fIra);
    const inv = rows.map((r) => r.invested);
    const hasInv = inv.some((v) => v > 1);
    const botH = hasInv ? 104 : 78;
    // A single big investment (a windfall swept to the brokerage) would flatten
    // every monthly withdrawal; below the baseline the scale stops at twice the
    // largest withdrawal and taller bars are clipped with their true amount.
    // Likewise a single month that pays back a cash hole would flatten every
    // ordinary withdrawal: when the largest is over three times the next, the
    // scale stops at twice the next and that bar is clipped with its amount.
    const ranked = [...out].sort((a, b) => b - a);
    const upCap = ranked.length > 1 && ranked[0] > 3 * Math.max(ranked[1], 1) && ranked[0] > 1000
      ? Math.max(ranked[1] * 2, 1000) : Infinity;
    const maxOut = Math.max(1, Math.min(ranked[0] ?? 0, upCap));
    const invCap = hasInv ? Math.min(Math.max(...inv), Math.max(maxOut * 2, 1000)) : 0;
    const sBot = niceScale(maxOut * 1.05, 2, -invCap * 1.05);
    const b0 = T + topH + gap;
    const yB = (v: number) => b0 + botH - ((v - sBot.min) / (sBot.max - sBot.min)) * botH;
    const axY = b0 + botH + 18;
    const msY = axY + 36;
    const flags = mergeMilestones(plan.milestones.filter((x) => x.month < rows.length && flagWorthy(x)));
    const placed = placeFlags(flags.map((f) => ({ m: f.m, text: f.text, warn: f.warn })), (m) => xc(Math.min(m, rows.length - 1)), W - 4);
    const H = msY + stripHeight(placed);
    return { W, H, L, R, T, topH, x0, x1, step, xc, cushion, sTop, yT, sBot, yB, b0, botH, axY, msY, placed, hasInv, out, inv, upCap };
  }, [width, narrow, rows, plan.cushionOn, plan.milestones]);

  if (!rows.length) return null;
  const i = clamp(cursor, 0, rows.length - 1);
  const inRange = cursor < rows.length;
  const r = rows[i];
  const c = g.cushion[i];
  const y0 = g.yT(0);

  const pts = rows.map((row, k) => [g.xc(k), g.yT(row.cash)] as const);
  const line = "M" + pts.map((p) => p.join(",")).join(" L");
  const area = `M${pts[0][0]},${y0} ` + pts.map((p) => `L${p[0]},${p[1]}`).join(" ") + ` L${pts[pts.length - 1][0]},${y0} Z`;
  let cushionPath = "";
  if (plan.cushionOn) {
    g.cushion.forEach((v, k) => {
      const xa = g.x0 + k * g.step, xb = xa + g.step, yy = g.yT(v);
      cushionPath += (k ? ` L${xa},${yy}` : `M${xa},${yy}`) + ` L${xb},${yy}`;
    });
  }
  const bw = Math.max(2, g.step * 0.62);
  // Cash resting at $0: say on the line itself what savings pay each month.
  const zeros = zeroSpells(plan, rows, 3).map((z) => {
    const xa = g.xc(z.first.m) - g.step / 2, xb = g.xc(z.last.m) + g.step / 2;
    const avg = money(z.total / z.months);
    const room = g.x1 - xa;
    const text = [`Cash at $0: savings pay ${avg} a month`, `$0: savings pay ${avg}/mo`]
      .find((t) => t.length * 6.3 <= Math.max(room, xb - xa));
    const below = y0 - g.T < 26;
    return { key: z.first.m, xa, xb, text, tx: Math.min(xa, g.x1 - (text?.length ?? 0) * 6.3), ty: below ? y0 + 15 : y0 - 8 };
  });
  const last = rows.length - 1;
  const yc = g.yT(rows[last].cash), yk = g.yT(g.cushion[last]);
  const close = Math.abs(yc - yk) < 16;

  const story = bridgeStory(plan, r);
  const iraName = r.sepp > 0 && Math.abs(r.sepp - r.fIra) < 2 ? "From the IRAs, all of it 72(t)"
    : r.sepp > 0 ? `From the IRAs, ${money(r.sepp)} of it 72(t)` : "From the IRAs";
  const readRows: ReadoutRow[] = [
    { color: COLORS.cash, value: money(r.cash), name: "Cash on hand", bad: r.cash < -1 },
  ];
  if (plan.cushionOn) {
    readRows.push({
      key: "line", value: money(c),
      name: plan.cushionFloor && Math.abs(c - plan.cushionFloor) < 1 ? `Cushion, at its ${money(plan.cushionFloor)} floor` : `Cushion, ${plan.cushionMonths} months of shortfall`,
    });
  }
  if (plan.present.has("brk")) readRows.push({ color: COLORS.brk, value: money(r.fBrk), name: "From the brokerage", muted: r.fBrk <= 0 });
  if (plan.present.has("rrif")) readRows.push({ color: COLORS.rrif, value: money(r.fRrif), name: "From the RRIF", muted: r.fRrif <= 0 });
  if (plan.present.has("ira")) readRows.push({ color: COLORS.ira, value: money(r.fIra), name: iraName, muted: r.fIra <= 0 });
  if (plan.investAbove) readRows.push({ color: "rgba(87, 60, 112, 0.42)", value: money(r.invested), name: "Put into the brokerage", muted: r.invested <= 0 });

  return (
    <div className="rt-board">
      <div
        ref={ref}
        className="rt-plot"
        tabIndex={0}
        aria-label="Cash and money taken from savings, month by month for five years. Use the arrow keys to move through time."
        onKeyDown={cursorKeys(1, i, rows.length - 1, setCursor)}
      >
        <svg width={g.W} height={g.H} role="img" aria-label="Cash and withdrawals for the next five years">
          <defs>
            <clipPath id={`${uid}-up`}><rect x={0} y={0} width={g.W} height={Math.max(0, y0)} /></clipPath>
            <clipPath id={`${uid}-dn`}><rect x={0} y={y0} width={g.W} height={g.H} /></clipPath>
          </defs>
          <GridY x0={g.x0} x1={g.x1} y={g.yT} scale={g.sTop} />
          <GridY x0={g.x0} x1={g.x1} y={g.yB} scale={g.sBot} />
          <text x={g.x0} y={g.T - 8} className="lbl2">Cash on hand</text>
          <text x={g.x0} y={g.b0 - 10} className="lbl2">
            {g.hasInv ? "Taken out of savings (up) and put into the brokerage (down), each month" : "Taken out of savings each month"}
          </text>

          <path d={area} fill={COLORS.cash} fillOpacity={0.1} clipPath={`url(#${uid}-up)`} />
          <path d={area} fill={BAD} fillOpacity={0.12} clipPath={`url(#${uid}-dn)`} />
          <path d={line} fill="none" stroke={COLORS.cash} strokeWidth={2.25} strokeLinejoin="round" clipPath={`url(#${uid}-up)`} />
          <path d={line} fill="none" stroke={BAD} strokeWidth={2.25} strokeLinejoin="round" clipPath={`url(#${uid}-dn)`} />
          {cushionPath && <path d={cushionPath} fill="none" stroke="#5c5a54" strokeWidth={1.5} strokeDasharray="5 4" />}
          {zeros.map((z) => (
            <g key={z.key} pointerEvents="none">
              <line x1={z.xa} x2={z.xb} y1={y0} y2={y0} stroke={COLORS.cash} strokeWidth={5} strokeOpacity={0.28} strokeLinecap="round" />
              {z.text && <text x={z.tx} y={z.ty} className="lbl2">{z.text}</text>}
            </g>
          ))}

          {rows.map((row, k) => {
            let base = 0;
            const segs: [string, number][] = [["brk", row.fBrk], ["rrif", row.fRrif], ["ira", row.fIra]];
            return (
              <g key={row.m} className={"bar" + (k === i ? " on" : "")}>
                {segs.map(([key, v]) => {
                  if (v <= 0 || base >= g.upCap) return null;
                  const ya = g.yB(Math.min(base + v, g.upCap)), yb = g.yB(base);
                  base += v;
                  return <rect key={key} x={g.xc(k) - bw / 2} y={ya} width={bw} height={Math.max(1, yb - ya)} fill={COLORS[key as "brk"]} rx={1.5} />;
                })}
                {g.out[k] > g.upCap && (() => {
                  const yTop = g.yB(g.upCap);
                  return (
                    <>
                      <path d={`M${g.xc(k) - bw / 2 - 2},${yTop + 9} L${g.xc(k) + bw / 2 + 2},${yTop + 5} L${g.xc(k) + bw / 2 + 2},${yTop + 9} L${g.xc(k) - bw / 2 - 2},${yTop + 13} Z`} fill="#f5f0e6" />
                      <text x={g.xc(k) + bw / 2 + 4} y={yTop + 8} className="lbl2">{money(g.out[k])}</text>
                    </>
                  );
                })()}
                {row.invested > 1 && (() => {
                  const clipped = row.invested > -g.sBot.min;
                  const yEnd = g.yB(-Math.min(row.invested, -g.sBot.min));
                  return (
                    <>
                      <rect x={g.xc(k) - bw / 2} y={g.yB(0)} width={bw} height={Math.max(1, yEnd - g.yB(0))}
                        fill={COLORS.brk} fillOpacity={0.42} rx={1.5} />
                      {clipped && (
                        <>
                          <path d={`M${g.xc(k) - bw / 2 - 2},${yEnd - 9} L${g.xc(k) + bw / 2 + 2},${yEnd - 13} L${g.xc(k) + bw / 2 + 2},${yEnd - 9} L${g.xc(k) - bw / 2 - 2},${yEnd - 5} Z`} fill="#f5f0e6" />
                          <text x={g.xc(k) + bw / 2 + 4} y={yEnd - 2} className="lbl2">{money(-row.invested)}</text>
                        </>
                      )}
                    </>
                  );
                })()}
              </g>
            );
          })}

          {!narrow && (
            <>
              <text x={g.x1 + 10} y={close ? Math.min(yc, yk) - 6 : yc + 4} className={rows[last].cash < 0 ? "lbl-bad" : "lbl"}>
                Cash {money(rows[last].cash)}
              </text>
              {plan.cushionOn && (
                <text x={g.x1 + 10} y={close ? Math.max(yc, yk) + 12 : yk + 4} className="lbl2">Cushion {money(g.cushion[last])}</text>
              )}
            </>
          )}

          <text x={g.xc(0) - 4} y={g.axY} className="ax">Now</text>
          <text x={g.xc(0) - 4} y={g.axY + 14} className="ax2">age {wholeAge(rows[0].a)}</text>
          {rows.map((row, k) =>
            row.d.endsWith("-01") && g.xc(k) - g.xc(0) > 56 ? (
              <g key={row.m}>
                <text x={g.xc(k)} y={g.axY} className="ax" textAnchor="middle">{row.d.slice(0, 4)}</text>
                <text x={g.xc(k)} y={g.axY + 14} className="ax2" textAnchor="middle">age {wholeAge(row.a)}</text>
              </g>
            ) : null,
          )}

          <MilestoneStrip placed={g.placed} yTop={g.b0 + g.botH} yBase={g.msY} onPick={setCursor} />

          <g pointerEvents="none">
            <line x1={g.xc(i)} x2={g.xc(i)} y1={g.T} y2={g.b0 + g.botH} stroke="#1c1b19" strokeWidth={1} strokeOpacity={0.55} />
            <circle cx={g.xc(i)} cy={g.yT(r.cash)} r={4.5} fill={r.cash < 0 ? BAD : COLORS.cash} stroke="#f5f0e6" strokeWidth={2} />
            {plan.cushionOn && <circle cx={g.xc(i)} cy={g.yT(c)} r={3.5} fill="#f5f0e6" stroke="#5c5a54" strokeWidth={1.5} />}
          </g>
          <HoverLayer x0={g.x0} x1={g.x1} top={g.T} bottom={g.b0 + g.botH}
            toIndex={(px) => clamp(Math.floor((px - g.x0) / g.step), 0, rows.length - 1)} onIndex={setCursor} />
        </svg>
      </div>
      <Readout
        when={`${monthName(r.d)}, age ${wholeAge(r.a)}${inRange ? "" : " (end of this view)"}`}
        story={story.text}
        bad={story.bad}
        rows={readRows}
        hint="Hover the chart or use the arrow keys. Milestones under the chart jump to their month."
      />
    </div>
  );
}
