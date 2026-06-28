import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

try:
    from funda import Funda, ListingNotFound  # noqa: F401 — imported for testability
except ImportError:
    Funda = None  # type: ignore[assignment,misc]
    ListingNotFound = LookupError  # type: ignore[assignment,misc]

from app.db import AsyncSessionLocal
from app.image_cache import cache_hero_sync, cache_photo_sync
from app.models import ListingCache
from app.time_utils import as_utc, now_utc

_pool = ThreadPoolExecutor(max_workers=4)
_LISTING_TTL = timedelta(hours=24)

_ENERGY_LABEL_MAP = {"A1": "A+", "A2": "A++", "A3": "A+++"}


def _fmt_energy_label(raw) -> str | None:
    if raw is None:
        return None
    return _ENERGY_LABEL_MAP.get(str(raw), str(raw))


def _fmt_price_per_m2(amount: int | None, area: int | None) -> str | None:
    if not amount or not area:
        return None
    ppm = amount // area
    # Format with period as thousands separator (Dutch convention)
    return f"€ {ppm:,} / m²".replace(",", ".")


def _listing_to_dict(listing) -> dict:
    price = None
    price_amount = None
    if listing.price:
        price_amount = getattr(listing.price, "amount", None)
        price = getattr(listing.price, "formatted", None) or (
            f"€{price_amount:,}" if price_amount else None
        )

    photo_url = None
    media = getattr(listing, "media", None)
    if media:
        urls = getattr(media, "photo_urls", ())
        if urls:
            photo_url = urls[0]
        else:
            # iter_search populates photo_ids but not photo_urls; construct URL from ID.
            # '228898333' -> 'https://cloud.funda.nl/valentina_media/228/898/333_groot.jpg'
            ids = getattr(media, "photo_ids", ())
            if ids:
                pid = str(ids[0])
                path = f"{pid[:-6]}/{pid[-6:-3]}/{pid[-3:]}" if len(pid) >= 9 else pid
                photo_url = f"https://cloud.funda.nl/valentina_media/{path}_groot.jpg"

    # Upgrade any klein/medium CDN variant to groot (~720×540) so cache_photo_sync
    # downscales rather than upscaling, producing a sharper thumbnail.
    if photo_url:
        photo_url = photo_url.replace("_klein.jpg", "_groot.jpg").replace("_medium.jpg", "_groot.jpg")

    pub_date = getattr(listing, "publication_date", None)

    pd = getattr(listing, "property_details", None)
    return {
        "global_id": str(listing.global_id),
        "url": listing.url,
        "title": listing.title,
        "city": listing.city,
        "postcode": getattr(listing, "postcode", None),
        "price": price,
        "price_amount": price_amount,
        "price_per_m2": _fmt_price_per_m2(price_amount, getattr(listing, "living_area", None)),
        "living_area": getattr(listing, "living_area", None),
        "plot_area": getattr(listing, "plot_area", None),
        "rooms_count": getattr(listing, "rooms_count", None),
        "bedrooms": getattr(listing, "bedrooms", None),
        "energy_label": _fmt_energy_label(getattr(listing, "energy_label", None)),
        "object_type": getattr(pd, "object_type", None) if pd else None,
        "photo_url": photo_url,
        "publication_date": pub_date if isinstance(pub_date, str) else (pub_date.isoformat() if pub_date else None),
    }


def _sync_search(params: dict) -> list[dict]:
    with Funda(timeout=30, max_retries=5, retry_backoff=0.1) as client:
        return [_listing_to_dict(l) for l in client.iter_search(**params)]


async def search_listings(params: dict) -> list[dict]:
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(_pool, _sync_search, params),
        timeout=600,
    )


def _listing_detail_to_dict(listing) -> dict:
    price = None
    price_amount = None
    if listing.price:
        price_amount = getattr(listing.price, "amount", None)
        price = getattr(listing.price, "formatted", None) or (
            f"€{price_amount:,}" if price_amount else None
        )

    addr = listing.address
    photos_raw: list[str] = []
    media = getattr(listing, "media", None)
    if media:
        for item in list(getattr(media, "photos", ()))[:20]:
            url = getattr(item, "url", None)
            if url:
                photos_raw.append(url)

    photos: list[str] = []
    for i, url in enumerate(photos_raw[:8]):
        if i == 0:
            cached = cache_hero_sync(url)   # full-quality, max 1200 px wide
        else:
            cached = cache_photo_sync(url)  # 320×240 thumbnail for gallery
        photos.append(cached or url)

    broker = None
    raw_broker = getattr(listing, "broker", None)
    if raw_broker:
        broker = {
            "name": getattr(raw_broker, "name", None),
            "association": getattr(raw_broker, "association", None),
            "relative_url": getattr(raw_broker, "relative_url", None),
        }

    loc = getattr(listing, "location", None)
    lat = getattr(loc, "latitude", None) if loc else None
    lon = getattr(loc, "longitude", None) if loc else None

    pd = getattr(listing, "property_details", None)
    pub_date = getattr(listing, "publication_date", None)

    return {
        "global_id": str(listing.global_id),
        "url": listing.url,
        "title": listing.title,
        "street": getattr(addr, "street_name", None),
        "house_number": getattr(addr, "house_number", None),
        "house_number_suffix": getattr(addr, "house_number_suffix", None),
        "postcode": getattr(addr, "postcode", None),
        "city": getattr(addr, "city", None),
        "municipality": getattr(addr, "municipality", None),
        "neighbourhood": getattr(addr, "neighbourhood", None),
        "neighbourhood_identifier": getattr(addr, "neighbourhood_identifier", None),
        "lat": lat,
        "lon": lon,
        "price": price,
        "price_amount": price_amount,
        "price_per_m2": _fmt_price_per_m2(price_amount, getattr(listing, "living_area", None)),
        "is_auction": bool(getattr(getattr(listing, "price", None), "is_auction", False)),
        "listing_status": getattr(listing, "status", None),
        "labels": [str(lb) for lb in (getattr(listing, "labels", None) or ())],
        "living_area": getattr(listing, "living_area", None),
        "plot_area": getattr(listing, "plot_area", None),
        "rooms_count": getattr(listing, "rooms_count", None),
        "bedrooms": getattr(listing, "bedrooms", None),
        "energy_label": _fmt_energy_label(getattr(listing, "energy_label", None)),
        "object_type": getattr(pd, "object_type", None) if pd else None,
        "house_type": getattr(pd, "house_type", None) if pd else None,
        "construction_year": getattr(pd, "construction_year", None) if pd else None,
        "description_title": getattr(listing, "description_title", None),
        "description": getattr(listing, "description", None),
        "photos": photos,
        "broker": broker,
        "publication_date": pub_date if isinstance(pub_date, str) else (pub_date.isoformat() if pub_date else None),
    }


def _sync_listing_detail(global_id: str) -> dict:
    with Funda(timeout=30, max_retries=3, retry_backoff=0.5) as client:
        listing = client.listing(int(global_id))
    return _listing_detail_to_dict(listing)


async def get_listing_detail(global_id: str, force_refresh: bool = False) -> dict:
    """Fetch full listing detail; check ListingCache first (24 h TTL)."""
    if not force_refresh:
        async with AsyncSessionLocal() as db:
            row = await db.get(ListingCache, global_id)
            if row and (now_utc() - as_utc(row.fetched_at)) < _LISTING_TTL:
                return json.loads(row.payload_json)

    loop = asyncio.get_running_loop()
    payload = await asyncio.wait_for(
        loop.run_in_executor(_pool, _sync_listing_detail, global_id),
        timeout=60,
    )

    async with AsyncSessionLocal() as db:
        now = now_utc()
        row = await db.get(ListingCache, global_id)
        if row:
            row.payload_json = json.dumps(payload)
            row.fetched_at = now
        else:
            db.add(ListingCache(
                global_id=global_id,
                payload_json=json.dumps(payload),
                fetched_at=now,
            ))
        await db.commit()

    return payload


# ---------------------------------------------------------------------------
# Best-effort address → live for-sale listing match (for the House info page)
#
# pyfunda can only search by location, not by exact address, so we search the
# narrowest location we can (full postcode, then PC4, then city) and match on
# postcode + house number. The scan is BOUNDED (never reuse search_listings,
# which materializes a whole city) and gracefully returns None if nothing
# matches or Funda rejects the location.
# ---------------------------------------------------------------------------

_MAX_SCAN = 300            # max listings to scan per location candidate
_FIND_TIMEOUT = 120        # seconds — far below the 600s full-search timeout


def _norm_pc(pc) -> str:
    return str(pc or "").replace(" ", "").upper()


def _norm_suffix(s) -> str:
    return str(s or "").strip().lower().replace("-", "").replace(" ", "")


def _parse_house_number(title: str | None) -> tuple[int | None, str]:
    """Extract (house_number, suffix) from a Funda search title.

    Funda titles are "Streetname huisnummer[suffix]"; the house number is the
    rightmost number-bearing token (handles streets that contain numbers).
    """
    if not title:
        return None, ""
    tokens = title.replace(",", " ").split()
    for i in range(len(tokens) - 1, -1, -1):
        if any(c.isdigit() for c in tokens[i]):
            m = re.match(r"(\d+)(.*)$", tokens[i])
            if not m:
                return None, ""
            trailing = tokens[i + 1] if i + 1 < len(tokens) else ""
            return int(m.group(1)), _norm_suffix(m.group(2) or trailing)
    return None, ""


def _search_match(listing, target: dict) -> bool:
    """Cheap pre-filter on a search-result object (postcode + house number)."""
    if _norm_pc(getattr(listing, "postcode", None)) != target["pc"]:
        return False
    num, _suffix = _parse_house_number(getattr(listing, "title", None))
    return num == target["huisnummer"]


def _detail_confirms(detail: dict, target: dict) -> bool:
    """Authoritative confirmation against structured listing-detail fields."""
    if _norm_pc(detail.get("postcode")) != target["pc"]:
        return False
    try:
        if int(detail.get("house_number")) != target["huisnummer"]:
            return False
    except (TypeError, ValueError):
        return False
    return _norm_suffix(detail.get("house_number_suffix")) == target["suffix"]


def _sync_find_candidates(loc_candidates: list[str], target: dict) -> list[str]:
    """Bounded scan: return global_ids whose postcode + number pre-match."""
    with Funda(timeout=30, max_retries=3, retry_backoff=0.5) as client:
        for loc in loc_candidates:
            matches: list[str] = []
            try:
                scanned = 0
                for listing in client.iter_search(
                    location=[loc], category="buy", sort="newest"
                ):
                    scanned += 1
                    if scanned > _MAX_SCAN:
                        break
                    if _search_match(listing, target):
                        matches.append(str(listing.global_id))
            except Exception:
                continue
            if matches:
                return matches
    return []


async def find_funda_listing(
    postcode: str,
    huisnummer: int,
    suffix: str | None = None,
    street: str | None = None,
    city: str | None = None,
) -> dict | None:
    """Return the full listing-detail dict for a currently for-sale address,
    or None if it isn't listed / can't be confidently matched.
    """
    if not postcode or huisnummer is None:
        return None
    try:
        target = {
            "pc": _norm_pc(postcode),
            "huisnummer": int(huisnummer),
            "suffix": _norm_suffix(suffix),
        }
    except (TypeError, ValueError):
        return None

    # Most precise location first, de-duped, skipping blanks. Funda rejects a
    # full postcode ("1016 GV" → 0 results) but accepts the PC4 ("1016"), which
    # is precise enough once we match on the full postcode + house number; the
    # city is the broad fallback.
    seen: set[str] = set()
    loc_candidates: list[str] = []
    for c in (target["pc"][:4], city):
        c = (c or "").strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            loc_candidates.append(c)
    if not loc_candidates:
        return None

    loop = asyncio.get_running_loop()
    try:
        candidate_ids = await asyncio.wait_for(
            loop.run_in_executor(_pool, _sync_find_candidates, loc_candidates, target),
            timeout=_FIND_TIMEOUT,
        )
    except Exception:
        return None

    for gid in candidate_ids:
        try:
            detail = await get_listing_detail(gid)
        except Exception:
            continue
        if _detail_confirms(detail, target):
            return detail
    return None
