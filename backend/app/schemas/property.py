"""Schemas for the Property P&L module.

Money crosses the API in dollars (matching cashflow/tracker + frontend formatCurrency).
The engine works in BIGINT cents internally; routes convert at the boundary.
"""

import datetime as _dt
from uuid import UUID

from pydantic import BaseModel


# ---------------------------------------------------------------------------
#  Properties
# ---------------------------------------------------------------------------

class PropertyBase(BaseModel):
    name: str
    address: str | None = None
    color: str | None = None
    value: float = 0.0
    loan_balance: float = 0.0
    purchase_price: float = 0.0
    purchase_date: _dt.date | None = None
    potential_monthly_rental: float | None = None
    potential_monthly_rental_full: float | None = None
    display_order: int = 0
    notes: list[str] = []
    extra_data: dict = {}


class PropertyCreate(PropertyBase):
    key: str


class PropertyUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    color: str | None = None
    value: float | None = None
    loan_balance: float | None = None
    purchase_price: float | None = None
    purchase_date: _dt.date | None = None
    potential_monthly_rental: float | None = None
    potential_monthly_rental_full: float | None = None
    display_order: int | None = None
    is_active: bool | None = None
    notes: list[str] | None = None
    extra_data: dict | None = None


class PropertyOut(BaseModel):
    id: UUID
    key: str
    name: str
    address: str | None
    color: str | None
    value: float
    loan_balance: float
    equity: float
    purchase_price: float
    purchase_date: _dt.date | None
    potential_monthly_rental: float | None
    potential_monthly_rental_full: float | None
    display_order: int
    is_active: bool
    notes: list[str]
    extra_data: dict


# ---------------------------------------------------------------------------
#  Rules
# ---------------------------------------------------------------------------

class RuleCreate(BaseModel):
    property_id: UUID | None = None
    match_type: str = "merchant_substring"
    pattern: str
    expense_category: str | None = None
    require_tx_category: str | None = None
    rule_kind: str = "expense"  # 'expense' | 'income' | 'exclusion'
    priority: int = 100
    is_active: bool = True
    notes: str | None = None


class RuleUpdate(BaseModel):
    property_id: UUID | None = None
    match_type: str | None = None
    pattern: str | None = None
    expense_category: str | None = None
    require_tx_category: str | None = None
    rule_kind: str | None = None
    priority: int | None = None
    is_active: bool | None = None
    notes: str | None = None


class RuleOut(BaseModel):
    id: UUID
    property_id: UUID | None
    match_type: str
    pattern: str
    expense_category: str | None
    require_tx_category: str | None
    rule_kind: str
    priority: int
    is_active: bool
    notes: str | None


# ---------------------------------------------------------------------------
#  P&L
# ---------------------------------------------------------------------------

class PropertyPnLTotals(BaseModel):
    total_cost: float
    rental_actual: float
    rental_potential: float
    net_cost: float
    monthly_cost: float
    net_per_year: float
    # Calendar months the per-month figures divide by, counted from first_month
    # ("YYYY-MM"): the later of the period start and the property's first transaction.
    months: int
    first_month: str | None


class PropertyPnL(BaseModel):
    property: PropertyOut
    categories: dict[str, dict[str, float]]  # category -> "YYYY-MM" -> dollars
    totals: PropertyPnLTotals
    cost_per_equity: float | None


class PnLResponse(BaseModel):
    months: list[str]
    properties: list[PropertyPnL]


# ---------------------------------------------------------------------------
#  Transactions / overrides / manual entries
# ---------------------------------------------------------------------------

class PropertyTransaction(BaseModel):
    id: UUID
    date: _dt.date
    merchant: str | None
    category: str | None
    amount: float
    property_id: UUID | None
    property_category: str | None
    property_source: str | None  # 'rule' | 'manual' | None


class PropertyTransactionsResponse(BaseModel):
    transactions: list[PropertyTransaction]
    total: int


class OverrideRequest(BaseModel):
    property_id: UUID | None = None
    property_category: str | None = None


class ManualEntryCreate(BaseModel):
    property_id: UUID
    date: _dt.date
    amount: float  # signed dollars: positive = income, negative = expense
    property_category: str
    merchant: str | None = None
    notes: str | None = None


class ManualEntryUpdate(BaseModel):
    property_id: UUID | None = None
    date: _dt.date | None = None
    amount: float | None = None
    property_category: str | None = None
    merchant: str | None = None
    notes: str | None = None


class ReclassifyResult(BaseModel):
    matched: int
    unmatched: int
    skipped_manual: int


class CategoriesResponse(BaseModel):
    expense_categories: list[str]
    income_category: str
