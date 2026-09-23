"""Plan months pinned to the calendar (sale_date / start_date) instead of sliding offsets."""

from datetime import date
from unittest.mock import patch

import pytest

from app.engines.fire_projections import resolve_month_offset
from tests.conftest import FROZEN_TODAY, _make_fire_config
from tests.test_projection_snapshots import _make_engine


class TestResolveMonthOffset:
    def test_pinned_month_wins_over_the_legacy_offset(self):
        cfg = {"sale_date": "2027-07", "sale_month": 3}
        assert resolve_month_offset(cfg, "sale_date", "sale_month", date(2026, 9, 22)) == 10

    def test_a_day_in_the_pinned_value_is_ignored(self):
        cfg = {"sale_date": "2027-07-31"}
        assert resolve_month_offset(cfg, "sale_date", "sale_month", date(2026, 9, 22)) == 10

    def test_pinned_month_does_not_slide(self):
        cfg = {"sale_date": "2027-07"}
        # Same calendar month, seen from two different "todays".
        assert resolve_month_offset(cfg, "sale_date", "sale_month", date(2026, 6, 2)) == 13
        assert resolve_month_offset(cfg, "sale_date", "sale_month", date(2026, 9, 22)) == 10

    def test_legacy_offset_still_counts_from_today(self):
        cfg = {"sale_month": 13}
        assert resolve_month_offset(cfg, "sale_date", "sale_month", date(2026, 9, 22)) == 13

    def test_past_pinned_month_is_now(self):
        cfg = {"start_date": "2026-01"}
        assert resolve_month_offset(cfg, "start_date", "start_month", date(2026, 9, 22)) == 0

    def test_malformed_pin_falls_back_to_offset(self):
        cfg = {"start_date": "soon", "start_month": 5}
        assert resolve_month_offset(cfg, "start_date", "start_month", date(2026, 9, 22)) == 5

    def test_missing_both_uses_default(self):
        assert resolve_month_offset({}, "start_date", "start_month", date(2026, 9, 22)) == 0


def _config_with(ca_overrides: dict):
    config = _make_fire_config()
    config.custom_assumptions = {**config.custom_assumptions, **ca_overrides}
    return config


SALE = {
    "key": "coastal_condo", "re_bucket": "primary", "value": 500_000, "cost_basis": 500_000,
    "agent_fee_pct": 0.06, "ltcg_rate": 0.15, "current_mortgage_balance": 0,
    "monthly_cost": 5_000, "in_base_burn": True, "proceeds_to": "taxable",
}


@pytest.mark.asyncio
async def test_pinned_sale_and_starts_land_on_their_calendar_month(
    frozen_today, net_worth_breakdown, mock_accounts,
):
    # FROZEN_TODAY = 2026-04-14 → Jul 2027 is 15 months out, Jan 2027 is 9.
    assert FROZEN_TODAY == date(2026, 4, 14)
    config = _config_with({
        "property_sales": [{**SALE, "sale_date": "2027-07", "sale_month": 2}],
        "sepp": {"sepp_monthly": 1_000, "start_date": "2027-01", "sepp_start_month": 1,
                 "ira_a_balance": 300_000, "ira_b_balance": 100_000},
        "rrsp": {"monthly_net": 1_500, "start_date": "2027-03", "start_month": 1,
                 "total_available": 50_000},
    })
    engine = _make_engine(config, net_worth_breakdown, mock_accounts, [])
    r = await engine.project_wealth_pools(end_age=82, bridge_months=24)
    months = {e["label"]: e["month"] for e in r.events}
    assert months["Sell Coastal Condo"] == 15
    assert months["SEPP starts"] == 9
    assert months["RRIF starts"] == 11
    by_month = {p.month: p for p in r.points}
    assert by_month[14].taxable == 0 and by_month[15].taxable > 0  # proceeds arrive in Jul 2027
    assert by_month[8].ira_draw == 0 and by_month[9].ira_draw == 1_000
    assert by_month[10].rrsp_draw == 0 and by_month[11].rrsp_draw == 1_500


@pytest.mark.asyncio
async def test_pinned_sale_holds_its_date_as_today_moves(net_worth_breakdown, mock_accounts):
    config = _config_with({"property_sales": [{**SALE, "sale_date": "2027-07"}]})

    async def sale_month_seen_on(day: date) -> int:
        with patch("app.engines.fire_projections.date") as mock_date:
            mock_date.today.return_value = day
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            engine = _make_engine(config, net_worth_breakdown, mock_accounts, [])
            r = await engine.project_wealth_pools(end_age=82)
        return next(e["month"] for e in r.events if e["label"].startswith("Sell"))

    # Offsets shrink as time passes: both point at July 2027.
    assert await sale_month_seen_on(date(2026, 6, 2)) == 13
    assert await sale_month_seen_on(date(2026, 9, 22)) == 10
