// Hooks and arithmetic shared by the Retirement charts (no components here,
// so fast refresh keeps working on the component files).

import { useEffect, useRef, useState, type KeyboardEvent } from "react";

export function useWidth<T extends HTMLElement>(fallback = 720) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      if (w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

function niceStep(raw: number) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 3 ? 3 : f <= 4 ? 4 : f <= 5 ? 5 : 10) * p;
}

/** A 0-based axis (or one dipping below zero) with round steps. */
export function niceScale(max: number, ticks: number, min = 0) {
  const span = Math.max(1, max - Math.min(0, min));
  const step = niceStep(span / ticks);
  return { step, max: Math.ceil(Math.max(max, step) / step) * step, min: min < 0 ? -Math.ceil(-min / step) * step : 0 };
}

/** Arrow keys move the shared cursor; Home/End jump to the ends. */
export function cursorKeys(step: number, cursor: number, last: number, set: (m: number) => void) {
  return (e: KeyboardEvent) => {
    const to = e.key === "ArrowRight" ? cursor + step : e.key === "ArrowLeft" ? cursor - step
      : e.key === "Home" ? 0 : e.key === "End" ? last : null;
    if (to == null) return;
    e.preventDefault();
    set(Math.max(0, Math.min(last, to)));
  };
}

export const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

/** The readout entries most likely to be the tallest: every one when there are
 * few, else the longest sentences and the ones with the most rows (see Readout). */
export function pickSizers<T extends { story: string; rows: unknown[] }>(entries: T[], max = 80): T[] {
  if (entries.length <= max) return entries;
  const mostRows = Math.max(...entries.map((e) => e.rows.length));
  const byLength = [...entries].sort((a, b) => b.story.length - a.story.length).slice(0, 16);
  const byRows = entries.filter((e) => e.rows.length === mostRows).slice(0, 8);
  return [...new Set([...byLength, ...byRows])];
}
