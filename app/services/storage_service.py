import os
import uuid
from functools import lru_cache
from mimetypes import guess_type
from typing import Protocol

from fastapi import UploadFile

from app.core.config import settings

try:
    import boto3
except ImportError:  # pragma: no cover - validated at runtime for S3 backend.
    boto3 = None


class StorageService(Protocol):
    async def save(self, file: UploadFile) -> str: ...

    async def save_bytes(self, filename: str | None, content: bytes) -> str: ...

    def url_for(self, storage_key: str | None) -> str | None: ...


def _extension_from_filename(filename: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if not ext or len(ext) > 10:
        return ""
    return ext


def _build_storage_name(filename: str | None) -> str:
    return f"{uuid.uuid4().hex}{_extension_from_filename(filename)}"


def _normalize_prefix(raw: str | None) -> str:
    cleaned = (raw or "").strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


class LocalStorageService:
    def __init__(self):
        os.makedirs(settings.STORAGE_DIR, exist_ok=True)

    async def save(self, file: UploadFile) -> str:
        content = await file.read()
        return await self.save_bytes(file.filename, content)

    async def save_bytes(self, filename: str | None, content: bytes) -> str:
        name = _build_storage_name(filename)
        abs_path = os.path.join(settings.STORAGE_DIR, name)
        with open(abs_path, "wb") as f:
            f.write(content)
        return name

    def url_for(self, storage_key: str | None) -> str | None:
        if not storage_key:
            return None
        return f"/storage/{storage_key}"


class S3StorageService:
    def __init__(self):
        if boto3 is None:
            raise RuntimeError("boto3 is required when STORAGE_BACKEND is 's3'")
        if not settings.S3_BUCKET:
            raise RuntimeError("S3_BUCKET is required when STORAGE_BACKEND is 's3'")

        self.bucket = settings.S3_BUCKET
        self.endpoint_url = settings.S3_ENDPOINT_URL
        self.key_prefix = _normalize_prefix(settings.S3_KEY_PREFIX)

        client_kwargs = {}
        if settings.S3_REGION:
            client_kwargs["region_name"] = settings.S3_REGION
        if self.endpoint_url:
            client_kwargs["endpoint_url"] = self.endpoint_url
        if settings.S3_ACCESS_KEY_ID and settings.S3_SECRET_ACCESS_KEY:
            client_kwargs["aws_access_key_id"] = settings.S3_ACCESS_KEY_ID
            client_kwargs["aws_secret_access_key"] = settings.S3_SECRET_ACCESS_KEY
        if settings.S3_SESSION_TOKEN:
            client_kwargs["aws_session_token"] = settings.S3_SESSION_TOKEN

        self.client = boto3.client("s3", **client_kwargs)

    async def save(self, file: UploadFile) -> str:
        content = await file.read()
        return await self.save_bytes(file.filename, content)

    async def save_bytes(self, filename: str | None, content: bytes) -> str:
        key = f"{self.key_prefix}{_build_storage_name(filename)}"
        mime_type = guess_type(filename or "")[0] or "application/octet-stream"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=mime_type,
        )
        return key

    def url_for(self, storage_key: str | None) -> str | None:
        if not storage_key:
            return None

        if settings.S3_USE_PRESIGNED_URL:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": storage_key},
                ExpiresIn=settings.S3_PRESIGNED_URL_EXPIRES,
            )

        if settings.S3_PUBLIC_BASE_URL:
            return f"{settings.S3_PUBLIC_BASE_URL.rstrip('/')}/{storage_key}"

        if self.endpoint_url:
            return f"{self.endpoint_url.rstrip('/')}/{self.bucket}/{storage_key}"

        region = settings.S3_REGION or self.client.meta.region_name or "us-east-1"
        if region == "us-east-1":
            return f"https://{self.bucket}.s3.amazonaws.com/{storage_key}"
        return f"https://{self.bucket}.s3.{region}.amazonaws.com/{storage_key}"


@lru_cache(maxsize=1)
def get_storage_service() -> StorageService:
    backend = (settings.STORAGE_BACKEND or "local").strip().lower()
    if backend == "local":
        return LocalStorageService()
    if backend == "s3":
        return S3StorageService()
    raise RuntimeError(f"Unsupported STORAGE_BACKEND: {settings.STORAGE_BACKEND}")
