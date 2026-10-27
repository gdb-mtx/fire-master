// Formatting for the Retirement page. One money style everywhere: $950, $9,950,
// $12.4K, $251K, $2.69M, with a true minus sign.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function money(v: number): string {
  const neg = v < 0;
  const a = Math.abs(v);
  let s: string;
  if (a >= 999_500) s = "$" + (a / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M";
  else if (a >= 99_500) s = "$" + Math.round(a / 1000) + "K";
  else if (a >= 9_950) s = "$" + (a / 1000).toFixed(1).replace(/\.0$/, "") + "K";
  else s = "$" + Math.round(a).toLocaleString("en-US");
  return (neg && Math.round(a) !== 0 ? "−\u2060" : "") + s; // word joiner: the sign never wraps alone
}

export const signedMoney = (v: number) => (v >= 0 ? "+" : "−") + money(Math.abs(v));

export const axisMoney = (v: number) => (v === 0 ? "$0" : money(v));

/** "2026-10-26" or "2026-10" → "Oct 2026" */
export function monthName(d: string): string {
  const [y, m] = d.split("-");
  return `${MONTHS[Number(m) - 1]} ${y}`;
}

export const wholeAge = (a: number) => Math.floor(a + 1e-6);

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
export const numberWord = (n: number) => (n >= 0 && n < WORDS.length ? WORDS[n] : String(n));

export const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** "A", "A and B", "A, B and C" */
export function listJoin(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
}

/** Event names as people wrote them, minus parenthetical notes: "Startup A vests (1.5x)" → "Startup A vests". */
export const tidyLabel = (label: string) => label.replace(/\s*\([^)]*\)\s*/g, " ").replace(/\s+/g, " ").trim();

export function clip(label: string, max = 24): string {
  return label.length <= max ? label : label.slice(0, max - 1).trimEnd() + "…";
}

export const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;

/** Shorten by dropping middle words, keeping the ends that tell similar names
 * apart: "Gradual Property Exit (no 72t)" → "Gradual … (no 72t)". */
export function midClip(name: string, max = 20): string {
  if (name.length <= max) return name;
  const words = name.split(" ");
  let cut = false;
  while (words.join(" ").length > max && words.length > 2) {
    words.splice(cut ? 2 : 1, 1);
    if (!cut) { words.splice(1, 0, "\u2026"); cut = true; }
  }
  const out = words.join(" ");
  return out.length <= max + 2 ? out : name.slice(0, max - 1) + "\u2026";
}
