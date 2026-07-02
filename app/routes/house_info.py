"""House info — look up any Dutch address and aggregate everything we know.

Fast path (renders immediately): resolve the address via PDOK Locatieserver,
then fan out to WOZ + CBS neighbourhood + crime/safety (all work for any address).
Slow/uncertain path (loaded lazily over HTMX): a best-effort match to a live Funda
listing + its AI bid estimate, shown only if the address is currently for sale.
"""
import asyncio
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.address_lookup import lookup_address, resolve_free_text, suggest_addresses
from app.auth import require_auth
from app.bid_estimator import compute_bid_estimate, get_cached_estimate
from app.cbs_client import get_buurtcode_from_coords, get_crime_stats, get_neighbourhood_stats
from app.cbs_view import build_crime_view, build_view
from app.funda_client import find_funda_listing
from app.models import User
from app.templates_env import templates
from app.value_estimator import estimate_value_for_address, get_cached_value_estimate
from app.woz_client import get_woz

router = APIRouter()


def _addr_key(nid: str, postcode: str, huisnummer: int | None, suffix: str) -> str:
    """The ValueEstimate/WOZ cache key: BAG id when known, else a synthetic address key."""
    if nid:
        return nid
    return (
        f"addr:{(postcode or '').replace(' ', '')}"
        f"-{huisnummer}{(suffix or '').lower()}"
    )


@router.get("/house-info", response_class=HTMLResponse)
async def house_info_page(request: Request, current_user: User = Depends(require_auth)):
    return templates.TemplateResponse(
        request, "house_info.html", {"current_user": current_user},
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
    estimate = await get_cached_value_estimate(addr_key)
    if estimate is None:
        address = {
            "nummeraanduiding_id": nid or None,
            "postcode": postcode or None,
            "huisnummer": huisnummer,
            "suffix": suffix or None,
            "street": street or None,
            "city": city or None,
        }
        asyncio.create_task(estimate_value_for_address(addr_key, address, woz_eur))

    qs = urlencode({
        k: v for k, v in {
            "nid": nid, "postcode": postcode, "huisnummer": huisnummer,
            "suffix": suffix, "street": street, "city": city, "woz_eur": woz_eur,
        }.items() if v not in (None, "")
    })
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
    current_user: User = Depends(require_auth),
):
    addr_key = _addr_key(nid, postcode, huisnummer, suffix)
    estimate = await get_cached_value_estimate(addr_key)
    if not estimate:
        return HTMLResponse("<p>No estimate available yet.</p>")
    # bid_estimate_rationale.html is generic over estimate.adjustments.
    return templates.TemplateResponse(
        request, "partials/bid_estimate_rationale.html",
        {"estimate": estimate, "global_id": addr_key},
    )


async def _none():
    """Awaitable that resolves to None — lets asyncio.gather stay uniform."""
    return None
