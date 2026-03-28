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

from .oil import PremiumWTIPredictor


def apply_production_feature_defaults(row_df, feature_names):
    """Mirror the inference-time feature defaults used in the main predictor."""
    for feature in feature_names:
        if feature in row_df.columns:
            continue
        if "dollar_strength" in feature:
            row_df[feature] = 100.0
        elif "dollar_trend" in feature:
            row_df[feature] = 0.0
        elif "trend" in feature or "momentum" in feature or "divergence" in feature:
            row_df[feature] = 0.0
        else:
            row_df[feature] = 0.0
    return row_df


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


def prepare_daily_dataset(predictor, wti_data, horizon):
    """Rebuild the per-horizon daily feature matrix used by the production predictor."""
    market_context_map = predictor.build_market_context_feature_map(wti_data)
    lookback_bars = max(30, int(predictor._get_daily_feature_lookback(horizon)))
    target_mode = getattr(predictor, "daily_target_mode", "price")
    horizon_steps = 1 if horizon == "1d" else 5

    rows = []
    for i in range(lookback_bars - 1, len(wti_data) - horizon_steps):
        window_data = wti_data.iloc[i - lookback_bars + 1 : i + 1]
        row_features = predictor.engineer_technical_features(window_data)
        row_key = predictor._date_feature_key(window_data.index[-1])
        row_features.update(market_context_map.get(row_key, {}))
        reference_close = float(wti_data["Close"].iloc[i])
        target_price = float(wti_data["Close"].iloc[i + horizon_steps])

        row_features["timestamp"] = str(wti_data.index[i])
        row_features["reference_close"] = reference_close
        row_features[f"target_{horizon}"] = predictor._encode_target_value(reference_close, target_price, target_mode)
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
    row_df = apply_production_feature_defaults(row_df, all_feature_names)

    transformed = selector.transform(row_df[all_feature_names])
    scaled = scaler.transform(transformed)

    model_preds = []
    model_weights = []
    model_pred_map = {}
    target_mode = package.get("target_mode", diagnostics.get("target_mode", "price"))
    for model_name, model in models.items():
        raw_pred = float(model.predict(scaled)[0])
        if np.isnan(raw_pred):
            continue
        pred = predictor._decode_target_value(row_features.get("reference_close", 0.0), raw_pred, target_mode)
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
    horizons = ["1d", "1w"]
    datasets = {}
    feature_cols_map = {}
    for horizon in horizons:
        dataset, feature_cols = prepare_daily_dataset(predictor, wti_data, horizon)
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
