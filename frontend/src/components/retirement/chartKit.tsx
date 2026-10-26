// Small shared components for the three Retirement charts: grid lines, the
// hover layer and the "show as a table" view.

import type { ReactNode } from "react";
import { axisMoney, money } from "./format";

export function GridY({ x0, x1, y, scale }: {
  x0: number; x1: number; y: (v: number) => number; scale: { step: number; max: number; min: number };
}) {
  const lines: ReactNode[] = [];
  for (let v = scale.min; v <= scale.max + 1e-6; v += scale.step) {
    const yy = y(v);
    lines.push(
      <g key={v}>
        <line x1={x0} x2={x1} y1={yy} y2={yy} stroke={Math.abs(v) < 1e-6 ? "#8a867c" : "#e6ddcd"} strokeWidth={1} />
        <text x={x0 - 8} y={yy + 4} className="ax" textAnchor="end">{axisMoney(v)}</text>
      </g>,
    );
  }
  return <g aria-hidden>{lines}</g>;
}

/** Transparent capture rect: pointer x → index. */
export function HoverLayer({ x0, x1, top, bottom, toIndex, onIndex }: {
  x0: number; x1: number; top: number; bottom: number;
  toIndex: (px: number) => number; onIndex: (i: number) => void;
}) {
  const move = (e: React.PointerEvent<SVGRectElement>) => {
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const box = svg.getBoundingClientRect();
    onIndex(toIndex(e.clientX - box.left));
  };
  return (
    <rect x={x0} y={top} width={Math.max(0, x1 - x0)} height={Math.max(0, bottom - top)}
      fill="transparent" style={{ cursor: "crosshair" }} onPointerMove={move} onPointerDown={move} />
  );
}

export function DataTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <div className="rt-tablewrap">
      <table>
        <thead><tr>{head.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j}>{typeof v === "number" && j > 1 ? money(v) : v}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
