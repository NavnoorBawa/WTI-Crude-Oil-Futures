"""Network-free, clock-independent tests for backend/vol_record.py, the live vol forecast record.

This module decides which forecast is recorded, which price window it is scored on, when it is
settled, skipped or expired, and what the dashboard reports. Every test injects `now`; nothing
reads the wall clock or the network.

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
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from backend import contract_calendar, safe_paths
from backend import vol_record as vr

UTC = timezone.utc
D = date.fromisoformat


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
    }
    block.update(extra)
    return block


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


def recorded(as_of="2026-11-02", issued_at="2026-11-03T13:00:00Z", now="2026-11-03T13:05:00Z", **kwargs):
    """A record holding one pending entry, made by the real recorder."""
    record = empty_record()
    message = vr.record_forecast(record, payload(live(as_of=as_of, issued_at=issued_at, **kwargs)), at(now))
    assert message.startswith("recorded"), message
    return record


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
        record = recorded()
        (entry,) = record["entries"]
        self.assertEqual(entry["as_of"], "2026-11-02")
        self.assertEqual(entry["issued_at"], "2026-11-03T13:00:00Z")
        self.assertEqual(entry["recorded_at"], "2026-11-03T13:05:00Z")
        self.assertEqual(entry["frozen_at"], "2026-11-03T13:02:00Z")
        self.assertEqual(entry["contract"], "CLZ26")
        self.assertEqual(entry["model_version"], "har-iv/2")
        self.assertEqual((entry["forecast_pct"], entry["median_pct"]), (40.0, 36.0))
        self.assertEqual((entry["lo80_pct"], entry["hi80_pct"]), (25.0, 55.0))
        self.assertEqual((entry["direction"], entry["rv5_pct"], entry["prob_vol_rises_pct"]), ("RISING", 30.0, 58.0))
        self.assertEqual(entry["inputs"]["ovx_session"], "2026-10-30")
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
        cutoff = vr.bars_cutoff(at(snapshot))
        return vr.resolve_entries(record, vr.completed_closes(payload(None, closes), cutoff), cutoff, at(snapshot))

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
                self.resolve(record, {}, now)
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


def resolved_entry(as_of, base, realized, forecast=40.0, lo=25.0, hi=55.0, direction="RISING", rv5=30.0,
                   challenger=38.0, challenger_key="har_iv_range"):
    sessions = vr.window_sessions(D(base))
    return {
        "as_of": as_of, "issued_at": f"{base}T13:00:00Z", "recorded_at": f"{base}T13:05:00Z",
        "model_version": "har-iv/2", "forecast_pct": forecast, "median_pct": forecast - 4,
        "lo80_pct": lo, "hi80_pct": hi, "direction": direction, "rv5_pct": rv5,
        "challenger": {"key": challenger_key, "forecast_pct": challenger, "median_pct": None} if challenger else None,
        "base_session": base, "target_start": sessions[1].isoformat(), "target_end": sessions[-1].isoformat(),
        "status": "resolved", "realized_pct": realized, "resolved_at": "2026-12-31T12:00:00Z",
        "closes": [[d.isoformat(), 80.0] for d in sessions],
    }


class SummaryTest(unittest.TestCase):
    SHAPE = {"started", "n_recorded", "n_resolved", "n_pending", "n_skipped_roll", "n_expired", "n_independent",
             "champion", "challenger", "recent", "promotion_rule", "note", "direction_note"}
    CHAMPION = {"model_version", "qlike", "interval_80_coverage_pct", "direction_hit_pct",
                "mean_forecast_pct", "mean_realized_pct"}
    RECENT = {"as_of", "issued_at", "target_start", "target_end", "forecast_pct", "median_pct", "lo80_pct",
              "hi80_pct", "challenger_pct", "realized_pct", "status"}

    def test_empty_record_has_the_contract_shape_with_null_statistics(self):
        summary = vr.summarize(empty_record())
        self.assertEqual(set(summary), self.SHAPE)
        self.assertEqual(set(summary["champion"]), self.CHAMPION)
        self.assertEqual(set(summary["challenger"]), {"key", "qlike", "n", "champion_qlike"})
        self.assertEqual(summary["started"], "2026-11-03")
        self.assertEqual([summary[k] for k in ("n_recorded", "n_resolved", "n_pending", "n_independent")], [0] * 4)
        self.assertTrue(all(v is None for v in summary["champion"].values()))
        self.assertIsNone(summary["challenger"]["qlike"])
        self.assertEqual(summary["recent"], [])
        self.assertEqual(summary["promotion_rule"],
                         "The challenger replaces the model of record after at least 52 independent resolved "
                         "weeks if its live QLIKE is lower with a one-sided Diebold-Mariano p below 0.10.")
        self.assertIn("settlement", summary["note"])
        json.dumps(summary, allow_nan=False)

    def test_pending_entries_leave_statistics_null(self):
        summary = vr.summarize(recorded())
        self.assertEqual((summary["n_recorded"], summary["n_pending"], summary["n_resolved"]), (1, 1, 0))
        self.assertEqual(summary["champion"]["model_version"], "har-iv/2")
        self.assertTrue(all(v is None for k, v in summary["champion"].items() if k != "model_version"))
        self.assertEqual(summary["challenger"]["key"], "har_iv_range")
        self.assertIsNone(summary["challenger"]["qlike"])
        (row,) = summary["recent"]
        self.assertEqual(set(row), self.RECENT)
        self.assertEqual((row["status"], row["realized_pct"], row["challenger_pct"]), ("pending", None, 38.0))

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
        self.assertEqual(s["champion"]["qlike"], round(sum(q) / 3, 4))
        self.assertEqual(s["champion"]["interval_80_coverage_pct"], 33.3)
        self.assertEqual(s["champion"]["direction_hit_pct"], 66.7)
        self.assertEqual(s["champion"]["mean_forecast_pct"], 40.0)
        self.assertEqual(s["champion"]["mean_realized_pct"], 40.0)
        # The challenger is scored only where it was published, against the champion on the same rows.
        self.assertEqual(s["challenger"]["n"], 2)
        self.assertEqual(s["challenger"]["qlike"], round((vr.qlike(40.0, 38.0) + vr.qlike(60.0, 50.0)) / 2, 4))
        self.assertEqual(s["challenger"]["champion_qlike"], round((q[0] + q[2]) / 2, 4))
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
        record = {"schema_version": 1, "started": "2026-11-03", "entries": [on_low, on_high, rolled, expired, pending]}
        vr.validate_record(record)
        s = vr.summarize(record)
        self.assertEqual([s[k] for k in ("n_recorded", "n_resolved", "n_pending", "n_skipped_roll", "n_expired")],
                         [5, 2, 1, 1, 1])
        self.assertEqual(s["champion"]["interval_80_coverage_pct"], 100.0)
        self.assertEqual([r["status"] for r in s["recent"]], ["pending", "expired", "skipped_roll", "resolved", "resolved"])

    def test_challenger_key_change_starts_its_own_comparison(self):
        old = resolved_entry("2026-11-02", "2026-11-03", realized=40.0, challenger=30.0, challenger_key="garch")
        new = resolved_entry("2026-11-09", "2026-11-10", realized=40.0, challenger=44.0)
        s = vr.summarize({"schema_version": 1, "started": "2026-11-03", "entries": [old, new]})
        self.assertEqual((s["challenger"]["key"], s["challenger"]["n"]), ("har_iv_range", 1))
        self.assertEqual(s["challenger"]["qlike"], round(vr.qlike(40.0, 44.0), 4))

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
            "resolved without realized": with_resolved(realized_pct=None),
            "resolved without closes": with_resolved(closes=[]),
            "resolved closes outside window": with_resolved(closes=[["2026-11-09", 80.0]] + res["closes"][1:]),
            "resolved bad price": with_resolved(closes=res["closes"][:-1] + [[res["target_end"], 0]]),
            "duplicate session": dict(good, entries=[entry, entry]),
            "out of order": dict(good, entries=[res, entry]),
        }
        vr.validate_record({"schema_version": 1, "started": "2026-11-03", "entries": [entry, res]})
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
        self.assertEqual(self.record_path.read_text(encoding="utf-8"),
                         '{\n  "schema_version": 1,\n  "started": "2026-11-03",\n  "entries": []\n}\n')
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
        self.assertEqual(data["vol_forecast"]["live"]["as_of"], "2026-11-02")   # the rest is untouched

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


if __name__ == "__main__":
    unittest.main()
