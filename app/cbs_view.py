"""View-model for CBS neighbourhood statistics display."""
from __future__ import annotations


def _fmt_int(v) -> str | None:
    if v is None: return None
    return f"{int(v):,}".replace(",", ".")

def _fmt_k_eur(v) -> str | None:
    if v is None: return None
    return f"€ {int(v * 1000):,}".replace(",", ".")

def _delta_pct(b, w) -> str | None:
    if b is None or w is None or w == 0: return None
    diff = (b - w) / abs(w) * 100
    if abs(diff) < 2: return None
    return f"+{diff:.0f}%" if diff > 0 else f"{diff:.0f}%"

def _delta_abs(b, w, unit="") -> str | None:
    if b is None or w is None: return None
    diff = b - w
    if abs(diff) < 0.05: return None
    s = f"+{diff:.1f}" if diff > 0 else f"{diff:.1f}"
    return (s + unit).rstrip("0").rstrip(".")

def _pct(n, d) -> float | None:
    if n is None or not d: return None
    return round(n / d * 100, 1)

def _walk_css(km) -> str:
    if km is None: return "nb-badge-grey"
    if km <= 0.5: return "nb-badge-green"
    if km <= 1.0: return "nb-badge-amber"
    return "nb-badge-red"

def _dot_scale(v, n=5) -> str:
    if v is None: return ""
    f = int(v)
    return ("● " * f + "○ " * (n - f)).rstrip()

def _urb_label(v) -> str:
    return {1:"Very urban",2:"Urban",3:"Moderately urban",4:"Low urban",5:"Rural"}.get(int(v),"") if v else ""

def _pct_segments(values_b, values_w, labels, colors) -> list[dict]:
    """Build segment list with pct_b (for bar width) and pct_w (for legend)."""
    total_b = sum(x for x in values_b if x is not None) or 1
    total_w = sum(x for x in values_w if x is not None) or 1
    segs = []
    for label, vb, vw, color in zip(labels, values_b, values_w, colors):
        pct_b = round(vb / total_b * 100, 1) if vb is not None else None
        pct_w = round(vw / total_w * 100, 1) if vw is not None else None
        if pct_b is not None or pct_w is not None:
            segs.append({"label": label, "pct_b": pct_b or 0, "pct_w": pct_w, "color": color})
    return segs


def build_view(cbs: dict) -> dict:
    b  = cbs.get("buurt", {})
    w  = cbs.get("wijk",  {})
    bp = b.get("population",  {}); wp = w.get("population",  {})
    bh = b.get("households",  {}); wh = w.get("households",  {})
    bho= b.get("housing",     {}); who= w.get("housing",     {})
    bht= b.get("housing_type",{}); wht= w.get("housing_type",{})
    bma= b.get("marital",     {}); wma= w.get("marital",     {})
    bhe= b.get("heritage",    {}); whe= w.get("heritage",    {})
    bbu= b.get("businesses",  {}); wbu= w.get("businesses",  {})
    bmo= b.get("mobility",    {}); wmo= w.get("mobility",    {})
    bpr= b.get("proximity",   {}); wpr= w.get("proximity",   {})
    bar= b.get("area",        {}); war= w.get("area",        {})
    ben= b.get("energy",      {}); wen= w.get("energy",      {})
    bed= b.get("education",   {}); wed= w.get("education",   {})
    bla= b.get("labour",      {}); wla= w.get("labour",      {})
    bin= b.get("income",      {}); win= w.get("income",      {})
    bbe= b.get("benefits",    {}); wbe= w.get("benefits",    {})
    bca= b.get("care",        {}); wca= w.get("care",        {})

    # Segments
    age_segs = _pct_segments(
        [bp.get("age_0_15"), bp.get("age_15_25"), bp.get("age_25_45"), bp.get("age_45_65"), bp.get("age_65plus")],
        [wp.get("age_0_15"), wp.get("age_15_25"), wp.get("age_25_45"), wp.get("age_45_65"), wp.get("age_65plus")],
        ["0–15", "15–25", "25–45", "45–65", "65+"], ["c-age0","c-age1","c-age2","c-age3","c-age4"]
    )
    mar_segs = _pct_segments(
        [bma.get("unmarried"), bma.get("married"), bma.get("divorced"), bma.get("widowed")],
        [wma.get("unmarried"), wma.get("married"), wma.get("divorced"), wma.get("widowed")],
        ["Unmarried","Married","Divorced","Widowed"], ["c-m0","c-m1","c-m2","c-m3"]
    )
    her_segs = _pct_segments(
        [bhe.get("total_nl"), bhe.get("total_europe"), bhe.get("total_outside_europe")],
        [whe.get("total_nl"), whe.get("total_europe"), whe.get("total_outside_europe")],
        ["Netherlands","Europe (excl. NL)","Outside Europe"], ["c-h0","c-h1","c-h2"]
    )
    tenure_segs = _pct_segments(
        [bho.get("pct_owner"), bho.get("pct_rental_corp"), bho.get("pct_rental_other")],
        [who.get("pct_owner"), who.get("pct_rental_corp"), who.get("pct_rental_other")],
        ["Owner-occ.","Corp. rental","Other rental"], ["c-t0","c-t1","c-t2"]
    )
    type_segs = _pct_segments(
        [bht.get("pct_terraced"), bht.get("pct_corner"), bht.get("pct_semi_detached"), bht.get("pct_detached"), bht.get("pct_apartment")],
        [wht.get("pct_terraced"), wht.get("pct_corner"), wht.get("pct_semi_detached"), wht.get("pct_detached"), wht.get("pct_apartment")],
        ["Terraced","Corner","Semi-det.","Detached","Apartment"], ["c-ht0","c-ht1","c-ht2","c-ht3","c-ht4"]
    )
    biz_segs = _pct_segments(
        [bbu.get("agriculture"), bbu.get("industry"), bbu.get("retail_hosp"), bbu.get("transport_ict"), bbu.get("finance_re"), bbu.get("business_svc"), bbu.get("gov_edu_health"), bbu.get("culture_other")],
        [wbu.get("agriculture"), wbu.get("industry"), wbu.get("retail_hosp"), wbu.get("transport_ict"), wbu.get("finance_re"), wbu.get("business_svc"), wbu.get("gov_edu_health"), wbu.get("culture_other")],
        ["Agriculture","Industry","Retail/hosp.","Transport/ICT","Finance/RE","Business svc","Gov/Edu/Health","Culture/other"], ["c-b0","c-b1","c-b2","c-b3","c-b4","c-b5","c-b6","c-b7"]
    )
    fuel_segs = _pct_segments(
        [bmo.get("cars_petrol"), bmo.get("cars_other_fuel")],
        [wmo.get("cars_petrol"), wmo.get("cars_other_fuel")],
        ["Petrol","Other (incl. EV)"], ["c-f0","c-f1"]
    )

    bt  = bp.get("total"); wt  = wp.get("total")
    bwoz= bho.get("woz_value_k"); wwoz= who.get("woz_value_k")
    bown= bho.get("pct_owner");   wown= who.get("pct_owner")
    bhsz= bh.get("avg_size");     whsz= wh.get("avg_size")
    burb= bar.get("urbanisation");warb= war.get("urbanisation")
    badn= bar.get("address_density"); wadn= war.get("address_density")

    glance = []
    if bt   is not None: glance.append({"label":"Residents",   "value":_fmt_int(bt),          "sub":f"wijk {_fmt_int(wt)}" if wt else None,          "delta":_delta_pct(bt,wt)})
    if bwoz is not None: glance.append({"label":"Avg. WOZ",    "value":f"€ {int(bwoz)}k",     "sub":f"wijk € {int(wwoz)}k" if wwoz else None,         "delta":_delta_pct(bwoz,wwoz)})
    if bown is not None: glance.append({"label":"Owner-occ.",  "value":f"{int(bown)}%",        "sub":f"wijk {int(wown)}%" if wown is not None else None,"delta":_delta_abs(bown,wown,"pp")})
    if bhsz is not None: glance.append({"label":"Avg. household","value":f"{bhsz}p",          "sub":f"wijk {whsz}p" if whsz else None,                "delta":_delta_abs(bhsz,whsz,"p")})
    if burb is not None: glance.append({"label":"Urbanisation","value":_dot_scale(burb),       "sub":_urb_label(burb),                                  "delta":None})
    if badn is not None: glance.append({"label":"Addr./km²",   "value":_fmt_int(badn),         "sub":f"wijk {_fmt_int(wadn)}" if wadn else None,        "delta":None})

    def _any(*vs): return any(v is not None for v in vs)
    has_energy   = _any(ben.get("avg_electricity_kwh"), ben.get("avg_gas_m3"), ben.get("pct_solar_panels"))
    has_edu      = _any(bed.get("pct_low"), bed.get("primary_pupils"))
    has_labour   = _any(bla.get("working_population"), bla.get("net_participation_pct"))
    has_income   = _any(bin.get("avg_per_resident_k"), bin.get("pct_poverty"))
    has_benefits = _any(bbe.get("pct_welfare"), bbe.get("pct_disability"))
    has_care     = _any(bca.get("youth_care_total"), bca.get("wmo_total"))

    return {
        "buurt_name": b.get("name",""), "wijk_name": w.get("name",""),
        "gemeente":   b.get("gemeente",""), "postcode": bar.get("postcode"),
        "glance": glance,
        "population": {
            "residents_b": _fmt_int(bt),   "residents_w": _fmt_int(wt),   "residents_d": _delta_pct(bt,wt),
            "age_segs": age_segs, "mar_segs": mar_segs, "her_segs": her_segs,
            "births_b": _fmt_int(bp.get("births_total")), "births_w": _fmt_int(wp.get("births_total")),
            "deaths_b": _fmt_int(bp.get("deaths_total")), "deaths_w": _fmt_int(wp.get("deaths_total")),
        },
        "households": {
            "total_b": _fmt_int(bh.get("total")), "total_w": _fmt_int(wh.get("total")),
            "total_d": _delta_pct(bh.get("total"), wh.get("total")),
            "single_pct_b": _pct(bh.get("single_person"), bh.get("total")),
            "single_pct_w": _pct(wh.get("single_person"), wh.get("total")),
            "children_pct_b": _pct(bh.get("with_children"), bh.get("total")),
            "children_pct_w": _pct(wh.get("with_children"), wh.get("total")),
            "avg_size_b": str(bhsz) if bhsz else None, "avg_size_w": str(whsz) if whsz else None,
            "density_b": _fmt_int(bh.get("density_per_km2")), "density_w": _fmt_int(wh.get("density_per_km2")),
        },
        "housing": {
            "woz_b": _fmt_k_eur(bwoz), "woz_w": _fmt_k_eur(wwoz), "woz_d": _delta_pct(bwoz,wwoz),
            "stock_b": _fmt_int(bho.get("total_stock")), "stock_w": _fmt_int(who.get("total_stock")),
            "vacant_b": bho.get("vacant"), "vacant_w": who.get("vacant"),
            "built_old_b": bho.get("pct_built_over_10y"), "built_old_w": who.get("pct_built_over_10y"),
            "gas_free_b": bho.get("pct_gas_free"), "gas_free_w": who.get("pct_gas_free"),
            "tenure_segs": tenure_segs, "type_segs": type_segs,
        },
        "businesses": {
            "total_b": _fmt_int(bbu.get("total")), "total_w": _fmt_int(wbu.get("total")),
            "total_d": _delta_pct(bbu.get("total"), wbu.get("total")), "segs": biz_segs,
        },
        "mobility": {
            "cars_hh_b": str(bmo.get("cars_per_household")) if bmo.get("cars_per_household") is not None else None,
            "cars_hh_w": str(wmo.get("cars_per_household")) if wmo.get("cars_per_household") is not None else None,
            "cars_hh_d": _delta_abs(bmo.get("cars_per_household"), wmo.get("cars_per_household")),
            "cars_total_b": _fmt_int(bmo.get("cars_total")), "cars_total_w": _fmt_int(wmo.get("cars_total")),
            "motorcycles_b": _fmt_int(bmo.get("motorcycles")), "motorcycles_w": _fmt_int(wmo.get("motorcycles")),
            "fuel_segs": fuel_segs,
        },
        "proximity": {
            "badges": [
                {"label":"GP",          "km":bpr.get("gp_km"),          "css":_walk_css(bpr.get("gp_km"))},
                {"label":"Supermarket", "km":bpr.get("supermarket_km"), "css":_walk_css(bpr.get("supermarket_km"))},
                {"label":"Childcare",   "km":bpr.get("childcare_km"),   "css":_walk_css(bpr.get("childcare_km"))},
                {"label":"School",      "km":bpr.get("school_km"),       "css":_walk_css(bpr.get("school_km"))},
            ],
            "schools_3km": bpr.get("schools_3km"),
        },
        "area": {
            "land_ha": bar.get("land_ha"), "water_ha": bar.get("water_ha"),
            "urb_dots_b": _dot_scale(burb), "urb_label_b": _urb_label(burb),
            "urb_dots_w": _dot_scale(warb), "urb_label_w": _urb_label(warb),
            "addr_den_b": _fmt_int(badn), "addr_den_w": _fmt_int(wadn),
            "coverage_b": bar.get("coverage_pct"),
        },
        "sparse": {
            "has_any": any([has_energy,has_edu,has_labour,has_income,has_benefits,has_care]),
            "energy":   {"has":has_energy,   "elec_b":ben.get("avg_electricity_kwh"), "gas_b":ben.get("avg_gas_m3"), "solar_b":ben.get("pct_solar_panels"), "ev_b":ben.get("ev_charge_points"), "elec_w":wen.get("avg_electricity_kwh"), "gas_w":wen.get("avg_gas_m3")},
            "education":{"has":has_edu,      "pct_low_b":bed.get("pct_low"), "pct_mid_b":bed.get("pct_mid"), "pct_high_b":bed.get("pct_high"), "pct_low_w":wed.get("pct_low"), "pct_mid_w":wed.get("pct_mid"), "pct_high_w":wed.get("pct_high")},
            "labour":   {"has":has_labour,   "participation_b":bla.get("net_participation_pct"), "pct_flex_b":bla.get("pct_flex"), "pct_selfempl_b":bla.get("pct_self_employed"), "participation_w":wla.get("net_participation_pct"), "pct_flex_w":wla.get("pct_flex"), "pct_selfempl_w":wla.get("pct_self_employed")},
            "income":   {"has":has_income,   "avg_b":_fmt_k_eur(bin.get("avg_per_resident_k")), "poverty_b":bin.get("pct_poverty"), "wealth_b":_fmt_k_eur(bin.get("median_wealth_k")), "avg_w":_fmt_k_eur(win.get("avg_per_resident_k")), "poverty_w":win.get("pct_poverty")},
            "benefits": {"has":has_benefits, "welfare_b":bbe.get("pct_welfare"), "disability_b":bbe.get("pct_disability"), "unemployment_b":bbe.get("pct_unemployment"), "welfare_w":wbe.get("pct_welfare"), "disability_w":wbe.get("pct_disability"), "unemployment_w":wbe.get("pct_unemployment")},
            "care":     {"has":has_care,     "youth_b":bca.get("pct_youth_care"), "wmo_b":bca.get("pct_wmo"), "youth_w":wca.get("pct_youth_care"), "wmo_w":wca.get("pct_wmo")},
        },
    }
