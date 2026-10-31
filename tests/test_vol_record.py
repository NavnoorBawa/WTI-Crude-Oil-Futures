"""Network-free, clock-independent tests for backend/vol_record.py, the live vol forecast record.

This module decides which forecast is recorded, which price window it is scored on, whether it was
committed before that window opened, when it is settled, skipped or expired, whether the challenger
is promoted, and what the dashboard reports. Every test injects `now`; nothing reads the wall clock
or the network.

Calendar facts used below (backend/contract_calendar.py, asserted in CalendarFactsTest): CLX26 last
trades 2026-10-20 and CLZ26 2026-11-20, so CL=F's daily bars roll on 2026-10-21 and 2026-11-23.
US daylight saving time ends 2026-11-01 (14:30 ET is 18:30Z before, 19:30Z after). Thanksgiving is
2026-11-26.
"""

import ast
import copy
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scipy import stats

from backend import contract_calendar, safe_paths
from backend import vol_record as vr

UTC = timezone.utc
D = date.fromisoformat
ROOT = Path(__file__).resolve().parents[1]


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def live(as_of="2026-11-02", issued_at="2026-11-03T13:00:00Z", forecast=40.0, median=36.0,
         lo=25.0, hi=55.0, direction="RISING", rv5=30.0, challenger="default", **extra):
    block = {
        "forecast_next_week_vol_annualized_pct": forecast,
        "forecast_median_pct": median,
        "prob_vol_rises_pct": 58.0,
        "direction": direction,
        "current_realized_vol_5d_annualized_pct": round(rv5, 1) if rv5 else None,
        "interval_80": {"low_pct": lo, "high_pct": hi, "method": "aci"},
        "model": "HAR-IV (RV5,RV22,RV66,OVX)",
        "model_version": "har-iv/2",
        "as_of": as_of,
        "issued_at": issued_at,
        "inputs": {"rv5_pct": rv5, "rv22_pct": 33.0, "rv66_pct": 35.0, "ovx": 41.0,
                   "ovx_session": "2026-10-30", "ovx_last_print": as_of, "ovx_source": "yahoo ^OVX"},
        "challenger": ({"key": "har_iv_range", "label": "HAR-IV + range", "as_of": as_of,
                        "forecast_next_week_vol_annualized_pct": forecast - 2, "forecast_median_pct": median - 2}
                       if challenger == "default" else challenger),
        "coefficients": list(FIT),
        "smearing_factor": 1.4128,
        "residual_sd": 0.4078,
    }
    block.update(extra)
    return block


# The live fit's intercept and slopes (ln RV5, ln RV22, ln RV66, ln OVX), as vol_forecast publishes them.
FIT = (-4.883566, 0.038819, 0.170853, -0.151549, 1.03495)


def bars(closes: dict, extra_points=()):
    """unified_data.actual with daily bars stamped at the session's midnight ET, plus noise points."""
    points = [(f"{day}T05:00:00Z", price, "1d") for day, price in closes.items()]
    points += list(extra_points)
    points.sort(key=lambda p: p[0])
    return {"actual": {"values": [p[1] for p in points], "timestamps": [p[0] for p in points],
                       "volumes": [0] * len(points), "kinds": [p[2] for p in points]}}


def payload(live_block=None, closes=None, frozen_at="2026-11-03T13:02:00Z", extra_points=()):
    data = {"frozen_at": frozen_at, "contract": {"symbol": "CLZ26"},
            "unified_data": bars(closes or {}, extra_points)}
    if live_block is not None:
        data["vol_forecast"] = {"live": live_block, "validation": {
            "direction_noise_floor": {"sims": 5, "dir_acc_pct_mean": 75.5, "dir_acc_pct_range": [74.1, 76.5]}}}
    return data


def empty_record(started="2026-11-03"):
    return {"schema_version": 1, "started": started, "entries": []}


def recorded(as_of="2026-11-02", issued_at="2026-11-03T13:00:00Z", now="2026-11-03T13:05:00Z",
             seal_after=timedelta(minutes=10), **kwargs):
    """A record holding one pending entry, made by the real recorder and, unless `seal_after` is None,
    sealed that long after it was recorded (the publish job's commit)."""
    record = empty_record()
    message = vr.record_forecast(record, payload(live(as_of=as_of, issued_at=issued_at, **kwargs)), at(now))
    assert message.startswith("recorded"), message
    if seal_after is not None:
        vr.seal_entries(record, at(now) + seal_after)
    return record


def resolve(record, closes, snapshot):
    """One resolution pass on a payload frozen at `snapshot` that carries `closes` as daily bars."""
    cutoff = vr.bars_cutoff(at(snapshot))
    return vr.resolve_entries(record, vr.completed_closes(payload(None, closes), cutoff), cutoff, at(snapshot))


# Closes for the 2026-11-03 .. 2026-11-10 window (k0 = Tue 2026-11-03).
WINDOW = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"]
ZIGZAG = dict(zip(WINDOW, [100.0, 101.0, 100.0, 101.0, 100.0, 101.0]))
ZIGZAG_VOL = math.log(1.01) * math.sqrt(252) * 100     # every |r| = ln(1.01)


class CalendarFactsTest(unittest.TestCase):
    def test_the_facts_the_other_tests_rely_on(self):
        self.assertEqual(contract_calendar.last_trade_date(2026, 11), D("2026-10-20"))
        self.assertEqual(contract_calendar.last_trade_date(2026, 12), D("2026-11-20"))
        self.assertIn(D("2026-11-26"), contract_calendar.nymex_holidays(2026))
        self.assertEqual(vr.settlement_time(D("2026-10-30")).astimezone(UTC), at("2026-10-30T18:30:00Z"))
        self.assertEqual(vr.settlement_time(D("2026-11-02")).astimezone(UTC), at("2026-11-02T19:30:00Z"))


class TargetWindowTest(unittest.TestCase):
    def window(self, issued):
        w = vr.target_window(at(issued))
        return w["base_session"].isoformat(), w["target_start"].isoformat(), w["target_end"].isoformat()

    def test_issued_before_the_settlement_opens_the_window_that_day(self):
        # Tue 2026-11-03 08:00 ET: that afternoon's settlement is the first one after issuance.
        self.assertEqual(self.window("2026-11-03T13:00:00Z"), ("2026-11-03", "2026-11-04", "2026-11-10"))
        self.assertEqual(self.window("2026-11-03T19:29:59Z"), ("2026-11-03", "2026-11-04", "2026-11-10"))

    def test_issued_at_or_after_the_settlement_waits_for_the_next_one(self):
        # 14:30:00 ET is not "later than" the settlement: the window opens at the next session.
        self.assertEqual(self.window("2026-11-03T19:30:00Z"), ("2026-11-04", "2026-11-05", "2026-11-11"))
        self.assertEqual(self.window("2026-11-03T22:00:00Z"), ("2026-11-04", "2026-11-05", "2026-11-11"))

    def test_settlement_cutoff_follows_daylight_saving_time(self):
        # 19:00Z is 15:00 EDT on Friday 2026-10-30 (after settlement) but 14:00 EST on Monday 2026-11-02.
        self.assertEqual(self.window("2026-10-30T19:00:00Z")[0], "2026-11-02")
        self.assertEqual(self.window("2026-11-02T19:00:00Z")[0], "2026-11-02")
        self.assertEqual(self.window("2026-10-30T18:29:00Z")[0], "2026-10-30")

    def test_weekends_roll_to_monday(self):
        self.assertEqual(self.window("2026-11-06T21:00:00Z"), ("2026-11-09", "2026-11-10", "2026-11-16"))
        self.assertEqual(self.window("2026-11-07T15:00:00Z"), ("2026-11-09", "2026-11-10", "2026-11-16"))
        self.assertEqual(self.window("2026-11-08T23:30:00Z"), ("2026-11-09", "2026-11-10", "2026-11-16"))

    def test_holidays_are_not_sessions(self):
        # Wed before Thanksgiving, after settlement: Thursday is a holiday, Friday opens the window.
        self.assertEqual(self.window("2026-11-25T20:00:00Z"), ("2026-11-27", "2026-11-30", "2026-12-04"))
        # A window across Thanksgiving skips it: 11-24, 11-25, 11-27, 11-30, 12-01.
        self.assertEqual(self.window("2026-11-23T15:00:00Z"), ("2026-11-23", "2026-11-24", "2026-12-01"))
        self.assertEqual([d.isoformat() for d in vr.window_sessions(D("2026-11-23"))],
                         ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30", "2026-12-01"])

    def test_naive_times_are_refused(self):
        with self.assertRaises(ValueError):
            vr.first_settlement_after(datetime(2026, 11, 3, 13, 0))


class RecordingTest(unittest.TestCase):
    def test_records_everything_needed_to_audit_and_score(self):
        record = recorded(seal_after=None)
        (entry,) = record["entries"]
        self.assertEqual(entry["as_of"], "2026-11-02")
        self.assertEqual(entry["issued_at"], "2026-11-03T13:00:00Z")
        self.assertEqual(entry["recorded_at"], "2026-11-03T13:05:00Z")
        self.assertIsNone(entry["sealed_at"])            # the publish job seals it, right before its commit
        self.assertEqual(entry["frozen_at"], "2026-11-03T13:02:00Z")
        self.assertEqual(entry["contract"], "CLZ26")
        self.assertEqual(entry["model_version"], "har-iv/2")
        self.assertEqual((entry["forecast_pct"], entry["median_pct"]), (40.0, 36.0))
        self.assertEqual((entry["lo80_pct"], entry["hi80_pct"]), (25.0, 55.0))
        self.assertEqual((entry["direction"], entry["rv5_pct"], entry["prob_vol_rises_pct"]), ("RISING", 30.0, 58.0))
        self.assertEqual(entry["inputs"]["ovx_session"], "2026-10-30")
        self.assertEqual((entry["coefficients"], entry["smearing_factor"]), (list(FIT), 1.4128))
        self.assertNotIn("residual_sd", entry)
        self.assertEqual(entry["challenger"], {"key": "har_iv_range", "forecast_pct": 38.0, "median_pct": 34.0})
        self.assertEqual((entry["base_session"], entry["target_start"], entry["target_end"]),
                         ("2026-11-03", "2026-11-04", "2026-11-10"))
        self.assertEqual(entry["status"], "pending")
        self.assertIsNone(entry["realized_pct"])
        vr.validate_record(record)

    def test_one_entry_per_session_and_the_first_issuance_wins(self):
        record = recorded()
        original = copy.deepcopy(record)
        later = payload(live(issued_at="2026-11-03T17:00:00Z", forecast=55.0))
        message = vr.record_forecast(record, later, at("2026-11-03T17:05:00Z"))
        self.assertIn("already recorded", message)
        self.assertEqual(record, original)
        # An older session is stale information and is not appended either.
        older = payload(live(as_of="2026-10-30", issued_at="2026-11-03T18:00:00Z"))
        self.assertIn("older than", vr.record_forecast(record, older, at("2026-11-03T18:05:00Z")))
        self.assertEqual(record, original)
        # The next session is a new entry.
        nxt = payload(live(as_of="2026-11-03", issued_at="2026-11-04T13:00:00Z"))
        self.assertTrue(vr.record_forecast(record, nxt, at("2026-11-04T13:05:00Z")).startswith("recorded"))
        self.assertEqual([e["as_of"] for e in record["entries"]], ["2026-11-02", "2026-11-03"])

    def test_the_window_opens_after_the_forecast_is_issued_and_recorded(self):
        # Issued 08:00 ET, recorded 08:05 ET: the 14:30 ET settlement is hours later.
        self.assertEqual(recorded()["entries"][0]["base_session"], "2026-11-03")
        # Issued 13:00 ET but recorded at 13:45 ET: the git commit could land after 14:30 ET, so the
        # window may not open at that settlement (COMMIT_MARGIN).
        late = recorded(issued_at="2026-11-03T18:00:00Z", now="2026-11-03T18:45:00Z")
        self.assertEqual(late["entries"][0]["base_session"], "2026-11-04")
        # Issued well before, recorded after the settlement: the next session opens the window.
        stale = recorded(issued_at="2026-11-03T13:00:00Z", now="2026-11-03T20:00:00Z")
        self.assertEqual(stale["entries"][0]["base_session"], "2026-11-04")
        # Issued 13:20 ET, recorded 13:25 ET: settlement in 65 minutes, outside the margin.
        close = recorded(issued_at="2026-11-03T18:20:00Z", now="2026-11-03T18:25:00Z")
        self.assertEqual(close["entries"][0]["base_session"], "2026-11-03")

    def test_unusable_forecasts_are_not_recorded(self):
        cases = {
            "no forecast block": payload(None),
            "session still trading": payload(live(as_of="2026-11-03")),
            "holiday session": payload(live(as_of="2026-11-26", issued_at="2026-11-27T13:00:00Z")),
            "issued in the future": payload(live(issued_at="2026-11-03T14:00:00Z")),
            "naive issued_at": payload(live(issued_at="2026-11-03T13:00:00")),
            "zero forecast": payload(live(forecast=0.0)),
            "boolean forecast": payload(live(forecast=True)),
            "no model version": payload(live(model_version="")),
            "non-finite input": payload(live(rv5=float("nan"))),
        }
        for name, data in cases.items():
            with self.subTest(name):
                record = empty_record()
                now = at("2026-11-27T13:05:00Z") if name == "holiday session" else at("2026-11-03T13:05:00Z")
                self.assertTrue(vr.record_forecast(record, data, now).startswith("not recorded"))
                self.assertEqual(record["entries"], [])

    def test_optional_fields_are_kept_only_when_valid(self):
        entry = recorded(lo=60.0, hi=50.0, direction="UP",
                         challenger={"key": "garch", "as_of": "2026-10-30",
                                     "forecast_next_week_vol_annualized_pct": 41.0})["entries"][0]
        self.assertIsNone(entry["lo80_pct"])
        self.assertIsNone(entry["hi80_pct"])
        self.assertIsNone(entry["direction"])
        self.assertIsNone(entry["challenger"])          # a challenger for another session is not this one's
        no_inputs = recorded(inputs=None, rv5=31.2)["entries"][0]
        self.assertEqual(no_inputs["rv5_pct"], 31.2)    # falls back to the rounded current RV5
        vr.validate_record({"schema_version": 1, "started": "2026-11-03", "entries": [entry]})

    def test_the_fit_is_stored_when_valid_and_never_decides_recording(self):
        # The plain-HAR fallback has four coefficients; integers are numbers too.
        har = recorded(coefficients=[-3.2, 0.4, 0.3, 1])["entries"][0]
        self.assertEqual(har["coefficients"], [-3.2, 0.4, 0.3, 1.0])
        bad_fits = {
            "missing": {"coefficients": None, "smearing_factor": None},
            "not a list": {"coefficients": {"b0": 1.0}},
            "empty": {"coefficients": []},
            "too long": {"coefficients": [0.1] * (vr.MAX_FIT_TERMS + 1)},
            "boolean": {"coefficients": [True, 0.1, 0.2, 0.3, 0.4]},
            "text": {"coefficients": ["-4.88", 0.1, 0.2, 0.3, 0.4]},
            "nested": {"coefficients": [[1.0], 0.1, 0.2, 0.3, 0.4]},
        }
        for name, fit in bad_fits.items():
            with self.subTest(name):
                entry = recorded(**fit)["entries"][0]
                self.assertIsNone(entry["coefficients"])
                self.assertEqual(entry["status"], "pending")
        for smearing in (None, 0, -1.2, True, "1.41"):
            with self.subTest(smearing=smearing):
                entry = recorded(smearing_factor=smearing)["entries"][0]
                self.assertIsNone(entry["smearing_factor"])
                self.assertEqual(entry["coefficients"], list(FIT))
        # A non-finite coefficient makes the payload invalid JSON for the record; the entry keeps no fit.
        nan = recorded(coefficients=[float("nan"), 0.1, 0.2, 0.3, 0.4])["entries"][0]
        self.assertIsNone(nan["coefficients"])

    def test_the_stored_fit_reproduces_the_recorded_median_and_forecast(self):
        # median = 100 exp(b0 + b1 ln RV5 + b2 ln RV22 + b3 ln RV66 + b4 ln OVX), RV in decimals and
        # OVX in points; forecast = median * sqrt(smearing). The Oct 27 2026 live call: 47.5 and 56.4.
        inputs = {"rv5_pct": 48.65, "rv22_pct": 37.69, "rv66_pct": 45.95, "ovx": 58.71,
                  "ovx_session": "2026-10-30", "ovx_last_print": "2026-11-02", "ovx_source": "yahoo ^OVX"}
        entry = recorded(inputs=inputs, forecast=56.4, median=47.5)["entries"][0]
        b, x = entry["coefficients"], entry["inputs"]
        log_median = b[0] + sum(bk * math.log(v) for bk, v in zip(
            b[1:], (x["rv5_pct"] / 100, x["rv22_pct"] / 100, x["rv66_pct"] / 100, x["ovx"])))
        median = 100 * math.exp(log_median)
        self.assertAlmostEqual(median, entry["median_pct"], delta=0.05)
        self.assertAlmostEqual(median * math.sqrt(entry["smearing_factor"]), entry["forecast_pct"], delta=0.05)


class BarsTest(unittest.TestCase):
    def test_only_completed_daily_sessions_are_used(self):
        closes = {"2026-11-03": 80.0, "2026-11-04": 81.0, "2026-11-26": 79.0, "2026-11-05": 82.0}
        extra = [("2026-11-04T15:00:00Z", 81.5, "1h"), ("2026-11-04T16:00:00Z", 90.0, "live"),
                 ("2026-11-02T05:00:00Z", -1.0, "1d"), ("not-a-time", 70.0, "1d"),
                 ("2026-11-05T04:00:00Z", 81.9, "1d")]
        data = payload(None, closes, extra_points=extra)
        got = vr.completed_closes(data, D("2026-11-30"))
        # 11-26 is Thanksgiving (a holiday partial), the hourly and live points are not sessions, a
        # non-positive close is not a price, and of two bars for one session the later one stands.
        self.assertEqual(got, {D("2026-11-03"): 80.0, D("2026-11-04"): 81.0, D("2026-11-05"): 82.0})
        # Bars after the cutoff are the session still trading.
        self.assertEqual(set(vr.completed_closes(data, D("2026-11-04"))), {D("2026-11-03"), D("2026-11-04")})

    def test_forecaster_closes_take_priority_and_chart_bars_fill_gaps(self):
        data = payload(None, {"2026-11-03": 80.0, "2026-11-04": 81.0})
        data["vol_forecast"] = {"recent_closes": {"date": ["2026-11-04", "2026-11-05", "2026-11-26", "bad"],
                                                  "close": [81.2, 82.0, 79.0, 70.0]}}
        got = vr.completed_closes(data, D("2026-11-30"))
        # The forecaster's own close wins over the chart bar for 11-04; the chart fills 11-03; the
        # holiday and the malformed date are dropped.
        self.assertEqual(got, {D("2026-11-03"): 80.0, D("2026-11-04"): 81.2, D("2026-11-05"): 82.0})
        self.assertEqual(set(vr.completed_closes(data, D("2026-11-04"))), {D("2026-11-03"), D("2026-11-04")})

    def test_bars_without_kinds_are_not_trusted(self):
        data = payload(None, {"2026-11-03": 80.0})
        del data["unified_data"]["actual"]["kinds"]
        self.assertEqual(vr.completed_closes(data, D("2026-11-30")), {})

    def test_cutoff_allows_for_bars_fetched_before_the_freeze_stamp(self):
        # Friday 17:30 ET: the session closed at 17:00, but bars fetched an hour earlier were live.
        self.assertEqual(vr.bars_cutoff(at("2026-11-06T22:30:00Z")), D("2026-11-05"))
        self.assertEqual(vr.bars_cutoff(at("2026-11-07T00:30:00Z")), D("2026-11-06"))   # Fri 19:30 ET
        # Tue 19:00 ET: after the reopen today's bar holds Wednesday's prices.
        self.assertEqual(vr.bars_cutoff(at("2026-11-04T00:00:00Z")), D("2026-11-02"))
        self.assertEqual(vr.bars_cutoff(at("2026-11-04T06:00:00Z")), D("2026-11-03"))   # Wed 01:00 ET

    def test_snapshot_is_the_freeze_time_never_later_than_the_run(self):
        now = at("2026-11-04T06:00:00Z")
        self.assertEqual(vr._snapshot_time({"frozen_at": "2026-11-03T21:00:00Z"}, now), at("2026-11-03T21:00:00Z"))
        self.assertEqual(vr._snapshot_time({"frozen_at": "2026-11-05T00:00:00Z"}, now), now)
        self.assertEqual(vr._snapshot_time({"frozen_at": None}, now), now)


class ResolutionTest(unittest.TestCase):
    def resolve(self, record, closes, snapshot):
        return resolve(record, closes, snapshot)

    def test_realized_vol_math(self):
        self.assertAlmostEqual(vr.realized_vol_pct(list(ZIGZAG.values())), ZIGZAG_VOL, places=9)
        closes = [100.0, 102.0, 99.0, 99.0, 101.0, 100.0]
        r = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        expected = math.sqrt(sum(x * x for x in r) / 5 * 252) * 100
        self.assertAlmostEqual(vr.realized_vol_pct(closes), expected, places=9)

    def test_resolves_on_the_six_closes_of_its_window(self):
        record = recorded()
        noisy = dict(ZIGZAG, **{"2026-11-02": 500.0, "2026-11-11": 1.0})   # outside the window
        self.assertEqual(len(self.resolve(record, noisy, "2026-11-11T12:00:00Z")), 1)
        entry = record["entries"][0]
        self.assertEqual(entry["status"], "resolved")
        self.assertAlmostEqual(entry["realized_pct"], ZIGZAG_VOL, places=3)
        self.assertEqual(entry["closes"], [[d, p] for d, p in ZIGZAG.items()])
        self.assertEqual(entry["resolved_at"], "2026-11-11T12:00:00Z")
        vr.validate_record(record)
        # A resolved entry is final.
        changed = dict(ZIGZAG, **{"2026-11-10": 150.0})
        self.assertEqual(self.resolve(record, changed, "2026-11-12T12:00:00Z"), [])
        self.assertAlmostEqual(entry["realized_pct"], ZIGZAG_VOL, places=3)

    def test_waits_for_every_close(self):
        record = recorded()
        missing_base = {d: p for d, p in ZIGZAG.items() if d != "2026-11-03"}
        self.assertEqual(self.resolve(record, missing_base, "2026-11-11T12:00:00Z"), [])
        self.assertEqual(record["entries"][0]["status"], "pending")
        # Tue 2026-11-10 15:00 ET: the bar dated 11-10 is the session still trading.
        self.assertEqual(self.resolve(record, ZIGZAG, "2026-11-10T20:00:00Z"), [])
        self.assertEqual(record["entries"][0]["status"], "pending")

    def test_a_window_across_a_contract_roll_is_skipped(self):
        # CLZ26 last trades Fri 2026-11-20; Mon 11-23's CL=F return books the Z/F spread.
        cases = {
            "2026-11-13T14:00:00Z": ("2026-11-13", "pending"),       # returns 11-16 .. 11-20
            "2026-11-16T14:00:00Z": ("2026-11-16", "skipped_roll"),  # returns 11-17 .. 11-23
            "2026-11-20T14:00:00Z": ("2026-11-20", "skipped_roll"),  # returns 11-23 .. 11-30
            "2026-11-23T14:00:00Z": ("2026-11-23", "pending"),       # returns 11-24 .. 12-01
        }
        for issued, (base, status) in cases.items():
            with self.subTest(issued=issued):
                as_of = contract_calendar.shift_business_days(D(base), -1).isoformat()
                now = (at(issued) + timedelta(minutes=5)).isoformat()
                record = recorded(as_of=as_of, issued_at=issued, now=now)
                self.assertEqual(record["entries"][0]["base_session"], base)
                # The next result is dated only by a window resolution will score, not skip.
                self.assertEqual(vr.summarize(record)["next_result_due"],
                                 record["entries"][0]["target_end"] if status == "pending" else None)
                # The summary says so on the row while it is still pending, and after it is closed.
                self.assertIs(vr.summarize(record)["recent"][0]["spans_roll"], status == "skipped_roll")
                self.resolve(record, {}, now)
                self.assertIs(vr.summarize(record)["recent"][0]["spans_roll"], status == "skipped_roll")
                self.assertEqual(record["entries"][0]["status"], status)
                self.assertIsNone(record["entries"][0]["realized_pct"])

    def test_expires_when_closes_stay_missing(self):
        record = recorded()
        partial = {d: p for d, p in ZIGZAG.items() if d != "2026-11-05"}
        self.assertEqual(self.resolve(record, partial, "2026-11-25T12:00:00Z"), [])    # cutoff 11-24: 10 sessions
        self.assertEqual(record["entries"][0]["status"], "pending")
        self.assertEqual(len(self.resolve(record, partial, "2026-11-26T12:00:00Z")), 1)  # cutoff 11-25: 11
        entry = record["entries"][0]
        self.assertEqual(entry["status"], "expired")
        self.assertEqual(entry["missing_sessions"], ["2026-11-05"])
        self.assertIsNone(entry["realized_pct"])
        vr.validate_record(record)


class SealTest(unittest.TestCase):
    """The publish job seals the record right before committing it; only entries sealed in time are scored.

    recorded() opens its window at the settlement of Tue 2026-11-03, 14:30 EST = 19:30Z, so a seal
    is in time strictly before 19:25Z (SEAL_MARGIN).
    """

    def test_a_seal_in_time_keeps_the_entry_pending_and_scorable(self):
        record = recorded(seal_after=None)
        self.assertEqual(len(vr.seal_entries(record, at("2026-11-03T19:24:59Z"))), 1)
        entry = record["entries"][0]
        self.assertEqual((entry["status"], entry["sealed_at"]), ("pending", "2026-11-03T19:24:59Z"))
        self.assertTrue(vr.sealed_in_time(entry))
        vr.validate_record(record)
        self.assertEqual(len(resolve(record, ZIGZAG, "2026-11-11T12:00:00Z")), 1)
        self.assertEqual(entry["status"], "resolved")
        vr.validate_record(record)

    def test_a_late_seal_is_a_late_commit_and_never_scored(self):
        for sealed_at in ("2026-11-03T19:25:00Z", "2026-11-03T19:30:00Z", "2026-11-03T23:00:00Z"):
            with self.subTest(sealed_at=sealed_at):
                record = recorded(seal_after=None)
                (message,) = vr.seal_entries(record, at(sealed_at))
                self.assertIn("late_commit", message)
                entry = record["entries"][0]
                self.assertEqual((entry["status"], entry["sealed_at"], entry["closed_at"]),
                                 ("late_commit", sealed_at, sealed_at))
                vr.validate_record(record)
                # Every close of its window arrives; it stays closed and unscored.
                self.assertEqual(resolve(record, ZIGZAG, "2026-11-11T12:00:00Z"), [])
                self.assertEqual(entry["status"], "late_commit")
                self.assertIsNone(entry["realized_pct"])
                summary = vr.summarize(record)
                self.assertEqual((summary["n_late_commit"], summary["n_resolved"], summary["n_pending"]), (1, 0, 0))
                self.assertIsNone(summary["champion"]["qlike"])
                self.assertEqual(summary["recent"][0]["status"], "late_commit")

    def test_an_entry_never_sealed_is_never_scored(self):
        record = recorded(seal_after=None)
        # The build job's own new entry: the seal can still be in time, so resolution leaves it.
        self.assertEqual(resolve(record, {}, "2026-11-03T19:24:59Z"), [])
        self.assertEqual(record["entries"][0]["status"], "pending")
        # Once it cannot (here a week later, with every close in), it is closed unscored.
        (message,) = resolve(record, ZIGZAG, "2026-11-11T12:00:00Z")
        self.assertIn("late_commit", message)
        entry = record["entries"][0]
        self.assertEqual((entry["status"], entry["sealed_at"], entry["realized_pct"]), ("late_commit", None, None))
        self.assertEqual(entry["closed_at"], "2026-11-11T12:00:00Z")
        vr.validate_record(record)
        # Its eventual seal records when it was committed; it stays closed.
        vr.seal_entries(record, at("2026-11-11T12:30:00Z"))
        self.assertEqual((entry["status"], entry["sealed_at"]), ("late_commit", "2026-11-11T12:30:00Z"))
        vr.validate_record(record)
        self.assertEqual(resolve(record, ZIGZAG, "2026-11-12T12:00:00Z"), [])
        self.assertEqual((vr.summarize(record)["n_late_commit"], vr.summarize(record)["n_resolved"]), (1, 0))

    def test_the_margin_boundary_also_applies_to_unsealed_entries(self):
        record = recorded(seal_after=None)
        self.assertEqual(len(resolve(record, {}, "2026-11-03T19:25:00Z")), 1)
        self.assertEqual(record["entries"][0]["status"], "late_commit")

    def test_sealing_again_changes_nothing(self):
        record = recorded()                                   # sealed at 13:15Z
        before = copy.deepcopy(record)
        self.assertEqual(vr.seal_entries(record, at("2026-11-03T19:29:00Z")), [])
        self.assertEqual(record, before)
        # The next session's entry is sealed on its own; the earlier seal stands.
        nxt = payload(live(as_of="2026-11-03", issued_at="2026-11-04T13:00:00Z"))
        vr.record_forecast(record, nxt, at("2026-11-04T13:05:00Z"))
        self.assertEqual(len(vr.seal_entries(record, at("2026-11-04T13:20:00Z"))), 1)
        self.assertEqual([e["sealed_at"] for e in record["entries"]],
                         ["2026-11-03T13:15:00Z", "2026-11-04T13:20:00Z"])
        self.assertEqual(vr.seal_entries(record, at("2026-11-04T14:00:00Z")), [])
        vr.validate_record(record)

    def test_the_seeded_record_seals_to_itself(self):
        record = empty_record()
        self.assertEqual(vr.seal_entries(record, at("2026-11-03T13:00:00Z")), [])
        self.assertEqual(record, empty_record())

    def test_naive_seal_times_are_refused(self):
        with self.assertRaises(ValueError):
            vr.seal_entries(recorded(seal_after=None), datetime(2026, 11, 3, 13, 20))


def resolved_entry(as_of, base, realized, forecast=40.0, lo=25.0, hi=55.0, direction="RISING", rv5=30.0,
                   challenger=38.0, challenger_key="har_iv_range"):
    sessions = vr.window_sessions(D(base))
    return {
        "as_of": as_of, "issued_at": f"{base}T13:00:00Z", "recorded_at": f"{base}T13:05:00Z",
        "sealed_at": f"{base}T13:15:00Z",
        "model_version": "har-iv/2", "forecast_pct": forecast, "median_pct": forecast - 4,
        "lo80_pct": lo, "hi80_pct": hi, "direction": direction, "rv5_pct": rv5,
        "challenger": {"key": challenger_key, "forecast_pct": challenger, "median_pct": None} if challenger else None,
        "base_session": base, "target_start": sessions[1].isoformat(), "target_end": sessions[-1].isoformat(),
        "status": "resolved", "realized_pct": realized, "resolved_at": "2026-12-31T12:00:00Z",
        "closes": [[d.isoformat(), 80.0] for d in sessions],
    }


# The pair the promotion test is pinned to, as summarize() publishes it, and a record with nothing
# left out of that pair's comparison.
PINNED = {"model_version": "har-iv/2", "challenger_key": "har_iv_range"}
NO_EXCLUSIONS = {"n_excluded_other_version": 0, "n_excluded_other_challenger": 0, "n_excluded_no_challenger": 0}
ACCRUING_EMPTY = {"status": "accruing", "n_independent_paired": 0, "required": 52, "dm_stat": None, "p_value": None,
                  "mean_qlike_difference": None, **PINNED, **NO_EXCLUSIONS}


def fallback(entry):
    """`entry` as the forecaster publishes it without a lagged OVX print: plain HAR ("har/2"), with no
    challenger and no interval."""
    return dict(entry, model_version="har/2", challenger=None, lo80_pct=None, hi80_pct=None)


class SummaryTest(unittest.TestCase):
    SHAPE = {"started", "n_recorded", "n_resolved", "n_pending", "n_skipped_roll", "n_expired", "n_late_commit",
             "n_independent", "next_result_due", "champion", "challenger", "promotion", "chain", "recent",
             "promotion_rule", "note", "direction_note"}
    PROMOTION = {"status", "n_independent_paired", "required", "dm_stat", "p_value", "mean_qlike_difference",
                 "model_version", "challenger_key", "n_excluded_other_version", "n_excluded_other_challenger",
                 "n_excluded_no_challenger"}
    CHAMPION = {"model_version", "n", "qlike", "interval_80_coverage_pct", "direction_hit_pct",
                "mean_forecast_pct", "mean_realized_pct"}
    RECENT = {"as_of", "issued_at", "recorded_at", "sealed_at", "model_version", "base_session", "target_start",
              "target_end", "forecast_pct", "median_pct", "lo80_pct", "hi80_pct", "prob_vol_rises_pct",
              "challenger_pct", "realized_pct", "inputs", "coefficients", "smearing_factor", "spans_roll", "status"}
    RECENT_INPUTS = {"ovx", "ovx_session", "rv5_pct", "rv22_pct", "rv66_pct"}

    def test_empty_record_has_the_contract_shape_with_null_statistics(self):
        summary = vr.summarize(empty_record())
        self.assertEqual(set(summary), self.SHAPE)
        self.assertEqual(set(summary["champion"]), self.CHAMPION)
        self.assertEqual(set(summary["challenger"]), {"key", "qlike", "n", "champion_qlike"})
        self.assertEqual(summary["started"], "2026-11-03")
        self.assertEqual([summary[k] for k in ("n_recorded", "n_resolved", "n_pending", "n_independent")], [0] * 4)
        # The pinned pair is named before any entry exists; every statistic is null.
        self.assertEqual((summary["champion"]["model_version"], summary["champion"]["n"]), ("har-iv/2", 0))
        self.assertTrue(all(v is None for k, v in summary["champion"].items() if k not in ("model_version", "n")))
        self.assertEqual(summary["challenger"], {"key": "har_iv_range", "qlike": None, "n": 0, "champion_qlike": None})
        self.assertEqual(summary["recent"], [])
        self.assertEqual(summary["n_late_commit"], 0)
        self.assertIsNone(summary["next_result_due"])
        self.assertEqual(summary["promotion"], ACCRUING_EMPTY)
        self.assertEqual(summary["chain"], {"n_linked": 0, "breaks": []})
        self.assertEqual(summary["promotion_rule"],
                         "The promotion test is pinned to one pair: HAR-IV v2 (model version har-iv/2), the model "
                         "of record, against the HAR-IV-range challenger (key har_iv_range). The challenger replaces "
                         "the model of record if, on the first 52 independent resolved weeks where both were "
                         "forecast, its mean QLIKE is lower with a one-sided Diebold-Mariano p below 0.10 "
                         "(Harvey-Leybourne-Newbold, Student t with 51 degrees of freedom). The test is run once, on "
                         "that fixed sample. Forecasts from another model version or challenger are recorded but "
                         "never enter it; a different challenger would start a new, separately declared test. A "
                         "maintainer applies the verdict.")
        self.assertIn("settlement", summary["note"])
        self.assertIn("late_commit", summary["note"])
        json.dumps(summary, allow_nan=False)

    def test_pending_entries_leave_statistics_null(self):
        summary = vr.summarize(recorded())
        self.assertEqual((summary["n_recorded"], summary["n_pending"], summary["n_resolved"]), (1, 1, 0))
        self.assertEqual(summary["next_result_due"], "2026-11-10")      # the pending window's last session
        self.assertEqual((summary["champion"]["model_version"], summary["champion"]["n"]), ("har-iv/2", 0))
        self.assertTrue(all(v is None for k, v in summary["champion"].items() if k not in ("model_version", "n")))
        self.assertEqual(summary["challenger"]["key"], "har_iv_range")
        self.assertIsNone(summary["challenger"]["qlike"])
        self.assertEqual(summary["promotion"], ACCRUING_EMPTY)          # a pending entry is not excluded, just pending
        (row,) = summary["recent"]
        self.assertEqual(set(row), self.RECENT)
        self.assertEqual((row["status"], row["realized_pct"], row["challenger_pct"], row["model_version"]),
                         ("pending", None, 38.0, "har-iv/2"))

    def test_recent_rows_carry_the_seal_and_the_inputs_each_entry_stores(self):
        record = recorded()                                  # recorded 13:05Z, sealed ten minutes later
        (entry,) = record["entries"]
        (row,) = vr.summarize(record)["recent"]
        # Passed through as stored, so the page can check each seal against the settlement that
        # opens its window (14:30 ET on base_session).
        for key in ("issued_at", "recorded_at", "sealed_at", "base_session", "prob_vol_rises_pct"):
            self.assertEqual(row[key], entry[key], key)
        self.assertEqual((row["recorded_at"], row["sealed_at"], row["base_session"], row["prob_vol_rises_pct"]),
                         ("2026-11-03T13:05:00Z", "2026-11-03T13:15:00Z", "2026-11-03", 58.0))
        # Only the forecaster's inputs; the OVX source and last-print bookkeeping stay in the record.
        self.assertEqual(row["inputs"], {"ovx": 41.0, "ovx_session": "2026-10-30", "rv5_pct": 30.0,
                                         "rv22_pct": 33.0, "rv66_pct": 35.0})
        self.assertEqual(set(row["inputs"]), self.RECENT_INPUTS)
        # The stored fit, so the page can recompute the call and split a change between two calls.
        self.assertEqual((row["coefficients"], row["smearing_factor"]), (list(FIT), 1.4128))
        self.assertIsNone(vr.summarize(recorded(seal_after=None))["recent"][0]["sealed_at"])
        json.dumps(row, allow_nan=False)

    def test_recent_rows_of_entries_without_the_newer_fields_are_null_for_them(self):
        # Entries recorded before inputs or a probability were stored, and inputs of the wrong type,
        # still summarise: the missing fields read null and nothing else changes.
        old = resolved_entry("2026-11-02", "2026-11-03", realized=40.0)
        self.assertNotIn("inputs", old)
        self.assertNotIn("prob_vol_rises_pct", old)
        partial = dict(resolved_entry("2026-11-06", "2026-11-09", realized=45.0),
                       inputs={"ovx": "41", "rv5_pct": True, "rv22_pct": 33.0, "ovx_session": 20261030})
        record = vr.validate_record({"schema_version": 1, "started": "2026-11-02", "entries": [old, partial]})
        summary = vr.summarize(record)
        newest, oldest = summary["recent"]
        self.assertEqual(set(oldest), self.RECENT)
        self.assertEqual((oldest["inputs"], oldest["prob_vol_rises_pct"]), (None, None))
        self.assertEqual((oldest["coefficients"], oldest["smearing_factor"]), (None, None))
        self.assertEqual((oldest["sealed_at"], oldest["base_session"]), (old["sealed_at"], "2026-11-03"))
        self.assertEqual(newest["inputs"], {"ovx": None, "ovx_session": None, "rv5_pct": None, "rv22_pct": 33.0,
                                            "rv66_pct": None})
        self.assertEqual((summary["n_resolved"], summary["champion"]["n"]), (2, 2))
        json.dumps(summary, allow_nan=False)

    def test_scores(self):
        a = resolved_entry("2026-11-02", "2026-11-03", realized=40.0, rv5=30.0)            # in, rose, hit
        b = resolved_entry("2026-11-06", "2026-11-09", realized=20.0, direction="FALLING",
                           rv5=25.0, challenger=None)                                      # out, fell, hit
        c = resolved_entry("2026-11-16", "2026-11-17", realized=60.0, direction="FALLING", rv5=50.0,
                           challenger=50.0)                                                # out, rose, miss
        record = {"schema_version": 1, "started": "2026-11-03", "entries": [a, b, c]}
        vr.validate_record(record)
        s = vr.summarize(record, noise_floor_pct=75.5)
        q = [vr.qlike(40.0, 40.0), vr.qlike(20.0, 40.0), vr.qlike(60.0, 40.0)]
        self.assertEqual(q[0], 0.0)
        self.assertAlmostEqual(q[1], 0.25 - math.log(0.25) - 1)
        self.assertEqual((s["champion"]["n"], s["champion"]["qlike"]), (3, round(sum(q) / 3, 4)))
        self.assertEqual(s["champion"]["interval_80_coverage_pct"], 33.3)
        self.assertEqual(s["champion"]["direction_hit_pct"], 66.7)
        self.assertEqual(s["champion"]["mean_forecast_pct"], 40.0)
        self.assertEqual(s["champion"]["mean_realized_pct"], 40.0)
        # The challenger is scored only where it was published, against the champion on the same rows.
        self.assertEqual(s["challenger"]["n"], 2)
        self.assertEqual(s["challenger"]["qlike"], round((vr.qlike(40.0, 38.0) + vr.qlike(60.0, 50.0)) / 2, 4))
        self.assertEqual(s["challenger"]["champion_qlike"], round((q[0] + q[2]) / 2, 4))
        self.assertEqual({k: s["promotion"][k] for k in NO_EXCLUSIONS},
                         dict(NO_EXCLUSIONS, n_excluded_no_challenger=1))            # b has no challenger
        self.assertEqual((s["n_resolved"], s["n_independent"]), (3, 2))   # a and b overlap by one session
        self.assertEqual([r["as_of"] for r in s["recent"]], ["2026-11-16", "2026-11-06", "2026-11-02"])
        self.assertEqual(s["recent"][0]["realized_pct"], 60.0)
        self.assertIn("noise floor is 75.5%", s["direction_note"])
        self.assertIn("three calls in four", vr.summarize(record)["direction_note"])
        self.assertIn("not evidence of skill", s["direction_note"])

    def test_counts_by_status_and_interval_bounds_count_as_hits(self):
        on_low = resolved_entry("2026-11-02", "2026-11-03", realized=25.0)     # exactly the 80% low bound
        on_high = resolved_entry("2026-11-09", "2026-11-10", realized=55.0)    # exactly the high bound
        rolled = dict(resolved_entry("2026-11-13", "2026-11-16", 40.0), status="skipped_roll", realized_pct=None)
        expired = dict(resolved_entry("2026-11-24", "2026-11-25", 40.0), status="expired", realized_pct=None)
        pending = dict(resolved_entry("2026-11-25", "2026-11-27", 40.0), status="pending", realized_pct=None)
        late = dict(resolved_entry("2026-11-27", "2026-11-30", 40.0), status="late_commit", realized_pct=None,
                    sealed_at="2026-11-30T19:26:00Z")
        record = {"schema_version": 1, "started": "2026-11-03",
                  "entries": [on_low, on_high, rolled, expired, pending, late]}
        vr.validate_record(record)
        s = vr.summarize(record)
        self.assertEqual([s[k] for k in ("n_recorded", "n_resolved", "n_pending", "n_skipped_roll", "n_expired",
                                         "n_late_commit")],
                         [6, 2, 1, 1, 1, 1])
        self.assertEqual(s["champion"]["interval_80_coverage_pct"], 100.0)
        self.assertEqual([r["status"] for r in s["recent"]],
                         ["late_commit", "pending", "expired", "skipped_roll", "resolved", "resolved"])

    def test_another_challenger_is_recorded_but_never_compared(self):
        # The comparison is pinned to har_iv_range, whichever challenger the newest entry carries.
        for newest_key in ("har_iv_range", "combo"):
            with self.subTest(newest=newest_key):
                older_key = "combo" if newest_key == "har_iv_range" else "har_iv_range"
                older = resolved_entry("2026-11-02", "2026-11-03", realized=40.0, challenger=44.0,
                                       challenger_key=older_key)
                newer = resolved_entry("2026-11-09", "2026-11-10", realized=40.0, challenger=44.0,
                                       challenger_key=newest_key)
                s = vr.summarize({"schema_version": 1, "started": "2026-11-03", "entries": [older, newer]})
                self.assertEqual((s["challenger"]["key"], s["challenger"]["n"]), ("har_iv_range", 1))
                self.assertEqual(s["challenger"]["qlike"], round(vr.qlike(40.0, 44.0), 4))
                self.assertEqual((s["champion"]["n"], s["n_resolved"]), (2, 2))    # both are the model of record's
                self.assertEqual({k: s["promotion"][k] for k in NO_EXCLUSIONS},
                                 dict(NO_EXCLUSIONS, n_excluded_other_challenger=1))
                self.assertEqual((s["promotion"]["challenger_key"], s["promotion"]["n_independent_paired"]),
                                 ("har_iv_range", 1))

    def test_champion_statistics_cover_the_pinned_version_only(self):
        # A fallback forecast (plain HAR) and a later model version are recorded and counted, but
        # their losses, coverage and means are not pooled with the model of record's.
        pinned = resolved_entry("2026-11-02", "2026-11-03", realized=40.0, challenger=44.0)
        plain = fallback(resolved_entry("2026-11-06", "2026-11-09", realized=20.0, direction="FALLING"))
        newer = dict(resolved_entry("2026-11-16", "2026-11-17", realized=60.0, lo=50.0, hi=70.0, challenger=30.0),
                     model_version="har-iv/3")
        record = {"schema_version": 1, "started": "2026-11-03", "entries": [pinned, plain, newer]}
        vr.validate_record(record)
        s = vr.summarize(record)
        self.assertEqual(s["n_resolved"], 3)
        self.assertEqual(s["champion"], {"model_version": "har-iv/2", "n": 1, "qlike": 0.0,
                                         "interval_80_coverage_pct": 100.0, "direction_hit_pct": 100.0,
                                         "mean_forecast_pct": 40.0, "mean_realized_pct": 40.0})
        self.assertEqual(s["challenger"], {"key": "har_iv_range", "qlike": round(vr.qlike(40.0, 44.0), 4), "n": 1,
                                           "champion_qlike": 0.0})
        self.assertEqual({k: s["promotion"][k] for k in NO_EXCLUSIONS},
                         dict(NO_EXCLUSIONS, n_excluded_other_version=2))
        self.assertEqual(s["promotion"]["n_independent_paired"], 1)
        self.assertEqual([r["model_version"] for r in s["recent"]], ["har-iv/3", "har/2", "har-iv/2"])

    def test_independent_windows(self):
        # Daily forecasts: ten consecutive k0 sessions; five-session windows overlap.
        sessions = [D("2026-10-21")]
        for _ in range(9):
            sessions.append(contract_calendar.shift_business_days(sessions[-1], 1))
        entries = [resolved_entry(contract_calendar.shift_business_days(k0, -1).isoformat(), k0.isoformat(), 40.0)
                   for k0 in sessions]
        self.assertEqual(len(vr.independent_entries(entries)), 2)
        self.assertEqual([e["base_session"] for e in vr.independent_entries(entries)], ["2026-10-21", "2026-10-28"])
        # Windows sharing only an endpoint close are independent; sharing a return session is not.
        first = resolved_entry("2026-10-20", "2026-10-21", 40.0)
        touching = resolved_entry("2026-10-27", "2026-10-28", 40.0)     # opens at first's target_end
        overlapping = resolved_entry("2026-10-26", "2026-10-27", 40.0)  # its first return is first's last
        self.assertEqual(len(vr.independent_entries([first, touching])), 2)
        self.assertEqual(len(vr.independent_entries([first, overlapping])), 1)
        self.assertEqual(len(vr.independent_entries([])), 0)

    def test_next_result_due_is_the_earliest_window_resolution_will_score(self):
        done = resolved_entry("2026-11-02", "2026-11-03", 40.0)                 # window ends 11-10, resolved
        rolled = as_pending(resolved_entry("2026-11-13", "2026-11-16", 40.0))   # returns 11-17 .. 11-23: the roll
        clean = as_pending(resolved_entry("2026-11-20", "2026-11-23", 40.0))    # returns 11-24 .. 12-01
        later = as_pending(resolved_entry("2026-11-27", "2026-11-30", 40.0))    # returns 12-01 .. 12-07

        def due(*entries):
            record = {"schema_version": 1, "started": "2026-11-03", "entries": list(entries)}
            vr.validate_record(record)
            return vr.summarize(record)["next_result_due"]

        self.assertTrue(contract_calendar.spans_roll(D("2026-11-16"), D("2026-11-23")))
        self.assertEqual(due(done, rolled, clean, later), "2026-12-01")
        self.assertEqual(due(done, rolled, later), "2026-12-07")
        self.assertIsNone(due(done, rolled))            # a skipped-to-be window dates nothing
        self.assertIsNone(due(done))
        self.assertEqual(vr.next_result_due([later, clean]), "2026-12-01")    # the earliest, not the first listed

    def test_recent_is_capped_and_newest_first(self):
        record = empty_record()
        day = D("2026-10-21")
        for _ in range(15):
            issued = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=13)
            as_of = contract_calendar.shift_business_days(day, -1).isoformat()
            vr.record_forecast(record, payload(live(as_of=as_of, issued_at=issued.isoformat())),
                               issued + timedelta(minutes=5))
            day = contract_calendar.shift_business_days(day, 1)
        self.assertEqual(len(record["entries"]), 15)
        recent = vr.summarize(record)["recent"]
        self.assertEqual(len(recent), 12)
        self.assertEqual(recent[0]["as_of"], record["entries"][-1]["as_of"])
        self.assertEqual(recent[-1]["as_of"], record["entries"][3]["as_of"])


def weekly_entries(n, realized=(30.0, 36.0), challenger=(33.0, 34.0), start="2026-10-21"):
    """`n` resolved entries whose windows are back to back (each opens at the previous one's last close),
    so none overlaps another. Realized vol and the challenger's forecast cycle through the given values;
    the model of record always forecasts 40%."""
    entries, base = [], D(start)
    for i in range(n):
        entries.append(resolved_entry(contract_calendar.shift_business_days(base, -1).isoformat(), base.isoformat(),
                                      realized=realized[i % len(realized)],
                                      challenger=challenger[i % len(challenger)]))
        base = contract_calendar.shift_business_days(base, vr.HORIZON_SESSIONS)
    return entries


def as_pending(entry):
    pending = {k: v for k, v in entry.items() if k not in ("resolved_at", "closes")}
    return dict(pending, status="pending", realized_pct=None)


class PromotionTest(unittest.TestCase):
    """The promotion rule: one one-sided Diebold-Mariano test on the first 52 independent paired weeks."""

    def promotion(self, entries):
        record = {"schema_version": 1, "started": "2026-10-20", "entries": entries}
        vr.validate_record(record)
        result = vr.summarize(record)["promotion"]
        self.assertEqual(set(result), SummaryTest.PROMOTION)
        json.dumps(result, allow_nan=False)
        return result

    def test_accruing_until_52_independent_paired_weeks(self):
        result = self.promotion(weekly_entries(51))
        self.assertEqual(result, dict(ACCRUING_EMPTY, n_independent_paired=51))

    def test_a_clear_edge_on_52_weeks_promotes(self):
        entries = weekly_entries(52)
        result = self.promotion(entries)
        diffs = [vr.qlike(e["realized_pct"], 40.0) - vr.qlike(e["realized_pct"], e["challenger"]["forecast_pct"])
                 for e in entries]
        stat = statistics.mean(diffs) / (statistics.stdev(diffs) / math.sqrt(52))
        self.assertEqual((result["status"], result["n_independent_paired"], result["required"]), ("promote", 52, 52))
        self.assertGreater(result["mean_qlike_difference"], 0)
        self.assertAlmostEqual(result["mean_qlike_difference"], statistics.mean(diffs), places=6)
        self.assertAlmostEqual(result["dm_stat"], stat, places=4)
        self.assertAlmostEqual(result["p_value"], float(stats.t.sf(stat, df=51)), places=6)
        self.assertLess(result["p_value"], 0.10)

    def test_the_p_value_is_from_student_t_with_51_degrees_of_freedom(self):
        # Harvey-Leybourne-Newbold: t(n - 1), whose 10% critical value 1.2984 is above the normal's
        # 1.2816. Differences a + (-1)^i on 52 weeks have mean a and standard error 1/sqrt(51), so
        # the statistic is a * sqrt(51); qlike is patched to return the forecast, which makes each
        # difference 40 - the challenger's forecast.
        critical = float(stats.t.isf(0.10, df=51))
        self.assertAlmostEqual(critical, 1.2984, places=4)
        self.assertAlmostEqual(float(stats.norm.isf(0.10)), 1.2816, places=4)
        cases = {1.29: "keep", critical - 1e-6: "keep", critical + 1e-6: "promote", 1.31: "promote"}
        for target, verdict in cases.items():
            with self.subTest(stat=target):
                entries = weekly_entries(52)
                for i, entry in enumerate(entries):
                    entry["challenger"]["forecast_pct"] = 40.0 - (target / math.sqrt(51) + (-1) ** i)
                with mock.patch.object(vr, "qlike", lambda realized, forecast: forecast):
                    result = vr.promotion_test(entries)
                self.assertEqual(result["status"], verdict)
                self.assertAlmostEqual(result["dm_stat"], target, places=4)
                self.assertAlmostEqual(result["p_value"], float(stats.t.sf(target, df=51)), places=6)
        # Between the two critical values the normal would have promoted.
        self.assertLess(float(stats.norm.sf(1.29)), 0.10)
        self.assertGreater(float(stats.t.sf(1.29, df=51)), 0.10)

    def test_no_edge_keeps_the_model_of_record(self):
        cases = {
            "challenger worse": dict(challenger=(50.0,)),
            "small edge, not significant": dict(realized=(30.0, 50.0), challenger=(33.0, 37.0)),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                result = self.promotion(weekly_entries(52, **kwargs))
                self.assertEqual((result["status"], result["n_independent_paired"]), ("keep", 52))
                self.assertGreaterEqual(result["p_value"], 0.10)
        small = self.promotion(weekly_entries(52, **cases["small edge, not significant"]))
        self.assertGreater(small["mean_qlike_difference"], 0)             # positive, but p = 0.34
        # Identical forecasts: every difference is zero and there is nothing to test.
        same = self.promotion(weekly_entries(52, challenger=(40.0,)))
        self.assertEqual((same["status"], same["dm_stat"], same["p_value"], same["mean_qlike_difference"]),
                         ("keep", None, None, 0.0))

    def test_a_verdict_is_final(self):
        decided = self.promotion(weekly_entries(52))
        # Thirty more weeks in which the challenger is far worse, then a pending entry: one look only.
        entries = weekly_entries(83)
        for entry in entries[52:]:
            entry["challenger"]["forecast_pct"] = 80.0
        entries[-1] = as_pending(entries[-1])
        self.assertEqual(self.promotion(entries), decided)
        kept = self.promotion(weekly_entries(52, challenger=(50.0,)))
        later = weekly_entries(80, challenger=(50.0,))
        for entry in later[52:]:
            entry["challenger"]["forecast_pct"] = 36.0
        self.assertEqual(self.promotion(later), kept)

    def test_overlapping_windows_are_not_counted(self):
        # Daily forecasts: ten consecutive k0 sessions, of which two windows do not overlap.
        sessions = [D("2026-10-21")]
        for _ in range(9):
            sessions.append(contract_calendar.shift_business_days(sessions[-1], 1))
        daily = [resolved_entry(contract_calendar.shift_business_days(k0, -1).isoformat(), k0.isoformat(), 40.0)
                 for k0 in sessions]
        self.assertEqual(self.promotion(daily)["n_independent_paired"], 2)
        summary = vr.summarize({"schema_version": 1, "started": "2026-10-20", "entries": daily})
        self.assertEqual(summary["challenger"]["n"], 10)                     # descriptive, overlaps included
        # 52 weekly entries with a daily (overlapping) entry after each: the sample is the weekly ones.
        weekly = weekly_entries(52)
        mixed = []
        for entry in weekly:
            mixed.append(entry)
            k0 = contract_calendar.shift_business_days(D(entry["base_session"]), 1)
            mixed.append(resolved_entry(entry["base_session"], k0.isoformat(), realized=30.0, challenger=80.0))
        self.assertEqual(len(mixed), 104)
        self.assertEqual(self.promotion(mixed), self.promotion(weekly))

    def test_a_pending_paired_entry_holds_back_the_entries_after_it(self):
        entries = weekly_entries(10)
        entries[3] = as_pending(entries[3])
        self.assertEqual(self.promotion(entries)["n_independent_paired"], 3)
        entries[3] = weekly_entries(10)[3]
        self.assertEqual(self.promotion(entries)["n_independent_paired"], 10)
        # Unpaired entries never resolve into the sample, so they do not hold it back.
        entries[3] = dict(as_pending(entries[3]), challenger=None)
        self.assertEqual(self.promotion(entries)["n_independent_paired"], 9)

    def test_only_the_pinned_pair_counts(self):
        # The newest entry does not choose the pair: another version or challenger is passed over and
        # counted, and the sample keeps the pinned pair's weeks.
        def exclusions(result):
            return {k: result[k] for k in NO_EXCLUSIONS}

        entries = weekly_entries(10)
        entries[-1]["model_version"] = "har-iv/3"
        result = self.promotion(entries)
        self.assertEqual((result["n_independent_paired"], exclusions(result)),
                         (9, dict(NO_EXCLUSIONS, n_excluded_other_version=1)))
        entries = weekly_entries(10)
        entries[-1]["challenger"]["key"] = "garch"
        result = self.promotion(entries)
        self.assertEqual((result["n_independent_paired"], exclusions(result)),
                         (9, dict(NO_EXCLUSIONS, n_excluded_other_challenger=1)))
        entries = weekly_entries(60)
        for entry in entries[:20]:
            entry["challenger"] = None
        result = self.promotion(entries)
        self.assertEqual((result["n_independent_paired"], exclusions(result)),
                         (40, dict(NO_EXCLUSIONS, n_excluded_no_challenger=20)))
        self.assertEqual({result["model_version"], result["challenger_key"]}, {"har-iv/2", "har_iv_range"})
        # After a verdict, a newest entry of another version or challenger leaves it as it was.
        decided = self.promotion(weekly_entries(60))
        for change in ({"model_version": "har-iv/3"}, {"challenger": {"key": "combo", "forecast_pct": 50.0,
                                                                       "median_pct": None}}):
            with self.subTest(newest=change):
                entries = weekly_entries(61)
                entries[-1].update(change)
                result = self.promotion(entries)
                self.assertEqual({k: v for k, v in result.items() if k not in NO_EXCLUSIONS},
                                 {k: v for k, v in decided.items() if k not in NO_EXCLUSIONS})
                self.assertEqual(sum(exclusions(result).values()), 1)

    def test_fallback_forecasts_never_enter_or_reset_the_test(self):
        # Without a lagged OVX print the forecaster publishes plain HAR ("har/2") with no challenger and
        # no interval. Recorded by the real recorder after a decided test, it is the newest entry, and
        # the verdict, the comparison and the model of record's statistics stay as they were.
        record = {"schema_version": 1, "started": "2026-10-20", "entries": weekly_entries(60)}
        before = vr.summarize(record)
        self.assertEqual((before["promotion"]["status"], before["promotion"]["n_independent_paired"]), ("promote", 52))
        as_of = contract_calendar.shift_business_days(D(record["entries"][-1]["target_end"]), 1)
        issued = datetime.combine(contract_calendar.shift_business_days(as_of, 1), datetime.min.time(), UTC) \
            + timedelta(hours=13)
        plain = live(as_of=as_of.isoformat(), issued_at=issued.isoformat(), model_version="har/2",
                     model="HAR (RV5,RV22,RV66)", challenger=None, interval_80=None)
        message = vr.record_forecast(record, payload(plain, frozen_at=(issued + timedelta(minutes=2)).isoformat()),
                                     issued + timedelta(minutes=5))
        self.assertTrue(message.startswith("recorded"), message)
        self.assertEqual(record["entries"][-1]["model_version"], "har/2")
        vr.validate_record(record)
        after = vr.summarize(record)
        for block in ("promotion", "champion", "challenger"):
            with self.subTest(block=block):
                self.assertEqual(after[block], before[block])
        self.assertEqual((after["recent"][0]["model_version"], after["recent"][0]["status"]), ("har/2", "pending"))
        # Fallback entries inside the sample, pending or resolved, neither hold it back nor enter it.
        for status in ("pending", "resolved"):
            with self.subTest(status=status):
                entries = weekly_entries(10)
                entries[3] = fallback(entries[3] if status == "resolved" else as_pending(entries[3]))
                result = self.promotion(entries)
                self.assertEqual((result["status"], result["n_independent_paired"]), ("accruing", 9))
                self.assertEqual(result["n_excluded_other_version"], 1 if status == "resolved" else 0)


class PinnedPairTest(unittest.TestCase):
    """The promotion test's pair is declared in vol_record, and it is the pair the live forecast publishes."""

    def test_the_pinned_pair_is_the_live_model_and_challenger(self):
        # A new model version or a re-chosen challenger fails here until it is declared as a new test.
        from backend import vol_forecast

        self.assertEqual(vr.PROMOTION_MODEL_VERSION, vol_forecast.MODEL_VERSION)
        lab = json.loads((ROOT / "data" / "vol_lab.json").read_text(encoding="utf-8"))
        self.assertEqual(vr.PROMOTION_CHALLENGER, lab["config"]["challenger"])
        entry = recorded()["entries"][0]                   # this file's payload carries the same pair
        self.assertEqual(vr._pair(entry), (vr.PROMOTION_MODEL_VERSION, vr.PROMOTION_CHALLENGER))

    def test_the_rule_and_the_summary_name_the_pair(self):
        for text in ("HAR-IV v2", "har-iv/2", "HAR-IV-range", "har_iv_range", "52", "0.10", "separately declared"):
            with self.subTest(text=text):
                self.assertIn(text, vr.PROMOTION_RULE)
        newest_fallback = weekly_entries(3)
        newest_fallback[-1] = fallback(as_pending(newest_fallback[-1]))
        for name, entries in {"seeded": [], "newest is a fallback": newest_fallback}.items():
            with self.subTest(name):
                s = vr.summarize({"schema_version": 1, "started": "2026-10-20", "entries": entries})
                self.assertEqual({k: s["promotion"][k] for k in PINNED}, PINNED)
                self.assertEqual((s["champion"]["model_version"], s["challenger"]["key"]), ("har-iv/2", "har_iv_range"))


class StudentTTest(unittest.TestCase):
    """vol_record.student_t_sf, the standard-library Student t tail the promotion test uses."""

    GRID = (0.0, 0.001, 0.1, 0.5, 1.0, 1.2816, 1.2984, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0, 30.0, 100.0, 1e4)

    def test_matches_scipy(self):
        for df in (1, 2, 3, 5, 10, 30, 51, 100):
            for t in self.GRID + tuple(-t for t in self.GRID):
                with self.subTest(df=df, t=t):
                    self.assertLess(abs(vr.student_t_sf(t, df) - float(stats.t.sf(t, df))), 1e-10)

    def test_closed_forms_and_limits(self):
        # df 1 is the Cauchy distribution, df 2 has P(T > t) = (1 - t / sqrt(2 + t^2)) / 2. (scipy's t.sf
        # returns exactly 0.5 for df 1 at t = 1e-9, about 3e-10 off, so tiny t is checked here, not there.)
        for t in (1e-9, 0.3, 1.0, 12.0, -4.0):
            with self.subTest(t=t):
                self.assertAlmostEqual(vr.student_t_sf(t, 1), 0.5 - math.atan(t) / math.pi, places=14)
                self.assertAlmostEqual(vr.student_t_sf(t, 2), 0.5 * (1 - t / math.sqrt(2 + t * t)), places=14)
        self.assertEqual(vr.student_t_sf(0.0, 51), 0.5)
        self.assertEqual((vr.student_t_sf(math.inf, 51), vr.student_t_sf(-math.inf, 51)), (0.0, 1.0))
        self.assertAlmostEqual(vr.student_t_sf(1.2984, 51), 0.10, places=4)
        # Large df approaches the normal tail.
        self.assertAlmostEqual(vr.student_t_sf(1.2816, 100_000), statistics.NormalDist().cdf(-1.2816), places=5)

    def test_bad_arguments_are_refused(self):
        for df in (0, -1, 2.0, True, None):
            with self.subTest(df=df), self.assertRaises(ValueError):
                vr.student_t_sf(1.0, df)
        with self.assertRaises(ValueError):
            vr.student_t_sf(math.nan, 5)


class RecordFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "vol_track_record.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, obj):
        self.path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")

    def test_round_trip(self):
        record = recorded()
        vr.save_record(record, self.path)
        self.assertEqual(vr.load_record(self.path), record)
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir()], ["vol_track_record.json"])

    def test_a_missing_record_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "--init"):
            vr.load_record(self.path)

    def test_writes_keep_an_existing_files_mode_and_create_new_files_owner_only(self):
        vr.save_record(recorded(), self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        os.chmod(self.path, 0o644)
        vr.save_record(recorded(), self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o644)

    def test_malformed_records_are_rejected(self):
        good = recorded()
        entry = good["entries"][0]
        res = resolved_entry("2026-11-09", "2026-11-10", 40.0)

        def with_entry(**changes):
            bad = copy.deepcopy(entry)
            for key, value in changes.items():
                if value is KeyError:
                    del bad[key]
                else:
                    bad[key] = value
            return {"schema_version": 1, "started": "2026-11-03", "entries": [bad]}

        def with_resolved(**changes):
            bad = copy.deepcopy(res)
            bad.update(changes)
            return {"schema_version": 1, "started": "2026-11-03", "entries": [bad]}

        cases = {
            "not json": "{",
            "NaN": json.dumps(good).replace('"rv22_pct": 33.0', '"rv22_pct": NaN'),
            "Infinity": json.dumps(good).replace("40.0", "Infinity"),
            "list": [],
            "calls (old format)": {"calls": []},
            "extra key": dict(good, summary={}),
            "schema version": dict(good, schema_version=2),
            "boolean schema version": dict(good, schema_version=True),
            "started": dict(good, started="soon"),
            "entries": dict(good, entries={}),
            "entry not object": dict(good, entries=[1]),
            "missing key": with_entry(target_end=KeyError),
            "bad status": with_entry(status="won"),
            "forecast": with_entry(forecast_pct=-1),
            "boolean forecast": with_entry(forecast_pct=True),
            "half interval": with_entry(lo80_pct=None),
            "half interval (high)": with_entry(hi80_pct=None),
            "inverted interval": with_entry(lo80_pct=60.0),
            "direction": with_entry(direction="UP"),
            "naive issued_at": with_entry(issued_at="2026-11-03T13:00:00"),
            "window before as_of": with_entry(base_session="2026-11-02"),
            "window order": with_entry(target_start="2026-11-11"),
            "recorded after the window opened": with_entry(recorded_at="2026-11-03T20:00:00Z"),
            "issued after recording": with_entry(issued_at="2026-11-03T14:00:00Z"),
            "pending with a realized vol": with_entry(realized_pct=30.0),
            "challenger": with_entry(challenger={"key": "", "forecast_pct": 1.0}),
            "coefficients not a list": with_entry(coefficients="-4.88,0.04"),
            "coefficients empty": with_entry(coefficients=[]),
            "coefficients too long": with_entry(coefficients=[0.1] * (vr.MAX_FIT_TERMS + 1)),
            "coefficient boolean": with_entry(coefficients=[True, 0.1, 0.2, 0.3, 0.4]),
            "coefficient text": with_entry(coefficients=["1", 0.1, 0.2, 0.3, 0.4]),
            "smearing zero": with_entry(smearing_factor=0),
            "smearing boolean": with_entry(smearing_factor=True),
            "naive sealed_at": with_entry(sealed_at="2026-11-03T13:15:00"),
            "sealed_at not a time": with_entry(sealed_at=1),
            "sealed before recording": with_entry(sealed_at="2026-11-03T12:59:00Z"),
            "pending sealed late": with_entry(sealed_at="2026-11-03T19:25:00Z"),
            "resolved without realized": with_resolved(realized_pct=None),
            "resolved without closes": with_resolved(closes=[]),
            "resolved closes outside window": with_resolved(closes=[["2026-11-09", 80.0]] + res["closes"][1:]),
            "resolved bad price": with_resolved(closes=res["closes"][:-1] + [[res["target_end"], 0]]),
            "resolved never sealed": with_resolved(sealed_at=None),
            "resolved sealed late": with_resolved(sealed_at="2026-11-10T19:25:00Z"),
            "skipped never sealed": with_resolved(status="skipped_roll", realized_pct=None, sealed_at=None),
            "expired sealed late": with_resolved(status="expired", realized_pct=None,
                                                 sealed_at="2026-11-10T20:00:00Z"),
            "late commit with a realized vol": with_resolved(status="late_commit"),
            "duplicate session": dict(good, entries=[entry, entry]),
            "out of order": dict(good, entries=[res, entry]),
        }
        vr.validate_record({"schema_version": 1, "started": "2026-11-03", "entries": [entry, res]})
        # An entry closed unscored needs no seal, or may carry a late one.
        for sealed_at in (None, "2026-11-10T19:25:00Z", "2026-11-11T09:00:00Z"):
            with self.subTest(late_commit=sealed_at):
                vr.validate_record(with_resolved(status="late_commit", realized_pct=None, sealed_at=sealed_at))
        vr.validate_record(with_entry(sealed_at="2026-11-03T13:01:00Z"))    # within the clock-skew allowance
        # Entries recorded before the fit was stored, or with no fit, are valid.
        vr.validate_record(with_entry(coefficients=KeyError, smearing_factor=KeyError))
        vr.validate_record(with_entry(coefficients=None, smearing_factor=None))
        for name, bad in cases.items():
            with self.subTest(name):
                self.write(bad)
                with self.assertRaises(ValueError):
                    vr.load_record(self.path)

    def test_writes_are_atomic(self):
        vr.save_record(empty_record(), self.path)
        before = self.path.read_text(encoding="utf-8")
        with mock.patch.object(vr.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            vr.save_record(recorded(), self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir()], ["vol_track_record.json"])
        with self.assertRaises(ValueError):                 # never writes an invalid record
            vr.save_record({"entries": []}, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)


class CommandLineTest(unittest.TestCase):
    """main() against a temporary project tree (safe_paths roots patched)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "project"
        (root / "data").mkdir(parents=True)
        (root / "public").mkdir()
        self.record_path, self.data_path = root / "data" / "vol_track_record.json", root / "public" / "data.json"
        patches = [mock.patch.object(safe_paths, "PROJECT_ROOT", root),
                   mock.patch.object(safe_paths, "DATA_ROOT", root / "data"),
                   mock.patch.object(safe_paths, "PUBLIC_ROOT", root / "public")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_main(self, *argv, now="2026-11-03T13:05:00Z"):
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            return vr.main(list(argv), now=at(now))

    def test_init_writes_a_record_that_loads_and_never_overwrites(self):
        self.assertEqual(self.run_main("--init"), 0)
        record = vr.load_record(self.record_path)
        self.assertEqual(record, {"schema_version": 1, "started": "2026-11-03", "entries": []})
        seeded = '{\n  "schema_version": 1,\n  "started": "2026-11-03",\n  "entries": []\n}\n'
        self.assertEqual(self.record_path.read_text(encoding="utf-8"), seeded)
        # The seeded record seals to itself and summarizes to an accruing test of the pinned pair.
        self.assertEqual(self.run_main("--seal", now="2026-11-03T13:20:00Z"), 0)
        self.assertEqual(self.record_path.read_text(encoding="utf-8"), seeded)
        summary = vr.summarize(vr.load_record(self.record_path))
        self.assertEqual(summary["promotion"], ACCRUING_EMPTY)
        self.assertEqual((summary["n_recorded"], summary["champion"]["n"], summary["challenger"]["n"]), (0, 0, 0))
        self.record_path.write_text(json.dumps(recorded()), encoding="utf-8")
        self.assertEqual(self.run_main("--init"), 1)
        self.assertEqual(len(vr.load_record(self.record_path)["entries"]), 1)

    def test_only_the_record_path_is_accepted(self):
        for value in ("data/vol_lab.json", "/tmp/vol_track_record.json", "data/live_track_record.json"):
            with self.subTest(value=value), self.assertRaises((SystemExit, ValueError)):
                self.run_main("--init", "--record", value)

    def test_a_cycle_records_resolves_and_publishes_the_summary(self):
        self.run_main("--init", now="2026-11-02T12:00:00Z")
        self.data_path.write_text(json.dumps(payload(live())), encoding="utf-8")
        self.assertEqual(self.run_main("--data", "public/data.json"), 0)
        data = json.loads(self.data_path.read_text(encoding="utf-8"))
        summary = data["vol_forecast"]["live_record"]
        self.assertEqual((summary["n_recorded"], summary["n_pending"], summary["started"]), (1, 1, "2026-11-02"))
        self.assertEqual(summary["promotion"]["status"], "accruing")
        self.assertEqual(data["vol_forecast"]["live"]["as_of"], "2026-11-02")   # the rest is untouched
        # The publish job seals the record right before committing it.
        self.assertIsNone(vr.load_record(self.record_path)["entries"][0]["sealed_at"])
        self.assertEqual(self.run_main("--seal", now="2026-11-03T13:20:00Z"), 0)
        self.assertEqual(vr.load_record(self.record_path)["entries"][0]["sealed_at"], "2026-11-03T13:20:00Z")

        # A week later: same session's forecast again (ignored), the window's closes now available.
        later = payload(live(issued_at="2026-11-11T13:00:00Z", forecast=99.0), ZIGZAG,
                        frozen_at="2026-11-11T13:02:00Z")
        self.data_path.write_text(json.dumps(later), encoding="utf-8")
        self.run_main("--data", "public/data.json", now="2026-11-11T13:05:00Z")
        record = vr.load_record(self.record_path)
        self.assertEqual(len(record["entries"]), 1)
        self.assertEqual(record["entries"][0]["forecast_pct"], 40.0)
        self.assertEqual(record["entries"][0]["status"], "resolved")
        summary = json.loads(self.data_path.read_text(encoding="utf-8"))["vol_forecast"]["live_record"]
        self.assertEqual((summary["n_resolved"], summary["n_independent"]), (1, 1))
        self.assertAlmostEqual(summary["champion"]["mean_realized_pct"], ZIGZAG_VOL, places=1)
        self.assertEqual(summary["champion"]["interval_80_coverage_pct"], 0.0)      # 15.8% < 25%

        # Nothing new: the record file is not rewritten (no timestamp-only commits).
        stamp = self.record_path.stat().st_mtime_ns
        os.utime(self.record_path, ns=(stamp - 10**9, stamp - 10**9))
        self.run_main("--data", "public/data.json", now="2026-11-11T14:05:00Z")
        self.assertEqual(self.record_path.stat().st_mtime_ns, stamp - 10**9)

    def test_the_payload_stays_compact(self):
        # freeze.py writes data.json compact (about half the size of an indented one); attaching the
        # summary must not re-indent it.
        self.run_main("--init", now="2026-11-02T12:00:00Z")
        self.data_path.write_text(json.dumps(payload(live()), separators=(",", ":")), encoding="utf-8")
        self.run_main("--data", "public/data.json")
        text = self.data_path.read_text(encoding="utf-8")
        written = json.loads(text)
        self.assertEqual(written["vol_forecast"]["live_record"]["n_recorded"], 1)
        self.assertEqual(text, json.dumps(written, separators=(",", ":")))
        self.assertNotIn("\n", text)

    def test_an_entry_the_seal_never_reached_is_closed_unscored(self):
        self.run_main("--init", now="2026-11-02T12:00:00Z")
        self.data_path.write_text(json.dumps(payload(live())), encoding="utf-8")
        self.run_main("--data", "public/data.json")
        later = payload(live(issued_at="2026-11-11T13:00:00Z"), ZIGZAG, frozen_at="2026-11-11T13:02:00Z")
        self.data_path.write_text(json.dumps(later), encoding="utf-8")
        self.run_main("--data", "public/data.json", now="2026-11-11T13:05:00Z")
        entry = vr.load_record(self.record_path)["entries"][0]
        self.assertEqual((entry["status"], entry["realized_pct"]), ("late_commit", None))
        summary = json.loads(self.data_path.read_text(encoding="utf-8"))["vol_forecast"]["live_record"]
        self.assertEqual((summary["n_late_commit"], summary["n_resolved"]), (1, 0))
        self.assertIsNone(summary["champion"]["qlike"])

    def test_seal_stamps_once_and_leaves_a_sealed_record_untouched(self):
        self.record_path.write_text(json.dumps(recorded(seal_after=None)), encoding="utf-8")
        self.assertEqual(self.run_main("--seal", now="2026-11-03T13:20:00Z"), 0)
        entry = vr.load_record(self.record_path)["entries"][0]
        self.assertEqual((entry["status"], entry["sealed_at"]), ("pending", "2026-11-03T13:20:00Z"))
        self.assertFalse(self.data_path.exists())               # the seal reads and writes only the record
        stamp = self.record_path.stat().st_mtime_ns - 10**9
        os.utime(self.record_path, ns=(stamp, stamp))
        self.assertEqual(self.run_main("--seal", now="2026-11-03T19:29:00Z"), 0)
        self.assertEqual(self.record_path.stat().st_mtime_ns, stamp)
        # A late seal is recorded as such.
        self.record_path.write_text(json.dumps(recorded(seal_after=None)), encoding="utf-8")
        self.run_main("--seal", now="2026-11-03T19:29:00Z")
        self.assertEqual(vr.load_record(self.record_path)["entries"][0]["status"], "late_commit")

    def test_seal_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "--init"):
            self.run_main("--seal")
        self.assertFalse(self.record_path.exists())
        self.record_path.write_text('{"calls": []}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_main("--seal")
        self.assertEqual(self.record_path.read_text(encoding="utf-8"), '{"calls": []}')
        with self.assertRaises(SystemExit):
            self.run_main("--init", "--seal")

    def test_without_a_forecast_block_the_record_still_resolves(self):
        self.record_path.write_text(json.dumps(recorded()), encoding="utf-8")
        bare = payload(None, ZIGZAG, frozen_at="2026-11-11T13:02:00Z")
        self.data_path.write_text(json.dumps(bare), encoding="utf-8")
        self.run_main("--data", "public/data.json", now="2026-11-11T13:05:00Z")
        self.assertEqual(vr.load_record(self.record_path)["entries"][0]["status"], "resolved")
        self.assertEqual(json.loads(self.data_path.read_text(encoding="utf-8")), bare)

    def test_a_malformed_record_fails_the_run_and_is_left_alone(self):
        self.record_path.write_text('{"calls": []}', encoding="utf-8")
        self.data_path.write_text(json.dumps(payload(live())), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_main("--data", "public/data.json")
        self.assertEqual(self.record_path.read_text(encoding="utf-8"), '{"calls": []}')
        self.assertNotIn("live_record", json.loads(self.data_path.read_text(encoding="utf-8"))["vol_forecast"])


def chained(sessions=("2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05")):
    """A record of one entry per session, each recorded and sealed by the real recorder and seal."""
    record = empty_record("2026-11-02")
    for session in sessions:
        issued = f"{D(session) + timedelta(days=1)}T13:00:00Z"
        message = vr.record_forecast(record, payload(live(as_of=session, issued_at=issued)), at(issued))
        assert message.startswith("recorded"), message
        vr.seal_entries(record, at(issued) + timedelta(minutes=10))
    return record


class HashChainTest(unittest.TestCase):
    """Each entry the seal stamps links to the sealed part of the entry before it (module docstring, CHAIN)."""

    def test_the_seal_links_each_entry_to_the_one_before_it(self):
        entries = chained()["entries"]
        self.assertIsNone(entries[0]["previous_sha256"])                         # the record's first entry
        for before, entry in zip(entries, entries[1:]):
            self.assertEqual(entry["previous_sha256"], vr.entry_digest(before))
        self.assertEqual(vr.chain_breaks(entries), [])
        self.assertEqual(vr.summarize({"schema_version": 1, "started": "2026-11-02", "entries": entries})["chain"],
                         {"n_linked": 3, "breaks": []})

    def test_several_entries_sealed_in_one_run_link_in_order_and_sealing_again_changes_nothing(self):
        record = empty_record("2026-11-02")
        for session in ("2026-11-02", "2026-11-03"):
            issued = f"{D(session) + timedelta(days=1)}T13:00:00Z"
            vr.record_forecast(record, payload(live(as_of=session, issued_at=issued)), at(issued))
        vr.seal_entries(record, at("2026-11-04T13:10:00Z"))
        self.assertEqual(record["entries"][1]["previous_sha256"], vr.entry_digest(record["entries"][0]))
        before = json.dumps(record, sort_keys=True)
        self.assertEqual(vr.seal_entries(record, at("2026-11-04T14:00:00Z")), [])
        self.assertEqual(json.dumps(record, sort_keys=True), before)

    def test_links_survive_saving_and_loading_the_record(self):
        record = chained()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            vr.save_record(record, path)
            self.assertEqual(vr.chain_breaks(vr.load_record(path)["entries"]), [])

    def test_resolution_never_breaks_a_link(self):
        record = chained()
        resolve(record, dict(zip(WINDOW, [100.0, 101.0, 100.0, 101.0, 100.0, 101.0])), "2026-11-11T06:00:00Z")
        self.assertEqual(record["entries"][0]["status"], "resolved")
        cutoff = vr.bars_cutoff(at("2026-12-04T06:00:00Z"))
        vr.resolve_entries(record, {}, cutoff, at("2026-12-04T06:00:00Z"))             # the rest expire
        self.assertEqual({e["status"] for e in record["entries"][1:]}, {"expired"})
        self.assertEqual(vr.chain_breaks(record["entries"]), [])

    def test_changing_removing_or_inserting_a_sealed_entry_breaks_the_next_link(self):
        cases = {
            "forecast edited": lambda e: e[1].__setitem__("forecast_pct", 30.0),
            "seal backdated": lambda e: e[1].__setitem__("sealed_at", "2026-11-04T13:01:00Z"),
            "inputs edited": lambda e: e[1]["inputs"].__setitem__("ovx", 30.0),
            "its own link rewritten": lambda e: e[1].__setitem__("previous_sha256", "0" * 64),
            "entry removed": lambda e: e.pop(1),
            "entry inserted": lambda e: e.insert(2, copy.deepcopy(e[1])),
            "a later link dropped": lambda e: e[2].pop("previous_sha256"),
        }
        for name, change in cases.items():
            with self.subTest(change=name):
                entries = chained()["entries"]
                change(entries)
                self.assertTrue(vr.chain_breaks(entries), name)

    def test_entries_before_the_chain_and_an_entry_not_sealed_yet_are_not_breaks(self):
        record = chained()
        entries = record["entries"]
        for entry in entries[:2]:                            # sealed before the chain existed: no link at all
            entry.pop("previous_sha256")
        for index in (2, 3):                                 # then sealed with it, linking forward
            entries[index]["previous_sha256"] = vr.entry_digest(entries[index - 1])
        issued = "2026-11-07T13:00:00Z"                      # the build job's newest entry, sealed later
        vr.record_forecast(record, payload(live(as_of="2026-11-06", issued_at=issued)), at(issued))
        self.assertIsNone(record["entries"][-1]["sealed_at"])
        with mock.patch.object(vr, "CHAIN_FROM", D("2026-11-04")):   # the chain shipped after the 11-03 entry
            self.assertEqual(vr.chain_breaks(record["entries"]), [])
        self.assertEqual(vr.chain_breaks(record["entries"]), ["2026-11-02", "2026-11-03"])

    def test_from_chain_from_on_a_sealed_entry_without_a_link_is_a_break_even_when_every_link_is_gone(self):
        self.assertEqual(vr.CHAIN_FROM, D("2026-11-02"))      # pinned: a later date would excuse stripped links
        entries = chained()["entries"]
        for entry in entries:                                # strip every link, then edit a sealed entry
            entry.pop("previous_sha256")
        entries[1]["forecast_pct"] = 30.0
        self.assertEqual(vr.chain_breaks(entries), [e["as_of"] for e in entries])
        self.assertIn("sealed without a previous_sha256", vr.chain_break_reason(entries[1]))
        relinked = chained()["entries"]
        relinked[1]["forecast_pct"] = 30.0
        self.assertEqual(vr.chain_breaks(relinked), ["2026-11-04"])
        self.assertIn("does not match the sealed part of the entry before it", vr.chain_break_reason(relinked[2]))

    def test_the_chain_cannot_see_its_newest_entry_or_later_links_recomputed(self):
        # What the docs say the chain does not show: it has no key, and nothing links to the newest entry.
        entries = chained()["entries"]
        entries[-1]["forecast_pct"] = 30.0                   # the newest entry, before the next seal
        self.assertEqual(vr.chain_breaks(entries), [])
        entries = chained()["entries"][:-1]                  # the newest entry removed
        self.assertEqual(vr.chain_breaks(entries), [])
        entries = chained()["entries"]
        entries[1]["forecast_pct"] = 30.0                    # an old entry edited, every later link recomputed
        for index in range(2, len(entries)):
            entries[index]["previous_sha256"] = vr.entry_digest(entries[index - 1])
        self.assertEqual(vr.chain_breaks(entries), [])

    def test_loading_checks_the_link_s_form_but_never_the_chain(self):
        record = chained()
        record["entries"][1]["forecast_pct"] = 30.0           # a break: still loads, so the pipeline never stops on it
        vr.validate_record(record)
        self.assertEqual(vr.chain_breaks(record["entries"]), ["2026-11-04"])
        for bad in ("abc", "G" * 64, 7, "0" * 63):
            with self.subTest(bad=bad):
                broken = chained()
                broken["entries"][1]["previous_sha256"] = bad
                with self.assertRaisesRegex(ValueError, "previous_sha256 must be a SHA-256 hex digest or null"):
                    vr.validate_record(broken)

    def test_the_digest_covers_exactly_the_sealed_part(self):
        entry = chained()["entries"][1]
        digest = vr.entry_digest(entry)
        for key, value in (("status", "resolved"), ("realized_pct", 30.0), ("closes", []), ("closed_at", "x"),
                           ("resolved_at", "x"), ("missing_sessions", [])):
            with self.subTest(key=key):
                self.assertEqual(vr.entry_digest({**entry, key: value}), digest)
        for key in sorted(set(entry) - vr._AFTER_SEAL_KEYS):
            with self.subTest(key=key):
                self.assertNotEqual(vr.entry_digest({**entry, key: "changed"}), digest)


class StandardLibraryOnlyTest(unittest.TestCase):
    def test_the_publish_job_can_import_it_without_dependencies(self):
        # The publish job validates the restored record before installing anything.
        allowed_local = {"contract_calendar", "safe_paths"}
        backend = Path(vr.__file__).parent
        for name in ("vol_record.py", "contract_calendar.py", "safe_paths.py"):
            tree = ast.parse((backend / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""] if node.level == 0 else [node.module or ""]
                else:
                    continue
                for module in modules:
                    top = module.split(".")[0]
                    with self.subTest(file=name, module=module):
                        self.assertTrue(top in sys.stdlib_module_names or top in allowed_local
                                        or top == "__future__", module)

    def test_load_seal_and_summary_run_without_site_packages(self):
        # -I -S: no site-packages and no environment, as close to an empty interpreter as it gets (-B:
        # no bytecode left in backend/).
        code = textwrap.dedent("""\
            import json, os, sys, sysconfig
            sys.path.insert(0, sys.argv[1])
            from datetime import datetime, timezone
            from backend import vol_record
            for record in json.loads(sys.argv[2]):
                vol_record.validate_record(record)
                vol_record.seal_entries(record, datetime(2026, 11, 3, 13, 20, tzinfo=timezone.utc))
                vol_record.validate_record(record)
                assert vol_record.summarize(record)["promotion"]["challenger_key"] == "har_iv_range"
            vol_record.student_t_sf(1.3, 51)
            roots = tuple(os.path.realpath(p) + os.sep for p in (sysconfig.get_paths()['stdlib'],
                          sysconfig.get_paths()['platstdlib'], os.path.join(sys.argv[1], 'backend')))
            files = [getattr(m, '__file__', None) for m in list(sys.modules.values())]
            print(sorted(f for f in files if f and not os.path.realpath(f).startswith(roots)))
            """)
        result = subprocess.run(  # nosec B603 - fixed argv: this interpreter and inline code
            [sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT),
             json.dumps([empty_record(), recorded(seal_after=None)])],
            capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
