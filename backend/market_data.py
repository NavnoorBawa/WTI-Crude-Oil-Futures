"""Reproducible market-data snapshot for the volatility research (data/market/).

Every number in the research artifact (data/vol_lab.json) is computed from the CSV files in
data/market/, whose SHA-256 digests are pinned in data/market/MANIFEST.json. Loading verifies the
digests, so a reviewer who clones the repository reproduces the artifact offline (bit for bit under
the same numeric environment; see vol_lab.compare_reports otherwise), without depending on what
Yahoo Finance serves on the day. `python -m backend.vol_lab --refresh`
downloads a new snapshot (free sources only) and rewrites the manifest.

Sources (all free, no API key):
  - CL=F daily OHLC, Yahoo Finance (continuous front-month NYMEX WTI, unadjusted; the same series
    the production forecaster uses). Only finished sessions are kept: the in-progress bar and the
    bars Yahoo dates on exchange holidays are dropped, exactly as in vol_forecast._completed.
  - ^OVX daily close, Yahoo Finance (CBOE Crude Oil ETF Volatility Index; production source).
  - OVXCLS, FRED (the same index as published by CBOE through the St. Louis Fed), used only as an
    independent cross-check of the Yahoo OVX series.
  - EIA's NYMEX WTI futures settlements, contract 1 and 2 (RCLC1, RCLC2; final, EIA stopped them
    after 2024-04-05), and the Cushing spot price (RWTC), copied from the committed
    data/eia_wti_futures_curve.json and data/eia_wti_spot_daily.json, used only as a second source
    for the CL=F closes and its contract rolls (cl_source_agreement, vol_lab.roll_convention_audit).
    No model reads them. `python -m backend.market_data --add-eia` adds them to an existing snapshot
    without touching its other files or digests.
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
    from .contract_calendar import is_business_day, last_completed_bar_date
    from .safe_paths import DATA_ROOT
except ImportError:  # Direct invocation from backend/
    import vol_forecast as vf  # type: ignore[import-not-found, no-redef]
    from contract_calendar import is_business_day, last_completed_bar_date  # type: ignore[import-not-found, no-redef]
    from safe_paths import DATA_ROOT  # type: ignore[import-not-found, no-redef]

SNAPSHOT_DIR = DATA_ROOT / "market"
MANIFEST_NAME = "MANIFEST.json"
FILES = {
    "cl_daily": "cl_f_daily.csv",
    "ovx_yahoo": "ovx_yahoo_daily.csv",
    "ovx_fred": "ovxcls_fred.csv",
    "eia_futures": "eia_rclc_daily.csv",
    "eia_spot": "eia_rwtc_daily.csv",
}
SOURCES = {
    "cl_daily": "Yahoo Finance CL=F daily OHLC (yfinance), finished NYMEX sessions only",
    "ovx_yahoo": "Yahoo Finance ^OVX daily close (yfinance), finished sessions only",
    "ovx_fred": "FRED OVXCLS (https://fred.stlouisfed.org/series/OVXCLS), keyless fredgraph CSV",
    "eia_futures": "EIA NYMEX WTI futures settlement, contracts 1 and 2 (RCLC1, RCLC2), final through 2024-04-05; "
                   "from data/eia_wti_futures_curve.json, a second source only",
    "eia_spot": "EIA Cushing WTI spot price (RWTC); from data/eia_wti_spot_daily.json, a second source only",
}
EIA_FUTURES_JSON = DATA_ROOT / "eia_wti_futures_curve.json"
EIA_SPOT_JSON = DATA_ROOT / "eia_wti_spot_daily.json"
TICK = 0.01                    # CL's price increment, $/bbl: EIA publishes cents, Yahoo stores float32
TICK_TOLERANCE = TICK + 1e-9           # on prices rounded to the cent (cents)
# freeze_source_check: a gap from Cushing spot this large is a gross error, not a market move (the
# largest since 2000-08-23 is 0.354, on 2020-03-30); dates listed per check in the payload.
SPOT_LOG_GAP_TOLERANCE = 0.40
SOURCE_CHECK_LISTED = 10
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
    eia_futures: pd.DataFrame | None = None     # c1, c2 ($/bbl, EIA's dates); a second source only
    eia_spot: pd.Series | None = None           # Cushing spot ($/bbl); a second source only


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


def eia_frames(futures_path: Path = EIA_FUTURES_JSON,
               spot_path: Path = EIA_SPOT_JSON) -> tuple[pd.DataFrame, pd.Series]:
    """EIA's contract 1 and 2 settlements and Cushing spot, from the committed JSON files, as published
    (the negative prints of 2020-04-20 included): never fetched here."""
    futures = json.loads(futures_path.read_text(encoding="utf-8"))
    if futures.get("columns") != ["date", "c1", "c2"]:
        raise ValueError(f"{futures_path.name}: expected columns date, c1, c2")
    frame = pd.DataFrame(futures["rows"], columns=["Date", "c1", "c2"])
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("Date")))
    spot_rows = json.loads(spot_path.read_text(encoding="utf-8"))["rows"]
    spot = pd.Series([row[1] for row in spot_rows], index=pd.DatetimeIndex(pd.to_datetime([row[0] for row in spot_rows])),
                     name="spot", dtype=float)
    return frame.astype(float).sort_index(), spot.sort_index()


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
    eia_futures, eia_spot = eia_frames()
    return MarketSnapshot(cl, ovx_yahoo, ovx_fred, {}, eia_futures, eia_spot), quality


def _write_csv(frame: pd.DataFrame | pd.Series, path: Path) -> None:
    out = frame.to_frame() if isinstance(frame, pd.Series) else frame
    out.index = pd.DatetimeIndex(out.index).strftime("%Y-%m-%d")
    out.index.name = "Date"
    path.write_text(out.to_csv(float_format=FLOAT_FORMAT, lineterminator="\n"), encoding="utf-8")


def _write_entry(key: str, frame: pd.DataFrame | pd.Series, directory: Path) -> dict[str, Any]:
    """Write one snapshot file and return its manifest entry."""
    path = directory / FILES[key]
    _write_csv(frame, path)
    index = pd.DatetimeIndex(frame.index)
    return {
        "file": FILES[key],
        "sha256": _sha256(path),
        "rows": int(len(frame)),
        "first": index.min().strftime("%Y-%m-%d"),
        "last": index.max().strftime("%Y-%m-%d"),
        "source": SOURCES[key],
    }


def write_snapshot(snapshot: MarketSnapshot, quality: dict[str, int], now: datetime | None = None,
                   directory: Path = SNAPSHOT_DIR) -> dict[str, Any]:
    """Write the CSVs and a manifest pinning their SHA-256 digests. Returns the manifest."""
    now = now or datetime.now(timezone.utc)
    directory.mkdir(parents=True, exist_ok=True)
    if snapshot.eia_futures is None or snapshot.eia_spot is None:
        raise ValueError("a snapshot needs its EIA second sources (eia_frames)")
    frames: dict[str, pd.DataFrame | pd.Series] = {
        "cl_daily": snapshot.cl, "ovx_yahoo": snapshot.ovx_yahoo, "ovx_fred": snapshot.ovx_fred,
        "eia_futures": snapshot.eia_futures, "eia_spot": snapshot.eia_spot}
    files = {key: _write_entry(key, frame, directory) for key, frame in frames.items()}
    manifest = {
        "retrieved_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sessions_through": last_completed_bar_date(now).isoformat(),
        "files": files,
        "cl_data_quality": quality,
    }
    (directory / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def add_eia(directory: Path = SNAPSHOT_DIR, futures_path: Path = EIA_FUTURES_JSON,
            spot_path: Path = EIA_SPOT_JSON) -> dict[str, Any]:
    """Add (or rewrite) the two EIA files of an existing snapshot from the committed JSON files. The
    other files, their digests and the manifest's retrieval time stay as they are. Returns the manifest."""
    manifest_path = directory / MANIFEST_NAME
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    futures, spot = eia_frames(futures_path, spot_path)
    files = {key: entry for key, entry in manifest["files"].items() if key not in ("eia_futures", "eia_spot")}
    files["eia_futures"] = _write_entry("eia_futures", futures, directory)
    files["eia_spot"] = _write_entry("eia_spot", spot, directory)
    manifest["files"] = files
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
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
    eia_futures = pd.read_csv(directory / FILES["eia_futures"], index_col="Date", parse_dates=True)
    eia_spot = pd.read_csv(directory / FILES["eia_spot"], index_col="Date", parse_dates=True)["spot"]
    return MarketSnapshot(cl, ovx_yahoo, ovx_fred, manifest, eia_futures, eia_spot)


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


def cents(close: pd.Series) -> pd.Series:
    """CL=F closes rounded to the cent: Yahoo stores them as float32 (104.970001), the exchange quotes cents."""
    return close.round(2)


def _business_days_only(index: pd.Index) -> pd.DatetimeIndex:
    days = pd.DatetimeIndex(index)
    return days[[is_business_day(day.date()) for day in days]]


def cl_source_agreement(snapshot: MarketSnapshot, sample_first: str | None = None) -> dict[str, Any]:
    """How closely the CL=F closes match EIA: contract 1 to the tick, and Cushing spot loosely.

    EIA's contract 1 is the NYMEX front-month settlement, the series CL=F follows, so on a session both
    have they should agree to the cent; CL=F is rounded to the cent first (cents), and a gap of up to one
    tick counts as agreement, a miss beyond it. Spot is the
    physical Cushing price, so it only bounds gross errors. `sample_first` (the lab's first
    out-of-sample date) adds the same contract-1 count for the sessions the research is scored on.
    """
    if snapshot.eia_futures is None or snapshot.eia_spot is None:
        raise ValueError("the snapshot has no EIA files (python -m backend.market_data --add-eia)")
    close = cents(snapshot.cl["Close"]).rename("yahoo")
    futures = snapshot.eia_futures.loc[_business_days_only(snapshot.eia_futures.index)]
    joined = pd.concat([close, futures], axis=1, join="inner").dropna()
    gap = (joined["yahoo"] - joined["c1"]).abs()
    miss = gap > TICK_TOLERANCE
    overlap = futures.index[(futures.index >= close.index.min()) & (futures.index <= joined.index.max())]
    largest = gap.sort_values(ascending=False, kind="stable").head(5)

    def row(day: pd.Timestamp) -> dict[str, Any]:
        return {"date": day.strftime("%Y-%m-%d"), "yahoo": round(float(cast(float, joined.at[day, "yahoo"])), 4),
                "c1": round(float(cast(float, joined.at[day, "c1"])), 2),
                "c2": round(float(cast(float, joined.at[day, "c2"])), 2)}

    contract1: dict[str, Any] = {
        "first": joined.index.min().strftime("%Y-%m-%d"), "last": joined.index.max().strftime("%Y-%m-%d"),
        "common_days": int(len(joined)),
        "share_same_cent": round(float((gap < TICK / 2).mean()), 4),
        "share_within_one_tick": round(float((~miss).mean()), 4),
        "misses": int(miss.sum()),
        "misses_by_year": {str(year): int(count) for year, count in
                           miss[miss].groupby(pd.DatetimeIndex(miss[miss].index).year).size().items()},
        "largest_gaps": [row(day) for day in largest.index],
        # Sessions EIA has that CL=F lacks: gaps in Yahoo's series, and apart from them the sessions the
        # negative-price rule drops (a non-positive settlement has no logarithm; vol_forecast._completed).
        "eia_sessions_missing_from_cl": [day.strftime("%Y-%m-%d") for day in overlap
                                         if day not in close.index and float(cast(float, futures.at[day, "c1"])) > 0],
        "nonpositive_sessions_dropped": [day.strftime("%Y-%m-%d") for day in overlap
                                         if day not in close.index and float(cast(float, futures.at[day, "c1"])) <= 0],
    }
    if sample_first is not None:
        since = miss[miss.index >= pd.Timestamp(sample_first)]
        contract1["since_sample_first"] = {"first": sample_first, "common_days": int(len(since)),
                                           "misses": int(since.sum()),
                                           "miss_dates": [day.strftime("%Y-%m-%d") for day in since[since].index]}
    spot = snapshot.eia_spot[snapshot.eia_spot > 0]
    paired = pd.concat([close[close > 0], spot], axis=1, join="inner").dropna()
    log_gap = pd.Series(np.abs(np.log(paired["yahoo"].to_numpy(dtype=float) / paired["spot"].to_numpy(dtype=float))),
                        index=paired.index)
    worst = pd.Timestamp(cast(Any, log_gap.idxmax()))
    return {
        "tolerance_usd": TICK,
        "contract1": contract1,
        "spot": {"first": paired.index.min().strftime("%Y-%m-%d"), "last": paired.index.max().strftime("%Y-%m-%d"),
                 "common_days": int(len(paired)),
                 "median_abs_log_gap": round(float(log_gap.median()), 4),
                 "p99_abs_log_gap": round(float(log_gap.quantile(0.99)), 4),
                 "max_abs_log_gap": round(float(log_gap.max()), 4),
                 "max_gap_date": worst.strftime("%Y-%m-%d")},
    }



def _iso(day: Any) -> str:
    return pd.Timestamp(day).strftime("%Y-%m-%d")


def freeze_source_check(close: pd.Series, snapshot: MarketSnapshot, spot: pd.Series | None) -> dict[str, Any]:
    """The live CL=F closes a freeze uses, checked against the pinned snapshot and a fresh Cushing spot series.

    Report only (vol_forecast._source_check puts it in the payload; nothing fails on it). `snapshot`:
    every pinned session must be in the live series and within one tick of it, rounded to the cent
    (cents), and no live session inside the pinned span may be new; a mismatch means Yahoo revised its
    history (prices only: volume is never compared). `spot`: live sessions after the pinned spot copy
    ends, paired with spot prices above zero, must be within SPOT_LOG_GAP_TOLERANCE in absolute log
    terms, which catches only a gross error. With nothing to pair the status is no_spot_rows, never a
    pass. Every value is an int, a float, a str or None (no NaN), so the block serialises as JSON.
    """
    live = cents(close)
    pinned = cents(snapshot.cl["Close"])
    first, last = pinned.index.min(), pinned.index.max()
    within = live[(live.index >= first) & (live.index <= last)]
    common = pinned.index.intersection(live.index)
    gap = (live.loc[common] - pinned.loc[common]).abs()
    missing = pinned.index.difference(live.index)
    added = within.index.difference(pinned.index)
    beyond = gap.index[gap.to_numpy() > TICK_TOLERANCE]
    flagged = sorted({*missing, *added, *beyond})
    pinned_check: dict[str, Any] = {
        "status": "beyond_tolerance" if flagged else "pass",
        "first": _iso(first), "last": _iso(last), "sessions": int(len(pinned)), "tolerance_usd": TICK,
        "n_missing": int(len(missing)), "n_added": int(len(added)), "n_beyond_tick": int(len(beyond)),
        "dates": [_iso(day) for day in flagged[:SOURCE_CHECK_LISTED]],
    }
    spot_from = snapshot.eia_spot.index.max() if snapshot.eia_spot is not None and len(snapshot.eia_spot) else last
    after = close[(close.index > spot_from) & (close > 0)].rename("cl")
    usable = spot[spot > 0].rename("spot") if spot is not None else pd.Series(dtype=float, name="spot")
    paired = pd.concat([after, usable], axis=1, join="inner").dropna()
    spot_check: dict[str, Any] = {
        "status": "no_spot_rows", "tolerance_abs_log_gap": SPOT_LOG_GAP_TOLERANCE, "after": _iso(spot_from),
        "sessions_after": int(len(after)), "spot_last": _iso(usable.index.max()) if len(usable) else None,
        "paired": int(len(paired)), "max_abs_log_gap": None, "n_above": 0, "dates": [],
    }
    if len(paired):
        log_gap = pd.Series(np.abs(np.log(paired["cl"].to_numpy(dtype=float) / paired["spot"].to_numpy(dtype=float))),
                            index=paired.index)
        above = log_gap.index[log_gap.to_numpy() > SPOT_LOG_GAP_TOLERANCE]
        spot_check.update(status="beyond_tolerance" if len(above) else "pass",
                          max_abs_log_gap=round(float(log_gap.max()), 4), n_above=int(len(above)),
                          dates=[_iso(day) for day in above[:SOURCE_CHECK_LISTED]])
    return {"snapshot": pinned_check, "spot": spot_check}

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="The pinned market-data snapshot (data/market/).")
    parser.add_argument("--add-eia", action="store_true",
                        help="add the EIA second-source files from the committed JSON, keeping every other file")
    args = parser.parse_args(argv)
    if not args.add_eia:
        parser.print_help()
        return 2
    manifest = add_eia()
    for key in ("eia_futures", "eia_spot"):
        entry = manifest["files"][key]
        print(f"{entry['file']}: {entry['rows']} rows, {entry['first']} to {entry['last']}, sha256 {entry['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
