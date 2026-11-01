"""backend/field_record.py: the unsealed shadow log of the whole field (ROADMAP 1.8).

Lines are appended by the record's window rule at write time, first issuance per (session, model) wins,
the file only grows, windows settle by the record's resolution rule, and nothing here touches the sealed
record. Network-free: the download and the models are patched where `issue` needs them.
"""

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
from backend.contract_calendar import is_business_day

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

    def test_a_later_line_for_a_session_must_share_its_window(self):
        first, _ = fr.append("", [issued(key="har_iv")], payload(), self.NOW)
        # Appended after the next settlement, the record's rule would give a later window: refused.
        late = at("2026-11-05T20:00:00Z")
        text, messages = fr.append(first, [issued(key="gjr", issued_at="2026-11-05T19:58:00Z")], payload(), late)
        self.assertEqual(text, first)
        self.assertTrue(messages[0].startswith("refused 2026-11-04 gjr: its window"), messages)

    def test_malformed_unknown_and_future_lines_are_refused(self):
        bad = [{**issued(), "model_key": "har_iv_sameday"}, {**issued(), "extra": 1},
               issued(issued_at="2026-11-05T14:00:00Z"), {**issued(), "sealed": True}]
        text, messages = fr.append("", bad, payload(), self.NOW)
        self.assertEqual((text, len(messages)), ("", 4))
        self.assertTrue(all(m.startswith("refused") for m in messages))

    def test_windows_settle_by_the_record_s_rule(self):
        first, _ = fr.append("", [issued()], payload(), self.NOW)
        base = date.fromisoformat(fr.parse(first)[0]["base_session"])
        days = vr.window_sessions(base)
        closes = {d: 100.0 + i for i, d in enumerate(sessions("2026-10-01", days[-1].isoformat()))}
        frozen = f"{days[-1] + timedelta(days=1)}T13:00:00Z"
        text, messages = fr.append(first, [], payload(frozen=frozen, closes=closes), at(frozen))
        outcome = fr.parse(text)[-1]
        self.assertEqual((outcome["kind"], outcome["status"]), ("outcome", "resolved"))
        self.assertEqual(outcome["realized_pct"], round(vr.realized_vol_pct([closes[d] for d in days]), 4))
        self.assertEqual(fr.append(text, [], payload(frozen=frozen, closes=closes), at(frozen))[0], text)  # once

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


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.text, _ = fr.append("", [issued(key=k) for k in ("har_iv", "gjr")], payload(), at("2026-11-05T13:05:00Z"))

    def test_tampering_is_caught(self):
        lines = self.text.splitlines(keepends=True)
        changed = lines[0].replace('"forecast_pct":40.0', '"forecast_pct":41.0')
        cases = {
            "a changed line": (changed + lines[1], self.text),
            "a removed line": (lines[1], self.text),
            "a duplicate": (self.text + lines[0], None),
            "not canonical": (self.text.replace('{"as_of"', '{ "as_of"', 1), None),
            "no final newline": (self.text.rstrip("\n"), None),
            "an unknown model": (self.text.replace('"gjr"', '"har_iv_sameday"'), None),
            "a sealed line": (self.text.replace('"sealed":false', '"sealed":true', 1), None),
        }
        for name, (text, previous) in cases.items():
            with self.subTest(name):
                self.assertTrue(fr.validate(text, previous=previous), name)

    def test_a_resolved_outcome_must_recompute(self):
        base = fr.parse(self.text)[0]["base_session"]
        days = vr.window_sessions(date.fromisoformat(base))
        line = {"kind": "outcome", "base_session": base, "status": "resolved", "sealed": False,
                "resolved_at": "2026-11-20T13:00:00Z", "closes": [[d.isoformat(), 100.0 + i] for i, d in enumerate(days)],
                "realized_pct": 1.0}
        self.assertTrue(fr.validate(self.text + fr.canonical(line) + "\n"))
        line["realized_pct"] = round(vr.realized_vol_pct([100.0 + i for i in range(len(days))]), 4)
        self.assertEqual(fr.validate(self.text + fr.canonical(line) + "\n"), [])


class IssueTest(unittest.TestCase):
    def _issue(self, published_har_iv=47.6, as_of="2026-11-04", lab=47.6):
        import numpy as np
        import pandas as pd

        dates = pd.DatetimeIndex(pd.to_datetime(["2026-11-03", "2026-11-04"]))
        meta = {"recent_closes": None, "ohlc": pd.DataFrame({c: [1.0, 1.0] for c in ("Open", "High", "Low", "Close")},
                                                            index=dates), "ovx_source": "yahoo ^OVX"}
        body = payload(as_of=as_of)
        body["vol_forecast"]["live"]["forecast_next_week_vol_annualized_pct"] = published_har_iv
        body["vol_forecast"]["recent_closes"] = None

        def model(key, *args):
            value = lab if key == "har_iv" else 48.4 if key == "har_iv_range" else 40.0
            if key == "lgbm":
                raise RuntimeError("no trees")
            return {"forecast_next_week_vol_annualized_pct": value, "forecast_median_pct": value - 5}

        with mock.patch("backend.vol_forecast._load_data_full", return_value=(np.zeros(2), dates, np.ones(2), meta)), \
                mock.patch("backend.vol_forecast._pick_ovx", return_value=np.ones(2)), \
                mock.patch("backend.vol_lab.live_model_forecast", side_effect=model):
            return fr.issue(body, [], at("2026-11-05T13:00:00Z"), "c" * 40)

    def test_it_logs_every_open_model_that_runs_and_skips_one_that_fails(self):
        lines, why = self._issue()
        self.assertEqual([line["model_key"] for line in lines], [k for k in fr.FIELD_KEYS if k != "lgbm"])
        self.assertTrue(all(set(line) == fr.ISSUED_KEYS and line["sealed"] is False for line in lines))
        self.assertEqual(why, f"{len(fr.FIELD_KEYS) - 1} of {len(fr.FIELD_KEYS)} open models logged for 2026-11-04 "
                              "(lgbm failed and stay open)")

    def test_a_download_that_does_not_reproduce_the_published_forecast_logs_nothing(self):
        for kwargs in ({"lab": 47.7}, {"as_of": "2026-11-05"}):
            with self.subTest(kwargs=kwargs):
                lines, why = self._issue(**kwargs)
                self.assertEqual(lines, [])


class CommandLineTest(unittest.TestCase):
    def test_append_and_validate_run_on_the_standard_library_alone(self):
        # The write job installs nothing: the command must work with numpy (and so pandas) unimportable.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "block").mkdir()
            (tmp / "block" / "sitecustomize.py").write_text("import sys\nsys.modules['numpy'] = None\nsys.modules['pandas'] = None\n")
            # The subprocess runs on the real clock: a line issued in the past.
            (tmp / "payload.json").write_text(json.dumps(payload(as_of="2026-10-29", frozen="2026-10-30T13:00:00Z")))
            (tmp / "new.jsonl").write_text(fr.canonical(issued(as_of="2026-10-29", issued_at="2026-10-30T13:00:00Z")) + "\n")
            env = {**os.environ, "PYTHONPATH": str(tmp / "block")}
            run = subprocess.run([sys.executable, "-B", "backend/field_record.py", "--append", "--log", str(tmp / "old.jsonl"),
                                  "--new", str(tmp / "new.jsonl"), "--payload", str(tmp / "payload.json"),
                                  "--out", str(tmp / "merged.jsonl")], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("logged 2026-10-29 har_iv", run.stdout)
            check = subprocess.run([sys.executable, "-B", "backend/field_record.py", "--validate", "--log",
                                    str(tmp / "merged.jsonl")], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual((check.returncode, check.stdout.strip()), (0, "OK"))

    def test_nothing_but_the_log_s_own_code_reads_it(self):
        # The log is never evidence: no other module, script or page may read it, so it can never feed
        # the promotion test or the site (refresh.yml writes it, from PR 2 of ROADMAP 1.8).
        allowed = {Path("backend/field_record.py"), Path(".github/workflows/refresh.yml")}
        for folder in ("backend", "scripts", "src", ".github"):
            for path in (ROOT / folder).rglob("*"):
                if path.suffix not in {".py", ".js", ".jsx", ".mjs", ".yml"} or path.relative_to(ROOT) in allowed:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for word in ("field_record", "vol_field_record", "field-data"):
                    self.assertNotIn(word, text, f"{path.relative_to(ROOT)} mentions {word}")

    def test_it_never_touches_the_sealed_record(self):
        source = (ROOT / "backend" / "field_record.py").read_text()
        for word in ("runtime-state", "vol_track_record", "save_record", "seal_entries", "load_record("):
            self.assertNotIn(word, source)


if __name__ == "__main__":
    unittest.main()
