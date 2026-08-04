from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, computed_field

from app.models.enums import AccountType, DataSource


class AccountResponse(BaseModel):
    id: UUID
    external_id: str | None
    name: str
    account_type: AccountType
    institution: str | None
    balance_cents: int
    is_asset: bool
    include_in_net_worth: bool
    source: DataSource
    last_synced_at: datetime | None

    # Enrichment fields
    notes: str | None = None
    tags: list[str] | None = None
    fire_role: str | None = None
    target_balance: int | None = None
    target_allocation_pct: float | None = None
    strategy: str | None = None
    custom_data: dict | None = None

    @computed_field
    @property
    def balance(self) -> float:
        return self.balance_cents / 100

    @computed_field
    @property
    def has_enrichment(self) -> bool:
        return any([self.notes, self.tags, self.fire_role, self.strategy])

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, account) -> "AccountResponse":
        return cls(
            id=account.id,
            external_id=account.external_id,
            name=account.name,
            account_type=account.account_type,
            institution=account.institution,
            balance_cents=account.current_balance,
            is_asset=account.is_asset,
            include_in_net_worth=account.include_in_net_worth,
            source=account.source,
            last_synced_at=account.last_synced_at,
            notes=account.notes,
            tags=account.tags,
            fire_role=account.fire_role,
            target_balance=account.target_balance,
            target_allocation_pct=account.target_allocation_pct,
            strategy=account.strategy,
            custom_data=account.custom_data,
        )


class AccountEnrichmentUpdate(BaseModel):
    notes: str | None = None
    tags: list[str] | None = None
    fire_role: str | None = None
    target_balance: int | None = None
    target_allocation_pct: float | None = None
    strategy: str | None = None
    custom_data: dict | None = None
    # Manual account-type override (fire-master#8): setting a value stamps
    # custom_data.account_type_manual so sync stops clobbering it; sending an
    # explicit null clears the override (auto-mapping resumes next sync).
    account_type: AccountType | None = None


class AccountDetailResponse(AccountResponse):
    balance_history: list[dict] = []
    recent_transactions: list[dict] = []
