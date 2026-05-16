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
        new_w = max(1, img.width // 4)
        new_h = max(1, img.height // 4)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        img.save(local_path, "JPEG", quality=82, optimize=True)

        return f"/img/{filename}"
    except Exception:
        return None


async def cache_photo(photo_url: str) -> str | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, cache_photo_sync, photo_url)
