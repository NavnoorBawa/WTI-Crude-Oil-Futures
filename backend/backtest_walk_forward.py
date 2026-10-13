#!/usr/bin/env python3
"""
Walk-forward backtest for WTI forecasts with baseline comparison.

This script evaluates the existing daily (1d, 1w) model pipeline using an
expanding-window walk-forward procedure and compares against simple baselines.
"""

import argparse
import json
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import binom, binomtest

from .oil import PremiumWTIPredictor
from .safe_paths import data_json_path

# 1 WTI futures contract = 1000 barrels.
# $100 round-trip is conservative (spread ~$0.03-0.05/bbl + commission).
TRANSACTION_COST_USD = 100.0
CONTRACT_BARRELS = 1000

# Yahoo period for the cross-asset context series. The predictor's own market_context_period
# ('3y', sized for the live model's 2y window) counts back from today, so on a longer or older
# backtest window every earlier row silently got constant default context.
BACKTEST_CONTEXT_PERIOD = "max"


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

    # Max drawdown on the cumulative P&L curve, which starts at 0: losses before the first
    # peak are a drawdown too.
    cum = np.concatenate([[0.0], np.cumsum(arr)])
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
    n_correct = int(np.sum(pred_direction == actual_direction))
    dir_acc = float(n_correct / n * 100.0)

    # One-sided binomial p-value: H0 = direction accuracy = 50% (coin flip). The survival
    # function stays accurate in the far tail, where 1 - cdf rounds to exactly 0.
    p_value = float(binom.sf(n_correct - 1, n, 0.5)) if n > 0 else 1.0
    # Two-sided too: a model significantly WORSE than a coin flip is not "not significant".
    p_two_sided = float(binomtest(n_correct, n, 0.5).pvalue) if n > 0 else 1.0

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
        "direction_p_value_two_sided": round(p_two_sided, 4),
        "direction_ci_95": [ci_low, ci_high],
        "is_significant_5pct": p_value < 0.05,
    }


@contextmanager
def _overridden(obj, **attrs):
    """Set attributes on obj for the duration of a with-block, then restore the originals."""
    missing = object()
    saved = {name: vars(obj).get(name, missing) for name in attrs}
    for name, value in attrs.items():
        setattr(obj, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is missing:
                delattr(obj, name)
            else:
                setattr(obj, name, value)


def build_backtest_context_map(predictor, wti_data):
    """Cross-asset context map over the whole backtest window, and the date it is complete from.

    Every context series is fetched with BACKTEST_CONTEXT_PERIOD rather than the predictor's
    market_context_period. The second value is the first date on which every context symbol has
    printed; rows before it carry constant fill values. It is None when a symbol returned nothing.
    """
    fetch = predictor._fetch_market_series
    first_prints = []

    def fetch_full_history(symbol, period=None, interval="1d"):
        series = fetch(symbol, period=BACKTEST_CONTEXT_PERIOD, interval=interval)
        first_prints.append(series.first_valid_index() if series is not None else None)
        return series

    with _overridden(predictor, _fetch_market_series=fetch_full_history):
        context_map = predictor.build_market_context_feature_map(wti_data)
    if not first_prints or any(first is None for first in first_prints):
        return context_map, None
    return context_map, max(predictor._date_feature_key(first) for first in first_prints)


def prepare_daily_dataset(predictor, wti_data, horizon, feature_mode="all", lag_context_days=0):
    """Rebuild the per-horizon daily feature matrix used by the production predictor.

    feature_mode controls which feature families are included — used to test for
    look-ahead leakage from revision-prone macro data:
      - "all":        technical + market-context (Brent/DXY/VIX) + FRED/EIA macro (the
                      macro comparison run; needs EIA_API_KEY for the EIA stock features)
      - "no_macro":   technical + market-context, NO FRED/EIA. This is the validated
                      production configuration (with lag_context_days=1): macro is the only
                      family subject to post-hoc revisions, so dropping it isolates the risk
      - "price_only": technical features only (purest; market data is point-in-time but external)

    lag_context_days shifts ALL cross-asset context features back N trading days
    (timing-leakage test): the backtest enters at the WTI settlement (~14:30 ET) but
    equity/vol context closes print at 16:00 ET, so same-day context contains ~90 min
    of post-entry information. Lagging one full day is strictly conservative.

    The returned frame's attrs["rows_without_context"] counts rows whose context date
    predates full cross-asset coverage (0 when every row has real context).
    """
    include_market = feature_mode in ("all", "no_macro")
    include_macro = feature_mode == "all"

    market_context_map, context_from = build_backtest_context_map(predictor, wti_data) if include_market else ({}, None)
    historical_external_map = {}
    if include_macro:
        # The predictor's historical-macro flag defaults to off (production excludes macro), and
        # with it off this map is constant defaults: "all" would silently equal "no_macro".
        with _overridden(predictor, use_historical_external_features_in_training=True):
            historical_external_map = predictor.build_historical_external_feature_map(wti_data)
    historical_external_defaults = predictor._historical_external_feature_defaults() if include_macro else {}
    lookback_bars = max(30, int(predictor._get_daily_feature_lookback(horizon)))
    target_mode = getattr(predictor, "daily_target_mode", "price")
    horizon_steps = 1 if horizon == "1d" else 5

    rows = []
    rows_without_context = 0
    for i in range(lookback_bars - 1, len(wti_data) - horizon_steps):
        window_data = wti_data.iloc[i - lookback_bars + 1 : i + 1]
        row_features = predictor.engineer_technical_features(window_data)
        row_key = predictor._date_feature_key(window_data.index[-1])
        if include_market:
            context_key = (
                predictor._date_feature_key(wti_data.index[i - lag_context_days])
                if lag_context_days > 0 and i - lag_context_days >= 0
                else row_key
            )
            if context_from is None or context_key < context_from:
                rows_without_context += 1
            row_features.update(market_context_map.get(context_key, {}))
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

    dataset.attrs["rows_without_context"] = rows_without_context
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


def horizon_steps_for(horizon: str) -> int:
    """Trading days ahead the target looks (1 for 1d, 5 for 1w)."""
    return 1 if horizon == "1d" else 5


def purge_count(horizon: str) -> int:
    """Rows to drop from the END of each training window so NO training label matures after the
    prediction point — the fix for the headline look-ahead leak (López de Prado purge/embargo).

    A row's label is the Close `horizon_steps` bars ahead, so the last (horizon_steps - 1) rows
    have labels that only exist after the decision moment. 1w -> drop 4, 1d -> drop 0 (a 1-day
    target cannot overlap the next prediction). Exposed as a function so the invariant is testable.
    """
    return horizon_steps_for(horizon) - 1


# The report the dashboard serves (backend/server.py loads it only for the configuration below).
DASHBOARD_ARTIFACT = "walk_forward_backtest_latest.json"
# The live 1D/1W models train on the latest 378 daily bars (~18 months; oil.py daily_training_rows).
PRODUCTION_TRAIN_WINDOW = 378


def is_validated_configuration(config: dict) -> bool:
    """True for the configuration production runs and the purged backtest validated: no FRED/EIA
    macro, context lagged one trading day, the production 378-bar rolling window, the purge applied
    on every horizon, and a pinned --start/--end window (period None), so the report can be re-run
    exactly. Mirrors backend/server.py's check on DASHBOARD_ARTIFACT."""
    purge_rows = config.get("purge_rows_by_horizon") or {}
    return (
        config.get("feature_mode") == "no_macro"
        and config.get("context_lag_days") == 1
        and config.get("train_window") == PRODUCTION_TRAIN_WINDOW
        and "period" in config and config["period"] is None
        and bool(purge_rows)
        and all(rows == purge_count(horizon) for horizon, rows in purge_rows.items())
    )


def evaluate_horizon(predictor, dataset, feature_cols, horizon, min_train, step, train_window=0, purge=True):
    """Run walk-forward for one horizon and return model/baseline metrics.

    purge=False reproduces the retracted look-ahead leak (the before/after comparison only).
    """
    target_col = f"target_{horizon}"
    actual_price_col = f"actual_price_{horizon}"
    horizon_steps = horizon_steps_for(horizon)

    prediction_streams = {
        "ensemble": [],
        "naive_last_price": [],
        "drift": [],
        "price_5d_ago": [],
    }
    actuals = []
    references = []
    timestamps = []
    trade_pnls = []       # net P&L per trade after transaction costs
    trade_records = []    # timestamped per-trade series for the OOS equity curve
    feature_selection_counts: dict = {}   # how often each feature survives SelectKBest across steps
    n_train_steps = 0

    total_steps = len(range(min_train, len(dataset), step))
    _t0 = time.perf_counter()

    for loop_i, end_idx in enumerate(range(min_train, len(dataset), step)):
        if loop_i and loop_i % 50 == 0:
            elapsed = time.perf_counter() - _t0
            rate = elapsed / loop_i
            eta = rate * (total_steps - loop_i)
            print(f"  [{horizon}] step {loop_i}/{total_steps}  elapsed {elapsed:.0f}s  eta {eta:.0f}s",
                  file=sys.stderr, flush=True)
        start_idx = max(0, end_idx - train_window) if train_window > 0 else 0
        full_train_df = dataset.iloc[start_idx:end_idx]   # all prices observable at decision time

        # Purge/embargo (López de Prado): a row's target is the Close horizon_steps bars ahead, so
        # the last (horizon_steps - 1) training rows have labels that mature AFTER the prediction
        # point — using them would leak future information. Drop them from MODEL training. The
        # baselines/drift below intentionally still use full_train_df (past prices only, no target
        # overlap), so the embargo affects only the learned model, not the observed-price baselines.
        purge_rows = purge_count(horizon) if purge else 0
        train_df = dataset.iloc[start_idx:end_idx - purge_rows].copy() if purge_rows > 0 else full_train_df.copy()
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

        n_train_steps += 1
        for feat in package["selected_features"]:
            feature_selection_counts[feat] = feature_selection_counts.get(feat, 0) + 1

        row_features = {k: float(row[k]) for k in feature_cols}
        row_features["reference_close"] = float(row["reference_close"])
        ref_price = float(row["reference_close"])
        actual_price = float(row[actual_price_col])

        train_ref = full_train_df["reference_close"].astype(float)
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
            prediction_streams["price_5d_ago"].append(float(train_ref.iloc[-5]))
        else:
            prediction_streams["price_5d_ago"].append(ref_price)

        actuals.append(actual_price)
        references.append(ref_price)
        timestamps.append(str(row["timestamp"]))

        # Dollar P&L: trade in the direction the model predicts, 1 contract = 1000 bbls.
        # gross = predicted_direction × actual_move × 1000
        # net   = gross − transaction_cost
        pred_dir = float(np.sign(ensemble_pred - ref_price))
        if pred_dir != 0:
            gross = pred_dir * (actual_price - ref_price) * CONTRACT_BARRELS
            net = gross - TRANSACTION_COST_USD
            trade_pnls.append(net)
            trade_records.append({
                "t": str(row["timestamp"])[:10],
                "dir": int(pred_dir),
                "pnl": round(net, 2),
                # Forecast magnitude in % — the model has no abstain state (sign() is
                # never exactly 0), so every OOS sample becomes a trade. Storing the
                # conviction lets later analysis test a no-trade threshold without rerunning.
                "fc_pct": round((ensemble_pred / ref_price - 1.0) * 100.0, 3) if ref_price > 0 else 0.0,
            })

    metrics = {
        model_name: compute_metrics(actuals, preds, references)
        for model_name, preds in prediction_streams.items()
    }

    baseline_names = ["naive_last_price", "drift", "price_5d_ago"]
    best_baseline = min(baseline_names, key=lambda name: metrics[name]["mae"] if metrics[name]["samples"] > 0 else float("inf"))

    ensemble_mae = metrics["ensemble"]["mae"]
    baseline_mae = metrics[best_baseline]["mae"]
    if baseline_mae > 0:
        mae_improvement = ((baseline_mae - ensemble_mae) / baseline_mae) * 100.0
    else:
        mae_improvement = 0.0

    # Annualise P&L at the cadence the backtest actually trades: one trade every
    # `step` trading days = 252/step trades per year (50.4 at step=5). The previous
    # 52 (1w) / 252 (1d) assumption ignored the stride and overstated Sharpe —
    # by ~1.6% at step=5 and ~2x at step=20.
    periods_per_year = 252.0 / max(1, step)
    pnl_metrics = compute_pnl_metrics(trade_pnls, periods_per_year)

    # Year-by-year breakdown — the first thing a PM asks when they see the headline Sharpe.
    # Groups trades by calendar year and computes Sharpe + win rate per year so sub-period
    # concentration is visible rather than hidden inside the aggregate number.
    yearly_pnl: dict = {}
    for record in trade_records:
        year = str(record["t"])[:4]
        yearly_pnl.setdefault(year, []).append(record["pnl"])

    yearly_breakdown = {}
    for year, pnls in sorted(yearly_pnl.items()):
        arr = np.array(pnls, dtype=float)
        n_yr = len(arr)
        mean_yr = float(np.mean(arr))
        std_yr = float(np.std(arr, ddof=1)) if n_yr > 1 else 0.0
        sharpe_yr = round(float(mean_yr / std_yr * np.sqrt(periods_per_year)), 3) if std_yr > 0 else 0.0
        yearly_breakdown[year] = {
            "n_trades":        n_yr,
            "total_pnl_usd":   round(float(np.sum(arr)), 2),
            "sharpe":          sharpe_yr,
            "win_rate_pct":    round(float(np.mean(arr > 0) * 100), 1),
        }

    # Feature-selection stability: SelectKBest refits per walk-forward step, so the
    # surviving feature set can drift. Report the share of steps each feature survived —
    # a top set hovering near 1.0 means the signal basis is stable, not refit noise.
    feature_stability = {
        feat: round(count / n_train_steps, 3)
        for feat, count in sorted(feature_selection_counts.items(), key=lambda kv: -kv[1])[:30]
    } if n_train_steps > 0 else {}

    return {
        "horizon": horizon,
        "samples": int(metrics["ensemble"]["samples"]),
        "walk_forward_step": int(step),
        "metrics": metrics,
        "best_baseline": best_baseline,
        "mae_improvement_vs_best_baseline_pct": float(mae_improvement),
        "pnl_metrics": pnl_metrics,
        "yearly_breakdown": yearly_breakdown,
        "feature_selection_stability": feature_stability,
        "trades": trade_records,
        "first_prediction_timestamp": timestamps[0] if timestamps else None,
        "last_prediction_timestamp": timestamps[-1] if timestamps else None,
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-forward backtest for WTI daily horizons.")
    parser.add_argument("--period", default="5y", help="yfinance period for historical data (default: 5y → ~200+ OOS samples)")
    parser.add_argument("--min-train", type=int, default=200, help="minimum expanding-window train rows before first test")
    parser.add_argument("--step", type=int, default=5, help="walk-forward stride in rows (default: 5)")
    parser.add_argument("--estimators", type=int, default=40, help="tree estimators per model for backtest runtime")
    parser.add_argument("--features", default="no_macro", choices=["all", "no_macro", "price_only"],
                        help="feature families to include (default 'no_macro', the validated set; "
                             "'all' adds FRED/EIA macro, 'price_only' drops cross-asset context)")
    parser.add_argument("--lag-context", type=int, default=1, metavar="DAYS",
                        help="lag cross-asset context features by N trading days (default 1, strictly "
                             "entry-time-clean and the validated setting; 0 = the timing-leakage comparison)")
    parser.add_argument("--train-window", type=int, default=PRODUCTION_TRAIN_WINDOW, metavar="BARS",
                        help=f"rolling training window in bars (default {PRODUCTION_TRAIN_WINDOW}, the "
                             "production ~18 months; 0 = expanding)")
    parser.add_argument("--horizons", default="1d,1w",
                        help="comma-separated horizons to evaluate (default: 1d,1w; use 1w to skip the dead 1d)")
    parser.add_argument("--start", metavar="YYYY-MM-DD",
                        help="first data date; with --end, pins the window so a run is reproducible "
                             "(--period is relative to today)")
    parser.add_argument("--end", metavar="YYYY-MM-DD", help="last data date (inclusive)")
    parser.add_argument("--no-purge", action="store_true",
                        help="skip the purge to reproduce the retracted look-ahead leak (before/after "
                             "comparison only; the --output file name must contain 'LEAKED')")
    parser.add_argument("--output", default=f"data/{DASHBOARD_ARTIFACT}",
                        help="allowlisted data/ file to write the JSON report to (default: the dashboard's "
                             "artifact, which takes only the validated configuration)")
    args = parser.parse_args()

    horizons = [h.strip() for h in args.horizons.split(",") if h.strip()]
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end pin a window together; give both (or neither, for --period)")
    run_config = {
        # None = a pinned --start/--end window, re-runnable exactly; else the today-relative period.
        "period": None if args.start else args.period,
        "feature_mode": args.features,
        "context_lag_days": max(0, int(args.lag_context)),
        "train_window": int(args.train_window),
        "purge_rows_by_horizon": {horizon: 0 if args.no_purge else purge_count(horizon) for horizon in horizons},
    }
    # Check where the report goes before the (long) run, not after it.
    try:
        output_path = data_json_path(args.output)
    except ValueError as exc:
        parser.error(f"--output {args.output}: {exc}")
    # A leaked report must never land in an artifact the dashboard or README reads as the result,
    # and comparison runs (--features all/price_only, --lag-context 0, ...) need their own file.
    if args.no_purge and "LEAKED" not in output_path.name:
        parser.error("--no-purge reproduces the look-ahead leak; its --output file name must contain 'LEAKED'")
    if output_path.name == DASHBOARD_ARTIFACT and not is_validated_configuration(run_config):
        parser.error(f"{DASHBOARD_ARTIFACT} takes only the validated configuration (--start/--end, "
                     f"--features no_macro --lag-context 1 --train-window {PRODUCTION_TRAIN_WINDOW}, purged); "
                     "pass --output for any other run")

    predictor = PremiumWTIPredictor()
    predictor.model_n_estimators = max(20, int(args.estimators))
    predictor.model_cpu_workers = max(1, int(predictor.model_cpu_workers))

    pinned = bool(args.start or args.end)
    wti_data = predictor.get_wti_historical_data(period="max" if pinned else args.period, interval="1d")
    if pinned:
        dates = wti_data.index.tz_localize(None) if wti_data.index.tz is not None else wti_data.index
        keep = pd.Series(True, index=wti_data.index)
        if args.start:
            keep &= dates >= pd.Timestamp(args.start)
        if args.end:
            keep &= dates <= pd.Timestamp(args.end)
        wti_data = wti_data[keep.to_numpy()].copy()
        if wti_data.empty:
            raise RuntimeError("No price history inside --start/--end")

    # Drop non-positive prices (e.g. the 2020-04-20 WTI negative settlement, -$37.63). That
    # print is a May-contract expiry artifact, not a tradeable level for a weekly continuous
    # strategy, and a negative price produces finite-but-nonsensical pct_change features that
    # the inf/nan guards do not catch — poisoning ~60 days of rolling features. No-op on any
    # window without a negative print (the canonical 5y run drops 0 rows).
    nonpos = int((wti_data["Close"] <= 0).sum())
    if nonpos:
        dropped = list(wti_data.index[wti_data["Close"] <= 0].date)
        wti_data = wti_data[wti_data["Close"] > 0].copy()
        print(f"Dropped {nonpos} non-positive-price row(s) as roll/expiry artifacts: {dropped}")

    datasets = {}
    feature_cols_map = {}
    for horizon in horizons:
        dataset, feature_cols = prepare_daily_dataset(
            predictor, wti_data, horizon,
            feature_mode=args.features,
            lag_context_days=run_config["context_lag_days"],
        )
        if dataset.empty:
            raise RuntimeError(f"No backtest rows available for horizon={horizon}")
        if len(dataset) <= args.min_train:
            raise RuntimeError(
                f"Insufficient rows for walk-forward: horizon={horizon}, rows={len(dataset)}, min_train={args.min_train}"
            )
        if dataset.attrs.get("rows_without_context"):
            print(f"WARNING: {dataset.attrs['rows_without_context']} of {len(dataset)} {horizon} rows predate "
                  "full cross-asset context coverage and carry constant fill values", file=sys.stderr)
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
            train_window=int(args.train_window),
            purge=not args.no_purge,
        )

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": {
            # The exact data window, so any report can be re-run with --start/--end.
            "data_start": str(wti_data.index[0].date()),
            "data_end": str(wti_data.index[-1].date()),
            "min_train": int(args.min_train),
            "step": int(args.step),
            "estimators": int(args.estimators),
            **run_config,
            "context_period": BACKTEST_CONTEXT_PERIOD if args.features != "price_only" else None,
            "rows_without_context_by_horizon": {
                horizon: int(datasets[horizon].attrs.get("rows_without_context", 0)) for horizon in horizons
            },
            "rows_by_horizon": {horizon: int(len(datasets[horizon])) for horizon in horizons},
            "feature_count_by_horizon": {horizon: int(len(feature_cols_map[horizon])) for horizon in horizons},
        },
        "results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("Backtest report written successfully")
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
        yb = hr.get("yearly_breakdown", {})
        if yb:
            print("\n  Year-by-year (Sharpe note: partial years have wider error bars):")
            print(f"    {'Year':<6} {'N':>4} {'Total PnL':>12} {'Sharpe':>8} {'Win%':>6}")
            for year, yd in sorted(yb.items()):
                print(f"    {year:<6} {yd['n_trades']:>4} ${yd['total_pnl_usd']:>10,.0f} {yd['sharpe']:>8.2f} {yd['win_rate_pct']:>5.1f}%")


if __name__ == "__main__":
    main()
