"""BAG building-attribute lookup for an arbitrary Dutch address.

Supplies the subject property's own floor area (gebruiksoppervlakte), build year
(bouwjaar) and a house/apartment guess — the inputs the bid model needs to value
an address that is NOT currently for sale on Funda (PDOK's Locatieserver and CBS
don't expose these).

Keyless PDOK BAG OGC API Features v2 — no API key, no auth, matching the project's
existing keyless-PDOK style (see woz_client.py / address_lookup.py).
  Source: https://api.pdok.nl/kadaster/bag/ogc/v2  (auth "Geen", cost "Geen")
  NOTE: the older https://api.pdok.nl/lv/bag/ogc/v1 URL is being discontinued by
  PDOK on 2026-07-15 — do not use it.

The API supports no CQL and no attribute filters (only `identificatie`), so this
is a 3-hop chain by id:
  1. adres            → adresseerbaar_object_identificatie (+ type)
  2. verblijfsobject  → oppervlakte, gebruiksdoel, pand.href
  3. pand             → bouwjaar, aantal_verblijfsobjecten

Failures are silent (returns None). Results cached in SQLite (BagInfo, ~1y TTL).
"""
from __future__ import annotations

import logging
from datetime import timedelta

import httpx

from app.db import AsyncSessionLocal
from app.models import BagInfo
from app.time_utils import as_utc, now_utc

logger = logging.getLogger(__name__)

_PDOK_LOCSERVER = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
_BAG_BASE = "https://api.pdok.nl/kadaster/bag/ogc/v2"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; funda-search/1.0; +https://funda.rominiek.nl)",
    "Accept": "application/json",
}
_TTL = timedelta(days=365)           # BAG is near-static
_NEGATIVE_TTL = timedelta(hours=24)  # retry failed lookups after a day


def _row_to_dict(row: BagInfo) -> dict:
    return {
        "living_area": row.living_area,
        "construction_year": row.construction_year,
        "gebruiksdoel": row.gebruiksdoel,
        "is_apartment": row.is_apartment,
        "aantal_verblijfsobjecten": row.aantal_verblijfsobjecten,
        "object_kind": row.object_kind,
    }


async def get_bag_info(
    nummeraanduiding_id: str | None,
    postcode: str | None = None,
    huisnummer: int | None = None,
    suffix: str | None = None,
) -> dict | None:
    """Return BAG attributes for an address, or None on any failure.

    -> {"living_area": int|None, "construction_year": int|None,
        "gebruiksdoel": str|None, "is_apartment": bool|None,
        "aantal_verblijfsobjecten": int|None, "object_kind": str|None}
    """
    nid = (nummeraanduiding_id or "").strip() or None
    if nid is None:
        nid = await _resolve_nid(postcode, huisnummer, suffix)
    if not nid:
        return None

    # DB cache check
    async with AsyncSessionLocal() as db:
        row = await db.get(BagInfo, nid)
        if row:
            age = now_utc() - as_utc(row.fetched_at)
            # A successful fetch (last_error is None) is cached even when it has
            # no floor area (houseboat / standplaats) — that's a real answer.
            if row.last_error is None and age < _TTL:
                return _row_to_dict(row)
            if row.last_error and age < _NEGATIVE_TTL:
                return None

    result = await _fetch_bag(nid)

    async with AsyncSessionLocal() as db:
        row = await db.get(BagInfo, nid)
        now = now_utc()
        if result is not None:
            values = dict(
                postcode=postcode,
                huisnummer=huisnummer,
                huisnummertoevoeging=suffix,
                living_area=result["living_area"],
                construction_year=result["construction_year"],
                gebruiksdoel=result["gebruiksdoel"],
                object_kind=result["object_kind"],
                aantal_verblijfsobjecten=result["aantal_verblijfsobjecten"],
                is_apartment=result["is_apartment"],
                fetched_at=now,
                last_error=None,
            )
        else:
            values = dict(
                postcode=postcode,
                huisnummer=huisnummer,
                huisnummertoevoeging=suffix,
                living_area=None,
                construction_year=None,
                gebruiksdoel=None,
                object_kind=None,
                aantal_verblijfsobjecten=None,
                is_apartment=None,
                fetched_at=now,
                last_error="lookup_failed",
            )
        if row:
            for k, v in values.items():
                setattr(row, k, v)
        else:
            db.add(BagInfo(nummeraanduiding_id=nid, **values))
        await db.commit()

    return result


async def _resolve_nid(postcode: str | None, huisnummer: int | None, suffix: str | None) -> str | None:
    """Resolve postcode+huisnummer to a BAG nummeraanduiding_id via PDOK Locatieserver."""
    if not postcode or huisnummer is None:
        return None
    query = f"postcode:{postcode} and huisnummer:{huisnummer}"
    if suffix:
        query += f" and huisnummertoevoeging:{suffix}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_PDOK_LOCSERVER, params={
                "q": query,
                "fl": "nummeraanduiding_id,huisnummer,huisnummertoevoeging",
                "rows": 5,
            })
            resp.raise_for_status()
            items = resp.json().get("response", {}).get("docs", [])
    except Exception as exc:
        logger.debug("PDOK nid resolve failed for %s %s: %s", postcode, huisnummer, exc)
        return None

    for item in items:
        if str(item.get("huisnummer", "")) == str(huisnummer):
            tv = str(item.get("huisnummertoevoeging") or "")
            if suffix is None or tv.lower() == str(suffix).lower():
                return item.get("nummeraanduiding_id")
    return items[0].get("nummeraanduiding_id") if items else None


async def _fetch_bag(nid: str) -> dict | None:
    """Run the 3-hop BAG OGC v2 lookup. Returns None only when the address /
    verblijfsobject can't be read at all; a successful-but-area-less object
    (houseboat, standplaats) returns a dict with living_area=None."""
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            # Hop 1: adres → adresseerbaar object id + type
            adres = await _first_feature(
                client, f"{_BAG_BASE}/collections/adres/items", {"identificatie": nid}
            )
            if not adres:
                return None
            aprops = adres.get("properties", {})
            obj_kind = aprops.get("adresseerbaar_object_type")
            vbo_id = aprops.get("adresseerbaar_object_identificatie")

            if obj_kind != "Verblijfsobject" or not vbo_id:
                # Ligplaats / Standplaats / unresolved → no floor area to model.
                return {
                    "living_area": None, "construction_year": None,
                    "gebruiksdoel": None, "is_apartment": None,
                    "aantal_verblijfsobjecten": None, "object_kind": obj_kind,
                }

            # Hop 2: verblijfsobject → oppervlakte, gebruiksdoel, pand link
            vbo = await _first_feature(
                client, f"{_BAG_BASE}/collections/verblijfsobject/items",
                {"identificatie": vbo_id},
            )
            if not vbo:
                return None
            vprops = vbo.get("properties", {})
            living_area = _as_int(vprops.get("oppervlakte"))
            gebruiksdoel = vprops.get("gebruiksdoel")
            if isinstance(gebruiksdoel, list):
                gebruiksdoel = ", ".join(str(g) for g in gebruiksdoel) or None

            # Hop 3: pand → bouwjaar + aantal_verblijfsobjecten (apartment heuristic)
            construction_year = None
            aantal = None
            pand_href = _href(vprops.get("pand.href"))
            if pand_href:
                pand = await _get_feature(client, pand_href)
                if pand:
                    pprops = pand.get("properties", {})
                    construction_year = _as_int(pprops.get("bouwjaar"))
                    aantal = _as_int(pprops.get("aantal_verblijfsobjecten"))

            is_apartment = (aantal > 1) if aantal is not None else None
            return {
                "living_area": living_area,
                "construction_year": construction_year,
                "gebruiksdoel": gebruiksdoel,
                "is_apartment": is_apartment,
                "aantal_verblijfsobjecten": aantal,
                "object_kind": obj_kind,
            }
    except Exception as exc:
        logger.debug("BAG fetch failed for %s: %s", nid, exc)
        return None


async def _first_feature(client: httpx.AsyncClient, url: str, params: dict) -> dict | None:
    """GET an OGC /items collection filtered by id; return the first Feature."""
    resp = await client.get(url, params={**params, "f": "json"})
    resp.raise_for_status()
    feats = resp.json().get("features") or []
    return feats[0] if feats else None


async def _get_feature(client: httpx.AsyncClient, url: str) -> dict | None:
    """GET a single-Feature href (hop 3 returns one Feature, not a collection)."""
    resp = await client.get(url, params={"f": "json"})
    resp.raise_for_status()
    data = resp.json()
    if data.get("features"):
        return data["features"][0]
    return data if data.get("properties") else None


def _href(value) -> str | None:
    """The pand link comes back as ['https://…'] (list) or a bare string."""
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, str):
        return value or None
    return None


def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value:
        return int(value)
    return None
