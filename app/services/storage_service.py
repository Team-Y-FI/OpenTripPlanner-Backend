import os, uuid
from fastapi import UploadFile
from app.core.config import settings

class LocalStorageService:
    def __init__(self):
        os.makedirs(settings.STORAGE_DIR, exist_ok=True)

    async def save(self, file: UploadFile) -> str:
        """Save file into STORAGE_DIR and return a storage key (relative filename)."""
        content = await file.read()
        return await self.save_bytes(file.filename, content)

    async def save_bytes(self, filename: str | None, content: bytes) -> str:
        ext = os.path.splitext(filename or "")[1].lower()
        name = f"{uuid.uuid4().hex}{ext if ext else ''}"
        abs_path = os.path.join(settings.STORAGE_DIR, name)
        with open(abs_path, "wb") as f:
            f.write(content)
        return name

    def url_for(self, storage_key: str) -> str | None:
        if not storage_key:
            return None
        return f"/storage/{storage_key}"
