#!/usr/bin/env python3
"""Fail closed when a frozen dashboard payload cannot render safely (docs/DATA_CONTRACT.md).

data.json (schema_version 3) must carry a sane quote and chart and the validated volatility forecast,
built from data no more than VOL_MAX_LAG_BUSINESS_DAYS sessions older than the quote. --research also
checks research.json with scripts/build_research_bundle.py's own validate(). Standard library only: it
runs in the deploy job that holds the write token.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Both modules are standard library only (needs the path above).
from backend.contract_calendar import business_days_between, trading_date  # noqa: E402
from backend.safe_paths import dashboard_payload_path, research_payload_path  # noqa: E402


SCHEMA_VERSION = 3
POINT_KINDS = {"1d", "1h", "live"}   # unified_data.actual.kinds (backend/oil.py get_historical_data)
VOL_DIRECTIONS = ("RISING", "FALLING")
# The forecast's as_of is the last completed session; one session behind the quote is normal, four
# covers a holiday weekend plus a late Yahoo bar. Older means the forecast is stale.
VOL_MAX_LAG_BUSINESS_DAYS = 4
LIVE_RECORD_COUNTS = ("n_recorded", "n_resolved", "n_pending", "n_skipped_roll", "n_independent")
# Outputs of the retracted 1-week direction model (schema_version 2 and earlier). It is not run in
# production, so any of them in a snapshot means a stale renderer and must never be published.
RETRACTED_MODEL_FIELDS = ("multi_horizon_predictions", "performance_metrics", "ml_prediction",
                          "ml_caveat", "geopolitical_risk", "enterprise_metrics")
RESEARCH_BUILDER = PROJECT_ROOT / "scripts" / "build_research_bundle.py"


def _finite_number(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and (not positive or float(value) > 0)


def _percentage(value: Any) -> bool:
    return _finite_number(value) and 0.0 <= float(value) <= 100.0


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def validate_payload(
    payload: Any,
    *,
    now: datetime | None = None,
    max_age_minutes: int | None = None,
    max_market_age_days: float | None = None,
    not_older_than: str | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """Return invariant violations; an empty list means the UI may consume it.

    `not_older_than` (optional) is the frozen_at of the snapshot already published: deploying an
    older payload would roll the site back. `warnings` (optional) collects non-fatal findings such
    as a missing optional card.
    """
    errors: list[str] = []
    notes = warnings if warnings is not None else []
    if not isinstance(payload, dict):
        return ["root must be a JSON object"]
    if payload.get("error"):
        errors.append("payload contains an error marker")
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

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
    if not_older_than is not None and frozen_at is not None:
        published_at = _parse_timestamp(not_older_than)
        if published_at is None:
            errors.append("the published snapshot's frozen_at is not an aware ISO-8601 timestamp")
        elif frozen_at < published_at:
            errors.append("frozen_at is older than the snapshot already published")

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
        elif "kinds" in actual and (not isinstance(actual["kinds"], list)
                                    or len(actual["kinds"]) != len(series[0])
                                    or any(kind not in POINT_KINDS for kind in actual["kinds"])):
            errors.append(f"actual kinds must be aligned with the history and one of {sorted(POINT_KINDS)}")
        elif price:
            errors.extend(_check_chart_against_quote(series[0], series[1], price, market_time, notes,
                                                     kinds=actual.get("kinds")))

    session = _quote_session(market_time, frozen_at, notes)
    errors.extend(_validate_vol_forecast(payload.get("vol_forecast"), session))

    retracted = _dict(payload.get("retracted_direction_model"))
    if retracted.get("status") != "RETRACTED":
        errors.append("retracted_direction_model.status must be RETRACTED")
    published = [key for key in RETRACTED_MODEL_FIELDS if key in payload]
    if "predicted" in _dict(payload.get("unified_data")):
        published.append("unified_data.predicted")
    if published:
        errors.append(f"retracted direction-model output must not be published: {', '.join(published)}")
    if not isinstance(payload.get("supply_shock_playbook"), dict):
        notes.append("supply_shock_playbook is absent; the dashboard will omit it")
    return errors


def _check_chart_against_quote(timestamps: list, values: list, price: float,
                               market_time: datetime | None, notes: list[str],
                               kinds: list | None = None) -> list[str]:
    """The newest exchange bar on the chart must agree with the quote (catches a chart series
    from another contract, or a stale one).

    The chart's newest point is not an exchange bar: the server appends the quote itself as a live
    point (oil.py get_historical_data), so the last point is the quote. Payloads tag each point
    ('1d', '1h', 'live'), so live points are skipped by kind; untagged payloads rely on bars being
    stamped at or before the quote's exchange time (contract.market_time).
    """
    if market_time is None:
        notes.append("contract.market_time is missing; the chart was not checked against the quote")
        return []
    kinds = kinds if isinstance(kinds, list) else [None] * len(timestamps)
    bars = [(stamp, value) for stamp, value, kind in zip(map(_parse_timestamp, timestamps), values, kinds)
            if stamp <= market_time and kind != "live"]
    if not bars:
        return ["the chart has no market bar at or before the quote time"]
    if abs(max(bars)[1] / price - 1.0) > 0.05:
        return ["current_price disagrees with the latest market bar by more than 5%"]
    return []


def _quote_session(market_time: datetime | None, frozen_at: datetime | None, notes: list[str]) -> date | None:
    """The CME session the quote belongs to (18:00 ET opens the next one), the vol freshness anchor."""
    if market_time is not None:
        return trading_date(market_time)
    if frozen_at is not None:
        notes.append("contract.market_time is missing; vol_forecast freshness was checked against frozen_at")
        return trading_date(frozen_at)
    return None


def _validate_vol_forecast(vol: Any, session: date | None) -> list[str]:
    """The validated volatility forecast is the product: required, and every number it renders sane."""
    if not isinstance(vol, dict):
        return ["vol_forecast is required"]
    errors: list[str] = []
    live, validation = _dict(vol.get("live")), _dict(vol.get("validation"))
    for key in ("forecast_next_week_vol_annualized_pct", "forecast_median_pct",
                "current_realized_vol_5d_annualized_pct"):
        if not _finite_number(live.get(key), positive=True):
            errors.append(f"vol_forecast.live.{key} must be finite and positive")
    if live.get("direction") not in VOL_DIRECTIONS:
        errors.append("vol_forecast.live.direction must be RISING or FALLING")
    if not _percentage(live.get("prob_vol_rises_pct")):
        errors.append("vol_forecast.live.prob_vol_rises_pct must be a percentage")

    interval = live.get("interval_80")
    if interval is not None:
        low, high = _dict(interval).get("low_pct"), _dict(interval).get("high_pct")
        median = live.get("forecast_median_pct")
        if not _finite_number(low, positive=True) or not _finite_number(high, positive=True):
            errors.append("vol_forecast.live.interval_80 bounds must be finite and positive")
        elif _finite_number(median) and not low <= median <= high:
            errors.append("vol_forecast.live.interval_80 must contain the median forecast")

    model_version = live.get("model_version")
    if not isinstance(model_version, str) or not model_version.strip():
        errors.append("vol_forecast.live.model_version must be a non-empty string")

    as_of = _parse_date(live.get("as_of"))
    if as_of is None:
        errors.append("vol_forecast.live.as_of must be an ISO date")
    elif session is not None:
        lag = business_days_between(as_of, session)
        if lag < 0:
            errors.append("vol_forecast.live.as_of is after the quote's session")
        elif lag > VOL_MAX_LAG_BUSINESS_DAYS:
            errors.append(f"vol_forecast.live.as_of is more than {VOL_MAX_LAG_BUSINESS_DAYS} "
                          "business days before the quote's session")

    n = validation.get("n")
    if not (isinstance(n, int) and not isinstance(n, bool) and n > 0):
        errors.append("vol_forecast.validation.n must be a positive integer")
    if not _finite_number(validation.get("har_qlike")):
        errors.append("vol_forecast.validation.har_qlike must be finite")

    if "live_record" in vol:
        errors.extend(_validate_live_record(vol["live_record"]))
    return errors


def _validate_live_record(record: Any) -> list[str]:
    """The live record (backend/vol_record.py) is optional, but when present it must be well formed."""
    if not isinstance(record, dict):
        return ["vol_forecast.live_record must be an object"]
    errors = [f"vol_forecast.live_record.{key} must be a non-negative integer"
              for key in LIVE_RECORD_COUNTS if not _count(record.get(key))]
    recent = record.get("recent")
    if not isinstance(recent, list) or not all(isinstance(row, dict) for row in recent):
        errors.append("vol_forecast.live_record.recent must be a list of objects")
    return errors


def _research_builder():
    """scripts/build_research_bundle.py, loaded by path (standard library only)."""
    spec = importlib.util.spec_from_file_location("build_research_bundle", RESEARCH_BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_research(bundle: Any) -> list[str]:
    """research.json violations, from build_research_bundle.validate (the builder's own checks)."""
    if not isinstance(bundle, dict):
        return ["research.json: root must be a JSON object"]
    try:
        problems = _research_builder().validate(bundle)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return [f"research.json: malformed ({type(exc).__name__})"]
    return [f"research.json: {problem}" for problem in problems]


def _read_json(resolve, value: str, label: str) -> Any:
    try:
        return json.loads(resolve(value).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:   # json.JSONDecodeError is a ValueError
        raise SystemExit(f"{label} is unreadable error_type={type(exc).__name__}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default="public/data.json")
    parser.add_argument("--max-age-minutes", type=int)
    parser.add_argument("--max-market-age-days", type=float,
                        help="fail if the quote's market_time is older than this at freeze time")
    parser.add_argument("--not-older-than", metavar="FROZEN_AT",
                        help="fail if frozen_at is earlier than this (the published snapshot's)")
    parser.add_argument("--research", metavar="PATH",
                        help="also validate research.json (public/research.json)")
    args = parser.parse_args()

    payload = _read_json(dashboard_payload_path, args.path, "Frozen payload")
    notes: list[str] = []
    errors = validate_payload(
        payload,
        max_age_minutes=args.max_age_minutes,
        max_market_age_days=args.max_market_age_days,
        not_older_than=args.not_older_than,
        warnings=notes,
    )
    if args.research is not None:
        errors.extend(validate_research(_read_json(research_payload_path, args.research, "research.json")))
    for note in notes:
        print(f"::warning title=Dashboard payload::{note}")
    if errors:
        rendered = "\n".join(f"- {error}" for error in errors)
        raise SystemExit(f"Frozen payload validation failed:\n{rendered}")
    print("Frozen payload validation passed" + (" (data.json and research.json)" if args.research else ""))


if __name__ == "__main__":
    main()
