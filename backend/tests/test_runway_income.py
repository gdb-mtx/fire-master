"""Runway declared-income model (the Jul 27 launch blocker fix).

Runway projects DECLARED money only: income sources (dates honored) + events.
Trailing transaction averages are reference display, never a projection input.
Also pins the intentional duplicate: CashflowEngine.modeled_income_for_month
must agree with FireProjectionsEngine._income_at_month until the shared helper
is extracted post-launch.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engines.cashflow import CashflowEngine
from app.engines.fire_projections import FireProjectionsEngine
from app.models.enums import IncomeType
from app.models.income_source import IncomeSource


def _src(name, annual_cents, income_type=IncomeType.OTHER, start=None, end=None, growth=None):
    s = IncomeSource()
    s.name = name
    s.income_type = income_type
    s.annual_amount = annual_cents
    s.start_date = start
    s.end_date = end
    s.growth_rate = growth
    s.is_active = True
    return s


SOURCES = [
    _src("Severance", 76_000_00, end=date(2026, 8, 12)),
    _src("Unemployment", 14_400_00, end=date(2026, 10, 12)),
    _src("Rental", 9_000_00, end=date(2026, 10, 12)),
    _src("Old Salary", 200_000_00, IncomeType.SALARY),  # no end_date -> dies at retirement
    _src("Raise Stream", 12_000_00, growth=4.0),
]
RETIREMENT = date(2026, 9, 1)


class TestEnginesAgree:
    @pytest.mark.parametrize("month", [
        date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1),
        date(2026, 10, 1), date(2027, 6, 1), date(2028, 7, 1),
    ])
    @pytest.mark.parametrize("retirement", [None, RETIREMENT])
    def test_engines_agree(self, month, retirement):
        fire = FireProjectionsEngine(AsyncMock())
        years = (month - date(2026, 7, 1)).days / 365.25
        assert CashflowEngine.modeled_income_for_month(
            SOURCES, month, retirement, years, inflation_pct=3.0
        ) == fire._income_at_month(SOURCES, month, retirement, years, inflation_pct=3.0)


class TestDeclaredRules:
    def test_taper_on_end_date(self):
        # retirement=None so the undated salary persists and cancels out of the diff
        aug = CashflowEngine.modeled_income_for_month(SOURCES, date(2026, 8, 1), None)
        nov = CashflowEngine.modeled_income_for_month(SOURCES, date(2026, 11, 1), None)
        # Severance+unemployment+rental active in Aug; all ended by Nov
        expected = int(76_000_00 / 12) + int(14_400_00 / 12) + int(9_000_00 / 12)
        assert aug - nov == expected

    def test_salary_dies_at_retirement_without_end_date(self):
        pre = CashflowEngine.modeled_income_for_month(SOURCES, date(2026, 8, 1), RETIREMENT)
        post = CashflowEngine.modeled_income_for_month(SOURCES, date(2026, 9, 1), RETIREMENT)
        assert pre - post >= 200_000_00 // 12 - 1


def _cash_account(cents):
    a = MagicMock()
    a.fire_role = "cash_reserve"
    a.is_asset = True
    a.current_balance = cents
    return a


def _outflow(cents, d, merchant="Store", id_=None):
    r = MagicMock()
    r.id = id_ or f"{merchant}-{d.isoformat()}-{cents}"
    r.date = d
    r.amount = -cents
    r.merchant = merchant
    return r


# Default trailing window: $15,000 of spending → $5,000/mo burn.
DEFAULT_OUTFLOWS = [_outflow(5_000_00, date(2026, 5, 10 + i), f"Bill {i}") for i in range(3)]


def _mock_db_for_runway(*, cash_cents, trailing_income_cents, sources, events, outflows=None):
    """AsyncMock db serving project_runway's 6 execute() calls in order."""
    def scalar(v):
        m = MagicMock(); m.scalar.return_value = v; return m
    def scalars_all(v):
        m = MagicMock(); m.scalars.return_value.all.return_value = v; return m
    def scalars_first(v):
        m = MagicMock(); m.scalars.return_value.first.return_value = v; return m
    def rows(v):
        m = MagicMock(); m.all.return_value = v; return m
    db = AsyncMock()
    db.execute.side_effect = [
        scalars_all([_cash_account(cash_cents)]),  # get_current_cash (role-based)
        rows(DEFAULT_OUTFLOWS if outflows is None else outflows),  # trailing outflows
        scalar(trailing_income_cents),   # trailing income (3-mo sum) — THE LUMP
        scalars_all(sources),            # income sources
        scalars_first(None),             # fire config (none -> no retirement date)
        scalars_all(events),             # active events
    ]
    return db


FIRST_OF_MONTH = date(2026, 6, 1)  # month 0 is a whole month
MID_MONTH = date(2026, 6, 21)      # 10 of June's 30 days remain (the 21st included)


def _event(name, cents, d, etype="income"):
    ev = MagicMock()
    ev.amount_cents = cents
    ev.probability = 1.0
    ev.event_type = etype
    ev.is_recurring = False
    ev.recurrence = None
    ev.end_date = None
    ev.name = name
    ev.date = d
    return ev


def _engine(db, sales=(), appreciation=0.0, scenario=None):
    """CashflowEngine with the active plan's pinned sales stubbed (the ordered db
    mock covers only the runway's own queries)."""
    engine = CashflowEngine(db)
    engine._get_plan_sales = AsyncMock(return_value=(list(sales), appreciation, scenario))
    return engine


class TestCurrentMonthIsPartial:
    """Month 0 is only the rest of this month: today's balance already holds
    everything that cleared since the 1st."""

    @pytest.mark.asyncio
    async def test_month_zero_carries_the_remaining_fraction(self):
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=0,
            sources=[_src("Consulting", 12_000_00)], events=[],   # $1,000/mo
        )  # trailing burn mock: $5,000/mo
        r = await _engine(db).project_runway(months=3, today=MID_MONTH)
        first, second = r.projection[0], r.projection[1]
        assert first.expenses == pytest.approx(5_000 / 3, abs=0.01)
        assert first.income == pytest.approx(1_000 / 3, abs=0.01)
        assert first.from_day == 21
        assert (second.expenses, second.income, second.from_day) == (5_000.0, 1_000.0, None)
        assert r.monthly_burn == 5_000.0  # headline rates stay monthly

    @pytest.mark.asyncio
    async def test_event_earlier_this_month_is_already_in_the_balance(self):
        paid = _event("Boat payment", 4_000_00, date(2026, 6, 5), etype="expense")
        due = _event("Refund", 2_000_00, date(2026, 6, 25))
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=0, sources=[], events=[paid, due],
        )
        r = await _engine(db).project_runway(months=3, today=MID_MONTH)
        assert r.projection[0].events == ["Refund"]
        assert r.projection[0].expenses == pytest.approx(5_000 / 3, abs=0.01)  # burn only
        assert r.projection[0].income == 2_000.0

    @pytest.mark.asyncio
    async def test_cash_zero_inside_month_zero_counts_from_today(self):
        db = _mock_db_for_runway(
            cash_cents=1_000_00, trailing_income_cents=0, sources=[], events=[],
        )  # $5,000/mo burn → $1,666.67 over the 10 remaining days ($166.67/day); $1,000 lasts 6
        r = await _engine(db).project_runway(months=3, today=MID_MONTH)
        assert r.cash_zero_date == date(2026, 6, 27)


class TestLumpImmunityAndEvents:
    @pytest.mark.asyncio
    async def test_lump_never_becomes_recurring_income(self):
        """$29,645 one-off in the trailing window must not appear in any projected month."""
        db = _mock_db_for_runway(
            cash_cents=50_000_00,
            trailing_income_cents=3 * 22_077_00,  # contaminated trailing mean
            sources=[_src("Consulting", 12_000_00)],  # $1,000/mo declared
            events=[],
        )
        r = await _engine(db).project_runway(months=6, today=FIRST_OF_MONTH)
        assert all(p.income == 1000.0 for p in r.projection)
        assert r.monthly_income == 1000.0 and r.income_provenance == "modeled"
        assert r.trailing_income == 22077.0  # still visible — as reference only

    @pytest.mark.asyncio
    async def test_zero_income_override_is_honored_and_modeled_figure_still_reported(self):
        """A 0 override means "no income" — sources drop out of the projection —
        while the sources figure is still returned so the page's reference line
        does not change when the user types."""
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=3 * 4_870_00,
            sources=[_src("Consulting", 12_000_00)], events=[],  # $1,000/mo declared
        )
        r = await _engine(db).project_runway(months=6, income_override=0, today=FIRST_OF_MONTH)
        assert all(p.income == 0.0 for p in r.projection)
        assert r.monthly_income == 0.0 and r.income_provenance == "override"
        assert r.modeled_income == 1000.0   # what sources would give, unchanged by the override
        assert r.trailing_income == 4870.0  # reference only

    @pytest.mark.asyncio
    async def test_event_counted_once_in_its_month(self):
        ev = MagicMock()
        ev.amount_cents = 28_000_00
        ev.probability = 1.0
        ev.event_type = "income"
        ev.is_recurring = False
        ev.name = "Remaining Severance"
        ev.date = FIRST_OF_MONTH
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=0,
            sources=[_src("Consulting", 12_000_00)], events=[ev],
        )
        r = await _engine(db).project_runway(months=6, today=FIRST_OF_MONTH)
        assert r.projection[0].income == 29000.0  # source + event, once
        assert all(p.income == 1000.0 for p in r.projection[1:])

    @pytest.mark.asyncio
    async def test_speculative_not_counted_as_cash(self):
        """Crypto under the speculative role is inert — not runway cash (Jul 27 decision)."""
        crypto = MagicMock(); crypto.fire_role = "speculative"; crypto.is_asset = True; crypto.current_balance = 14_000_00
        db = AsyncMock()
        m = MagicMock(); m.scalars.return_value.all.return_value = [_cash_account(85_000_00), crypto]
        db.execute.return_value = m
        from app.engines.cashflow import CashflowEngine as CE
        assert await CE(db).get_current_cash() == 85_000.0

    @pytest.mark.asyncio
    async def test_virgin_install_falls_back_to_account_type(self):
        """No liquid roles enriched -> type-based fallback, not $0."""
        from app.models.enums import AccountType
        chk = MagicMock(); chk.fire_role = None; chk.is_asset = True
        chk.current_balance = 21_000_00; chk.account_type = AccountType.CHECKING
        db = AsyncMock()
        m = MagicMock(); m.scalars.return_value.all.return_value = [chk]
        db.execute.return_value = m
        from app.engines.cashflow import CashflowEngine as CE
        assert await CE(db).get_current_cash() == 21_000.0

    @pytest.mark.asyncio
    async def test_no_sources_fails_conservative(self):
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=3 * 22_077_00,
            sources=[], events=[],
        )
        r = await _engine(db).project_runway(months=24, today=FIRST_OF_MONTH)
        assert r.monthly_income == 0.0  # income is 0 + events, not the trailing mirage
        assert r.months_remaining is not None  # burn > 0 -> cash zero is found in-window


CONDO_SALE = {
    "key": "condo", "sale_date": "2026-09", "value": 300_000, "cost_basis": 300_000,
    "agent_fee_pct": 0.0, "ltcg_rate": 0.15, "current_mortgage_balance": 0,
    "monthly_cost": 2_000, "post_sale_rent": 500, "monthly_income": 1_000,
    "proceeds_to": "taxable", "suppress_cashflow_match": "condo sale",
}


class TestPlanSalesDriveTheRunway:
    """The active plan's pinned property sales reach the Runway (one source, one date)."""

    @pytest.mark.asyncio
    async def test_proceeds_land_and_costs_stop_from_the_sale_month(self):
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=0,
            sources=[_src("Condo rent", 12_000_00)], events=[],   # $1,000/mo
        )  # burn $5,000/mo
        r = await _engine(db, sales=[CONDO_SALE], scenario="Sell the condo").project_runway(
            months=6, today=FIRST_OF_MONTH)
        before, sale_month, after = r.projection[2], r.projection[3], r.projection[4]
        assert (before.income, before.expenses) == (1_000.0, 5_000.0)
        assert sale_month.month == "2026-09"
        assert sale_month.income == pytest.approx(300_000.0)   # proceeds; rent stopped
        assert sale_month.expenses == 5_000 - 2_000 + 500
        assert (after.income, after.expenses) == (0.0, 3_500.0)
        assert "Sell Condo (+$300,000\u2192taxable)" in sale_month.events
        assert r.scenario_name == "Sell the condo"
        assert [(x.key, x.month, x.month_index) for x in r.scenario_sales] == [("condo", "2026-09", 3)]

    @pytest.mark.asyncio
    async def test_the_sale_replaces_its_hand_made_income_event_only(self):
        manual = _event("Condo Sale Proceeds", 250_000_00, date(2026, 9, 15))
        assessment = _event("Condo sale-prep assessment", 3_000_00, date(2026, 8, 1), etype="expense")
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=0, sources=[], events=[manual, assessment],
        )
        r = await _engine(db, sales=[CONDO_SALE]).project_runway(months=6, today=FIRST_OF_MONTH)
        names = [n for p in r.projection for n in p.events]
        assert "Condo Sale Proceeds" not in names          # the plan owns the sale
        assert "Condo sale-prep assessment" in names       # real money out stays
        assert r.projection[3].income == pytest.approx(300_000.0)  # counted once


@pytest.mark.asyncio
async def test_only_pinned_sales_reach_the_runway():
    from app.engines import fire_projections

    config = MagicMock()
    config.custom_assumptions = {
        "property_sales": [
            {**CONDO_SALE},
            {"key": "cabin", "sale_month": 3, "value": 100_000},   # legacy rolling offset
        ],
        "projection": {"re_appreciation_rate": 0.02},
    }
    db = AsyncMock()
    name = MagicMock(); name.scalar_one_or_none.return_value = "Plan B"
    db.execute.return_value = name
    with patch.object(fire_projections.FireProjectionsEngine, "get_effective_config",
                      AsyncMock(return_value=config)):
        sales, appreciation, scenario = await CashflowEngine(db)._get_plan_sales()
    assert [s["key"] for s in sales] == ["condo"]
    assert (appreciation, scenario) == (0.02, "Plan B")


class TestBurnBaselineExcludesModeledEvents:
    """A payment that paid a cashflow event is the event — already modeled — and
    must not be averaged into the burn and projected forward as if it recurred."""

    @pytest.mark.asyncio
    async def test_event_matched_payment_leaves_the_average(self):
        today = date(2026, 9, 22)
        assessment = _event("Assessment #4", 4_400_00, date(2026, 7, 14), etype="expense")
        outflows = [
            _outflow(4_429_04, date(2026, 7, 14), "HOA portal"),        # paid the assessment
            _outflow(4_500_00, date(2026, 7, 20), "Rent"),
            _outflow(4_500_00, date(2026, 8, 20), "Rent"),
            _outflow(4_500_00, date(2026, 9, 20), "Rent"),
        ]
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=0, sources=[], events=[assessment],
            outflows=outflows,
        )
        r = await _engine(db).project_runway(months=3, today=today)
        assert r.trailing_burn_raw == pytest.approx((3 * 4_500 + 4_429.04) / 3, abs=0.01)
        assert r.trailing_burn == pytest.approx(4_500.0, abs=0.01)
        assert r.monthly_burn == pytest.approx(4_500.0, abs=0.01)  # the default is the clean one
        [ex] = r.burn_exclusions
        assert (ex.event_name, ex.amount, ex.date) == ("Assessment #4", 4_429.04, date(2026, 7, 14))

    @pytest.mark.asyncio
    async def test_unmatched_one_off_stays_in(self):
        # No event describes it, so nothing says it won't recur — it stays.
        outflows = [_outflow(2_721_00, date(2026, 9, 21), "Tax payment")]
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=0, sources=[], events=[], outflows=outflows,
        )
        r = await _engine(db).project_runway(months=3, today=date(2026, 9, 22))
        assert r.trailing_burn == r.trailing_burn_raw == pytest.approx(907.0, abs=0.01)
        assert r.burn_exclusions == []
