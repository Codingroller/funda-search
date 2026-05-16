import asyncio
import hashlib
import io
from pathlib import Path

import httpx
from PIL import Image

from app.config import settings


def _images_dir() -> Path:
    d = Path(settings.db_path).parent / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_photo_sync(photo_url: str) -> str | None:
    """Download photo_url, resize to 25%, save locally. Returns /img/<hash>.jpg or None on error."""
    url_hash = hashlib.sha256(photo_url.encode()).hexdigest()[:20]
    filename = f"{url_hash}.jpg"
    local_path = _images_dir() / filename

    if local_path.exists():
        return f"/img/{filename}"

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
    """Download the hero photo at full quality (max 1200 px wide, no crop).

    Upgrades _klein CDN URLs to _groot for a larger source image.
    Cached separately from thumbnails using a 'h_' filename prefix.
    """
    hero_url = photo_url.replace("_klein.jpg", "_groot.jpg")
    url_hash = hashlib.sha256(hero_url.encode()).hexdigest()[:20]
    filename = f"h_{url_hash}.jpg"
    local_path = _images_dir() / filename

    if local_path.exists():
        return f"/img/{filename}"

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            resp = client.get(hero_url)
            if resp.status_code != 200 and hero_url != photo_url:
                resp = client.get(photo_url)  # fall back to original URL
            resp.raise_for_status()

        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        max_w = 1200
        if img.width > max_w:
            new_h = int(img.height * max_w / img.width)
            img = img.resize((max_w, new_h), Image.LANCZOS)
        img.save(local_path, "JPEG", quality=85, optimize=True)
        return f"/img/{filename}"
    except Exception:
        return None
