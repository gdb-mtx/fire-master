"""Bridge-chart start markers come from the plan, not from a fixed month."""

import pytest

from tests.conftest import _make_fire_config
from tests.test_projection_snapshots import _make_engine


def _labels(result) -> dict[str, int]:
    return {e["label"]: e["month"] for e in result.events}


def _config(sepp: dict, rrsp: dict):
    config = _make_fire_config()
    ca = dict(config.custom_assumptions)
    ca["sepp"] = {**ca.get("sepp", {}), **sepp}
    ca["rrsp"] = rrsp
    config.custom_assumptions = ca
    return config


@pytest.mark.asyncio
async def test_markers_follow_configured_start_months(
    frozen_today, net_worth_breakdown, mock_accounts,
):
    config = _config(
        {"sepp_monthly": 2_000, "sepp_start_month": 7},
        {"monthly_net": 1_500, "start_month": 18, "total_available": 100_000},
    )
    engine = _make_engine(config, net_worth_breakdown, mock_accounts, [])
    labels = _labels(await engine.project_wealth_pools(end_age=82))
    assert labels["SEPP starts"] == 7
    assert labels["RRIF starts"] == 18


@pytest.mark.asyncio
async def test_no_markers_when_draws_are_off(frozen_today, net_worth_breakdown, mock_accounts):
    config = _config({"sepp_monthly": 0, "sepp_start_month": 12}, {})
    engine = _make_engine(config, net_worth_breakdown, mock_accounts, [])
    labels = _labels(await engine.project_wealth_pools(end_age=82))
    assert "SEPP starts" not in labels
    assert "RRIF starts" not in labels
