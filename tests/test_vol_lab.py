"""Network-free tests for the volatility research lab, its statistics and its pinned data snapshot.

The look-ahead test is the centerpiece, as in test_vol_forecast.py: every benchmark (including the
GARCH refits, the tree model and the combination) must give the same forecast for a test week no
matter what happens after that week's test day. The statistical tests are checked against cases
whose answer is known, and the committed artifact is checked against its own contract (and, with
RUN_SLOW=1, rebuilt from the committed snapshot and compared with vol_lab.compare_reports).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

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


class NoLookAheadTest(unittest.TestCase):
    """A forecast for week k may depend only on data up to and including its test day."""

    def test_every_model_ignores_the_future(self):
        r, dates, ovx, ohlc = _synthetic_market()
        design = lab.build_design(r, dates, ovx, ohlc)
        self.assertGreater(len(design.ends), 40)
        base = lab.walk_forward(design)
        k = len(design.ends) // 2
        t = int(design.pos[design.ends[k]])
        rng = np.random.default_rng(99)
        r2, ovx2, ohlc2 = r.copy(), ovx.copy(), ohlc.copy()
        r2[t + 1:] = rng.normal(0, 0.05, r2.size - t - 1)          # a completely different future
        ovx2[t + 1:] = ovx2[t + 1:] * 3.0
        ohlc2.iloc[t + 1:, :] = ohlc2.iloc[t + 1:, :].to_numpy() * 1.7
        d2 = lab.build_design(r2, dates, ovx2, ohlc2)
        moved = lab.walk_forward(d2)
        # Every forecast up to and including week k is unchanged; the TARGET for week k may change.
        for key in lab.MODEL_KEYS:
            np.testing.assert_allclose(base[key].iloc[: k + 1].to_numpy(), moved[key].iloc[: k + 1].to_numpy(),
                                       rtol=1e-9, err_msg=key)
        self.assertFalse(np.allclose(base["actual"].iloc[k + 1:], moved["actual"].iloc[k + 1:]))

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

    def test_live_model_forecast_matches_a_fit_on_all_rows(self):
        r, dates, ovx, ohlc = _synthetic_market()
        out = lab.live_model_forecast("har_iv", r, dates, ovx, ohlc)
        design = lab.build_design(r, dates, ovx, ohlc)
        beta, smear = lab._fit_log(design.X, design.y)
        t = len(r) - 1
        x = np.array([vf._rvol(r[t - 4: t + 1]), vf._rvol(r[t - 21: t + 1]), vf._rvol(r[t - 65: t + 1]), ovx[t - 1]])
        med, mean = lab._median_mean(beta, smear, x)
        self.assertAlmostEqual(out["forecast_next_week_vol_annualized_pct"], round(mean * 100, 1))
        self.assertAlmostEqual(out["forecast_median_pct"], round(med * 100, 1))
        combo = lab.live_model_forecast("combo", r, dates, ovx, ohlc)
        self.assertGreater(combo["forecast_next_week_vol_annualized_pct"], 0)


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
        res = fe.model_confidence_set(losses, reps=500)
        self.assertIn("good", res["included"])
        self.assertNotIn("bad", res["included"])

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

    def test_mcs_membership_matches_p_values(self):
        board = self.report["leaderboard"]
        for row in board["models"]:
            self.assertEqual(row["in_mcs_qlike"], row["mcs_qlike_p"] >= lab.MCS_SIZE, row["key"])
        self.assertEqual({r["key"] for r in board["models"]}, set(lab.MODEL_KEYS))

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

    @unittest.skipUnless(os.environ.get("RUN_SLOW") == "1", "set RUN_SLOW=1 to rebuild the artifact (~3 min)")
    def test_artifact_reproduces_from_the_snapshot(self):
        rebuilt = json.loads(json.dumps(lab._jsonable(lab.build_report(md.load_snapshot()))))
        if os.environ.get("VOL_LAB_REBUILT"):                                # kept for the CI job summary
            Path(os.environ["VOL_LAB_REBUILT"]).write_text(json.dumps(rebuilt), encoding="utf-8")
        committed = json.loads((ROOT / "data" / "vol_lab.json").read_text())
        self.assertEqual(lab.compare_reports(committed, rebuilt)["violations"], [])


if __name__ == "__main__":
    unittest.main()
