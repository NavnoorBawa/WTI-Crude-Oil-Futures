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
from scipy import integrate, optimize, special, stats

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

    def _labels(self, r, dates, ovx, pos):
        return lab.regime_labels(r, dates, ovx, pos)["labels"]

    def test_regime_labels_ignore_later_ovx_and_returns(self):
        # ROADMAP 2.2's acceptance: later OVX values (and later returns) leave every label up to t unchanged.
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        pos = design.pos[np.asarray(design.ends)]
        base = self._labels(r, dates, ovx, pos)
        rng = np.random.default_rng(5)
        moved_somewhere = False
        for k in (len(pos) // 3, 2 * len(pos) // 3):
            t = int(pos[k])
            ovx2, r2 = ovx.copy(), r.copy()
            ovx2[t:] *= rng.uniform(0.5, 2.0, ovx2.size - t)      # from the decision's own same-day close on
            r2[t + 1:] *= rng.uniform(0.5, 2.0, r2.size - t - 1)
            moved = self._labels(r2, dates, ovx2, pos)
            for var in lab.REGIMES:
                np.testing.assert_array_equal(base[var][: k + 1], moved[var][: k + 1], err_msg=f"{var} at {k}")
                moved_somewhere |= bool((base[var][k + 1:] != moved[var][k + 1:]).any())
        self.assertTrue(moved_somewhere, "the perturbation must be able to change later labels")

    def test_every_regime_cut_point_reads_strictly_earlier_sessions(self):
        # The exact inputs, row by row: each third's history is every finite value before t, the gap's median is over
        # the previous REGIME_GAP_WINDOW gaps, and each label compares the value at t with those cut points. A
        # perturbation test can miss a cut point that peeks a session or two ahead; this cannot.
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        pos = design.pos[np.asarray(design.ends)]
        ovx_l, rv5, gap = lab.regime_inputs(r, ovx)
        calls = []
        tercile = lab._tercile

        def spy(value, history):
            calls.append((value, np.array(history, copy=True)))
            return tercile(value, history)

        with mock.patch.object(lab, "_tercile", side_effect=spy):
            out = lab.regime_labels(r, dates, ovx, pos)
        self.assertEqual(len(calls), 2 * pos.size)
        for i, t in enumerate(pos):
            (ovx_value, ovx_hist), (rv5_value, rv5_hist) = calls[2 * i], calls[2 * i + 1]
            np.testing.assert_array_equal(ovx_hist, ovx_l[:t][np.isfinite(ovx_l[:t])])
            np.testing.assert_array_equal(rv5_hist, rv5[:t][np.isfinite(rv5[:t])])
            self.assertEqual((ovx_value, rv5_value), (ovx_l[t], rv5[t]))
            earlier = gap[:t][np.isfinite(gap[:t])][-lab.REGIME_GAP_WINDOW:]
            self.assertEqual(out["gap_median"][i], np.median(earlier))
            self.assertEqual(out["labels"]["ovx_rv_gap"][i], int(gap[t] > np.median(earlier)))
            for var, value, history in (("ovx_tercile", ovx_l[t], ovx_hist), ("rv5_tercile", rv5[t], rv5_hist)):
                c1, c2 = np.quantile(history, [1 / 3, 2 / 3])
                self.assertEqual(out["labels"][var][i], int(value > c1) + int(value > c2), (var, i))

    def test_regime_labels_survive_an_extreme_future_at_every_25th_row(self):
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        pos = design.pos[np.asarray(design.ends)]
        base = self._labels(r, dates, ovx, pos)
        for k in range(0, pos.size, 25):
            t = int(pos[k])
            ovx2, r2 = ovx.copy(), r.copy()
            ovx2[t:] *= 50.0
            r2[t + 1:] *= 0.02
            moved = self._labels(r2, dates, ovx2, pos)
            for var in lab.REGIMES:
                np.testing.assert_array_equal(base[var][: k + 1], moved[var][: k + 1], err_msg=f"{var} at {k}")

    def test_regime_labels_read_the_models_inputs_and_need_history(self):
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        ends = np.asarray(design.ends)
        ovx_l, rv5, gap = lab.regime_inputs(r, ovx)
        pos = design.pos[ends]
        np.testing.assert_allclose(ovx_l[pos], design.X[ends, 3])       # the model's lagged OVX ...
        np.testing.assert_allclose(rv5[pos], design.X[ends, 0])          # ... and its RV5 term
        with self.assertRaises(ValueError):
            lab.regime_labels(r, dates, ovx, np.array([lab.REGIME_MIN_HISTORY - 10]))

    def test_a_value_on_a_cut_point_goes_to_the_lower_third(self):
        history = np.arange(1.0, 301.0)
        c1, c2 = np.quantile(history, [1 / 3, 2 / 3])
        self.assertEqual(lab._tercile(float(c1), history)[0], 0)
        self.assertEqual(lab._tercile(float(c1) + 1e-9, history)[0], 1)
        self.assertEqual(lab._tercile(float(c2), history)[0], 1)
        self.assertEqual(lab._tercile(float(c2) + 1e-9, history)[0], 2)

    def test_the_roll_label_is_the_target_window_on_the_snapshot(self):
        from backend.contract_calendar import spans_roll

        r, dates, ovx, ohlc = md.research_inputs(md.load_snapshot())
        ovx = vf._pick_ovx(ovx)
        design = lab.build_design(r, dates, ovx, ohlc)
        pos = design.pos[np.asarray(design.ends)]
        labels = self._labels(r, dates, ovx, pos)["target_spans_roll"]
        expected = np.array([spans_roll(dates[t].date(), dates[t + vf.H].date()) for t in pos])
        np.testing.assert_array_equal(labels, expected.astype(int))

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
        params = [f"{m}__{p}" for m in ("garch", "gjr") for p in ("h1", *lab.GARCH_PARAMS)]
        for key in [*lab.MODEL_KEYS, *params]:
            np.testing.assert_allclose(base[key].iloc[: k + 1].to_numpy(), moved[key].iloc[: k + 1].to_numpy(),
                                       rtol=1e-9, err_msg=key)
        self.assertFalse(np.allclose(base["actual"].iloc[k + 1:], moved["actual"].iloc[k + 1:]))
        # The kept parameters are the ones behind each GARCH forecast: their expected variance path (gamma / 2 for
        # symmetric shocks) gives the published point forecast, so the predictive distribution simulates that model.
        for m in ("garch", "gjr"):
            mu, omega, alpha, gamma, beta, _ = (base[f"{m}__{p}"].to_numpy() for p in lab.GARCH_PARAMS)
            h = [base[f"{m}__h1"].to_numpy()]
            for _ in range(vf.H - 1):
                h.append(omega + (alpha + gamma / 2 + beta) * h[-1])
            point = np.sqrt(np.mean(h, axis=0) * lab.ANNUAL) / lab.GARCH_SCALE
            np.testing.assert_allclose(point, base[m].to_numpy(), rtol=1e-12, err_msg=m)
            self.assertEqual(bool((gamma == 0).all()), m == "garch")
            self.assertTrue(np.isfinite(mu).all())

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


class CoverageByGroupTest(unittest.TestCase):
    """forecast_eval.coverage_by_group_test against statsmodels and against simulations with a known answer."""

    def test_it_is_statsmodels_hac_wald(self):
        import statsmodels.api as sm

        rng = np.random.default_rng(1)
        group = rng.integers(0, 3, 1500)
        miss = (rng.random(1500) < np.where(group == 2, 0.26, 0.2)).astype(float)
        dummies = np.column_stack([(group == j).astype(float) for j in (1, 2)])
        fit = sm.OLS(miss, sm.add_constant(dummies)).fit(cov_type="HAC", cov_kwds={"maxlags": 10})
        expected = fit.wald_test("x1 = 0, x2 = 0", scalar=True)
        got = fe.coverage_by_group_test(miss, group, 3, 0.2, 10)
        self.assertAlmostEqual(got["equal"]["wald"], float(expected.statistic), places=8)
        self.assertAlmostEqual(got["equal"]["p_value"], float(expected.pvalue), places=8)
        self.assertEqual((got["equal"]["df"], got["all_cells"]["df"], got["lags"]), (2, 3, 10))

    @staticmethod
    def _markov(rng, n, k, spell):
        stay = 1 - 1 / spell
        states = np.empty(n, dtype=int)
        states[0] = rng.integers(k)
        for i in range(1, n):
            states[i] = states[i - 1] if rng.random() < stay else rng.integers(k)
        return states

    def test_equal_rates_hold_their_size_and_find_a_difference(self):
        rng = np.random.default_rng(2)
        rejected = np.mean([fe.coverage_by_group_test((rng.random(1500) < 0.2).astype(float),
                                                      self._markov(rng, 1500, 3, 30), 3, 0.2, 10)["equal"]["p_value"] < 0.05
                            for _ in range(600)])
        self.assertTrue(0.025 <= rejected <= 0.08, rejected)
        power = []
        for _ in range(200):
            group = self._markov(rng, 3000, 3, 30)
            rate = np.choose(group, [0.25, 0.20, 0.15])
            power.append(fe.coverage_by_group_test((rng.random(3000) < rate).astype(float), group, 3, 0.2, 10)
                         ["equal"]["p_value"] < 0.05)
        self.assertGreater(np.mean(power), 0.95)

    def test_an_empty_group_is_refused(self):
        with self.assertRaises(ValueError):
            fe.coverage_by_group_test(np.zeros(10), np.zeros(10, dtype=int), 2, 0.2, 2)


def _ar1(rng, n, rho, burn=500):
    e = rng.standard_normal(n + burn)
    x = np.empty(n + burn)
    x[0] = e[0]
    for t in range(1, n + burn):
        x[t] = rho * x[t - 1] + e[t]
    return x[burn:]


def _gw_draw(rng, n, beta=0.0, phi=0.0):
    """A 5-step differential d_t = e_{t+1} + ... + e_{t+5} (MA(4), matured at t + 5) with instruments known at t: its
    value 5 rows earlier and two AR(1) states with OVX's and the gap's persistence (0.986, 0.772). beta makes its
    mean move with the first state; phi makes it depend on its matured lag."""
    h = vf.H
    e = rng.standard_normal(n + h)
    c = np.r_[0.0, np.cumsum(e)]
    d = c[h + 1:n + h + 1] - c[1:n + 1]
    x1, x2 = _ar1(rng, n, 0.986), _ar1(rng, n, 0.772)
    d = d + beta * np.sqrt(5.0) * (x1 - x1.mean()) / x1.std()
    if phi:
        out = d.copy()
        for t in range(h, n):
            out[t] = phi * out[t - h] + d[t]
        d = out
    lagged = np.r_[np.full(h, np.nan), d[:-h]]
    ok = np.isfinite(lagged)
    return d[ok], np.column_stack([lagged, x1, x2])[ok]


def _rolling_dm_prefix(d, window, *, lags, kernel="truncated"):
    """A second implementation of forecast_eval.rolling_dm from prefix sums (centred within each window:
    sum (d_t - m)(d_{t-j} - m) = sum d_t d_{t-j} - m (sum d_t + sum d_{t-j}) + (window - j) m^2)."""
    d = np.asarray(d, float)
    n = d.size
    c = np.r_[0.0, np.cumsum(d)]
    s = c[window:] - c[:-window]
    mean = s / window
    a = np.arange(n - window + 1)
    weights = [1.0] + [2.0 * (1.0 if kernel == "truncated" else 1.0 - j / (lags + 1.0)) for j in range(1, lags + 1)]
    lrv = np.zeros(a.size)
    for j, w in enumerate(weights):
        prod = np.r_[0.0, np.cumsum(d[j:] * d[:n - j])]
        lrv += w * ((prod[a + window - j] - prod[a]) - mean * (c[a + window] - c[a + j])
                    - mean * (c[a + window - j] - c[a]) + (window - j) * mean ** 2) / window
    return s / np.sqrt(window * lrv)


_NULLS: dict = {}


def _cached_null(n, windows, *, draws, seed, batch=1000):
    """forecast_eval.fluctuation_null, computed once per module for each (n, windows, draws, seed)."""
    key = (n, tuple(windows), draws, tuple(seed))
    if key not in _NULLS:
        _NULLS[key] = fe.fluctuation_null(n, windows, draws=draws, seed=seed, batch=batch)
    return _NULLS[key]


_FAST: dict = {}


def _fast_lab():
    """The snapshot's fast walk-forward frame (no GARCH, GJR, LightGBM or combination), with what 2.2 and 2.3 read."""
    if not _FAST:
        r, dates, ovx, ohlc = md.research_inputs(md.load_snapshot())
        ovx = vf._pick_ovx(ovx)
        design = lab.build_design(r, dates, ovx, ohlc)
        frame = lab.walk_forward(design, include_slow=False)
        pos = design.pos[np.asarray(design.ends)]
        _FAST.update(r=r, dates=dates, ovx=ovx, design=design, frame=frame, pos=pos,
                     regimes=lab.regime_labels(r, dates, ovx, pos))
    return _FAST


class GiacominiWhiteTest(unittest.TestCase):
    """forecast_eval.gw_test: ROADMAP 2.3's acceptance (size when the differential is not predictable, power when it
    is) and cases with a known answer."""

    def test_it_holds_its_size_and_rejects_a_predictable_differential(self):
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 2303]))
        full = cond = 0
        draws = 2000
        for _ in range(draws):
            g = fe.gw_test(*_gw_draw(rng, 4000), lags=vf.H - 1)
            full += g["p_value"] < 0.05
            cond += g["conditional"]["p_value"] < 0.05
        self.assertTrue(0.035 <= full / draws <= 0.065, full / draws)
        self.assertTrue(0.035 <= cond / draws <= 0.065, cond / draws)
        for kw in ({"beta": 0.25}, {"phi": 0.15}):
            hits = [fe.gw_test(*_gw_draw(rng, 4000, **kw), lags=vf.H - 1) for _ in range(150)]
            self.assertGreater(np.mean([g["p_value"] < 0.05 for g in hits]), 0.9, kw)
            self.assertGreater(np.mean([g["conditional"]["p_value"] < 0.05 for g in hits]), 0.9, kw)

    def test_known_answers_and_invariances(self):
        rng = np.random.default_rng(7)
        d, x = _gw_draw(rng, 1500, beta=0.1)
        n = d.size
        # With no lags, W = n R^2 of a vector of ones regressed on Z (and W_c on u).
        g0 = fe.gw_test(d, x, lags=0)
        z = np.column_stack([np.ones(n), x]) * d[:, None]
        ones = np.ones(n)
        fit = z @ np.linalg.lstsq(z, ones, rcond=None)[0]
        self.assertAlmostEqual(g0["stat"], n * (fit @ fit) / n, places=8)
        u = (x - x.mean(axis=0)) * (d - d.mean())[:, None]
        fit = u @ np.linalg.lstsq(u, ones, rcond=None)[0]
        self.assertAlmostEqual(g0["conditional"]["stat"], fit @ fit, places=8)
        # The conditional part ignores a constant shift of the differential; the full test does not.
        g = fe.gw_test(d, x, lags=4)
        shifted = fe.gw_test(d + 3.0, x, lags=4)
        self.assertAlmostEqual(g["conditional"]["stat"], shifted["conditional"]["stat"], places=8)
        self.assertNotAlmostEqual(g["stat"], shifted["stat"], places=3)
        # An invertible affine map of the instruments, as (ln OVX, gap) -> (ln OVX, ln RV5), changes no statistic.
        levels = np.column_stack([x[:, 0], x[:, 1], x[:, 1] - x[:, 2]])
        lv = fe.gw_test(d, levels, lags=4)
        self.assertAlmostEqual(lv["stat"], g["stat"], places=8)
        self.assertAlmostEqual(lv["conditional"]["stat"], g["conditional"]["stat"], places=8)
        b = g["coef"]
        np.testing.assert_allclose(lv["coef"], [b[0], b[1], b[2] + b[3], -b[3]], atol=1e-10)
        self.assertEqual((g["df"], g["conditional"]["df"], g["n"], g["lags"], g["kernel"]), (4, 3, n, 4, "truncated"))

    def test_an_indefinite_hac_matrix_or_a_bad_input_is_refused(self):
        t = np.arange(1200)
        d = np.cos(2 * np.pi * 0.15 * t)            # truncated-kernel sum over lags -4..4 at frequency 0.15 is negative
        x = (1.0 + 0.01 * np.random.default_rng(1).standard_normal(1200))[:, None]
        with self.assertRaises(np.linalg.LinAlgError):
            fe.gw_test(d, x, lags=4)
        with self.assertRaises(ValueError):
            fe.gw_test(np.r_[d[:-1], np.nan], x, lags=4)
        with self.assertRaises(ValueError):
            fe.gw_test(d[:-1], x, lags=4)

    def test_the_instruments_are_known_at_the_decision(self):
        rng = np.random.default_rng(3)
        pos = np.r_[np.arange(300, 700), np.arange(705, 900)]       # a missing stretch of sessions
        d = rng.standard_normal(pos.size)
        ln_ovx, gap = rng.standard_normal(pos.size), rng.standard_normal(pos.size)
        x, ok = lab.gw_instruments(d, pos, ln_ovx, gap)
        for i in range(pos.size):
            earlier = np.flatnonzero(pos == pos[i] - vf.H)
            self.assertEqual(bool(ok[i]), earlier.size == 1, i)
            if earlier.size:
                self.assertEqual(x[i, 0], d[earlier[0]])            # the last matured differential
        for i in (10, 250, 410, 594):
            future = d.copy()
            future[i - vf.H + 1:] = rng.standard_normal(future.size - (i - vf.H + 1)) * 50   # rows t-4.. and after
            x2, ok2 = lab.gw_instruments(future, pos, ln_ovx, gap)
            np.testing.assert_array_equal(x2[: i + 1][ok[: i + 1]], x[: i + 1][ok[: i + 1]])
        self.assertEqual(int(ok.sum()), pos.size - 2 * vf.H)


class FluctuationTest(unittest.TestCase):
    """forecast_eval's fluctuation test: the path against brute force (ROADMAP 2.3's acceptance), the pinned null,
    the critical values and p-values, the window-length adjustment, and the size of the per-window test."""

    WINDOWS = (463, 927, 1390, 1854, 2317)
    SEED = [20261020, 23, 4634]

    def null(self):
        return _cached_null(4634, self.WINDOWS, draws=lab.FLUCT_NULL_DRAWS, seed=self.SEED)

    def test_the_path_is_a_brute_force_rolling_dm(self):
        rng = np.random.default_rng(1)
        d, _ = _gw_draw(rng, 2000)
        fast, lrv = fe.rolling_dm(d, 400, lags=4)
        loop = np.array([w.mean() / np.sqrt(fe.hac_lrv(w, 4, centred=True)[0, 0] / 400)
                         for w in (d[i:i + 400] for i in range(d.size - 399))])
        np.testing.assert_allclose(fast, loop, rtol=0, atol=1e-10)
        np.testing.assert_allclose(fast, _rolling_dm_prefix(d, 400, lags=4), rtol=0, atol=1e-10)
        bart, _ = fe.rolling_dm(d, 400, lags=40, kernel="bartlett")
        np.testing.assert_allclose(bart, _rolling_dm_prefix(d, 400, lags=40, kernel="bartlett"), rtol=0, atol=1e-10)
        loop = np.array([w.mean() / np.sqrt(fe.hac_lrv(w, 40, kernel="bartlett", centred=True)[0, 0] / 400)
                         for w in (d[i:i + 400] for i in range(0, d.size - 399, 97))])
        np.testing.assert_allclose(bart[::97], loop, rtol=0, atol=1e-10)
        self.assertEqual(fast.size, d.size - 399)
        with self.assertRaises(ValueError):
            fe.rolling_dm(np.r_[np.ones(500), rng.standard_normal(500)], 400, lags=4)   # a constant window
        # On the snapshot: three of the lab's differentials.
        fast_lab = _fast_lab()
        frame = fast_lab["frame"]
        for model in ("har_iv_range", "ovx_only", "persistence"):
            dd = fe.qlike(frame["actual"], frame["har_iv"]) - fe.qlike(frame["actual"], frame[model])
            np.testing.assert_allclose(fe.rolling_dm(dd, 927, lags=4)[0], _rolling_dm_prefix(dd, 927, lags=4),
                                       rtol=0, atol=1e-10)

    def test_the_hac_variance_is_the_double_sum(self):
        x = np.random.default_rng(2).standard_normal(300)
        e = x - x.mean()
        direct = sum(e[i] * e[j] for i in range(300) for j in range(300) if abs(i - j) <= 4) / 300
        self.assertAlmostEqual(float(fe.hac_lrv(x, 4, centred=True)[0, 0]), direct, places=10)
        self.assertAlmostEqual(float(fe.hac_lrv(x, 0, centred=True)[0, 0]), float(np.var(x)), places=12)
        bart = e @ e / 300 + 2 * sum((1 - j / 11) * (e[j:] @ e[:-j]) / 300 for j in range(1, 11))
        self.assertAlmostEqual(float(fe.hac_lrv(x, 10, kernel="bartlett", centred=True)[0, 0]), bart, places=10)

    def test_the_null_is_pinned_and_its_critical_values_and_p_values_agree(self):
        null = self.null()
        reference = fe.fluctuation_reference(null)
        result = fe.fluctuation_test([0.0] * 5, null, reference)
        got = [(round(c["0.05"], 4), round(c["0.1"], 4)) for c in result["critical"]]
        self.assertEqual(got, [(3.4951, 3.2657), (3.2434, 2.9997), (3.0812, 2.8227), (2.9394, 2.6625),
                               (2.8127, 2.5369)])
        # The sample's resolution puts the 5% value above Giacomini and Rossi's coarse-grid table, below the limit.
        self.assertTrue(lab.GR2010_TABLE1[0.2][0] < got[1][0] < 3.30)
        draws = null.shape[0]
        for value in np.linspace(2.5, 4.2, 35):
            res = fe.fluctuation_test([value] * 5, null, reference)
            for j in range(5):
                p = res["marginal_p"][j]
                self.assertGreaterEqual(p, 1 / (draws + 1))
                self.assertEqual(p < 0.05, value > res["critical"][j]["0.05"])
                self.assertEqual(p < 0.10, value > res["critical"][j]["0.1"])
            self.assertGreaterEqual(res["p_value"], res["min_p"])
            self.assertEqual(res["min_p"], min(res["marginal_p"]))
        one = null[:, [1]]
        single = fe.fluctuation_test([3.1], one, fe.fluctuation_reference(one))
        self.assertEqual(single["p_value"], single["marginal_p"][0])
        with self.assertRaises(ValueError):
            small = null[:1000]
            fe.fluctuation_test([3.0] * 5, small, fe.fluctuation_reference(small))

    def test_the_draws_are_fixed_by_the_seed_not_the_batch(self):
        a = fe.fluctuation_null(500, (50, 100), draws=999, seed=[1, 2, 3])
        b = fe.fluctuation_null(500, (50, 100), draws=999, seed=[1, 2, 3], batch=97)
        np.testing.assert_array_equal(a, b)
        self.assertFalse(np.array_equal(a, fe.fluctuation_null(500, (50, 100), draws=999, seed=[1, 2, 4])))

    def test_the_per_window_test_holds_its_size_under_the_overlap(self):
        # At mu = 0.2 it holds its size; at mu = 0.1 (463 sessions) it over-rejects, which is why that window's
        # p-value is labelled anti-conservative wherever it is printed.
        null = self.null()
        critical = fe.fluctuation_test([0.0] * 5, null, fe.fluctuation_reference(null))["critical"]
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 2304]))
        rejected, rejected_short = [], []
        for _ in range(2000):
            e = rng.standard_normal(4634 + vf.H)
            c = np.r_[0.0, np.cumsum(e)]
            d = c[vf.H + 1:] - c[1:-vf.H]                        # MA(4): the overlap and nothing else
            rejected.append(np.abs(_rolling_dm_prefix(d, 927, lags=4)).max() > critical[1]["0.05"])
            rejected_short.append(np.abs(_rolling_dm_prefix(d, 463, lags=4)).max() > critical[0]["0.05"])
        self.assertTrue(0.03 <= np.mean(rejected) <= 0.08, np.mean(rejected))
        self.assertGreater(np.mean(rejected_short), 0.06)


class StabilityReportTest(unittest.TestCase):
    """vol_lab.stability_report on a small synthetic frame: a test it cannot compute is published as such, and the
    rest of the report is built."""

    @staticmethod
    def _frame(n=1500, seed=5):
        rng = np.random.default_rng(seed)
        dates = pd.bdate_range("2010-01-04", periods=n)
        actual = np.exp(rng.normal(-1.2, 0.3, n))
        frame = pd.DataFrame({"date": dates, "grid": np.arange(n) % vf.STEP, "actual": actual,
                              "ovx": np.exp(rng.normal(-1.0, 0.2, n))})
        for k, key in enumerate(lab.MODEL_KEYS):
            frame[key] = actual * np.exp(rng.normal(0.0, 0.3 + 0.01 * k, n))
        frame["combo"] = frame["har_iv"]                    # identical forecasts: d = 0 everywhere
        return frame, np.arange(n) + 300, rng.normal(0.2, 0.3, n)

    def test_a_test_that_cannot_be_computed_is_published_as_such(self):
        from backend import research_ledger as rl

        frame, pos, gap = self._frame()
        rep = lab.stability_report(frame, gap, pos, "har_iv_range")
        gw, fl = rep["gw"]["combo"], rep["fluctuation"]["combo"]
        self.assertIsNone(gw["p_value"])
        self.assertIsNone(gw["conditional"]["p_value"])
        self.assertIn("not positive definite", gw["not_computed"])
        self.assertIsNone(fl["p_value"])
        self.assertIsNone(fl["search_adjusted"]["p_value"])
        self.assertIn("not positive", fl["not_computed"])
        for key in lab.MODEL_KEYS:
            if key not in (lab.CHAMPION, "combo"):
                self.assertIsNotNone(rep["gw"][key]["conditional"]["p_value"], key)
                self.assertIsNotNone(rep["fluctuation"][key]["search_adjusted"]["p_value"], key)
        self.assertTrue(all(v is None for v in rep["series"]["fluct_t_challenger"][:20]))
        # The ledger rows built from such a block have no p-value, read as inconclusive, and are outside the family.
        doc = json.loads((ROOT / "data" / "vol_lab.json").read_text())
        for model in doc["leaderboard"]["models"]:
            if model["key"] == "combo":
                model["gw_qlike_vs_har_iv"] = json.loads(json.dumps(lab._jsonable(gw)))
                model["fluctuation_qlike_vs_har_iv"] = json.loads(json.dumps(lab._jsonable(fl)))
        ledger = rl.build(lambda name: doc if name == rl.LAB else rl.load_artifact(name))
        rows = {r["id"]: r for r in ledger["rows"]}
        for rid in ("vol_gw_combo", "vol_fluct_combo"):
            self.assertIsNone(rows[rid]["p_value"], rid)
            self.assertEqual(rows[rid]["status"], "inconclusive", rid)
            self.assertIsNone(rows[rid]["q_value_bh"], rid)
        self.assertEqual(ledger["summary"]["rows_in_multiplicity_family"],
                         ledger["summary"]["rows_with_p_value"] - len(ledger["summary"]["selected_after_search"]))


# --- ROADMAP 2.4: scoring the whole predictive distribution --------------------------------------------------------

DIST_TAUS = np.round(np.arange(1, 20) * 0.05, 2)                # the 19-level grid, 0.05 .. 0.95
DECILES = np.arange(1, 10) / 10.0


def _gauss_crps(y, mu, s):
    """The closed-form CRPS of N(mu, s^2) at y (Gneiting and Raftery 2007)."""
    z = (y - mu) / s
    return s * (z * (2 * stats.norm.cdf(z) - 1) + 2 * stats.norm.pdf(z) - 1 / np.sqrt(np.pi))


def _real_layout():
    """The lab's row layout: one row per out-of-sample end of the snapshot design (4,634), the band's first evaluated
    row (264) and the frame's sampling grid (end - MIN_TRAIN) % STEP. Only the design is built (about 1 s)."""
    if _FAST:
        design = _FAST["design"]
    else:
        r, dates, ovx, ohlc = md.research_inputs(md.load_snapshot())
        design = lab.build_design(r, dates, vf._pick_ovx(ovx), ohlc)
    ends = np.asarray(design.ends)
    return ends.size, lab.WARMUP * vf.STEP + vf.H - 1, (ends - vf.MIN_TRAIN) % vf.STEP


class DistributionScoreTest(unittest.TestCase):
    """forecast_eval's scores of a predictive distribution given by quantiles: the pinball loss and the grid CRPS by
    hand, ROADMAP 2.4's acceptance (a point forecast scores its MAE exactly; the 19-level grid against the Gaussian
    closed form, its analytic bias and its convergence as the grid widens), and the monotone rearrangement."""

    def test_the_pinball_loss_by_hand(self):
        got = fe.pinball_loss(np.array([3.0, 1.0, 2.0]), np.array([1.0, 3.0, 2.0]), 0.1)
        np.testing.assert_allclose(got, [0.2, 1.8, 0.0], rtol=0, atol=1e-15)    # (y - q)(tau - 1{y < q})
        np.testing.assert_allclose(fe.pinball_loss(0.5, np.array([0.2, 0.8]), 0.9), [0.27, 0.03], rtol=0, atol=1e-15)

    def test_the_grid_crps_by_hand_and_its_weight_conditions(self):
        taus, q = np.array([0.25, 0.5, 0.75]), np.array([0.1, 0.2, 0.4])
        # pinball at y = 0.3: 0.2 * 0.25, 0.1 * 0.5, (-0.1)(0.75 - 1); the grid CRPS is 2/3 of their sum
        self.assertAlmostEqual(float(fe.crps_from_quantiles(0.3, q, taus)), 2 / 3 * 0.125, places=15)
        weights = np.array([0.2, 0.6, 0.2])                          # sum 1, sum w tau = 1/2
        self.assertAlmostEqual(float(fe.crps_from_quantiles(0.3, q, taus, weights)),
                               2 * (0.2 * 0.05 + 0.6 * 0.05 + 0.2 * 0.025), places=15)
        rows = fe.crps_from_quantiles(np.array([0.3, 0.0]), np.vstack([q, q]), taus)
        self.assertEqual(rows.shape, (2,))
        self.assertAlmostEqual(float(rows[1]), 2 / 3 * (0.1 * 0.75 + 0.2 * 0.5 + 0.4 * 0.25), places=15)
        for bad in ([0.5, 0.5, 0.5], [0.5, 0.25, 0.25], [-0.1, 1.2, -0.1], [0.5, 0.5]):
            with self.assertRaises(ValueError):                      # sum, sum w tau, sign, length
                fe.crps_from_quantiles(0.3, q, taus, bad)
        with self.assertRaises(ValueError):                          # equal weights on an asymmetric grid
            fe.crps_from_quantiles(0.3, q[:2], taus[:2])
        with self.assertRaises(ValueError):
            fe.crps_from_quantiles(0.3, q[:2], taus)

    def test_a_point_forecast_scores_its_absolute_error(self):
        # ROADMAP 2.4 acceptance 1: every quantile equal to x gives |y - x|, to 1e-12 relative on 10,000 seeded pairs
        # (the design's prototype: at most 1.8e-15). Rectangle weights 0.05 * sum QS give 0.95 MAE and are refused.
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 1]))
        y = rng.standard_t(3, 10_000) * 0.3 + 0.35
        x = rng.lognormal(np.log(0.35), 0.4, 10_000)
        q = np.repeat(x[:, None], DIST_TAUS.size, axis=1)
        mae = np.abs(y - x)
        np.testing.assert_allclose(fe.crps_from_quantiles(y, q, DIST_TAUS), mae, rtol=1e-12, atol=0)
        rectangle = 2 * 0.05 * fe.pinball_loss(y[:, None], q, DIST_TAUS[None, :]).sum(axis=1)
        np.testing.assert_allclose(rectangle, 0.95 * mae, rtol=1e-12, atol=0)
        with self.assertRaises(ValueError):
            fe.crps_from_quantiles(y, q, DIST_TAUS, np.full(DIST_TAUS.size, 0.05))

    def test_the_19_level_grid_against_the_gaussian_closed_form(self):
        # ROADMAP 2.4 acceptance 2. A fixed 19-level grid cannot converge to the closed form: under a correct Gaussian
        # forecast its expected score is (2/19) sum_k phi(Phi^-1(tau_k)) s = 0.5914192445 s against s / sqrt(pi),
        # 4.826% above. Here the expectation of crps_from_quantiles itself, by quadrature (piecewise smooth between
        # the quantiles; the density beyond +-12 is below 1e-31).
        zt = special.ndtri(DIST_TAUS)
        expected, _ = integrate.quad(lambda v: float(fe.crps_from_quantiles(v, zt, DIST_TAUS)) * stats.norm.pdf(v),
                                     -12.0, 12.0, points=list(zt), epsabs=1e-13, epsrel=1e-13, limit=500)
        self.assertAlmostEqual(expected, 0.5914192445, delta=1e-9)
        self.assertAlmostEqual(expected, 2 / 19 * float(np.sum(stats.norm.pdf(zt))), delta=1e-12)
        self.assertAlmostEqual(expected * np.sqrt(np.pi), 1.0482633, delta=1e-7)
        # Paired sample: on 20,000 seeded draws with random mu and s, mean(grid CRPS / s) over mean(closed form / s) on
        # the same draws is within 5e-4 of 1.0482633 (across 2,000 seeds: sd 3.8e-5, largest deviation 1.5e-4).
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 2, 0]))
        mu, s = rng.uniform(-1, 1, 20_000), rng.uniform(0.5, 2, 20_000)
        y = mu + s * rng.standard_normal(20_000)
        grid = fe.crps_from_quantiles(y, mu[:, None] + s[:, None] * zt[None, :], DIST_TAUS)
        ratio = np.mean(grid / s) / np.mean(_gauss_crps(y, mu, s) / s)
        self.assertLess(abs(ratio - 1.0482633), 5e-4)

    def test_the_grid_crps_converges_as_the_grid_widens(self):
        # The same equal-weight estimator on [h, 1 - h] in steps of h: expected relative error 4.83%, 2.45%, 0.99% and
        # 0.0999% at h = 0.05, 0.025, 0.01 and 0.001. The sample errors use 5,000 seeded draws (the design used
        # 20,000; at 5,000 they sit within 4e-5 of the expectation), and at h = 0.001 no single observation is more
        # than 0.004 s from its closed form. Refining inside the fixed [0.05, 0.95] would instead converge to the
        # truncated integral, 2.00% below the closed form, with weights summing to 0.9, which are refused.
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 2]))
        n = 5_000
        mu, s = rng.normal(0, 1, n), rng.lognormal(0, 0.5, n)
        y = mu + s * rng.standard_normal(n)
        closed = _gauss_crps(y, mu, s)
        errors = []
        for h, analytic in ((0.05, 0.04826), (0.025, 0.02450), (0.01, 0.00990), (0.001, 0.000999)):
            k = round(1 / h) - 1
            taus = np.arange(1, k + 1) / (k + 1)
            zt = special.ndtri(taus)
            expected = 2 / k * float(np.sum(stats.norm.pdf(zt))) * np.sqrt(np.pi) - 1
            self.assertAlmostEqual(expected, analytic, delta=6e-5)
            grid = fe.crps_from_quantiles(y, mu[:, None] + s[:, None] * zt[None, :], taus)
            errors.append(grid.mean() / closed.mean() - 1)
            self.assertLess(abs(errors[-1] - expected), 5e-4, h)
        self.assertEqual(errors, sorted(errors, reverse=True))
        self.assertLess(errors[-1], 2e-3)
        self.assertLessEqual(float(np.max(np.abs(grid - closed) / s)), 0.004)
        line = np.linspace(-12, 12, 4001)                         # every outcome, not only the sampled ones
        self.assertLessEqual(float(np.max(np.abs(fe.crps_from_quantiles(line, zt, taus) - _gauss_crps(line, 0, 1)))),
                             0.004)
        inner = np.round(np.arange(0.05, 0.9505, 0.001), 10)
        with self.assertRaises(ValueError):
            fe.crps_from_quantiles(0.0, special.ndtri(inner), inner, np.full(inner.size, 0.001))

    def test_rearrangement_sorts_flags_crossing_and_never_raises_the_score(self):
        q, crossed = fe.rearrange(np.array([[3.0, 1.0, 2.0], [1.0, 2.0, 3.0], [1.0, 1.0, 2.0]]))
        np.testing.assert_array_equal(q, [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0], [1.0, 1.0, 2.0]])
        np.testing.assert_array_equal(crossed, [True, False, False])          # a tie is not a crossing
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 40]))
        raw = np.sort(rng.normal(0, 1, (10_000, 19)), axis=1) + rng.normal(0, 0.6, (10_000, 19))
        y = rng.normal(0, 1.5, 10_000)
        fixed, crossed = fe.rearrange(raw)
        self.assertGreater(crossed.mean(), 0.9)
        before, after = fe.crps_from_quantiles(y, raw, DIST_TAUS), fe.crps_from_quantiles(y, fixed, DIST_TAUS)
        self.assertTrue(np.all(after <= before + 1e-12))
        self.assertLess(after.mean(), before.mean())


class TrailingPitTest(unittest.TestCase):
    """The trailing-residual quantiles and mid-rank PIT of HAR-IV, the challenger and OVX-only, and the simulated null
    their Berkowitz tests are referred to (ROADMAP 2.4)."""

    WINDOW, WARMUP = lab.CONFORMAL_WINDOW, lab.WARMUP * vf.STEP

    def test_they_are_the_band_without_its_adaptive_step(self):
        rng = np.random.default_rng(9)
        e = rng.normal(0, 1, 1800) * np.r_[np.full(900, 0.3), np.full(900, 0.9)]
        kw = {"delay": vf.H, "window": self.WINDOW, "warmup": self.WARMUP}
        tq = fe.trailing_quantiles(e, DIST_TAUS, **kw)
        aci = fe.delayed_aci(e, alpha=0.2, gamma=0.0, **kw)
        np.testing.assert_array_equal(tq["q"][:, 1], aci["lo"])         # tau 0.10, bit for bit (NaN in the warm-up)
        np.testing.assert_array_equal(tq["q"][:, 17], aci["hi"])        # tau 0.90
        first = self.WARMUP + vf.H - 1
        self.assertTrue(np.isnan(tq["q"][:first]).all() and np.isfinite(tq["q"][first:]).all())
        self.assertTrue((tq["rank"][:first] == -1).all() and (tq["n_past"][:first] == 0).all())
        self.assertTrue(np.isnan(tq["pit"][:first]).all())
        for i, n_past in ((first, self.WARMUP), (first + 100, self.WARMUP + 100), (1500, self.WINDOW)):
            past = e[max(0, i - vf.H + 1 - self.WINDOW): i - vf.H + 1]
            self.assertEqual(past.size, n_past)
            np.testing.assert_array_equal(tq["q"][i], np.quantile(past, DIST_TAUS))
            self.assertEqual((tq["rank"][i], tq["n_past"][i]), (int(np.sum(past < e[i])), n_past))
            self.assertEqual(tq["pit"][i], (np.sum(past < e[i]) + 0.5) / (n_past + 1))
        # Only matured residuals: changing rows i - 4 onwards leaves every quantile up to row i unchanged.
        for i in (first, 900, 1500):
            moved = e.copy()
            moved[i - vf.H + 1:] = 50 * rng.standard_normal(e.size - (i - vf.H + 1))
            np.testing.assert_array_equal(fe.trailing_quantiles(moved, DIST_TAUS, **kw)["q"][: i + 1], tq["q"][: i + 1])

    def test_the_vectorised_ranks_and_the_rank_resolved_deciles(self):
        rng = np.random.default_rng(11)
        e = rng.standard_normal(2_000 + self.WARMUP + vf.H - 1)
        first = self.WARMUP + vf.H - 1
        tq = fe.trailing_quantiles(e, DECILES, delay=vf.H, window=self.WINDOW, warmup=self.WARMUP)
        rank, n_past = fe._trailing_ranks(e, first, window=self.WINDOW, delay=vf.H)
        np.testing.assert_array_equal(rank, tq["rank"][first:])
        np.testing.assert_array_equal(n_past, tq["n_past"][first:])
        # The decile bin from the rank (2,000 rows, growing and full windows) equals the bin against np.quantile; the
        # ranks np.quantile's interpolation leaves ambiguous occur and are resolved.
        bins = fe._rank_deciles(e, np.arange(first, e.size), rank, n_past, DECILES, window=self.WINDOW, delay=vf.H)
        np.testing.assert_array_equal(bins, (tq["q"][first:] <= e[first:, None]).sum(axis=1))
        ambiguous = rank[:, None] == np.floor((n_past - 1)[:, None] * DECILES[None, :]) + 1
        self.assertGreater(int(ambiguous.sum()), 5)

    def test_the_null_is_seeded_per_draw(self):
        kw = {"window": 100, "delay": vf.H, "warmup": 60, "seed": [1, 2, 3]}
        grid = np.arange(600) % vf.STEP
        a = fe.rank_pit_null(600, 64, grid, draws=40, bin_draws=10, **kw)
        b = fe.rank_pit_null(600, 64, grid, draws=20, bin_draws=10, **kw)
        np.testing.assert_array_equal(a["lr"][:20], b["lr"])
        np.testing.assert_array_equal(a["exceed"][:20], b["exceed"])
        np.testing.assert_array_equal(a["share_sd_rank"], b["share_sd_rank"])
        self.assertEqual(a["lr"].shape, (40, 5))
        for bad in ({"first": 63}, {"grid": grid[:-1]}, {"bin_draws": 41}, {"bin_draws": 1}):
            args = {"n": 600, "first": 64, "grid": grid, "draws": 40, "bin_draws": 10} | bad
            with self.assertRaises(ValueError):
                fe.rank_pit_null(**args, **kw)

    def test_the_simulated_null_holds_its_size_where_chi2_does_not(self):
        # ROADMAP 2.4 acceptance 3, trailing-rank part, on the real row layout (4,634 rows evaluated from 264, the
        # frame's sampling grids). A 1,000-draw null (the lab draws 10,000) and 300 fresh null draws referred to it:
        # the simulated-null Bonferroni rejects in [1.5%, 8%], the chi2(3) Bonferroni below 2% (the design measured
        # 4.0% and 0.2% with 10,000 and 400). About 7 s.
        n, first, grid = _real_layout()
        self.assertEqual((n, first), (4634, 264))
        kw = {"window": self.WINDOW, "delay": vf.H, "warmup": self.WARMUP}
        null = fe.rank_pit_null(n, first, grid, draws=1000, seed=[20261020, 24, 1], bin_draws=200, **kw)
        fresh = fe.rank_pit_null(n, first, grid, draws=300, seed=[20261020, 24, 2], bin_draws=2, **kw)
        draws = null["lr"].shape[0]
        simulated = [fe.bonferroni_min([(1 + np.sum(null["lr"][:, g] >= lr[g])) / (draws + 1) for g in range(5)])
                     for lr in fresh["lr"]]
        chi2 = [fe.bonferroni_min(stats.chi2.sf(lr, 3)) for lr in fresh["lr"]]
        self.assertTrue(0.015 <= np.mean(np.array(simulated) < 0.05) <= 0.08, np.mean(np.array(simulated) < 0.05))
        self.assertLess(np.mean(np.array(chi2) < 0.05), 0.02)
        # The PIT re-centres itself: the null LR per grid has mean ~1.87 and 95th percentile ~5.2 (chi2(3): 3, 7.81).
        self.assertTrue(np.all((1.6 < null["lr"].mean(axis=0)) & (null["lr"].mean(axis=0) < 2.2)))
        self.assertTrue(4.6 < np.quantile(null["lr"], 0.95) < 5.9)
        # Its decile shares vary less than an exact PIT's (1.96 sd ~0.7 against ~1.2 to 1.8 points), and the tail bins
        # average above 10.
        band_rank, band_exact = 1.96 * null["share_sd_rank"], 1.96 * null["share_sd_exact"]
        self.assertTrue(np.all((0.5 < band_rank) & (band_rank < 0.95)), band_rank)
        self.assertTrue(np.all((1.0 < band_exact) & (band_exact < 2.1)), band_exact)
        self.assertTrue(np.all(band_rank < band_exact))
        self.assertAlmostEqual(float(null["share_mean_rank"].sum()), 100.0, places=9)
        self.assertTrue(null["share_mean_rank"][0] > 10.1 and null["share_mean_rank"][-1] > 10.1)
        # Draw 0 rebuilt by the slow path (trailing_quantiles' loop, berkowitz_test per grid) gives the same LR.
        x = np.random.default_rng(np.random.SeedSequence([20261020, 24, 1, 0])).standard_normal(n + vf.H) ** 2
        c = np.r_[0.0, np.cumsum(x)]
        e = 0.5 * np.log((c[vf.H + 1:] - c[1:n + 1]) / vf.H)
        slow = fe.trailing_quantiles(e, [0.5], **kw)
        z = special.ndtri(slow["pit"][first:])
        np.testing.assert_allclose(null["lr"][0], [fe.berkowitz_test(z[grid[first:] == g])["lr"] for g in range(5)],
                                   rtol=0, atol=1e-9)
        rank, n_past = slow["rank"][first:], slow["n_past"][first:]
        self.assertEqual(null["exceed"][0], int(np.sum((rank == 0) | (rank == n_past))))


class BerkowitzTest(unittest.TestCase):
    """Berkowitz's LR3 and the doubly censored LR: ROADMAP 2.4 acceptance 3 (about 5% on independent uniform PITs,
    hence one test per sampling grid), the closed form against a regression, the censored likelihood against its
    scipy.stats form, the simulated-sample PIT, and the p-value combinations."""

    def test_it_rejects_about_five_percent_of_independent_uniform_pits(self):
        # 2,000 seeded samples of 874 weeks (the design's prototype: 4.88% over 10,000).
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 30]))
        rejected = np.mean([fe.berkowitz_test(special.ndtri(rng.uniform(size=874)))["p_value"] < 0.05
                            for _ in range(2000)])
        self.assertTrue(0.035 <= rejected <= 0.065, rejected)

    def test_overlapping_pits_need_the_sampling_grids(self):
        # Correctly calibrated 5-session PITs on 4,370 days (the chi2(5) cdf of overlapping sums of five squared normal
        # returns): the every-day test rejects nearly always; each grid's 874 weeks hold the size, and the Bonferroni
        # over the five grids is conservative (the design measured 3.1%).
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 20]))
        every, grids, bonferroni = [], [], []
        for k in range(1000):
            c = np.r_[0.0, np.cumsum(rng.standard_normal(4370 + vf.H) ** 2)]
            z = special.ndtri(stats.chi2.cdf(c[vf.H + 1:] - c[1:-vf.H], vf.H))
            if k < 200:
                every.append(fe.berkowitz_test(z)["p_value"] < 0.05)
            p = [fe.berkowitz_test(z[g::vf.STEP])["p_value"] for g in range(vf.STEP)]
            grids.append(np.array(p) < 0.05)
            bonferroni.append(fe.bonferroni_min(p) < 0.05)
        self.assertGreater(np.mean(every), 0.95)
        rates = np.mean(grids, axis=0)
        self.assertTrue(np.all((0.03 <= rates) & (rates <= 0.075)), rates)
        self.assertTrue(0.015 <= np.mean(bonferroni) <= 0.055, np.mean(bonferroni))

    def test_the_closed_form_is_the_regression_likelihood_ratio(self):
        rng = np.random.default_rng(3)
        for rho, scale, shift in ((0.0, 1.0, 0.0), (0.3, 1.2, 0.1), (-0.2, 0.8, -0.3)):
            z = shift + scale * _ar1(rng, 874, rho, burn=50)
            y, x = z[1:], z[:-1]
            design = np.column_stack([np.ones(x.size), x])
            b, *_ = np.linalg.lstsq(design, y, rcond=None)
            resid = y - design @ b
            s2 = resid @ resid / y.size
            lr = 2 * (-0.5 * y.size * (np.log(2 * np.pi * s2) + 1) - stats.norm.logpdf(y).sum())
            got = fe.berkowitz_test(z)
            self.assertAlmostEqual(got["lr"], lr, delta=1e-9)
            self.assertAlmostEqual(got["rho"], b[1], places=12)
            self.assertAlmostEqual(got["mu"], b[0] / (1 - b[1]), places=12)
            self.assertAlmostEqual(got["sigma"], np.sqrt(s2 / (1 - b[1] ** 2)), places=12)
            self.assertEqual((got["p_value"], got["reference"], got["n"]), (stats.chi2.sf(got["lr"], 3), "chi2(3)", 874))
        null = np.linspace(0.0, 30.0, 999)
        sim = fe.berkowitz_test(z, null_lr=null)
        self.assertEqual(sim["p_value"], (1 + np.sum(null >= sim["lr"])) / 1000)
        self.assertEqual((sim["reference"], sim["p_chi2"]), ("simulated null", got["p_value"]))
        self.assertEqual(fe.berkowitz_test(z, null_lr=[1e9])["p_value"], 1.0)
        self.assertEqual(fe.berkowitz_test(z, null_lr=[-1.0])["p_value"], 0.5)
        with self.assertRaises(ValueError):                     # takes z, not u: a PIT of 1 is an infinite score
            fe.berkowitz_test(special.ndtri(np.r_[np.full(10, 0.5), 1.0]))

    @staticmethod
    def _censored_stats_form(z, cl, ch):
        """The doubly censored likelihood on scipy.stats (the design's prototype) and its LR."""
        inner = z[(z >= cl) & (z <= ch)]
        below, above = np.sum(z < cl), np.sum(z > ch)

        def ll(theta):
            mu, s = theta[0], np.exp(theta[1])
            return (np.sum(stats.norm.logpdf(inner, mu, s)) + below * stats.norm.logcdf((cl - mu) / s)
                    + above * stats.norm.logsf((ch - mu) / s))
        fit = optimize.minimize(lambda t: -ll(t), np.zeros(2), method="Nelder-Mead",
                                options={"xatol": 1e-10, "fatol": 1e-10, "maxiter": 4000})
        return max(0.0, 2 * (-fit.fun - ll(np.zeros(2)))), ll

    def test_the_censored_form_is_the_scipy_stats_likelihood(self):
        zt = special.ndtri(DIST_TAUS)
        cl, ch = zt[0], zt[-1]
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 10]))
        for loc, scale in ((0.0, 1.0), (0.03, 1.05), (-0.2, 0.8), (0.5, 1.0)):
            x = loc + scale * rng.standard_normal(874)
            z = np.where(x < cl, -np.inf, np.where(x > ch, np.inf, x))
            got = fe.berkowitz_censored(z)
            lr, ll = self._censored_stats_form(z, cl, ch)
            self.assertAlmostEqual(got["lr"], lr, delta=1e-8)
            self.assertAlmostEqual(got["lr"], 2 * (ll([got["mu"], np.log(got["sigma"])]) - ll(np.zeros(2))), delta=1e-8)
            self.assertEqual((got["below"], got["above"], got["n"]), (int(np.sum(x < cl)), int(np.sum(x > ch)), 874))
            self.assertEqual((got["p_value"], got["reference"]), (stats.chi2.sf(got["lr"], 2), "chi2(2)"))
            # Any value outside the interior counts like -inf / +inf.
            self.assertEqual(fe.berkowitz_censored(np.where(np.isinf(z), np.sign(z) * 5.0, z))["lr"], got["lr"])
        self.assertLess(got["p_value"], 1e-6)                         # a shift of half a standard deviation
        with self.assertRaises(ValueError):
            fe.berkowitz_censored(np.r_[z[:10], np.nan])

    def test_the_censored_form_holds_its_size(self):
        # 1,000 samples of 874 weeks for each of two truths (log-normal, log-t3) with exactly right quantiles, the
        # interior interpolated in normal-score space (the design measured 5.0% to 5.2% over 2,000). About 4 s.
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 11]))
        zt = special.ndtri(DIST_TAUS)
        for dist in (stats.norm(), stats.t(3)):
            lq = dist.ppf(DIST_TAUS)
            rejected = 0
            for _ in range(1000):
                x = dist.rvs(size=874, random_state=rng)
                z = np.where(x < lq[0], -np.inf, np.where(x > lq[-1], np.inf, np.interp(x, lq, zt)))
                rejected += fe.berkowitz_censored(z)["p_value"] < 0.05
            self.assertTrue(30 <= rejected <= 70, rejected)

    def test_the_pit_against_a_simulated_sample(self):
        draws = np.array([1.0, 2.0, 3.0, 4.0])                         # Hazen positions 1/8, 3/8, 5/8, 7/8
        self.assertEqual(fe.sample_pit(draws, 2.0), 0.375)
        self.assertEqual(fe.sample_pit(draws, 2.5), 0.5)                 # linear between draws
        self.assertEqual(fe.sample_pit(draws, 3.25), 0.6875)
        for actual, u in ((1.0, 0.125), (-5.0, 0.125), (4.0, 0.875), (9.0, 0.875)):   # clamped at the ends
            self.assertEqual(fe.sample_pit(draws, actual), u)
        for bad in (np.array([2.0, 1.0]), np.array([])):
            with self.assertRaises(ValueError):
                fe.sample_pit(bad, 1.0)
        # Draws at the Hazen quantiles of N(0, 1): the PIT is Phi, exactly at the draws, to interpolation error between.
        n = 2 ** 14
        hazen = special.ndtri((np.arange(n) + 0.5) / n)
        self.assertEqual(fe.sample_pit(hazen, hazen[1234]), 1234.5 / n)
        line = np.linspace(-3, 3, 601)
        u = np.array([fe.sample_pit(hazen, v) for v in line])
        np.testing.assert_allclose(u, stats.norm.cdf(line), rtol=0, atol=1e-6)
        # Continuous and monotone: an input moved by 1e-12 moves u by at most 1e-9.
        moved = np.array([fe.sample_pit(hazen, v + 1e-12) for v in line])
        self.assertTrue(np.all(np.diff(u) > 0) and np.all(moved >= u))
        self.assertLessEqual(float(np.max(moved - u)), 1e-9)

    def test_p_value_combinations(self):
        self.assertAlmostEqual(fe.bonferroni_min([0.02, 0.5, 0.3]), 0.06, places=15)
        self.assertEqual(fe.bonferroni_min([0.3] * 5), 1.0)
        self.assertEqual(fe.bonferroni_min(np.array([0.04])), 0.04)
        for p in (1e-6, 0.04, 0.5, 0.93):
            self.assertAlmostEqual(fe.cauchy_combination([p]) / p, 1.0, places=9)
            self.assertAlmostEqual(fe.cauchy_combination([p] * 5) / p, 1.0, places=9)
        t = (np.tan(0.49 * np.pi) + np.tan(0.0)) / 2                       # p = 0.01 and 0.5
        self.assertAlmostEqual(fe.cauchy_combination([0.01, 0.5]), 0.5 - np.arctan(t) / np.pi, places=12)
        self.assertAlmostEqual(fe.cauchy_combination([0.5, 0.01]), fe.cauchy_combination([0.01, 0.5]), places=15)
        self.assertAlmostEqual(fe.cauchy_combination([1e-20, 0.5]) / 2e-20, 1.0, places=6)   # 1/(p pi) below 1e-16
        self.assertEqual(fe.cauchy_combination([0.0, 0.7]), 0.0)
        for bad in ([], [1.2], [np.nan]):
            with self.assertRaises(ValueError):
                fe.cauchy_combination(bad)
        with self.assertRaises(ValueError):
            fe.bonferroni_min([])


class DistributionDmTest(unittest.TestCase):
    """The Diebold-Mariano rules for the distribution scores: dm_test with a given Bartlett bandwidth (and unchanged
    without one), the Newey-West (1994) bandwidth, and the batch-means test, registered because the score
    differentials stay correlated far longer than 9 lags."""

    @staticmethod
    def _pair():
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 8]))
        c = np.r_[0.0, np.cumsum(rng.standard_normal(1504))]
        a = ((c[5:] - c[:-5]) / np.sqrt(5)) ** 2 + 1.0
        return a, a + 0.05 + 0.3 * rng.standard_normal(1500)

    def test_dm_test_without_lags_is_unchanged(self):
        # Pinned from main's dm_test (30772d6), before the `lags` argument existed.
        a, b = self._pair()
        for horizon, stat, p in ((1, -3.958, 7.896910363502783e-05), (vf.H, -3.948, 8.249579272209455e-05)):
            res = fe.dm_test(a, b, horizon=horizon)
            self.assertEqual((res["stat"], res["lags"]), (stat, 7))
            self.assertAlmostEqual(res["p_value"] / p, 1.0, places=12)
            self.assertAlmostEqual(res["mean_diff"], -0.03211675828258572, places=15)
            self.assertEqual(fe.dm_test(a, b, horizon=horizon, lags=None), res)
            self.assertEqual(fe.dm_test(a, b, horizon=horizon, lags=7), res)     # the automatic bandwidth, given

    def test_dm_test_with_lags_is_a_bartlett_computation(self):
        a, b = self._pair()
        d = a - b
        n, h, lags = d.size, vf.H, 40
        e = d - d.mean()
        lrv = e @ e / n + 2 * sum((1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n for k in range(1, lags + 1))
        stat = d.mean() / np.sqrt(lrv / n) * np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
        res = fe.dm_test(a, b, horizon=h, lags=lags)
        self.assertEqual((res["stat"], res["lags"]), (round(stat, 3), lags))
        self.assertAlmostEqual(res["p_value"], 2 * stats.t.sf(abs(stat), n - 1), delta=1e-12)
        self.assertEqual(fe.dm_test(a, b, horizon=h, lags=5000)["lags"], n - 1)          # capped at n - 1

    def test_the_newey_west_1994_bandwidth(self):
        rng = np.random.default_rng(12)
        d = _ar1(rng, 1000, 0.6)
        n, m = d.size, d.mean()
        pilot = int(4 * (n / 100) ** (2 / 9))                                   # 6 at n = 1,000
        g = [sum((d[t] - m) * (d[t - j] - m) for t in range(j, n)) / n for j in range(pilot + 1)]
        s0, s1 = g[0] + 2 * sum(g[1:]), 2 * sum(j * g[j] for j in range(1, pilot + 1))
        self.assertEqual(fe.nw94_bandwidth(d), int(1.1447 * ((s1 / s0) ** 2 * n) ** (1 / 3)))
        # On a known AR(1) it estimates the population value 1.1447 (alpha n)^(1/3), alpha = (2 rho / (1 - rho^2))^2
        # (22.67 at rho = 0.5 and n = 4,370; the pilot's truncation at 9 lags moves it by 0.1%). One draw's bandwidth
        # varies by about 14%, so the mean of 40 seeded draws is compared (measured 21.2), within 10%.
        from scipy.signal import lfilter

        n, rho = 4370, 0.5
        population = 1.1447 * ((2 * rho / (1 - rho ** 2)) ** 2 * n) ** (1 / 3)
        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 12]))
        bandwidths = [fe.nw94_bandwidth(lfilter([1.0], [1.0, -rho], rng.standard_normal(n + 500))[500:])
                      for _ in range(40)]
        self.assertLess(abs(np.mean(bandwidths) / population - 1), 0.10, np.mean(bandwidths))
        self.assertEqual((fe.nw94_bandwidth(np.ones(50)), fe.nw94_bandwidth([1.0])), (0, 0))

    def test_the_batch_means_test_on_constructed_periods(self):
        means = np.array([0.3, -0.1, 0.2, 0.5, 0.0, 0.4, 0.1, -0.2, 0.6, 0.25, 0.15, 0.05])
        sizes = [101] * 5 + [100] * 7                                           # np.array_split of 1,205 rows
        rng = np.random.default_rng(13)
        noise = [rng.standard_normal(k) for k in sizes]
        d = np.concatenate([mean + (w - w.mean()) for mean, w in zip(means, noise)])     # each period's mean exact
        res = fe.batch_means_test(d, 12)
        np.testing.assert_allclose(res["period_means"], means, rtol=0, atol=1e-14)
        t = means.mean() / (means.std(ddof=1) / np.sqrt(12))
        self.assertAlmostEqual(res["stat"], t, places=10)
        self.assertAlmostEqual(res["p_value"], 2 * stats.t.sf(abs(t), 11), places=12)
        self.assertEqual((res["groups"], res["periods_positive"]), (12, 9))
        flipped = fe.batch_means_test(-d, 12)
        self.assertAlmostEqual(flipped["stat"], -res["stat"], places=12)
        self.assertAlmostEqual(flipped["p_value"], res["p_value"], places=14)
        same = fe.batch_means_test(np.full(30, 0.25), 3)                   # every period mean exactly the same
        self.assertEqual((same["stat"], same["p_value"]), (0.0, 1.0))
        for groups in (1, 1206):
            with self.assertRaises(ValueError):
                fe.batch_means_test(d, groups)

    def test_the_batch_means_test_holds_its_size_where_dm_test_does_not(self):
        # 1,000 draws of the design's GARCH-like null (section 1.6): d = sqrt(1/2) MA(4) + sqrt(1/2) AR(1) with
        # phi = 0.95, mean zero, 4,370 days, whose ACF matches the real CRPS differentials against GARCH-t. The design
        # measured 6.2% for 12 batch means and 29.1% for dm_test's 9 Bartlett lags. About 1 s.
        from scipy.signal import lfilter

        rng = np.random.default_rng(np.random.SeedSequence([20261020, 24, 90, 50, 0]))
        n, w, phi = 4370, 0.5, 0.95
        batch = dm = 0
        for _ in range(1000):
            a = np.convolve(rng.standard_normal(n + 4), np.ones(5), "valid") / np.sqrt(5)
            b = lfilter([1.0], [1.0, -phi], rng.standard_normal(n + 500))[500:] * np.sqrt(1 - phi ** 2)
            d = np.sqrt(1 - w) * a + np.sqrt(w) * b
            batch += fe.batch_means_test(d, 12)["p_value"] < 0.05
            dm += fe.dm_test(d, np.zeros(n), horizon=vf.H)["p_value"] < 0.05
        self.assertTrue(35 <= batch <= 85, batch)
        self.assertGreater(dm, 150)


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

    def test_a_bridge_starts_at_zero_ends_at_its_end_and_has_bridge_increments(self):
        rng = np.random.default_rng(3)
        end = rng.standard_normal(200_000)
        path = lab._bridge(end, np.full(end.size, 2.0), 10, rng)
        np.testing.assert_array_equal(path[:, 0], 0.0)
        np.testing.assert_allclose(path[:, -1], end, atol=1e-12)
        increments = np.diff(path, axis=1) - end[:, None] / 10        # given its end, each step has variance v/n (1 - 1/n)
        self.assertLess(abs(increments.var() / (2.0 / 10 * (1 - 1 / 10)) - 1), 0.02)

    def test_the_overnight_part_is_the_bridge_point_at_the_reopen(self):
        n, var = 200_000, 0.0004
        rng = np.random.default_rng(4)
        z = rng.standard_normal(n) * np.sqrt(var)
        bars = lab.floor_bars(z, np.full(n, var), pd.bdate_range("2000-01-03", periods=n), rng)
        overnight = np.log(bars["Open"].to_numpy())[1:] - np.log(bars["Close"].to_numpy())[:-1]
        slope, intercept = np.polyfit(z[1:], overnight, 1)
        share = lab.RANGE_FLOOR_SETTLE_SHARE
        self.assertLess(abs(slope - share), 0.005)
        residual = overnight - slope * z[1:] - intercept
        self.assertLess(abs(residual.var() / (share * (1 - share) * var) - 1), 0.02)

    def test_a_student_t_day_s_variance_averages_the_sample_s_and_rises_with_its_return(self):
        rng = np.random.default_rng(6)
        sd = 0.02
        t5 = rng.standard_t(5, 400_000) / np.sqrt(5 / 3) * sd
        days = lab.t5_day_variance(t5, sd, np.random.default_rng(7))
        self.assertLess(abs(days.mean() / sd ** 2 - 1), 0.02)
        small, large = np.abs(t5) < np.quantile(np.abs(t5), 0.5), np.abs(t5) > np.quantile(np.abs(t5), 0.9)
        self.assertGreater(days[large].mean(), 2 * days[small].mean())

    def test_qlike_floor_scores_the_noise_floor_s_draws(self):
        r, dates, ovx, ohlc = _synthetic_market()
        d = lab.build_design(r, dates, ovx, ohlc)
        with mock.patch.object(lab, "noise_floor_draws", wraps=lab.noise_floor_draws) as draws:
            block = lab.qlike_floor(d)
        self.assertEqual([call.args[1] for call in draws.call_args_list], list(range(vf.NOISE_FLOOR_SIMS)))
        self.assertTrue(all(call.args[0] is d.r for call in draws.call_args_list))
        self.assertEqual(block["sims"], vf.NOISE_FLOOR_SIMS)

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


class DistributionTest(unittest.TestCase):
    """ROADMAP 2.4 in the lab: the quantile HAR's exact LP, the GARCH family's and EWMA's predictive distributions, the
    scores on a truncated fast walk of the snapshot, and the guard that keeps the quantile HAR out of everything that
    ranks, bands or selects a model."""

    ROWS = 900                                            # the truncated design: ~10 quarterly refits

    @classmethod
    def setUpClass(cls):
        import dataclasses

        r, dates, ovx, ohlc = md.research_inputs(md.load_snapshot())
        cls.design = lab.build_design(r, dates, vf._pick_ovx(ovx), ohlc)
        cls.short = dataclasses.replace(cls.design, ends=[e for e in cls.design.ends if e < cls.ROWS])
        cls.frame = lab.walk_forward(cls.short, include_slow=False)
        cls.qhar = lab.quantile_har(cls.design, rows=cls.ROWS)

    def _check_loss(self, ly, A, beta, tau):
        return float(np.sum(fe.pinball_loss(ly, A @ beta, tau)))

    def test_the_lp_is_the_exact_quantile_regression(self):
        import warnings
        from types import SimpleNamespace

        from statsmodels.regression.quantile_regression import QuantReg

        A = np.column_stack([np.ones(600), np.log(self.design.X[:600])])
        ly = np.log(self.design.y[:600])
        taus = (0.05, 0.5, 0.95)
        beta = lab._quantile_fit(ly, A, taus)
        for tau, b in zip(taus, beta):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                irls = QuantReg(ly, A).fit(q=tau, max_iter=20000).params
            # The LP is the exact optimum: its check loss is never above the iterative fit's, and the two agree.
            self.assertLessEqual(self._check_loss(ly, A, b, tau), self._check_loss(ly, A, irls, tau) * (1 + 1e-12))
            np.testing.assert_allclose(b, irls, rtol=1e-5, atol=1e-5, err_msg=str(tau))
        noisy = ly * (1 + 1e-15 * np.random.default_rng(1).standard_normal(ly.size))
        np.testing.assert_allclose(lab._quantile_fit(noisy, A, taus), beta, rtol=0, atol=1e-12)
        failed = SimpleNamespace(status=2, message="The problem is infeasible.")
        with mock.patch("scipy.optimize.linprog", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "quantile LP failed at tau=0.05"):
                lab._quantile_fit(ly, A, taus)

    def test_the_quantile_har_follows_the_tree_models_schedule_and_never_looks_ahead(self):
        import dataclasses

        self.assertEqual(lab.QHAR_RETRAIN_WEEKS, lab.LGBM_RETRAIN_WEEKS)
        ends = self.short.ends
        period = lab.LGBM_RETRAIN_WEEKS * vf.STEP
        schedule = sorted({max(ends[0], vf.MIN_TRAIN + (e - vf.MIN_TRAIN) // period * period) for e in ends})
        q = self.qhar
        self.assertEqual(q["fit_rows"], schedule)
        self.assertEqual(q["refits"], len(schedule))
        self.assertEqual(q["q"].shape, (len(ends), len(lab.DIST_TAUS)))
        self.assertTrue(np.all(np.diff(q["q"], axis=1) >= 0))
        np.testing.assert_allclose(q["forecast"], q["q"][:, lab.DIST_MEDIAN] * np.sqrt(q["median_smear"]), rtol=1e-15)
        # A crossing is counted on the regression's own quantiles, before they are sorted.
        i = int(np.argmax(q["crossed"])) if q["crossed"].any() else 0
        fit_row = max(row for row in schedule if row <= ends[i])
        A = np.column_stack([np.ones(self.ROWS), np.log(self.design.X[: self.ROWS])])
        beta = lab._quantile_fit(np.log(self.design.y[: fit_row - (vf.H - 1)]), A[: fit_row - (vf.H - 1)], lab.DIST_TAUS)
        raw = beta @ A[ends[i]]
        self.assertEqual(bool(np.any(np.diff(raw) < 0)), bool(q["crossed"][i]))
        np.testing.assert_allclose(q["q"][i], np.exp(np.sort(raw)), rtol=1e-12)
        # Outcomes that have not matured at row k (targets of rows k - 4 onwards) leave every row up to k unchanged.
        rows, k = 400, 330
        base = lab.quantile_har(self.design, rows=rows)
        y = self.design.y.copy()
        y[k - (vf.H - 1):] *= 3.0
        moved = lab.quantile_har(dataclasses.replace(self.design, y=y), rows=rows)
        upto = ends.index(k) + 1
        np.testing.assert_array_equal(moved["q"][:upto], base["q"][:upto])
        self.assertFalse(np.array_equal(moved["q"][upto:], base["q"][upto:]))

    def test_the_garch_simulation_follows_its_variance_path(self):
        from scipy import stats

        U = lab._sobol_shocks()
        np.testing.assert_array_equal(U, lab._sobol_shocks())                      # seeded
        self.assertEqual(U.shape, (2 ** lab.DIST_SIM_LOG2, vf.H))
        nu = 7.0
        z = stats.t.ppf(U, nu) * np.sqrt((nu - 2) / nu)
        mu, omega, alpha, gamma, beta, h1 = 0.03, 0.05, 0.04, 0.08, 0.88, 4.0
        sim, static = lab._garch_rv5(z, (mu, omega, alpha, gamma, beta), h1)
        path = [h1]
        for _ in range(vf.H - 1):
            path.append(omega + (alpha + gamma / 2 + beta) * path[-1])
        expected = (vf.H * mu ** 2 + sum(path)) * (lab.ANNUAL / vf.H) / lab.GARCH_SCALE ** 2
        # E sum r_k^2 = 5 mu^2 + sum_k E h_k for both readings; the static one holds h_k at E h_k.
        self.assertAlmostEqual(float(np.mean(sim ** 2)) / expected, 1.0, delta=0.005)
        self.assertAlmostEqual(float(np.mean(static ** 2)) / expected, 1.0, delta=0.002)
        self.assertTrue(np.all(np.diff(sim) >= 0) and np.all(np.diff(static) >= 0))
        # The asymmetric recursion spreads the distribution beyond the static reading's.
        self.assertGreater(np.quantile(sim, 0.95) - np.quantile(sim, 0.05),
                           np.quantile(static, 0.95) - np.quantile(static, 0.05))

    def test_the_ewma_distribution_is_a_scaled_chi_square_with_a_survival_tail(self):
        frame = pd.DataFrame({"ewma": [0.3, 0.3, 0.3], "actual": [0.3, 1.8, 0.01]})
        pred = lab.ewma_predictive(frame)
        np.testing.assert_allclose(np.round(pred["q"][0, [0, lab.DIST_MEDIAN, -1]] / 0.3, 3), [0.479, 0.933, 1.488])
        self.assertTrue(np.isfinite(pred["z"]).all())
        self.assertGreater(pred["z"][1], 10)                         # a 6-sigma week: the cdf alone reads 1
        self.assertLess(pred["z"][2], -5)

    def test_the_scores_on_a_fast_walk(self):
        frame, n = self.frame, len(self.frame)
        conf = lab.conformal_report(frame, "har_iv_range")
        rep = lab.distribution_scores(frame, self.qhar, conf, "har_iv_range", null_draws=200)
        crps = rep.pop("_crps")
        self.assertIsNone(lab.distribution_mcs(crps))                # no GARCH models without the slow walk
        rows = {m["key"]: m for m in rep["models"]}
        self.assertEqual(list(rows), ["har_iv", "har_iv_range", "ovx_only", "ewma", lab.QHAR_KEY])
        first = lab.WARMUP * vf.STEP + vf.H - 1
        self.assertEqual(rep["n_days"], n - first)
        self.assertEqual(rep["n_days"], conf["har_iv"]["n_days"])           # the band's days
        # HAR-IV's grid CRPS by hand: trailing residual quantiles around the median forecast.
        y, med = frame["actual"].to_numpy(), frame["har_iv__med"].to_numpy()
        e = np.log(y) - np.log(med)
        taus = np.asarray(lab.DIST_TAUS)
        scores = []
        for i in range(first, n):
            q = med[i] * np.exp(np.quantile(e[max(0, i - vf.H + 1 - lab.CONFORMAL_WINDOW): i - vf.H + 1], taus))
            scores.append(2 / taus.size * np.sum((y[i] - q) * (taus - (y[i] < q))))
        self.assertEqual(rows["har_iv"]["crps_grid_pct"], round(float(np.mean(scores)) * 100, 3))
        # The band's interval score splits into its two pinball losses; the band is not these quantiles.
        for key in ("har_iv", "har_iv_range"):
            split = rows[key]["band_split"]
            self.assertAlmostEqual(split["interval_score_pct"], conf[key]["interval_score_pct"], delta=0.011)
            self.assertNotIn("band_split", rows["ovx_only"])
        # At tau = 0.10 and 0.90 the trailing quantiles are delayed_aci's bounds without its adaptive step.
        aci = fe.delayed_aci(e, delay=vf.H, alpha=0.2, gamma=0.0, window=lab.CONFORMAL_WINDOW, warmup=lab.WARMUP * vf.STEP)
        tq = fe.trailing_quantiles(e, (0.1, 0.9), delay=vf.H, window=lab.CONFORMAL_WINDOW, warmup=lab.WARMUP * vf.STEP)
        np.testing.assert_array_equal(tq["q"][first:, 0], aci["lo"][first:])
        np.testing.assert_array_equal(tq["q"][first:, 1], aci["hi"][first:])
        for key, row in rows.items():
            berk = row["berkowitz"]
            self.assertEqual(len(berk["by_grid"]), vf.STEP, key)
            self.assertEqual(berk["p_value"], min(1.0, vf.STEP * min(g["p_value"] for g in berk["by_grid"])), key)
            self.assertAlmostEqual(sum(row["pit_decile_share_pct"]), 100, delta=0.6)
            self.assertEqual(len(row["pit_chance_band_pct"]), 10)
            if key == lab.CHAMPION:
                self.assertIsNone(row["dm_vs_har_iv"])
                continue
            dm = row["dm_vs_har_iv"]["crps_grid"]
            self.assertEqual(len(dm["period_means"]), lab.DIST_DM_GROUPS)
            self.assertEqual(dm["periods_model_lower"] + dm["periods_har_iv_lower"], lab.DIST_DM_GROUPS)
            self.assertEqual(np.sign(dm["stat"]), np.sign(dm["mean_diff"]))
        self.assertEqual({rows[k]["berkowitz"]["reference"] for k in ("har_iv", "har_iv_range", "ovx_only")},
                         {"simulated null"})
        self.assertEqual(rows["ewma"]["berkowitz"]["reference"], "chi2(3)")
        qb = rows[lab.QHAR_KEY]["berkowitz"]
        self.assertEqual((qb["registered"], qb["reference"]), ("censored", "doubly censored, chi2(2)"))
        self.assertEqual(rows[lab.QHAR_KEY]["quantile_har"]["refits"], self.qhar["refits"])
        self.assertEqual(rep["config"]["gaussian_ratio_k19"], 1.048263)

    def test_the_quantile_har_never_enters_the_field(self):
        import ast
        import inspect

        from backend import research_ledger as rl

        # FEASIBLE is also the shadow log's field (its own tests keep the two equal).
        for name, field in (("MODEL_KEYS", lab.MODEL_KEYS), ("FEASIBLE", lab.FEASIBLE), ("LOG_MODELS", lab.LOG_MODELS),
                            ("LAB_COMPARED", rl.LAB_COMPARED)):
            self.assertNotIn(lab.QHAR_KEY, field, name)
        names = {"quantile_har", "QHAR_KEY", "_quantile_fit", "quantile_har_field", "distribution_report",
                 "distribution_scores", "distribution_mcs", "garch_predictive", "empirical_predictive", "ewma_predictive",
                 "_garch_rv5", "GARCH_PARAMS"}
        tree = ast.parse(inspect.getsource(lab))
        readers = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))}
        self.assertEqual(readers, {"build_report", "_garch_columns", "garch_predictive", "distribution_scores",
                                   "distribution_report", "quantile_har_field", "field", "member", "quantile_har"})
        for function in (lab.choose_challenger, lab.oos_selection, lab.conformal_report, lab.leaderboard, lab._losses,
                         lab.stability_report, lab.regime_report, lab.var_backtest, lab.chart_series):
            source = inspect.getsource(function)
            self.assertFalse([name for name in names if name in source], function.__name__)
        # And on a frame: a quantile_har column changes neither the challenger, the selection nor the band. The fast
        # walk stands in for the slow models with distinct deterministic copies (identical ones break arch's MCS).
        frame = self.frame.copy()
        for key, source, scale in (("garch", "ewma", 1.03), ("gjr", "ewma", 0.97), ("lgbm", "har_rv", 1.02)):
            frame[key] = frame[f"{key}__med"] = frame[source] * scale
        frame["combo"] = np.sqrt(np.mean([frame[m] ** 2 for m in lab.COMBO_MEMBERS], axis=0))
        frame["combo__med"] = np.exp(np.mean([np.log(frame[f"{m}__med"]) for m in lab.COMBO_MEMBERS], axis=0))
        added = frame.copy()
        added[lab.QHAR_KEY] = added[f"{lab.QHAR_KEY}__med"] = self.qhar["forecast"]
        self.assertEqual(lab.choose_challenger(added), lab.choose_challenger(frame))
        self.assertEqual(json.dumps(lab._jsonable(lab.oos_selection(added))),
                         json.dumps(lab._jsonable(lab.oos_selection(frame))))
        strip = lambda conf: {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in conf.items()}  # noqa: E731
        self.assertEqual(strip(lab.conformal_report(added, "har_iv_range")),
                         strip(lab.conformal_report(frame, "har_iv_range")))

    def test_a_berkowitz_p_from_the_simulated_null_may_move_a_few_draws(self):
        step = 1 / (lab.DIST_NULL_DRAWS + 1)
        block = {"distribution": {"models": [
            {"pit_construction": "trailing_rank", "berkowitz": {"p_value": 0.0925, "p_cauchy": 0.05, "mc_se": 0.0067,
                                                                "p_chi2": 0.356, "by_grid": [{"p_value": 0.0185}]}},
            {"pit_construction": "exact", "berkowitz": {"p_value": 0.0023, "by_grid": [{"p_value": 0.0015}]}}]}}
        moved = json.loads(json.dumps(block))
        trailing, exact = (m["berkowitz"] for m in moved["distribution"]["models"])
        trailing["by_grid"][0]["p_value"] += 9 * step
        trailing["p_value"] += 45 * step
        trailing["p_cauchy"] += 30 * step
        self.assertEqual(lab.compare_reports(block, moved)["violations"], [])
        trailing["by_grid"][0]["p_value"] += 3 * step                      # beyond the allowance
        self.assertEqual(len(lab.compare_reports(block, moved)["violations"]), 1)
        trailing["by_grid"][0]["p_value"] -= 3 * step
        trailing["p_chi2"] += 0.002                                        # chi2: not a count of draws
        exact["by_grid"][0]["p_value"] += 0.0005                           # a parametric PIT's p: not either
        self.assertEqual(len(lab.compare_reports(block, moved)["violations"]), 2)


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
        import ast
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
        for key in ("leaderboard", "oos_selection", "conformal", "var_backtest", "yearly", "regimes"):
            self.assertNotIn("floor", json.dumps(self.report[key]), key)
        names = {"qlike_floor", "normal_qlike_floor", "t_qlike_floor", "_floor_scores", "floor_bars",
                 "noise_floor_draws", "t5_day_variance"}
        for module, readers in ((lab, {"qlike_floor", "t_qlike_floor", "build_report"}), (fe, set()), (rl, set())):
            tree = ast.parse(inspect.getsource(module))
            found = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and any(isinstance(n, (ast.Name, ast.Attribute)) and (getattr(n, "id", None) in names
                             or getattr(n, "attr", None) in names) for n in ast.walk(node))}
            self.assertEqual(found, readers, module.__name__)
        self.assertNotIn("qlike_floor", inspect.getsource(rl))
        # Plausible magnitudes: the range values sit below the five-return floor; the bars' level is stated.
        rng = block["range"]
        self.assertLess(rng["normal"]["simulated_mean"], rng["student_t5"]["simulated_mean"])
        self.assertLess(rng["student_t5"]["simulated_mean"], five["normal"]["closed_form"])
        for name in ("normal", "student_t5"):
            self.assertTrue(1 < rng[name]["proxy_to_true_variance"] < 1.3)
        self.assertTrue(0.8 < rng["data_proxy_to_close_variance"] < 1.3)

    def test_regime_blocks_add_up(self):
        # ROADMAP 2.2's acceptance: counts sum to the days they describe, and the cells reproduce the pooled values.
        regimes, board, conf = self.report["regimes"], self.report["leaderboard"], self.report["conformal"]
        n_days = self.report["sample"]["n_days"]
        self.assertEqual(n_days, board["n_days"])
        for var, levels in lab.REGIMES.items():
            self.assertEqual(regimes["variables"][var]["levels"], list(levels))
            self.assertEqual(sum(regimes["n_days_by_regime"][var].values()), n_days, var)
            for key, c in conf.items():
                cc = c["conditional_coverage"][var]
                self.assertEqual(sum(c["n_days_by_regime"][var].values()), c["n_days"], (key, var))
                self.assertEqual(sum(cell["n_days"] for cell in cc["cells"]), c["n_days"], (key, var))
                self.assertEqual(sum(sum(cell["n_by_grid"]) for cell in cc["cells"]), c["n_days"], (key, var))
                self.assertEqual(cc["df"], len(levels) - 1)
                self.assertTrue(0 <= cc["p_value"] <= 1)
                weights = c["n_days_by_regime"][var]
                pooled = sum(c["coverage_by_regime_pct"][var][lv] * weights[lv] for lv in levels) / c["n_days"]
                self.assertAlmostEqual(pooled, c["coverage_pct"], delta=0.1)
                score = sum(c["interval_score_by_regime"][var][lv] * weights[lv] for lv in levels) / c["n_days"]
                self.assertAlmostEqual(score, c["interval_score_pct"], delta=0.02)
            for model in board["models"]:
                by = model["qlike_by_regime"][var]
                pooled = sum(by[lv] * regimes["n_days_by_regime"][var][lv] for lv in levels) / n_days
                self.assertAlmostEqual(pooled, model["qlike"], delta=1e-3, msg=(model["key"], var))
        weeks = len(self.report["series"]["date"])
        self.assertEqual(len(self.report["series"]["ovx_rv_gap"]), weeks)
        self.assertEqual(len(self.report["series"]["ovx_rv_gap_median"]), weeks)

    def test_regime_blocks_are_current(self):
        snapshot = md.load_snapshot()
        r, dates, ovx, ohlc = md.research_inputs(snapshot)
        ovx = vf._pick_ovx(ovx)
        design = lab.build_design(r, dates, ovx, ohlc)
        frame = lab.walk_forward(design, include_slow=False)
        conf = lab.conformal_report(frame, self.report["config"]["challenger"])
        regimes = lab.regime_labels(r, dates, ovx, design.pos[np.asarray(design.ends)])
        rebuilt = json.loads(json.dumps(lab._jsonable(lab.regime_report(frame, conf, regimes))))
        committed = {"regimes": self.report["regimes"],
                     "conformal": {key: {k: c[k] for k in rebuilt["conformal"][key]}
                                   for key, c in self.report["conformal"].items()},
                     "qlike": {m["key"]: m["qlike_by_regime"] for m in self.report["leaderboard"]["models"]
                               if m["key"] in rebuilt["qlike"]}}
        diff = lab.compare_reports(committed, rebuilt)
        self.assertEqual(diff["violations"], [], diff)
        series = lab.chart_series(frame, conf, self.report["config"]["challenger"], regimes)
        for key in ("ovx_rv_gap", "ovx_rv_gap_median"):
            self.assertEqual(series[key], self.report["series"][key], key)

    def test_the_band_never_reads_a_regime(self):
        import ast
        import inspect

        from backend import research_ledger as rl

        names = {"regime_inputs", "regime_labels", "regime_report", "REGIMES", "_coverage_by_regime"}
        tree = ast.parse(inspect.getsource(lab))
        readers = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))}
        self.assertEqual(readers, {"regime_labels", "regime_report", "_coverage_by_regime", "build_report"})
        for function in (lab.conformal_report, lab.choose_challenger, fe.delayed_aci, fe.adaptive_conformal):
            source = inspect.getsource(function)
            self.assertFalse([name for name in names if name in source], function.__name__)
        imported = {alias.name for node in ast.walk(ast.parse(inspect.getsource(rl)))
                    if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
        self.assertFalse({"vol_lab", "backend.vol_lab"} & imported)        # the ledger reads the artifact only

    def test_stability_blocks_are_consistent(self):
        from scipy import stats

        stab = self.report["stability"]
        fluct = stab["fluctuation"]
        c5 = fluct["critical_values"]["five_pct"]
        weeks = len(self.report["series"]["date"])
        n_days = self.report["sample"]["n_days"]
        self.assertEqual((fluct["mu"], fluct["window_sessions"], fluct["n_days"]), (lab.FLUCT_MU, round(0.2 * n_days), n_days))
        self.assertEqual(fluct["null"]["seed"], [lab.SEED, lab.FLUCT_SEED_TAG, n_days])
        for model in self.report["leaderboard"]["models"]:
            key = model["key"]
            gw, fl = model["gw_qlike_vs_har_iv"], model["fluctuation_qlike_vs_har_iv"]
            if key == lab.CHAMPION:
                self.assertIsNone(gw)
                self.assertIsNone(fl)
                continue
            with self.subTest(key):
                self.assertAlmostEqual(gw["p_value"], stats.chi2.sf(gw["stat"], gw["df"]), delta=2e-3 * max(gw["p_value"], 1e-3))
                c = gw["conditional"]
                self.assertAlmostEqual(c["p_value"], stats.chi2.sf(c["stat"], c["df"]), delta=2e-3 * max(c["p_value"], 1e-3))
                self.assertEqual((gw["df"], c["df"], gw["n_days"]), (4, 3, stab["gw"]["n_days"]))
                self.assertEqual(fl["rejects_5pct"], fl["p_value"] < 0.05)
                self.assertEqual(fl["rejects_10pct"], fl["p_value"] < 0.10)
                if abs(fl["sup"] - c5) > 1e-3:
                    self.assertEqual(fl["rejects_5pct"], fl["sup"] > c5)
                marginals = [fl["p_value"]] + [v["p_value"] for v in fl["by_mu"].values()]
                self.assertEqual(fl["search_adjusted"]["min_p"], min(marginals))
                self.assertGreaterEqual(fl["search_adjusted"]["p_value"], fl["search_adjusted"]["min_p"])
                self.assertTrue(fluct["first_window_end"] <= fl["max_at"] <= self.report["sample"]["last"])
                self.assertTrue(fluct["first_window_end"] <= fl["min_at"] <= self.report["sample"]["last"])
                self.assertEqual(fl["crosses_both_sides"], fl["max"] > c5 and fl["min"] < -c5)
                if fl["outside_5pct"]:
                    self.assertTrue(fl["rejects_5pct"])
                dm = model["dm_qlike_vs_har_iv"]
                if dm["p_value"] < 0.05 and fl["rejects_5pct"]:
                    self.assertEqual(fl["sign"], int(np.sign(dm["mean_diff"])))
        for name in stab["series"]["fluct_t"]:
            values = self.report["series"][f"fluct_t_{name}"]
            self.assertEqual(len(values), weeks)
            first = next(i for i, v in enumerate(values) if v is not None)
            self.assertTrue(all(v is not None for v in values[first:]))
            self.assertGreaterEqual(self.report["series"]["date"][first], fluct["first_window_end"])
        drawn = {"challenger": self.report["config"]["challenger"], "ovx_only": "ovx_only", "raw_ovx": "raw_ovx"}
        for name, key in drawn.items():
            model = next(m for m in self.report["leaderboard"]["models"] if m["key"] == key)
            values = self.report["series"][f"fluct_t_{name}"]
            for run in model["fluctuation_qlike_vs_har_iv"]["outside_5pct"]:
                self.assertTrue(0 <= run["week_from"] <= run["week_to"] < weeks, run)
                self.assertTrue(all(values[i] is not None for i in range(run["week_from"], run["week_to"] + 1)))
        for name in stab["series"]["cum_qlike_diff"]:
            self.assertEqual(len(self.report["series"][f"cum_qlike_diff_{name}"]), weeks)

    def test_stability_blocks_are_current(self):
        fast = _fast_lab()
        frame, challenger = fast["frame"], self.report["config"]["challenger"]
        with mock.patch.object(lab, "fluctuation_null", _cached_null):
            rebuilt = json.loads(json.dumps(lab._jsonable(lab.stability_report(
                frame, fast["regimes"]["gap"], fast["pos"], challenger))))
        committed = {m["key"]: m for m in self.report["leaderboard"]["models"]}
        for key, block in rebuilt["gw"].items():
            diff = lab.compare_reports(committed[key]["gw_qlike_vs_har_iv"], block)
            self.assertEqual(diff["violations"], [], (key, diff))
        for key, block in rebuilt["fluctuation"].items():
            diff = lab.compare_reports(committed[key]["fluctuation_qlike_vs_har_iv"], block)
            self.assertEqual(diff["violations"], [], (key, diff))
        diff = lab.compare_reports(self.report["stability"], rebuilt["stability"])
        self.assertEqual(diff["violations"], [], diff)
        for key, values in rebuilt["series"].items():
            self.assertEqual(values, self.report["series"][key], key)
        # The every-day cumulative total is n times DM's mean difference, to floating-point rounding.
        n_days = self.report["sample"]["n_days"]
        for name, total in self.report["stability"]["cumulative"]["end_all_days"].items():
            key = challenger if name == "challenger" else name
            self.assertLess(abs(total - n_days * committed[key]["dm_qlike_vs_har_iv"]["mean_diff"]), 1e-9, name)

    def test_the_distribution_block_is_consistent(self):
        # ROADMAP 2.4: the scores cover the band's days, the quantile HAR stays out of the field, and every registered
        # number agrees with the parts it is built from.
        dist, conf, board = self.report["distribution"], self.report["conformal"], self.report["leaderboard"]
        champion, challenger = self.report["config"]["champion"], self.report["config"]["challenger"]
        self.assertEqual(dist["n_days"], conf[champion]["n_days"])
        self.assertEqual(sum(dist["weeks_per_grid"]), dist["n_days"])
        keys = [m["key"] for m in dist["models"]]
        self.assertEqual(keys, [champion, challenger, "ovx_only", "garch", "gjr", "ewma", lab.QHAR_KEY])
        self.assertEqual(dist["config"]["taus"], list(lab.DIST_TAUS))
        for where in (board["models"], board["spa_qlike_vs_har_iv"]["models"], board["mcs_qlike_included"],
                      board["mcs_robustness_qlike"], board["mcs_qlike_by_grid"], self.report["oos_selection"]):
            self.assertNotIn(lab.QHAR_KEY, json.dumps(where))
        for m in dist["models"]:
            with self.subTest(m["key"]):
                self.assertTrue(m["pinball_10_pct"] < m["crps_grid_pct"] and m["pinball_90_pct"] < m["crps_grid_pct"])
                self.assertAlmostEqual(sum(m["pit_decile_share_pct"]), 100, delta=0.6)
                berk = m["berkowitz"]
                grid_p = [g["p_value"] for g in berk["by_grid"]]
                self.assertEqual(berk["p_value"], min(1.0, vf.STEP * min(grid_p)))
                self.assertEqual(berk["grids_below_5pct"], sum(p < 0.05 for p in grid_p))
                self.assertEqual([g["n_weeks"] for g in berk["by_grid"]], dist["weeks_per_grid"])
                if m["key"] in conf:
                    self.assertAlmostEqual(m["band_split"]["interval_score_pct"], conf[m["key"]]["interval_score_pct"],
                                           delta=0.011)
                if m["key"] == champion:
                    self.assertIsNone(m["dm_vs_har_iv"])
                    continue
                for score in ("crps_grid", "pinball_10", "pinball_90"):
                    dm = m["dm_vs_har_iv"][score]
                    self.assertEqual(np.sign(dm["stat"]), np.sign(dm["mean_diff"]))
                    self.assertEqual(len(dm["period_means"]), lab.DIST_DM_GROUPS)
                    self.assertEqual(dm["periods_model_lower"], sum(v > 0 for v in dm["period_means"]))
        size = dist["config"]["berkowitz"]["null_size_pct"]
        self.assertTrue(2 <= size["simulated"] <= 8 and size["chi2"] < 2, size)
        field = dist["quantile_har_field"]
        self.assertEqual(field["n_days"], self.report["sample"]["n_days"])
        # Same seed and block: adding a model that is not the best leaves the statistic and every other draw unchanged
        # and can only raise each bootstrap maximum, so the p-value cannot fall.
        if field["spa"]["best_model"] != lab.QHAR_KEY:
            self.assertEqual(field["spa"]["best_model"], board["spa_qlike_vs_har_iv"]["best_model"])
            self.assertGreaterEqual(field["spa"]["p_consistent"], board["spa_qlike_vs_har_iv"]["p_consistent"])
        self.assertIn(lab.QHAR_KEY, field["spa"]["models"])

    def test_nothing_that_ranks_or_bands_a_model_reads_the_stability_tests(self):
        import ast
        import inspect

        names = {"stability_report", "gw_test", "gw_instruments", "rolling_dm", "fluctuation_null", "fluctuation_test",
                 "fluctuation_reference", "_gw_block", "hac_lrv", "critical_value_mc_se"}
        tree = ast.parse(inspect.getsource(lab))
        readers = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))}
        self.assertEqual(readers, {"stability_report", "_gw_block", "build_report"})
        for function in (lab.choose_challenger, lab.oos_selection, lab.conformal_report, lab.leaderboard):
            source = inspect.getsource(function)
            self.assertFalse([name for name in names if name in source], function.__name__)

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

    def test_a_simulated_p_value_may_move_a_few_draws_but_its_flag_may_not(self):
        step = 1 / (lab.FLUCT_NULL_DRAWS + 1)
        block = {"leaderboard": {"models": [{"fluctuation_qlike_vs_har_iv": {
            "p_value": 0.04578, "rejects_5pct": True, "search_adjusted": {"p_value": 0.0702, "min_p": 0.0264},
            "by_mu": {"mu_0_4": {"p_value": 0.04578}}, "robustness": {"by_grid": {"p_value": [0.0114, 0.03]}}},
            "dm_qlike_vs_har_iv": {"p_value": 0.04578}}]}}
        moved = json.loads(json.dumps(block))
        fl = moved["leaderboard"]["models"][0]["fluctuation_qlike_vs_har_iv"]
        fl["p_value"] -= step
        fl["by_mu"]["mu_0_4"]["p_value"] -= step
        fl["search_adjusted"]["p_value"] += 6 * step
        fl["search_adjusted"]["min_p"] += step
        fl["robustness"]["by_grid"]["p_value"][0] += 2 * step
        self.assertEqual(lab.compare_reports(block, moved)["violations"], [])
        fl["search_adjusted"]["p_value"] += 20 * step                       # beyond the allowance
        self.assertEqual(len(lab.compare_reports(block, moved)["violations"]), 1)
        fl["search_adjusted"]["p_value"] -= 20 * step
        fl["rejects_5pct"] = False                                          # a conclusion is never noise
        self.assertEqual(len(lab.compare_reports(block, moved)["violations"]), 1)
        fl["rejects_5pct"] = True
        moved["leaderboard"]["models"][0]["dm_qlike_vs_har_iv"]["p_value"] -= step   # not a simulated p
        self.assertEqual(len(lab.compare_reports(block, moved)["violations"]), 1)

    def test_reproduction_rule_separates_noise_from_a_changed_result(self):
        committed = {"sets": ["a", "b"], "p": 0.0028, "acc": 66.1, "raw": 0.20370903366594823, "n": 4}
        noise = {**committed, "p": 0.0029, "raw": committed["raw"] * (1 + 2e-5)}      # last bits, last place
        self.assertEqual(lab.compare_reports(committed, noise)["violations"], [])
        self.assertFalse(lab.compare_reports(committed, noise)["identical"])
        for key, value in (("sets", ["a"]), ("p", 0.0030), ("acc", 66.3), ("raw", 0.2038), ("n", 5)):
            with self.subTest(key=key):
                self.assertTrue(lab.compare_reports(committed, {**committed, key: value})["violations"])
        self.assertTrue(lab.compare_reports(committed, {**committed, "extra": 1})["violations"])

    @unittest.skipUnless(os.environ.get("RUN_SLOW") == "1", "set RUN_SLOW=1 to rebuild the artifact (~7 min)")
    def test_artifact_reproduces_from_the_snapshot(self):
        rebuilt = json.loads(json.dumps(lab._jsonable(lab.build_report(md.load_snapshot()))))
        if os.environ.get("VOL_LAB_REBUILT"):                                # kept for the CI job summary
            Path(os.environ["VOL_LAB_REBUILT"]).write_text(json.dumps(rebuilt), encoding="utf-8")
        committed = json.loads((ROOT / "data" / "vol_lab.json").read_text())
        self.assertEqual(lab.compare_reports(committed, rebuilt)["violations"], [])


if __name__ == "__main__":
    unittest.main()
