"""Pure-function tests for the Property P&L classifier (no DB).

Mirrors the rule-set shape in config/properties.example.json and locks the behaviour
ported from scripts/report_property_pnl.py: exclusions win, merchant-rule precedence,
require_tx_category gating, income only on positive amounts, fallback to Other,
unmatched -> None.
"""

import uuid

from app.engines.property_pnl import (
    Classification,
    RuleView,
    classify_transaction,
    match_tag_to_property,
    resolve_with_tag,
    tag_category,
)

COASTAL = uuid.uuid4()
MOUNTAIN = uuid.uuid4()
RIVER = uuid.uuid4()


def _rules() -> list[RuleView]:
    """A representative slice of a seeded rule set, with the real priority scheme."""
    return [
        # Global exclusions (priority 0).
        RuleView(None, "Geico", None, None, "exclusion", 0),
        RuleView(None, "Progressive", None, None, "exclusion", 0),
        RuleView(None, "Acme Life", None, None, "exclusion", 0),
        # Specific merchant_rules (priority 10).
        RuleView(COASTAL, "CondoPay", "HOA / Condo Fees", None, "expense", 10),
        RuleView(COASTAL, "Lender A", "Mortgage", None, "expense", 10),
        RuleView(COASTAL, "Coastal Power", "Utilities", None, "expense", 10),
        RuleView(RIVER, "Valley Electric", "Utilities", None, "expense", 10),
        RuleView(RIVER, "Metro Gas", "Utilities", None, "expense", 10),
        RuleView(RIVER, "Shield Insurance", "Insurance", None, "expense", 10),
        RuleView(RIVER, "County Tax", "Property Tax", None, "expense", 10),
        RuleView(MOUNTAIN, "Lender B", "Mortgage", None, "expense", 10),
        # Income rules (priority 20).
        RuleView(RIVER, "Acme Property Mgmt", None, None, "income", 20),
        RuleView(RIVER, "Airbnb", None, None, "income", 20),
        RuleView(COASTAL, "Tenant Direct", None, None, "income", 20),
        # Moorage — gated on tx category == "Rent" (priority 60).
        RuleView(RIVER, "Check #", "Moorage", "Rent", "expense", 60),
    ]


def test_exclusion_wins():
    c = classify_transaction("Geico Auto Insurance", "Insurance", -10000, _rules())
    assert c.matched is False
    assert c.property_id is None


def test_merchant_rule_precedence_and_category():
    c = classify_transaction("CONDOPAY HOA PMT", "Rent", -267200, _rules())
    assert c.property_id == COASTAL
    assert c.category == "HOA / Condo Fees"
    assert c.matched is True


def test_utility_leak_now_classified():
    # The exact merchant shapes that were leaking into the Tracker.
    cp = classify_transaction("Coastal Power & Light", "Utilities", -15000, _rules())
    assert cp.property_id == COASTAL and cp.category == "Utilities"
    ve = classify_transaction("Valley Electric Cooperative", "Utilities", -8000, _rules())
    assert ve.property_id == RIVER and ve.category == "Utilities"
    mg = classify_transaction("Metro Gas", "Gas", -4000, _rules())
    assert mg.property_id == RIVER and mg.category == "Utilities"


def test_require_tx_category_gate():
    # "Check #" only counts as moorage when category is "Rent".
    rent_check = classify_transaction("Check # 1234", "Rent", -51500, _rules())
    assert rent_check.property_id == RIVER and rent_check.category == "Moorage"

    grocery_check = classify_transaction("Check # 9999", "Groceries", -51500, _rules())
    assert grocery_check.matched is False
    assert grocery_check.property_id is None


def test_income_only_on_positive_amount():
    inc = classify_transaction("Acme Property Mgmt", "Rental Income", 330000, _rules())
    assert inc.property_id == RIVER and inc.category == "Rental Income"

    # Same merchant on a negative amount must not match income.
    neg = classify_transaction("Acme Property Mgmt", "Rental Income", -330000, _rules())
    assert neg.matched is False


def test_income_on_credit_card_is_not_rental():
    # A positive "Airbnb" amount on a credit card is a guest REFUND (a trip the owner booked),
    # not a host payout — host payouts land in checking. Must not classify as rental income.
    refund = classify_transaction("Airbnb", "Other Income", 255837, _rules(), is_credit_card=True)
    assert refund.matched is False
    assert refund.property_id is None

    # The same payout in a depository account still classifies as rental income.
    payout = classify_transaction("Airbnb", "Other Income", 255837, _rules(), is_credit_card=False)
    assert payout.property_id == RIVER and payout.category == "Rental Income"


def test_expense_on_credit_card_still_classifies():
    # The guard is income-only: property expenses legitimately go on a credit card.
    c = classify_transaction("Coastal Power & Light", "Utilities", -15000, _rules(), is_credit_card=True)
    assert c.property_id == COASTAL and c.category == "Utilities"


# --- Inbound: Monarch tag -> property (the reverse direction) ---------------

NAME_TO_ID = {"coastal condo": COASTAL, "mountain house": MOUNTAIN, "river house": RIVER}


def test_tag_matches_property_case_insensitively():
    assert match_tag_to_property(["River House"], NAME_TO_ID) == RIVER
    assert match_tag_to_property(["river house"], NAME_TO_ID) == RIVER
    # Non-property tags are ignored; the property tag still resolves.
    assert match_tag_to_property(["Reimburse", "Coastal Condo"], NAME_TO_ID) == COASTAL


def test_tag_no_match_returns_none():
    assert match_tag_to_property([], NAME_TO_ID) is None
    assert match_tag_to_property(None, NAME_TO_ID) is None
    assert match_tag_to_property(["Vacation", "Tax"], NAME_TO_ID) is None


def test_tag_on_unruled_txn_feeds_pnl():
    # A P2P payment a rule can't catch, tagged "River House" in Monarch.
    unmatched = Classification(None, None, False)
    # Income -> Rental Income.
    pid, cat, src = resolve_with_tag(unmatched, RIVER, 115625, "Other Income")
    assert (pid, cat, src) == (RIVER, "Rental Income", "monarch_tag")
    # Expense -> fallback category.
    pid, cat, src = resolve_with_tag(unmatched, RIVER, -64900, "Home Services")
    assert (pid, src) == (RIVER, "monarch_tag") and cat == "Other"


def test_tag_overrides_disagreeing_rule_but_defers_to_agreeing_rule():
    coastal_rule = Classification(COASTAL, "Utilities", True)
    # Tag disagrees with the rule -> explicit tag wins; category is re-derived from the raw
    # Monarch category via fallback (the disagreeing rule's category is not carried over).
    assert resolve_with_tag(coastal_rule, RIVER, -8000, "Electric") == (RIVER, "Utilities", "monarch_tag")
    # Tag agrees with the rule -> keep the rule's richer category + 'rule' source (no churn).
    assert resolve_with_tag(coastal_rule, COASTAL, -8000, "Electric") == (COASTAL, "Utilities", "rule")
    # No tag -> plain rule result.
    assert resolve_with_tag(coastal_rule, None, -8000, "Electric") == (COASTAL, "Utilities", "rule")


def test_expense_rule_ignored_on_positive_amount():
    # A positive amount should never match an expense rule.
    c = classify_transaction("Lender A Mortgage", "Mortgage", 312000, _rules())
    assert c.matched is False


def test_unmatched_merchant():
    c = classify_transaction("Trader Joe's", "Groceries", -8500, _rules())
    assert c.matched is False
    assert c.property_id is None and c.category is None


def test_fallback_category_when_rule_has_none():
    # A property expense rule carrying no expense_category falls back on tx category.
    rules = [RuleView(COASTAL, "Some Vendor", None, None, "expense", 50)]
    c = classify_transaction("Some Vendor Repair Co", "Home Improvement", -20000, rules)
    assert c.property_id == COASTAL and c.category == "Repairs / Maintenance"


def test_null_merchant_is_safe():
    c = classify_transaction(None, None, -5000, _rules())
    assert c.matched is False


# --- Contra-entries: negative income, positive expense ---------------------------
#
# The module treated sign as identity — negative IS expense, positive IS income. That
# holds for ~99% of rows and breaks on every contra-entry. A security deposit returned
# to a tenant of an income property is negative Rental Income, but the negative branch
# fell through to _fallback_category, whose `"rent" in cat` test matches
# the substring inside "Rental Income" and returned "HOA / Condo Fees". Gross rents and
# HOA expense were each overstated by the same $3,000 — net profit right, Schedule E
# wrong. The matching UI gap (no income option on a negative row) is in TransactionsPage.


def test_returned_deposit_is_negative_rental_income_not_an_hoa_fee():
    unmatched = Classification(None, None, False)
    pid, cat, src = resolve_with_tag(unmatched, RIVER, -300_000, "Rental Income")
    assert (pid, cat, src) == (RIVER, "Rental Income", "monarch_tag")


def test_rent_expense_categories_still_map_to_hoa():
    """The guard that makes the fix safe: ~83 real condo-fee rows arrive from Monarch
    as "Rent" / "Mortgage & Rent". Only an EXACT "Rental Income" match may flip to
    income — a substring test on "rent" is the original bug."""
    assert tag_category(-259_260, "Rent") == "HOA / Condo Fees"
    assert tag_category(-7_385, "Rent") == "HOA / Condo Fees"
    assert tag_category(-134_064, "Mortgage & Rent") == "Mortgage"


def test_tag_category_is_case_and_whitespace_insensitive():
    assert tag_category(-300_000, "  rental income  ") == "Rental Income"
    assert tag_category(-300_000, "RENTAL INCOME") == "Rental Income"


def test_positive_and_unrelated_categories_unchanged():
    assert tag_category(115_625, "Other Income") == "Rental Income"
    assert tag_category(300_000, "Rental Income") == "Rental Income"
    assert tag_category(-64_900, "Home Services") == "Other"
    assert tag_category(-10_000, "Home Repair") == "Repairs / Maintenance"
    assert tag_category(-5_000, None) == "Other"
