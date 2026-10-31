#!/usr/bin/env python3
"""
Git-auditable forward record of the live next-week realized-volatility forecast.

The model of record (backend/vol_forecast.py, HAR-IV) and the live challenger chosen by
backend/vol_lab.py are validated on a purged walk-forward backtest. A backtest cannot prove the
forecasts would have been the same in real time; only a record kept forward can. Every CI run
(refresh.yml, after freeze.py) appends the published forecast to data/vol_track_record.json and
scores every forecast whose target week has settled. The workflow persists the file on the
dedicated live-data branch, so each entry and each resolution is timestamped by a git commit
made by automation that cannot reach protected main. Nothing here changes a recorded forecast or
deletes an entry: settling an entry only adds its outcome, and a settled entry is never revisited.

Rules (conservative by construction; sessions are CME business days, see contract_calendar):

- One entry per forecast session (`as_of`, the last completed session the forecast uses). The
  first issuance recorded for a session wins and is never overwritten; a forecast for a session
  older than the newest recorded one is ignored (it was built from stale data).
- A forecast is recorded only if its `as_of` session had finished when it was issued (the model
  never saw a partial bar) and its `issued_at` is not in the future of the recorder's clock.
- TARGET WINDOW (entry-time clean). A forecast counts as issued at the later of its own
  `issued_at` stamp and the moment this recorder wrote it down, plus COMMIT_MARGIN. Let k0 be the
  first business day whose CL settlement (14:30 America/New_York on that date) is later than that
  moment. The forecast is scored on the realized volatility of the five settlement-to-settlement
  log returns ending at sessions k0+1 .. k0+5 (closes k0 .. k0+5):
      realized = sqrt(mean(r^2) * 252), in percent.
  This is STRICTER than the backtest, whose target window opens at the `as_of` settlement itself:
  the live forecast is published hours after that settlement, so the price move between the
  `as_of` settlement and publication is already known when the forecast appears and must not be
  scored. The live window therefore starts at least one session later than the backtest's. The
  one-hour margin leaves room for the time between this step and the publish job's commit of the
  record (after the site deploys, normally within a quarter of an hour); the seal below checks
  that the commit came first. The record stores k0 as `base_session` and the scored return
  sessions as `target_start` .. `target_end`.
- SEAL (status "late_commit"). The build job records an entry; the publish job commits it to the
  live-data branch later. Immediately before that commit the publish job runs --seal, which stamps
  `sealed_at` (its clock) on every entry not yet sealed. The seal, the commit and the push run in
  one workflow step capped at four minutes, so the commit is made within SEAL_MARGIN (five
  minutes, the cap plus a minute of slack) of `sealed_at`. An entry whose window-opening
  settlement (14:30 ET on base_session) is not later than `sealed_at` + SEAL_MARGIN may have been
  committed after that settlement: the seal marks it "late_commit", and it is never scored.
  Resolution settles only entries sealed in time. An entry still unsealed when its window is about
  to open was never committed by the seal step (for example, it was written by hand), so it is
  closed as "late_commit" too. A failed persist never gets that far: the run's new entry does not
  reach live-data at all, and later runs restore the record without it.
- CHAIN. When the seal stamps an entry, it also stores `previous_sha256`: the SHA-256 of the sealed
  part of the entry before it (entry_digest: every field but status and the ones resolution sets,
  _AFTER_SEAL_KEYS, as Python's canonical JSON). That part is final once an entry is sealed, and it
  includes the earlier entry's own link, so changing, removing or inserting a sealed entry while
  keeping the later links breaks a link (chain_breaks), and so does a sealed entry with no link from
  CHAIN_FROM on. The record's first entry links to nothing (null); entries sealed before the chain
  existed carry no link and are not checked. The chain has no key: whoever can write live-data can
  recompute every later link, and nothing links to the newest entry yet, so the chain shows an edit
  only against a copy or timestamp of the record from before it. Additive: nothing else about the
  seal changes, and loading the record never checks the chain, so a break can never stop the
  pipeline; scripts/verify_record.py, the record summary (summary["chain"], shown on LIVE) and the
  Health check report it.
- FIT. A new entry also stores the fit that produced it: `coefficients` (the log-log regression's
  intercept, then its slopes on ln RV5, ln RV22 and ln RV66 in decimals and ln OVX in index points;
  four numbers for the plain-HAR fallback) and the Duan `smearing_factor`, so a sealed
  forecast can be recomputed from its own entry and the change between two calls split into input
  moves and the refit. Both are descriptive: nothing in recording, sealing, resolution or scoring
  reads them. Entries recorded before they were stored have neither.
- Closes come from `vol_forecast.recent_closes` (the finished-session closes the forecaster itself
  used) and, for any session it lacks, from the payload's completed daily ("1d") chart bars
  (unified_data.actual), dated by their session. A bar newer than the last finished session
  (contract_calendar.last_completed_bar_date, evaluated at the payload's freeze time and again
  BAR_FETCH_MARGIN earlier, since the bars are fetched before the freeze is stamped) or dated on
  an exchange holiday is never used: it is the session still trading, or a holiday partial.
- RESOLVE once the closes of k0 .. k0+5 are all available as completed bars.
- SKIP (status "skipped_roll") a window that spans a CL contract roll: CL=F's daily bars roll on
  the session after the expiring contract's last trade date, and that day's return books the
  calendar spread, not a price move. Such entries are recorded but never scored.
- EXPIRE (status "expired") an entry whose closes are still missing more than MAX_WAIT_SESSIONS
  business days after k0+5, instead of scoring it later against whatever data turns up.
- SCORE resolved entries: QLIKE on variances (Patton 2011) for the model of record's
  variance-consistent forecast and, on the entries where it was published, the challenger's;
  80% interval coverage; and the direction call (median vs the current 5-session RV). The model of
  record's statistics (summary["champion"]) cover its pinned version, PROMOTION_MODEL_VERSION,
  only: an entry of another version, such as the plain-HAR forecast ("har/2") the forecaster
  publishes when it has no lagged OVX print, comes from a different model and is counted but not
  pooled with them. Direction accuracy is not evidence of skill: it has a noise floor of about 75%
  (on simulated returns with no volatility dynamics the same forecaster is right three times in
  four, because a 5-session realized vol is a noisy estimate that reverts toward its mean). Daily
  forecasts of a five-session window overlap, so `n_independent` counts the resolved entries whose
  windows do not overlap (greedy in time order): that, not `n_resolved`, is the effective sample
  size.
- PROMOTION: one test, run once, on a fixed sample (summary["promotion"]). The test is pinned to
  the pair it was declared for: the model of record at model_version PROMOTION_MODEL_VERSION
  against the challenger PROMOTION_CHALLENGER. An entry of any other model version, with another
  challenger, or with no challenger forecast is recorded and counted (summary["promotion"]
  n_excluded_*) but never enters the sample, so it can neither hold the sample back nor restart
  it. A different pair is a new test, declared by changing those two constants. In record order,
  take the resolved entries of the pinned pair, keep the ones whose windows do not overlap
  (greedy, as for `n_independent`), and stop at the first entry of that pair still pending: it may
  still resolve, and its window comes before those of later entries, while everything before it is
  final. The first PROMOTION_WEEKS entries selected are the sample. On it, a one-sided
  Diebold-Mariano test of the QLIKE differences, model of record minus challenger (positive
  favours the challenger): the mean difference over its standard error, from the plain sample
  variance with an n - 1 denominator and no autocovariance terms, as for one-step-ahead forecasts,
  since the windows do not overlap. For one step this equals the Harvey-Leybourne-Newbold (1997)
  corrected statistic, and as they recommend its p-value is from Student's t with n - 1 = 51
  degrees of freedom (student_t_sf), not the standard normal: the normal's 10% critical value,
  1.2816, is below t(51)'s 1.2984, and rejecting above it has a size of about 10.3% under t(51). The
  verdict is "promote" if the mean difference is positive and p < PROMOTION_ALPHA, else "keep";
  "accruing" until the sample is full. Entries added later never change it (a single look).
  Applying a "promote" verdict, i.e. swapping the models, is a maintainer's change to the code.
- NEXT RESULT (summary["next_result_due"]): the earliest target_end among pending entries whose
  windows do not span a roll (the same spans_roll test resolution applies), or null.

The summary is written into the payload at vol_forecast.live_record (docs/DATA_CONTRACT.md).

Standard library only (plus contract_calendar and safe_paths, which are standard-library only):
the publish job validates the restored record with `from backend.vol_record import load_record`
and seals it with --seal without installing any dependency.

Usage:
    python backend/vol_record.py --data public/data.json      # CI build job, after freeze.py
    python backend/vol_record.py --seal                       # CI publish job, right before the commit
    python backend/vol_record.py --init                       # write an empty record (seeding)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TypedDict

if TYPE_CHECKING:
    # The record, its entries and the frozen payload are JSON objects (validate_record checks the record).
    Record = dict[str, Any]
    Entry = dict[str, Any]
    Payload = dict[str, Any]

    class Challenger(TypedDict):
        key: str
        forecast_pct: float
        median_pct: float | None

try:
    from .contract_calendar import (
        EXCHANGE_TZ, business_days_between, is_business_day, last_completed_bar_date,
        shift_business_days, spans_roll,
    )
    from .safe_paths import data_json_path, public_json_path
except ImportError:  # Direct invocation: python backend/vol_record.py
    from contract_calendar import (  # type: ignore[import-not-found, no-redef]
        EXCHANGE_TZ, business_days_between, is_business_day, last_completed_bar_date,
        shift_business_days, spans_roll,
    )
    from safe_paths import data_json_path, public_json_path  # type: ignore[import-not-found, no-redef]

SCHEMA_VERSION = 1
RECORD_PATH = data_json_path("vol_track_record.json")
SETTLEMENT_ET = time(14, 30)                 # NYMEX CL daily settlement (America/New_York)
HORIZON_SESSIONS = 5                         # the forecast's target: next 5 sessions
ANNUALIZATION = 252
COMMIT_MARGIN = timedelta(hours=1)           # recording -> the publish job's commit, with room to spare
SEAL_MARGIN = timedelta(minutes=5)           # seal -> commit and push: refresh.yml caps that step at 4 minutes
CHAIN_FROM = date(2026, 11, 2)               # the first session recorded after the chain shipped: every sealed
                                             # entry from this as_of on must carry previous_sha256
BAR_FETCH_MARGIN = timedelta(hours=1)        # daily bars are fetched before frozen_at is stamped
MAX_CLOCK_SKEW = timedelta(minutes=5)        # an issued_at later than the run's clock is refused
MAX_WAIT_SESSIONS = 10                       # closes missing this long after k0+5: expired
RECENT_ENTRIES = 12
MAX_FIT_TERMS = 8                            # stored coefficients: intercept + slopes (5 for HAR-IV v2)
STATUSES = ("pending", "resolved", "skipped_roll", "expired", "late_commit")
_SEALED_IN_TIME = ("resolved", "skipped_roll", "expired")    # only entries sealed in time get these
DIRECTIONS = ("RISING", "FALLING")
PROMOTION_WEEKS = 52
PROMOTION_ALPHA = 0.10
# The pair the promotion test was declared for: the model of record's version (vol_forecast.MODEL_VERSION)
# and the challenger's key (data/vol_lab.json config.challenger). tests/test_vol_record.py checks both
# against their sources, so a new model version or challenger fails CI until it is declared here.
PROMOTION_MODEL_VERSION = "har-iv/2"
PROMOTION_CHALLENGER = "har_iv_range"
PROMOTION_RULE = (f"The promotion test is pinned to one pair: HAR-IV v2 (model version "
                  f"{PROMOTION_MODEL_VERSION}), the model of record, against the HAR-IV-range challenger (key "
                  f"{PROMOTION_CHALLENGER}). The challenger replaces the model of record if, on the first "
                  f"{PROMOTION_WEEKS} independent resolved weeks where both were forecast, its mean QLIKE is lower "
                  f"with a one-sided Diebold-Mariano p below {PROMOTION_ALPHA:.2f} (Harvey-Leybourne-Newbold, "
                  f"Student t with {PROMOTION_WEEKS - 1} degrees of freedom). The test is run once, on that fixed "
                  f"sample. Forecasts from another model version or challenger are recorded but never enter it; a "
                  f"different challenger would start a new, separately declared test. A maintainer applies the "
                  f"verdict.")
WINDOW_NOTE = ("Each forecast is scored on the realized volatility of the five settlement-to-settlement "
               "returns after the first CL settlement (14:30 ET) at least an hour after it was issued and "
               "recorded, one session or more later than the backtest's window. The record is sealed just "
               "before each git commit; an entry not sealed more than five minutes before that settlement is "
               "marked late_commit and never scored, so every scored forecast was committed before any price "
               "it is scored on.")
_RECORD_KEYS = {"schema_version", "started", "entries"}
_REQUIRED_ENTRY_KEYS = {
    "as_of", "issued_at", "recorded_at", "model_version", "forecast_pct", "base_session",
    "target_start", "target_end", "status",
}


# --------------------------------------------------------------------------- parsing helpers

def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: object) -> float | None:
    """A finite real number (bools excluded), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _positive(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _iso_date(value: object) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"not an ISO date: {value!r}")
    return date.fromisoformat(value)


def _aware_utc(value: object) -> datetime:
    """An ISO-8601 timestamp WITH an offset ('Z' accepted), as UTC. A naive time is ambiguous: refused."""
    if not isinstance(value, str) or "T" not in value:
        raise ValueError(f"not an ISO timestamp: {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp without a UTC offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_constant(token: str) -> NoReturn:
    raise ValueError(f"non-finite number {token} in the record")


# --------------------------------------------------------------------------- calendar rules

def settlement_time(session: date) -> datetime:
    """When the CL contract settles on `session` (14:30 America/New_York)."""
    return datetime.combine(session, SETTLEMENT_ET, tzinfo=EXCHANGE_TZ)


def first_settlement_after(moment: datetime) -> date:
    """k0: the first business day whose 14:30 ET settlement is strictly later than `moment`."""
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    session = moment.astimezone(EXCHANGE_TZ).date()
    while not is_business_day(session) or settlement_time(session) <= moment:
        session += timedelta(days=1)
    return session


def target_window(issued_at: datetime) -> dict[str, date]:
    """The scored window for a forecast issued at `issued_at` (see the module docstring).

    base_session is k0 (its close opens the window); the five scored returns end at target_start
    (k0+1) .. target_end (k0+5).
    """
    base = first_settlement_after(issued_at)
    return {
        "base_session": base,
        "target_start": shift_business_days(base, 1),
        "target_end": shift_business_days(base, HORIZON_SESSIONS),
    }


def sealed_in_time(entry: Entry) -> bool:
    """Whether the entry was sealed more than SEAL_MARGIN before the settlement that opens its window."""
    sealed_at = entry.get("sealed_at")
    if sealed_at is None:
        return False
    return settlement_time(_iso_date(entry["base_session"])) > _aware_utc(sealed_at) + SEAL_MARGIN


def window_sessions(base_session: date) -> list[date]:
    """k0 .. k0+5: the six sessions whose closes the window needs."""
    return [base_session] + [shift_business_days(base_session, i) for i in range(1, HORIZON_SESSIONS + 1)]


def bars_cutoff(snapshot: datetime) -> date:
    """Newest session whose daily bar was certainly finished when the payload's bars were fetched.

    The bars are fetched at most BAR_FETCH_MARGIN before `snapshot`. last_completed_bar_date rises
    at 17:00 ET and at midnight and falls back at the 18:00 ET reopen; it never falls and then rises
    again within an hour, so its minimum over that interval is its value at one of the two ends.
    """
    return min(last_completed_bar_date(snapshot), last_completed_bar_date(snapshot - BAR_FETCH_MARGIN))


# --------------------------------------------------------------------------- record file

def init_record(now: datetime) -> Record:
    """An empty, valid record started on `now`'s UTC date."""
    return {"schema_version": SCHEMA_VERSION, "started": now.astimezone(timezone.utc).date().isoformat(),
            "entries": []}


def _validate_entry(entry: object, index: int) -> date:
    where = f"entry {index}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: not an object")
    missing = _REQUIRED_ENTRY_KEYS - entry.keys()
    if missing:
        raise ValueError(f"{where}: missing {sorted(missing)}")
    as_of = _iso_date(entry["as_of"])
    issued_at = _aware_utc(entry["issued_at"])
    recorded_at = _aware_utc(entry["recorded_at"])
    if not isinstance(entry["model_version"], str) or not entry["model_version"]:
        raise ValueError(f"{where}: model_version must be a non-empty string")
    if _positive(entry["forecast_pct"]) is None:
        raise ValueError(f"{where}: forecast_pct must be a positive number")
    for key in ("median_pct", "lo80_pct", "hi80_pct", "rv5_pct"):
        if entry.get(key) is not None and _positive(entry[key]) is None:
            raise ValueError(f"{where}: {key} must be a positive number or null")
    if (entry.get("lo80_pct") is None) != (entry.get("hi80_pct") is None):
        raise ValueError(f"{where}: an interval needs both bounds")
    if entry.get("lo80_pct") is not None and entry["lo80_pct"] > entry["hi80_pct"]:
        raise ValueError(f"{where}: lo80_pct exceeds hi80_pct")
    prob = entry.get("prob_vol_rises_pct")
    if prob is not None and (_number(prob) is None or not 0 <= prob <= 100):
        raise ValueError(f"{where}: prob_vol_rises_pct must be a percentage or null")
    if entry.get("direction") is not None and entry["direction"] not in DIRECTIONS:
        raise ValueError(f"{where}: direction must be one of {DIRECTIONS} or null")
    for key in ("contract", "model", "frozen_at"):
        if entry.get(key) is not None and not isinstance(entry[key], str):
            raise ValueError(f"{where}: {key} must be a string or null")
    if entry.get("inputs") is not None and not isinstance(entry["inputs"], dict):
        raise ValueError(f"{where}: inputs must be an object or null")
    coefficients = entry.get("coefficients")
    if coefficients is not None and (not isinstance(coefficients, list) or not 0 < len(coefficients) <= MAX_FIT_TERMS
                                     or any(_number(value) is None for value in coefficients)):
        raise ValueError(f"{where}: coefficients must be 1 to {MAX_FIT_TERMS} finite numbers or null")
    if entry.get("smearing_factor") is not None and _positive(entry["smearing_factor"]) is None:
        raise ValueError(f"{where}: smearing_factor must be a positive number or null")
    challenger = entry.get("challenger")
    if challenger is not None:
        if (not isinstance(challenger, dict) or not isinstance(challenger.get("key"), str)
                or not challenger["key"] or _positive(challenger.get("forecast_pct")) is None
                or (challenger.get("median_pct") is not None and _positive(challenger["median_pct"]) is None)):
            raise ValueError(f"{where}: challenger must be {{key, forecast_pct, median_pct}} or null")
    base, start, end = (_iso_date(entry[k]) for k in ("base_session", "target_start", "target_end"))
    if not as_of < base < start <= end:
        raise ValueError(f"{where}: window must satisfy as_of < base_session < target_start <= target_end")
    if issued_at > recorded_at + MAX_CLOCK_SKEW or recorded_at >= settlement_time(base):
        raise ValueError(f"{where}: recorded after its window opened, or issued after it was recorded")
    if entry.get("sealed_at") is not None and recorded_at > _aware_utc(entry["sealed_at"]) + MAX_CLOCK_SKEW:
        raise ValueError(f"{where}: sealed before it was recorded")
    link = entry.get("previous_sha256")
    if link is not None and (not isinstance(link, str) or len(link) != 64 or set(link) - set("0123456789abcdef")):
        raise ValueError(f"{where}: previous_sha256 must be a SHA-256 hex digest or null")
    status = entry["status"]
    if status not in STATUSES:
        raise ValueError(f"{where}: unknown status {status!r}")
    # A late seal is caught by the seal itself (late_commit), so a pending entry that carries a seal
    # was sealed in time, and resolution never settles anything else.
    if (status in _SEALED_IN_TIME or (status == "pending" and entry.get("sealed_at") is not None)) \
            and not sealed_in_time(entry):
        raise ValueError(f"{where}: a {status} entry must have been sealed more than "
                         f"{SEAL_MARGIN.total_seconds() / 60:g} minutes before its window opened")
    if status == "resolved":
        if _positive(entry.get("realized_pct")) is None:
            raise ValueError(f"{where}: a resolved entry needs a positive realized_pct")
        closes = entry.get("closes")
        if not isinstance(closes, list) or len(closes) != HORIZON_SESSIONS + 1:
            raise ValueError(f"{where}: a resolved entry needs the {HORIZON_SESSIONS + 1} closes it was scored on")
        days = []
        for pair in closes:
            if not isinstance(pair, list) or len(pair) != 2 or _positive(pair[1]) is None:
                raise ValueError(f"{where}: closes must be [date, positive price] pairs")
            days.append(_iso_date(pair[0]))
        if days[0] != base or days[-1] != end or any(a >= b for a, b in zip(days, days[1:])):
            raise ValueError(f"{where}: closes must run from base_session to target_end in order")
        _aware_utc(entry.get("resolved_at"))
    elif entry.get("realized_pct") is not None:
        raise ValueError(f"{where}: only a resolved entry carries realized_pct")
    return as_of


def validate_record(record: object) -> Record:
    """Raise ValueError unless `record` satisfies the structural contract this module writes."""
    if not isinstance(record, dict):
        raise ValueError("the record must be a JSON object")
    if set(record) != _RECORD_KEYS:
        raise ValueError(f"the record must have exactly the keys {sorted(_RECORD_KEYS)}")
    version = record["schema_version"]
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version!r}")
    _iso_date(record["started"])
    entries = record["entries"]
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    previous = None
    for index, entry in enumerate(entries):
        as_of = _validate_entry(entry, index)
        if previous is not None and as_of <= previous:
            raise ValueError(f"entry {index}: as_of {as_of} is not after the previous entry's {previous}")
        previous = as_of
    return record


def load_record(path: str | os.PathLike[str] | None = None) -> Record:
    """Read and validate the record. Fails closed: a missing, unreadable or malformed file raises.

    A missing file is an error, not an empty record: silently starting over would erase the
    forward record. Seed a new one deliberately with --init.
    """
    target = Path(path) if path is not None else RECORD_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"vol track record {target} is unreadable ({exc}); seed one with --init") from exc
    try:
        record = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"refusing to use malformed vol track record: {exc}") from exc
    try:
        return validate_record(record)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"refusing to use malformed vol track record: {exc}") from exc


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temporary file in the same directory and rename it into place. A file that
    already exists keeps its permission bits; a new one keeps mkstemp's owner-only mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode: int | None = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mode = None
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def save_record(record: Record, path: str | os.PathLike[str] | None = None) -> None:
    validate_record(record)
    _atomic_write(Path(path) if path is not None else RECORD_PATH,
                  json.dumps(record, indent=2, allow_nan=False) + "\n")


# --------------------------------------------------------------------------- payload

def _parse_challenger(raw: object, as_of: str) -> Challenger | None:
    """The published challenger forecast for the same session, or None."""
    if not isinstance(raw, dict) or raw.get("as_of", as_of) != as_of:
        return None
    key, forecast = raw.get("key"), _positive(raw.get("forecast_next_week_vol_annualized_pct"))
    if not isinstance(key, str) or not key or forecast is None:
        return None
    return {"key": key, "forecast_pct": forecast, "median_pct": _positive(raw.get("forecast_median_pct"))}


def _parse_fit(live: dict[str, Any]) -> tuple[list[float] | None, float | None]:
    """The live fit's coefficients (1 to MAX_FIT_TERMS finite numbers) and smearing factor (positive),
    each None when absent or malformed: they describe the forecast and never decide whether it is recorded."""
    raw = live.get("coefficients")
    coefficients = None
    if isinstance(raw, list) and 0 < len(raw) <= MAX_FIT_TERMS:
        values = [_number(value) for value in raw]
        if all(value is not None for value in values):
            coefficients = [value for value in values if value is not None]
    return coefficients, _positive(live.get("smearing_factor"))


def extract_entry(payload: Payload, now: datetime) -> tuple[Entry | None, str]:
    """Build the record entry for the payload's live forecast, or (None, why it is not recordable)."""
    live = _dict(_dict(payload).get("vol_forecast")).get("live")
    if not isinstance(live, dict):
        return None, "the payload carries no live vol forecast"
    forecast = _positive(live.get("forecast_next_week_vol_annualized_pct"))
    model_version = live.get("model_version")
    if forecast is None or not isinstance(model_version, str) or not model_version:
        return None, "the live forecast has no positive forecast or no model_version"
    try:
        as_of = _iso_date(live.get("as_of"))
        issued_at = _aware_utc(live.get("issued_at"))
    except (TypeError, ValueError) as exc:
        return None, f"the live forecast's as_of/issued_at is unusable ({exc})"
    if issued_at > now + MAX_CLOCK_SKEW:
        return None, f"issued_at {_stamp(issued_at)} is in the future of this run ({_stamp(now)})"
    if not is_business_day(as_of) or as_of > last_completed_bar_date(issued_at):
        return None, f"as_of {as_of} was not a finished session when the forecast was issued"
    window = target_window(max(issued_at, now) + COMMIT_MARGIN)

    interval = _dict(live.get("interval_80"))
    lo, hi = _positive(interval.get("low_pct")), _positive(interval.get("high_pct"))
    if lo is None or hi is None or lo > hi:
        lo = hi = None
    prob = _number(live.get("prob_vol_rises_pct"))
    inputs = live.get("inputs") if isinstance(live.get("inputs"), dict) else None
    rv5 = _positive((inputs or {}).get("rv5_pct"))
    if rv5 is None:
        rv5 = _positive(live.get("current_realized_vol_5d_annualized_pct"))
    contract = _dict(payload.get("contract"))
    frozen_at = payload.get("frozen_at")
    model = live.get("model")
    coefficients, smearing = _parse_fit(live)
    entry = {
        "as_of": as_of.isoformat(),
        "issued_at": _stamp(issued_at),
        "recorded_at": _stamp(now),
        "sealed_at": None,                       # stamped by --seal, right before the commit
        "frozen_at": frozen_at if isinstance(frozen_at, str) else None,
        "contract": contract.get("symbol") if isinstance(contract.get("symbol"), str) else None,
        "model_version": model_version,
        "model": model if isinstance(model, str) else None,
        "forecast_pct": forecast,
        "median_pct": _positive(live.get("forecast_median_pct")),
        "prob_vol_rises_pct": prob if prob is not None and 0 <= prob <= 100 else None,
        "direction": live.get("direction") if live.get("direction") in DIRECTIONS else None,
        "rv5_pct": rv5,
        "lo80_pct": lo,
        "hi80_pct": hi,
        "inputs": json.loads(json.dumps(inputs, allow_nan=False)) if inputs is not None else None,
        "coefficients": coefficients,
        "smearing_factor": smearing,
        "challenger": _parse_challenger(live.get("challenger"), as_of.isoformat()),
        "base_session": window["base_session"].isoformat(),
        "target_start": window["target_start"].isoformat(),
        "target_end": window["target_end"].isoformat(),
        "status": "pending",
        "realized_pct": None,
    }
    return entry, "ok"


def record_forecast(record: Record, payload: Payload, now: datetime) -> str:
    """Append the payload's forecast unless its session is already recorded (first issuance wins)."""
    try:
        entry, why = extract_entry(payload, now)
    except ValueError as exc:            # e.g. non-finite numbers inside `inputs`
        entry, why = None, f"the live forecast is malformed ({exc})"
    if entry is None:
        return f"not recorded: {why}"
    newest = record["entries"][-1]["as_of"] if record["entries"] else None
    if newest is not None and entry["as_of"] <= newest:
        if entry["as_of"] == newest:
            return f"session {entry['as_of']} already recorded (the first issuance stands)"
        return f"not recorded: as_of {entry['as_of']} is older than the newest recorded session {newest}"
    record["entries"].append(entry)
    return (f"recorded as_of {entry['as_of']} {entry['model_version']} {entry['forecast_pct']}% "
            f"(window {entry['target_start']}..{entry['target_end']} from the {entry['base_session']} close)")


def completed_closes(payload: Payload, cutoff: date) -> dict[date, float]:
    """Session -> close of completed sessions up to `cutoff`.

    The primary source is `vol_forecast.recent_closes` (the finished-session CL=F closes the
    forecaster itself used, one per session); the chart's daily ("1d") bars fill in any session it
    lacks. Both are filtered to business days on or before `cutoff`.
    """
    closes: dict[date, float] = {}
    recent = _dict(_dict(_dict(payload).get("vol_forecast")).get("recent_closes"))
    days, prices = recent.get("date"), recent.get("close")
    if isinstance(days, list) and isinstance(prices, list) and len(days) == len(prices):
        for day, value in zip(days, prices):
            price = _positive(value)
            try:
                session = date.fromisoformat(str(day))
            except ValueError:
                continue
            if price is not None and session <= cutoff and is_business_day(session):
                closes[session] = price
    for session, price in _chart_closes(payload, cutoff).items():
        closes.setdefault(session, price)
    return closes


def _chart_closes(payload: Payload, cutoff: date) -> dict[date, float]:
    """Session -> close for the payload's completed daily ("1d") chart bars up to `cutoff`."""
    actual = _dict(_dict(_dict(payload).get("unified_data")).get("actual"))
    values, stamps, kinds = actual.get("values"), actual.get("timestamps"), actual.get("kinds")
    if not (isinstance(values, list) and isinstance(stamps, list) and isinstance(kinds, list)
            and len(values) == len(stamps) == len(kinds)):
        return {}
    closes: dict[date, float] = {}
    for value, stamp, kind in zip(values, stamps, kinds):
        if kind != "1d":
            continue
        price = _positive(value)
        try:
            session = _aware_utc(stamp).date()      # daily bars are stamped at the session's midnight
        except (TypeError, ValueError):
            continue
        if price is None or session > cutoff or not is_business_day(session):
            continue
        closes[session] = price                     # the later bar for a date wins, as in vol_forecast
    return closes


def realized_vol_pct(closes: list[float]) -> float:
    """Annualized realized vol (percent) of the log returns between consecutive closes."""
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    return math.sqrt(sum(r * r for r in returns) / len(returns) * ANNUALIZATION) * 100.0


# The fields an entry gains or changes after its seal: resolution's, and the seal's own late_commit
# check. Everything else, sealed_at and previous_sha256 included, is fixed once the entry is sealed.
_AFTER_SEAL_KEYS = frozenset({"status", "closed_at", "resolved_at", "closes", "realized_pct", "missing_sessions"})


def entry_digest(entry: Entry) -> str:
    """SHA-256 (hex) of the entry's sealed part, every field except _AFTER_SEAL_KEYS, written by Python's
    json.dumps with sorted keys, separators "," and ":", ASCII escapes and no NaN (so a float such as 46.0
    keeps its ".0"). The next entry stores it as previous_sha256 when it is sealed."""
    sealed = {key: value for key, value in entry.items() if key not in _AFTER_SEAL_KEYS}
    text = json.dumps(sealed, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chain_breaks(entries: list[Entry]) -> list[str]:
    """The as_of of every sealed entry whose link is missing or does not match the sealed part of the entry before it.

    Entries sealed before the chain existed carry no previous_sha256 and are not checked. Every sealed entry
    from CHAIN_FROM on, and every one after the first entry that carries a link, must carry one, so dropping
    one link, or all of them, is a break. An entry not sealed yet (the build job's newest, before the publish
    job seals it) has no link yet. The record's first entry links to nothing (null).
    """
    breaks, linked = [], False
    for index, entry in enumerate(entries):
        if entry.get("sealed_at") is None:
            continue
        if "previous_sha256" not in entry:
            if linked or _iso_date(entry["as_of"]) >= CHAIN_FROM:
                breaks.append(entry["as_of"])
            continue
        linked = True
        if entry["previous_sha256"] != (entry_digest(entries[index - 1]) if index else None):
            breaks.append(entry["as_of"])
    return breaks


def chain_break_reason(entry: Entry, first: bool = False) -> str:
    """Why chain_breaks lists `entry` (`first`: it is the record's first entry), in words."""
    if "previous_sha256" not in entry:
        return ("it was sealed without a previous_sha256: its link was removed, or a run without the chain "
                "sealed it")
    if first:
        return "it is the record's first entry but links to an entry before it: entries were removed before it"
    return ("its previous_sha256 does not match the sealed part of the entry before it: that entry or this link "
            "was changed, or an entry was removed or inserted between them")


def seal_entries(record: Record, now: datetime) -> list[str]:
    """Stamp sealed_at = `now` on every entry not yet sealed; returns one message per entry sealed.

    Runs immediately before the record is committed (refresh.yml's publish job). A pending entry whose
    window-opening settlement is not later than `now` + SEAL_MARGIN becomes "late_commit": its commit
    cannot be shown to precede that settlement, so it is never scored. Entries already sealed are left
    alone, so sealing again changes nothing. Each entry sealed also gets its link in the chain,
    previous_sha256 (the module docstring's CHAIN): the entry before it is sealed by then, earlier or
    just above in this loop, so its sealed part is final. Mutates `record`.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    messages = []
    entries = record["entries"]
    for index, entry in enumerate(entries):
        if entry.get("sealed_at") is not None:
            continue
        entry["sealed_at"] = _stamp(now)
        entry["previous_sha256"] = entry_digest(entries[index - 1]) if index else None
        if entry["status"] == "pending" and not sealed_in_time(entry):
            entry["status"] = "late_commit"
            entry["closed_at"] = _stamp(now)
            messages.append(f"as_of {entry['as_of']}: sealed too close to the {entry['base_session']} settlement "
                            "that opens its window — late_commit, never scored")
        else:
            messages.append(f"as_of {entry['as_of']}: sealed ({entry['status']})")
    return messages


def resolve_entries(record: Record, closes: dict[date, float], cutoff: date, now: datetime) -> list[str]:
    """Settle every pending entry that can be settled; returns one message per change.

    Only entries sealed in time are settled. An unsealed one waits for the seal while that can still be
    in time, and is closed as "late_commit" once it cannot.
    """
    messages = []
    for entry in record["entries"]:
        if entry["status"] != "pending":
            continue
        base, end = date.fromisoformat(entry["base_session"]), date.fromisoformat(entry["target_end"])
        if not sealed_in_time(entry):
            if entry.get("sealed_at") is not None or settlement_time(base) <= now + SEAL_MARGIN:
                entry["status"] = "late_commit"
                entry["closed_at"] = _stamp(now)
                messages.append(f"as_of {entry['as_of']}: not sealed before the {entry['base_session']} "
                                "settlement that opens its window — late_commit, never scored")
            continue
        if spans_roll(base, end):
            entry["status"] = "skipped_roll"
            entry["closed_at"] = _stamp(now)
            messages.append(f"as_of {entry['as_of']}: window spans a contract roll — skipped")
            continue
        sessions = window_sessions(base)
        if all(day in closes for day in sessions):
            prices = [closes[day] for day in sessions]
            entry["status"] = "resolved"
            entry["resolved_at"] = _stamp(now)
            entry["closes"] = [[day.isoformat(), price] for day, price in zip(sessions, prices)]
            entry["realized_pct"] = round(realized_vol_pct(prices), 4)
            messages.append(f"as_of {entry['as_of']}: realized {entry['realized_pct']:.2f}% "
                            f"vs forecast {entry['forecast_pct']}%")
        elif business_days_between(end, cutoff) > MAX_WAIT_SESSIONS:
            entry["status"] = "expired"
            entry["closed_at"] = _stamp(now)
            entry["missing_sessions"] = [day.isoformat() for day in sessions if day not in closes]
            messages.append(f"as_of {entry['as_of']}: closes still missing {MAX_WAIT_SESSIONS} sessions "
                            "after the window — expired")
    return messages


# --------------------------------------------------------------------------- scoring

def qlike(realized_pct: float, forecast_pct: float) -> float:
    """QLIKE loss on variances (0 = perfect); same definition as backend/forecast_eval.qlike."""
    ratio = (realized_pct / forecast_pct) ** 2
    return ratio - math.log(ratio) - 1.0


def _mean(values: list[float], digits: int) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


def _share_pct(flags: list[bool]) -> float | None:
    return round(100.0 * sum(flags) / len(flags), 1) if flags else None


def independent_entries(resolved: list[Entry]) -> list[Entry]:
    """Greedy, in time order, the resolved entries whose scored return windows do not overlap."""
    chosen, last_end = [], None
    for entry in sorted(resolved, key=lambda e: (e["target_start"], e["target_end"])):
        if last_end is None or entry["target_start"] > last_end:
            chosen.append(entry)
            last_end = entry["target_end"]
    return chosen


def _pair(entry: Entry) -> tuple[str, str | None]:
    """(model of record version, challenger key) of an entry; the key is None without a challenger."""
    return entry["model_version"], (entry.get("challenger") or {}).get("key")


def promotion_sample(entries: list[Entry], model_version: str = PROMOTION_MODEL_VERSION,
                     challenger_key: str | None = PROMOTION_CHALLENGER) -> list[Entry]:
    """The first PROMOTION_WEEKS (or fewer, while accruing) independent resolved entries of the pair.

    The pair defaults to the declared one. Entries of any other pair are passed over, so they never
    enter the selection or stop the scan. Entries are taken in record order; those whose windows
    overlap an already selected one are skipped (greedy, as independent_entries does). The scan
    stops at the first entry of the pair that is still pending: it may resolve and its window
    precedes later ones, whereas every entry before it is final. So the selection only grows, and
    once full it never changes.
    """
    if challenger_key is None:
        return []
    chosen, last_end = [], None
    for entry in entries:
        if _pair(entry) != (model_version, challenger_key):
            continue
        if entry["status"] == "pending":
            break
        if entry["status"] == "resolved" and (last_end is None or entry["target_start"] > last_end):
            chosen.append(entry)
            last_end = entry["target_end"]
            if len(chosen) == PROMOTION_WEEKS:
                break
    return chosen


def promotion_exclusions(entries: list[Entry], model_version: str = PROMOTION_MODEL_VERSION,
                         challenger_key: str | None = PROMOTION_CHALLENGER) -> dict[str, int]:
    """Resolved entries kept out of the pair's comparison, counted by reason.

    An entry of another model version counts under that reason whatever its challenger; an entry of
    the pair's model version counts under another challenger or under no challenger. Together with
    the entries of the pair they add up to every resolved entry.
    """
    resolved = [e for e in entries if e["status"] == "resolved"]
    keys = [_pair(e)[1] for e in resolved if e["model_version"] == model_version]
    return {
        "n_excluded_other_version": len(resolved) - len(keys),
        "n_excluded_other_challenger": sum(key is not None and key != challenger_key for key in keys),
        "n_excluded_no_challenger": sum(key is None for key in keys),
    }


def student_t_sf(t: float, df: int) -> float:
    """P(T > t) for Student's t with a positive integer number of degrees of freedom `df`.

    The closed-form finite series of Abramowitz & Stegun 26.7.3 (odd df) and 26.7.4 (even df) for
    A = P(|T| <= |t|), with theta = atan(|t| / sqrt(df)), evaluated as the two-tailed complement
    1 - A. For odd df that is (2/pi) [atan(sqrt(df) / |t|) - sin cos (1 + 2/3 cos^2 + ...)], the
    series ending at cos^(df-3) (empty for df = 1); for even df, 1 - sin (1 + 1/2 cos^2 +
    1*3/(2*4) cos^4 + ...), ending at cos^(df-2). Standard library only. The complement is formed by
    subtraction, so it is accurate in absolute terms (about 1e-15), which is what a p-value reported
    to six decimals needs, not in relative terms far in the tail.
    """
    if isinstance(df, bool) or not isinstance(df, int) or df < 1:
        raise ValueError(f"df must be a positive integer, got {df!r}")
    if math.isnan(t):
        raise ValueError("t must be a number")
    x = abs(t)
    if math.isinf(x):
        two_tailed = 0.0
    else:
        root = math.sqrt(df)
        hyp = math.hypot(x, root)
        sin, cos = x / hyp, root / hyp
        cos2 = cos * cos
        series, term = 0.0, 1.0
        if df % 2:
            for k in range(1, (df - 1) // 2 + 1):
                series += term
                term *= cos2 * (2 * k) / (2 * k + 1)
            two_tailed = 2.0 / math.pi * (math.atan2(root, x) - sin * cos * series)
        else:
            for k in range(1, df // 2 + 1):
                series += term
                term *= cos2 * (2 * k - 1) / (2 * k)
            two_tailed = 1.0 - sin * series
        two_tailed = min(max(two_tailed, 0.0), 1.0)
    return 0.5 * two_tailed if t >= 0 else 1.0 - 0.5 * two_tailed


def promotion_test(entries: list[Entry], model_version: str = PROMOTION_MODEL_VERSION,
                   challenger_key: str | None = PROMOTION_CHALLENGER) -> dict[str, Any]:
    """The promotion rule (PROMOTION_RULE), computed once on its fixed sample (module docstring).

    The pair defaults to the declared one, and the result names it, with the resolved entries left
    out of it by reason (promotion_exclusions). The loss difference is QLIKE(model of record) -
    QLIKE(challenger), so a positive mean favours the challenger. The Diebold-Mariano statistic is
    the mean over its standard error from the sample variance (n - 1 denominator), with no
    autocovariance terms because the windows do not overlap; for one-step forecasts that is the
    Harvey-Leybourne-Newbold statistic, and the one-sided p-value is P(T >= stat) for
    T ~ Student t(n - 1), as they recommend.
    """
    sample = promotion_sample(entries, model_version, challenger_key)
    result: dict[str, Any] = {"status": "accruing", "n_independent_paired": len(sample), "required": PROMOTION_WEEKS,
                              "dm_stat": None, "p_value": None, "mean_qlike_difference": None,
                              "model_version": model_version, "challenger_key": challenger_key,
                              **promotion_exclusions(entries, model_version, challenger_key)}
    if len(sample) < PROMOTION_WEEKS:
        return result
    diffs = [qlike(e["realized_pct"], e["forecast_pct"]) - qlike(e["realized_pct"], e["challenger"]["forecast_pct"])
             for e in sample]
    n = len(diffs)
    mean = sum(diffs) / n
    variance = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    result["mean_qlike_difference"] = round(mean, 6)
    promote = False
    if variance > 0:                     # zero only if every difference is identical: nothing to test
        stat = mean / math.sqrt(variance / n)
        p_value = student_t_sf(stat, n - 1)
        result["dm_stat"], result["p_value"] = round(stat, 4), round(p_value, 6)
        promote = mean > 0 and p_value < PROMOTION_ALPHA
    result["status"] = "promote" if promote else "keep"
    return result


def next_result_due(entries: list[Entry]) -> str | None:
    """The earliest target_end (ISO date) of a pending entry that resolution will score, or None.

    Pending entries whose windows span a roll are left out: resolution skips them with the same
    spans_roll test.
    """
    due = [e["target_end"] for e in entries if e["status"] == "pending"
           and not spans_roll(_iso_date(e["base_session"]), _iso_date(e["target_end"]))]
    return min(due) if due else None


# The forecaster's inputs a `recent` row repeats (live.inputs as recorded), so the dashboard can show
# how they moved from one call to the next. Numbers are vol points; ovx_session is the OVX print's date.
_RECENT_INPUT_NUMBERS = ("ovx", "rv5_pct", "rv22_pct", "rv66_pct")


def _recent_inputs(raw: object) -> dict[str, Any] | None:
    """The subset of an entry's recorded `inputs` a `recent` row carries, or None when it has none."""
    if not isinstance(raw, dict):
        return None
    try:
        session: str | None = _iso_date(raw.get("ovx_session")).isoformat()
    except ValueError:
        session = None
    return {**{key: _number(raw.get(key)) for key in _RECENT_INPUT_NUMBERS}, "ovx_session": session}


def _recent(entry: Entry) -> dict[str, Any]:
    """One `recent` row: what the entry already stores, for the dashboard. Entries recorded before a
    field existed (no `inputs` or fit, no seal yet) give null for it. `spans_roll` is the test resolution
    skips a window with, so a pending call that will never be scored can say so before it is closed."""
    challenger = entry.get("challenger") or {}
    return {
        "as_of": entry["as_of"], "issued_at": entry["issued_at"], "recorded_at": entry["recorded_at"],
        "sealed_at": entry.get("sealed_at"), "model_version": entry["model_version"],
        "base_session": entry["base_session"], "target_start": entry["target_start"], "target_end": entry["target_end"],
        "forecast_pct": entry["forecast_pct"], "median_pct": entry.get("median_pct"),
        "lo80_pct": entry.get("lo80_pct"), "hi80_pct": entry.get("hi80_pct"),
        "prob_vol_rises_pct": entry.get("prob_vol_rises_pct"),
        "challenger_pct": challenger.get("forecast_pct"),
        "realized_pct": entry.get("realized_pct") if entry["status"] == "resolved" else None,
        "inputs": _recent_inputs(entry.get("inputs")),
        "coefficients": entry.get("coefficients"), "smearing_factor": entry.get("smearing_factor"),
        "spans_roll": spans_roll(_iso_date(entry["base_session"]), _iso_date(entry["target_end"])),
        "status": entry["status"],
    }


def direction_note(noise_floor_pct: object = None) -> str:
    """The direction-accuracy caveat, quoting the validation's own noise floor when the payload has it."""
    floor = _number(noise_floor_pct)
    level = f"{floor:.1f}%" if floor is not None else "about three calls in four"
    return (f"Direction accuracy is not evidence of skill: on simulated returns with nothing to forecast, "
            f"the backtest's noise floor is {level}, because a five-session realized vol is a noisy "
            f"estimate that reverts toward its mean. QLIKE is the skill measure.")


def summarize(record: Record, noise_floor_pct: object = None) -> dict[str, Any]:
    """The dashboard summary (vol_forecast.live_record in docs/DATA_CONTRACT.md).

    Every statistic stays null until a resolved entry it covers exists. The top-level counts cover
    the whole record. `champion` scores the resolved entries of the pinned model version
    (PROMOTION_MODEL_VERSION; `n` of them), whatever the newest entry's version: a fallback forecast
    from another model is not pooled with it. `challenger` compares the pinned challenger
    (PROMOTION_CHALLENGER) with the model of record on the resolved entries of exactly that pair;
    `promotion` is the promotion rule's single test on the same pair. `chain` counts the entries that
    carry a hash-chain link and lists the as_of of any broken link (chain_breaks). `next_result_due` is the date
    the next score can arrive (next_result_due()). `recent` lists up to RECENT_ENTRIES entries,
    newest first, each with its model_version, its recording and seal times and the inputs it was
    recorded with (_recent()), so a reader can check each seal against the window it opened.
    """
    entries = record["entries"]
    resolved = [e for e in entries if e["status"] == "resolved"]
    champion = [e for e in resolved if e["model_version"] == PROMOTION_MODEL_VERSION]
    with_interval = [e for e in champion if e.get("lo80_pct") is not None]
    with_direction = [e for e in champion if e.get("direction") in DIRECTIONS and e.get("rv5_pct") is not None]
    paired = [e for e in champion if _pair(e)[1] == PROMOTION_CHALLENGER]
    return {
        "started": record["started"],
        "n_recorded": len(entries),
        "n_resolved": len(resolved),
        "n_pending": sum(e["status"] == "pending" for e in entries),
        "n_skipped_roll": sum(e["status"] == "skipped_roll" for e in entries),
        "n_expired": sum(e["status"] == "expired" for e in entries),
        "n_late_commit": sum(e["status"] == "late_commit" for e in entries),
        "n_independent": len(independent_entries(resolved)),
        "next_result_due": next_result_due(entries),
        "champion": {
            "model_version": PROMOTION_MODEL_VERSION,
            "n": len(champion),
            "qlike": _mean([qlike(e["realized_pct"], e["forecast_pct"]) for e in champion], 4),
            "interval_80_coverage_pct": _share_pct(
                [e["lo80_pct"] <= e["realized_pct"] <= e["hi80_pct"] for e in with_interval]),
            "direction_hit_pct": _share_pct(
                [(e["realized_pct"] > e["rv5_pct"]) == (e["direction"] == "RISING") for e in with_direction]),
            "mean_forecast_pct": _mean([e["forecast_pct"] for e in champion], 2),
            "mean_realized_pct": _mean([e["realized_pct"] for e in champion], 2),
        },
        "challenger": {
            "key": PROMOTION_CHALLENGER,
            "qlike": _mean([qlike(e["realized_pct"], e["challenger"]["forecast_pct"]) for e in paired], 4),
            # Descriptive: both models on every paired resolved entry, overlapping windows included.
            # The promotion decision is `promotion`, on independent windows only.
            "n": len(paired),
            "champion_qlike": _mean([qlike(e["realized_pct"], e["forecast_pct"]) for e in paired], 4),
        },
        "promotion": promotion_test(entries),
        # The hash chain (module docstring, CHAIN): how many entries carry a link, and where one breaks.
        "chain": {"n_linked": sum(e.get("previous_sha256") is not None for e in entries),
                  "breaks": chain_breaks(entries)},
        "recent": [_recent(e) for e in reversed(entries[-RECENT_ENTRIES:])],
        "promotion_rule": PROMOTION_RULE,
        "note": WINDOW_NOTE,
        "direction_note": direction_note(noise_floor_pct),
    }


# --------------------------------------------------------------------------- one CI cycle

def _snapshot_time(payload: Payload, now: datetime) -> datetime:
    """When the payload's bars were current: its freeze time, never later than this run."""
    try:
        return min(_aware_utc(payload.get("frozen_at")), now)
    except (TypeError, ValueError):
        return now


def update(record: Record, payload: Payload, now: datetime) -> list[str]:
    """Record the payload's forecast and settle what can be settled. Mutates `record`."""
    messages = [record_forecast(record, payload, now)]
    cutoff = bars_cutoff(_snapshot_time(payload, now))
    messages += resolve_entries(record, completed_closes(payload, cutoff), cutoff, now)
    return messages


def _noise_floor(payload: Payload) -> object:
    validation = _dict(_dict(payload.get("vol_forecast")).get("validation"))
    return _dict(validation.get("direction_noise_floor")).get("dir_acc_pct_mean")


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record + resolve the live vol forecast track record.")
    parser.add_argument("--data", default="public/data.json", help="frozen payload (public/data.json)")
    parser.add_argument("--record", default="data/vol_track_record.json", help="the record file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="write an empty record (refuses to overwrite)")
    mode.add_argument("--seal", action="store_true",
                      help="stamp sealed_at on the entries not yet sealed, immediately before the record is committed")
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    record_path = data_json_path(args.record)
    if record_path.name != "vol_track_record.json":
        parser.error("--record must name data/vol_track_record.json")

    if args.init:
        if record_path.exists() or record_path.is_symlink():
            print(f"vol_record: {args.record} already exists; refusing to overwrite a record", file=sys.stderr)
            return 1
        record = init_record(now)
        save_record(record, record_path)
        print(json.dumps(record, indent=2))
        return 0

    if args.seal:
        record = load_record(record_path)
        messages = seal_entries(record, now)
        for message in messages:
            print(f"vol_record: {message}")
        if messages:
            save_record(record, record_path)
        else:
            print("vol_record: every entry is already sealed; file left untouched")
        return 0

    data_path = public_json_path(args.data)
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("the payload must be a JSON object")
    record = load_record(record_path)
    before = json.dumps(record, sort_keys=True)
    for message in update(record, payload, now):
        print(f"vol_record: {message}")
    if json.dumps(record, sort_keys=True) != before:
        save_record(record, record_path)
    else:
        print("vol_record: no record changes; file left untouched")

    summary = summarize(record, _noise_floor(payload))
    block = payload.get("vol_forecast")
    if isinstance(block, dict):
        if block.get("live_record") != summary:
            block["live_record"] = summary
            _atomic_write(data_path, json.dumps(payload, separators=(",", ":")))    # compact, as freeze.py writes it
    else:
        print("vol_record: the payload has no vol_forecast block; summary not attached")
    champion, promotion = summary["champion"], summary["promotion"]
    print(f"vol_record: {summary['n_recorded']} recorded, {summary['n_resolved']} resolved "
          f"({summary['n_independent']} independent), {summary['n_pending']} pending, "
          f"{summary['n_skipped_roll']} skipped (roll), {summary['n_expired']} expired, "
          f"{summary['n_late_commit']} late commits; QLIKE {champion['qlike']} ({champion['model_version']}, "
          f"n {champion['n']}); promotion {promotion['model_version']} vs {promotion['challenger_key']} "
          f"{promotion['status']} ({promotion['n_independent_paired']}/{promotion['required']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
