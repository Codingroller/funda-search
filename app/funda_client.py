import asyncio
from concurrent.futures import ThreadPoolExecutor

try:
    from funda import Funda  # noqa: F401 — imported for testability
except ImportError:
    Funda = None  # type: ignore[assignment,misc]

_pool = ThreadPoolExecutor(max_workers=4)


def _listing_to_dict(listing) -> dict:
    price = None
    if listing.price:
        price = getattr(listing.price, "formatted", None) or (
            f"€{listing.price.amount:,}" if getattr(listing.price, "amount", None) else None
        )

    photo_url = None
    media = getattr(listing, "media", None)
    if media:
        urls = getattr(media, "photo_urls", None)
        if urls:
            photo_url = urls[0]

    pub_date = getattr(listing, "publication_date", None)

    return {
        "global_id": str(listing.global_id),
        "url": listing.url,
        "title": listing.title,
        "city": listing.city,
        "price": price,
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


def _sync_autocomplete(value: str) -> list[dict]:
    with Funda(timeout=10) as client:
        suggestions = client.autocomplete(value)
        result = []
        for s in suggestions:
            sid = getattr(s, "id", None)
            label = None
            for attr in ("label", "name", "title", "display_name", "value"):
                label = getattr(s, attr, None)
                if label:
                    break
            label = label or str(sid)
            result.append({"id": str(sid), "label": str(label)})
        return result


async def search_listings(params: dict) -> list[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, _sync_search, params)


async def autocomplete(value: str) -> list[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, _sync_autocomplete, value)
