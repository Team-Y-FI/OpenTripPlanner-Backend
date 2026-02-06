import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select, func, or_

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.upload import Photo

from pillow_heif import register_heif_opener
from PIL import Image


def _is_heic_path(path: str) -> bool:
    lower = path.lower()
    return lower.endswith(".heic") or lower.endswith(".heif")


def _to_jpeg_name(storage_path: str) -> str:
    return str(Path(storage_path).with_suffix(".jpg").name)


def _normalize_filename(filename: str | None) -> str | None:
    if not filename:
        return filename
    lower = filename.lower()
    if lower.endswith(".heic") or lower.endswith(".heif"):
        return str(Path(filename).with_suffix(".jpg").name)
    if "." not in filename:
        return f"{filename}.jpg"
    return filename


async def convert(dry_run: bool) -> int:
    register_heif_opener()

    storage_dir = Path(settings.STORAGE_DIR).resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as session:
        stmt = select(Photo).where(
            or_(
                func.lower(Photo.storage_path).like("%.heic"),
                func.lower(Photo.storage_path).like("%.heif"),
            )
        )
        res = await session.execute(stmt)
        photos = list(res.scalars().all())

        if not photos:
            print("No HEIC/HEIF photos found.")
            return 0

        converted = 0
        for photo in photos:
            src = storage_dir / photo.storage_path
            if not src.exists():
                print(f"[SKIP] missing file: {src}")
                continue

            dst_name = _to_jpeg_name(photo.storage_path)
            dst = storage_dir / dst_name
            if dst.exists():
                print(f"[SKIP] jpeg exists: {dst}")
            else:
                try:
                    with Image.open(src) as img:
                        rgb = img.convert("RGB")
                        rgb.save(dst, "JPEG", quality=90)
                    print(f"[OK] {src.name} -> {dst.name}")
                except Exception as exc:
                    print(f"[FAIL] {src.name}: {exc}")
                    continue

            if not dry_run:
                photo.storage_path = dst_name
                photo.file_name = _normalize_filename(photo.file_name)
                converted += 1

        if not dry_run and converted:
            await session.commit()

        print(f"Converted: {converted} photo(s).")
        return converted


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert HEIC/HEIF photos in storage to JPEG and update DB.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report only, no writes.")
    args = parser.parse_args()

    asyncio.run(convert(args.dry_run))


if __name__ == "__main__":
    main()
