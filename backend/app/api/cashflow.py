"""Cash flow events API — CRUD + runway projection."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.engines.cashflow import CashflowEngine
from app.models.cashflow_event import CashflowEvent
from app.schemas.cashflow import (
    CashflowEventCreate,
    CashflowEventResponse,
    CashflowEventUpdate,
    RunwayResponse,
)

router = APIRouter(prefix="/api/cashflow", tags=["cashflow"])


@router.get("/events", response_model=list[CashflowEventResponse])
async def list_events(
    status: str | None = None,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(CashflowEvent).order_by(CashflowEvent.date)
    if status:
        q = q.where(CashflowEvent.status == status)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/events", response_model=CashflowEventResponse)
async def create_event(
    data: CashflowEventCreate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    event = CashflowEvent(
        name=data.name,
        event_type=data.event_type,
        amount_cents=int(round(data.amount * 100)),
        date=data.date,
        is_recurring=data.is_recurring,
        recurrence=data.recurrence,
        end_date=data.end_date,
        probability=data.probability,
        status=data.status,
        category=data.category,
        linked_account_id=data.linked_account_id,
        notes=data.notes,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


@router.put("/events/{event_id}", response_model=CashflowEventResponse)
async def update_event(
    event_id: UUID,
    data: CashflowEventUpdate,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CashflowEvent).where(CashflowEvent.id == event_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    update_data = data.model_dump(exclude_unset=True)
    if "amount" in update_data:
        update_data["amount_cents"] = int(round(update_data.pop("amount") * 100))

    for field, value in update_data.items():
        setattr(event, field, value)

    await db.commit()
    await db.refresh(event)
    return event


@router.delete("/events/{event_id}")
async def delete_event(
    event_id: UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CashflowEvent).where(CashflowEvent.id == event_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    await db.delete(event)
    await db.commit()
    return {"detail": "deleted"}


@router.get("/runway", response_model=RunwayResponse)
async def get_runway(
    months: int = 24,
    income_override: float | None = Query(
        None, description="Override monthly income baseline (dollars). If omitted, uses DECLARED income: active income sources (dates honored) plus income events."
    ),
    burn_override: float | None = Query(
        None, description="Override monthly expense baseline (dollars). If omitted, uses trailing 90-day outflows minus payments matched to cashflow expense events."
    ),
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = CashflowEngine(db)
    return await engine.project_runway(
        months=months,
        income_override=income_override,
        burn_override=burn_override,
    )
