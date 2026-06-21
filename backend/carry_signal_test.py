#!/usr/bin/env python3
"""
Term-structure CARRY test for WTI direction — a documented NEGATIVE result.

Carry (backwardation vs contango) is one of the most robust commodity factors cross-sectionally,
so it was the most promising remaining free-data directional signal after the price-momentum
direction model turned out to be a leak. This script tests it honestly for WTI time-series timing.

Design (leak-free, with the one subtlety that matters):
  - SIGNAL: carry = (front - second) / second, from EIA's free WTI futures curve (RCLC1, RCLC2).
    >0 = backwardation (classically bullish), <0 = contango.
  - RETURNS: from the back-adjusted continuous series (yfinance CL=F), NOT the raw RCLC1 series.
    The raw "contract 1" price rolls DOWN mechanically when backwardated, which would spuriously
    make carry predict the wrong sign — using the back-adjusted holding return avoids that bug.
  - Purged walk-forward, 21-day (monthly) horizon, rule = "long when carry > training median".

Result (2004-2024, n~896 OOS): accuracy 48.1% vs a 55.4% base rate (p=1.0); carry-timed Sharpe
-0.03 vs buy-and-hold 0.25. The a-priori carry hypothesis FAILS for WTI direction timing. The data
leans the opposite way (contango -> higher forward returns), but that is the crash-recovery cycle
(deep contango marks oil-glut bottoms that then bounce), not a robust signal — testing that reverse
would be reverse-engineering the in-sample answer, so it is left as an observation, not a strategy.

Conclusion: consistent with the rest of the project — WTI direction is not forecastable from the
signals reachable here (momentum was a leak; carry has no edge). Only volatility is forecastable.

Usage: python -m backend.carry_signal_test   (needs EIA_API_KEY in .env; writes the artifact)
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import dotenv_values
from scipy.stats import binom

HORIZON = 21        # trading days (monthly carry rebalance)
MIN_TRAIN = 500
STEP = 5


def _eia_series(series: str, key: str, length: int = 5000) -> pd.Series:
    url = "https://api.eia.gov/v2/petroleum/pri/fut/data/?" + urllib.parse.urlencode({
        "api_key": key, "frequency": "daily", "data[0]": "value", "facets[series][]": series,
        "sort[0][column]": "period", "sort[0][direction]": "desc", "length": str(length),
    }, doseq=True)
    with urllib.request.urlopen(url, timeout=40) as r:
        data = json.load(r)["response"]["data"]
    s = pd.Series({pd.Timestamp(x["period"]): float(x["value"])
                   for x in data if x["value"] is not None})
    return s.sort_index()


def run() -> dict:
    key = dotenv_values(".env").get("EIA_API_KEY", "")
    if not key:
        raise SystemExit("EIA_API_KEY not found in .env")

    curve = pd.DataFrame({"c1": _eia_series("RCLC1", key), "c2": _eia_series("RCLC2", key)}).dropna()
    carry = (curve.c1 - curve.c2) / curve.c2

    cl = yf.Ticker("CL=F").history(period="max")["Close"]
    cl.index = cl.index.tz_localize(None)
    px = cl[cl > 0].reindex(curve.index).ffill()
    fwd = px.shift(-HORIZON) / px - 1.0

    df = pd.DataFrame({"carry": carry, "fwd": fwd}).dropna()
    X, ret = df.carry.values, df.fwd.values
    up = (ret > 0).astype(int)

    preds, acts, rets, longflag = [], [], [], []
    for end in range(MIN_TRAIN, len(df), STEP):
        train_end = end - (HORIZON - 1)                 # purge rows whose forward return overlaps
        thr = np.median(X[:train_end])                  # a-priori rule, threshold from training only
        preds.append(int(X[end] > thr))
        acts.append(int(up[end])); rets.append(float(ret[end])); longflag.append(bool(X[end] > thr))
    preds, acts, rets, longflag = map(np.array, (preds, acts, rets, longflag))

    acc = float((preds == acts).mean())
    base = float(max(acts.mean(), 1 - acts.mean()))
    n_correct = int(round(acc * len(acts)))
    p_value = float(1.0 - binom.cdf(n_correct - 1, len(acts), base))

    def sharpe(r):
        return float(r.mean() / r.std() * np.sqrt(252 / HORIZON)) if r.std() > 0 else 0.0

    return {
        "n_oos": int(len(acts)),
        "span": f"{df.index.min().date()}..{df.index.max().date()}",
        "horizon_days": HORIZON,
        "carry_direction_accuracy_pct": round(acc * 100, 1),
        "base_rate_pct": round(base * 100, 1),
        "p_value": p_value,
        "carry_timed_sharpe": round(sharpe(np.where(longflag, rets, 0.0)), 2),
        "buy_hold_sharpe": round(sharpe(rets), 2),
        "mean_fwd_ret_backwardated_pct": round(float(rets[longflag].mean()) * 100, 2),
        "mean_fwd_ret_contango_pct": round(float(rets[~longflag].mean()) * 100, 2),
        "verdict": "NEGATIVE: carry does not time WTI direction (accuracy below base rate, p=1.0).",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    res = run()
    Path("data/carry_signal_test.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"Carry direction test ({res['span']}, n={res['n_oos']}, {res['horizon_days']}d):")
    print(f"  accuracy {res['carry_direction_accuracy_pct']}% vs base rate {res['base_rate_pct']}%  p={res['p_value']:.2g}")
    print(f"  carry-timed Sharpe {res['carry_timed_sharpe']} vs buy-hold {res['buy_hold_sharpe']}")
    print(f"  -> {res['verdict']}")


if __name__ == "__main__":
    main()
