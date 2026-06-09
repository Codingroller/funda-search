"""Unit tests for bid_model: fit(), predict(), and confidence_level()."""

import math
import pytest
from app.bid_model import (
    FittedModel,
    _N_EFF_OLS,
    _N_EFF_RIDGE,
    _BAND_MIN,
    _BAND_MAX,
    confidence_level,
    fit,
    predict,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(price=500_000, area=100, energy="C", year=2000, plot=0, obj="house", **kw):
    return {
        "price_amount": price,
        "living_area": area,
        "energy_label": energy,
        "construction_year": year,
        "plot_area": plot,
        "object_type": obj,
        **kw,
    }


def _cohort(n=15, **row_kw):
    """Create a homogeneous cohort of n identical rows for baseline tests."""
    return [_row(**row_kw) for _ in range(n)]


# ── TestFit ────────────────────────────────────────────────────────────────

class TestFit:
    def test_returns_fitted_model(self):
        rows = _cohort(15)
        m = fit(rows, [1.0] * 15)
        assert isinstance(m, FittedModel)

    def test_fallback_below_ridge_threshold(self):
        rows = _cohort(4)
        m = fit(rows, [1.0] * 4)
        assert m.fallback is True
        assert m.n_eff < _N_EFF_RIDGE

    def test_ridge_engages_between_5_and_12(self):
        rows = _cohort(8)
        m = fit(rows, [1.0] * 8)
        assert not m.fallback
        assert m.ridge_lambda > 0

    def test_pure_ols_above_12(self):
        rows = _cohort(15)
        m = fit(rows, [1.0] * 15)
        assert not m.fallback
        assert m.ridge_lambda == 0.0

    def test_median_ppm_always_set_when_rows_present(self):
        rows = _cohort(15)
        m = fit(rows, [1.0] * 15)
        assert m.median_ppm == pytest.approx(5000.0, rel=0.01)  # 500k / 100m²

    def test_constant_area_feature_dropped(self):
        # If all rows have identical area, log_area has zero std → excluded
        rows = _cohort(15, area=100)
        m = fit(rows, [1.0] * 15)
        assert "log_area" not in m.feature_used

    def test_varying_area_feature_used(self):
        rows = [_row(area=a, price=a * 5000) for a in range(60, 160, 7)]  # 15 rows
        m = fit(rows, [1.0] * len(rows))
        assert "log_area" in m.feature_used

    def test_r2_near_one_for_perfectly_linear_data(self):
        rows = [_row(area=a, price=a * 5000) for a in range(60, 160, 7)]
        m = fit(rows, [1.0] * len(rows))
        assert m.r2 > 0.90

    def test_weighted_fit_ignores_low_weight_outlier(self):
        # 14 rows at 500k/100m² + 1 outlier at 2M; outlier has weight 0.01
        rows = _cohort(14) + [_row(price=2_000_000)]
        ws = [1.0] * 14 + [0.01]
        m = fit(rows, ws)
        assert m.median_ppm == pytest.approx(5000.0, rel=0.05)

    def test_empty_rows_returns_fallback(self):
        m = fit([], [])
        assert m.fallback is True
        assert m.median_ppm is None

    def test_rows_missing_area_excluded(self):
        rows = [_row()] * 10 + [{"price_amount": 500_000, "living_area": None}] * 5
        m = fit(rows, [1.0] * 15)
        assert m.n_eff == pytest.approx(10.0)


# ── TestPredict ────────────────────────────────────────────────────────────

class TestPredict:
    def test_recommended_near_median_for_homogeneous_cohort(self):
        rows = _cohort(15)
        m = fit(rows, [1.0] * 15)
        lo, rec, hi = predict(m, _row())
        assert abs(rec - 500_000) / 500_000 < 0.05

    def test_rounded_to_nearest_100(self):
        rows = _cohort(15, price=487_350)
        m = fit(rows, [1.0] * 15)
        _, rec, _ = predict(m, _row(price=487_350))
        assert rec % 100 == 0

    def test_low_less_than_recommended_less_than_high(self):
        rows = _cohort(15)
        m = fit(rows, [1.0] * 15)
        lo, rec, hi = predict(m, _row())
        assert lo < rec < hi

    def test_band_within_min_max(self):
        rows = _cohort(15)
        m = fit(rows, [1.0] * 15)
        lo, rec, hi = predict(m, _row())
        assert (rec - lo) / rec >= _BAND_MIN * 0.9
        assert (hi - rec) / rec <= _BAND_MAX * 1.1

    def test_missing_living_area_returns_zeros(self):
        rows = _cohort(15)
        m = fit(rows, [1.0] * 15)
        assert predict(m, {"price_amount": 500_000}) == (0, 0, 0)

    def test_fallback_uses_median_ppm_times_area(self):
        rows = _cohort(3)   # n_eff=3 < 5 → fallback
        m = fit(rows, [1.0] * 3)
        _, rec, _ = predict(m, _row(area=80))
        assert abs(rec - 400_000) / 400_000 < 0.02

    def test_wider_band_for_low_n(self):
        rows_small = _cohort(7)   # ridge → low confidence
        rows_large = _cohort(15)  # OLS → normal confidence
        lo_s, rec_s, hi_s = predict(fit(rows_small, [1.0] * 7), _row())
        lo_l, rec_l, hi_l = predict(fit(rows_large, [1.0] * 15), _row())
        band_small = (hi_s - lo_s) / rec_s
        band_large = (hi_l - lo_l) / rec_l
        assert band_small >= band_large

    def test_better_energy_label_predicts_higher(self):
        # Build cohort where energy label correlates with price
        labels = ["A", "B", "C", "D", "G"]
        prices = [600_000, 550_000, 500_000, 450_000, 400_000]
        rows = []
        for label, price in zip(labels * 3, prices * 3):
            rows.append(_row(price=price, energy=label))
        m = fit(rows, [1.0] * 15)
        _, rec_a, _ = predict(m, _row(energy="A"))
        _, rec_g, _ = predict(m, _row(energy="G"))
        assert rec_a > rec_g

    def test_newer_construction_predicts_higher(self):
        # Build cohort where year correlates with price
        years_prices = [(2020, 600_000), (2000, 550_000), (1980, 500_000),
                        (1960, 450_000), (1930, 400_000)]
        rows = []
        for year, price in years_prices * 3:
            rows.append(_row(price=price, year=year))
        m = fit(rows, [1.0] * 15)
        _, rec_new, _ = predict(m, _row(year=2020))
        _, rec_old, _ = predict(m, _row(year=1930))
        assert rec_new > rec_old


# ── TestConfidenceLevel ───────────────────────────────────────────────────

class TestGardenNonNegative:
    def test_has_plot_coefficient_never_negative(self):
        # Build a cohort where having a plot spuriously correlates with LOWER
        # price (e.g. plotted houses are older/cheaper here). The guard must
        # prevent a negative has_plot coefficient.
        rows = []
        for i in range(10):
            rows.append(_row(price=600_000, plot=0, obj="apartment", area=100))   # no plot, pricey
        for i in range(10):
            rows.append(_row(price=400_000, plot=150, obj="house", area=100))      # plot, cheaper
        m = fit(rows, [1.0] * len(rows))
        assert m.coef.get("has_plot", 0.0) >= 0.0

    def test_garden_adds_value_when_correlated_up(self):
        # When a plot genuinely correlates with higher price, the premium applies.
        rows = []
        for i in range(10):
            rows.append(_row(price=400_000, plot=0, obj="apartment", area=100))
        for i in range(10):
            rows.append(_row(price=600_000, plot=150, obj="house", area=100))
        m = fit(rows, [1.0] * len(rows))
        _, rec_plot, _ = predict(m, _row(plot=150, obj="house", area=100))
        _, rec_none, _ = predict(m, _row(plot=0, obj="apartment", area=100))
        assert rec_plot >= rec_none


class TestConfidenceLevel:
    def test_fallback_is_low(self):
        m = fit(_cohort(3), [1.0] * 3)
        assert m.fallback is True
        assert confidence_level(m) == "low"

    def test_ridge_is_low(self):
        m = fit(_cohort(8), [1.0] * 8)
        assert not m.fallback
        assert m.n_eff < _N_EFF_OLS
        assert confidence_level(m) == "low"

    def test_ols_is_normal(self):
        m = fit(_cohort(15), [1.0] * 15)
        assert confidence_level(m) == "normal"


# ── TestNullFeatureHandling ───────────────────────────────────────────────
# These guard against None values in nullable features (year_built, log_area)
# crashing fit() or predict(). Each test maps to a real production failure mode.

class TestNullFeatureHandling:
    def test_fit_does_not_crash_with_null_year_built_in_cohort(self):
        # year varies across rows → year_built enters feature_used; some rows
        # have construction_year=None → must impute mean, not crash on None - float
        rows = [_row(year=y, price=500_000 + y * 100) for y in range(1980, 1995)]
        rows += [_row(year=None, price=500_000) for _ in range(3)]
        m = fit(rows, [1.0] * len(rows))
        assert isinstance(m, FittedModel)
        assert not m.fallback

    def test_fit_null_year_rows_treated_as_cohort_mean(self):
        # Null-year rows contribute z=0, so they don't skew coef but also don't crash
        rows = [_row(area=a, year=2000, price=a * 5000) for a in range(80, 95)]
        rows += [_row(area=90, year=None, price=450_000) for _ in range(3)]
        m = fit(rows, [1.0] * len(rows))
        lo, rec, hi = predict(m, _row(area=90))
        assert rec > 0

    def test_fit_mixed_null_features_across_rows(self):
        # Different rows missing different nullable features
        rows = [_row(area=a, year=2000 + a, price=a * 5000) for a in range(80, 93)]
        rows += [_row(area=90, year=None, price=450_000)]
        rows += [_row(area=85, year=1995, price=425_000)]
        m = fit(rows, [1.0] * len(rows))
        assert isinstance(m, FittedModel)

    def test_predict_subject_with_null_year_built(self):
        rows = [_row(year=y, area=a, price=a * 5000)
                for y, a in zip(range(1990, 2005), range(80, 95))]
        m = fit(rows, [1.0] * len(rows))
        subject = {**_row(), "construction_year": None}
        lo, rec, hi = predict(m, subject)
        assert rec > 0
        assert lo < rec < hi

    def test_predict_subject_all_optional_features_none(self):
        # Only living_area is provided; all other fields are None
        rows = [_row(year=y, area=a, price=a * 5000)
                for y, a in zip(range(1990, 2005), range(80, 95))]
        m = fit(rows, [1.0] * len(rows))
        subject = {
            "living_area": 90,
            "price_amount": None,
            "construction_year": None,
            "energy_label": None,
            "plot_area": None,
            "object_type": None,
        }
        lo, rec, hi = predict(m, subject)
        assert rec > 0


# ── TestBandWidthReflectsDispersion ──────────────────────────────────────

class TestBandWidthReflectsDispersion:
    def test_uniform_cohort_has_tighter_band(self):
        # Identical prices → residual_std ≈ 0 → band hits floor
        rows_tight = _cohort(15, price=500_000)
        rows_wide = [_row(price=400_000 + i * 20_000) for i in range(15)]
        m_tight = fit(rows_tight, [1.0] * 15)
        m_wide = fit(rows_wide, [1.0] * 15)
        lo_t, rec_t, hi_t = predict(m_tight, _row())
        lo_w, rec_w, hi_w = predict(m_wide, _row())
        assert (hi_t - lo_t) / rec_t <= (hi_w - lo_w) / rec_w + 0.01
