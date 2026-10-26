// The panel beside each chart: the hovered moment in one sentence, then values
// next to big colour keys, in the same order as the chart.

export interface ReadoutRow {
  value: string;
  name: string;
  color?: string;
  key?: "swatch" | "line" | "tick" | "ring";
  muted?: boolean;
  total?: boolean;
  bad?: boolean;
}

export default function Readout({ when, story, rows, hint, bad }: {
  when: string;
  story: string;
  rows: ReadoutRow[];
  hint?: string;
  bad?: boolean;
}) {
  return (
    <aside className="rt-readout" aria-live="polite">
      <div className="when">{when}</div>
      <div className={"story" + (bad ? " bad" : "")}>{story}</div>
      <div className="rt-rows">
        {rows.map((r) => (
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
      {hint && <div className="hint">{hint}</div>}
    </aside>
  );
}
