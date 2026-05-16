import httpx
from app.config import settings


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
        "X-Title": title[:250],
        "X-Priority": priority,
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click_url:
        headers["X-Click"] = click_url
    if photo_url:
        headers["X-Attach"] = photo_url
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, content=message.encode(), headers=headers)
        response.raise_for_status()
