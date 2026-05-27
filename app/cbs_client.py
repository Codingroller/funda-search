"""CBS StatLine OData v4 — Kerncijfers wijken en buurten 2025 (dataset 86165NED).

Primary lookup: buurtcode from pyfunda address.neighbourhood_identifier.
Fallback lookup: lat/lon → PDOK OGC bbox → buurtcode → CBS OData.
Wijk code is derived from the buurt code: 'WK' + buurtcode[2:8].
Results are persisted to SQLite (cbs_buurt / cbs_wijk) with a 365-day TTL.
"""

import asyncio
import json
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import CbsBuurt, CbsGemeente, CbsWijk
from app.time_utils import as_utc, now_utc

_ODATA_BASE = "https://datasets.cbs.nl/odata/v1/CBS/86165NED"
_CRIME_BASE = "https://datasets.cbs.nl/odata/v1/CBS/83648NED"
_SAFETY_BASE = "https://datasets.cbs.nl/odata/v1/CBS/85146NED"
_TTL_DAYS = 365
_TTL_CRIME_DAYS = 180


def _is_stale(fetched_at) -> bool:
    return now_utc() - as_utc(fetched_at) > timedelta(days=_TTL_DAYS)


# ---------------------------------------------------------------------------
# Measure → structured output mapping
# 70 measures have data in the current 2025 dataset release.
# ---------------------------------------------------------------------------

def _v(obs: dict, code: str):
    """Return value for a measure code, or None if missing/suppressed."""
    return obs.get(code)


def _structure(obs: dict, code: str, name: str, gemeente: str) -> dict:
    """Map flat {measure_code: value} to a themed structure for the template."""
    return {
        "code": code,
        "name": name,
        "gemeente": gemeente,
        # -- Population --
        "population": {
            "total":       _v(obs, "T001036"),
            "men":         _v(obs, "3000"),
            "women":       _v(obs, "4000"),
            "age_0_15":    _v(obs, "10680"),
            "age_15_25":   _v(obs, "53050"),
            "age_25_45":   _v(obs, "53310"),
            "age_45_65":   _v(obs, "53715"),
            "age_65plus":  _v(obs, "80200"),
            # births / deaths — available in some quarterly releases
            "births_total":   _v(obs, "M000173_1"),
            "births_rate":    _v(obs, "M000173_2"),
            "deaths_total":   _v(obs, "M000179_1"),
            "deaths_rate":    _v(obs, "M000179_2"),
        },
        # -- Marital status --
        "marital": {
            "unmarried": _v(obs, "1010"),
            "married":   _v(obs, "1020"),
            "divorced":  _v(obs, "1080"),
            "widowed":   _v(obs, "1050"),
        },
        # -- Heritage (CBS 2022 classification) --
        # _1 = all residents by background; _2 = born in NL by background;
        # _3 = born abroad by background.
        "heritage": {
            "total_nl":                   _v(obs, "1012600_1"),
            "total_europe":               _v(obs, "H007933_1"),
            "total_outside_europe":       _v(obs, "H008859_1"),
            "born_nl_heritage_nl":        _v(obs, "1012600_2"),
            "born_nl_heritage_europe":    _v(obs, "H007933_2"),
            "born_nl_heritage_outside":   _v(obs, "H008859_2"),
            "born_abroad_heritage_europe":  _v(obs, "H007933_3"),
            "born_abroad_heritage_outside": _v(obs, "H008859_3"),
        },
        # -- Households --
        "households": {
            "total":           _v(obs, "1050010_2"),
            "single_person":   _v(obs, "1050015"),
            "without_children":_v(obs, "1016040"),
            "with_children":   _v(obs, "1016030"),
            "avg_size":        _v(obs, "M000114"),
            "density_per_km2": _v(obs, "M000100"),
        },
        # -- Housing stock --
        "housing": {
            "total_stock":       _v(obs, "M000297"),
            "non_residential":   _v(obs, "M008258"),
            "vacant":            _v(obs, "M008208"),
            "woz_value_k":       _v(obs, "M001642"),  # avg WOZ ×1000 €
            "pct_owner":         _v(obs, "1014800"),
            "pct_rental":        _v(obs, "1014850_2"),
            "pct_rental_corp":   _v(obs, "A047047"),
            "pct_rental_other":  _v(obs, "A047048"),
            "pct_built_over_10y":_v(obs, "M008209"),
            "pct_built_last_10y":_v(obs, "M008210"),
            "pct_gas_free":      _v(obs, "M008295"),
            "pct_gas_heated":    _v(obs, "M008296"),
            # new builds — available in some releases
            "new_builds":        _v(obs, "M003003"),
            "new_non_residential": _v(obs, "M008211"),
        },
        # -- Housing type (percentages) --
        "housing_type": {
            "pct_single_family": _v(obs, "ZW10290"),
            "pct_terraced":      _v(obs, "ZW25805"),
            "pct_corner":        _v(obs, "ZW25806"),
            "pct_semi_detached": _v(obs, "ZW10300"),
            "pct_detached":      _v(obs, "ZW10320"),
            "pct_apartment":     _v(obs, "ZW10340"),
        },
        # -- Energy (quarterly additions — may be None) --
        "energy": {
            "avg_electricity_kwh":        _v(obs, "M000221_2"),
            "avg_electricity_return_kwh": _v(obs, "M008294"),
            "avg_gas_m3":                 _v(obs, "M000219_2"),
            "pct_district_heating":       _v(obs, "M000369"),
            "pct_solar_panels":           _v(obs, "M008297"),
            "pct_electric_heating":       _v(obs, "M008298"),
            "ev_charge_points":           _v(obs, "M008299"),
        },
        # -- Education (quarterly additions — may be None) --
        "education": {
            "primary_pupils":    _v(obs, "A025301"),
            "secondary_pupils":  _v(obs, "T001345"),
            "mbo_students":      _v(obs, "A041867"),
            "hbo_students":      _v(obs, "A025294"),
            "wo_students":       _v(obs, "A025297"),
            "pct_low":           _v(obs, "2018700"),
            "pct_mid":           _v(obs, "2018740"),
            "pct_high":          _v(obs, "2018790"),
        },
        # -- Labour (quarterly additions — may be None) --
        "labour": {
            "working_population":   _v(obs, "M008300"),
            "net_participation_pct":_v(obs, "M001796_2"),
            "pct_employees":        _v(obs, "2021320"),
            "pct_permanent":        _v(obs, "2021330"),
            "pct_flex":             _v(obs, "2021340"),
            "pct_self_employed":    _v(obs, "2021380"),
        },
        # -- Income (added Jan 2026, full 2025 data end 2026) --
        "income": {
            "recipients":          _v(obs, "M000232"),
            "avg_per_recipient_k": _v(obs, "M000223"),  # ×1000 €
            "avg_per_resident_k":  _v(obs, "M000224"),  # ×1000 €
            "pct_lowest_40":       _v(obs, "D000187"),
            "pct_highest_20":      _v(obs, "D000185"),
            "pct_poverty":         _v(obs, "M008349"),
            "pct_near_poverty":    _v(obs, "M008348"),
            "avg_standardized_k":  _v(obs, "M000222"),  # ×1000 €
            "pct_hh_lowest_40":    _v(obs, "D000186"),
            "pct_hh_highest_20":   _v(obs, "D000184"),
            "median_wealth_k":     _v(obs, "M000939"),  # ×1000 €
        },
        # -- Social benefits (quarterly additions — may be None) --
        "benefits": {
            "pct_welfare":       _v(obs, "D006842"),
            "pct_disability":    _v(obs, "D006837"),
            "pct_unemployment":  _v(obs, "D001827"),
            "pct_pension":       _v(obs, "D000193"),
        },
        # -- Care (quarterly additions — may be None) --
        "care": {
            "youth_care_total":  _v(obs, "T001203"),
            "pct_youth_care":    _v(obs, "A045561"),
            "wmo_total":         _v(obs, "M001342_1"),
            "pct_wmo":           _v(obs, "M001342_2"),
        },
        # -- Businesses --
        "businesses": {
            "total":            _v(obs, "M000200_2"),
            "agriculture":      _v(obs, "301000"),
            "industry":         _v(obs, "300003"),
            "retail_hosp":      _v(obs, "300005"),
            "transport_ict":    _v(obs, "383105"),
            "finance_re":       _v(obs, "300009"),
            "business_svc":     _v(obs, "300010"),
            "gov_edu_health":   _v(obs, "300012"),
            "culture_other":    _v(obs, "300014"),
        },
        # -- Mobility --
        "mobility": {
            "cars_total":        _v(obs, "A018943_2"),
            "cars_petrol":       _v(obs, "A019276"),
            "cars_other_fuel":   _v(obs, "D001045"),
            "cars_per_household":_v(obs, "M000368"),
            "cars_per_km2":      _v(obs, "A018943_4"),
            "motorcycles":       _v(obs, "A018944"),
        },
        # -- Proximity (km, weighted average distance) --
        "proximity": {
            "gp_km":         _v(obs, "D000028"),
            "supermarket_km":_v(obs, "D000025"),
            "childcare_km":  _v(obs, "D000029"),
            "school_km":     _v(obs, "D000045"),
            "schools_3km":   _v(obs, "D000263"),
        },
        # -- Area & urbanisation --
        "area": {
            "total_ha":       _v(obs, "T001455_2"),
            "land_ha":        _v(obs, "A047044"),
            "water_ha":       _v(obs, "A047040"),
            "postcode":       _v(obs, "PC000C"),
            "urbanisation":   _v(obs, "ST0001"),   # 1 (very urban) – 5 (rural)
            "address_density":_v(obs, "ST0003"),   # addresses / km²
            "coverage_pct":   _v(obs, "M000217"),
        },
    }


# ---------------------------------------------------------------------------
# CBS OData fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_observations(region_code: str) -> dict:
    """Return {measure_code: value} for one region. Empty dict on error."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_ODATA_BASE}/Observations",
                params={"$filter": f"WijkenEnBuurten eq '{region_code}'"},
            )
            resp.raise_for_status()
            obs: dict = {}
            for r in resp.json().get("value", []):
                sv = r.get("StringValue") or ""
                obs[r["Measure"]] = r["Value"] if r["Value"] is not None else (sv.strip() or None)
            return obs
    except Exception:
        return {}


async def _fetch_region_name(code: str) -> str:
    """Return the display name for a buurt or wijk code."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_ODATA_BASE}/WijkenEnBuurtenCodes",
                params={"$filter": f"Identifier eq '{code}'", "$select": "Title"},
            )
            resp.raise_for_status()
            items = resp.json().get("value", [])
            return items[0]["Title"] if items else code
    except Exception:
        return code


_PDOK_OGC = "https://api.pdok.nl/cbs/wijken-en-buurten-2025/ogc/v1/collections/buurten/items"
_BBOX_DELTA = 0.003


async def get_buurtcode_from_coords(lat: float, lon: float) -> str | None:
    """Resolve lat/lon to a CBS buurtcode via PDOK OGC bbox query."""
    bbox = f"{lon - _BBOX_DELTA},{lat - _BBOX_DELTA},{lon + _BBOX_DELTA},{lat + _BBOX_DELTA}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_PDOK_OGC, params={"bbox": bbox, "limit": 1})
            resp.raise_for_status()
            features = resp.json().get("features", [])
            if features:
                return features[0].get("properties", {}).get("buurtcode")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_neighbourhood_stats(neighbourhood_identifier: str) -> dict | None:
    """Return structured CBS stats for buurt + its parent wijk, or None on failure.

    neighbourhood_identifier is the CBS buurtcode from pyfunda's
    address.neighbourhood_identifier (e.g. 'BU03920301').
    Wijk code is derived as 'WK' + buurtcode[2:8].
    """
    if not neighbourhood_identifier or not neighbourhood_identifier.upper().startswith("BU"):
        return None

    buurtcode = neighbourhood_identifier.upper()
    wijkcode = "WK" + buurtcode[2:8]

    # -- DB cache check --
    async with AsyncSessionLocal() as db:
        buurt_row = await db.get(CbsBuurt, buurtcode)
        wijk_row = await db.get(CbsWijk, wijkcode)

        b_fresh = buurt_row and not _is_stale(buurt_row.fetched_at)
        w_fresh = wijk_row and not _is_stale(wijk_row.fetched_at)

        if b_fresh and w_fresh:
            return {
                "buurt": _structure(
                    json.loads(buurt_row.properties_json),
                    buurtcode, buurt_row.buurtnaam, buurt_row.gemeentecode or "",
                ),
                "wijk": _structure(
                    json.loads(wijk_row.properties_json),
                    wijkcode, wijk_row.wijknaam, wijk_row.gemeentecode or "",
                ),
            }

    # -- Fetch from CBS OData in parallel --
    buurt_obs, wijk_obs, buurt_name, wijk_name = await asyncio.gather(
        _fetch_observations(buurtcode),
        _fetch_observations(wijkcode),
        _fetch_region_name(buurtcode),
        _fetch_region_name(wijkcode),
    )

    if not buurt_obs and not wijk_obs:
        return None

    gemeente = (buurt_obs or wijk_obs).get("GM000C", "")

    now = now_utc()
    async with AsyncSessionLocal() as db:
        if buurt_obs:
            row = await db.get(CbsBuurt, buurtcode)
            payload = json.dumps(buurt_obs)
            if row:
                row.properties_json = payload
                row.buurtnaam = buurt_name
                row.fetched_at = now
            else:
                db.add(CbsBuurt(
                    buurtcode=buurtcode,
                    buurtnaam=buurt_name,
                    wijkcode=wijkcode,
                    gemeentecode=gemeente,
                    bbox_min_lon=0.0, bbox_min_lat=0.0,
                    bbox_max_lon=0.0, bbox_max_lat=0.0,
                    properties_json=payload,
                    fetched_at=now,
                ))

        if wijk_obs:
            row_w = await db.get(CbsWijk, wijkcode)
            payload_w = json.dumps(wijk_obs)
            if row_w:
                row_w.properties_json = payload_w
                row_w.wijknaam = wijk_name
                row_w.fetched_at = now
            else:
                db.add(CbsWijk(
                    wijkcode=wijkcode,
                    wijknaam=wijk_name,
                    gemeentecode=gemeente,
                    bbox_min_lon=0.0, bbox_min_lat=0.0,
                    bbox_max_lon=0.0, bbox_max_lat=0.0,
                    properties_json=payload_w,
                    fetched_at=now,
                ))

        await db.commit()

    return {
        "buurt": _structure(buurt_obs, buurtcode, buurt_name, gemeente),
        "wijk":  _structure(wijk_obs,  wijkcode,  wijk_name,  gemeente),
    }


# ---------------------------------------------------------------------------
# Crime & safety stats (gemeente level)
# 83648NED — Geregistreerde criminaliteit, per gemeente, annual
# 85146NED — Veiligheidsmonitor, per gemeente, biennial (55 largest only)
# ---------------------------------------------------------------------------

async def _fetch_crime_obs(gemeente_code: str) -> tuple[str | None, dict]:
    """Return (year, {(SoortMisdrijf, Measure): value}) for the latest annual period."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{_CRIME_BASE}/Observations",
                params={"$filter": f"RegioS eq '{gemeente_code}'", "$top": "2000"},
            )
            resp.raise_for_status()
            rows = resp.json().get("value", [])
        if not rows:
            return None, {}
        annual = sorted(
            {r["Perioden"] for r in rows if r.get("Perioden", "").endswith("JJ00")},
            reverse=True,
        )
        if not annual:
            return None, {}
        latest = annual[0]
        obs = {
            (r["SoortMisdrijf"], r["Measure"]): r.get("Value")
            for r in rows if r.get("Perioden") == latest
        }
        return latest[:4], obs
    except Exception:
        return None, {}


async def _fetch_safety_obs(gemeente_code: str) -> tuple[str | None, dict]:
    """Return (year, {Measure: value}) for the latest period in 85146NED."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_SAFETY_BASE}/Observations",
                params={"$filter": f"RegioS eq '{gemeente_code}'", "$top": "500"},
            )
            resp.raise_for_status()
            rows = resp.json().get("value", [])
        if not rows:
            return None, {}
        latest = sorted({r["Perioden"] for r in rows}, reverse=True)[0]
        obs = {r["Measure"]: r.get("Value") for r in rows if r.get("Perioden") == latest}
        return latest[:4], obs
    except Exception:
        return None, {}


async def get_crime_stats(buurtcode: str) -> dict | None:
    """Return structured crime/safety stats for the gemeente containing buurtcode.

    buurtcode format: BU03920301 → gemeente GM0392 (chars 2–5).
    Returns None if no data is available.
    """
    if not buurtcode or not buurtcode.upper().startswith("BU") or len(buurtcode) < 6:
        return None

    gemeente_code = "GM" + buurtcode.upper()[2:6]
    ttl = timedelta(days=_TTL_CRIME_DAYS)

    async with AsyncSessionLocal() as db:
        row = await db.get(CbsGemeente, gemeente_code)
        if row and (now_utc() - as_utc(row.fetched_at)) < ttl:
            return {
                "gemeente": gemeente_code,
                "gemeentenaam": row.gemeentenaam,
                "crime": json.loads(row.crime_json),
                "safety": json.loads(row.safety_json),
            }

    (crime_year, crime_obs), (safety_year, safety_obs), gemeente_name = await asyncio.gather(
        _fetch_crime_obs(gemeente_code),
        _fetch_safety_obs(gemeente_code),
        _fetch_region_name(gemeente_code),
    )

    if not crime_obs and not safety_obs:
        return None

    def _v(obs, *keys):
        for k in keys:
            v = obs.get(k)
            if v is not None:
                return v
        return None

    crime = {
        "year":            crime_year,
        "total_per_1k":    _v(crime_obs, ("T001161", "M004200_4")),
        "theft_per_1k":    _v(crime_obs, ("CRI1100", "M004200_4")),
        "vandalism_per_1k":_v(crime_obs, ("CRI2100", "M004200_4")),
        "disorder_per_1k": _v(crime_obs, ("CRI2200", "M004200_4")),
    }
    safety = {
        "year":                safety_year,
        "score":               _v(safety_obs, "M005017"),
        "pct_feel_unsafe":     _v(safety_obs, "A047555"),
        "pct_much_crime":      _v(safety_obs, "A047556"),
        "pct_burglary_victim": _v(safety_obs, "D003829_7"),
        "pct_property_victim": _v(safety_obs, "D003829_6"),
        "pct_vandalism_victim":_v(safety_obs, "D003829_15"),
        "pct_assault_victim":  _v(safety_obs, "D003829_4"),
    }

    now = now_utc()
    async with AsyncSessionLocal() as db:
        row = await db.get(CbsGemeente, gemeente_code)
        crime_str, safety_str = json.dumps(crime), json.dumps(safety)
        if row:
            row.gemeentenaam = gemeente_name
            row.crime_json = crime_str
            row.safety_json = safety_str
            row.fetched_at = now
        else:
            db.add(CbsGemeente(
                gemeentecode=gemeente_code,
                gemeentenaam=gemeente_name,
                crime_json=crime_str,
                safety_json=safety_str,
                fetched_at=now,
            ))
        await db.commit()

    return {
        "gemeente": gemeente_code,
        "gemeentenaam": gemeente_name,
        "crime": crime,
        "safety": safety,
    }
