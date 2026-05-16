import httpx
from app.config import settings

_ASCII_MAP = str.maketrans({
    '—': '-',   # em dash
    '–': '-',   # en dash
    '€': 'EUR', # €
    '²': '2',   # ²
    '•': '*',   # •
    'é': 'e', 'ë': 'e', 'ê': 'e',  # é ë ê
    'ö': 'o', 'ü': 'u', 'ä': 'a',  # ö ü ä
    'ï': 'i', 'î': 'i',                  # ï î
})


def _h(s: str) -> str:
    """Make a string safe for HTTP headers (ASCII only)."""
    return s.translate(_ASCII_MAP).encode('ascii', errors='ignore').decode()


async def send_ntfy(
    topic: str,
    title: str,
    message: str,
    click_url: str | None = None,
    photo_url: str | None = None,
    priority: str = "default",
) -> None:
    url = f"{settings.ntfy_base_url.rstrip('/')}/{topic}"
    headers: dict[str, str] = {
        "X-Title": _h(title[:250]),
        "X-Priority": priority,
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click_url:
        headers["X-Click"] = _h(click_url)
    if photo_url:
        headers["X-Attach"] = _h(photo_url)
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, content=message.encode(), headers=headers)
        response.raise_for_status()
