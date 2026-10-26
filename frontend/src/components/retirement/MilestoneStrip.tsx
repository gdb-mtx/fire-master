// Milestone flags under a chart's x axis (placement in flags.ts). Clicking or
// hovering a flag moves the shared cursor to its month.

import { ROW, type Placed } from "./flags";
import { BAD } from "./model";

export default function MilestoneStrip({ placed, yTop, yBase, onPick }: {
  placed: Placed[];
  yTop: number; // where the dotted drop line starts (the plot's bottom)
  yBase: number; // baseline of the first label row
  onPick: (m: number) => void;
}) {
  return (
    <g>
      {placed.map((p) => {
        const yy = yBase + p.row * ROW;
        return (
          <g key={`${p.m}-${p.text}`}>
            <line x1={p.px} x2={p.px} y1={yTop} y2={yy - 11} stroke={p.warn ? BAD : "#8a867c"} strokeWidth={1} strokeDasharray="1 3" />
            <circle cx={p.px} cy={yy - 4} r={3} fill={p.warn ? BAD : "#1c1b19"} />
            <text
              x={p.flip ? p.px - 7 : p.px + 7} y={yy} textAnchor={p.flip ? "end" : "start"}
              className={"ms" + (p.warn ? " warn" : "")}
              onPointerEnter={() => onPick(p.m)} onClick={() => onPick(p.m)}
            >
              <title>Jump to this month</title>
              {p.text}
            </text>
          </g>
        );
      })}
    </g>
  );
}
