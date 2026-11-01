#!/usr/bin/env python3
"""
WTI realized-volatility forecaster (HAR-IV) — the project's model of record.

Why this exists: the original 1-week DIRECTION model was a look-ahead leakage artifact and,
once purged, is a coin flip (direction on a liquid contract is near-unforecastable, as theory
predicts). VOLATILITY is different. Volatility clustering and mean-reversion are among the most
replicated facts in financial econometrics, so a properly validated vol forecast has genuine
out-of-sample skill. This module forecasts next-week (5-trading-day) realized volatility from a
heterogeneous-autoregressive feature set augmented with implied volatility (HAR-IV: RV over
5/22/66 days plus OVX, the free CBOE oil implied-vol index; Corsi 2009, Busch-Christensen-Nielsen
2011), validated with the SAME purged walk-forward used to expose the direction leak.

Evaluation design (everything below is computed by validate(); no figure is hand-maintained):
  - Sample: every day since OVX's inception (May 2007), a FIXED anchor. The headline therefore only
    drifts as new out-of-sample weeks accrue; a rolling window would also move it, base rate
    included, each time an old year dropped out, with nothing changed in the model.
  - Vol-LEVEL losses lead: QLIKE, the loss that ranks volatility forecasts consistently when the
    realized-vol proxy is noisy (Patton 2011), against naive persistence and against a regression on
    OVX alone (the options market's own forecast), each with a Diebold-Mariano statistic; R^2 and
    MAE beside them.
  - Vol-DIRECTION (will next week's realized vol exceed this week's?) is reported beside its noise
    floor, the same pipeline on i.i.d. returns where nothing is forecastable: calling "up" when this
    week's vol is low is right about three times in four on any continuous i.i.d. series, so
    accuracy alone is not evidence of skill. Accuracy is also compared with the majority class
    (exact binomial test), a mean-reversion rule (vol reverts toward its 66-day average; paired HAC
    z on the 0/1 hit differences) and the same HAR model without OVX fitted on the SAME rows (the
    nested baseline that isolates what implied vol adds). On the noise floor's simulated returns the
    pipeline also beats the majority class and the mean-reversion rule, so those two comparisons are
    not evidence of skill either. The hit series is autocorrelated (consecutive labels share a week
    of returns), so an i.i.d. z-score is reported alongside a Newey-West (HAC) one.
  - Economic value: a volatility-targeting overlay (does the forecast size a long
    WTI position better than trailing vol or buy-and-hold?) and the oil variance risk premium (does
    OVX exceed subsequent realized vol by more than it would with no premium, and does the forecast
    time it?). Both are out-of-sample and run on same-contract returns (see the data caveat), so
    they end where EIA's curve ends.

Scope: this is an implementation of a KNOWN effect, not novel alpha. It is a validated
volatility/regime INDICATOR, not a directional return signal, and the economic tests below do not
show it converting into trading profit.

Parsimony note: the standard HAR "leverage" enhancement (downside realized semivolatility, so down
weeks predict higher vol) is re-tested inside validate() as a nested variant on the same rows. It is
left out of the deployed model because it does not significantly improve out-of-sample results —
added complexity that does not earn its keep is how vol models overfit.

Leakage controls (identical discipline to backtest_walk_forward.py):
  - Features at day t use returns up to and including t and OVX up to t - 1. OVX settles at 16:15
    ET, after the 14:30 ET CL settlement that opens the target window, so it enters lagged one
    session (lag_ovx, OVX_LAG_SESSIONS; model version 2). Version 1 used the same day's OVX.
  - Target = realized vol of days t+1..t+H, so the walk-forward PURGES the last H-1 training rows
    whose target matures after the prediction point.
  - The 2020-04-20 negative settlement (an expiry artifact) is dropped before any computation.

Data caveat: CL=F is Yahoo's UNADJUSTED front-month series (it equals EIA's contract-1 price), so
the monthly roll day carries the contract-1/contract-2 price gap (mostly contango, so mostly
positive; economic.returns.roll_gap_mean_pct). For the vol forecast the gap is small noise:
rebuilding the roll days through the curve's end as same-contract returns changes direction
accuracy and QLIKE only slightly (validate() reports both in economic.returns), so the forecast
keeps the live CL=F series (EIA stopped publishing the curve in April 2024 and Yahoo keeps no
expired contracts, so no live correction exists).
For a P&L the gap is a bias, not noise (every strategy earns its weight times every gap, which
flatters a long book), so the economic tests use same-contract returns and stop at the curve's end.

Usage:
    python -m backend.vol_forecast                 # print validation + write artifact
    python -m backend.vol_forecast --out data/vol_forecast_validation.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

try:
    from . import carry_signal_test as eia_curve
    from .contract_calendar import is_business_day, last_completed_bar_date, last_trade_date
    from .forecast_eval import adaptive_conformal, dm_test, mincer_zarnowitz, qlike
    from .forecast_eval import hac_z as _hac_z, newey_west_lrv  # noqa: F401 - re-exported
    from .safe_paths import data_json_path
except ImportError:  # Direct invocation: python backend/vol_forecast.py
    import carry_signal_test as eia_curve  # type: ignore[import-not-found, no-redef]
    from contract_calendar import (  # type: ignore[import-not-found, no-redef]
        is_business_day, last_completed_bar_date, last_trade_date)
    from forecast_eval import (  # type: ignore[import-not-found, no-redef]
        adaptive_conformal, dm_test, mincer_zarnowitz, qlike)
    from forecast_eval import hac_z as _hac_z, newey_west_lrv  # type: ignore[no-redef]  # noqa: F401 - re-exported
    from safe_paths import data_json_path  # type: ignore[import-not-found, no-redef]

import numpy as np
import pandas as pd
from scipy.stats import binom, kurtosis, norm, skew, ttest_ind

if TYPE_CHECKING:
    import numpy.typing as npt

    # (daily log returns, their session dates, the same-day aligned OVX or None), as _load_data returns it;
    # _load_data_full adds the metadata dict.
    MarketData = tuple[np.ndarray, pd.DatetimeIndex, np.ndarray | None]
    MarketDataWithMeta = tuple[np.ndarray, pd.DatetimeIndex, np.ndarray | None, dict[str, Any]]

logger = logging.getLogger(__name__)

H = 5                      # forecast horizon in trading days (1 week)
MIN_TRAIN = 250            # minimum expanding-window rows before the first OOS forecast
STEP = 5                   # walk-forward stride (non-overlapping weekly test points)
ANNUALIZE = np.sqrt(252.0)
HISTORY_PERIOD = "max"     # all history; HAR-IV rows start where OVX starts (May 2007)
OVX_FFILL_LIMIT = 5        # carry an OVX print over at most a week of missing days, never longer
MIN_OVX_ROWS = MIN_TRAIN + 52 * STEP   # need >= ~1 year of OOS weeks before trusting HAR-IV

# Volatility-targeting overlay: scale a long position by TARGET_VOL / forecast, capped.
TARGET_VOL = 0.35          # ~WTI's long-run realized vol; Sharpe is insensitive to it below the cap
LEVERAGE_CAP = 2.0
COST_PER_TURN = 0.0005     # 5 bps per unit of notional traded
# Variance risk premium: OVX is 30-calendar-day implied vol, ~20 trading days (4 weekly steps).
VRP_STEPS = 4
VRP_NULL_REPS = 10000      # bootstrap draws for the zero-premium test of the vol-point share
# Forecast intervals: adaptive conformal inference on the out-of-sample log errors (Gibbs-Candes 2021).
INTERVAL_ALPHA = 0.20      # 80% intervals
RECENT_OOS_WEEKS = 156     # out-of-sample weeks shipped to the dashboard (3 years)
RECENT_CLOSES = 40         # finished-session closes shipped for the live track record
# v1: log-OLS on RV5, RV22, RV66 and the same day's OVX; published the median forecast.
# v2: OVX lagged one session (entry-time clean); losses and risk use the variance-consistent mean.
MODEL_VERSION = "har-iv/2"
NOISE_FLOOR_SIMS = 10      # the research lab reports this same floor (vol_lab.noise_floor calls noise_floor)
NOISE_FLOOR_SEED = 20261020
YF_ATTEMPTS = 3
# Entry-time-clean implied vol: OVX settles at 16:15 ET, ~1h45m after CL's 14:30 ET settlement that
# starts the target window, so the model uses the PREVIOUS session's OVX close (model version 2).
OVX_LAG_SESSIONS = 1


def _completed(series: pd.Series, now: datetime | None = None) -> pd.Series:
    """Finite, positive daily closes of finished sessions only, on a timezone-naive date index.

    Yahoo's newest daily bar is the session still trading for most of the day (and, from the 18:00 ET
    reopen to midnight, the next session's prices under today's date). Validation only ever sees
    completed sessions, so the live forecast must too: a partial day understates realized vol. A bar
    dated on an exchange holiday is never a settlement either (Yahoo kept two, 2006-09-04 and
    2025-07-04, partials of the next session), so it is dropped wherever it sits in the history.
    """
    s = pd.to_numeric(series, errors="coerce")
    if getattr(s.index, "tz", None) is not None:
        s.index = cast("pd.DatetimeIndex", s.index).tz_localize(None)
    s.index = pd.DatetimeIndex(s.index).normalize()
    cutoff = pd.Timestamp(last_completed_bar_date(now or datetime.now(timezone.utc)))
    values = s.to_numpy(dtype=float)
    settled = np.array([is_business_day(day.date()) for day in s.index], dtype=bool)
    s = s[(s.index <= cutoff) & settled & np.isfinite(values) & (values > 0)]
    return cast("pd.Series", s[~s.index.duplicated(keep="last")])


def _yf_history(symbol: str, period: str) -> pd.DataFrame:
    """yfinance daily history with a short bounded retry (Yahoo rate-limits bursts with HTTP 429)."""
    import time

    import yfinance as yf  # deferred: keeps the pure math importable (and testable) offline

    last_error: Exception | None = None
    for attempt in range(YF_ATTEMPTS):
        try:
            frame = yf.Ticker(symbol).history(period=period, interval="1d")
            if frame is not None and not frame.empty and "Close" in frame:
                return cast("pd.DataFrame", frame)
            last_error = RuntimeError(f"{symbol} history empty")
        except Exception as exc:  # noqa: BLE001 - retried, then surfaced
            last_error = exc
        if attempt < YF_ATTEMPTS - 1:
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"{symbol} history unavailable") from last_error


def _load_data_full(period: str = HISTORY_PERIOD, now: datetime | None = None) -> MarketDataWithMeta:
    """Daily WTI log returns (CL=F, completed sessions, non-positive prints dropped), aligned OVX and
    metadata about the OVX print: (returns, dates, ovx|None, meta).

    OVX (CBOE Crude Oil Volatility Index) comes from Yahoo; if Yahoo fails it falls back to FRED's
    OVXCLS (the same index, published a day later). meta = {"ovx_source", "ovx_as_of", "ovx_error",
    "ohlc", "ohlc_error", "recent_closes"} where ovx_as_of is the date of the last actual OVX print on
    or before the last return (the aligned series carries a print forward at most OVX_FFILL_LIMIT
    sessions), and each *_error is null or the failure that left a source out (also logged).
    """
    df = _yf_history("CL=F", period)
    # Non-positive closes are dropped: the 2020-04-20 negative settlement artifact.
    close = _completed(df["Close"], now)
    logret = np.log(close).diff().dropna()
    tail = close.iloc[-RECENT_CLOSES:]
    meta: dict[str, Any] = {
        "ovx_source": None, "ovx_as_of": None, "ovx_error": None, "ohlc": None, "ohlc_error": None,
        # The live track record (backend/vol_record.py) scores forecasts on these closes.
        "recent_closes": {"date": [str(d.date()) for d in tail.index],
                          "close": [round(float(v), 4) for v in tail.to_numpy()]},
        # The closes themselves, for the freeze's source check (_source_check); never serialised.
        "cl_close": close}
    try:
        try:
            from .market_data import clean_ohlc
        except ImportError:  # pragma: no cover - direct invocation
            from market_data import clean_ohlc  # type: ignore[import-not-found, no-redef]
        meta["ohlc"] = clean_ohlc(df, now)[0].loc[logret.index, ["Open", "High", "Low", "Close"]]
    except Exception as exc:  # noqa: BLE001 - only the challenger's range features need the bar
        meta["ohlc_error"] = _failure(exc)
        logger.warning("CL=F OHLC bars unusable, so no challenger forecast: %s", meta["ohlc_error"])
    raw = None
    try:
        raw = _completed(_yf_history("^OVX", period)["Close"], now)
        meta["ovx_source"] = "yahoo ^OVX"
    except Exception as yahoo_exc:  # noqa: BLE001 - FRED is the fallback, then pure HAR
        yahoo_error = _failure(yahoo_exc)
        logger.warning("Yahoo ^OVX unavailable, trying FRED OVXCLS: %s", yahoo_error)
        try:
            from .market_data import _fetch_fred_ovx
        except ImportError:  # pragma: no cover - direct invocation
            from market_data import _fetch_fred_ovx  # type: ignore[no-redef]
        try:
            raw = _completed(_fetch_fred_ovx(), now)
            meta["ovx_source"] = "FRED OVXCLS"
            meta["ovx_error"] = f"Yahoo ^OVX: {yahoo_error}"
        except Exception as fred_exc:  # noqa: BLE001 - OVX is optional; pure HAR is the documented fallback
            raw = None
            meta["ovx_error"] = f"Yahoo ^OVX: {yahoo_error}; FRED OVXCLS: {_failure(fred_exc)}"
            logger.warning("no OVX source, so the forecast falls back to pure HAR: %s", meta["ovx_error"])
    ovx = None
    if raw is not None:
        # Aligned as published: element t is the session-t close, which settles after CL's session-t
        # settlement, so the model uses it only through lag_ovx. Days before OVX existed stay NaN.
        aligned = raw.reindex(logret.index).ffill(limit=OVX_FFILL_LIMIT).to_numpy(dtype=float)
        if np.isfinite(aligned).any():
            ovx = aligned
            printed = raw[raw.index <= logret.index[-1]]
            meta["ovx_as_of"] = str(printed.index[-1].date()) if len(printed) else None
    return logret.to_numpy(dtype=float), pd.DatetimeIndex(logret.index), ovx, meta


def _source_check(close: pd.Series | None) -> dict[str, Any] | None:
    """The freeze's report on the live CL=F closes (market_data.freeze_source_check), or None without them.

    It reports and never stops a freeze: any failure (no snapshot, no EIA data) gives
    {"status": "unavailable", "reason": <the exception's class name>}. The message is left out on
    purpose, here and in the log: an EIA request error carries its URL, and the URL the API key.
    """
    if close is None:
        return None
    try:
        try:
            from .market_data import freeze_source_check, load_snapshot
            from .supply_shock_playbook import fetch_wti_daily
        except ImportError:  # pragma: no cover - direct invocation
            from market_data import freeze_source_check, load_snapshot  # type: ignore[no-redef]
            from supply_shock_playbook import fetch_wti_daily  # type: ignore[import-not-found, no-redef]
        rows = cast("list[tuple[str, float]]", fetch_wti_daily())  # type: ignore[no-untyped-call]
        spot = pd.Series([float(price) for _, price in rows],
                         index=pd.DatetimeIndex(pd.to_datetime([day for day, _ in rows])), dtype=float)
        block = freeze_source_check(close, load_snapshot(), spot)
        return cast("dict[str, Any]", json.loads(json.dumps(block, allow_nan=False)))
    except Exception as exc:  # noqa: BLE001 - a report never stops the freeze
        logger.warning("CL=F source check unavailable: %s", type(exc).__name__)
        return {"status": "unavailable", "reason": type(exc).__name__}


def _load_data(period: str = HISTORY_PERIOD, now: datetime | None = None) -> MarketData:
    """(returns, dates, ovx|None): see _load_data_full."""
    r, dates, ovx, _ = _load_data_full(period, now)
    return r, dates, ovx


def _failure(exc: BaseException) -> str:
    """An exception as recorded in the payload and the log: its type and message, and its cause's."""
    text = f"{type(exc).__name__}: {exc}"
    cause = exc.__cause__
    return f"{text} (from {type(cause).__name__}: {cause})" if cause is not None else text


def _rvol(daily_logrets: np.ndarray) -> float:
    """Annualized realized volatility from a block of daily log returns."""
    arr = np.asarray(daily_logrets, dtype=float)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr ** 2)) * ANNUALIZE)


def _downside_semivol(daily_logrets: np.ndarray) -> float:
    """Annualized downside realized semivolatility (only negative returns), the HAR leverage term."""
    arr = np.minimum(np.asarray(daily_logrets, dtype=float), 0.0)
    return float(np.sqrt(np.mean(arr ** 2)) * ANNUALIZE) if arr.size else 0.0


def _build_matrix(r: np.ndarray, dates: pd.DatetimeIndex, ovx: np.ndarray | None = None,
                  return_positions: bool = False) -> tuple[Any, ...]:  # (X, y, tdate[, positions])
    """HAR features (RV over 5/22/66 days, all <= t) + optional OVX, target = RV of days t+1..t+H.

    Feature order keeps RV5 first (the persistence baseline / current-vol reference) and RV66 third
    (the mean-reversion anchor). When OVX is present it is appended as a 4th feature (HAR-IV) and
    rows with a missing OVX are skipped. Rows with a zero realized vol are skipped too: the model is
    fitted in logs, and one log(0) would silently turn every coefficient into NaN.
    """
    use_ovx = ovx is not None
    feats, target, tdate, positions = [], [], [], []
    for t in range(66, len(r) - H):
        past = r[: t + 1]
        row = [_rvol(past[-5:]), _rvol(past[-22:]), _rvol(past[-66:])]
        if use_ovx:                             # ovx is not None; mypy does not narrow through use_ovx
            if not np.isfinite(ovx[t]) or ovx[t] <= 0:  # type: ignore[index]
                continue
            row.append(float(ovx[t]))  # type: ignore[index]
        y = _rvol(r[t + 1 : t + 1 + H])
        if y <= 0 or min(row) <= 0:
            continue
        feats.append(tuple(row))
        target.append(y)
        tdate.append(dates[t])
        positions.append(t)
    if return_positions:
        return np.array(feats), np.array(target), pd.to_datetime(tdate), np.array(positions, dtype=int)
    return np.array(feats), np.array(target), pd.to_datetime(tdate)


def _fit_log_har(X_train: np.ndarray, y_train: np.ndarray) -> np.ndarray:
    """OLS in log space — the standard HAR specification (keeps forecasts positive).

    exp() of a log-space forecast is the conditional MEDIAN, which is exactly the statistic that
    decides P(next vol > current vol) > 1/2, so it is the right point forecast for the direction call.
    """
    A = np.column_stack([np.ones(len(X_train)), np.log(X_train)])
    beta, *_ = np.linalg.lstsq(A, np.log(y_train), rcond=None)
    return cast("np.ndarray", beta)


def _predict_log_har(beta: np.ndarray, x: np.ndarray) -> float:
    return float(np.exp(beta[0] + beta[1:] @ np.log(x)))


def _smearing(X_train: np.ndarray, y_train: np.ndarray, beta: np.ndarray) -> float:
    """Duan (1983) smearing factor for the VARIANCE of a fitted log-HAR: mean(exp(2 e)) over the
    training residuals e.

    exp(prediction) is the conditional MEDIAN of realized vol. For a variance loss (QLIKE, MSE) or a
    risk number the right point forecast is the conditional mean of the variance; its square root is
    median * sqrt(smearing), with no distributional assumption (exp(s^2) if the errors are normal).
    The median stays the right input for the direction call (P(next > current) > 1/2 iff median >
    current) and for quantile intervals.
    """
    A = np.column_stack([np.ones(len(X_train)), np.log(X_train)])
    resid = np.log(y_train) - A @ beta
    return float(np.mean(np.exp(2.0 * resid)))


def _resid_sd(X_train: np.ndarray, y_train: np.ndarray, beta: np.ndarray) -> float:
    """Residual standard deviation of a fitted log-HAR (degrees-of-freedom corrected)."""
    A = np.column_stack([np.ones(len(X_train)), np.log(X_train)])
    resid = np.log(y_train) - A @ beta
    return float(np.sqrt(resid @ resid / max(len(resid) - A.shape[1], 1)))


def _r2(actual: np.ndarray, pred: np.ndarray) -> float:
    ss_res = np.sum((actual - pred) ** 2)
    ss_tot = np.sum((actual - actual.mean()) ** 2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def _qlike(actual: npt.ArrayLike, pred: npt.ArrayLike) -> float:
    """QLIKE loss on variances (0 = perfect). Robust to noise in the realized-vol proxy (Patton 2011)."""
    ratio = (np.asarray(actual, float) / np.asarray(pred, float)) ** 2
    return float(np.mean(ratio - np.log(ratio) - 1.0))


def lag_ovx(ovx: np.ndarray | None, sessions: int = OVX_LAG_SESSIONS) -> np.ndarray | None:
    """OVX as known `sessions` sessions earlier: element t holds the close of session t - sessions."""
    if ovx is None or sessions <= 0:
        return ovx
    arr = np.asarray(ovx, dtype=float)
    return cast("np.ndarray", np.r_[np.full(sessions, np.nan), arr[:-sessions]])


def _pick_ovx(ovx: np.ndarray | None) -> np.ndarray | None:
    """Use OVX only when there is enough of it to validate a HAR-IV model on; else pure HAR."""
    if ovx is None:
        return None
    valid = np.isfinite(ovx) & (ovx > 0)
    return ovx if int(valid.sum()) >= MIN_OVX_ROWS else None


def _walk_forward(X: np.ndarray, y: np.ndarray, tdate: pd.DatetimeIndex, has_ovx: bool,
                  leverage: np.ndarray | None = None, offset: int = 0) -> pd.DataFrame:
    """Purged expanding-window walk-forward, one test point per STEP rows.

    Returns one record per OOS week with the HAR(-IV) forecast, the nested pure-HAR forecast fitted
    on the SAME rows (when OVX is in use), the model plus a leverage term (downside semivolatility,
    when `leverage` is given: the candidate that was tested and left out), and the persistence /
    mean-reversion references. Every log model gives its median forecast (`pred*`, for the direction
    call) and its variance-consistent forecast (`pred*_mean`, median * sqrt(Duan smearing factor) on
    its own training residuals, for every level loss).
    """
    X_lev = np.column_stack([X, leverage]) if leverage is not None else None
    recs = []

    def fit(X_train: np.ndarray, y_train: np.ndarray, x: np.ndarray) -> tuple[float, float]:
        """(median, variance-consistent) forecast of a log-HAR fitted on the training rows."""
        beta = _fit_log_har(X_train, y_train)
        median = _predict_log_har(beta, x)
        return median, median * float(np.sqrt(_smearing(X_train, y_train, beta)))

    for end in range(MIN_TRAIN + offset, len(X), STEP):
        train_end = end - (H - 1)               # PURGE: drop rows whose target overlaps the test point
        xt = X[end]
        pred, pred_mean = fit(X[:train_end], y[:train_end], xt)
        if has_ovx:
            pred_har, pred_har_mean = fit(X[:train_end, :3], y[:train_end], xt[:3])
            # Implied vol alone: the options market's own forecast, the benchmark HAR-IV must beat.
            pred_ovx, pred_ovx_mean = fit(X[:train_end, 3:4], y[:train_end], xt[3:4])
        else:
            pred_har, pred_har_mean = pred, pred_mean
            pred_ovx = pred_ovx_mean = float("nan")
        if X_lev is not None:
            pred_lev, pred_lev_mean = fit(X_lev[:train_end], y[:train_end], X_lev[end])
        else:
            pred_lev, pred_lev_mean = pred, pred_mean
        recs.append({
            "date": tdate[end],
            "actual": float(y[end]),
            "pred": float(pred),
            "pred_mean": float(pred_mean),
            "pred_har": float(pred_har),
            "pred_har_mean": float(pred_har_mean),
            "pred_lev": float(pred_lev),
            "pred_lev_mean": float(pred_lev_mean),
            "pred_ovx": float(pred_ovx),
            "pred_ovx_mean": float(pred_ovx_mean),
            "current": float(xt[0]),            # persistence baseline: next vol = current 5d RV
            "long_avg": float(xt[2]),           # mean-reversion anchor: 66d RV
            "ovx": float(xt[3]) / 100.0 if has_ovx else float("nan"),
            "rv22": float(xt[1]),
        })
    d = pd.DataFrame(recs)
    if d.empty:
        return d
    d["year"] = d["date"].dt.year
    d["act_up"] = d["actual"] > d["current"]
    d["hit"] = (d["pred"] > d["current"]) == d["act_up"]
    d["hit_har"] = (d["pred_har"] > d["current"]) == d["act_up"]
    d["hit_lev"] = (d["pred_lev"] > d["current"]) == d["act_up"]
    d["hit_mr"] = (d["long_avg"] > d["current"]) == d["act_up"]
    d["hit_ovx"] = (d["pred_ovx"] > d["current"]) == d["act_up"]
    return d


def _pct(x: float) -> float:
    return round(float(x) * 100.0, 1)


def _sharpe(returns: np.ndarray, periods_per_year: float) -> float:
    arr = np.asarray(returns, dtype=float)
    if arr.size < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    # "+ 0.0" normalizes a rounded -0.0 so the dashboard never renders a signed zero.
    return round(float(np.mean(arr)) / sd * np.sqrt(periods_per_year), 2) + 0.0 if sd > 0 else 0.0


def _sharpe_positive_test(pnl: np.ndarray) -> dict[str, Any]:
    """One-sided test that the per-period Sharpe ratio of an i.i.d. P&L series is positive, with the
    non-normal standard error of Mertens (2002) and Opdyke (2007):
        SE(SR) = sqrt((1 - g3 SR + (g4 - 1) / 4 SR^2) / n),
    g3 the skewness and g4 the raw kurtosis (moment estimators). With g3 = 0 and g4 = 3 it is Lo's
    (2002) normal-returns SE; negative skew and fat tails, which short-volatility P&L has, widen it."""
    x = np.asarray(pnl, dtype=float)
    sd = float(np.std(x, ddof=1))
    sr = float(np.mean(x)) / sd if sd > 0 else 0.0
    g3, g4 = float(skew(x)), float(kurtosis(x, fisher=False))
    var = (1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2) / x.size
    p = float(norm.sf(sr / np.sqrt(var))) if var > 0 else None
    return {"sharpe_per_period": round(sr, 4) + 0.0, "skewness": round(g3, 2) + 0.0,
            "excess_kurtosis": round(g4 - 3.0, 2), "p_value_one_sided": round(p, 4) if p is not None else None}


def _overlay_returns(weights: np.ndarray, period_returns: np.ndarray) -> np.ndarray:
    """Net per-period returns of holding `weights` over each period, paying COST_PER_TURN on turnover."""
    w = np.asarray(weights, dtype=float)
    turnover = np.abs(np.diff(np.concatenate([[0.0], w])))
    return cast("np.ndarray", w * np.asarray(period_returns, dtype=float) - COST_PER_TURN * turnover)


def same_contract_returns(r: np.ndarray, dates: pd.DatetimeIndex,
                          curve: pd.DataFrame | None = None) -> tuple[np.ndarray, int, int]:
    """CL=F log returns with every roll day rebuilt as a same-contract return, where EIA allows.

    CL=F is unadjusted: its first return after each last trade date is new contract today over OLD
    contract at the expiry close, so it books the contract-1/contract-2 gap (mostly contango, so
    mostly positive) as if it were a market move. A position rolled at that close earns the same
    return minus the gap, log(contract 2 / contract 1) at the expiry close, which EIA's curve gives.
    EIA stopped publishing in April 2024 and Yahoo keeps no expired contracts, so only roll days up
    to the curve's end can be rebuilt.

    Returns (returns, n_roll_days_rebuilt, end): every return at a position <= end is roll-free.
    """
    curve = eia_curve.clean_curve(eia_curve.load_curve() if curve is None else curve)
    # numpy's stubs type np.log of a Series as an ndarray; it returns a Series.
    days, gap = curve.index, np.log(curve["c2"] / curve["c1"]).to_numpy()  # type: ignore[attr-defined]
    out = np.asarray(r, dtype=float).copy()
    end = int(np.searchsorted(dates, days[-1], side="right")) - 1
    rebuilt = 0
    y, m = dates[0].year, dates[0].month
    while (y, m) <= (days[-1].year + 1, days[-1].month):
        expiry = pd.Timestamp(last_trade_date(y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        # CL=F's first row after expiry. numpy's stubs reject a DatetimeIndex (or Index) and a Timestamp,
        # which searchsorted compares as datetime64, here and for the EIA dates below.
        i = int(np.searchsorted(dates, expiry, side="right"))  # type: ignore[call-overload]
        if not 0 < i <= end:
            continue
        # EIA misses a few expiry days before 2007; the calendar spread a day earlier stands in.
        k = int(np.searchsorted(days, expiry, side="right")) - 1  # type: ignore[call-overload]
        if k < 0 or (expiry - days[k]).days > 7:
            raise RuntimeError(f"EIA has no settlement near the {expiry.date()} expiry")
        out[i] -= gap[k]
        rebuilt += 1
    return out, rebuilt, end


def _nw_regression(y: np.ndarray, x: np.ndarray) -> dict[str, Any] | None:
    """OLS of y on [1, x] with a Newey-West slope t-statistic; None when too short."""
    yv, xv = np.asarray(y, float), np.asarray(x, float)
    if yv.size < 24:
        return None
    A = np.column_stack([np.ones(yv.size), xv])
    beta, *_ = np.linalg.lstsq(A, yv, rcond=None)
    resid = yv - A @ beta
    xc = xv - xv.mean()
    lrv, lags = newey_west_lrv(xc * resid)
    se = np.sqrt(lrv / yv.size) / (xc @ xc / yv.size) if lrv > 0 else float("nan")
    t = float(beta[1] / se) if np.isfinite(se) and se > 0 else 0.0
    return {"slope": round(float(beta[1]), 4), "t_hac": round(t, 2), "p_value": round(float(2 * norm.sf(abs(t))), 3),
            "n": int(yv.size), "lags": int(lags)}


def _risk_profile(weekly: np.ndarray, periods_per_year: float) -> dict[str, Any]:
    """Risk-control scorecard of a weekly return series: realized vol vs the target, vol of vol
    (dispersion of 26-week realized vol), kurtosis, worst week, 97.5% expected shortfall and drawdown."""
    w = np.asarray(weekly, dtype=float)
    rolling = pd.Series(w).rolling(26).std().dropna().to_numpy() * np.sqrt(periods_per_year)
    wealth = np.cumprod(1.0 + w)
    drawdown = 1.0 - wealth / np.maximum.accumulate(wealth)
    tail = np.sort(w)[: max(1, int(np.ceil(0.025 * w.size)))]
    return {
        "sharpe": _sharpe(w, periods_per_year),
        "realized_vol_pct": _pct(np.std(w, ddof=1) * np.sqrt(periods_per_year)),
        "vol_of_vol_pct": _pct(np.std(rolling, ddof=1)) if rolling.size > 1 else None,
        # pandas-stubs type kurt() as any scalar; on a float Series it is a float.
        "excess_kurtosis": round(float(pd.Series(w).kurt()), 2),  # type: ignore[arg-type]
        "worst_week_pct": _pct(w.min()),
        "expected_shortfall_97_5_pct": _pct(tail.mean()),
        "max_drawdown_pct": _pct(drawdown.max()),
    }


def _sharpe_difference(a: np.ndarray, b: np.ndarray, periods_per_year: float, reps: int = 2000) -> dict[str, Any]:
    """Sharpe(a) - Sharpe(b) with a stationary block bootstrap p-value (two-sided), preserving the
    serial dependence and the cross-correlation of the two weekly series (Ledoit-Wolf 2008 spirit)."""
    from arch.bootstrap import StationaryBootstrap

    x, y = np.asarray(a, float), np.asarray(b, float)

    def diff(u: np.ndarray, v: np.ndarray) -> float:
        su, sv = np.std(u, ddof=1), np.std(v, ddof=1)
        return (np.mean(u) / su - np.mean(v) / sv) * np.sqrt(periods_per_year) if su > 0 and sv > 0 else 0.0

    observed = diff(x, y)
    block = max(2, int(round(x.size ** (1 / 3))))
    boot = StationaryBootstrap(block, x, y, seed=NOISE_FLOOR_SEED)
    # arch types the resampled data as array, Series or DataFrame; resampling ndarrays gives ndarrays.
    draws = np.array([diff(u, v) for (u, v), _ in boot.bootstrap(reps)])  # type: ignore[arg-type]
    centered = draws - draws.mean()
    p = float(np.mean(np.abs(centered) >= abs(observed)))
    return {"difference": round(float(observed), 2), "p_value": round(p, 3),
            "ci95": [round(float(np.quantile(draws, 0.025)), 2), round(float(np.quantile(draws, 0.975)), 2)]}


def _zero_premium_null(implied: np.ndarray, realized: np.ndarray, reps: int = VRP_NULL_REPS) -> dict[str, Any]:
    """The vol-point comparison as it would look with NO variance risk premium, and a test of the
    observed share of periods with implied above realized vol against it.

    OVX is a variance-swap-style rate, so a zero premium means IV^2 = E[RV^2] in every period. A
    20-session realized vol is a noisy, right-skewed estimate and RV is concave in RV^2, so even then
    IV exceeds RV in more than half the periods and E[IV - RV] > 0 (Jensen's inequality). The null
    keeps the observed shape of the per-period ratio RV^2 / IV^2 and rescales it to mean 1: the share
    of rescaled ratios below 1 is the share of periods with IV > RV expected with no premium, and
    IV * (1 - sqrt(rescaled ratio)) is the vol-point gap expected with no premium. Period-to-period
    variation in the premium also widens the observed ratios, which raises the null share, so the
    test errs on the conservative side.

    The test is a stationary block bootstrap that imposes the null: it resamples the rescaled ratios
    and in every draw computes the share below 1 minus the share below the draw's own mean (the null
    share re-estimated, as it is on the data), so the uncertainty in the rescaling is included.
    One-sided p = (1 + #{draw >= observed}) / (1 + reps).
    """
    from arch.bootstrap import StationaryBootstrap

    ratio = (np.asarray(realized, float) / np.asarray(implied, float)) ** 2

    def excess_periods(x: np.ndarray) -> int:
        """Periods with IV > RV beyond those expected with no premium (x rescaled to its own mean)."""
        return int(np.sum(x < 1.0)) - int(np.sum(x < np.mean(x)))

    scale = float(np.mean(ratio))
    null_ratio = ratio / scale
    observed = excess_periods(ratio)
    block = max(2, int(round(ratio.size ** (1 / 3))))
    boot = StationaryBootstrap(block, null_ratio, seed=NOISE_FLOOR_SEED)
    # arch types the resampled data as array, Series or DataFrame; resampling an ndarray gives an ndarray.
    draws = np.array([excess_periods(x) for (x,), _ in boot.bootstrap(reps)])  # type: ignore[arg-type]
    p = (1.0 + float(np.sum(draws >= observed))) / (1.0 + reps)
    return {
        "share_positive_pct": _pct(np.mean(null_ratio < 1.0)),
        "mean_premium_vol_pts": round(float(np.mean(implied * (1.0 - np.sqrt(null_ratio)))) * 100.0, 1),
        "mean_ratio_realized_to_implied_variance": round(scale, 4),
        "excess_share_pct": _pct(observed / ratio.size),
        "p_value_one_sided": round(p, 4),
        "bootstrap": {"reps": int(reps), "mean_block_periods": block, "seed": NOISE_FLOOR_SEED},
        "method": ("zero premium: the per-period ratios RV^2 / IV^2 rescaled to mean 1 (their observed shape kept); "
                   "share_positive_pct and mean_premium_vol_pts are the share of periods with IV > RV and the mean "
                   "IV - RV expected with no premium. Test of the observed share against it: stationary block "
                   "bootstrap of the rescaled ratios, re-estimating the null share in every draw; one-sided."),
    }


def _economic_tests(d: pd.DataFrame, r: np.ndarray, dates: pd.DatetimeIndex,
                    curve: pd.DataFrame | None = None) -> dict[str, Any]:
    """Out-of-sample economic value: a vol-targeting overlay and the variance risk premium.

    Both use only the walk-forward forecasts (made with data up to each decision date) and the
    realized path AFTER it, so they carry the same leak-free guarantee as the accuracy figures.
    Unlike direction accuracy, a P&L is biased by roll gaps (every strategy earns its weight times
    each gap), so both tests run on same-contract returns and stop where EIA's curve stops.
    """
    out: dict[str, Any] = {}
    try:
        spliced = np.asarray(r, dtype=float)
        r, rebuilt, last = same_contract_returns(spliced, dates, curve)
    except Exception as exc:  # noqa: BLE001 - no roll-free returns, no P&L figures (never biased ones)
        out["unavailable_reason"] = f"no same-contract returns ({_failure(exc)})"
        logger.warning("economic tests skipped: %s", out["unavailable_reason"])
        return out
    pos = dates.get_indexer(pd.DatetimeIndex(d["date"]))
    ok = (pos >= 0) & (pos + H <= last)
    dd, pos = d.loc[ok].reset_index(drop=True), pos[ok]
    if len(dd) < 20:
        out["unavailable_reason"] = f"{len(dd)} out-of-sample weeks end inside EIA's curve; 20 are needed"
        logger.warning("economic tests skipped: %s", out["unavailable_reason"])
        return out
    # The gaps this window's rebuilt roll days removed (log points; mostly contango, so positive).
    gaps = (spliced - r)[pos[0]:last + 1]
    gaps = gaps[gaps != 0.0]
    out["returns"] = {
        "series": "CL=F with roll days rebuilt as same-contract returns (EIA contract 1 and 2)",
        "roll_days_rebuilt": int(rebuilt),
        "sample_start": str(dd["date"].iloc[0].date()),
        "sample_end": str(dates[last].date()),
        "roll_gap_mean_pct": round(float(np.mean(gaps)) * 100, 2) if gaps.size else None,
        "roll_gap_median_pct": round(float(np.median(gaps)) * 100, 2) if gaps.size else None,
    }
    ppy = 252.0 / STEP
    week = np.array([np.expm1(r[p + 1 : p + 1 + H].sum()) for p in pos])   # next-week simple return
    # Size on the variance-consistent forecast: a vol target is a variance target.
    books = {
        "buy_hold": _overlay_returns(np.ones(len(week)), week),
        "trailing_vol_target": _overlay_returns(np.minimum(LEVERAGE_CAP, TARGET_VOL / dd["rv22"].to_numpy()), week),
        "forecast_vol_target": _overlay_returns(np.minimum(LEVERAGE_CAP, TARGET_VOL / dd["pred_mean"].to_numpy()), week),
    }
    risk = {name: _risk_profile(ret, ppy) for name, ret in books.items()}
    out["vol_target_overlay"] = {
        "n_weeks": int(len(week)),
        "sharpe_buy_hold": risk["buy_hold"]["sharpe"],
        "sharpe_trailing_vol_target": risk["trailing_vol_target"]["sharpe"],
        "sharpe_forecast_vol_target": risk["forecast_vol_target"]["sharpe"],
        # What vol targeting is for (Harvey et al. 2018): hitting the risk target and taming the tails.
        "risk": risk,
        "sharpe_difference_tests": {
            "forecast_minus_buy_hold": _sharpe_difference(books["forecast_vol_target"], books["buy_hold"], ppy),
            "forecast_minus_trailing": _sharpe_difference(books["forecast_vol_target"], books["trailing_vol_target"], ppy),
        },
        "target_vol_pct": _pct(TARGET_VOL),
        "leverage_cap": LEVERAGE_CAP,
        "cost_bps_per_turn": round(COST_PER_TURN * 1e4, 1),
        "method": ("Weekly rebalanced long front-month WTI, rolled at each expiry (same-contract returns); "
                   "position = min(cap, target vol / forecast vol), sized on the variance-consistent forecast. "
                   "Trailing-vol sizing uses 22-day realized vol; costs on every unit of turnover. Sharpe "
                   "differences use a stationary block bootstrap."),
    }

    has_ovx = bool(np.isfinite(dd["ovx"]).all())
    if has_ovx:
        # Non-overlapping ~20-trading-day windows: OVX at t (30 calendar days, ~20 trading days)
        # against the realized vol that followed. Variance-swap P&L per unit of vega notional is
        # (implied^2 - realized^2) / (2 * implied): what a short-variance position would have earned.
        idx = np.arange(0, len(dd), VRP_STEPS)
        idx = idx[pos[idx] + VRP_STEPS * STEP <= last]
        if len(idx) >= 24:
            implied = dd["ovx"].to_numpy()[idx]
            realized = np.array([_rvol(r[pos[i] + 1 : pos[i] + 1 + VRP_STEPS * STEP]) for i in idx])
            premium_pts = (implied - realized) * 100.0
            short_var = (implied ** 2 - realized ** 2) / (2.0 * implied)
            # Timing test: sell only when implied looks rich versus the model's own OOS forecast.
            spread = implied - dd["pred"].to_numpy()[idx]
            rich = np.array([spread[k] > np.median(spread[: k]) if k >= 12 else False
                             for k in range(len(spread))])
            scored = np.arange(len(spread)) >= 12
            rich_pnl, cheap_pnl = short_var[scored & rich], short_var[scored & ~rich]
            timing_p = (float(ttest_ind(rich_pnl, cheap_pnl, equal_var=False, alternative="greater").pvalue)
                        if len(rich_pnl) > 2 and len(cheap_pnl) > 2 else None)
            starts = pd.DatetimeIndex(dd["date"].to_numpy()[idx])
            var_premium = (implied ** 2 - realized ** 2) * 1e4            # variance points (vol pts^2)
            lrv, _ = newey_west_lrv(var_premium)
            half = 1.96 * np.sqrt(lrv / len(var_premium)) if lrv > 0 else 0.0
            var_ci = (float(np.mean(var_premium) - half), float(np.mean(var_premium) + half))
            timing_reg = _nw_regression(short_var[scored], spread[scored])
            worst = np.argsort(premium_pts)[:3]
            worst_year = int(starts[worst[0]].year)
            outside = starts.year != worst_year
            per_year = 252.0 / (VRP_STEPS * STEP)
            out["variance_risk_premium"] = {
                "n_periods": int(len(idx)),
                "mean_premium_vol_pts": round(float(np.mean(premium_pts)), 1),
                "share_positive_pct": _pct(np.mean(premium_pts > 0)),
                # What the two vol-point figures above would be with no premium at all, and the test of
                # the observed share against that share (not against 50%).
                "zero_premium_null": _zero_premium_null(implied, realized),
                "vol_points_caveat": ("With zero variance premium (IV^2 = E[RV^2]) a 20-session realized vol still "
                                      "falls below implied vol in most periods and E[IV - RV] > 0 (Jensen's "
                                      "inequality), so comparing the share of positive periods with 50%, or the mean "
                                      "premium in vol points with 0, overstates the evidence for a premium; "
                                      "zero_premium_null gives both as expected with no premium. The short vol-swap "
                                      "proxy's mean has the same bias, so its Sharpe test against 0 overstates the "
                                      "evidence too."),
                # Linear (vol-swap) vs convex (variance-swap) short-vol proxies: the gap between the
                # two Sharpe ratios is the convexity cost, (IV - RV)^2 / (2 IV) per period, paid every
                # period and largest in the left tail. The worst periods and both Sharpes without the
                # worst year show how much of it one episode explains.
                "short_vol_swap_sharpe": _sharpe(premium_pts, per_year),
                "short_variance_sharpe": _sharpe(short_var, per_year),
                "worst_period_vol_pts": round(float(np.min(premium_pts)), 1),
                "worst_periods": [{"start": str(starts[k].date()), "premium_vol_pts": round(float(premium_pts[k]), 1)}
                                  for k in worst],
                "worst_year": worst_year,
                # Both proxies without the worst year, so the gap is compared like for like.
                "short_vol_swap_sharpe_ex_worst_year": _sharpe(premium_pts[outside], per_year),
                "short_variance_sharpe_ex_worst_year": _sharpe(short_var[outside], per_year),
                # Is either Sharpe ratio positive? The P&L is far from normal (large negative skew,
                # fat tails), so the standard error allows for both.
                "sharpe_positive_test": {
                    "short_vol_swap": _sharpe_positive_test(premium_pts),
                    "short_variance": _sharpe_positive_test(short_var),
                    "method": ("one-sided test of Sharpe > 0 on the non-overlapping periods, standard error "
                               "of Mertens (2002) / Opdyke (2007) for i.i.d. non-normal returns: "
                               "sqrt((1 - skewness * SR + (raw kurtosis - 1) / 4 * SR^2) / n), SR per period; "
                               "raw kurtosis = excess_kurtosis + 3"),
                },
                # The premium in VARIANCE units (what a variance swap pays), with a Newey-West 95% CI.
                "mean_premium_variance_pts": round(float(np.mean(var_premium)), 1),
                "mean_premium_variance_ci95": [round(float(v), 1) for v in var_ci],
                # Timing as a regression: does (OVX - model forecast) predict the short-variance P&L?
                "timing_regression": timing_reg,
                "timed_rich_mean_pnl": round(float(np.mean(rich_pnl)), 4) if len(rich_pnl) else None,
                "timed_cheap_mean_pnl": round(float(np.mean(cheap_pnl)), 4) if len(cheap_pnl) else None,
                "timing_p_value": round(timing_p, 3) if timing_p is not None else None,
                "method": ("OVX (the CBOE Crude Oil ETF Volatility Index, a 30-calendar-day implied vol from "
                           "options on the USO ETF, not on CL futures, so it carries a basis to front-month WTI) "
                           "vs realized vol over the next 20 trading days (about 29 calendar days), non-overlapping. "
                           "Short-vol proxies: vol swap = IV - RV; variance swap per unit vega = (IV^2 - RV^2) / (2 IV). "
                           "Timing: short-variance P&L regressed on OVX minus the model's forecast (Newey-West t), "
                           "and rich = that spread above its trailing median."),
            }
    return out


def _summarize(d: pd.DataFrame, has_ovx: bool, has_leverage: bool = False) -> dict[str, Any]:
    """Overall + year-by-year metrics from the walk-forward records."""
    n_total = int(len(d))
    up_rate = float(d["act_up"].mean())
    base_rate = max(up_rate, 1.0 - up_rate)
    hits = d["hit"].to_numpy(dtype=float)
    n_correct = int(hits.sum())
    acc = float(hits.mean())
    # Exact one-sided binomial test against the majority class: survival function, so the p-value
    # stays representable (1 - cdf underflows to exactly 0.0 below ~1e-16).
    p_value = float(binom.sf(n_correct - 1, n_total, base_rate))
    se_iid = float(np.sqrt(base_rate * (1.0 - base_rate) / n_total))
    z_iid = (acc - base_rate) / se_iid if se_iid > 0 else 0.0
    z_hac, lags = _hac_z(hits, base_rate)
    z_vs_mr, _ = _hac_z(hits - d["hit_mr"].to_numpy(dtype=float))

    overall = {
        "n": n_total,
        "sample_start": str(d["date"].iloc[0].date()),
        "sample_end": str(d["date"].iloc[-1].date()),
        "har_dir_acc_pct": _pct(acc),
        "majority_class_pct": _pct(base_rate),
        "har_dir_p_value_vs_base_rate": p_value,
        "har_dir_z_score": round(float(z_iid), 2),
        "har_dir_z_score_hac": round(float(z_hac), 2),
        "hac_lags": int(lags),
        "mean_reversion_dir_acc_pct": _pct(d["hit_mr"].mean()),
        "har_vs_mean_reversion_z_hac": round(float(z_vs_mr), 2),
        "har_vs_mean_reversion_p_value": float(norm.sf(z_vs_mr)),
        # Level metrics of every model use its variance-consistent forecast (median * sqrt(smearing));
        # har_qlike_median_forecast, the model of record's median forecast scored the same way, is the one
        # labelled extra, kept so the effect of the correction is visible.
        "har_level_r2": round(_r2(d["actual"].to_numpy(), d["pred_mean"].to_numpy()), 3),
        "persistence_level_r2": round(_r2(d["actual"].to_numpy(), d["current"].to_numpy()), 3),
        "har_level_mae": round(float(np.mean(np.abs(d["actual"] - d["pred_mean"]))), 4),
        "persistence_level_mae": round(float(np.mean(np.abs(d["actual"] - d["current"]))), 4),
        "har_qlike": round(_qlike(d["actual"], d["pred_mean"]), 3),
        "har_qlike_median_forecast": round(_qlike(d["actual"], d["pred"]), 3),
        "persistence_qlike": round(_qlike(d["actual"], d["current"]), 3),
        "har_vs_persistence_qlike_dm": round(float(dm_test(qlike(d["actual"], d["current"]),
                                                          qlike(d["actual"], d["pred_mean"]))["stat"]), 2),
        "har_mean_ratio_actual_to_forecast": round(float(np.mean(d["actual"] / d["pred_mean"])), 3),
    }
    if has_ovx:
        z_ovx, _ = _hac_z(hits - d["hit_har"].to_numpy(dtype=float))
        overall.update({
            "har_no_ovx_dir_acc_pct": _pct(d["hit_har"].mean()),
            "har_no_ovx_level_r2": round(_r2(d["actual"].to_numpy(), d["pred_har_mean"].to_numpy()), 3),
            "har_no_ovx_qlike": round(_qlike(d["actual"], d["pred_har_mean"]), 3),
            "ovx_gain_dir_z_hac": round(float(z_ovx), 2),
        })
        # Does HAR-IV beat implied vol ALONE? Direction (paired HAC z) and QLIKE (Diebold-Mariano).
        z_iv, _ = _hac_z(hits - d["hit_ovx"].to_numpy(dtype=float))
        dm_iv = dm_test(qlike(d["actual"], d["pred_ovx_mean"]), qlike(d["actual"], d["pred_mean"]))
        overall.update({
            "ovx_only_dir_acc_pct": _pct(d["hit_ovx"].mean()),
            "ovx_only_qlike": round(_qlike(d["actual"], d["pred_ovx_mean"]), 3),
            "har_vs_ovx_only_dir_z_hac": round(float(z_iv), 2),
            "har_vs_ovx_only_qlike_dm": round(float(dm_iv["stat"]), 2),
            "har_vs_ovx_only_qlike_dm_p_value": round(float(dm_iv["p_value"]), 4),
        })
    if has_leverage:
        # The parsimony check, re-run every time: does adding the leverage term (downside
        # semivolatility) to the deployed model help out of sample? It stays out unless it does.
        z_lev, _ = _hac_z(d["hit_lev"].to_numpy(dtype=float) - hits)
        overall.update({
            "leverage_variant_dir_acc_pct": _pct(d["hit_lev"].mean()),
            "leverage_variant_level_r2": round(_r2(d["actual"].to_numpy(), d["pred_lev_mean"].to_numpy()), 3),
            "leverage_variant_qlike": round(_qlike(d["actual"], d["pred_lev_mean"]), 3),
            "leverage_gain_dir_z_hac": round(float(z_lev), 2),
        })

    yearly: dict[str, dict[str, Any]] = {}
    for yr, g in d.groupby("year"):
        g_up = float(g["act_up"].mean())
        yearly[str(yr)] = {
            "n": int(len(g)),
            "har_dir_acc_pct": _pct(g["hit"].mean()),
            "majority_class_pct": _pct(max(g_up, 1.0 - g_up)),
            "mean_reversion_dir_acc_pct": _pct(g["hit_mr"].mean()),
            "level_r2": round(_r2(g["actual"].to_numpy(), g["pred_mean"].to_numpy()), 3),
        }
    ex20 = d[d["year"] != 2020]
    overall["ex_2020_har_dir_acc_pct"] = _pct(ex20["hit"].mean()) if len(ex20) else None
    overall["ex_2020_n"] = int(len(ex20))
    # Year-by-year robustness is shipped so the dashboard renders it from live data instead of
    # hardcoding figures that drift as OOS weeks accrue.
    yr_accs = [v["har_dir_acc_pct"] for v in yearly.values()]
    overall["yearly_acc_min_pct"] = min(yr_accs) if yr_accs else None
    overall["yearly_acc_max_pct"] = max(yr_accs) if yr_accs else None
    overall["years_total"] = len(yearly)
    overall["years_above_base_rate"] = sum(
        1 for v in yearly.values() if v["har_dir_acc_pct"] > v["majority_class_pct"]
    )
    overall["years_beating_mean_reversion"] = sum(
        1 for v in yearly.values() if v["har_dir_acc_pct"] > v["mean_reversion_dir_acc_pct"]
    )
    overall["model"] = "HAR-IV (RV5,RV22,RV66,OVX)" if has_ovx else "HAR (RV5,RV22,RV66)"
    return {"overall": overall, "yearly": yearly}


def noise_floor(r: np.ndarray, dates: pd.DatetimeIndex, sims: int = NOISE_FLOOR_SIMS) -> dict[str, Any]:
    """Direction accuracy when nothing is forecastable: i.i.d. Student-t(5) returns with the sample's
    volatility through the identical feature builder and walk-forward (pure HAR, no OVX). The
    mean-reversion rule is scored on the same rows: HAR beats it here too, with nothing to forecast.
    The one implementation of the floor: the research lab reports it too (vol_lab.noise_floor)."""
    sd = float(np.std(r))
    accs, accs_mr = [], []
    for seed in range(sims):
        rng = np.random.default_rng(NOISE_FLOOR_SEED + seed)
        z = rng.standard_t(5, len(r)) / np.sqrt(5 / 3) * sd
        X, y, tdate = _build_matrix(z, dates, None)
        d = _walk_forward(X, y, tdate, has_ovx=False)
        accs.append(float(d["hit"].mean()))
        accs_mr.append(float(d["hit_mr"].mean()))
    return {"sims": sims, "dir_acc_pct_mean": _pct(np.mean(accs)),
            "dir_acc_pct_range": [_pct(min(accs)), _pct(max(accs))],
            "mean_reversion_dir_acc_pct_mean": _pct(np.mean(accs_mr))}


def validate(period: str = HISTORY_PERIOD, data: MarketData | None = None,
             return_frame: bool = False) -> dict[str, Any] | tuple[dict[str, Any], pd.DataFrame]:
    """Purged walk-forward validation: overall + year-by-year metrics, baselines, economic tests.

    `data` is an optional (returns, dates, ovx) tuple, with ovx the SAME-DAY aligned series, so
    callers can share one download between validate() and live_forecast(). The model uses OVX lagged
    OVX_LAG_SESSIONS (entry-time clean). With return_frame=True the out-of-sample weekly records are
    returned too, as (report, frame), for the forecast interval and the dashboard series.
    """
    r, dates, ovx_raw = data[:3] if data is not None else _load_data(period)
    ovx_raw = _pick_ovx(ovx_raw)
    ovx = lag_ovx(ovx_raw)
    X, y, tdate, pos = _build_matrix(r, dates, ovx, return_positions=True)
    if len(X) <= MIN_TRAIN:
        raise RuntimeError(f"insufficient history for validation ({len(X)} rows <= {MIN_TRAIN})")
    # Leverage term on the same rows; floored so a week without a down day never takes log(0).
    leverage = np.array([max(_downside_semivol(r[t - 4 : t + 1]), 1e-4) for t in pos])
    d = _walk_forward(X, y, tdate, has_ovx=ovx is not None, leverage=leverage)
    report = _summarize(d, has_ovx=ovx is not None, has_leverage=True)
    overall = report["overall"]
    overall["ovx_lag_sessions"] = OVX_LAG_SESSIONS if ovx is not None else None
    overall["forecast_used_for_losses"] = "variance-consistent: median * sqrt(Duan smearing factor)"
    mz = mincer_zarnowitz(d["actual"].to_numpy(), d["pred_mean"].to_numpy())
    overall["har_mincer_zarnowitz"] = {k: round(v, 4) for k, v in mz.items()}
    overall["direction_noise_floor"] = noise_floor(r, dates)
    report["economic"] = _economic_tests(d, r, dates)
    # The weekly test grid samples one day in five; the other four offsets test the same model on the
    # other days. A headline that held on one offset only would be a sampling accident.
    offsets = [_walk_forward(X, y, tdate, has_ovx=ovx is not None, offset=k) for k in range(STEP)]
    overall["dir_acc_pct_by_grid_offset"] = [_pct(o["hit"].mean()) for o in offsets]
    overall["majority_class_pct_by_grid_offset"] = [
        _pct(max(o["act_up"].mean(), 1.0 - o["act_up"].mean())) for o in offsets
    ]
    overall["qlike_by_grid_offset"] = [round(_qlike(o["actual"], o["pred_mean"]), 3) for o in offsets]
    if ovx is not None:
        # Timing robustness: the v1 model used the test day's OWN OVX close, which settles at 16:15 ET,
        # ~1h45m after the 14:30 ET CL settlement that starts the target window. Same rows, same grid.
        Xs = X.copy()
        # ovx is lag_ovx(ovx_raw), so ovx_raw is not None here.
        Xs[:, 3] = np.array([ovx_raw[t] if np.isfinite(ovx_raw[t]) and ovx_raw[t] > 0 else X[i, 3]  # type: ignore[index]
                             for i, t in enumerate(pos)])
        ds = _walk_forward(Xs, y, tdate, has_ovx=True)
        overall["robustness_same_day_ovx"] = {
            "qlike": round(_qlike(ds["actual"], ds["pred_mean"]), 3),
            "qlike_dm_vs_lagged": round(float(dm_test(qlike(d["actual"], d["pred_mean"]),
                                                      qlike(ds["actual"], ds["pred_mean"]))["stat"]), 2),
            "dir_acc_pct": _pct(ds["hit"].mean()),
        }
    returns = report["economic"].get("returns")
    if returns is not None:
        # How much the roll gaps left in the forecast series matter for the headline: the same
        # walk-forward on same-contract returns (rebuilt through the curve's end, CL=F after it).
        r_same = same_contract_returns(r, dates)[0]
        X_s, y_s, tdate_s = _build_matrix(r_same, dates, ovx)
        d_same = _walk_forward(X_s, y_s, tdate_s, has_ovx=ovx is not None)
        returns["har_dir_acc_pct_on_same_contract_returns"] = _pct(d_same["hit"].mean())
        returns["har_qlike_on_same_contract_returns"] = round(_qlike(d_same["actual"], d_same["pred_mean"]), 3)
        returns["n_on_same_contract_returns"] = int(len(d_same))
    return (report, d) if return_frame else report


def _conformal_interval(frame: pd.DataFrame) -> dict[str, Any]:
    """Adaptive conformal 80% interval state from the out-of-sample log errors of the MEDIAN forecast."""
    e = np.log(frame["actual"].to_numpy(dtype=float)) - np.log(frame["pred"].to_numpy(dtype=float))
    aci = adaptive_conformal(e, alpha=INTERVAL_ALPHA)
    hits = aci["hit"][np.isfinite(aci["hit"])]
    return {"aci": aci, "coverage_pct": _pct(hits.mean()) if hits.size else None, "n": int(hits.size)}


def live_forecast(period: str = HISTORY_PERIOD, data: MarketData | None = None, meta: dict[str, Any] | None = None,
                  frame: pd.DataFrame | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Train on all matured rows and forecast next-week realized vol.

    Publishes the variance-consistent forecast (the expected vol, for risk and losses), the median
    (the typical outcome, which decides the direction call), the model's probability that next
    week's realized vol exceeds this week's, and, with `frame` (validate()'s out-of-sample records),
    an adaptive conformal 80% interval. Every field needed to audit or later score the call is
    included: the session the forecast is made at (as_of), the inputs with the OVX session used, and
    the fitted coefficients.
    """
    r, dates, ovx_raw = data[:3] if data is not None else _load_data(period)
    ovx = lag_ovx(_pick_ovx(ovx_raw))
    cur_feat = [_rvol(r[-5:]), _rvol(r[-22:]), _rvol(r[-66:])]
    # HAR-IV needs the (lagged) OVX print. If it is missing (stale beyond the ffill limit), the
    # forecast falls back to pure HAR rather than mixing a 4-coefficient model with 3 features.
    live_ovx = float(ovx[-1]) if ovx is not None and np.isfinite(ovx[-1]) and ovx[-1] > 0 else None
    use_ovx = live_ovx is not None
    X, y, _ = _build_matrix(r, dates, ovx if use_ovx else None)
    if len(X) < MIN_TRAIN:
        raise RuntimeError(f"insufficient history for a live forecast ({len(X)} rows)")
    beta = _fit_log_har(X, y)                   # all matured rows; the last H days have no target yet
    smear = _smearing(X, y, beta)
    sd = _resid_sd(X, y, beta)
    if use_ovx:                                 # live_ovx is not None; mypy does not narrow through use_ovx
        cur_feat.append(live_ovx)  # type: ignore[arg-type]
    median = _predict_log_har(beta, np.array(cur_feat))
    mean = median * float(np.sqrt(smear))
    current_vol = float(cur_feat[0])
    prob_up = float(norm.sf((np.log(current_vol) - np.log(median)) / sd)) if sd > 0 else None
    as_of = dates[-1].date()
    out = {
        "current_realized_vol_5d_annualized_pct": round(current_vol * 100, 1),
        "forecast_next_week_vol_annualized_pct": round(mean * 100, 1),
        "forecast_median_pct": round(median * 100, 1),
        "prob_vol_rises_pct": round(prob_up * 100, 1) if prob_up is not None else None,
        "direction": "RISING" if median > current_vol else "FALLING",
        "implied_vol_ovx_pct": round(live_ovx, 1) if use_ovx else None,  # type: ignore[arg-type]
        "model": "HAR-IV (RV5,RV22,RV66,OVX)" if use_ovx else "HAR (RV5,RV22,RV66)",
        "model_version": MODEL_VERSION if use_ovx else "har/2",
        "as_of": str(as_of),
        "issued_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "inputs": {"rv5_pct": round(cur_feat[0] * 100, 2), "rv22_pct": round(cur_feat[1] * 100, 2),
                   "rv66_pct": round(cur_feat[2] * 100, 2),
                   "ovx": round(live_ovx, 2) if use_ovx else None,  # type: ignore[arg-type]
                   "ovx_session": str(dates[-1 - OVX_LAG_SESSIONS].date()) if use_ovx else None,
                   "ovx_last_print": (meta or {}).get("ovx_as_of"),
                   "ovx_source": (meta or {}).get("ovx_source")},
        # Null, or why an OVX source failed (Yahoo, then FRED): with both down the model is pure HAR.
        "ovx_error": (meta or {}).get("ovx_error"),
        "coefficients": [round(float(b), 6) for b in beta],
        "smearing_factor": round(smear, 4),
        "residual_sd": round(sd, 4),
        "interval_80": None,
        "challenger": None,
    }
    if frame is not None and len(frame) > 60 and use_ovx:
        state = _conformal_interval(frame)["aci"]
        out["interval_80"] = {
            "low_pct": round(median * np.exp(state["next_lo"]) * 100, 1),
            "high_pct": round(median * np.exp(state["next_hi"]) * 100, 1),
            "method": "adaptive conformal inference on out-of-sample log errors (Gibbs-Candes 2021)",
        }
    return out


def _recent_series(frame: pd.DataFrame, weeks: int = RECENT_OOS_WEEKS) -> dict[str, Any]:
    """The last `weeks` out-of-sample weeks, compact, for the dashboard (percent, 1 decimal)."""
    aci = _conformal_interval(frame)["aci"]
    tail = slice(max(0, len(frame) - weeks), len(frame))
    med = frame["pred"].to_numpy(dtype=float)

    def pct(values: npt.ArrayLike) -> list[float | None]:
        return [None if not np.isfinite(v) else round(float(v) * 100, 1) for v in np.asarray(values, float)[tail]]

    return {
        "date": [str(t.date()) for t in frame["date"].iloc[tail]],
        "actual": pct(frame["actual"]), "forecast": pct(frame["pred_mean"]), "median": pct(med),
        "rv5": pct(frame["current"]), "lo80": pct(med * np.exp(aci["lo"])), "hi80": pct(med * np.exp(aci["hi"])),
    }


def _lab_challenger() -> tuple[str | None, str | None]:
    """(the challenger chosen by the research lab in data/vol_lab.json, None) or (None, why not)."""
    try:
        lab = json.loads(data_json_path("vol_lab.json").read_text(encoding="utf-8"))
        return str(lab["config"]["challenger"]), None
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return None, f"no challenger in data/vol_lab.json ({_failure(exc)})"


def _challenger_forecast(r: np.ndarray, dates: pd.DatetimeIndex, ovx: np.ndarray | None,
                         meta: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """(the live challenger forecast, None) or (None, why there is none). A failure is logged and
    never touches the model of record, which does not depend on the challenger."""
    challenger, reason = _lab_challenger()
    if challenger is not None and _pick_ovx(ovx) is None:
        reason = "too little OVX history for the challenger, which uses implied vol"
    elif challenger is not None and meta.get("ohlc") is None:
        reason = f"no usable OHLC bars for the challenger's range features ({meta.get('ohlc_error')})"
    elif challenger is not None:
        try:
            try:
                from . import vol_lab
            except ImportError:  # pragma: no cover - direct invocation
                import vol_lab  # type: ignore[import-not-found, no-redef]
            # _pick_ovx(ovx) is not None on this branch, so ovx is not None.
            return vol_lab.live_model_forecast(challenger, r, dates, ovx, meta["ohlc"]), None  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - the challenger is optional; the model of record never depends on it
            reason = f"{challenger} forecast failed ({_failure(exc)})"
    logger.warning("no live challenger forecast: %s", reason)
    return None, reason


def forecast_bundle(period: str = HISTORY_PERIOD) -> dict[str, Any]:
    """Live forecast + validation from ONE download (the dashboard payload's vol_forecast block)."""
    r, dates, ovx, meta = _load_data_full(period)
    data = (r, dates, ovx)
    report, frame = cast("tuple[dict[str, Any], pd.DataFrame]", validate(period, data=data, return_frame=True))
    use_ovx = _pick_ovx(ovx) is not None
    interval = _conformal_interval(frame) if use_ovx else None
    validation = dict(report["overall"])
    if interval is not None:
        validation["interval_80_coverage_pct"] = interval["coverage_pct"]
        validation["interval_80_n"] = interval["n"]
    live = live_forecast(period, data=data, meta=meta, frame=frame)
    # challenger_error is null when the challenger is published, else the reason it is missing.
    live["challenger"], live["challenger_error"] = _challenger_forecast(r, dates, ovx, meta)
    return {
        "schema_version": 2,
        "live": live,
        "validation": validation,
        "economic": report.get("economic", {}),
        "recent_oos": _recent_series(frame) if use_ovx else None,
        "recent_closes": meta.get("recent_closes"),
        # A report on the closes behind this forecast, outside `live`: never part of a sealed entry.
        "source_check": _source_check(meta.get("cl_close")),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="WTI HAR-IV realized-vol forecaster + purged validation.")
    ap.add_argument("--period", default=HISTORY_PERIOD, help="yfinance history window (default: max)")
    ap.add_argument("--out", default="data/vol_forecast_validation.json", help="artifact path")
    args = ap.parse_args()

    r, dates, ovx, meta = _load_data_full(args.period)
    data = (r, dates, ovx)
    report, frame = cast("tuple[dict[str, Any], pd.DataFrame]", validate(args.period, data=data, return_frame=True))
    report["live"] = live_forecast(args.period, data=data, meta=meta, frame=frame)
    report["config"] = {"horizon_days": H, "min_train": MIN_TRAIN, "step": STEP,
                        "model": report["overall"].get("model", "HAR"), "period": args.period}
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    output_path = data_json_path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    o = report["overall"]
    print(f"Vol-forecast validation ({o['sample_start']}..{o['sample_end']}, {o.get('model')} {MODEL_VERSION}, "
          f"OVX lagged {o.get('ovx_lag_sessions')} session, purged walk-forward, n={o['n']}):")
    print(f"  QLIKE (variance-consistent): {o['har_qlike']} vs persistence {o['persistence_qlike']} "
          f"(DM {o['har_vs_persistence_qlike_dm']}); median forecast {o['har_qlike_median_forecast']}")
    mz = o["har_mincer_zarnowitz"]
    print(f"  Mincer-Zarnowitz        : slope {mz['slope']:.2f} (SE {mz['slope_se']:.2f}), joint p {mz['p_unbiased']:.3f}")
    if "ovx_only_qlike" in o:
        print(f"  vs implied vol only     : QLIKE {o['ovx_only_qlike']} (DM {o['har_vs_ovx_only_qlike_dm']}, "
              f"p {o['har_vs_ovx_only_qlike_dm_p_value']})")
    nf = o["direction_noise_floor"]
    print(f"  vol-DIRECTION accuracy  : {o['har_dir_acc_pct']}% (noise floor with nothing to forecast: "
          f"{nf['dir_acc_pct_mean']}%; not evidence of skill on its own)")
    print(f"  vs mean-reversion       : {o['mean_reversion_dir_acc_pct']}% "
          f"(paired HAC z={o['har_vs_mean_reversion_z_hac']})")
    if "har_no_ovx_dir_acc_pct" in o:
        print(f"  vs HAR without OVX     : {o['har_no_ovx_dir_acc_pct']}% "
              f"(paired HAC z={o['ovx_gain_dir_z_hac']}); R2 {o['har_no_ovx_level_r2']}, QLIKE {o['har_no_ovx_qlike']}")
    if "leverage_variant_dir_acc_pct" in o:
        print(f"  + leverage term (tested, left out): {o['leverage_variant_dir_acc_pct']}% "
              f"(paired HAC z={o['leverage_gain_dir_z_hac']}); R2 {o['leverage_variant_level_r2']}, "
              f"QLIKE {o['leverage_variant_qlike']}")
    print(f"  ex-2020                : {o['ex_2020_har_dir_acc_pct']}%  (n={o['ex_2020_n']})")
    print(f"  years above base rate  : {o['years_above_base_rate']}/{o['years_total']} "
          f"({o['yearly_acc_min_pct']}-{o['yearly_acc_max_pct']}%), beats mean-reversion in "
          f"{o['years_beating_mean_reversion']}/{o['years_total']}")
    print(f"  level R2 / MAE          : {o['har_level_r2']} / {o['har_level_mae']} "
          f"vs persistence {o['persistence_level_r2']} / {o['persistence_level_mae']}")
    econ = report.get("economic", {})
    ov = econ.get("vol_target_overlay")
    if ov:
        print(f"  vol-target overlay     : Sharpe buy-hold {ov['sharpe_buy_hold']} | trailing-vol "
              f"{ov['sharpe_trailing_vol_target']} | forecast {ov['sharpe_forecast_vol_target']}")
    vrp = econ.get("variance_risk_premium")
    if vrp:
        sp, null = vrp["sharpe_positive_test"], vrp["zero_premium_null"]
        print(f"  variance risk premium  : {vrp['mean_premium_vol_pts']} vol pts, positive "
              f"{vrp['share_positive_pct']}% of periods (with zero premium: {null['mean_premium_vol_pts']} vol pts, "
              f"{null['share_positive_pct']}%; p {null['p_value_one_sided']}), short-vol Sharpe "
              f"{vrp['short_vol_swap_sharpe']} (p {sp['short_vol_swap']['p_value_one_sided']}, non-normal SE; variance-swap "
              f"{vrp['short_variance_sharpe']}, p {sp['short_variance']['p_value_one_sided']}), "
              f"worst {vrp['worst_period_vol_pts']} pts; timing p={vrp['timing_p_value']}")
    live = report["live"]
    print(f"  live: next-week vol {live['forecast_next_week_vol_annualized_pct']}% "
          f"({live['direction']}) vs current {live['current_realized_vol_5d_annualized_pct']}%")
    print("  -> report written successfully")


if __name__ == "__main__":
    main()
