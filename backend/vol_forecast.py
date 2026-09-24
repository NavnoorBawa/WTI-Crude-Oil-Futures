#!/usr/bin/env python3
"""
WTI realized-volatility forecaster (HAR-IV) — the project's real, validated signal.

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
    drifts as new out-of-sample weeks accrue — it does not shift because a rolling window silently
    drops old years (the previous 10-year window moved the base rate by ~3pp in three weeks).
  - Vol-DIRECTION (will next week's realized vol exceed this week's?) against three references:
    the majority class (exact binomial test), a mean-reversion rule (vol reverts toward its 66-day
    average — a PAIRED Diebold-Mariano test on the 0/1 hit series, Newey-West variance), and the
    same HAR model without OVX, fitted on the SAME rows (the nested baseline that isolates what
    implied vol adds).
  - The hit series is autocorrelated (consecutive labels share a week of returns), so an i.i.d.
    z-score is reported alongside a Newey-West (HAC) one; the dashboard quotes the smaller.
  - Vol-LEVEL: R^2 and MAE, plus QLIKE — the loss that ranks volatility forecasts consistently
    when the realized-vol proxy is noisy (Patton 2011) — versus naive persistence.
  - Economic value, stated honestly: a volatility-targeting overlay (does the forecast size a long
    WTI position better than trailing vol or buy-and-hold?) and the oil variance risk premium (does
    OVX exceed subsequent realized vol, and does the forecast time it?). Both are out-of-sample.

Honest scope: this is a clean implementation of a KNOWN effect, not novel alpha. It is a validated
volatility/regime INDICATOR, not a directional return signal, and the economic tests below do not
show it converting into trading profit.

Parsimony note: the standard HAR "leverage" enhancement (downside realized semivolatility, so down
weeks predict higher vol) is re-tested inside validate() as a nested variant on the same rows. It is
left out of the deployed model because it does not significantly improve out-of-sample results —
added complexity that does not earn its keep is how vol models overfit.

Leakage controls (identical discipline to backtest_walk_forward.py):
  - Features at day t use only returns and OVX up to and including t.
  - Target = realized vol of days t+1..t+H, so the walk-forward PURGES the last H-1 training rows
    whose target matures after the prediction point.
  - The 2020-04-20 negative settlement (an expiry artifact) is dropped before any computation.

Data caveat: CL=F is Yahoo's UNADJUSTED front-month series (it equals EIA's contract-1 price), so
the monthly roll day carries the contract-1/contract-2 price gap. Measured on 2016-2024 (where EIA's
curve is available) the median gap is ~0.5% and replacing roll-day returns with roll-adjusted ones
moves direction accuracy by under 1pp, so the effect is documented rather than patched — EIA stopped
publishing the curve in April 2024, so it could not be corrected live.

Usage:
    python -m backend.vol_forecast                 # print validation + write artifact
    python -m backend.vol_forecast --out data/vol_forecast_validation.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

try:
    from .safe_paths import data_json_path
except ImportError:  # Direct invocation: python backend/vol_forecast.py
    from safe_paths import data_json_path

import numpy as np
import pandas as pd
from scipy.stats import binom, norm, ttest_ind

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


def _load_data(period: str = HISTORY_PERIOD):
    """Daily WTI log returns (CL=F, non-positive prints dropped) plus aligned OVX implied vol.

    Returns (returns, dates, ovx|None) with a timezone-naive date index. OVX (CBOE Crude Oil
    Volatility Index) is fetched best-effort; the caller falls back to pure HAR when it is missing
    or too short to validate, so the forecast still works without it.
    """
    import yfinance as yf  # deferred: keeps the pure math importable (and testable) offline

    df = yf.Ticker("CL=F").history(period=period, interval="1d")
    if df is None or df.empty or "Close" not in df:
        raise RuntimeError("CL=F history unavailable")
    close = df["Close"]
    close = close[close > 0]                # drop the 2020-04-20 negative settlement artifact
    logret = np.log(close).diff().dropna()
    if getattr(logret.index, "tz", None) is not None:
        logret.index = logret.index.tz_localize(None)
    ovx = None
    try:
        o = yf.Ticker("^OVX").history(period=period, interval="1d")["Close"]
        if getattr(o.index, "tz", None) is not None:
            o.index = o.index.tz_localize(None)
        o = o[o > 0]
        # OVX at day t is known at t (no leak). Leading days before OVX existed stay NaN.
        aligned = o.reindex(logret.index).ffill(limit=OVX_FFILL_LIMIT).to_numpy(dtype=float)
        ovx = aligned if np.isfinite(aligned).any() else None
    except Exception:  # noqa: BLE001 - OVX is optional; pure HAR is the documented fallback
        ovx = None
    return logret.to_numpy(dtype=float), pd.DatetimeIndex(logret.index), ovx


load_market_data = _load_data


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
                  return_positions: bool = False):
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
        if use_ovx:
            if not np.isfinite(ovx[t]) or ovx[t] <= 0:
                continue
            row.append(float(ovx[t]))
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
    return beta


def _predict_log_har(beta: np.ndarray, x: np.ndarray) -> float:
    return float(np.exp(beta[0] + beta[1:] @ np.log(x)))


def _r2(actual: np.ndarray, pred: np.ndarray) -> float:
    ss_res = np.sum((actual - pred) ** 2)
    ss_tot = np.sum((actual - actual.mean()) ** 2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def _qlike(actual: np.ndarray, pred: np.ndarray) -> float:
    """QLIKE loss on variances (0 = perfect). Robust to noise in the realized-vol proxy (Patton 2011)."""
    ratio = (np.asarray(actual, float) / np.asarray(pred, float)) ** 2
    return float(np.mean(ratio - np.log(ratio) - 1.0))


def newey_west_lrv(x: np.ndarray, lags: int | None = None) -> tuple[float, int]:
    """Bartlett-kernel long-run variance of a series around its mean, with the Newey-West (1994)
    automatic bandwidth floor(4 (n/100)^(2/9)) when lags is None. Returns (variance, lags)."""
    arr = np.asarray(x, dtype=float)
    n = arr.size
    if n < 2:
        return 0.0, 0
    if lags is None:
        lags = int(np.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))
    lags = max(0, min(int(lags), n - 1))
    e = arr - arr.mean()
    lrv = float(e @ e) / n
    for k in range(1, lags + 1):
        lrv += 2.0 * (1.0 - k / (lags + 1.0)) * float(e[k:] @ e[:-k]) / n
    return max(lrv, 0.0), lags


def _hac_z(x: np.ndarray, null_mean: float = 0.0) -> tuple[float, int]:
    """z-statistic for mean(x) > null_mean with a Newey-West standard error."""
    arr = np.asarray(x, dtype=float)
    lrv, lags = newey_west_lrv(arr)
    if arr.size == 0 or lrv <= 0:
        return 0.0, lags
    return float((arr.mean() - null_mean) / np.sqrt(lrv / arr.size)), lags


def _pick_ovx(ovx: np.ndarray | None) -> np.ndarray | None:
    """Use OVX only when there is enough of it to validate a HAR-IV model on; else pure HAR."""
    if ovx is None:
        return None
    valid = np.isfinite(ovx) & (ovx > 0)
    return ovx if int(valid.sum()) >= MIN_OVX_ROWS else None


def _walk_forward(X: np.ndarray, y: np.ndarray, tdate: pd.DatetimeIndex, has_ovx: bool,
                  leverage: np.ndarray | None = None) -> pd.DataFrame:
    """Purged expanding-window walk-forward, one test point per STEP rows.

    Returns one record per OOS week with the HAR(-IV) forecast, the nested pure-HAR forecast fitted
    on the SAME rows (when OVX is in use), the model plus a leverage term (downside semivolatility,
    when `leverage` is given — the candidate that was tested and rejected), and the persistence /
    mean-reversion references.
    """
    X_lev = np.column_stack([X, leverage]) if leverage is not None else None
    recs = []
    for end in range(MIN_TRAIN, len(X), STEP):
        train_end = end - (H - 1)               # PURGE: drop rows whose target overlaps the test point
        xt = X[end]
        beta = _fit_log_har(X[:train_end], y[:train_end])
        pred = _predict_log_har(beta, xt)
        if has_ovx:
            beta_har = _fit_log_har(X[:train_end, :3], y[:train_end])
            pred_har = _predict_log_har(beta_har, xt[:3])
        else:
            pred_har = pred
        if X_lev is not None:
            pred_lev = _predict_log_har(_fit_log_har(X_lev[:train_end], y[:train_end]), X_lev[end])
        else:
            pred_lev = pred
        recs.append({
            "date": tdate[end],
            "actual": float(y[end]),
            "pred": float(pred),
            "pred_har": float(pred_har),
            "pred_lev": float(pred_lev),
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


def _overlay_returns(weights: np.ndarray, period_returns: np.ndarray) -> np.ndarray:
    """Net per-period returns of holding `weights` over each period, paying COST_PER_TURN on turnover."""
    w = np.asarray(weights, dtype=float)
    turnover = np.abs(np.diff(np.concatenate([[0.0], w])))
    return w * np.asarray(period_returns, dtype=float) - COST_PER_TURN * turnover


def _economic_tests(d: pd.DataFrame, r: np.ndarray, dates: pd.DatetimeIndex) -> dict:
    """Out-of-sample economic value: a vol-targeting overlay and the variance risk premium.

    Both use only the walk-forward forecasts (made with data up to each decision date) and the
    realized path AFTER it, so they carry the same leak-free guarantee as the accuracy figures.
    """
    out: dict = {}
    pos = dates.get_indexer(pd.DatetimeIndex(d["date"]))
    ok = (pos >= 0) & (pos + H < len(r))
    dd, pos = d.loc[ok].reset_index(drop=True), pos[ok]
    if len(dd) < 20:
        return out
    ppy = 252.0 / STEP
    week = np.array([np.expm1(r[p + 1 : p + 1 + H].sum()) for p in pos])   # next-week simple return
    buy_hold = _overlay_returns(np.ones(len(week)), week)
    naive = _overlay_returns(np.minimum(LEVERAGE_CAP, TARGET_VOL / dd["rv22"].to_numpy()), week)
    model = _overlay_returns(np.minimum(LEVERAGE_CAP, TARGET_VOL / dd["pred"].to_numpy()), week)
    out["vol_target_overlay"] = {
        "n_weeks": int(len(week)),
        "sharpe_buy_hold": _sharpe(buy_hold, ppy),
        "sharpe_trailing_vol_target": _sharpe(naive, ppy),
        "sharpe_forecast_vol_target": _sharpe(model, ppy),
        "target_vol_pct": _pct(TARGET_VOL),
        "leverage_cap": LEVERAGE_CAP,
        "cost_bps_per_turn": round(COST_PER_TURN * 1e4, 1),
        "method": ("Weekly rebalanced long WTI (CL=F); position = min(cap, target vol / forecast vol). "
                   "Trailing-vol sizing uses 22-day realized vol; costs on every unit of turnover."),
    }

    has_ovx = bool(np.isfinite(dd["ovx"]).all())
    if has_ovx:
        # Non-overlapping ~20-trading-day windows: OVX at t (30 calendar days, ~20 trading days)
        # against the realized vol that followed. Variance-swap P&L per unit of vega notional is
        # (implied^2 - realized^2) / (2 * implied): what a short-variance position would have earned.
        idx = np.arange(0, len(dd), VRP_STEPS)
        idx = idx[pos[idx] + VRP_STEPS * STEP < len(r)]
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
            out["variance_risk_premium"] = {
                "n_periods": int(len(idx)),
                "mean_premium_vol_pts": round(float(np.mean(premium_pts)), 1),
                "share_positive_pct": _pct(np.mean(premium_pts > 0)),
                # Linear (vol-swap) vs convex (variance-swap) short-vol proxies: the gap between the
                # two Sharpe ratios IS the left tail — convexity is what 2008/2014/2020 cost a seller.
                "short_vol_swap_sharpe": _sharpe(premium_pts, 252.0 / (VRP_STEPS * STEP)),
                "short_variance_sharpe": _sharpe(short_var, 252.0 / (VRP_STEPS * STEP)),
                "worst_period_vol_pts": round(float(np.min(premium_pts)), 1),
                "timed_rich_mean_pnl": round(float(np.mean(rich_pnl)), 4) if len(rich_pnl) else None,
                "timed_cheap_mean_pnl": round(float(np.mean(cheap_pnl)), 4) if len(cheap_pnl) else None,
                "timing_p_value": round(timing_p, 3) if timing_p is not None else None,
                "method": ("OVX vs realized vol over the next 20 trading days, non-overlapping. Short-vol "
                           "proxies: vol swap = IV - RV; variance swap per unit vega = (IV^2 - RV^2) / (2 IV). "
                           "Timing: rich = OVX minus the model's forecast above its trailing median."),
            }
    return out


def _summarize(d: pd.DataFrame, has_ovx: bool, has_leverage: bool = False) -> dict:
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
        "har_level_r2": round(_r2(d["actual"].to_numpy(), d["pred"].to_numpy()), 3),
        "persistence_level_r2": round(_r2(d["actual"].to_numpy(), d["current"].to_numpy()), 3),
        "har_level_mae": round(float(np.mean(np.abs(d["actual"] - d["pred"]))), 4),
        "persistence_level_mae": round(float(np.mean(np.abs(d["actual"] - d["current"]))), 4),
        "har_qlike": round(_qlike(d["actual"], d["pred"]), 3),
        "persistence_qlike": round(_qlike(d["actual"], d["current"]), 3),
    }
    if has_ovx:
        z_ovx, _ = _hac_z(hits - d["hit_har"].to_numpy(dtype=float))
        overall.update({
            "har_no_ovx_dir_acc_pct": _pct(d["hit_har"].mean()),
            "har_no_ovx_level_r2": round(_r2(d["actual"].to_numpy(), d["pred_har"].to_numpy()), 3),
            "har_no_ovx_qlike": round(_qlike(d["actual"], d["pred_har"]), 3),
            "ovx_gain_dir_z_hac": round(float(z_ovx), 2),
        })
    if has_leverage:
        # The parsimony check, re-run every time: does adding the leverage term (downside
        # semivolatility) to the deployed model help out of sample? It stays out unless it does.
        z_lev, _ = _hac_z(d["hit_lev"].to_numpy(dtype=float) - hits)
        overall.update({
            "leverage_variant_dir_acc_pct": _pct(d["hit_lev"].mean()),
            "leverage_variant_level_r2": round(_r2(d["actual"].to_numpy(), d["pred_lev"].to_numpy()), 3),
            "leverage_variant_qlike": round(_qlike(d["actual"], d["pred_lev"]), 3),
            "leverage_gain_dir_z_hac": round(float(z_lev), 2),
        })

    yearly = {}
    for yr, g in d.groupby("year"):
        g_up = float(g["act_up"].mean())
        yearly[str(yr)] = {
            "n": int(len(g)),
            "har_dir_acc_pct": _pct(g["hit"].mean()),
            "majority_class_pct": _pct(max(g_up, 1.0 - g_up)),
            "mean_reversion_dir_acc_pct": _pct(g["hit_mr"].mean()),
            "level_r2": round(_r2(g["actual"].to_numpy(), g["pred"].to_numpy()), 3),
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


def validate(period: str = HISTORY_PERIOD, data: tuple | None = None) -> dict:
    """Purged walk-forward validation: overall + year-by-year metrics, baselines, economic tests.

    `data` is an optional (returns, dates, ovx) tuple so callers can share one download between
    validate() and live_forecast().
    """
    r, dates, ovx = data if data is not None else _load_data(period)
    ovx = _pick_ovx(ovx)
    X, y, tdate, pos = _build_matrix(r, dates, ovx, return_positions=True)
    if len(X) <= MIN_TRAIN:
        raise RuntimeError(f"insufficient history for validation ({len(X)} rows <= {MIN_TRAIN})")
    # Leverage term on the same rows; floored so a week without a down day never takes log(0).
    leverage = np.array([max(_downside_semivol(r[t - 4 : t + 1]), 1e-4) for t in pos])
    d = _walk_forward(X, y, tdate, has_ovx=ovx is not None, leverage=leverage)
    report = _summarize(d, has_ovx=ovx is not None, has_leverage=True)
    report["economic"] = _economic_tests(d, r, dates)
    return report


def live_forecast(period: str = HISTORY_PERIOD, data: tuple | None = None) -> dict:
    """Train on all matured rows and forecast next-week realized vol + its direction."""
    r, dates, ovx = data if data is not None else _load_data(period)
    ovx = _pick_ovx(ovx)
    cur_feat = [_rvol(r[-5:]), _rvol(r[-22:]), _rvol(r[-66:])]
    # HAR-IV needs today's OVX. If the latest print is missing (stale beyond the ffill limit), the
    # forecast falls back to pure HAR rather than mixing a 4-coefficient model with 3 features.
    live_ovx = float(ovx[-1]) if ovx is not None and np.isfinite(ovx[-1]) and ovx[-1] > 0 else None
    use_ovx = live_ovx is not None
    X, y, _ = _build_matrix(r, dates, ovx if use_ovx else None)
    if len(X) < MIN_TRAIN:
        raise RuntimeError(f"insufficient history for a live forecast ({len(X)} rows)")
    beta = _fit_log_har(X, y)                   # all matured rows; the last H days have no target yet
    if use_ovx:
        cur_feat.append(live_ovx)
    pred = _predict_log_har(beta, np.array(cur_feat))
    current_vol = float(cur_feat[0])
    return {
        "current_realized_vol_5d_annualized_pct": round(current_vol * 100, 1),
        "forecast_next_week_vol_annualized_pct": round(pred * 100, 1),
        "direction": "RISING" if pred > current_vol else "FALLING",
        "implied_vol_ovx_pct": round(live_ovx, 1) if use_ovx else None,
        "model": "HAR-IV (RV5,RV22,RV66,OVX)" if use_ovx else "HAR (RV5,RV22,RV66)",
        "as_of": str(dates[-1].date()),
    }


def forecast_bundle(period: str = HISTORY_PERIOD) -> dict:
    """Live forecast + validation from ONE download (the dashboard payload's vol_forecast block)."""
    data = _load_data(period)
    report = validate(period, data=data)
    return {
        "live": live_forecast(period, data=data),
        "validation": report["overall"],
        "economic": report.get("economic", {}),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="WTI HAR-IV realized-vol forecaster + purged validation.")
    ap.add_argument("--period", default=HISTORY_PERIOD, help="yfinance history window (default: max)")
    ap.add_argument("--out", default="data/vol_forecast_validation.json", help="artifact path")
    args = ap.parse_args()

    data = _load_data(args.period)
    report = validate(args.period, data=data)
    report["live"] = live_forecast(args.period, data=data)
    report["config"] = {"horizon_days": H, "min_train": MIN_TRAIN, "step": STEP,
                        "model": report["overall"].get("model", "HAR"), "period": args.period}
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    output_path = data_json_path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    o = report["overall"]
    print(f"Vol-forecast validation ({o['sample_start']}..{o['sample_end']}, {o.get('model')}, "
          f"purged walk-forward, n={o['n']}):")
    print(f"  vol-DIRECTION accuracy : {o['har_dir_acc_pct']}% vs majority {o['majority_class_pct']}% "
          f"(z={o['har_dir_z_score']}, HAC z={o['har_dir_z_score_hac']}, p={o['har_dir_p_value_vs_base_rate']:.1e})")
    print(f"  vs mean-reversion      : {o['mean_reversion_dir_acc_pct']}% "
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
    print(f"  level R2 / MAE / QLIKE : {o['har_level_r2']} / {o['har_level_mae']} / {o['har_qlike']} "
          f"vs persistence {o['persistence_level_r2']} / {o['persistence_level_mae']} / {o['persistence_qlike']}")
    econ = report.get("economic", {})
    ov = econ.get("vol_target_overlay")
    if ov:
        print(f"  vol-target overlay     : Sharpe buy-hold {ov['sharpe_buy_hold']} | trailing-vol "
              f"{ov['sharpe_trailing_vol_target']} | forecast {ov['sharpe_forecast_vol_target']}")
    vrp = econ.get("variance_risk_premium")
    if vrp:
        print(f"  variance risk premium  : {vrp['mean_premium_vol_pts']} vol pts, positive "
              f"{vrp['share_positive_pct']}% of periods, short-vol Sharpe {vrp['short_vol_swap_sharpe']} "
              f"(variance-swap {vrp['short_variance_sharpe']}), "
              f"worst {vrp['worst_period_vol_pts']} pts; timing p={vrp['timing_p_value']}")
    live = report["live"]
    print(f"  live: next-week vol {live['forecast_next_week_vol_annualized_pct']}% "
          f"({live['direction']}) vs current {live['current_realized_vol_5d_annualized_pct']}%")
    print("  -> report written successfully")


if __name__ == "__main__":
    main()
