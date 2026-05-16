"""Tests for app/cbs_view.py — view-model that converts raw CBS data to a template-ready dict."""
import pytest

from app.cbs_view import (
    build_view,
    _fmt_int,
    _fmt_k_eur,
    _walk_css,
    _dot_scale,
    _delta_pct,
)


# ---------------------------------------------------------------------------
# Sample CBS data — matches the structure returned by cbs_client.get_neighbourhood_stats
# for Garenkokerskwartier (BU03920301) / Zijlwegkwartier (WK039203).
# ---------------------------------------------------------------------------

def _make_area(buurt=True):
    return {
        "total_ha": 26.0,
        "land_ha": 24.0,
        "water_ha": 2.0,
        "postcode": "2014" if buurt else None,
        "urbanisation": 1.0,
        "address_density": 6200.0 if buurt else 5800.0,
        "coverage_pct": 98.0,
    }


_BUURT = {
    "code": "BU03920301",
    "name": "Garenkokerskwartier",
    "gemeente": "Haarlem",
    "population": {
        "total": 1985.0,
        "men": 960.0,
        "women": 1025.0,
        "age_0_15": 210.0,
        "age_15_25": 240.0,
        "age_25_45": 720.0,
        "age_45_65": 560.0,
        "age_65plus": 255.0,
        "births_total": None,
        "births_rate": None,
        "deaths_total": None,
        "deaths_rate": None,
    },
    "marital": {
        "unmarried": 950.0,
        "married": 720.0,
        "divorced": 195.0,
        "widowed": 120.0,
    },
    "heritage": {
        "total_nl": 1550.0,
        "total_europe": 185.0,
        "total_outside_europe": 250.0,
        "born_nl_heritage_nl": None,
        "born_nl_heritage_europe": None,
        "born_nl_heritage_outside": None,
        "born_abroad_heritage_europe": None,
        "born_abroad_heritage_outside": None,
    },
    "households": {
        "total": 1020.0,
        "single_person": 460.0,
        "without_children": 310.0,
        "with_children": 250.0,
        "avg_size": 1.9,
        "density_per_km2": 4250.0,
    },
    "housing": {
        "total_stock": 1050.0,
        "non_residential": 30.0,
        "vacant": 2.0,
        "woz_value_k": 670.0,
        "pct_owner": 55.0,
        "pct_rental": 45.0,
        "pct_rental_corp": 28.0,
        "pct_rental_other": 17.0,
        "pct_built_over_10y": 92.0,
        "pct_built_last_10y": 8.0,
        "pct_gas_free": None,
        "pct_gas_heated": None,
        "new_builds": None,
        "new_non_residential": None,
    },
    "housing_type": {
        "pct_single_family": 30.0,
        "pct_terraced": 12.0,
        "pct_corner": 8.0,
        "pct_semi_detached": 5.0,
        "pct_detached": 5.0,
        "pct_apartment": 70.0,
    },
    "energy": {k: None for k in ["avg_electricity_kwh","avg_electricity_return_kwh","avg_gas_m3","pct_district_heating","pct_solar_panels","pct_electric_heating","ev_charge_points"]},
    "education": {k: None for k in ["primary_pupils","secondary_pupils","mbo_students","hbo_students","wo_students","pct_low","pct_mid","pct_high"]},
    "labour": {k: None for k in ["working_population","net_participation_pct","pct_employees","pct_permanent","pct_flex","pct_self_employed"]},
    "income": {k: None for k in ["recipients","avg_per_recipient_k","avg_per_resident_k","pct_lowest_40","pct_highest_20","pct_poverty","pct_near_poverty","avg_standardized_k","pct_hh_lowest_40","pct_hh_highest_20","median_wealth_k"]},
    "benefits": {k: None for k in ["pct_welfare","pct_disability","pct_unemployment","pct_pension"]},
    "care": {k: None for k in ["youth_care_total","pct_youth_care","wmo_total","pct_wmo"]},
    "businesses": {
        "total": 180.0,
        "agriculture": 2.0,
        "industry": 10.0,
        "retail_hosp": 40.0,
        "transport_ict": 15.0,
        "finance_re": 20.0,
        "business_svc": 50.0,
        "gov_edu_health": 30.0,
        "culture_other": 13.0,
    },
    "mobility": {
        "cars_total": 620.0,
        "cars_petrol": 480.0,
        "cars_other_fuel": 140.0,
        "cars_per_household": 0.6,
        "cars_per_km2": 2580.0,
        "motorcycles": 55.0,
    },
    "proximity": {
        "gp_km": 0.4,
        "supermarket_km": 0.3,
        "childcare_km": 0.6,
        "school_km": 0.5,
        "schools_3km": 12.0,
    },
    "area": _make_area(buurt=True),
}

_WIJK = {
    "code": "WK039203",
    "name": "Zijlwegkwartier",
    "gemeente": "Haarlem",
    "population": {
        "total": 8180.0,
        "men": 3940.0,
        "women": 4240.0,
        "age_0_15": 900.0,
        "age_15_25": 970.0,
        "age_25_45": 2900.0,
        "age_45_65": 2320.0,
        "age_65plus": 1090.0,
        "births_total": None,
        "births_rate": None,
        "deaths_total": None,
        "deaths_rate": None,
    },
    "marital": {
        "unmarried": 3900.0,
        "married": 3000.0,
        "divorced": 800.0,
        "widowed": 480.0,
    },
    "heritage": {
        "total_nl": 6500.0,
        "total_europe": 700.0,
        "total_outside_europe": 980.0,
        "born_nl_heritage_nl": None,
        "born_nl_heritage_europe": None,
        "born_nl_heritage_outside": None,
        "born_abroad_heritage_europe": None,
        "born_abroad_heritage_outside": None,
    },
    "households": {
        "total": 4100.0,
        "single_person": 1600.0,
        "without_children": 1300.0,
        "with_children": 1200.0,
        "avg_size": 2.0,
        "density_per_km2": 3800.0,
    },
    "housing": {
        "total_stock": 4200.0,
        "non_residential": 120.0,
        "vacant": 1.8,
        "woz_value_k": 580.0,
        "pct_owner": 48.0,
        "pct_rental": 52.0,
        "pct_rental_corp": 32.0,
        "pct_rental_other": 20.0,
        "pct_built_over_10y": 90.0,
        "pct_built_last_10y": 10.0,
        "pct_gas_free": None,
        "pct_gas_heated": None,
        "new_builds": None,
        "new_non_residential": None,
    },
    "housing_type": {
        "pct_single_family": 28.0,
        "pct_terraced": 10.0,
        "pct_corner": 7.0,
        "pct_semi_detached": 6.0,
        "pct_detached": 5.0,
        "pct_apartment": 72.0,
    },
    "energy": {k: None for k in ["avg_electricity_kwh","avg_electricity_return_kwh","avg_gas_m3","pct_district_heating","pct_solar_panels","pct_electric_heating","ev_charge_points"]},
    "education": {k: None for k in ["primary_pupils","secondary_pupils","mbo_students","hbo_students","wo_students","pct_low","pct_mid","pct_high"]},
    "labour": {k: None for k in ["working_population","net_participation_pct","pct_employees","pct_permanent","pct_flex","pct_self_employed"]},
    "income": {k: None for k in ["recipients","avg_per_recipient_k","avg_per_resident_k","pct_lowest_40","pct_highest_20","pct_poverty","pct_near_poverty","avg_standardized_k","pct_hh_lowest_40","pct_hh_highest_20","median_wealth_k"]},
    "benefits": {k: None for k in ["pct_welfare","pct_disability","pct_unemployment","pct_pension"]},
    "care": {k: None for k in ["youth_care_total","pct_youth_care","wmo_total","pct_wmo"]},
    "businesses": {
        "total": 750.0,
        "agriculture": 8.0,
        "industry": 45.0,
        "retail_hosp": 160.0,
        "transport_ict": 65.0,
        "finance_re": 80.0,
        "business_svc": 210.0,
        "gov_edu_health": 120.0,
        "culture_other": 62.0,
    },
    "mobility": {
        "cars_total": 2500.0,
        "cars_petrol": 1900.0,
        "cars_other_fuel": 600.0,
        "cars_per_household": 0.6,
        "cars_per_km2": 2100.0,
        "motorcycles": 220.0,
    },
    "proximity": {
        "gp_km": 0.5,
        "supermarket_km": 0.4,
        "childcare_km": 0.7,
        "school_km": 0.6,
        "schools_3km": 14.0,
    },
    "area": _make_area(buurt=False),
}

_SAMPLE_CBS = {"buurt": _BUURT, "wijk": _WIJK}


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------

def test_fmt_int_basic():
    assert _fmt_int(1985) == "1.985"

def test_fmt_int_large():
    assert _fmt_int(10000) == "10.000"

def test_fmt_int_none():
    assert _fmt_int(None) is None

def test_fmt_k_eur():
    assert _fmt_k_eur(670) == "€ 670.000"

def test_fmt_k_eur_none():
    assert _fmt_k_eur(None) is None

def test_walk_css_green():
    assert _walk_css(0.4) == "nb-badge-green"

def test_walk_css_green_boundary():
    assert _walk_css(0.5) == "nb-badge-green"

def test_walk_css_amber():
    assert _walk_css(0.8) == "nb-badge-amber"

def test_walk_css_amber_boundary():
    assert _walk_css(1.0) == "nb-badge-amber"

def test_walk_css_red():
    assert _walk_css(1.5) == "nb-badge-red"

def test_walk_css_none():
    assert _walk_css(None) == "nb-badge-grey"

def test_dot_scale_one():
    # 1 filled, 4 empty (full 5-dot scale)
    assert _dot_scale(1) == "● ○ ○ ○ ○"

def test_dot_scale_three():
    # 3 filled, 2 empty
    assert _dot_scale(3) == "● ● ● ○ ○"

def test_dot_scale_five():
    assert _dot_scale(5) == "● ● ● ● ●"

def test_dot_scale_none():
    assert _dot_scale(None) == ""

def test_delta_pct_no_diff():
    assert _delta_pct(100.0, 100.0) is None

def test_delta_pct_none_b():
    assert _delta_pct(None, 100) is None

def test_delta_pct_none_w():
    assert _delta_pct(100, None) is None

def test_delta_pct_positive():
    result = _delta_pct(120.0, 100.0)
    assert result is not None
    assert result.startswith("+")

def test_delta_pct_negative():
    result = _delta_pct(80.0, 100.0)
    assert result is not None
    assert result.startswith("-")


# ---------------------------------------------------------------------------
# build_view — top-level structure
# ---------------------------------------------------------------------------

def test_build_view_returns_all_top_level_keys():
    view = build_view(_SAMPLE_CBS)
    for key in ["glance", "population", "households", "housing", "businesses", "mobility", "proximity", "area", "sparse"]:
        assert key in view, f"Missing key: {key}"

def test_build_view_names():
    view = build_view(_SAMPLE_CBS)
    assert view["buurt_name"] == "Garenkokerskwartier"
    assert view["wijk_name"] == "Zijlwegkwartier"

def test_build_view_postcode():
    view = build_view(_SAMPLE_CBS)
    assert view["postcode"] == "2014"


# ---------------------------------------------------------------------------
# Glance strip
# ---------------------------------------------------------------------------

def test_glance_has_six_items():
    view = build_view(_SAMPLE_CBS)
    assert len(view["glance"]) == 6

def test_glance_first_item_residents():
    view = build_view(_SAMPLE_CBS)
    kpi = view["glance"][0]
    assert kpi["label"] == "Residents"
    assert kpi["value"] == "1.985"

def test_glance_woz_kpi():
    view = build_view(_SAMPLE_CBS)
    woz = next(k for k in view["glance"] if k["label"] == "Avg. WOZ")
    assert woz["value"] == "€ 670k"


# ---------------------------------------------------------------------------
# Population section
# ---------------------------------------------------------------------------

def test_population_residents():
    view = build_view(_SAMPLE_CBS)
    assert view["population"]["residents_b"] == "1.985"
    assert view["population"]["residents_w"] == "8.180"

def test_population_age_segs_count():
    view = build_view(_SAMPLE_CBS)
    assert len(view["population"]["age_segs"]) == 5

def test_population_age_segs_pct_sum():
    view = build_view(_SAMPLE_CBS)
    total = sum(s["pct_b"] for s in view["population"]["age_segs"])
    assert abs(total - 100.0) <= 1.0

def test_population_age_seg_keys():
    view = build_view(_SAMPLE_CBS)
    for seg in view["population"]["age_segs"]:
        for key in ["label", "pct_b", "pct_w", "color"]:
            assert key in seg, f"Age seg missing key: {key}"


# ---------------------------------------------------------------------------
# Housing section
# ---------------------------------------------------------------------------

def test_housing_woz():
    view = build_view(_SAMPLE_CBS)
    assert view["housing"]["woz_b"] == "€ 670.000"

def test_housing_woz_delta_positive():
    # buurt WOZ (670k) > wijk WOZ (580k), so delta should be positive
    view = build_view(_SAMPLE_CBS)
    assert view["housing"]["woz_d"] is not None
    assert view["housing"]["woz_d"].startswith("+")

def test_housing_tenure_segs_count():
    view = build_view(_SAMPLE_CBS)
    assert len(view["housing"]["tenure_segs"]) == 3

def test_housing_tenure_segs_pct_sum():
    view = build_view(_SAMPLE_CBS)
    total = sum(s["pct_b"] for s in view["housing"]["tenure_segs"])
    assert abs(total - 100.0) <= 1.0


# ---------------------------------------------------------------------------
# Proximity section
# ---------------------------------------------------------------------------

def test_proximity_badges_count():
    view = build_view(_SAMPLE_CBS)
    assert len(view["proximity"]["badges"]) == 4

def test_proximity_gp_badge_green():
    view = build_view(_SAMPLE_CBS)
    gp = next(b for b in view["proximity"]["badges"] if b["label"] == "GP")
    assert gp["km"] == 0.4
    assert gp["css"] == "nb-badge-green"


# ---------------------------------------------------------------------------
# Area section
# ---------------------------------------------------------------------------

def test_area_urb_dots():
    view = build_view(_SAMPLE_CBS)
    # urbanisation=1 → 1 filled dot, 4 empty (full 5-dot scale)
    assert view["area"]["urb_dots_b"] == "● ○ ○ ○ ○"

def test_area_urb_label():
    view = build_view(_SAMPLE_CBS)
    assert view["area"]["urb_label_b"] == "Very urban"


# ---------------------------------------------------------------------------
# Sparse section
# ---------------------------------------------------------------------------

def test_sparse_has_any_false_when_all_none():
    view = build_view(_SAMPLE_CBS)
    # All energy/edu/labour/income/benefits/care are None in sample data
    assert view["sparse"]["has_any"] is False

def test_sparse_energy_has_false():
    view = build_view(_SAMPLE_CBS)
    assert view["sparse"]["energy"]["has"] is False

def test_sparse_education_has_false():
    view = build_view(_SAMPLE_CBS)
    assert view["sparse"]["education"]["has"] is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_build_view_empty_dict_does_not_crash():
    view = build_view({})
    assert isinstance(view, dict)
    assert "glance" in view
    assert view["glance"] == []

def test_build_view_empty_dict_sparse_has_any_false():
    view = build_view({})
    assert view["sparse"]["has_any"] is False

def test_build_view_partial_data():
    """Only buurt with partial population, no wijk — should not crash."""
    partial = {
        "buurt": {
            "name": "TestBuurt",
            "gemeente": "TestGem",
            "population": {"total": 500.0},
            "area": {},
        }
    }
    view = build_view(partial)
    assert view["buurt_name"] == "TestBuurt"
    assert view["population"]["residents_b"] == "500"

def test_build_view_sparse_has_any_true_when_energy_present():
    """When energy data is present, sparse.has_any should be True."""
    cbs_with_energy = {
        "buurt": {
            **_BUURT,
            "energy": {
                **_BUURT["energy"],
                "avg_electricity_kwh": 3000.0,
            },
        },
        "wijk": _WIJK,
    }
    view = build_view(cbs_with_energy)
    assert view["sparse"]["has_any"] is True
    assert view["sparse"]["energy"]["has"] is True
    assert view["sparse"]["energy"]["elec_b"] == 3000.0
