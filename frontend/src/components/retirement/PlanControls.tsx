// The plan switch (the app's existing ScenarioSelector behaviour: picking a plan
// activates it everywhere) and "Compare with", which only draws another plan's
// line on the holdings chart and never activates anything.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { useActivateScenario, useDeactivateScenario } from "../../api/queries";
import type { FireScenario } from "../../types/fire";

function Menu({ label, children, open, setOpen }: {
  label: ReactNode; children: ReactNode; open: boolean; setOpen: (v: boolean) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const click = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", click);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", click); document.removeEventListener("keydown", key); };
  }, [open, setOpen]);
  return (
    <div className="relative" ref={ref}>
      <button type="button" className="rt-pill" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        {label}
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} aria-hidden>
          <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && <div className="rt-menu" role="menu">{children}</div>}
    </div>
  );
}

function Item({ on, title, note, onPick }: { on: boolean; title: string; note?: string | null; onPick: () => void }) {
  return (
    <button type="button" role="menuitemradio" aria-checked={on} className={on ? "on" : ""} onClick={onPick}>
      <span className="tick" />
      <span>{title}{note && <small>{note}</small>}</span>
    </button>
  );
}

export function ScenarioSelector({ scenarios }: { scenarios: FireScenario[] }) {
  const activate = useActivateScenario();
  const deactivate = useDeactivateScenario();
  const [open, setOpen] = useState(false);
  const active = scenarios.find((s) => s.is_active);
  return (
    <Menu open={open} setOpen={setOpen} label={<><span className="dot" /><span className="muted">Plan</span>{active ? active.name : "Base plan"}</>}>
      <Item on={!active} title="Base plan" note="Your settings with no scenario applied" onPick={() => { if (active) deactivate.mutate(); setOpen(false); }} />
      {scenarios.map((s) => (
        <Item key={s.id} on={s.is_active} title={s.name} note={s.description}
          onPick={() => { if (!s.is_active) activate.mutate(s.id); setOpen(false); }} />
      ))}
      <hr />
      <Link to="/settings" className="link" onClick={() => setOpen(false)}>Manage plans in Configure</Link>
    </Menu>
  );
}

/** "active" plan excluded; "" = compare with nothing. */
export function ComparePicker({ scenarios, value, onChange }: {
  scenarios: FireScenario[];
  value: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = scenarios.find((s) => s.is_active);
  const choices: { id: string; name: string; note?: string | null }[] = [
    ...(active ? [{ id: "base", name: "Base plan", note: "No scenario applied" }] : []),
    ...scenarios.filter((s) => !s.is_active).map((s) => ({ id: s.id, name: s.name, note: s.description })),
  ];
  if (!choices.length) return null;
  const current = choices.find((c) => c.id === value);
  return (
    <Menu open={open} setOpen={setOpen} label={<><span className="muted">Compare with</span>{current ? current.name : "nothing"}</>}>
      <Item on={!current} title="Nothing" onPick={() => { onChange(""); setOpen(false); }} />
      {choices.map((c) => (
        <Item key={c.id} on={c.id === value} title={c.name} note={c.note} onPick={() => { onChange(c.id); setOpen(false); }} />
      ))}
    </Menu>
  );
}
