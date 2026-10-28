"""Network-free tests for the volatility research lab, its statistics and its pinned data snapshot.

The look-ahead test is the centerpiece, as in test_vol_forecast.py: every benchmark (including the
GARCH refits, the tree model and the combination) must give the same forecast for a test day no
matter what happens after that day's 14:30 ET settlement, in later sessions or in the test day's
own bar after the settlement (its High and Low run to 17:00 ET). The statistical tests are checked
against cases whose answer is known, and the committed artifact is checked against its own contract
(and, with RUN_SLOW=1, rebuilt from the committed snapshot and compared with
vol_lab.compare_reports).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from backend import forecast_eval as fe
from backend import market_data as md
from backend import vol_forecast as vf
from backend import vol_lab as lab

ROOT = Path(__file__).resolve().parents[1]


def _synthetic_market(n=760, seed=3):
    """GARCH-like returns with a matching OHLC path and an OVX-like series (all positive)."""
    rng = np.random.default_rng(seed)
    var = np.empty(n)
    r = np.empty(n)
    var[0] = 0.0004
    for t in range(n):
        if t:
            var[t] = 0.00001 + 0.08 * r[t - 1] ** 2 + 0.9 * var[t - 1]
        r[t] = rng.standard_t(6) * np.sqrt(var[t] * 4 / 6)
    dates = pd.bdate_range("2010-01-04", periods=n)
    close = 80 * np.exp(np.cumsum(r))
    open_ = np.r_[80.0, close[:-1]] * np.exp(rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.008, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.008, n)))
    ohlc = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close}, index=dates)
    ovx = np.sqrt(var * 252) * 100 * 1.15 * np.exp(rng.normal(0, 0.05, n))
    return r, dates, ovx, ohlc


class RangeVarianceTest(unittest.TestCase):
    def test_rogers_satchell_plus_overnight(self):
        ohlc = pd.DataFrame({"Open": [100.0, 102.0], "High": [101.0, 104.0], "Low": [99.0, 101.0],
                             "Close": [100.5, 103.0]})
        r = np.array([0.0, np.log(103.0 / 100.5)])
        v = lab.range_variance(ohlc, r)
        o, h, low, c = np.log(102.0), np.log(104.0), np.log(101.0), np.log(103.0)
        rs = (h - c) * (h - o) + (low - c) * (low - o)
        self.assertAlmostEqual(v[1], rs + (o - np.log(100.5)) ** 2, places=12)

    def test_roll_day_overnight_gap_is_replaced(self):
        dates = pd.bdate_range("2024-01-02", periods=400)
        rng = np.random.default_rng(12)
        close = 80 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
        open_ = np.r_[80.0, close[:-1]] * np.exp(rng.normal(0, 0.002, 400))
        roll = lab.roll_days(dates)
        level = 1.05 ** np.cumsum(roll)                       # each roll moves the series to a contract 5% higher
        open_, close = open_ * level, close * level
        ohlc = pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.004,
                             "Low": np.minimum(open_, close) * 0.996, "Close": close}, index=dates)
        r = np.r_[0.0, np.diff(np.log(close))]
        naive = lab.range_variance(ohlc, r)
        safe = lab.range_variance(ohlc, r, dates)
        later_rolls = np.where(roll)[0][2:]
        self.assertTrue((safe[later_rolls] < naive[later_rolls] / 10).all())
        np.testing.assert_allclose(safe[~roll], naive[~roll])

    def test_unusable_bar_falls_back_to_squared_return(self):
        ohlc = pd.DataFrame({"Open": [100.0, np.nan], "High": [101.0, np.nan], "Low": [99.0, np.nan],
                             "Close": [100.0, 90.0]})
        r = np.array([0.0, np.log(0.9)])
        self.assertAlmostEqual(lab.range_variance(ohlc, r)[1], np.log(0.9) ** 2, places=12)
        self.assertAlmostEqual(lab.settlement_range_variance(ohlc, r)[1], np.log(0.9) ** 2, places=12)

    def test_settlement_part_is_overnight_plus_open_to_settle(self):
        ohlc = pd.DataFrame({"Open": [100.0, 102.0], "High": [101.0, 104.0], "Low": [99.0, 101.0],
                             "Close": [100.5, 103.0]})
        r = np.array([0.0, np.log(103.0 / 100.5)])
        v = lab.settlement_range_variance(ohlc, r)
        self.assertAlmostEqual(v[1], np.log(102.0 / 100.5) ** 2 + np.log(103.0 / 102.0) ** 2, places=12)
        wide = ohlc.assign(High=ohlc["High"] * 1.2, Low=ohlc["Low"] * 0.8)     # the bar after 14:30 ET
        np.testing.assert_array_equal(lab.settlement_range_variance(wide, r), v)


def _widen_bar(ohlc, t, factor):
    """Session t's High and Low moved outward, its Open and Close unchanged."""
    out = ohlc.copy()
    out.iloc[t, out.columns.get_loc("High")] *= factor
    out.iloc[t, out.columns.get_loc("Low")] /= factor
    return out


def _row_features(design, i):
    """Every input any model reads at row i: each regression design and the tree model's features."""
    designs = lab._regression_designs(design.X, design.XR, design.lev, design.ovx_same)
    feats = np.column_stack([design.X, design.XR, design.lev])
    return {**{key: Z[i] for key, Z in designs.items()}, "lgbm": feats[i]}


class NoLookAheadTest(unittest.TestCase):
    """A forecast made at a test day's 14:30 ET settlement may depend only on data known then."""

    def test_every_model_ignores_the_future(self):
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        self.assertGreater(len(design.ends), 40)
        base = lab.walk_forward(design)
        k = len(design.ends) // 2
        t = int(design.pos[design.ends[k]])
        rng = np.random.default_rng(99)
        r2, ovx2 = r.copy(), ovx.copy()
        r2[t + 1:] = rng.normal(0, 0.05, r2.size - t - 1)          # a completely different future
        ovx2[t + 1:] = ovx2[t + 1:] * 3.0
        ohlc2 = _widen_bar(ohlc, t, 1.3)                            # day t's trading after its settlement
        ohlc2.iloc[t + 1:, :] = ohlc2.iloc[t + 1:, :].to_numpy() * 1.7
        d2 = lab.build_design(r2, dates, ovx2, ohlc2)
        moved = lab.walk_forward(d2)
        # Every forecast up to and including week k is unchanged; the TARGET for week k may change.
        for key in lab.MODEL_KEYS:
            np.testing.assert_allclose(base[key].iloc[: k + 1].to_numpy(), moved[key].iloc[: k + 1].to_numpy(),
                                       rtol=1e-9, err_msg=key)
        self.assertFalse(np.allclose(base["actual"].iloc[k + 1:], moved["actual"].iloc[k + 1:]))

    def test_range_features_read_the_test_days_bar_only_to_its_settlement(self):
        # Yahoo's daily High and Low run to the 17:00 ET close, after the 14:30 ET settlement (Close).
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        i = len(design.pos) // 2
        t = int(design.pos[i])
        base = _row_features(design, i)
        same_day = _row_features(lab.build_design(r, dates, ovx, _widen_bar(ohlc, t, 1.3)), i)
        for key, value in base.items():
            np.testing.assert_array_equal(same_day[key], value, err_msg=key)
        day_before = _row_features(lab.build_design(r, dates, ovx, _widen_bar(ohlc, t - 1, 1.3)), i)
        changed = {key for key, value in base.items() if not np.array_equal(day_before[key], value)}
        self.assertEqual(changed, {"har_range", "har_iv_range", "lgbm"})
        self.assertTrue((day_before["har_range"] > base["har_range"]).all())     # RV5, RV22 and RV66 all move

    def test_only_the_v1_timing_row_reads_the_test_days_own_ovx(self):
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        base = lab.walk_forward(design)
        k = len(design.ends) // 2
        t = int(design.pos[design.ends[k]])
        ovx2 = ovx.copy()
        ovx2[t] *= 2.0                                   # the test day's own OVX close only
        moved = lab.walk_forward(lab.build_design(r, dates, ovx2, ohlc))
        changed = {m for m in lab.MODEL_KEYS if not np.isclose(base[m].iat[k], moved[m].iat[k], rtol=1e-12)}
        self.assertEqual(changed, {"har_iv_sameday"})

    def test_grid_matches_the_production_validator(self):
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        X, y, tdate = vf._build_matrix(r, dates, vf.lag_ovx(ovx))       # production: OVX lagged
        prod = vf._walk_forward(X, y, tdate, has_ovx=True)
        frame = lab.walk_forward(design, include_slow=False)
        grid0 = frame[frame["grid"] == 0].reset_index(drop=True)                # the production grid
        np.testing.assert_allclose(grid0["har_iv__med"].to_numpy(), prod["pred"].to_numpy(), rtol=1e-10)
        np.testing.assert_allclose(grid0["har_iv"].to_numpy(), prod["pred_mean"].to_numpy(), rtol=1e-10)
        np.testing.assert_allclose(grid0["har_rv__med"].to_numpy(), prod["pred_har"].to_numpy(), rtol=1e-10)
        np.testing.assert_allclose(grid0["har_rv"].to_numpy(), prod["pred_har_mean"].to_numpy(), rtol=1e-10)
        np.testing.assert_allclose(grid0["ovx_only"].to_numpy(), prod["pred_ovx_mean"].to_numpy(), rtol=1e-10)
        self.assertEqual(list(grid0["date"]), list(prod["date"]))
        self.assertEqual(sorted(frame["grid"].unique()), list(range(vf.STEP)))   # every day is forecast

    def test_ovx_enters_lagged_one_session(self):
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        for i in (0, 10, len(design.pos) - 1):
            t = int(design.pos[i])
            self.assertEqual(design.X[i, 3], ovx[t - 1])                  # entry-time clean
            self.assertEqual(design.ovx_same[i], ovx[t])                  # v1 timing, robustness row only

    def test_variance_consistent_forecast_uses_duan_smearing(self):
        rng = np.random.default_rng(8)
        x = rng.uniform(0.2, 0.6, (20000, 1))
        y = np.exp(np.log(x[:, 0]) + rng.normal(0, 0.3, 20000))
        beta, smear = lab._fit_log(x, y)
        # Normal errors with sd 0.3: E[exp(2 e)] = exp(2 * 0.09).
        self.assertAlmostEqual(smear, np.exp(0.18), delta=0.01)
        med, mean = lab._median_mean(beta, smear, np.array([0.4]))
        self.assertAlmostEqual(mean / med, np.sqrt(smear), places=12)
        # The squared forecast is an unbiased forecast of y^2 at x = 0.4 (Monte Carlo check).
        sims = np.exp(np.log(0.4) + rng.normal(0, 0.3, 200000)) ** 2
        self.assertAlmostEqual(mean ** 2 / sims.mean(), 1.0, delta=0.02)

    def test_roll_days_are_the_session_after_each_last_trade_date(self):
        from backend.contract_calendar import last_trade_date

        dates = pd.bdate_range("2024-01-02", "2024-12-31")
        mask = lab.roll_days(dates)
        expected = set()
        for month in range(2, 14):
            year, m = (2024, month) if month <= 12 else (2025, 1)
            ltd = pd.Timestamp(last_trade_date(year, m))
            later = dates[dates > ltd]
            if len(later):
                expected.add(later[0])
        self.assertEqual(set(dates[mask]), expected)

    def test_live_forecast_is_the_walk_forward_forecast_of_the_same_day(self):
        # Sized so the walk-forward's last row is a day on which it refits GARCH (grid 0) and the tree
        # model (a quarterly refit), with more than WARMUP weeks of matured tree errors behind it, so
        # the tree's out-of-sample smearing factor is in use.
        r, dates, ovx, ohlc = _synthetic_market(n=322 + 5 * lab.LGBM_RETRAIN_WEEKS * vf.STEP)
        design = lab.build_design(r, dates, ovx, ohlc)
        end = design.ends[-1]
        self.assertEqual((end - vf.MIN_TRAIN) % (lab.LGBM_RETRAIN_WEEKS * vf.STEP), 0)
        self.assertGreaterEqual(end - vf.H - vf.MIN_TRAIN + 1, lab.WARMUP * vf.STEP)
        last = lab.walk_forward(design).iloc[-1]
        t = int(design.pos[end])                              # the walk-forward's last test day
        today = (r[:t + 1], dates[:t + 1], ovx[:t + 1], ohlc.iloc[:t + 1])
        for key in lab.MODEL_KEYS:
            # The GARCH fits start cold live and warm-started in the walk-forward: same optimum, last digits.
            rtol = 1e-5 if key in ("garch", "gjr", "combo") else 1e-12
            median, mean = lab._live_forecast(key, *today)
            np.testing.assert_allclose(median, last[f"{key}__med"], rtol=rtol, err_msg=key)
            np.testing.assert_allclose(mean, last[key], rtol=rtol, err_msg=key)
        # The tree model's smearing factor is its out-of-sample one, not its in-sample residuals'.
        live = lab._live_forecast("lgbm", *today)
        feats = np.log(np.column_stack([design.X, design.XR, design.lev]))[:end - vf.H + 1]
        tree = lab._lgbm().fit(feats, np.log(design.y[:end - vf.H + 1]))
        in_sample = float(np.mean(np.exp(2.0 * (np.log(design.y[:end - vf.H + 1]) - tree.predict(feats)))))
        self.assertGreater(abs(live[1] / live[0] - np.sqrt(in_sample)), 1e-3)
        out = lab.live_model_forecast("har_iv", *today)
        self.assertEqual(out["forecast_next_week_vol_annualized_pct"], round(last["har_iv"] * 100, 1))
        self.assertEqual(out["label"], "HAR-IV (model of record)")

    def test_live_range_forecast_uses_the_settlement_time_features(self):
        r, dates, ovx, ohlc = _synthetic_market()
        t = len(r) - 1
        design = lab.build_design(r, dates, ovx, ohlc)
        beta, smear = lab._fit_log(design.XR, design.y)
        _, mean = lab._median_mean(beta, smear, np.array(lab.range_features(design.v_range, design.v_settle, t)))
        out = lab.live_model_forecast("har_range", r, dates, ovx, ohlc)
        self.assertAlmostEqual(out["forecast_next_week_vol_annualized_pct"], round(mean * 100, 1))
        for key in ("har_range", "har_iv_range", "lgbm"):
            wide = lab.live_model_forecast(key, r, dates, ovx, _widen_bar(ohlc, t, 1.3))
            self.assertEqual(wide, lab.live_model_forecast(key, r, dates, ovx, ohlc), key)


class LossAndTestTest(unittest.TestCase):
    def test_qlike_zero_only_at_truth(self):
        self.assertAlmostEqual(float(fe.qlike(np.array([0.3]), np.array([0.3]))[0]), 0.0)
        self.assertGreater(float(fe.qlike(np.array([0.3]), np.array([0.4]))[0]), 0.0)
        self.assertGreater(float(fe.qlike(np.array([0.3]), np.array([0.2]))[0]), 0.0)

    def test_kupiec_known_value(self):
        exceed = np.zeros(250, bool)
        exceed[:10] = True                       # 10 exceedances where 2.5 are expected at 1%
        res = fe.kupiec(exceed, 0.01)
        phat = 10 / 250
        lr = -2 * (240 * np.log(0.99) + 10 * np.log(0.01) - 240 * np.log(1 - phat) - 10 * np.log(phat))
        self.assertAlmostEqual(res["lr"], round(lr, 3), places=3)
        self.assertLess(res["p_value"], 0.001)

    def test_christoffersen_flags_clustering_but_not_iid(self):
        rng = np.random.default_rng(1)
        iid = rng.random(2000) < 0.05
        self.assertGreater(fe.christoffersen(iid, 0.05)["p_independence"], 0.01)
        clustered = np.zeros(2000, bool)
        for start in range(0, 2000, 100):
            clustered[start: start + 5] = True   # 5% exceedances, all in runs
        self.assertLess(fe.christoffersen(clustered, 0.05)["p_independence"], 1e-6)

    def test_dq_flags_predictable_hits(self):
        rng = np.random.default_rng(2)
        hits = rng.random(1500) < 0.05
        self.assertGreater(fe.dq_test(hits, np.full(1500, 0.05), 0.05)["p_value"], 0.01)
        runs = np.zeros(1500, bool)
        runs[np.arange(0, 1500, 40)] = True
        runs[np.arange(1, 1500, 40)] = True      # every hit is followed by another
        self.assertLess(fe.dq_test(runs, np.full(1500, 0.05), 0.05)["p_value"], 1e-6)

    def test_dm_sign_and_size(self):
        rng = np.random.default_rng(4)
        a = rng.normal(1.0, 0.5, 600) ** 2
        better = a * 0.8
        res = fe.dm_test(a, better)
        self.assertGreater(res["stat"], 3)       # positive: the second argument is better
        same = fe.dm_test(a, a + rng.normal(0, 1e-6, 600))
        self.assertLess(abs(same["stat"]), 3)

    def test_mincer_zarnowitz_unbiased_forecast(self):
        rng = np.random.default_rng(5)
        f = rng.uniform(0.2, 0.6, 3000)
        y = np.sqrt(f ** 2 + rng.normal(0, 0.01, 3000))      # E[y^2 | f] = f^2
        mz = fe.mincer_zarnowitz(y, f)
        self.assertAlmostEqual(mz["slope"], 1.0, delta=0.05)
        self.assertGreater(mz["p_unbiased"], 0.01)

    def test_mcs_drops_a_clearly_worse_model(self):
        rng = np.random.default_rng(6)
        good = rng.normal(1.0, 0.2, 500)
        losses = pd.DataFrame({"good": good, "also_good": good + rng.normal(0, 0.05, 500),
                               "bad": good + 0.5})
        res = fe.model_confidence_set(losses, block=10, size=0.10, reps=500, seed=lab.SEED)
        self.assertIn("good", res["included"])
        self.assertNotIn("bad", res["included"])

    @staticmethod
    def _spa_case(seed: int) -> tuple[np.ndarray, pd.DataFrame]:
        """A benchmark, a model that is better by a little on every draw, and a noisy model that is worse."""
        rng = np.random.default_rng(seed)
        bench = rng.normal(1.0, 0.3, 400)
        return bench, pd.DataFrame({"quiet": bench - 0.02 + rng.normal(0, 0.05, 400),
                                    "noisy": bench + 0.02 + rng.normal(0, 1.5, 400)})

    def test_spa_studentized_finds_a_quiet_better_model_the_raw_test_misses(self):
        bench, models = self._spa_case(0)
        res = fe.spa_test(bench, models, block=10, reps=1000, seed=lab.SEED)
        self.assertEqual(res["best_model"], "quiet")
        self.assertLess(res["p_consistent"], 0.01)
        # On raw means the noisy model's bootstrap swings set the maximum and hide the quiet one.
        self.assertGreater(res["non_studentized"]["p_consistent"], 0.10)

    def test_spa_does_not_reject_when_the_benchmark_is_best(self):
        rng = np.random.default_rng(3)
        bench = rng.normal(1.0, 0.3, 400)
        worse = pd.DataFrame({"a": bench + 0.2 + rng.normal(0, 0.1, 400), "b": bench + 0.1 + rng.normal(0, 0.3, 400)})
        res = fe.spa_test(bench, worse, block=10, reps=500, seed=lab.SEED)
        self.assertEqual(res["statistic"], 0.0)
        self.assertEqual((res["p_lower"], res["p_consistent"], res["p_upper"]), (1.0, 1.0, 1.0))
        with self.assertRaises(ValueError):                    # a copy of the benchmark has nothing to test
            fe.spa_test(bench, worse.assign(a=bench), block=10, reps=10, seed=lab.SEED)

    def test_spa_matches_arch_where_arch_is_right(self):
        """The variance kernel is arch's, and on raw means the p-values are arch.bootstrap.SPA's on the
        same draws (arch 8.0.0 computes the non-studentized test whatever its studentize flag says)."""
        from arch.bootstrap import SPA

        bench, models = self._spa_case(1)
        res = fe.spa_test(bench, models, block=10, reps=500, seed=lab.SEED)
        spa = SPA(bench, models, block_size=10, reps=500, bootstrap="stationary", seed=lab.SEED)
        spa.compute()
        self.assertEqual(res["non_studentized"], {f"p_{k}": round(float(v), 4) for k, v in spa.pvalues.items()})
        lrv = fe.stationary_bootstrap_lrv(bench[:, None] - models.to_numpy(), 10)
        np.testing.assert_allclose(lrv, spa._loss_diff_var, rtol=1e-12)          # arch's private kernel

    def test_delayed_aci_uses_only_matured_errors_and_covers(self):
        rng = np.random.default_rng(9)
        e = rng.normal(0, 1, 3000) * np.r_[np.full(1500, 0.3), np.full(1500, 0.9)]
        aci = fe.delayed_aci(e, delay=5, window=500, warmup=200)
        self.assertAlmostEqual(np.nanmean(aci["hit"]), 0.80, delta=0.03)
        # The interval at row i must not change when rows > i - 5 change (they are not yet observed).
        i = 1800
        e2 = e.copy()
        e2[i - 4:] = 50.0
        moved = fe.delayed_aci(e2, delay=5, window=500, warmup=200)
        self.assertEqual(aci["lo"][i], moved["lo"][i])
        self.assertEqual(aci["hi"][i], moved["hi"][i])

    def test_adaptive_conformal_reaches_target_coverage_under_drift(self):
        rng = np.random.default_rng(7)
        scale = np.r_[np.full(600, 0.2), np.full(600, 0.6)]      # error scale triples halfway
        aci = fe.adaptive_conformal(rng.normal(0, 1, 1200) * scale)
        cov = np.nanmean(aci["hit"])
        self.assertAlmostEqual(cov, 0.80, delta=0.03)
        self.assertTrue(np.isnan(aci["hit"][:52]).all())


class SnapshotTest(unittest.TestCase):
    def _frame(self):
        idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"])
        return pd.DataFrame({"Open": [70.0, -1.0, 72.0], "High": [71.0, 73.0, 71.5], "Low": [69.0, 68.0, 71.0],
                             "Close": [70.5, 72.5, 71.8], "Volume": [10, 20, 30]}, index=idx)

    def test_clean_keeps_every_positive_close(self):
        from datetime import datetime, timezone

        cleaned, report = md.clean_ohlc(self._frame(), datetime(2024, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(len(cleaned), 3)
        self.assertTrue(np.isnan(cleaned.loc["2024-01-03", "Open"]))         # unusable bar: range blanked
        self.assertEqual(cleaned.loc["2024-01-03", "Close"], 72.5)            # close kept
        self.assertEqual(cleaned.loc["2024-01-04", "High"], 72.0)             # widened to contain the open
        self.assertEqual(report["range_blanked_nonpositive_bars"], 1)
        self.assertEqual(report["repaired_high_below_open_or_close"], 1)

    def test_roundtrip_and_tamper_detection(self):
        from datetime import datetime, timezone

        now = datetime(2024, 1, 10, tzinfo=timezone.utc)
        cl, quality = md.clean_ohlc(self._frame(), now)
        ovx = pd.Series([30.0, 31.0, 32.0], index=cl.index, name="Close")
        snap = md.MarketSnapshot(cl, ovx, ovx.rename("OVXCLS"), {})
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = md.write_snapshot(snap, quality, now, directory)
            again = md.write_snapshot(snap, quality, now, directory)
            self.assertEqual(first, again)                                    # identical bytes, identical digests
            loaded = md.load_snapshot(directory)
            self.assertEqual(loaded.cl["Close"].tolist(), cl["Close"].tolist())
            path = directory / md.FILES["cl_daily"]
            path.write_text(path.read_text().replace("72.500000", "72.600000"))
            with self.assertRaises(md.SnapshotIntegrityError):
                md.load_snapshot(directory)


class ArtifactContractTest(unittest.TestCase):
    """The committed data/vol_lab.json is internally consistent and pins its inputs."""

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ROOT / "data" / "vol_lab.json").read_text())

    def test_inputs_are_pinned_to_the_committed_snapshot(self):
        manifest = json.loads((ROOT / "data" / "market" / "MANIFEST.json").read_text())
        self.assertEqual(self.report["generated_from"], manifest)
        md.load_snapshot()                                                    # digests verify

    def test_series_lengths_and_sample(self):
        series = self.report["series"]
        n = self.report["sample"]["n_weeks"]
        self.assertGreater(self.report["sample"]["n_days"], 4 * n)
        for key, values in series.items():
            self.assertEqual(len(values), n, key)
        self.assertEqual(series["date"][0], self.report["sample"]["first"])

    def test_noise_floor_is_reported_next_to_direction(self):
        floor = self.report["noise_floor"]
        self.assertGreater(floor["har_direction_acc_pct_mean"], 70)            # ~75% with nothing to forecast
        self.assertEqual(floor["theoretical_floor_pct"], 75.0)
        # One procedure for the lab and the production validator: the same simulations.
        prod = json.loads((ROOT / "data" / "vol_forecast_validation.json").read_text())["overall"]
        self.assertEqual(floor["sims"], vf.NOISE_FLOOR_SIMS)
        self.assertEqual(prod["direction_noise_floor"]["sims"], vf.NOISE_FLOOR_SIMS)

    def test_mcs_settings_are_the_ones_recorded(self):
        self.assertEqual(self.report["config"]["mcs"]["size"], lab.MCS_SIZE)
        self.assertEqual(self.report["config"]["mcs"]["reps"], lab.MCS_REPS)
        self.assertEqual(self.report["config"]["mcs"]["seed"], lab.SEED)
        losses = pd.DataFrame({"a": [1.0, 2.0], "b": [2.0, 1.0]})
        with mock.patch.object(lab, "model_confidence_set", return_value={}) as mcs:
            lab._mcs(losses, 7)
        mcs.assert_called_once_with(losses, block=7, size=lab.MCS_SIZE, reps=lab.MCS_REPS, seed=lab.SEED)

    def test_mcs_membership_matches_p_values(self):
        board = self.report["leaderboard"]
        for row in board["models"]:
            if row["timing_control"]:
                continue
            self.assertEqual(row["in_mcs_qlike"], row["mcs_qlike_p"] >= lab.MCS_SIZE, row["key"])
            self.assertEqual(row["in_mcs_mse"], row["mcs_mse_p"] >= lab.MCS_SIZE, row["key"])
        self.assertEqual({r["key"] for r in board["models"]}, set(lab.MODEL_KEYS))

    def test_timing_control_is_in_no_model_set_or_test_field(self):
        board = self.report["leaderboard"]
        self.assertEqual(board["mcs_excludes"], list(lab.TIMING_CONTROLS))
        self.assertEqual({r["key"] for r in board["models"] if r["timing_control"]}, set(lab.TIMING_CONTROLS))
        robustness = board["mcs_robustness_qlike"]
        sets = [board["mcs_qlike_included"], board["mcs_mse_included"], board["mcs_qlike_range_proxy_included"],
                *board["mcs_qlike_by_grid"].values(), board["spa_qlike_vs_har_iv"]["models"],
                self.report["oos_selection"]["candidates"],
                *(v["included"] if isinstance(v, dict) else v for v in robustness.values())]
        for members in sets:
            self.assertFalse(set(members) & set(lab.TIMING_CONTROLS), members)
        for check in board["spa_robustness_qlike"].values():
            self.assertNotIn(check["best_model"], lab.TIMING_CONTROLS)
        self.assertNotIn(self.report["config"]["challenger"], lab.TIMING_CONTROLS)
        for row in board["models"]:
            if row["timing_control"]:
                for key in ("mcs_qlike_p", "in_mcs_qlike", "mcs_mse_p", "in_mcs_mse", "in_mcs_qlike_grids",
                            "in_mcs_qlike_range_proxy"):
                    self.assertIsNone(row[key], key)
                self.assertIsNotNone(row["dm_qlike_vs_har_iv"])                 # still reported

    def test_champion_matches_the_production_validator(self):
        champ = next(r for r in self.report["leaderboard"]["models"] if r["key"] == lab.CHAMPION)
        prod = json.loads((ROOT / "data" / "vol_forecast_validation.json").read_text())["overall"]
        # Same model, timing and weekly grid (grid 0); the snapshot and the production run may differ
        # by a week at the end of the sample.
        self.assertAlmostEqual(champ["qlike_by_grid"][0], prod["har_qlike"], delta=0.005)

    def test_reproduction_rule_separates_noise_from_a_changed_result(self):
        committed = {"sets": ["a", "b"], "p": 0.0028, "acc": 66.1, "raw": 0.20370903366594823, "n": 4}
        noise = {**committed, "p": 0.0029, "raw": committed["raw"] * (1 + 2e-5)}      # last bits, last place
        self.assertEqual(lab.compare_reports(committed, noise)["violations"], [])
        self.assertFalse(lab.compare_reports(committed, noise)["identical"])
        for key, value in (("sets", ["a"]), ("p", 0.0030), ("acc", 66.3), ("raw", 0.2038), ("n", 5)):
            with self.subTest(key=key):
                self.assertTrue(lab.compare_reports(committed, {**committed, key: value})["violations"])
        self.assertTrue(lab.compare_reports(committed, {**committed, "extra": 1})["violations"])

    @unittest.skipUnless(os.environ.get("RUN_SLOW") == "1", "set RUN_SLOW=1 to rebuild the artifact (~4 min)")
    def test_artifact_reproduces_from_the_snapshot(self):
        rebuilt = json.loads(json.dumps(lab._jsonable(lab.build_report(md.load_snapshot()))))
        if os.environ.get("VOL_LAB_REBUILT"):                                # kept for the CI job summary
            Path(os.environ["VOL_LAB_REBUILT"]).write_text(json.dumps(rebuilt), encoding="utf-8")
        committed = json.loads((ROOT / "data" / "vol_lab.json").read_text())
        self.assertEqual(lab.compare_reports(committed, rebuilt)["violations"], [])


if __name__ == "__main__":
    unittest.main()
