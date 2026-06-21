#!/usr/bin/env python3
"""
WTI realized-volatility forecaster (HAR-RV) — the project's real, validated signal.

Why this exists: the original 1-week DIRECTION model was a look-ahead leakage artifact and,
once purged, is a coin flip (direction on a liquid contract is near-unforecastable, as theory
predicts). VOLATILITY is different. Volatility clustering and mean-reversion are among the most
replicated facts in financial econometrics, so a properly validated vol forecast has genuine
out-of-sample skill. This module forecasts next-week (5-trading-day) realized volatility from a heterogeneous-
autoregressive feature set augmented with implied volatility (HAR-IV: RV over 5/22/66 days plus
OVX, the free CBOE oil implied-vol index), validated with the SAME purged walk-forward used to
expose the direction leak. Adding OVX nearly doubles the level R^2 (0.30 -> 0.50, and 0.23 -> 0.40
ex-2020, improving in almost every year), because implied vol is forward-looking — a real, leak-free
gain, not a tuning artifact. If OVX is unavailable it falls back to the pure-HAR feature set.

What is honest about the result (10y, leak-free, n=439 OOS, see validate()):
  - Vol-DIRECTION (will next week's realized vol rise or fall vs this week): ~72% accuracy vs a
    51.7% base rate, STABLE every year (62-86%), and 2020/COVID is the weakest year, not the
    driver. Beats a smart mean-reversion baseline (~65%) by ~7pp in nearly every year.
  - Vol-LEVEL: log-HAR roughly ties naive persistence on R^2 (~0.30) and beats it ~15% on MAE.
    The level calibration is the weaker part; the direction/regime signal is the strong, usable one.
  - This is a clean implementation of a KNOWN effect, not novel alpha. It is a validated
    volatility/regime INDICATOR, not a directional return signal.
  - P&L honesty: a vol-targeting overlay (scale a long WTI position inversely to forecast vol) was
    tested and did NOT beat buy-and-hold risk-adjusted (Sharpe 0.36 buy-hold vs 0.28 naive-lagged
    vs 0.27 HAR), and the forecast did NOT beat trivial lagged-vol sizing. No trading P&L is
    claimed from this forecast; its honest use is risk monitoring / options-vol input.
  - Parsimony note: the standard HAR "leverage" enhancement (adding downside realized semivariance,
    so down days predict higher future vol) was tested on this series and did NOT improve OOS
    (71.5% vs 72.0% direction, R^2 0.300 vs 0.304). It is deliberately left out — added complexity
    that does not earn its keep is how vol models overfit. The model stays the parsimonious HAR.

Leakage controls (identical discipline to backtest_walk_forward.py):
  - Features at day t use only returns up to and including t.
  - Target = realized vol of days t+1..t+H, so the walk-forward PURGES the last H-1 training rows
    whose target matures after the prediction point.
  - The 2020-04-20 negative settlement (a roll artifact) is dropped before any computation.

Usage:
    python -m backend.vol_forecast                 # print validation + write artifact
    python -m backend.vol_forecast --period 10y --out data/vol_forecast_validation.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import binom

H = 5                      # forecast horizon in trading days (1 week)
MIN_TRAIN = 250            # minimum expanding-window rows before the first OOS forecast
STEP = 5                   # walk-forward stride (non-overlapping weekly test points)
ANNUALIZE = np.sqrt(252.0)


def _load_data(period: str = "10y"):
    """Daily WTI log returns (CL=F, negative print dropped) plus aligned OVX implied vol.

    OVX (CBOE Crude Oil Volatility Index) is forward-looking and adds real predictive power for
    realized vol (the HAR-IV result). It is fetched best-effort: if unavailable, the caller falls
    back to the pure-HAR feature set so the forecast still works. Returns (returns, dates, ovx|None).
    """
    df = yf.Ticker("CL=F").history(period=period, interval="1d")
    df = df[df["Close"] > 0].copy()        # drop the 2020-04-20 negative settlement artifact
    logret = np.log(df["Close"]).diff().dropna()
    ovx = None
    try:
        o = yf.Ticker("^OVX").history(period=period, interval="1d")["Close"]
        o.index = o.index.tz_localize(None)
        o = o[o > 0]
        logret.index = logret.index.tz_localize(None)
        ovx = o.reindex(logret.index).ffill().values    # OVX at day t, known at t (no leak)
    except Exception:
        ovx = None
    return logret.values, logret.index, ovx


def _rvol(daily_logrets: np.ndarray) -> float:
    """Annualized realized volatility from a block of daily log returns."""
    arr = np.asarray(daily_logrets, dtype=float)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr ** 2)) * ANNUALIZE)


def _build_matrix(r: np.ndarray, dates: pd.DatetimeIndex, ovx: np.ndarray | None = None):
    """HAR features (RV over 5/22/66 days, all <= t) + optional OVX, target = RV of days t+1..t+H.

    Feature order keeps RV5 first (used as the persistence baseline / current-vol reference).
    When OVX is present it is appended as a 4th feature (HAR-IV); rows with a missing OVX are skipped.
    """
    use_ovx = ovx is not None
    feats, target, tdate = [], [], []
    for t in range(66, len(r) - H):
        past = r[: t + 1]
        row = [_rvol(past[-5:]), _rvol(past[-22:]), _rvol(past[-66:])]
        if use_ovx:
            if not np.isfinite(ovx[t]) or ovx[t] <= 0:
                continue
            row.append(float(ovx[t]))
        feats.append(tuple(row))
        target.append(_rvol(r[t + 1 : t + 1 + H]))
        tdate.append(dates[t])
    return np.array(feats), np.array(target), pd.to_datetime(tdate)


def _fit_log_har(X_train: np.ndarray, y_train: np.ndarray) -> np.ndarray:
    """OLS in log space — the standard HAR specification (keeps forecasts positive)."""
    A = np.column_stack([np.ones(len(X_train)), np.log(X_train)])
    beta, *_ = np.linalg.lstsq(A, np.log(y_train), rcond=None)
    return beta


def _predict_log_har(beta: np.ndarray, x: np.ndarray) -> float:
    return float(np.exp(beta[0] + beta[1:] @ np.log(x)))


def _r2(actual: np.ndarray, pred: np.ndarray) -> float:
    ss_res = np.sum((actual - pred) ** 2)
    ss_tot = np.sum((actual - actual.mean()) ** 2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def validate(period: str = "10y") -> dict:
    """Purged walk-forward validation. Returns overall + year-by-year metrics and baselines."""
    r, dates, ovx = _load_data(period)
    X, y, tdate = _build_matrix(r, dates, ovx)

    recs = []
    for end in range(MIN_TRAIN, len(X), STEP):
        train_end = end - (H - 1)               # PURGE: drop rows whose target overlaps the test point
        beta = _fit_log_har(X[:train_end], y[:train_end])
        xt = X[end]
        cur, long_avg = xt[0], xt[2]            # current 5d RV and 66d RV
        pred = _predict_log_har(beta, xt)
        recs.append({
            "year": int(tdate[end].year),
            "actual": float(y[end]),
            "pred": float(pred),
            "persistence": float(cur),          # naive baseline: next vol = current 5d RV
            "act_up": bool(y[end] > cur),
            "har_up": bool(pred > cur),
            "mr_up": bool(long_avg > cur),      # smart baseline: vol reverts toward 66d average
        })
    d = pd.DataFrame(recs)

    def dir_acc(mask_pred, mask_act):
        return round(float((mask_pred == mask_act).mean()) * 100, 1)

    # One-sided binomial p-value: is HAR direction accuracy better than just predicting the
    # majority class? Null p0 = base rate, so this is the honest "beats the trivial predictor" test.
    n_total = len(d)
    base_rate = float(max(d.act_up.mean(), 1 - d.act_up.mean()))
    n_correct = int(round(float((d.har_up == d.act_up).mean()) * n_total))
    har_p_value = float(1.0 - binom.cdf(n_correct - 1, n_total, base_rate)) if n_total else 1.0

    overall = {
        "n": int(n_total),
        "har_dir_acc_pct": dir_acc(d.har_up, d.act_up),
        "har_dir_p_value_vs_base_rate": har_p_value,
        "mean_reversion_dir_acc_pct": dir_acc(d.mr_up, d.act_up),
        "majority_class_pct": round(base_rate * 100, 1),
        "har_level_r2": round(_r2(d.actual.values, d.pred.values), 3),
        "persistence_level_r2": round(_r2(d.actual.values, d.persistence.values), 3),
        "har_level_mae": round(float(np.mean(np.abs(d.actual - d.pred))), 4),
        "persistence_level_mae": round(float(np.mean(np.abs(d.actual - d.persistence))), 4),
    }
    yearly = {
        str(yr): {
            "n": int(len(g)),
            "har_dir_acc_pct": dir_acc(g.har_up, g.act_up),
            "mean_reversion_dir_acc_pct": dir_acc(g.mr_up, g.act_up),
            "level_r2": round(_r2(g.actual.values, g.pred.values), 3),
        }
        for yr, g in d.groupby("year")
    }
    ex20 = d[d.year != 2020]
    overall["ex_2020_har_dir_acc_pct"] = dir_acc(ex20.har_up, ex20.act_up)
    overall["ex_2020_n"] = int(len(ex20))
    overall["model"] = "HAR-IV (RV5,RV22,RV66,OVX)" if ovx is not None else "HAR (RV5,RV22,RV66)"

    return {"overall": overall, "yearly": yearly}


def live_forecast(period: str = "10y") -> dict:
    """Train on all available data and forecast next-week realized vol + its direction."""
    r, dates, ovx = _load_data(period)
    X, y, _ = _build_matrix(r, dates, ovx)
    beta = _fit_log_har(X, y)                   # all matured rows; the last H-1 days have no target yet
    # Current feature row uses the most recent 66 returns (all observed), plus current OVX if present.
    cur_feat = [_rvol(r[-5:]), _rvol(r[-22:]), _rvol(r[-66:])]
    if ovx is not None and np.isfinite(ovx[-1]) and ovx[-1] > 0:
        cur_feat.append(float(ovx[-1]))
    pred = _predict_log_har(beta, np.array(cur_feat))
    current_vol = float(cur_feat[0])
    return {
        "current_realized_vol_5d_annualized_pct": round(current_vol * 100, 1),
        "forecast_next_week_vol_annualized_pct": round(pred * 100, 1),
        "direction": "RISING" if pred > current_vol else "FALLING",
        "implied_vol_ovx_pct": round(float(ovx[-1]), 1) if (ovx is not None and np.isfinite(ovx[-1])) else None,
        "as_of": str(dates[-1].date()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="WTI HAR realized-vol forecaster + purged validation.")
    ap.add_argument("--period", default="10y", help="yfinance history window (default 10y)")
    ap.add_argument("--out", default="data/vol_forecast_validation.json", help="artifact path")
    args = ap.parse_args()

    report = validate(args.period)
    report["live"] = live_forecast(args.period)
    report["config"] = {"horizon_days": H, "min_train": MIN_TRAIN, "step": STEP,
                        "model": report["overall"].get("model", "HAR"), "period": args.period}
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")

    o = report["overall"]
    print(f"Vol-forecast validation ({args.period}, {o.get('model')}, purged walk-forward, n={o['n']}):")
    print(f"  vol-DIRECTION accuracy : {o['har_dir_acc_pct']}%  "
          f"(mean-reversion base {o['mean_reversion_dir_acc_pct']}%, majority {o['majority_class_pct']}%, "
          f"p={o['har_dir_p_value_vs_base_rate']:.1e})")
    print(f"  ex-2020                : {o['ex_2020_har_dir_acc_pct']}%  (n={o['ex_2020_n']})")
    print(f"  level R2 (HAR vs persistence): {o['har_level_r2']} vs {o['persistence_level_r2']}")
    print(f"  level MAE (HAR vs persistence): {o['har_level_mae']} vs {o['persistence_level_mae']}")
    print(f"  live: next-week vol {report['live']['forecast_next_week_vol_annualized_pct']}% "
          f"({report['live']['direction']}) vs current {report['live']['current_realized_vol_5d_annualized_pct']}%")
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
