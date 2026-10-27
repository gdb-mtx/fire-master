"""Open every IRA after 59½ (projection.open_all_iras_after_59_5), Oct 2026.

Unset, only IRA-B is drawn by choice and a cash hole is repaired from the
brokerage and the Roth alone; tests/test_wealth_pools_golden.py proves that path
byte for byte. Set, the 72(t) account (IRA-A) pays too once it opens, and in
threshold mode the IRAs repair a cash hole from 59½.

The 72(t) account opens at the LATER of 59½ and five years after the series'
first payment (an earlier extra withdrawal "modifies" the series: the 10%
penalty comes back on every payment so far, with interest).

Frozen today is 2026-04-14 (month 0 = Apr 2026). Interest and growth are off
wherever an exact dollar figure matters.
"""

from copy import deepcopy
from datetime import date

import pytest

from tests.conftest import SCENARIO_KEEP_BOTH, _apply_scenario, _make_fire_config
from tests.test_cash_cushion import QUIET, _engine, _run, assert_money_identity
from tests.test_projection_snapshots import _make_engine
from tests.test_wealth_pools_golden import GOLDEN, fingerprint

OPEN = {"open_all_iras_after_59_5": True}
DOB_52 = date(1973, 7, 1)  # 52.8 today; 59½ is month 80 (Dec 2032)
DOB_58 = date(1968, 4, 1)  # 58.0 today; 59½ is month 17
DOB_61 = date(1965, 1, 1)  # 61.3 today: every IRA but a running 72(t) is open now


def _ca(*, ira_a=0.0, ira_b=0.0, sepp=0.0, sepp_start=None, taxable=0.0, roth=0.0, **projection):
    ca = {
        "sepp": {"ira_a_balance": ira_a, "ira_b_balance": ira_b, "sepp_monthly": sepp,
                 "ira_growth_rate": 0.0},
        "taxable_pool": {"starting_balance": taxable, "return_rate": 0.0},
        "projection": {**QUIET, **projection},
    }
    if sepp_start:
        ca["sepp"]["start_date"] = sepp_start
    if roth:
        ca["roth_pool"] = {"starting_balance": roth, "return_rate": 0.0}
    return ca


def _milestone(r, label):
    return next((x for x in r.milestones if x.label == label), None)


# ── When the 72(t) account opens ─────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("dob, sepp_start, opens", [
    (DOB_52, "2026-07", 80),   # started at 53: five years end at 58, so 59½ decides
    (DOB_52, "2030-07", 111),  # started at 57: five years end at 62 (Jul 2035)
    (DOB_58, "2024-04", 36),   # started at 56, two years ago: 61, not five years from today
    (DOB_52, "2034-01", 80),   # starts after 59½: no penalty to protect, opens at 59½
], ids=["started-53", "started-57", "started-in-the-past", "starts-after-59.5"])
async def test_the_72t_account_opens_at_the_later_of_59_5_and_five_years(frozen_today, dob, sepp_start, opens):
    ca = _ca(ira_a=500_000, ira_b=100_000, sepp=2_000, sepp_start=sepp_start, **OPEN)
    r, _ = await _run(_engine(1_000_000, ca, dob=dob), months=12)
    assert r.cash_rule.open_all_iras is True
    assert r.cash_rule.ira_a_open_month == opens
    marker = _milestone(r, "72(t) account opens")
    if opens == r.cash_rule.age_59_5_month:
        assert marker is None  # the "Age 59½" milestone already says it
    else:
        assert marker.month == opens and marker.kind == "draw"


@pytest.mark.asyncio
async def test_without_a_series_the_72t_account_opens_at_59_5(frozen_today):
    r, _ = await _run(_engine(1_000_000, _ca(ira_a=500_000, ira_b=100_000, **OPEN)), months=12)
    assert r.cash_rule.ira_a_open_month == r.cash_rule.age_59_5_month == 80
    assert _milestone(r, "72(t) account opens") is None


@pytest.mark.asyncio
async def test_unset_never_opens_the_72t_account(frozen_today):
    r, _ = await _run(_engine(1_000_000, _ca(ira_a=500_000, ira_b=100_000, sepp=2_000,
                                             sepp_start="2030-07")), months=12)
    assert r.cash_rule.open_all_iras is False and r.cash_rule.ira_a_open_month is None
    assert r.cash_rule.age_59_5_month == 80
    assert _milestone(r, "72(t) account opens") is None


# ── What the IRAs pay ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_72t_account_pays_only_its_payments_until_it_opens(frozen_today):
    # $10K a month of spending, no other money: cash falls $10K a month, then $8K
    # once the $2K payments start in Jul 2030 (month 51). 59½ (month 80) opens
    # nothing here: IRA-B is empty and the series runs to Jul 2035 (month 111).
    ca = _ca(ira_a=2_000_000, sepp=2_000, sepp_start="2030-07", **OPEN)
    r, pts = await _run(_engine(0, ca), months=400)
    assert all(pts[m].ira_draw == 2_000 for m in range(51, 111))
    assert pts[110].cash == pytest.approx(-990_000, abs=1)
    # Month 111: the payment, the month's other $8K, and the $990K hole.
    assert pts[111].ira_draw == pytest.approx(1_000_000, abs=1) and pts[111].cash == 0
    assert pts[112].ira_draw == 10_000 and pts[112].cash == 0
    # $880K left at $10K a month: the account is empty in month 199, and with it the plan.
    assert r.sepp_depletes_age == pytest.approx(52.79 + 199 / 12, abs=0.1)
    assert r.money_runs_out_month == 199
    # Unset, the account pays its $2K and nothing more, to the end.
    r0, p0 = await _run(_engine(0, {**ca, "projection": QUIET}), months=400)
    assert p0[111].ira_draw == p0[200].ira_draw == 2_000 and p0[200].cash < -1_000_000
    assert r0.money_runs_out_month is None


@pytest.mark.asyncio
async def test_the_iras_close_a_cash_hole_from_59_5(frozen_today):
    # Threshold rule. Before 59½ nothing can be drawn and cash falls to −$800K.
    ca = _ca(ira_b=2_000_000, **OPEN)
    _, pts = await _run(_engine(0, ca), months=120)
    assert pts[79].cash == -800_000
    assert pts[80].ira_draw == 810_000 and pts[80].cash == 0  # the month's $10K and the hole
    assert pts[81].ira_draw == 10_000 and pts[81].cash == 0
    # Unset, IRA-B pays each month's $10K but the hole never closes.
    _, p0 = await _run(_engine(0, _ca(ira_b=2_000_000)), months=120)
    assert p0[80].ira_draw == 10_000 and p0[80].cash == p0[119].cash == -800_000


@pytest.mark.asyncio
async def test_the_cushion_refill_reaches_the_72t_account(frozen_today):
    # Age 61, no series: IRA-B ($30K) goes first, the 72(t) account fills the rest.
    ca = _ca(ira_a=500_000, ira_b=30_000, cash_cushion_months=6, **OPEN)
    _, pts = await _run(_engine(0, ca, dob=DOB_61), months=24)
    assert pts[0].ira_draw == 70_000 and pts[0].cash == 60_000  # the $10K month + the $60K cushion
    assert pts[1].ira_draw == 10_000 and pts[1].cash == 60_000
    # Unset, IRA-B alone refills, and cash runs dry beside $500K.
    _, p0 = await _run(_engine(0, _ca(ira_a=500_000, ira_b=30_000, cash_cushion_months=6), dob=DOB_61),
                       months=24)
    assert p0[0].ira_draw == 30_000 and p0[0].cash == 20_000 and p0[3].cash == -10_000


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [{}, {"cash_cushion_months": 6}], ids=["threshold", "cushion"])
async def test_the_roth_is_still_last(frozen_today, mode):
    # IRA-B $30K, then the 72(t) account $50K, then the Roth.
    ca = _ca(ira_a=50_000, ira_b=30_000, roth=100_000, **mode, **OPEN)
    r, pts = await _run(_engine(0, ca, dob=DOB_61), months=24)
    first_roth = next(m for m in sorted(pts) if pts[m].roth_draw > 0)
    assert sum(pts[m].ira_draw for m in range(first_roth + 1)) == 80_000
    assert all(pts[m].roth_draw == 0 for m in range(first_roth))
    assert first_roth == (2 if mode else 8)  # cushion: $70K month 0, $10K month 1; threshold: $10K a month
    assert_money_identity(r)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [{}, {"cash_cushion_months": 6, "cash_cushion_floor": 25_000}],
                         ids=["threshold", "cushion"])
async def test_money_identity_when_every_branch_fires(frozen_today, mode):
    # Locked IRAs, a 72(t) series that outlasts 59½, the repair, the Roth.
    ca = _ca(ira_a=900_000, ira_b=150_000, sepp=2_000, sepp_start="2030-07", roth=60_000,
             taxable=40_000, **mode, **OPEN)
    r, _ = await _run(_engine(20_000, ca), months=600, end_age=95)
    assert any(p.taxable_draw for p in r.points) and any(p.roth_draw for p in r.points)
    assert any(p.ira_draw > p.sepp_draw + 1 for p in r.points if p.month >= 111)
    assert_money_identity(r)


@pytest.mark.asyncio
async def test_explicit_false_is_the_default_path(
    frozen_today, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources,
):
    config = _make_fire_config()
    _apply_scenario(config, deepcopy(SCENARIO_KEEP_BOTH))
    config.custom_assumptions["projection"] = {**config.custom_assumptions.get("projection", {}),
                                               "open_all_iras_after_59_5": False}
    engine = _make_engine(config, net_worth_breakdown, mock_accounts,
                          mock_cashflow_events, mock_income_sources)
    r = await engine.project_wealth_pools(end_age=82, bridge_months=12 * 60)
    assert fingerprint(r) == GOLDEN["keep_both"]


# ── Words ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_assumptions_name_the_unlock_dates(frozen_today):
    off, _ = await _run(_engine(10_000, _ca(ira_a=500_000, ira_b=100_000, sepp=2_000,
                                            sepp_start="2030-07")), months=12)
    a = {x.key: x for x in off.assumptions}
    assert a["ira_access"].value == "one IRA only" and a["ira_access"].kind == "rule"

    early, _ = await _run(_engine(10_000, _ca(ira_a=500_000, ira_b=100_000, sepp=2_000,
                                              sepp_start="2026-07", **OPEN)), months=12)
    a = {x.key: x for x in early.assumptions}
    assert a["ira_access"].value == "every IRA from 59½" and a["ira_access"].kind == "setting"
    assert a["ira_access"].detail == ("Every IRA can pay the bills from 59½, in Dec 2032, "
                                      "the 72(t) account included.")

    late, _ = await _run(_engine(10_000, _ca(ira_a=500_000, ira_b=100_000, sepp=2_000,
                                             sepp_start="2030-07", cash_cushion_months=6, **OPEN)),
                         months=12)
    a = {x.key: x for x in late.assumptions}
    assert a["ira_access"].value == "72(t) account from Jul 2035"
    assert "joins in Jul 2035, five years after its first payment" in a["ira_access"].detail
    assert "10% penalty" in a["ira_access"].detail
    assert "the IRAs after 59½ (the 72(t) account from Jul 2035)" in a["cash_rule"].detail

    # Only a 72(t) account, no IRA-B: the cushion still names the IRAs as a refill.
    solo, _ = await _run(_engine(10_000, _ca(ira_a=500_000, cash_cushion_months=6, **OPEN)), months=12)
    assert "the brokerage, then the IRAs after 59½." in {x.key: x for x in solo.assumptions}["cash_rule"].detail
