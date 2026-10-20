"""Volatility research lab: benchmark field, forecast evaluation, risk backtests, conformal bands.

The production forecaster (backend/vol_forecast.py, HAR-IV) was specified first and is the model of
record. This module asks the questions a risk desk would ask of it, on the SAME out-of-sample grid
(every model forecasts the same 5-session target at the same weekly test points, with the same
purge, from information available at the close of the test day):

  1. Benchmarks. Persistence, raw implied vol (OVX as the forecast), EWMA (RiskMetrics),
     GARCH(1,1)-t and GJR-GARCH(1,1)-t (arch), HAR-RV (Corsi 2009), HAR-RV with a downside
     semivolatility term (Patton-Sheppard 2015), HAR on a range-based realized measure
     (Rogers-Satchell + overnight, Yang-Zhang 2000 style), an implied-vol-only regression, HAR-IV,
     HAR-IV with OVX lagged one session (strictly entry-time clean), HAR-IV on the range measure, a
     gradient-boosted tree model (LightGBM) on the same features, and an equal-weight combination.
  2. Evaluation. QLIKE and MSE on variances, the two losses that rank forecasts consistently under a
     noisy volatility proxy (Patton 2011), plus the realized utility of a volatility-targeting investor
     (Bollerslev, Hood, Huss and Pedersen 2018), applied to VARIANCE-CONSISTENT forecasts: a
     regression in logs estimates the conditional median, which is biased low for a variance loss,
     so each log model's forecast is retransformed with Duan's (1983) smearing estimator computed on
     its own purged training residuals, vol_mean = vol_median * sqrt(mean(exp(2 e))). The Model
     Confidence Set (Hansen, Lunde and Nason 2011) and Hansen's (2005) SPA test against the model of
     record, both from arch.bootstrap; Diebold-Mariano tests with Newey-West variance and the
     Harvey-Leybourne-Newbold correction; Mincer-Zarnowitz regressions; a second, range-based
     realized proxy; sub-period and block-size robustness; HAR-IV coefficients with HAC t-statistics.
     Direction accuracy (will next week's realized vol exceed this week's?) is reported only next to
     its noise floor: in a constant-volatility world, where nothing is forecastable, the same pipeline
     scores ~75%, because the 5-session realized vol it compares against is itself noisy.
  3. Honest model choice. A forecaster chosen each week by its trailing out-of-sample loss (only
     outcomes already observed), so the benefit of picking a model is measured without hindsight.
  4. Risk use. Weekly 95% and 99% Value-at-Risk from each volatility forecast, with normal and
     filtered-historical-simulation quantiles, against unconditional historical simulation;
     Kupiec, Christoffersen (independence, conditional coverage) and Engle-Manganelli DQ tests.
  5. Uncertainty. Adaptive conformal inference (Gibbs and Candes 2021) for 80% forecast intervals,
     scored by coverage and by the interval score (Gneiting and Raftery 2007).
  6. Context. A realized-volatility cone and implied-vs-realized series for the dashboard.

Sampling. Forecasts are made EVERY out-of-sample day (about 4,600) for the same 5-session target, so
consecutive targets overlap; inference uses Newey-West variances and block bootstraps long enough to
cover the overlap. The weekly test grid used by the production validator samples one day in five,
and conclusions drawn on one weekday grid can differ from the next (the QLIKE gap between two models
moved from p = 0.39 to p = 0.003 when the grid moved by one day), so every comparison is also run on
each of the five weekday grids separately and the report says on how many it holds. VaR tests,
which need non-overlapping outcomes, run on each weekly grid.

Every input comes from the pinned snapshot in data/market (backend/market_data.py), so the artifact
data/vol_lab.json reproduces from it offline: `python -m backend.vol_lab` (or `--refresh` to download a
new snapshot first). Under the same numeric environment the rebuild is bit for bit; across BLAS
thread counts and CPU kernels the iterative GARCH fits differ in the last bits, so `compare_reports`
defines agreement: every model set, flag, label and count identical, every number equal to 1e-4
relative or to the last decimal it is published with.
"""

from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd
from scipy import stats

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import numpy.typing as npt
    from arch.univariate.base import ARCHModelFixedResult, ARCHModelResult

    Progress = Callable[[int, int], object]

try:
    from . import market_data
    from . import vol_forecast as vf
    from .contract_calendar import last_trade_date
    from .forecast_eval import (basel_zone, delayed_aci, christoffersen, dm_test, dq_test, hac_z,
                                kupiec, mincer_zarnowitz, model_confidence_set, mse_var, qlike)
    from .safe_paths import data_json_path
except ImportError:  # Direct invocation from backend/
    import market_data  # type: ignore[import-not-found, no-redef]
    import vol_forecast as vf  # type: ignore[import-not-found, no-redef]
    from contract_calendar import last_trade_date  # type: ignore[import-not-found, no-redef]
    from forecast_eval import (  # type: ignore[import-not-found, no-redef]
        basel_zone, delayed_aci, christoffersen, dm_test, dq_test, hac_z,
        kupiec, mincer_zarnowitz, model_confidence_set, mse_var, qlike)
    from safe_paths import data_json_path  # type: ignore[import-not-found, no-redef]

ARTIFACT = "vol_lab.json"
ANNUAL = 252.0
SEED = 20261020                  # every bootstrap is seeded: the artifact is deterministic
MCS_SIZE = 0.10                  # 90% Model Confidence Set
MCS_REPS = 5000
MCS_BLOCK = 20                   # daily losses with 5-session overlapping targets; robustness at 10 and 40
MCS_BLOCK_WEEKLY = 10            # ~ n^(1/3) on one ~930-week grid
SELECTION_WINDOW = 156           # trailing weeks (3 years) for out-of-sample model choice
WARMUP = 52                      # weeks of out-of-sample history before any data-driven choice
CONFORMAL_ALPHA = 0.20           # 80% intervals
CONFORMAL_GAMMA = 0.005          # ACI step size (Gibbs-Candes 2021 use 0.005)
CONFORMAL_WINDOW = 156 * 5       # trailing matured daily errors (3 years)
VAR_LEVELS = (0.05, 0.01)
EWMA_LAMBDA = 0.94               # RiskMetrics daily decay
GARCH_SCALE = 100.0              # arch fits percent returns for numerical stability
GARCH_TOL = 1e-9                 # optimizer tolerance: converge to the optimum, not near it (below)
REPRO_REL_TOL = 1e-4             # reproduction check: relative agreement required of every number
LGBM_RETRAIN_WEEKS = 13          # retrain the tree model quarterly (it is the slow one)
CONE_WINDOWS = (5, 10, 22, 66, 126, 252)
CONE_PERCENTILES = (5, 25, 50, 75, 95)

CHAMPION = "har_iv"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    family: str
    description: str


MODELS = [
    ModelSpec("persistence", "Persistence (RV5)", "naive", "Next week's vol equals the last 5 sessions' realized vol."),
    ModelSpec("raw_ovx", "Implied vol (raw OVX)", "implied", "OVX itself (previous session's close) as the forecast: no fitting."),
    ModelSpec("ewma", "EWMA (RiskMetrics)", "time-series", "Exponentially weighted variance, lambda 0.94, all returns since 2000."),
    ModelSpec("garch", "GARCH(1,1)-t", "time-series", "Student-t GARCH(1,1), refit weekly on all returns since 2000."),
    ModelSpec("gjr", "GJR-GARCH(1,1)-t", "time-series", "Asymmetric (leverage) GARCH, Student-t, refit weekly."),
    ModelSpec("har_rv", "HAR-RV", "HAR", "Log-HAR on 5/22/66-session close-to-close realized vol (Corsi 2009)."),
    ModelSpec("har_rs", "HAR-RV + semivariance", "HAR", "HAR-RV plus 5-session downside semivolatility (Patton-Sheppard 2015)."),
    ModelSpec("har_range", "HAR-range", "HAR", "Log-HAR on a range-based measure: Rogers-Satchell plus squared overnight return."),
    ModelSpec("ovx_only", "Implied-vol regression", "implied", "Log-regression of next-week realized vol on OVX alone."),
    ModelSpec("har_iv", "HAR-IV (model of record)", "HAR", "Log-HAR on 5/22/66-session realized vol plus OVX; the production model."),
    ModelSpec("har_iv_sameday", "HAR-IV, same-day OVX (v1 timing)", "HAR",
              "HAR-IV with the test day's own OVX close, which settles ~1h45m after CL: overlaps the target."),
    ModelSpec("har_iv_range", "HAR-IV-range", "HAR", "HAR-IV with the range-based realized measure in place of close-to-close."),
    ModelSpec("lgbm", "LightGBM", "machine learning", "Gradient-boosted trees on all of the above features, retrained quarterly."),
    ModelSpec("combo", "Combination", "combination",
              "Equal-weight pool of the variances of HAR-IV, GJR-GARCH-t and the implied-vol regression."),
]
MODEL_KEYS = [m.key for m in MODELS]
LOG_MODELS = ("har_rv", "har_rs", "har_range", "ovx_only", "har_iv", "har_iv_sameday", "har_iv_range", "lgbm")
COMBO_MEMBERS = ("har_iv", "gjr", "ovx_only")
NOISE_FLOOR_SIMS = 10
SPA_REPS = 5000
UTILITY_SR, UTILITY_GAMMA = 0.4, 2.0   # Bollerslev-Hood-Huss-Pedersen (2018) baseline investor
ROLL_OVERNIGHT_WINDOW = 250            # trailing non-roll sessions for the roll-day overnight proxy


# ----------------------------------------------------------------------------------------------
# Realized measures and the design matrix
# ----------------------------------------------------------------------------------------------

def roll_days(dates: pd.DatetimeIndex) -> np.ndarray:
    """True on the first CL=F session after each contract's last trade date: the day Yahoo's
    continuous series switches contracts, so its overnight gap (old contract's close to the new
    contract's open) is the calendar spread, not a market move."""
    days = pd.DatetimeIndex(dates)
    mask = np.zeros(len(days), dtype=bool)
    year, month = days[0].year, days[0].month
    while (year, month) <= (days[-1].year + 1, days[-1].month):
        expiry: Any = pd.Timestamp(last_trade_date(year, month))     # numpy's stubs reject a Timestamp
        i = int(np.searchsorted(days, expiry, side="right"))
        if 0 < i < len(days):
            mask[i] = True
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return mask


def range_variance(ohlc: pd.DataFrame, returns: np.ndarray, dates: pd.DatetimeIndex | None = None) -> np.ndarray:
    """Daily variance proxy from the bar: Rogers-Satchell (drift-robust) plus the squared overnight
    (previous close to open) return, i.e. the open-to-close and close-to-open parts of a Yang-Zhang
    style estimator. On roll days (when `dates` is given) the overnight gap is the calendar spread,
    so it is replaced by the median squared overnight return of the previous ROLL_OVERNIGHT_WINDOW
    non-roll sessions. Where the bar is unusable (no positive open/high/low) the squared
    close-to-close return is used instead. Element t uses only session t's bar and earlier data.
    """
    o, h, low, c = (np.log(ohlc[k].to_numpy(dtype=float)) for k in ("Open", "High", "Low", "Close"))
    rs = (h - c) * (h - o) + (low - c) * (low - o)
    prev_c = np.r_[np.nan, c[:-1]]
    j2 = np.where(np.isfinite(o - prev_c), (o - prev_c) ** 2, 0.0)
    if dates is not None:
        roll = roll_days(dates)
        clean = pd.Series(np.where(roll, np.nan, j2))
        proxy = clean.rolling(ROLL_OVERNIGHT_WINDOW, min_periods=20).median().shift(1).to_numpy()
        j2 = np.where(roll, np.where(np.isfinite(proxy), proxy, 0.0), j2)
    var = np.clip(rs, 0.0, None) + j2
    r2 = np.asarray(returns, dtype=float) ** 2
    return np.where(np.isfinite(var), var, r2)


@dataclass
class Design:
    r: np.ndarray                  # daily log returns (all sessions)
    dates: pd.DatetimeIndex        # session of each return
    X: np.ndarray                  # rows: RV5, RV22, RV66, OVX (annualized, OVX in vol points)
    y: np.ndarray                  # next-5-session realized vol (annualized)
    tdate: pd.DatetimeIndex        # test-day date of each row
    pos: np.ndarray                # index into r of each row's test day
    XR: np.ndarray                 # range-based RV5, RV22, RV66
    v_range: np.ndarray            # daily range-based variance (roll-safe), all sessions
    lev: np.ndarray                # 5-session downside semivol
    ovx_same: np.ndarray           # the test day's own OVX close (v1 timing, robustness only)
    ends: list[int] = field(default_factory=list)   # OOS row indices (every day from MIN_TRAIN on)


def build_design(r: np.ndarray, dates: pd.DatetimeIndex, ovx: np.ndarray, ohlc: pd.DataFrame,
                 offset: int = 0) -> Design:
    """Rows and OOS grid identical to vol_forecast.validate(): same features, target, purge, grid.

    `ovx` is the SAME-DAY aligned OVX series; the design uses it lagged by vf.OVX_LAG_SESSIONS (the
    entry-time-clean convention of the production model) and keeps the same-day value per row only
    for the v1-timing robustness model.
    """
    ovx = np.asarray(ovx, float)
    X, y, tdate, pos = vf._build_matrix(r, dates, vf.lag_ovx(ovx), return_positions=True)
    v = range_variance(ohlc, r, dates)
    XR = np.array([[np.sqrt(np.mean(v[t - w + 1: t + 1]) * ANNUAL) for w in (5, 22, 66)] for t in pos])
    lev = np.array([max(vf._downside_semivol(r[t - 4: t + 1]), 1e-4) for t in pos])
    ovx_same = np.array([ovx[t] if np.isfinite(ovx[t]) and ovx[t] > 0 else X[i, 3] for i, t in enumerate(pos)])
    ends = list(range(vf.MIN_TRAIN + offset, len(X)))        # every out-of-sample day
    return Design(r, dates, X, y, tdate, pos, XR, v, lev, ovx_same, ends)


# ----------------------------------------------------------------------------------------------
# The walk-forward
# ----------------------------------------------------------------------------------------------

def _ewma_vol(returns: np.ndarray, lam: float = EWMA_LAMBDA) -> float:
    weights = lam ** np.arange(len(returns))[::-1]
    var = (1.0 - lam) * float(np.sum(weights * returns ** 2)) / (1.0 - lam ** len(returns))
    return float(np.sqrt(var * ANNUAL))


class _GarchState:
    """Re-estimated weekly (warm-started), filtered daily with the latest estimates. Deterministic:
    the same inputs give the same refit path and the same forecasts."""

    def __init__(self, asymmetric: bool) -> None:
        self.o = 1 if asymmetric else 0
        self.params: np.ndarray | None = None

    def forecast(self, returns: np.ndarray, horizon: int, refit: bool = True) -> float:
        from arch import arch_model

        model = arch_model(returns * GARCH_SCALE, mean="Constant", vol="GARCH", p=1, o=self.o, q=1, dist="t")
        res: ARCHModelResult | ARCHModelFixedResult
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if refit or self.params is None:
                # Each refit is warm-started from the last, so with the default tolerance a last-bit
                # difference in a BLAS result (thread count, CPU kernel) carried through ~930 refits
                # and moved published GJR figures (direction accuracy, an MCS p-value). Converging
                # tightly keeps two machines' artifacts within ~2e-5 relative (measured: 4 BLAS
                # threads vs one thread on the Haswell kernel), with every rounded figure equal.
                res = model.fit(disp="off", starting_values=self.params, show_warning=False, tol=GARCH_TOL)
                self.params = res.params.to_numpy()
            else:
                res = model.fix(self.params)
        var = res.forecast(horizon=horizon, reindex=False).variance.to_numpy()[-1]
        return float(np.sqrt(np.mean(var) * ANNUAL) / GARCH_SCALE)


def _lgbm() -> Any:
    import lightgbm as lgb

    return lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=40,
                             subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=SEED % 9973,
                             n_jobs=1, deterministic=True, force_row_wise=True, verbose=-1)


def _fit_log(X_train: np.ndarray, y_train: np.ndarray) -> tuple[np.ndarray, float]:
    """Log-OLS (the HAR specification) and its Duan (1983) smearing factor for VARIANCE,
    mean(exp(2 e)) over the training residuals e (distribution-free; exp(2 s^2) if e is normal)."""
    A = np.column_stack([np.ones(len(X_train)), np.log(X_train)])
    beta, *_ = np.linalg.lstsq(A, np.log(y_train), rcond=None)
    resid = np.log(y_train) - A @ beta
    return beta, float(np.mean(np.exp(2.0 * resid)))


def _median_mean(beta: np.ndarray, smear: float, x: np.ndarray) -> tuple[float, float]:
    """(median, variance-consistent) vol forecast of a log model: exp(mu) and sqrt(exp(2 mu) * smear),
    the square root of the smeared conditional mean of the variance."""
    mu = float(beta[0] + beta[1:] @ np.log(x))
    return float(np.exp(mu)), float(np.exp(mu) * np.sqrt(smear))


def _regression_designs(X: np.ndarray, XR: np.ndarray, lev: np.ndarray, ovx_same: np.ndarray) -> dict[str, np.ndarray]:
    """Feature matrix of every log-regression model (rows aligned with X). Works on 2-D designs and
    on a single 1-row design built for the live forecast."""
    return {
        "har_rv": X[:, :3],
        "har_rs": np.column_stack([X[:, :3], lev]),
        "har_range": XR,
        "ovx_only": X[:, 3:4],
        "har_iv": X,
        "har_iv_sameday": np.column_stack([X[:, :3], ovx_same]),
        "har_iv_range": np.column_stack([XR, X[:, 3]]),
    }


def walk_forward(d: Design, *, include_slow: bool = True, progress: Progress | None = None) -> pd.DataFrame:
    """One row per OOS day: the realized target, the persistence / mean-reversion references, the
    5-session forward return (for VaR), the weekday grid the row belongs to (`grid`, 0-4; grid 0 is
    the production validator's), and for every model its variance-consistent forecast (column
    `<model>`, used for losses and risk) and its median forecast (column `<model>__med`, used for the
    direction call and the intervals). Both are annualized vols.

    Each forecast at row `end` uses training rows [0, end - (H - 1)) for the regression models (the
    purge: no training target overlaps the test target) and returns up to and including the test
    day for the time-series models, so nothing after the test day's close is ever used. GARCH is
    re-estimated on grid-0 days and filtered daily; the tree model is retrained every quarter.
    """
    X, XR, y = d.X, d.XR, d.y
    designs = _regression_designs(X, XR, d.lev, d.ovx_same)
    feats = np.log(np.column_stack([X, XR, d.lev]))
    garch, gjr = _GarchState(False), _GarchState(True)
    tree, tree_smear = None, 1.0
    lgbm_err: list[tuple[int, float]] = []           # (row, log error), usable once matured
    ewma_s = ewma_w = 0.0
    ewma_t = -1
    rows = []
    for k, end in enumerate(d.ends):
        train_end = end - (vf.H - 1)
        grid = (end - vf.MIN_TRAIN) % vf.STEP
        t = int(d.pos[end])
        past = d.r[: t + 1]
        while ewma_t < t:                             # recursive RiskMetrics variance through day t
            ewma_t += 1
            ewma_s = EWMA_LAMBDA * ewma_s + (1 - EWMA_LAMBDA) * d.r[ewma_t] ** 2
            ewma_w = EWMA_LAMBDA * ewma_w + (1 - EWMA_LAMBDA)
        f: dict[str, float] = {}
        for key, value in (("persistence", float(X[end, 0])), ("raw_ovx", float(X[end, 3]) / 100.0),
                           ("ewma", float(np.sqrt(ewma_s / ewma_w * ANNUAL)))):
            f[key] = f[f"{key}__med"] = value
        coef_har_iv: tuple[np.ndarray, float] | None = None
        for key, Z in designs.items():
            beta, smear = _fit_log(Z[:train_end], y[:train_end])
            f[f"{key}__med"], f[key] = _median_mean(beta, smear, Z[end])
            if key == "har_iv":
                coef_har_iv = (beta, smear)
        if include_slow:
            for key, state in (("garch", garch), ("gjr", gjr)):
                f[key] = f[f"{key}__med"] = state.forecast(past, vf.H, refit=grid == 0)
            if tree is None or (end - vf.MIN_TRAIN) % (LGBM_RETRAIN_WEEKS * vf.STEP) == 0:
                tree = _lgbm()
                tree.fit(feats[:train_end], np.log(y[:train_end]))
                tree_smear = float(np.mean(np.exp(2.0 * (np.log(y[:train_end]) - tree.predict(feats[:train_end])))))
            med = float(np.exp(tree.predict(feats[end:end + 1])[0]))
            # A boosted model's in-sample residuals understate its error; once a year of its own
            # MATURED out-of-sample log errors exists, their smearing factor replaces the in-sample one.
            matured = np.array([e for row, e in lgbm_err if row <= end - vf.H][-CONFORMAL_WINDOW:])
            smear = float(np.mean(np.exp(2.0 * matured))) if matured.size >= WARMUP * vf.STEP else tree_smear
            f["lgbm__med"], f["lgbm"] = med, med * float(np.sqrt(smear))
            lgbm_err.append((end, float(np.log(y[end]) - np.log(med))))
            # Linear pool of the members' variance forecasts (a geometric pool of means is biased low).
            f["combo"] = float(np.sqrt(np.mean([f[m] ** 2 for m in COMBO_MEMBERS])))
            f["combo__med"] = float(np.exp(np.mean([np.log(f[f"{m}__med"]) for m in COMBO_MEMBERS])))
        beta, smear = cast("tuple[np.ndarray, float]", coef_har_iv)
        rows.append({
            "date": d.tdate[end],
            "grid": int(grid),
            "actual": float(y[end]),
            "rv5": float(X[end, 0]),
            "rv66": float(X[end, 2]),
            "ovx": float(X[end, 3]) / 100.0,
            "ret5": float(np.sum(d.r[t + 1: t + 1 + vf.H])),
            "b_const": float(beta[0]), "b_rv5": float(beta[1]), "b_rv22": float(beta[2]),
            "b_rv66": float(beta[3]), "b_ovx": float(beta[4]), "smear": smear,
            "actual_range": float(np.sqrt(np.mean(d.v_range[t + 1: t + 1 + vf.H]) * ANNUAL)),
            **f,
        })
        if progress and k % 500 == 0:
            progress(k, len(d.ends))
    return pd.DataFrame(rows)


def live_model_forecast(key: str, r: np.ndarray, dates: pd.DatetimeIndex, ovx_same: np.ndarray,
                        ohlc: pd.DataFrame) -> dict[str, Any]:
    """Today's forecast from any model in MODELS, fitted on every matured row (the production
    challenger uses this, so it is computed by exactly the definition that was benchmarked)."""
    if key not in MODEL_KEYS:
        raise ValueError(f"unknown model {key!r}")
    d = build_design(r, dates, ovx_same, ohlc)
    t = len(r) - 1
    v = range_variance(ohlc, r, dates)
    ovx_l = cast("np.ndarray", vf.lag_ovx(np.asarray(ovx_same, float)))
    x_now = np.array([[vf._rvol(r[t - 4: t + 1]), vf._rvol(r[t - 21: t + 1]), vf._rvol(r[t - 65: t + 1]), ovx_l[t]]])
    xr_now = np.array([[np.sqrt(np.mean(v[t - w + 1: t + 1]) * ANNUAL) for w in (5, 22, 66)]])
    lev_now = np.array([max(vf._downside_semivol(r[t - 4: t + 1]), 1e-4)])
    if not (np.isfinite(x_now).all() and x_now[0, 3] > 0):
        raise RuntimeError("no lagged OVX print for a live forecast")
    train = _regression_designs(d.X, d.XR, d.lev, d.ovx_same)
    now = _regression_designs(x_now, xr_now, lev_now, np.array([ovx_same[t]]))
    cache: dict[str, tuple[float, float]] = {}

    def forecast(m: str) -> tuple[float, float]:
        if m in cache:
            return cache[m]
        if m in train:
            beta, smear = _fit_log(train[m], d.y)
            out = _median_mean(beta, smear, now[m][0])
        elif m == "persistence":
            out = (float(x_now[0, 0]),) * 2
        elif m == "raw_ovx":
            out = (float(x_now[0, 3]) / 100.0,) * 2
        elif m == "ewma":
            out = (_ewma_vol(r),) * 2
        elif m in ("garch", "gjr"):
            out = (_GarchState(m == "gjr").forecast(r, vf.H),) * 2
        elif m == "combo":
            members = [forecast(c) for c in COMBO_MEMBERS]
            out = (float(np.exp(np.mean([np.log(a) for a, _ in members]))),
                   float(np.sqrt(np.mean([b ** 2 for _, b in members]))))
        else:                                                   # lgbm
            feats = np.log(np.column_stack([d.X, d.XR, d.lev]))
            tree = _lgbm()
            tree.fit(feats, np.log(d.y))
            smear = float(np.mean(np.exp(2.0 * (np.log(d.y) - tree.predict(feats)))))
            med = float(np.exp(tree.predict(np.log(np.column_stack([x_now, xr_now, lev_now])))[0]))
            out = (med, med * float(np.sqrt(smear)))
        cache[m] = out
        return out

    median, mean = forecast(key)
    spec = next(m for m in MODELS if m.key == key)
    return {"key": key, "label": spec.label, "forecast_next_week_vol_annualized_pct": round(mean * 100, 1),
            "forecast_median_pct": round(median * 100, 1), "as_of": str(dates[-1].date())}


def noise_floor(r: np.ndarray, dates: pd.DatetimeIndex, sims: int = NOISE_FLOOR_SIMS) -> dict[str, Any]:
    """Direction accuracy when there is nothing to forecast: i.i.d. Student-t(5) returns with the
    sample's volatility, run through the identical feature builder and walk-forward (HAR, no OVX)."""
    sd = float(np.std(r))
    har, mr = [], []
    for seed in range(sims):
        rng = np.random.default_rng(SEED + seed)
        z = rng.standard_t(5, len(r)) / np.sqrt(5 / 3) * sd
        X, y, tdate = vf._build_matrix(z, dates, None)
        sim = vf._walk_forward(X, y, tdate, has_ovx=False)
        har.append(float(sim["hit"].mean()))
        mr.append(float(sim["hit_mr"].mean()))
    return {"sims": sims, "process": "i.i.d. Student-t(5), constant volatility (nothing to forecast)",
            "har_direction_acc_pct_mean": round(float(np.mean(har)) * 100, 1),
            "har_direction_acc_pct_range": [round(min(har) * 100, 1), round(max(har) * 100, 1)],
            "mean_reversion_acc_pct_mean": round(float(np.mean(mr)) * 100, 1),
            "theoretical_floor_pct": 75.0,
            "note": ("For any continuous i.i.d. series, calling 'up' exactly when the current 5-session vol is "
                     "below its median is right with probability E[max(U, 1-U)] = 75%.")}


def coefficient_report(d: Design) -> dict[str, Any]:
    """Full-sample HAR-IV log regression with Newey-West t-statistics (overlapping 5-session targets
    on daily rows, so 10 lags)."""
    import statsmodels.api as sm

    A = sm.add_constant(np.log(d.X))
    res = sm.OLS(np.log(d.y), A).fit(cov_type="HAC", cov_kwds={"maxlags": 10})
    names = ["const", "log RV5", "log RV22", "log RV66", "log OVX"]
    return {"terms": [{"term": n, "coef": round(float(b), 4), "t_hac": round(float(t), 2)}
                      for n, b, t in zip(names, res.params, res.tvalues)],
            "r2_log": round(float(res.rsquared), 3), "n_rows": int(len(d.y)), "hac_lags": 10}


# ----------------------------------------------------------------------------------------------
# Losses and tests
# ----------------------------------------------------------------------------------------------


# ----------------------------------------------------------------------------------------------
# Conformal intervals
# ----------------------------------------------------------------------------------------------


# ----------------------------------------------------------------------------------------------
# Report sections
# ----------------------------------------------------------------------------------------------

def _hit(frame: pd.DataFrame, key: str) -> np.ndarray:
    """Direction hits of a model's MEDIAN forecast: P(next > current) > 1/2 iff median > current."""
    up = (frame["actual"] > frame["rv5"]).to_numpy()
    return cast("np.ndarray", ((frame[f"{key}__med"] > frame["rv5"]).to_numpy() == up).astype(float))


def _mr_hit(frame: pd.DataFrame) -> np.ndarray:
    up = (frame["actual"] > frame["rv5"]).to_numpy()
    return cast("np.ndarray", ((frame["rv66"] > frame["rv5"]).to_numpy() == up).astype(float))


def _losses(frame: pd.DataFrame, target: str = "actual") -> tuple[pd.DataFrame, pd.DataFrame]:
    y = frame[target].to_numpy()
    LQ = pd.DataFrame({m: qlike(y, frame[m].to_numpy()) for m in MODEL_KEYS})
    LM = pd.DataFrame({m: mse_var(y, frame[m].to_numpy()) for m in MODEL_KEYS})
    return LQ, LM


def realized_utility(actual: npt.ArrayLike, forecast: npt.ArrayLike, sr: float = UTILITY_SR,
                     gamma: float = UTILITY_GAMMA) -> np.ndarray:
    """Per-period realized utility of a mean-variance investor who sizes a position by the vol
    forecast (Bollerslev, Hood, Huss and Pedersen 2018): (SR^2 / gamma) * (y/f - (y/f)^2 / 2), in
    annualized units. Maximized at f = y (SR^2 / (2 gamma)); free of the noise in realized returns."""
    ratio = np.asarray(actual, float) / np.asarray(forecast, float)
    return cast("np.ndarray", (sr ** 2 / gamma) * (ratio - 0.5 * ratio ** 2))


def spa_vs_champion(losses: pd.DataFrame, block: int = MCS_BLOCK, reps: int = SPA_REPS,
                    seed: int = SEED) -> dict[str, Any]:
    """Hansen (2005) Superior Predictive Ability: is ANY model in the field better than the model of
    record, after accounting for having searched over all of them? Small p: some model beats it."""
    from arch.bootstrap import SPA

    others = losses.drop(columns=[CHAMPION, "har_iv_sameday"], errors="ignore")
    spa = SPA(losses[CHAMPION], others, block_size=block, reps=reps, bootstrap="stationary", seed=seed)
    spa.compute()
    pv = spa.pvalues
    return {"p_consistent": round(float(pv["consistent"]), 4), "p_lower": round(float(pv["lower"]), 4),
            "p_upper": round(float(pv["upper"]), 4), "models": list(others.columns),
            "note": "excludes the same-day-OVX variant, which uses information the model of record may not"}


def leaderboard(frame: pd.DataFrame) -> dict[str, Any]:
    """Every model on every OOS day, plus the same comparison on each weekday grid on its own."""
    y = frame["actual"].to_numpy()
    LQ, LM = _losses(frame)
    mcs_q = model_confidence_set(LQ, block=MCS_BLOCK)
    mcs_m = model_confidence_set(LM, block=MCS_BLOCK)
    grids = {}
    for g in range(vf.STEP):
        mask = (frame["grid"] == g).to_numpy()
        LQg = LQ[mask].reset_index(drop=True)
        grids[g] = {"mcs": model_confidence_set(LQg, block=MCS_BLOCK_WEEKLY)["included"],
                    "dm": {m: dm_test(LQg[CHAMPION], LQg[m]) for m in MODEL_KEYS if m != CHAMPION},
                    "qlike": {m: float(LQg[m].mean()) for m in MODEL_KEYS}}
    mr = _mr_hit(frame)
    U = pd.DataFrame({m: realized_utility(y, frame[m].to_numpy()) for m in MODEL_KEYS})
    LQr, _ = _losses(frame, "actual_range")
    mcs_range = model_confidence_set(LQr, block=MCS_BLOCK)
    rows = []
    for spec in MODELS:
        m = spec.key
        hit = _hit(frame, m)
        z_dir, _ = hac_z(hit - mr)
        mz = mincer_zarnowitz(y, frame[m].to_numpy(), maxlags=10)
        dm_grid = [grids[g]["dm"][m]["stat"] for g in range(vf.STEP)] if m != CHAMPION else None
        rows.append({
            "key": m, "label": spec.label, "family": spec.family, "description": spec.description,
            "qlike": round(float(LQ[m].mean()), 4),
            "qlike_median_forecast": (round(float(qlike(y, frame[f"{m}__med"].to_numpy()).mean()), 4)
                                      if m in LOG_MODELS or m == "combo" else None),
            "qlike_by_grid": [round(grids[g]["qlike"][m], 4) for g in range(vf.STEP)],
            "mse_var": round(float(LM[m].mean()), 5),
            "realized_utility_pct": round(float(U[m].mean()) * 100, 3),
            "dm_utility_vs_har_iv": None if m == CHAMPION else dm_test(-U[CHAMPION], -U[m], horizon=vf.H),
            "qlike_range_proxy": round(float(LQr[m].mean()), 4),
            "in_mcs_qlike_range_proxy": m in mcs_range["included"],
            "dm_qlike_vs_har_iv": None if m == CHAMPION else dm_test(LQ[CHAMPION], LQ[m], horizon=vf.H),
            "dm_qlike_vs_har_iv_by_grid": None if m == CHAMPION else [round(v, 2) for v in cast("list[float]", dm_grid)],
            "dm_mse_vs_har_iv": None if m == CHAMPION else dm_test(LM[CHAMPION], LM[m], horizon=vf.H),
            "direction_acc_pct": None if m == "persistence" else round(float(hit.mean()) * 100, 1),
            "direction_z_vs_mean_reversion": None if m == "persistence" else round(float(z_dir), 2),
            "mean_ratio_actual_to_forecast": round(float(np.mean(y / frame[m].to_numpy())), 3),
            "mincer_zarnowitz": {k: round(v, 4) for k, v in mz.items()},
            "mcs_qlike_p": mcs_q["p_values"][m], "in_mcs_qlike": m in mcs_q["included"],
            "mcs_mse_p": mcs_m["p_values"][m], "in_mcs_mse": m in mcs_m["included"],
            "in_mcs_qlike_grids": int(sum(m in grids[g]["mcs"] for g in range(vf.STEP))),
        })
    rows.sort(key=lambda r: r["qlike"])
    for rank, row in enumerate(rows, 1):
        row["rank_qlike"] = rank
    robustness = {f"block_{b}": model_confidence_set(LQ, block=b)["included"] for b in (10, 40)}
    for start, end in (("2008-01-01", "2016-12-31"), ("2017-01-01", "2099-12-31")):
        mask = ((frame["date"] >= start) & (frame["date"] <= end)).to_numpy()
        label = f"{start[:4]}-{min(int(end[:4]), int(frame['date'].max().year))}"
        robustness[label] = {"n_days": int(mask.sum()),
                             "included": model_confidence_set(LQ[mask].reset_index(drop=True),
                                                              block=MCS_BLOCK)["included"]}
    up = (frame["actual"] > frame["rv5"]).to_numpy()
    return {"models": rows, "mcs_qlike_included": mcs_q["included"], "mcs_mse_included": mcs_m["included"],
            "mcs_qlike_range_proxy_included": mcs_range["included"],
            "spa_qlike_vs_har_iv": spa_vs_champion(LQ),
            "realized_utility_max_pct": round(UTILITY_SR ** 2 / (2 * UTILITY_GAMMA) * 100, 3),
            "mcs_qlike_by_grid": {str(g): grids[g]["mcs"] for g in range(vf.STEP)},
            "mcs_robustness_qlike": robustness, "n_days": int(len(frame)),
            "base_rate_pct": round(float(max(up.mean(), 1 - up.mean())) * 100, 1),
            "mean_reversion_acc_pct": round(float(mr.mean()) * 100, 1)}


def oos_selection(frame: pd.DataFrame) -> dict[str, Any]:
    """Each day, use the model with the lowest trailing QLIKE over the previous SELECTION_WINDOW weeks
    of MATURED outcomes (rows at least H sessions old); HAR-IV until a year of them exists. Measures
    the value of choosing among the models without hindsight."""
    cands = [m for m in MODEL_KEYS if m not in ("persistence", "har_iv_sameday")]
    y = frame["actual"].to_numpy()
    L = np.column_stack([qlike(y, frame[m].to_numpy()) for m in cands])
    window, warm = SELECTION_WINDOW * vf.STEP, WARMUP * vf.STEP
    csum = np.vstack([np.zeros(len(cands)), np.cumsum(L, axis=0)])
    picks, chosen, chosen_med = [], np.empty(len(frame)), np.empty(len(frame))
    for i in range(len(frame)):
        hi = i - vf.H + 1                                   # outcomes of rows < hi have matured
        if hi < warm:
            j = cands.index(CHAMPION)
        else:
            lo = max(0, hi - window)
            j = int(np.argmin((csum[hi] - csum[lo]) / (hi - lo)))
        picks.append(cands[j])
        chosen[i] = cast(float, frame[cands[j]].iat[i])
        chosen_med[i] = cast(float, frame[f"{cands[j]}__med"].iat[i])
    lq_sel = qlike(y, chosen)
    lq_champ = qlike(y, frame[CHAMPION].to_numpy())
    counts = pd.Series(picks).value_counts()
    return {
        "rule": (f"each day, the lowest trailing {SELECTION_WINDOW}-week QLIKE over matured outcomes; "
                 f"HAR-IV for the first {WARMUP} weeks"),
        "candidates": cands,
        "qlike": round(float(lq_sel.mean()), 4), "qlike_har_iv": round(float(lq_champ.mean()), 4),
        "dm_vs_har_iv": dm_test(lq_champ, lq_sel, horizon=vf.H),
        "direction_acc_pct": round(float(((chosen_med > frame["rv5"].to_numpy()) == (y > frame["rv5"].to_numpy())).mean()) * 100, 1),
        "pick_share_pct": {k: round(float(v) / len(picks) * 100, 1) for k, v in counts.items()},
    }


def var_backtest(frame: pd.DataFrame,
                 models: tuple[str, ...] = ("har_iv", "gjr", "ewma", "raw_ovx")) -> dict[str, Any]:
    """Weekly (5-session) VaR for a long position, VaR = -q * sigma_5d, on each weekday grid (VaR
    tests need non-overlapping outcomes). q is the normal quantile, or (filtered historical
    simulation) the empirical quantile of the grid's PAST standardized outcomes ret5 / sigma_5d
    (after WARMUP weeks). Unconditional historical simulation (trailing quantile of past 5-session
    returns, no volatility model) is the benchmark. Every method is scored on the same weeks."""
    out: dict[str, dict[str, Any]] = {"grids": {}, "summary": {}}
    for g in range(vf.STEP):
        wk = frame[frame["grid"] == g].reset_index(drop=True)
        ret = wk["ret5"].to_numpy()
        n = ret.size
        eval_idx = np.arange(WARMUP, n)
        methods = []
        for p in VAR_LEVELS:
            for m in models:
                s5 = wk[m].to_numpy() / np.sqrt(ANNUAL) * np.sqrt(vf.H)
                z = ret / s5
                for kind in ("normal", "fhs"):
                    if kind == "normal":
                        q = np.full(n, stats.norm.ppf(p))
                    else:
                        q = np.array([np.quantile(z[:i], p) if i >= WARMUP else np.nan for i in range(n)])
                    methods.append(_var_scores(f"{m}:{kind}", m, kind, p, ret, -q * s5, eval_idx, wk))
            hs = np.array([np.quantile(ret[max(0, i - SELECTION_WINDOW): i], p) if i >= WARMUP else np.nan
                           for i in range(n)])
            methods.append(_var_scores("historical_simulation", None, "historical_simulation", p, ret, -hs,
                                       eval_idx, wk))
        out["grids"][str(g)] = {"evaluation_weeks": int(eval_idx.size), "methods": methods}
    # How often each method passes each test at 5%, across the five grids.
    for entry in out["grids"]["0"]["methods"]:
        key, level = entry["key"], entry["level"]
        same = [next(m for m in out["grids"][str(g)]["methods"] if m["key"] == key and m["level"] == level)
                for g in range(vf.STEP)]
        out["summary"][f"{key}@{level}"] = {
            "kupiec_pass_grids": int(sum(m["kupiec"]["p_value"] >= 0.05 for m in same)),
            "independence_pass_grids": int(sum(m["christoffersen"]["p_independence"] >= 0.05 for m in same)),
            "conditional_coverage_pass_grids": int(sum(m["christoffersen"]["p_conditional_coverage"] >= 0.05 for m in same)),
            "dq_pass_grids": int(sum(m["dq"]["p_value"] >= 0.05 for m in same)),
            "exceedances_by_grid": [m["kupiec"]["exceedances"] for m in same],
            "expected": same[0]["kupiec"]["expected"],
        }
    return out


def _var_scores(key: str, model: str | None, kind: str, p: float, ret: np.ndarray, var: np.ndarray,
                idx: np.ndarray, frame: pd.DataFrame) -> dict[str, Any]:
    exceed = ret[idx] < -var[idx]
    last = exceed[-250:]
    return {
        "key": key, "model": model, "quantile": kind, "level": p,
        "kupiec": kupiec(exceed, p), "christoffersen": christoffersen(exceed, p),
        "dq": dq_test(exceed, var[idx], p),
        "exceedances_last_250": int(last.sum()),
        "basel_zone_last_250": basel_zone(int(last.sum())) if p == 0.01 else None,
        "mean_var_pct": round(float(np.mean(var[idx])) * 100, 2),
    }


def choose_challenger(frame: pd.DataFrame) -> str:
    """The challenger: the lowest-QLIKE model in the 90% Model Confidence Set other than the model of
    record and its v1-timing variant (or the lowest-QLIKE eligible model if the set holds none). The
    choice is made on the full 2008-present sample, so the challenger's historical edge is in-sample to
    the choice; its forward live record is the clean test (see the promotion rule in the README)."""
    LQ, _ = _losses(frame)
    eligible = set(MODEL_KEYS) - {CHAMPION, "har_iv_sameday"}
    included = set(model_confidence_set(LQ, block=MCS_BLOCK)["included"]) & eligible
    pool: set[str] = included or eligible
    return min(pool, key=lambda m: (float(cast(Any, LQ[m].mean())), m))




def conformal_report(frame: pd.DataFrame, challenger: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    years = frame["date"].dt.year.to_numpy()
    for m in dict.fromkeys((CHAMPION, challenger)):
        e = np.log(frame["actual"].to_numpy()) - np.log(frame[f"{m}__med"].to_numpy())
        aci = delayed_aci(e, delay=vf.H, alpha=CONFORMAL_ALPHA, gamma=CONFORMAL_GAMMA,
                          window=CONFORMAL_WINDOW, warmup=WARMUP * vf.STEP)
        hits = aci["hit"][np.isfinite(aci["hit"])]
        width = np.exp(aci["hi"]) / np.exp(aci["lo"])
        by_year = {int(y): round(float(np.nanmean(aci["hit"][years == y])) * 100, 1)
                   for y in np.unique(years) if np.isfinite(aci["hit"][years == y]).any()}
        med = frame[f"{m}__med"].to_numpy()
        lo_v, hi_v, act = med * np.exp(aci["lo"]), med * np.exp(aci["hi"]), frame["actual"].to_numpy()
        score = ((hi_v - lo_v) + (2 / CONFORMAL_ALPHA) * np.maximum(lo_v - act, 0)
                 + (2 / CONFORMAL_ALPHA) * np.maximum(act - hi_v, 0))
        out[m] = {"target_coverage_pct": round((1 - CONFORMAL_ALPHA) * 100, 1),
                  "coverage_pct": round(float(hits.mean()) * 100, 1), "n_days": int(hits.size),
                  "interval_score_pct": round(float(np.nanmean(score)) * 100, 2),
                  "median_hi_lo_ratio": round(float(np.nanmedian(width)), 2),
                  "coverage_by_year_pct": by_year,
                  "next_alpha": round(aci["alpha_next"], 4),
                  "next_log_bounds": [round(aci["next_lo"], 4), round(aci["next_hi"], 4)],
                  "_lo": aci["lo"], "_hi": aci["hi"]}
    return out


def yearly(frame: pd.DataFrame, challenger: str) -> list[dict[str, Any]]:
    """Per calendar year, over every OOS day: direction hit rates and QLIKE."""
    out: list[dict[str, Any]] = []
    for year, g in frame.groupby(frame["date"].dt.year):
        up = (g["actual"] > g["rv5"]).to_numpy()
        row = {"year": int(year), "n_days": int(len(g)),
               "base_rate_pct": round(float(max(up.mean(), 1 - up.mean())) * 100, 1),
               "mean_reversion_acc_pct": round(float(_mr_hit(g).mean()) * 100, 1)}
        for m in dict.fromkeys((CHAMPION, challenger, "gjr", "ovx_only", "raw_ovx")):
            row[f"{m}_acc_pct"] = round(float(_hit(g, m).mean()) * 100, 1)
            row[f"{m}_qlike"] = round(float(qlike(g["actual"], g[m]).mean()), 4)
        out.append(row)
    return out


def vol_cone(r: np.ndarray, dates: pd.DatetimeIndex, ovx_last: float | None) -> dict[str, Any]:
    """Distribution of annualized close-to-close realized vol over rolling windows of each length
    (all overlapping windows in the snapshot), with the latest value of each."""
    s = pd.Series(np.asarray(r, float) ** 2, index=dates)
    rows = []
    for w in CONE_WINDOWS:
        rv = np.sqrt(s.rolling(w).mean() * ANNUAL).dropna()
        pct = np.percentile(rv, CONE_PERCENTILES)
        rows.append({"window": w, **{f"p{q}": round(float(v) * 100, 1) for q, v in zip(CONE_PERCENTILES, pct)},
                     "latest": round(float(rv.iloc[-1]) * 100, 1),
                     "latest_percentile": round(float((rv < rv.iloc[-1]).mean()) * 100, 1)})
    return {"windows": rows, "since": dates[0].strftime("%Y-%m-%d"), "as_of": dates[-1].strftime("%Y-%m-%d"),
            "implied_ovx": None if ovx_last is None else round(float(ovx_last), 1)}


def chart_series(frame: pd.DataFrame, conf: dict[str, dict[str, Any]], challenger: str) -> dict[str, list[Any]]:
    """Compact weekly arrays (grid 0, the production grid) for the dashboard: percent units with 1
    decimal; elasticities with 3. VaR uses the grid's own filtered-historical-simulation quantiles."""
    def pct(a: Any) -> list[float | None]:
        return [None if not np.isfinite(v) else round(float(v) * 100, 1) for v in np.asarray(a, float)]

    mask = (frame["grid"] == 0).to_numpy()
    wk = frame[mask].reset_index(drop=True)
    lo = conf[CHAMPION]["_lo"][mask]
    hi = conf[CHAMPION]["_hi"][mask]
    med = wk[f"{CHAMPION}__med"].to_numpy()
    s5 = wk[CHAMPION].to_numpy() / np.sqrt(ANNUAL) * np.sqrt(vf.H)
    z = wk["ret5"].to_numpy() / s5
    n = len(wk)
    q99 = np.array([np.quantile(z[:i], 0.01) if i >= WARMUP else np.nan for i in range(n)])
    q95 = np.array([np.quantile(z[:i], 0.05) if i >= WARMUP else np.nan for i in range(n)])
    return {
        "date": wk["date"].dt.strftime("%Y-%m-%d").tolist(),
        "actual": pct(wk["actual"]), "rv5": pct(wk["rv5"]), "ovx": pct(wk["ovx"]),
        "har_iv": pct(wk[CHAMPION]), "har_iv_median": pct(med),
        "challenger": pct(wk[challenger]),
        "lo80": pct(med * np.exp(lo)), "hi80": pct(med * np.exp(hi)),
        "ret5": pct(wk["ret5"]),
        "var95": pct(-q95 * s5), "var99": pct(-q99 * s5),
        "ovx_elasticity": [round(float(v), 3) for v in wk["b_ovx"]],
        "rv_elasticity": [round(float(a + b + c), 3) for a, b, c in zip(wk["b_rv5"], wk["b_rv22"], wk["b_rv66"])],
    }


def build_report(snapshot: market_data.MarketSnapshot, *, include_slow: bool = True,
                 progress: Progress | None = None) -> dict[str, Any]:
    ovx: np.ndarray | None
    r, dates, ovx, ohlc = market_data.research_inputs(snapshot)
    ovx = vf._pick_ovx(ovx)
    if ovx is None:
        raise RuntimeError("OVX history too short for the research lab")
    design = build_design(r, dates, ovx, ohlc)
    frame = walk_forward(design, include_slow=include_slow, progress=progress)
    challenger = choose_challenger(frame)
    conf = conformal_report(frame, challenger)
    var = var_backtest(frame, models=tuple(dict.fromkeys((CHAMPION, challenger, "gjr", "ewma", "raw_ovx"))))
    grid0 = frame[frame["grid"] == 0]
    board = leaderboard(frame)
    selection = oos_selection(frame)
    report = {
        "generated_from": snapshot.manifest,
        "ovx_source_agreement": market_data.ovx_source_agreement(snapshot),
        "config": {
            "horizon_sessions": vf.H, "step": vf.STEP, "min_train_rows": vf.MIN_TRAIN,
            "purge_rows": vf.H - 1, "ovx_lag_sessions": vf.OVX_LAG_SESSIONS,
            "evaluation": "every OOS day (overlapping 5-session targets), plus each weekday grid on its own",
            "mcs": {"size": MCS_SIZE, "reps": MCS_REPS, "block_daily": MCS_BLOCK, "block_weekly": MCS_BLOCK_WEEKLY,
                    "statistic": "range", "bootstrap": "stationary", "seed": SEED},
            "conformal": {"method": "adaptive conformal inference (Gibbs-Candes 2021) with 5-session delayed feedback",
                          "alpha": CONFORMAL_ALPHA, "gamma": CONFORMAL_GAMMA, "window_days": CONFORMAL_WINDOW},
            "selection_window_weeks": SELECTION_WINDOW, "warmup_weeks": WARMUP,
            "champion": CHAMPION, "challenger": challenger, "combo_members": list(COMBO_MEMBERS),
            "forecasts": ("losses and VaR use variance-consistent forecasts (log models scaled by exp(s^2)); "
                          "direction and intervals use median forecasts"),
        },
        "sample": {"n_days": int(len(frame)), "n_weeks": int(len(grid0)),
                   "first": frame["date"].min().strftime("%Y-%m-%d"),
                   "last": frame["date"].max().strftime("%Y-%m-%d")},
        "models": [m.__dict__ for m in MODELS],
        "leaderboard": board,
        "oos_selection": selection,
        "var_backtest": var,
        "conformal": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in conf.items()},
        "yearly": yearly(frame, challenger),
        "noise_floor": noise_floor(r, dates),
        "har_iv_coefficients": coefficient_report(design),
        "vol_cone": vol_cone(r, dates, float(ovx[-1]) if np.isfinite(ovx[-1]) else None),
        "series": chart_series(frame, conf, challenger),
    }
    return report


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if not np.isfinite(v) else v
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _last_place(value: float) -> float:
    """One unit in the last decimal place of a published (rounded) number; 0 for full precision."""
    text = repr(value)
    if "e" in text or "E" in text or "." not in text:
        return 0.0
    decimals = len(text.split(".")[1])
    return 10.0 ** -decimals if decimals < 15 else 0.0


def compare_reports(committed: Any, rebuilt: Any) -> dict[str, Any]:
    """How a rebuilt artifact differs from the committed one (both as parsed JSON).

    `violations` lists every difference that is not floating-point noise: a key, length, string,
    flag, null or integer that differs, or a number off by more than REPRO_REL_TOL relative and more
    than one unit in the last decimal it is published with (a value rounded to 3 places may land on
    the other side of a rounding boundary). Empty means every conclusion reproduces."""
    violations: list[str] = []
    numeric: list[tuple[float, str]] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key in a and key in b:
                    walk(a[key], b[key], f"{path}/{key}")
                else:
                    violations.append(f"{path}/{key}: present on one side only")
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                violations.append(f"{path}: length {len(a)} != {len(b)}")
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}[{i}]")
        elif isinstance(a, float) and isinstance(b, float):
            if a != b:
                rel = abs(a - b) / max(abs(a), abs(b))
                numeric.append((rel, path))
                if rel > REPRO_REL_TOL and abs(a - b) > _last_place(a) * (1 + 1e-9):
                    violations.append(f"{path}: committed {a!r}, rebuilt {b!r}")
        elif type(a) is not type(b) or a != b:
            violations.append(f"{path}: committed {a!r}, rebuilt {b!r}")

    walk(committed, rebuilt, "")
    numeric.sort(reverse=True)
    return {"identical": committed == rebuilt, "numbers_differing": len(numeric),
            "largest_relative_difference": numeric[0][0] if numeric else 0.0,
            "largest_at": numeric[0][1] if numeric else None, "violations": violations}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Volatility research lab (benchmarks, MCS, VaR, conformal).")
    parser.add_argument("--refresh", action="store_true", help="download a new data snapshot first")
    parser.add_argument("--out", default="data/vol_lab.json", help="artifact path (allowlisted)")
    args = parser.parse_args(argv)
    out_path = data_json_path(args.out)
    if args.refresh:
        now = datetime.now(timezone.utc)
        snap, quality = market_data.download(now)
        market_data.write_snapshot(snap, quality, now)
    snapshot = market_data.load_snapshot()
    report = build_report(snapshot, progress=lambda k, n: print(f"  day {k}/{n}", flush=True))
    out_path.write_text(json.dumps(_jsonable(report), indent=1, sort_keys=False) + "\n", encoding="utf-8")
    board = report["leaderboard"]
    print(f"wrote {out_path}: {report['sample']['n_days']} days; MCS(QLIKE) = {board['mcs_qlike_included']}; "
          f"challenger = {report['config']['challenger']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
