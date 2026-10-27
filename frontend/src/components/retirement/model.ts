// The Retirement page's data model: engine points → display rows, calendar
// years, and every sentence on the page. Pure functions only; the charts draw
// what this computes and never invent a number or a word.

import type { ProjectionMilestone, WealthPoolPoint, WealthPoolProjection } from "../../types/fire";
import {
  capitalize, clip, listJoin, money, monthName, numberWord, plural, tidyLabel, wholeAge,
} from "./format";

// ── Series: palette and stack orders ──────────────────────────────────────────
export type SourceKey = "income" | "ss" | "ira" | "rrif" | "brk" | "cash" | "one";
export type HoldingKey = "brk" | "rrif" | "ira" | "cash" | "home" | "priv";

// Hue families from the Oct 26 2026 palette decision (income amber, Social Security green,
// IRAs blue, RRIF orange, brokerage violet, cash green, one-time pink, property and
// private in warm grays), muted to sit on the paper: old gold, verdigris, denim,
// rust, aubergine, bottle green, mauve, stone, sand. Every pair that can touch in
// a stack (neighbours, and the pairs an empty series leaves touching) is at least
// ΔE 15 apart in OKLab, so neighbours stay distinct for full-colour readers.
export const COLORS = {
  income: "#cfa856", ss: "#59a4a1", ira: "#3770a3", rrif: "#b15c3d", brk: "#573c70",
  cash: "#3f6a35", one: "#c88cae", home: "#b1a28f", priv: "#ded6c9",
} as const;
/** Shortfalls and warnings: a crimson kept clear of the RRIF rust. */
export const BAD = "#9b1c31";

/** Bottom → top. Blue (IRAs) never sits directly on violet (brokerage). */
export const SOURCE_ORDER: SourceKey[] = ["income", "ss", "ira", "rrif", "brk", "cash", "one"];
export const HOLDING_ORDER: HoldingKey[] = ["brk", "rrif", "ira", "cash", "home", "priv"];

// ── Rows ──────────────────────────────────────────────────────────────────────
export interface Row {
  m: number; // month offset (0 = this month)
  d: string; // "YYYY-MM"
  a: number; // age
  cash: number;
  cushion: number;
  brk: number;
  ira: number; // every IRA, Roth included
  iraA: number; // the 72(t) account (IRA-A), inside ira
  rrif: number;
  home: number;
  priv: number;
  total: number;
  spend: number; // spending + every outflow event
  inc: number; // income sources + recurring events + cash interest
  ss: number;
  one: number; // one-time money into cash
  fIra: number; // taken from the IRAs (Roth included), net of redeposits
  sepp: number; // the 72(t) part of fIra
  fRrif: number;
  fBrk: number;
  invested: number; // cash moved into the brokerage
  dCash: number; // change in cash this month
  gap: number; // regular shortfall: spending − income sources − SS − 72(t) − RRIF
  ev: string | null;
}

const n = (v: number | undefined | null) => v ?? 0;

export function toRows(p: WealthPoolProjection): Row[] {
  let prev = n(p.starting_cash) || (p.points[0] ? p.points[0].cash : 0);
  return p.points.map((x: WealthPoolPoint) => {
    const ss = n(x.ss_income);
    const evIn = n(x.event_income);
    const evOut = n(x.event_expense);
    const one = n(x.one_time_income);
    const regular = x.income - ss - evIn + evOut; // sources (+ legacy overlays)
    const row: Row = {
      m: x.month ?? 0,
      d: x.date.slice(0, 7),
      a: x.age,
      cash: x.cash,
      cushion: n(x.cash_cushion),
      brk: n(x.taxable),
      ira: x.ira_sepp + x.ira_growth + n(x.roth),
      iraA: x.ira_sepp,
      rrif: x.rrsp,
      home: x.real_estate,
      priv: x.illiquid,
      total: x.total,
      spend: x.expenses + evOut,
      inc: regular + (evIn - one) + x.cash_interest,
      ss,
      one,
      fIra: x.ira_draw - n(x.rmd_redeposit) + n(x.roth_draw),
      sepp: n(x.sepp_draw),
      fRrif: x.rrsp_draw,
      fBrk: n(x.taxable_draw),
      invested: n(x.cash_invested),
      dCash: x.cash - prev,
      gap: x.expenses - (regular + ss) - n(x.sepp_draw) - x.rrsp_draw,
      ev: x.event,
    };
    prev = x.cash;
    return row;
  });
}

export const holdingValue = (r: Row, k: HoldingKey) =>
  k === "cash" ? Math.max(0, r.cash) : r[k];

export const spendable = (r: Row) => r.brk + r.rrif + r.ira + Math.max(0, r.cash);

// ── Years ─────────────────────────────────────────────────────────────────────
export interface YearRow {
  y: number;
  a: number; // age at the year's first month
  m0: number;
  m1: number;
  n: number;
  spend: number;
  inc: number;
  ss: number;
  one: number;
  ira: number;
  sepp: number;
  rrif: number;
  brk: number;
  fromCash: number;
  toCash: number;
  invested: number;
  end: Row;
}

/** Calendar years, full years only (a three-month stub would read as a cheap year). */
export function toYears(rows: Row[]): YearRow[] {
  const by = new Map<number, YearRow & { dCash: number }>();
  for (const r of rows) {
    const y = Number(r.d.slice(0, 4));
    let t = by.get(y);
    if (!t) {
      t = { y, a: r.a, m0: r.m, m1: r.m, n: 0, spend: 0, inc: 0, ss: 0, one: 0, ira: 0, sepp: 0,
        rrif: 0, brk: 0, fromCash: 0, toCash: 0, invested: 0, dCash: 0, end: r };
      by.set(y, t);
    }
    t.n++; t.m1 = r.m; t.end = r;
    t.spend += r.spend; t.inc += r.inc; t.ss += r.ss; t.one += r.one; t.ira += r.fIra;
    t.sepp += r.sepp; t.rrif += r.fRrif; t.brk += r.fBrk; t.invested += r.invested; t.dCash += r.dCash;
  }
  const all = [...by.values()];
  const full = all.filter((t) => t.n === 12);
  const out = full.length ? full : all;
  for (const t of out) {
    t.fromCash = Math.max(0, -t.dCash);
    t.toCash = Math.max(0, t.dCash);
  }
  return out;
}

export const sourceValue = (t: YearRow, k: SourceKey): number =>
  ({ income: t.inc, ss: t.ss, ira: t.ira, rrif: t.rrif, brk: t.brk, cash: t.fromCash, one: t.one })[k];

// ── The plan, ready to draw ───────────────────────────────────────────────────
export interface Plan {
  name: string;
  proj: WealthPoolProjection;
  rows: Row[];
  years: YearRow[];
  endAge: number;
  milestones: ProjectionMilestone[];
  cushionOn: boolean;
  cushionMonths: number;
  cushionFloor: number;
  investAbove: boolean;
  thresholdMonths: number;
  hasRental: boolean;
  hasIraA: boolean; // a 72(t) account (IRA-A) holds money
  present: Set<SourceKey | HoldingKey>; // series with money somewhere in the plan
  // Runs out: a cash shortfall that never recovers before the run ends (the
  // engine keeps running while an IRA it won't draw still holds money, and stops
  // once every pool is empty), or every pool empty with no shortfall before it.
  outMonth: number | null;
  outByEngine: boolean; // the out month is the engine's: every savings pool empty
  endsEarly: boolean; // the engine stopped before endAge (every pool empty)
  shortRows: Row[]; // months that end with cash below zero
  spells: { first: Row; last: Row }[]; // stretches of consecutive short months
  // IRA access (projection.open_all_iras_after_59_5)
  openAllIras: boolean;
  age59Month: number; // month of 59½; 0 once past
  iraAOpenMonth: number | null; // the 72(t) account takes extra withdrawals from here; null = never
}

export function toPlan(name: string, proj: WealthPoolProjection): Plan {
  const rows = toRows(proj);
  const years = toYears(rows);
  const rule = proj.cash_rule;
  const present = new Set<SourceKey | HoldingKey>();
  for (const r of rows) {
    if (r.inc > 1) present.add("income");
    if (r.ss > 1) present.add("ss");
    if (r.fIra > 1 || r.ira > 1) present.add("ira");
    if (r.fRrif > 1 || r.rrif > 1) present.add("rrif");
    if (r.fBrk > 1 || r.brk > 1) present.add("brk");
    if (r.one > 1) present.add("one");
    if (r.home > 1) present.add("home");
    if (r.priv > 1) present.add("priv");
  }
  present.add("cash");
  const spells: { first: Row; last: Row }[] = [];
  for (const r of rows) {
    if (r.cash >= -1) continue;
    const s = spells[spells.length - 1];
    if (s && s.last.m === r.m - 1) s.last = r;
    else spells.push({ first: r, last: r });
  }
  const tail = spells[spells.length - 1];
  const stranded = tail && rows.length && tail.last.m === rows[rows.length - 1].m ? tail.first.m : null;
  const engineOut = proj.money_runs_out_month ?? null;
  // A shortfall that never recovers wins over the engine's month: the engine may
  // stop years later, when unlocked IRAs drain into a hole they can't close.
  const outMonth = stranded ?? engineOut;
  return {
    name,
    proj,
    rows,
    years,
    endAge: proj.end_age ?? 82,
    milestones: proj.milestones ?? [],
    cushionOn: rule?.mode === "cushion",
    cushionMonths: rule?.cushion_months ?? 0,
    cushionFloor: rule?.cushion_floor ?? 0,
    investAbove: !!rule?.invest_above_cushion,
    thresholdMonths: rule?.threshold_months ?? 12,
    hasRental: (proj.assumptions ?? []).some((a) => a.key === "rental_occupancy"),
    hasIraA: (proj.assumptions ?? []).some((a) => a.key === "ira_access"),
    present,
    outMonth,
    outByEngine: engineOut != null && outMonth === engineOut,
    endsEarly: engineOut != null,
    shortRows: rows.filter((r) => r.cash < -1),
    spells,
    openAllIras: !!rule?.open_all_iras,
    age59Month: rule?.age_59_5_month ?? 0,
    iraAOpenMonth: rule?.ira_a_open_month ?? null,
  };
}

// ── IRA access and money drawn ────────────────────────────────────────────────
/** "YYYY-MM" of month offset m (rows[0] is month 0). */
function monthAt(p: Plan, m: number): string {
  const [y, mo] = (p.rows[0]?.d ?? "2000-01").split("-").map(Number);
  const k = y * 12 + (mo - 1) + m;
  return `${Math.floor(k / 12)}-${String((k % 12) + 1).padStart(2, "0")}`;
}

/** Why IRA money can't pay the bills in month m, and until when; null once it can. */
export function iraLock(p: Plan, m: number): { until: string; what: string } | null {
  if (m < p.age59Month) return { until: monthName(monthAt(p, p.age59Month)), what: "in IRAs you can't reach without a penalty" };
  if (p.openAllIras && p.iraAOpenMonth != null && m < p.iraAOpenMonth) {
    return { until: monthName(monthAt(p, p.iraAOpenMonth)), what: "in the 72(t) account, which can't take extra withdrawals" };
  }
  return null;
}

/** The unlock in month m, in words, and how much of row r's IRA money it opens. */
function unlockAt(p: Plan, m: number, r?: Row): { what: string; amount: number } | null {
  const other = r ? r.ira - r.iraA : 0;
  if (p.age59Month > 0 && m === p.age59Month) {
    if (p.openAllIras && p.iraAOpenMonth != null && p.iraAOpenMonth > m) return { what: "the other IRAs open at 59½", amount: other };
    if (!p.openAllIras && p.hasIraA) return { what: "the IRA not set aside for 72(t) opens at 59½", amount: other };
    return { what: "the IRAs open at 59½", amount: r ? r.ira : 0 };
  }
  if (p.openAllIras && p.iraAOpenMonth != null && m === p.iraAOpenMonth) return { what: "the 72(t) account opens", amount: r ? r.iraA : 0 };
  return null;
}

const drawn = (r: Row) => r.fBrk + r.fIra + r.fRrif;
/** Cash at $0 this month while savings pay the bills (the threshold rule after a repair). */
const atZero = (r: Row) => Math.abs(r.cash) <= 1 && drawn(r) > 1;
/** ...and it was at $0 last month too: savings pay only the month's bills. */
const restsAtZero = (r: Row) => atZero(r) && Math.abs(r.cash - r.dCash) <= 1;
/** Money this month that pays back an earlier shortfall (cash was below $0 and rose);
 * 0 when it is a small part of the month's withdrawals (not worth a clause). */
const repaidOf = (r: Row) => {
  const v = r.cash - r.dCash < -1 && r.dCash > 1 ? Math.min(r.dCash, -(r.cash - r.dCash)) : 0;
  return v > 0.2 * drawn(r) ? v : 0;
};

/** What savings pay this month and, if it closes a hole, how much of it does. */
function payWords(r: Row): string {
  const repaid = repaidOf(r);
  return drawWords(r) + (repaid > 1 ? `, ${money(repaid)} of it paying back the shortfall` : "");
}

/** What savings pay in one month, every source named: "$6.4K from the IRAs and $2.3K from the brokerage". */
export function drawWords(r: Row): string {
  const parts = ([["ira", r.fIra], ["brk", r.fBrk], ["rrif", r.fRrif]] as [SourceKey, number][])
    .filter((x) => x[1] > 1).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${money(v)} from ${SOURCE_WORD[k]}` + (k === "ira" && r.sepp > 1 && r.fIra - r.sepp > 1 ? ` (${money(r.sepp)} of it 72(t))` : ""));
  return listJoin(parts);
}

export interface ZeroSpell { first: Row; last: Row; months: number; total: number; words: string }

/** Stretches of at least `min` months where cash rests at $0, with what pays the bills. */
export function zeroSpells(p: Plan, rows: Row[] = p.rows, min = 3): ZeroSpell[] {
  const out: ZeroSpell[] = [];
  let run: Row[] = [];
  const flush = () => {
    if (run.length >= min) {
      const by = (["ira", "brk", "rrif"] as SourceKey[]).map((k) => {
        const pay = run.filter((r) => (k === "ira" ? r.fIra : k === "brk" ? r.fBrk : r.fRrif) > 1);
        const sum = pay.reduce((t, r) => t + (k === "ira" ? r.fIra : k === "brk" ? r.fBrk : r.fRrif), 0);
        return { k, sum, from: pay[0]?.m ?? 0, to: pay[pay.length - 1]?.m ?? 0 };
      }).filter((x) => x.sum > 1).sort((a, b) => a.from - b.from);
      const sepp = run.reduce((t, r) => t + r.sepp, 0);
      const name = (k: SourceKey, sum: number) => SOURCE_WORD[k] + (k !== "ira" || sepp <= 1 ? ""
        : sepp >= sum - 1 ? " (all 72(t) payments)" : ` (${money(sepp)} of it 72(t))`);
      // "then" when a source starts after the one before it stops paying
      const words = by.map((x, j) => (j && x.from > by[j - 1].to ? "then " : "") + `${money(x.sum)} from ${name(x.k, x.sum)}`);
      out.push({
        first: run[0], last: run[run.length - 1], months: run.length,
        total: by.reduce((t, x) => t + x.sum, 0),
        words: by.length === 1 ? `${money(by[0].sum)} in total, all from ${name(by[0].k, by[0].sum)}` : listJoin(words),
      });
    }
    run = [];
  };
  for (const r of rows) {
    if (restsAtZero(r)) run.push(r);
    else flush();
  }
  flush();
  return out;
}

/** One sentence for a stretch at $0: how long, how much a month, from where. */
function zeroSentence(p: Plan, z: ZeroSpell): string {
  const toEnd = z.last.m === endRow(p).m;
  const span = toEnd ? `From ${monthName(z.first.d)} on` : `From ${monthName(z.first.d)} to ${monthName(z.last.d)}`;
  return `${span}, cash rests at $0, so savings pay every bill as it comes: ${money(z.total / z.months)} a month on average, ${z.words}.`;
}

export const endRow = (p: Plan) => p.rows[p.rows.length - 1];

export type Tone = "ok" | "short" | "out";
export const toneOf = (p: Plan): Tone => (p.outMonth != null ? "out" : p.shortRows.length ? "short" : "ok");

const rowAt = (p: Plan, m: number) => p.rows.find((r) => r.m === m) ?? endRow(p);
const sales = (p: Plan) => p.milestones.filter((x) => x.kind === "sale");

/** First month any savings pool pays (brokerage, IRAs beyond 72(t), Roth). */
function firstSavingsRow(p: Plan): Row | undefined {
  return p.rows.find((r) => r.fBrk > 1 || r.fIra - r.sepp > 1);
}

/** What ends a short stretch: "City Loft sells in Oct 2029", "the IRAs open at 59½ in Jun 2034". */
function ending(p: Plan, end: Row) {
  const after = p.rows.find((r) => r.m > end.m);
  if (!after) return { after, until: "" };
  const sale = p.milestones.find((x) => (x.kind === "sale" || (x.kind === "money" && (x.amount ?? 0) > 0)) && x.month === after.m);
  const unlock = sale ? null : unlockAt(p, after.m, end);
  const what = sale ? sale.label.replace(/ sold$/, " sells") : unlock?.what;
  return {
    after,
    unlock: !!unlock,
    until: (what ? `${what} in ${monthName(after.d)}` : monthName(after.d)) +
      (unlock && unlock.amount > 1 ? `, unlocking ${money(unlock.amount)}` : ""),
  };
}

/** The first stretch of short months, what ends it, and the totals across all stretches. */
function shortSpell(p: Plan) {
  const s = p.spells[0];
  if (!s) return null;
  const { first, last } = s;
  const { after, until, unlock } = ending(p, last);
  const lastEnd = ending(p, p.spells[p.spells.length - 1].last);
  const lowest = p.shortRows.reduce((a, r) => (r.cash < a.cash ? r : a), first);
  // IRA money out of reach while cash is short: the first short month that has some
  const locked = p.shortRows.find((r) => r.ira > 1 && iraLock(p, r.m));
  return { first, last, months: last.m - first.m + 1, after, until, lastUntil: lastEnd.until, lowest, locked,
    namesUnlock: unlock || lastEnd.unlock,
    stretches: p.spells.length, total: p.shortRows.length };
}

/** "$480K sits in IRAs you can't reach without a penalty until Jun 2034." */
function lockedWords(p: Plan, r: Row, verb = "sits"): string {
  const l = iraLock(p, r.m);
  return l ? `${money(r.ira)} ${verb} ${l.what} until ${l.until}` : "";
}

// ── Words: verdict and facts ──────────────────────────────────────────────────
export interface Fact {
  value: string;
  label: string;
  tone?: "bad" | "good";
}

export interface VerdictWords {
  tone: Tone;
  headline: string;
  sub: string;
  facts: Fact[];
}

function restPhrase(r: Row): string {
  const home = r.home > 1, priv = r.priv > 1;
  if (home && priv) return "; the rest is property and private investments";
  if (home) return "; the rest is property";
  if (priv) return "; the rest is private investments";
  return "";
}

export function verdictWords(p: Plan, compare: Plan | null): VerdictWords {
  const tone = toneOf(p);
  const end = endRow(p);
  const facts: Fact[] = [];
  let headline: string;
  let sub: string;

  if (tone === "out") {
    const out = rowAt(p, p.outMonth!);
    headline = `Money runs out in ${monthName(out.d)}, at ${wholeAge(out.a)}.`;
    const away = out.m < 24 ? plural(Math.max(1, out.m), "month") : plural(Math.round(out.m / 12), "year");
    if (p.outByEngine) {
      sub = `Every savings pool is empty by then, ${away} from now. From that month on, spending isn't covered.`;
      const before = p.spells.filter((x) => x.last.m < out.m);
      if (before.length) {
        const n = before.reduce((t, x) => t + x.last.m - x.first.m + 1, 0);
        const till = ending(p, before[before.length - 1].last).until;
        sub += ` Before that, bills can't be paid ${before.length === 1 ? "for" : "in"} ${plural(n, "month")}, from ${monthName(before[0].first.d)}${till ? ` until ${till}` : ""}.`;
      }
      const zero = zeroSpells(p, p.rows.filter((r) => r.m < out.m), 6).filter((z) => z.first.m >= 60 || z.months >= 24)
        .sort((a, b) => b.months - a.months)[0];
      if (zero) sub += " " + zeroSentence(p, zero);
    } else {
      sub = `That's ${away} from now. From then on spending isn't covered, and ` +
        (p.endsEarly ? `by ${monthName(end.d)} every savings pool is empty, with cash ${money(-end.cash)} short.`
          : `by ${p.endAge} cash is ${money(-end.cash)} short.`);
      const locked = out.ira > 1 ? lockedWords(p, out, "is") : "";
      if (locked) {
        sub += ` ${locked}` + (p.openAllIras ? ", and even then it doesn't cover the gap."
          : "; this projection never uses it to pay the shortfall back. See which IRA money it draws, below.");
      } else if (out.ira > 1) {
        sub += ` ${money(out.ira)} is still in IRAs when it happens; see which IRA money this projection draws, below.`;
      }
    }
    facts.push({ value: monthName(out.d), label: `when the money runs out, at age ${wholeAge(out.a)}.`, tone: "bad" });
  } else {
    const spell = tone === "short" ? shortSpell(p) : null;
    if (spell) {
      headline = `Cash runs short in ${monthName(spell.first.d)}.`;
      const until = spell.until ? `, until ${spell.until}` : "";
      sub = spell.stretches === 1
        ? `Bills can't be paid for ${plural(spell.months, "month")}${until}; at worst cash is ${money(spell.lowest.cash)}. After that the money lasts past ${p.endAge}.`
        : `Bills can't be paid in ${plural(spell.total, "month")} across ${numberWord(spell.stretches)} stretches; the first lasts ${plural(spell.months, "month")}${until}. At worst cash is ${money(spell.lowest.cash)}. ` +
          (ending(p, p.spells[p.spells.length - 1].last).unlock ? `The last ends when ${spell.lastUntil}; after that` : "After the last one,") +
          ` the money lasts past ${p.endAge}.`;
      if (spell.locked && !spell.namesUnlock) {
        const outlasts = !p.openAllIras && p.age59Month > 0 && p.shortRows.some((r) => r.m >= p.age59Month && r.ira > 1);
        sub += ` While cash is short, ${lockedWords(p, spell.locked)}` +
          (outlasts ? "; after that this projection still never uses IRAs to pay a shortfall back." : ".");
      }
    } else {
      headline = `Your money lasts past ${p.endAge}.`;
      sub = "Nothing runs out. " + healthySub(p);
    }
    // Cash at $0: the cash chart labels a stretch in its five years; the verdict
    // names one it can't show, or one long enough to define the plan.
    const zero = zeroSpells(p, p.rows, 6).filter((z) => z.first.m >= 60 || z.months >= 24)
      .sort((a, b) => b.months - a.months)[0];
    if (zero) sub += " " + zeroSentence(p, zero);
    const spend = spendable(end);
    facts.push({
      value: money(end.total),
      label: `at ${p.endAge}. ` + (end.total - spend > 1
        ? `${money(spend)} of it you can spend${restPhrase(end)}.`
        : "All of it is money you can spend."),
    });
  }

  if (compare && compare.rows.length) {
    const diff = endRow(p).total - endRow(compare).total;
    facts.push({
      value: (diff >= 0 ? "+" : "−") + money(Math.abs(diff)),
      label: `at ${p.endAge} versus ${compare.name} (${money(endRow(compare).total)}).`,
      tone: diff < 0 ? "bad" : undefined,
    });
  } else {
    const five = p.rows.slice(0, 60);
    const low = five.reduce((a, r) => (r.cash < a.cash ? r : a), five[0]);
    facts.push({
      value: money(low.cash),
      label: `lowest cash in the next five years, in ${monthName(low.d)}.`,
      tone: low.cash < 0 ? "bad" : undefined,
    });
  }

  const now = p.rows[0];
  if (p.cushionOn) {
    facts.push({
      value: money(now.cushion),
      label: `cash cushion now: ${p.cushionMonths} months of what your income doesn't cover` +
        (p.cushionFloor ? `, never under ${money(p.cushionFloor)}` : "") + ". It moves when that changes.",
    });
  } else if (now.gap > 1) {
    facts.push({
      value: `${money(now.gap)} a month`,
      label: `is what income doesn't cover now. Savings start paying it once cash falls below ${p.thresholdMonths} months of it.`,
    });
  } else {
    facts.push({ value: money(now.cash), label: "in cash now. Income covers spending, so savings aren't needed." });
  }
  return { tone, headline, sub, facts };
}

function healthySub(p: Plan): string {
  const parts: string[] = [];
  const s = sales(p).filter((x) => (x.amount ?? 0) > 0);
  if (s.length) {
    const total = s.reduce((t, x) => t + (x.amount ?? 0), 0);
    const toBrk = s.every((x) => x.destination === "brokerage");
    const names = s.map((x) => x.label.replace(/ sold$/, ""));
    const who = s.length <= 3 ? `Selling ${listJoin(names)}` : `${capitalize(numberWord(s.length))} property sales`;
    const last = rowAt(p, s[s.length - 1].month);
    parts.push(`${who} ${s.length <= 3 ? "moves" : "move"} ${money(total)} into ${toBrk ? "the brokerage" : "savings"} by ${monthName(last.d)}`);
  }
  if (p.cushionOn) {
    const refill = p.rows.find((r) => r.fBrk > 1 || r.fIra - r.sepp > 1);
    if (refill) {
      parts.push(`${parts.length ? "and from" : "From"} ${monthName(refill.d)} on, savings keep cash topped up to a ${p.cushionMonths}-month cushion` +
        (p.investAbove ? ", investing anything above it" : ""));
    } else {
      parts.push(`${parts.length ? "and cash" : "Cash"} stays above its ${p.cushionMonths}-month cushion the whole way`);
    }
  } else {
    const first = firstSavingsRow(p);
    if (first) {
      parts.push(`${parts.length ? "and savings" : "Savings"} start paying the bills in ${monthName(first.d)}, once cash falls below ${p.thresholdMonths} months of the shortfall`);
    } else {
      parts.push(`${parts.length ? "and income" : "Income"} and cash cover every month without touching savings`);
    }
  }
  return parts.join(", ") + ".";
}

// ── Words: answers above each chart ───────────────────────────────────────────
export function bridgeAnswer(p: Plan): { text: string; bad: boolean } {
  const five = p.rows.slice(0, 60);
  const short = five.filter((r) => r.cash < -1);
  if (short.length) {
    const low = short.reduce((a, r) => (r.cash < a.cash ? r : a), short[0]);
    const locked = short.find((r) => r.ira > 1 && iraLock(p, r.m));
    return {
      bad: true,
      text: `No. Cash runs out in ${monthName(short[0].d)} and is short in ${plural(short.length, "month")} of the next five years, ${money(low.cash)} at worst in ${monthName(low.d)}.` +
        (locked ? ` Meanwhile ${lockedWords(p, locked)}.` : ""),
    };
  }
  const low = five.reduce((a, r) => (r.cash < a.cash ? r : a), five[0]);
  const zero = zeroSpells(p).find((z) => z.first.m < five.length);
  let text = zero
    ? `Yes, but with no buffer. ${zeroSentence(p, zero)}`
    : `Yes. Cash never runs dry; its lowest point is ${money(low.cash)} in ${monthName(low.d)}.`;
  const lump = p.milestones
    .filter((x) => x.kind === "money" && (x.amount ?? 0) > 0 && x.month < five.length)
    .sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0))[0];
  if (lump) {
    const when = monthName(rowAt(p, lump.month).d);
    const what = tidyLabel(lump.label);
    if (p.cushionOn && p.investAbove) text += ` The ${what} in ${when} goes into the brokerage, above what the cushion needs.`;
    else if (p.cushionOn) text += ` The ${what} lands in cash in ${when}, so cash sits above the cushion for a while and pays the bills first.`;
    else text += ` The ${what} lands in cash in ${when}.`;
  }
  return { text, bad: false };
}

const SOURCE_WORD: Record<SourceKey, string> = {
  income: "income", ss: "Social Security", ira: "the IRAs", rrif: "the RRIF", brk: "the brokerage",
  cash: "cash", one: "one-time money",
};
export const sourceWord = (p: Plan, k: SourceKey) =>
  k === "income" && p.hasRental ? "rent and other income" : SOURCE_WORD[k];
const PLURAL_SUBJECTS = new Set(["the IRAs", "the IRAs and RRIF"]);
const verb = (subject: string, base: string) => (PLURAL_SUBJECTS.has(subject) ? base : base + "s");

export function sourcesAnswer(p: Plan): string {
  const ten = p.years.slice(0, 10);
  if (!ten.length) return "";
  const total = ten.reduce((t, y) => t + y.spend, 0);
  const iraWord = p.present.has("rrif") ? "the IRAs and RRIF" : "the IRAs";
  const buckets: [string, number][] = [
    ["the brokerage", ten.reduce((t, y) => t + y.brk, 0)],
    [iraWord, ten.reduce((t, y) => t + y.ira + y.rrif, 0)],
    ["one-time money", ten.reduce((t, y) => t + y.one, 0)],
    [sourceWord(p, "income"), ten.reduce((t, y) => t + y.inc, 0)],
    ["Social Security", ten.reduce((t, y) => t + y.ss, 0)],
    ["cash", ten.reduce((t, y) => t + y.fromCash, 0)],
  ];
  const top = buckets.filter((b) => b[1] > 500).sort((a, b) => b[1] - a[1]).slice(0, 3);
  if (!top.length) return "";
  const span = ten.length === 10 ? "the next ten years" : `the next ${plural(ten.length, "year")}`;
  let text = `Most of it comes from ${top[0][0]}. Over ${span} you spend ${money(total)}: ` +
    top.map(([w, v], i) => (i === 0 ? `${w} ${verb(w, "cover")} ${money(v)}` : `${w} ${money(v)}`)).join(", ") + ".";
  const ssStart = p.years.find((y) => y.ss > 0);
  if (ssStart && p.years[0].ss === 0) text += ` Social Security starts in ${ssStart.y}, at ${money(ssStart.ss)} that year.`;
  return text;
}

export function holdingsAnswer(p: Plan): string {
  const end = endRow(p);
  if (toneOf(p) === "out") {
    const out = rowAt(p, p.outMonth!);
    const left = [end.ira > 1 ? `${money(end.ira)} in IRAs` : "", end.home > 1 ? `${money(end.home)} in property` : "",
      end.priv > 1 ? `${money(end.priv)} in private investments` : ""].filter(Boolean);
    if (end.m === out.m) return `The money you can spend is gone in ${monthName(out.d)}` + (left.length ? `, with ${listJoin(left)} left.` : ".");
    return `The money you can spend is gone in ${monthName(out.d)}.` +
      (end.cash < -1 ? ` By ${p.endsEarly ? monthName(end.d) : p.endAge} the plan is ${money(-end.cash)} short` + (left.length ? `, with ${listJoin(left)} left.` : ".") : "");
  }
  const spend = spendable(end);
  let text = `${money(end.total)} at ${p.endAge}, ` +
    (end.total - spend <= 1 ? "all of it money you can spend."
      : spend / end.total >= 0.95 ? "almost all of it money you can spend."
        : `${money(spend)} of it money you can spend.`);
  const s = sales(p);
  if (s.length) text += ` Your property turns into brokerage money by ${rowAt(p, s[s.length - 1].month).d.slice(0, 4)}.`;
  // Watch the brokerage when it nearly empties after having been the main pool.
  const startM = s.length ? s[s.length - 1].month : 0;
  const after = p.rows.filter((r) => r.m >= startM);
  const peak = after.reduce((a, r) => Math.max(a, r.brk), 0);
  if (peak > 100_000) {
    const peakIdx = after.findIndex((r) => r.brk === peak);
    const later = after.slice(peakIdx);
    const low = later.reduce((a, r) => (r.brk < a.brk ? r : a), later[0]);
    const yearOfSpend = (p.rows[0]?.spend ?? 0) * 12;
    if (low.brk < peak * 0.1 && low.brk < 2 * yearOfSpend && low.m < end.m) {
      const l = iraLock(p, low.m);
      text += ` Watch the brokerage: it falls to ${money(low.brk)} in ${monthName(low.d)}, at ${wholeAge(low.a)}` +
        (low.ira <= 3 * Math.max(low.brk, 1) ? "."
          : l ? `, while ${money(low.ira)} sits ${l.what} until ${l.until}.`
            : p.openAllIras ? `; from there the IRAs (${money(low.ira)}) take over.`
              : `, while ${money(low.ira)} sits in IRAs.`);
    }
  }
  return text;
}

// ── Readout sentences ─────────────────────────────────────────────────────────
export function bridgeStory(p: Plan, r: Row): { text: string; bad: boolean } {
  const bits: string[] = [];
  const here = p.milestones.filter((x) => x.month === r.m);
  for (const x of here.filter((x) => x.kind === "sale")) {
    bits.push(`${x.label}: ${money(x.amount ?? 0)} goes into ${x.destination === "cash" ? "cash" : "the brokerage"}.`);
  }
  for (const x of here.filter((x) => x.kind === "money")) {
    const a = x.amount ?? 0;
    bits.push(a > 0 ? `${tidyLabel(x.label)} adds ${money(a)} to cash.` : `${tidyLabel(x.label)} takes ${money(-a)}.`);
  }
  for (const x of here.filter((x) => x.kind === "draw" || x.kind === "age")) bits.push(milestoneWords(p, x));
  if (atZero(r)) {
    bits.push(`You spend ${money(r.spend)}. Cash is ${repaidOf(r) > 1 ? "back " : ""}at $0, so savings pay what income doesn't: ${payWords(r)}.`);
  } else if (repaidOf(r) > 1) {
    bits.push(`You spend ${money(r.spend)}. Savings pay ${payWords(r)}.`);
  } else if (!here.some((x) => x.kind === "sale" || (x.kind === "money" && (x.amount ?? 0) > 0))) {
    const covers = ([
      ["income", r.inc], ["ss", r.ss], ["ira", r.fIra], ["rrif", r.fRrif], ["brk", r.fBrk], ["cash", Math.max(0, -r.dCash)],
    ] as [SourceKey, number][]).filter((x) => x[1] > 50).sort((a, b) => b[1] - a[1]).slice(0, 3);
    if (covers.length) bits.push(`You spend ${money(r.spend)}: ` + covers.map(([k, v]) => `${money(v)} from ${sourceWord(p, k)}`).join(", ") + ".");
  }
  const bad = r.cash < -1;
  if (bad) {
    bits.push(`Cash is ${money(-r.cash)} short this month.`);
    if (r.ira > 1 && iraLock(p, r.m)) bits.push(`${lockedWords(p, r)}.`);
  } else if (atZero(r)) {
    // said above
  } else if (p.cushionOn) {
    if (r.invested > 1) bits.push(`${money(r.invested)} above the cushion goes into the brokerage.`);
    else if (r.cash < r.cushion - 60) bits.push("Cash is below the cushion, with nothing to refill it from yet.");
    else if (r.fBrk > 1 || r.fIra - r.sepp > 1) bits.push(`Savings top cash back up to the ${money(r.cushion)} cushion.`);
    else if (r.cash > r.cushion + 60) bits.push("Cash is above the cushion, so it pays first.");
  } else if (r.fBrk > 1 || r.fIra - r.sepp > 1) bits.push("Savings pay this month's shortfall.");
  return { text: bits.join(" "), bad };
}

export function sourcesStory(p: Plan, t: YearRow): string {
  const parts = SOURCE_ORDER.map((k) => [k, sourceValue(t, k)] as [SourceKey, number])
    .filter((x) => x[1] > 50).sort((a, b) => b[1] - a[1]);
  let s = `You spend ${money(t.spend)}.`;
  if (parts.length) {
    s += ` The most comes from ${sourceWord(p, parts[0][0])} (${money(parts[0][1])})` +
      (parts[1] ? `, then ${sourceWord(p, parts[1][0])} (${money(parts[1][1])}).` : ".");
  }
  if (t.invested > 500) s += ` ${money(t.invested)} goes into the brokerage.`;
  if (t.toCash > 500) s += ` ${money(t.toCash)} more comes in than goes out, so cash grows.`;
  return s;
}

export function holdingsStory(p: Plan, r: Row): string {
  const spend = spendable(r);
  const tot = spend + r.home + r.priv;
  let s = `You own ${money(tot)}; ${money(spend)} of it is money you can spend.`;
  if (r.home > 1) s += ` ${money(r.home)} is in property.`;
  if (r.priv > 1) s += ` ${money(r.priv)} is in private investments.`;
  if (atZero(r)) s += ` Cash is at $0: savings pay the bills as they come, ${payWords(r)} this month.`;
  else if (r.cash < -1) {
    s += ` Cash is ${money(-r.cash)} short` + (drawn(r) > 1 ? `; savings pay ${payWords(r)} this month.` : ".");
    if (r.ira > 1 && iraLock(p, r.m)) s += ` ${lockedWords(p, r)}.`;
  }
  return s;
}

/** A draw or age milestone in the readout, with what it unlocks. */
function milestoneWords(p: Plan, x: ProjectionMilestone): string {
  if (x.label === "Age 59½") {
    if (!p.openAllIras) {
      return p.hasIraA ? "Age 59½: the IRA not set aside for 72(t) can pay the bills from here."
        : "Age 59½: the IRAs can pay the bills from here.";
    }
    return p.iraAOpenMonth != null && p.iraAOpenMonth > x.month
      ? `Age 59½: the other IRAs can pay the bills from here; the 72(t) account joins in ${monthName(monthAt(p, p.iraAOpenMonth))}.`
      : "Age 59½: every IRA can pay the bills from here.";
  }
  if (x.label === "72(t) account opens") return "The 72(t) account opens: its five years of payments are done, so it can pay the bills too.";
  return `${x.label}.`;
}

// ── Milestone labels ──────────────────────────────────────────────────────────
export function shortMilestone(x: ProjectionMilestone): string {
  switch (x.kind) {
    case "money": {
      const a = x.amount ?? 0;
      return `${clip(tidyLabel(x.label), 22)} ${a >= 0 ? "+" : "−"}${money(Math.abs(a))}`;
    }
    case "draw":
      return x.label.replace(/ payments start$/, " starts").replace("RRIF money runs out", "RRIF ends");
    case "age":
      return x.label.replace(/^Age 59½$/, "59½").replace("Social Security starts", "Social Security")
        .replace("Required IRA withdrawals start", "Required withdrawals").replace(/^Spending steps to /, "Spending to ");
    default:
      return x.label;
  }
}

/** Milestones within `window` months of each other share one label. */
export function mergeMilestones(items: ProjectionMilestone[], window = 1) {
  const out: { m: number; text: string; warn: boolean }[] = [];
  for (const x of [...items].sort((a, b) => a.month - b.month)) {
    const last = out[out.length - 1];
    if (last && x.month - last.m <= window) {
      last.text += ", " + shortMilestone(x);
      last.warn ||= x.kind === "warning";
    } else out.push({ m: x.month, text: shortMilestone(x), warn: x.kind === "warning" });
  }
  return out;
}

/** Which money milestones are worth a flag: inflows from $5K, outflows from $25K. */
export const flagWorthy = (x: ProjectionMilestone) =>
  x.kind !== "money" || (x.amount ?? 0) >= 5_000 || (x.amount ?? 0) <= -25_000;
