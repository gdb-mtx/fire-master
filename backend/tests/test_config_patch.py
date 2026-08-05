"""Config PATCH regression tests — prevents the Apr 14 data-wipe bug.

The bug: saving from the config page sent nullable fields as null when the
form was empty, overwriting target_annual_spending, social_security_monthly,
and healthcare_monthly_cost to NULL on every save. Also: parseFloat("0") || 2
treated zero as falsy, silently replacing 0% rates with fallback defaults.

These tests verify the Pydantic schema + setattr pattern at the heart of the
PATCH endpoint, with zero DB or server dependency.
"""

from app.models.fire_config import FireConfig
from app.schemas.fire import FireConfigUpdate


class TestExcludeUnset:
    """Verify model_dump(exclude_unset=True) correctly distinguishes
    'field not sent' from 'field explicitly sent as null'."""

    def test_partial_update_excludes_absent_fields(self):
        """PATCH with only safe_withdrawal_rate should NOT include spending/SS/healthcare."""
        update = FireConfigUpdate(safe_withdrawal_rate=3.5)
        dump = update.model_dump(exclude_unset=True)
        assert "safe_withdrawal_rate" in dump
        assert dump["safe_withdrawal_rate"] == 3.5
        # These must NOT be in the dump — they weren't sent
        assert "target_annual_spending" not in dump
        assert "social_security_monthly" not in dump
        assert "healthcare_monthly_cost" not in dump
        assert "custom_assumptions" not in dump

    def test_explicit_null_is_included(self):
        """When a field is explicitly sent as None, it SHOULD appear in the dump."""
        update = FireConfigUpdate(**{"target_annual_spending": None, "safe_withdrawal_rate": 3.5})
        dump = update.model_dump(exclude_unset=True)
        assert "target_annual_spending" in dump
        assert dump["target_annual_spending"] is None

    def test_custom_assumptions_included_when_sent(self):
        """custom_assumptions should be in dump when explicitly provided."""
        ca = {"projection": {"re_appreciation_rate": 0.0}}
        update = FireConfigUpdate(**{"custom_assumptions": ca})
        dump = update.model_dump(exclude_unset=True)
        assert "custom_assumptions" in dump
        assert dump["custom_assumptions"]["projection"]["re_appreciation_rate"] == 0.0


class TestSetAttrPreservesFields:
    """Verify the PATCH endpoint's setattr loop doesn't wipe existing values."""

    def test_partial_update_preserves_existing(self, base_fire_config: FireConfig):
        """Changing one field must not null out other fields."""
        update = FireConfigUpdate(safe_withdrawal_rate=3.5)
        update_data = update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(base_fire_config, field, value)

        # Changed field updated
        assert base_fire_config.safe_withdrawal_rate == 3.5
        # Critical fields preserved (the Apr 14 bug wiped these)
        assert base_fire_config.target_annual_spending == 15_300_000
        assert base_fire_config.social_security_monthly == 465_000
        assert base_fire_config.healthcare_monthly_cost == 60_000

    def test_custom_assumptions_with_zero_values(self, base_fire_config: FireConfig):
        """Zero-value rates must survive the setattr round-trip (the || fallback bug)."""
        ca = dict(base_fire_config.custom_assumptions)
        ca["projection"] = {**ca["projection"], "re_appreciation_rate": 0.0, "cash_savings_rate_late": 0.0}

        update = FireConfigUpdate(**{"custom_assumptions": ca})
        update_data = update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(base_fire_config, field, value)

        proj = base_fire_config.custom_assumptions["projection"]
        assert proj["re_appreciation_rate"] == 0.0
        assert proj["cash_savings_rate_late"] == 0.0

    def test_full_save_round_trip(self, base_fire_config: FireConfig):
        """Simulate a full config page save with all projection fields."""
        # The frontend builds the full custom_assumptions dict on every save
        ca = dict(base_fire_config.custom_assumptions)
        ca["projection"] = {
            "surplus_investment_rate": 0.04,
            "cash_reserve_months": 12,
            "cash_savings_rate_early": 0.01,
            "cash_savings_rate_late": 0.0,  # zero — must not become 0.02
            "cash_savings_cutover_month": 60,
            "re_appreciation_rate": 0.0,  # zero — must not become 0.02
            "primary_property_purchase_price": 510_000,
            "primary_property_agent_fee_pct": 0.06,
            "primary_property_mortgage_pi": 3_100,
            "ss_early_reduction": 0.70,
            "ss_claim_age": 62,
            "spending_phase_slow": 0.85,
            "spending_phase_floor": 0.75,
            "spending_phase_slow_age": 70,
            "spending_phase_floor_age": 80,
            "ira_b_draw_threshold_months": 12,
        }

        update = FireConfigUpdate(**{
            "safe_withdrawal_rate": 4.0,
            "custom_assumptions": ca,
        })
        update_data = update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(base_fire_config, field, value)

        proj = base_fire_config.custom_assumptions["projection"]
        assert proj["re_appreciation_rate"] == 0.0
        assert proj["cash_savings_rate_late"] == 0.0
        assert proj["surplus_investment_rate"] == 0.04
        # Non-projection fields preserved
        assert base_fire_config.target_annual_spending == 15_300_000
        assert base_fire_config.social_security_monthly == 465_000


class TestCustomAssumptionsMergePatch:
    """fire-master#9: PATCHing custom_assumptions is an RFC 7386 merge, not a
    replace. Tim's incident: {"custom_assumptions": {"sepp": {"sepp_monthly": X}}}
    silently deleted every sibling key and every sibling field inside sepp."""

    def _patch(self, config: FireConfig, body: dict) -> FireConfig:
        """Replicate the PATCH endpoint's field loop exactly."""
        from app.core.merge import json_merge_patch

        update = FireConfigUpdate(**body)
        for field, value in update.model_dump(exclude_unset=True).items():
            if field == "custom_assumptions":
                value = json_merge_patch(config.custom_assumptions, value)
            setattr(config, field, value)
        return config

    def test_partial_nested_update_preserves_siblings(self, base_fire_config: FireConfig):
        """The exact issue-9 reproduction: one sepp leaf must not wipe anything."""
        before = {k: v for k, v in base_fire_config.custom_assumptions.items()}
        self._patch(base_fire_config, {"custom_assumptions": {"sepp": {"sepp_monthly": 2_500}}})
        ca = base_fire_config.custom_assumptions
        # The leaf changed
        assert ca["sepp"]["sepp_monthly"] == 2_500
        # sepp's sibling fields survive
        assert ca["sepp"]["ira_a_balance"] == 402_000
        assert ca["sepp"]["ira_growth_rate"] == 0.06
        # every top-level sibling key survives
        assert set(ca.keys()) == set(before.keys())
        assert ca["projection"] == before["projection"]
        assert ca["tax"] == before["tax"]

    def test_explicit_null_deletes_key(self, base_fire_config: FireConfig):
        """Deletion is spelled null, matching the config page's cleared fields."""
        self._patch(base_fire_config, {"custom_assumptions": {"tax": {"state": None}}})
        tax = base_fire_config.custom_assumptions["tax"]
        assert "state" not in tax
        assert tax["filing_status"] == "single"  # siblings intact

    def test_arrays_replace_wholesale(self, base_fire_config: FireConfig):
        """property_sales-style lists replace, never element-merge."""
        sales = [{"key": "coastal_condo", "sale_month": 24}]
        self._patch(base_fire_config, {"custom_assumptions": {"property_sales": sales}})
        self._patch(base_fire_config, {"custom_assumptions": {"property_sales": []}})
        assert base_fire_config.custom_assumptions["property_sales"] == []

    def test_new_subkey_added(self, base_fire_config: FireConfig):
        self._patch(base_fire_config, {"custom_assumptions": {"taxable_pool": {"return_rate": 0.065}}})
        assert base_fire_config.custom_assumptions["taxable_pool"] == {"return_rate": 0.065}
        assert base_fire_config.custom_assumptions["sepp"]["sepp_monthly"] == 2_100

    def test_top_level_null_is_explicit_full_clear(self, base_fire_config: FireConfig):
        self._patch(base_fire_config, {"custom_assumptions": None})
        assert base_fire_config.custom_assumptions is None

    def test_merge_onto_null_base(self, base_fire_config: FireConfig):
        base_fire_config.custom_assumptions = None
        self._patch(base_fire_config, {"custom_assumptions": {"sepp": {"sepp_monthly": 900}}})
        assert base_fire_config.custom_assumptions == {"sepp": {"sepp_monthly": 900}}

    def test_config_page_save_shape_preserves_unmanaged_keys(self, base_fire_config: FireConfig):
        """The config page now sends ONLY its managed keys (no client-side
        spread); unmanaged keys like sell_event_label_match must survive."""
        self._patch(base_fire_config, {"custom_assumptions": {
            "tax": {"filing_status": "single", "household_size": 1, "cost_basis_pct": 0.6,
                    "state": None, "state_tax_rate": None},
            "projection": {"re_appreciation_rate": 0.0, "primary_property_purchase_price": None},
            "rental_occupancy_rate": 0.7,
        }})
        ca = base_fire_config.custom_assumptions
        proj = ca["projection"]
        # cleared fields deleted, zero preserved as zero
        assert "primary_property_purchase_price" not in proj
        assert proj["re_appreciation_rate"] == 0.0
        # unmanaged keys the form never touches survive
        assert proj["sell_event_label_match"] == "mountain house"
        assert proj["primary_property_mortgage_pi"] == 3_100
        assert ca["occupancy_source_match"] == ["river house"]
        assert ca["sepp"]["ira_a_balance"] == 402_000

    def test_inputs_not_mutated(self, base_fire_config: FireConfig):
        from app.core.merge import json_merge_patch

        base = {"a": {"b": 1}}
        patch = {"a": {"c": 2}}
        out = json_merge_patch(base, patch)
        assert base == {"a": {"b": 1}} and patch == {"a": {"c": 2}}
        out["a"]["b"] = 99
        assert base["a"]["b"] == 1
