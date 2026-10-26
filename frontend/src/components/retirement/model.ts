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
  present: Set<SourceKey | HoldingKey>; // series with money somewhere in the plan
  outMonth: number | null; // every spendable pool empty
  shortRows: Row[]; // months that end with cash below zero
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
    present,
    outMonth: proj.money_runs_out_month ?? null,
    shortRows: rows.filter((r) => r.cash < -1),
  };
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

/** The run of short months starting at the first one, and what ends it. */
function shortSpell(p: Plan) {
  const first = p.shortRows[0];
  if (!first) return null;
  let last = first;
  for (const r of p.rows) {
    if (r.m <= first.m) continue;
    if (r.cash < -1) last = r;
    else break;
  }
  const after = p.rows.find((r) => r.m > last.m);
  const rescue = after
    ? p.milestones.find((x) => (x.kind === "sale" || (x.kind === "money" && (x.amount ?? 0) > 0)) && x.month === after.m)
    : undefined;
  const lowest = p.shortRows.reduce((a, r) => (r.cash < a.cash ? r : a), first);
  return { first, last, months: last.m - first.m + 1, after, rescue, lowest };
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
    const years = Math.max(1, Math.round(out.m / 12));
    sub = `Every savings pool is empty by then, ${plural(years, "year")} from now. From that month on, spending isn't covered.`;
    facts.push({ value: monthName(out.d), label: `when the money runs out, at age ${wholeAge(out.a)}.`, tone: "bad" });
  } else {
    const spell = tone === "short" ? shortSpell(p) : null;
    if (spell) {
      headline = `Cash runs short in ${monthName(spell.first.d)}.`;
      const until = spell.rescue
        ? `, until ${spell.rescue.label.replace(/ sold$/, " sells")} in ${monthName(spell.after!.d)}`
        : spell.after ? `, until ${monthName(spell.after.d)}` : "";
      sub = `Bills can't be paid for ${plural(spell.months, "month")}${until}; at worst cash is ${money(spell.lowest.cash)}. After that the money lasts past ${p.endAge}.`;
    } else {
      headline = `Your money lasts past ${p.endAge}.`;
      sub = "Nothing runs out. " + healthySub(p);
    }
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
    return {
      bad: true,
      text: `No. Cash runs out in ${monthName(short[0].d)} and is short in ${plural(short.length, "month")} of the next five years, ${money(low.cash)} at worst in ${monthName(low.d)}.`,
    };
  }
  const low = five.reduce((a, r) => (r.cash < a.cash ? r : a), five[0]);
  let text = `Yes. Cash never runs dry; its lowest point is ${money(low.cash)} in ${monthName(low.d)}.`;
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
      text += ` Watch the brokerage: it falls to ${money(low.brk)} in ${monthName(low.d)}, at ${wholeAge(low.a)}` +
        (low.ira > 3 * Math.max(low.brk, 1) ? `, while ${money(low.ira)} sits in IRAs.` : ".");
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
  for (const x of here.filter((x) => x.kind === "draw" || x.kind === "age")) bits.push(`${x.label}.`);
  if (!here.some((x) => x.kind === "sale" || (x.kind === "money" && (x.amount ?? 0) > 0))) {
    const covers = ([
      ["income", r.inc], ["ss", r.ss], ["ira", r.fIra], ["rrif", r.fRrif], ["brk", r.fBrk], ["cash", Math.max(0, -r.dCash)],
    ] as [SourceKey, number][]).filter((x) => x[1] > 50).sort((a, b) => b[1] - a[1]).slice(0, 3);
    if (covers.length) bits.push(`You spend ${money(r.spend)}: ` + covers.map(([k, v]) => `${money(v)} from ${sourceWord(p, k)}`).join(", ") + ".");
  }
  const bad = r.cash < -1;
  if (bad) bits.push(`Cash is ${money(-r.cash)} short; nothing can refill it yet.`);
  else if (p.cushionOn) {
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

export function holdingsStory(r: Row): string {
  const spend = spendable(r);
  const tot = spend + r.home + r.priv;
  let s = `You own ${money(tot)}; ${money(spend)} of it is money you can spend.`;
  if (r.home > 1) s += ` ${money(r.home)} is in property.`;
  if (r.priv > 1) s += ` ${money(r.priv)} is in private investments.`;
  return s;
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
