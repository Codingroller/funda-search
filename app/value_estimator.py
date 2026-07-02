"""Market-value estimator for a looked-up address (House info page).

Unlike bid_estimator — which needs a live Funda listing keyed by global_id — this
values ANY Dutch address:

  1. If the address IS for sale on Funda → defer to the real listing-based bid
     estimate (no double-computing) and re-present it here.
  2. Otherwise fetch the subject's own attributes from BAG (floor area, build
     year, house/apartment), gather Funda comparables by location, and run the
     same hedonic model on a *synthetic* subject.

A WOZ cross-check anchor is added, calibrated by a bounded, locally-derived
asking/WOZ ratio (sampled from a few sold comps, cached per PC4).

Results are cached in the ValueEstimate table (7-day TTL, keyed by addr_key),
version-gated on CURRENT_MODEL_VERSION exactly like bid_estimator.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
from datetime import timedelta

from app.bag_client import get_bag_info
from app.bid_comps import gather_cohort, weights_for_cohort
from app.bid_estimator import compute_bid_estimate, get_cached_estimate
from app.bid_explain import build_explanation
from app.bid_model import CURRENT_MODEL_VERSION, confidence_level, fit, predict
from app.config import settings
from app.db import AsyncSessionLocal
from app.funda_client import find_funda_listing
from app.models import Pc4WozRatio, ValueEstimate
from app.time_utils import as_utc, now_utc
from app.woz_client import get_woz

logger = logging.getLogger(__name__)

_VALUE_TTL = timedelta(days=7)
_PC4_RATIO_TTL = timedelta(days=60)
_computing: set[str] = set()     # addr_keys currently being computed

# WOZ→market local ratio (bounded cost)
_WOZ_SAMPLE_N = 6        # at most this many sold comps get a WOZ lookup
_WOZ_MIN_PAIRS = 3       # need at least this many usable price/WOZ pairs
_WOZ_RATIO_MIN = 0.9     # clamp the median ratio to a sane band
_WOZ_RATIO_MAX = 1.8
_DIVERGENCE = 0.20       # flag when the comps estimate and WOZ anchor disagree > this


def _fmt_eur(amount: int | None) -> str:
    if not amount:
        return "—"
    return f"€ {amount:,}".replace(",", ".")


def _grp(amount: int | None) -> str:
    return f"{int(amount):,}".replace(",", ".") if amount else "—"


# ── public compute + read ────────────────────────────────────────────────────

async def estimate_value_for_address(
    addr_key: str,
    address: dict,
    subject_woz_eur: int | None = None,
) -> dict | None:
    """Compute + persist a market-value estimate for a looked-up address.

    Returns the persisted estimate dict, or None if a compute is already in flight.
    """
    if addr_key in _computing:
        return None
    _computing.add(addr_key)
    try:
        postcode = address.get("postcode")
        huisnummer = address.get("huisnummer")
        suffix = address.get("suffix") or None
        city = address.get("city")
        street = address.get("street")
        pc4 = (postcode or "")[:4].strip() or None

        # 1) For-sale dedup: prefer the real, listing-based bid estimate.
        if postcode and huisnummer is not None:
            try:
                listing = await find_funda_listing(
                    postcode=postcode, huisnummer=huisnummer,
                    suffix=suffix, street=street, city=city,
                )
            except Exception:
                listing = None
            if listing and listing.get("living_area"):
                gid = listing["global_id"]
                await compute_bid_estimate(gid)
                est = await get_cached_estimate(gid)
                if est and est.get("confidence") != "unavailable":
                    await _persist_from_listing(addr_key, listing, est, pc4, subject_woz_eur)
                    return await get_cached_value_estimate(addr_key)
                # For-sale but no usable estimate → fall through to the BAG path.

        # 2) Synthetic BAG-subject path (address not for sale, or estimate missing).
        bag = await get_bag_info(
            address.get("nummeraanduiding_id"), postcode, huisnummer, suffix
        )
        living_area = bag.get("living_area") if bag else None
        if not living_area:
            await _persist_unavailable(addr_key, subject_woz_eur, bag)
            return await get_cached_value_estimate(addr_key)

        is_apartment = bag.get("is_apartment")
        subject = {
            "city": city,
            "postcode": postcode,
            "living_area": living_area,
            "construction_year": bag.get("construction_year"),
            # None object_type → gather_cohort won't type-filter, model imputes is_apartment
            "object_type": (
                "apartment" if is_apartment else "house"
            ) if is_apartment is not None else None,
        }

        cohort = await gather_cohort(subject)
        weights = weights_for_cohort(cohort)
        model = fit(cohort.active + cohort.sold, weights)
        overbid = settings.bid_overbid_pct
        low, recommended, high = predict(model, subject, overbid=overbid)

        if recommended <= 0:
            # No usable comparables and no median €/m² — nothing to anchor on.
            await _persist_unavailable(addr_key, subject_woz_eur, bag)
            return await get_cached_value_estimate(addr_key)

        adjustments = build_explanation(model, subject, cohort, recommended, overbid=overbid)
        confidence = confidence_level(model)

        anchor = await _woz_anchor(subject_woz_eur, pc4, recommended, cohort=cohort)
        woz_line = _woz_adjustment_line(anchor)
        if woz_line:
            adjustments.append(woz_line)
        if _diverges(anchor, recommended):
            adjustments.append({
                "label": "WOZ divergence",
                "delta_pct": 0,
                "note": (
                    "The comps estimate and the WOZ-implied value differ by more than "
                    f"{int(_DIVERGENCE * 100)}% — treat this estimate with extra caution"
                ),
            })

        await _upsert_value(addr_key, {
            "low": low, "recommended": recommended, "high": high,
            "comparables_count": len(cohort.active) + len(cohort.sold),
            "median_price_per_m2": round(model.median_ppm) if model.median_ppm else None,
            "confidence": confidence,
            "adjustments_json": json.dumps(adjustments),
            "model_version": CURRENT_MODEL_VERSION,
            "tier": cohort.tier,
            "n_active": len(cohort.active),
            "n_sold": len(cohort.sold),
            "r2": round(model.r2, 4) if not model.fallback else None,
            "residual_std": round(model.residual_std, 4) if not model.fallback else None,
            "living_area": living_area,
            "construction_year": bag.get("construction_year"),
            "is_apartment": is_apartment,
            "from_listing": False,
            **_anchor_columns(anchor),
        })

        logger.info(
            "value estimate %s: recommended=%d confidence=%s n_comps=%d tier=%s woz_src=%s",
            addr_key, recommended, confidence,
            len(cohort.active) + len(cohort.sold), cohort.tier, anchor.get("woz_ratio_source"),
        )
        return await get_cached_value_estimate(addr_key)
    except Exception:
        logger.exception("value estimate failed for %s", addr_key)
        return None
    finally:
        _computing.discard(addr_key)


async def get_cached_value_estimate(addr_key: str) -> dict | None:
    async with AsyncSessionLocal() as db:
        row = await db.get(ValueEstimate, addr_key)
        if row and (now_utc() - as_utc(row.computed_at)) < _VALUE_TTL:
            if row.model_version != CURRENT_MODEL_VERSION:
                return None
            return _row_to_dict(row)
    return None


# ── persistence helpers ──────────────────────────────────────────────────────

async def _upsert_value(addr_key: str, values: dict) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(ValueEstimate, addr_key)
        values = {**values, "computed_at": now_utc()}
        if row:
            for k, v in values.items():
                setattr(row, k, v)
        else:
            db.add(ValueEstimate(addr_key=addr_key, **values))
        await db.commit()


async def _persist_unavailable(addr_key: str, subject_woz_eur: int | None, bag: dict | None) -> None:
    await _upsert_value(addr_key, {
        "low": 0, "recommended": 0, "high": 0,
        "comparables_count": 0, "median_price_per_m2": None,
        "confidence": "unavailable", "adjustments_json": "[]",
        "model_version": CURRENT_MODEL_VERSION, "tier": None,
        "n_active": 0, "n_sold": 0, "r2": None, "residual_std": None,
        "living_area": bag.get("living_area") if bag else None,
        "construction_year": bag.get("construction_year") if bag else None,
        "is_apartment": bag.get("is_apartment") if bag else None,
        "from_listing": False,
        "woz_eur": subject_woz_eur or None,
        "woz_ratio": None, "woz_implied_eur": None, "woz_ratio_source": None,
    })


async def _persist_from_listing(
    addr_key: str, listing: dict, est: dict, pc4: str | None, subject_woz_eur: int | None
) -> None:
    """Re-present a live listing's real bid estimate as a value estimate + WOZ anchor."""
    anchor = await _woz_anchor(subject_woz_eur, pc4, est["recommended"], cohort=None)
    adjustments = list(est.get("adjustments") or [])
    woz_line = _woz_adjustment_line(anchor)
    if woz_line:
        adjustments.append(woz_line)
    obj = str(listing.get("object_type") or "").lower()
    await _upsert_value(addr_key, {
        "low": est["low"], "recommended": est["recommended"], "high": est["high"],
        "comparables_count": est["comparables_count"],
        "median_price_per_m2": est.get("median_price_per_m2"),
        "confidence": est["confidence"],
        "adjustments_json": json.dumps(adjustments),
        "model_version": CURRENT_MODEL_VERSION,
        "tier": est.get("tier"),
        "n_active": None, "n_sold": None,
        "r2": est.get("r2"), "residual_std": est.get("residual_std"),
        "living_area": listing.get("living_area"),
        "construction_year": listing.get("construction_year"),
        "is_apartment": ("apartment" in obj) if obj else None,
        "from_listing": True,
        **_anchor_columns(anchor),
    })


def _row_to_dict(row: ValueEstimate) -> dict:
    return {
        "addr_key": row.addr_key,
        "low": row.low, "recommended": row.recommended, "high": row.high,
        "low_fmt": _fmt_eur(row.low),
        "recommended_fmt": _fmt_eur(row.recommended),
        "high_fmt": _fmt_eur(row.high),
        "comparables_count": row.comparables_count,
        "median_price_per_m2": row.median_price_per_m2,
        "confidence": row.confidence,
        "adjustments": json.loads(row.adjustments_json),
        "computed_at": row.computed_at.isoformat(),
        "model_version": row.model_version,
        "tier": row.tier,
        "r2": row.r2,
        "residual_std": row.residual_std,
        "living_area": row.living_area,
        "construction_year": row.construction_year,
        "is_apartment": row.is_apartment,
        "from_listing": row.from_listing,
        "woz_eur": row.woz_eur,
        "woz_eur_fmt": _fmt_eur(row.woz_eur),
        "woz_ratio": row.woz_ratio,
        "woz_implied_eur": row.woz_implied_eur,
        "woz_implied_fmt": _fmt_eur(row.woz_implied_eur),
        "woz_ratio_source": row.woz_ratio_source,
    }


# ── WOZ→market local ratio ───────────────────────────────────────────────────

def _anchor_columns(anchor: dict) -> dict:
    """The persisted subset of a WOZ anchor (drops the transient woz_n)."""
    return {
        "woz_eur": anchor.get("woz_eur"),
        "woz_ratio": anchor.get("woz_ratio"),
        "woz_implied_eur": anchor.get("woz_implied_eur"),
        "woz_ratio_source": anchor.get("woz_ratio_source"),
    }


def _diverges(anchor: dict, recommended: int) -> bool:
    implied = anchor.get("woz_implied_eur")
    if not implied or not recommended:
        return False
    return abs(recommended - implied) / recommended > _DIVERGENCE


def _woz_adjustment_line(anchor: dict) -> dict | None:
    woz = anchor.get("woz_eur")
    if not woz:
        return None
    src = anchor.get("woz_ratio_source")
    ratio = anchor.get("woz_ratio")
    if src == "pc4-comps" and anchor.get("woz_implied_eur"):
        n = anchor.get("woz_n") or 0
        return {
            "label": "WOZ cross-check",
            "delta_pct": 0,
            "note": (
                f"WOZ € {_grp(woz)} × local {ratio:.2f} asking/WOZ "
                f"(from {n} nearby sale{'s' if n != 1 else ''}) ≈ € {_grp(anchor['woz_implied_eur'])}"
            ),
        }
    if src == "self-implied" and ratio:
        return {
            "label": "WOZ cross-check",
            "delta_pct": 0,
            "note": (
                f"Estimate is {ratio:.2f}× the WOZ (€ {_grp(woz)}); too few nearby sales "
                f"to derive an independent local ratio"
            ),
        }
    return {"label": "WOZ", "delta_pct": 0,
            "note": f"WOZ € {_grp(woz)} — municipal assessed value (not a market price)"}


async def _woz_anchor(
    subject_woz_eur: int | None, pc4: str | None, recommended: int, cohort=None
) -> dict:
    anchor = {
        "woz_eur": subject_woz_eur or None, "woz_ratio": None,
        "woz_implied_eur": None, "woz_ratio_source": None, "woz_n": 0,
    }
    if not subject_woz_eur:
        return anchor

    ratio_pack = None
    if cohort is not None:
        ratio_pack = await _local_woz_ratio(cohort, pc4)   # cache-check + bounded sampling
    elif pc4:
        ratio_pack = await _get_cached_pc4_ratio(pc4)       # cache-only (cheap for-sale path)

    if ratio_pack:
        ratio, n = ratio_pack
        implied = int(round(subject_woz_eur * ratio / 1000) * 1000)
        anchor.update(woz_ratio=round(ratio, 3), woz_implied_eur=implied,
                      woz_ratio_source="pc4-comps", woz_n=n)
    elif recommended:
        anchor.update(woz_ratio=round(recommended / subject_woz_eur, 3),
                      woz_ratio_source="self-implied")
    return anchor


async def _local_woz_ratio(cohort, pc4: str | None) -> tuple[float, int] | None:
    """Median asking/WOZ ratio from a bounded sample of sold comps. Cached per PC4."""
    if pc4:
        cached = await _get_cached_pc4_ratio(pc4)
        if cached is not None:
            return cached

    sold = getattr(cohort, "sold", [])
    candidates = [
        c for c in sold
        if c.get("postcode") and c.get("house_number") is not None
        and c.get("price_amount")
    ]
    # Prefer comps in the subject's own PC4, then cap the sample.
    candidates.sort(key=lambda c: 0 if (c.get("postcode") or "")[:4] == pc4 else 1)
    candidates = candidates[:_WOZ_SAMPLE_N]
    if len(candidates) < _WOZ_MIN_PAIRS:
        return None

    results = await asyncio.gather(*[_comp_ratio(c) for c in candidates])
    ratios = [r for r in results if r is not None]
    if len(ratios) < _WOZ_MIN_PAIRS:
        return None

    ratio = min(max(statistics.median(ratios), _WOZ_RATIO_MIN), _WOZ_RATIO_MAX)
    if pc4:
        await _cache_pc4_ratio(pc4, ratio, len(ratios))
    return (ratio, len(ratios))


async def _comp_ratio(comp: dict) -> float | None:
    """asking/WOZ for a single sold comp, or None if WOZ can't be resolved."""
    try:
        pc = comp["postcode"]
        hn = int(comp["house_number"])
    except (KeyError, TypeError, ValueError):
        return None
    suf = comp.get("house_number_suffix")
    key = comp.get("global_id") or f"addr:{pc.replace(' ', '')}-{hn}{(suf or '').lower()}"
    try:
        woz = await get_woz(key, pc, hn, suf)
    except Exception:
        return None
    if not woz or not woz.get("latest_woz_eur"):
        return None
    price = comp.get("price_amount")
    w = woz["latest_woz_eur"]
    if not price or w <= 0:
        return None
    r = price / w
    if r < 0.5 or r > 3.0:   # reject obviously mismatched pairs before the median
        return None
    return r


async def _get_cached_pc4_ratio(pc4: str) -> tuple[float, int] | None:
    async with AsyncSessionLocal() as db:
        row = await db.get(Pc4WozRatio, pc4)
        if row and (now_utc() - as_utc(row.fetched_at)) < _PC4_RATIO_TTL:
            return (row.ratio, row.n)
    return None


async def _cache_pc4_ratio(pc4: str, ratio: float, n: int) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(Pc4WozRatio, pc4)
        now = now_utc()
        if row:
            row.ratio = ratio
            row.n = n
            row.fetched_at = now
        else:
            db.add(Pc4WozRatio(pc4=pc4, ratio=ratio, n=n, fetched_at=now))
        await db.commit()
