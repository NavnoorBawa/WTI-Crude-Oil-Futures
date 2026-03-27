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

from oil import PremiumWTIPredictor


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

    return {
        "samples": int(len(y_true)),
        "mae": float(np.mean(abs_errors)),
        "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
        "mape": float(np.mean(abs_errors / safe_den) * 100.0),
        "direction_accuracy": float(np.mean(pred_direction == actual_direction) * 100.0),
    }


def prepare_daily_dataset(predictor, wti_data):
    """Rebuild the daily feature matrix used by the production predictor."""
    market_context_map = predictor.build_market_context_feature_map(wti_data)

    rows = []
    for i in range(20, len(wti_data) - 5):
        window_data = wti_data.iloc[i - 20 : i + 1]
        row_features = predictor.engineer_technical_features(window_data)
        row_key = predictor._date_feature_key(window_data.index[-1])
        row_features.update(market_context_map.get(row_key, {}))

        row_features["timestamp"] = str(wti_data.index[i])
        row_features["reference_close"] = float(wti_data["Close"].iloc[i])
        row_features["target_1d"] = float(wti_data["Close"].iloc[i + 1])
        row_features["target_1w"] = float(wti_data["Close"].iloc[i + 5])
        rows.append(row_features)

    if not rows:
        return pd.DataFrame(), []

    dataset = pd.DataFrame(rows)
    protected_cols = {"timestamp", "reference_close", "target_1d", "target_1w", "target_1h"}
    feature_cols = [c for c in dataset.columns if c not in protected_cols]

    for col in feature_cols:
        dataset[col] = pd.to_numeric(dataset[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

    return dataset, feature_cols


def build_ensemble_prediction(package, row_features):
    """Generate weighted-ensemble prediction from trained package and one row."""
    models = package["models"]
    scores = package["scores"]
    scaler = package["scaler"]
    selector = package["selector"]
    all_feature_names = package["all_feature_names"]

    row_df = pd.DataFrame([row_features])
    for feature in all_feature_names:
        if feature not in row_df.columns:
            row_df[feature] = 0.0

    transformed = selector.transform(row_df[all_feature_names])
    scaled = scaler.transform(transformed)

    model_preds = []
    model_weights = []
    for model_name, model in models.items():
        pred = float(model.predict(scaled)[0])
        if np.isnan(pred):
            continue
        model_preds.append(pred)
        model_weights.append(max(0.3, min(1.0, float(scores.get(model_name, 0.5)))))

    if not model_preds:
        return None

    return float(np.average(model_preds, weights=model_weights))


def evaluate_horizon(predictor, dataset, feature_cols, horizon, min_train, step):
    """Run walk-forward for one horizon and return model/baseline metrics."""
    target_col = f"target_{horizon}"
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

    for end_idx in range(min_train, len(dataset), step):
        train_df = dataset.iloc[:end_idx].copy()
        row = dataset.iloc[end_idx]

        train_input = train_df[feature_cols + [target_col]].copy()
        package_tuple = predictor.train_prediction_models(train_input, target_col)
        package = {
            "models": package_tuple[0],
            "scores": package_tuple[1],
            "scaler": package_tuple[2],
            "selector": package_tuple[3],
            "selected_features": package_tuple[4],
            "all_feature_names": package_tuple[5],
            "diagnostics": package_tuple[6],
        }

        if not package["models"]:
            continue

        row_features = {k: float(row[k]) for k in feature_cols}
        ensemble_pred = build_ensemble_prediction(package, row_features)
        if ensemble_pred is None:
            continue

        ref_price = float(row["reference_close"])
        actual_price = float(row[target_col])

        train_ref = train_df["reference_close"].astype(float)
        avg_step_change = float(train_ref.diff().dropna().mean()) if len(train_ref) > 1 else 0.0

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

    return {
        "horizon": horizon,
        "samples": int(metrics["ensemble"]["samples"]),
        "walk_forward_step": int(step),
        "metrics": metrics,
        "best_baseline": best_baseline,
        "mae_improvement_vs_best_baseline_pct": float(mae_improvement),
        "first_prediction_timestamp": timestamps[0] if timestamps else None,
        "last_prediction_timestamp": timestamps[-1] if timestamps else None,
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-forward backtest for WTI daily horizons.")
    parser.add_argument("--period", default="18mo", help="yfinance period for historical data (default: 18mo)")
    parser.add_argument("--min-train", type=int, default=140, help="minimum expanding-window train rows before first test")
    parser.add_argument("--step", type=int, default=5, help="walk-forward stride in rows (default: 5)")
    parser.add_argument("--estimators", type=int, default=40, help="tree estimators per model for backtest runtime")
    parser.add_argument("--output", default="data/walk_forward_backtest_latest.json", help="path to write JSON report")
    args = parser.parse_args()

    predictor = PremiumWTIPredictor()
    predictor.model_n_estimators = max(20, int(args.estimators))
    predictor.model_cpu_workers = max(1, int(predictor.model_cpu_workers))

    wti_data = predictor.get_wti_historical_data(period=args.period, interval="1d")
    dataset, feature_cols = prepare_daily_dataset(predictor, wti_data)

    if dataset.empty:
        raise RuntimeError("No backtest rows available from historical dataset")
    if len(dataset) <= args.min_train:
        raise RuntimeError(f"Insufficient rows for walk-forward: rows={len(dataset)}, min_train={args.min_train}")

    horizons = ["1d", "1w"]
    results = {}
    for horizon in horizons:
        results[horizon] = evaluate_horizon(
            predictor=predictor,
            dataset=dataset,
            feature_cols=feature_cols,
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
            "rows": int(len(dataset)),
            "feature_count": int(len(feature_cols)),
        },
        "results": results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Backtest report written to: {output_path}")
    for horizon in horizons:
        horizon_result = report["results"][horizon]
        ensemble_mae = horizon_result["metrics"]["ensemble"]["mae"]
        best_baseline = horizon_result["best_baseline"]
        baseline_mae = horizon_result["metrics"][best_baseline]["mae"]
        improvement = horizon_result["mae_improvement_vs_best_baseline_pct"]
        print(
            f"{horizon.upper()}: ensemble_mae={ensemble_mae:.4f}, "
            f"best_baseline={best_baseline} ({baseline_mae:.4f}), "
            f"mae_improvement={improvement:+.2f}%"
        )


if __name__ == "__main__":
    main()
