import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from pywebpush import webpush, WebPushException
from sqlalchemy import delete, select, update

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import PushSubscription

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=4)


def _send_one(sub_info: dict, payload: str) -> int | None:
    """Synchronous pywebpush call — run in ThreadPoolExecutor. Returns status code on failure."""
    try:
        webpush(
            subscription_info=sub_info,
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            timeout=10,
        )
        return None
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        log.warning("push to %s... -> %s", sub_info["endpoint"][:60], status)
        return status or 0
    except Exception as exc:
        log.warning("push error: %s", exc)
        return 0


async def notify_user(
    user_id: int,
    title: str,
    body: str,
    url: str | None = None,
    image: str | None = None,
    tag: str | None = None,
    count: int = 1,
) -> None:
    if not settings.vapid_private_key or not settings.vapid_public_key:
        log.warning("VAPID keys not configured — skipping push notification")
        return

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )).scalars().all()

    if not rows:
        return

    payload = json.dumps({
        "title": title[:120],
        "body": body[:240],
        "url": url,
        "image": image,
        "tag": tag,
        "count": count,
    })

    loop = asyncio.get_running_loop()
    dead: list[str] = []
    alive: list[str] = []

    for sub in rows:
        info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        status = await loop.run_in_executor(_pool, _send_one, info, payload)
        if status in (404, 410):
            dead.append(sub.endpoint)
        elif status is None:
            alive.append(sub.endpoint)

    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        if dead:
            await db.execute(
                delete(PushSubscription).where(PushSubscription.endpoint.in_(dead))
            )
        if alive:
            await db.execute(
                update(PushSubscription)
                .where(PushSubscription.endpoint.in_(alive))
                .values(last_used_at=now)
            )
        await db.commit()
