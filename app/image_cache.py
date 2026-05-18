import asyncio
import hashlib
import io
import time
from pathlib import Path

import httpx
from PIL import Image

from app.config import settings

IMAGE_TTL = 30 * 24 * 3600  # 30 days in seconds


def _images_dir() -> Path:
    d = Path(settings.db_path).parent / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_photo_sync(photo_url: str) -> str | None:
    """Download photo_url, resize to 320×240, save locally. Returns /img/<hash>.jpg or None on error."""
    url_hash = hashlib.sha256(photo_url.encode()).hexdigest()[:20]
    filename = f"{url_hash}.jpg"
    local_path = _images_dir() / filename

    if local_path.exists():
        if time.time() - local_path.stat().st_mtime < IMAGE_TTL:
            return f"/img/{filename}"
        local_path.unlink()  # expired — fall through to re-download

    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(photo_url)
            resp.raise_for_status()

        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        # Crop to 4:3 centre, then resize to fit the card's photo panel (320×240 @2x)
        target_ratio = 4 / 3
        src_ratio = img.width / img.height
        if src_ratio > target_ratio:
            new_w = int(img.height * target_ratio)
            left = (img.width - new_w) // 2
            img = img.crop((left, 0, left + new_w, img.height))
        elif src_ratio < target_ratio:
            new_h = int(img.width / target_ratio)
            top = (img.height - new_h) // 2
            img = img.crop((0, top, img.width, top + new_h))
        img = img.resize((320, 240), Image.LANCZOS)
        img.save(local_path, "JPEG", quality=82, optimize=True)

        return f"/img/{filename}"
    except Exception:
        return None


async def cache_photo(photo_url: str) -> str | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, cache_photo_sync, photo_url)


def cache_hero_sync(photo_url: str) -> str | None:
    """Download the hero photo and cache the raw bytes without reprocessing.

    pyfunda's client.listing() returns bare CDN URLs (no size suffix) which
    are already full-resolution. Writing raw bytes preserves that quality exactly.
    """
    url_hash = hashlib.sha256(photo_url.encode()).hexdigest()[:20]
    filename = f"h_{url_hash}.jpg"
    local_path = _images_dir() / filename

    if local_path.exists():
        if time.time() - local_path.stat().st_mtime < IMAGE_TTL:
            return f"/img/{filename}"
        local_path.unlink()  # expired — fall through to re-download

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            resp = client.get(photo_url)
            resp.raise_for_status()

        local_path.write_bytes(resp.content)
        return f"/img/{filename}"
    except Exception:
        return None
