import asyncio
import json
import logging
import statistics
from datetime import date, datetime, timedelta

from app.cbs_client import get_neighbourhood_stats
from app.db import AsyncSessionLocal
from app.funda_client import get_listing_detail, search_listings
from app.models import BidEstimate

logger = logging.getLogger(__name__)

_BID_TTL = timedelta(days=7)
_computing: set[str] = set()  # global_ids currently being computed
_NATIONAL_AVG_WOZ_K = 320  # thousands €, approximate 2025 national average

_ENERGY_ORDER = ["A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"]
_ENERGY_DELTA_ABS = {
    "A+++": 3, "A++": 3, "A+": 3, "A": 3,
    "B": 1, "C": 0, "D": -2,
    "E": -5, "F": -5, "G": -5,
}


def _energy_rank(label: str | None) -> int:
    if not label:
        return 5
    try:
        return _ENERGY_ORDER.index(label)
    except ValueError:
        return 5


def _fmt_eur(amount: int | None) -> str:
    if not amount:
        return "—"
    return f"€ {amount:,}".replace(",", ".")


def _comparables_params(subject: dict) -> dict:
    params: dict = {
        "location": [subject["city"]],
        "category": "buy",
        "sort": "newest",
        "max_pages": 2,
    }
    area = subject.get("living_area")
    if area:
        params["min_area"] = int(area * 0.75)
        params["max_area"] = int(area * 1.25)
    obj_type = subject.get("object_type")
    if obj_type:
        params["object_type"] = [obj_type]
    return params


def _filter_comps(subject: dict, comps: list[dict]) -> list[dict]:
    return [
        c for c in comps
        if c.get("global_id") != subject.get("global_id")
        and c.get("living_area")
        and c.get("price_amount")
    ][:30]


def _compute(subject: dict, comps: list[dict], cbs_stats: dict | None) -> dict:
    if not subject.get("living_area"):
        return {"confidence": "unavailable"}

    if len(comps) < 3:
        confidence = "low"
        band = 0.08
    else:
        confidence = "normal"
        band = 0.04

    if not comps:
        asking = subject.get("price_amount")
        if not asking:
            return {"confidence": "unavailable"}
        recommended = round(asking / 100) * 100
        return {
            "low": round(recommended * 0.95 / 100) * 100,
            "recommended": recommended,
            "high": round(recommended * 1.05 / 100) * 100,
            "adjustments": [{"label": "No comparables found", "delta_pct": 0,
                             "note": "Estimate based on asking price ±5% — no comparable listings found"}],
            "confidence": "low",
            "median_ppm": None,
            "comparables_count": 0,
        }

    ppms = [c["price_amount"] / c["living_area"] for c in comps]
    median_ppm = statistics.median(ppms)
    baseline = median_ppm * subject["living_area"]

    adjustments = []
    total_delta = 0.0

    # Energy label
    subj_label = subject.get("energy_label")
    comp_labels = [c.get("energy_label") for c in comps if c.get("energy_label")]
    if subj_label and comp_labels:
        ranks = sorted(_energy_rank(l) for l in comp_labels)
        median_rank = ranks[len(ranks) // 2]
        subj_rank = _energy_rank(subj_label)
        delta = max(-5, min(5, round((median_rank - subj_rank) * 1.5)))
        if delta != 0:
            total_delta += delta
            sign = "+" if delta > 0 else ""
            adjustments.append({
                "label": f"Energy label {subj_label}",
                "delta_pct": delta,
                "note": f"{sign}{delta}% vs. comparable average (your label: {subj_label})",
            })
    elif subj_label:
        delta = _ENERGY_DELTA_ABS.get(subj_label, 0)
        if delta != 0:
            total_delta += delta
            sign = "+" if delta > 0 else ""
            adjustments.append({
                "label": f"Energy label {subj_label}",
                "delta_pct": delta,
                "note": f"{sign}{delta}% for energy label {subj_label}",
            })

    # Construction year
    year = subject.get("construction_year")
    if year:
        try:
            y = int(year)
            if y > 2010:
                year_delta, year_note = 3, f"+3% for recent construction (built {y})"
            elif y >= 1990:
                year_delta, year_note = 1, f"+1% for post-1990 construction (built {y})"
            elif y >= 1945:
                year_delta, year_note = 0, None
            else:
                year_delta, year_note = -2, f"-2% for pre-1945 construction (built {y})"
            if year_delta != 0:
                total_delta += year_delta
                adjustments.append({
                    "label": f"Construction year ({y})",
                    "delta_pct": year_delta,
                    "note": year_note,
                })
        except (ValueError, TypeError):
            pass

    # Plot area scarcity
    obj_type = str(subject.get("object_type") or "").lower()
    if subject.get("plot_area") and "apartment" not in obj_type:
        comp_plots = [c.get("plot_area") for c in comps]
        has_plot_ratio = sum(1 for p in comp_plots if p) / len(comp_plots)
        if has_plot_ratio < 0.3:
            total_delta += 2
            adjustments.append({
                "label": "Garden / plot area",
                "delta_pct": 2,
                "note": "+2% — fewer than 30% of comparables have a plot (plot scarcity in this area)",
            })

    # CBS neighbourhood wealth
    if cbs_stats:
        woz_k = ((cbs_stats.get("buurt") or {}).get("housing") or {}).get("woz_value_k")
        if woz_k:
            if woz_k > _NATIONAL_AVG_WOZ_K * 1.3:
                total_delta += 2
                adjustments.append({
                    "label": "Neighbourhood wealth",
                    "delta_pct": 2,
                    "note": f"+2% — avg. WOZ value in this buurt is {_fmt_eur(int(woz_k * 1000))} (above average)",
                })
            elif woz_k < _NATIONAL_AVG_WOZ_K * 0.7:
                total_delta -= 2
                adjustments.append({
                    "label": "Neighbourhood wealth",
                    "delta_pct": -2,
                    "note": f"-2% — avg. WOZ value in this buurt is {_fmt_eur(int(woz_k * 1000))} (below average)",
                })

    # Market heat (recency proxy)
    cutoff = datetime.utcnow() - timedelta(days=30)
    recent_count = 0
    for c in comps:
        pub = c.get("publication_date")
        if pub:
            try:
                pub_dt = datetime.fromisoformat(pub[:10])
                if pub_dt >= cutoff:
                    recent_count += 1
            except (ValueError, TypeError):
                pass

    market_heat_factor = 1.0
    recent_ratio = recent_count / len(comps)
    if recent_ratio > 0.6:
        market_heat_factor = 1.02
        adjustments.append({
            "label": "Market activity",
            "delta_pct": 2,
            "note": f"+2% — {recent_count}/{len(comps)} comparables listed in last 30 days (active market)",
        })
    elif recent_ratio < 0.2:
        market_heat_factor = 0.98
        adjustments.append({
            "label": "Market activity",
            "delta_pct": -2,
            "note": f"-2% — only {recent_count}/{len(comps)} comparables listed recently (slow market)",
        })

    adjusted = baseline * (1 + total_delta / 100) * market_heat_factor
    recommended = round(adjusted / 100) * 100
    low = round(recommended * (1 - band) / 100) * 100
    high = round(recommended * (1 + band) / 100) * 100

    return {
        "low": int(low),
        "recommended": int(recommended),
        "high": int(high),
        "adjustments": adjustments,
        "confidence": confidence,
        "median_ppm": round(median_ppm),
        "comparables_count": len(comps),
    }


async def _upsert(db, global_id: str, asking_price, low, recommended, high,
                  comparables_count, median_ppm, confidence, adjustments_json: str) -> None:
    now = datetime.utcnow()
    row = await db.get(BidEstimate, global_id)
    if row:
        row.asking_price = asking_price
        row.low = low
        row.recommended = recommended
        row.high = high
        row.comparables_count = comparables_count
        row.median_price_per_m2 = median_ppm
        row.confidence = confidence
        row.adjustments_json = adjustments_json
        row.computed_at = now
    else:
        db.add(BidEstimate(
            global_id=global_id,
            asking_price=asking_price,
            low=low,
            recommended=recommended,
            high=high,
            comparables_count=comparables_count,
            median_price_per_m2=median_ppm,
            confidence=confidence,
            adjustments_json=adjustments_json,
            computed_at=now,
        ))
    await db.commit()


def _is_sold(subject: dict) -> bool:
    labels = subject.get("labels") or []
    return any("verkocht" in str(lb).lower() for lb in labels)


async def compute_bid_estimate(global_id: str) -> None:
    if global_id in _computing:
        return
    _computing.add(global_id)
    try:
        subject = await get_listing_detail(global_id)

        if not subject.get("living_area"):
            return

        if subject.get("is_auction") or _is_sold(subject):
            async with AsyncSessionLocal() as db:
                await _upsert(db, global_id, subject.get("price_amount"), 0, 0, 0, 0, None, "unavailable", "[]")
            return

        comps_params = _comparables_params(subject)
        comps = await search_listings(comps_params)
        comps = _filter_comps(subject, comps)

        cbs_stats = None
        identifier = subject.get("neighbourhood_identifier")
        if identifier and str(identifier).upper().startswith("BU"):
            try:
                cbs_stats = await get_neighbourhood_stats(identifier)
            except Exception:
                pass

        result = _compute(subject, comps, cbs_stats)

        if result.get("confidence") == "unavailable":
            async with AsyncSessionLocal() as db:
                await _upsert(db, global_id, subject.get("price_amount"), 0, 0, 0, 0, None, "unavailable", "[]")
            return

        async with AsyncSessionLocal() as db:
            await _upsert(
                db, global_id,
                subject.get("price_amount"),
                result["low"], result["recommended"], result["high"],
                result["comparables_count"], result.get("median_ppm"),
                result["confidence"],
                json.dumps(result["adjustments"]),
            )

        logger.info("bid estimate %s: recommended=%d confidence=%s n_comps=%d",
                    global_id, result["recommended"], result["confidence"], result["comparables_count"])
    except Exception:
        logger.exception("bid estimate failed for %s", global_id)
    finally:
        _computing.discard(global_id)


def _row_to_dict(row: BidEstimate) -> dict:
    return {
        "global_id": row.global_id,
        "asking_price": row.asking_price,
        "low": row.low,
        "recommended": row.recommended,
        "high": row.high,
        "low_fmt": _fmt_eur(row.low),
        "recommended_fmt": _fmt_eur(row.recommended),
        "high_fmt": _fmt_eur(row.high),
        "comparables_count": row.comparables_count,
        "median_price_per_m2": row.median_price_per_m2,
        "confidence": row.confidence,
        "adjustments": json.loads(row.adjustments_json),
        "computed_at": row.computed_at.isoformat(),
    }


async def get_cached_estimate(global_id: str) -> dict | None:
    async with AsyncSessionLocal() as db:
        row = await db.get(BidEstimate, global_id)
        if row and (datetime.utcnow() - row.computed_at) < _BID_TTL:
            return _row_to_dict(row)
    return None


async def get_estimate_force(global_id: str) -> dict | None:
    """Compute fresh estimate (ignoring cache) and return result dict."""
    await compute_bid_estimate(global_id)
    async with AsyncSessionLocal() as db:
        row = await db.get(BidEstimate, global_id)
        if row:
            return _row_to_dict(row)
    return None
