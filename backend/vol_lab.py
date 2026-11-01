"""Volatility research lab: benchmark field, forecast evaluation, risk backtests, conformal bands.

The production forecaster (backend/vol_forecast.py, HAR-IV) was specified first and is the model of
record. This module asks the questions a risk desk would ask of it, on the SAME out-of-sample days
(every model forecasts the same 5-session target on every out-of-sample day, with the same purge,
from information available at the test day's 14:30 ET settlement, the decision time; the one
exception is a timing control, below):

  1. Benchmarks. Persistence, raw implied vol (OVX as the forecast), EWMA (RiskMetrics),
     GARCH(1,1)-t and GJR-GARCH(1,1)-t (arch), HAR-RV (Corsi 2009), HAR-RV with a downside
     semivolatility term (Patton-Sheppard 2015), HAR on a range-based realized measure
     (Rogers-Satchell + overnight, Yang-Zhang 2000 style; the test day's own bar enters only up to
     its settlement), an OVX-only regression, HAR-IV (OVX lagged one session), HAR-IV on
     the range measure, a gradient-boosted tree model (LightGBM) on the same features, and an
     equal-weight combination. A timing control, HAR-IV with the test day's own OVX close (which
     settles at 16:15 ET, after the decision), is reported but kept out of every Model Confidence
     Set, the SPA test and the model selection: it could not have been made at the decision time.
  2. Evaluation. QLIKE and MSE on variances, the two losses that rank forecasts consistently under a
     noisy volatility proxy (Patton 2011), plus the realized utility of a volatility-targeting investor
     (Bollerslev, Hood, Huss and Pedersen 2018), applied to VARIANCE-CONSISTENT forecasts: a
     regression in logs estimates the conditional median, which is biased low for a variance loss,
     so each log model's forecast is retransformed with Duan's (1983) smearing estimator computed on
     its own purged training residuals, vol_mean = vol_median * sqrt(mean(exp(2 e))). The Model
     Confidence Set (Hansen, Lunde and Nason 2011; arch.bootstrap.MCS) and Hansen's (2005) studentized
     SPA test against the model of record (forecast_eval.spa_test, on arch's stationary bootstrap);
     Diebold-Mariano tests with Newey-West variance and the
     Harvey-Leybourne-Newbold correction; Mincer-Zarnowitz regressions; a second, range-based
     realized proxy; sub-period and block-size robustness; HAR-IV coefficients with HAC t-statistics.
     Direction accuracy (will next week's realized vol exceed this week's?) is reported only next to
     its noise floor: in a constant-volatility world, where nothing is forecastable, the same pipeline
     scores ~75%, because the 5-session realized vol it compares against is itself noisy.
  3. Honest model choice. A forecaster chosen each day by its trailing out-of-sample loss (only
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
and conclusions drawn on one sampling grid can differ from the next (the QLIKE gap between two models
moved from p = 0.39 to p = 0.003 when the grid moved by one day), so the QLIKE comparisons (each
model's loss, its Diebold-Mariano test against the model of record and its Model Confidence Set
membership) are also run on each of the five 5-session sampling grids (offsets 0-4) separately, and
the report says on how many a result holds. VaR tests, which need non-overlapping outcomes, run only
on the sampling grids.

Every input comes from the pinned snapshot in data/market (backend/market_data.py), so the artifact
data/vol_lab.json reproduces from it offline: `python -m backend.vol_lab` (or `--refresh` to download a
new snapshot first). Under the same numeric environment the rebuild is bit for bit; across BLAS
thread counts and CPU kernels the iterative GARCH fits differ in the last bits, so `compare_reports`
defines agreement: every model set, flag, label and count identical, every number within 1e-4
relative or within one unit of the last decimal it is published with.
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
from scipy import integrate, special, stats

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import numpy.typing as npt
    from arch.univariate.base import ARCHModelFixedResult, ARCHModelResult

    Progress = Callable[[int, int], object]

try:
    from . import market_data
    from . import vol_forecast as vf
    from .contract_calendar import contract_code, last_trade_date, next_contract
    from .forecast_eval import (basel_zone, delayed_aci, christoffersen, dm_test, dq_test, hac_z,
                                kupiec, mincer_zarnowitz, model_confidence_set, mse_var, qlike, spa_test)
    from .safe_paths import data_json_path
except ImportError:  # Direct invocation from backend/
    import market_data  # type: ignore[import-not-found, no-redef]
    import vol_forecast as vf  # type: ignore[import-not-found, no-redef]
    from contract_calendar import contract_code, last_trade_date, next_contract  # type: ignore[import-not-found, no-redef]
    from forecast_eval import (  # type: ignore[import-not-found, no-redef]
        basel_zone, delayed_aci, christoffersen, dm_test, dq_test, hac_z,
        kupiec, mincer_zarnowitz, model_confidence_set, mse_var, qlike, spa_test)
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
    ModelSpec("raw_ovx", "Raw OVX", "implied", "OVX itself (previous session's close) as the forecast: no fitting."),
    ModelSpec("ewma", "EWMA (RiskMetrics)", "time-series", "Exponentially weighted variance, lambda 0.94, all returns since 2000."),
    ModelSpec("garch", "GARCH(1,1)-t", "time-series", "Student-t GARCH(1,1), refit weekly on all returns since 2000."),
    ModelSpec("gjr", "GJR-GARCH(1,1)-t", "time-series", "Asymmetric (leverage) GARCH, Student-t, refit weekly."),
    ModelSpec("har_rv", "HAR-RV", "HAR", "Log-HAR on 5/22/66-session close-to-close realized vol (Corsi 2009)."),
    ModelSpec("har_rs", "HAR-RV + downside semivol", "HAR", "HAR-RV plus 5-session downside semivolatility (Patton-Sheppard 2015)."),
    ModelSpec("har_range", "HAR-range", "HAR", "Log-HAR on a range-based measure: Rogers-Satchell plus squared overnight "
              "return, with the test day's own bar taken only to its settlement."),
    ModelSpec("ovx_only", "OVX-only regression", "implied", "Log-regression of next-week realized vol on OVX alone."),
    ModelSpec("har_iv", "HAR-IV (model of record)", "HAR", "Log-HAR on 5/22/66-session realized vol plus OVX; the production model."),
    ModelSpec("har_iv_sameday", "HAR-IV, same-day OVX (v1 timing)", "HAR",
              "HAR-IV with the test day's own OVX close, which settles ~1h45m after CL: overlaps the target."),
    ModelSpec("har_iv_range", "HAR-IV-range", "HAR", "HAR-IV with the range-based realized measure in place of close-to-close."),
    ModelSpec("lgbm", "LightGBM", "machine learning", "Gradient-boosted trees on all of the above features, retrained quarterly."),
    ModelSpec("combo", "Combination", "combination",
              "Equal-weight pool of the variances of HAR-IV, GJR-GARCH-t and the OVX-only regression."),
]
MODEL_KEYS = [m.key for m in MODELS]
# Models that read information published after the decision: reported (losses, DM) as controls,
# never in a Model Confidence Set, the SPA field or the selection candidates, whose results depend
# on the field they are computed over.
TIMING_CONTROLS = ("har_iv_sameday",)
FEASIBLE = [m for m in MODEL_KEYS if m not in TIMING_CONTROLS]
LOG_MODELS = ("har_rv", "har_rs", "har_range", "ovx_only", "har_iv", "har_iv_sameday", "har_iv_range", "lgbm")
COMBO_MEMBERS = ("har_iv", "gjr", "ovx_only")
SPA_REPS = 5000
UTILITY_SR, UTILITY_GAMMA = 0.4, 2.0   # Bollerslev-Hood-Huss-Pedersen (2018) baseline investor
ROLL_OVERNIGHT_WINDOW = 250            # trailing sessions (roll days left out) for the roll-day overnight proxy


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


def window_index(dates: pd.DatetimeIndex, day: pd.Timestamp) -> int:
    """The position of a session that is in `dates`."""
    return int(np.flatnonzero(dates == day)[0])


def roll_convention_audit(snapshot: market_data.MarketSnapshot) -> dict[str, Any]:
    """Whether Yahoo's CL=F switches contracts on the lab's roll days, checked against EIA's contracts 1 and 2.

    For each contract that expires while both CL=F and EIA's contracts 1 and 2 have data, CL=F should
    equal EIA's contract 1 (which rolls at expiry) on the lab's last session before the roll and on the
    lab's roll day; within one tick, as cl_source_agreement. A contract where it does is counted; one
    where CL=F followed the next contract (EIA's contract 2) before the roll is a mismatch, with the
    session it switched (searched back to the previous roll day); one that cannot be told (no EIA row,
    contracts 1 and 2 within a tick of each other, or CL=F matching neither) is listed as indeterminate.
    Roll days after EIA's last date are counted as not checked.

    What it cannot show: an error in the lab's own calendar (contract_calendar.last_trade_date, tested
    against known expiry dates). EIA's contract 1 rolls with the market, so if the lab's roll day were a
    session early or late, CL=F would still equal contract 1 on both sessions checked.
    """
    if snapshot.eia_futures is None:
        raise ValueError("the snapshot has no EIA files (python -m backend.market_data --add-eia)")
    close = market_data.cents(snapshot.cl["Close"])
    dates = pd.DatetimeIndex(close.index)
    eia = snapshot.eia_futures
    tol = market_data.TICK_TOLERANCE

    def on(day: pd.Timestamp, column: str) -> bool:
        return bool(abs(float(cast(float, close.at[day])) - float(cast(float, eia.at[day, column]))) <= tol)

    checked, on_roll_day, mismatches, indeterminate, unchecked = 0, 0, [], [], 0
    previous = 0                                       # the session after the previous roll (or the first session)
    year, month = dates[0].year, dates[0].month
    while (year, month) <= (dates[-1].year + 1, dates[-1].month):
        expiry: Any = pd.Timestamp(last_trade_date(year, month))     # numpy's stubs reject a Timestamp
        i = int(np.searchsorted(dates, expiry, side="right"))
        year, month, code = *next_contract(year, month), contract_code(year, month)
        if not 0 < i < len(dates):
            continue
        roll, before = dates[i], dates[i - 1]
        start, previous = previous, i
        if roll > eia.index.max():
            unchecked += 1
            continue
        if before < eia.index.min():
            continue
        checked += 1
        facts = {"contract": code, "last_trade": expiry.strftime("%Y-%m-%d"), "lab_roll_day": roll.strftime("%Y-%m-%d")}
        if before not in eia.index or roll not in eia.index:
            indeterminate.append({**facts, "reason": "no EIA row on the session before or on the roll day"})
            continue
        if abs(float(cast(float, eia.at[before, "c1"])) - float(cast(float, eia.at[before, "c2"]))) <= tol:
            indeterminate.append({**facts, "reason": "contracts 1 and 2 within a tick of each other"})
            continue
        if on(before, "c1") and on(roll, "c1"):
            on_roll_day += 1
            continue
        # The first session from which CL=F follows the next contract through the roll day, searched back
        # to the session after the previous roll.
        window = [day for day in dates[start:i + 1] if day in eia.index]
        follows = [on(day, "c2") and not on(day, "c1") if day <= expiry else on(day, "c1") for day in window]
        switch = next((day for k, day in enumerate(window) if all(follows[k:])), None)
        if switch is not None and switch < roll:
            mismatches.append({**facts, "yahoo_switch": switch.strftime("%Y-%m-%d"),
                               "offset_sessions": window_index(dates, switch) - i})
        else:
            indeterminate.append({**facts, "reason": "CL=F matches neither contract on the session before or on the roll day"})
    return {
        "eia_through": eia.index.max().strftime("%Y-%m-%d"),
        "contracts_checked": checked,
        "on_lab_roll_day": on_roll_day,
        "mismatches": mismatches,
        "indeterminate": indeterminate,
        "roll_days_after_eia": unchecked,
    }


def _overnight_variance(o: np.ndarray, c: np.ndarray, dates: pd.DatetimeIndex | None) -> np.ndarray:
    """Squared overnight (previous close to open) log return. On roll days (when `dates` is given)
    the gap is the calendar spread, so it is replaced by the median squared overnight return of the
    non-roll sessions among the previous ROLL_OVERNIGHT_WINDOW sessions (a trailing window that ends
    the session before, roll days left out; at least 20 such sessions, else zero). A gap that cannot
    be computed (no usable open) counts as zero."""
    prev_c = np.r_[np.nan, c[:-1]]
    j2 = np.where(np.isfinite(o - prev_c), (o - prev_c) ** 2, 0.0)
    if dates is not None:
        roll = roll_days(dates)
        clean = pd.Series(np.where(roll, np.nan, j2))
        proxy = clean.rolling(ROLL_OVERNIGHT_WINDOW, min_periods=20).median().shift(1).to_numpy()
        j2 = np.where(roll, np.where(np.isfinite(proxy), proxy, 0.0), j2)
    return j2


def _log_bar(ohlc: pd.DataFrame, *keys: str) -> list[np.ndarray]:
    return [np.log(ohlc[k].to_numpy(dtype=float)) for k in keys]


def range_variance(ohlc: pd.DataFrame, returns: np.ndarray, dates: pd.DatetimeIndex | None = None) -> np.ndarray:
    """Daily variance proxy from the full bar: Rogers-Satchell (drift-robust) plus the squared
    overnight return (roll-safe when `dates` is given, see _overnight_variance), i.e. the
    open-to-close and close-to-open parts of a Yang-Zhang style estimator. Where the bar is unusable
    (no positive open/high/low) the squared close-to-close return is used instead. Element t uses
    only session t's bar and earlier data.

    Session t's High and Low run to the 17:00 ET Globex close, 2.5 hours after the 14:30 ET
    settlement (the Close) at which a forecast is made, so element t is known only after the
    decision of day t. It is the realized proxy for sessions after the decision (the range-based
    target) and enters the features only for sessions before it (see range_features).
    """
    o, h, low, c = _log_bar(ohlc, "Open", "High", "Low", "Close")
    rs = (h - c) * (h - o) + (low - c) * (low - o)
    var = np.clip(rs, 0.0, None) + _overnight_variance(o, c, dates)
    r2 = np.asarray(returns, dtype=float) ** 2
    return np.where(np.isfinite(var), var, r2)


def settlement_range_variance(ohlc: pd.DataFrame, returns: np.ndarray,
                              dates: pd.DatetimeIndex | None = None) -> np.ndarray:
    """The part of session t's range variance known at its own 14:30 ET settlement, the decision
    time: the squared overnight return (as in range_variance) plus the squared open-to-settlement
    return (log Close - log Open; the Open is the session's first trade, the evening before). The
    High and Low are not read: they cover the session to 17:00 ET, after the settlement. Where there
    is no usable open, the squared close-to-close return is used instead."""
    o, c = _log_bar(ohlc, "Open", "Close")
    var = _overnight_variance(o, c, dates) + (c - o) ** 2
    r2 = np.asarray(returns, dtype=float) ** 2
    return np.where(np.isfinite(var), var, r2)


def range_features(v_range: np.ndarray, v_settle: np.ndarray, t: int) -> list[float]:
    """Range-based RV5, RV22 and RV66 (annualized) as known at day t's settlement: the full-bar
    variance of each earlier session (every such bar closed before day t's session opened) and only
    the settlement part of day t's own bar."""
    return [float(np.sqrt((np.sum(v_range[t - w + 1: t]) + v_settle[t]) / w * ANNUAL)) for w in (5, 22, 66)]


@dataclass
class Design:
    r: np.ndarray                  # daily log returns (all sessions)
    dates: pd.DatetimeIndex        # session of each return
    X: np.ndarray                  # rows: RV5, RV22, RV66, OVX (annualized, OVX in vol points)
    y: np.ndarray                  # next-5-session realized vol (annualized)
    tdate: pd.DatetimeIndex        # test-day date of each row
    pos: np.ndarray                # index into r of each row's test day
    XR: np.ndarray                 # range-based RV5, RV22, RV66 as known at the test day's settlement
    v_range: np.ndarray            # daily full-bar range variance (roll-safe), all sessions: the target proxy
    v_settle: np.ndarray           # the part of each session's range variance known at its settlement
    lev: np.ndarray                # 5-session downside semivol
    ovx_same: np.ndarray           # the test day's own OVX close (the timing control only)
    ends: list[int] = field(default_factory=list)   # OOS row indices (every day from MIN_TRAIN on)


def build_design(r: np.ndarray, dates: pd.DatetimeIndex, ovx: np.ndarray, ohlc: pd.DataFrame,
                 offset: int = 0) -> Design:
    """Rows and OOS grid identical to vol_forecast.validate(): same features, target, purge, grid.

    `ovx` is the SAME-DAY aligned OVX series; the design uses it lagged by vf.OVX_LAG_SESSIONS (the
    entry-time-clean convention of the production model) and keeps the same-day value per row only
    for the timing control. The range features read the test day's own bar only up to its
    settlement (range_features); the full bar of the sessions after it is the range-based target.
    """
    ovx = np.asarray(ovx, float)
    X, y, tdate, pos = vf._build_matrix(r, dates, vf.lag_ovx(ovx), return_positions=True)
    v = range_variance(ohlc, r, dates)
    v_settle = settlement_range_variance(ohlc, r, dates)
    XR = np.array([range_features(v, v_settle, t) for t in pos])
    lev = np.array([max(vf._downside_semivol(r[t - 4: t + 1]), 1e-4) for t in pos])
    ovx_same = np.array([ovx[t] if np.isfinite(ovx[t]) and ovx[t] > 0 else X[i, 3] for i, t in enumerate(pos)])
    ends = list(range(vf.MIN_TRAIN + offset, len(X)))        # every out-of-sample day
    return Design(r, dates, X, y, tdate, pos, XR, v, v_settle, lev, ovx_same, ends)


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
                # Each refit is warm-started from the last, so a last-bit difference in a BLAS result
                # (thread count, CPU kernel) would be carried through every later refit, and at the
                # default tolerance it moves published GJR figures. Converging tightly keeps every
                # figure within REPRO_REL_TOL relative or one unit of its last published decimal
                # (compare_reports) across BLAS thread counts and CPU kernels; a figure that sits next
                # to a rounding boundary can still move by that one unit.
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


class _TreeModel:
    """LightGBM as benchmarked. It is fitted on the purged training rows of the first out-of-sample
    row and refitted every LGBM_RETRAIN_WEEKS weeks counted from MIN_TRAIN (it is the slow model). A
    boosted model's in-sample residuals understate its error, so once WARMUP weeks of its own MATURED
    out-of-sample log errors exist, the smearing factor of the latest CONFORMAL_WINDOW of them
    replaces the in-sample one."""

    def __init__(self, feats: np.ndarray, y: np.ndarray, first: int) -> None:
        self.feats, self.y, self.first = feats, y, first
        self.tree: Any = None
        self.fitted_for = -1                         # the row whose purged training set the tree used
        self.in_sample_smear = 1.0
        self.errors: list[tuple[int, float]] = []    # (row, log error), in row order

    def median(self, end: int) -> float:
        """Median forecast for row `end`, from the tree of the latest scheduled fit at or before it."""
        period = LGBM_RETRAIN_WEEKS * vf.STEP
        fit_row = max(self.first, vf.MIN_TRAIN + (end - vf.MIN_TRAIN) // period * period)
        if fit_row != self.fitted_for:
            train_end = fit_row - (vf.H - 1)
            self.tree = _lgbm()
            self.tree.fit(self.feats[:train_end], np.log(self.y[:train_end]))
            fitted = self.tree.predict(self.feats[:train_end])
            self.in_sample_smear = float(np.mean(np.exp(2.0 * (np.log(self.y[:train_end]) - fitted))))
            self.fitted_for = fit_row
        return float(np.exp(self.tree.predict(self.feats[end:end + 1])[0]))

    def oos_smear(self, end: int) -> float | None:
        """Smearing factor of the out-of-sample errors matured by row `end` (rows <= end - H), or None
        while fewer than WARMUP weeks of them exist."""
        matured = np.array([e for row, e in self.errors if row <= end - vf.H][-CONFORMAL_WINDOW:])
        return float(np.mean(np.exp(2.0 * matured))) if matured.size >= WARMUP * vf.STEP else None

    def record(self, end: int, median: float) -> None:
        """Row `end`'s log error; oos_smear uses it only once its outcome has matured."""
        self.errors.append((end, float(np.log(self.y[end]) - np.log(median))))


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
    5-session forward log return (`ret5`, for VaR), the 5-session sampling grid the row belongs to (`grid`,
    0-4; grid 0 is the production validator's), and for every model its variance-consistent
    forecast (column `<model>`, used for losses and risk) and its median forecast (column
    `<model>__med`, used for the direction call and the intervals). Both are annualized vols.

    Each forecast at row `end` uses training rows [0, end - (H - 1)) for the regression models (the
    purge: no training target overlaps the test target) and returns up to and including the test
    day for the time-series models, so nothing after the test day's settlement is used (except the
    test day's OVX close by the timing control). GARCH is re-estimated on grid-0 days and filtered
    daily; the tree model is retrained every quarter.
    """
    X, XR, y = d.X, d.XR, d.y
    designs = _regression_designs(X, XR, d.lev, d.ovx_same)
    feats = np.log(np.column_stack([X, XR, d.lev]))
    garch, gjr = _GarchState(False), _GarchState(True)
    tree = _TreeModel(feats, y, d.ends[0] if d.ends else vf.MIN_TRAIN)
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
            med = tree.median(end)
            oos = tree.oos_smear(end)
            f["lgbm__med"], f["lgbm"] = med, med * float(np.sqrt(tree.in_sample_smear if oos is None else oos))
            tree.record(end, med)
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
            "ret5": float(np.sum(d.r[t + 1: t + 1 + vf.H])),    # sum of the next 5 daily log returns
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
    """Today's forecast from any model in MODELS (the production challenger uses this): see
    _live_forecast for how it matches the benchmarked definition."""
    median, mean = _live_forecast(key, r, dates, ovx_same, ohlc)
    spec = next(m for m in MODELS if m.key == key)
    return {"key": key, "label": spec.label, "forecast_next_week_vol_annualized_pct": round(mean * 100, 1),
            "forecast_median_pct": round(median * 100, 1), "as_of": str(dates[-1].date())}


def _live_forecast(key: str, r: np.ndarray, dates: pd.DatetimeIndex, ovx_same: np.ndarray,
                   ohlc: pd.DataFrame) -> tuple[float, float]:
    """(median, variance-consistent) forecast for the session after the last return, by the
    definition walk_forward benchmarks: the same features (the range features built from today's bar
    only to its settlement), every matured row for training, the same smearing (the tree model's from
    its walk-forward out-of-sample errors). The slow models are refitted today, where the walk-forward
    refits them on a schedule: GARCH and GJR-GARCH weekly (warm-started from the previous fit) and
    LightGBM quarterly. On a day the walk-forward refits both, the two forecasts agree, the GARCH ones
    to the optimizer's tolerance (tests/test_vol_lab.py)."""
    if key not in MODEL_KEYS:
        raise ValueError(f"unknown model {key!r}")
    d = build_design(r, dates, ovx_same, ohlc)
    t = len(r) - 1
    ovx_l = cast("np.ndarray", vf.lag_ovx(np.asarray(ovx_same, float)))
    x_now = np.array([[vf._rvol(r[t - 4: t + 1]), vf._rvol(r[t - 21: t + 1]), vf._rvol(r[t - 65: t + 1]), ovx_l[t]]])
    xr_now = np.array([range_features(d.v_range, d.v_settle, t)])
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
            # The walk-forward's out-of-sample errors over the smearing window (every row of the design
            # has matured by today), then a tree fitted on every row.
            path, n = _TreeModel(feats, d.y, vf.MIN_TRAIN), len(d.y)
            for row in range(max(vf.MIN_TRAIN, n - CONFORMAL_WINDOW), n):
                path.record(row, path.median(row))
            tree = _lgbm()
            tree.fit(feats, np.log(d.y))
            oos = path.oos_smear(n - 1 + vf.H)
            if oos is None:
                oos = float(np.mean(np.exp(2.0 * (np.log(d.y) - tree.predict(feats)))))
            med = float(np.exp(tree.predict(np.log(np.column_stack([x_now, xr_now, lev_now])))[0]))
            out = (med, med * float(np.sqrt(oos)))
        cache[m] = out
        return out

    return forecast(key)


def noise_floor(r: np.ndarray, dates: pd.DatetimeIndex) -> dict[str, Any]:
    """Direction accuracy when there is nothing to forecast: i.i.d. Student-t(5) returns with the
    sample's volatility, run through the identical feature builder and walk-forward (HAR, no OVX).
    It is the production validator's procedure (vol_forecast.noise_floor, same simulations and
    seeds), reported under the lab's key names."""
    floor = vf.noise_floor(r, dates)
    return {"sims": floor["sims"], "process": "i.i.d. Student-t(5), constant volatility (nothing to forecast)",
            "har_direction_acc_pct_mean": floor["dir_acc_pct_mean"],
            "har_direction_acc_pct_range": floor["dir_acc_pct_range"],
            "mean_reversion_acc_pct_mean": floor["mean_reversion_dir_acc_pct_mean"],
            "theoretical_floor_pct": 75.0,
            "note": ("For any continuous i.i.d. series, calling 'up' exactly when the current 5-session vol is "
                     "below its median is right with probability E[max(U, 1-U)] = 75%.")}


# --------------------------------------------------------------------------- the proxy's QLIKE noise floor

QLIKE_FLOOR_BAR_SEED_OFFSET = 1000         # simulated bars: default_rng(NOISE_FLOOR_SEED + 1000 + k), a second stream
RANGE_FLOOR_SETTLE_SHARE = 2.5 / 23        # trading-time share of a session from the 14:30 settlement to 17:00 ET
RANGE_FLOOR_POINTS = (5, 41)               # bridge points: settlement -> 17:00, 18:00 open -> settlement
_FLOOR_LNW = np.linspace(-40.0, 8.0, 6001)  # trapezoid grid in ln(W / nu) for t_qlike_floor


def normal_qlike_floor(n: int = vf.H) -> float:
    """E[X - ln X - 1] with X ~ chi2(n)/n: the expected QLIKE of a forecast equal to the true variance, scored
    against the realized variance of n returns that are normal with a constant variance (the target is not
    demeaned), -digamma(n/2) - ln(2/n). A lower bound for returns that are normal given a variance that does not
    depend on the week's own shocks (Jensen's inequality and the Schur-concavity of E ln sum w_i z_i^2)."""
    return float(np.log(n / 2) - special.digamma(n / 2))


def t_qlike_floor(nu: float, n: int = vf.H) -> float:
    """The same floor for i.i.d. unit-variance Student-t(nu) returns (normal_qlike_floor when nu is infinite):
    -E ln X = int_0^inf (M(s/n)^n - e^-s)/s ds (Frullani), M(u) = E[(1 + 2u(nu-2)/W)^(-1/2)], W ~ chi2(nu),
    with M by the trapezoid rule in ln W. Deterministic, about 10 ms."""
    if not np.isfinite(nu):
        return normal_qlike_floor(n)
    w = np.exp(_FLOOR_LNW) * nu
    weights = np.exp(stats.chi2.logpdf(w, nu) + np.log(w))
    weights = weights / weights.sum()                 # the density of ln W on the grid; < 1e-15 lies outside

    def integrand(s: float) -> float:
        m = float(np.dot(weights, (1.0 + 2.0 * (s / n) * (nu - 2.0) / w) ** -0.5))
        return float((m ** n - np.exp(-s)) / s)

    head = integrate.quad(integrand, 0.0, 1.0, limit=400, epsabs=1e-13)[0]
    tail = integrate.quad(integrand, 1.0, np.inf, limit=400, epsabs=1e-13)[0]
    return float(head + tail)


def noise_floor_draws(r: np.ndarray, k: int) -> np.ndarray:
    """Simulation k of the direction noise floor: vol_forecast.noise_floor's own Student-t(5) returns (the same
    generator, seed and calls; tests/test_vol_lab.py pins the equality)."""
    rng = np.random.default_rng(vf.NOISE_FLOOR_SEED + k)
    return np.asarray(rng.standard_t(5, len(r)) / np.sqrt(5 / 3) * float(np.std(r)), dtype=float)


def t5_day_variance(t5: np.ndarray, sd: float, rng: np.random.Generator) -> np.ndarray:
    """Each Student-t(5) day's variance drawn given its return: T = N sqrt(S'), S' = 5/chi2(5), so
    S' | T = t ~ (5 + t^2) / chi2(6); scaled so the day variances average sd^2 (T has variance 5/3)."""
    t = np.asarray(t5, dtype=float) / sd * np.sqrt(5 / 3)
    return np.asarray((5 + t ** 2) / rng.chisquare(6, len(t)) * (3 / 5) * sd ** 2, dtype=float)


def _bridge(end: np.ndarray, var: np.ndarray, steps: int, rng: np.random.Generator) -> np.ndarray:
    """A Brownian bridge from 0 to `end` (per row) with total variance `var`: its steps + 1 grid points."""
    free = np.concatenate([np.zeros((end.size, 1)),
                           np.cumsum(rng.standard_normal((end.size, steps)) * np.sqrt(var / steps)[:, None], 1)], 1)
    return np.asarray(free - (np.arange(steps + 1)[None, :] / steps) * (free[:, -1:] - end[:, None]), dtype=float)


def _extremes(points: np.ndarray, step_var: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """The path's exact maximum and minimum: between two grid points of a Brownian bridge with variance v,
    max = (x0 + x1 + sqrt((x1 - x0)^2 - 2 v ln U)) / 2, and the minimum by symmetry. (Reading the extremes at
    the grid points only would understate the range.)"""
    x0, x1 = points[:, :-1], points[:, 1:]
    gap, v = (x1 - x0) ** 2, step_var[:, None]
    high = 0.5 * (x0 + x1 + np.sqrt(gap - 2 * v * np.log(rng.random(x0.shape))))
    low = 0.5 * (x0 + x1 - np.sqrt(gap - 2 * v * np.log(rng.random(x0.shape))))
    return high.max(1), low.min(1)


def floor_bars(z: np.ndarray, day_var: np.ndarray, dates: pd.DatetimeIndex, rng: np.random.Generator) -> pd.DataFrame:
    """Daily bars whose settlement-to-settlement log returns are exactly `z`: each day a Brownian bridge with
    variance `day_var`, split at the 18:00 ET reopen (RANGE_FLOOR_SETTLE_SHARE of it after the settlement).
    Open is the reopen; High and Low run from the open to the next 17:00 ET, as range_variance assumes; Close is
    the settlement."""
    share, (n_after, n_day) = RANGE_FLOOR_SETTLE_SHARE, RANGE_FLOOR_POINTS
    after = share * z + np.sqrt(share * (1 - share) * day_var) * rng.standard_normal(len(z))   # settlement -> reopen
    day_high, day_low = _extremes(_bridge(z - after, (1 - share) * day_var, n_day - 1, rng),
                                  (1 - share) * day_var / (n_day - 1), rng)
    after_high, after_low = _extremes(_bridge(after, share * day_var, n_after - 1, rng),
                                      share * day_var / (n_after - 1), rng)
    settle = np.log(50.0) + np.cumsum(z)
    opened = np.r_[np.log(50.0), settle[:-1]] + after
    # Session i's High and Low also cover the next day's first segment, from its settlement to 17:00.
    high = np.maximum(opened + day_high, np.r_[after_high[1:], 0.0] + settle)
    low = np.minimum(opened + day_low, np.r_[after_low[1:], 0.0] + settle)
    return pd.DataFrame({"Open": np.exp(opened), "High": np.exp(high), "Low": np.exp(low), "Close": np.exp(settle)},
                        index=dates)


def _floor_scores(z: np.ndarray, dates: pd.DatetimeIndex, forecast: np.ndarray | float,
                  v_range: np.ndarray | None = None) -> float:
    """Mean QLIKE of `forecast` over every row of the OVX-free feature matrix of `z` from MIN_TRAIN on
    (rows_per_sim; not the lab's 2008- OOS sample: the simulated series have the length and dates of the real
    returns), against the five-return target of `z`, or against the range target when the daily range
    variances `v_range` are given."""
    _, y, _, pos = vf._build_matrix(z, dates, None, return_positions=True)
    y, pos = y[vf.MIN_TRAIN:], pos[vf.MIN_TRAIN:]
    if v_range is not None:
        y = np.array([np.sqrt(np.mean(v_range[t + 1: t + 1 + vf.H]) * ANNUAL) for t in pos])
    f = np.full(y.shape, forecast) if np.isscalar(forecast) else np.asarray(forecast)[pos]
    return float(np.mean(qlike(y, f)))


def _summary(values: list[float]) -> dict[str, Any]:
    # Six decimals, so a value shown at three is rounded once (a 4-dp store would round 0.36353 down to 0.363).
    return {"simulated_mean": round(float(np.mean(values)), 6),
            "simulated_range": [round(float(min(values)), 6), round(float(max(values)), 6)]}


def qlike_floor(d: Design) -> dict[str, Any]:
    """The proxy's QLIKE noise floor: the mean QLIKE of a forecast equal to the true variance of the target week,
    against the five-return proxy and against the range proxy, under stated return processes. It belongs to the
    proxy and the assumed returns, never to a model, and nothing subtracts it from any loss or test.

    The simulations are the direction noise floor's (NOISE_FLOOR_SIMS draws of i.i.d. Student-t(5) returns with
    the sample's volatility, seeds NOISE_FLOOR_SEED + k), scored on every row of the OVX-free feature matrix from
    MIN_TRAIN on (rows_per_sim); the normal ones use the same seeds. Range bars are Brownian bridges through each simulated return (floor_bars), drawn from a second
    stream (seed + QLIKE_FLOOR_BAR_SEED_OFFSET + k) so the first is untouched. 'Kurtosis-matched' is the
    conditional kurtosis of the lab's GARCH(1,1)-t fitted to all returns (chosen after the unconditional kurtosis,
    which puts the floor above most models' losses, and a within-week dispersion match, which a variance moving
    within the week biases low, were found unusable); garch_t_dynamics lets that fitted model's variance move
    within the week, so its floor also counts moves no forecast made at the decision can know."""
    from arch import arch_model

    r, dates, sims = d.r, d.dates, vf.NOISE_FLOOR_SIMS
    sd = float(np.std(r))
    true_vol = sd * vf.ANNUALIZE
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = arch_model(r * GARCH_SCALE, mean="Constant", vol="GARCH", p=1, o=0, q=1, dist="t").fit(
            disp="off", show_warning=False, tol=GARCH_TOL)
    omega, alpha, beta, nu = (float(fit.params[k]) for k in ("omega", "alpha[1]", "beta[1]", "nu"))
    persistence, mean_var = alpha + beta, omega / (1 - alpha - beta)
    scores: dict[str, list[float]] = {"normal": [], "t5": [], "garch": [], "range_normal": [], "range_t5": []}
    ratios: dict[str, list[float]] = {"range_normal": [], "range_t5": []}
    for k in range(sims):
        normal = np.random.default_rng(vf.NOISE_FLOOR_SEED + k).standard_normal(len(r)) * sd
        t5 = noise_floor_draws(r, k)
        scores["normal"].append(_floor_scores(normal, dates, true_vol))
        scores["t5"].append(_floor_scores(t5, dates, true_vol))
        # The fitted GARCH(1,1)-t with its variance moving; the forecast is its conditional mean of the target.
        eps = np.random.default_rng(vf.NOISE_FLOOR_SEED + k).standard_t(nu, len(r)) / np.sqrt(nu / (nu - 2))
        h, x = np.empty(len(r) + 1), np.empty(len(r))
        h[0] = mean_var
        for i in range(len(r)):
            x[i] = np.sqrt(h[i]) * eps[i]
            h[i + 1] = omega + alpha * x[i] ** 2 + beta * h[i]
        expected = np.mean([mean_var + persistence ** j * (h[1:] - mean_var) for j in range(vf.H)], axis=0)
        scores["garch"].append(_floor_scores(x / GARCH_SCALE, dates, np.sqrt(expected) / GARCH_SCALE * vf.ANNUALIZE))
        # Range proxy: bars through the same returns. A Student-t(5) day's variance is drawn given its return
        # (T = N sqrt(S'), S' | t ~ (5 + t^2) / chi2(6)), so every bar keeps the noise floor's draw.
        bars_rng = np.random.default_rng(vf.NOISE_FLOOR_SEED + QLIKE_FLOOR_BAR_SEED_OFFSET + k)
        for name, z, day_var in (("range_normal", normal, np.full(len(r), sd ** 2)),
                                 ("range_t5", t5, t5_day_variance(t5, sd, bars_rng))):
            v = range_variance(floor_bars(z, day_var, dates, bars_rng), z, None)
            scores[name].append(_floor_scores(z, dates, true_vol, v_range=v))
            _, _, _, pos = vf._build_matrix(z, dates, None, return_positions=True)
            proxy = np.array([np.mean(v[t + 1: t + 1 + vf.H]) for t in pos[vf.MIN_TRAIN:]])
            ratios[name].append(float(np.mean(proxy) / sd ** 2))
    ends = np.asarray(d.ends)
    close_var = d.y[ends] ** 2
    range_var = np.array([np.mean(d.v_range[t + 1: t + 1 + vf.H]) * ANNUAL for t in d.pos[ends]])
    kurtosis = 3 + 6 / (nu - 4)
    _, _, _, pos = vf._build_matrix(r, dates, None, return_positions=True)
    return {
        "what": ("The mean QLIKE of a forecast equal to the true variance of the target week, scored against each "
                 "realized-vol proxy: the proxy's noise floor. It belongs to the proxy and the assumed returns, not "
                 "to any model, and is never subtracted from any loss or test."),
        "horizon_sessions": vf.H, "sims": sims, "seed": vf.NOISE_FLOOR_SEED,
        "bar_seed_offset": QLIKE_FLOOR_BAR_SEED_OFFSET,
        "seeds": "simulation k uses default_rng(seed + k); its range bars use default_rng(seed + bar_seed_offset + k)",
        "rows_per_sim": int(len(pos) - vf.MIN_TRAIN),
        "five_return": {
            "proxy": "realized vol of the 5 target sessions' close-to-close returns, not demeaned",
            "normal": {"closed_form": normal_qlike_floor(vf.H), "formula": "-digamma(5/2) - ln(2/5)",
                       "lower_bound": "for returns normal given a variance that does not depend on the week's own shocks",
                       **_summary(scores["normal"])},
            "student_t5": {"df": 5, "kurtosis": 9.0,
                           "process": "i.i.d. Student-t(5), constant volatility (nothing to forecast)",
                           "exact": t_qlike_floor(5.0), **_summary(scores["t5"])},
            "kurtosis_matched": {"df": round(nu, 2), "kurtosis": round(kurtosis, 2), "exact": t_qlike_floor(nu),
                                 "source": ("conditional kurtosis of the GARCH(1,1)-t fit to all returns (the lab's "
                                            "GARCH spec); i.i.d. Student-t returns with that kurtosis and constant "
                                            "variance")},
            "garch_t_dynamics": {"process": ("the fitted GARCH(1,1)-t itself; the forecast is its conditional mean of "
                                             "the proxy, so the floor includes variance moves within the week that no "
                                             "forecast made at the decision can know"),
                                 **_summary(scores["garch"])},
        },
        "range": {
            "proxy": "range_variance of the 5 target sessions (Rogers-Satchell plus the overnight return), not rescaled",
            "bar": ("Brownian bridge through each simulated return; High/Low to 17:00 ET, 2.5 h after the 14:30 "
                    "settlement; share 2.5/23 of the day's variance after the settlement; exact extremes between "
                    "bridge points"),
            "normal": {**_summary(scores["range_normal"]),
                       "proxy_to_true_variance": round(float(np.mean(ratios["range_normal"])), 6)},
            "student_t5": {**_summary(scores["range_t5"]),
                           "proxy_to_true_variance": round(float(np.mean(ratios["range_t5"])), 6)},
            "data_proxy_to_close_variance": round(float(np.mean(range_var) / np.mean(close_var)), 6),
        },
    }


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


def _mcs(losses: pd.DataFrame, block: int) -> dict[str, Any]:
    """The Model Confidence Set at the lab's settings (MCS_SIZE, MCS_REPS, SEED; config.mcs records
    them) with mean bootstrap block length `block`."""
    return model_confidence_set(losses, block=block, size=MCS_SIZE, reps=MCS_REPS, seed=SEED)


def realized_utility(actual: npt.ArrayLike, forecast: npt.ArrayLike, sr: float = UTILITY_SR,
                     gamma: float = UTILITY_GAMMA) -> np.ndarray:
    """Per-period realized utility of a mean-variance investor who sizes a position by the vol
    forecast (Bollerslev, Hood, Huss and Pedersen 2018): (SR^2 / gamma) * (y/f - (y/f)^2 / 2), in
    annualized units. Maximized at f = y (SR^2 / (2 gamma)); free of the noise in realized returns."""
    ratio = np.asarray(actual, float) / np.asarray(forecast, float)
    return cast("np.ndarray", (sr ** 2 / gamma) * (ratio - 0.5 * ratio ** 2))


def _spa_summary(spa: dict[str, Any]) -> dict[str, Any]:
    """The parts of an SPA result a robustness check reports."""
    return {"p_consistent": spa["p_consistent"], "best_model": spa["best_model"],
            "p_consistent_non_studentized": spa["non_studentized"]["p_consistent"]}


def spa_vs_champion(losses: pd.DataFrame, block: int = MCS_BLOCK, reps: int = SPA_REPS,
                    seed: int = SEED) -> dict[str, Any]:
    """Hansen (2005) Superior Predictive Ability: is ANY model in the field better than the model of
    record, after accounting for having searched over all of them? Small p: some model beats it.
    Studentized, as Hansen defines it (forecast_eval.spa_test); `non_studentized` keeps the p-values
    arch.bootstrap.SPA 8.0.0 returns on the same draws, which ignore its studentize flag."""
    others = losses.drop(columns=[CHAMPION, *TIMING_CONTROLS], errors="ignore")
    result = spa_test(losses[CHAMPION], others, block=block, reps=reps, seed=seed)
    return {**result, "models": list(others.columns),
            "note": "excludes the timing control (same-day OVX), which uses information published after the decision"}


def leaderboard(frame: pd.DataFrame) -> dict[str, Any]:
    """Every model on every OOS day, plus the same comparison on each 5-session sampling grid on its own. Every
    Model Confidence Set is computed over the FEASIBLE models; the timing controls keep their losses
    and DM tests, with null MCS fields."""
    y = frame["actual"].to_numpy()
    LQ, LM = _losses(frame)
    mcs_q = _mcs(LQ[FEASIBLE], MCS_BLOCK)
    mcs_m = _mcs(LM[FEASIBLE], MCS_BLOCK)
    grids = {}
    for g in range(vf.STEP):
        mask = (frame["grid"] == g).to_numpy()
        LQg = LQ[mask].reset_index(drop=True)
        grids[g] = {"mcs": _mcs(LQg[FEASIBLE], MCS_BLOCK_WEEKLY)["included"],
                    "dm": {m: dm_test(LQg[CHAMPION], LQg[m]) for m in MODEL_KEYS if m != CHAMPION},
                    "qlike": {m: float(LQg[m].mean()) for m in MODEL_KEYS}}
    mr = _mr_hit(frame)
    U = pd.DataFrame({m: realized_utility(y, frame[m].to_numpy()) for m in MODEL_KEYS})
    LQr, _ = _losses(frame, "actual_range")
    mcs_range = _mcs(LQr[FEASIBLE], MCS_BLOCK)
    rows: list[dict[str, Any]] = []
    for spec in MODELS:
        m = spec.key
        control = m in TIMING_CONTROLS
        hit = _hit(frame, m)
        z_dir, _ = hac_z(hit - mr)
        mz = mincer_zarnowitz(y, frame[m].to_numpy(), maxlags=10)
        dm_grid = [grids[g]["dm"][m]["stat"] for g in range(vf.STEP)] if m != CHAMPION else None
        rows.append({
            "key": m, "label": spec.label, "family": spec.family, "description": spec.description,
            "timing_control": control,
            "qlike": round(float(LQ[m].mean()), 4),
            "qlike_median_forecast": (round(float(qlike(y, frame[f"{m}__med"].to_numpy()).mean()), 4)
                                      if m in LOG_MODELS or m == "combo" else None),
            "qlike_by_grid": [round(grids[g]["qlike"][m], 4) for g in range(vf.STEP)],
            "mse_var": round(float(LM[m].mean()), 5),
            "realized_utility_pct": round(float(U[m].mean()) * 100, 3),
            "dm_utility_vs_har_iv": None if m == CHAMPION else dm_test(-U[CHAMPION], -U[m], horizon=vf.H),
            "qlike_range_proxy": round(float(LQr[m].mean()), 4),
            "in_mcs_qlike_range_proxy": None if control else m in mcs_range["included"],
            "dm_qlike_vs_har_iv": None if m == CHAMPION else dm_test(LQ[CHAMPION], LQ[m], horizon=vf.H),
            "dm_qlike_vs_har_iv_by_grid": None if m == CHAMPION else [round(v, 2) for v in cast("list[float]", dm_grid)],
            "dm_mse_vs_har_iv": None if m == CHAMPION else dm_test(LM[CHAMPION], LM[m], horizon=vf.H),
            "direction_acc_pct": None if m == "persistence" else round(float(hit.mean()) * 100, 1),
            "direction_z_vs_mean_reversion": None if m == "persistence" else round(float(z_dir), 2),
            "mean_ratio_actual_to_forecast": round(float(np.mean(y / frame[m].to_numpy())), 3),
            "mincer_zarnowitz": {k: round(v, 4) for k, v in mz.items()},
            "mcs_qlike_p": None if control else mcs_q["p_values"][m],
            "in_mcs_qlike": None if control else m in mcs_q["included"],
            "mcs_mse_p": None if control else mcs_m["p_values"][m],
            "in_mcs_mse": None if control else m in mcs_m["included"],
            "in_mcs_qlike_grids": None if control else int(sum(m in grids[g]["mcs"] for g in range(vf.STEP))),
        })
    rows.sort(key=lambda r: r["qlike"])
    for rank, row in enumerate(rows, 1):
        row["rank_qlike"] = rank
    robustness = {f"block_{b}": _mcs(LQ[FEASIBLE], b)["included"] for b in (10, 40)}
    spa_robustness = {f"block_{b}": _spa_summary(spa_vs_champion(LQ, block=b)) for b in (10, 40)}
    for start, end in (("2008-01-01", "2016-12-31"), ("2017-01-01", "2099-12-31")):
        mask = ((frame["date"] >= start) & (frame["date"] <= end)).to_numpy()
        label = f"{start[:4]}-{min(int(end[:4]), int(frame['date'].max().year))}"
        robustness[label] = {"n_days": int(mask.sum()),
                             "included": _mcs(LQ[FEASIBLE][mask].reset_index(drop=True), MCS_BLOCK)["included"]}
        spa_robustness[label] = {"n_days": int(mask.sum()),
                                 **_spa_summary(spa_vs_champion(LQ[mask].reset_index(drop=True)))}
    up = (frame["actual"] > frame["rv5"]).to_numpy()
    return {"models": rows, "mcs_qlike_included": mcs_q["included"], "mcs_mse_included": mcs_m["included"],
            "mcs_qlike_range_proxy_included": mcs_range["included"],
            "mcs_excludes": list(TIMING_CONTROLS),
            "mcs_note": ("Every Model Confidence Set is computed over the feasible models only. har_iv_sameday "
                         "reads the test day's OVX close (16:15 ET), published after the 14:30 ET decision, and "
                         "MCS p-values depend on the whole field, so it would push feasible models out; its "
                         "losses and DM tests are reported as a timing control."),
            "spa_qlike_vs_har_iv": spa_vs_champion(LQ),
            "spa_robustness_qlike": spa_robustness,
            "realized_utility_max_pct": round(UTILITY_SR ** 2 / (2 * UTILITY_GAMMA) * 100, 3),
            "mcs_qlike_by_grid": {str(g): grids[g]["mcs"] for g in range(vf.STEP)},
            "mcs_robustness_qlike": robustness, "n_days": int(len(frame)),
            "base_rate_pct": round(float(max(up.mean(), 1 - up.mean())) * 100, 1),
            "mean_reversion_acc_pct": round(float(mr.mean()) * 100, 1)}


def oos_selection(frame: pd.DataFrame) -> dict[str, Any]:
    """Each day, use the model with the lowest trailing QLIKE over the previous SELECTION_WINDOW weeks
    of MATURED outcomes (rows at least H sessions old); HAR-IV until a year of them exists. Measures
    the value of choosing among the models without hindsight."""
    cands = [m for m in FEASIBLE if m != "persistence"]
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
    """Weekly (5-session) VaR for a long position, VaR = -q * sigma_5d, on each 5-session sampling grid (VaR
    tests need non-overlapping outcomes). q is the normal quantile, or (filtered historical
    simulation) the empirical quantile of the grid's PAST standardized outcomes ret5 / sigma_5d
    (after WARMUP weeks). Unconditional historical simulation (trailing quantile of past 5-session
    log returns, no volatility model) is the benchmark. Every method is scored on the same weeks."""
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
                    methods.append(_var_scores(f"{m}:{kind}", m, kind, p, ret, -q * s5, eval_idx))
            hs = np.array([np.quantile(ret[max(0, i - SELECTION_WINDOW): i], p) if i >= WARMUP else np.nan
                           for i in range(n)])
            methods.append(_var_scores("historical_simulation", None, "historical_simulation", p, ret, -hs,
                                       eval_idx))
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
                idx: np.ndarray) -> dict[str, Any]:
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
    """The challenger: the lowest-QLIKE model in the 90% Model Confidence Set of the feasible models,
    other than the model of record (or the lowest-QLIKE eligible model if the set holds none). The
    choice is made on the full 2008-present sample, so the challenger's historical edge is in-sample to
    the choice; its forward live record is the clean test (see the promotion rule in docs/RESULTS.md, section 3)."""
    LQ, _ = _losses(frame)
    eligible = set(FEASIBLE) - {CHAMPION}
    included = set(_mcs(LQ[FEASIBLE], MCS_BLOCK)["included"]) & eligible
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
        "ret5": pct(wk["ret5"]),                  # 5-session log return: the sum of the daily log returns
        "var95": pct(-q95 * s5), "var99": pct(-q99 * s5),
        "ovx_elasticity": [round(float(v), 3) for v in wk["b_ovx"]],
        "rv_elasticity": [round(float(a + b + c), 3) for a, b, c in zip(wk["b_rv5"], wk["b_rv22"], wk["b_rv66"])],
    }


def build_report(snapshot: market_data.MarketSnapshot, *, progress: Progress | None = None) -> dict[str, Any]:
    ovx: np.ndarray | None
    r, dates, ovx, ohlc = market_data.research_inputs(snapshot)
    ovx = vf._pick_ovx(ovx)
    if ovx is None:
        raise RuntimeError("OVX history too short for the research lab")
    design = build_design(r, dates, ovx, ohlc)
    frame = walk_forward(design, progress=progress)
    challenger = choose_challenger(frame)
    conf = conformal_report(frame, challenger)
    var = var_backtest(frame, models=tuple(dict.fromkeys((CHAMPION, challenger, "gjr", "ewma", "raw_ovx"))))
    grid0 = frame[frame["grid"] == 0]
    board = leaderboard(frame)
    selection = oos_selection(frame)
    report = {
        "generated_from": snapshot.manifest,
        "ovx_source_agreement": market_data.ovx_source_agreement(snapshot),
        "cl_source_agreement": market_data.cl_source_agreement(snapshot, frame["date"].min().strftime("%Y-%m-%d")),
        "roll_convention_audit": roll_convention_audit(snapshot),
        "config": {
            "horizon_sessions": vf.H, "step": vf.STEP, "min_train_rows": vf.MIN_TRAIN,
            "purge_rows": vf.H - 1, "ovx_lag_sessions": vf.OVX_LAG_SESSIONS,
            "evaluation": "every OOS day (overlapping 5-session targets), plus each 5-session sampling grid on its own",
            "mcs": {"size": MCS_SIZE, "reps": MCS_REPS, "block_daily": MCS_BLOCK, "block_weekly": MCS_BLOCK_WEEKLY,
                    "statistic": "range", "bootstrap": "stationary", "seed": SEED},
            "conformal": {"method": "adaptive conformal inference (Gibbs-Candes 2021) with 5-session delayed feedback",
                          "alpha": CONFORMAL_ALPHA, "gamma": CONFORMAL_GAMMA, "window_days": CONFORMAL_WINDOW},
            "selection_window_weeks": SELECTION_WINDOW, "warmup_weeks": WARMUP,
            "champion": CHAMPION, "challenger": challenger, "combo_members": list(COMBO_MEMBERS),
            "forecasts": ("losses and VaR use variance-consistent forecasts (each log model's median scaled by "
                          "sqrt(mean(exp(2e))) over its own residuals e: Duan's smearing estimator); "
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
        "qlike_floor": qlike_floor(design),
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
    parser.add_argument("--out", default=f"data/{ARTIFACT}", help="artifact path (allowlisted)")
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
    print(f"wrote {args.out}: {report['sample']['n_days']} days; MCS(QLIKE) = {board['mcs_qlike_included']}; "
          f"challenger = {report['config']['challenger']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
