"""Pure numeric model for bid estimation.

Weighted log-linear OLS (with ridge regularisation for small cohorts).
No async I/O. Fully unit-testable without a DB session.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import numpy as np

CURRENT_MODEL_VERSION = "v2.1"

_ENERGY_ORDER = ["A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"]
# has_plot is collinear with log_plot (+50 floor) and is_apartment, so the
# unconstrained regression could hand it a negative coefficient and report a
# backwards "garden lowers price" effect. fit() guards against that: if the
# fitted has_plot coefficient is negative it is dropped and the model refit, so
# a garden can only ever add value (or be neutral), never subtract.
_FEATURE_ORDER = ["log_area", "log_plot", "has_plot", "energy_rank", "year_built", "is_apartment"]
_NON_NEGATIVE_FEATURES = ("has_plot",)   # coefficients constrained to ≥ 0

_N_EFF_OLS = 12.0      # pure OLS above this
_N_EFF_RIDGE = 5.0     # ridge above this, median-fallback below
_BAND_Z_NORMAL = 1.28  # 80% prediction interval
_BAND_Z_LOW = 1.65     # 90% prediction interval for small N
_BAND_MIN = 0.03
_BAND_MAX = 0.15
_SMEARING_MAX = 1.20   # cap the retransformation factor against heavy-tailed cohorts


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
        log_plot = None    # unknown house plot → impute to cohort mean
        has_plot = None
    else:
        log_plot = math.log((plot_raw or 0) + 50)   # +50 floor: apartment → log(50)≈3.9
        has_plot = 1.0 if (plot_raw or 0) > 0 else 0.0

    # Only score energy when the label is a known grade. Missing/unknown labels
    # are imputed (None → cohort mean) rather than silently treated as "C",
    # which used to skew the cohort average.
    label = row.get("energy_label")
    energy_rank = float(_energy_rank(label)) if (label and label in _ENERGY_ORDER) else None

    return {
        "log_area": math.log(area) if area else None,
        "log_plot": log_plot,
        "has_plot": has_plot,
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
    smearing: float = 1.0            # Duan retransformation factor (log-fit → mean, not median)


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

    y = np.array(log_prices, dtype=float)
    w = np.array(ws, dtype=float)

    def _solve(used: list[str]):
        """Weighted (ridge) OLS over the given feature set. Returns
        (intercept, coef, r2, residual_std, ridge_lambda, smearing)."""
        n_features = len(used)
        # Design matrix: z-score each feature; impute missing with cohort mean (→ z=0)
        X_rows = [[
            ((f[fname] if f[fname] is not None else means[fname]) - means[fname]) / stds[fname]
            for fname in used
        ] for f in feats]
        X = np.array(X_rows, dtype=float).reshape(len(feats), n_features)

        X_aug = np.hstack([np.ones((len(feats), 1)), X])   # prepend intercept column
        XTWX = X_aug.T @ (w[:, None] * X_aug)
        XTWy = X_aug.T @ (w * y)

        rl = 0.0
        if n_eff < _N_EFF_OLS:
            rl = 0.5 * float(np.trace(XTWX)) / max(n_features, 1)
            reg = rl * np.eye(n_features + 1)
            reg[0, 0] = 0.0   # don't penalise intercept
            XTWX = XTWX + reg

        try:
            beta = np.linalg.solve(XTWX, XTWy)
        except np.linalg.LinAlgError:
            beta, _, _, _ = np.linalg.lstsq(X_aug, y, rcond=None)

        intercept = float(beta[0])
        cf = {fname: float(beta[i + 1]) for i, fname in enumerate(used)}

        residuals = y - X_aug @ beta
        ss_res = float(np.dot(w, residuals ** 2))
        y_wm = float(np.dot(w, y) / total_w)
        ss_tot = float(np.dot(w, (y - y_wm) ** 2))
        r2_ = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 1e-10 else 0.0
        dof = max(n_eff - n_features - 1, 1.0)   # n_eff - n_features - intercept

        # Duan's smearing estimator: a log-OLS fit predicts the conditional
        # *median* (geometric mean), which is systematically below the mean and
        # made every estimate read low. The weighted mean of exp(residual) — ≥ 1
        # by Jensen since the weighted residuals sum to zero — rescales the
        # back-transformed prediction to target the conditional mean instead.
        smearing = float(np.dot(w, np.exp(residuals)) / total_w)
        smearing = min(max(smearing, 1.0), _SMEARING_MAX)
        return intercept, cf, r2_, math.sqrt(ss_res / dof), rl, smearing

    intercept_val, coef, r2, residual_std, ridge_lambda, smearing = _solve(feature_used)

    # Non-negativity guard: a garden (and any other monotone-up feature) must
    # not lower the estimate. If collinearity hands such a feature a negative
    # coefficient, drop it and refit so its effect is neutral, never a penalty.
    drop = [f for f in _NON_NEGATIVE_FEATURES if coef.get(f, 0.0) < 0]
    if drop:
        feature_used = [f for f in feature_used if f not in drop]
        intercept_val, coef, r2, residual_std, ridge_lambda, smearing = _solve(feature_used)

    return FittedModel(
        coef=coef, intercept=intercept_val,
        feature_means=means, feature_stds=stds,
        feature_used=feature_used, n_eff=n_eff, r2=r2,
        residual_std=residual_std, ridge_lambda=ridge_lambda,
        fallback=False, median_ppm=median_ppm, smearing=smearing,
    )


def predict(model: FittedModel, subject: dict, overbid: float = 0.0) -> tuple[int, int, int]:
    """Return (low_eur, recommended_eur, high_eur). Returns (0,0,0) on failure.

    `overbid` is the competitive over-asking uplift (e.g. 0.05 = +5%): the fit
    targets comparable *asking*-price value, but a winning Dutch bid sits above
    asking, so the final recommendation is scaled by (1 + overbid).
    """
    area = subject.get("living_area")
    if not area:
        return 0, 0, 0

    uplift = 1.0 + max(0.0, overbid)

    if model.fallback:
        if not model.median_ppm:
            return 0, 0, 0
        recommended = round(model.median_ppm * area * uplift / 100) * 100
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

    # Smearing lifts the geometric-mean fit to the conditional mean; uplift then
    # turns that fair value into a competitive winning bid.
    recommended = round(math.exp(log_price) * model.smearing * uplift / 100) * 100
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
