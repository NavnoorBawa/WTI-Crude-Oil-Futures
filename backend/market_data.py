"""Reproducible market-data snapshot for the volatility research (data/market/).

Every number in the research artifact (data/vol_lab.json) is computed from the CSV files in
data/market/, whose SHA-256 digests are pinned in data/market/MANIFEST.json. Loading verifies the
digests, so a reviewer who clones the repository reproduces the artifact bit for bit, offline,
without depending on what Yahoo Finance serves on the day. `python -m backend.vol_lab --refresh`
downloads a new snapshot (free sources only) and rewrites the manifest.

Sources (all free, no API key):
  - CL=F daily OHLC, Yahoo Finance (continuous front-month NYMEX WTI, unadjusted; the same series
    the production forecaster uses). Only finished sessions are kept: the in-progress bar and the
    bars Yahoo dates on exchange holidays are dropped, exactly as in vol_forecast._completed.
  - ^OVX daily close, Yahoo Finance (CBOE Crude Oil ETF Volatility Index; production source).
  - OVXCLS, FRED (the same index as published by CBOE through the St. Louis Fed), used only as an
    independent cross-check of the Yahoo OVX series.
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

try:
    from . import vol_forecast as vf
    from .contract_calendar import last_completed_bar_date
    from .safe_paths import DATA_ROOT
except ImportError:  # Direct invocation from backend/
    import vol_forecast as vf  # type: ignore[import-not-found, no-redef]
    from contract_calendar import last_completed_bar_date  # type: ignore[import-not-found, no-redef]
    from safe_paths import DATA_ROOT  # type: ignore[import-not-found, no-redef]

SNAPSHOT_DIR = DATA_ROOT / "market"
MANIFEST_NAME = "MANIFEST.json"
FILES = {
    "cl_daily": "cl_f_daily.csv",
    "ovx_yahoo": "ovx_yahoo_daily.csv",
    "ovx_fred": "ovxcls_fred.csv",
}
SOURCES = {
    "cl_daily": "Yahoo Finance CL=F daily OHLC (yfinance), finished NYMEX sessions only",
    "ovx_yahoo": "Yahoo Finance ^OVX daily close (yfinance), finished sessions only",
    "ovx_fred": "FRED OVXCLS (https://fred.stlouisfed.org/series/OVXCLS), keyless fredgraph CSV",
}
FRED_OVX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=OVXCLS"
FLOAT_FORMAT = "%.6f"          # fixed formatting: identical inputs give identical bytes and digests
OHLC = ["Open", "High", "Low", "Close"]


class SnapshotIntegrityError(RuntimeError):
    """A snapshot file is missing or does not match the digest pinned in the manifest."""


@dataclass(frozen=True)
class MarketSnapshot:
    cl: pd.DataFrame            # Open, High, Low, Close, Volume on a tz-naive date index
    ovx_yahoo: pd.Series
    ovx_fred: pd.Series
    manifest: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_ohlc(frame: pd.DataFrame, now: datetime | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    """Finished sessions with a positive close, and a consistent bar wherever the bar is usable.

    The sessions kept are exactly the production forecaster's (vol_forecast._completed: finished,
    business-day, positive close), so close-to-close returns match it bit for bit. A bar whose
    open, high or low is not positive (2020-04-21, the session after the negative settlement) keeps
    its close but has its range blanked: range estimators fall back to the squared close-to-close
    return there. Inconsistent bars (a handful in 2001-2002, where Yahoo's High/Low exclude the
    open or close) are repaired by widening the range to contain the open and close; the close
    itself is never changed. Returns the cleaned frame and a data-quality report.
    """
    close = vf._completed(frame["Close"], now)
    px = frame.copy()
    if getattr(px.index, "tz", None) is not None:
        px.index = cast("pd.DatetimeIndex", px.index).tz_localize(None)
    px.index = pd.DatetimeIndex(px.index).normalize()
    px = px[~px.index.duplicated(keep="last")].reindex(close.index)
    px["Close"] = close
    unusable = ~(px[["Open", "High", "Low"]] > 0).all(axis=1)
    px.loc[unusable, ["Open", "High", "Low"]] = np.nan
    usable = ~unusable
    high_fix = usable & (px["High"] < px[["Open", "Close"]].max(axis=1))
    low_fix = usable & (px["Low"] > px[["Open", "Close"]].min(axis=1))
    px.loc[usable, "High"] = px.loc[usable, ["High", "Open", "Close"]].max(axis=1)
    px.loc[usable, "Low"] = px.loc[usable, ["Low", "Open", "Close"]].min(axis=1)
    px["Volume"] = (pd.to_numeric(px["Volume"], errors="coerce") if "Volume" in px
                    else pd.Series(np.nan, index=px.index)).round().astype("Int64")
    report = {
        "sessions": int(len(px)),
        "range_blanked_nonpositive_bars": int(unusable.sum()),
        "repaired_high_below_open_or_close": int(high_fix.sum()),
        "repaired_low_above_open_or_close": int(low_fix.sum()),
    }
    return px[OHLC + ["Volume"]], report


def _completed_close(series: pd.Series, now: datetime | None = None) -> pd.Series:
    return vf._completed(series, now).rename("Close")


def _fetch_fred_ovx(timeout: float = 30.0) -> pd.Series:
    request = urllib.request.Request(FRED_OVX_URL, headers={"User-Agent": "wti-vol-research/1.0"})
    # Fixed HTTPS URL on the FRED host; no user input reaches it.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        text = response.read().decode("utf-8")
    raw = pd.read_csv(io.StringIO(text), index_col=0, parse_dates=True)
    series = pd.to_numeric(raw.iloc[:, 0], errors="coerce").dropna()
    series.index = pd.DatetimeIndex(series.index).normalize()
    return series[series > 0].rename("OVXCLS")


def download(now: datetime | None = None) -> tuple[MarketSnapshot, dict[str, int]]:
    """Fetch every source fresh. Returns the snapshot (unsaved) and the CL data-quality report."""
    import yfinance as yf  # deferred: keeps the module importable offline

    now = now or datetime.now(timezone.utc)
    cl_raw = yf.Ticker("CL=F").history(period="max", interval="1d", auto_adjust=False)
    if cl_raw is None or cl_raw.empty:
        raise RuntimeError("CL=F history unavailable")
    cl, quality = clean_ohlc(cl_raw, now)
    ovx_raw = yf.Ticker("^OVX").history(period="max", interval="1d", auto_adjust=False)
    if ovx_raw is None or ovx_raw.empty:
        raise RuntimeError("^OVX history unavailable")
    ovx_yahoo = _completed_close(ovx_raw["Close"], now)
    ovx_fred = _fetch_fred_ovx()
    ovx_fred = ovx_fred[ovx_fred.index <= pd.Timestamp(last_completed_bar_date(now))]
    return MarketSnapshot(cl, ovx_yahoo, ovx_fred, {}), quality


def _write_csv(frame: pd.DataFrame | pd.Series, path: Path) -> None:
    out = frame.to_frame() if isinstance(frame, pd.Series) else frame
    out.index = pd.DatetimeIndex(out.index).strftime("%Y-%m-%d")
    out.index.name = "Date"
    path.write_text(out.to_csv(float_format=FLOAT_FORMAT, lineterminator="\n"), encoding="utf-8")


def write_snapshot(snapshot: MarketSnapshot, quality: dict[str, int], now: datetime | None = None,
                   directory: Path = SNAPSHOT_DIR) -> dict[str, Any]:
    """Write the CSVs and a manifest pinning their SHA-256 digests. Returns the manifest."""
    now = now or datetime.now(timezone.utc)
    directory.mkdir(parents=True, exist_ok=True)
    frames: dict[str, pd.DataFrame | pd.Series] = {"cl_daily": snapshot.cl, "ovx_yahoo": snapshot.ovx_yahoo, "ovx_fred": snapshot.ovx_fred}
    files: dict[str, dict[str, Any]] = {}
    for key, frame in frames.items():
        path = directory / FILES[key]
        _write_csv(frame, path)
        index = pd.DatetimeIndex(frame.index)
        files[key] = {
            "file": FILES[key],
            "sha256": _sha256(path),
            "rows": int(len(frame)),
            "first": index.min().strftime("%Y-%m-%d"),
            "last": index.max().strftime("%Y-%m-%d"),
            "source": SOURCES[key],
        }
    manifest = {
        "retrieved_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sessions_through": last_completed_bar_date(now).isoformat(),
        "files": files,
        "cl_data_quality": quality,
    }
    (directory / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def load_snapshot(directory: Path = SNAPSHOT_DIR, verify: bool = True) -> MarketSnapshot:
    """Read the pinned snapshot, refusing any file whose digest differs from the manifest."""
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.exists():
        raise SnapshotIntegrityError(f"no snapshot manifest at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key, name in FILES.items():
        path = directory / name
        entry = manifest.get("files", {}).get(key)
        if entry is None or not path.exists():
            raise SnapshotIntegrityError(f"snapshot file {name} missing")
        if verify and _sha256(path) != entry["sha256"]:
            raise SnapshotIntegrityError(f"snapshot file {name} does not match its pinned SHA-256")
    cl = pd.read_csv(directory / FILES["cl_daily"], index_col="Date", parse_dates=True)
    ovx_yahoo = pd.read_csv(directory / FILES["ovx_yahoo"], index_col="Date", parse_dates=True)["Close"]
    ovx_fred = pd.read_csv(directory / FILES["ovx_fred"], index_col="Date", parse_dates=True)["OVXCLS"]
    return MarketSnapshot(cl, ovx_yahoo, ovx_fred, manifest)


def research_inputs(snapshot: MarketSnapshot,
                    ovx_source: str = "yahoo") -> tuple[np.ndarray, pd.DatetimeIndex, np.ndarray, pd.DataFrame]:
    """(returns, dates, ovx, ohlc) aligned exactly as vol_forecast._load_data aligns live data.

    Returns are close-to-close log returns of finished sessions; `ohlc` is the cleaned bar for the
    same dates (the day each return ends on); OVX is carried forward at most OVX_FFILL_LIMIT days.
    """
    close = snapshot.cl["Close"]
    logret = np.log(close).diff().dropna()
    source = {"yahoo": snapshot.ovx_yahoo, "fred": snapshot.ovx_fred}[ovx_source]
    ovx = source.reindex(logret.index).ffill(limit=vf.OVX_FFILL_LIMIT).to_numpy(dtype=float)
    ohlc = snapshot.cl.loc[logret.index, OHLC]
    return logret.to_numpy(dtype=float), pd.DatetimeIndex(logret.index), ovx, ohlc


def ovx_source_agreement(snapshot: MarketSnapshot) -> dict[str, Any]:
    """How closely the production OVX (Yahoo) matches the official series redistributed by FRED."""
    joined = pd.concat([snapshot.ovx_yahoo.rename("yahoo"), snapshot.ovx_fred.rename("fred")],
                       axis=1, join="inner").dropna()
    diff = (joined["yahoo"] - joined["fred"]).abs()
    worst = diff.sort_values(ascending=False).head(3)
    return {
        "common_days": int(len(joined)),
        "share_within_0_05_pts": round(float((diff <= 0.05).mean()), 4),
        "max_abs_diff_pts": round(float(diff.max()), 2),
        "correlation": round(float(cast(float, joined.corr().iloc[0, 1])), 6),
        "largest_differences": [
            {"date": day.strftime("%Y-%m-%d"), "yahoo": round(float(cast(float, joined.at[day, "yahoo"])), 2),
             "fred": round(float(cast(float, joined.at[day, "fred"])), 2)}
            for day in worst.index
        ],
    }
