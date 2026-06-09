"""Pure numeric model for bid estimation.

Weighted log-linear OLS (with ridge regularisation for small cohorts).
No async I/O. Fully unit-testable without a DB session.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import numpy as np

CURRENT_MODEL_VERSION = "v2.0"

_ENERGY_ORDER = ["A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"]
# NOTE: has_plot was removed — it was collinear with log_plot (which has a +50
# floor) and is_apartment, which made the regression flip its sign and report a
# negative "garden presence" effect for houses that have a garden.
_FEATURE_ORDER = ["log_area", "log_plot", "energy_rank", "year_built", "is_apartment"]

_N_EFF_OLS = 12.0      # pure OLS above this
_N_EFF_RIDGE = 5.0     # ridge above this, median-fallback below
_BAND_Z_NORMAL = 1.28  # 80% prediction interval
_BAND_Z_LOW = 1.65     # 90% prediction interval for small N
_BAND_MIN = 0.03
_BAND_MAX = 0.15


def _energy_rank(label: str | None) -> int:
    if not label:
        return 5
    try:
        return _ENERGY_ORDER.index(label)
    except ValueError:
        return 5


def _fmt_eur(amount: int | None) -> str:
    if not amount:
        return "—"
    return f"€ {amount:,}".replace(",", ".")


def _extract_features(row: dict) -> dict[str, float | None]:
    area = row.get("living_area")
    year_raw = row.get("construction_year")
    obj = str(row.get("object_type") or "").lower()
    is_apartment = "apartment" in obj

    # A missing plot_area on a non-apartment means "unknown", not "no garden".
    # Treating it as 0 wrongly dragged houses down to the apartment baseline, so
    # impute it (None → cohort mean). Apartments legitimately have no plot.
    plot_raw = row.get("plot_area")
    if plot_raw is None and not is_apartment:
        log_plot = None
    else:
        log_plot = math.log((plot_raw or 0) + 50)   # +50 floor: apartment → log(50)≈3.9

    # Only score energy when the label is a known grade. Missing/unknown labels
    # are imputed (None → cohort mean) rather than silently treated as "C",
    # which used to skew the cohort average.
    label = row.get("energy_label")
    energy_rank = float(_energy_rank(label)) if (label and label in _ENERGY_ORDER) else None

    return {
        "log_area": math.log(area) if area else None,
        "log_plot": log_plot,
        "energy_rank": energy_rank,
        "year_built": (int(year_raw) - 1970) / 50 if year_raw is not None else None,
        "is_apartment": 1.0 if is_apartment else 0.0,
    }


@dataclass(frozen=True)
class FittedModel:
    coef: dict[str, float]           # {feature_name: beta} in standardised scale
    intercept: float                 # log-price intercept
    feature_means: dict[str, float]  # cohort means for z-scoring subject
    feature_stds: dict[str, float]   # cohort stds for z-scoring subject
    feature_used: list[str]          # non-constant features that entered the fit
    n_eff: float                     # sum of sample weights
    r2: float
    residual_std: float              # residual std in log-price space
    ridge_lambda: float              # 0.0 = pure OLS
    fallback: bool                   # True → median-ppm path (sparse cohort)
    median_ppm: float | None         # always computed; used in fallback + display


def fit(rows: list[dict], weights: list[float]) -> FittedModel:
    """Fit a weighted log-linear model on a list of listing dicts."""
    valid = [
        (r, w) for r, w in zip(rows, weights)
        if r.get("living_area") and r.get("price_amount")
    ]

    ppms = [r["price_amount"] / r["living_area"] for r, _ in valid]
    median_ppm = statistics.median(ppms) if ppms else None
    n_eff = sum(w for _, w in valid)

    if n_eff < _N_EFF_RIDGE:
        return FittedModel(
            coef={}, intercept=0.0,
            feature_means={}, feature_stds={},
            feature_used=[], n_eff=n_eff, r2=0.0,
            residual_std=0.10, ridge_lambda=0.0,
            fallback=True, median_ppm=median_ppm,
        )

    feats = [_extract_features(r) for r, _ in valid]
    log_prices = [math.log(r["price_amount"]) for r, _ in valid]
    ws = [w for _, w in valid]
    total_w = sum(ws)

    # Weighted mean + std per feature (skip rows where value is None)
    means: dict[str, float] = {}
    stds: dict[str, float] = {}
    for fname in _FEATURE_ORDER:
        paired = [(f[fname], ws[i]) for i, f in enumerate(feats) if f.get(fname) is not None]
        if not paired:
            means[fname] = 0.0
            stds[fname] = 0.0
            continue
        tw = sum(pw for _, pw in paired)
        wm = sum(v * w for v, w in paired) / tw
        wvar = sum(w * (v - wm) ** 2 for v, w in paired) / tw
        means[fname] = wm
        stds[fname] = math.sqrt(wvar) if wvar > 1e-10 else 0.0

    # Drop constant features (std ≈ 0); they can't be estimated
    feature_used = [f for f in _FEATURE_ORDER if stds[f] > 1e-8]
    n_features = len(feature_used)

    # Build design matrix: z-score each feature; impute missing with cohort mean (→ z=0)
    X_rows = []
    for f in feats:
        X_rows.append([
            ((f[fname] if f[fname] is not None else means[fname]) - means[fname]) / stds[fname]
            for fname in feature_used
        ])

    X = np.array(X_rows, dtype=float)           # (n, n_features)
    y = np.array(log_prices, dtype=float)
    w = np.array(ws, dtype=float)

    X_aug = np.hstack([np.ones((len(X_rows), 1)), X])   # prepend intercept column
    XTWX = X_aug.T @ (w[:, None] * X_aug)
    XTWy = X_aug.T @ (w * y)

    ridge_lambda = 0.0
    if n_eff < _N_EFF_OLS:
        ridge_lambda = 0.5 * float(np.trace(XTWX)) / max(n_features, 1)
        reg = ridge_lambda * np.eye(n_features + 1)
        reg[0, 0] = 0.0   # don't penalise intercept
        XTWX = XTWX + reg

    try:
        beta = np.linalg.solve(XTWX, XTWy)
    except np.linalg.LinAlgError:
        beta, _, _, _ = np.linalg.lstsq(X_aug, y, rcond=None)

    intercept_val = float(beta[0])
    coef = {fname: float(beta[i + 1]) for i, fname in enumerate(feature_used)}

    y_hat = X_aug @ beta
    residuals = y - y_hat
    ss_res = float(np.dot(w, residuals ** 2))
    y_wm = float(np.dot(w, y) / total_w)
    ss_tot = float(np.dot(w, (y - y_wm) ** 2))
    r2 = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 1e-10 else 0.0
    # Degrees of freedom: n_eff - n_features - 1 (intercept)
    dof = max(n_eff - n_features - 1, 1.0)
    residual_std = math.sqrt(ss_res / dof)

    return FittedModel(
        coef=coef, intercept=intercept_val,
        feature_means=means, feature_stds=stds,
        feature_used=feature_used, n_eff=n_eff, r2=r2,
        residual_std=residual_std, ridge_lambda=ridge_lambda,
        fallback=False, median_ppm=median_ppm,
    )


def predict(model: FittedModel, subject: dict) -> tuple[int, int, int]:
    """Return (low_eur, recommended_eur, high_eur). Returns (0,0,0) on failure."""
    area = subject.get("living_area")
    if not area:
        return 0, 0, 0

    if model.fallback:
        if not model.median_ppm:
            return 0, 0, 0
        recommended = round(model.median_ppm * area / 100) * 100
        low = round(recommended * (1 - _BAND_MAX) / 100) * 100
        high = round(recommended * (1 + _BAND_MAX) / 100) * 100
        return int(low), int(recommended), int(high)

    subj_feats = _extract_features(subject)
    log_price = model.intercept
    for fname in model.feature_used:
        val = subj_feats.get(fname)
        if val is None:
            val = model.feature_means.get(fname, 0.0)
        std = model.feature_stds.get(fname, 1.0)
        z = (val - model.feature_means[fname]) / std if std > 1e-8 else 0.0
        log_price += model.coef[fname] * z

    recommended = round(math.exp(log_price) / 100) * 100
    if recommended <= 0:
        return 0, 0, 0

    z = _BAND_Z_NORMAL if model.n_eff >= _N_EFF_OLS else _BAND_Z_LOW
    band = max(_BAND_MIN, min(_BAND_MAX, math.exp(z * model.residual_std) - 1))
    low = round(recommended * (1 - band) / 100) * 100
    high = round(recommended * (1 + band) / 100) * 100
    return int(low), int(recommended), int(high)


def confidence_level(model: FittedModel) -> str:
    if model.fallback:
        return "low"
    return "normal" if model.n_eff >= _N_EFF_OLS else "low"
