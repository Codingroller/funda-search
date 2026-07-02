"""Bid estimate orchestrator.

Coordinates async I/O (comp fetch, DB upsert, cache), delegates all
numerics to bid_model, bid_comps, and bid_explain.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from app.bid_comps import gather_cohort, market_overbid, weights_for_cohort
from app.time_utils import as_utc, now_utc
from app.bid_explain import build_explanation
from app.bid_model import (
    CURRENT_MODEL_VERSION,
    FittedModel,
    _energy_rank,           # re-exported so existing importers don't break
    confidence_level,
    fit,
    predict,
)
from app.config import settings
from app.db import AsyncSessionLocal
from app.funda_client import get_listing_detail
from app.models import BidEstimate

logger = logging.getLogger(__name__)

_BID_TTL = timedelta(days=7)
_computing: set[str] = set()   # global_ids currently being computed


def _is_sold(subject: dict) -> bool:
    labels = subject.get("labels") or []
    return any("verkocht" in str(lb).lower() for lb in labels)


def _fmt_eur(amount: int | None) -> str:
    if not amount:
        return "—"
    return f"€ {amount:,}".replace(",", ".")


async def _upsert(
    db,
    global_id: str,
    asking_price,
    low: int,
    recommended: int,
    high: int,
    n_active: int,
    n_sold: int,
    median_ppm,
    confidence: str,
    adjustments_json: str,
    model_version: str | None,
    tier: str | None,
    r2: float | None,
    residual_std: float | None,
) -> None:
    now = now_utc()
    comparables_count = n_active + n_sold
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
        row.model_version = model_version
        row.tier = tier
        row.n_active = n_active
        row.n_sold = n_sold
        row.r2 = r2
        row.residual_std = residual_std
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
            model_version=model_version,
            tier=tier,
            n_active=n_active,
            n_sold=n_sold,
            r2=r2,
            residual_std=residual_std,
        ))
    await db.commit()


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
                await _upsert(db, global_id, subject.get("price_amount"),
                              0, 0, 0, 0, 0, None, "unavailable", "[]",
                              CURRENT_MODEL_VERSION, None, None, None)
            return

        cohort = await gather_cohort(subject)
        all_rows = cohort.active + cohort.sold
        weights = weights_for_cohort(cohort)

        model: FittedModel = fit(all_rows, weights)
        overbid, hot = market_overbid(
            cohort, lo=settings.bid_overbid_min,
            base=settings.bid_overbid_base, hi=settings.bid_overbid_max,
        )
        low, recommended, high = predict(model, subject, overbid=overbid)

        if recommended == 0:
            # No usable comparables and no median_ppm — fall back to asking price
            asking = subject.get("price_amount")
            if not asking:
                async with AsyncSessionLocal() as db:
                    await _upsert(db, global_id, None, 0, 0, 0, 0, 0, None,
                                  "unavailable", "[]", CURRENT_MODEL_VERSION, None, None, None)
                return
            recommended = round(asking / 100) * 100
            low = round(recommended * 0.95 / 100) * 100
            high = round(recommended * 1.05 / 100) * 100
            adjustments = [{"label": "No comparables found", "delta_pct": 0,
                            "note": "Estimate based on asking price ±5% — no comparable listings found"}]
        else:
            adjustments = build_explanation(model, subject, cohort, recommended, overbid=overbid, hot=hot)

        confidence = confidence_level(model)

        async with AsyncSessionLocal() as db:
            await _upsert(
                db, global_id,
                subject.get("price_amount"),
                low, recommended, high,
                len(cohort.active), len(cohort.sold),
                round(model.median_ppm) if model.median_ppm else None,
                confidence,
                json.dumps(adjustments),
                CURRENT_MODEL_VERSION,
                cohort.tier,
                round(model.r2, 4) if not model.fallback else None,
                round(model.residual_std, 4) if not model.fallback else None,
            )

        logger.info(
            "bid estimate %s: recommended=%d confidence=%s n_comps=%d tier=%s",
            global_id, recommended, confidence,
            len(cohort.active) + len(cohort.sold), cohort.tier,
        )
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
        "model_version": getattr(row, "model_version", None),
        "tier": getattr(row, "tier", None),
        "r2": getattr(row, "r2", None),
        "residual_std": getattr(row, "residual_std", None),
    }


async def get_cached_estimate(global_id: str) -> dict | None:
    async with AsyncSessionLocal() as db:
        row = await db.get(BidEstimate, global_id)
        if row and (now_utc() - as_utc(row.computed_at)) < _BID_TTL:
            # Invalidate rows computed with an older model version
            if getattr(row, "model_version", None) != CURRENT_MODEL_VERSION:
                return None
            return _row_to_dict(row)
    return None


async def get_estimate_force(global_id: str) -> dict | None:
    await compute_bid_estimate(global_id)
    async with AsyncSessionLocal() as db:
        row = await db.get(BidEstimate, global_id)
        if row:
            return _row_to_dict(row)
    return None
