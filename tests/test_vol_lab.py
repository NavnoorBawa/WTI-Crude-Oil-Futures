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
        self.assertIsNone(res["best_model"])                       # nothing beats it, so nothing leads
        self.assertEqual((res["p_lower"], res["p_consistent"], res["p_upper"]), (1.0, 1.0, 1.0))
        with self.assertRaises(ValueError):                    # a copy of the benchmark has nothing to test
            fe.spa_test(bench, worse.assign(a=bench), block=10, reps=10, seed=lab.SEED)

    def test_spa_studentized_p_matches_a_second_implementation(self):
        """Hansen's p-values written again, vectorized over the draws, with arch's own variance kernel
        (SPA._compute_variance) and arch's stationary-bootstrap draws for the same seed."""
        from arch.bootstrap import SPA, StationaryBootstrap

        bench, models = self._spa_case(2)
        models = models.assign(mid=bench - 0.005 + np.random.default_rng(4).normal(0, 0.4, 400))
        res = fe.spa_test(bench, models, block=10, reps=400, seed=lab.SEED)
        d = bench[:, None] - models.to_numpy()
        n = len(d)
        spa = SPA(bench, models, block_size=10, reps=400, bootstrap="stationary", seed=lab.SEED)
        spa._compute_variance()
        omega = np.sqrt(spa._loss_diff_var)
        means = np.array([data[0].mean(axis=0) for data, _ in StationaryBootstrap(10, d, seed=lab.SEED).bootstrap(400)])
        dbar = d.mean(axis=0)
        t_obs = max((np.sqrt(n) * dbar / omega).max(), 0.0)
        keep = dbar >= -omega * np.sqrt(2 * np.log(np.log(n)) / n)
        for name, centre in (("lower", np.maximum(dbar, 0)), ("consistent", np.where(keep, dbar, 0)), ("upper", dbar)):
            t_star = np.maximum((np.sqrt(n) * (means - centre) / omega).max(axis=1), 0.0)
            self.assertEqual(res[f"p_{name}"], round(float((t_star >= t_obs).mean()), 4), name)

    def test_spa_size_at_the_boundary_of_the_null(self):
        """Every model exactly as good as the benchmark on average, with dependent losses: the consistent
        p-value rejects at 5% about 5% of the time (Hansen's test is mildly liberal at this boundary)."""
        rng = np.random.default_rng(11)
        rejections, sims, n = 0, 120, 300
        for i in range(sims):
            shocks = rng.normal(0, 1, (n + 5, 4))
            common = np.convolve(rng.normal(0, 1, n + 5), np.ones(5) / 5, mode="same")   # serial dependence
            losses = 1.0 + 0.3 * (shocks + common[:, None])[5:]
            scales = np.array([0.5, 1.0, 2.0])                                         # unequal noise
            models = pd.DataFrame(losses[:, :1] + (losses[:, 1:] - losses[:, :1]) * scales, columns=["a", "b", "c"])
            res = fe.spa_test(losses[:, 0], models, block=5, reps=199, seed=i)
            rejections += res["p_consistent"] < 0.05
        self.assertLess(rejections / sims, 0.12)

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


class QlikeFloorTest(unittest.TestCase):
    """The proxy's QLIKE noise floor (docs/ROADMAP.md, Stage 2, item 1): the closed form, the Student-t
    quadrature, the simulations' draws and the simulated bars."""

    def test_the_closed_form_is_the_roadmap_s_and_an_elementary_identity(self):
        from scipy import integrate, special, stats

        floor = lab.normal_qlike_floor(5)
        self.assertLess(abs(floor - (-special.digamma(2.5) - np.log(0.4))), 1e-9)       # ROADMAP 2.1's acceptance
        self.assertLess(abs(floor - (np.euler_gamma + np.log(10) - 8 / 3)), 1e-9)       # psi(5/2) = 8/3 - gamma - 2 ln 2
        for n in (1, 2, 5, 10):
            with self.subTest(n=n):
                dist = stats.chi2(n, scale=1 / n)
                direct = integrate.quad(lambda x: (x - np.log(x) - 1) * dist.pdf(x), 0, np.inf, limit=400)[0]
                self.assertLess(abs(lab.normal_qlike_floor(n) - direct), 1e-8)

    def test_the_student_t_floor_matches_slow_quadrature_and_falls_with_the_tails(self):
        from scipy import integrate, stats

        def slow(nu, n=5):   # -E ln X by nested adaptive quadrature over W ~ chi2(nu)
            c = (nu - 2) / nu

            def m(u):
                return integrate.quad(lambda w: stats.chi2.pdf(w, nu) * (1 + 2 * u * c * nu / w) ** -0.5,
                                      0, np.inf, limit=200)[0]
            f = lambda s: (m(s / n) ** n - np.exp(-s)) / s   # noqa: E731
            return integrate.quad(f, 0, 1, limit=200)[0] + integrate.quad(f, 1, np.inf, limit=200)[0]

        self.assertLess(abs(lab.t_qlike_floor(5.0) - slow(5.0)), 1e-7)
        self.assertLess(abs(lab.t_qlike_floor(1e7) - lab.normal_qlike_floor(5)), 1e-6)
        self.assertEqual(lab.t_qlike_floor(np.inf), lab.normal_qlike_floor(5))
        values = [lab.t_qlike_floor(nu) for nu in (4.5, 5.0, 7.37, 10.0, 31.5, 200.0, 1000.0)]
        self.assertTrue(all(a > b for a, b in zip(values, values[1:])), values)
        self.assertGreater(values[-1], lab.normal_qlike_floor(5))

    def test_the_simulations_are_the_direction_noise_floor_s_own_draws(self):
        r, dates, _, _ = _synthetic_market(n=400, seed=11)
        seen = []
        build = vf._build_matrix

        def capture(z, *args, **kwargs):
            seen.append(np.array(z, copy=True))
            return build(z, *args, **kwargs)

        with mock.patch.object(vf, "_build_matrix", side_effect=capture), \
                mock.patch.object(vf, "_walk_forward", side_effect=RuntimeError("stop")):
            for k in range(2):
                seen.clear()
                with mock.patch.object(vf, "NOISE_FLOOR_SEED", vf.NOISE_FLOOR_SEED + k), self.assertRaises(RuntimeError):
                    vf.noise_floor(r, dates, sims=1)
                np.testing.assert_array_equal(seen[0], lab.noise_floor_draws(r, k))

    def test_the_simulated_bars_keep_every_return_and_are_valid_bars(self):
        rng = np.random.default_rng(1)
        n = 3000
        z = rng.standard_t(5, n) * 0.02
        dates = pd.bdate_range("2010-01-04", periods=n)
        bars = lab.floor_bars(z, np.full(n, 0.0004), dates, np.random.default_rng(2))
        settle = np.log(bars["Close"].to_numpy())
        np.testing.assert_allclose(np.diff(settle), z[1:], atol=1e-12)
        self.assertLess(abs(settle[0] - np.log(50.0) - z[0]), 1e-12)
        self.assertTrue((bars["Low"] <= np.minimum(bars["Open"], bars["Close"]) + 1e-12).all())
        self.assertTrue((bars["High"] >= np.maximum(bars["Open"], bars["Close"]) - 1e-12).all())

    def test_the_exact_extremes_make_rogers_satchell_unbiased(self):
        # With nothing after the settlement, a bar is one settlement-to-settlement diffusion with constant
        # variance, for which Rogers-Satchell is unbiased; extremes read at the grid points would bias it low.
        n, var = 100_000, 0.0004
        rng = np.random.default_rng(5)
        z = rng.standard_normal(n) * np.sqrt(var)
        with mock.patch.object(lab, "RANGE_FLOOR_SETTLE_SHARE", 0.0):
            bars = lab.floor_bars(z, np.full(n, var), pd.bdate_range("2000-01-03", periods=n), rng)
        o, h, low, c = (np.log(bars[col].to_numpy()) for col in ("Open", "High", "Low", "Close"))
        rs = (h - c) * (h - o) + (low - c) * (low - o)
        self.assertLess(abs(rs.mean() / var - 1), 0.01)


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
        eia = pd.DataFrame({"c1": [70.5, 72.5, 71.8], "c2": [70.9, 72.8, 72.1]}, index=cl.index)
        snap = md.MarketSnapshot(cl, ovx, ovx.rename("OVXCLS"), {}, eia, eia["c1"].rename("spot"))
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
            with self.assertRaisesRegex(ValueError, "EIA second sources"):
                md.write_snapshot(md.MarketSnapshot(cl, ovx, ovx.rename("OVXCLS"), {}), quality, now, directory)


def _sessions(first: str, last: str) -> pd.DatetimeIndex:
    from backend.contract_calendar import is_business_day

    days = pd.date_range(first, last, freq="D")
    return pd.DatetimeIndex([day for day in days if is_business_day(day.date())])


def _snapshot(close: pd.Series, eia: pd.DataFrame, spot: pd.Series | None = None) -> md.MarketSnapshot:
    cl = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0})
    ovx = pd.Series(30.0, index=close.index)
    return md.MarketSnapshot(cl, ovx, ovx, {}, eia, close.rename("spot") if spot is None else spot)


class SecondSourceTest(unittest.TestCase):
    """CL=F against EIA (docs/ROADMAP.md, Stage 1, item 7): the snapshot files, the agreement, the roll audit."""

    def test_add_eia_keeps_every_other_file_and_its_digest(self):
        import shutil

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in ("cl_f_daily.csv", "ovx_yahoo_daily.csv", "ovxcls_fred.csv", md.MANIFEST_NAME):
                shutil.copy(md.SNAPSHOT_DIR / name, directory / name)
            before = json.loads((directory / md.MANIFEST_NAME).read_text())
            before["files"] = {k: v for k, v in before["files"].items() if k not in ("eia_futures", "eia_spot")}
            (directory / md.MANIFEST_NAME).write_text(json.dumps(before, indent=2) + "\n")
            first = md.add_eia(directory)
            self.assertEqual(md.add_eia(directory), first)                    # idempotent, same digests
            self.assertEqual({k: v for k, v in first.items() if k != "files"},
                             {k: v for k, v in before.items() if k != "files"})  # retrieval time and quality kept
            for key, entry in before["files"].items():
                self.assertEqual(first["files"][key], entry)
            self.assertEqual(list(first["files"]), list(md.FILES))
            loaded = md.load_snapshot(directory)
            futures, spot = md.eia_frames()
            pd.testing.assert_frame_equal(loaded.eia_futures, futures, check_names=False, check_freq=False)
            pd.testing.assert_series_equal(loaded.eia_spot, spot, check_names=False, check_freq=False)
            self.assertEqual(float(loaded.eia_futures.loc["2020-04-20", "c1"]), -37.63)   # published as is

    def test_the_pinned_eia_copies_are_the_committed_json_up_to_their_last_date(self):
        # The JSON files are caches the supply-shock study refreshes; the pinned copies stop at the
        # dates the manifest records, so a newer cache neither breaks this nor moves a digest.
        snapshot = md.load_snapshot()
        files = snapshot.manifest["files"]
        futures, spot = md.eia_frames()
        pd.testing.assert_frame_equal(snapshot.eia_futures, futures.loc[:files["eia_futures"]["last"]],
                                      check_names=False, check_freq=False)
        pd.testing.assert_series_equal(snapshot.eia_spot, spot.loc[:files["eia_spot"]["last"]],
                                       check_names=False, check_freq=False)

    def test_agreement_counts_ticks_on_cents_misses_and_missing_sessions(self):
        days = _sessions("2024-03-01", "2024-03-14")                         # 10 sessions
        # Yahoo stores float32: 80.970001 is 80.97, and 81.010001 is one tick from 81.00 only once rounded.
        close = pd.Series([80.0, 80.970001, 81.010001, 81.5, 82.0, 82.5, 83.0, 83.5, 84.0, 84.5], index=days)
        c1 = close.round(2).copy()
        c1.iloc[2] = 81.0                                                      # one tick apart: agreement
        c1.iloc[4] = 82.5                                                      # fifty cents: a miss
        c1.iloc[5] = 82.52                                                     # two ticks: a miss
        c1.iloc[8] = -5.0                                                      # a negative settlement
        eia = pd.DataFrame({"c1": c1, "c2": c1 + 1.0})
        snapshot = _snapshot(close.drop([days[7], days[8]]), eia, (close * 1.02).rename("spot"))  # gaps in CL=F
        found = md.cl_source_agreement(snapshot, sample_first=str(days[3].date()))
        c = found["contract1"]
        self.assertEqual((c["common_days"], c["misses"], c["misses_by_year"]), (8, 2, {"2024": 2}))
        self.assertEqual(c["share_same_cent"], round(5 / 8, 4))
        self.assertEqual(c["share_within_one_tick"], round(6 / 8, 4))
        self.assertEqual(c["largest_gaps"][0], {"date": str(days[4].date()), "yahoo": 82.0, "c1": 82.5, "c2": 83.5})
        self.assertEqual(c["eia_sessions_missing_from_cl"], [str(days[7].date())])     # a gap in Yahoo's series
        self.assertEqual(c["nonpositive_sessions_dropped"], [str(days[8].date())])   # the rule, not a gap
        self.assertEqual(c["since_sample_first"], {"first": str(days[3].date()), "common_days": 5, "misses": 2,
                                                   "miss_dates": [str(days[4].date()), str(days[5].date())]})
        self.assertAlmostEqual(found["spot"]["max_abs_log_gap"], round(float(np.log(1.02)), 4))
        self.assertEqual(found["spot"]["common_days"], 8)

    def _roll_case(self, before_c1, roll_c1, cl_before, cl_roll, drop=None, c2_before=None):
        # CLF24 last traded 2023-12-19; the lab's roll day is the next session, 2023-12-20.
        days = _sessions("2023-12-07", "2023-12-22")
        close = pd.Series(70.0, index=days)
        eia = pd.DataFrame({"c1": 70.0, "c2": 71.0}, index=days)
        before, roll = pd.Timestamp("2023-12-19"), pd.Timestamp("2023-12-20")
        eia.loc[before, "c1"], eia.loc[roll, "c1"] = before_c1, roll_c1
        if c2_before is not None:
            eia.loc[before, "c2"] = c2_before
        close.loc[before], close.loc[roll] = cl_before, cl_roll
        if drop is not None:
            eia = eia.drop(pd.Timestamp(drop))
        return lab.roll_convention_audit(_snapshot(close, eia))

    def test_roll_audit_counts_switches_on_the_roll_day_and_names_early_ones(self):
        on_day = self._roll_case(before_c1=70.0, roll_c1=71.2, cl_before=70.0, cl_roll=71.2)
        self.assertEqual((on_day["contracts_checked"], on_day["on_lab_roll_day"], on_day["mismatches"]), (1, 1, []))
        early = self._roll_case(before_c1=70.0, roll_c1=71.2, cl_before=71.0, cl_roll=71.2)   # on contract 2 a day early
        self.assertEqual(early["mismatches"], [{"contract": "CLF24", "last_trade": "2023-12-19",
                                                "lab_roll_day": "2023-12-20", "yahoo_switch": "2023-12-19",
                                                "offset_sessions": -1}])
        reasons = {
            "no EIA row on the session before or on the roll day": dict(drop="2023-12-19"),
            "contracts 1 and 2 within a tick of each other": dict(c2_before=70.01),
            "CL=F matches neither contract on the session before or on the roll day": dict(cl_before=75.0),
        }
        for reason, change in reasons.items():
            with self.subTest(reason=reason):
                args = {**dict(before_c1=70.0, roll_c1=71.2, cl_before=70.0, cl_roll=71.2), **change}
                found = self._roll_case(**args)
                self.assertEqual([i["reason"] for i in found["indeterminate"]], [reason])
                self.assertEqual(found["on_lab_roll_day"], 0)
        late = self._roll_case(before_c1=70.0, roll_c1=71.2, cl_before=70.0, cl_roll=71.2, drop="2023-12-22")
        self.assertEqual(late["eia_through"], "2023-12-21")

    def test_an_early_switch_is_found_however_far_back_it_starts(self):
        # CL=F follows the next contract (EIA's contract 2) for the 12 sessions up to CLF24's last trade
        # date (2023-12-19): the switch is found back to the session after CLZ23's roll (2023-11-21).
        days = _sessions("2023-11-01", "2023-12-22")
        eia = pd.DataFrame({"c1": 70.0, "c2": 71.0}, index=days)
        eia.loc[days >= pd.Timestamp("2023-12-20"), "c1"] = 71.2
        close = eia["c1"].copy()
        early = (days >= pd.Timestamp("2023-12-04")) & (days <= pd.Timestamp("2023-12-19"))
        close[early] = eia.loc[early, "c2"]
        found = lab.roll_convention_audit(_snapshot(close, eia))
        self.assertEqual(found["mismatches"], [{"contract": "CLF24", "last_trade": "2023-12-19",
                                                "lab_roll_day": "2023-12-20", "yahoo_switch": "2023-12-04",
                                                "offset_sessions": -12}])
        self.assertEqual(found["on_lab_roll_day"], 1)                          # CLZ23 rolled on its roll day

    def test_roll_days_after_eia_are_counted_not_checked(self):
        days = _sessions("2023-11-01", "2024-01-31")
        close = pd.Series(70.0, index=days)
        eia = pd.DataFrame({"c1": 70.0, "c2": 71.0}, index=days[days <= pd.Timestamp("2023-12-15")])
        found = lab.roll_convention_audit(_snapshot(close, eia))
        self.assertEqual(found["roll_days_after_eia"], 2)                     # CLF24 and CLG24 roll after 12-15
        self.assertEqual(found["contracts_checked"], 1)                       # CLZ23 (last trade 2023-11-20)


class ArtifactContractTest(unittest.TestCase):
    """The committed data/vol_lab.json is internally consistent and pins its inputs."""

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ROOT / "data" / "vol_lab.json").read_text())

    def test_inputs_are_pinned_to_the_committed_snapshot(self):
        manifest = json.loads((ROOT / "data" / "market" / "MANIFEST.json").read_text())
        self.assertEqual(self.report["generated_from"], manifest)
        md.load_snapshot()                                                    # digests verify

    def test_the_qlike_floor_is_current_and_never_subtracted(self):
        import inspect

        from backend import research_ledger as rl

        block = self.report["qlike_floor"]
        r, dates, ovx, ohlc = md.research_inputs(md.load_snapshot())
        rebuilt = json.loads(json.dumps(lab._jsonable(lab.qlike_floor(lab.build_design(r, dates, ovx, ohlc)))))
        diff = lab.compare_reports(block, rebuilt)
        self.assertEqual(diff["violations"], [], diff)
        self.assertEqual((block["sims"], block["seed"]), (vf.NOISE_FLOOR_SIMS, vf.NOISE_FLOOR_SEED))
        self.assertEqual(block["five_return"]["student_t5"]["process"], self.report["noise_floor"]["process"])
        five = block["five_return"]
        # ROADMAP 2.1's acceptance: the closed form, and a simulation within 0.01 of the value it estimates.
        self.assertLess(abs(five["normal"]["closed_form"] - lab.normal_qlike_floor(5)), 1e-12)
        self.assertLess(abs(five["normal"]["simulated_mean"] - five["normal"]["closed_form"]), 0.01)
        self.assertLess(abs(five["student_t5"]["simulated_mean"] - five["student_t5"]["exact"]), 0.01)
        self.assertLess(five["normal"]["closed_form"], five["kurtosis_matched"]["exact"])
        self.assertLess(five["kurtosis_matched"]["exact"], five["student_t5"]["exact"])
        # It describes the proxy: nothing that scores, ranks or tests a model reads it, and no test row cites it.
        for key in ("leaderboard", "oos_selection", "conformal", "var_backtest", "yearly"):
            self.assertNotIn("floor", json.dumps(self.report[key]), key)
        for function in (lab.leaderboard, lab._losses, lab.spa_vs_champion, fe.dm_test):
            self.assertNotIn("qlike_floor", inspect.getsource(function), function.__name__)
        self.assertNotIn("qlike_floor", inspect.getsource(rl))

    def test_the_second_source_blocks_are_current_and_say_what_the_docs_say(self):
        snapshot = md.load_snapshot()
        agreement = md.cl_source_agreement(snapshot, self.report["sample"]["first"])
        audit = lab.roll_convention_audit(snapshot)
        self.assertEqual(self.report["cl_source_agreement"], agreement)
        self.assertEqual(self.report["roll_convention_audit"], audit)
        # In the scored sessions EIA covers, CL=F is EIA's contract 1 to the tick but on a handful of
        # days, and around the lab's roll days it equals contract 1 wherever EIA can tell (which says
        # nothing about the lab's calendar itself: roll_convention_audit's docstring).
        since = agreement["contract1"]["since_sample_first"]
        self.assertLessEqual(since["misses"], 5)
        self.assertTrue(all(m["last_trade"] < self.report["sample"]["first"] for m in audit["mismatches"]))
        self.assertGreater(audit["on_lab_roll_day"], 0.9 * audit["contracts_checked"])

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
