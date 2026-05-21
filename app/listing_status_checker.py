import asyncio
import logging

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import LikedListing

try:
    from funda import ListingNotFound
except ImportError:
    ListingNotFound = LookupError  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


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
                await db.commit()
        except Exception:
            logger.exception("db update failed for %s", global_id)

        await asyncio.sleep(2)

    logger.info("liked status check: done — %d/%d listings changed", changed, len(global_ids))
