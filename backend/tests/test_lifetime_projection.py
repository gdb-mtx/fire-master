"""Frame + liveness tests for the single-pool lifetime projection and the
FIRE number.

Two regressions pinned here:
- compute_fire_number imports a constant from monte_carlo (DEFAULT_RETURN_STD);
  a rename there once broke /api/fire/number|readiness|metrics with an
  ImportError that no test caught. test_fire_number_executes pins the import
  path end-to-end.
- project_lifetime was NOMINAL until Jul 2026 (inflated spending, COLA'd SS,
  nominal returns). It is now real-terms like every other engine: flat flows,
  real compounding. The flat-flow tests fail loudly against nominal code.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engines.fire_projections import FireProjectionsEngine
from app.engines.net_worth import NetWorthEngine

from .conftest import _make_fire_config


def _lifetime_engine():
    return FireProjectionsEngine(db=None)


def _patch_common(config, *, net_worth=1_500_000.0, spending_cents=15_300_000,
                  income_cents=0, sources=(), events=()):
    return [
        patch.object(FireProjectionsEngine, "_get_cashflow_events",
                     AsyncMock(return_value=list(events))),
        patch.object(FireProjectionsEngine, "get_effective_config",
                     AsyncMock(return_value=config)),
        patch.object(FireProjectionsEngine, "_get_annual_spending",
                     AsyncMock(return_value=spending_cents)),
        patch.object(FireProjectionsEngine, "_get_annual_income",
                     AsyncMock(return_value=income_cents)),
        patch.object(FireProjectionsEngine, "_get_income_sources",
                     AsyncMock(return_value=list(sources))),
        patch.object(NetWorthEngine, "calculate_current",
                     AsyncMock(return_value=MagicMock(net_worth=net_worth))),
    ]


async def test_fire_number_executes_and_is_positive(base_fire_config, frozen_today, net_worth_breakdown):
    """Pins the monte_carlo constant import (once broke with ImportError) and
    the real-return discounting path end-to-end."""
    engine = _lifetime_engine()
    patches = _patch_common(base_fire_config) + [
        patch.object(FireProjectionsEngine, "_compute_net_worth_breakdown",
                     AsyncMock(return_value=net_worth_breakdown)),
    ]
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = await engine.compute_fire_number()
    assert result.fire_number > 0
    assert 0 <= result.progress_pct


async def test_fire_number_uses_withdrawal_rate_and_extra_healthcare(
    base_fire_config, frozen_today, net_worth_breakdown,
):
    """The headline is an auditable SWR target, not the lower spend-to-zero PV."""
    config = base_fire_config
    config.safe_withdrawal_rate = 3.0
    config.target_retirement_age = 52
    config.medicare_start_age = 65
    config.healthcare_monthly_cost = 100_000  # $1,000/mo extra
    engine = _lifetime_engine()
    patches = _patch_common(config, spending_cents=12_000_000) + [
        patch.object(FireProjectionsEngine, "_compute_net_worth_breakdown",
                     AsyncMock(return_value=net_worth_breakdown)),
    ]
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = await engine.compute_fire_number()

    assert result.annual_spending == 132_000
    assert result.fire_number == 4_400_000
    assert result.lifetime_spend_down_number != result.fire_number
    assert result.taxes_included is False


async def test_lifetime_spending_is_flat_real(base_fire_config, frozen_today):
    """Same-phase retirement spending must be IDENTICAL across years — the
    real-terms signature (nominal code inflated it ~3%/yr)."""
    config = base_fire_config
    config.healthcare_monthly_cost = None  # avoid the Medicare step for a clean pin
    engine = _lifetime_engine()
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in _patch_common(config):
            stack.enter_context(p)
        result = await engine.project_lifetime("moderate")

    # Retired, pre-70 (no phase step), yearly points
    window = [p for p in result.points if p.phase == "retirement" and p.age < 69]
    assert len(window) >= 3
    assert len({p.spending for p in window}) == 1  # flat


async def test_lifetime_ss_is_flat_real(base_fire_config, frozen_today):
    """Post-SS income must be IDENTICAL across years — COLA offsets inflation
    (nominal code compounded a 2.5% COLA)."""
    config = base_fire_config
    config.healthcare_monthly_cost = None
    engine = _lifetime_engine()
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in _patch_common(config):
            stack.enter_context(p)
        result = await engine.project_lifetime("moderate")

    # SS starts at 67 (persona). Compare income across post-SS years.
    post_ss = [p for p in result.points if p.age >= 68]
    assert len(post_ss) >= 3
    assert len({p.income for p in post_ss}) == 1  # flat
    assert post_ss[0].income == pytest.approx(4_650 * 12, abs=1)


async def test_lifetime_conservative_below_optimistic(base_fire_config, frozen_today):
    """Scenario adjusters still order outcomes after deflation: conservative
    (nominal −2, inflation +1) < moderate < optimistic in real end wealth.

    Uses a surviving portfolio — once a portfolio goes negative this simple
    model compounds the hole at the return rate, which inverts the ordering
    (a known quirk; money_lasts is the signal for that regime)."""
    config = base_fire_config
    engine = _lifetime_engine()
    from contextlib import ExitStack
    results = {}
    for scenario in ("conservative", "moderate", "optimistic"):
        with ExitStack() as stack:
            for p in _patch_common(config, net_worth=5_000_000.0):
                stack.enter_context(p)
            results[scenario] = await engine.project_lifetime(scenario)
    assert results["moderate"].money_lasts
    assert (results["conservative"].net_worth_at_end
            < results["moderate"].net_worth_at_end
            < results["optimistic"].net_worth_at_end)
