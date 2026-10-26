"""Golden master for the pool engine's DEFAULT path: every month, every field.

The snapshot tests allow $100K of drift; this file allows none. Each case runs
project_wealth_pools() month by month to age 82 (95 for the RMD case) and
fingerprints the full output: every point field that existed before the
Oct 2026 Retirement redesign, the chart events, and the summary numbers.

The fingerprints were RECORDED AGAINST THE ENGINE BEFORE the opt-in cash
cushion landed (commit history shows this file passing on that engine). A
change that moves any number for a config that does not set the new keys fails
here. Opt-in features add point fields; the fingerprint ignores fields it does
not list, so additions never trip it, changes always do.

Re-record ONLY for an intended default-path change, and say so in the commit:
    GOLDEN_RECORD=1 uv run pytest tests/test_wealth_pools_golden.py -s
"""

import hashlib
import json
import os
from copy import deepcopy

import pytest

from tests.conftest import (
    SCENARIO_KEEP_BOTH,
    SCENARIO_PRIMARY_5YR,
    SCENARIO_SELL_ALL_THREE,
    SCENARIO_SELL_INCOME_PROPERTY,
    _apply_scenario,
    _make_fire_config,
)
from tests.test_projection_snapshots import _make_engine

# Point fields as they stood before the redesign. Fields added later are
# deliberately absent: they are new outputs, not changes to old ones.
LEGACY_POINT_FIELDS = (
    "date", "age", "cash", "ira_sepp", "ira_growth", "rrsp", "real_estate", "illiquid",
    "taxable", "taxable_draw", "roth", "roth_draw", "total", "income", "expenses",
    "ira_draw", "rmd_redeposit", "rrsp_draw", "cash_interest", "month", "event",
)
LEGACY_SUMMARY_FIELDS = (
    "events", "cash_zero_month", "total_at_end", "sepp_monthly", "sepp_depletes_age",
    "demo_persona",
)

ROTH_AND_LOW_BUFFER = {
    **SCENARIO_SELL_ALL_THREE,
    "roth_pool": {"starting_balance": 90_000, "return_rate": 0.05},
    "projection": {"ira_b_draw_threshold_months": 3},
}
RMD_HEAVY = {
    "sepp": {"ira_a_balance": 0, "ira_b_balance": 900_000, "sepp_monthly": 0,
             "ira_growth_rate": 0.05},
    "taxable_pool": {"starting_balance": 250_000, "return_rate": 0.05},
}

CASES = {
    "base": (None, 82),
    "keep_both": (SCENARIO_KEEP_BOTH, 82),
    "primary_5yr": (SCENARIO_PRIMARY_5YR, 82),
    "sell_income_property": (SCENARIO_SELL_INCOME_PROPERTY, 82),
    "sell_all_three": (SCENARIO_SELL_ALL_THREE, 82),
    "roth_low_buffer": (ROTH_AND_LOW_BUFFER, 82),
    "rmd_heavy": (RMD_HEAVY, 95),
}

GOLDEN = {
    "base": "11f98218e2ebc389a5ffda9a7ceae6c35233f51ed9050f105a1248dc9ad94ac3",
    "keep_both": "78efbf920afd31d4bd958727f9e3fda31e025fc33207be40440653b63f4e6157",
    "primary_5yr": "d8daf4e802fe5434b8232817977b1b0fa9b2e5742bf76c0df434f305945352c7",
    "sell_income_property": "1eb52c4da9fa66d2ca0aecc0ff2d51a5e6d8d6ff5666fa5ed1d13b54283930e1",
    "sell_all_three": "a43893cad4766866c88cfd461280db83e19502d219bd8d1ddc65e0521ef607c6",
    "roth_low_buffer": "23e756f598df62b1c3d88986d89abcb74c5ef7cfa7e601a0f59e16fe9807829f",
    "rmd_heavy": "798e2156969cfedfe38db8705042f8d53efa34dd2e522e48ad0b07904ec87a83",
}


# Each case must actually reach the branch it is here for, or its fingerprint
# guards nothing: the field must be non-zero in at least one month.
EXERCISES = {
    "keep_both": ("rrsp_draw", "ira_draw"),
    "sell_all_three": ("taxable_draw",),
    "roth_low_buffer": ("roth_draw", "taxable_draw"),
    "rmd_heavy": ("rmd_redeposit", "taxable_draw"),
}


def fingerprint(result) -> str:
    dump = result.model_dump()
    body = {
        "points": [{k: p[k] for k in LEGACY_POINT_FIELDS} for p in dump["points"]],
        **{k: dump[k] for k in LEGACY_SUMMARY_FIELDS},
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(CASES))
async def test_default_path_is_unchanged(
    case, frozen_today, net_worth_breakdown, mock_accounts, mock_cashflow_events,
    mock_income_sources,
):
    overrides, end_age = CASES[case]
    config = _make_fire_config()
    if overrides:
        _apply_scenario(config, deepcopy(overrides))
    engine = _make_engine(config, net_worth_breakdown, mock_accounts,
                          mock_cashflow_events, mock_income_sources)
    result = await engine.project_wealth_pools(end_age=end_age, bridge_months=12 * 60)
    assert len(result.points) > 100, "monthly points expected"
    for field in EXERCISES.get(case, ()):
        assert any(getattr(p, field) for p in result.points), f"{case} never exercises {field}"
    got = fingerprint(result)
    if os.environ.get("GOLDEN_RECORD"):
        print(f'\n    "{case}": "{got}",')
        return
    assert got == GOLDEN[case], (
        f"{case}: the default projection moved. If that is intended, re-record "
        "(see module docstring) and say why in the commit."
    )
