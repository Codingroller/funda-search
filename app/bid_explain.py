"""Human-readable explanation of a bid estimate for the rationale modal.

Converts FittedModel partial effects into the {label, delta_pct, note}
list that bid_estimate_rationale.html iterates over.
"""
from __future__ import annotations

import math

from app.bid_model import (
    FittedModel,
    _ENERGY_ORDER,
    _extract_features,
    _fmt_eur,
)

# Condition-gauge positions (must match the cond-zone buttons in the templates).
_CONDITION_LABELS = {"low": "Needs work", "mid": "Average", "high": "Good"}
_CONDITION_POS = {"low": "lower end", "mid": "middle", "high": "upper end"}


def condition_view(estimate: dict, cond: str) -> dict:
    """Map a condition-gauge position to the value + label the rationale shows.

    The condition gauge repositions the headline within the low–high band
    (needs-work → low, average → recommended, good → high). The rationale modal
    is fetched per-open with the active ``cond`` so its header and condition line
    reflect the number actually displayed on the card.
    """
    cond = cond if cond in _CONDITION_LABELS else "mid"
    value_fmt = {
        "low": estimate.get("low_fmt"),
        "mid": estimate.get("recommended_fmt"),
        "high": estimate.get("high_fmt"),
    }[cond]
    return {
        "cond": cond,
        "cond_label": _CONDITION_LABELS[cond],
        "cond_pos": _CONDITION_POS[cond],
        "sel_value_fmt": value_fmt,
    }


# Display label + short description for each feature
_FEATURE_LABELS: dict[str, str] = {
    "log_area":     "Living area",
    "log_plot":     "Plot / garden area",
    "has_plot":     "Garden premium",
    "energy_rank":  "Energy label",
    "year_built":   "Construction year",
    "is_apartment": "Property type",
}


def _feature_note(fname: str, raw_val: float | None, mean: float, delta_pct: float) -> str:
    sign = "+" if delta_pct > 0 else ""
    if fname == "energy_rank" and raw_val is not None:
        rank = int(round(raw_val))
        label = _ENERGY_ORDER[rank] if 0 <= rank < len(_ENERGY_ORDER) else "?"
        return f"{sign}{delta_pct:.1f}% — energy label {label} vs. comparable average"
    if fname == "year_built" and raw_val is not None:
        year = int(raw_val * 50 + 1970)
        return f"{sign}{delta_pct:.1f}% — built {year} vs. comparable average"
    if fname == "log_area":
        return f"{sign}{delta_pct:.1f}% — living area relative to comparable average"
    if fname == "log_plot":
        return f"{sign}{delta_pct:.1f}% — plot size relative to comparable average"
    if fname == "has_plot":
        desc = "has a garden / plot" if raw_val and raw_val > 0.5 else "no garden / plot"
        return f"{sign}{delta_pct:.1f}% — {desc}"
    return f"{sign}{delta_pct:.1f}% — vs. comparable average"


def _overbid_item(overbid: float, hot: dict | None = None) -> dict | None:
    """The competitive over-asking uplift, shown as its own rationale line.

    The uplift is market-hotness aware (bid_comps.market_overbid): it scales with
    the local sell-through rate, so the note explains *why* this much was added.
    """
    pct = overbid * 100

    # Describe the local market when we have a hotness read.
    context = ""
    if hot:
        n_sold = hot.get("n_recent_sold")
        n_active = hot.get("n_active")
        if hot.get("thin"):
            context = " — too few recent local sales to gauge the market, so a neutral uplift is used"
        elif n_sold is not None and n_active is not None:
            st = hot.get("sell_through")
            heat = "hot" if (st or 0) >= 0.55 else ("balanced" if (st or 0) >= 0.4 else "cool")
            context = (
                f" — local market looks {heat} "
                f"({n_sold} recently sold vs {n_active} still for sale)"
            )

    if not overbid or overbid <= 0:
        # Cold market: no uplift. Still surface the reasoning when we know it.
        if not hot:
            return None
        return {
            "label": "Competitive bid uplift",
            "delta_pct": 0,
            "note": f"No uplift — bidding at fitted fair value{context}",
        }

    return {
        "label": "Competitive bid uplift",
        "delta_pct": round(pct, 1),
        "note": (
            f"+{pct:.1f}% over fitted fair value — comparables reflect asking prices, "
            f"but homes here sell above asking, so the bid is sized to win{context}"
        ),
    }


def build_explanation(
    model: FittedModel,
    subject: dict,
    cohort: "CompCohort",  # noqa: F821
    recommended: int,
    overbid: float = 0.0,
    hot: dict | None = None,
) -> list[dict]:
    """Return [{label, delta_pct, note}] for the rationale modal."""
    items: list[dict] = []
    n_active = len(getattr(cohort, "active", []))
    n_sold = len(getattr(cohort, "sold", []))
    total_n = n_active + n_sold
    overbid_item = _overbid_item(overbid, hot)

    if model.fallback:
        ppm_note = f" at {_fmt_eur(int(model.median_ppm))}/m²" if model.median_ppm else ""
        items.append({
            "label": "Estimate basis",
            "delta_pct": 0,
            "note": (
                f"Median comparable price × living area{ppm_note} "
                f"({total_n} listing{'s' if total_n != 1 else ''} found — sparse cohort)"
            ),
        })
        if overbid_item:
            items.append(overbid_item)
        _add_context(items, model, cohort, total_n)
        return items

    subj_feats = _extract_features(subject)
    threshold_eur = recommended * 0.005  # hide effects smaller than 0.5% of recommended

    for fname in model.feature_used:
        coef = model.coef.get(fname, 0.0)
        mean = model.feature_means.get(fname, 0.0)
        std = model.feature_stds.get(fname, 1.0)
        raw_val = subj_feats.get(fname)
        if raw_val is None:
            continue   # imputed → no deviation from cohort mean → zero effect
        z = (raw_val - mean) / std if std > 1e-8 else 0.0
        delta_log = coef * z
        delta_pct = round((math.exp(delta_log) - 1) * 100, 1)
        delta_eur = recommended * (math.exp(delta_log) - 1)

        if abs(delta_eur) < threshold_eur:
            continue

        items.append({
            "label": _FEATURE_LABELS.get(fname, fname),
            "delta_pct": delta_pct,
            "note": _feature_note(fname, raw_val, mean, delta_pct),
        })

    # Largest effects first
    items.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)

    if overbid_item:
        items.append(overbid_item)

    _add_context(items, model, cohort, total_n)
    return items


def _add_context(items: list[dict], model: FittedModel, cohort: "CompCohort", total_n: int) -> None:
    n_sold = len(getattr(cohort, "sold", []))

    # Cohort summary
    parts = [f"{total_n} comparable listing{'s' if total_n != 1 else ''}"]
    if n_sold > 0:
        parts.append(f"{n_sold} recently sold")
    if model.median_ppm:
        parts.append(f"median {_fmt_eur(int(model.median_ppm))}/m²")
    items.append({"label": "Cohort", "delta_pct": 0, "note": " · ".join(parts)})

    # Model quality (only for non-fallback)
    if not model.fallback:
        r2_pct = round(model.r2 * 100)
        spread_pct = round((math.exp(model.residual_std) - 1) * 100)
        items.append({
            "label": "Model quality",
            "delta_pct": 0,
            "note": f"Cohort R² {r2_pct}% · typical spread ±{spread_pct}%",
        })

    # Scope
    tier = getattr(cohort, "tier", "")
    if tier and tier != "unavailable":
        items.append({"label": "Scope", "delta_pct": 0, "note": tier})
