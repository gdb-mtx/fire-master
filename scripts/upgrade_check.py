"""Upgrade check — what the September 2026 update changed in THIS install.

Read-only. Run it after updating, before trusting the projections; it prints a
report of everything in your own data that the update touched and what (if
anything) you should decide. Nothing is written.

    docker compose exec backend uv run python ../scripts/upgrade_check.py
    # or, native:  cd backend && uv run python ../scripts/upgrade_check.py

Each finding is OK (nothing to do) or REVIEW (a decision for you); the fix for
every REVIEW item is in docs/UPGRADING.md.
"""

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.engines.plan_months import (  # noqa: E402
    PLAN_MONTH_KEYS,
    SALE_KEYS,
    is_held,
    month_from_offset,
)

SALE_WORDS = ("sale", "proceeds", "sell")


# ---------------------------------------------------------------------------
# Pure checks (unit-tested in backend/tests/test_upgrade_check.py)
# ---------------------------------------------------------------------------

def pinned_months(current_ca: dict, before_ca: dict | None, last_edit: date | None) -> list[dict]:
    """Plan months that were offsets before and are calendar months now.

    `before_ca` is the last stored version that still had offsets; `last_edit`
    is when the user last saved it. The update pinned each offset where it
    pointed ON THE UPDATE DAY; counted from the last edit it would land earlier.
    """
    if not isinstance(before_ca, dict):
        return []
    out = []
    for section, rel, pinned in PLAN_MONTH_KEYS:
        old = (before_ca.get(section) or {}).get(rel)
        now = (current_ca.get(section) or {}).get(pinned)
        if old is not None and now:
            out.append({"what": f"{section}.{pinned}", "offset": old, "pinned": now,
                        "from_last_edit": month_from_offset(last_edit, old) if last_edit else None})
    now_sales = {s.get("key"): s for s in current_ca.get("property_sales") or [] if isinstance(s, dict)}
    for s in before_ca.get("property_sales") or []:
        if not isinstance(s, dict) or s.get(SALE_KEYS[0]) is None:
            continue
        cur = now_sales.get(s.get("key")) or {}
        if cur.get(SALE_KEYS[1]):
            out.append({"what": f"property sale '{s.get('key')}'", "offset": s[SALE_KEYS[0]],
                        "pinned": cur[SALE_KEYS[1]],
                        "from_last_edit": month_from_offset(last_edit, s[SALE_KEYS[0]]) if last_edit else None})
    return out


def double_counted_sales(sales: list[dict], events: list) -> list[str]:
    """Future income events that look like a sale's proceeds while a (non-held)
    property_sales entry models a sale and no entry suppresses the event."""
    selling = [s for s in sales if isinstance(s, dict) and not is_held(s)]
    if not selling:
        return []
    tokens = [s["suppress_cashflow_match"].lower() for s in selling if s.get("suppress_cashflow_match")]
    hits = []
    for ev in events:
        name = (ev.name or "").lower()
        if ev.event_type != "income" or not any(w in name for w in SALE_WORDS):
            continue
        if not any(t in name for t in tokens):
            hits.append(ev.name)
    return hits


def expense_events_now_counted(sales: list[dict], events: list) -> list[str]:
    """Expense events a sale's suppress_cashflow_match used to drop from Retirement."""
    tokens = [s["suppress_cashflow_match"].lower() for s in sales
              if isinstance(s, dict) and s.get("suppress_cashflow_match") and not is_held(s)]
    return [ev.name for ev in events
            if ev.event_type == "expense" and any(t in (ev.name or "").lower() for t in tokens)]


def held_properties(sales: list[dict]) -> list[str]:
    """Entries with no sale date: they used to sell in month 0, now they are kept."""
    return [str(s.get("key")) for s in sales if isinstance(s, dict) and is_held(s)]


def bad_events(events: list) -> list[str]:
    """Events the API now rejects on write (negative amount, probability outside 0–1)."""
    out = []
    for ev in events:
        if ev.amount_cents < 0:
            out.append(f"{ev.name}: negative amount (an expense with a minus sign projects as INCOME)")
        if ev.probability is not None and not 0 <= ev.probability <= 1:
            out.append(f"{ev.name}: probability {ev.probability} outside 0–1")
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _section(title: str, ok: bool, lines: list[str]) -> None:
    print(f"\n[{'OK' if ok else 'REVIEW'}] {title}")
    for line in lines:
        print(f"    {line}")


async def _last_offset_version(db, model, id_col, row_id, get_ca):
    """(custom_assumptions with offsets, date saved) from the newest history row that still had offsets."""
    rows = (await db.execute(
        select(model).where(id_col == row_id).order_by(model.id.desc())
    )).scalars().all()
    for h in rows:
        ca = get_ca(h.data) or {}
        has_offset = any((ca.get(sec) or {}).get(rel) is not None for sec, rel, _ in PLAN_MONTH_KEYS) or any(
            isinstance(s, dict) and s.get(SALE_KEYS[0]) is not None for s in ca.get("property_sales") or [])
        if has_offset:
            saved = (h.data.get("updated_at") or "")[:10]
            return ca, (date.fromisoformat(saved) if saved else None)
    return None, None


async def _tag_guard_rows(db) -> list[str]:
    """Tagged rows the new tag guards classify differently: a property tag on a
    LOAN account is ignored (principal lines are not rental income), a tagged
    positive on a CREDIT CARD is a refund (contra-expense), never income."""
    from app.engines.property_pnl import (
        PropertyPnLEngine, classify_transaction, match_tag_to_property, resolve_with_tag,
    )
    from app.models.account import Account
    from app.models.enums import AccountType
    from app.models.property import Property
    from app.models.transaction import Transaction

    name_to_id = {n.strip().lower(): pid for pid, n in (await db.execute(select(Property.id, Property.name))).all()}
    if not name_to_id:
        return []
    rules = await PropertyPnLEngine(db)._load_rules()
    rows = (await db.execute(
        select(Transaction.date, Transaction.merchant, Transaction.category, Transaction.amount,
               Transaction.tags, Account.account_type, Account.is_asset, Account.name)
        .join(Account, Account.id == Transaction.account_id)
        .where(Transaction.property_source.is_distinct_from("manual"))
    )).all()
    out = []
    for r in rows:
        tag_pid = match_tag_to_property(r.tags, name_to_id)
        if tag_pid is None:
            continue
        is_cc = r.account_type == AccountType.CREDIT_CARD
        c = classify_transaction(r.merchant, r.category, int(r.amount), rules, is_credit_card=is_cc)
        old = resolve_with_tag(c, tag_pid, int(r.amount), r.category)
        new = resolve_with_tag(c, tag_pid, int(r.amount), r.category,
                               is_credit_card=is_cc, is_loan=not r.is_asset and not is_cc)
        if old[:2] != new[:2]:
            out.append(f"{r.date} {r.name} ${r.amount / 100:,.2f}: {old[1]} → {new[1] or 'not a property row'}")
    return out


async def main() -> None:
    from app.engines.cashflow import CashflowEngine
    from app.models.cashflow_event import CashflowEvent
    from app.models.fire_config import FireConfig
    from app.models.fire_config_history import FireConfigHistory
    from app.models.fire_scenario import FireScenario
    from app.models.fire_scenario_history import FireScenarioHistory

    today = date.today()
    async with async_session_factory() as db:
        config = (await db.execute(select(FireConfig))).scalars().first()
        scenarios = (await db.execute(select(FireScenario))).scalars().all()
        events = (await db.execute(
            select(CashflowEvent).where(CashflowEvent.status.in_(["planned", "confirmed"]))
        )).scalars().all()
        future = [e for e in events if (e.end_date or e.date) >= today]

        print("FIREMaster upgrade check — September 2026 update (read-only)")
        print(f"{len(scenarios)} scenario(s), {len(events)} active cashflow event(s)")

        # 1. Plan months pinned by the update ----------------------------------
        plans = [("Base config", config.custom_assumptions or {} if config else {},
                  *(await _last_offset_version(db, FireConfigHistory, FireConfigHistory.config_id,
                                               config.id, lambda d: d.get("custom_assumptions"))))
                 ] if config else []
        for sc in scenarios:
            cur = (sc.overrides or {}).get("custom_assumptions") or {}
            before, edited = await _last_offset_version(
                db, FireScenarioHistory, FireScenarioHistory.scenario_id, sc.id,
                lambda d: (d.get("overrides") or {}).get("custom_assumptions"))
            plans.append((f"Scenario '{sc.name}'" + (" (active)" if sc.is_active else ""), cur, before, edited))
        lines = []
        for label, cur, before, edited in plans:
            for p in pinned_months(cur, before, edited):
                alt = (f" — if you set it when you last saved this ({edited}), you meant {p['from_last_edit']}"
                       if p["from_last_edit"] and p["from_last_edit"] != p["pinned"] else "")
                lines.append(f"{label}: {p['what']} was 'month {p['offset']} from today' → now {p['pinned']}{alt}")
        _section("Plan months are now calendar dates (they used to slide a month later every month)",
                 not lines, lines or ["no month offsets were stored — nothing was pinned"])

        # 2. Sales on the Runway / double counting -------------------------------
        all_sales = [(label, cur.get("property_sales") or []) for label, cur, _, _ in plans]
        dup = []
        for label, sales in all_sales:
            for name in double_counted_sales(sales, future):
                dup.append(f"{label}: event '{name}' looks like sale proceeds — if this plan also "
                           "models that sale, it is counted twice")
        _section("Property sales vs hand-made sale events (Runway now shows the active plan's sales)",
                 not dup, dup or ["no hand-made sale event overlaps a modeled sale"])

        # 3. Expense events no longer dropped -----------------------------------
        exp = []
        for label, sales in all_sales:
            exp += [f"{label}: '{n}' now counts in Retirement (it was silently dropped)"
                    for n in expense_events_now_counted(sales, future)]
        _section("Expense events named like a sold property", not exp, exp or ["none"])

        # 4. Held properties ------------------------------------------------------
        held = []
        for label, sales in all_sales:
            held += [f"{label}: '{k}' has no sale date → kept for good (before this update an entry "
                     "without a date sold immediately)" for k in held_properties(sales)]
        _section("Property entries without a sale date — confirm you mean to keep these",
                 not held, held or ["none"])

        # 5. Runway burn default ------------------------------------------------
        saved = ((config.custom_assumptions or {}).get("runway") or {}) if config else {}
        if saved.get("monthly_burn") is not None:
            _section("Runway burn default", True, [f"you saved ${saved['monthly_burn']:,.0f}/mo — unchanged"])
        else:
            r = await CashflowEngine(db).project_runway(months=1)
            old = ((config.target_annual_spending or 0) / 1200 + (config.healthcare_monthly_cost or 0) / 100) if config else 0
            _section("Runway burn default changed", False, [
                f"now: last 90 days of spending, minus planned-event payments = ${r.trailing_burn:,.0f}/mo",
                f"before: target spending + healthcare = ${old:,.0f}/mo",
                "to keep the old number: type it into Monthly Burn on the Runway page → Save as default",
            ])

        # 6. Events the API now rejects -----------------------------------------
        bad = bad_events(events)
        _section("Cashflow events with invalid values", not bad, bad or ["none"])

        # 7. Monarch tags on loans / credit cards (Property P&L) ----------------
        tagged = await _tag_guard_rows(db)
        _section("Monarch property tags on loan accounts or card refunds", not tagged, tagged or ["none"])

    print("\nFixes for every REVIEW item: docs/UPGRADING.md")


if __name__ == "__main__":
    asyncio.run(main())
