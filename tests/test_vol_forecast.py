"""Network-free unit tests for backend/vol_forecast.py.

The centerpiece is a look-ahead leak check on the feature/target builder, because an undetected
overlapping-target leak is exactly what invalidated this project's original direction headline.
These tests use synthetic data only (no yfinance), so they run deterministically in CI.
"""

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import numpy as np
import pandas as pd
from scipy.stats import binom

from backend import contract_calendar as cc
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


class WalkForwardPurgeTest(unittest.TestCase):
    def test_every_fit_stops_h_minus_1_rows_before_the_test_point(self):
        # Row t's target is the vol of days t+1..t+H, so a fit for test row `end` may only use rows
        # whose target has matured by `end`: rows < end - (H - 1). Training on more is the leak.
        r, dates, _ = _synthetic_market(n=700)
        X, y, tdate = vf._build_matrix(r, dates, None)
        seen = []
        real_fit = vf._fit_log_har

        def recording_fit(X_train, y_train):
            seen.append(len(X_train))
            return real_fit(X_train, y_train)

        with mock.patch.object(vf, "_fit_log_har", side_effect=recording_fit):
            vf._walk_forward(X, y, tdate, has_ovx=False)
        expected = [end - (vf.H - 1) for end in range(vf.MIN_TRAIN, len(X), vf.STEP)]
        self.assertEqual(seen, expected)

    def test_offsets_test_the_other_days_of_the_week(self):
        r, dates, _ = _synthetic_market(n=700)
        X, y, tdate = vf._build_matrix(r, dates, None)
        grids = [set(vf._walk_forward(X, y, tdate, has_ovx=False, offset=k)["date"]) for k in range(vf.STEP)]
        self.assertTrue(all(not (a & b) for i, a in enumerate(grids) for b in grids[i + 1:]))   # disjoint
        self.assertEqual(len(set().union(*grids)), len(X) - vf.MIN_TRAIN)                     # complete

    def test_p_value_is_the_exact_binomial_tail(self):
        r, dates, _ = _synthetic_market(n=900)
        X, y, tdate = vf._build_matrix(r, dates, None)
        d = vf._walk_forward(X, y, tdate, has_ovx=False)
        o = vf._summarize(d, has_ovx=False)["overall"]
        k, n = int(d["hit"].sum()), len(d)
        base = max(d["act_up"].mean(), 1 - d["act_up"].mean())
        self.assertAlmostEqual(o["har_dir_p_value_vs_base_rate"], float(binom.sf(k - 1, n, base)), places=15)


def _staircase(start=date(2009, 1, 2), end=date(2013, 12, 31)):
    """EIA-style C1/C2 where contract k is priced 50 + 0.5 k for its whole life (steady contango)."""
    rows, d = [], start
    while d <= end:
        if cc.is_business_day(d):
            y, m = cc.front_contract(d)
            k = (y - 2009) * 12 + m
            rows.append((pd.Timestamp(d), 50 + 0.5 * k, 50 + 0.5 * (k + 1)))
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["date", "c1", "c2"]).set_index("date")


class SameContractReturnsTest(unittest.TestCase):
    def test_roll_gaps_are_removed_through_the_end_of_the_curve(self):
        # CL=F is contract 1, unadjusted: flat contracts in contango still show a positive return on
        # every roll day. A position rolled at the expiry close earned exactly nothing.
        curve = _staircase()
        dates = pd.DatetimeIndex([d for d in curve.index if d >= pd.Timestamp("2010-01-04")]
                                 + list(pd.bdate_range("2014-01-02", periods=40)))
        c1 = np.concatenate([curve["c1"].reindex(dates[dates <= curve.index[-1]]).to_numpy(),
                             np.full((dates > curve.index[-1]).sum(), 80.0)])
        r = np.diff(np.log(c1), prepend=np.log(c1[0]))
        self.assertGreater(int((r[dates <= curve.index[-1]] > 0).sum()), 40)   # ~one gap per month
        adj, rebuilt, end = vf.same_contract_returns(r, dates, curve)
        self.assertEqual(dates[end], curve.index[-1])
        np.testing.assert_allclose(adj[: end + 1], 0.0, atol=1e-12)
        np.testing.assert_array_equal(adj[end + 1:], r[end + 1:])   # beyond EIA: untouched
        self.assertGreaterEqual(rebuilt, 47)


class CompletedSessionsTest(unittest.TestCase):
    def test_the_bar_of_a_session_still_trading_is_dropped(self):
        idx = pd.DatetimeIndex(["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]).tz_localize("America/New_York")
        close = pd.Series([90.0, 91.0, float("nan"), 92.5], index=idx)
        tue_10am = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)
        tue_1730 = datetime(2026, 10, 13, 21, 30, tzinfo=timezone.utc)
        self.assertEqual([str(d.date()) for d in vf._completed(close, tue_10am).index],
                         ["2026-10-08", "2026-10-09"])                 # Monday NaN, Tuesday live
        self.assertEqual([str(d.date()) for d in vf._completed(close, tue_1730).index],
                         ["2026-10-08", "2026-10-09", "2026-10-13"])   # Tuesday has settled


class HolidayBarTest(unittest.TestCase):
    def test_a_holiday_bar_anywhere_in_the_history_is_dropped(self):
        # Yahoo kept a 2025-07-04 bar (a partial of the next session) between two settlements.
        idx = pd.DatetimeIndex(["2025-07-02", "2025-07-03", "2025-07-04", "2025-07-07"])
        close = pd.Series([67.4, 67.0, 66.5, 67.93], index=idx)
        later = datetime(2025, 7, 8, 19, 0, tzinfo=timezone.utc)        # long after, mid-session
        kept = vf._completed(close, later)
        self.assertEqual([str(d.date()) for d in kept.index], ["2025-07-02", "2025-07-03", "2025-07-07"])


class EconomicDiagnosticsTest(unittest.TestCase):
    def test_both_proxies_are_reported_without_the_worst_year_and_roll_gaps_are_measured(self):
        report = vf.validate(data=_synthetic_market())
        vrp = report["economic"]["variance_risk_premium"]
        self.assertIn("short_vol_swap_sharpe_ex_worst_year", vrp)
        self.assertIn("short_variance_sharpe_ex_worst_year", vrp)
        returns = report["economic"]["returns"]
        for key in ("roll_gap_mean_pct", "roll_gap_median_pct", "har_dir_acc_pct_on_same_contract_returns"):
            self.assertIn(key, returns)
        self.assertEqual(returns["n_on_same_contract_returns"], report["overall"]["n"])


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
