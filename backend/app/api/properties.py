"""Property P&L API — properties CRUD, classification rules, P&L, overrides, manual entries."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.engines.property_pnl import EXPENSE_CATEGORIES, RENTAL_INCOME, PropertyPnLEngine
from app.models.account import Account
from app.models.enums import AccountType, DataSource
from app.models.property import Property
from app.models.property_rule import PropertyRule
from app.models.transaction import Transaction
from app.schemas.property import (
    CategoriesResponse,
    ManualEntryCreate,
    ManualEntryUpdate,
    OverrideRequest,
    PnLResponse,
    PropertyCreate,
    PropertyOut,
    PropertyPnL,
    PropertyPnLTotals,
    PropertyTransaction,
    PropertyTransactionsResponse,
    PropertyUpdate,
    ReclassifyResult,
    RuleCreate,
    RuleOut,
    RuleUpdate,
)

router = APIRouter(prefix="/api/properties", tags=["properties"])

MANUAL_ACCOUNT_NAME = "Manual / Off-Monarch"


def _d(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)


def _c(dollars: float | None) -> int | None:
    return None if dollars is None else int(round(dollars * 100))


def _property_out(p: Property) -> PropertyOut:
    return PropertyOut(
        id=p.id,
        key=p.key,
        name=p.name,
        address=p.address,
        color=p.color,
        value=_d(p.value_cents) or 0.0,
        loan_balance=_d(p.loan_balance_cents) or 0.0,
        equity=_d(p.equity_cents) or 0.0,
        purchase_price=_d(p.purchase_price_cents) or 0.0,
        purchase_date=p.purchase_date,
        potential_monthly_rental=_d(p.potential_monthly_rental_cents),
        potential_monthly_rental_full=_d(p.potential_monthly_rental_full_cents),
        display_order=p.display_order,
        is_active=p.is_active,
        notes=p.notes or [],
        extra_data=p.extra_data or {},
    )


async def _get_property(db: AsyncSession, property_id: UUID) -> Property:
    result = await db.execute(select(Property).where(Property.id == property_id))
    prop = result.scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    return prop


# ---------------------------------------------------------------------------
#  Categories (vocabulary for dropdowns)
# ---------------------------------------------------------------------------

@router.get("/categories", response_model=CategoriesResponse)
async def get_categories(_user: str = Depends(get_current_user)):
    return CategoriesResponse(expense_categories=EXPENSE_CATEGORIES, income_category=RENTAL_INCOME)


# ---------------------------------------------------------------------------
#  Properties CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=list[PropertyOut])
async def list_properties(
    include_inactive: bool = Query(False),
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Property).order_by(Property.display_order)
    if not include_inactive:
        q = q.where(Property.is_active == True)  # noqa: E712
    result = await db.execute(q)
    return [_property_out(p) for p in result.scalars().all()]


@router.post("", response_model=PropertyOut)
async def create_property(
    data: PropertyCreate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prop = Property(
        key=data.key,
        name=data.name,
        address=data.address,
        color=data.color,
        value_cents=_c(data.value) or 0,
        loan_balance_cents=_c(data.loan_balance) or 0,
        purchase_price_cents=_c(data.purchase_price) or 0,
        purchase_date=data.purchase_date,
        potential_monthly_rental_cents=_c(data.potential_monthly_rental),
        potential_monthly_rental_full_cents=_c(data.potential_monthly_rental_full),
        display_order=data.display_order,
        notes=data.notes,
        extra_data=data.extra_data,
    )
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    return _property_out(prop)


@router.patch("/{property_id}", response_model=PropertyOut)
async def update_property(
    property_id: UUID,
    data: PropertyUpdate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prop = await _get_property(db, property_id)
    payload = data.model_dump(exclude_unset=True)
    cents_map = {
        "value": "value_cents",
        "loan_balance": "loan_balance_cents",
        "purchase_price": "purchase_price_cents",
        "potential_monthly_rental": "potential_monthly_rental_cents",
        "potential_monthly_rental_full": "potential_monthly_rental_full_cents",
    }
    for field, value in payload.items():
        if field in cents_map:
            setattr(prop, cents_map[field], _c(value))
        else:
            setattr(prop, field, value)
    await db.commit()
    await db.refresh(prop)
    return _property_out(prop)


@router.delete("/{property_id}")
async def delete_property(
    property_id: UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete (is_active=False) so historical transaction links survive."""
    prop = await _get_property(db, property_id)
    prop.is_active = False
    await db.commit()
    return {"detail": "deactivated"}


# ---------------------------------------------------------------------------
#  Rules CRUD
# ---------------------------------------------------------------------------

def _rule_out(r: PropertyRule) -> RuleOut:
    return RuleOut(
        id=r.id,
        property_id=r.property_id,
        match_type=r.match_type,
        pattern=r.pattern,
        expense_category=r.expense_category,
        require_tx_category=r.require_tx_category,
        rule_kind=r.rule_kind,
        priority=r.priority,
        is_active=r.is_active,
        notes=r.notes,
    )


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(
    property_id: UUID | None = None,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(PropertyRule).order_by(PropertyRule.priority)
    if property_id is not None:
        q = q.where(PropertyRule.property_id == property_id)
    result = await db.execute(q)
    return [_rule_out(r) for r in result.scalars().all()]


@router.post("/rules", response_model=RuleOut)
async def create_rule(
    data: RuleCreate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = PropertyRule(**data.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return _rule_out(rule)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(
    rule_id: UUID,
    data: RuleUpdate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(PropertyRule).where(PropertyRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return _rule_out(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(PropertyRule).where(PropertyRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await db.commit()
    return {"detail": "deleted"}


@router.post("/reclassify", response_model=ReclassifyResult)
async def reclassify(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    counts = await PropertyPnLEngine(db).reclassify()
    await db.commit()
    return ReclassifyResult(**counts)


# ---------------------------------------------------------------------------
#  P&L
# ---------------------------------------------------------------------------

@router.get("/pnl", response_model=PnLResponse)
async def get_pnl(
    start: date | None = None,
    end: date | None = None,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raw = await PropertyPnLEngine(db).get_pnl(start=start, end=end)
    properties = []
    for entry in raw["properties"]:
        cats = {
            cat: {month: _d(cents) for month, cents in by_month.items()}
            for cat, by_month in entry["categories"].items()
        }
        t = entry["totals"]
        prop_model = await _get_property(db, UUID(entry["property"]["id"]))
        properties.append(PropertyPnL(
            property=_property_out(prop_model),
            categories=cats,
            totals=PropertyPnLTotals(
                total_cost=_d(t["total_cost_cents"]) or 0.0,
                rental_actual=_d(t["rental_actual_cents"]) or 0.0,
                rental_potential=_d(t["rental_potential_cents"]) or 0.0,
                net_cost=_d(t["net_cost_cents"]) or 0.0,
                monthly_cost=_d(t["monthly_cost_cents"]) or 0.0,
                net_per_year=_d(t["net_per_year_cents"]) or 0.0,
                months=t["months"],
                first_month=t["first_month"],
            ),
            cost_per_equity=entry["cost_per_equity"],
        ))
    return PnLResponse(months=raw["months"], properties=properties)


# ---------------------------------------------------------------------------
#  Transactions + overrides
# ---------------------------------------------------------------------------

def _tx_out(t: Transaction) -> PropertyTransaction:
    return PropertyTransaction(
        id=t.id,
        date=t.date,
        merchant=t.merchant,
        category=t.category,
        amount=_d(t.amount) or 0.0,
        property_id=t.property_id,
        property_category=t.property_category,
        property_source=t.property_source,
    )


@router.get("/{property_id}/transactions", response_model=PropertyTransactionsResponse)
async def list_property_transactions(
    property_id: UUID,
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conditions = [Transaction.property_id == property_id]
    if month:
        conditions.append(func.to_char(Transaction.date, "YYYY-MM") == month)

    total_result = await db.execute(
        select(func.count()).select_from(Transaction).where(*conditions)
    )
    total = int(total_result.scalar() or 0)

    result = await db.execute(
        select(Transaction)
        .where(*conditions)
        .order_by(Transaction.date.desc())
        .limit(limit)
        .offset(offset)
    )
    return PropertyTransactionsResponse(
        transactions=[_tx_out(t) for t in result.scalars().all()],
        total=total,
    )


@router.put("/transactions/{tx_id}", response_model=PropertyTransaction)
async def override_transaction(
    tx_id: UUID,
    data: OverrideRequest,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await PropertyPnLEngine(db).set_override(tx_id, data.property_id, data.property_category)
    await db.commit()
    result = await db.execute(select(Transaction).where(Transaction.id == tx_id))
    tx = result.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return _tx_out(tx)


@router.delete("/transactions/{tx_id}/override", response_model=PropertyTransaction)
async def clear_transaction_override(
    tx_id: UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await PropertyPnLEngine(db).clear_override(tx_id)
    await db.commit()
    result = await db.execute(select(Transaction).where(Transaction.id == tx_id))
    tx = result.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return _tx_out(tx)


# ---------------------------------------------------------------------------
#  Manual entries (off-Monarch income/expense)
# ---------------------------------------------------------------------------

async def _manual_account(db: AsyncSession) -> Account:
    result = await db.execute(select(Account).where(Account.name == MANUAL_ACCOUNT_NAME))
    acct = result.scalar_one_or_none()
    if acct is None:
        acct = Account(
            name=MANUAL_ACCOUNT_NAME,
            account_type=AccountType.OTHER,
            current_balance=0,
            is_asset=False,
            include_in_net_worth=False,
            source=DataSource.MANUAL,
        )
        db.add(acct)
        await db.flush()
    return acct


@router.get("/manual-entries", response_model=list[PropertyTransaction])
async def list_manual_entries(
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Transaction)
        .where(Transaction.source == DataSource.MANUAL, Transaction.property_id.isnot(None))
        .order_by(Transaction.date.desc())
    )
    return [_tx_out(t) for t in result.scalars().all()]


@router.post("/manual-entries", response_model=PropertyTransaction)
async def create_manual_entry(
    data: ManualEntryCreate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    acct = await _manual_account(db)
    tx = Transaction(
        account_id=acct.id,
        external_id=None,
        date=data.date,
        amount=_c(data.amount) or 0,
        category=data.property_category,
        merchant=data.merchant,
        source=DataSource.MANUAL,
        property_id=data.property_id,
        property_category=data.property_category,
        property_source="manual",
        notes=data.notes,
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)
    return _tx_out(tx)


@router.patch("/manual-entries/{tx_id}", response_model=PropertyTransaction)
async def update_manual_entry(
    tx_id: UUID,
    data: ManualEntryUpdate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == tx_id, Transaction.source == DataSource.MANUAL
        )
    )
    tx = result.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Manual entry not found")
    payload = data.model_dump(exclude_unset=True)
    if "amount" in payload:
        tx.amount = _c(payload.pop("amount")) or 0
    if "property_category" in payload:
        tx.category = payload["property_category"]
    for field, value in payload.items():
        setattr(tx, field, value)
    await db.commit()
    await db.refresh(tx)
    return _tx_out(tx)


@router.delete("/manual-entries/{tx_id}")
async def delete_manual_entry(
    tx_id: UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == tx_id, Transaction.source == DataSource.MANUAL
        )
    )
    tx = result.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Manual entry not found")
    await db.delete(tx)
    await db.commit()
    return {"detail": "deleted"}
