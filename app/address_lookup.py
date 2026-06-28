"""Free-text Dutch address resolution via the PDOK Locatieserver.

Three thin wrappers over the v3_1 Locatieserver:

  * suggest  — autocomplete: returns (id, display name) pairs while the user types
  * lookup   — resolve a chosen suggestion id to a full, structured address
  * free     — best-effort resolve of raw typed text (when no suggestion was picked)

The ``free`` endpoint is the same one ``woz_client`` already uses to map
postcode + huisnummer to a BAG nummeraanduiding_id.

All failures are silent (return ``None`` / ``[]``), matching the convention in
``woz_client`` / ``cbs_client`` — the caller degrades gracefully.

Note: the buurtcode (for CBS stats) is intentionally NOT taken from Locatieserver
here; derive it from lat/lon via ``cbs_client.get_buurtcode_from_coords`` instead,
exactly as the listing-detail page does for listings without a neighbourhood id.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.pdok.nl/bzk/locatieserver/search/v3_1"
_SUGGEST_URL = f"{_BASE}/suggest"
_LOOKUP_URL = f"{_BASE}/lookup"
_FREE_URL = f"{_BASE}/free"

# Fields requested from lookup/free so we can build a full address record.
_FL = (
    "id,weergavenaam,straatnaam,huisnummer,huisletter,huisnummertoevoeging,"
    "postcode,woonplaatsnaam,gemeentenaam,centroide_ll,nummeraanduiding_id"
)


async def suggest_addresses(q: str, rows: int = 8) -> list[dict]:
    """Return up to ``rows`` address suggestions for autocomplete.

    Each item is ``{"id": <pdok id>, "label": <display name>}``. The id is fed
    back into :func:`lookup_address` to resolve the full record.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_SUGGEST_URL, params={
                "q": q,
                "fq": "type:adres",
                "rows": rows,
            })
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
    except Exception as exc:
        logger.debug("PDOK suggest failed for %r: %s", q, exc)
        return []

    out: list[dict] = []
    for d in docs:
        pid = d.get("id")
        label = d.get("weergavenaam")
        if pid and label:
            out.append({"id": pid, "label": label})
    return out


def _parse_centroide_ll(wkt: str | None) -> tuple[float | None, float | None]:
    """Parse PDOK ``centroide_ll`` WKT ``POINT(lon lat)`` to ``(lat, lon)``."""
    if not wkt:
        return None, None
    try:
        inner = wkt[wkt.index("(") + 1 : wkt.index(")")]
        lon_str, lat_str = inner.split()
        return float(lat_str), float(lon_str)
    except Exception:
        return None, None


def _doc_to_address(doc: dict) -> dict:
    """Map a Locatieserver address doc to a normalized address record."""
    huisnummer = doc.get("huisnummer")
    try:
        huisnummer = int(huisnummer) if huisnummer is not None else None
    except (TypeError, ValueError):
        huisnummer = None

    suffix = doc.get("huisnummertoevoeging") or doc.get("huisletter") or None
    lat, lon = _parse_centroide_ll(doc.get("centroide_ll"))

    return {
        "pdok_id": doc.get("id"),
        "label": doc.get("weergavenaam"),
        "street": doc.get("straatnaam"),
        "huisnummer": huisnummer,
        "suffix": suffix,
        "postcode": doc.get("postcode"),
        "city": doc.get("woonplaatsnaam"),
        "municipality": doc.get("gemeentenaam"),
        "nummeraanduiding_id": doc.get("nummeraanduiding_id"),
        "lat": lat,
        "lon": lon,
    }


async def lookup_address(pdok_id: str) -> dict | None:
    """Resolve a chosen suggestion id to a full, structured address."""
    if not pdok_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_LOOKUP_URL, params={"id": pdok_id, "fl": _FL})
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
    except Exception as exc:
        logger.debug("PDOK lookup failed for %r: %s", pdok_id, exc)
        return None
    if not docs:
        return None
    return _doc_to_address(docs[0])


async def resolve_free_text(q: str) -> dict | None:
    """Best-effort resolve raw typed text to the top matching address."""
    q = (q or "").strip()
    if len(q) < 2:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_FREE_URL, params={
                "q": q,
                "fq": "type:adres",
                "fl": _FL,
                "rows": 1,
            })
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
    except Exception as exc:
        logger.debug("PDOK free resolve failed for %r: %s", q, exc)
        return None
    if not docs:
        return None
    return _doc_to_address(docs[0])
