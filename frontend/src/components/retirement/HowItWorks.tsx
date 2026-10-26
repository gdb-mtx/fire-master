// Every rule the projection rests on, in one plain sentence each, straight
// from the API's `assumptions` (built from the plan on screen).

import { Link } from "react-router";
import type { ProjectionAssumption } from "../../types/fire";

export default function HowItWorks({ items }: { items: ProjectionAssumption[] }) {
  if (!items.length) return null;
  const anyDefault = items.some((a) => a.is_default);
  return (
    <section className="rt-rules" aria-labelledby="rt-rules-h">
      <h2 id="rt-rules-h">How this projection works</h2>
      <p className="sub">
        Every rule the numbers above rest on, in one place.
        {anyDefault && <> Values tagged <i>default</i> are the engine's own; you haven't set them.</>}{" "}
        Change any setting in <Link to="/settings">Configure</Link>.
      </p>
      {items.map((a) => (
        <div className="rt-rule" key={a.key}>
          <div className="t">
            {a.label}
            <small>{a.detail}</small>
          </div>
          <div className="v">
            {a.is_default && <span className="tag default">default</span>}
            {a.kind === "limit" && <span className="tag limit">limit</span>}
            {a.value}
          </div>
        </div>
      ))}
    </section>
  );
}
