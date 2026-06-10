#!/usr/bin/env python3
"""
Timing leakage test: same-day vs one-day-lagged cross-asset context features.

The walk-forward backtest enters trades at the WTI settlement (~14:30 ET), but the
cross-asset context features (VIX, XLE, XOP, SPY, TNX) close at 16:00 ET — so the
same-day feature set contains ~90 minutes of post-entry information.

Hypothesis: if the 1W signal depends on that post-entry window, lagging ALL context
features by one full trading day (strictly conservative — entry-time clean) should
materially degrade direction accuracy and Sharpe.

If the lagged config holds, the signal is robust to entry timing and the headline
config should switch to the lagged (bulletproof) feature set.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure backend is importable from project root.
sys.path.insert(0, str(Path(__file__).parent))

from backend.backtest_walk_forward import (
    prepare_daily_dataset,
    evaluate_horizon,
)
from backend.oil import PremiumWTIPredictor


PERIOD = "5y"
MIN_TRAIN = 200
STEP = 10        # default: quick screen, ~100 OOS samples per config
ESTIMATORS = 20  # default: fewer trees — enough to detect signal direction
HORIZON = "1w"   # the only live signal
# Full headline-config confirmation (matches walk_forward_backtest_latest.json):
#   python run_timing_leakage_test.py --step 5 --estimators 40


def run_config(predictor, wti_data, lag_days: int, min_train: int, step: int) -> dict:
    dataset, feature_cols = prepare_daily_dataset(
        predictor, wti_data, HORIZON, feature_mode="no_macro", lag_context_days=lag_days
    )
    if dataset.empty or len(dataset) <= min_train:
        return {"error": f"insufficient rows: {len(dataset)}"}
    result = evaluate_horizon(predictor, dataset, feature_cols, HORIZON, min_train, step)
    ens = result["metrics"]["ensemble"]
    pnl = result.get("pnl_metrics", {})
    return {
        "lag_context_days": lag_days,
        "samples": ens["samples"],
        "direction_accuracy": round(ens["direction_accuracy"], 2),
        "direction_p_value": ens.get("direction_p_value"),
        "is_significant_5pct": ens.get("is_significant_5pct"),
        "mae": round(ens["mae"], 4),
        "sharpe": pnl.get("sharpe_ratio_annualized"),
        "win_rate_pct": pnl.get("win_rate_pct"),
        "total_pnl_usd": pnl.get("total_pnl_usd"),
        "n_trades": pnl.get("n_trades"),
    }


def main():
    parser = argparse.ArgumentParser(description="Timing-leakage test: same-day vs lagged context.")
    parser.add_argument("--step", type=int, default=STEP, help="walk-forward stride (5 = headline config)")
    parser.add_argument("--estimators", type=int, default=ESTIMATORS, help="trees per model (40 = headline config)")
    parser.add_argument("--min-train", type=int, default=MIN_TRAIN)
    parser.add_argument("--output", default="data/timing_leakage_test.json")
    args = parser.parse_args()

    print("=== Timing Leakage Test (same-day vs lagged cross-asset context) ===")
    print(f"Period: {PERIOD}, min_train: {args.min_train}, step: {args.step}, estimators: {args.estimators}\n")

    predictor = PremiumWTIPredictor()
    predictor.model_n_estimators = max(20, int(args.estimators))
    predictor.model_cpu_workers = 1

    print("Fetching WTI data...")
    wti_data = predictor.get_wti_historical_data(period=PERIOD, interval="1d")
    print(f"Loaded {len(wti_data)} bars of WTI data\n")

    results = {}
    for lag in (0, 1):
        label = "same_day" if lag == 0 else "lagged_1d"
        print(f"Running {label} config (no_macro, context lag = {lag})...")
        results[label] = run_config(predictor, wti_data, lag, args.min_train, args.step)
        r = results[label]
        print(f"  acc={r.get('direction_accuracy')}% p={r.get('direction_p_value')} "
              f"sharpe={r.get('sharpe')} n={r.get('samples')}\n")

    same = results.get("same_day", {})
    lag1 = results.get("lagged_1d", {})
    s0, s1 = same.get("sharpe") or 0, lag1.get("sharpe") or 0
    a0, a1 = same.get("direction_accuracy") or 0, lag1.get("direction_accuracy") or 0
    print("=== VERDICT ===")
    print(f"same_day:  acc={a0}%  sharpe={s0:.3f}")
    print(f"lagged_1d: acc={a1}%  sharpe={s1:.3f}  (delta sharpe {s1 - s0:+.3f})")
    if s1 >= s0 - 0.5 and a1 >= 50:
        print("CLEAN: signal survives strictly entry-time-clean features — "
              "headline config should adopt the lagged feature set")
    else:
        print("TIMING LEAKAGE: same-day context closes materially inflate the result — "
              "headline numbers must be revised to the lagged config")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "config": {"period": PERIOD, "min_train": args.min_train, "step": args.step,
                   "estimators": args.estimators, "feature_mode": "no_macro"},
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
