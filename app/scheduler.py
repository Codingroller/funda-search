import json
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import SavedQuery, SeenListing, RunLog, User

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Europe/Amsterdam")


async def run_query_job(query_id: int) -> None:
    async with AsyncSessionLocal() as db:
        query = await db.get(SavedQuery, query_id)
        if not query or not query.enabled:
            return

        params = json.loads(query.params_json)
        started_at = datetime.utcnow()

        try:
            from app.funda_client import search_listings

            listings = await search_listings(params)

            existing = await db.execute(
                select(SeenListing.global_id).where(SeenListing.query_id == query_id)
            )
            seen_ids = {row[0] for row in existing.all()}

            new_listings = [l for l in listings if l["global_id"] not in seen_ids]

            # Download and cache photos locally before storing
            from app.image_cache import cache_photo
            for listing in new_listings:
                if listing.get("photo_url"):
                    cached = await cache_photo(listing["photo_url"])
                    if cached:
                        listing["photo_url"] = cached

            for listing in new_listings:
                db.add(SeenListing(query_id=query_id, global_id=listing["global_id"]))

            user = await db.get(User, query.user_id)

            if user and new_listings:
                from app.notifier import send_ntfy

                for listing in new_listings:
                    title = " - ".join(
                        filter(
                            None,
                            [query.name, listing.get("price"), listing.get("city")],
                        )
                    )
                    parts = [listing.get("title", "")]
                    if listing.get("living_area"):
                        parts.append(f"{listing['living_area']} m²")
                    if listing.get("rooms_count"):
                        parts.append(f"{listing['rooms_count']} rooms")
                    if listing.get("energy_label"):
                        parts.append(f"Energy {listing['energy_label']}")
                    body = " • ".join(p for p in parts if p)

                    try:
                        await send_ntfy(
                            topic=user.ntfy_topic,
                            title=title[:250],
                            message=body,
                            click_url=listing.get("url"),
                            photo_url=listing.get("photo_url"),
                        )
                    except Exception as notify_err:
                        log.warning("ntfy failed for listing %s: %s", listing.get("global_id"), notify_err)

            db.add(
                RunLog(
                    query_id=query_id,
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    status="ok",
                    result_count=len(listings),
                    new_count=len(new_listings),
                    new_listings_json=json.dumps(listings[:30]),
                )
            )

            query.last_run_at = datetime.utcnow()
            query.last_run_status = "ok"
            query.consecutive_errors = 0

            await db.commit()

        except Exception as exc:
            await db.rollback()
            log.exception("Query %d failed: %s", query_id, exc)

            async with AsyncSessionLocal() as db2:
                q2 = await db2.get(SavedQuery, query_id)
                if q2:
                    q2.consecutive_errors = (q2.consecutive_errors or 0) + 1
                    q2.last_run_at = datetime.utcnow()
                    q2.last_run_status = "error"
                    db2.add(
                        RunLog(
                            query_id=query_id,
                            started_at=started_at,
                            finished_at=datetime.utcnow(),
                            status="error",
                            result_count=0,
                            new_count=0,
                            new_listings_json="[]",
                            error_message=str(exc)[:500],
                        )
                    )
                    await db2.commit()


def add_query_job(query_id: int, interval_minutes: int, last_run_at: datetime | None = None) -> None:
    job_id = f"query_{query_id}"
    if scheduler.get_job(job_id):
        scheduler.reschedule_job(job_id, trigger="interval", minutes=interval_minutes)
        return

    # Compute next_run_time so restarts respect the existing schedule.
    # If last_run_at is unknown, APScheduler's default fires one interval from now.
    next_run_time = None
    if last_run_at is not None:
        ideal = last_run_at + timedelta(minutes=interval_minutes)
        now = datetime.utcnow()
        # If the ideal time has passed, run soon (30 s after startup) instead of
        # waiting another full interval.
        next_run_time = ideal if ideal > now else now + timedelta(seconds=30)

    scheduler.add_job(
        run_query_job,
        "interval",
        minutes=interval_minutes,
        id=job_id,
        args=[query_id],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=next_run_time,
    )


def remove_query_job(query_id: int) -> None:
    job_id = f"query_{query_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def reconcile_jobs() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SavedQuery).where(SavedQuery.enabled == True)  # noqa: E712
        )
        for query in result.scalars().all():
            add_query_job(query.id, query.interval_minutes, last_run_at=query.last_run_at)
