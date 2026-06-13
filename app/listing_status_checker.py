import asyncio
import json
import logging

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import LikedListing

try:
    from funda import ListingNotFound
except ImportError:
    ListingNotFound = LookupError  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Human-readable phrasing for push notifications when a status changes.
_STATUS_LABELS = {
    "active": "available",
    "sold": "sold",
    "under_reservation": "sold under reservation",
    "negotiations": "under negotiation",
    "rented": "rented",
    "withdrawn": "withdrawn",
    "removed": "removed from Funda",
}


async def _notify_status_change(
    user_id: int, global_id: str, old_status: str, new_status: str, payload_json: str | None
) -> None:
    """Push a mobile notification to the user who liked this listing."""
    from app.notifier import notify_user

    try:
        payload = json.loads(payload_json) if payload_json else {}
    except (ValueError, TypeError):
        payload = {}

    title = payload.get("title") or "Liked listing"
    city = payload.get("city")
    if city:
        title = f"{title} · {city}"

    old_label = _STATUS_LABELS.get(old_status, old_status)
    new_label = _STATUS_LABELS.get(new_status, new_status)

    try:
        await notify_user(
            user_id,
            title=title,
            body=f"Status changed: now {new_label} (was {old_label})",
            url=f"/listings/{global_id}",
            image=payload.get("photo_url"),
            tag=f"status-{global_id}",
            count=1,
        )
    except Exception:
        logger.warning(
            "status-change push failed for %s (user %s)", global_id, user_id, exc_info=True
        )


def _normalize_status(detail: dict) -> str:
    # pyfunda normalizes to: "available", "negotiations", "sold", "rented", None
    api_status = str(detail.get("listing_status") or "").lower()
    labels = [str(lb).lower() for lb in (detail.get("labels") or [])]

    if api_status == "sold" or any("verkocht" in lb for lb in labels):
        # Distinguish sold-under-reservation via Dutch label text
        if any("voorbehoud" in lb for lb in labels):
            return "under_reservation"
        return "sold"
    if api_status == "negotiations":
        return "negotiations"
    if api_status == "rented":
        return "rented"
    if any("ingetrokken" in lb for lb in labels):
        return "withdrawn"
    return "active"


async def check_liked_listing_statuses() -> None:
    logger.info("liked status check: starting")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(LikedListing.global_id).distinct())
        global_ids = [row[0] for row in result.all()]

    if not global_ids:
        logger.info("liked status check: no liked listings, skipping")
        return

    from app.funda_client import get_listing_detail

    changed = 0
    for global_id in global_ids:
        try:
            detail = await get_listing_detail(global_id, force_refresh=True)
            new_status = _normalize_status(detail)
        except ListingNotFound:
            new_status = "removed"
            logger.info("listing %s not found (404) — marking as removed", global_id)
        except Exception:
            logger.exception("status check failed for %s", global_id)
            await asyncio.sleep(2)
            continue

        pending_notifications: list[tuple[int, str, str | None]] = []
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(LikedListing).where(LikedListing.global_id == global_id)
                )
                for row in result.scalars().all():
                    old = row.listing_status or "active"
                    if new_status != old:
                        row.listing_status = new_status
                        changed += 1
                        logger.info("status changed %s: %s → %s", global_id, old, new_status)
                        pending_notifications.append((row.user_id, old, row.payload_json))
                await db.commit()
        except Exception:
            logger.exception("db update failed for %s", global_id)
            pending_notifications = []   # commit failed — don't notify on un-persisted changes

        # Notify each user who liked this listing (only after a successful commit)
        for user_id, old, payload_json in pending_notifications:
            await _notify_status_change(user_id, global_id, old, new_status, payload_json)

        await asyncio.sleep(2)

    logger.info("liked status check: done — %d/%d listings changed", changed, len(global_ids))
