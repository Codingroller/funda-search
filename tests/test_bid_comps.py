"""Unit tests for bid_comps using monkeypatched search_listings / get_listing_detail."""

import pytest
from datetime import timedelta
from unittest.mock import patch
from app.bid_comps import (
    CompCohort,
    gather_cohort,
    market_overbid,
    _filter_sold_recent,
    _is_valid,
)

# Use dynamic dates so tests don't break as time passes
from app.time_utils import now_utc
_RECENT = (now_utc() - timedelta(days=30)).strftime("%Y-%m-%d")
_STALE  = "2020-01-01"


# ── helpers ──────────────────────────────────────────────────────────────────

def _comp(global_id="c1", area=100, price=500_000, postcode="1012AB",
          pub=None, energy="C"):
    if pub is None:
        pub = _RECENT
    return {
        "global_id": global_id,
        "living_area": area,
        "price_amount": price,
        "postcode": postcode,
        "publication_date": pub,
        "energy_label": energy,
    }


def _subject(global_id="s1", city="Amsterdam", area=100, postcode="1012XX"):
    return {
        "global_id": global_id,
        "city": city,
        "living_area": area,
        "postcode": postcode,
    }


# ── TestIsValid ───────────────────────────────────────────────────────────

class TestIsValid:
    def test_valid_comp(self):
        assert _is_valid(_comp(), _subject())

    def test_same_global_id_excluded(self):
        assert not _is_valid(_comp(global_id="s1"), _subject())

    def test_missing_area_excluded(self):
        c = _comp()
        c["living_area"] = None
        assert not _is_valid(c, _subject())

    def test_missing_price_excluded(self):
        c = _comp()
        c["price_amount"] = 0
        assert not _is_valid(c, _subject())


# ── TestFilterSoldRecent ─────────────────────────────────────────────────

class TestFilterSoldRecent:
    def test_recent_included(self):
        comps = [_comp(pub=_RECENT)]
        assert len(_filter_sold_recent(comps, days=60)) == 1

    def test_stale_excluded(self):
        comps = [_comp(pub=_STALE)]
        assert len(_filter_sold_recent(comps, days=365)) == 0

    def test_missing_date_included(self):
        c = _comp()
        del c["publication_date"]
        assert len(_filter_sold_recent([c])) == 1


# ── TestGatherCohort ──────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGatherCohort:
    async def test_missing_city_returns_unavailable(self):
        sub = _subject()
        del sub["city"]
        cohort = await gather_cohort(sub)
        assert cohort.tier == "unavailable"

    async def test_pc4_tight_when_sufficient(self):
        pc4_comps = [_comp(global_id=str(i), postcode="1012AB") for i in range(6)]
        other_comps = [_comp(global_id=str(i + 100), postcode="2000XX") for i in range(10)]

        async def fake_search(params):
            if params.get("category") == "buy":
                return pc4_comps + other_comps
            return []

        with patch("app.bid_comps.search_listings", new=fake_search):
            cohort = await gather_cohort(_subject())

        # PC4 filter keeps only 1012-prefix comps
        assert all((c.get("postcode") or "")[:4] == "1012" for c in cohort.active)
        assert "PC4 1012" in cohort.tier

    async def test_city_wide_fallback_when_pc4_sparse(self):
        # Only 3 comps in PC4 (below _PC4_MIN=5), rest from other postcodes
        pc4_comps = [_comp(global_id=str(i), postcode="1012AB") for i in range(3)]
        other_comps = [_comp(global_id=str(i + 100), postcode="2000XX") for i in range(12)]

        async def fake_search(params):
            if params.get("category") == "buy":
                return pc4_comps + other_comps
            return []

        with patch("app.bid_comps.search_listings", new=fake_search):
            cohort = await gather_cohort(_subject())

        assert len(cohort.active) == 15  # all comps used
        assert "city-wide" in cohort.tier

    async def test_subject_excluded_from_cohort(self):
        comps = [_comp(global_id="s1")] + [_comp(global_id=str(i)) for i in range(10)]

        async def fake_search(params):
            return comps if params.get("category") == "buy" else []

        with patch("app.bid_comps.search_listings", new=fake_search):
            cohort = await gather_cohort(_subject())

        assert all(c["global_id"] != "s1" for c in cohort.active)

    async def test_sold_comps_included(self):
        active_comps = [_comp(global_id=str(i), postcode="1012AB") for i in range(6)]
        sold_comps = [_comp(global_id=str(i + 50), postcode="1012AB", pub=_RECENT)
                      for i in range(4)]

        async def fake_search(params):
            if params.get("category") == "buy":
                return active_comps
            return sold_comps

        async def fake_detail(global_id):
            return {"global_id": global_id, "living_area": 100, "price_amount": 500_000,
                    "publication_date": _RECENT}

        with patch("app.bid_comps.search_listings", new=fake_search), \
             patch("app.bid_comps.get_listing_detail", new=fake_detail):
            cohort = await gather_cohort(_subject())

        assert len(cohort.sold) > 0
        assert "sold" in cohort.tier.lower()

    async def test_sold_comps_older_than_365_days_excluded(self):
        active_comps = [_comp(global_id=str(i), postcode="1012AB") for i in range(6)]
        stale_sold = [_comp(global_id=str(i + 50), postcode="1012AB", pub=_STALE)
                      for i in range(4)]

        async def fake_search(params):
            if params.get("category") == "buy":
                return active_comps
            return stale_sold

        async def fake_detail(global_id):
            return {"global_id": global_id, "living_area": 100, "price_amount": 500_000,
                    "publication_date": _STALE}

        with patch("app.bid_comps.search_listings", new=fake_search), \
             patch("app.bid_comps.get_listing_detail", new=fake_detail):
            cohort = await gather_cohort(_subject())

        assert cohort.sold == []

    async def test_search_failure_returns_empty_cohort_not_exception(self):
        async def bad_search(params):
            raise RuntimeError("network error")

        with patch("app.bid_comps.search_listings", new=bad_search):
            cohort = await gather_cohort(_subject())

        assert isinstance(cohort, CompCohort)
        assert cohort.active == []
        assert cohort.sold == []


# ── TestMarketOverbid ─────────────────────────────────────────────────────

def _cohort(n_active=0, n_sold_recent=0, n_sold_stale=0):
    active = [_comp(global_id=f"a{i}") for i in range(n_active)]
    sold = (
        [_comp(global_id=f"sr{i}", pub=_RECENT) for i in range(n_sold_recent)]
        + [_comp(global_id=f"ss{i}", pub=_STALE) for i in range(n_sold_stale)]
    )
    return CompCohort(active=active, sold=sold)


class TestMarketOverbid:
    def test_hot_market_reaches_max(self):
        # 12 recent sold vs 3 active → sell-through 0.80 ≥ 0.70 → max uplift
        overbid, meta = market_overbid(_cohort(n_active=3, n_sold_recent=12),
                                       lo=0.0, base=0.03, hi=0.06)
        assert overbid == 0.06
        assert meta["hotness"] == 1.0
        assert meta["sell_through"] == 0.8
        assert meta["thin"] is False

    def test_cold_market_reaches_min(self):
        # 1 recent sold vs 20 active → sell-through ≈ 0.05 ≤ 0.35 → min uplift
        overbid, meta = market_overbid(_cohort(n_active=20, n_sold_recent=1),
                                       lo=0.0, base=0.03, hi=0.06)
        assert overbid == 0.0
        assert meta["hotness"] == 0.0

    def test_balanced_market_between_bounds(self):
        # 6 recent sold vs 8 active → sell-through ≈ 0.43 → strictly interior
        overbid, meta = market_overbid(_cohort(n_active=8, n_sold_recent=6),
                                       lo=0.0, base=0.03, hi=0.06)
        assert 0.0 < overbid < 0.06
        assert 0.0 < meta["hotness"] < 1.0

    def test_thin_cohort_returns_base(self):
        overbid, meta = market_overbid(_cohort(n_active=2, n_sold_recent=2),
                                       lo=0.0, base=0.03, hi=0.06)
        assert overbid == 0.03
        assert meta["thin"] is True
        assert meta["sell_through"] is None

    def test_stale_sold_not_counted_as_recent(self):
        # 10 active + 10 stale sold: none within the window → sell-through 0 → min
        overbid, meta = market_overbid(_cohort(n_active=10, n_sold_stale=10),
                                       lo=0.0, base=0.03, hi=0.06)
        assert meta["n_recent_sold"] == 0
        assert overbid == 0.0

    def test_custom_band_is_respected(self):
        overbid, _ = market_overbid(_cohort(n_active=2, n_sold_recent=13),
                                    lo=0.01, base=0.02, hi=0.10)
        assert overbid == 0.10  # hot → hits custom max

    def test_meta_reports_counts(self):
        _, meta = market_overbid(_cohort(n_active=5, n_sold_recent=7))
        assert meta["n_active"] == 5
        assert meta["n_recent_sold"] == 7
