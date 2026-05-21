"""Unit tests for the pure _compute() function in bid_estimator."""

import pytest
from app.bid_estimator import _compute, _energy_rank


def _make_subject(**kwargs) -> dict:
    defaults = {
        "global_id": "123",
        "city": "Amsterdam",
        "living_area": 100,
        "energy_label": "C",
        "construction_year": 2000,
        "object_type": "house",
        "plot_area": None,
        "neighbourhood_identifier": None,
        "price_amount": 500_000,
        "is_auction": False,
        "labels": [],
    }
    defaults.update(kwargs)
    return defaults


def _make_comp(price_amount: int = 500_000, living_area: int = 100,
               energy_label: str = "C", publication_date: str = "2020-01-01",
               global_id: str = "comp1") -> dict:
    return {
        "global_id": global_id,
        "price_amount": price_amount,
        "living_area": living_area,
        "energy_label": energy_label,
        "publication_date": publication_date,
        "plot_area": None,
    }


class TestEnergyRank:
    def test_known_labels(self):
        assert _energy_rank("A") < _energy_rank("C") < _energy_rank("G")

    def test_unknown_label_returns_neutral(self):
        assert _energy_rank("X") == 5

    def test_none_returns_neutral(self):
        assert _energy_rank(None) == 5


class TestComputeBaseline:
    def test_baseline_price(self):
        # Use neutral subject (no year/energy adjustment) and old comps (no market heat)
        comps = [_make_comp(price_amount=500_000, living_area=100, global_id=str(i))
                 for i in range(10)]
        result = _compute(_make_subject(energy_label=None, construction_year=None), comps, None)
        assert result["confidence"] == "normal"
        # With old comps (all 2020-01-01) market heat factor = 0.98 → ~490,000
        # Just assert it's within 5% of the median
        assert abs(result["recommended"] - 500_000) / 500_000 < 0.05
        assert result["recommended"] % 100 == 0

    def test_range_is_4_pct_band(self):
        comps = [_make_comp(price_amount=500_000, living_area=100, global_id=str(i))
                 for i in range(10)]
        result = _compute(_make_subject(), comps, None)
        assert result["low"] < result["recommended"] < result["high"]
        pct_low = (result["recommended"] - result["low"]) / result["recommended"]
        pct_high = (result["high"] - result["recommended"]) / result["recommended"]
        assert abs(pct_low - 0.04) < 0.01
        assert abs(pct_high - 0.04) < 0.01

    def test_rounded_to_nearest_100(self):
        comps = [_make_comp(price_amount=487_350, living_area=100, global_id=str(i))
                 for i in range(10)]
        result = _compute(_make_subject(), comps, None)
        assert result["recommended"] % 100 == 0

    def test_comparables_count_stored(self):
        comps = [_make_comp(global_id=str(i)) for i in range(7)]
        result = _compute(_make_subject(), comps, None)
        assert result["comparables_count"] == 7


class TestEnergyAdjustment:
    def test_label_A_vs_median_C_gives_positive_delta(self):
        comps = [_make_comp(energy_label="C", global_id=str(i)) for i in range(5)]
        result = _compute(_make_subject(energy_label="A"), comps, None)
        labels = [a["label"] for a in result["adjustments"]]
        energy_adj = next((a for a in result["adjustments"] if "Energy" in a["label"]), None)
        assert energy_adj is not None
        assert energy_adj["delta_pct"] > 0

    def test_label_G_vs_median_C_gives_negative_delta(self):
        comps = [_make_comp(energy_label="C", global_id=str(i)) for i in range(5)]
        result = _compute(_make_subject(energy_label="G"), comps, None)
        energy_adj = next((a for a in result["adjustments"] if "Energy" in a["label"]), None)
        assert energy_adj is not None
        assert energy_adj["delta_pct"] < 0

    def test_same_label_no_adjustment(self):
        comps = [_make_comp(energy_label="C", global_id=str(i)) for i in range(5)]
        result = _compute(_make_subject(energy_label="C"), comps, None)
        energy_adj = next((a for a in result["adjustments"] if "Energy" in a["label"]), None)
        assert energy_adj is None


class TestConstructionYear:
    def test_post_2010_gives_plus_3(self):
        comps = [_make_comp(global_id=str(i)) for i in range(5)]
        result = _compute(_make_subject(energy_label=None, construction_year=2015), comps, None)
        year_adj = next((a for a in result["adjustments"] if "Construction" in a["label"]), None)
        assert year_adj is not None
        assert year_adj["delta_pct"] == 3

    def test_pre_1945_gives_minus_2(self):
        comps = [_make_comp(global_id=str(i)) for i in range(5)]
        result = _compute(_make_subject(energy_label=None, construction_year=1930), comps, None)
        year_adj = next((a for a in result["adjustments"] if "Construction" in a["label"]), None)
        assert year_adj is not None
        assert year_adj["delta_pct"] == -2

    def test_boundary_1944_is_pre(self):
        comps = [_make_comp(global_id=str(i)) for i in range(5)]
        result = _compute(_make_subject(energy_label=None, construction_year=1944), comps, None)
        year_adj = next((a for a in result["adjustments"] if "Construction" in a["label"]), None)
        assert year_adj["delta_pct"] == -2

    def test_boundary_1945_is_neutral(self):
        comps = [_make_comp(global_id=str(i)) for i in range(5)]
        result = _compute(_make_subject(energy_label=None, construction_year=1945), comps, None)
        year_adj = next((a for a in result["adjustments"] if "Construction" in a["label"]), None)
        assert year_adj is None


class TestSparseComparables:
    def test_two_comps_gives_low_confidence(self):
        comps = [_make_comp(global_id=str(i)) for i in range(2)]
        result = _compute(_make_subject(energy_label=None, construction_year=None), comps, None)
        assert result["confidence"] == "low"

    def test_low_confidence_wider_band(self):
        comps = [_make_comp(global_id=str(i)) for i in range(2)]
        result = _compute(_make_subject(energy_label=None, construction_year=None), comps, None)
        pct_low = (result["recommended"] - result["low"]) / result["recommended"]
        assert abs(pct_low - 0.08) < 0.01

    def test_no_comps_falls_back_to_asking_price(self):
        result = _compute(_make_subject(price_amount=400_000, energy_label=None,
                                        construction_year=None), [], None)
        assert result["confidence"] == "low"
        assert result["recommended"] == 400_000

    def test_no_living_area_returns_unavailable(self):
        result = _compute(_make_subject(living_area=None), [], None)
        assert result["confidence"] == "unavailable"


class TestCbsWealthAdjustment:
    def _make_cbs(self, woz_k: float) -> dict:
        return {"buurt": {"housing": {"woz_value_k": woz_k}}, "wijk": {}}

    def test_high_woz_adds_2pct(self):
        comps = [_make_comp(global_id=str(i)) for i in range(5)]
        cbs = self._make_cbs(500.0)  # 500k > 320k * 1.3
        result = _compute(_make_subject(energy_label=None, construction_year=None), comps, cbs)
        cbs_adj = next((a for a in result["adjustments"] if "Neighbourhood" in a["label"]), None)
        assert cbs_adj is not None
        assert cbs_adj["delta_pct"] == 2

    def test_low_woz_subtracts_2pct(self):
        comps = [_make_comp(global_id=str(i)) for i in range(5)]
        cbs = self._make_cbs(150.0)  # 150k < 320k * 0.7
        result = _compute(_make_subject(energy_label=None, construction_year=None), comps, cbs)
        cbs_adj = next((a for a in result["adjustments"] if "Neighbourhood" in a["label"]), None)
        assert cbs_adj is not None
        assert cbs_adj["delta_pct"] == -2

    def test_average_woz_no_adjustment(self):
        comps = [_make_comp(global_id=str(i)) for i in range(5)]
        cbs = self._make_cbs(320.0)
        result = _compute(_make_subject(energy_label=None, construction_year=None), comps, cbs)
        cbs_adj = next((a for a in result["adjustments"] if "Neighbourhood" in a["label"]), None)
        assert cbs_adj is None
