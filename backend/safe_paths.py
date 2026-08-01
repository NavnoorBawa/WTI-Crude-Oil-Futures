"""Constrain command-line file arguments to trusted repository directories."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(
    os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
)
DATA_ROOT = PROJECT_ROOT / "data"
PUBLIC_ROOT = PROJECT_ROOT / "public"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"


def _normalized_argument(value: str | os.PathLike[str]) -> str:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("path must be a non-empty text value")
    return raw.replace("\\", "/")


def _trusted_file(root: Path, filename: str) -> Path:
    """Resolve an allowlisted file after normalization and boundary checks."""
    project_root = os.path.realpath(os.fspath(PROJECT_ROOT))
    trusted_root = os.path.realpath(os.fspath(root))
    if not trusted_root.startswith(project_root + os.sep):
        raise ValueError("trusted directory resolves outside the repository")

    resolved = os.path.realpath(os.path.join(trusted_root, filename))
    if not resolved.startswith(trusted_root + os.sep):
        raise ValueError("trusted repository file resolves outside its directory")
    if os.path.dirname(resolved) != trusted_root:
        raise ValueError("trusted repository file resolves outside its directory")
    return Path(resolved)


def data_json_path(value: str | os.PathLike[str]) -> Path:
    normalized = _normalized_argument(value)
    if normalized in {
        "signal_state.json",
        "data/signal_state.json",
        "./data/signal_state.json",
    }:
        return _trusted_file(DATA_ROOT, "signal_state.json")
    if normalized in {
        "live_track_record.json",
        "data/live_track_record.json",
        "./data/live_track_record.json",
    }:
        return _trusted_file(DATA_ROOT, "live_track_record.json")
    if normalized in {
        "walk_forward_backtest_latest.json",
        "data/walk_forward_backtest_latest.json",
        "./data/walk_forward_backtest_latest.json",
    }:
        return _trusted_file(DATA_ROOT, "walk_forward_backtest_latest.json")
    if normalized in {
        "wf_10y_rolling_purged.json",
        "data/wf_10y_rolling_purged.json",
        "./data/wf_10y_rolling_purged.json",
    }:
        return _trusted_file(DATA_ROOT, "wf_10y_rolling_purged.json")
    if normalized in {
        "timing_leakage_test.json",
        "data/timing_leakage_test.json",
        "./data/timing_leakage_test.json",
    }:
        return _trusted_file(DATA_ROOT, "timing_leakage_test.json")
    if normalized in {
        "serial_correlation_check.json",
        "data/serial_correlation_check.json",
        "./data/serial_correlation_check.json",
    }:
        return _trusted_file(DATA_ROOT, "serial_correlation_check.json")
    if normalized in {
        "vol_forecast_validation.json",
        "data/vol_forecast_validation.json",
        "./data/vol_forecast_validation.json",
    }:
        return _trusted_file(DATA_ROOT, "vol_forecast_validation.json")
    raise ValueError("data path is not an allowlisted repository artifact")


def public_json_path(value: str | os.PathLike[str]) -> Path:
    normalized = _normalized_argument(value)
    if normalized in {"data.json", "public/data.json", "./public/data.json"}:
        return _trusted_file(PUBLIC_ROOT, "data.json")
    raise ValueError("public path must be public/data.json")


def fixture_json_path(value: str | os.PathLike[str]) -> Path:
    normalized = _normalized_argument(value)
    if normalized in {
        "valid_frozen_payload.json",
        "tests/fixtures/valid_frozen_payload.json",
        "./tests/fixtures/valid_frozen_payload.json",
    }:
        return _trusted_file(FIXTURE_ROOT, "valid_frozen_payload.json")
    raise ValueError("fixture path must name the tracked frozen-payload fixture")


def dashboard_payload_path(value: str | os.PathLike[str]) -> Path:
    """Resolve one of the two payload locations used by deploy/test validation."""
    normalized = _normalized_argument(value)
    if normalized in {"data.json", "public/data.json", "./public/data.json"}:
        return public_json_path("data.json")
    if normalized in {
        "valid_frozen_payload.json",
        "tests/fixtures/valid_frozen_payload.json",
        "./tests/fixtures/valid_frozen_payload.json",
    }:
        return fixture_json_path("valid_frozen_payload.json")
    raise ValueError(
        "payload path must be public/data.json or the tracked test fixture"
    )


def public_output_directory(value: str | os.PathLike[str]) -> Path:
    """The production freezer writes only to the repository's public directory."""
    normalized = _normalized_argument(value).rstrip("/")
    if normalized not in {"public", "./public"}:
        raise ValueError("freeze output directory must be public")

    project_root = os.path.realpath(os.fspath(PROJECT_ROOT))
    public_root = os.path.realpath(os.fspath(PUBLIC_ROOT))
    if not public_root.startswith(project_root + os.sep):
        raise ValueError("public directory resolves outside the repository")
    return Path(public_root)
