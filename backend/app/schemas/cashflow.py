"""Schemas for cash flow events and runway projections."""

import datetime as _dt
from uuid import UUID

from pydantic import BaseModel, Field, computed_field


class CashflowEventCreate(BaseModel):
    name: str
    event_type: str  # "income" or "expense"
    amount: float  # dollars (converted to cents on save)
    date: _dt.date
    is_recurring: bool = False
    recurrence: str | None = None  # "monthly", "quarterly", "annual"
    end_date: _dt.date | None = None
    probability: float = Field(1.0, ge=0.0, le=1.0)  # 0 is valid: a parked event
    status: str = "planned"
    category: str | None = None
    linked_account_id: UUID | None = None
    notes: str | None = None


class CashflowEventUpdate(BaseModel):
    name: str | None = None
    event_type: str | None = None
    amount: float | None = None
    date: _dt.date | None = None
    is_recurring: bool | None = None
    recurrence: str | None = None
    end_date: _dt.date | None = None
    probability: float | None = Field(None, ge=0.0, le=1.0)
    status: str | None = None
    category: str | None = None
    linked_account_id: UUID | None = None
    notes: str | None = None


class CashflowEventResponse(BaseModel):
    id: UUID
    name: str
    event_type: str
    amount_cents: int
    date: _dt.date
    is_recurring: bool
    recurrence: str | None = None
    end_date: _dt.date | None = None
    probability: float = 1.0
    status: str = "planned"
    category: str | None = None
    linked_account_id: UUID | None = None
    notes: str | None = None
    created_at: _dt.datetime | None = None

    @computed_field
    @property
    def amount(self) -> float:
        return self.amount_cents / 100

    model_config = {"from_attributes": True}


class MonthlyProjectionPoint(BaseModel):
    month: str  # "YYYY-MM"
    starting_cash: float
    income: float
    expenses: float
    net: float
    ending_cash: float
    events: list[str]  # names of events hitting this month


class RunwayResponse(BaseModel):
    current_cash: float
    monthly_burn: float  # active baseline (override or trailing)
    monthly_income: float  # CURRENT month's baseline (override, or modeled from income sources — tapers over the projection)
    net_monthly: float  # current-month income minus burn
    months_remaining: float | None  # from the projection's cash-zero crossing; None if cash never hits zero in the window
    cash_zero_date: _dt.date | None
    income_provenance: str = "modeled"  # "override" | "modeled" — where monthly_income came from
    trailing_burn: float  # historical trailing average (for reference)
    trailing_income: float  # observed trailing average — REFERENCE ONLY, never a projection input
    projection: list[MonthlyProjectionPoint]
