"""Unit tests for bid_comps using monkeypatched search_listings / get_listing_detail."""

import pytest
from datetime import timedelta
from unittest.mock import patch
from app.bid_comps import (
    CompCohort,
    gather_cohort,
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
