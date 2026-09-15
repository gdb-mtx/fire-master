import uuid
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Date, DateTime, Float, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FireConfig(Base):
    """Single-row table holding all FIRE assumptions and targets."""

    __tablename__ = "fire_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )

    # Personal
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    target_retirement_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_retirement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    life_expectancy: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    fire_variant: Mapped[str] = mapped_column(String, nullable=False, default="regular")

    # Core assumptions
    safe_withdrawal_rate: Mapped[float] = mapped_column(Float, nullable=False, default=4.0)
    expected_annual_return: Mapped[float] = mapped_column(Float, nullable=False, default=7.0)
    expected_inflation_rate: Mapped[float] = mapped_column(Float, nullable=False, default=3.0)
    target_annual_spending: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Social Security + Pension
    social_security_monthly: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    social_security_start_age: Mapped[int] = mapped_column(Integer, nullable=False, default=67)
    pension_monthly: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pension_start_age: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Healthcare + Medicare
    healthcare_monthly_cost: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    post_medicare_healthcare_monthly_cost: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    medicare_start_age: Mapped[int] = mapped_column(Integer, nullable=False, default=65)

    # Tax + RMDs
    rmd_start_age: Mapped[int] = mapped_column(Integer, nullable=False, default=73)
    state_tax_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    federal_marginal_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Draw-down strategy
    target_legacy: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # cents — $0 = spend it all

    # Notes + extensible
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_assumptions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
