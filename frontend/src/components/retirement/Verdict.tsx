// Answer first: one sentence on whether the money lasts, a computed line on
// why, and three facts. Red when the plan runs short or runs out.

import type { VerdictWords } from "./model";

export default function Verdict({ words }: { words: VerdictWords }) {
  const bad = words.tone !== "ok";
  return (
    <>
      <header className={"rt-verdict" + (bad ? " is-bad" : "")}>
        <h1>{words.headline}</h1>
        <p>{words.sub}</p>
      </header>
      <div className="rt-facts">
        {words.facts.map((f) => (
          <div className="rt-fact" key={f.label}>
            <div className={"v" + (f.tone === "bad" ? " bad" : "")}>{f.value}</div>
            <div className="l">{f.label}</div>
          </div>
        ))}
      </div>
    </>
  );
}
