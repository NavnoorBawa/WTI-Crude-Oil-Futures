#!/usr/bin/env python3
"""
Macro leakage test of the RETRACTED direction model: no_macro vs with_macro walk-forward Sharpe.

Record only. Both configurations ran the original 1W direction model's walk-forward, which
trained on labels that matured after the decision (a look-ahead leak; see
data/leak_anatomy.json), so the committed data/macro_leakage_test.json says nothing about a real
signal. That record is a quick run with a coarser stride and fewer trees than the constants
below (its config block gives both). The engine now purges by default
(backtest_walk_forward.evaluate_horizon), so a rerun measures the purged model and overwrites
that record.

What it tested: if macro features (EIA weekly stocks, FRED monthly) inflated the backtest
result through data-revision look-ahead, the WITH_MACRO Sharpe would be materially higher than
the NO_MACRO Sharpe.

NOTE: requires EIA_API_KEY and internet access to FRED. Runtime is long
(~2× headline backtest) because the with_macro config must fetch and align
EIA weekly stocks + FRED monthly series for every training fold.
"""

import json
import sys
from datetime import datetime, timezone
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
STEP = 5         # the retracted 5-year run's stride (~199 OOS decisions)
ESTIMATORS = 40  # the retracted 5-year run's tree count
HORIZONS = ["1w"]  # the retracted model's 1W target


def run_config(predictor, wti_data, feature_mode: str, horizon: str) -> dict:
    dataset, feature_cols = prepare_daily_dataset(predictor, wti_data, horizon, feature_mode=feature_mode)
    if dataset.empty or len(dataset) <= MIN_TRAIN:
        return {"error": f"insufficient rows: {len(dataset)}"}
    result = evaluate_horizon(predictor, dataset, feature_cols, horizon, MIN_TRAIN, STEP)
    ens = result["metrics"]["ensemble"]
    pnl = result.get("pnl_metrics", {})
    return {
        "feature_mode": feature_mode,
        "horizon": horizon,
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
    print("=== Macro Leakage Test ===")
    print(f"Period: {PERIOD}, min_train: {MIN_TRAIN}, step: {STEP}, estimators: {ESTIMATORS}")
    print(f"Horizons: {HORIZONS}\n")

    predictor = PremiumWTIPredictor()
    predictor.model_n_estimators = ESTIMATORS
    predictor.model_cpu_workers = 1

    print("Fetching WTI data...")
    wti_data = predictor.get_wti_historical_data(period=PERIOD, interval="1d")
    print(f"Loaded {len(wti_data)} bars of WTI data\n")

    results = {}

    # ── Config 1: NO_MACRO (the retracted model's feature set) ───────────────
    print("Running NO_MACRO config (technical + cross-asset, no FRED/EIA)...")
    predictor.use_historical_external_features_in_training = False
    for h in HORIZONS:
        key = f"no_macro_{h}"
        results[key] = run_config(predictor, wti_data, "no_macro", h)
        r = results[key]
        print(f"  {h}: acc={r.get('direction_accuracy')}% p={r.get('direction_p_value')} "
              f"sharpe={r.get('sharpe')} n={r.get('samples')}")

    print()

    # ── Config 2: WITH_MACRO (FRED/EIA with publication lags) ───────────────
    print("Running WITH_MACRO config (adds FRED/EIA with publication lags)...")
    print("  (This fetches EIA weekly stocks and FRED monthly series — takes time)\n")
    predictor.use_historical_external_features_in_training = True
    for h in HORIZONS:
        key = f"with_macro_{h}"
        results[key] = run_config(predictor, wti_data, "all", h)
        r = results[key]
        print(f"  {h}: acc={r.get('direction_accuracy')}% p={r.get('direction_p_value')} "
              f"sharpe={r.get('sharpe')} n={r.get('samples')}")

    print()

    # ── Leakage verdict ──────────────────────────────────────────────────────
    print("=== VERDICT ===")
    for h in HORIZONS:
        nm = results.get(f"no_macro_{h}", {})
        wm = results.get(f"with_macro_{h}", {})
        nm_sharpe = nm.get("sharpe") or 0
        wm_sharpe = wm.get("sharpe") or 0
        diff = wm_sharpe - nm_sharpe
        verdict = (
            "LEAKAGE RISK: with_macro sharpe much higher — macro data may be revised look-ahead"
            if diff > 0.5
            else "No inflation: macro features raise the Sharpe by 0.5 or less (no revision-leakage evidence)"
            if diff <= 0.5
            else "INCONCLUSIVE"
        )
        print(f"{h}: no_macro={nm_sharpe:.3f}  with_macro={wm_sharpe:.3f}  delta={diff:+.3f}")
        print(f"   {verdict}")

    # Save results
    out_path = Path("data/macro_leakage_test.json")
    out_path.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": {"period": PERIOD, "min_train": MIN_TRAIN, "step": STEP, "estimators": ESTIMATORS},
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
