"""Account-type manual override (fire-master#8).

The enrichment PATCH accepts account_type; setting it stamps
custom_data.account_type_manual (the sync upsert's clobber guard), and an
explicit null clears the flag so auto-mapping resumes on the next sync.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engines.asset_hub import AssetHubEngine
from app.models.enums import AccountType
from app.schemas.account import AccountEnrichmentUpdate


def _engine_with_account(**attrs):
    account = MagicMock()
    account.custom_data = attrs.pop("custom_data", None)
    account.account_type = attrs.pop("account_type", AccountType.OTHER)
    for k, v in attrs.items():
        setattr(account, k, v)
    engine = AssetHubEngine(db=MagicMock())
    engine.db.get = AsyncMock(return_value=account)
    engine.db.flush = AsyncMock()
    return engine, account


class TestAccountTypeOverride:
    async def test_setting_type_stamps_manual_flag(self):
        engine, account = _engine_with_account()
        data = AccountEnrichmentUpdate(account_type=AccountType.VEHICLE)
        await engine.update_account_enrichment("id", data)
        assert account.account_type == AccountType.VEHICLE
        assert account.custom_data["account_type_manual"] is True

    async def test_explicit_null_clears_flag_keeps_type(self):
        engine, account = _engine_with_account(
            account_type=AccountType.VEHICLE,
            custom_data={"account_type_manual": True, "keep": "me"},
        )
        # exclude_unset semantics: explicit null must be present in the payload
        data = AccountEnrichmentUpdate.model_validate({"account_type": None})
        await engine.update_account_enrichment("id", data)
        assert "account_type_manual" not in account.custom_data
        assert account.custom_data["keep"] == "me"
        # type untouched now; next sync re-maps it
        assert account.account_type == AccountType.VEHICLE

    async def test_absent_field_touches_nothing(self):
        engine, account = _engine_with_account(
            custom_data={"account_type_manual": True}
        )
        data = AccountEnrichmentUpdate(notes="hello")
        await engine.update_account_enrichment("id", data)
        assert account.custom_data == {"account_type_manual": True}
        assert account.notes == "hello"

    async def test_existing_custom_data_preserved_on_set(self):
        engine, account = _engine_with_account(custom_data={"foo": "bar"})
        data = AccountEnrichmentUpdate(account_type=AccountType.SAVINGS)
        await engine.update_account_enrichment("id", data)
        assert account.custom_data == {"foo": "bar", "account_type_manual": True}
