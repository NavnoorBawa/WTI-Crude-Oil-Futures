#!/usr/bin/env python3
"""Whether the live pipeline needs a person, from copies of what it published (docs/ROADMAP.md, Stage 1, item 5).

.github/workflows/health.yml runs this daily and after every Refresh and Reproduce run, on
`gh api` copies of live-data's runtime-state/vol_track_record.json and gh-pages' data.json and on
`gh run list` output for the two workflows, and keeps one GitHub issue open while it reports a
problem. A problem is something that does not fix itself and that a person should look at:

- the record does not load under vol_record.load_record's contract;
- a session's entry is missing although its forecast was due. NYMEX session d is due DUE_GRACE
  after the midnight ET that follows it: from that midnight on, the record's clock rule
  (contract_calendar.last_completed_bar_date) counts d's bar as finished at every moment, and the
  four-hourly refresh has had at least two chances. (Between d's 17:00 ET close and midnight that
  rule falls back to the previous session at the 18:00 ET reopen, so counting from the close would
  report a problem and then withdraw it.) A missed session cannot be recorded later (the first
  issuance stands and the record only grows), so it is reported for LOOKBACK_DAYS;
- an entry was closed late_commit (not sealed in time, never scored) or expired (its closes never
  arrived) in the last LOOKBACK_DAYS;
- a pending entry's result is overdue: its window's last session was due, by the same rule;
- the deployed payload's frozen_at is more than STALE_HOURS old;
- the last REFRESH_FAILURES completed Refresh runs on main all failed (one failure is usually a
  superseded build, which the next run replaces), or the latest completed Reproduce run failed
  (docs/ROADMAP.md, Stage 0: a red rebuild is a research incident).

It reads copies only: it never writes the record, never seals, and never touches data/. Exit status
0 when healthy, 1 when it reports a problem, 2 on a usage error. With --markdown it writes the
issue body, which ends in a digest of the problems so the workflow comments only when they change.
Standard library only (backend.vol_record and backend.contract_calendar are standard library only).

Usage:
    python scripts/record_health.py --record record.json --payload data.json \\
        [--refresh-runs refresh.json] [--reproduce-runs reproduce.json] [--now 2026-10-30T18:00:00Z] \\
        [--markdown health.md]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import vol_record as vr  # noqa: E402  (standard library only, needs the path above)
from backend.contract_calendar import EXCHANGE_TZ, is_business_day, spans_roll  # noqa: E402

DUE_GRACE = timedelta(hours=12)      # two to three scheduled refreshes after a bar is finished
STALE_HOURS = 12                     # the site refreshes every four hours on GitHub's best-effort schedule
LOOKBACK_DAYS = 7                    # how long a permanent incident (a missed session, a late seal) is reported
REFRESH_FAILURES = 2                 # consecutive failed Refresh runs that need a person
FAILED = {"failure", "timed_out", "startup_failure"}


def _utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


def _closed_at(entry: dict[str, Any]) -> datetime | None:
    stamp = entry.get("closed_at") or entry.get("recorded_at")
    return _utc(stamp) if isinstance(stamp, str) else None


def due_through(now: datetime) -> date:
    """The newest session that is due: DUE_GRACE has passed since the midnight ET after it. Never decreases."""
    day = (now - DUE_GRACE).astimezone(EXCHANGE_TZ).date() - timedelta(days=1)
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


def record_problems(record: dict[str, Any], now: datetime) -> list[str]:
    """Problems in a record that loaded: missed sessions, recent late_commit or expired entries, overdue results."""
    problems: list[str] = []
    entries = record["entries"]
    since = (now - timedelta(days=LOOKBACK_DAYS)).date()
    due = due_through(now)

    recorded = {entry["as_of"] for entry in entries}
    day = max(date.fromisoformat(record["started"]), since)
    missing = []
    while day <= due:
        if is_business_day(day) and day.isoformat() not in recorded:
            missing.append(day.isoformat())
        day += timedelta(days=1)
    if missing:
        problems.append(f"No entry for session{'s' if len(missing) > 1 else ''} {', '.join(missing)}: "
                        f"the forecast was due by now, and a missed session cannot be recorded later.")

    for entry in entries:
        closed = _closed_at(entry)
        recent = closed is not None and closed.date() >= since
        if entry["status"] == "late_commit" and recent:
            problems.append(f"Entry {entry['as_of']} is late_commit: it was not sealed more than "
                            f"{int(vr.SEAL_MARGIN.total_seconds() // 60)} minutes before the settlement that opens "
                            f"its window, so it is never scored.")
        elif entry["status"] == "expired" and recent:
            missing_closes = ", ".join(entry.get("missing_sessions") or []) or "unknown sessions"
            problems.append(f"Entry {entry['as_of']} expired: its closes never arrived ({missing_closes}).")
        elif entry["status"] == "pending":
            base, end = date.fromisoformat(entry["base_session"]), date.fromisoformat(entry["target_end"])
            if spans_roll(base, end) or end > due:
                continue
            problems.append(f"Entry {entry['as_of']} is still pending although its window ended on {end}: "
                            f"its result is overdue.")
    return problems


def payload_problems(payload: dict[str, Any], now: datetime) -> list[str]:
    """A deployed payload that is older than STALE_HOURS or carries no usable frozen_at."""
    stamp = payload.get("frozen_at")
    try:
        frozen = _utc(stamp) if isinstance(stamp, str) else None
    except ValueError:
        frozen = None
    if frozen is None:
        return ["The deployed data.json has no readable frozen_at."]
    age = now - frozen
    if age > timedelta(hours=STALE_HOURS):
        return [f"The deployed data.json was frozen at {stamp}, {age.total_seconds() / 3600:.1f} hours ago "
                f"(more than {STALE_HOURS}): the site is not refreshing."]
    return []


def run_problems(name: str, runs: list[dict[str, Any]], failures_needed: int) -> list[str]:
    """The workflow's latest completed runs, newest first (gh run list --json conclusion,url,createdAt)."""
    completed = [run for run in runs if run.get("conclusion") not in (None, "", "cancelled", "skipped")]
    latest = completed[:failures_needed]
    if len(latest) < failures_needed or not all(run.get("conclusion") in FAILED for run in latest):
        return []
    which = "run" if failures_needed == 1 else f"{failures_needed} runs"
    return [f"The last {which} of {name} failed: " + ", ".join(
        f"{run.get('url', 'run')} ({run.get('createdAt', 'unknown time')})" for run in latest) + "."]


def check(record_path: Path, payload_path: Path, now: datetime, refresh_runs: list[dict[str, Any]] | None = None,
          reproduce_runs: list[dict[str, Any]] | None = None) -> list[str]:
    """Every problem, in a fixed order; an empty list means healthy."""
    problems: list[str] = []
    try:
        record = vr.load_record(record_path)
    except ValueError as exc:
        problems.append(f"The live record does not load: {exc}")
    else:
        problems += record_problems(record, now)
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"The deployed data.json is unreadable: {exc}")
    else:
        problems += payload_problems(payload if isinstance(payload, dict) else {}, now)
    if refresh_runs is not None:
        problems += run_problems("Refresh", refresh_runs, REFRESH_FAILURES)
    if reproduce_runs is not None:
        problems += run_problems("Reproduce", reproduce_runs, 1)
    return problems


def digest(problems: list[str]) -> str:
    return hashlib.sha256("\n".join(problems).encode("utf-8")).hexdigest()[:16]


def markdown(problems: list[str], now: datetime) -> str:
    """The issue body: what is wrong, how it was checked, and a digest the workflow compares."""
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    if not problems:
        return f"Healthy at {stamp}.\n"
    lines = [f"The live pipeline needs attention (checked {stamp}):", ""]
    lines += [f"- {problem}" for problem in problems]
    lines += ["", "Checked by `scripts/record_health.py` (`.github/workflows/health.yml`) on copies of the record on "
              "`live-data` and of `data.json` on `gh-pages`. This issue closes itself on the first healthy run.",
              "", f"<!-- health-digest: {digest(problems)} -->"]
    return "\n".join(lines) + "\n"


def _runs(path: str | None) -> list[dict[str, Any]] | None:
    if path is None:
        return None
    runs = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(runs, list):
        raise ValueError(f"{path}: expected a list of runs")
    return runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", required=True, help="a copy of runtime-state/vol_track_record.json")
    parser.add_argument("--payload", required=True, help="a copy of the deployed data.json")
    parser.add_argument("--refresh-runs", help="gh run list --json conclusion,url,createdAt for refresh.yml")
    parser.add_argument("--reproduce-runs", help="the same for reproduce.yml")
    parser.add_argument("--now", help="check as of this UTC time (ISO 8601) instead of the clock: a drill")
    parser.add_argument("--markdown", help="write the issue body here")
    args = parser.parse_args(argv)
    try:
        now = _utc(args.now) if args.now else datetime.now(timezone.utc)
        refresh, reproduce = _runs(args.refresh_runs), _runs(args.reproduce_runs)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    problems = check(Path(args.record), Path(args.payload), now, refresh, reproduce)
    if args.markdown:
        Path(args.markdown).write_text(markdown(problems, now), encoding="utf-8")
    for problem in problems:
        print(f"PROBLEM {problem}")
    print(f"{'healthy' if not problems else f'{len(problems)} problem(s)'} at {now.strftime('%Y-%m-%dT%H:%MZ')}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
