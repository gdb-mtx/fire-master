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

function Body({ e, hidden }: { e: ReadoutEntry; hidden?: boolean }) {
  return (
    <div className={"rt-readout-body" + (hidden ? " sizer" : "")} aria-hidden={hidden || undefined}>
      <div className="when">{e.when}</div>
      <div className={"story" + (e.bad ? " bad" : "")}>{e.story}</div>
      <div className="rt-rows">
        {e.rows.map((r) => (
          <div key={r.name} className={"rt-row" + (r.muted ? " muted" : "") + (r.total ? " total" : "") + (r.bad ? " bad" : "")}>
            <span
              className={"key" + (r.key && r.key !== "swatch" ? " " + r.key : "")}
              style={{ background: !r.key || r.key === "swatch" ? r.color : undefined, visibility: r.total ? "hidden" : undefined }}
            />
            <span className="val">{r.value}</span>
            <span className="nm">{r.name}</span>
          </div>
        ))}
      </div>
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
  // Same element objects across hovers, so React skips the hidden copies.
  const hiddenEls = useMemo(() => (sizers ?? []).map((e, i) => <Body key={i} e={e} hidden />), [sizers]);
  return (
    <aside className="rt-readout" aria-live="polite">
      <div className="rt-readout-stack">
        {hiddenEls}
        <Body e={{ when, story, bad, rows }} />
      </div>
      {hint && <div className="hint">{hint}</div>}
    </aside>
  );
}
