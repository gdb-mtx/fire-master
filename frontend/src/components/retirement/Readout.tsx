// The panel beside each chart: the hovered moment in one sentence, then values
// next to big colour keys, in the same order as the chart.
//
// Its height never moves while you hover: every moment's panel (`sizers`) is
// rendered invisibly in the same grid cell, so the panel is always as tall as
// the tallest one for this plan, at any width. Without it, a longer sentence
// or an extra row shifted every section below the chart.

import { useMemo } from "react";

export interface ReadoutRow {
  value: string;
  name: string;
  color?: string;
  key?: "swatch" | "line" | "tick" | "ring" | "hatch";
  muted?: boolean;
  total?: boolean;
  bad?: boolean;
}

export interface ReadoutEntry {
  when: string;
  story: string;
  bad?: boolean;
  rows: ReadoutRow[];
}

function Head({ e, hidden }: { e: ReadoutEntry; hidden?: boolean }) {
  return (
    <div className={"rt-readout-head" + (hidden ? " sizer" : "")} aria-hidden={hidden || undefined}>
      <div className="when">{e.when}</div>
      <div className={"story" + (e.bad ? " bad" : "")}>{e.story}</div>
    </div>
  );
}

function RowView({ r, at, hidden }: { r: ReadoutRow; at: number; hidden?: boolean }) {
  return (
    <div
      className={"rt-row" + (r.muted ? " muted" : "") + (r.total ? " total" : "") + (r.bad ? " bad" : "") + (hidden ? " sizer" : "")}
      aria-hidden={hidden || undefined}
      style={{ gridRow: at + 1 }}
    >
      <span
        className={"key" + (r.key && r.key !== "swatch" ? " " + r.key : "")}
        style={{ background: !r.key || r.key === "swatch" ? r.color : undefined, visibility: r.total ? "hidden" : undefined }}
      />
      <span className="val">{r.value}</span>
      <span className="nm">{r.name}</span>
    </div>
  );
}

export default function Readout({ when, story, rows, hint, bad, sizers }: {
  when: string;
  story: string;
  rows: ReadoutRow[];
  hint?: string;
  bad?: boolean;
  sizers?: ReadoutEntry[];
}) {
  // The sentence area is as tall as the longest sentence, and each row slot as
  // tall as its tallest version, so the color keys sit at the same place for
  // every year. Same element objects across hovers, so React skips the hidden copies.
  const hiddenHeads = useMemo(() => (sizers ?? []).map((e, i) => <Head key={i} e={e} hidden />), [sizers]);
  const hiddenRows = useMemo(
    () => (sizers ?? []).flatMap((e, i) => e.rows.map((r, j) => <RowView key={`${i}-${j}`} r={r} at={j} hidden />)),
    [sizers],
  );
  return (
    <aside className="rt-readout" aria-live="polite">
      <div className="rt-readout-stack">
        {hiddenHeads}
        <Head e={{ when, story, bad, rows }} />
      </div>
      <div className="rt-rows">
        {hiddenRows}
        {rows.map((r, j) => <RowView key={`v-${r.name}`} r={r} at={j} />)}
      </div>
      {hint && <div className="hint">{hint}</div>}
    </aside>
  );
}
