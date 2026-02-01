# app/models/email_verification.py
import uuid
import datetime as dt

from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    verification_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
