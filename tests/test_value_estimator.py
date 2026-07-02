"""Unit + integration tests for the address value estimator."""

import pytest

from app import value_estimator
from app.bid_comps import CompCohort


# ── WOZ→market local ratio (pc4=None avoids the DB cache path) ────────────────

def _sold(gid, price, postcode="1234AB", hn=5):
    return {
        "global_id": gid, "postcode": postcode, "house_number": hn,
        "house_number_suffix": None, "price_amount": price, "living_area": 100,
    }


@pytest.mark.asyncio
class TestLocalWozRatio:
    async def test_median_ratio(self, monkeypatch):
        cohort = CompCohort(active=[], sold=[_sold("a", 500_000, hn=1),
                                            _sold("b", 500_000, hn=2),
                                            _sold("c", 500_000, hn=3)], pc4=None)

        async def fake_woz(key, pc, hn, suf):
            return {"latest_woz_eur": 400_000}   # ratio 1.25 each

        monkeypatch.setattr(value_estimator, "get_woz", fake_woz)
        result = await value_estimator._local_woz_ratio(cohort, None)
        assert result is not None
        ratio, n = result
        assert n == 3
        assert ratio == pytest.approx(1.25, abs=0.01)

    async def test_too_few_pairs_returns_none(self, monkeypatch):
        cohort = CompCohort(active=[], sold=[_sold("a", 500_000, hn=1),
                                            _sold("b", 500_000, hn=2)], pc4=None)

        async def fake_woz(key, pc, hn, suf):
            return {"latest_woz_eur": 400_000}

        monkeypatch.setattr(value_estimator, "get_woz", fake_woz)
        assert await value_estimator._local_woz_ratio(cohort, None) is None

    async def test_ratio_clamped_to_band(self, monkeypatch):
        # price/woz = 2.0 (passes the <3.0 sanity gate) but exceeds the 1.8 clamp.
        cohort = CompCohort(active=[], sold=[_sold("a", 400_000, hn=1),
                                            _sold("b", 400_000, hn=2),
                                            _sold("c", 400_000, hn=3)], pc4=None)

        async def fake_woz(key, pc, hn, suf):
            return {"latest_woz_eur": 200_000}

        monkeypatch.setattr(value_estimator, "get_woz", fake_woz)
        ratio, _ = await value_estimator._local_woz_ratio(cohort, None)
        assert ratio == value_estimator._WOZ_RATIO_MAX

    async def test_missing_woz_pairs_ignored(self, monkeypatch):
        cohort = CompCohort(active=[], sold=[_sold("a", 500_000, hn=1),
                                            _sold("b", 500_000, hn=2),
                                            _sold("c", 500_000, hn=3)], pc4=None)

        async def fake_woz(key, pc, hn, suf):
            return None   # WOZ never resolves

        monkeypatch.setattr(value_estimator, "get_woz", fake_woz)
        assert await value_estimator._local_woz_ratio(cohort, None) is None


@pytest.mark.asyncio
class TestWozAnchor:
    async def test_no_woz_returns_empty_anchor(self):
        a = await value_estimator._woz_anchor(None, "1234", 500_000, cohort=None)
        assert a["woz_eur"] is None
        assert a["woz_ratio_source"] is None

    async def test_self_implied_when_no_local_ratio(self):
        # cohort=None + pc4=None → no independent ratio → self-implied vs WOZ.
        a = await value_estimator._woz_anchor(400_000, None, 500_000, cohort=None)
        assert a["woz_ratio_source"] == "self-implied"
        assert a["woz_ratio"] == pytest.approx(1.25, abs=0.01)
        assert a["woz_implied_eur"] is None

    async def test_pc4_comps_anchor(self, monkeypatch):
        cohort = CompCohort(active=[], sold=[_sold("a", 500_000, hn=1),
                                            _sold("b", 500_000, hn=2),
                                            _sold("c", 500_000, hn=3)], pc4=None)

        async def fake_woz(key, pc, hn, suf):
            return {"latest_woz_eur": 400_000}

        monkeypatch.setattr(value_estimator, "get_woz", fake_woz)
        a = await value_estimator._woz_anchor(400_000, None, 500_000, cohort=cohort)
        assert a["woz_ratio_source"] == "pc4-comps"
        # 400k × 1.25 = 500k, rounded to nearest 1000
        assert a["woz_implied_eur"] == 500_000
        assert a["woz_n"] == 3

    async def test_divergence_flag(self):
        assert value_estimator._diverges({"woz_implied_eur": 800_000}, 500_000) is True
        assert value_estimator._diverges({"woz_implied_eur": 520_000}, 500_000) is False
        assert value_estimator._diverges({"woz_implied_eur": None}, 500_000) is False


# ── end-to-end orchestration (needs the tables) ──────────────────────────────

async def _create_tables():
    from app.db import Base, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _active_comps(n=15):
    comps = []
    for i in range(n):
        area = 80 + i * 3
        comps.append({
            "global_id": f"c{i}", "postcode": "1234AB",
            "living_area": area, "price_amount": area * 5000,
            "energy_label": "C", "object_type": "house",
        })
    return comps


@pytest.mark.asyncio
class TestEstimateForAddress:
    async def test_not_for_sale_uses_synthetic_subject(self, monkeypatch):
        await _create_tables()

        async def no_listing(**kwargs):
            return None

        async def fake_bag(nid, postcode, huisnummer, suffix):
            return {"living_area": 100, "construction_year": 1990,
                    "is_apartment": False, "gebruiksdoel": "woonfunctie",
                    "aantal_verblijfsobjecten": 1, "object_kind": "Verblijfsobject"}

        async def fake_cohort(subject, **kwargs):
            return CompCohort(active=_active_comps(), sold=[], tier="1234 city-wide", pc4="1234")

        monkeypatch.setattr(value_estimator, "find_funda_listing", no_listing)
        monkeypatch.setattr(value_estimator, "get_bag_info", fake_bag)
        monkeypatch.setattr(value_estimator, "gather_cohort", fake_cohort)

        address = {"nummeraanduiding_id": "NID-E1", "postcode": "1234 AB",
                   "huisnummer": 5, "suffix": None, "city": "Testcity", "street": "Teststraat"}
        est = await value_estimator.estimate_value_for_address("NID-E1", address, subject_woz_eur=350_000)

        assert est is not None
        assert est["confidence"] in ("normal", "low")
        assert est["recommended"] > 0
        assert est["from_listing"] is False
        assert est["living_area"] == 100
        assert est["woz_eur"] == 350_000
        # cache round-trips
        cached = await value_estimator.get_cached_value_estimate("NID-E1")
        assert cached["recommended"] == est["recommended"]

    async def test_missing_floor_area_is_unavailable(self, monkeypatch):
        await _create_tables()

        async def no_listing(**kwargs):
            return None

        async def fake_bag(nid, postcode, huisnummer, suffix):
            return {"living_area": None, "construction_year": None, "is_apartment": None,
                    "gebruiksdoel": None, "aantal_verblijfsobjecten": None, "object_kind": "Ligplaats"}

        monkeypatch.setattr(value_estimator, "find_funda_listing", no_listing)
        monkeypatch.setattr(value_estimator, "get_bag_info", fake_bag)

        address = {"nummeraanduiding_id": "NID-E2", "postcode": "1234 AB",
                   "huisnummer": 9, "suffix": None, "city": "Testcity", "street": "Teststraat"}
        est = await value_estimator.estimate_value_for_address("NID-E2", address, subject_woz_eur=None)
        assert est["confidence"] == "unavailable"
        assert est["recommended"] == 0

    async def test_for_sale_defers_to_listing_estimate(self, monkeypatch):
        await _create_tables()

        async def fake_find(**kwargs):
            return {"global_id": "GID-9", "living_area": 120,
                    "construction_year": 2001, "object_type": "house"}

        async def fake_compute(gid):
            return None

        async def fake_cached(gid):
            return {"low": 490_000, "recommended": 520_000, "high": 550_000,
                    "comparables_count": 18, "median_price_per_m2": 4200,
                    "confidence": "normal", "adjustments": [{"label": "Living area", "delta_pct": 5, "note": "x"}],
                    "tier": "PC4", "r2": 0.7, "residual_std": 0.1}

        monkeypatch.setattr(value_estimator, "find_funda_listing", fake_find)
        monkeypatch.setattr(value_estimator, "compute_bid_estimate", fake_compute)
        monkeypatch.setattr(value_estimator, "get_cached_estimate", fake_cached)

        address = {"nummeraanduiding_id": "NID-E3", "postcode": "1234 AB",
                   "huisnummer": 7, "suffix": None, "city": "Testcity", "street": "Teststraat"}
        est = await value_estimator.estimate_value_for_address("NID-E3", address, subject_woz_eur=430_000)

        assert est["from_listing"] is True
        assert est["recommended"] == 520_000
        assert est["living_area"] == 120
        # WOZ self-implied anchor present
        assert est["woz_eur"] == 430_000
