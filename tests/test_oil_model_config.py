"""The live 1D/1W model reproduces the validated backtest configuration, plus the supporting fixes
(completed bars only, conformal intervals, relative regime, keyword matching, publication lags,
external-source quota handling). Network-free."""

import math
import os
import random
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backend import oil
from backend.backtest_walk_forward import build_ensemble_prediction, prepare_daily_dataset, purge_count
from backend.oil import (
    GEO_RISK_PATTERNS,
    NEWS_POSITIVE_PATTERNS,
    PremiumWTIPredictor,
    ml_regime_caveat,
    split_conformal_quantile,
)

ET = ZoneInfo("America/New_York")
CONTEXT_SYMBOLS = {"BZ=F": 2.0, "DX-Y.NYB": 0.2, "^VIX": 20.0, "^OVX": 30.0, "^TNX": 4.0,
                   "XLE": 10.0, "XOP": 8.0, "SPY": 15.0}


def synthetic_market(bars=110, seed=3):
    """Business-day WTI bars (Yahoo-style midnight-ET index) and matching context series."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2025-01-06", periods=bars).tz_localize("America/New_York")
    close = 70 * np.exp(np.cumsum(rng.normal(0, 0.02, bars)))
    wti = pd.DataFrame(
        {
            "Open": close * (1 + rng.normal(0, 0.004, bars)),
            "High": close * (1 + np.abs(rng.normal(0, 0.015, bars))),
            "Low": close * (1 - np.abs(rng.normal(0, 0.015, bars))),
            "Close": close,
            "Volume": rng.integers(50_000, 400_000, bars).astype(float),
        },
        index=index,
    )
    context = {
        symbol: pd.Series(50 + offset + np.cumsum(rng.normal(0, 0.5, bars)), index=index)
        for symbol, offset in CONTEXT_SYMBOLS.items()
    }
    return wti, context


def context_predictor(context, lookback=40):
    predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
    predictor.market_context_period = "3y"
    predictor.daily_feature_lookback_bars = lookback
    predictor.daily_target_mode = "return"
    predictor.context_lag_days = 1
    predictor.daily_training_rows = 0
    predictor._fetch_market_series = lambda symbol, period="3y", interval="1d": context.get(symbol)
    return predictor


class ProductionMatchesBacktestTest(unittest.TestCase):
    def test_training_rows_equal_backtest_rows_with_one_day_context_lag(self):
        wti, context = synthetic_market()
        predictor = context_predictor(context)
        dataset, feature_cols = prepare_daily_dataset(predictor, wti, "1w", feature_mode="no_macro", lag_context_days=1)
        market_map = predictor.build_market_context_feature_map(wti)
        frame, _, first_position = predictor._build_daily_model_inputs(wti, "1w", market_map)

        self.assertEqual(first_position, 39)
        self.assertEqual(list(frame.columns), feature_cols + ["target_1w"])
        self.assertEqual(len(frame), len(dataset))
        np.testing.assert_allclose(frame[feature_cols].to_numpy(), dataset[feature_cols].to_numpy())
        np.testing.assert_allclose(frame["target_1w"].to_numpy(), dataset["target_1w"].to_numpy())

        # The context really is the previous bar's: vix_level of row i == same-day vix_level of row i-1.
        same_day, _ = prepare_daily_dataset(predictor, wti, "1w", feature_mode="no_macro", lag_context_days=0)
        np.testing.assert_allclose(frame["vix_level"].iloc[1:].to_numpy(), same_day["vix_level"].iloc[:-1].to_numpy())

    def test_inference_row_equals_the_backtest_row_for_the_same_bar(self):
        wti, context = synthetic_market()
        predictor = context_predictor(context)
        dataset, feature_cols = prepare_daily_dataset(predictor, wti, "1w", feature_mode="no_macro", lag_context_days=1)
        prefix = wti.iloc[:100]  # production sees bars up to position 99 and forecasts from it
        _, inference_row, _ = predictor._build_daily_model_inputs(prefix, "1w", predictor.build_market_context_feature_map(prefix))
        backtest_row = dataset.iloc[99 - 39]  # dataset rows start at position lookback - 1
        self.assertEqual(backtest_row["timestamp"], str(prefix.index[-1]))
        np.testing.assert_allclose([inference_row[c] for c in feature_cols], backtest_row[feature_cols].astype(float).to_numpy())

    def test_rolling_window_keeps_the_backtest_train_window_minus_the_purge(self):
        wti, context = synthetic_market()
        predictor = context_predictor(context)
        predictor.daily_training_rows = 50
        frame, _, first_position = predictor._build_daily_model_inputs(wti, "1w", predictor.build_market_context_feature_map(wti))
        self.assertEqual(first_position, len(wti) - 1 - 50)
        self.assertEqual(len(frame), 50 - purge_count("1w"))

    def test_live_1w_forecast_equals_the_backtest_ensemble_on_the_same_data(self):
        """End to end: production's 1W number is the validated pipeline's number (no regime multiplier)."""
        wti, context = synthetic_market(bars=140)
        live = wti.iloc[:135]
        contract = {"symbol": "CLX26", "yfinance_symbol": "CL=F", "history_symbol": "CL=F",
                    "current_price": float(live["Close"].iloc[-1]), "volume": 1000}
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)  # the predictor persists its stores under ./data
            try:
                with patch.object(oil, "get_current_wti_contract", return_value=contract):
                    predictor = PremiumWTIPredictor()
                predictor.model_n_estimators = 20
                predictor.model_cpu_workers = 1
                predictor.daily_training_rows = 0
                predictor.daily_target_mode = "return"
                predictor.daily_feature_lookback_bars = 63
                predictor.daily_feature_lookback_bars_1d = 63
                predictor.daily_feature_lookback_bars_1w = 63
                predictor.use_historical_external_features_in_training = False
                predictor.use_external_features_in_training = False
                predictor._fetch_market_series = lambda symbol, period="3y", interval="1d": context.get(symbol)
                predictor.get_wti_historical_data = lambda period=None, interval="1d": live.copy()
                predictor.get_wti_hourly_data = lambda period=None: None
                predictor.get_external_data_sources = lambda: {"geopolitical": {"regime": "LOW", "data_quality": 90}}
                predictor._refresh_contract_if_needed = lambda: None
                # The old code multiplied tree-model weights by 1.5x in HIGH_VOLATILITY; it must not matter now.
                with patch.object(PremiumWTIPredictor, "detect_market_regime", return_value="HIGH_VOLATILITY"), \
                        patch.object(oil, "_utc_now", return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)):
                    record = predictor.get_multi_horizon_predictions()
            finally:
                os.chdir(cwd)

            backtester = context_predictor(context, lookback=63)
            backtester.model_n_estimators = 20
            backtester.model_cpu_workers = 1
            backtester.time_series_cv_splits = predictor.time_series_cv_splits
            backtester.max_selected_features = predictor.max_selected_features
            dataset, feature_cols = prepare_daily_dataset(backtester, wti, "1w", feature_mode="no_macro", lag_context_days=1)
            end_idx = (len(live) - 1) - 62  # dataset row of the live inference bar
            train_df = dataset.iloc[0:end_idx - purge_count("1w")]
            package_tuple = backtester.train_prediction_models(train_df[feature_cols + ["target_1w"]], "target_1w", target_mode="return")
            package = dict(zip(["models", "scores", "scaler", "selector", "selected_features", "all_feature_names", "diagnostics"], package_tuple))
            package["target_mode"] = "return"
            row = dataset.iloc[end_idx]
            row_features = {k: float(row[k]) for k in feature_cols}
            row_features["reference_close"] = float(row["reference_close"])
            expected = build_ensemble_prediction(backtester, package, row_features, "1w",
                                                 dataset.iloc[0:end_idx]["reference_close"].astype(float).tolist())

        self.assertEqual(record["market_regime"], "HIGH_VOLATILITY")
        self.assertEqual(record["model_configuration"]["context_lag_days"], 1)
        self.assertFalse(record["model_configuration"]["regime_weighting"])
        self.assertAlmostEqual(record["predictions"]["1w"], expected, places=6)
        self.assertEqual(record["prediction_intervals"]["1w"]["interval_level"], 0.8)
        self.assertEqual(record["prediction_intervals"]["1w"]["interval_method"], "split_conformal")


class CompletedBarsTest(unittest.TestCase):
    def setUp(self):
        self.predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        self.frame = pd.DataFrame(
            {"Close": [90.0, 91.0, 92.0]},
            index=pd.DatetimeIndex(["2026-09-21", "2026-09-22", "2026-09-23"]).tz_localize("America/New_York"),
        )

    def _kept_last(self, now_et):
        kept = self.predictor._drop_in_progress_daily_bar(self.frame, now=now_et.astimezone(timezone.utc))
        return str(kept.index[-1].date())

    def test_the_current_session_bar_is_dropped_until_the_17_00_et_close(self):
        self.assertEqual(self._kept_last(datetime(2026, 9, 23, 11, tzinfo=ET)), "2026-09-22")
        self.assertEqual(self._kept_last(datetime(2026, 9, 23, 17, 30, tzinfo=ET)), "2026-09-23")
        # After the 18:00 ET reopen Yahoo's bar dated today holds the NEXT session's prices.
        self.assertEqual(self._kept_last(datetime(2026, 9, 23, 21, tzinfo=ET)), "2026-09-22")
        self.assertEqual(self._kept_last(datetime(2026, 9, 24, 3, tzinfo=ET)), "2026-09-23")

    def test_weekend_keeps_fridays_completed_bar(self):
        friday = pd.DataFrame({"Close": [90.0, 91.0]},
                              index=pd.DatetimeIndex(["2026-09-24", "2026-09-25"]).tz_localize("America/New_York"))
        for now_et in (datetime(2026, 9, 25, 19, tzinfo=ET), datetime(2026, 9, 26, 12, tzinfo=ET),
                       datetime(2026, 9, 27, 19, tzinfo=ET)):
            kept = self.predictor._drop_in_progress_daily_bar(friday, now=now_et.astimezone(timezone.utc))
            self.assertEqual(str(kept.index[-1].date()), "2026-09-25")


class ConformalIntervalTest(unittest.TestCase):
    def test_finite_sample_rank(self):
        self.assertEqual(split_conformal_quantile(range(1, 10), 0.8), 8)  # ceil(10 * 0.8) = 8th
        self.assertEqual(split_conformal_quantile([3, 1, 2], 0.8), 3)     # rank 4 > n: the max
        self.assertIsNone(split_conformal_quantile([float("nan")], 0.8))

    def test_coverage_on_synthetic_residuals_is_near_the_stated_level(self):
        rng = np.random.default_rng(11)
        calibration = np.abs(rng.standard_t(df=4, size=4_000))
        fresh = np.abs(rng.standard_t(df=4, size=100_000))
        coverage = float(np.mean(fresh <= split_conformal_quantile(calibration, 0.8)))
        self.assertGreater(coverage, 0.78)
        self.assertLess(coverage, 0.82)
        # The retired heuristic, max(1.1 * MAE, 0.9 * RMSE), under-covers on the same residuals.
        heuristic = max(1.1 * np.mean(calibration), 0.9 * np.sqrt(np.mean(calibration ** 2)))
        self.assertLess(float(np.mean(fresh <= heuristic)), 0.78)

    def _interval_predictor(self, accuracy):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        predictor.target_interval_coverage = 0.8
        predictor.interval_coverage_gain = 0.25
        predictor.accuracy_metrics = accuracy
        predictor.predictions_1w = {}
        return predictor

    def test_margin_uses_the_level_and_zero_live_coverage_is_the_worst_case(self):
        residuals = [i / 1000 for i in range(1, 101)]  # 0.1% .. 10% of price
        margin, meta = self._interval_predictor({})._conformal_interval_margin("1w", 100.0, residuals)
        self.assertAlmostEqual(margin, 8.1)  # ceil(101 * 0.8) = 81st score = 8.1% of 100
        self.assertEqual(meta["interval_level"], 0.8)
        self.assertEqual(meta["interval_method"], "split_conformal")

        never_covered = {"1w": {"interval_total": 12, "interval_hits": 0, "interval_coverage": 0.0}}
        wide, wide_meta = self._interval_predictor(never_covered)._conformal_interval_margin("1w", 100.0, residuals)
        self.assertEqual(wide_meta["observed_live_coverage"], 0.0)
        self.assertGreater(wide, margin)
        self.assertEqual(wide_meta["effective_level"], 0.99)


class RegimeTest(unittest.TestCase):
    def _frame(self, ranges):
        closes = np.full(len(ranges), 70.0)
        return pd.DataFrame({"Close": closes, "High": closes + np.asarray(ranges) / 2, "Low": closes - np.asarray(ranges) / 2})

    def test_typical_wti_volatility_is_normal_not_permanently_high(self):
        ranges = [70 * 0.037] * 300  # ATR14 = 3.7% of price, WTI's median; the old 1.5% cut said HIGH
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        self.assertEqual(predictor.detect_market_regime(self._frame(ranges)), "NORMAL")

    def test_top_and_bottom_quintiles_of_the_trailing_year(self):
        rng = random.Random(9)
        base = [70 * 0.037 * (0.9 + 0.2 * rng.random()) for _ in range(286)]
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        self.assertEqual(predictor.detect_market_regime(self._frame(base + [x * 3 for x in base[:14]])), "HIGH_VOLATILITY")
        self.assertEqual(predictor.detect_market_regime(self._frame(base + [x * 0.3 for x in base[:14]])), "LOW_VOLATILITY")


class KeywordMatchingTest(unittest.TestCase):
    def test_terms_match_whole_words_only(self):
        def positive(text):
            return oil._count_term_hits(NEWS_POSITIVE_PATTERNS, text)

        self.assertEqual(positive("supply concerns at the enterprise level"), 0)
        self.assertEqual(positive("prices rose as crude jumped"), 2)
        self.assertEqual(positive("oil is up"), 1)
        conflict = GEO_RISK_PATTERNS["conflict"]
        for text in ("forward guidance", "analysts warn", "award ceremony", "software update"):
            self.assertFalse(any(p.search(text) for p in conflict), text)
        self.assertTrue(any(p.search("trade wars escalate") for p in conflict))
        self.assertTrue(any(p.search("opec+ meets") for p in GEO_RISK_PATTERNS["opec"]))

    def test_geopolitical_score_ignores_substring_false_positives(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        predictor.config = SimpleNamespace(NEWSAPI_KEY="key")
        now = datetime.now(timezone.utc).isoformat()
        articles = [{"title": "Forward curve steady as analysts warn of award delays", "description": "",
                     "publishedAt": now, "source": {"name": "x"}}]
        response = SimpleNamespace(status_code=200, json=lambda: {"articles": articles})
        with patch("backend.oil.requests.get", return_value=response):
            payload = predictor.get_geopolitical_risk()
        self.assertEqual(payload["conflict_articles"], 0)
        self.assertEqual(payload["regime"], "LOW")


class PublicationLagTest(unittest.TestCase):
    def test_monthly_values_wait_for_release_and_daily_values_one_business_day(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        index = pd.date_range("2026-08-25", "2026-09-30", freq="D")
        monthly = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-07-01", "2026-08-01"]))
        aligned = predictor._align_released_series_to_index(index, monthly, 20, frequency="monthly")
        # August's value (dated 08-01) is usable from 2026-09-21, not 08-16 as with a 15-day lag.
        self.assertEqual(aligned[pd.Timestamp("2026-09-20")], 1.0)
        self.assertEqual(aligned[pd.Timestamp("2026-09-21")], 2.0)
        daily = pd.Series([5.0], index=pd.to_datetime(["2026-09-04"]))  # a Friday
        aligned = predictor._align_released_series_to_index(index, daily, 1, frequency="daily")
        self.assertTrue(math.isnan(aligned[pd.Timestamp("2026-09-06")]))
        self.assertEqual(aligned[pd.Timestamp("2026-09-07")], 5.0)


class ExternalSourceQuotaTest(unittest.TestCase):
    def _predictor(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        predictor.use_external_features_in_training = False
        predictor.strict_premium_api_required = False
        predictor.external_data_ttl_seconds = 180
        predictor.external_fetch_workers = 2
        predictor.external_data_cache = os.devnull
        predictor._atomic_write_json = lambda path, payload: None
        predictor.calls = 0
        return predictor

    def test_only_the_regime_feed_is_called_and_it_is_cached_with_failure_backoff(self):
        predictor = self._predictor()
        results = [{"regime": "ELEVATED", "data_quality": 90},
                   {"regime": "UNKNOWN", "data_quality": 0, "source": "newsapi_geopolitical_failed"}]

        def fetch():
            predictor.calls += 1
            return dict(results[min(predictor.calls - 1, 1)])

        predictor.get_geopolitical_risk = fetch
        self.assertEqual(list(predictor._external_source_fetchers()), ["geopolitical"])
        clock = [1_000_000.0]
        with patch.object(oil.time, "time", side_effect=lambda: clock[0]):
            self.assertEqual(predictor.get_external_data_sources()["geopolitical"]["regime"], "ELEVATED")
            clock[0] += 1799  # within the 30-minute NewsAPI TTL: no request
            predictor.get_external_data_sources()
            self.assertEqual(predictor.calls, 1)
            clock[0] += 2  # expired: refetch fails -> last good payload served, flagged stale
            served = predictor.get_external_data_sources()["geopolitical"]
            self.assertEqual(predictor.calls, 2)
            self.assertEqual(served["regime"], "ELEVATED")
            self.assertTrue(served["stale"])
            clock[0] += 60  # the failure is cached (backoff), not retried every cycle
            predictor.get_external_data_sources()
            self.assertEqual(predictor.calls, 2)
            clock[0] += 300
            predictor.get_external_data_sources()
            self.assertEqual(predictor.calls, 3)

    def test_unknown_regime_is_reported_instead_of_a_silent_all_clear(self):
        self.assertIsNone(ml_regime_caveat({"regime": "LOW"}))
        self.assertIn("HIGH/CRITICAL", ml_regime_caveat({"regime": "CRITICAL"}))
        self.assertIn("unavailable", ml_regime_caveat({"regime": "UNKNOWN"}))
        self.assertIn("unavailable", ml_regime_caveat({"skipped": True, "data_quality": 0}))


class ConfigurationDefaultsTest(unittest.TestCase):
    def test_defaults_match_the_validated_configuration_and_gates(self):
        contract = {"symbol": "CLX26", "yfinance_symbol": "CL=F", "history_symbol": "CL=F", "current_price": 90.0}
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch.object(oil, "get_current_wti_contract", return_value=contract), \
                        patch.dict(os.environ, {"MIN_BACKTEST_DIRECTION_ACCURACY_PERCENT": "45"}):
                    for name in ("MIN_REQUIRED_EXTERNAL_SOURCES", "DAILY_TRAINING_ROWS"):
                        os.environ.pop(name, None)
                    predictor = PremiumWTIPredictor()
            finally:
                os.chdir(cwd)
        self.assertEqual(predictor.min_required_external_sources, 0)
        self.assertEqual(predictor.min_backtest_direction_accuracy, 50.0)  # a 45% floor is refused
        self.assertEqual(predictor.context_lag_days, 1)
        self.assertEqual(predictor.daily_training_rows, 378)
        self.assertEqual(predictor.history_symbol, "CL=F")
        self.assertEqual(predictor._get_market_symbol_candidates(), ["CL=F"])


if __name__ == "__main__":
    unittest.main()
