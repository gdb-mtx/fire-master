/**
 * Calendar-date helpers.
 *
 * The API sends dates as date-only strings ("2027-07-01") and months as
 * "2027-07". `new Date("2027-07-01")` parses a date-only string as UTC
 * midnight, which every US timezone renders as the PREVIOUS day — so an event
 * on the 1st displays in the prior month. Always go through parseLocalDate.
 */

const DATE_ONLY = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/;

/** Date-only or month string → a LOCAL Date. Full timestamps pass through. */
export function parseLocalDate(value: string): Date {
  const m = DATE_ONLY.exec(value);
  if (!m) return new Date(value);
  return new Date(Number(m[1]), Number(m[2]) - 1, m[3] ? Number(m[3]) : 1);
}

/** Today's LOCAL calendar date as YYYY-MM-DD (toISOString gives the UTC date). */
export function todayISO(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/** "Jul 2027" (short) or "July 2027" (long). */
export function fmtMonthYear(value: string, month: "short" | "long" = "short"): string {
  return parseLocalDate(value).toLocaleDateString("en-US", { month, year: "numeric" });
}

/** "Jul 1, 2027". */
export function fmtDate(value: string): string {
  return parseLocalDate(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** A "YYYY-MM" month shifted by n calendar months. */
export function shiftMonth(month: string, n: number): string {
  const d = parseLocalDate(month);
  d.setMonth(d.getMonth() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
