#!/usr/bin/env python3
"""
Walk-forward backtest for WTI forecasts with baseline comparison.

This script evaluates the existing daily (1d, 1w) model pipeline using an
expanding-window walk-forward procedure and compares against simple baselines.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binom

from .oil import PremiumWTIPredictor

# 1 WTI futures contract = 1000 barrels.
# $100 round-trip is conservative (spread ~$0.03-0.05/bbl + commission).
TRANSACTION_COST_USD = 100.0
CONTRACT_BARRELS = 1000


def compute_pnl_metrics(pnls: list, periods_per_year: float) -> dict:
    """Dollar P&L statistics for a list of per-trade net P&L values.

    This is the number that actually matters: direction accuracy only tells you
    how often you're right. P&L tells you whether being right makes money after costs.
    """
    if not pnls:
        return {"n_trades": 0}
    arr = np.array(pnls, dtype=float)
    n = len(arr)
    mean_pnl = float(np.mean(arr))
    std_pnl  = float(np.std(arr, ddof=1)) if n > 1 else 0.0

    # Annualised Sharpe: mean / std * sqrt(periods per year)
    sharpe = float(mean_pnl / std_pnl * np.sqrt(periods_per_year)) if std_pnl > 0 else 0.0

    # Max drawdown on cumulative P&L curve
    cum = np.cumsum(arr)
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.max(peak - cum))

    # Profit factor: gross wins / abs(gross losses)
    wins   = arr[arr > 0]
    losses = arr[arr < 0]
    pf = float(np.sum(wins) / abs(np.sum(losses))) if losses.size > 0 and np.sum(losses) != 0 else None

    return {
        "n_trades":                   n,
        "total_pnl_usd":              round(float(np.sum(arr)), 2),
        "mean_pnl_per_trade_usd":     round(mean_pnl, 2),
        "std_pnl_per_trade_usd":      round(std_pnl, 2),
        "sharpe_ratio_annualized":    round(sharpe, 3),
        "win_rate_pct":               round(float(np.mean(arr > 0) * 100), 1),
        "max_drawdown_usd":           round(max_dd, 2),
        "profit_factor":              round(pf, 3) if pf is not None else None,
        "transaction_cost_per_trade": TRANSACTION_COST_USD,
    }


def compute_metrics(actuals, predictions, references):
    """Compute regression and directional metrics for one model stream."""
    if not actuals:
        return {
            "samples": 0,
            "mae": 0.0,
            "rmse": 0.0,
            "mape": 0.0,
            "direction_accuracy": 0.0,
        }

    y_true = np.asarray(actuals, dtype=float)
    y_pred = np.asarray(predictions, dtype=float)
    y_ref = np.asarray(references, dtype=float)

    abs_errors = np.abs(y_true - y_pred)
    safe_den = np.maximum(np.abs(y_true), 1e-6)

    pred_direction = np.sign(y_pred - y_ref)
    actual_direction = np.sign(y_true - y_ref)

    n = int(len(y_true))
    dir_acc = float(np.mean(pred_direction == actual_direction) * 100.0)
    n_correct = int(round(dir_acc / 100 * n))

    # One-sided binomial p-value: H0 = direction accuracy = 50% (coin flip)
    p_value = float(1.0 - binom.cdf(n_correct - 1, n, 0.5)) if n > 0 else 1.0

    # Wilson 95% confidence interval on direction accuracy
    z = 1.96
    p_hat = dir_acc / 100.0
    if n > 0:
        denom = 1 + z ** 2 / n
        center = (p_hat + z ** 2 / (2 * n)) / denom
        margin = z * np.sqrt(p_hat * (1 - p_hat) / n + z ** 2 / (4 * n ** 2)) / denom
        ci_low  = round(max(0.0, (center - margin) * 100), 1)
        ci_high = round(min(100.0, (center + margin) * 100), 1)
    else:
        ci_low, ci_high = 0.0, 100.0

    return {
        "samples": n,
        "mae": float(np.mean(abs_errors)),
        "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
        "mape": float(np.mean(abs_errors / safe_den) * 100.0),
        "direction_accuracy": dir_acc,
        "direction_p_value": round(p_value, 4),
        "direction_ci_95": [ci_low, ci_high],
        "is_significant_5pct": p_value < 0.05,
    }


def prepare_daily_dataset(predictor, wti_data, horizon, feature_mode="all"):
    """Rebuild the per-horizon daily feature matrix used by the production predictor.

    feature_mode controls which feature families are included — used to test for
    look-ahead leakage from revision-prone macro data:
      - "all":        technical + market-context (Brent/DXY/VIX) + FRED/EIA macro (production)
      - "no_macro":   technical + market-context, NO FRED/EIA  (leakage test — macro is the
                      only family subject to post-hoc revisions, so dropping it isolates the risk)
      - "price_only": technical features only (purest; market data is point-in-time but external)
    """
    include_market = feature_mode in ("all", "no_macro")
    include_macro = feature_mode == "all"

    market_context_map = predictor.build_market_context_feature_map(wti_data) if include_market else {}
    historical_external_map = predictor.build_historical_external_feature_map(wti_data) if include_macro else {}
    historical_external_defaults = predictor._historical_external_feature_defaults() if include_macro else {}
    lookback_bars = max(30, int(predictor._get_daily_feature_lookback(horizon)))
    target_mode = getattr(predictor, "daily_target_mode", "price")
    horizon_steps = 1 if horizon == "1d" else 5

    rows = []
    for i in range(lookback_bars - 1, len(wti_data) - horizon_steps):
        window_data = wti_data.iloc[i - lookback_bars + 1 : i + 1]
        row_features = predictor.engineer_technical_features(window_data)
        row_key = predictor._date_feature_key(window_data.index[-1])
        if include_market:
            row_features.update(market_context_map.get(row_key, {}))
        if include_macro:
            row_features.update(historical_external_map.get(row_key, historical_external_defaults))
        reference_close = float(wti_data["Close"].iloc[i])
        target_price = float(wti_data["Close"].iloc[i + horizon_steps])
        baseline_return = predictor._compute_target_baseline_return(window_data["Close"], horizon)

        row_features["timestamp"] = str(wti_data.index[i])
        row_features["reference_close"] = reference_close
        row_features[f"baseline_return_{horizon}"] = baseline_return
        row_features[f"target_{horizon}"] = predictor._encode_target_value(
            reference_close,
            target_price,
            target_mode,
            baseline_return=baseline_return,
        )
        row_features[f"actual_price_{horizon}"] = target_price
        rows.append(row_features)

    if not rows:
        return pd.DataFrame(), []

    dataset = pd.DataFrame(rows)
    protected_cols = {"timestamp", "reference_close", f"target_{horizon}", f"actual_price_{horizon}"}
    feature_cols = [c for c in dataset.columns if c not in protected_cols]

    for col in feature_cols:
        dataset[col] = pd.to_numeric(dataset[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

    return dataset, feature_cols


def build_ensemble_prediction(predictor, package, row_features, horizon, train_reference_closes):
    """Generate weighted-ensemble prediction from trained package and one row."""
    models = package["models"]
    scores = package["scores"]
    scaler = package["scaler"]
    selector = package["selector"]
    all_feature_names = package["all_feature_names"]
    diagnostics = package.get("diagnostics", {})

    row_df = pd.DataFrame([row_features])
    row_df = predictor._apply_feature_defaults(row_df, all_feature_names)

    transformed = selector.transform(row_df[all_feature_names])
    scaled = scaler.transform(transformed)

    model_preds = []
    model_weights = []
    model_pred_map = {}
    target_mode = package.get("target_mode", diagnostics.get("target_mode", "price"))
    baseline_return = row_features.get(f"baseline_return_{horizon}", 0.0)
    for model_name, model in models.items():
        raw_pred = float(model.predict(scaled)[0])
        if np.isnan(raw_pred):
            continue
        pred = predictor._decode_target_value(
            row_features.get("reference_close", 0.0),
            raw_pred,
            target_mode,
            baseline_return=baseline_return,
        )
        model_preds.append(pred)
        model_weights.append(max(0.3, min(1.0, float(scores.get(model_name, 0.5)))))
        model_pred_map[model_name] = pred

    if not model_preds:
        return None

    ensemble_pred = float(np.average(model_preds, weights=model_weights))
    reference_price = float(row_features.get("reference_close", 0.0) or 0.0)
    stabilized_pred, _ = predictor._stabilize_ensemble_prediction(
        reference_price,
        ensemble_pred,
        model_pred_map,
        scores,
        diagnostics.get("model_direction_scores", {}),
    )
    drift_challenger = predictor._compute_drift_challenger(train_reference_closes, reference_price, horizon)
    blended_pred, _ = predictor._blend_with_drift_challenger(
        reference_price,
        stabilized_pred,
        drift_challenger,
        diagnostics.get("latest_fold_backtest", {}),
        horizon=horizon,
    )
    return float(blended_pred)


def evaluate_horizon(predictor, dataset, feature_cols, horizon, min_train, step):
    """Run walk-forward for one horizon and return model/baseline metrics."""
    target_col = f"target_{horizon}"
    actual_price_col = f"actual_price_{horizon}"
    horizon_steps = 1 if horizon == "1d" else 5

    prediction_streams = {
        "ensemble": [],
        "naive_last_price": [],
        "drift": [],
        "seasonal_5d": [],
    }
    actuals = []
    references = []
    timestamps = []
    trade_pnls = []       # net P&L per trade after transaction costs

    for end_idx in range(min_train, len(dataset), step):
        train_df = dataset.iloc[:end_idx].copy()
        row = dataset.iloc[end_idx]

        train_input = train_df[feature_cols + [target_col]].copy()
        package_tuple = predictor.train_prediction_models(
            train_input,
            target_col,
            target_mode=getattr(predictor, "daily_target_mode", "price"),
        )
        package = {
            "models": package_tuple[0],
            "scores": package_tuple[1],
            "scaler": package_tuple[2],
            "selector": package_tuple[3],
            "selected_features": package_tuple[4],
            "all_feature_names": package_tuple[5],
            "diagnostics": package_tuple[6],
            "target_mode": getattr(predictor, "daily_target_mode", "price"),
        }

        if not package["models"]:
            continue

        row_features = {k: float(row[k]) for k in feature_cols}
        row_features["reference_close"] = float(row["reference_close"])
        ref_price = float(row["reference_close"])
        actual_price = float(row[actual_price_col])

        train_ref = train_df["reference_close"].astype(float)
        avg_step_change = float(train_ref.diff().dropna().mean()) if len(train_ref) > 1 else 0.0

        ensemble_pred = build_ensemble_prediction(
            predictor,
            package,
            row_features,
            horizon,
            train_ref.tolist(),
        )
        if ensemble_pred is None:
            continue

        prediction_streams["ensemble"].append(float(ensemble_pred))
        prediction_streams["naive_last_price"].append(ref_price)
        prediction_streams["drift"].append(ref_price + avg_step_change * horizon_steps)

        if len(train_ref) >= 5:
            prediction_streams["seasonal_5d"].append(float(train_ref.iloc[-5]))
        else:
            prediction_streams["seasonal_5d"].append(ref_price)

        actuals.append(actual_price)
        references.append(ref_price)
        timestamps.append(str(row["timestamp"]))

        # Dollar P&L: trade in the direction the model predicts, 1 contract = 1000 bbls.
        # gross = predicted_direction × actual_move × 1000
        # net   = gross − transaction_cost
        pred_dir = float(np.sign(ensemble_pred - ref_price))
        if pred_dir != 0:
            gross = pred_dir * (actual_price - ref_price) * CONTRACT_BARRELS
            trade_pnls.append(gross - TRANSACTION_COST_USD)

    metrics = {
        model_name: compute_metrics(actuals, preds, references)
        for model_name, preds in prediction_streams.items()
    }

    baseline_names = ["naive_last_price", "drift", "seasonal_5d"]
    best_baseline = min(baseline_names, key=lambda name: metrics[name]["mae"] if metrics[name]["samples"] > 0 else float("inf"))

    ensemble_mae = metrics["ensemble"]["mae"]
    baseline_mae = metrics[best_baseline]["mae"]
    if baseline_mae > 0:
        mae_improvement = ((baseline_mae - ensemble_mae) / baseline_mae) * 100.0
    else:
        mae_improvement = 0.0

    # Annualise P&L: 1W trades ~52 times/year, 1D trades ~252 times/year
    periods_per_year = 52.0 if horizon == "1w" else 252.0
    pnl_metrics = compute_pnl_metrics(trade_pnls, periods_per_year)

    return {
        "horizon": horizon,
        "samples": int(metrics["ensemble"]["samples"]),
        "walk_forward_step": int(step),
        "metrics": metrics,
        "best_baseline": best_baseline,
        "mae_improvement_vs_best_baseline_pct": float(mae_improvement),
        "pnl_metrics": pnl_metrics,
        "first_prediction_timestamp": timestamps[0] if timestamps else None,
        "last_prediction_timestamp": timestamps[-1] if timestamps else None,
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-forward backtest for WTI daily horizons.")
    parser.add_argument("--period", default="5y", help="yfinance period for historical data (default: 5y → ~200+ OOS samples)")
    parser.add_argument("--min-train", type=int, default=200, help="minimum expanding-window train rows before first test")
    parser.add_argument("--step", type=int, default=5, help="walk-forward stride in rows (default: 5)")
    parser.add_argument("--estimators", type=int, default=40, help="tree estimators per model for backtest runtime")
    parser.add_argument("--features", default="all", choices=["all", "no_macro", "price_only"],
                        help="feature families to include (leakage test: 'no_macro' drops FRED/EIA)")
    parser.add_argument("--output", default="data/walk_forward_backtest_latest.json", help="path to write JSON report")
    args = parser.parse_args()

    predictor = PremiumWTIPredictor()
    predictor.model_n_estimators = max(20, int(args.estimators))
    predictor.model_cpu_workers = max(1, int(predictor.model_cpu_workers))

    wti_data = predictor.get_wti_historical_data(period=args.period, interval="1d")
    horizons = ["1d", "1w"]
    datasets = {}
    feature_cols_map = {}
    for horizon in horizons:
        dataset, feature_cols = prepare_daily_dataset(predictor, wti_data, horizon, feature_mode=args.features)
        if dataset.empty:
            raise RuntimeError(f"No backtest rows available for horizon={horizon}")
        if len(dataset) <= args.min_train:
            raise RuntimeError(
                f"Insufficient rows for walk-forward: horizon={horizon}, rows={len(dataset)}, min_train={args.min_train}"
            )
        datasets[horizon] = dataset
        feature_cols_map[horizon] = feature_cols

    results = {}
    for horizon in horizons:
        results[horizon] = evaluate_horizon(
            predictor=predictor,
            dataset=datasets[horizon],
            feature_cols=feature_cols_map[horizon],
            horizon=horizon,
            min_train=int(args.min_train),
            step=int(args.step),
        )

    report = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "config": {
            "period": args.period,
            "min_train": int(args.min_train),
            "step": int(args.step),
            "estimators": int(args.estimators),
            "feature_mode": args.features,
            "rows_by_horizon": {horizon: int(len(datasets[horizon])) for horizon in horizons},
            "feature_count_by_horizon": {horizon: int(len(feature_cols_map[horizon])) for horizon in horizons},
        },
        "results": results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Backtest report written to: {output_path}")
    for horizon in horizons:
        hr  = report["results"][horizon]
        ens = hr["metrics"]["ensemble"]
        pnl = hr.get("pnl_metrics", {})
        bb  = hr["best_baseline"]
        sig = "SIG ✓" if ens.get("is_significant_5pct") else "NOT SIG"
        ci  = ens.get("direction_ci_95", ["-", "-"])
        print(
            f"\n{horizon.upper()}:"
            f"\n  Direction: {ens['direction_accuracy']:.1f}% [{ci[0]}-{ci[1]}%] "
            f"p={ens.get('direction_p_value','?')} ({sig}), n={ens['samples']}"
            f"\n  MAE: {ens['mae']:.4f} vs {bb} ({hr['metrics'][bb]['mae']:.4f}) "
            f"improvement={hr['mae_improvement_vs_best_baseline_pct']:+.2f}%"
        )
        if pnl.get("n_trades", 0) > 0:
            print(
                f"\n  P&L (1 contract, ${pnl['transaction_cost_per_trade']:.0f} cost/trade):"
                f"\n    Sharpe (ann.):      {pnl['sharpe_ratio_annualized']:.3f}"
                f"\n    E[PnL] per trade:   ${pnl['mean_pnl_per_trade_usd']:,.2f}"
                f"\n    Win rate:           {pnl['win_rate_pct']:.1f}%"
                f"\n    Total PnL:          ${pnl['total_pnl_usd']:,.2f}"
                f"\n    Max drawdown:       ${pnl['max_drawdown_usd']:,.2f}"
                f"\n    Profit factor:      {pnl['profit_factor']}"
            )


if __name__ == "__main__":
    main()
