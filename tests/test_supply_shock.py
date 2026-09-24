"""Network-free unit test for backend/supply_shock_playbook.realized_move.

The event study's headline claims (physical-loss vs threat-only price response) all flow through
realized_move, which computes the move around an event from the EIA daily series. This pins its
arithmetic on a synthetic series with known answers, so the computation is guarded without hitting
the EIA API.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import supply_shock_playbook as sp
from backend.supply_shock_playbook import realized_move


class RealizedMoveTest(unittest.TestCase):
    def setUp(self):
        # 30 consecutive days; flat at 100 through the event, a +20% spike one day later,
        # then a 110 plateau. Baseline (5 days before the event) is exactly 100.
        self.dates = [f"2020-01-{d:02d}" for d in range(1, 31)]
        self.series = {}
        for k, d in enumerate(self.dates):
            self.series[d] = 100.0 if k <= 10 else (120.0 if k == 11 else 110.0)

    def test_known_response(self):
        rm = realized_move(self.dates, self.series, "2020-01-11", before=5, window=20, settle=10)
        self.assertEqual(rm["base"], 100.0)
        self.assertEqual(rm["peak_pct"], 20.0)        # 100 -> 120
        self.assertEqual(rm["peak_day"], 1)           # spike is one day after the event
        self.assertEqual(rm["trough_pct"], 0.0)       # never dips below baseline
        self.assertEqual(rm["settle_pct"], 10.0)      # price at event+10 days is 110
        self.assertEqual(rm["trajectory"][0], 0.0)    # day 0 == baseline
        self.assertEqual(rm["trajectory"][1], 20.0)   # day 1 == the spike

    def test_event_after_series_returns_none(self):
        self.assertIsNone(realized_move(self.dates, self.series, "2099-01-01"))

    def test_event_without_enough_forward_data_is_not_scored(self):
        self.assertIsNone(realized_move(self.dates, self.series, "2020-01-25", settle=10))

    def test_day0_reaction_and_further_rise_exclude_day0(self):
        # Day 0 (Jan 11) jumps 100 -> 100 (flat) here; the +20% spike comes the next day, so the
        # further rise after day 0 is 20%, measured from the day-0 close, not from the baseline.
        rm = realized_move(self.dates, self.series, "2020-01-11", before=5, window=20, settle=10)
        self.assertEqual(rm["day0_pct"], 0.0)
        self.assertEqual(rm["further_rise_after_day0_pct"], 20.0)

    def test_big_day0_move_does_not_count_as_further_upside(self):
        # A +10% day 0 followed by a flat tape: the old peak-including-day-0 statistic reported a
        # 10% "eventual peak" for this event; the further rise after day 0 is correctly 0.
        series = {d: (100.0 if k < 10 else 110.0) for k, d in enumerate(self.dates)}
        rm = realized_move(self.dates, series, "2020-01-11", before=5, window=20, settle=10)
        self.assertEqual(rm["day0_pct"], 10.0)
        self.assertEqual(rm["peak_pct"], 10.0)
        self.assertEqual(rm["further_rise_after_day0_pct"], 0.0)


class ClassificationTest(unittest.TestCase):
    def test_threat_only_excludes_gluts_and_demand_events(self):
        payload = sp.get_playbook_for_api()
        threat_n = payload["distributions"]["threat_only"]["n"]
        manual = [e for e in sp.SHOCK_EVENTS
                  if e["supply_mbpd"] == 0.0 and e["type"] not in sp.NON_THREAT_TYPES]
        self.assertEqual(threat_n, len(manual))
        glut_ids = {e["id"] for e in sp.SHOCK_EVENTS if e["type"] in sp.NON_THREAT_TYPES}
        self.assertIn("saudi_russia_price_war_2020", glut_ids)
        self.assertEqual(payload["event_count"], payload["defined_event_count"])


class CacheSafetyTest(unittest.TestCase):
    def test_empty_eia_response_never_replaces_a_good_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "spot.json"
            good = {"fetched_at": "2000-01-01", "rows": [["2020-01-02", 61.2], ["2020-01-03", 63.0]]}
            cache.write_text(json.dumps(good))
            empty = mock.Mock(is_redirect=False, is_permanent_redirect=False)
            empty.json.return_value = {"response": {"data": []}}
            with mock.patch.object(sp, "_CACHE", cache), \
                    mock.patch.object(sp.requests, "get", return_value=empty):
                rows = sp.fetch_wti_daily(api_key="test-key")
            self.assertEqual(rows, [("2020-01-02", 61.2), ("2020-01-03", 63.0)])
            self.assertEqual(json.loads(cache.read_text()), good)


if __name__ == "__main__":
    unittest.main()
