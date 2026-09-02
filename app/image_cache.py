import asyncio
import hashlib
import io
import time
from pathlib import Path

from curl_cffi import requests as curl_requests
from PIL import Image

from app.config import settings

IMAGE_TTL = 30 * 24 * 3600  # 30 days in seconds

# cloud.funda.nl serves the newer tiara-media photo paths (returned by pyfunda's
# web-search fallback) only to browser-like clients — plain httpx gets a 403 —
# so downloads go through curl_cffi, already installed as a pyfunda dependency.
# The explicit JPEG Accept stops the CDN negotiating AVIF/WebP, which matters
# because cache_hero_sync writes the bytes out verbatim as a .jpg.
_IMAGE_HEADERS = {"Accept": "image/jpeg,image/*;q=0.8"}


def _fetch_image(photo_url: str, timeout: int) -> bytes:
    resp = curl_requests.get(
        photo_url,
        impersonate="chrome120",
        timeout=timeout,
        headers=_IMAGE_HEADERS,
    )
    resp.raise_for_status()
    return resp.content


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
        img = Image.open(io.BytesIO(_fetch_image(photo_url, 15))).convert("RGB")
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
        local_path.write_bytes(_fetch_image(photo_url, 20))
        return f"/img/{filename}"
    except Exception:
        return None
