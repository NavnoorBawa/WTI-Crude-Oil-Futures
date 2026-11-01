"""The freeze's report-only check of the live CL=F closes (ROADMAP 1.7, second part).

market_data.freeze_source_check is pure; vol_forecast._source_check wraps it so that nothing it does can
stop a freeze or reach a sealed record entry; validate_frozen_payload and record_health read the block it
leaves in data.json. Network-free: EIA's spot download is always patched.
"""

import copy
import dataclasses
import json
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from backend import market_data as md
from backend import vol_forecast as vf
from backend import vol_record as vr
from scripts import record_health as rh
from scripts import validate_frozen_payload as vfp

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = md.load_snapshot()
CLOSE = SNAPSHOT.cl["Close"]


def spot_after(days: pd.DatetimeIndex, factor: float = 1.01) -> pd.Series:
    return pd.Series(float(CLOSE.iloc[-1]) * factor, index=days, dtype=float)


def sessions(first: str, last: str) -> pd.DatetimeIndex:
    from backend.contract_calendar import is_business_day

    return pd.DatetimeIndex([d for d in pd.date_range(first, last) if is_business_day(d.date())])


class FreezeSourceCheckTest(unittest.TestCase):
    def test_the_pinned_closes_pass_and_a_missing_spot_is_never_a_pass(self):
        found = md.freeze_source_check(CLOSE, SNAPSHOT, None)
        self.assertEqual(found["snapshot"]["status"], "pass")
        self.assertEqual((found["snapshot"]["n_missing"], found["snapshot"]["n_added"], found["snapshot"]["n_beyond_tick"]),
                         (0, 0, 0))
        self.assertEqual(found["spot"]["status"], "no_spot_rows")
        self.assertIsNone(found["spot"]["max_abs_log_gap"])                    # never NaN
        self.assertEqual(found["spot"]["after"], SNAPSHOT.manifest["files"]["eia_spot"]["last"])
        json.dumps(found, allow_nan=False)

    def test_a_revised_missing_or_added_session_is_reported_with_its_date(self):
        live = CLOSE.copy()
        revised, dropped = live.index[100], live.index[200]
        live.loc[revised] += 0.02                                              # two ticks
        live.loc[live.index[300]] += 0.01                                      # one tick: agreement
        live = live.drop(dropped)
        added = pd.Timestamp("2026-10-17")                                     # a Saturday inside the pinned span
        live.loc[added] = 80.0
        live = live.sort_index()
        found = md.freeze_source_check(live, SNAPSHOT, None)["snapshot"]
        self.assertEqual(found["status"], "beyond_tolerance")
        self.assertEqual((found["n_missing"], found["n_added"], found["n_beyond_tick"]), (1, 1, 1))
        self.assertEqual(found["dates"], sorted(d.strftime("%Y-%m-%d") for d in (revised, dropped, added)))

    def test_float32_noise_and_sessions_after_the_snapshot_are_not_findings(self):
        live = CLOSE + 4e-7
        later = sessions("2026-10-20", "2026-10-30")
        live = pd.concat([live, pd.Series(70.0, index=later)])
        self.assertEqual(md.freeze_source_check(live, SNAPSHOT, None)["snapshot"]["status"], "pass")

    def test_spot_catches_only_gross_gaps_after_the_pinned_spot_copy(self):
        later = sessions("2026-10-20", "2026-10-30")
        live = pd.concat([CLOSE, pd.Series(float(CLOSE.iloc[-1]), index=later)])
        ok = md.freeze_source_check(live, SNAPSHOT, spot_after(later, 1.30))["spot"]  # a 0.26 log gap: a market can do that
        self.assertEqual((ok["status"], ok["n_above"], ok["paired"]), ("pass", 0, len(later)))
        bad_spot = spot_after(later, 1.30)
        bad_spot.iloc[3] = float(CLOSE.iloc[-1]) * 2.0                         # a 0.69 log gap
        bad = md.freeze_source_check(live, SNAPSHOT, bad_spot)["spot"]
        self.assertEqual((bad["status"], bad["n_above"], bad["dates"]),
                         ("beyond_tolerance", 1, [later[3].strftime("%Y-%m-%d")]))
        self.assertEqual(bad["spot_last"], later[-1].strftime("%Y-%m-%d"))

    def test_one_tick_everywhere_is_agreement(self):
        found = md.freeze_source_check(CLOSE + 0.01, SNAPSHOT, None)["snapshot"]
        self.assertEqual((found["status"], found["n_beyond_tick"]), ("pass", 0))

    def test_the_spot_check_starts_at_the_pinned_closes_end_when_the_spot_copy_runs_longer(self):
        # Re-pinning spot from a fresh cache can carry it past the pinned closes; no session may then
        # fall between the two checks.
        later = sessions("2026-10-20", "2026-10-30")
        longer = dataclasses.replace(SNAPSHOT, eia_spot=pd.concat([SNAPSHOT.eia_spot, spot_after(later[:6])]))
        live = pd.concat([CLOSE, pd.Series(float(CLOSE.iloc[-1]), index=later)])
        live.loc[later[2]] *= 3.0
        found = md.freeze_source_check(live, longer, spot_after(later))["spot"]
        self.assertEqual(found["after"], "2026-10-19")
        self.assertEqual((found["status"], found["dates"]), ("beyond_tolerance", [later[2].strftime("%Y-%m-%d")]))

    def test_non_finite_and_non_positive_prices_are_left_unpaired(self):
        later = sessions("2026-10-20", "2026-10-30")
        live = pd.concat([CLOSE, pd.Series(float(CLOSE.iloc[-1]), index=later)])
        live.loc[later[1]] = float("nan")
        spot = spot_after(later)
        spot.iloc[2], spot.iloc[3], spot.iloc[4] = 0.0, -36.98, float("inf")
        found = md.freeze_source_check(live, SNAPSHOT, spot)["spot"]
        self.assertEqual((found["status"], found["paired"]), ("pass", len(later) - 4))
        json.dumps(found, allow_nan=False)

    def test_listed_dates_are_capped(self):
        live = CLOSE.copy()
        live.iloc[:50] += 1.0
        found = md.freeze_source_check(live, SNAPSHOT, None)["snapshot"]
        self.assertEqual((found["n_beyond_tick"], len(found["dates"])), (50, md.SOURCE_CHECK_LISTED))

    def test_the_freeze_s_session_rule_keeps_every_pinned_session(self):
        # A change to the calendar or _completed would otherwise turn into a permanent snapshot finding.
        stamp = pd.Timestamp(SNAPSHOT.manifest["retrieved_at"]).to_pydatetime()
        kept = vf._completed(CLOSE, stamp)
        pd.testing.assert_index_equal(pd.DatetimeIndex(kept.index), pd.DatetimeIndex(CLOSE.index), check_names=False)


class SourceCheckWrapperTest(unittest.TestCase):
    def test_the_freeze_checks_the_closes_its_forecast_uses(self):
        # _load_data_full hands the completed, tz-naive closes (not Yahoo's raw tz-aware frame) to the
        # check, and forecast_bundle publishes the check's block beside `live`.
        days = pd.date_range("2026-10-26", periods=4, freq="B", tz="America/New_York")
        raw = pd.DataFrame({"Open": 70.0, "High": 71.0, "Low": 69.0, "Close": [70.0, 71.0, 72.0, 73.0],
                            "Volume": 1}, index=days)
        now = pd.Timestamp("2026-10-30T23:00:00Z").to_pydatetime()
        with mock.patch.object(vf, "_yf_history", side_effect=[raw, raw]):
            _, _, _, meta = vf._load_data_full(now=now)
        expected = vf._completed(raw["Close"], now)
        pd.testing.assert_series_equal(meta["cl_close"], expected)
        self.assertIsNone(pd.DatetimeIndex(meta["cl_close"].index).tz)
        # forecast_bundle's use of it: tests/test_vol_forecast.py, the source-check test.


    def test_it_returns_plain_json_and_none_without_closes(self):
        rows = [("2026-10-20", 70.0), ("2026-10-21", float("nan"))]
        with mock.patch("backend.supply_shock_playbook.fetch_wti_daily", return_value=rows):
            block = vf._source_check(CLOSE)
        self.assertEqual(json.loads(json.dumps(block, allow_nan=False)), block)
        self.assertIsNone(vf._source_check(None))

    def test_a_failure_is_reported_by_class_name_and_never_leaks_its_message(self):
        secret = "https://api.eia.gov/v2/x?api_key=SECRET123"
        with mock.patch("backend.supply_shock_playbook.fetch_wti_daily", side_effect=ConnectionError(secret)), \
                self.assertLogs("backend.vol_forecast", level="WARNING") as logs:
            block = vf._source_check(CLOSE)
        self.assertEqual(block, {"status": "unavailable", "reason": "ConnectionError"})
        self.assertNotIn("SECRET123", json.dumps(block) + " ".join(logs.output))
        with mock.patch("backend.market_data.load_snapshot", side_effect=md.SnapshotIntegrityError("digest")), \
                mock.patch("backend.supply_shock_playbook.fetch_wti_daily", return_value=[]), \
                self.assertLogs("backend.vol_forecast", level="WARNING"):
            self.assertEqual(vf._source_check(CLOSE)["status"], "unavailable")


class NeverInASealedEntryTest(unittest.TestCase):
    def test_a_sealed_entry_is_byte_identical_with_and_without_the_block(self):
        payload = json.loads((ROOT / "tests" / "fixtures" / "valid_frozen_payload.json").read_text())
        found = md.freeze_source_check(CLOSE.iloc[:-1], SNAPSHOT, None)          # a finding: one session missing
        with_block = copy.deepcopy(payload)
        with_block["vol_forecast"]["source_check"] = found
        now = pd.Timestamp(payload["frozen_at"]).to_pydatetime() + pd.Timedelta(minutes=5)
        plain, _ = vr.extract_entry(payload, now)
        checked, _ = vr.extract_entry(with_block, now)
        self.assertIsNotNone(plain)
        self.assertEqual(json.dumps(plain, sort_keys=True), json.dumps(checked, sort_keys=True))
        self.assertEqual(vr.entry_digest(plain), vr.entry_digest(checked))


class ValidatorTest(unittest.TestCase):
    def _errors(self, block):
        notes: list[str] = []
        return vfp._validate_source_check(block, notes), notes

    def test_every_shape_the_check_produces_passes_and_findings_are_notes(self):
        later = sessions("2026-10-20", "2026-10-30")
        live = pd.concat([CLOSE.iloc[:-1], pd.Series(float(CLOSE.iloc[-1]), index=later)])
        bad_spot = spot_after(later)
        bad_spot.iloc[0] *= 3
        shapes = [md.freeze_source_check(CLOSE, SNAPSHOT, None), md.freeze_source_check(live, SNAPSHOT, bad_spot),
                  md.freeze_source_check(live, SNAPSHOT, spot_after(later)),
                  {"status": "unavailable", "reason": "ConnectionError"}]
        for block in shapes:
            with self.subTest(block=json.dumps(block)[:80]):
                errors, notes = self._errors(json.loads(json.dumps(block)))
                self.assertEqual(errors, [])
                self.assertTrue(all("SECRET" not in n and len(n) < 120 for n in notes))
        self.assertEqual(len(self._errors(shapes[1])[1]), 2)                  # both checks found something

    def test_malformed_blocks_are_errors(self):
        good = md.freeze_source_check(CLOSE, SNAPSHOT, None)
        cases = {
            "not an object": [1],
            "unavailable with a message": {"status": "unavailable", "reason": "Error: api_key=SECRET"},
            "an unknown key": {**good, "extra": 1},
            "a bad status": {**good, "spot": {**good["spot"], "status": "fail"}},
            "a NaN gap": {**good, "spot": {**good["spot"], "max_abs_log_gap": float("nan")}},
            "a negative count": {**good, "snapshot": {**good["snapshot"], "n_missing": -1}},
            "a bad date": {**good, "snapshot": {**good["snapshot"], "dates": ["Fri, 30 Oct 2026"]}},
            "too many dates": {**good, "snapshot": {**good["snapshot"], "dates": ["2026-10-01"] * 11}},
            "an extra key inside": {**good, "spot": {**good["spot"], "note": "x"}},
        }
        for name, block in cases.items():
            with self.subTest(name):
                self.assertTrue(self._errors(block)[0], name)

    def test_the_block_is_optional(self):
        payload = json.loads((ROOT / "tests" / "fixtures" / "valid_frozen_payload.json").read_text())
        notes: list[str] = []
        self.assertEqual(vfp._validate_vol_forecast(payload["vol_forecast"], None, notes), [])
        payload["vol_forecast"]["source_check"] = None
        self.assertEqual(vfp._validate_vol_forecast(payload["vol_forecast"], None, notes), [])


class HealthTest(unittest.TestCase):
    def _payload(self, block, as_of="2026-10-30"):
        return {"frozen_at": "2026-10-31T12:00:00Z", "vol_forecast": {"live": {"as_of": as_of}, "source_check": block}}

    def test_findings_are_source_incidents_with_stable_keys(self):
        later = sessions("2026-10-20", "2026-10-30")
        live = pd.concat([CLOSE.iloc[:-1], pd.Series(float(CLOSE.iloc[-1]), index=later)])
        bad_spot = spot_after(later)
        bad_spot.iloc[0] *= 3
        block = json.loads(json.dumps(md.freeze_source_check(live, SNAPSHOT, bad_spot)))
        problems = rh.source_problems(self._payload(block))
        self.assertEqual([p.key for p in problems], ["source:snapshot:2026-10-19:1", "source:spot:2026-10-20"])
        self.assertFalse(any(p.needs_a_person for p in problems))
        self.assertEqual(rh.status(problems), rh.INCIDENTS)
        body = rh.markdown(problems, pd.Timestamp("2026-10-31T13:00:00Z").to_pydatetime())
        self.assertIn("**Source checks**", body)
        self.assertIn("<!-- health-incidents: source:snapshot:2026-10-19:1 source:spot:2026-10-20 -->", body)
        self.assertEqual(rh.acknowledged_keys(body), {"source:snapshot:2026-10-19:1", "source:spot:2026-10-20"})

    def test_a_stale_spot_series_and_an_unavailable_check_are_reported(self):
        stale = json.loads(json.dumps(md.freeze_source_check(CLOSE, SNAPSHOT, SNAPSHOT.eia_spot)))
        self.assertEqual([p.key for p in rh.source_problems(self._payload(stale))], ["source:spot-stale:2026-06-15"])
        fresh = json.loads(json.dumps(md.freeze_source_check(CLOSE, SNAPSHOT, spot_after(sessions("2026-10-20", "2026-10-29")))))
        self.assertEqual(rh.source_problems(self._payload(fresh)), [])
        self.assertEqual([p.key for p in rh.source_problems(self._payload({"status": "unavailable", "reason": "X"}))],
                         ["source:unavailable"])

    def test_an_absent_or_malformed_block_never_breaks_the_check(self):
        for payload in ({}, {"vol_forecast": None}, self._payload(None), self._payload([1]),
                        self._payload({"snapshot": "x", "spot": {"dates": 5, "status": "beyond_tolerance"}}),
                        self._payload({"snapshot": {"status": "beyond_tolerance", "dates": ["<b>", 3]}}),
                        self._payload({"snapshot": {}, "spot": {"spot_last": "2026-02-30"}})):
            with self.subTest(payload=str(payload)[:60]):
                self.assertTrue(all(p.key.startswith("source:") for p in rh.source_problems(payload)))


if __name__ == "__main__":
    unittest.main()
