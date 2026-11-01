"""backend/field_record.py: the unsealed shadow log of the whole field (ROADMAP 1.8).

Lines are appended by the record's window rule at write time, first issuance per (session, model) wins,
the file only grows, windows settle by the record's resolution rule, validate() checks from the file
alone every rule append() applies, and nothing here touches the sealed record. Network-free: the
download and the models are patched where `issue` needs them.
"""

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from backend import field_record as fr
from backend import vol_record as vr
from backend.contract_calendar import is_business_day, shift_business_days, spans_roll

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def sessions(first: str, last: str) -> list[date]:
    day, out = date.fromisoformat(first), []
    while day <= date.fromisoformat(last):
        if is_business_day(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def payload(as_of="2026-11-04", frozen="2026-11-05T13:00:00Z", closes=None):
    days = sorted(closes or {})
    return {"frozen_at": frozen,
            "vol_forecast": {"live": {"as_of": as_of, "issued_at": frozen, "forecast_next_week_vol_annualized_pct": 47.6,
                                      "challenger": {"forecast_next_week_vol_annualized_pct": 48.4}},
                             "recent_closes": {"date": [d.isoformat() for d in days], "close": [closes[d] for d in days]}}}


def issued(as_of="2026-11-04", key="har_iv", issued_at="2026-11-05T13:00:00Z", forecast=40.0):
    return {"kind": "forecast", "as_of": as_of, "issued_at": issued_at, "model_key": key, "forecast_pct": forecast,
            "median_pct": forecast - 5, "inputs_sha256": "a" * 64, "ovx_source": "yahoo ^OVX", "code_sha": None,
            "spec_sha256": "b" * 64, "sealed": False}


def frozen_for(cutoff: date) -> str:
    """A freeze time whose bars_cutoff is `cutoff` (the next calendar day, 08:00 ET)."""
    stamp = f"{cutoff + timedelta(days=1)}T13:00:00Z"
    assert vr.bars_cutoff(at(stamp)) == cutoff, stamp
    return stamp


class FieldTest(unittest.TestCase):
    def test_the_field_is_the_lab_s_without_the_timing_control(self):
        from backend import vol_lab

        self.assertEqual(fr.FIELD_KEYS, tuple(vol_lab.FEASIBLE))
        self.assertNotIn("har_iv_sameday", fr.FIELD_KEYS)

    def test_open_keys_follow_what_is_logged_and_never_reopen_an_older_session(self):
        text, _ = fr.append("", [issued(key="har_iv"), issued(key="gjr")], payload(), at("2026-11-05T13:05:00Z"))
        lines = fr.parse(text)
        self.assertEqual(fr.open_keys(lines, "2026-11-04"), [k for k in fr.FIELD_KEYS if k not in ("har_iv", "gjr")])
        self.assertEqual(fr.open_keys(lines, "2026-11-03"), [])                 # an older session stays closed
        self.assertEqual(len(fr.open_keys(lines, "2026-11-05")), len(fr.FIELD_KEYS))


class AppendTest(unittest.TestCase):
    NOW = at("2026-11-05T13:05:00Z")

    def test_lines_get_the_record_s_window_and_the_file_only_grows(self):
        first, messages = fr.append("", [issued(key=k) for k in fr.FIELD_KEYS], payload(), self.NOW)
        self.assertEqual(len(fr.parse(first)), len(fr.FIELD_KEYS))
        window = vr.target_window(self.NOW + vr.COMMIT_MARGIN)
        self.assertTrue(all(line["base_session"] == window["base_session"].isoformat() for line in fr.parse(first)))
        self.assertEqual(fr.validate(first), [])
        # A second run: duplicates and older sessions are refused; nothing is rewritten.
        again, messages = fr.append(first, [issued(key="har_iv", forecast=99.0), issued(as_of="2026-11-03")],
                                    payload(), self.NOW + timedelta(hours=4))
        self.assertEqual(again, first)
        self.assertEqual(len([m for m in messages if m.startswith("refused")]), 2)
        self.assertEqual(fr.validate(again, previous=first), [])

    def test_the_window_is_the_record_s_rule_at_the_later_of_issue_and_append_time(self):
        # Appended soon after issue: the 2026-11-05 settlement (19:30 UTC) is still more than an hour away.
        line = fr.parse(fr.append("", [issued()], payload(), self.NOW)[0])[0]
        self.assertEqual((line["appended_at"], line["base_session"], line["target_start"], line["target_end"]),
                         ("2026-11-05T13:05:00Z", "2026-11-05", "2026-11-06", "2026-11-12"))
        # Appended late: within an hour of that settlement, the window moves to the next session.
        late = at("2026-11-05T18:45:00Z")
        line = fr.parse(fr.append("", [issued()], payload(), late)[0])[0]
        self.assertEqual((line["base_session"], line["target_end"]), ("2026-11-06", "2026-11-13"))
        # Issued a little after the run's clock (within the allowed skew): the issue time decides.
        line = fr.parse(fr.append("", [issued(issued_at="2026-11-05T18:33:00Z")], payload(), at("2026-11-05T18:29:00Z"))[0])[0]
        self.assertEqual(line["base_session"], "2026-11-06")

    def test_a_later_line_for_a_session_must_share_its_window(self):
        first, _ = fr.append("", [issued(key="har_iv")], payload(), self.NOW)
        # Appended after the next settlement, the record's rule would give a later window: refused.
        late = at("2026-11-05T20:00:00Z")
        text, messages = fr.append(first, [issued(key="gjr", issued_at="2026-11-05T19:58:00Z")], payload(), late)
        self.assertEqual(text, first)
        self.assertTrue(messages[0].startswith("refused 2026-11-04 gjr: its window"), messages)

    def test_malformed_unknown_and_future_lines_are_refused(self):
        bad = [{**issued(), "model_key": "har_iv_sameday"}, {**issued(), "extra": 1},
               issued(issued_at="2026-11-05T14:00:00Z"), {**issued(), "sealed": True},
               issued(as_of="20261104"), issued(as_of="2026-11-07"), issued(issued_at="soon"),
               issued(as_of="2026-11-05")]                                      # its window would open on its own session
        text, messages = fr.append("", bad, payload(), self.NOW)
        self.assertEqual((text, len(messages)), ("", len(bad)))
        self.assertTrue(all(m.startswith("refused") for m in messages), messages)

    def test_windows_settle_by_the_record_s_rule(self):
        first, _ = fr.append("", [issued()], payload(), self.NOW)
        base = date.fromisoformat(fr.parse(first)[0]["base_session"])
        days = vr.window_sessions(base)
        closes = {d: 100.0 + i for i, d in enumerate(sessions("2026-10-01", days[-1].isoformat()))}
        frozen = frozen_for(days[-1])
        text, messages = fr.append(first, [], payload(frozen=frozen, closes=closes), at(frozen))
        outcome = fr.parse(text)[-1]
        self.assertEqual((outcome["kind"], outcome["status"]), ("outcome", "resolved"))
        self.assertEqual(outcome["closes"], [[d.isoformat(), closes[d]] for d in days])
        self.assertEqual(outcome["realized_pct"], round(vr.realized_vol_pct([closes[d] for d in days]), 4))
        self.assertEqual(fr.append(text, [], payload(frozen=frozen, closes=closes), at(frozen))[0], text)  # once
        self.assertEqual(fr.validate(text, previous=first), [])

    def test_the_cutoff_is_the_freeze_time_s_never_later_than_the_run(self):
        first, _ = fr.append("", [issued()], payload(), self.NOW)
        days = vr.window_sessions(date.fromisoformat(fr.parse(first)[0]["base_session"]))
        closes = {d: 100.0 + i for i, d in enumerate(sessions("2026-10-01", days[-1].isoformat()))}
        early = f"{days[-1]}T19:00:00Z"                    # 14:00 ET on the last session: its bar is not finished
        self.assertLess(vr.bars_cutoff(at(early)), days[-1])
        # Frozen before the last bar finished: no outcome, however late the run.
        text, _ = fr.append(first, [], payload(frozen=early, closes=closes), at("2026-11-20T13:00:00Z"))
        self.assertEqual(text, first)
        # A freeze time later than the run counts as the run's time.
        text, _ = fr.append(first, [], payload(frozen="2026-11-20T13:00:00Z", closes=closes), at(early))
        self.assertEqual(text, first)

    def test_a_window_across_a_roll_is_skipped(self):
        line = issued(as_of="2026-11-13", issued_at="2026-11-16T13:00:00Z")
        text, messages = fr.append("", [line], payload(as_of="2026-11-13", frozen="2026-11-16T13:00:00Z"),
                                   at("2026-11-16T13:05:00Z"))
        forecast, outcome = fr.parse(text)
        self.assertTrue(spans_roll(date.fromisoformat(forecast["base_session"]), date.fromisoformat(forecast["target_end"])))
        self.assertEqual((outcome["status"], outcome["closes"], outcome["realized_pct"]), ("skipped_roll", None, None))
        # A model logged later for the same session, before its window opens, still joins it.
        more, _ = fr.append(text, [{**line, "model_key": "gjr"}], payload(as_of="2026-11-13", frozen="2026-11-16T13:00:00Z"),
                            at("2026-11-16T14:00:00Z"))
        self.assertEqual(fr.parse(more)[-1]["model_key"], "gjr")
        self.assertEqual(fr.validate(more, previous=text), [])

    def test_a_window_expires_after_ten_missing_sessions_not_before(self):
        first, _ = fr.append("", [issued()], payload(), self.NOW)
        base = date.fromisoformat(fr.parse(first)[0]["base_session"])
        end = vr.window_sessions(base)[-1]
        for waited, expected in ((vr.MAX_WAIT_SESSIONS, None), (vr.MAX_WAIT_SESSIONS + 1, "expired")):
            with self.subTest(waited=waited):
                cutoff = shift_business_days(end, waited)
                closes = {d: 100.0 for d in sessions("2026-10-20", cutoff.isoformat()) if d != base}
                frozen = frozen_for(cutoff)
                text, _ = fr.append(first, [], payload(frozen=frozen, closes=closes), at(frozen))
                status = fr.parse(text)[-1].get("status") if text != first else None
                self.assertEqual(status, expected)

    def test_a_window_older_than_the_payload_s_closes_is_never_expired(self):
        first, _ = fr.append("", [issued()], payload(), self.NOW)
        later = sessions("2026-12-15", "2027-01-29")                          # the window's closes have scrolled out
        closes = {d: 100.0 for d in later}
        frozen = "2027-01-30T13:00:00Z"
        text, _ = fr.append(first, [], payload(frozen=frozen, closes=closes), at(frozen))
        self.assertEqual(text, first)
        # With the window inside the payload's span and its closes missing, it does expire.
        base = date.fromisoformat(fr.parse(first)[0]["base_session"])
        span = {d: 100.0 for d in sessions((base - timedelta(days=3)).isoformat(), "2026-12-01") if d != base}
        text, _ = fr.append(first, [], payload(frozen="2026-12-02T13:00:00Z", closes=span), at("2026-12-02T13:00:00Z"))
        self.assertEqual(fr.parse(text)[-1]["status"], "expired")
        self.assertEqual(fr.validate(text, previous=first), [])

    def test_append_refuses_to_extend_an_invalid_log(self):
        first, _ = fr.append("", [issued()], payload(), self.NOW)
        with self.assertRaises(ValueError):
            fr.append(first.replace('"forecast_pct":40.0', '"forecast_pct":0.0'), [], payload(), self.NOW)


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.text, _ = fr.append("", [issued(key=k) for k in ("har_iv", "gjr")], payload(), at("2026-11-05T13:05:00Z"))
        self.base = fr.parse(self.text)[0]["base_session"]
        days = vr.window_sessions(date.fromisoformat(self.base))
        closes = {d: 100.0 + i for i, d in enumerate(sessions("2026-10-01", days[-1].isoformat()))}
        frozen = frozen_for(days[-1])
        self.resolved, _ = fr.append(self.text, [], payload(frozen=frozen, closes=closes), at(frozen))
        self.outcome = fr.parse(self.resolved)[-1]

    def check(self, cases):
        for name, (text, previous, expected) in cases.items():
            with self.subTest(name):
                errors = fr.validate(text, previous=previous)
                self.assertTrue(any(expected in error for error in errors), (name, errors))

    def line(self, index=1, text=None, **changes):
        return fr.canonical({**fr.parse(text or self.text)[index], **changes}) + "\n"

    def test_the_good_logs_pass(self):
        self.assertEqual(fr.validate(self.text), [])
        self.assertEqual(fr.validate(self.resolved, previous=self.text), [])

    def test_tampering_with_lines_is_caught(self):
        lines = self.text.splitlines(keepends=True)
        changed = lines[0].replace('"forecast_pct":40.0', '"forecast_pct":41.0')
        self.check({
            "a changed line": (changed + lines[1], self.text, "not a prefix"),
            "a removed line": (lines[1], self.text, "not a prefix"),
            "a duplicate": (self.text + lines[0], None, "logged twice"),
            "not canonical": (self.text.replace('{"appended_at"', '{ "appended_at"', 1), None, "not canonical"),
            "no final newline": (self.text.rstrip("\n"), None, "newline"),
            "a blank line": (lines[0] + "\n" + lines[1], None, "blank"),
            "CRLF line ends": (self.text.replace("\n", "\r\n"), None, "not canonical"),
            "an unknown model": (self.text.replace('"gjr"', '"har_iv_sameday"'), None, "not in the field"),
            "a sealed line": (self.text.replace('"sealed":false', '"sealed":true', 1), None, "sealed: false"),
            "an unknown kind": (self.text + '{"kind":"note","sealed":false}\n', None, "unknown kind"),
            "a missing key": (self.text + fr.canonical({k: v for k, v in fr.parse(self.text)[1].items()
                                                         if k != "spec_sha256"}) + "\n", None, "exactly"),
            "a zero forecast": (lines[0] + self.line(forecast_pct=0), None, "positive number"),
            "a boolean forecast": (lines[0] + self.line(median_pct=True), None, "positive number"),
            "a bad digest": (lines[0] + self.line(inputs_sha256="x"), None, "SHA-256"),
            "a compact date": (lines[0] + self.line(as_of="20261104"), None, "malformed"),
        })

    def test_windows_and_time_are_checked_from_the_file_alone(self):
        lines = self.text.splitlines(keepends=True)
        self.check({
            "a second window": (lines[0] + self.line(base_session="2026-11-06", target_start="2026-11-09",
                                                     target_end="2026-11-13", appended_at="2026-11-05T18:45:00Z"),
                                None, "a second window"),
            "appended after the window's rule moved": (lines[0] + self.line(appended_at="2026-11-05T19:00:00Z"), None,
                                                       "does not follow the record's rule"),
            "a window that already opened when issued": (self.line(0, issued_at="2026-11-20T13:00:00Z",
                                                                   appended_at="2026-11-20T13:00:00Z"), None,
                                                         "does not follow the record's rule"),
            "issued after it was appended": (lines[0] + self.line(issued_at="2026-11-05T13:20:00Z"), None,
                                             "issued after it was appended"),
            "out of time order": (self.line(1, appended_at="2026-11-05T14:00:00Z") + lines[0], None, "time order"),
            "an older session after a newer one": (self.text + self.line(as_of="2026-11-03", model_key="ewma",
                                                                         appended_at="2026-11-05T13:06:00Z"),
                                                   None, "after the later session"),
            # A line backfilled after its window resolved: either back-dated (out of order) or its rule moved.
            "a hindsight line, back-dated": (self.resolved + self.line(model_key="ewma"), None, "time order"),
            "a hindsight line, dated honestly": (self.resolved + self.line(model_key="ewma",
                                                                           issued_at=self.outcome["resolved_at"],
                                                                           appended_at=self.outcome["resolved_at"]),
                                                 None, "does not follow the record's rule"),
        })

    def test_outcomes_must_agree_with_their_window(self):
        def outcome(**changes):
            return self.text + fr.canonical({**self.outcome, **changes}) + "\n"

        closes = self.outcome["closes"]
        shifted = [[f"2025{pair[0][4:]}", pair[1]] for pair in closes]
        self.check({
            "an orphan": (outcome(base_session="2026-11-20"), None, "no line above opened"),
            "a second outcome": (self.resolved + fr.canonical(self.outcome) + "\n", None, "a second outcome"),
            "wrongly dated closes": (outcome(closes=shifted), None, "dated 2026-11-05 to 2026-11-12"),
            "five closes": (outcome(closes=closes[:5]), None, "six positive closes"),
            "a boolean close": (outcome(closes=closes[:5] + [[closes[5][0], True]]), None, "six positive closes"),
            "closes that are not pairs": (outcome(closes="abcdef"), None, "six positive closes"),
            "a realized_pct that does not recompute": (outcome(realized_pct=1.0), None, "does not recompute"),
            "resolved before the last session settled": (outcome(resolved_at="2026-11-12T15:00:00Z"), None,
                                                         "before its last session settled"),
            "skipped without a roll": (outcome(status="skipped_roll", closes=None, realized_pct=None), None,
                                       "spans a contract roll"),
            "expired early": (outcome(status="expired", closes=None, realized_pct=None,
                                      resolved_at="2026-11-13T13:00:00Z"), None, "expired before"),
            "expired with closes": (outcome(status="expired", resolved_at="2026-12-20T13:00:00Z"), None,
                                    "only a resolved outcome"),
            "an unknown status": (outcome(status="late"), None, "known status"),
        })


class IssueTest(unittest.TestCase):
    def _issue(self, published_har_iv=47.6, as_of="2026-11-04", lab=47.6, range_lab=48.4, meta_closes=None,
               ohlc=True, pick=True):
        import numpy as np
        import pandas as pd

        dates = pd.DatetimeIndex(pd.to_datetime(["2026-11-03", "2026-11-04"]))
        bars = pd.DataFrame({c: [1.0, 1.0] for c in ("Open", "High", "Low", "Close")}, index=dates)
        meta = {"recent_closes": meta_closes, "ohlc": bars if ohlc else None, "ovx_source": "yahoo ^OVX"}
        body = payload(as_of=as_of)
        body["vol_forecast"]["live"]["forecast_next_week_vol_annualized_pct"] = published_har_iv
        body["vol_forecast"]["recent_closes"] = None

        def model(key, *args):
            value = lab if key == "har_iv" else range_lab if key == "har_iv_range" else 40.0
            if key == "lgbm":
                raise RuntimeError("no trees")
            return {"forecast_next_week_vol_annualized_pct": value, "forecast_median_pct": value - 5}

        with mock.patch("backend.vol_forecast._load_data_full", return_value=(np.zeros(2), dates, np.ones(2), meta)), \
                mock.patch("backend.vol_forecast._pick_ovx", return_value=np.ones(2) if pick else None), \
                mock.patch("backend.vol_lab.live_model_forecast", side_effect=model):
            return fr.issue(body, [], at("2026-11-05T13:00:00Z"), "c" * 40)

    def test_it_logs_every_open_model_that_runs_and_skips_one_that_fails(self):
        lines, why = self._issue()
        self.assertEqual([line["model_key"] for line in lines], [k for k in fr.FIELD_KEYS if k != "lgbm"])
        self.assertTrue(all(set(line) == fr.ISSUED_KEYS and line["sealed"] is False for line in lines))
        self.assertEqual(why, f"{len(fr.FIELD_KEYS) - 1} of {len(fr.FIELD_KEYS)} open models logged for 2026-11-04 "
                              "(lgbm failed and stay open)")
        merged, _ = fr.append("", lines, payload(), at("2026-11-05T13:05:00Z"))
        self.assertEqual(fr.validate(merged), [])

    def test_a_download_that_does_not_reproduce_the_published_forecast_logs_nothing(self):
        cases = [({"lab": 47.7}, "the lab's har_iv gives 47.7, not the published 47.6"),
                 ({"range_lab": 48.5}, "the lab's har_iv_range gives 48.5, not the published 48.4"),
                 ({"as_of": "2026-11-05"}, "the download ends 2026-11-04, not at the published session 2026-11-05"),
                 ({"meta_closes": {"date": ["2026-11-04"], "close": [60.0]}}, "recent closes differ"),
                 ({"ohlc": False}, "no usable OVX history or OHLC bars"),
                 ({"pick": False}, "no usable OVX history or OHLC bars")]
        for kwargs, reason in cases:
            with self.subTest(kwargs=kwargs):
                lines, why = self._issue(**kwargs)
                self.assertEqual(lines, [])
                self.assertIn(reason, why)


class CommandLineTest(unittest.TestCase):
    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fr.main(list(argv), now=at("2026-11-05T13:05:00Z"))
        return code, out.getvalue()

    def test_the_write_side_runs_on_the_standard_library_alone(self):
        # The write job installs nothing: --open-keys, --append and --validate must work with numpy (and so
        # pandas) unimportable.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "block").mkdir()
            (tmp / "block" / "sitecustomize.py").write_text("import sys\nsys.modules['numpy'] = None\nsys.modules['pandas'] = None\n")
            # The subprocess runs on the real clock: a line issued in the past.
            (tmp / "payload.json").write_text(json.dumps(payload(as_of="2026-10-29", frozen="2026-10-30T13:00:00Z")))
            (tmp / "new.jsonl").write_text(fr.canonical(issued(as_of="2026-10-29", issued_at="2026-10-30T13:00:00Z")) + "\n")
            (tmp / "old.jsonl").write_text("")
            env = {**os.environ, "PYTHONPATH": str(tmp / "block")}

            def run(*argv):
                return subprocess.run([sys.executable, "-B", "backend/field_record.py", *argv], cwd=ROOT, env=env,
                                      capture_output=True, text=True)

            keys = run("--open-keys", "--payload", str(tmp / "payload.json"), "--log", str(tmp / "old.jsonl"))
            self.assertEqual((keys.returncode, keys.stdout.strip()), (0, str(len(fr.FIELD_KEYS))), keys.stderr)
            merged = run("--append", "--log", str(tmp / "old.jsonl"), "--new", str(tmp / "new.jsonl"),
                         "--payload", str(tmp / "payload.json"), "--out", str(tmp / "merged.jsonl"))
            self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
            self.assertIn("logged 2026-10-29 har_iv", merged.stdout)
            keys = run("--open-keys", "--payload", str(tmp / "payload.json"), "--log", str(tmp / "merged.jsonl"))
            self.assertEqual((keys.returncode, keys.stdout.strip()), (0, str(len(fr.FIELD_KEYS) - 1)), keys.stderr)
            check = run("--validate", "--log", str(tmp / "merged.jsonl"), "--previous", str(tmp / "old.jsonl"))
            self.assertEqual((check.returncode, check.stdout.strip()), (0, "OK"), check.stderr)

    def test_a_missing_file_is_an_error_never_an_empty_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log, gone = tmp / "log.jsonl", str(tmp / "gone.jsonl")
            log.write_text(fr.append("", [issued()], payload(), at("2026-11-05T13:05:00Z"))[0])
            (tmp / "payload.json").write_text(json.dumps(payload()))
            for argv in (["--validate", "--log", gone], ["--validate", "--log", str(log), "--previous", gone],
                         ["--validate"], ["--open-keys", "--payload", str(tmp / "payload.json"), "--log", gone],
                         ["--open-keys", "--payload", gone, "--log", str(log)],
                         ["--append", "--payload", str(tmp / "payload.json"), "--log", str(log), "--new", gone,
                          "--out", str(tmp / "out.jsonl")],
                         ["--append", "--payload", str(tmp / "payload.json"), "--log", str(log), "--new", str(log)]):
                with self.subTest(argv=argv[:1] + [Path(a).name for a in argv[1:]]):
                    code, printed = self.run_main(*argv)
                    self.assertEqual(code, 2, printed)
                    self.assertTrue(printed.startswith("field log: "), printed)
            self.assertEqual(self.run_main("--validate", "--log", str(log), "--previous", str(log)), (0, "OK\n"))

    def test_nothing_but_the_log_s_own_code_reads_it(self):
        # The log is never evidence: no other tracked file but the docs may name it, so no module, script,
        # workflow, page or build step can feed it to the promotion test or the site. (PR 2 of ROADMAP 1.8
        # adds the Refresh jobs that write it; this test then allows those jobs and nothing else there.)
        allowed = {"backend/field_record.py", "tests/test_field_record.py", "CHANGELOG.md", "pyproject.toml"}
        try:
            listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
            paths = [name for name in listed.decode().split("\0") if name]
        except (OSError, subprocess.CalledProcessError):
            skip = {".git", "node_modules", ".venv", "dist", "__pycache__", ".mypy_cache", ".ruff_cache"}
            paths = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                     if p.is_file() and not skip & set(p.relative_to(ROOT).parts)]
        self.assertGreater(len(paths), 100)
        for name in paths:
            if name in allowed or name.startswith("docs/") or not (ROOT / name).is_file():
                continue
            text = (ROOT / name).read_bytes().decode("utf-8", errors="replace")
            for word in ("field_record", "vol_field_record", "field-data"):
                self.assertNotIn(word, text, f"{name} mentions {word}")

    def test_it_never_touches_the_sealed_record(self):
        source = (ROOT / "backend" / "field_record.py").read_text()
        for word in ("runtime-state", "vol_track_record", "RECORD_PATH", "save_record", "seal_entries", "load_record("):
            self.assertNotIn(word, source)
        # Only the record's pure helpers and constants: nothing that reads, writes or seals it.
        pure = {"COMMIT_MARGIN", "HORIZON_SESSIONS", "MAX_CLOCK_SKEW", "MAX_WAIT_SESSIONS", "_aware_utc", "_dict",
                "_snapshot_time", "_stamp", "bars_cutoff", "completed_closes", "realized_vol_pct", "settlement_time",
                "target_window", "window_sessions"}
        tree = ast.parse(source)
        used = {node.attr for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "vr"}
        self.assertLessEqual(used, pure, f"field_record uses vr.{sorted(used - pure)}")
        bare = sum(isinstance(node, ast.Name) and node.id == "vr" for node in ast.walk(tree))
        self.assertEqual(bare, sum(1 for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                                   and isinstance(node.value, ast.Name) and node.value.id == "vr"),
                         "vr is used only as vr.<name> (no getattr, no aliases)")
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                          and node.module in ("backend.vol_record", "vol_record")], "import vol_record as vr only")


if __name__ == "__main__":
    unittest.main()
