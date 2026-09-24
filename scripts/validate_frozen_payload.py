#!/usr/bin/env python3
"""Fail closed when a frozen dashboard payload cannot render safely."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.safe_paths import dashboard_payload_path


HORIZONS = ("1h", "1d", "1w")


def _finite_number(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and (not positive or float(value) > 0)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def validate_payload(
    payload: Any,
    *,
    now: datetime | None = None,
    max_age_minutes: int | None = None,
    max_market_age_days: float | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """Return invariant violations; an empty list means the UI may consume it.

    `warnings` (optional) collects non-fatal findings such as a missing optional card.
    """
    errors: list[str] = []
    notes = warnings if warnings is not None else []
    if not isinstance(payload, dict):
        return ["root must be a JSON object"]
    if payload.get("error"):
        errors.append("payload contains an error marker")

    price = payload.get("current_price")
    if not _finite_number(price, positive=True):
        errors.append("current_price must be a finite positive number")
        price = None
    for key in ("price_change", "price_change_percent"):
        # Unknown is published as null, never as a fabricated 0.0; a present value must be numeric.
        if payload.get(key) is not None and not _finite_number(payload.get(key)):
            errors.append(f"{key} must be a finite number or null")

    frozen_at = _parse_timestamp(payload.get("frozen_at"))
    if frozen_at is None:
        errors.append("frozen_at must be an aware ISO-8601 timestamp")
    elif max_age_minutes is not None:
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age_seconds = (reference - frozen_at).total_seconds()
        if age_seconds < -300:
            errors.append("frozen_at is implausibly in the future")
        elif age_seconds > max_age_minutes * 60:
            errors.append(f"frozen_at is older than {max_age_minutes} minutes")

    contract = payload.get("contract")
    if not isinstance(contract, dict) or not str(contract.get("symbol", "")).strip():
        errors.append("contract.symbol must be present")
    market_time = _parse_timestamp(_dict(contract).get("market_time"))
    if max_market_age_days is not None and frozen_at is not None:
        # The freeze stamps frozen_at seconds before this check runs, so its age proves nothing
        # about the data. The quote's own exchange timestamp does: a provider serving a stale
        # series must fail the deploy instead of publishing old prices as current.
        if market_time is None:
            notes.append("contract.market_time is missing; market-data freshness was not checked")
        elif (frozen_at - market_time).total_seconds() > max_market_age_days * 86400:
            errors.append(f"quote is more than {max_market_age_days:g} days older than the snapshot")

    multi = payload.get("multi_horizon_predictions")
    if not isinstance(multi, dict) or multi.get("is_real_prediction") is not True:
        errors.append("multi_horizon_predictions must be a real prediction object")
        multi = {}
    predictions = multi.get("predictions", {})
    percentages = multi.get("percentage_changes", {})
    intervals = multi.get("prediction_intervals", {})
    for horizon in HORIZONS:
        prediction = predictions.get(horizon) if isinstance(predictions, dict) else None
        if not _finite_number(prediction, positive=True):
            errors.append(f"predictions.{horizon} must be finite and positive")
        percentage = percentages.get(horizon) if isinstance(percentages, dict) else None
        if not _finite_number(percentage):
            errors.append(f"percentage_changes.{horizon} must be finite")
        interval = intervals.get(horizon) if isinstance(intervals, dict) else None
        if not isinstance(interval, dict):
            errors.append(f"prediction_intervals.{horizon} must be an object")
            continue
        lower, upper = interval.get("lower"), interval.get("upper")
        if not _finite_number(lower, positive=True) or not _finite_number(upper, positive=True):
            errors.append(f"prediction_intervals.{horizon} bounds must be finite and positive")
        elif lower > upper:
            errors.append(f"prediction_intervals.{horizon} lower exceeds upper")
        elif _finite_number(prediction, positive=True) and not lower <= prediction <= upper:
            errors.append(f"predictions.{horizon} lies outside its interval")
        if (price and _finite_number(prediction, positive=True) and _finite_number(percentage)
                and abs((prediction / price - 1.0) * 100.0 - percentage) > 0.11):
            errors.append(f"percentage_changes.{horizon} disagrees with its prediction")

    h1w = _dict(_dict(_dict(payload.get("performance_metrics")).get("by_horizon")).get("1w"))
    p_value, significant = h1w.get("wf_p_value"), h1w.get("wf_is_significant")
    if _finite_number(p_value) and significant is not None and bool(significant) != (p_value < 0.05):
        errors.append("wf_is_significant contradicts wf_p_value")

    actual = _dict(payload.get("unified_data")).get("actual")
    if not isinstance(actual, dict):
        errors.append("unified_data.actual must be an object")
    else:
        series = [actual.get(name) for name in ("timestamps", "values", "volumes")]
        if not all(isinstance(values, list) for values in series):
            errors.append("actual timestamps, values, and volumes must be arrays")
        elif len({len(values) for values in series}) != 1:
            errors.append("actual timestamps, values, and volumes must be aligned")
        elif not series[0]:
            errors.append("actual history must not be empty")
        elif any(_parse_timestamp(value) is None for value in series[0]):
            errors.append("actual history contains an invalid timestamp")
        elif any(not _finite_number(value, positive=True) for value in series[1]):
            errors.append("actual history contains a non-positive/non-finite price")
        elif price and abs(series[1][-1] / price - 1.0) > 0.05:
            errors.append("current_price disagrees with the latest chart price by more than 5%")

    errors.extend(_validate_vol_forecast(payload.get("vol_forecast"), notes))
    for optional in ("supply_shock_playbook", "live_record"):
        if not isinstance(payload.get(optional), dict):
            notes.append(f"{optional} is absent; the dashboard will omit it")
    return errors


def _validate_vol_forecast(vol: Any, notes: list[str]) -> list[str]:
    """The validated volatility card is optional (a vol-data hiccup must not block a deploy) but,
    when present, every number it renders must be sane."""
    if vol is None:
        notes.append("vol_forecast is absent; the dashboard will omit the validated-signal card")
        return []
    errors: list[str] = []
    live, validation = _dict(_dict(vol).get("live")), _dict(_dict(vol).get("validation"))
    for key in ("current_realized_vol_5d_annualized_pct", "forecast_next_week_vol_annualized_pct"):
        if not _finite_number(live.get(key), positive=True):
            errors.append(f"vol_forecast.live.{key} must be finite and positive")
    if live.get("direction") not in ("RISING", "FALLING"):
        errors.append("vol_forecast.live.direction must be RISING or FALLING")
    if not (isinstance(validation.get("n"), int) and validation["n"] > 0):
        errors.append("vol_forecast.validation.n must be a positive integer")
    for key in ("har_dir_acc_pct", "majority_class_pct", "mean_reversion_dir_acc_pct"):
        value = validation.get(key)
        if not (_finite_number(value) and 0.0 <= value <= 100.0):
            errors.append(f"vol_forecast.validation.{key} must be a percentage")
    p_value = validation.get("har_dir_p_value_vs_base_rate")
    if not (_finite_number(p_value) and 0.0 <= p_value <= 1.0):
        errors.append("vol_forecast.validation.har_dir_p_value_vs_base_rate must be a probability")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="public/data.json")
    parser.add_argument("--max-age-minutes", type=int)
    parser.add_argument("--max-market-age-days", type=float,
                        help="fail if the quote's market_time is older than this at freeze time")
    args = parser.parse_args()

    try:
        payload_path = dashboard_payload_path(args.path)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Frozen payload is unreadable error_type={type(exc).__name__}"
        ) from exc

    notes: list[str] = []
    errors = validate_payload(
        payload,
        max_age_minutes=args.max_age_minutes,
        max_market_age_days=args.max_market_age_days,
        warnings=notes,
    )
    for note in notes:
        print(f"::warning title=Dashboard payload::{note}")
    if errors:
        rendered = "\n".join(f"- {error}" for error in errors)
        raise SystemExit(f"Frozen payload validation failed:\n{rendered}")
    print("Frozen payload validation passed")


if __name__ == "__main__":
    main()
