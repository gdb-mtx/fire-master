"""Unit tests for the setup-status decision function (app/api/setup.py).

The route is a thin gatherer; all lifecycle/state logic lives in
compute_setup_status(), tested here without a DB.
"""

from app.api.setup import compute_setup_status

BASE = dict(
    demo_mode=False,
    demo_persona=False,
    accounts_total=0,
    accounts_monarch=0,
    accounts_with_fire_role=0,
    roles_in_use=[],
    config_present=True,
    config_updated_at="2026-07-01T00:00:00+00:00",
    sepp_configured=False,
    properties=0,
    property_rules=0,
    scenarios_total=0,
    active_scenario=None,
    income_sources_active=0,
)


def _status(**over):
    return compute_setup_status(**{**BASE, **over})


class TestLifecycleStates:
    def test_empty_virgin_database(self):
        s = _status(config_present=False, config_updated_at=None)
        assert s.state == "empty"
        assert "No accounts exist" in s.next_steps[0]

    def test_demo_persona(self):
        s = _status(demo_persona=True, accounts_total=17,
                    accounts_with_fire_role=17,
                    roles_in_use=["cash_reserve"] * 17)
        assert s.state == "demo"
        assert "DEMO persona" in s.next_steps[0]

    def test_half_onboarded_no_roles(self):
        s = _status(accounts_total=12, accounts_monarch=12, demo_persona=True)
        assert s.state == "half_onboarded"
        assert "do NOT present projections" in s.next_steps[0]
        # The Jul 23 verified fact must be in the guidance: SEPP config is the only door in.
        assert any("ONLY through" in step and "sepp" in step.lower() for step in s.next_steps)

    def test_half_onboarded_demo_config_survives_after_roles_started(self):
        # Roles partially enriched but demo config not rebuilt -> still half_onboarded
        s = _status(accounts_total=12, accounts_monarch=12, demo_persona=True,
                    accounts_with_fire_role=4, roles_in_use=["cash_reserve"] * 4)
        assert s.state == "half_onboarded"

    def test_onboarded(self):
        s = _status(accounts_total=12, accounts_monarch=12,
                    accounts_with_fire_role=12,
                    roles_in_use=["cash_reserve", "retirement_core"],
                    sepp_configured=True)
        assert s.state == "onboarded"
        # Standing drift check is the onboarded guidance
        assert any("custom_assumptions.sepp" in step for step in s.next_steps)


class TestVocabulary:
    def test_unknown_role_flagged(self):
        s = _status(accounts_total=3, accounts_monarch=3, demo_persona=False,
                    accounts_with_fire_role=3,
                    roles_in_use=["cash_reserve", "retirment_core", "cash_reserve"])
        assert s.state == "onboarded"
        assert s.unknown_fire_roles == ["retirment_core"]
        assert any("unrecognized fire_role" in step for step in s.next_steps)

    def test_vocabulary_is_complete(self):
        s = _status()
        all_roles = {r for group in s.valid_fire_roles.values() for r in group}
        assert len(all_roles) == 16  # pool roles + education + depreciating + system
        assert {"system", "retirement_core", "education_restricted", "depreciating"} <= all_roles

    def test_demo_mode_lock_noted(self):
        s = _status(demo_mode=True, demo_persona=True, accounts_total=17)
        assert s.demo_mode is True
        assert any("DEMO_MODE lock" in step for step in s.next_steps)
