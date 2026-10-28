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
import re
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
# vol_forecast.live_record, as backend/vol_record.py summarize() writes it.
LIVE_RECORD_COUNTS = ("n_recorded", "n_resolved", "n_pending", "n_skipped_roll", "n_expired", "n_late_commit",
                      "n_independent")
LIVE_RECORD_STATUSES = ("pending", "resolved", "skipped_roll", "expired", "late_commit")
PROMOTION_STATUSES = ("accruing", "promote", "keep")
# The forecaster inputs a recent[] row repeats (vol_record._RECENT_INPUT_NUMBERS), in vol points.
RECENT_INPUT_NUMBERS = ("ovx", "rv5_pct", "rv22_pct", "rv66_pct")
# A recent[] row's stored fit: intercept plus slopes (vol_record.MAX_FIT_TERMS).
RECENT_MAX_FIT_TERMS = 8
# Resolved entries left out of the pinned pair's comparison, by reason (vol_record.promotion_exclusions).
PROMOTION_EXCLUSIONS = ("n_excluded_other_version", "n_excluded_other_challenger", "n_excluded_no_challenger")
PROMOTION_KEYS = ("status", "n_independent_paired", "required", "dm_stat", "p_value", "mean_qlike_difference",
                  "model_version", "challenger_key", *PROMOTION_EXCLUSIONS)
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
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


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
    errors.extend(_validate_vol_forecast(payload.get("vol_forecast"), session, notes))

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


def _validate_vol_forecast(vol: Any, session: date | None, notes: list[str]) -> list[str]:
    """The validated volatility forecast is the product: required, and every number it renders sane.

    A missing challenger forecast, or an economic block marked unavailable, is not an error (the
    dashboard renders without them), but it is noted: a session without a challenger cannot enter
    the promotion test. The optional reasons (live.challenger_error, live.ovx_error,
    economic.unavailable_reason) must be non-empty strings or null. They are not echoed into the
    notes, which CI prints as workflow commands.
    """
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

    if not _text(live.get("model_version")):
        errors.append("vol_forecast.live.model_version must be a non-empty string")

    for key in ("challenger_error", "ovx_error"):
        if live.get(key) is not None and not _text(live[key]):
            errors.append(f"vol_forecast.live.{key} must be a non-empty string or null")
    challenger_error = live.get("challenger_error")
    if live.get("challenger") is None:
        notes.append("vol_forecast.live has no challenger forecast, so this session cannot enter the promotion test; "
                     + ("vol_forecast.live.challenger_error gives the reason" if _text(challenger_error)
                        else "the payload gives no reason"))
    unavailable = _dict(vol.get("economic")).get("unavailable_reason")
    if unavailable is not None and not _text(unavailable):
        errors.append("vol_forecast.economic.unavailable_reason must be a non-empty string or null")
    elif unavailable is not None:
        notes.append("vol_forecast.economic is unavailable; vol_forecast.economic.unavailable_reason gives the reason")

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
    """The live record (backend/vol_record.py) is optional, but when present it must be well formed.

    next_result_due is required: the dashboard dates the next result from it, and null (nothing
    pending that will be scored) must be said explicitly rather than by a missing key."""
    if not isinstance(record, dict):
        return ["vol_forecast.live_record must be an object"]
    errors = [f"vol_forecast.live_record.{key} must be a non-negative integer"
              for key in LIVE_RECORD_COUNTS if not _count(record.get(key))]
    recent = record.get("recent")
    if not isinstance(recent, list) or not all(isinstance(row, dict) for row in recent):
        errors.append("vol_forecast.live_record.recent must be a list of objects")
    else:
        if any(row.get("status") not in LIVE_RECORD_STATUSES for row in recent):
            errors.append(f"vol_forecast.live_record.recent statuses must be one of {', '.join(LIVE_RECORD_STATUSES)}")
        if not all(_text(row.get("model_version")) for row in recent):
            errors.append("vol_forecast.live_record.recent model_version must be a non-empty string")
        errors.extend(_validate_recent_audit_fields(recent))
    if "next_result_due" not in record:
        errors.append("vol_forecast.live_record.next_result_due is required (an ISO date or null)")
    elif record["next_result_due"] is not None and not _strict_iso_date(record["next_result_due"]):
        errors.append("vol_forecast.live_record.next_result_due must be an ISO date (YYYY-MM-DD) or null")
    elif record["next_result_due"] is not None and record.get("n_pending") == 0:
        errors.append("vol_forecast.live_record.next_result_due must be null with no pending entry")
    promotion_errors = _validate_promotion(record.get("promotion"))
    errors.extend(promotion_errors)
    errors.extend(_validate_comparison(record, check_promotion=not promotion_errors))
    return errors


def _validate_recent_audit_fields(recent: list[dict]) -> list[str]:
    """The optional audit fields of recent[] rows (vol_record._recent), checked only when present.

    Summaries written before these fields existed omit them; a row may also carry null for any of
    them (an entry not sealed yet, or recorded before inputs or the fit were stored). When present,
    each must be what the dashboard reads: timestamps with a UTC offset, an ISO date, a percentage, an
    inputs object of finite vol points with an ISO-dated OVX session, the fit's coefficients (1 to
    RECENT_MAX_FIT_TERMS finite numbers) and positive smearing factor, and a boolean spans_roll.
    """
    where = "vol_forecast.live_record.recent"
    errors: list[str] = []
    for key in ("recorded_at", "sealed_at"):
        if any(row.get(key) is not None and _parse_timestamp(row[key]) is None for row in recent):
            errors.append(f"{where} {key} must be an ISO timestamp with a UTC offset, or null")
    if any(row.get("base_session") is not None and not _strict_iso_date(row["base_session"]) for row in recent):
        errors.append(f"{where} base_session must be an ISO date (YYYY-MM-DD), or null")
    if any(row.get("prob_vol_rises_pct") is not None and not _percentage(row["prob_vol_rises_pct"]) for row in recent):
        errors.append(f"{where} prob_vol_rises_pct must be a percentage, or null")
    if any(row.get("spans_roll") is not None and not isinstance(row["spans_roll"], bool) for row in recent):
        errors.append(f"{where} spans_roll must be true or false, or null")
    if any(row.get("coefficients") is not None
           and (not isinstance(row["coefficients"], list) or not 0 < len(row["coefficients"]) <= RECENT_MAX_FIT_TERMS
                or not all(_finite_number(value) for value in row["coefficients"]))
           for row in recent):
        errors.append(f"{where} coefficients must be a list of 1 to {RECENT_MAX_FIT_TERMS} finite numbers, or null")
    if any(row.get("smearing_factor") is not None and not _finite_number(row["smearing_factor"], positive=True)
           for row in recent):
        errors.append(f"{where} smearing_factor must be a positive number, or null")
    for row in recent:
        inputs = row.get("inputs")
        if inputs is None:
            continue
        if (not isinstance(inputs, dict)
                or any(inputs.get(key) is not None and not _finite_number(inputs[key])
                       for key in RECENT_INPUT_NUMBERS)
                or (inputs.get("ovx_session") is not None and not _strict_iso_date(inputs["ovx_session"]))):
            errors.append(f"{where} inputs must be an object of finite vol points ({', '.join(RECENT_INPUT_NUMBERS)}) "
                          "and an ISO ovx_session, each possibly null, or null")
            break
    return errors


def _validate_comparison(record: dict, *, check_promotion: bool) -> list[str]:
    """champion and challenger name the promotion test's pinned pair, and the resolved entries add up.

    champion.n counts the resolved entries of the pinned model version and challenger.n those of the
    pinned pair; the promotion block counts the rest by reason. With a malformed promotion block
    (reported by _validate_promotion) only the two blocks' own shape is checked.
    """
    where = "vol_forecast.live_record"
    champion, challenger = record.get("champion"), record.get("challenger")
    errors = []
    if not isinstance(champion, dict) or not _text(champion.get("model_version")) or not _count(champion.get("n")):
        errors.append(f"{where}.champion must be an object with a non-empty model_version and a non-negative "
                      "integer n")
    if not isinstance(challenger, dict) or not _text(challenger.get("key")) or not _count(challenger.get("n")):
        errors.append(f"{where}.challenger must be an object with a non-empty key and a non-negative integer n")
    if errors or not check_promotion:
        return errors
    promotion = record["promotion"]
    if (champion["model_version"], challenger["key"]) != (promotion["model_version"], promotion["challenger_key"]):
        errors.append(f"{where}: champion.model_version and challenger.key must be the promotion test's model_version "
                      "and challenger_key")
    n_resolved = record.get("n_resolved")
    if _count(n_resolved) and n_resolved != champion["n"] + promotion["n_excluded_other_version"]:
        errors.append(f"{where}: n_resolved must equal champion.n plus promotion.n_excluded_other_version")
    if champion["n"] != (challenger["n"] + promotion["n_excluded_other_challenger"]
                         + promotion["n_excluded_no_challenger"]):
        errors.append(f"{where}: champion.n must equal challenger.n plus promotion.n_excluded_other_challenger and "
                      "promotion.n_excluded_no_challenger")
    return errors


def _strict_iso_date(value: Any) -> bool:
    """YYYY-MM-DD only: date.fromisoformat alone also accepts forms such as 20261027 and 2026-W44-2."""
    return isinstance(value, str) and ISO_DATE.fullmatch(value) is not None and _parse_date(value) is not None


def _validate_promotion(block: Any) -> list[str]:
    """The promotion test's result: accruing with no statistics, or a verdict on exactly the required sample."""
    where = "vol_forecast.live_record.promotion"
    if not isinstance(block, dict):
        return [f"{where} must be an object"]
    missing = [key for key in PROMOTION_KEYS if key not in block]
    if missing:
        return [f"{where} is missing {', '.join(missing)}"]
    errors = []
    status, n, required = block["status"], block["n_independent_paired"], block["required"]
    if status not in PROMOTION_STATUSES:
        errors.append(f"{where}.status must be one of {', '.join(PROMOTION_STATUSES)}")
    if not _count(required) or required == 0:
        errors.append(f"{where}.required must be a positive integer")
    if not _count(n):
        errors.append(f"{where}.n_independent_paired must be a non-negative integer")
    for key in ("dm_stat", "mean_qlike_difference"):
        if block[key] is not None and not _finite_number(block[key]):
            errors.append(f"{where}.{key} must be a finite number or null")
    if block["p_value"] is not None and not (_finite_number(block["p_value"]) and 0.0 <= block["p_value"] <= 1.0):
        errors.append(f"{where}.p_value must be a probability or null")
    for key in ("model_version", "challenger_key"):
        if not _text(block[key]):
            errors.append(f"{where}.{key} must be a non-empty string")
    for key in PROMOTION_EXCLUSIONS:
        if not _count(block[key]):
            errors.append(f"{where}.{key} must be a non-negative integer")
    if errors:
        return errors
    stats = [block[key] for key in ("dm_stat", "p_value", "mean_qlike_difference")]
    if status == "accruing":
        if n >= required or any(value is not None for value in stats):
            errors.append(f"{where}: an accruing test has fewer than the required weeks and no statistics")
    elif (n != required or block["mean_qlike_difference"] is None
          or (block["dm_stat"] is None) != (block["p_value"] is None)):
        errors.append(f"{where}: a verdict needs exactly the required weeks, a mean difference, and the "
                      "statistic with its p-value")
    elif status == "promote" and (block["p_value"] is None or block["mean_qlike_difference"] <= 0):
        errors.append(f"{where}: a promote verdict needs a positive mean difference and a p-value")
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
