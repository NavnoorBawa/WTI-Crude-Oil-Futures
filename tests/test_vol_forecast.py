"""Network-free unit tests for backend/vol_forecast.py.

The centerpiece is a look-ahead leak check on the feature/target builder, because an undetected
overlapping-target leak is exactly what invalidated this project's original direction headline.
These tests use synthetic data only (no yfinance), so they run deterministically in CI.
"""

import unittest

import numpy as np
import pandas as pd

from backend import vol_forecast as vf


class RVolTest(unittest.TestCase):
    def test_annualization(self):
        # Constant-magnitude returns: realized vol = |c| * sqrt(252).
        c = 0.01
        self.assertAlmostEqual(vf._rvol(np.full(20, c)), c * np.sqrt(252), places=10)

    def test_empty_is_zero(self):
        self.assertEqual(vf._rvol(np.array([])), 0.0)


class BuildMatrixLeakTest(unittest.TestCase):
    """Prove _build_matrix never lets a feature see data at or after its prediction point."""

    def _make(self, r):
        dates = pd.date_range("2020-01-01", periods=len(r), freq="D")
        return vf._build_matrix(np.asarray(r, float), dates)

    def test_features_use_only_past_target_uses_future(self):
        rng = np.random.default_rng(0)
        r = rng.normal(0, 0.01, 200)
        X1, y1, td1 = self._make(r)

        # Perturb one return at index K; rebuild. Any row whose prediction point t < K must have
        # IDENTICAL features (it cannot see index K), while rows whose 5-day target window covers K
        # must have a CHANGED target. That is the leak-free contract.
        K = 150
        r2 = r.copy(); r2[K] += 5.0
        X2, y2, td2 = self._make(r2)

        self.assertEqual(len(X1), len(X2))
        # Map each output row back to its source index t = 66 + row_position.
        for i in range(len(X1)):
            t = 66 + i
            if t < K:
                # feature window is r[:t+1], which excludes index K -> features unchanged
                np.testing.assert_allclose(X1[i], X2[i], atol=1e-12,
                                           err_msg=f"feature at t={t} leaked future index {K}")
            if t + 1 <= K <= t + vf.H:
                # target window r[t+1:t+1+H] includes K -> target must change
                self.assertNotAlmostEqual(y1[i], y2[i], places=9,
                                          msg=f"target at t={t} ignored its own future")

    def test_target_horizon_alignment(self):
        # Target at the first row must equal rvol of exactly the next H returns after t=66.
        r = np.linspace(-0.02, 0.02, 120)
        X, y, td = self._make(r)
        t0 = 66
        expected = vf._rvol(r[t0 + 1 : t0 + 1 + vf.H])
        self.assertAlmostEqual(y[0], expected, places=12)


class FitPredictTest(unittest.TestCase):
    def test_recovers_loglinear_relationship(self):
        # If log(y) = a + b·log(x), the log-HAR OLS should recover (a, b) closely.
        rng = np.random.default_rng(1)
        x = np.exp(rng.normal(0, 0.3, 400))
        y = np.exp(0.5 + 0.8 * np.log(x))
        beta = vf._fit_log_har(x.reshape(-1, 1), y)
        self.assertAlmostEqual(beta[0], 0.5, places=6)
        self.assertAlmostEqual(beta[1], 0.8, places=6)
        self.assertAlmostEqual(vf._predict_log_har(beta, np.array([2.0])),
                               float(np.exp(0.5 + 0.8 * np.log(2.0))), places=6)


class OvxFeatureTest(unittest.TestCase):
    def test_ovx_appended_and_bad_rows_skipped(self):
        rng = np.random.default_rng(2)
        r = rng.normal(0, 0.01, 120)
        dates = pd.date_range("2020-01-01", periods=len(r), freq="D")
        ovx = np.full(len(r), 30.0)
        ovx[80] = np.nan          # one bad reading -> that row must be skipped
        ovx[81] = -1.0            # a non-positive reading -> skipped too
        X3, _, _ = vf._build_matrix(r, dates, None)      # pure HAR -> 3 features
        X4, y4, td4 = vf._build_matrix(r, dates, ovx)    # HAR-IV -> 4 features
        self.assertEqual(X3.shape[1], 3)
        self.assertEqual(X4.shape[1], 4)
        np.testing.assert_allclose(X4[:, 3], 30.0)       # appended OVX column
        self.assertLess(len(X4), len(X3))                # rows with bad OVX were dropped


def _synthetic_market(n=1500, seed=7):
    """Returns with persistent (GARCH-like) volatility plus an OVX that tracks it with noise."""
    rng = np.random.default_rng(seed)
    log_vol = np.empty(n)
    log_vol[0] = np.log(0.02)
    for i in range(1, n):
        log_vol[i] = 0.98 * log_vol[i - 1] + 0.02 * np.log(0.02) + rng.normal(0, 0.08)
    daily_vol = np.exp(log_vol)
    r = rng.normal(0, 1, n) * daily_vol
    dates = pd.bdate_range("2010-01-04", periods=n)
    ovx = daily_vol * np.sqrt(252) * 100 * np.exp(rng.normal(0.1, 0.05, n))
    return r, dates, ovx


class ValidateOfflineTest(unittest.TestCase):
    """End-to-end validate()/live_forecast() on synthetic data (the `data=` hook, no network)."""

    @classmethod
    def setUpClass(cls):
        cls.data = _synthetic_market()
        cls.report = vf.validate(data=cls.data)

    def test_reports_nested_baseline_and_robust_statistics(self):
        o = self.report["overall"]
        self.assertEqual(o["model"], "HAR-IV (RV5,RV22,RV66,OVX)")
        for key in ("har_dir_z_score_hac", "har_vs_mean_reversion_z_hac", "har_no_ovx_dir_acc_pct",
                    "har_qlike", "persistence_qlike", "sample_start", "sample_end",
                    "years_above_base_rate", "leverage_variant_dir_acc_pct", "leverage_gain_dir_z_hac"):
            self.assertIn(key, o)
        # Persistent synthetic vol is forecastable: the model must beat the majority class.
        self.assertGreater(o["har_dir_acc_pct"], o["majority_class_pct"])
        self.assertLess(o["har_qlike"], o["persistence_qlike"])

    def test_p_value_never_underflows_to_zero(self):
        # 1 - cdf rounds to exactly 0.0 for strong results; the survival function must not.
        p = self.report["overall"]["har_dir_p_value_vs_base_rate"]
        self.assertGreater(p, 0.0)
        self.assertLess(p, 0.05)

    def test_economic_tests_are_computed_from_the_walk_forward(self):
        econ = self.report["economic"]
        self.assertIn("vol_target_overlay", econ)
        self.assertIn("variance_risk_premium", econ)
        self.assertEqual(econ["vol_target_overlay"]["n_weeks"], self.report["overall"]["n"])
        # The synthetic OVX is set ~10% above true vol, so the premium must come out positive.
        self.assertGreater(econ["variance_risk_premium"]["mean_premium_vol_pts"], 0)

    def test_live_forecast_uses_ovx_when_current(self):
        live = vf.live_forecast(data=self.data)
        self.assertEqual(live["model"], "HAR-IV (RV5,RV22,RV66,OVX)")
        self.assertIsNotNone(live["implied_vol_ovx_pct"])
        self.assertIn(live["direction"], ("RISING", "FALLING"))

    def test_live_forecast_falls_back_when_latest_ovx_is_missing(self):
        # Previously a missing latest OVX fed 3 features to a 4-coefficient model (shape crash).
        r, dates, ovx = self.data
        stale = ovx.copy()
        stale[-1] = np.nan
        live = vf.live_forecast(data=(r, dates, stale))
        self.assertEqual(live["model"], "HAR (RV5,RV22,RV66)")
        self.assertIsNone(live["implied_vol_ovx_pct"])

    def test_empty_or_short_ovx_falls_back_to_pure_har(self):
        # Previously an all-NaN OVX (yfinance's empty-frame failure mode) skipped every row and crashed.
        r, dates, _ = self.data
        for bad in (np.full(len(r), np.nan), np.where(np.arange(len(r)) > len(r) - 50, 30.0, np.nan)):
            report = vf.validate(data=(r, dates, bad))
            self.assertEqual(report["overall"]["model"], "HAR (RV5,RV22,RV66)")
            self.assertNotIn("har_no_ovx_dir_acc_pct", report["overall"])


class RobustStatisticsTest(unittest.TestCase):
    def test_newey_west_lrv_matches_variance_without_lags(self):
        x = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
        lrv, lags = vf.newey_west_lrv(x, lags=0)
        self.assertEqual(lags, 0)
        self.assertAlmostEqual(lrv, float(np.var(x)), places=12)

    def test_positive_autocorrelation_inflates_long_run_variance(self):
        rng = np.random.default_rng(3)
        e = rng.normal(size=5000)
        ar = np.empty_like(e)
        ar[0] = e[0]
        for i in range(1, len(e)):
            ar[i] = 0.6 * ar[i - 1] + e[i]
        lrv, _ = vf.newey_west_lrv(ar, lags=20)
        self.assertGreater(lrv, 2.0 * float(np.var(ar)))

    def test_zero_volatility_rows_are_skipped_not_logged(self):
        # A run of flat closes would make RV5 = 0 and log(0) = -inf would NaN every coefficient.
        r = np.random.default_rng(4).normal(0, 0.01, 200)
        r[100:106] = 0.0
        X, y, _ = vf._build_matrix(r, pd.date_range("2020-01-01", periods=len(r), freq="D"))
        self.assertTrue(np.all(X > 0))
        self.assertTrue(np.all(y > 0))
        self.assertTrue(np.all(np.isfinite(vf._fit_log_har(X, y))))


if __name__ == "__main__":
    unittest.main()
