"""The Retirement page's "What's in the budget" breakdown (api/fire.spending_breakdown)."""

from app.api.fire import spending_breakdown


def test_property_sales_split_in_budget_and_on_top():
    ca = {
        "property_sales": [
            {"key": "coastal_condo", "re_bucket": "primary", "monthly_cost": 6_000,
             "mortgage_pi": 2_400, "in_base_burn": True, "post_sale_rent": 2_500},
            {"key": "mountain_house", "re_bucket": "secondary", "monthly_cost": 2_400,
             "in_base_burn": False},
            {"key": "river_house", "re_bucket": "income", "monthly_cost": 1_000},
        ],
    }
    b = spending_breakdown(ca, 12_000)
    assert b.primary_property_all_in == 6_000
    assert b.primary_property_pi == 2_400
    assert b.post_sale_rent == 2_500
    assert b.income_property_cost == 1_000
    assert b.secondary_property_cost == 0  # carried on top, not inside the budget
    assert b.non_housing == 12_000 - 6_000 - 1_000
    assert b.outside_budget_monthly == 2_400
    assert [(l.label, l.in_budget) for l in b.properties] == [
        ("Coastal Condo", True), ("Mountain House", False), ("River House", True),
    ]


def test_no_property_config_is_all_non_housing():
    b = spending_breakdown({}, 9_000)
    assert b.non_housing == 9_000
    assert b.properties == [] and b.outside_budget_monthly == 0
