#!/usr/bin/env python3
"""
Serial-correlation / effective-sample-size audit for the 1W walk-forward result.

The headline significance claim (65.8% direction accuracy, p<0.001) rests on the OOS
trades being effectively independent. They are NOT perfectly independent: the 63-bar
feature window and 5-bar stride mean adjacent predictions share ~92% of their feature
inputs. Feature overlap does not force error correlation — the *targets* (the 5-day
forward returns) are non-overlapping at step=5 — but regime persistence and model
continuity still leave a little. This script measures it instead of assuming it.

Method (reproduces data/serial_correlation_check.json exactly):
  1. Build the OOS direction-hit series from the backtest trades. A trade is
     "gross-correct" (hit=1) when its net P&L > -cost, i.e. the direction was right
     before the fixed transaction cost is subtracted.
  2. Lag-1..10 autocorrelation of the hit series (and, for reference, of the P&L series).
  3. Bartlett-kernel Newey-West effective sample size at L=5 lags:
         ESS = n / (1 + 2 * Σ_{k=1..L} (1 - k/(L+1)) * rho_k)
     L=5 matches the stride: lags beyond 5 correspond to non-overlapping target
     windows, so there is no principled reason to deflate further.
  4. One-sided z-test that accuracy > 50% at the *effective* sample size.

Pure standard library (no numpy/scipy) so it runs anywhere and the headline number is
reproducible from the committed trades alone.

Usage:
    python backend/serial_correlation.py
    python backend/serial_correlation.py --backtest data/walk_forward_backtest_latest.json \
                                         --horizon 1w --out data/serial_correlation_check.json
"""

import argparse
import json
from datetime import datetime, timezone
from math import erf, sqrt
from pathlib import Path

TRANSACTION_COST_USD = 100.0  # must match backtest_walk_forward.TRANSACTION_COST_USD
NEWEY_WEST_LAGS = 5           # L: matches the walk-forward stride (step=5)
MAX_REPORTED_LAG = 10


def autocorrelation(series: list, lag: int) -> float:
    """Sample autocorrelation of `series` at the given lag (0 -> 1.0)."""
    n = len(series)
    if lag <= 0:
        return 1.0
    if n <= lag:
        return 0.0
    mean = sum(series) / n
    denom = sum((v - mean) ** 2 for v in series)
    if denom == 0:
        return 0.0
    num = sum((series[i] - mean) * (series[i - lag] - mean) for i in range(lag, n))
    return num / denom


def bartlett_ess(series: list, lags: int) -> float:
    """Bartlett-kernel Newey-West effective sample size."""
    n = len(series)
    if n == 0:
        return 0.0
    factor = 1.0 + 2.0 * sum(
        (1.0 - k / (lags + 1)) * autocorrelation(series, k)
        for k in range(1, lags + 1)
    )
    # A strongly negatively-autocorrelated series can push the factor below 1 (ESS > n);
    # clamp at n because you can never have *more* independent observations than samples.
    return n / factor if factor > 0 else float(n)


def norm_sf(z: float) -> float:
    """One-sided upper-tail probability of the standard normal (1 - CDF)."""
    return 0.5 * (1.0 - erf(z / sqrt(2.0)))


def round_sig(x: float, sig: int) -> float:
    """Round to `sig` significant figures (keeps tiny p-values readable)."""
    if x == 0:
        return 0.0
    from math import floor, log10
    return round(x, -int(floor(log10(abs(x)))) + (sig - 1))


def analyze(trades: list, horizon: str) -> dict:
    hits = [1.0 if float(t["pnl"]) > -TRANSACTION_COST_USD else 0.0 for t in trades]
    pnls = [float(t["pnl"]) for t in trades]
    n = len(hits)
    if n == 0:
        raise ValueError("no trades to analyze")

    hit_rate = sum(hits) / n
    acf_hits = [round(autocorrelation(hits, k), 4) for k in range(1, MAX_REPORTED_LAG + 1)]
    acf_pnl = [round(autocorrelation(pnls, k), 4) for k in range(1, MAX_REPORTED_LAG + 1)]

    ess = bartlett_ess(hits, NEWEY_WEST_LAGS)
    z = (hit_rate - 0.5) / sqrt(0.25 / ess) if ess > 0 else 0.0
    p_one_sided = norm_sf(z)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": f"data/walk_forward_backtest_latest.json ({horizon} trades, n={n})",
        "method": (
            "Lag-1..10 autocorrelation of the OOS direction-hit series "
            "(gross-correct = pnl > -cost), Bartlett-kernel Newey-West ESS at L=5, "
            "z-test for accuracy > 50% at that ESS."
        ),
        "hit_rate_pct": round(hit_rate * 100, 2),
        "autocorr_hits_lag1_10": acf_hits,
        "autocorr_pnl_lag1_10": acf_pnl,
        "ess_bartlett_L5": round(ess, 1),
        "n_nominal": n,
        "z_at_ess": round(z, 3),
        "p_one_sided_at_ess": round_sig(p_one_sided, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Serial-correlation / ESS audit of walk-forward trades.")
    parser.add_argument("--backtest", default="data/walk_forward_backtest_latest.json",
                        help="walk-forward report produced by backtest_walk_forward.py")
    parser.add_argument("--horizon", default="1w", help="horizon key to audit (default: 1w)")
    parser.add_argument("--out", default="data/serial_correlation_check.json",
                        help="path to write the audit JSON")
    args = parser.parse_args()

    report = json.loads(Path(args.backtest).read_text())
    horizon_result = report.get("results", {}).get(args.horizon, {})
    trades = horizon_result.get("trades", [])
    if not trades:
        raise SystemExit(f"No '{args.horizon}' trades found in {args.backtest}")

    result = analyze(trades, args.horizon)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"Serial-correlation audit written to: {out_path}")
    print(f"  hit rate:        {result['hit_rate_pct']}%  (n={result['n_nominal']})")
    print(f"  autocorr lag-1:  {result['autocorr_hits_lag1_10'][0]}")
    print(f"  ESS (Bartlett):  {result['ess_bartlett_L5']} / {result['n_nominal']}")
    print(f"  z @ ESS:         {result['z_at_ess']}")
    print(f"  p (one-sided):   {result['p_one_sided_at_ess']}")


if __name__ == "__main__":
    main()
