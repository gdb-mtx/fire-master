"""Seed a complete demo financial life so a fresh install renders every page alive.

Creates the synthetic demo persona: a couple in their early 50s, one spouse
just laid off, bridging to penalty-free retirement account access at 59½.
Three properties — a City Loft (primary residence, mortgaged), a Desert Casita
(snowbird short-term rental, under contract to sell), and a Lakeside Cabin
(rented long-term) — plus retirement accounts, a Canadian RRSP to draw down,
startup equity, severance runway, and a SEPP/72(t) bridge plan.

What it seeds:
  - 17 accounts with FIRE-role enrichment (cash, 401(k)/IRAs, real estate,
    mortgages, private equity, vehicles)
  - ~2 years of weekly balance history per account + net worth history
  - 6 income sources (ended salary, severance, unemployment, three rentals)
  - 10 future cashflow events (severance lump, property sale, equity vests …)
  - The three properties + merchant classification rules, and ~18 months of
    transactions (recurring merchants, rental income, property expenses) so the
    Spending, Tracker, Transactions, and Properties pages all render alive —
    classified through the real rules engine, not pre-stamped
  - The FIRE config for the persona: SEPP bridge starting month 12, a
    property_sales plan (sell the Desert Casita in ~6 months, downsize out of
    the City Loft at month 60), Social Security at 62

All dates are anchored to TODAY at seed time, so the story is always "just laid
off, severance ending soon, sale closing in ~6 months" — re-running the script
re-anchors the timeline. Demo rows are manual-source (external_id = NULL), so a
later real Monarch sync ignores them completely: try the app on demo data first,
connect Monarch when ready, then remove the demo with --remove.

Safety: refuses to run if Monarch-synced accounts exist (you have real data;
seeding a fake persona on top would only confuse things). Override with --force
(data rows only — an already-configured FIRE config is never overwritten unless
you also pass --with-config).

Idempotent: upserts by name, rebuilds demo balance history, recomputes net worth
snapshots.

Usage:
    cd backend && uv run python ../scripts/seed_demo.py            # seed
    cd backend && uv run python ../scripts/seed_demo.py --remove   # remove demo rows
"""

import argparse
import asyncio
import math
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dateutil.relativedelta import relativedelta
from sqlalchemy import delete, select

from app.core.database import async_session_factory
from app.engines.net_worth import NetWorthEngine
from app.engines.property_pnl import PropertyPnLEngine
from app.ingestion.category_sync import CategorySyncService
from app.models.account import Account
from app.models.balance_snapshot import BalanceSnapshot
from app.models.cashflow_event import CashflowEvent
from app.models.enums import AccountType, DataSource, IncomeType
from app.models.fire_config import FireConfig
from app.models.income_source import IncomeSource
from app.models.net_worth_snapshot import NetWorthSnapshot
from app.models.property import Property
from app.models.property_rule import PropertyRule
from app.models.transaction import Transaction

DEMO_MARKER = {"demo_seed": True}
HISTORY_WEEKS = 104  # ~2 years of weekly balance snapshots


# ---------------------------------------------------------------------------
# Persona — accounts (balances in cents; buckets match the projection engine's
# fire_role taxonomy: liquid / retirement / real-estate / illiquid / other)
# ---------------------------------------------------------------------------
# (name, account_type, institution, fire_role, balance_cents, is_asset,
#  history_start_factor, wiggle_amplitude)
#
# history_start_factor = balance 2 years ago as a fraction of today's balance
# (>1 = the asset has been depreciating). wiggle_amplitude adds a deterministic
# sine wobble so charts look like markets, not CAD drawings.
ACCOUNTS = [
    ("Everyday Checking",        AccountType.CHECKING,  "First Ridgeline Bank", "operating_account",       2_100_000,  True,  0.85, 0.06),
    ("High-Yield Savings",       AccountType.SAVINGS,   "First Ridgeline Bank", "cash_reserve",            6_400_000,  True,  0.60, 0.01),
    ("Crypto Portfolio",         AccountType.CRYPTO,    "Coinvault",           "speculative",              1_400_000,  True,  0.62, 0.10),
    ("Employer 401(k)",          AccountType.FOUR_OH_ONE_K, "Meridian Retirement", "retirement_core",     39_200_000,  True,  0.82, 0.02),
    ("Rollover IRA (bridge)",    AccountType.IRA,       "Keystone Brokerage",  "retirement_bridge",       43_600_000,  True,  0.82, 0.02),
    ("Roth IRA",                 AccountType.ROTH_IRA,  "Keystone Brokerage",  "retirement_supplemental", 17_200_000,  True,  0.82, 0.02),
    ("HSA",                      AccountType.HSA,       "Bluecrest Health",    "tax_free_reserve",           720_000,  True,  0.70, 0.02),
    ("City Loft",                AccountType.REAL_ESTATE, None,                "primary_residence",       46_800_000,  True,  0.962, 0.004),
    ("City Loft Mortgage",       AccountType.REAL_ESTATE, "Granite Home Loans", "primary_mortgage",       29_600_000,  False, None, None),
    ("Desert Casita",            AccountType.REAL_ESTATE, None,                "sell_candidate",          38_600_000,  True,  0.962, 0.004),
    ("Desert Casita Mortgage",   AccountType.REAL_ESTATE, "Sunline Mortgage",  "sell_with_property",      23_800_000,  False, None, None),
    ("Lakeside Cabin",           AccountType.REAL_ESTATE, None,                "income_producing",        42_800_000,  True,  0.962, 0.004),
    ("Startup A Equity",         AccountType.PRIVATE,   None,                  "illiquid_private",         8_400_000,  True,  1.0,  0.0),
    ("Startup B Equity",         AccountType.PRIVATE,   None,                  "illiquid_private",         1_900_000,  True,  1.0,  0.0),
    ("Startup C Equity",         AccountType.PRIVATE,   None,                  "illiquid_private",         3_100_000,  True,  1.0,  0.0),
    ("Pickup Truck",             AccountType.VEHICLE,   None,                  "depreciating",             3_800_000,  True,  1.24, 0.0),
    ("Pontoon Boat",             AccountType.VEHICLE,   None,                  "depreciating",             1_900_000,  True,  1.13, 0.0),
]

# Mortgages amortize linearly back in time: balance N months ago was higher by
# N × monthly principal. (name → monthly principal paydown, cents)
MORTGAGE_MONTHLY_PRINCIPAL = {
    "City Loft Mortgage": 132_000,      # $2,850 P&I @ ~6.2% on $296K
    "Desert Casita Mortgage": 31_000,   # $1,650 P&I @ ~6.75% on $238K
}


# ---------------------------------------------------------------------------
# Persona — income sources (annual amounts in cents; end offsets in days from
# the seed anchor). The salary just ended — that's the story.
# ---------------------------------------------------------------------------
# (name, income_type, annual_amount_cents, end_offset_days or None)
INCOME_SOURCES = [
    ("Employer Salary (ended)",  IncomeType.SALARY, 23_500_000, -21),
    ("Employer Severance",       IncomeType.OTHER,   7_600_000,  16),
    ("State Unemployment",       IncomeType.OTHER,   1_440_000,  77),
    ("City Loft Rental",         IncomeType.RENTAL,    430_000, None),
    ("Desert Casita Rental",     IncomeType.RENTAL,    900_000,  77),
    ("Lakeside Cabin Rental",    IncomeType.RENTAL,  4_140_000, None),
]


# ---------------------------------------------------------------------------
# Persona — cashflow events (amounts in cents; offsets from the seed anchor).
# "Desert Casita Sale Proceeds" is deliberately BOTH an event and a
# property_sales entry: the Runway page consumes the event, while the wealth
# projection suppresses it (suppress_cashflow_match) because the generic sale
# path computes the proceeds itself. That's the supported pattern.
# ---------------------------------------------------------------------------
# (name, event_type, amount_cents, offset_days or ("months", n), probability,
#  is_recurring, recurrence, end_offset_days)
CASHFLOW_EVENTS = [
    ("City Loft Special Assessment",     "expense",    420_000, -13, 1.0,  True,  "monthly", 139),
    ("Boat Payment",                     "expense",    460_000,  14, 1.0,  False, None, None),
    ("Remaining Severance",              "income",   2_800_000,  16, 1.0,  False, None, None),
    ("Prior-Year Tax Refund",            "income",   3_600_000,  78, 0.9,  False, None, None),
    ("Desert Casita Sale Proceeds",      "income",  11_200_000, 184, 0.85, False, None, None),
    ("RRSP Cash-Out (Canada)",           "income",  10_800_000, 474, 0.5,  False, None, None),
    ("Auto loan payoff savings",         "income",      82_000, 443, 1.0,  True,  "monthly", None),
    ("Startup A vests (1.5x)",           "income",  12_600_000, ("months", 60), 0.7, False, None, None),
    ("Startup B vests (1.5x)",           "income",   2_900_000, ("months", 72), 0.6, False, None, None),
    ("Startup C vests (2x)",             "income",   6_200_000, ("months", 84), 0.5, False, None, None),
]


# ---------------------------------------------------------------------------
# Persona — properties + merchant classification rules. The demo transactions
# below are classified through the REAL rules engine (PropertyPnLEngine), not
# pre-stamped, so the Properties page demos the actual product mechanism.
# ---------------------------------------------------------------------------
# (key, name, address, color, value_cents, loan_cents, purchase_cents,
#  potential_rental_cents, potential_rental_full_cents, notes)
DEMO_PROPERTIES = [
    ("city_loft", "City Loft", "808 Foundry Row #4W, Midvale City", "#b04a56",
     46_800_000, 29_600_000, 46_800_000, 240_000, 420_000,
     ["Primary residence — owner-occupied, spare room rented to a long-term tenant ($360/mo)",
      "Monthly HOA ~$680 via LoftPay; special assessment running this year",
      "Downsize plan: sell around month 60 (see the property_sales config)"]),
    ("desert_casita", "Desert Casita", "17 Ocotillo Court, Dry Wells", "#c4813d",
     38_600_000, 23_800_000, 36_100_000, 280_000, None,
     ["Snowbird short-term rental, under contract to sell in ~6 months",
      "High season is winter (Sunbelt Stays payouts Nov-Mar) until the sale",
      "Carry cost ~$2,250/mo all-in while held"]),
    ("lakeside_cabin", "Lakeside Cabin", "9 Loon Point, Pinecrest Lake", "#2e8b6e",
     42_800_000, 0, 21_200_000, 350_000, None,
     ["Fully paid off, rented long-term through Northwoods Property Mgmt",
      "Dock slip lease paid by check (see the category-gated 'Check #' rule)",
      "The income property: ~$3,450/mo rent against ~$1,100/mo carry"]),
]

# (property_key, pattern, rule_kind, expense_category, require_tx_category, priority)
DEMO_PROPERTY_RULES = [
    ("city_loft", "Granite Home Loans",           "expense", "Mortgage",              None,   10),
    ("city_loft", "LoftPay",                      "expense", "HOA / Condo Fees",      None,   10),
    ("city_loft", "Loft Assoc",                   "expense", "Assessments",           None,   10),
    ("city_loft", "Metro Power",                  "expense", "Utilities",             None,   10),
    ("city_loft", "Cornerstone Home Insurance",   "expense", "Insurance",             None,   10),
    ("city_loft", "Metro County Treasurer",       "expense", "Property Tax",          None,   10),
    ("city_loft", "Tenant Direct",                "income",  None,                    None,   20),
    ("desert_casita", "Sunline Mortgage",         "expense", "Mortgage",              None,   10),
    ("desert_casita", "Desert Sky Power",         "expense", "Utilities",             None,   10),
    ("desert_casita", "Sunbelt Home Insurance",   "expense", "Insurance",             None,   10),
    ("desert_casita", "Mesa County Treasurer",    "expense", "Property Tax",          None,   10),
    ("desert_casita", "Adobe Cleaners",           "expense", "Repairs / Maintenance", None,   10),
    ("desert_casita", "Sunbelt Stays",            "income",  None,                    None,   20),
    ("lakeside_cabin", "Lakeland Mutual",         "expense", "Insurance",             None,   10),
    ("lakeside_cabin", "Lakeshore Propane",       "expense", "Utilities",             None,   10),
    ("lakeside_cabin", "Valley Electric",         "expense", "Utilities",             None,   10),
    ("lakeside_cabin", "Pinecrest County",        "expense", "Property Tax",          None,   10),
    ("lakeside_cabin", "Ace Hardware",            "expense", "Repairs / Maintenance", None,   10),
    ("lakeside_cabin", "Check #",                 "expense", "Moorage",               "Rent", 60),
    ("lakeside_cabin", "Northwoods Property Mgmt", "income", None,                    None,   20),
]


# ---------------------------------------------------------------------------
# Persona — ~18 months of transactions. Cadences generate deterministic dates
# (seeded RNG) offset from the seed anchor, so the ledger is always current.
#
# cadence: ("monthly", day_of_month)
#        | ("interval", days)             every ~N days
#        | ("weekly", weekday, prob)      that weekday, present with probability
# window: (min_offset_days, max_offset_days) relative to the anchor — lets the
#         ended salary stop 3 weeks ago and unemployment start 4 weeks ago.
# Amounts in cents: negative = expense, positive = income (Monarch convention).
# ---------------------------------------------------------------------------
TXN_WINDOW_DAYS = 548  # ~18 months
# (merchant, raw_category, cadence, amount_cents, jitter_pct, account, window)
DEMO_TRANSACTIONS = [
    # --- income (ledger realism; property rents classify via income rules) ---
    ("Employer Payroll",        "Paychecks",       ("interval", 14),   605_000, 0.0,  "Everyday Checking", (-TXN_WINDOW_DAYS, -21)),
    ("Employer Severance",      "Paychecks",       ("interval", 14),   290_000, 0.0,  "Everyday Checking", (-84, 0)),
    ("State Employment Dept",   "Other Income",    ("interval", 14),    62_000, 0.0,  "Everyday Checking", (-28, 0)),
    ("Interest Payment",        "Interest",        ("monthly", 1),      39_000, 0.15, "High-Yield Savings", None),
    ("Northwoods Property Mgmt", "Business Income", ("monthly", 3),    345_000, 0.0,  "Everyday Checking", None),
    ("Tenant Direct",           "Business Income", ("monthly", 5),      36_000, 0.0,  "Everyday Checking", None),
    # --- property expenses (classified by the rules above) ---
    ("Granite Home Loans",      "Mortgage & Rent", ("monthly", 1),    -285_000, 0.0,  "Everyday Checking", None),
    ("LoftPay HOA",             "Mortgage & Rent", ("monthly", 5),     -68_000, 0.0,  "Everyday Checking", None),
    ("Metro Power & Light",     "Utilities",       ("monthly", 12),    -13_500, 0.30, "Everyday Checking", None),
    ("Cornerstone Home Insurance", "Insurance",    ("monthly", 8),     -19_000, 0.0,  "Everyday Checking", None),
    ("Sunline Mortgage",        "Mortgage & Rent", ("monthly", 1),    -165_000, 0.0,  "Everyday Checking", None),
    ("Desert Sky Power",        "Utilities",       ("monthly", 15),     -7_500, 0.35, "Everyday Checking", None),
    ("Adobe Cleaners",          "Home Improvement", ("interval", 40),  -11_500, 0.35, "Everyday Checking", None),
    ("Loft Assoc Special Assessment", "Mortgage & Rent", ("monthly", 6), -420_000, 0.0, "Everyday Checking", (-45, 0)),
    ("Sunbelt Home Insurance",  "Insurance",       ("monthly", 20),    -13_000, 0.0,  "Everyday Checking", None),
    ("Lakeland Mutual",         "Insurance",       ("monthly", 16),    -12_500, 0.0,  "Everyday Checking", None),
    ("Lakeshore Propane",       "Utilities",       ("monthly", 22),     -7_000, 0.45, "Everyday Checking", None),
    ("Valley Electric",         "Utilities",       ("monthly", 24),     -6_500, 0.30, "Everyday Checking", None),
    ("Ace Hardware",            "Home Improvement", ("interval", 42),  -11_000, 0.60, "Everyday Checking", None),
    # --- personal spending (the Tracker's world: non-property, non-tax) ---
    ("Green Basket Market",     "Groceries",       ("weekly", 5, 0.90), -19_500, 0.35, "Everyday Checking", None),
    ("City Market Provisions",  "Groceries",       ("interval", 24),    -9_500, 0.40, "Everyday Checking", None),
    ("Corner Grind Coffee",     "Coffee Shops",    ("weekly", 1, 0.75),    -900, 0.30, "Everyday Checking", None),
    ("Salt & Vine",             "Restaurants & Bars", ("interval", 11), -10_500, 0.40, "Everyday Checking", None),
    ("Basil Thai",              "Restaurants & Bars", ("interval", 13),  -6_200, 0.30, "Everyday Checking", None),
    ("Taco Cantina",            "Fast Food",       ("interval", 16),    -3_800, 0.30, "Everyday Checking", None),
    ("Brick Oven Pizza",        "Restaurants & Bars", ("interval", 19), -4_400, 0.30, "Everyday Checking", None),
    ("Fuel Stop",               "Gas & Fuel",      ("interval", 10),    -5_800, 0.25, "Everyday Checking", None),
    ("Truck & Auto Service",    "Service & Parts", ("interval", 100),  -31_000, 0.50, "Everyday Checking", None),
    ("Roadstar Auto Insurance", "Insurance",       ("monthly", 12),    -16_800, 0.0,  "Everyday Checking", None),
    ("Family Health Plan",      "Insurance",       ("monthly", 1),    -115_000, 0.0,  "Everyday Checking", None),
    ("Wellcare Pharmacy",       "Pharmacy",        ("interval", 20),    -3_800, 0.45, "Everyday Checking", None),
    ("Midtown Family Clinic",   "Medical",         ("interval", 75),   -14_000, 0.50, "Everyday Checking", None),
    ("Ridge Fitness",           "Health & Fitness", ("monthly", 3),     -4_900, 0.0,  "Everyday Checking", None),
    ("Metro Wireless",          "Mobile Phone",    ("monthly", 8),      -9_500, 0.0,  "Everyday Checking", None),
    ("City Fiber Internet",     "Internet & Cable", ("monthly", 12),    -7_900, 0.0,  "Everyday Checking", None),
    ("StreamFlix",              "Entertainment & Recreation", ("monthly", 4),  -1_900, 0.0, "Everyday Checking", None),
    ("CloudTunes",              "Music",           ("monthly", 15),     -1_100, 0.0,  "Everyday Checking", None),
    ("PrimeShip",               "Shopping",        ("monthly", 17),     -1_400, 0.0,  "Everyday Checking", None),
    ("Lakeview Marina Slip",    "Boat Storage",    ("monthly", 2),     -31_500, 0.0,  "Everyday Checking", None),
    ("Lakeview Fuel Dock",      "Boat Gas",        ("interval", 26),    -7_800, 0.40, "Everyday Checking", None),
    ("Boatworks Supply",        "Boat Service & Parts", ("interval", 60), -15_000, 0.50, "Everyday Checking", None),
    ("ShopFast",                "Shopping",        ("interval", 6),     -5_400, 0.60, "Everyday Checking", None),
    ("Outdoor Outfitters",      "Clothing",        ("interval", 45),   -16_000, 0.50, "Everyday Checking", None),
    ("Page & Bindery",          "Books",           ("interval", 40),    -2_800, 0.40, "Everyday Checking", None),
    ("CinePlex",                "Entertainment & Recreation", ("interval", 30), -3_400, 0.30, "Everyday Checking", None),
    ("Clip Joint Barbers",      "Personal Care",   ("interval", 32),    -3_800, 0.15, "Everyday Checking", None),
    ("Skyline Air",             "Air Travel",      ("interval", 130),  -42_000, 0.30, "Everyday Checking", None),
    ("Lakeview Lodge",          "Hotel",           ("interval", 150),  -52_000, 0.30, "Everyday Checking", None),
    ("Bloom & Branch",          "Gifts & Donations", ("interval", 55),  -6_500, 0.40, "Everyday Checking", None),
    ("Community Foodbank",      "Gifts & Donations", ("monthly", 25),   -5_000, 0.0,  "Everyday Checking", None),
    ("Cash & ATM",              "Cash & ATM",      ("interval", 40),   -10_000, 0.0,  "Everyday Checking", None),
]

# Semiannual property tax bills: (merchant, amount_cents, months of year)
DEMO_PROPERTY_TAX_BILLS = [
    ("Metro County Treasurer",     -255_000, (4, 10)),
    ("Mesa County Treasurer",      -180_000, (5, 11)),
    ("Pinecrest County Treasurer", -110_000, (4, 10)),
]


def build_demo_config_values(anchor: date) -> dict:
    """FIRE config for the demo persona, anchored to the seed date.

    Age ~51.75, married filing jointly, retiring now, bridging to 59½ on
    severance + SEPP/72(t) + a Canadian RRSP drawdown. Uses the generic
    property_sales mechanism (the documented path): sell the Desert Casita in
    ~6 months, downsize out of the City Loft at month 60, proceeds compound in
    a taxable brokerage pool.
    """
    dob = anchor.replace(day=1) - relativedelta(months=621)  # age 51y 9mo
    return dict(
        date_of_birth=dob,
        target_retirement_age=52,
        life_expectancy=90,
        fire_variant="regular",
        safe_withdrawal_rate=4.0,
        expected_annual_return=7.0,
        expected_inflation_rate=3.0,
        target_annual_spending=15_600_000,  # $156,000/yr = $13,000/mo (cents)
        social_security_monthly=540_000,  # $5,400/mo combined at full retirement age (cents)
        social_security_start_age=67,
        pension_monthly=None,
        pension_start_age=None,
        healthcare_monthly_cost=115_000,  # $1,150/mo for the couple pre-Medicare (cents)
        medicare_start_age=65,
        rmd_start_age=73,
        target_legacy=0,
        custom_assumptions={
            "demo_persona": True,  # sentinel: lets re-runs (and --remove) recognize this config
            "sepp_bridge": True,
            "bridge_end_age": 59.5,
            "penalty_free_age": 59.5,
            "rule_of_55_eligible": False,
            "rental_occupancy_rate": 1.0,
            # Occupancy haircut (when a scenario lowers the rate) applies only
            # to the income property's rental source
            "occupancy_source_match": ["lakeside cabin"],
            "tax": {
                "filing_status": "married_filing_jointly",
                "household_size": 2,
                "cost_basis_pct": 0.6,
                "state": "CO",
                "state_tax_rate": 4.4,
            },
            # SEPP/72(t): fixed draws from the Rollover IRA starting month 12
            "sepp": {
                "ira_a_balance": 436_000,
                "ira_b_balance": 172_000,
                "sepp_monthly": 2_275,
                "sepp_start_month": 12,
                "ira_growth_rate": 0.06,
            },
            # Canadian RRSP cash-out (worked in Toronto in the 2000s):
            # $1,850/mo net starting month 12
            "rrsp": {
                "monthly_net": 1_850,
                "start_month": 12,
                "total_available": 198_000,
            },
            # Sale proceeds land in a taxable brokerage pool (6.5% real),
            # drawn before retirement accounts when cash runs thin
            "taxable_pool": {"starting_balance": 0, "return_rate": 0.065},
            # The documented property-sale mechanism (dollars, not cents)
            "property_sales": [
                {
                    "key": "desert_casita",
                    "re_bucket": "secondary",
                    "sale_month": 6,
                    "value": 386_000,
                    "cost_basis": 361_000,
                    "agent_fee_pct": 0.06,
                    "ltcg_rate": 0.15,
                    "state_tax_rate": 0.045,
                    "section_121_exclusion": 0,  # not the primary residence
                    "current_mortgage_balance": 238_000,
                    "mortgage_rate": 0.0675,
                    "mortgage_pi": 1_650,
                    "monthly_cost": 2_250,
                    "in_base_burn": False,  # carry cost sits outside the spending target while held
                    "post_sale_rent": 0,
                    "monthly_income": 0,  # its rental source ends before the sale
                    "proceeds_to": "taxable",
                    # The projection owns this sale — ignore the planned
                    # "Desert Casita Sale Proceeds" cashflow event (the Runway
                    # page still uses it)
                    "suppress_cashflow_match": "desert casita",
                },
                {
                    "key": "city_loft",
                    "re_bucket": "primary",
                    "sale_month": 60,
                    "value": 468_000,
                    "cost_basis": 468_000,
                    "agent_fee_pct": 0.06,
                    "ltcg_rate": 0.15,
                    "state_tax_rate": 0.0,
                    "section_121_exclusion": 500_000,  # married-filing-jointly §121
                    "current_mortgage_balance": 296_000,
                    "mortgage_rate": 0.062,
                    "mortgage_pi": 2_850,
                    "monthly_cost": 5_900,
                    "in_base_burn": True,  # all-in cost is inside the spending target
                    "post_sale_rent": 2_400,
                    "monthly_income": 360,  # spare-room rental that ends at sale
                    "proceeds_to": "taxable",
                },
            ],
            # Projection assumptions (all REAL, after-inflation rates)
            "projection": {
                "surplus_investment_rate": 0.04,
                "cash_reserve_months": 12,
                "cash_savings_rate_early": 0.01,
                "cash_savings_rate_late": 0.0,
                "cash_savings_cutover_month": 60,
                "re_appreciation_rate": 0.01,
                "primary_property_purchase_price": 468_000,
                "primary_property_agent_fee_pct": 0.06,
                "primary_property_mortgage_pi": 2_850,
                "primary_property_cost_basis": 468_000,
                "primary_property_ltcg_rate": 0.15,
                "ss_early_reduction": 0.70,
                "ss_claim_age": 62,
                "spending_phase_slow": 0.85,
                "spending_phase_floor": 0.75,
                "spending_phase_slow_age": 70,
                "spending_phase_floor_age": 80,
                "ira_b_draw_threshold_months": 12,
            },
        },
    )


# ---------------------------------------------------------------------------
# Balance history generation (deterministic — no randomness, same input same DB)
# ---------------------------------------------------------------------------

def _history_balances(name: str, end_balance: int, start_factor: float,
                      amplitude: float, weeks: int) -> list[int]:
    """Weekly balances from `weeks` ago to today, geometric drift + sine wobble."""
    phase = sum(ord(c) for c in name) % 7
    out = []
    for i in range(weeks + 1):
        t = i / weeks
        drift = end_balance * (start_factor ** (1 - t))
        wobble = 1 + amplitude * math.sin(0.9 * i + phase)
        out.append(int(drift * wobble))
    out[-1] = end_balance  # today's snapshot must equal the live balance
    return out


def _mortgage_balances(end_balance: int, monthly_principal: int, weeks: int) -> list[int]:
    """Mortgage balance declines ~linearly; it was higher in the past."""
    weekly_principal = monthly_principal * 12 / 52
    return [int(end_balance + weekly_principal * (weeks - i)) for i in range(weeks + 1)]


# ---------------------------------------------------------------------------
# Transaction generation (seeded RNG — same anchor date, same ledger)
# ---------------------------------------------------------------------------

def _month_day(anchor: date, months_back: int, day: int) -> date:
    """The `day`-th of the month `months_back` months ago, clamped to month length."""
    base = (anchor.replace(day=1) - relativedelta(months=months_back))
    last = (base + relativedelta(months=1) - timedelta(days=1)).day
    return base.replace(day=min(day, last))


def generate_demo_transactions(anchor: date) -> list[dict]:
    """Expand the cadence specs into dated rows. Deterministic per anchor date."""
    rng = random.Random(4257)
    start = anchor - timedelta(days=TXN_WINDOW_DAYS)
    rows: list[dict] = []

    def emit(day: date, merchant: str, category: str, amount: int, account: str):
        if start <= day <= anchor:
            rows.append(dict(date=day, merchant=merchant, category=category,
                             amount=amount, account=account))

    for merchant, category, cadence, amount, jitter, account, window in DEMO_TRANSACTIONS:
        lo = anchor + timedelta(days=window[0]) if window else start
        hi = anchor + timedelta(days=window[1]) if window else anchor

        def jittered() -> int:
            return int(amount * (1 + jitter * rng.uniform(-1, 1)))

        if cadence[0] == "monthly":
            for m in range(TXN_WINDOW_DAYS // 28):
                d = _month_day(anchor, m, cadence[1])
                if lo <= d <= hi:
                    emit(d, merchant, category, jittered(), account)
        elif cadence[0] == "interval":
            step = cadence[1]
            d = hi - timedelta(days=rng.randint(0, step // 2))
            while d >= lo:
                emit(d, merchant, category, jittered(), account)
                d -= timedelta(days=int(step * rng.uniform(0.75, 1.25)))
        elif cadence[0] == "weekly":
            weekday, prob = cadence[1], cadence[2]
            d = hi - timedelta(days=(hi.weekday() - weekday) % 7)
            while d >= lo:
                if rng.random() < prob:
                    emit(d, merchant, category, jittered(), account)
                d -= timedelta(days=7)

    # Semiannual property tax bills (raw category "Taxes" keeps them out of the
    # Tracker even before classification; the rules put them on the right P&L).
    for merchant, amount, months in DEMO_PROPERTY_TAX_BILLS:
        d = start.replace(day=15)
        while d <= anchor:
            if d.month in months:
                emit(d, merchant, "Taxes", amount, "Everyday Checking")
            d += relativedelta(months=1)

    # Snowbird-season short-term-rental payouts for the Desert Casita: bigger
    # and more frequent in winter, occasional in summer, quiet in the shoulders.
    d = start
    while d <= anchor:
        if d.month in (11, 12, 1, 2, 3):
            for _ in range(2):
                emit(d.replace(day=rng.randint(2, 27)), "Sunbelt Stays Payout",
                     "Business Income", int(90_000 * rng.uniform(0.6, 1.5)),
                     "Everyday Checking")
        elif d.month in (6, 7, 8) and rng.random() < 0.5:
            emit(d.replace(day=rng.randint(2, 27)), "Sunbelt Stays Payout",
                 "Business Income", int(45_000 * rng.uniform(0.7, 1.3)),
                 "Everyday Checking")
        d += relativedelta(months=1)

    # The cabin's dock-slip lease, paid by numbered check — exercises the
    # category-gated "Check #" rule (only a "Rent"-categorized check matches).
    check_no = 1041
    for m in range(TXN_WINDOW_DAYS // 30, -1, -1):
        d = _month_day(anchor, m, 6)
        if start <= d <= anchor:
            emit(d, f"Check #{check_no}", "Rent", -58_000, "Everyday Checking")
            check_no += 1

    return rows


# ---------------------------------------------------------------------------
# Seed / remove
# ---------------------------------------------------------------------------

async def _demo_rows(session, model):
    result = await session.execute(
        select(model).where(model.custom_data["demo_seed"].as_boolean() == True)  # noqa: E712
    )
    return result.scalars().all()


async def seed(force: bool, with_config: bool) -> None:
    anchor = date.today()

    async with async_session_factory() as session:
        # --- Guard: never seed a fake persona on top of real data ---
        result = await session.execute(
            select(Account).where(Account.source == DataSource.MONARCH).limit(1)
        )
        if result.scalar_one_or_none() is not None and not force:
            print("Monarch-synced accounts found — this database holds REAL data.")
            print("Seeding the demo persona on top would only confuse things.")
            print("(Use --force to seed demo data rows anyway, e.g. on a throwaway DB.)")
            sys.exit(1)

        # --- Accounts (upsert by name) ---
        names = [a[0] for a in ACCOUNTS]
        result = await session.execute(select(Account).where(Account.name.in_(names)))
        existing = {a.name: a for a in result.scalars().all()}

        accounts_by_name: dict[str, Account] = {}
        created = updated = 0
        for name, acct_type, institution, role, balance, is_asset, _, _ in ACCOUNTS:
            acct = existing.get(name)
            if acct is None:
                acct = Account(name=name, account_type=acct_type, source=DataSource.MANUAL)
                session.add(acct)
                created += 1
            else:
                updated += 1
            acct.institution = institution
            acct.current_balance = balance
            acct.is_asset = is_asset
            acct.include_in_net_worth = True
            acct.fire_role = role
            acct.custom_data = {**(acct.custom_data or {}), **DEMO_MARKER}
            accounts_by_name[name] = acct
        await session.flush()  # assign ids
        print(f"  Accounts: {created} created, {updated} updated")

        # --- Balance history (rebuild: delete + insert, dates re-anchor to today) ---
        demo_ids = [a.id for a in accounts_by_name.values()]
        await session.execute(
            delete(BalanceSnapshot).where(BalanceSnapshot.account_id.in_(demo_ids))
        )
        snap_count = 0
        for name, _, _, _, balance, _, start_factor, amplitude in ACCOUNTS:
            if name in MORTGAGE_MONTHLY_PRINCIPAL:
                balances = _mortgage_balances(
                    balance, MORTGAGE_MONTHLY_PRINCIPAL[name], HISTORY_WEEKS
                )
            else:
                balances = _history_balances(
                    name, balance, start_factor, amplitude, HISTORY_WEEKS
                )
            acct_id = accounts_by_name[name].id
            for i, bal in enumerate(balances):
                session.add(BalanceSnapshot(
                    account_id=acct_id,
                    date=anchor - timedelta(weeks=HISTORY_WEEKS - i),
                    balance=bal,
                    source=DataSource.MANUAL,
                ))
                snap_count += 1
        await session.flush()
        print(f"  Balance snapshots: {snap_count} ({HISTORY_WEEKS} weeks × {len(ACCOUNTS)} accounts)")

        # --- Income sources (upsert by name) ---
        src_names = [s[0] for s in INCOME_SOURCES]
        result = await session.execute(
            select(IncomeSource).where(IncomeSource.name.in_(src_names))
        )
        existing_src = {s.name: s for s in result.scalars().all()}
        for name, itype, annual, end_offset in INCOME_SOURCES:
            src = existing_src.get(name)
            if src is None:
                src = IncomeSource(name=name, income_type=itype, annual_amount=annual)
                session.add(src)
            src.income_type = itype
            src.annual_amount = annual
            src.frequency = "monthly"
            src.end_date = anchor + timedelta(days=end_offset) if end_offset is not None else None
            src.is_active = True
            src.is_taxable = True
            src.custom_data = {**(src.custom_data or {}), **DEMO_MARKER}
        print(f"  Income sources: {len(INCOME_SOURCES)}")

        # --- Cashflow events (upsert by name; drop stale demo events) ---
        ev_names = [e[0] for e in CASHFLOW_EVENTS]
        for stale in await _demo_rows(session, CashflowEvent):
            if stale.name not in ev_names:
                await session.delete(stale)
        result = await session.execute(
            select(CashflowEvent).where(CashflowEvent.name.in_(ev_names))
        )
        existing_ev = {e.name: e for e in result.scalars().all()}
        for name, etype, amount, offset, prob, recurring, recurrence, end_offset in CASHFLOW_EVENTS:
            if isinstance(offset, tuple):
                ev_date = anchor + relativedelta(months=offset[1])
            else:
                ev_date = anchor + timedelta(days=offset)
            ev = existing_ev.get(name)
            if ev is None:
                ev = CashflowEvent(name=name, event_type=etype, amount_cents=amount, date=ev_date)
                session.add(ev)
            ev.event_type = etype
            ev.amount_cents = amount
            ev.date = ev_date
            ev.probability = prob
            ev.is_recurring = recurring
            ev.recurrence = recurrence
            ev.end_date = anchor + timedelta(days=end_offset) if end_offset is not None else None
            ev.status = "planned"
            ev.custom_data = {**(ev.custom_data or {}), **DEMO_MARKER}
        print(f"  Cashflow events: {len(CASHFLOW_EVENTS)}")

        # --- Properties (upsert by key) + classification rules (rebuild) ---
        prop_keys = [p[0] for p in DEMO_PROPERTIES]
        result = await session.execute(select(Property).where(Property.key.in_(prop_keys)))
        existing_props = {p.key: p for p in result.scalars().all()}
        props_by_key: dict[str, Property] = {}
        for order, (key, pname, address, color, value, loan, purchase,
                    rental, rental_full, notes) in enumerate(DEMO_PROPERTIES):
            prop = existing_props.get(key)
            if prop is None:
                prop = Property(key=key)
                session.add(prop)
            prop.name = pname
            prop.address = address
            prop.color = color
            prop.value_cents = value
            prop.loan_balance_cents = loan
            prop.purchase_price_cents = purchase
            prop.potential_monthly_rental_cents = rental
            prop.potential_monthly_rental_full_cents = rental_full
            prop.display_order = order
            prop.is_active = True
            prop.notes = notes
            prop.extra_data = {**(prop.extra_data or {}), **DEMO_MARKER}
            props_by_key[key] = prop
        await session.flush()

        prop_ids = [p.id for p in props_by_key.values()]
        await session.execute(
            delete(PropertyRule).where(PropertyRule.property_id.in_(prop_ids))
        )
        for key, pattern, kind, category, require_cat, priority in DEMO_PROPERTY_RULES:
            session.add(PropertyRule(
                property_id=props_by_key[key].id, pattern=pattern, rule_kind=kind,
                expense_category=category, require_tx_category=require_cat,
                priority=priority,
            ))
        print(f"  Properties: {len(DEMO_PROPERTIES)} ({len(DEMO_PROPERTY_RULES)} rules)")

        # --- Transactions (rebuild: delete + regenerate, dates re-anchor) ---
        await session.execute(
            delete(Transaction).where(Transaction.account_id.in_(demo_ids))
        )
        txn_rows = generate_demo_transactions(anchor)
        for row in txn_rows:
            session.add(Transaction(
                account_id=accounts_by_name[row["account"]].id,
                external_id=None,
                date=row["date"],
                amount=row["amount"],
                category=row["category"],
                merchant=row["merchant"],
                source=DataSource.MANUAL,
            ))

        # Two peer-to-peer rent deposits for the Desert Casita. P2P merchants
        # carry no property signal, so rules can never classify them: one is
        # manually assigned (the override workflow), one is left unclassified —
        # find it in the Transactions ledger and assign it yourself.
        checking_id = accounts_by_name["Everyday Checking"].id
        session.add(Transaction(
            account_id=checking_id, external_id=None,
            date=anchor - timedelta(days=41), amount=68_500,
            category="Other Income", merchant="PayFriend",
            notes="Snowbird-week rent, paid person-to-person",
            source=DataSource.MANUAL,
            property_id=props_by_key["desert_casita"].id,
            property_category="Rental Income", property_source="manual",
        ))
        session.add(Transaction(
            account_id=checking_id, external_id=None,
            date=anchor - timedelta(days=6), amount=68_500,
            category="Other Income", merchant="PayFriend",
            notes="Snowbird-week rent — unclassified on purpose: assign it to a property from the Transactions page",
            source=DataSource.MANUAL,
        ))
        await session.flush()
        print(f"  Transactions: {len(txn_rows) + 2} over ~{TXN_WINDOW_DAYS // 30} months")

        # --- Category mappings (normalize the raw categories just inserted) ---
        mapped = await CategorySyncService(session).sync_from_transactions()
        if mapped:
            print(f"  Category mappings: {mapped} backfilled")

        # --- Classify transactions through the real rules engine ---
        counts = await PropertyPnLEngine(session).reclassify()
        print(f"  Reclassify: {counts}")

        # --- FIRE config ---
        result = await session.execute(select(FireConfig).limit(1))
        config = result.scalar_one_or_none()
        is_starter = (
            config is not None
            and config.date_of_birth == date(1976, 1, 1)
            and config.target_annual_spending == 12_000_000
        )  # fingerprint of scripts/seed_config.py's untouched starter persona
        is_demo = bool(config and (config.custom_assumptions or {}).get("demo_persona"))
        if config is None or config.date_of_birth is None or is_starter or is_demo or with_config:
            if config is None:
                config = FireConfig()
                session.add(config)
            for field, value in build_demo_config_values(anchor).items():
                setattr(config, field, value)
            print("  FIRE config: demo persona written")
        else:
            print("  FIRE config: already customized — left untouched"
                  " (pass --with-config to overwrite with the demo persona)")

        # --- Net worth history (recompute from balance snapshots) ---
        if not force:
            # Fresh-install path: stale aggregate rows from a previous anchor
            # would linger (upsert is by date), so rebuild from scratch.
            await session.execute(delete(NetWorthSnapshot))
        nw_count = await NetWorthEngine(session).backfill_snapshots()
        print(f"  Net worth snapshots: {nw_count} recomputed")

        await session.commit()

    print(f"""
Done. The demo persona is live (anchored to {anchor}).

  Open the app: Dashboard, Retirement, Runway, Spending, Tracker, Transactions,
  Properties, and Config are all populated.
  Optional: seed example what-if scenarios too →  uv run python ../scripts/seed_scenarios.py
  Going live with your own data later? Connect Monarch (see docs/MONARCH_SETUP.md),
  then remove the demo rows:  uv run python ../scripts/seed_demo.py --remove
""")


async def seed_if_empty() -> None:
    """First-run auto-seed used by the migrate step: seed the demo persona ONLY when the
    database has no accounts and SEED_DEMO is not 'false'. No-op otherwise. Never raises —
    a seeding hiccup must not block the stack from starting."""
    if os.environ.get("SEED_DEMO", "true").strip().lower() == "false":
        print("SEED_DEMO=false — skipping first-run demo auto-seed.")
        return
    try:
        async with async_session_factory() as session:
            existing = await session.execute(select(Account).limit(1))
            if existing.scalar_one_or_none() is not None:
                print("Database already has accounts — skipping demo auto-seed.")
                return
        await seed(force=False, with_config=False)
    except Exception as exc:  # noqa: BLE001 — must never block stack startup
        print(f"Demo auto-seed skipped (non-fatal): {exc}")


async def remove() -> None:
    from app.ingestion.demo_data import clear_demo_data

    async with async_session_factory() as session:
        summary = await clear_demo_data(session)
        print(f"  Accounts removed: {summary['accounts']} (and their balance history)")
        print(f"  Transactions removed: {summary['transactions']}")
        print(f"  Properties removed: {summary['properties']} (and their rules)")
        print(f"  Income sources removed: {summary['income_sources']}")
        print(f"  Cashflow events removed: {summary['cashflow_events']}")
        if summary["net_worth_snapshots"]:
            print(f"  Net worth snapshots: {summary['net_worth_snapshots']} recomputed from remaining data")
        else:
            print("  Net worth snapshots: cleared (no balance history left)")

        result = await session.execute(select(FireConfig).limit(1))
        config = result.scalar_one_or_none()
        if config is not None and (config.custom_assumptions or {}).get("demo_persona"):
            print("\n  NOTE: the FIRE config still holds the demo persona's plan.")
            print("  Replace it with your own numbers under Settings -> Plan (or run seed_config.py")
            print("  after clearing it) — projections reflect the demo until you do.")

        await session.commit()
    print("\nDemo data removed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remove", action="store_true", help="delete previously seeded demo rows")
    parser.add_argument("--force", action="store_true",
                        help="seed data rows even if Monarch accounts exist (demo/testing only)")
    parser.add_argument("--with-config", action="store_true",
                        help="overwrite an already-customized FIRE config with the demo persona")
    parser.add_argument("--if-empty", action="store_true",
                        help="first-run auto-seed: seed only if the DB has no accounts and "
                             "SEED_DEMO != false; no-op otherwise (used by the migrate step)")
    args = parser.parse_args()

    if args.remove:
        asyncio.run(remove())
    elif args.if_empty:
        asyncio.run(seed_if_empty())
    else:
        asyncio.run(seed(force=args.force, with_config=args.with_config))
