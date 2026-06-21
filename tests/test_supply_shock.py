"""Network-free unit test for backend/supply_shock_playbook.realized_move.

The event study's headline claims (physical-loss vs threat-only price response) all flow through
realized_move, which computes the move around an event from the EIA daily series. This pins its
arithmetic on a synthetic series with known answers, so the computation is guarded without hitting
the EIA API.
"""

import unittest

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


if __name__ == "__main__":
    unittest.main()
