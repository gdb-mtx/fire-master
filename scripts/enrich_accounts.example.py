"""Example: bulk-enrich accounts over the REST API.

Account enrichment (fire_role, strategy, notes, targets) is what turns raw Monarch
balances into projection inputs — fire_role drives the net-worth partition and the
RE equity buckets in the wealth projection. Enrichment fields survive Monarch
re-syncs.

This is a TEMPLATE: copy it (e.g. to scripts/enrich_accounts.py, which is yours to
keep out of git), put your real account names in ACCOUNT_ENRICHMENT, and run it
once. You can also enrich account-by-account in the UI — this script just makes a
full first pass faster.

fire_role values the engine understands (see app/engines/fire_projections.py):
  liquid:      cash_reserve | operating_account
  retirement:  retirement_core | retirement_bridge | retirement_supplemental | tax_free_reserve
  real estate: primary_residence | primary_mortgage | sell_candidate | sell_with_property | income_producing
  inert:       illiquid_private | speculative | depreciating | system
(Authoritative list: GET /api/setup/status → valid_fire_roles. Anything else is
accepted but treated as "other", so the account does nothing in projections.)

Usage:
    API_URL=http://localhost:8000 FIREMASTER_USER=me FIREMASTER_PASSWORD=... \
        uv run python ../scripts/enrich_accounts.example.py
"""

import os
import sys

import httpx

API_URL = os.environ.get("API_URL", "http://localhost:8000")
USERNAME = os.environ.get("FIREMASTER_USER", "")
PASSWORD = os.environ.get("FIREMASTER_PASSWORD", "")

# Account name (as shown in /api/accounts) -> enrichment fields.
# Any subset of: fire_role, strategy, notes, tags, target_balance, target_allocation_pct.
ACCOUNT_ENRICHMENT: dict[str, dict] = {
    "Example Checking": {
        "fire_role": "operating_account",
        "strategy": "Monthly bills flow through here; keep ~1 month of expenses.",
    },
    "Example High-Yield Savings": {
        "fire_role": "cash_reserve",
        "strategy": "Emergency fund — 12 months of expenses.",
    },
    "Example Rollover IRA": {
        "fire_role": "retirement_core",
        "strategy": "Boglehead three-fund; no withdrawals before 59.5.",
    },
    "Example Brokerage": {
        "fire_role": "retirement_bridge",
        "strategy": "Taxable bridge funds for the pre-59.5 gap.",
    },
    "Example Home": {"fire_role": "primary_residence"},
    "Example Home Mortgage": {"fire_role": "primary_mortgage"},
    "Example Rental Property": {"fire_role": "income_producing"},
}


def main() -> int:
    if not PASSWORD:
        print("Set FIREMASTER_USER / FIREMASTER_PASSWORD env vars first.")
        return 1

    with httpx.Client(base_url=API_URL, timeout=30) as client:
        # 1. Login -> bearer token
        resp = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
        resp.raise_for_status()
        client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

        # 2. Fetch accounts, index by name
        accounts = client.get("/api/accounts").raise_for_status().json()
        by_name = {a["name"]: a for a in accounts}

        # 3. PATCH enrichment for every mapped account
        applied, missing = 0, []
        for name, fields in ACCOUNT_ENRICHMENT.items():
            acct = by_name.get(name)
            if acct is None:
                missing.append(name)
                continue
            client.patch(f"/api/accounts/{acct['id']}/enrichment", json=fields).raise_for_status()
            print(f"  ENRICH {name}: {fields}")
            applied += 1

        print(f"\nDone. Enriched {applied} account(s).")
        if missing:
            print(f"Not found (check names against /api/accounts): {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
