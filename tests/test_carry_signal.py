"""Network-free tests for backend/carry_signal_test.py.

The centerpiece mirrors the bug that invalidated the first carry test: returns measured on an
unadjusted front-month series pick up the roll gap. On a synthetic contango "staircase" where every
contract's price never moves, the true holding return is exactly zero in every month, so any nonzero
return would be a leaked roll gap.
"""

import json
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend import carry_signal_test as carry
from backend import contract_calendar as cc


def _staircase_curve(start=date(2000, 1, 3), end=date(2012, 12, 31)):
    """C1/C2 where contract k is priced 50 + 0.5 k for its whole life (steady contango)."""
    rows, d = [], start
    while d <= end:
        if cc.is_business_day(d):
            y, m = cc.front_contract(d)
            k = (y - 2000) * 12 + m
            rows.append((pd.Timestamp(d), 50 + 0.5 * k, 50 + 0.5 * (k + 1)))
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["date", "c1", "c2"]).set_index("date")


class RollFreeReturnsTest(unittest.TestCase):
    def setUp(self):
        self.cycles = carry.monthly_cycles(_staircase_curve())

    def test_one_cycle_per_contract_month(self):
        self.assertGreater(len(self.cycles), 140)
        gaps = self.cycles.index.to_series().diff().dropna().dt.days
        self.assertTrue(((gaps >= 20) & (gaps <= 40)).all())

    def test_holding_returns_never_include_a_roll_gap(self):
        np.testing.assert_allclose(self.cycles["ret"], 0.0, atol=1e-12)

    def test_unadjusted_front_month_return_does(self):
        # The original method's 21-day contract-1 return crosses a roll in most months and books the
        # contango step as a fake gain; this is the bias the rebuild removes.
        self.assertGreater((self.cycles["ret_unadjusted"] > 0).mean(), 0.5)

    def test_carry_sign(self):
        self.assertTrue((self.cycles["carry"] < 0).all())   # steady contango


class WalkForwardTest(unittest.TestCase):
    def test_threshold_uses_only_prior_months(self):
        idx = pd.date_range("2000-01-31", periods=8, freq="ME")
        cycles = pd.DataFrame({"carry": [1, 2, 3, 4, 10, -10, 2.4, 2.6],
                               "ret": 0.0, "ret_unadjusted": 0.0}, index=idx)
        oos = carry.walk_forward(cycles, min_train=4)
        # medians of prior carry: [1..4]=2.5, [1..10]=3, [..,-10]=2.5, [..,2.4]=2.4
        self.assertEqual(oos["long"].tolist(), [True, False, False, True])


class CommittedArtifactTest(unittest.TestCase):
    def test_artifact_reproduces_from_committed_curve(self):
        result = carry.run()
        artifact = json.loads(carry.ARTIFACT_PATH.read_text(encoding="utf-8"))
        for block in ("full_sample", "pre_2007", "post_2007", "original_span_2004_2024",
                      "roll_bias_of_original_method"):
            self.assertEqual(result[block], artifact[block], msg=block)

    def test_committed_curve_is_the_final_eia_history(self):
        curve = carry.load_curve()
        self.assertEqual(str(curve.index.max().date()), "2024-04-05")
        self.assertTrue((curve[["c1", "c2"]] != 0).all().all())


if __name__ == "__main__":
    unittest.main()
