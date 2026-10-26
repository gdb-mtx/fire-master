"""Scenario overrides: the real merge, the effective config, and write-time checks.

Until Oct 2026 nothing exercised the real _apply_overrides (conftest re-implements
the custom_assumptions half) or get_effective_config (always mocked). These pin:
- read-time tolerance: a bad stored blob is skipped, never crashes a projection;
- get_effective_config always hands out a detached copy, and an unknown explicit
  scenario_id raises instead of quietly answering with the base plan;
- write-time validation: unknown keys, a null custom_assumptions and non-ISO
  dates are rejected when a scenario is saved.
"""

import json
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.engines.fire_projections import FireProjectionsEngine, ScenarioNotFoundError
from app.models.fire_config import FireConfig
from app.schemas.fire import FireScenarioCreate, FireScenarioUpdate


def _base_config() -> FireConfig:
    c = FireConfig()
    c.id = uuid.uuid4()
    c.date_of_birth = date(1973, 7, 1)
    c.target_annual_spending = 12_000_000
    c.custom_assumptions = {
        "projection": {"re_appreciation_rate": 0.01, "surplus_investment_rate": 0.04},
        "property_sales": [{"key": "condo", "sale_date": "2027-03"}],
    }
    return c


class TestApplyOverrides:
    def test_one_level_merge_and_list_replace(self):
        base = _base_config()
        merged = FireProjectionsEngine._apply_overrides(base, {
            "target_annual_spending": 9_000_000,
            "custom_assumptions": {
                "projection": {"re_appreciation_rate": 0.0},
                "property_sales": [{"key": "lake", "sale_date": "2028-01"}],
            },
        })
        assert merged.target_annual_spending == 9_000_000
        # one level: the sibling projection key survives
        assert merged.custom_assumptions["projection"] == {
            "re_appreciation_rate": 0.0, "surplus_investment_rate": 0.04,
        }
        # lists replace wholesale
        assert [s["key"] for s in merged.custom_assumptions["property_sales"]] == ["lake"]
        # the base row is untouched
        assert base.target_annual_spending == 12_000_000
        assert base.custom_assumptions["projection"]["re_appreciation_rate"] == 0.01

    @pytest.mark.parametrize("bad", [None, [], "x"])
    def test_non_object_custom_assumptions_is_ignored_not_fatal(self, bad):
        base = _base_config()
        merged = FireProjectionsEngine._apply_overrides(base, {"custom_assumptions": bad})
        assert merged.custom_assumptions == base.custom_assumptions

    def test_unknown_and_locked_keys_are_ignored(self):
        base = _base_config()
        merged = FireProjectionsEngine._apply_overrides(base, {
            "rental_occupancy_rate": 0.5,  # belongs under custom_assumptions
            "id": uuid.uuid4(),
            "_sa_instance_state": None,    # hasattr() used to let this through
        })
        assert merged.id == base.id
        assert not hasattr(merged, "rental_occupancy_rate")

    def test_iso_date_string_is_parsed(self):
        merged = FireProjectionsEngine._apply_overrides(_base_config(), {"date_of_birth": "1975-02-03"})
        assert merged.date_of_birth == date(1975, 2, 3)

    def test_bad_date_string_is_ignored(self):
        base = _base_config()
        merged = FireProjectionsEngine._apply_overrides(base, {"date_of_birth": "soon"})
        assert merged.date_of_birth == base.date_of_birth


def _engine_with(config: FireConfig, scenario) -> FireProjectionsEngine:
    def result(value):
        r = MagicMock()
        r.scalar_one_or_none.return_value = value
        return r
    db = AsyncMock()
    db.execute.side_effect = [result(config), result(scenario)]  # config row, then scenario
    return FireProjectionsEngine(db)


class TestGetEffectiveConfig:
    async def test_no_scenario_returns_a_detached_copy(self):
        base = _base_config()
        cfg = await _engine_with(base, None).get_effective_config()
        assert cfg is not base
        assert cfg.target_annual_spending == base.target_annual_spending
        cfg.custom_assumptions["projection"]["re_appreciation_rate"] = 0.5
        assert base.custom_assumptions["projection"]["re_appreciation_rate"] == 0.01

    async def test_active_scenario_is_applied(self):
        scenario = MagicMock()
        scenario.overrides = {"target_annual_spending": 9_000_000}
        cfg = await _engine_with(_base_config(), scenario).get_effective_config()
        assert cfg.target_annual_spending == 9_000_000

    async def test_unknown_explicit_scenario_raises(self):
        with pytest.raises(ScenarioNotFoundError):
            await _engine_with(_base_config(), None).get_effective_config(uuid.uuid4())

    async def test_unknown_scenario_maps_to_404(self):
        from app.main import _scenario_not_found, app

        assert app.exception_handlers.get(ScenarioNotFoundError) is _scenario_not_found
        resp = await _scenario_not_found(None, ScenarioNotFoundError("Scenario x not found"))
        assert resp.status_code == 404
        assert json.loads(resp.body) == {"detail": "Scenario x not found"}


class TestWriteValidation:
    @pytest.mark.parametrize("schema", [FireScenarioCreate, FireScenarioUpdate])
    @pytest.mark.parametrize("overrides, message", [
        ({"custom_assumptions": None}, "must be an object"),
        ({"rental_occupancy_rate": 0.8}, "not fire_config settings: rental_occupancy_rate"),
        ({"id": "x"}, "not fire_config settings: id"),
        ({"date_of_birth": "soon"}, "ISO date"),
    ])
    def test_rejected(self, schema, overrides, message):
        with pytest.raises(ValidationError, match=message):
            schema(name="s", overrides=overrides)

    def test_accepted(self):
        s = FireScenarioCreate(name="s", overrides={
            "target_annual_spending": 9_000_000,
            "date_of_birth": "1975-02-03",
            "custom_assumptions": {"sepp": {"start_date": "2027-06"}},
        })
        assert s.overrides["custom_assumptions"]["sepp"]["start_date"] == "2027-06"
        assert FireScenarioUpdate(overrides=None).overrides is None
