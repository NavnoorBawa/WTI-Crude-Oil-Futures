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


if __name__ == "__main__":
    unittest.main()
