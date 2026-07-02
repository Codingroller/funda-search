"""House info — look up any Dutch address and aggregate everything we know.

Fast path (renders immediately): resolve the address via PDOK Locatieserver,
then fan out to WOZ + CBS neighbourhood + crime/safety (all work for any address).
Slow/uncertain path (loaded lazily over HTMX): a best-effort match to a live Funda
listing + its AI bid estimate, shown only if the address is currently for sale.
"""
import asyncio
import logging
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select

from app.address_lookup import lookup_address, resolve_free_text, suggest_addresses
from app.auth import require_auth
from app.bid_estimator import compute_bid_estimate, get_cached_estimate
from app.bid_explain import condition_view
from app.cbs_client import get_buurtcode_from_coords, get_crime_stats, get_neighbourhood_stats
from app.cbs_view import build_crime_view, build_view
from app.db import AsyncSessionLocal
from app.funda_client import find_funda_listing
from app.models import HouseInfoSearch, User
from app.templates_env import templates
from app.time_utils import now_utc
from app.value_estimator import estimate_value_for_address, get_cached_value_estimate
from app.woz_client import get_woz

logger = logging.getLogger(__name__)
router = APIRouter()

_RECENT_LIMIT = 5     # recent searches shown under the search box
_RECENT_KEEP = 20     # rows retained per user before pruning


async def _record_search(user_id: int, address: dict) -> None:
    """Remember a resolved address as a recent search (best-effort; dedups by
    pdok_id, else by label). Never lets a failure break the result page."""
    label = address.get("label")
    if not label:
        return
    pdok_id = address.get("pdok_id")
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(HouseInfoSearch).where(HouseInfoSearch.user_id == user_id)
            stmt = (stmt.where(HouseInfoSearch.pdok_id == pdok_id) if pdok_id
                    else stmt.where(HouseInfoSearch.label == label))
            existing = (await db.execute(stmt)).scalars().first()
            now = now_utc()
            if existing:
                existing.searched_at = now
                existing.label = label
                existing.query = label
                existing.pdok_id = pdok_id
            else:
                db.add(HouseInfoSearch(user_id=user_id, pdok_id=pdok_id,
                                       label=label, query=label, searched_at=now))
            await db.commit()

            # Prune to keep the table small per user.
            ids = (await db.execute(
                select(HouseInfoSearch.id).where(HouseInfoSearch.user_id == user_id)
                .order_by(HouseInfoSearch.searched_at.desc())
            )).scalars().all()
            if len(ids) > _RECENT_KEEP:
                await db.execute(
                    delete(HouseInfoSearch).where(HouseInfoSearch.id.in_(ids[_RECENT_KEEP:]))
                )
                await db.commit()
    except Exception:
        logger.warning("Failed to record house-info search", exc_info=True)


async def _recent_searches(user_id: int, limit: int = _RECENT_LIMIT) -> list[dict]:
    """Return the user's most recent lookups as [{label, url}] for the search page."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(HouseInfoSearch).where(HouseInfoSearch.user_id == user_id)
            .order_by(HouseInfoSearch.searched_at.desc()).limit(limit)
        )).scalars().all()
    out = []
    for r in rows:
        if r.pdok_id:
            url = f"/house-info/result?pdok_id={quote(r.pdok_id, safe='')}"
        else:
            url = f"/house-info/result?q={quote(r.query or r.label, safe='')}"
        out.append({"label": r.label, "url": url})
    return out


def _addr_key(nid: str, postcode: str, huisnummer: int | None, suffix: str) -> str:
    """The ValueEstimate/WOZ cache key: BAG id when known, else a synthetic address key."""
    if nid:
        return nid
    return (
        f"addr:{(postcode or '').replace(' ', '')}"
        f"-{huisnummer}{(suffix or '').lower()}"
    )


def _value_params(nid, postcode, huisnummer, suffix, street, city, woz_eur):
    """Build the (address dict, query-string) pair the value-card endpoints share."""
    address = {
        "nummeraanduiding_id": nid or None,
        "postcode": postcode or None,
        "huisnummer": huisnummer,
        "suffix": suffix or None,
        "street": street or None,
        "city": city or None,
    }
    qs = urlencode({
        k: v for k, v in {
            "nid": nid, "postcode": postcode, "huisnummer": huisnummer,
            "suffix": suffix, "street": street, "city": city, "woz_eur": woz_eur,
        }.items() if v not in (None, "")
    })
    return address, qs


@router.get("/house-info", response_class=HTMLResponse)
async def house_info_page(request: Request, current_user: User = Depends(require_auth)):
    recent = await _recent_searches(current_user.id)
    return templates.TemplateResponse(
        request, "house_info.html",
        {"current_user": current_user, "recent_searches": recent},
    )


@router.get("/house-info/autocomplete", response_class=HTMLResponse)
async def house_info_autocomplete(
    request: Request, q: str = "", current_user: User = Depends(require_auth)
):
    q = q.strip()
    if len(q) < 2:
        return HTMLResponse("")
    suggestions = await suggest_addresses(q)
    return templates.TemplateResponse(
        request, "partials/address_autocomplete.html", {"suggestions": suggestions},
    )


@router.get("/house-info/result", response_class=HTMLResponse)
async def house_info_result(
    request: Request,
    pdok_id: str = "",
    q: str = "",
    current_user: User = Depends(require_auth),
):
    # Resolve the typed/picked address to a structured record.
    address = None
    if pdok_id:
        address = await lookup_address(pdok_id)
    if address is None and q.strip():
        address = await resolve_free_text(q)

    if address is None:
        return templates.TemplateResponse(
            request, "house_info_result.html",
            {"address": None, "current_user": current_user},
        )

    # Remember this lookup for the "recent searches" list on the search page.
    await _record_search(current_user.id, address)

    # Fast fan-out: WOZ (postcode+huisnummer) + CBS (lat/lon → buurtcode).
    buurtcode = None
    if address.get("lat") is not None and address.get("lon") is not None:
        buurtcode = await get_buurtcode_from_coords(address["lat"], address["lon"])

    woz_key = address.get("nummeraanduiding_id") or (
        f"addr:{(address.get('postcode') or '').replace(' ', '')}"
        f"-{address.get('huisnummer')}{(address.get('suffix') or '').lower()}"
    )

    woz, cbs, crime_stats = await asyncio.gather(
        get_woz(woz_key, address.get("postcode"), address.get("huisnummer"), address.get("suffix")),
        get_neighbourhood_stats(buurtcode) if buurtcode else _none(),
        get_crime_stats(buurtcode) if buurtcode else _none(),
    )

    view = build_view(cbs) if cbs else None
    crime_view = build_crime_view(crime_stats) if crime_stats else None

    return templates.TemplateResponse(
        request, "house_info_result.html",
        {
            "address": address,
            "woz": woz,
            "view": view,
            "crime_view": crime_view,
            "current_user": current_user,
        },
    )


@router.get("/house-info/funda-panel", response_class=HTMLResponse)
async def house_info_funda_panel(
    request: Request,
    postcode: str = "",
    huisnummer: int | None = None,
    suffix: str = "",
    street: str = "",
    city: str = "",
    current_user: User = Depends(require_auth),
):
    listing = None
    if postcode and huisnummer is not None:
        listing = await find_funda_listing(
            postcode=postcode,
            huisnummer=huisnummer,
            suffix=suffix or None,
            street=street or None,
            city=city or None,
        )

    estimate = None
    if listing:
        global_id = listing["global_id"]
        estimate = await get_cached_estimate(global_id)
        if estimate is None:
            # Kick off compute now; the bid card polls (auto_poll) until it lands.
            asyncio.create_task(compute_bid_estimate(global_id))

    return templates.TemplateResponse(
        request, "partials/house_info_funda_panel.html",
        {
            "listing": listing,
            "estimate": estimate,
            "auto_poll": True,
            "is_liked": False,
            "current_user": current_user,
        },
    )


@router.get("/house-info/value-card", response_class=HTMLResponse)
async def house_info_value_card(
    request: Request,
    nid: str = "",
    postcode: str = "",
    huisnummer: int | None = None,
    suffix: str = "",
    street: str = "",
    city: str = "",
    woz_eur: int | None = None,
    current_user: User = Depends(require_auth),
):
    """Self-polling market-value card for a looked-up address (listed or not).

    Kicks off the estimate in the background on first load, then polls itself
    (hx-trigger) until the estimate lands — mirroring the bid-estimate card.
    """
    if huisnummer is None and not nid:
        return HTMLResponse("")

    addr_key = _addr_key(nid, postcode, huisnummer, suffix)
    address, qs = _value_params(nid, postcode, huisnummer, suffix, street, city, woz_eur)
    estimate = await get_cached_value_estimate(addr_key)
    if estimate is None:
        asyncio.create_task(estimate_value_for_address(addr_key, address, woz_eur))

    return templates.TemplateResponse(
        request, "partials/house_info_value_panel.html",
        {"estimate": estimate, "qs": qs, "current_user": current_user},
    )


@router.post("/house-info/value-card/recompute", response_class=HTMLResponse)
async def house_info_value_recompute(
    request: Request,
    nid: str = "",
    postcode: str = "",
    huisnummer: int | None = None,
    suffix: str = "",
    street: str = "",
    city: str = "",
    woz_eur: int | None = None,
    current_user: User = Depends(require_auth),
):
    """Force a fresh market-value estimate for a looked-up address.

    Recomputes synchronously (bypassing the 7-day cache) and returns the
    re-rendered value panel — mirroring the bid card's Recompute button.
    """
    if huisnummer is None and not nid:
        return HTMLResponse("")

    addr_key = _addr_key(nid, postcode, huisnummer, suffix)
    address, qs = _value_params(nid, postcode, huisnummer, suffix, street, city, woz_eur)
    estimate = await estimate_value_for_address(addr_key, address, woz_eur)
    if estimate is None:
        # A compute is already in flight — fall back to whatever's cached.
        estimate = await get_cached_value_estimate(addr_key)

    return templates.TemplateResponse(
        request, "partials/house_info_value_panel.html",
        {"estimate": estimate, "qs": qs, "current_user": current_user},
    )


@router.get("/house-info/value-rationale", response_class=HTMLResponse)
async def house_info_value_rationale(
    request: Request,
    nid: str = "",
    postcode: str = "",
    huisnummer: int | None = None,
    suffix: str = "",
    cond: str = "mid",
    current_user: User = Depends(require_auth),
):
    addr_key = _addr_key(nid, postcode, huisnummer, suffix)
    estimate = await get_cached_value_estimate(addr_key)
    if not estimate:
        return HTMLResponse("<p>No estimate available yet.</p>")
    # bid_estimate_rationale.html is generic over estimate.adjustments; cond
    # reflects the condition zone selected on the gauge.
    return templates.TemplateResponse(
        request, "partials/bid_estimate_rationale.html",
        {"estimate": estimate, "global_id": addr_key, **condition_view(estimate, cond)},
    )


async def _none():
    """Awaitable that resolves to None — lets asyncio.gather stay uniform."""
    return None
