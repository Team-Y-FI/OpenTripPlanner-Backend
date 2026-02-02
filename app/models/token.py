# app/models/token.py
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    # ✅ DB가 token_id 자동생성 안 해주는 상태이므로 "직접 생성하는 문자열 PK"로 운영
    token_id: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False)

    # ✅ users.user_id가 varchar라면 FK도 varchar로 맞춤
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # ✅ hash_refresh_token이 sha256 hex(64자)면 64로 맞추는 게 안전
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user = relationship("User", back_populates="refresh_tokens")
