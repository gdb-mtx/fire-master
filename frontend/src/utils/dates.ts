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

/** Whole calendar months from this month to a "YYYY-MM[-DD]" value (0 if past). */
export function monthsFromNow(value: string): number {
  const d = parseLocalDate(value);
  const now = new Date();
  return Math.max(0, (d.getFullYear() - now.getFullYear()) * 12 + (d.getMonth() - now.getMonth()));
}

/**
 * A plan month (property sale, SEPP/RRSP start). A pinned calendar month
 * ("2027-07") wins; otherwise the legacy offset counts from TODAY and slides
 * a month later every month — labelled so it can't be mistaken for a date.
 */
export function planMonth(pinned: unknown, offset: unknown): { offset: number; label: string } {
  if (typeof pinned === "string" && pinned) {
    const n = monthsFromNow(pinned);
    return { offset: n, label: `${fmtMonthYear(pinned)} (in ${n} mo)` };
  }
  const n = Number(offset ?? 0);
  return { offset: n, label: `Month ${n} from now` };
}
