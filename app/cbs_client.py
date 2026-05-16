"""PDOK CBS Wijken en Buurten 2025 — OGC API client with SQLite persistence."""

import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import CbsBuurt, CbsWijk

_CBS_BASE = "https://api.pdok.nl/cbs/wijken-en-buurten-2025/ogc/v1/collections"
_BBOX_DELTA = 0.003   # ~300 m — wide enough to catch the containing polygon
_TTL_DAYS = 365       # CBS publishes annually


def _is_stale(fetched_at: datetime) -> bool:
    return datetime.utcnow() - fetched_at > timedelta(days=_TTL_DAYS)


def _bbox_from_feature(feat: dict, fallback_lon: float, fallback_lat: float) -> tuple[float, float, float, float]:
    """Extract [min_lon, min_lat, max_lon, max_lat] from a GeoJSON feature."""
    if "bbox" in feat:
        b = feat["bbox"]
        return b[0], b[1], b[2], b[3]
    geom = feat.get("geometry") or {}
    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])
    try:
        if gtype == "Polygon":
            flat = [p for ring in coords for p in ring]
        elif gtype == "MultiPolygon":
            flat = [p for poly in coords for ring in poly for p in ring]
        else:
            flat = []
        if flat:
            lons = [p[0] for p in flat]
            lats = [p[1] for p in flat]
            return min(lons), min(lats), max(lons), max(lats)
    except Exception:
        pass
    delta = _BBOX_DELTA * 3
    return fallback_lon - delta, fallback_lat - delta, fallback_lon + delta, fallback_lat + delta


async def _fetch_feature(collection: str, lat: float, lon: float) -> dict | None:
    bbox = f"{lon - _BBOX_DELTA},{lat - _BBOX_DELTA},{lon + _BBOX_DELTA},{lat + _BBOX_DELTA}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_CBS_BASE}/{collection}/items",
                params={"bbox": bbox, "limit": 1},
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
            return features[0] if features else None
    except Exception:
        return None


async def get_neighbourhood_stats(lat: float, lon: float) -> dict | None:
    """Return {"buurt": {props}, "wijk": {props}} using DB cache, or None on failure."""
    async with AsyncSessionLocal() as db:
        buurt_row = (await db.execute(
            select(CbsBuurt).where(
                CbsBuurt.bbox_min_lon <= lon,
                CbsBuurt.bbox_max_lon >= lon,
                CbsBuurt.bbox_min_lat <= lat,
                CbsBuurt.bbox_max_lat >= lat,
            )
        )).scalars().first()

        wijk_row = None
        if buurt_row and not _is_stale(buurt_row.fetched_at) and buurt_row.wijkcode:
            wijk_row = (await db.execute(
                select(CbsWijk).where(CbsWijk.wijkcode == buurt_row.wijkcode)
            )).scalars().first()
            if wijk_row and _is_stale(wijk_row.fetched_at):
                wijk_row = None

        if buurt_row and not _is_stale(buurt_row.fetched_at) and wijk_row:
            return {
                "buurt": json.loads(buurt_row.properties_json),
                "wijk": json.loads(wijk_row.properties_json),
            }

    # Cache miss / stale — hit PDOK
    buurt_feat = await _fetch_feature("buurten", lat, lon)
    wijk_feat = await _fetch_feature("wijken", lat, lon)

    if not buurt_feat and not wijk_feat:
        return None

    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        if buurt_feat:
            props = buurt_feat.get("properties", {})
            code = props.get("buurtcode", "")
            if code:
                bb = _bbox_from_feature(buurt_feat, lon, lat)
                row = await db.get(CbsBuurt, code)
                if row:
                    row.properties_json = json.dumps(props)
                    row.fetched_at = now
                else:
                    db.add(CbsBuurt(
                        buurtcode=code,
                        buurtnaam=props.get("buurtnaam", ""),
                        wijkcode=props.get("wijkcode"),
                        gemeentecode=props.get("gemeentecode"),
                        bbox_min_lon=bb[0], bbox_min_lat=bb[1],
                        bbox_max_lon=bb[2], bbox_max_lat=bb[3],
                        properties_json=json.dumps(props),
                        fetched_at=now,
                    ))

        if wijk_feat:
            props_w = wijk_feat.get("properties", {})
            code_w = props_w.get("wijkcode", "")
            if code_w:
                bb_w = _bbox_from_feature(wijk_feat, lon, lat)
                row_w = await db.get(CbsWijk, code_w)
                if row_w:
                    row_w.properties_json = json.dumps(props_w)
                    row_w.fetched_at = now
                else:
                    db.add(CbsWijk(
                        wijkcode=code_w,
                        wijknaam=props_w.get("wijknaam", ""),
                        gemeentecode=props_w.get("gemeentecode"),
                        bbox_min_lon=bb_w[0], bbox_min_lat=bb_w[1],
                        bbox_max_lon=bb_w[2], bbox_max_lat=bb_w[3],
                        properties_json=json.dumps(props_w),
                        fetched_at=now,
                    ))

        await db.commit()

    result: dict = {}
    if buurt_feat:
        result["buurt"] = buurt_feat.get("properties", {})
    if wijk_feat:
        result["wijk"] = wijk_feat.get("properties", {})
    return result or None
