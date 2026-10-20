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
from scipy.stats import norm

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

    def test_gappy_curve_still_never_holds_across_a_roll(self):
        # EIA's real history misses business days and repeats prices on some holidays. Expiries
        # must come from the exchange calendar: counting the curve's own rows put them days early,
        # so the "same-contract" hold started before the roll and booked a gap.
        curve = _staircase_curve()
        drop = []
        for year in range(2003, 2008):
            for month in range(1, 13):
                ltd = cc.last_trade_date(year, month)
                after = [d for d in curve.index if ltd < d.date() < ltd + timedelta(days=6)]
                drop += after[1:3]                             # holes between expiry and the 25th
                if month % 4 == 0:
                    drop.append(pd.Timestamp(ltd))             # and on the expiry day itself
        curve = curve.drop(drop)
        thanksgiving = pd.Timestamp("2005-11-24")              # a repeated "settlement" on a holiday
        curve.loc[thanksgiving] = curve.loc[:thanksgiving].iloc[-1]
        cycles = carry.monthly_cycles(curve.sort_index())
        self.assertGreater(len(cycles), 140)
        np.testing.assert_allclose(cycles["ret"], 0.0, atol=1e-12)


class WalkForwardTest(unittest.TestCase):
    def test_threshold_uses_only_prior_months(self):
        idx = pd.date_range("2000-01-31", periods=8, freq="ME")
        cycles = pd.DataFrame({"carry": [1, 2, 3, 4, 2.6, 10, -10, 2.4],
                               "ret": 0.0, "ret_unadjusted": 0.0}, index=idx)
        oos = carry.walk_forward(cycles, min_train=4)
        # Medians of PRIOR carry: 2.5, 2.6, 2.8, 2.6. The first month discriminates: a threshold that
        # included the month itself (median 2.6) would not go long on 2.6.
        self.assertEqual(oos["long"].tolist(), [True, True, False, False])


def _block(carry_values, long_flags, rets):
    idx = pd.date_range("2000-01-31", periods=len(rets), freq="ME")
    return pd.DataFrame({"carry": carry_values, "long": long_flags, "ret": rets}, index=idx)


class SplitLabelTest(unittest.TestCase):
    def test_rule_split_is_not_the_sign_split(self):
        # The rule goes long above the running median, which here is positive: two backwardated months
        # (carry > 0) are flat. The rule's group must not be reported as "backwardated".
        oos = _block([0.01, 0.02, 0.03, 0.04, -0.01, -0.02], [False, False, True, True, False, False],
                     [0.01, 0.02, 0.05, 0.03, -0.01, 0.00])
        rule = carry.evaluate(oos)
        sign = carry.evaluate_sign_split(oos)
        self.assertEqual(rule["n_above_median_carry"], 2)
        self.assertEqual(sign["n_carry_positive"], 4)
        self.assertEqual(sign["mean_ret_carry_positive_pct"], 2.75)
        self.assertEqual(rule["mean_ret_above_median_carry_pct"], 4.0)
        self.assertEqual(sign["share_agreeing_with_median_rule_pct"], round(4 / 6 * 100, 1))

    def test_legacy_keys_alias_the_precise_names(self):
        artifact = json.loads(carry.ARTIFACT_PATH.read_text(encoding="utf-8"))
        for block in ("full_sample", "pre_2007", "post_2007", "original_span_2004_2024"):
            for legacy, precise in carry.LEGACY_ALIASES.items():
                self.assertEqual(artifact[block][legacy], artifact[block][precise], msg=(block, legacy))
                self.assertIn(legacy, artifact["field_notes"])


class SharpeDifferenceTest(unittest.TestCase):
    def test_identical_strategies_have_no_difference(self):
        r = np.random.default_rng(0).normal(0.01, 0.05, 240)
        lw = carry.ledoit_wolf_sharpe_test(r, r)
        self.assertEqual(lw["diff"], 0.0)
        self.assertEqual(lw["p_value"], 1.0)

    def test_standard_error_matches_the_iid_formula(self):
        # Independent i.i.d. normal series: Var(SR1 - SR2) = (1 + SR1^2/2)/T + (1 + SR2^2/2)/T.
        rng = np.random.default_rng(1)
        t = 20000
        a, b = rng.normal(0.1, 1.0, t), rng.normal(0.0, 1.0, t)
        lw = carry.ledoit_wolf_sharpe_test(a, b)
        sr_a, sr_b = a.mean() / a.std(), b.mean() / b.std()
        expected = np.sqrt((2 + sr_a ** 2 / 2 + sr_b ** 2 / 2) / t)
        self.assertAlmostEqual(lw["se"] / expected, 1.0, delta=0.05)
        self.assertLess(lw["p_value"], 1e-6)

    def test_stationary_bootstrap_blocks(self):
        rng = np.random.default_rng(2)
        idx = carry.stationary_bootstrap_indices(50, 1e12, 3, rng)       # one block: consecutive, wrapping
        np.testing.assert_array_equal((idx - idx[:, :1]) % 50, np.tile(np.arange(50), (3, 1)))
        idx = carry.stationary_bootstrap_indices(200, 4.0, 400, np.random.default_rng(3))
        self.assertTrue(((idx >= 0) & (idx < 200)).all())
        breaks = np.mean(np.diff(idx, axis=1) % 200 != 1)
        self.assertAlmostEqual(breaks, 1 / 4.0, delta=0.02)               # a new block w.p. 1 / mean length


class PowerTest(unittest.TestCase):
    def test_power_at_the_mde_is_the_target(self):
        artifact = json.loads(carry.ARTIFACT_PATH.read_text(encoding="utf-8"))
        pw = artifact["power_post_2007"]
        z_a, z_b = norm.ppf(1 - carry.POWER_ALPHA), norm.ppf(carry.POWER_TARGET)
        self.assertAlmostEqual(pw["mde_spread_pct_per_month"], (z_a + z_b) * pw["spread_se_pct"], delta=0.01)
        self.assertAlmostEqual(pw["power_vs_pre_2007_spread"],
                               norm.cdf(pw["pre_2007_spread_pct"] / pw["spread_se_pct"] - z_a), delta=0.005)
        # The published "faded to nothing" reading needs this caveat: the post-2007 sample cannot
        # reliably detect even the pre-2007 effect.
        self.assertGreater(pw["mde_spread_pct_per_month"], pw["pre_2007_spread_pct"])

    def test_identical_regimes_show_no_change(self):
        rng = np.random.default_rng(4)
        block = _block(rng.normal(0, 0.02, 120), rng.random(120) > 0.5, rng.normal(0.005, 0.08, 120))
        change = carry.regime_change_test(block, block.copy())
        self.assertEqual(change["spread_difference_p_value"], 1.0)
        self.assertEqual(change["rank_corr_difference_p_value"], 1.0)


class CommittedArtifactTest(unittest.TestCase):
    def test_artifact_reproduces_from_committed_curve(self):
        result = carry.run()
        artifact = json.loads(carry.ARTIFACT_PATH.read_text(encoding="utf-8"))
        for block in ("full_sample", "pre_2007", "post_2007", "original_span_2004_2024",
                      "roll_bias_of_original_method", "sign_split", "power_post_2007", "regime_change_test",
                      "field_notes", "verdict", "method"):
            self.assertEqual(result[block], artifact[block], msg=block)

    def test_existing_consumer_fields_survive(self):
        # scripts/build_research_bundle.py copies these blocks; the README and the docs read these keys.
        artifact = json.loads(carry.ARTIFACT_PATH.read_text(encoding="utf-8"))
        for block in ("full_sample", "pre_2007", "post_2007", "original_span_2004_2024"):
            for key in ("n_months", "span", "direction_accuracy_pct", "base_rate_pct", "direction_p_value",
                        "mean_ret_backwardated_pct", "mean_ret_contango_pct", "n_backwardated",
                        "spread_p_value_one_sided", "carry_slope_t_hc3", "carry_rank_corr",
                        "carry_rank_corr_p_value", "carry_timed_sharpe", "buy_hold_sharpe"):
                self.assertIn(key, artifact[block], msg=(block, key))
        for key in ("method", "data", "verdict", "generated_at", "roll_bias_of_original_method"):
            self.assertIn(key, artifact)

    def test_committed_curve_is_the_final_eia_history(self):
        curve = carry.load_curve()
        self.assertEqual(str(curve.index.max().date()), "2024-04-05")
        self.assertTrue((curve[["c1", "c2"]] != 0).all().all())


if __name__ == "__main__":
    unittest.main()
