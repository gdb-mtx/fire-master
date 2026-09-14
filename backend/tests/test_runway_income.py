"""Runway declared-income model (the Jul 27 launch blocker fix).

Runway projects DECLARED money only: income sources (dates honored) + events.
Trailing transaction averages are reference display, never a projection input.
Also pins the intentional duplicate: CashflowEngine.modeled_income_for_month
must agree with FireProjectionsEngine._income_at_month until the shared helper
is extracted post-launch.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

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
    s.custom_data = None
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
    def test_net_projection_override_does_not_use_gross_income(self):
        source = _src("Gross salary", 1_200_000_00, IncomeType.SALARY)
        source.custom_data = {"net_annual_amount": 823_669_04}

        projected = CashflowEngine.modeled_income_for_month(
            [source], date(2026, 8, 1), None,
        )

        assert projected == int(823_669_04 / 12)

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


def _mock_db_for_runway(*, cash_cents, trailing_income_cents, sources, events):
    """AsyncMock db serving project_runway's 6 execute() calls in order."""
    def scalar(v):
        m = MagicMock(); m.scalar.return_value = v; return m
    def scalars_all(v):
        m = MagicMock(); m.scalars.return_value.all.return_value = v; return m
    def scalars_first(v):
        m = MagicMock(); m.scalars.return_value.first.return_value = v; return m
    db = AsyncMock()
    db.execute.side_effect = [
        scalars_all([_cash_account(cash_cents)]),  # get_current_cash (role-based)
        scalar(3 * 5_000_00),            # trailing burn (3-mo sum)
        scalar(trailing_income_cents),   # trailing income (3-mo sum) — THE LUMP
        scalars_all(sources),            # income sources
        scalars_first(None),             # fire config (none -> no retirement date)
        scalars_all(events),             # active events
    ]
    return db


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
        r = await CashflowEngine(db).project_runway(months=6)
        assert all(p.income == 1000.0 for p in r.projection)
        assert r.monthly_income == 1000.0 and r.income_provenance == "modeled"
        assert r.trailing_income == 22077.0  # still visible — as reference only

    @pytest.mark.asyncio
    async def test_event_counted_once_in_its_month(self):
        ev = MagicMock()
        ev.amount_cents = 28_000_00
        ev.probability = 1.0
        ev.event_type = "income"
        ev.is_recurring = False
        ev.name = "Remaining Severance"
        ev.date = date.today().replace(day=1)
        db = _mock_db_for_runway(
            cash_cents=50_000_00, trailing_income_cents=0,
            sources=[_src("Consulting", 12_000_00)], events=[ev],
        )
        r = await CashflowEngine(db).project_runway(months=6)
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
        r = await CashflowEngine(db).project_runway(months=24)
        assert r.monthly_income == 0.0  # income is 0 + events, not the trailing mirage
        assert r.months_remaining is not None  # burn > 0 -> cash zero is found in-window
