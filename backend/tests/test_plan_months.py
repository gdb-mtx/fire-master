"""Plan months are stored as calendar months: offsets are pinned on every write."""

from datetime import date

from app.api.fire import _pinned_overrides
from app.core.merge import json_merge_patch
from app.engines.plan_months import month_from_offset, pin_plan_months

TODAY = date(2026, 9, 22)


def test_month_from_offset_is_calendar_arithmetic():
    assert month_from_offset(TODAY, 0) == "2026-09"
    assert month_from_offset(TODAY, 13) == "2027-10"
    assert month_from_offset(date(2026, 1, 31), 1) == "2026-02"


def test_every_offset_is_pinned_where_it_points_today():
    ca = {
        "sepp": {"sepp_monthly": 2_000, "sepp_start_month": 12},
        "rrsp": {"monthly_net": 1_500, "start_month": 9},
        "projection": {"cash_savings_cutover_month": 60, "re_appreciation_rate": 0.015},
        "debt_paydown": {"car_loan_payoff_month": 19, "car_loan_payment": 869},
        "property_sales": [{"key": "condo", "sale_month": 13}, {"key": "cabin", "sale_date": "2028-07"}],
    }
    out = pin_plan_months(ca, TODAY)
    assert out["sepp"] == {"sepp_monthly": 2_000, "start_date": "2027-09"}
    assert out["rrsp"] == {"monthly_net": 1_500, "start_date": "2027-06"}
    assert out["projection"] == {"cash_savings_cutover_date": "2031-09", "re_appreciation_rate": 0.015}
    assert out["debt_paydown"] == {"car_loan_payoff_date": "2028-04", "car_loan_payment": 869}
    assert out["property_sales"] == [{"key": "condo", "sale_date": "2027-10"},
                                     {"key": "cabin", "sale_date": "2028-07"}]
    assert ca["sepp"]["sepp_start_month"] == 12  # input not mutated


def test_existing_pin_wins_and_the_stale_offset_is_dropped():
    out = pin_plan_months({"sepp": {"start_date": "2027-06", "sepp_start_month": 12}}, TODAY)
    assert out["sepp"] == {"start_date": "2027-06"}


def test_patch_offset_is_pinned_and_deletes_the_stored_offset():
    stored = {"sepp": {"sepp_monthly": 2_000, "sepp_start_month": 3, "ira_a_balance": 400_000}}
    patch = pin_plan_months({"sepp": {"sepp_start_month": 12}}, TODAY, as_patch=True)
    assert patch == {"sepp": {"start_date": "2027-09", "sepp_start_month": None}}
    merged = json_merge_patch(stored, patch)
    assert merged["sepp"] == {"sepp_monthly": 2_000, "ira_a_balance": 400_000, "start_date": "2027-09"}


def test_patch_null_stays_a_delete_instruction():
    assert pin_plan_months({"rrsp": {"start_month": None}}, TODAY, as_patch=True) == {
        "rrsp": {"start_month": None}
    }


def test_documents_without_plan_months_pass_through():
    assert pin_plan_months({"tax": {"state": "FL"}}, TODAY) == {"tax": {"state": "FL"}}
    assert pin_plan_months(None, TODAY) is None


def test_scenario_overrides_are_pinned_on_write():
    out = _pinned_overrides({"custom_assumptions": {"property_sales": [{"key": "x", "sale_month": 0}]},
                             "target_annual_spending": 1})
    assert out["custom_assumptions"]["property_sales"][0]["sale_date"]  # today's month
    assert "sale_month" not in out["custom_assumptions"]["property_sales"][0]
    assert out["target_annual_spending"] == 1
    assert _pinned_overrides({"target_annual_spending": 1}) == {"target_annual_spending": 1}


def test_migration_converter_matches_the_app_converter():
    # The migration carries a frozen copy of the converter; on today's keys the
    # two must agree (a stored document the API would pin, the migration pins alike).
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic/versions/c3d4e5f6a7b8_pin_plan_months.py"
    spec = importlib.util.spec_from_file_location("pin_migration", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    ca = {
        "sepp": {"sepp_start_month": 12, "sepp_monthly": 1},
        "rrsp": {"start_month": None},
        "projection": {"cash_savings_cutover_month": 60},
        "debt_paydown": {"car_loan_payoff_month": 19},
        "property_sales": [{"key": "a", "sale_month": 13}, {"key": "b", "sale_date": "2028-07", "sale_month": 1}],
        "some_other_block": {"sale_month": 60},  # not a registered plan month: left alone
    }
    migrated, changed = mig.pin(ca, TODAY)
    assert changed
    assert migrated == pin_plan_months(ca, TODAY)
    assert migrated["some_other_block"] == {"sale_month": 60}
    assert mig.pin({"tax": {}}, TODAY) == ({"tax": {}}, False)
