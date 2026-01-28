import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base

class Plan(Base):
    __tablename__ = "plans"

    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), index=True)

    region: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)
    duration_hours: Mapped[float] = mapped_column(nullable=False)

    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    crowd_mode: Mapped[str] = mapped_column(String(16), nullable=False)

    purposes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    seed_spot_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    variants_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class SavedPlan(Base):
    __tablename__ = "saved_plans"

    saved_plan_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("plans.plan_id", ondelete="CASCADE"), index=True)

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    region: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class PlanSpotLink(Base):
    __tablename__ = "plan_spot_links"
    link_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    saved_plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("saved_plans.saved_plan_id", ondelete="CASCADE"), index=True)
    spot_id: Mapped[str] = mapped_column(String(36), ForeignKey("spots.spot_id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
