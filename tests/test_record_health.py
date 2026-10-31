"""scripts/record_health.py: each problem it reports, at its threshold, on records made by the real recorder.

Records are built with backend/vol_record.py's own recorder, seal and resolution, written to a
temporary file and read back through vol_record.load_record, as the Health workflow does with its
copy of live-data's record."""

import ast
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from backend import vol_record as vr
from scripts import record_health as rh

UTC = timezone.utc
_SCRATCH = tempfile.TemporaryDirectory()      # every Files() lives under it; removed after the module


def tearDownModule():
    _SCRATCH.cleanup()


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def live_payload(as_of: str, issued_at: str, frozen_at: str | None = None) -> dict:
    return {"frozen_at": frozen_at or issued_at, "contract": {"symbol": "CLZ26"},
            "vol_forecast": {"live": {"forecast_next_week_vol_annualized_pct": 40.0, "forecast_median_pct": 36.0,
                                      "model_version": "har-iv/2", "as_of": as_of, "issued_at": issued_at}}}


def record_with(sessions: list[str], started: str | None = None, seal: bool = True) -> dict:
    """One entry per session, each issued at 05:00 UTC the next calendar day and sealed ten minutes later."""
    record = {"schema_version": 1, "started": started or sessions[0], "entries": []}
    for session in sessions:
        issued = f"{date.fromisoformat(session) + timedelta(days=1)}T05:00:00Z"
        message = vr.record_forecast(record, live_payload(session, issued), at(issued))
        assert message.startswith("recorded"), message
        if seal:
            vr.seal_entries(record, at(issued) + timedelta(minutes=10))
    return record


class Files:
    """The record, payload and run lists as files, as the workflow writes them."""

    def __init__(self, record, payload=None, refresh=None, reproduce=None):
        root = Path(tempfile.mkdtemp(dir=_SCRATCH.name))
        self.root = root
        self.record, self.payload = root / "record.json", root / "data.json"
        self.record.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
        self.payload.write_text(json.dumps(payload if payload is not None else {"frozen_at": "2026-11-04T16:00:00Z"}),
                                encoding="utf-8")
        self.refresh, self.reproduce = refresh, reproduce

    def check(self, now: str) -> list:
        return rh.check(self.record, self.payload, at(now), self.refresh, self.reproduce)

    def keys(self, now: str) -> list[str]:
        return [problem.key for problem in self.check(now)]


def run(conclusion, n, event="schedule"):
    return {"conclusion": conclusion, "url": f"https://example.test/{n}", "createdAt": f"T{n}", "event": event}


class MissedSessionTest(unittest.TestCase):
    def test_every_due_session_recorded_is_healthy(self):
        files = Files(record_with(["2026-11-02", "2026-11-03"]))
        self.assertEqual(files.check("2026-11-04T18:00:00Z"), [])

    def test_a_due_session_that_can_still_be_recorded_needs_a_person(self):
        files = Files(record_with(["2026-11-02"]), payload={"frozen_at": "2026-11-04T16:00:00Z"})
        problems = files.check("2026-11-04T17:01:00Z")
        self.assertEqual([p.key for p in problems], ["missing:2026-11-03"])
        self.assertTrue(problems[0].needs_a_person)
        self.assertIn("can still record it", problems[0].text)

    def test_a_session_is_lost_once_a_later_one_is_recorded_or_its_last_chance_has_passed(self):
        later = Files(record_with(["2026-11-02", "2026-11-04"], started="2026-11-02"),
                      payload={"frozen_at": "2026-11-05T16:00:00Z"})
        problems = later.check("2026-11-05T18:00:00Z")
        self.assertEqual([(p.key, p.needs_a_person) for p in problems], [("lost:2026-11-03", False)])
        # No later entry: Nov 3 can be recorded until midnight ET after Nov 4 (05:00 UTC on Nov 5).
        alone = Files(record_with(["2026-11-02"]), payload={"frozen_at": "2026-11-05T04:00:00Z"})
        self.assertEqual(alone.keys("2026-11-05T04:59:00Z"), ["missing:2026-11-03"])
        self.assertEqual(alone.keys("2026-11-05T05:01:00Z"), ["lost:2026-11-03"])

    def test_a_friday_can_be_recorded_until_monday_midnight(self):
        files = Files(record_with(["2026-11-05"]), payload={"frozen_at": "2026-11-09T20:00:00Z"})
        self.assertEqual(files.keys("2026-11-07T16:59:00Z"), [])                     # due at Saturday noon ET
        self.assertEqual(files.keys("2026-11-07T17:01:00Z"), ["missing:2026-11-06"])
        self.assertEqual(files.keys("2026-11-09T20:00:00Z"), ["missing:2026-11-06"])
        self.assertIn("lost:2026-11-06", files.keys("2026-11-10T05:01:00Z"))

    def test_a_session_is_due_twelve_hours_after_the_midnight_that_follows_it(self):
        files = Files(record_with(["2026-11-02"]))
        # Nov 3's entry is due at noon ET on Nov 4 (17:00 UTC), and from then on: the rule never flaps
        # at the 18:00 ET reopen, where the record's bar-date clock falls back a day.
        for now, due in (("2026-11-04T10:30:00Z", False), ("2026-11-04T16:59:00Z", False),
                         ("2026-11-04T17:01:00Z", True), ("2026-11-04T23:30:00Z", True)):
            with self.subTest(now=now):
                self.assertEqual("missing:2026-11-03" in files.keys(now), due)
        stamps = [at(f"2026-11-0{d}T{h:02d}:00:00Z") for d in (3, 4, 5) for h in range(0, 24, 3)]
        self.assertEqual([rh.due_through(s) for s in stamps], sorted(rh.due_through(s) for s in stamps))

    def test_weekends_and_exchange_holidays_are_not_sessions(self):
        # Thanksgiving 2026 (Nov 26) is a NYMEX holiday; Nov 28-29 is a weekend.
        files = Files(record_with(["2026-11-25", "2026-11-27"]), payload={"frozen_at": "2026-11-30T12:00:00Z"})
        self.assertEqual(files.check("2026-11-30T14:00:00Z"), [])

    def test_a_lost_session_is_an_incident_for_a_week_then_left_to_the_issue_history(self):
        with_newer = record_with(["2026-11-02", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10",
                                  "2026-11-11"])
        files = Files(with_newer, payload={"frozen_at": "2026-11-12T08:00:00Z"})
        self.assertIn("lost:2026-11-03", files.keys("2026-11-09T18:00:00Z"))
        self.assertNotIn("lost:2026-11-03", files.keys("2026-11-12T18:00:00Z"))


class EntryStatusTest(unittest.TestCase):
    def test_a_recent_late_commit_is_an_incident_and_an_old_one_is_not_reported(self):
        record = record_with(["2026-11-02"], seal=False)
        # Unsealed when the settlement that opens its window passes: resolution closes it late_commit.
        now = at("2026-11-04T05:30:00Z")
        vr.resolve_entries(record, {}, vr.bars_cutoff(now), now)
        self.assertEqual(record["entries"][0]["status"], "late_commit")
        record["entries"].append(record_with(["2026-11-03"])["entries"][0])
        problems = Files(record, payload={"frozen_at": "2026-11-04T16:00:00Z"}).check("2026-11-04T18:00:00Z")
        self.assertEqual([(p.key, p.needs_a_person) for p in problems], [("late_commit:2026-11-02", False)])
        self.assertIn("Entry 2026-11-02 is late_commit", problems[0].text)
        later = Files(record, payload={"frozen_at": "2026-11-13T16:00:00Z"}).keys("2026-11-13T18:00:00Z")
        self.assertNotIn("late_commit:2026-11-02", later)

    def test_a_recent_expired_entry_is_an_incident_and_an_old_one_is_not_reported(self):
        record = record_with(["2026-11-02"])
        # Its window ends Nov 10; with no closes it expires once more than MAX_WAIT_SESSIONS sessions follow.
        now = at("2026-11-26T06:00:00Z")
        vr.resolve_entries(record, {}, vr.bars_cutoff(now), now)
        self.assertEqual(record["entries"][0]["status"], "expired")
        problems = Files(record, payload={"frozen_at": "2026-11-26T08:00:00Z"}).check("2026-11-26T09:00:00Z")
        expired = [p for p in problems if p.key == "expired:2026-11-02"]
        self.assertEqual(len(expired), 1)
        self.assertFalse(expired[0].needs_a_person)
        self.assertIn(record["entries"][0]["missing_sessions"][0], expired[0].text)
        week_later = Files(record, payload={"frozen_at": "2026-12-04T08:00:00Z"}).keys("2026-12-04T09:00:00Z")
        self.assertNotIn("expired:2026-11-02", week_later)

    def test_a_result_is_overdue_once_its_last_session_is_due(self):
        record = record_with(["2026-11-02"])
        end = record["entries"][0]["target_end"]
        files = Files(record, payload={"frozen_at": "2026-11-11T08:00:00Z"})
        next_day = date.fromisoformat(end) + timedelta(days=1)
        before = [p for p in files.check(f"{next_day}T16:59:00Z") if p.key.startswith("overdue:")]
        after = [p for p in files.check(f"{next_day}T17:01:00Z") if p.key.startswith("overdue:")]
        self.assertEqual(before, [])
        self.assertEqual([(p.key, p.needs_a_person) for p in after], [("overdue:2026-11-02", True)])
        self.assertIn(f"Entry 2026-11-02 is still pending although its window ended on {end}", after[0].text)

    def test_a_record_that_does_not_load_needs_a_person(self):
        for text in ("", "{", json.dumps({"schema_version": 1})):
            with self.subTest(text=text):
                problems = Files(text).check("2026-11-04T18:00:00Z")
                self.assertEqual((problems[0].key, problems[0].needs_a_person), ("record", True))
                self.assertTrue(problems[0].text.startswith("The live record does not load"))


class PayloadAndRunsTest(unittest.TestCase):
    def test_a_payload_older_than_eighteen_hours_is_stale(self):
        record = record_with(["2026-11-02", "2026-11-03"])
        fresh = Files(record, payload={"frozen_at": "2026-11-04T00:30:00Z"}).check("2026-11-04T18:00:00Z")
        stale = Files(record, payload={"frozen_at": "2026-11-03T23:30:00Z"}).check("2026-11-04T18:00:00Z")
        self.assertEqual(fresh, [])
        self.assertEqual([(p.key, p.needs_a_person) for p in stale], [("stale:2026-11-03T23:30:00Z", True)])
        self.assertIn("18.5 hours ago (more than 18)", stale[0].text)
        for payload in ({}, {"frozen_at": "yesterday"}, [1, 2]):
            with self.subTest(payload=payload):
                self.assertEqual(Files(record, payload=payload).keys("2026-11-04T18:00:00Z"), ["payload"])

    def test_two_failed_refreshes_in_a_row_need_a_person_and_one_does_not(self):
        record = record_with(["2026-11-02", "2026-11-03"])
        cases = [
            ([run("failure", 2), run("success", 1)], None),
            ([run("failure", 3), run("cancelled", 2), run("timed_out", 1)], "refresh:https://example.test/1"),
            ([run("startup_failure", 2), run("failure", 1)], "refresh:https://example.test/1"),
            ([run("success", 2), run("failure", 1)], None),
            ([run("failure", 1)], None),
            ([run("failure", 3, "pull_request"), run("failure", 2), run("success", 1)], None),
        ]
        for runs, key in cases:
            with self.subTest(runs=[(r["conclusion"], r["event"]) for r in runs]):
                keys = [k for k in Files(record, refresh=runs).keys("2026-11-04T18:00:00Z") if k.startswith("refresh")]
                self.assertEqual(keys, [key] if key else [])

    def test_a_growing_failure_streak_stays_one_problem(self):
        record = record_with(["2026-11-02", "2026-11-03"])
        two = [run("failure", 3), run("failure", 2), run("success", 1)]
        three = [run("failure", 4)] + two
        self.assertEqual(Files(record, refresh=two).keys("2026-11-04T18:00:00Z"),
                         Files(record, refresh=three).keys("2026-11-04T18:00:00Z"))

    def test_a_failed_reproduce_run_on_main_needs_a_person_and_a_pull_request_run_does_not_count(self):
        record = record_with(["2026-11-02", "2026-11-03"])
        cases = [
            ([run("failure", 1)], True),
            ([run("success", 1)], False),
            ([run("cancelled", 2), run("success", 1)], False),
            ([run("failure", 2, "pull_request"), run("success", 1, "push")], False),
            ([run("success", 2, "pull_request"), run("failure", 1, "push")], True),
        ]
        for runs, reported in cases:
            with self.subTest(runs=[(r["conclusion"], r["event"]) for r in runs]):
                keys = Files(record, reproduce=runs).keys("2026-11-04T18:00:00Z")
                self.assertEqual(any(k.startswith("reproduce:") for k in keys), reported)


class OutputTest(unittest.TestCase):
    def test_the_digest_follows_the_problems_not_their_details(self):
        record = record_with(["2026-11-02", "2026-11-03"])
        stale = Files(record, payload={"frozen_at": "2026-11-03T20:00:00Z"})
        early, late = stale.check("2026-11-04T15:00:00Z"), stale.check("2026-11-04T18:00:00Z")
        self.assertNotEqual([p.text for p in early], [p.text for p in late])        # the age moved on
        self.assertEqual(rh.digest(early), rh.digest(late))
        other = Files(record, payload={"frozen_at": "2026-11-03T21:00:00Z"}).check("2026-11-04T18:00:00Z")
        self.assertNotEqual(rh.digest(other), rh.digest(late))
        body = rh.markdown(late, at("2026-11-04T18:00:00Z"))
        self.assertIn(f"<!-- health-digest: {rh.digest(late)} -->", body)
        self.assertIn("**Needs a person**", body)
        self.assertEqual(rh.markdown([], at("2026-11-04T18:00:00Z")), "Healthy at 2026-11-04 18:00 UTC.\n")

    def test_exit_status_says_healthy_incidents_or_needs_a_person(self):
        healthy = Files(record_with(["2026-11-02", "2026-11-03"]))
        body = healthy.root / "health.md"
        args = ["--record", str(healthy.record), "--payload", str(healthy.payload), "--markdown", str(body)]
        self.assertEqual(rh.main([*args, "--now", "2026-11-04T18:00:00Z"]), rh.HEALTHY)
        self.assertTrue(body.read_text(encoding="utf-8").startswith("Healthy"))
        self.assertEqual(rh.main([*args, "--now", "2026-11-09T18:00:00Z"]), rh.NEEDS_A_PERSON)
        self.assertIn("health-digest", body.read_text(encoding="utf-8"))
        lost = Files(record_with(["2026-11-02", "2026-11-04"], started="2026-11-02"),
                     payload={"frozen_at": "2026-11-05T16:00:00Z"})
        self.assertEqual(rh.main(["--record", str(lost.record), "--payload", str(lost.payload),
                                  "--now", "2026-11-05T18:00:00Z"]), rh.INCIDENTS)
        with self.assertRaises(SystemExit) as raised:
            rh.main([*args, "--now", "not a time"])
        self.assertEqual(raised.exception.code, 2)

    def test_a_failure_of_the_check_itself_writes_no_issue_body(self):
        files = Files(record_with(["2026-11-02"]))
        body = files.root / "health.md"
        with mock.patch.object(rh, "check", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(rh.main(["--record", str(files.record), "--payload", str(files.payload),
                                      "--markdown", str(body)]), rh.BROKEN)
        self.assertFalse(body.exists())

    def test_it_imports_only_the_standard_library_and_the_record_modules(self):
        # The Health job holds an issues token and installs nothing.
        tree = ast.parse(Path(rh.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                with self.subTest(module=module):
                    self.assertTrue(module.split(".")[0] in sys.stdlib_module_names or module == "__future__"
                                    or module in {"backend", "backend.contract_calendar"}, module)


if __name__ == "__main__":
    unittest.main()
