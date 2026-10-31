"""scripts/verify_record.py: a reviewer's check of the sealed live record, from the record file alone.

Records here are made by the real recorder, sealer and resolver (backend/vol_record.py) with an injected
clock, so a clean record is exactly what production writes; each tampering case then changes one stored
fact and must be caught. Network-free and clock-independent.
"""

import copy
import io
import json
import math
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend import vol_record as vr
from scripts import verify_record as v

UTC = timezone.utc
FIT = [-4.883566, 0.038819, 0.170853, -0.151549, 1.03495]
INPUTS = {"rv5_pct": 48.65, "rv22_pct": 37.69, "rv66_pct": 45.95, "ovx": 58.71, "ovx_session": "2026-10-30"}
# Closes for the 2026-11-03 .. 2026-11-10 window (k0 = Tue 2026-11-03): every |log return| is ln(1.01).
WINDOW = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"]
ZIGZAG = dict(zip(WINDOW, [100.0, 101.0, 100.0, 101.0, 100.0, 101.0]))


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def payload(as_of: str, issued_at: str, closes: dict | None = None, fit: bool = True) -> dict:
    live = {
        "forecast_next_week_vol_annualized_pct": 56.4, "forecast_median_pct": 47.5, "prob_vol_rises_pct": 47.6,
        "direction": "FALLING", "current_realized_vol_5d_annualized_pct": 48.7,
        "interval_80": {"low_pct": 27.6, "high_pct": 75.8}, "model": "HAR-IV (RV5,RV22,RV66,OVX)",
        "model_version": "har-iv/2", "as_of": as_of, "issued_at": issued_at, "inputs": dict(INPUTS),
        "challenger": {"key": "har_iv_range", "as_of": as_of, "forecast_next_week_vol_annualized_pct": 52.9,
                       "forecast_median_pct": 44.8},
    }
    if fit:
        live.update(coefficients=list(FIT), smearing_factor=1.4128)
    days = sorted(closes or {})
    return {"frozen_at": issued_at, "contract": {"symbol": "CLZ26"},
            "vol_forecast": {"live": live, "recent_closes": {"date": days, "close": [closes[d] for d in days]}}}


def clean_record() -> dict:
    """Two entries as production writes them: one resolved on the zigzag window, one still pending."""
    record = {"schema_version": 1, "started": "2026-11-03", "entries": []}
    vr.record_forecast(record, payload("2026-11-02", "2026-11-03T13:00:00Z"), at("2026-11-03T13:05:00Z"))
    vr.seal_entries(record, at("2026-11-03T13:15:00Z"))
    vr.record_forecast(record, payload("2026-11-03", "2026-11-04T13:00:00Z"), at("2026-11-04T13:05:00Z"))
    vr.seal_entries(record, at("2026-11-04T13:15:00Z"))
    snapshot = at("2026-11-11T13:00:00Z")
    cutoff = vr.bars_cutoff(snapshot)
    vr.resolve_entries(record, vr.completed_closes(payload("2026-11-10", "2026-11-11T13:00:00Z", ZIGZAG), cutoff),
                       cutoff, snapshot)
    assert [e["status"] for e in record["entries"]] == ["resolved", "pending"], record
    return record


def run(record) -> tuple[int, str]:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "vol_track_record.json"
        path.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = v.main([str(path)])
    return code, out.getvalue()


class VerifyRecordTest(unittest.TestCase):
    def test_a_record_as_production_writes_it_passes(self):
        code, out = run(clean_record())
        self.assertEqual(code, 0, out)
        self.assertIn("OK: every entry passes", out)
        lines = out.splitlines()
        resolved = next(line for line in lines if line.startswith("2026-11-02"))
        # The zigzag window's realized vol (15.80), its QLIKE against 56.4, below the 27.6-75.8 band,
        # sealed 6 h 15 min (375 min) before the 14:30 ET (19:30Z) settlement that opens it.
        realized = math.log(1.01) * math.sqrt(252) * 100
        self.assertIn(f"{realized:.2f}", resolved)
        self.assertIn(f"{vr.qlike(round(realized, 4), 56.4):.3f}", resolved)
        self.assertIn("375 min", resolved)
        self.assertIn(" out ", resolved)
        self.assertIn("reproduces", resolved)
        self.assertIn("har-iv/2: 1 resolved", out)

    def test_entries_recorded_before_the_fit_was_stored_pass_and_say_so(self):
        record = clean_record()
        for entry in record["entries"]:      # recorded before 2.2.0: no fit, and sealed before the chain
            entry.pop("coefficients", None)
            entry.pop("smearing_factor", None)
            entry.pop("previous_sha256", None)
        code, out = run(record)
        self.assertEqual(code, 0, out)
        self.assertIn("not stored", out)

    def test_each_kind_of_tampering_is_caught(self):
        def tampered(change):
            record = clean_record()
            change(record["entries"])
            return record

        def later_window(entries):
            # A valid-looking window one session later than the issue and record times give.
            entries[1].update(base_session="2026-11-05", target_start="2026-11-06", target_end="2026-11-12")

        def realized(entries):
            entries[0]["realized_pct"] = 25.0

        def a_close(entries):
            entries[0]["closes"][3][1] = 105.0

        def fit(entries):
            entries[1]["coefficients"][4] += 0.01

        def roll(entries):
            entries[1].update(status="skipped_roll", closed_at="2026-11-11T13:00:00Z")

        cases = {
            "window": (later_window, "is not the one its issue and record times give"),
            "realized vol": (realized, "but its closes give"),
            "a stored close": (a_close, "but its closes give"),
            "stored fit": (fit, "its stored fit gives forecast"),
            "roll status": (roll, "skipped_roll does not match the roll rule"),
        }
        for name, (change, message) in cases.items():
            with self.subTest(name):
                code, out = run(tampered(change))
                self.assertEqual(code, 1, out)
                self.assertIn(message, out)
                self.assertIn("check(s) failed", out)

    def test_a_resolved_window_across_a_roll_is_caught(self):
        record = clean_record()
        entry = record["entries"][0]
        with mock.patch.object(v, "spans_roll", return_value=True):
            facts, problems = v.check_entry(entry)
        self.assertTrue(facts["spans_roll"])
        self.assertIn("resolved although its window spans a contract roll", problems)

    def test_a_record_that_breaks_its_contract_fails(self):
        bad = clean_record()
        bad["entries"][0]["sealed_at"] = "2026-11-03T19:28:00Z"      # 2 minutes before its window opened
        for record in (bad, "{", {"entries": []}):
            with self.subTest(record=str(record)[:40]):
                code, out = run(record)
                self.assertEqual(code, 1)
                self.assertIn("FAIL: the record does not satisfy its contract", out)

    def test_reads_standard_input(self):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(clean_record()))), redirect_stdout(out):
            self.assertEqual(v.main(["-"]), 0)
        self.assertIn("2 entries", out.getvalue())

    def test_latest_limits_the_rows_printed_not_the_checks(self):
        record = clean_record()
        record["entries"][0]["realized_pct"] = 25.0
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = v.main([str(path), "--latest", "1"])
        self.assertEqual(code, 1)
        self.assertNotIn("2026-11-02    ", out.getvalue().split("fit\n", 1)[1].split("har-iv")[0])
        self.assertIn("FAIL 2026-11-02", out.getvalue())

    def test_recomputed_forecast_matches_the_backend_arithmetic(self):
        entry = copy.deepcopy(clean_record()["entries"][0])
        median, forecast = v.recomputed_forecast(entry)
        self.assertAlmostEqual(median, 47.5, delta=v.PRINT_TOLERANCE)
        self.assertAlmostEqual(forecast, 56.4, delta=v.PRINT_TOLERANCE)
        self.assertIsNone(v.recomputed_forecast({**entry, "coefficients": FIT[:4]}))
        self.assertIsNone(v.recomputed_forecast({**entry, "inputs": {**INPUTS, "ovx": None}}))
        self.assertEqual(timedelta(minutes=5), vr.SEAL_MARGIN)


if __name__ == "__main__":
    unittest.main()
