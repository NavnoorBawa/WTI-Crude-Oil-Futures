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
    from .forecast_eval import (basel_zone, batch_means_test, berkowitz_censored, berkowitz_test, bonferroni_min,
                                cauchy_combination, coverage_by_group_test, critical_value_mc_se, crps_from_quantiles,
                                delayed_aci, christoffersen, dm_test, dq_test, fluctuation_null, fluctuation_reference,
                                fluctuation_test, gw_test, hac_lrv, hac_z, kupiec, mincer_zarnowitz, model_confidence_set,
                                mse_var, newey_west_lrv, nw94_bandwidth, pinball_loss, qlike, rank_pit_null, rearrange,
                                rolling_dm, sample_pit, spa_test, trailing_quantiles)
    from .safe_paths import data_json_path
except ImportError:  # Direct invocation from backend/
    import market_data  # type: ignore[import-not-found, no-redef]
    import vol_forecast as vf  # type: ignore[import-not-found, no-redef]
    from contract_calendar import contract_code, last_trade_date, next_contract  # type: ignore[import-not-found, no-redef]
    from forecast_eval import (  # type: ignore[import-not-found, no-redef]
        basel_zone, batch_means_test, berkowitz_censored, berkowitz_test, bonferroni_min, cauchy_combination,
        coverage_by_group_test, critical_value_mc_se, crps_from_quantiles, delayed_aci, christoffersen, dm_test, dq_test,
        fluctuation_null, fluctuation_reference, fluctuation_test, gw_test, hac_lrv, hac_z, kupiec, mincer_zarnowitz,
        model_confidence_set, mse_var, newey_west_lrv, nw94_bandwidth, pinball_loss, qlike, rank_pit_null, rearrange,
        rolling_dm, sample_pit, spa_test, trailing_quantiles)
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
# Regimes at the decision (ROADMAP 2.2): labels read only what is known at the 14:30 ET settlement of session t;
# cut points come from strictly earlier sessions. Reported against the band and the losses, never read by them.
REGIMES = {"ovx_tercile": ("low", "mid", "high"), "ovx_rv_gap": ("at_or_below", "above"),
           "rv5_tercile": ("low", "mid", "high"), "target_spans_roll": ("no", "yes")}
REGIME_MIN_HISTORY = 250         # earlier values a cut point needs (vf.MIN_TRAIN, one year)
REGIME_GAP_WINDOW = 250          # trailing sessions for the OVX/RV5 gap's median
REGIME_HAC_LAGS = 10             # the lab's HAC lag count for overlapping 5-session targets (coefficient_report)
REGIME_ROBUST_LAGS = (20, 40)
# Conditional predictive ability and stability over time (ROADMAP 2.3), reported against every model's QLIKE
# difference with the model of record, never read by the challenger's choice, the band or the leaderboard.
GW_LAGS = vf.H - 1               # Giacomini-White's truncated kernel at tau - 1: under their null the moments are MA(4)
GW_ROBUST = (("bartlett", 10), ("bartlett", 40))
FLUCT_MUS = (0.1, 0.2, 0.3, 0.4, 0.5)   # every window share the prototypes looked at; the ledger p adjusts over them
FLUCT_MU = 0.2                   # the window drawn in STAB: the shortest whose size holds under every MA(4) design
FLUCT_ROBUST = ("bartlett", 40)  # each window's variance allowing dependence beyond the 5-session overlap
FLUCT_NULL_DRAWS = 49_999        # (draws + 1) * 0.05 and * 0.10 are integers: exact critical values
FLUCT_SEED_TAG = 23              # null seed [SEED, FLUCT_SEED_TAG, n], fixed before its values were computed
GR2010_TABLE1 = {0.1: (3.393, 3.170), 0.2: (3.179, 2.948), 0.3: (3.012, 2.766), 0.4: (2.890, 2.626),
                 0.5: (2.779, 2.500)}   # Giacomini and Rossi (2010) Table 1, two-sided 5% and 10%
STABILITY_SERIES = ("challenger", "ovx_only", "raw_ovx")                # weekly rolling-t paths
CUMULATIVE_SERIES = ("challenger", "ovx_only", "raw_ovx", "persistence")  # weekly cumulative differences
VAR_LEVELS = (0.05, 0.01)
# The whole predictive distribution (ROADMAP 2.4): every setting below was fixed before the artifact was rebuilt.
DIST_TAUS = tuple(round(0.05 * k, 2) for k in range(1, 20))   # 19 levels; grid CRPS = their mean quantile score
DIST_PINBALL_TAUS = (0.10, 0.90)
DIST_MEDIAN = DIST_TAUS.index(0.5)
DIST_DECILES = tuple(DIST_TAUS.index(round(0.1 * k, 1)) for k in range(1, 10))   # the PIT histogram's bin edges
DIST_SIM_LOG2 = 14               # GARCH-family predictive: 2^14 scrambled Sobol paths of 5 sessions
DIST_SEED_TAG = 24               # seeds [SEED, 24] (Sobol), [SEED, 24, 1, k] (rank-PIT null), [SEED, 24, n(, k)] (tails)
DIST_DM_GROUPS = 12              # batch-means DM (Ibragimov and Mueller 2010): 12 consecutive periods of ~17 months
DIST_DM_GROUPS_CONTEXT = (8, 16)
DIST_ACF_LAGS = (5, 10, 20, 40)
DIST_NULL_DRAWS = 10_000         # simulated null of the trailing-rank PIT's per-grid Berkowitz LR
DIST_NULL_BIN_DRAWS = 2_000      # ...of which the first 2,000 give each PIT bin's chance spread
DIST_TAIL_SEEDS = 100            # the quantile HAR's randomized-tail PIT (context): its spread over seeds
DIST_MCS_BLOCKS = (20, 40, 80)   # Model Confidence Set on the grid CRPS (context)
GARCH_PARAMS = ("mu", "omega", "alpha", "gamma", "beta", "nu")
QHAR_KEY = "quantile_har"        # a robustness competitor: never in MODELS, FEASIBLE or the live pair
QHAR_LABEL = "Quantile HAR-IV (not in the benchmark)"
QHAR_RETRAIN_WEEKS = 13          # LightGBM's quarterly schedule (LGBM_RETRAIN_WEEKS)
EWMA_LAMBDA = 0.94               # RiskMetrics daily decay
GARCH_SCALE = 100.0              # arch fits percent returns for numerical stability
GARCH_TOL = 1e-9                 # optimizer tolerance: converge to the optimum, not near it (below)
REPRO_REL_TOL = 1e-4             # reproduction check: relative agreement required of every number
SIMULATED_P_DRAWS = 10           # ...and of the fluctuation test's p-values, which count simulated draws: a sup that
                                 # moves past one draw (a GARCH fit's last bits on another CPU) moves p by 1/(B + 1)
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
        # The parameters and one-step variance (percent^2) behind the latest forecast: its predictive distribution.
        self.last: tuple[np.ndarray, float] | None = None

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
        self.last = (np.asarray(res.params, dtype=float).copy(), float(var[0]))
        return float(np.sqrt(np.mean(var) * ANNUAL) / GARCH_SCALE)


def _garch_columns(key: str, params: np.ndarray, h1: float, asymmetric: bool) -> dict[str, float]:
    """The frame's `<key>__h1` (one-step variance, percent^2) and `<key>__<param>` columns: what garch_predictive
    simulates from. arch orders the parameters mu, omega, alpha, [gamma,] beta, nu; GARCH's gamma is 0."""
    values = (h1, params[0], params[1], params[2], params[3] if asymmetric else 0.0, params[-2], params[-1])
    return {f"{key}__{name}": float(v) for name, v in zip(("h1", *GARCH_PARAMS), values)}


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
    daily; the tree model is retrained every quarter. With the slow models, the GARCH family's parameters in
    force and one-step variance are kept too (`<key>__h1`, `<key>__mu`, ...: _garch_columns), for their
    predictive distributions; nothing that ranks, bands or selects a model reads them.
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
                params, h1 = cast("tuple[np.ndarray, float]", state.last)
                f.update(_garch_columns(key, params, h1, state.o == 1))
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
        # The same quantiles' interval score on the log scale: scale-free, so regimes at different vol levels compare.
        log_score = ((aci["hi"] - aci["lo"]) + (2 / CONFORMAL_ALPHA) * np.maximum(aci["lo"] - e, 0)
                     + (2 / CONFORMAL_ALPHA) * np.maximum(e - aci["hi"], 0))
        out[m] = {"target_coverage_pct": round((1 - CONFORMAL_ALPHA) * 100, 1),
                  "coverage_pct": round(float(hits.mean()) * 100, 1), "n_days": int(hits.size),
                  "interval_score_pct": round(float(np.nanmean(score)) * 100, 2),
                  "median_hi_lo_ratio": round(float(np.nanmedian(width)), 2),
                  "coverage_by_year_pct": by_year,
                  "next_alpha": round(aci["alpha_next"], 4),
                  "next_log_bounds": [round(aci["next_lo"], 4), round(aci["next_hi"], 4)],
                  "_lo": aci["lo"], "_hi": aci["hi"], "_hit": aci["hit"], "_score": score, "_log_score": log_score}
    return out


def regime_inputs(r: np.ndarray, ovx_same: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Session-indexed values known at each session's 14:30 ET settlement: OVX of the session before (the model's
    lagged input, in vol points), RV5 of the five returns to the session (the model's first term, a decimal), and
    their log gap ln(OVX/100) - ln(RV5); NaN where either is missing."""
    ovx_l = np.asarray(vf.lag_ovx(np.asarray(ovx_same, float)), float)
    r2 = np.asarray(r, float) ** 2
    rv5 = np.full(r2.size, np.nan)
    cum = np.cumsum(np.r_[0.0, r2])
    rv5[vf.H - 1:] = np.sqrt((cum[vf.H:] - cum[:-vf.H]) / vf.H * ANNUAL)
    ok = np.isfinite(ovx_l) & (ovx_l > 0) & np.isfinite(rv5) & (rv5 > 0)
    gap = np.full(r2.size, np.nan)
    gap[ok] = np.log(ovx_l[ok] / 100.0) - np.log(rv5[ok])
    return ovx_l, rv5, gap


def _tercile(value: float, history: np.ndarray) -> tuple[int, tuple[float, float]]:
    """0, 1 or 2 by the thirds of `history` (np.quantile at 1/3 and 2/3); a value on a cut goes to the lower third."""
    if history.size < REGIME_MIN_HISTORY or not np.isfinite(value):
        raise ValueError(f"a regime cut point needs {REGIME_MIN_HISTORY} earlier values")
    c1, c2 = (float(c) for c in np.quantile(history, [1 / 3, 2 / 3]))
    return int(value > c1) + int(value > c2), (c1, c2)


def regime_labels(r: np.ndarray, dates: pd.DatetimeIndex, ovx_same: np.ndarray, pos: np.ndarray) -> dict[str, Any]:
    """Each decision session t in `pos` labelled by REGIMES, reading only sessions up to t (OVX closes up to t - 1)
    and, for the roll, the exchange calendar: OVX and RV5 thirds with cut points from strictly earlier sessions, the
    OVX/RV5 gap against its median over the previous REGIME_GAP_WINDOW sessions, and whether the target's sessions
    t+1..t+5 include a roll day. Raises ValueError where a label would rest on too little history."""
    ovx_l, rv5, gap = regime_inputs(r, ovx_same)
    rolls = roll_days(dates)
    pos = np.asarray(pos, dtype=int)
    labels = {var: np.empty(pos.size, dtype=int) for var in REGIMES}
    medians = np.empty(pos.size)
    cuts: list[dict[str, Any]] = []
    for i, t in enumerate(pos):
        before = ovx_l[:t]
        labels["ovx_tercile"][i], ovx_cut = _tercile(float(ovx_l[t]), before[np.isfinite(before)])
        before = rv5[:t]
        labels["rv5_tercile"][i], rv5_cut = _tercile(float(rv5[t]), before[np.isfinite(before)])
        before = gap[:t]
        before = before[np.isfinite(before)][-REGIME_GAP_WINDOW:]
        if before.size < REGIME_GAP_WINDOW or not np.isfinite(gap[t]):
            raise ValueError(f"the gap's median needs {REGIME_GAP_WINDOW} earlier values")
        medians[i] = float(np.median(before))
        labels["ovx_rv_gap"][i] = int(gap[t] > medians[i])
        labels["target_spans_roll"][i] = int(rolls[t + 1: t + 1 + vf.H].any())
        if i in (0, pos.size - 1):
            cuts.append({"date": dates[t].strftime("%Y-%m-%d"), "ovx": [round(c, 2) for c in ovx_cut],
                         "rv5_pct": [round(c * 100, 1) for c in rv5_cut], "gap_median": round(float(medians[i]), 3)})
    return {"labels": labels, "gap": gap[pos], "gap_median": medians,
            "cut_points": {"first": cuts[0], "last": cuts[-1]}}


def _by_level(values: np.ndarray, label: np.ndarray, levels: tuple[str, ...], digits: int,
              scale: float = 1.0) -> dict[str, float]:
    return {name: round(float(np.mean(values[label == j])) * scale, digits) for j, name in enumerate(levels)}


def _coverage_by_regime(frame: pd.DataFrame, conf: dict[str, Any], labels: dict[str, np.ndarray]) -> dict[str, Any]:
    """One banded model's coverage, misses, interval scores and conditional-coverage tests by regime, on the days
    with a band (after the ACI warm-up)."""
    hit = np.asarray(conf["_hit"], float)
    band = np.isfinite(hit)
    med = conf["_med"]
    act = frame["actual"].to_numpy()
    above = act > med * np.exp(conf["_hi"])
    miss = 1.0 - hit[band]
    grids = frame["grid"].to_numpy()[band]
    out: dict[str, Any] = {key: {} for key in ("coverage_by_regime_pct", "n_days_by_regime", "miss_above_by_regime_pct",
                                              "miss_below_by_regime_pct", "interval_score_by_regime",
                                              "log_interval_score_by_regime", "conditional_coverage")}
    out["log_interval_score"] = round(float(np.mean(conf["_log_score"][band])), 3)
    for var, levels in REGIMES.items():
        label = labels[var][band]
        k = len(levels)
        out["n_days_by_regime"][var] = {name: int((label == j).sum()) for j, name in enumerate(levels)}
        out["coverage_by_regime_pct"][var] = _by_level(1.0 - miss, label, levels, 1, 100.0)
        out["miss_above_by_regime_pct"][var] = _by_level(above[band].astype(float), label, levels, 1, 100.0)
        out["miss_below_by_regime_pct"][var] = _by_level(miss - above[band], label, levels, 1, 100.0)
        out["interval_score_by_regime"][var] = _by_level(conf["_score"][band], label, levels, 2, 100.0)
        out["log_interval_score_by_regime"][var] = _by_level(conf["_log_score"][band], label, levels, 3)
        main = coverage_by_group_test(miss, label, k, CONFORMAL_ALPHA, REGIME_HAC_LAGS)
        robust = {f"hac_lags_{lags}": coverage_by_group_test(miss, label, k, CONFORMAL_ALPHA, lags)["equal"]
                  for lags in REGIME_ROBUST_LAGS}
        by_grid_lags, by_grid_p = [], []
        cells = []
        for j, name in enumerate(levels):
            inside = (label == j) & (miss == 0)
            n_grid = [int(((label == j) & (grids == g)).sum()) for g in range(vf.STEP)]
            in_grid = [int((inside & (grids == g)).sum()) for g in range(vf.STEP)]
            cells.append({"regime": name, "n_days": int((label == j).sum()), "inside": int(inside.sum()),
                          "coverage_pct": out["coverage_by_regime_pct"][var][name],
                          "hac_z": round(main["cells"][j]["z"], 3), "hac_p": round(main["cells"][j]["p_value"], 4),
                          "n_by_grid": n_grid, "inside_by_grid": in_grid,
                          "binomial_p_by_grid": [round(float(stats.binomtest(i_g, n_g, 1 - CONFORMAL_ALPHA).pvalue), 4)
                                                 if n_g else None for i_g, n_g in zip(in_grid, n_grid)]})
        for g in range(vf.STEP):
            on_grid = grids == g
            lags = newey_west_lrv(miss[on_grid])[1]
            by_grid_lags.append(int(lags))
            by_grid_p.append(round(coverage_by_group_test(miss[on_grid], label[on_grid], k, CONFORMAL_ALPHA,
                                                          lags)["equal"]["p_value"], 4))
        out["conditional_coverage"][var] = {
            "wald": round(main["equal"]["wald"], 3), "df": main["equal"]["df"],
            "p_value": round(main["equal"]["p_value"], 4), "hac_lags": REGIME_HAC_LAGS, "cells": cells,
            "robustness": {**{key: {"wald": round(v["wald"], 3), "p_value": round(v["p_value"], 4)}
                              for key, v in robust.items()},
                           "all_cells": {"wald": round(main["all_cells"]["wald"], 3), "df": main["all_cells"]["df"],
                                         "p_value": round(main["all_cells"]["p_value"], 4)},
                           "by_grid": {"hac_lags": by_grid_lags, "p_value": by_grid_p}}}
    return out


def regime_report(frame: pd.DataFrame, conf: dict[str, dict[str, Any]], regimes: dict[str, Any]) -> dict[str, Any]:
    """Coverage and loss by regime (ROADMAP 2.2): the top-level `regimes` block, each banded model's additions to its
    `conformal` entry, and every model's QLIKE by regime over every OOS day. Descriptive except the conditional
    coverage tests; the band, the challenger's choice and the losses never read a label."""
    labels = regimes["labels"]
    act = frame["actual"].to_numpy()
    block = {
        "what": ("Labels at the decision (the 14:30 ET settlement of session t), each from what was known then: OVX "
                 "of session t-1 and RV5 of the five returns to t against cut points from strictly earlier "
                 "sessions, and whether the target window contains a contract roll (the exchange calendar)."),
        "decision": "14:30 ET settlement of session t",
        "variables": {
            "ovx_tercile": {"levels": list(REGIMES["ovx_tercile"]), "value": "OVX close of session t-1 (vol points)",
                            "cut_points": "thirds of every earlier session's value", "min_history": REGIME_MIN_HISTORY},
            "ovx_rv_gap": {"levels": list(REGIMES["ovx_rv_gap"]), "value": "ln(OVX_{t-1}/100) - ln(RV5_t)",
                           "cut_points": "median over the previous sessions", "window_sessions": REGIME_GAP_WINDOW},
            "rv5_tercile": {"levels": list(REGIMES["rv5_tercile"]), "value": "RV5 of the five returns to t",
                            "cut_points": "thirds of every earlier session's value", "min_history": REGIME_MIN_HISTORY},
            "target_spans_roll": {"levels": list(REGIMES["target_spans_roll"]),
                                  "value": "a roll day among the target's sessions t+1..t+5",
                                  "source": "exchange calendar (roll_days)"}},
        "n_days_by_regime": {var: {name: int((labels[var] == j).sum()) for j, name in enumerate(levels)}
                             for var, levels in REGIMES.items()},
        "cut_points": regimes["cut_points"],
        "test": {"form": "equal miss rates across regimes (HAC Wald, chi-squared with K-1 df)",
                 "hac": "Newey-West, Bartlett", "hac_lags": REGIME_HAC_LAGS, "robust_lags": list(REGIME_ROBUST_LAGS)},
        "note": "Reported, never used to change the interval: the band's code never reads these labels.",
    }
    qlike_rows = {m: {var: _by_level(qlike(act, frame[m].to_numpy()), labels[var], levels, 4)
                      for var, levels in REGIMES.items()} for m in MODEL_KEYS if m in frame}
    banded = {m: _coverage_by_regime(frame, {**c, "_med": frame[f"{m}__med"].to_numpy()}, labels)
              for m, c in conf.items()}
    return {"regimes": block, "conformal": banded, "qlike": qlike_rows}


def _mu_key(mu: float) -> str:
    """0.2 -> "mu_0_2" (research_ledger paths split on dots)."""
    return "mu_" + f"{mu:g}".replace(".", "_")


def gw_instruments(d: np.ndarray, pos: np.ndarray, ln_ovx: np.ndarray, gap: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Giacomini-White instruments at each OOS row, all known at its decision: the differential of the row whose
    session is 5 earlier (its target ended at this decision; the 4 rows between have not matured), ln OVX of the
    session before and the OVX/RV5 gap. Returns (x, ok): ok marks rows with a matured lag and finite values."""
    pos = np.asarray(pos, dtype=int)
    n = pos.size
    lag_row = np.minimum(np.searchsorted(pos, pos - vf.H), n - 1)
    has_lag = pos[lag_row] == pos - vf.H
    x = np.column_stack([np.where(has_lag, np.asarray(d, dtype=float)[lag_row], np.nan), ln_ovx, gap])
    return x, has_lag & np.isfinite(x).all(axis=1)


def _gw_block(d: np.ndarray, x: np.ndarray, grid: np.ndarray) -> dict[str, Any]:
    """One model's Giacomini-White block: the full test (context), its conditional part (the ledger's), the OLS
    coefficients in levels, and the robustness. A HAC matrix that is not positive definite publishes p_value null."""
    try:
        main = gw_test(d, x, lags=GW_LAGS)
    except np.linalg.LinAlgError as exc:
        return {"stat": None, "df": x.shape[1] + 1, "p_value": None, "n_days": int(d.size), "lags": GW_LAGS,
                "kernel": "truncated", "conditional": {"stat": None, "df": x.shape[1], "p_value": None},
                "robustness": {f"{kernel}_{lags}": {"p_value": None, "conditional_p_value": None}
                               for kernel, lags in GW_ROBUST},
                "not_computed": str(exc)}
    b = main["coef"]
    robust: dict[str, Any] = {}
    for kernel, lags in GW_ROBUST:
        try:
            alt = gw_test(d, x, lags=lags, kernel=kernel)
            robust[f"{kernel}_{lags}"] = {"p_value": alt["p_value"], "conditional_p_value": alt["conditional"]["p_value"]}
        except np.linalg.LinAlgError as exc:
            robust[f"{kernel}_{lags}"] = {"p_value": None, "conditional_p_value": None, "not_computed": str(exc)}
    by_grid: dict[str, list[float | None]] = {"p_value": [], "conditional_p_value": []}
    for g in range(vf.STEP):
        mask = grid == g
        try:
            alt = gw_test(d[mask], x[mask], lags=0)
            by_grid["p_value"].append(alt["p_value"])
            by_grid["conditional_p_value"].append(alt["conditional"]["p_value"])
        except np.linalg.LinAlgError:
            by_grid["p_value"].append(None)
            by_grid["conditional_p_value"].append(None)
    robust["by_grid"] = by_grid
    return {"stat": round(main["stat"], 3), "df": main["df"], "p_value": main["p_value"], "n_days": main["n"],
            "lags": GW_LAGS, "kernel": "truncated",
            "conditional": {"stat": round(main["conditional"]["stat"], 3), "df": main["conditional"]["df"],
                            "p_value": main["conditional"]["p_value"]},
            # Levels form: (ln OVX, gap) and (ln OVX, ln RV5) span the same space, gap = ln OVX - ln RV5.
            "coef_levels": {"constant": round(b[0], 4), "d_lag": round(b[1], 4), "ln_ovx": round(b[2] + b[3], 4),
                            "ln_rv5": round(-b[3], 4)},
            "robustness": robust}


def _week_span(rows: tuple[int, int], weekly_rows: np.ndarray, first_week: int) -> tuple[int, int]:
    """Weekly indices bracketing OOS rows a..b, snapped outward, never before the first week with a value."""
    wf = int(np.searchsorted(weekly_rows, rows[0], side="right")) - 1
    wt = int(np.searchsorted(weekly_rows, rows[1], side="left"))
    wf = max(wf, first_week)
    wt = min(wt, weekly_rows.size - 1)
    return wf, max(wt, wf)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(first, last) index of every run of True in mask."""
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(int), 0]))
    return [(int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2])]


def _peak(t: np.ndarray, ends: np.ndarray) -> dict[str, Any]:
    k = int(np.argmax(np.abs(t)))
    return {"sup": round(float(abs(t[k])), 3), "sign": int(np.sign(t[k])), "at": str(ends[k]), "_sup": float(abs(t[k]))}


def stability_report(frame: pd.DataFrame, gap: np.ndarray, pos: np.ndarray, challenger: str) -> dict[str, Any]:
    """Conditional predictive ability and stability over time (ROADMAP 2.3) of every model's daily QLIKE difference
    with the model of record, d = QLIKE(har_iv) - QLIKE(model) (positive: the model's loss is lower, the sign of
    dm_qlike_vs_har_iv). Returns the per-model `gw` and `fluctuation` blocks, the top-level `stability` block and
    the weekly series. Giacomini-White: instruments known at the decision (d of the row 5 sessions earlier, the last
    matured; ln OVX of the session before; the OVX/RV5 gap), truncated kernel at 4 lags. Fluctuation: a rolling DM t
    on each window's own long-run variance, for every share in FLUCT_MUS, against one seeded null of Gaussian random
    walks at this sample's length; the p-value adjusted for the choice among the shares is the ledger's. A test that
    cannot be computed (a HAC matrix not positive definite, a window variance not positive) publishes p_value null
    and `not_computed`; nothing here is read by the challenger's choice, the band or the leaderboard."""
    act = frame["actual"].to_numpy()
    LQ = {m: qlike(act, frame[m].to_numpy()) for m in MODEL_KEYS if m in frame}
    dates = frame["date"].dt.strftime("%Y-%m-%d").to_numpy()
    grid = frame["grid"].to_numpy()
    n = len(frame)
    ln_ovx = np.log(frame["ovx"].to_numpy())
    gap = np.asarray(gap, dtype=float)
    pos = np.asarray(pos, dtype=int)
    windows = [int(round(mu * n)) for mu in FLUCT_MUS]
    j_draw = FLUCT_MUS.index(FLUCT_MU)
    m = windows[j_draw]
    null = fluctuation_null(n, windows, draws=FLUCT_NULL_DRAWS, seed=[SEED, FLUCT_SEED_TAG, n])
    reference = fluctuation_reference(null)
    sorted_draw = reference[0][j_draw]
    grid_sizes = sorted({int((grid == g).sum()) for g in range(vf.STEP)})
    grid_nulls = {}
    for size in grid_sizes:
        col = fluctuation_null(size, [int(round(FLUCT_MU * size))], draws=FLUCT_NULL_DRAWS,
                               seed=[SEED, FLUCT_SEED_TAG, size])
        grid_nulls[size] = (col, fluctuation_reference(col))
    base = fluctuation_test([0.0] * len(windows), null, reference)["critical"]
    c5, c10 = base[j_draw]["0.05"], base[j_draw]["0.1"]
    weekly_rows = np.flatnonzero(grid == 0)
    first_week = int(np.flatnonzero(weekly_rows >= m - 1)[0])

    def marginal(col: np.ndarray, sup: float) -> float:
        return float((1 + (col >= sup).sum()) / (col.size + 1))

    gw: dict[str, Any] = {}
    fluct: dict[str, Any] = {}
    paths: dict[str, np.ndarray | None] = {}
    cumulative: dict[str, np.ndarray] = {}
    first_ok = None
    for key in MODEL_KEYS:
        if key == CHAMPION or key not in LQ:
            continue
        d = LQ[CHAMPION] - LQ[key]
        x, ok = gw_instruments(d, pos, ln_ovx, gap)
        first_ok = ok
        gw[key] = _gw_block(d[ok], x[ok], grid[ok])
        cumulative[key] = np.cumsum(d)
        try:
            tests = [rolling_dm(d, w, lags=GW_LAGS)[0] for w in windows]
        except ValueError as exc:
            # Every field the ledger reads, null; the rest of the report is built as usual.
            fluct[key] = {"sup": None, "sign": None, "at": None, "window_start": None, "p_value": None,
                          "rejects_5pct": None, "rejects_10pct": None, "rejects_5pct_gr2010": None,
                          "search_adjusted": {"over_mu": list(FLUCT_MUS), "min_p": None, "at_mu": None, "p_value": None},
                          "by_mu": {_mu_key(mu): {"window_sessions": w, "p_value": None}
                                    for j, (mu, w) in enumerate(zip(FLUCT_MUS, windows)) if j != j_draw},
                          "robustness": {"bartlett_40": {"p_value": None}, "full_sample_variance": {"p_value": None},
                                         "by_grid": {"outside_5pct": None}},
                          "not_computed": str(exc)}
            paths[key] = None
            continue
        peaks = [_peak(t, dates[w - 1:]) for t, w in zip(tests, windows)]
        result = fluctuation_test([pk["_sup"] for pk in peaks], null, reference)
        path = tests[j_draw]
        ends = dates[m - 1:]
        peak = peaks[j_draw]
        p_draw = result["marginal_p"][j_draw]
        means = np.convolve(d, np.ones(m), mode="valid") / m
        k_peak = int(np.argmax(np.abs(path)))
        k_same = int(np.argmax(np.sign(path[k_peak]) * means))
        runs = []
        for a, b in _runs(np.abs(path) > c5):
            wf, wt = _week_span((a + m - 1, b + m - 1), weekly_rows, first_week)
            runs.append({"from": str(ends[a]), "to": str(ends[b]), "sign": int(np.sign(path[a])), "sessions": b - a + 1,
                         "week_from": wf, "week_to": wt})
        robust: dict[str, Any] = {}
        try:
            alt = _peak(rolling_dm(d, m, lags=FLUCT_ROBUST[1], kernel=FLUCT_ROBUST[0])[0], ends)
            p_alt = marginal(sorted_draw, alt.pop("_sup"))
            robust["bartlett_40"] = {**alt, "p_value": p_alt, "rejects_5pct": p_alt < 0.05}
        except ValueError as exc:
            robust["bartlett_40"] = {"p_value": None, "not_computed": str(exc)}
        sigma2 = float(hac_lrv(d, GW_LAGS)[0, 0])
        csum = np.r_[0.0, np.cumsum(d)]
        if sigma2 > 0:
            full = _peak((csum[m:] - csum[:-m]) / np.sqrt(m * sigma2), ends)
            p_full = marginal(sorted_draw, full.pop("_sup"))
            robust["full_sample_variance"] = {**full, "p_value": p_full, "rejects_5pct": p_full < 0.05}
        else:
            robust["full_sample_variance"] = {"p_value": None, "not_computed": "the full-sample variance is not positive"}
        signed: list[float | None] = []
        grid_p: list[float | None] = []
        for g in range(vf.STEP):
            dg = d[grid == g]
            col, _ = grid_nulls[dg.size]
            try:
                tg = rolling_dm(dg, int(round(FLUCT_MU * dg.size)), lags=0)[0]
            except ValueError:
                signed.append(None)
                grid_p.append(None)
                continue
            kg = int(np.argmax(np.abs(tg)))
            signed.append(round(float(tg[kg]), 2))
            grid_p.append(marginal(col[:, 0], float(abs(tg[kg]))))
        robust["by_grid"] = {"signed_sup": signed, "p_value": grid_p,
                             "outside_5pct": int(sum(p is not None and p < 0.05 for p in grid_p))}
        by_mu = {}
        for j, (mu, w) in enumerate(zip(FLUCT_MUS, windows)):
            if j == j_draw:
                continue
            pk = {k: v for k, v in peaks[j].items() if k != "_sup"}
            by_mu[_mu_key(mu)] = {"window_sessions": w, **pk, "p_value": result["marginal_p"][j],
                                  "rejects_5pct": result["marginal_p"][j] < 0.05}
        fluct[key] = {
            "sup": peak["sup"], "sign": peak["sign"], "at": peak["at"], "window_start": str(dates[k_peak]),
            "p_value": p_draw, "rejects_5pct": p_draw < 0.05, "rejects_10pct": p_draw < 0.10,
            "rejects_5pct_gr2010": peak["_sup"] > GR2010_TABLE1[FLUCT_MU][0],
            "search_adjusted": {"over_mu": list(FLUCT_MUS), "min_p": result["min_p"],
                                "at_mu": FLUCT_MUS[result["min_at"]], "p_value": result["p_value"]},
            "max": round(float(path.max()), 3), "max_at": str(ends[int(np.argmax(path))]),
            "min": round(float(path.min()), 3), "min_at": str(ends[int(np.argmin(path))]),
            "crosses_both_sides": bool((path > c5).any() and (path < -c5).any()),
            "outside_5pct": runs,
            "peak_window_mean_diff": round(float(means[k_peak]), 4),
            "largest_same_sign_window": {"mean_diff": round(float(means[k_same]), 4), "window_end": str(ends[k_same]),
                                         "t": round(float(path[k_same]), 2)},
            "by_mu": by_mu,
            "robustness": robust,
        }
        paths[key] = path
    mc_se = critical_value_mc_se(sorted_draw, 0.05)
    block = {
        "differential": ("QLIKE(har_iv) - QLIKE(model) on every out-of-sample day; positive when the model's loss is "
                         "lower (the sign of dm_qlike_vs_har_iv)"),
        "null": ("given what was known at each decision, equal expected QLIKE at every date; so no autocorrelation "
                 "beyond the 5-session overlap"),
        "gw": {"instruments": ["constant", "d_lag", "ln_ovx", "ovx_rv_gap"], "d_lag_sessions": vf.H, "lags": GW_LAGS,
               "kernel": "truncated", "variance": "uncentred (the null imposed)",
               "n_days": int(cast("np.ndarray", first_ok).sum()),
               "first": str(dates[np.flatnonzero(cast("np.ndarray", first_ok))[0]]),
               "conditional": "the instruments without the constant, chi2(3): unchanged by a constant shift of the differential",
               "registered": "conditional part only; the chi2(4) form with the constant is context and not in the ledger",
               "coef_levels": "OLS of the differential on (1, d_lag, ln OVX, ln RV5): the instruments' span in levels",
               "robustness": ["bartlett_10", "bartlett_40", "by_grid (tau = 1, 0 lags)"]},
        "fluctuation": {
            "mu": FLUCT_MU, "window_sessions": m, "n_days": n, "first_window_end": str(dates[m - 1]),
            "lags": GW_LAGS, "kernel": "truncated", "variance": "each window's own, around its mean",
            "dated": "window end", "two_sided": True,
            "critical_values": {"five_pct": round(c5, 4), "ten_pct": round(c10, 4), "mc_se_five_pct": round(mc_se, 4)},
            "null": {"draws": FLUCT_NULL_DRAWS, "seed": [SEED, FLUCT_SEED_TAG, n],
                     "method": "Gaussian random walks of n_days steps, one set for every window length"},
            "search": {"over_mu": list(FLUCT_MUS),
                       "method": "smallest marginal p over the window lengths, calibrated on the same walks"},
            "gr2010_table1": {"five_pct": GR2010_TABLE1[FLUCT_MU][0], "ten_pct": GR2010_TABLE1[FLUCT_MU][1]},
            "context": {_mu_key(mu): {"window_sessions": w,
                                      "critical_values": {"five_pct": round(base[j]["0.05"], 4),
                                                          "ten_pct": round(base[j]["0.1"], 4)},
                                      "gr2010_table1": {"five_pct": GR2010_TABLE1[mu][0], "ten_pct": GR2010_TABLE1[mu][1]}}
                        for j, (mu, w) in enumerate(zip(FLUCT_MUS, windows)) if j != j_draw},
            "robustness": {"bartlett_40": "each window's Bartlett variance at 40 lags",
                           "full_sample_variance": ("Giacomini and Rossi's form: one full-sample variance (truncated 4, "
                                                    "uncentred)")},
            "grid": {"window_weeks": int(round(FLUCT_MU * min(grid_sizes))), "lags": 0,
                     "critical_values_five_pct": {str(size): round(fluctuation_test([0.0], col, ref)["critical"][0]["0.05"], 4)
                                                  for size, (col, ref) in grid_nulls.items()}}},
        "cumulative": {"end_date": str(dates[-1]),
                       "end_all_days": {name: float(cumulative[challenger if name == "challenger" else name][-1])
                                        for name in CUMULATIVE_SERIES}},
        "series": {"fluct_t": list(STABILITY_SERIES), "cum_qlike_diff": list(CUMULATIVE_SERIES)},
    }
    series: dict[str, list[float | None]] = {}
    for name in STABILITY_SERIES:
        drawn = paths[challenger if name == "challenger" else name]
        series[f"fluct_t_{name}"] = [None if drawn is None or r < m - 1 else round(float(drawn[r - m + 1]), 2)
                                     for r in weekly_rows]
    for name in CUMULATIVE_SERIES:
        cum = cumulative[challenger if name == "challenger" else name]
        series[f"cum_qlike_diff_{name}"] = [round(float(cum[r]), 3) for r in weekly_rows]
    return {"gw": gw, "fluctuation": fluct, "stability": block, "series": series}


# ----------------------------------------------------------------------------------------------
# The whole predictive distribution (ROADMAP 2.4)
# ----------------------------------------------------------------------------------------------

def _quantile_fit(y_log: np.ndarray, A: np.ndarray, taus: Sequence[float]) -> np.ndarray:
    """Koenker-Bassett linear quantile regression of y_log on A at each level, solved exactly as the standard linear
    program (2p + 2n non-negative variables, n equality rows) by scipy's HiGHS interior point with crossover: the
    exact optimum, stable to input noise where an iterative fit is not. Returns (len(taus), p) coefficients."""
    from scipy import sparse
    from scipy.optimize import linprog

    n, p = A.shape
    a = sparse.csr_matrix(A)
    eye = sparse.identity(n, format="csr")
    a_eq: Any = sparse.hstack([a, -a, eye, -eye]).tocsc()      # scipy's stubs do not list sparse A_eq
    out = np.empty((len(taus), p))
    for k, tau in enumerate(taus):
        c = np.concatenate([np.zeros(2 * p), np.full(n, tau), np.full(n, 1.0 - tau)])
        res: Any = linprog(c, A_eq=a_eq, b_eq=y_log, bounds=(0, None), method="highs-ipm")
        if res.status != 0:
            raise RuntimeError(f"quantile LP failed at tau={tau}: {res.message}")
        out[k] = res.x[:p] - res.x[p:2 * p]
    return out


def quantile_har(d: Design, *, rows: int | None = None) -> dict[str, Any]:
    """The quantile HAR-IV: linear quantile regressions of log RV5 on HAR-IV's terms (the design's X, logged) at the
    19 levels of DIST_TAUS, fitted on the purged training rows (< fit_row - 4) of LightGBM's quarterly schedule and
    rearranged by sorting each day (Chernozhukov, Fernandez-Val and Galichon 2010). Its point forecast follows the
    lab's rule for a log model: the rearranged median times sqrt(Duan smearing of the median fit's training
    residuals). A robustness competitor, outside MODELS and FEASIBLE: nothing that ranks, bands or selects a model
    reads it. `rows` truncates the design (tests)."""
    X, y = (d.X, d.y) if rows is None else (d.X[:rows], d.y[:rows])
    ends = [e for e in d.ends if e < len(y)]
    A = np.column_stack([np.ones(len(X)), np.log(X)])
    ly = np.log(y)
    period = QHAR_RETRAIN_WEEKS * vf.STEP
    raw = np.empty((len(ends), len(DIST_TAUS)))
    smear = np.empty(len(ends))
    fit_rows: list[int] = []
    beta = np.empty((len(DIST_TAUS), A.shape[1]))
    s = 1.0
    for i, end in enumerate(ends):
        fit_row = max(ends[0], vf.MIN_TRAIN + (end - vf.MIN_TRAIN) // period * period)
        if not fit_rows or fit_row != fit_rows[-1]:
            train_end = fit_row - (vf.H - 1)
            beta = _quantile_fit(ly[:train_end], A[:train_end], DIST_TAUS)
            s = float(np.mean(np.exp(2.0 * (ly[:train_end] - A[:train_end] @ beta[DIST_MEDIAN]))))
            fit_rows.append(fit_row)
        raw[i] = beta @ A[end]
        smear[i] = s
    log_q, crossed = rearrange(raw)
    q = np.exp(log_q)
    return {"q": q, "crossed": crossed, "median_smear": smear, "forecast": q[:, DIST_MEDIAN] * np.sqrt(smear),
            "fit_rows": fit_rows, "refits": len(fit_rows)}


def empirical_predictive(frame: pd.DataFrame, key: str) -> dict[str, Any]:
    """A log model's predictive distribution from its own matured errors: its median forecast times exp of the
    trailing quantiles of its matured log residuals, over the conformal band's window (CONFORMAL_WINDOW days, 5-session
    delay, WARMUP weeks' warm-up) without the band's adaptive step. The normal score is that of the outcome's mid-rank
    among the same residuals."""
    med = frame[f"{key}__med"].to_numpy()
    e = np.log(frame["actual"].to_numpy()) - np.log(med)
    tq = trailing_quantiles(e, DIST_TAUS, delay=vf.H, window=CONFORMAL_WINDOW, warmup=WARMUP * vf.STEP)
    return {"q": med[:, None] * np.exp(tq["q"]), "z": stats.norm.ppf(tq["pit"]), "rank": tq["rank"],
            "n_past": tq["n_past"]}


def _sobol_shocks() -> np.ndarray:
    """2^DIST_SIM_LOG2 scrambled Sobol points in vf.H dimensions (one per session), seeded [SEED, DIST_SEED_TAG]."""
    sampler = stats.qmc.Sobol(d=vf.H, scramble=True, rng=np.random.default_rng([SEED, DIST_SEED_TAG]))
    return cast("np.ndarray", sampler.random_base2(DIST_SIM_LOG2))


def _garch_rv5(z: np.ndarray, params: npt.ArrayLike, h1: float) -> tuple[np.ndarray, np.ndarray]:
    """The 5-session realized vol (decimal, annualized; sorted) of each shock path z (draws x vf.H, unit variance)
    run through the fitted recursion from one-step variance h1 (percent^2): eps_k = sqrt(h_k) z_k, r_k = mu + eps_k,
    h_{k+1} = omega + (alpha + gamma 1{eps_k < 0}) eps_k^2 + beta h_k. Second, the "static" reading: the same shocks
    on the variance path held at its expectation (gamma enters as gamma / 2 for symmetric shocks), as the point
    forecast's own variance path."""
    mu, omega, alpha, gamma, beta = (float(v) for v in np.asarray(params, dtype=float)[:5])
    h = np.full(z.shape[0], h1)
    h_bar = float(h1)
    ssq, ssq_static = np.zeros(z.shape[0]), np.zeros(z.shape[0])
    for k in range(z.shape[1]):
        eps = np.sqrt(h) * z[:, k]
        ssq += (mu + eps) ** 2
        h = omega + (alpha + gamma * (eps < 0)) * eps ** 2 + beta * h
        ssq_static += (mu + np.sqrt(h_bar) * z[:, k]) ** 2
        h_bar = omega + (alpha + 0.5 * gamma + beta) * h_bar
    scale = ANNUAL / z.shape[1]
    return np.sort(np.sqrt(ssq * scale) / GARCH_SCALE), np.sort(np.sqrt(ssq_static * scale) / GARCH_SCALE)


def garch_predictive(frame: pd.DataFrame, key: str) -> dict[str, Any]:
    """A GARCH-family model's own predictive distribution of the 5-session realized vol: its fitted recursion,
    run forward from the parameters in force and the one-step variance at each row (the frame's `<key>__*` columns)
    with Student-t shocks scaled to unit variance, on 2^14 scrambled Sobol points (_garch_rv5). The PIT is the Hazen
    CDF of the simulated sample (continuous in its inputs), clamped half a draw from 0 and 1; the count PIT is
    context, and so is the "static" reading."""
    U = _sobol_shocks()
    draws = U.shape[0]
    taus = np.asarray(DIST_TAUS)
    y = frame["actual"].to_numpy()
    par = frame[[f"{key}__{name}" for name in GARCH_PARAMS]].to_numpy()
    h1 = frame[f"{key}__h1"].to_numpy()
    n = len(frame)
    q, q_static = np.empty((n, taus.size)), np.empty((n, taus.size))
    u, u_count, u_static = np.empty(n), np.empty(n), np.empty(n)
    shocks: dict[float, np.ndarray] = {}
    for i in range(n):
        nu = float(par[i, -1])
        if nu not in shocks:
            shocks.clear()
            shocks[nu] = stats.t.ppf(U, nu) * np.sqrt((nu - 2.0) / nu)
        sim, static = _garch_rv5(shocks[nu], par[i, :5], float(h1[i]))
        q[i], q_static[i] = np.quantile(sim, taus), np.quantile(static, taus)
        u[i], u_static[i] = sample_pit(sim, y[i]), sample_pit(static, y[i])
        u_count[i] = (np.searchsorted(sim, y[i], side="left") + 0.5) / (draws + 1)
    return {"q": q, "z": stats.norm.ppf(u), "u": u, "z_count": stats.norm.ppf(u_count), "draws": draws,
            "q_static": q_static, "z_static": stats.norm.ppf(u_static)}


def ewma_predictive(frame: pd.DataFrame) -> dict[str, Any]:
    """EWMA's predictive distribution under its own assumption, zero-mean normal returns at a flat daily variance:
    5 (RV5 / sigma)^2 is chi-squared with 5 degrees of freedom. The normal score comes from the survival function
    above the median, so a far-tail outcome keeps a finite score."""
    sigma = frame["ewma"].to_numpy()
    y = frame["actual"].to_numpy()
    ratios = np.sqrt(stats.chi2.ppf(DIST_TAUS, vf.H) / vf.H)
    x = vf.H * (y / sigma) ** 2
    cdf, sf = stats.chi2.cdf(x, vf.H), stats.chi2.sf(x, vf.H)
    with np.errstate(divide="ignore"):
        z = np.where(cdf <= 0.5, stats.norm.ppf(cdf), stats.norm.isf(sf))
    return {"q": sigma[:, None] * ratios[None, :], "z": z}


def _interior_z(y: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Normal score from quantiles alone: inside [q_1, q_K], linear in (log q_k, Phi^-1(tau_k)), exact for a
    log-normal truth; below q_1 -inf and above q_K +inf (the censored test counts them)."""
    zt = stats.norm.ppf(DIST_TAUS)
    ly, lq = np.log(y), np.log(q)
    z = np.empty(ly.size)
    for i in range(ly.size):
        z[i] = -np.inf if ly[i] < lq[i, 0] else np.inf if ly[i] > lq[i, -1] else float(np.interp(ly[i], lq[i], zt))
    return z


def _randomized_tail_z(z: np.ndarray, draws: np.ndarray) -> np.ndarray:
    """_interior_z with each censored tail replaced by a seeded uniform draw on its probability mass (context)."""
    lo, hi = DIST_TAUS[0], DIST_TAUS[-1]
    out = z.copy()
    out[z == -np.inf] = stats.norm.ppf(lo * draws[z == -np.inf])
    out[z == np.inf] = stats.norm.ppf(hi + (1 - hi) * draws[z == np.inf])
    return out


def _acf(x: np.ndarray, k: int) -> float:
    x = np.asarray(x, dtype=float) - np.mean(x)
    return float(x[k:] @ x[:-k] / (x @ x))


def _score_dm(a: np.ndarray, b: np.ndarray, grid: np.ndarray, years: np.ndarray | None = None) -> dict[str, Any]:
    """Model b's score against the model of record's (a) on the evaluation days, d = a - b (positive: b scores
    lower). Registered: the batch-means test over DIST_DM_GROUPS consecutive periods. Context: other period counts,
    the lab's dm_test (9 lags, as every published QLIKE DM), Newey-West (1994)'s data-dependent bandwidth, each
    sampling grid on its own (lab dm_test, horizon 1) with their Bonferroni, the differential's autocorrelation and,
    when `years` is given, the shares of the total difference from 2020 and from the 1% of days with the largest
    differences."""
    d = a - b
    bm = batch_means_test(d, DIST_DM_GROUPS)
    means = [float(v) for v in bm["period_means"]]
    nw = nw94_bandwidth(d)
    dm9 = dm_test(a, b, horizon=vf.H)
    dmn = dm_test(a, b, horizon=vf.H, lags=nw)
    by_grid = [dm_test(a[grid == g], b[grid == g]) for g in range(vf.STEP)]
    out: dict[str, Any] = {
        "stat": round(float(bm["stat"]), 3), "p_value": float(bm["p_value"]), "groups": DIST_DM_GROUPS,
        "mean_diff": round(float(d.mean()), 4),
        "periods_model_lower": int(sum(v > 0 for v in means)), "periods_har_iv_lower": int(sum(v < 0 for v in means)),
        "period_means": [round(v, 4) for v in means],
        **{f"groups_{q}": {"stat": round(float(r["stat"]), 3), "p_value": float(r["p_value"])}
           for q, r in ((q, batch_means_test(d, q)) for q in DIST_DM_GROUPS_CONTEXT)},
        "dm_test": {"stat": dm9["stat"], "p_value": dm9["p_value"], "lags": dm9["lags"]},
        "nw94": {"lags": nw, "stat": dmn["stat"], "p_value": dmn["p_value"]},
        "by_grid": [{"stat": r["stat"], "p_value": r["p_value"]} for r in by_grid],
        "grid_bonferroni_p": bonferroni_min([r["p_value"] for r in by_grid]),
        "acf": {str(k): round(_acf(d, k), 3) for k in DIST_ACF_LAGS},
    }
    if years is not None:
        total = float(d.sum())
        top = np.argsort(-np.abs(d), kind="stable")[: int(round(0.01 * d.size))]
        out["concentration"] = {"share_2020_pct": round(float(d[years == 2020].sum()) / total * 100, 1),
                                "share_largest_1pct_days_pct": round(float(d[top].sum()) / total * 100, 1)}
    return out


def _bonferroni_se(p_values: list[float], draws: int) -> float:
    """Monte Carlo standard error of a Bonferroni p over grids whose p-values count `draws` simulated draws."""
    p = min(p_values)
    return float(min(1.0, len(p_values) * np.sqrt(p * (1 - p) / draws)))


def distribution_scores(frame: pd.DataFrame, qhar: dict[str, Any], conf: dict[str, dict[str, Any]], challenger: str,
                        *, null_draws: int = DIST_NULL_DRAWS) -> dict[str, Any]:
    """Every distribution model's 19 predictive quantiles scored on the same evaluation days (the conformal band's:
    every OOS row once WARMUP weeks of matured errors exist): the grid CRPS (equal weights over DIST_TAUS, vol
    points), the pinball losses at 0.10 and 0.90, the log-scale grid CRPS, coverage of [q10, q90] and the PIT
    histogram's decile shares with each bin's chance spread; the batch-means DM of each against the model of record on
    each score; Berkowitz's LR3 on each sampling grid (non-overlapping weeks), Bonferroni over the grids, against a
    simulated null for the trailing-rank PITs, chi2(3) for the parametric ones and a doubly censored chi2(2) form for
    the quantile HAR. The GARCH models are scored when the frame has their parameter columns (the slow walk). Also
    returns the per-day grid CRPS (`_crps`) for the context MCS."""
    n = len(frame)
    first = WARMUP * vf.STEP + vf.H - 1
    ev = slice(first, n)
    y = frame["actual"].to_numpy()
    ys = y[ev]
    grid = frame["grid"].to_numpy()
    gs = grid[ev]
    years = frame["date"].dt.year.to_numpy()[ev]
    taus = np.asarray(DIST_TAUS)
    i10, i90 = DIST_TAUS.index(DIST_PINBALL_TAUS[0]), DIST_TAUS.index(DIST_PINBALL_TAUS[1])
    labels = {m.key: m.label for m in MODELS} | {QHAR_KEY: QHAR_LABEL}
    trailing = tuple(dict.fromkeys((CHAMPION, challenger, "ovx_only")))
    null = rank_pit_null(n, first, grid, window=CONFORMAL_WINDOW, delay=vf.H, warmup=WARMUP * vf.STEP,
                         draws=null_draws, seed=[SEED, DIST_SEED_TAG, 1], bin_draws=min(DIST_NULL_BIN_DRAWS, null_draws))
    null_lr = np.asarray(null["lr"])
    preds: dict[str, dict[str, Any]] = {m: empirical_predictive(frame, m) for m in trailing}
    parametric = tuple(m for m in ("garch", "gjr") if f"{m}__h1" in frame)
    for m in parametric:
        preds[m] = garch_predictive(frame, m)
    preds["ewma"] = ewma_predictive(frame)
    preds[QHAR_KEY] = {"q": qhar["q"], "z": _interior_z(y, qhar["q"])}
    for m, pred in preds.items():
        if not np.all(np.isfinite(pred["q"][ev])):
            raise RuntimeError(f"{m}: predictive quantiles missing on an evaluation day")

    def scores(q: np.ndarray) -> dict[str, np.ndarray]:
        qs = q[ev]
        return {"crps_grid": crps_from_quantiles(ys, qs, taus) * 100,
                "pinball_10": pinball_loss(ys, qs[:, i10], DIST_PINBALL_TAUS[0]) * 100,
                "pinball_90": pinball_loss(ys, qs[:, i90], DIST_PINBALL_TAUS[1]) * 100,
                "crps_grid_log": crps_from_quantiles(np.log(ys), np.log(qs), taus)}

    daily = {m: scores(pred["q"]) for m, pred in preds.items()}
    share_sd = {"trailing_rank": np.asarray(null["share_sd_rank"], dtype=float),
                "exact": np.asarray(null["share_sd_exact"], dtype=float)}
    share_center = {"trailing_rank": np.asarray(null["share_mean_rank"], dtype=float),
                    "exact": np.full(len(DIST_DECILES) + 1, 100.0 / (len(DIST_DECILES) + 1))}
    rows: list[dict[str, Any]] = []
    for m, pred in preds.items():
        qs = pred["q"][ev]
        z = pred["z"][ev]
        construction = ("trailing_rank" if m in trailing else "estimated_quantiles" if m == QHAR_KEY else "exact")
        bins = np.sum(qs[:, list(DIST_DECILES)] <= ys[:, None], axis=1)
        shares = np.bincount(bins, minlength=len(DIST_DECILES) + 1) / ys.size * 100
        sd_key = "trailing_rank" if construction == "trailing_rank" else "exact"
        sc = daily[m]
        row: dict[str, Any] = {
            "key": m, "label": labels[m],
            "method": {"trailing_rank": "trailing quantiles of its matured log residuals around its median forecast",
                       "exact": ("simulated from its fitted recursion with Student-t shocks" if m in ("garch", "gjr")
                                 else "normal returns at its variance: RV5 = sigma sqrt(chi2_5 / 5)"),
                       "estimated_quantiles": "linear quantile regressions on HAR-IV's terms, refit quarterly, sorted"
                       }[construction],
            "pit_construction": construction,
            "crps_grid_pct": round(float(sc["crps_grid"].mean()), 3),
            "crps_grid_log": round(float(sc["crps_grid_log"].mean()), 4),
            "pinball_10_pct": round(float(sc["pinball_10"].mean()), 3),
            "pinball_90_pct": round(float(sc["pinball_90"].mean()), 3),
            "coverage_10_90_pct": round(float(np.mean((ys >= qs[:, i10]) & (ys <= qs[:, i90]))) * 100, 1),
            "mae_median_pct": round(float(np.mean(np.abs(ys - qs[:, DIST_MEDIAN]))) * 100, 3),
            "pit_decile_share_pct": [round(float(v), 1) for v in shares],
            "pit_chance_center_pct": [round(float(v), 1) for v in share_center[sd_key]],
            "pit_chance_band_pct": [round(1.96 * float(v), 1) for v in share_sd[sd_key]],
            "dm_vs_har_iv": None,
        }
        if m != CHAMPION:
            base = daily[CHAMPION]
            row["dm_vs_har_iv"] = {s: _score_dm(base[s], sc[s], gs, years if s == "crps_grid" else None)
                                   for s in ("crps_grid", "pinball_10", "pinball_90")}
            log_bm = batch_means_test(base["crps_grid_log"] - sc["crps_grid_log"], DIST_DM_GROUPS)
            log_dm = dm_test(base["crps_grid_log"], sc["crps_grid_log"], horizon=vf.H)
            row["dm_vs_har_iv"]["crps_grid_log"] = {"stat": round(float(log_bm["stat"]), 3),
                                                    "p_value": float(log_bm["p_value"]),
                                                    "dm_test": {"stat": log_dm["stat"], "p_value": log_dm["p_value"]}}
        by_grid: list[dict[str, Any]] = []
        if construction == "estimated_quantiles":
            for g in range(vf.STEP):
                b = berkowitz_censored(z[gs == g], DIST_TAUS[0], DIST_TAUS[-1])
                by_grid.append({"grid": g, "n_weeks": int((gs == g).sum()), "lr": round(float(b["lr"]), 3),
                                "p_value": float(b["p_value"]), "mu": round(float(b["mu"]), 3),
                                "sigma": round(float(b["sigma"]), 3), "below": int(b["below"]),
                                "above": int(b["above"])})
        else:
            for g in range(vf.STEP):
                b = berkowitz_test(z[gs == g], null_lr=null_lr[:, g] if construction == "trailing_rank" else None)
                entry = {"grid": g, "n_weeks": int((gs == g).sum()), "lr": round(float(b["lr"]), 3),
                         "p_value": float(b["p_value"])}
                if construction == "trailing_rank":
                    entry["p_chi2"] = float(b["p_chi2"])
                by_grid.append({**entry, "mu": round(float(b["mu"]), 3), "sigma": round(float(b["sigma"]), 3),
                                "rho": round(float(b["rho"]), 3)})
        ps = [e["p_value"] for e in by_grid]
        berk: dict[str, Any] = {
            "registered": "censored" if construction == "estimated_quantiles" else "lr3",
            "reference": {"trailing_rank": "simulated null", "exact": "chi2(3)",
                          "estimated_quantiles": "doubly censored, chi2(2)"}[construction],
            "p_value": bonferroni_min(ps), "p_cauchy": cauchy_combination(ps),
            "grids_below_5pct": int(sum(p < 0.05 for p in ps)), "by_grid": by_grid}
        if construction == "trailing_rank":
            rank, n_past = pred["rank"][ev], pred["n_past"][ev]
            berk["mc_se"] = round(_bonferroni_se(ps, null_draws), 4)
            berk["p_chi2"] = bonferroni_min([e["p_chi2"] for e in by_grid])
            exceed = np.asarray(null["exceed"], dtype=float)
            berk["window_exceedances"] = {"days": int(np.sum((rank == 0) | (rank == n_past))),
                                          "null_mean": round(float(exceed.mean()), 1),
                                          "null_q95": float(np.quantile(exceed, 0.95))}
        if m in parametric:
            zc = pred["z_count"][ev]
            berk["p_count_pit"] = bonferroni_min([berkowitz_test(zc[gs == g])["p_value"] for g in range(vf.STEP)])
            u = pred["u"][ev]
            half = 0.5 / pred["draws"]
            berk["clamp_days"] = int(np.sum((u <= half) | (u >= 1 - half)))
            berk["extreme_days"] = int(np.sum((u < 1e-3) | (u > 1 - 1e-3)))
            zs = pred["z_static"][ev]
            static = scores(pred["q_static"])
            row["alternatives"] = {"static_t": {
                "crps_grid_pct": round(float(static["crps_grid"].mean()), 3),
                "berkowitz_p": bonferroni_min([berkowitz_test(zs[gs == g])["p_value"] for g in range(vf.STEP)])}}
        if m == QHAR_KEY:
            zfull = pred["z"]
            alt = []
            for k in range(DIST_TAIL_SEEDS + 1):
                seed = [SEED, DIST_SEED_TAG, n] if k == 0 else [SEED, DIST_SEED_TAG, n, k - 1]
                zr = _randomized_tail_z(zfull, np.random.default_rng(seed).uniform(size=n))[ev]
                alt.append(bonferroni_min([berkowitz_test(zr[gs == g])["p_value"] for g in range(vf.STEP)]))
            spread = np.asarray(alt[1:])
            berk["randomized_tail"] = {"p_value": alt[0], "seed": [SEED, DIST_SEED_TAG, n],
                                       "seed_min": float(spread.min()), "seed_max": float(spread.max()),
                                       "seeds": DIST_TAIL_SEEDS,
                                       "share_below_5pct": round(float(np.mean(spread < 0.05)) * 100, 1)}
            crossed = np.asarray(qhar["crossed"], dtype=bool)
            row["quantile_har"] = {"refits": int(qhar["refits"]), "crossing_days_oos": int(crossed.sum()),
                                   "crossing_days_evaluation": int(crossed[ev].sum()),
                                   "tail_days": int(np.sum(~np.isfinite(z)))}
        row["berkowitz"] = berk
        if m in conf:
            med = frame[f"{m}__med"].to_numpy()[ev]
            lo, hi = med * np.exp(conf[m]["_lo"][ev]), med * np.exp(conf[m]["_hi"][ev])
            a = CONFORMAL_ALPHA / 2
            lower = float(np.mean(pinball_loss(ys, lo, a))) * 100
            upper = float(np.mean(pinball_loss(ys, hi, 1 - a))) * 100
            row["band_split"] = {"pinball_lower_pct": round(lower, 3), "pinball_upper_pct": round(upper, 3),
                                 "interval_score_pct": round((lower + upper) / a, 2)}
        rows.append(row)
    # The registered rule's size on the null draws themselves (each draw against all draws), and chi2(3)'s.
    sim_p = np.empty_like(null_lr)
    for g in range(vf.STEP):
        col = np.sort(null_lr[:, g])
        sim_p[:, g] = (col.size - np.searchsorted(col, null_lr[:, g], side="left")) / col.size
    chi2_p = stats.chi2.sf(null_lr, 3)
    gauss = float(2.0 / len(DIST_TAUS) * np.sum(stats.norm.pdf(stats.norm.ppf(DIST_TAUS))) * np.sqrt(np.pi))
    config = {
        "taus": list(DIST_TAUS), "pinball_taus": list(DIST_PINBALL_TAUS),
        "crps": "(1/19) sum of the quantile scores QS_tau = 2 pinball_tau over DIST_TAUS: equal weights (WIS)",
        "scale": "vol points (annualized vol x 100); the log scale is context",
        "gaussian_ratio_k19": round(gauss, 6),
        "residual_window_days": CONFORMAL_WINDOW, "delay_sessions": vf.H, "warmup_days": WARMUP * vf.STEP,
        "garch": {"method": "simulated recursion, Student-t shocks scaled to unit variance", "draws": 2 ** DIST_SIM_LOG2,
                  "sequence": "scrambled Sobol", "seed": [SEED, DIST_SEED_TAG],
                  "pit": "Hazen CDF of the simulated sample, clamped half a draw from 0 and 1"},
        "ewma": "normal returns at the EWMA variance: RV5 = sigma sqrt(chi2_5 / 5)",
        "quantile_har": {"design": CHAMPION, "estimator": "Koenker-Bassett, exact LP (scipy HiGHS interior point)",
                         "refit_weeks": QHAR_RETRAIN_WEEKS, "rearranged": True,
                         "point_forecast": "rearranged median x sqrt(Duan smear of the median fit)"},
        "berkowitz": {"per": "sampling grid", "form": "LR3, conditional Gaussian AR(1)", "combine": "bonferroni",
                      "reference": {"trailing_rank": "simulated null", "parametric": "chi2(3)",
                                    "quantile_har": "doubly censored, chi2(2)"},
                      "null": {"draws": null_draws, "seed": [SEED, DIST_SEED_TAG, 1],
                               "returns": "i.i.d. normal, the real row layout, window and grids",
                               "lr_mean_by_grid": [round(float(v), 3) for v in null_lr.mean(axis=0)],
                               "lr_q95_by_grid": [round(float(v), 3) for v in np.quantile(null_lr, 0.95, axis=0)]},
                      "null_size_pct": {name: round(float(np.mean(np.minimum(1.0, vf.STEP * p.min(axis=1)) < 0.05))
                                                    * 100, 2)
                                        for name, p in (("simulated", sim_p), ("chi2", chi2_p))}},
        "dm": {"registered": "batch means", "groups": DIST_DM_GROUPS,
               "context": ["groups_8", "groups_16", "dm_test (9 lags)", "nw94", "by_grid", "log scale"]},
        "pit_bins": len(DIST_DECILES) + 1, "chance_band": "1.96 sd of each bin's share across the simulated null",
    }
    return {"config": config, "n_days": int(ys.size), "first": str(frame["date"].iloc[first].date()),
            "last": str(frame["date"].iloc[-1].date()), "weeks_per_grid": [int((gs == g).sum()) for g in range(vf.STEP)],
            "models": rows, "_crps": {m: daily[m]["crps_grid"] for m in preds}}


def distribution_mcs(crps: dict[str, np.ndarray]) -> dict[str, Any] | None:
    """Model Confidence Set on the grid CRPS at three block lengths (context: the set depends on the block). None
    without the GARCH models (the fast walk)."""
    if not {"garch", "gjr"} <= set(crps):
        return None
    losses = pd.DataFrame(crps)
    return {**{f"block_{b}": _mcs(losses, b) for b in DIST_MCS_BLOCKS},
            "note": "context; the set depends on the bootstrap block length"}


def quantile_har_field(frame: pd.DataFrame, qhar: dict[str, Any]) -> dict[str, Any]:
    """The QLIKE comparisons rerun with the quantile HAR's point forecast added to the feasible field, on every OOS
    day, at the lab's own settings (context; nothing here selects, ranks or bands a model): its batch-means DM against
    the model of record, the Model Confidence Sets of the headline and its robustness checks, and the SPA test. The
    root mean square of its 19 quantiles is a second point forecast, published beside it."""
    y = frame["actual"].to_numpy()
    grid = frame["grid"].to_numpy()
    LQ, _ = _losses(frame)
    base = LQ[CHAMPION].to_numpy()

    def field(forecast: np.ndarray) -> pd.DataFrame:
        out = LQ[FEASIBLE].copy()
        out[QHAR_KEY] = qlike(y, forecast)
        return out

    def member(res: dict[str, Any]) -> dict[str, Any]:
        return {"included": res["included"], "p_quantile_har": res["p_values"][QHAR_KEY]}

    point = field(np.asarray(qhar["forecast"]))
    lq = point[QHAR_KEY].to_numpy()
    d = base - lq
    bm = batch_means_test(d, DIST_DM_GROUPS)
    nw = nw94_bandwidth(d)
    dm9 = dm_test(base, lq, horizon=vf.H)
    head = _mcs(point, MCS_BLOCK)
    out: dict[str, Any] = {
        "n_days": int(len(frame)), "qlike": round(float(lq.mean()), 4),
        "dm_qlike_vs_har_iv": {"batch_means": {"stat": round(float(bm["stat"]), 3), "p_value": float(bm["p_value"])},
                               "dm_test": {"stat": dm9["stat"], "p_value": dm9["p_value"], "lags": dm9["lags"]},
                               "nw94": {"lags": nw, "p_value": dm_test(base, lq, horizon=vf.H, lags=nw)["p_value"]},
                               "by_grid_p": [dm_test(base[grid == g], lq[grid == g])["p_value"] for g in range(vf.STEP)]},
        "mcs": head,
        **{f"mcs_block_{b}": member(_mcs(point, b)) for b in (10, 40)},
        "mcs_by_grid": {str(g): member(_mcs(point[grid == g].reset_index(drop=True), MCS_BLOCK_WEEKLY))
                        for g in range(vf.STEP)},
    }
    for start, end in (("2008-01-01", "2016-12-31"), ("2017-01-01", "2099-12-31")):
        mask = ((frame["date"] >= start) & (frame["date"] <= end)).to_numpy()
        label = f"{start[:4]}_{min(int(end[:4]), int(frame['date'].max().year))}"
        out[f"mcs_{label}"] = {"n_days": int(mask.sum()), **member(_mcs(point[mask].reset_index(drop=True), MCS_BLOCK))}
    spa = spa_vs_champion(point)
    out["spa"] = {"p_consistent": spa["p_consistent"], "best_model": spa["best_model"], "models": spa["models"]}
    alt = field(np.sqrt(np.mean(np.asarray(qhar["q"]) ** 2, axis=1)))
    la = alt[QHAR_KEY].to_numpy()
    alt_mcs = _mcs(alt, MCS_BLOCK)
    out["alt_point_forecast"] = {
        "method": "sqrt(mean of the 19 squared quantiles)", "qlike": round(float(la.mean()), 4),
        "batch_means_p": float(batch_means_test(base - la, DIST_DM_GROUPS)["p_value"]),
        "dm_test_p": dm_test(base, la, horizon=vf.H)["p_value"],
        "mcs_included": QHAR_KEY in alt_mcs["included"], "mcs_p": alt_mcs["p_values"][QHAR_KEY]}
    out["note"] = ("context: the quantile HAR is a robustness competitor outside the benchmark field, never eligible "
                   "for the live pair; this rerun selects nothing")
    return out


def distribution_report(frame: pd.DataFrame, qhar: dict[str, Any], conf: dict[str, dict[str, Any]],
                        challenger: str) -> dict[str, Any]:
    """ROADMAP 2.4's block: distribution_scores, the CRPS MCS (context) and the quantile HAR's field rerun
    (context; it needs the slow walk's columns)."""
    scores = distribution_scores(frame, qhar, conf, challenger)
    crps = scores.pop("_crps")
    return {**scores, "mcs_crps": distribution_mcs(crps), "quantile_har_field": quantile_har_field(frame, qhar)}


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


def chart_series(frame: pd.DataFrame, conf: dict[str, dict[str, Any]], challenger: str,
                 regimes: dict[str, Any] | None = None, extra: dict[str, list[Any]] | None = None) -> dict[str, list[Any]]:
    """Compact weekly arrays (grid 0, the production grid) for the dashboard: percent units with 1
    decimal; elasticities and the OVX/RV5 log gap (with its trailing median, the regime split) with 3. VaR uses
    the grid's own filtered-historical-simulation quantiles."""
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
        **({"ovx_rv_gap": [round(float(v), 3) for v in regimes["gap"][mask]],
            "ovx_rv_gap_median": [round(float(v), 3) for v in regimes["gap_median"][mask]]} if regimes else {}),
        **(extra or {}),
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
    qhar = quantile_har(design)
    if len(frame) != len(design.ends):
        raise RuntimeError("the OOS frame and the design's OOS rows disagree")
    regimes = regime_labels(r, dates, ovx, design.pos[np.asarray(design.ends)])
    by_regime = regime_report(frame, conf, regimes)
    var = var_backtest(frame, models=tuple(dict.fromkeys((CHAMPION, challenger, "gjr", "ewma", "raw_ovx"))))
    grid0 = frame[frame["grid"] == 0]
    board = leaderboard(frame)
    for row in board["models"]:
        row["qlike_by_regime"] = by_regime["qlike"][row["key"]]
    stability = stability_report(frame, regimes["gap"], design.pos[np.asarray(design.ends)], challenger)
    for row in board["models"]:
        row["gw_qlike_vs_har_iv"] = stability["gw"].get(row["key"])
        row["fluctuation_qlike_vs_har_iv"] = stability["fluctuation"].get(row["key"])
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
        "conformal": {k: {**{kk: vv for kk, vv in v.items() if not kk.startswith("_")}, **by_regime["conformal"][k]}
                      for k, v in conf.items()},
        "yearly": yearly(frame, challenger),
        "noise_floor": noise_floor(r, dates),
        "qlike_floor": qlike_floor(design),
        "regimes": by_regime["regimes"],
        "stability": stability["stability"],
        "distribution": distribution_report(frame, qhar, conf, challenger),
        "har_iv_coefficients": coefficient_report(design),
        "vol_cone": vol_cone(r, dates, float(ovx[-1]) if np.isfinite(ovx[-1]) else None),
        "series": chart_series(frame, conf, challenger, regimes, stability["series"]),
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
    the other side of a rounding boundary). A fluctuation-test p-value counts simulated draws, so it may
    also differ by up to SIMULATED_P_DRAWS draws, and so may a trailing-rank model's Berkowitz p-values (five
    times that for the Bonferroni and Cauchy combinations over the grids); their flags stay exact. Empty means
    every conclusion reproduces."""
    violations: list[str] = []
    numeric: list[tuple[float, str]] = []
    draw_step = SIMULATED_P_DRAWS / (FLUCT_NULL_DRAWS + 1)
    dist_step = SIMULATED_P_DRAWS / (DIST_NULL_DRAWS + 1)
    dist = committed.get("distribution") if isinstance(committed, dict) else None
    ranked = [f"/distribution/models[{i}]/berkowitz/"
              for i, m in enumerate(dist.get("models") or [] if isinstance(dist, dict) else [])
              if isinstance(m, dict) and m.get("pit_construction") == "trailing_rank"]

    def simulated_p(path: str) -> float:
        """The allowance for a p-value that counts simulated draws; 0 for every other number."""
        leaf = path.rsplit("/", 1)[-1].split("[", 1)[0]
        if "/fluctuation_qlike_vs_har_iv/" in path and leaf in ("p_value", "min_p"):
            return draw_step
        for prefix in ranked:
            if path.startswith(prefix):
                if path.startswith(f"{prefix}by_grid[") and leaf == "p_value":
                    return dist_step
                if path.count("/") == prefix.count("/") and leaf in ("p_value", "p_cauchy", "mc_se"):
                    return vf.STEP * dist_step
        return 0.0

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
                if (rel > REPRO_REL_TOL and abs(a - b) > _last_place(a) * (1 + 1e-9)
                        and not abs(a - b) <= simulated_p(path) * (1 + 1e-9)):
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
