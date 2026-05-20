import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

try:
    from funda import Funda, ListingNotFound  # noqa: F401 — imported for testability
except ImportError:
    Funda = None  # type: ignore[assignment,misc]
    ListingNotFound = LookupError  # type: ignore[assignment,misc]

from app.db import AsyncSessionLocal
from app.image_cache import cache_hero_sync, cache_photo_sync
from app.models import ListingCache

_pool = ThreadPoolExecutor(max_workers=4)
_LISTING_TTL = timedelta(hours=24)


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

    return {
        "global_id": str(listing.global_id),
        "url": listing.url,
        "title": listing.title,
        "city": listing.city,
        "price": price,
        "price_per_m2": _fmt_price_per_m2(price_amount, getattr(listing, "living_area", None)),
        "living_area": getattr(listing, "living_area", None),
        "rooms_count": getattr(listing, "rooms_count", None),
        "bedrooms": getattr(listing, "bedrooms", None),
        "energy_label": getattr(listing, "energy_label", None),
        "photo_url": photo_url,
        "publication_date": pub_date if isinstance(pub_date, str) else (pub_date.isoformat() if pub_date else None),
    }


def _sync_search(params: dict) -> list[dict]:
    with Funda(timeout=30, max_retries=5, retry_backoff=0.1) as client:
        return [_listing_to_dict(l) for l in client.iter_search(max_pages=2, **params)]


async def search_listings(params: dict) -> list[dict]:
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(_pool, _sync_search, params),
        timeout=120,
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
        "price_per_m2": _fmt_price_per_m2(price_amount, getattr(listing, "living_area", None)),
        "living_area": getattr(listing, "living_area", None),
        "plot_area": getattr(listing, "plot_area", None),
        "rooms_count": getattr(listing, "rooms_count", None),
        "bedrooms": getattr(listing, "bedrooms", None),
        "energy_label": getattr(listing, "energy_label", None),
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


async def get_listing_detail(global_id: str) -> dict:
    """Fetch full listing detail; check ListingCache first (24 h TTL)."""
    async with AsyncSessionLocal() as db:
        row = await db.get(ListingCache, global_id)
        if row and (datetime.utcnow() - row.fetched_at) < _LISTING_TTL:
            return json.loads(row.payload_json)

    loop = asyncio.get_running_loop()
    payload = await asyncio.wait_for(
        loop.run_in_executor(_pool, _sync_listing_detail, global_id),
        timeout=60,
    )

    async with AsyncSessionLocal() as db:
        now = datetime.utcnow()
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
