"""Backtest engine inputs, outputs and P&L statistics: cross-asset context must cover the whole
window, '--features all' must really load macro, drawdown counts from the starting equity, and the
report records its purge and never lands in the dashboard's artifact unless it is the validated
configuration. Network-free."""

import contextlib
import io
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from backend import backtest_walk_forward as bwf
from backend import safe_paths
from backend.backtest_walk_forward import compute_pnl_metrics, prepare_daily_dataset
from backend.oil import PremiumWTIPredictor

CONTEXT_OFFSETS = {"BZ=F": 2.0, "DX-Y.NYB": 0.2, "^VIX": 20.0, "^OVX": 30.0, "^TNX": 4.0,
                   "XLE": 10.0, "XOP": 8.0, "SPY": 15.0}
BARS = 120
RECENT_BARS = 40   # what a fixed look-back fetch ('3y' in production) covers of this window


def wti_bars(bars=BARS):
    index = pd.bdate_range("2016-06-20", periods=bars).tz_localize("America/New_York")
    close = pd.Series([70 + 0.2 * i + 4 * math.sin(i / 6.0) for i in range(bars)], index=index)
    return pd.DataFrame({"Open": close - 0.5, "High": close + 1.5, "Low": close - 1.5, "Close": close,
                         "Volume": [1000.0 + 9 * i for i in range(bars)]}, index=index)


def yahoo_like_fetcher(index, starts=None):
    """Context closes over the whole index, but like Yahoo a fixed look-back period ('3y', '2y', ...)
    returns only the most recent RECENT_BARS. `starts` delays a symbol's first print to a bar."""
    calls = []

    def fetch(symbol, period="2y", interval="1d"):
        calls.append((symbol, period))
        offset = CONTEXT_OFFSETS[symbol]
        series = pd.Series([50 + offset + 0.1 * i + 2 * math.sin(i / 4.0) for i in range(len(index))], index=index)
        series = series.iloc[(starts or {}).get(symbol, 0):]
        return series if period == "max" else series.iloc[-RECENT_BARS:]

    return fetch, calls


def backtest_predictor(fetch):
    predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
    predictor.market_context_period = "3y"           # the live model's look-back, counted from today
    predictor.daily_feature_lookback_bars = 40
    predictor.daily_target_mode = "return"
    predictor.use_historical_external_features_in_training = False   # the env default
    predictor._fetch_market_series = fetch
    return predictor


class ContextCoversTheWindowTest(unittest.TestCase):
    def test_context_is_real_from_the_first_row_of_a_window_longer_than_the_live_lookback(self):
        wti = wti_bars()
        fetch, calls = yahoo_like_fetcher(wti.index)
        predictor = backtest_predictor(fetch)
        dataset, _ = prepare_daily_dataset(predictor, wti, "1w", feature_mode="no_macro", lag_context_days=1)

        self.assertGreater(len(dataset), RECENT_BARS)   # the window is longer than the live fetch
        self.assertEqual({period for _, period in calls}, {"max"})
        # Row 0 is bar 39 and, lagged one day, carries bar 38's VIX close, not the 20.0 fill value.
        expected_vix = 50 + CONTEXT_OFFSETS["^VIX"] + 0.1 * 38 + 2 * math.sin(38 / 4.0)
        self.assertAlmostEqual(dataset["vix_level"].iloc[0], expected_vix, places=9)
        self.assertNotEqual(dataset["dxy_level"].iloc[0], 100.0)
        early = dataset.iloc[: len(dataset) - RECENT_BARS]
        for column in ("vix_level", "dxy_level", "brent_wti_spread", "tnx_level", "xle_return_5d"):
            self.assertGreater(early[column].nunique(), 1, msg=f"{column} is constant before the live look-back")
        self.assertEqual(dataset.attrs["rows_without_context"], 0)

        # The live predictor is left as it was.
        self.assertEqual(predictor.market_context_period, "3y")
        self.assertIs(predictor._fetch_market_series, fetch)

    def test_rows_before_a_symbols_first_print_are_counted_as_without_context(self):
        wti = wti_bars()
        fetch, _ = yahoo_like_fetcher(wti.index, starts={"^OVX": 70})
        dataset, _ = prepare_daily_dataset(backtest_predictor(fetch), wti, "1w", feature_mode="no_macro",
                                           lag_context_days=1)
        # Context dates are bars 38, 39, ...: those before bar 70 (OVX's first close) lack OVX.
        self.assertEqual(dataset.attrs["rows_without_context"], 70 - 38)

    def test_price_only_fetches_no_context(self):
        wti = wti_bars()
        fetch, calls = yahoo_like_fetcher(wti.index)
        dataset, feature_cols = prepare_daily_dataset(backtest_predictor(fetch), wti, "1w", feature_mode="price_only")
        self.assertEqual(calls, [])
        self.assertNotIn("vix_level", feature_cols)
        self.assertEqual(dataset.attrs["rows_without_context"], 0)


class MacroFeatureModeTest(unittest.TestCase):
    def _predictor(self, wti):
        fetch, _ = yahoo_like_fetcher(wti.index)
        predictor = backtest_predictor(fetch)
        predictor._historical_external_mem_cache = {}
        predictor.eia_release_lag_days = 5
        self.macro_calls = []
        weeks = pd.date_range("2016-01-01", periods=60, freq="W-FRI")
        months = pd.date_range("2014-06-01", periods=40, freq="MS")
        days = pd.bdate_range("2015-01-01", "2017-06-30")

        def eia():
            self.macro_calls.append("EIA")
            return pd.Series([400.0 + 3 * math.sin(i) for i in range(len(weeks))], index=weeks)

        def fred(series_id, start_date=None, end_date=None):
            self.macro_calls.append(series_id)
            if series_id == "T10Y2Y":
                return pd.Series([0.5 + 0.01 * i for i in range(len(days))], index=days)
            return pd.Series([100.0 + i + (i % 3) for i in range(len(months))], index=months)

        predictor._fetch_eia_weekly_stocks_series = eia
        predictor._fetch_fred_csv_series = fred
        return predictor

    def test_all_loads_macro_even_with_the_predictor_flag_off(self):
        wti = wti_bars()
        predictor = self._predictor(wti)
        dataset, feature_cols = prepare_daily_dataset(predictor, wti, "1w", feature_mode="all", lag_context_days=1)

        macro_cols = [c for c in feature_cols if c.startswith("hist_")]
        self.assertEqual(len(macro_cols), 14)
        for column in ("hist_eia_stocks_level", "hist_fred_curve_slope", "hist_fred_indpro_yoy"):
            self.assertGreater(dataset[column].nunique(), 1, msg=f"{column} is a constant default")
        self.assertIn("EIA", self.macro_calls)
        self.assertFalse(predictor.use_historical_external_features_in_training)   # restored

    def test_no_macro_neither_fetches_nor_includes_macro(self):
        wti = wti_bars()
        predictor = self._predictor(wti)
        _, feature_cols = prepare_daily_dataset(predictor, wti, "1w", feature_mode="no_macro", lag_context_days=1)
        self.assertFalse([c for c in feature_cols if c.startswith("hist_")])
        self.assertEqual(self.macro_calls, [])


class MaxDrawdownTest(unittest.TestCase):
    def test_losses_from_the_first_trade_count_from_zero_equity(self):
        # The opening trades of the committed unpurged 1d run: equity 0 -> -19,430 is the drawdown.
        self.assertEqual(compute_pnl_metrics([-2050.0, -9030.0, -8350.0], 50.4)["max_drawdown_usd"], 19430.0)
        self.assertEqual(compute_pnl_metrics([-2050.0, -9030.0, -8350.0, 25000.0], 50.4)["max_drawdown_usd"], 19430.0)

    def test_drawdown_after_a_peak_and_a_curve_that_only_rises(self):
        self.assertEqual(compute_pnl_metrics([1000.0, -3000.0, 500.0], 50.4)["max_drawdown_usd"], 3000.0)
        self.assertEqual(compute_pnl_metrics([100.0, 200.0], 50.4)["max_drawdown_usd"], 0.0)
        self.assertTrue(np.isfinite(compute_pnl_metrics([-5.0], 50.4)["max_drawdown_usd"]))


class DirectionSignificanceTest(unittest.TestCase):
    def test_worse_than_a_coin_flip_is_significant_two_sided(self):
        # The 5-year purged 1d report: 74 of 199 right. One-sided ("better than 50%?") that is
        # p = 0.9999, which read as merely "not significant"; two-sided it is p = 0.0004.
        refs = [100.0] * 199
        actual = [101.0] * 199
        predicted = [101.0] * 74 + [99.0] * 125
        metrics = bwf.compute_metrics(actual, predicted, refs)
        self.assertAlmostEqual(metrics["direction_accuracy"], 37.19, places=2)
        self.assertGreater(metrics["direction_p_value"], 0.99)
        self.assertLess(metrics["direction_p_value_two_sided"], 0.001)
        self.assertFalse(metrics["is_significant_5pct"])            # significance means skill


class StubPredictor:
    """Just enough of PremiumWTIPredictor for main(): history in, no models out."""

    model_n_estimators = 40
    model_cpu_workers = 1
    daily_target_mode = "return"
    history_requests = []

    def get_wti_historical_data(self, period=None, interval="1d"):
        StubPredictor.history_requests.append(period)
        return wti_bars(80)

    def train_prediction_models(self, train_input, target_col, target_mode="price"):
        return {}, {}, None, None, [], [], {}


def stub_dataset(predictor, wti_data, horizon, feature_mode="all", lag_context_days=0):
    rows = 60
    dataset = pd.DataFrame({
        "timestamp": [str(ts) for ts in pd.bdate_range("2026-01-05", periods=rows)],
        "feature": [float(i) for i in range(rows)],
        "reference_close": [70.0 + 0.1 * i for i in range(rows)],
        f"target_{horizon}": [0.01] * rows,
        f"actual_price_{horizon}": [70.5 + 0.1 * i for i in range(rows)],
    })
    dataset.attrs["rows_without_context"] = 0
    return dataset, ["feature"]


class CommandLineTest(unittest.TestCase):
    """main() end to end on stubs: what the report's config says and where it may be written."""

    def _run(self, *argv):
        StubPredictor.history_requests = []
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(bwf, "PremiumWTIPredictor", StubPredictor), \
                patch.object(bwf, "prepare_daily_dataset", stub_dataset), \
                patch.object(bwf, "data_json_path", lambda value: Path(tmp) / Path(value).name), \
                patch("sys.argv", ["backtest_walk_forward", "--min-train", "20", *argv]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            bwf.main()
            written = list(Path(tmp).glob("*.json"))
            self.assertEqual(len(written), 1)
            return written[0].name, json.loads(written[0].read_text(encoding="utf-8"))["config"]

    def _refused(self, *argv):
        with self.assertRaises(SystemExit):
            self._run(*argv)
        self.assertEqual(StubPredictor.history_requests, [], msg="refused only after fetching data")

    def test_default_run_is_the_validated_configuration_and_records_its_purge(self):
        name, config = self._run()
        self.assertEqual(name, bwf.DASHBOARD_ARTIFACT)
        self.assertEqual(config["purge_rows_by_horizon"], {"1d": 0, "1w": 4})
        self.assertEqual((config["feature_mode"], config["context_lag_days"], config["train_window"]),
                         ("no_macro", 1, 0))
        self.assertTrue(bwf.is_validated_configuration(config))

        _, rolling = self._run("--train-window", "378", "--horizons", "1w")
        self.assertEqual(rolling["purge_rows_by_horizon"], {"1w": 4})
        self.assertTrue(bwf.is_validated_configuration(rolling))

    def test_comparison_runs_need_their_own_output(self):
        for argv in (("--features", "all"), ("--features", "price_only"), ("--lag-context", "0"),
                     ("--train-window", "250"), ("--no-purge",)):
            with self.subTest(argv=argv):
                self._refused(*argv)
        name, config = self._run("--features", "all", "--output", "data/wf_compare_all.json")
        self.assertEqual((name, config["feature_mode"]), ("wf_compare_all.json", "all"))
        self.assertFalse(bwf.is_validated_configuration(config))

    def test_unpurged_run_records_no_purge_and_must_be_named_leaked(self):
        self._refused("--no-purge", "--output", "data/wf_5y_purged.json")
        name, config = self._run("--no-purge", "--output", "data/wf_5y_unpurged_LEAKED.json")
        self.assertEqual(config["purge_rows_by_horizon"], {"1d": 0, "1w": 0})
        self.assertFalse(bwf.is_validated_configuration(config))

    def test_unallowlisted_output_is_refused_before_the_run(self):
        StubPredictor.history_requests = []
        with patch("sys.argv", ["backtest_walk_forward", "--output", "data/elsewhere.json"]), \
                patch.object(bwf, "PremiumWTIPredictor", StubPredictor), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bwf.main()
        self.assertEqual(StubPredictor.history_requests, [])

    def test_validated_configuration_check(self):
        good = {"feature_mode": "no_macro", "context_lag_days": 1, "train_window": 378,
                "purge_rows_by_horizon": {"1d": 0, "1w": 4}}
        self.assertTrue(bwf.is_validated_configuration(good))
        for change in ({"feature_mode": "all"}, {"context_lag_days": 0}, {"train_window": 252},
                       {"purge_rows_by_horizon": {"1d": 0, "1w": 0}}, {"purge_rows_by_horizon": {}}):
            with self.subTest(change=change):
                self.assertFalse(bwf.is_validated_configuration({**good, **change}))


class CommittedArtifactsTest(unittest.TestCase):
    """The committed walk-forward reports are what the README and dashboard say they are."""

    @staticmethod
    def _report(name):
        return json.loads((safe_paths.DATA_ROOT / name).read_text(encoding="utf-8"))

    def test_dashboard_and_purged_reports_are_the_validated_configuration(self):
        for name in (bwf.DASHBOARD_ARTIFACT, "wf_5y_purged.json", "wf_10y_rolling_purged.json"):
            with self.subTest(name=name):
                config = self._report(name)["config"]
                self.assertTrue(bwf.is_validated_configuration(config))
                self.assertEqual(set(config["rows_without_context_by_horizon"].values()), {0})

    def test_every_day_1d_report_contains_the_every_fifth_day_calls(self):
        # The README quotes the 1d result from this all-days run, which must reproduce every call of
        # the every-fifth-day report (same configuration and window; only the step differs).
        every, fifth = self._report("wf_5y_purged_1d_every_day.json"), self._report("wf_5y_purged.json")
        self.assertTrue(bwf.is_validated_configuration(every["config"]))
        shared = ("data_start", "data_end", "min_train", "estimators", "feature_mode", "context_lag_days",
                  "train_window", "context_period")
        self.assertEqual({k: every["config"][k] for k in shared}, {k: fifth["config"][k] for k in shared})
        calls = {t["t"]: t["dir"] for t in every["results"]["1d"]["trades"]}
        sampled = fifth["results"]["1d"]["trades"]
        self.assertEqual([calls[t["t"]] for t in sampled], [t["dir"] for t in sampled])
        self.assertAlmostEqual(every["results"]["1d"]["metrics"]["ensemble"]["direction_accuracy"], 50.8, places=1)

    def test_purged_and_leaked_pairs_differ_only_in_the_purge(self):
        # The README's before/after tables compare these pairs: same configuration, window and test
        # dates, the purge being the only difference.
        def without_purge(config):
            return {k: v for k, v in config.items() if k != "purge_rows_by_horizon"}

        for purged_name, leaked_name in (("wf_5y_purged.json", "wf_5y_unpurged_LEAKED.json"),
                                         ("wf_10y_rolling_purged.json", "wf_10y_rolling_unpurged_LEAKED.json")):
            purged, leaked = self._report(purged_name), self._report(leaked_name)
            with self.subTest(pair=purged_name):
                self.assertEqual(set(leaked["config"]["purge_rows_by_horizon"].values()), {0})
                self.assertEqual(without_purge(purged["config"]), without_purge(leaked["config"]))
                self.assertEqual(purged["results"].keys(), leaked["results"].keys())
                for horizon in purged["results"]:
                    self.assertEqual([t["t"] for t in purged["results"][horizon]["trades"]],
                                     [t["t"] for t in leaked["results"][horizon]["trades"]])


class OutputAllowlistTest(unittest.TestCase):
    def test_every_readme_backtest_output_has_its_own_data_file(self):
        names = ("walk_forward_backtest_latest.json", "wf_5y_purged.json", "wf_5y_unpurged_LEAKED.json",
                 "wf_5y_purged_1d_every_day.json",
                 "wf_10y_rolling_purged.json", "wf_10y_rolling_unpurged_LEAKED.json",
                 "wf_compare_all.json", "wf_compare_price_only.json")
        resolved = {safe_paths.data_json_path(f"data/{name}") for name in names}
        self.assertEqual(resolved, {Path(os.path.realpath(safe_paths.DATA_ROOT / n)) for n in names})


if __name__ == "__main__":
    unittest.main()
