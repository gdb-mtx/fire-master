"""Asset Hub tiles: the 30-day change is in net-worth terms for assets AND liabilities.

A liability's Account.current_balance is a positive amount owed, while its
BalanceSnapshot rows are negative. The tile used to subtract the raw values, which
ADDED the two for every liability: a $327K mortgage showed "+$654K" and a card whose
debt grew from $7.3K to $10.3K showed a green "+$17.7K". Now debt going up is a
negative (red) change and paying it down is positive (green).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engines.asset_hub import AssetHubEngine
from app.models.enums import AccountType


def _account(name, balance_cents, is_asset, account_type):
    a = MagicMock()
    a.id = uuid.uuid4()
    a.name = name
    a.account_type = account_type
    a.institution = None
    a.current_balance = balance_cents
    a.is_asset = is_asset
    a.fire_role = None
    a.tags = None
    a.notes = None
    a.strategy = None
    return a


def _snapshot_row(account_id, balance_cents):
    r = MagicMock()
    r.account_id = account_id
    r.balance = balance_cents
    return r


async def _tiles(accounts, old_balances):
    accounts_result = MagicMock()
    accounts_result.scalars.return_value.all.return_value = accounts
    snaps_result = MagicMock()
    snaps_result.all.return_value = [_snapshot_row(a.id, b) for a, b in zip(accounts, old_balances)]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[accounts_result, snaps_result])
    return {t.name: t.value_change_30d for t in await AssetHubEngine(db=db).get_account_tiles()}


@pytest.mark.asyncio
async def test_liability_change_is_in_net_worth_terms():
    card = _account("0% card", 1_034_670, False, AccountType.CREDIT_CARD)      # owes $10,346.70
    loan = _account("Mortgage", 32_710_900, False, AccountType.REAL_ESTATE)    # owes $327,109
    changes = await _tiles([card, loan], [-732_531, -32_735_100])             # snapshots: owed then, negative
    assert changes["0% card"] == pytest.approx(-3_021.39)    # debt grew -> net worth down (red)
    assert changes["Mortgage"] == pytest.approx(242.00)      # paid down -> net worth up (green)


@pytest.mark.asyncio
async def test_asset_change_unchanged():
    checking = _account("Checking", 6_682_000, True, AccountType.CHECKING)
    changes = await _tiles([checking], [6_000_000])
    assert changes["Checking"] == pytest.approx(6_820.00)


@pytest.mark.asyncio
async def test_liability_snapshot_stored_positive_still_correct():
    # Defensive: a snapshot written with a positive sign must not flip the result.
    card = _account("Card", 100_000, False, AccountType.CREDIT_CARD)
    changes = await _tiles([card], [150_000])
    assert changes["Card"] == pytest.approx(500.00)          # owed $1,500 -> $1,000: +$500
