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

# Display label + short description for each feature
_FEATURE_LABELS: dict[str, str] = {
    "log_area":     "Living area",
    "log_plot":     "Plot / garden area",
    "has_plot":     "Garden presence",
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
        desc = "has garden / plot" if raw_val and raw_val > 0.5 else "no garden / plot"
        return f"{sign}{delta_pct:.1f}% — {desc}"
    return f"{sign}{delta_pct:.1f}% — vs. comparable average"


def build_explanation(
    model: FittedModel,
    subject: dict,
    cohort: "CompCohort",  # noqa: F821
    recommended: int,
) -> list[dict]:
    """Return [{label, delta_pct, note}] for the rationale modal."""
    items: list[dict] = []
    n_active = len(getattr(cohort, "active", []))
    n_sold = len(getattr(cohort, "sold", []))
    total_n = n_active + n_sold

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
