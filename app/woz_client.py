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
from app.time_utils import as_utc, now_utc

logger = logging.getLogger(__name__)

_PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
# The WOZ-waardeloket front-end moved its API behind the Kadaster LV-WOZ gateway;
# the old www.wozwaardeloket.nl path now serves the SPA shell (HTML, not JSON).
# Source: https://www.wozwaardeloket.nl/assets/endpoints.json ("wozService").
_WOZ_URL  = "https://api.kadaster.nl/lvwoz/wozwaardeloket-api/v1/wozwaarde/nummeraanduiding/{}"
_WOZ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; funda-search/1.0; +https://funda.rominiek.nl)",
    "Accept": "application/json",
}
_TTL = timedelta(days=180)
_NEGATIVE_TTL = timedelta(hours=24)   # cache failed lookups for 24 h
_MAX_WOZ_CANDIDATES = 6               # sibling nummeraanduidingen to try before giving up

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
            age = now_utc() - as_utc(row.fetched_at)
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
        now = now_utc()
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
    """Perform the two-step PDOK + WOZ lookup. Returns None on any failure.

    A suffixed unit (e.g. "20-H") can 404 at the WOZ gateway even when a sibling
    unit in the same building holds the value, so we resolve *all* units at the
    postcode + house number, try the exact address first, then fall back to the
    siblings until one returns a WOZ value.
    """
    nids = await _resolve_candidate_nids(postcode, huisnummer, toevoeging)
    if not nids:
        logger.debug("PDOK returned no nummeraanduiding for %s %s", postcode, huisnummer)
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        for nid in nids:
            result = await _woz_for_nid(client, nid)
            if result:
                return result
    return None


async def _resolve_candidate_nids(
    postcode: str, huisnummer: int, toevoeging: str | None
) -> list[str]:
    """Resolve postcode + house number to candidate nummeraanduiding ids via PDOK,
    ordered so the exact address (matching toevoeging/huisletter) comes first."""
    query = f"postcode:{postcode} and huisnummer:{huisnummer}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_PDOK_URL, params={
                "q": query,
                "fl": "nummeraanduiding_id,huisnummer,huisletter,huisnummertoevoeging",
                "rows": 15,
            })
            resp.raise_for_status()
            items = resp.json().get("response", {}).get("docs", [])
    except Exception as exc:
        logger.debug("PDOK lookup failed for %s %s: %s", postcode, huisnummer, exc)
        return []

    items = [it for it in items if str(it.get("huisnummer", "")) == str(huisnummer)]
    return _rank_nids(items, toevoeging)


def _rank_nids(items: list[dict], toevoeging: str | None) -> list[str]:
    """Order units so the exact suffix match is first, then siblings; dedup + cap.

    Pure helper — the suffix may live in `huisnummertoevoeging` or `huisletter`
    (Amsterdam "-H" is a huisletter), so we compare against either.
    """
    tgt = str(toevoeging or "").strip().lower()

    def suffix_of(it: dict) -> str:
        return str(it.get("huisnummertoevoeging") or it.get("huisletter") or "").strip().lower()

    exact_idx = [i for i, it in enumerate(items) if tgt and suffix_of(it) == tgt]
    exact_set = set(exact_idx)
    ordered = [items[i] for i in exact_idx] + [
        it for i, it in enumerate(items) if i not in exact_set
    ]

    nids: list[str] = []
    for it in ordered:
        nid = it.get("nummeraanduiding_id")
        if nid and nid not in nids:
            nids.append(nid)
    return nids[:_MAX_WOZ_CANDIDATES]


async def _woz_for_nid(client: httpx.AsyncClient, nid: str) -> dict | None:
    """Fetch + parse WOZ history for a single nummeraanduiding. None if not found.

    The BAG nummeraanduiding id is zero-padded to 16 chars (as the official
    front-end does).
    """
    try:
        resp = await client.get(_WOZ_URL.format(str(nid).zfill(16)), headers=_WOZ_HEADERS)
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

    now = now_utc()
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
