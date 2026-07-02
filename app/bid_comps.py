"""Comparable-listing selection pipeline.

Fetches active and sold listings in parallel; tries to use a PC4-tight
cohort first, falls back to city-wide.  Sold listings need a detail fetch
(capped at 12) to obtain construction_year and full features; this reuses
the 24-hour ListingCache so previously viewed listings are served from DB.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.funda_client import get_listing_detail, search_listings
from app.time_utils import as_utc, now_utc

logger = logging.getLogger(__name__)

_MAX_ACTIVE = 25
_MAX_SOLD = 12
_PC4_MIN = 5             # use PC4 cohort only when we have at least this many
_SOLD_LOOKBACK_DAYS = 365
_DECAY_DAYS = 547.5      # 1.5-year decay constant for sold comps
_PC4_WEIGHT_BOOST = 8.0  # upweight comps in the subject's own PC4 (neighbourhood anchor)

# ── market-hotness (sell-through) → competitive-bid uplift ────────────────────
# Funda exposes neither realized sale prices nor days-on-market, so we can't
# measure over-asking directly. We proxy local "hotness" with a sell-through
# index: of the recently-active + recently-listed-and-sold homes in the cohort,
# what share already sold. A high share means homes are clearing fast (tight
# supply, competitive bidding); a low share means stock is piling up (cold).
_HOT_WINDOW_DAYS = 120   # only count sold comps listed within this window
_HOT_MIN_N = 8           # need this many comps to judge hotness; else neutral base
_SELL_THROUGH_LO = 0.35  # sell-through ≤ this → cold → min uplift
_SELL_THROUGH_HI = 0.70  # sell-through ≥ this → hot  → max uplift


@dataclass
class CompCohort:
    active: list[dict] = field(default_factory=list)
    sold: list[dict] = field(default_factory=list)
    tier: str = ""
    pc4: str | None = None
    notes: list[str] = field(default_factory=list)


def _pc4_first(rows: list[dict], pc4: str | None) -> list[dict]:
    """Order PC4-matching comps first so they survive the _MAX_* cap in the
    city-wide fallback. The neighbourhood weighting in bid_estimator can only
    anchor the estimate if the (usually few) local comps are actually present
    in the cohort rather than truncated out by the 'newest' sort."""
    if not pc4:
        return rows
    head = [c for c in rows if (c.get("postcode") or "")[:4] == pc4]
    head_ids = {c.get("global_id") for c in head}
    return head + [c for c in rows if c.get("global_id") not in head_ids]


def _is_valid(comp: dict, subject: dict) -> bool:
    return (
        comp.get("global_id") != subject.get("global_id")
        and bool(comp.get("living_area"))
        and bool(comp.get("price_amount"))
    )


def _filter_sold_recent(comps: list[dict], days: int = _SOLD_LOOKBACK_DAYS) -> list[dict]:
    cutoff = now_utc() - timedelta(days=days)
    result = []
    for c in comps:
        pub = c.get("publication_date")
        if not pub:
            result.append(c)
            continue
        try:
            if datetime.fromisoformat(pub[:10]).replace(tzinfo=timezone.utc) >= cutoff:
                result.append(c)
        except (ValueError, TypeError):
            result.append(c)
    return result


async def _enrich_sold(listings: list[dict]) -> list[dict]:
    """Fetch listing detail for sold comps to get construction_year and full features.

    Falls back to the raw search dict on any per-listing error so the rest
    of the cohort is never lost.
    """
    if not listings:
        return []
    tasks = [
        asyncio.wait_for(get_listing_detail(c["global_id"]), timeout=20)
        for c in listings
    ]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        return listings

    enriched = []
    for raw, detail in zip(listings, results):
        if isinstance(detail, Exception) or not isinstance(detail, dict):
            enriched.append(raw)
        else:
            merged = {**raw, **{k: v for k, v in detail.items() if v is not None}}
            enriched.append(merged)
    return enriched


async def gather_cohort(subject: dict, *, target_n: int = 25) -> CompCohort:
    """Return a CompCohort for the given subject listing."""
    city = subject.get("city")
    area = subject.get("living_area")
    obj = subject.get("object_type")
    pc4 = (subject.get("postcode") or "")[:4].strip()

    if not city or not area:
        return CompCohort(tier="unavailable", notes=["Subject missing city or living area"])

    base: dict = {
        "location": [city],
        "sort": "newest",
        "max_pages": 3,
        "min_area": int(area * 0.75),
        "max_area": int(area * 1.25),
    }
    if obj:
        base["object_type"] = [obj]

    # Fetch active and sold search results in parallel
    raw_active, raw_sold = await asyncio.gather(
        _safe_search({**base, "category": "buy"}),
        _safe_search({**base, "category": "sold"}),
    )

    valid_active = [c for c in raw_active if _is_valid(c, subject)]
    valid_sold = [c for c in raw_sold if _is_valid(c, subject)]

    # PC4 subsets (client-side — postcode is now included in search results)
    pc4_active = (
        [c for c in valid_active if (c.get("postcode") or "")[:4] == pc4]
        if pc4 else []
    )
    pc4_sold = (
        [c for c in valid_sold if (c.get("postcode") or "")[:4] == pc4]
        if pc4 else []
    )

    # Choose active pool
    notes: list[str] = []
    if len(pc4_active) >= _PC4_MIN:
        active = pc4_active[:_MAX_ACTIVE]
        use_pc4 = True
        notes.append(f"{len(active)} active listing{'s' if len(active) != 1 else ''} in PC4 {pc4}")
    else:
        active = _pc4_first(valid_active, pc4)[:_MAX_ACTIVE]
        use_pc4 = False
        if pc4 and pc4_active:
            notes.append(
                f"PC4 {pc4} had only {len(pc4_active)} active listing(s); "
                f"using city-wide ({len(active)})"
            )
        elif pc4:
            notes.append(f"No PC4 {pc4} active listings; using city-wide ({len(active)})")
        else:
            notes.append(f"{len(active)} active listings in {city}")

    # Choose sold pool and enrich with detail
    sold_candidates = (pc4_sold if use_pc4 else _pc4_first(valid_sold, pc4))[:_MAX_SOLD]
    enriched_sold = await _enrich_sold(sold_candidates)
    sold = _filter_sold_recent(enriched_sold)
    if sold:
        notes.append(f"{len(sold)} recently listed sold comparison{'s' if len(sold) != 1 else ''} included")

    # Build human-readable tier label
    scope = f"PC4 {pc4}" if use_pc4 else f"{city} city-wide"
    tier = scope + (" + recent sold" if sold else "")

    return CompCohort(active=active, sold=sold, tier=tier, pc4=pc4 or None, notes=notes)


async def _safe_search(params: dict) -> list[dict]:
    try:
        return await search_listings(params)
    except Exception as exc:
        logger.warning("search_listings failed (%s): %r", params.get("category"), exc)
        return []


def weights_for_cohort(cohort: CompCohort) -> list[float]:
    """Sample weights for fit(): time-decay sold comps and upweight same-PC4 comps.

    Pure (no I/O) — shared by both the listing-based bid estimator and the
    address-based value estimator.
    """
    now = now_utc()
    ws: list[float] = [1.0] * len(cohort.active)
    for c in cohort.sold:
        pub = c.get("publication_date")
        try:
            delta = (now - as_utc(datetime.fromisoformat(pub[:10]))).days
            ws.append(math.exp(-delta / _DECAY_DAYS))
        except Exception:
            ws.append(1.0)

    # Neighbourhood anchoring: when the PC4-tight cohort is too small we fall
    # back to a city-wide cohort, which dilutes premium/cheap micro-markets
    # (e.g. a new-build district priced well above the city median). Upweight
    # comps that share the subject's PC4 so the estimate stays anchored locally.
    subject_pc4 = cohort.pc4
    if subject_pc4:
        for i, c in enumerate(cohort.active + cohort.sold):
            if (c.get("postcode") or "")[:4] == subject_pc4:
                ws[i] *= _PC4_WEIGHT_BOOST

    # Preserve total weight so n_eff (OLS/ridge/confidence thresholds) is unchanged;
    # only the *relative* weighting shifts toward the local market.
    total = sum(ws)
    if total > 0:
        scale = len(ws) / total
        ws = [w * scale for w in ws]
    return ws


def _recent_sold_count(sold: list[dict], window_days: int) -> int:
    """Count sold comps listed within `window_days` of now (undated → counted)."""
    cutoff = now_utc() - timedelta(days=window_days)
    n = 0
    for c in sold:
        pub = c.get("publication_date")
        if not pub:
            n += 1
            continue
        try:
            if as_utc(datetime.fromisoformat(pub[:10])) >= cutoff:
                n += 1
        except (ValueError, TypeError):
            n += 1
    return n


def market_overbid(
    cohort: CompCohort,
    *,
    lo: float = 0.0,
    base: float = 0.03,
    hi: float = 0.05,
) -> tuple[float, dict]:
    """Competitive-bid uplift scaled by local market hotness (pure, no I/O).

    Replaces the old flat percentage. Hotness is a *sell-through* proxy: of the
    currently-active homes plus recently-listed-and-sold homes in the cohort,
    the share that already sold. It maps linearly from `lo` (cold, sell-through
    ≤ _SELL_THROUGH_LO) to `hi` (hot, sell-through ≥ _SELL_THROUGH_HI). When the
    cohort is too thin to judge (< _HOT_MIN_N comps) it returns the neutral
    `base`. Shared by the listing bid estimator and the address value estimator.

    Returns (overbid_fraction, meta) where meta feeds the rationale line.
    """
    n_active = len(cohort.active)
    n_recent_sold = _recent_sold_count(cohort.sold, _HOT_WINDOW_DAYS)
    total = n_active + n_recent_sold

    if total < _HOT_MIN_N:
        return base, {
            "overbid_pct": round(base * 100, 1), "sell_through": None,
            "n_recent_sold": n_recent_sold, "n_active": n_active, "hotness": None,
            "thin": True,
        }

    sell_through = n_recent_sold / total
    span = _SELL_THROUGH_HI - _SELL_THROUGH_LO
    hotness = (sell_through - _SELL_THROUGH_LO) / span if span > 0 else 0.0
    hotness = min(max(hotness, 0.0), 1.0)
    overbid = lo + (hi - lo) * hotness

    return overbid, {
        "overbid_pct": round(overbid * 100, 1),
        "sell_through": round(sell_through, 3),
        "n_recent_sold": n_recent_sold, "n_active": n_active,
        "hotness": round(hotness, 3), "thin": False,
    }
