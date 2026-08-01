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


def validate_payload(
    payload: Any,
    *,
    now: datetime | None = None,
    max_age_minutes: int | None = None,
) -> list[str]:
    """Return invariant violations; an empty list means the UI may consume it."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root must be a JSON object"]
    if payload.get("error"):
        errors.append("payload contains an error marker")

    if not _finite_number(payload.get("current_price"), positive=True):
        errors.append("current_price must be a finite positive number")

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

    actual = payload.get("unified_data", {}).get("actual", {})
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

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="public/data.json")
    parser.add_argument("--max-age-minutes", type=int)
    args = parser.parse_args()

    try:
        payload_path = dashboard_payload_path(args.path)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Frozen payload is unreadable error_type={type(exc).__name__}"
        ) from exc

    errors = validate_payload(payload, max_age_minutes=args.max_age_minutes)
    if errors:
        rendered = "\n".join(f"- {error}" for error in errors)
        raise SystemExit(f"Frozen payload validation failed:\n{rendered}")
    print("Frozen payload validation passed")


if __name__ == "__main__":
    main()
