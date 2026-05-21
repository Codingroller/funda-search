"""WOZ-waardeloket lookup for a subject property.

Two-step unauthenticated lookup:
  1. PDOK Locatieserver → resolves postcode + huisnummer to a BAG nummeraanduiding_id
  2. WOZ-waardeloket public proxy → returns WOZ history for that id

Failures are silent (returns None); the bid estimator functions without WOZ.
Results are cached in SQLite (WozValue table, 180-day TTL).

When the WOZ endpoint appears blocked (consecutive failures), a push notification
is sent to admin users once per 24 hours so the issue can be investigated.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import WozValue

logger = logging.getLogger(__name__)

_PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
_WOZ_URL  = "https://www.wozwaardeloket.nl/wozwaardeloket-api/v1/wozwaarde/nummeraanduiding/{}"
_TTL = timedelta(days=180)
_NEGATIVE_TTL = timedelta(hours=24)   # cache failed lookups for 24 h

# Track the last time we sent the "WOZ API blocked" notification (in-process only;
# resets on restart, but that's fine — we'd rather over-notify than not notify).
_last_block_notified: datetime | None = None
_block_notify_interval = timedelta(hours=24)


async def get_woz(
    global_id: str,
    postcode: str,
    huisnummer: int,
    toevoeging: str | None = None,
) -> dict | None:
    """Return {'latest_woz_eur': int, 'latest_peildatum': 'YYYY-MM-DD', 'history': [...]}
    or None on any failure.
    """
    if not postcode or not huisnummer:
        return None

    # DB cache check
    async with AsyncSessionLocal() as db:
        row = await db.get(WozValue, global_id)
        if row:
            age = datetime.utcnow() - row.fetched_at
            # Positive hit cached within TTL
            if row.latest_woz_eur and age < _TTL:
                return {
                    "latest_woz_eur": row.latest_woz_eur,
                    "latest_peildatum": row.latest_peildatum,
                    "history": json.loads(row.history_json),
                }
            # Negative hit (last_error set) cached within negative TTL
            if row.last_error and age < _NEGATIVE_TTL:
                return None

    result = await _fetch_woz(postcode, huisnummer, toevoeging)

    async with AsyncSessionLocal() as db:
        row = await db.get(WozValue, global_id)
        now = datetime.utcnow()
        if result:
            values = dict(
                postcode=postcode,
                huisnummer=huisnummer,
                huisnummertoevoeging=toevoeging,
                latest_woz_eur=result["latest_woz_eur"],
                latest_peildatum=result["latest_peildatum"],
                history_json=json.dumps(result["history"]),
                fetched_at=now,
                last_error=None,
            )
        else:
            values = dict(
                postcode=postcode,
                huisnummer=huisnummer,
                huisnummertoevoeging=toevoeging,
                latest_woz_eur=None,
                latest_peildatum=None,
                history_json="[]",
                fetched_at=now,
                last_error="lookup_failed",
            )
        if row:
            for k, v in values.items():
                setattr(row, k, v)
        else:
            db.add(WozValue(global_id=global_id, **values))
        await db.commit()

    if result is None:
        await _maybe_notify_block()

    return result


async def _fetch_woz(postcode: str, huisnummer: int, toevoeging: str | None) -> dict | None:
    """Perform the two-step PDOK + WOZ lookup. Returns None on any failure."""
    # Step 1: resolve to nummeraanduiding_id via PDOK Locatieserver
    query = f"postcode:{postcode} and huisnummer:{huisnummer}"
    if toevoeging:
        query += f" and huisnummertoevoeging:{toevoeging}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_PDOK_URL, params={
                "q": query,
                "fl": "nummeraanduiding_id,huisnummer,huisnummertoevoeging",
                "rows": 5,
            })
            resp.raise_for_status()
            items = resp.json().get("response", {}).get("docs", [])
    except Exception as exc:
        logger.debug("PDOK lookup failed for %s %s: %s", postcode, huisnummer, exc)
        return None

    if not items:
        logger.debug("PDOK returned no results for %s %s", postcode, huisnummer)
        return None

    # Pick the best matching item
    nid = None
    for item in items:
        hn = str(item.get("huisnummer", ""))
        tv = str(item.get("huisnummertoevoeging") or "")
        if hn == str(huisnummer):
            if toevoeging is None or tv.lower() == str(toevoeging).lower():
                nid = item.get("nummeraanduiding_id")
                break
    if not nid:
        nid = items[0].get("nummeraanduiding_id")  # best-effort fallback

    if not nid:
        return None

    # Step 2: fetch WOZ history from the public proxy
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_WOZ_URL.format(nid))
            if resp.status_code == 403:
                logger.warning("WOZ-waardeloket returned 403 — endpoint may be blocked")
                return None
            if resp.status_code == 404:
                logger.debug("WOZ not found for nummeraanduiding %s", nid)
                return None
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (403, 429):
            logger.warning("WOZ-waardeloket HTTP %d — endpoint may be blocked",
                           exc.response.status_code)
        return None
    except Exception as exc:
        logger.debug("WOZ fetch failed for %s: %s", nid, exc)
        return None

    woz_values = data.get("wozWaarden") or []
    if not woz_values:
        return None

    # Sort descending by peildatum → take the most recent
    try:
        woz_values.sort(key=lambda x: x.get("peildatum", ""), reverse=True)
    except Exception:
        pass

    latest = woz_values[0]
    latest_eur = latest.get("vastgesteldeWaarde")
    latest_date = latest.get("peildatum", "")
    if not latest_eur:
        return None

    history = [
        {"peildatum": v.get("peildatum", ""), "woz_eur": v.get("vastgesteldeWaarde")}
        for v in woz_values
        if v.get("vastgesteldeWaarde")
    ]
    return {
        "latest_woz_eur": int(latest_eur),
        "latest_peildatum": str(latest_date)[:10],
        "history": history,
    }


async def _maybe_notify_block() -> None:
    """Send a push notification to admin users if WOZ seems blocked.

    Rate-limited to once per 24 hours (in-process; resets on restart).
    """
    global _last_block_notified

    now = datetime.utcnow()
    if _last_block_notified and (now - _last_block_notified) < _block_notify_interval:
        return

    # Count recent consecutive failures in the last 2 hours
    two_hours_ago = now - timedelta(hours=2)
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(WozValue).where(
                    WozValue.last_error.isnot(None),
                    WozValue.fetched_at >= two_hours_ago,
                )
            )).scalars().all()
        if len(rows) < 3:
            return  # not enough failures to declare a block
    except Exception:
        return

    _last_block_notified = now

    # Notify all admin users
    try:
        from sqlalchemy import select as sa_select
        from app.models import User
        from app.notifier import notify_user

        async with AsyncSessionLocal() as db:
            admins = (await db.execute(
                sa_select(User).where(User.is_admin.is_(True))
            )).scalars().all()

        for admin in admins:
            await notify_user(
                admin.id,
                title="WOZ lookup appears blocked",
                body=(
                    "The wozwaardeloket.nl API has failed ≥3 times in the last 2 hours. "
                    "Bid estimates will work without WOZ, but parcel-level anchoring is disabled."
                ),
                tag="woz-blocked",
            )
            logger.info("WOZ-blocked notification sent to admin user_id=%d", admin.id)
    except Exception as exc:
        logger.warning("Failed to send WOZ-blocked notification: %s", exc)
