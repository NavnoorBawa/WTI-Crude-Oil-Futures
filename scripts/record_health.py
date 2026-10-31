#!/usr/bin/env python3
"""Whether the live pipeline needs a person, from copies of what it published (docs/ROADMAP.md, Stage 1, item 5).

.github/workflows/health.yml runs this daily and after every Refresh and Reproduce run on main, on
`gh api` copies of live-data's runtime-state/vol_track_record.json and gh-pages' data.json and on
`gh run list` output for the two workflows. It reports two kinds of problem.

Needs a person (the Health run fails until it clears):
- the record does not load under vol_record.load_record's contract, or data.json is unreadable;
- a session's entry is due but missing, and can still be recorded. NYMEX session d is due DUE_GRACE
  after the midnight ET that follows it: from that midnight on, the record's clock rule
  (contract_calendar.last_completed_bar_date) counts d's bar as finished at every moment, and the
  four-hourly refresh has had at least two chances. (Between d's 17:00 ET close and midnight that
  rule falls back to the previous session at the 18:00 ET reopen, so counting from the close would
  report a problem and then withdraw it.) A Refresh run can still record d until a later session
  is recorded (record_forecast keeps the first issuance and only appends newer sessions) or the
  midnight ET after the next session passes (after that, no run produces d again);
- a pending entry's result is overdue: its window's last session was due, by the same rule;
- the deployed payload's frozen_at is more than STALE_HOURS old;
- the last REFRESH_FAILURES completed Refresh runs on main all failed, or the latest completed
  Reproduce run on main failed (docs/ROADMAP.md, Stage 0: a red rebuild is a research incident).
  Runs triggered by pull requests are left out: a fork's branch can also be named main.

Recorded incidents (nothing can undo them; they are reported for LOOKBACK_DAYS so a person sees
them, and closing the issue acknowledges them): a missed session that can no longer be recorded,
and an entry closed late_commit (not sealed in time, never scored) or expired (its closes still
missing MAX_WAIT_SESSIONS sessions after its window) in that period.

Each problem has a stable key (the stale payload's frozen_at, the first failed run of a streak, the
session), and the issue body ends in a digest of the keys, so the workflow edits the issue only
when the set of problems changes, not when an age or a run count does.

It reads copies only: it never writes the record, never seals, and never touches data/. Exit status
0 when healthy, 3 when there are recorded incidents only, 4 when something needs a person, 2 on a
usage error or a failure of the check itself. Standard library only (backend.vol_record and
backend.contract_calendar are standard library only).

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
import traceback
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import vol_record as vr  # noqa: E402  (standard library only, needs the path above)
from backend.contract_calendar import EXCHANGE_TZ, is_business_day, shift_business_days, spans_roll  # noqa: E402

DUE_GRACE = timedelta(hours=12)      # two to three scheduled refreshes after a bar is finished
# GitHub runs the four-hourly Refresh best effort: from August to October 2026 the longest gap
# between successful runs with no failed run in it was 13.1 hours (dropped slots), so a site is
# stale only well past that.
STALE_HOURS = 18
LOOKBACK_DAYS = 7                    # how long a recorded incident is reported
# Single failed Refresh runs happen and the next scheduled run clears them (8 of the 400 runs from
# August to October 2026 failed, in streaks of one to three); two in a row is eight hours without a
# refresh.
REFRESH_FAILURES = 2
FAILED = {"failure", "timed_out", "startup_failure"}
NOT_A_RESULT = {None, "", "cancelled", "skipped", "neutral", "stale"}
PULL_REQUEST_EVENTS = {"pull_request", "pull_request_target"}
HEALTHY, INCIDENTS, NEEDS_A_PERSON, BROKEN = 0, 3, 4, 2


class Problem(NamedTuple):
    key: str            # stable while the problem lasts; the issue's digest is over these
    text: str           # what the issue says, with current details (ages, run links)
    needs_a_person: bool


def _utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


def _closed_at(entry: dict[str, Any]) -> datetime | None:
    stamp = entry.get("closed_at") or entry.get("recorded_at")
    return _utc(stamp) if isinstance(stamp, str) else None


def _midnight_after(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=EXCHANGE_TZ)


def due_through(now: datetime) -> date:
    """The newest session that is due: DUE_GRACE has passed since the midnight ET after it. Never decreases."""
    day = (now - DUE_GRACE).astimezone(EXCHANGE_TZ).date() - timedelta(days=1)
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


def record_problems(record: dict[str, Any], now: datetime) -> list[Problem]:
    """Problems in a record that loaded: missed sessions, recent late_commit or expired entries, overdue results."""
    problems: list[Problem] = []
    entries = record["entries"]
    since = (now - timedelta(days=LOOKBACK_DAYS)).date()
    due = due_through(now)
    newest = max((entry["as_of"] for entry in entries), default=None)

    recorded = {entry["as_of"] for entry in entries}
    day = max(date.fromisoformat(record["started"]), since)
    while day <= due:
        if is_business_day(day) and day.isoformat() not in recorded:
            next_session = shift_business_days(day, 1)
            lost = (newest is not None and newest > day.isoformat()) or now >= _midnight_after(next_session)
            if lost:
                problems.append(Problem(f"lost:{day}", f"Session {day} has no entry and can no longer get one: a "
                                        f"later session is recorded or the run that could have recorded it has "
                                        f"passed.", False))
            else:
                problems.append(Problem(f"missing:{day}", f"Session {day} has no entry although it was due. A "
                                        f"Refresh run before midnight ET after {next_session} can still record it, "
                                        f"if no later session is recorded first.", True))
        day += timedelta(days=1)

    for entry in entries:
        closed = _closed_at(entry)
        recent = closed is not None and closed.date() >= since
        if entry["status"] == "late_commit" and recent:
            problems.append(Problem(f"late_commit:{entry['as_of']}", f"Entry {entry['as_of']} is late_commit: it was "
                                    f"not sealed more than {int(vr.SEAL_MARGIN.total_seconds() // 60)} minutes before "
                                    f"the settlement that opens its window, so it is never scored.", False))
        elif entry["status"] == "expired" and recent:
            missing_closes = ", ".join(entry.get("missing_sessions") or []) or "unknown sessions"
            problems.append(Problem(f"expired:{entry['as_of']}", f"Entry {entry['as_of']} expired: its closes were "
                                    f"still missing {vr.MAX_WAIT_SESSIONS} sessions after its window "
                                    f"({missing_closes}).", False))
        elif entry["status"] == "pending":
            base, end = date.fromisoformat(entry["base_session"]), date.fromisoformat(entry["target_end"])
            if spans_roll(base, end) or end > due:
                continue
            problems.append(Problem(f"overdue:{entry['as_of']}", f"Entry {entry['as_of']} is still pending although "
                                    f"its window ended on {end}: its result is overdue.", True))
    return problems


def payload_problems(payload: dict[str, Any], now: datetime) -> list[Problem]:
    """A deployed payload that is older than STALE_HOURS or carries no usable frozen_at."""
    stamp = payload.get("frozen_at")
    try:
        frozen = _utc(stamp) if isinstance(stamp, str) else None
    except ValueError:
        frozen = None
    if frozen is None:
        return [Problem("payload", "The deployed data.json has no readable frozen_at.", True)]
    age = now - frozen
    if age > timedelta(hours=STALE_HOURS):
        return [Problem(f"stale:{stamp}", f"The deployed data.json was frozen at {stamp}, "
                        f"{age.total_seconds() / 3600:.1f} hours ago (more than {STALE_HOURS}): the site is not "
                        f"refreshing.", True)]
    return []


def run_problems(name: str, runs: list[dict[str, Any]], failures_needed: int) -> list[Problem]:
    """The workflow's runs, newest first (gh run list --json conclusion,url,createdAt,event): a failure streak
    at the head of its completed runs on main, keyed by the streak's first run so it stays one problem."""
    completed = [run for run in runs if run.get("conclusion") not in NOT_A_RESULT
                 and run.get("event") not in PULL_REQUEST_EVENTS]
    streak = []
    for run in completed:
        if run.get("conclusion") not in FAILED:
            break
        streak.append(run)
    if len(streak) < failures_needed:
        return []
    latest, first = streak[0], streak[-1]
    count = f"The last {len(streak)} completed runs" if len(streak) > 1 else "The last completed run"
    return [Problem(f"{name.lower()}:{first.get('url', '')}", f"{count} of {name} on main failed; the latest is "
                    f"{latest.get('url', 'unknown')} ({latest.get('createdAt', 'unknown time')}).", True)]


def check(record_path: Path, payload_path: Path, now: datetime, refresh_runs: list[dict[str, Any]] | None = None,
          reproduce_runs: list[dict[str, Any]] | None = None) -> list[Problem]:
    """Every problem, in a fixed order; an empty list means healthy."""
    problems: list[Problem] = []
    try:
        record = vr.load_record(record_path)
    except ValueError as exc:
        problems.append(Problem("record", f"The live record does not load: {exc}", True))
    else:
        problems += record_problems(record, now)
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(Problem("payload", f"The deployed data.json is unreadable: {exc}", True))
    else:
        problems += payload_problems(payload if isinstance(payload, dict) else {}, now)
    if refresh_runs is not None:
        problems += run_problems("Refresh", refresh_runs, REFRESH_FAILURES)
    if reproduce_runs is not None:
        problems += run_problems("Reproduce", reproduce_runs, 1)
    return problems


def digest(problems: list[Problem]) -> str:
    return hashlib.sha256("\n".join(sorted(p.key for p in problems)).encode("utf-8")).hexdigest()[:16]


def status(problems: list[Problem]) -> int:
    if any(p.needs_a_person for p in problems):
        return NEEDS_A_PERSON
    return INCIDENTS if problems else HEALTHY


def markdown(problems: list[Problem], now: datetime) -> str:
    """The issue body: what needs a person, what was recorded, how it was checked, and the digest."""
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    if not problems:
        return f"Healthy at {stamp}.\n"
    act = [p.text for p in problems if p.needs_a_person]
    seen = [p.text for p in problems if not p.needs_a_person]
    lines = [f"Checked {stamp}."]
    if act:
        lines += ["", "**Needs a person**", ""] + [f"- {text}" for text in act]
    if seen:
        lines += ["", f"**Recorded incidents** (nothing can undo them; they are listed for {LOOKBACK_DAYS} days, and "
                  "closing this issue acknowledges them)", ""] + [f"- {text}" for text in seen]
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
    parser.add_argument("--refresh-runs", help="gh run list --json conclusion,url,createdAt,event for refresh.yml")
    parser.add_argument("--reproduce-runs", help="the same for reproduce.yml")
    parser.add_argument("--now", help="check as of this UTC time (ISO 8601) instead of the clock: a drill")
    parser.add_argument("--markdown", help="write the issue body here")
    args = parser.parse_args(argv)
    try:
        now = _utc(args.now) if args.now else datetime.now(timezone.utc)
        refresh, reproduce = _runs(args.refresh_runs), _runs(args.reproduce_runs)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    try:
        problems = check(Path(args.record), Path(args.payload), now, refresh, reproduce)
        if args.markdown:
            Path(args.markdown).write_text(markdown(problems, now), encoding="utf-8")
    except Exception:                    # a failure of the check itself: no verdict, and no issue is touched
        traceback.print_exc()
        return BROKEN
    for problem in problems:
        print(f"{'NEEDS A PERSON' if problem.needs_a_person else 'INCIDENT'} {problem.text}")
    result = status(problems)
    print({HEALTHY: "healthy", INCIDENTS: "recorded incidents only", NEEDS_A_PERSON: "needs a person"}[result]
          + f" at {now.strftime('%Y-%m-%dT%H:%MZ')}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
