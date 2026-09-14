import uuid
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, Float, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import IncomeType


class IncomeSource(Base):
    __tablename__ = "income_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    income_type: Mapped[IncomeType] = mapped_column(
        Enum(IncomeType, name="income_type"), nullable=False
    )
    annual_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    frequency: Mapped[str] = mapped_column(String, nullable=False, default="monthly")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    growth_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tax_treatment: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    linked_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


def projection_annual_amount_cents(source: IncomeSource) -> int:
    """Return the amount of an income source that is actually investable.

    ``annual_amount`` remains the gross amount used by the tax engine.  A
    source may optionally carry ``custom_data.net_annual_amount`` when gross
    compensation and observed take-home cash flow differ.  Projection engines
    use that override so payroll taxes and withholding are not counted as
    money available to spend or invest.
    """
    custom_data = getattr(source, "custom_data", None)
    if not isinstance(custom_data, dict):
        return int(source.annual_amount)
    net_amount = custom_data.get("net_annual_amount")
    if net_amount is not None and not isinstance(net_amount, bool):
        try:
            return int(round(float(net_amount)))
        except (TypeError, ValueError, OverflowError):
            pass
    return int(source.annual_amount)
