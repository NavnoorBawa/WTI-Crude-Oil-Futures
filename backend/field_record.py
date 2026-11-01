"""The shadow log of the whole benchmark field: unsealed, descriptive (docs/ROADMAP.md, Stage 1, item 8).

Only the pinned pair is recorded live (backend/vol_record.py). This log keeps, for each published session
whose fresh download reproduces the published forecast, the live forecast of every model in the lab's
field (vol_lab.FEASIBLE, never the timing control), so that in a few years one can ask whether the lab's
ranking held out of sample for the other models too. A session that never passes that check while it is
the published one stays unlogged. It is
NOT sealed evidence: no seal, no hash chain, no promotion test reads it, and the pinned pair's
promotion test (vol_record.promotion_test) is the only test of har-iv/2 against har_iv_range. A
comparison drawn from this log is descriptive; it must never be read as a second look at that pair.

The file is JSON Lines (FIELD_FILE on the `field-data` branch), append-only, one canonical JSON object
per line (sorted keys, compact, ASCII, no NaN), of two kinds:
- forecast: {kind, as_of, issued_at, appended_at, model_key, forecast_pct, median_pct, inputs_sha256,
  ovx_source, code_sha, spec_sha256, base_session, target_start, target_end, sealed: false}. The window
  is the record's rule applied when the line is appended (vol_record.target_window of the later of
  issued_at and appended_at, plus COMMIT_MARGIN), so it can be checked from the file alone; every line
  for a session must share one window (a line whose rule would give another is refused), so the field's
  losses for one session line up. inputs_sha256 hashes the exact inputs
  (dates, returns, aligned OVX, OHLC), never outputs, so two lines are comparable when their as_of,
  inputs_sha256 and spec_sha256 (backend/vol_lab.py and backend/vol_forecast.py) agree.
- outcome: {kind, base_session, status, closes, realized_pct, resolved_at, sealed: false}, one per
  window, by the record's resolution rule (vol_record: resolved on the six closes, skipped_roll,
  expired), from the payload's closes, with the record's cutoff (bars_cutoff of the freeze time, never
  later than the run). A window that opened before the payload's oldest close is never marked expired:
  its closes have only scrolled out of the payload.

Lines are in time order (appended_at, resolved_at) and forecast sessions never go back; validate() checks
from the file alone every rule append() applies, except what needs the payload (which closes it held).

First issuance per (as_of, model_key) wins; a line is never rewritten. Commands:
    --open-keys  the models still to log for the payload's session (standard library)
    --issue      today's lines for those models (downloads market data and needs the models' packages:
                 the read-only build side)
    --append     the merged file and its new outcomes (standard library: the write-token side)
    --validate   a file, and that an earlier file is a byte prefix of it (standard library)
Every file named must exist (an empty file is an empty log).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import vol_record as vr  # noqa: E402  (standard library only)
from backend.contract_calendar import business_days_between, is_business_day, spans_roll  # noqa: E402

FIELD_FILE = "vol_field_record.jsonl"
# The lab's field without the timing control (vol_lab.FEASIBLE; tests/test_field_record.py keeps them equal).
FIELD_KEYS = ("persistence", "raw_ovx", "ewma", "garch", "gjr", "har_rv", "har_rs", "har_range", "ovx_only",
              "har_iv", "har_iv_range", "lgbm", "combo")
FORECAST_KEYS = {"kind", "as_of", "issued_at", "appended_at", "model_key", "forecast_pct", "median_pct",
                 "inputs_sha256", "ovx_source", "code_sha", "spec_sha256", "base_session", "target_start",
                 "target_end", "sealed"}
ISSUED_KEYS = FORECAST_KEYS - {"appended_at", "base_session", "target_start", "target_end"}
OUTCOME_KEYS = {"kind", "base_session", "status", "closes", "realized_pct", "resolved_at", "sealed"}
OUTCOME_STATUSES = ("resolved", "skipped_roll", "expired")
SPEC_FILES = ("backend/vol_lab.py", "backend/vol_forecast.py")

Line = dict[str, Any]


def canonical(line: Line) -> str:
    return json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _reject(token: str) -> Any:
    raise ValueError(f"non-finite number {token}")


def _rows(text: str) -> list[str]:
    rows = text.split("\n")
    if rows[-1] == "":
        rows.pop()
    return rows


def parse(text: str) -> list[Line]:
    """The lines of a log (an empty text is an empty log). Raises ValueError on a blank line or a line that is
    not a JSON object; lines end with "\n" only."""
    lines = []
    for number, raw in enumerate(_rows(text), 1):
        if not raw:
            raise ValueError(f"line {number} is blank")
        line = json.loads(raw, parse_constant=_reject)
        if not isinstance(line, dict):
            raise ValueError(f"line {number} is not a JSON object")
        lines.append(line)
    return lines


def _forecasts(lines: list[Line]) -> list[Line]:
    return [line for line in lines if line.get("kind") == "forecast"]


def _session(value: object) -> date:
    """A session date written YYYY-MM-DD that is a business day; ValueError otherwise."""
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"{value!r} is not a YYYY-MM-DD date")
    day = date.fromisoformat(value)
    if not is_business_day(day):
        raise ValueError(f"{value} is not a business day")
    return day


def _span(moment: datetime) -> tuple[str, str, str]:
    """The record's window for a line issued or appended at `moment` (vol_record.target_window, + COMMIT_MARGIN)."""
    window = vr.target_window(moment + vr.COMMIT_MARGIN)
    return (window["base_session"].isoformat(), window["target_start"].isoformat(), window["target_end"].isoformat())


def open_keys(lines: list[Line], as_of: str) -> list[str]:
    """The field's models not yet logged for `as_of`; none for a session older than the newest logged."""
    logged = _forecasts(lines)
    if logged and as_of < max(line["as_of"] for line in logged):
        return []
    done = {line["model_key"] for line in logged if line["as_of"] == as_of}
    return [key for key in FIELD_KEYS if key not in done]


def spec_sha256(root: Path = PROJECT_ROOT) -> str:
    digest = hashlib.sha256()
    for name in SPEC_FILES:
        digest.update(name.encode("utf-8") + b"\0" + (root / name).read_bytes() + b"\0")
    return digest.hexdigest()


# --------------------------------------------------------------------------- the build side (read-only)

def inputs_sha256(r: Any, dates: Any, ovx: Any, ohlc: Any) -> str:
    """SHA-256 of the exact inputs every model refits on: dates, returns, aligned OVX and OHLC (NaN as null)."""
    def num(value: Any) -> Any:
        value = float(value)
        return repr(value) if math.isfinite(value) else None

    digest = hashlib.sha256()
    digest.update(json.dumps([str(day.date()) for day in dates]).encode())
    digest.update(json.dumps([num(v) for v in r]).encode())
    digest.update(json.dumps([num(v) for v in (ovx if ovx is not None else [])]).encode())
    if ohlc is not None:
        for column in ("Open", "High", "Low", "Close"):
            digest.update(json.dumps([num(v) for v in ohlc[column].to_numpy()]).encode())
    return digest.hexdigest()


def issue(payload: dict[str, Any], lines: list[Line], now: datetime, code_sha: str | None) -> tuple[list[Line], str]:
    """Today's forecast lines for the field's open models, or ([], why there are none).

    One fresh download (vol_forecast._load_data_full) must give what the published forecast used: the
    same session and recent closes, and the lab's har_iv and har_iv_range must reproduce the published
    har-iv/2 forecast and challenger (to the payload's rounding). A model that fails gets no line, so a
    later run can still log it.
    """
    from backend import vol_forecast as vf
    from backend import vol_lab

    live = vr._dict(vr._dict(payload.get("vol_forecast")).get("live"))
    as_of = live.get("as_of")
    if not isinstance(as_of, str):
        return [], "the payload carries no live forecast"
    keys = open_keys(lines, as_of)
    if not keys:
        return [], f"every model is already logged for {as_of} (or a later session is)"
    r, dates, ovx, meta = vf._load_data_full()
    if str(dates[-1].date()) != as_of:
        return [], f"the download ends {dates[-1].date()}, not at the published session {as_of}"
    if meta.get("recent_closes") != vr._dict(payload.get("vol_forecast")).get("recent_closes"):
        return [], "the download's recent closes differ from the published ones"
    if vf._pick_ovx(ovx) is None or meta.get("ohlc") is None:
        return [], "no usable OVX history or OHLC bars for the field"
    published = {"har_iv": live.get("forecast_next_week_vol_annualized_pct"),
                 "har_iv_range": vr._dict(live.get("challenger")).get("forecast_next_week_vol_annualized_pct")}
    digest, spec, out = inputs_sha256(r, dates, ovx, meta["ohlc"]), spec_sha256(), []
    forecasts: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for key in FIELD_KEYS:
        if key in keys or key in published:
            try:
                forecasts[key] = vol_lab.live_model_forecast(key, r, dates, cast(Any, ovx), meta["ohlc"])
            except Exception:  # noqa: BLE001 - a model that fails is left for a later run
                failed.append(key)
    for key, value in published.items():
        if value is not None and key in forecasts and forecasts[key]["forecast_next_week_vol_annualized_pct"] != value:
            return [], f"the lab's {key} gives {forecasts[key]['forecast_next_week_vol_annualized_pct']}, not the published {value}"
    for key in keys:
        if key in forecasts:
            out.append({"kind": "forecast", "as_of": as_of, "issued_at": vr._stamp(now), "model_key": key,
                        "forecast_pct": forecasts[key]["forecast_next_week_vol_annualized_pct"],
                        "median_pct": forecasts[key]["forecast_median_pct"], "inputs_sha256": digest,
                        "ovx_source": meta.get("ovx_source"), "code_sha": code_sha, "spec_sha256": spec,
                        "sealed": False})
    return out, (f"{len(out)} of {len(keys)} open models logged for {as_of}"
                 + (f" ({', '.join(failed)} failed and stay open)" if failed else ""))


# --------------------------------------------------------------------------- the write side (standard library)

def _window_of(lines: list[Line], as_of: str) -> tuple[str, str, str] | None:
    for line in _forecasts(lines):
        if line["as_of"] == as_of:
            return line["base_session"], line["target_start"], line["target_end"]
    return None


def _outcome(base_session: str, target_end: str, closes: dict[date, float], cutoff: date, oldest: date | None,
             now: datetime) -> Line | None:
    base, end = date.fromisoformat(base_session), date.fromisoformat(target_end)
    line: Line = {"kind": "outcome", "base_session": base_session, "resolved_at": vr._stamp(now), "sealed": False,
                  "closes": None, "realized_pct": None}
    if spans_roll(base, end):
        return {**line, "status": "skipped_roll"}
    sessions = vr.window_sessions(base)
    if all(day in closes for day in sessions):
        prices = [closes[day] for day in sessions]
        return {**line, "status": "resolved", "closes": [[d.isoformat(), p] for d, p in zip(sessions, prices)],
                "realized_pct": round(vr.realized_vol_pct(prices), 4)}
    if oldest is not None and base >= oldest and business_days_between(end, cutoff) > vr.MAX_WAIT_SESSIONS:
        return {**line, "status": "expired"}
    return None


def append(old_text: str, new_lines: list[Line], payload: dict[str, Any], now: datetime) -> tuple[str, list[str]]:
    """The log with the new forecast lines (windowed now) and every window that can be settled; the old text
    is kept byte for byte at the head. Returns (text, one message per line added or refused)."""
    lines = parse(old_text)
    if old_text and not old_text.endswith("\n"):
        raise ValueError("the log does not end with a newline")
    messages: list[str] = []
    added: list[Line] = []
    for line in new_lines:
        if set(line) != ISSUED_KEYS or line.get("kind") != "forecast" or line.get("sealed") is not False:
            messages.append("refused a line that is not an issued forecast line")
            continue
        key, as_of = line["model_key"], line["as_of"]
        try:
            _session(as_of)
            issued_at = vr._aware_utc(line["issued_at"])
        except (TypeError, ValueError):
            messages.append(f"refused {as_of!r} {key!r}: a malformed session or issue time")
            continue
        span = _span(max(issued_at, now))
        fixed = _window_of(lines + added, as_of)
        if key not in FIELD_KEYS:
            messages.append(f"refused {key!r}: not in the field")
        elif issued_at > now + vr.MAX_CLOCK_SKEW:
            messages.append(f"refused {as_of} {key}: issued in the future")
        elif key not in open_keys(lines + added, as_of):
            messages.append(f"refused {as_of} {key}: already logged, or an older session")
        elif not as_of < span[0]:
            messages.append(f"refused {as_of} {key}: its window would open at or before its own session")
        elif fixed is not None and fixed != span:
            messages.append(f"refused {as_of} {key}: its window {span[0]} is not the session's {fixed[0]}")
        else:
            added.append({**line, "appended_at": vr._stamp(now), "base_session": span[0], "target_start": span[1],
                          "target_end": span[2]})
            messages.append(f"logged {as_of} {key}: {line['forecast_pct']}%")
    cutoff = vr.bars_cutoff(vr._snapshot_time(payload, now))
    closes = vr.completed_closes(payload, cutoff)
    oldest = min(closes) if closes else None
    settled = {line["base_session"] for line in lines + added if line.get("kind") == "outcome"}
    windows = {line["base_session"]: line["target_end"] for line in _forecasts(lines + added)}
    for base_session, target_end in sorted(windows.items()):
        if base_session in settled:
            continue
        outcome = _outcome(base_session, target_end, closes, cutoff, oldest, now)
        if outcome is not None:
            added.append(outcome)
            messages.append(f"window from {base_session}: {outcome['status']}")
    text = old_text + "".join(canonical(line) + "\n" for line in added)
    errors = validate(text, previous=old_text)
    if errors:
        raise ValueError("the merged log is invalid: " + "; ".join(errors[:5]))
    return text, messages


def _hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and not set(value) - set("0123456789abcdef")


def _positive_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


class _Walk:
    """validate()'s state while it walks the lines in file order."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.seen: set[tuple[str, str]] = set()
        self.windows: dict[str, tuple[str, str, str]] = {}   # as_of -> its window
        self.ends: dict[str, str] = {}                       # base_session -> target_end, of logged windows
        self.outcomes: set[str] = set()
        self.newest: str | None = None                       # the newest forecast session so far
        self.clock: datetime | None = None                   # the latest appended_at or resolved_at so far

    def tick(self, where: str, moment: datetime) -> None:
        if self.clock is not None and moment < self.clock - vr.MAX_CLOCK_SKEW:
            self.errors.append(f"{where}: written before the line above it (lines are in time order)")
        self.clock = moment if self.clock is None else max(self.clock, moment)

    def forecast(self, where: str, line: Line) -> None:
        errors = self.errors
        if set(line) != FORECAST_KEYS:
            errors.append(f"{where}: a forecast line has exactly {sorted(FORECAST_KEYS)}")
            return
        as_of, key = line["as_of"], line["model_key"]
        _session(as_of)
        if key not in FIELD_KEYS:
            errors.append(f"{where}: {key!r} is not in the field")
        if (as_of, key) in self.seen:
            errors.append(f"{where}: {as_of} {key} logged twice")
        self.seen.add((as_of, key))
        if self.newest is not None and as_of < self.newest:
            errors.append(f"{where}: {as_of} is logged after the later session {self.newest}")
        self.newest = max(self.newest or as_of, as_of)
        span = (line["base_session"], line["target_start"], line["target_end"])
        if self.windows.setdefault(as_of, span) != span:
            errors.append(f"{where}: a second window for {as_of}")
        issued_at, appended_at = vr._aware_utc(line["issued_at"]), vr._aware_utc(line["appended_at"])
        if issued_at > appended_at + vr.MAX_CLOCK_SKEW:
            errors.append(f"{where}: issued after it was appended")
        if span != _span(max(issued_at, appended_at)) or not as_of < span[0]:
            errors.append(f"{where}: its window does not follow the record's rule at its issue and append times")
        self.tick(where, appended_at)
        self.ends.setdefault(span[0], span[2])
        for name in ("forecast_pct", "median_pct"):
            if not _positive_number(line[name]):
                errors.append(f"{where}: {name} must be a positive number")
        if not (_hex(line["inputs_sha256"], 64) and _hex(line["spec_sha256"], 64)
                and (line["code_sha"] is None or _hex(line["code_sha"], 40))
                and (line["ovx_source"] is None or isinstance(line["ovx_source"], str))):
            errors.append(f"{where}: inputs_sha256 and spec_sha256 are SHA-256 digests, code_sha a commit or null, "
                          "ovx_source a string or null")

    def outcome(self, where: str, line: Line) -> None:
        errors = self.errors
        if set(line) != OUTCOME_KEYS or line["status"] not in OUTCOME_STATUSES:
            errors.append(f"{where}: an outcome line has exactly {sorted(OUTCOME_KEYS)} and a known status")
            return
        base_session, status = line["base_session"], line["status"]
        if base_session not in self.ends:
            errors.append(f"{where}: an outcome for {base_session!r}, which no line above opened")
            return
        if base_session in self.outcomes:
            errors.append(f"{where}: a second outcome for the window from {base_session}")
        self.outcomes.add(base_session)
        resolved_at = vr._aware_utc(line["resolved_at"])
        self.tick(where, resolved_at)
        base, end = date.fromisoformat(base_session), date.fromisoformat(self.ends[base_session])
        if (status == "skipped_roll") != spans_roll(base, end):
            errors.append(f"{where}: a window is skipped_roll exactly when it spans a contract roll")
        if status != "resolved":
            if line["closes"] is not None or line["realized_pct"] is not None:
                errors.append(f"{where}: only a resolved outcome carries closes and realized_pct")
            if status == "expired" and not business_days_between(end, resolved_at.date()) > vr.MAX_WAIT_SESSIONS:
                errors.append(f"{where}: expired before {vr.MAX_WAIT_SESSIONS} sessions had passed after the window")
            return
        closes = line["closes"]
        days = [day.isoformat() for day in vr.window_sessions(base)]
        if (not isinstance(closes, list) or len(closes) != len(days)
                or any(not isinstance(pair, list) or len(pair) != 2 or not _positive_number(pair[1]) for pair in closes)
                or [pair[0] for pair in closes] != days):
            errors.append(f"{where}: a resolved window needs its six positive closes, dated {days[0]} to {days[-1]}")
        elif line["realized_pct"] != round(vr.realized_vol_pct([pair[1] for pair in closes]), 4):
            errors.append(f"{where}: realized_pct does not recompute from its closes")
        if resolved_at <= vr.settlement_time(end):
            errors.append(f"{where}: resolved before its last session settled")


def validate(text: str, previous: str | None = None) -> list[str]:
    """Every problem with a log, from the file alone: lines canonical and well formed, in time order, sessions
    never going back, each (as_of, model) once with one window per session by the record's rule at its issue
    and append times, one outcome per logged window that agrees with that window (a roll, the six closes
    and their realized_pct, an expiry only after MAX_WAIT_SESSIONS), and `previous` a byte prefix."""
    walk = _Walk()
    errors = walk.errors
    if previous is not None and not text.startswith(previous):
        errors.append("the earlier log is not a prefix of this one: a line was changed or removed")
    if text and not text.endswith("\n"):
        errors.append("the log does not end with a newline")
    try:
        lines = parse(text)
    except ValueError as exc:
        return errors + [str(exc)]
    for number, (line, row) in enumerate(zip(lines, _rows(text)), 1):
        where = f"line {number}"
        try:
            if canonical(line) != row:
                errors.append(f"{where}: not canonical JSON")
        except ValueError:
            errors.append(f"{where}: not finite")
        if line.get("sealed") is not False:
            errors.append(f"{where}: every line says sealed: false")
        try:
            if line.get("kind") == "forecast":
                walk.forecast(where, line)
            elif line.get("kind") == "outcome":
                walk.outcome(where, line)
            else:
                errors.append(f"{where}: unknown kind {line.get('kind')!r}")
        except (TypeError, ValueError, KeyError, IndexError, AttributeError) as exc:
            errors.append(f"{where}: malformed ({type(exc).__name__}: {exc})")
    return errors


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    for flag in ("--open-keys", "--issue", "--append", "--validate"):
        action.add_argument(flag, action="store_true")
    parser.add_argument("--payload", help="the frozen data.json")
    parser.add_argument("--log", help="the current log (it must exist; an empty file: none yet)")
    parser.add_argument("--new", help="--append: the issued lines")
    parser.add_argument("--out", help="where to write the result")
    parser.add_argument("--previous", help="--validate: an earlier copy that must be a byte prefix")
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)

    def read(path: str | None, flag: str) -> str:
        if not path:
            raise ValueError(f"{flag} is required here")
        return Path(path).read_text(encoding="utf-8")   # a missing file is an error, never an empty log

    def out(path: str | None) -> Path:
        if not path:
            raise ValueError("--out is required here")
        return Path(path)

    try:
        if args.validate:
            errors = validate(read(args.log, "--log"), read(args.previous, "--previous") if args.previous else None)
            for error in errors:
                print(f"FAIL {error}")
            print("OK" if not errors else f"{len(errors)} problem(s)")
            return 1 if errors else 0
        payload = json.loads(read(args.payload, "--payload"))
        if not isinstance(payload, dict):
            raise ValueError("the payload is not a JSON object")
        lines = parse(read(args.log, "--log"))
        if args.open_keys:
            as_of = vr._dict(vr._dict(payload.get("vol_forecast")).get("live")).get("as_of")
            keys = open_keys(lines, as_of) if isinstance(as_of, str) else []
            print(len(keys))
            return 0
        if args.issue:
            new, why = issue(payload, lines, now, os.environ.get("GITHUB_SHA") or None)
            out(args.out).write_text("".join(canonical(line) + "\n" for line in new), encoding="utf-8")
            print(why)
            return 0
        text, messages = append(read(args.log, "--log"), parse(read(args.new, "--new")), payload, now)
        out(args.out).write_text(text, encoding="utf-8")
        print("\n".join(messages) or "nothing to add")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"field log: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
