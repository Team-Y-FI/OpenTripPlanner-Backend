import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, DateTime, ForeignKey, Float, Text
from app.models.base import Base

class UploadSession(Base):
    __tablename__ = "upload_sessions"
    upload_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    photos = relationship("Photo", back_populates="upload", cascade="all, delete-orphan")

class Photo(Base):
    __tablename__ = "photos"

    photo_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    upload_id: Mapped[str] = mapped_column(String(36), ForeignKey("upload_sessions.upload_id", ondelete="CASCADE"), index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="processing", index=True)

    exif_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    exif_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    place_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    place_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    place_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    place_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    place_lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    upload = relationship("UploadSession", back_populates="photos")
