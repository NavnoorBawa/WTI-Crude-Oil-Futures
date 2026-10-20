import unittest
import json
import math
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from backend import oil
from backend.oil import PremiumWTIPredictor, get_historical_data


class OilLogicGuardsTest(unittest.TestCase):
    def _make_predictor_stub(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        predictor.actual_quote_heartbeat_seconds = 300
        predictor.market_timezone = ZoneInfo("America/Chicago")
        predictor.storage_timezone = timezone.utc
        predictor.min_live_quality_samples = 10
        predictor.min_live_direction_accuracy = 50.0
        predictor.min_backtest_direction_accuracy = 45.0
        predictor.min_backtest_samples = 30
        predictor.min_quality_confidence = 15.0
        predictor.max_quality_drift_score = 3.0
        predictor.contract_info = {"current_price": 101.25}
        predictor.accuracy_metrics = {
            "1h": {"total_predictions": 15, "direction_accuracy": 53.3},
            "1d": {"total_predictions": 0, "direction_accuracy": 0.0},
            "1w": {"total_predictions": 0, "direction_accuracy": 0.0},
        }
        return predictor

    def test_dedupe_actual_store_collapses_closed_market_duplicates(self):
        predictor = self._make_predictor_stub()
        payload = {
            "2026-03-28T09:52:10.489031": {"timestamp": "2026-03-28T09:52:10.489031", "price": 99.64, "volume": 350768},
            "2026-03-28T09:52:40.620131": {"timestamp": "2026-03-28T09:52:40.620131", "price": 99.64, "volume": 350768},
            "2026-03-28T09:53:10.633921": {"timestamp": "2026-03-28T09:53:10.633921", "price": 99.64, "volume": 350768},
        }

        cleaned, changed = predictor._dedupe_actual_price_store(payload)

        self.assertTrue(changed)
        self.assertEqual(len(cleaned), 1)
        self.assertIn("2026-03-28T09:53:10.633921", cleaned)

    def test_horizon_quality_marks_weak_daily_forecast_unqualified(self):
        predictor = self._make_predictor_stub()

        quality = predictor._assess_horizon_quality(
            "1d",
            confidence_pct=10.0,
            drift_score=3.55,
            backtest_metrics={"direction_accuracy": 39.4, "samples": 33},
        )

        self.assertEqual(quality["status"], "unqualified")
        self.assertIn("low_direction_accuracy", quality["reasons"])
        self.assertIn("low_confidence", quality["reasons"])
        self.assertIn("high_feature_drift", quality["reasons"])

    def test_horizon_quality_marks_supported_hourly_forecast_qualified(self):
        predictor = self._make_predictor_stub()

        quality = predictor._assess_horizon_quality(
            "1h",
            confidence_pct=31.7,
            drift_score=0.82,
            backtest_metrics={"direction_accuracy": 45.4, "samples": 240},
        )

        self.assertEqual(quality["status"], "qualified")
        self.assertTrue(quality["qualified"])

    def test_sorted_time_items_handles_mixed_naive_and_utc_timestamps(self):
        predictor = self._make_predictor_stub()
        payload = {
            "2026-03-28T10:30:00Z": {"price": 101.0},
            "2026-03-28T10:00:00": {"price": 100.0},
            "2026-03-28T10:15:00+00:00": {"price": 100.5},
        }

        ordered_keys = [timestamp for timestamp, _ in predictor._sorted_time_items(payload)]

        self.assertEqual(
            ordered_keys,
            ["2026-03-28T10:00:00", "2026-03-28T10:15:00+00:00", "2026-03-28T10:30:00Z"],
        )

    def test_prediction_reference_price_prefers_live_contract_quote(self):
        predictor = self._make_predictor_stub()

        self.assertEqual(predictor._get_prediction_reference_price(99.0), 101.25)

        predictor.contract_info = {"current_price": 0}
        self.assertEqual(predictor._get_prediction_reference_price(99.0), 99.0)

    def test_provider_exception_does_not_copy_api_key_into_payload_or_logs(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        secret = "news-provider-secret-value"
        predictor.config = SimpleNamespace(NEWSAPI_KEY=secret)

        with (
            patch(
                "backend.oil.requests.get",
                side_effect=RuntimeError(f"request failed at ?apiKey={secret}"),
            ),
            self.assertLogs("backend.oil", level="WARNING") as logs,
        ):
            payload = predictor.get_geopolitical_risk()

        self.assertEqual(payload["error"], "GEOPOLITICAL_SOURCE_FAILED")
        self.assertNotIn(secret, json.dumps(payload))
        self.assertNotIn(secret, "\n".join(logs.output))

    def test_return_target_encoding_round_trips_back_to_price(self):
        predictor = self._make_predictor_stub()

        encoded = predictor._encode_target_value(100.0, 103.5, "return")
        decoded = predictor._decode_target_value(100.0, encoded, "return")

        self.assertAlmostEqual(encoded, 0.035, places=6)
        self.assertAlmostEqual(decoded, 103.5, places=6)

    def test_excess_return_target_encoding_round_trips_back_to_price(self):
        predictor = self._make_predictor_stub()

        encoded = predictor._encode_target_value(100.0, 103.5, "excess_return", baseline_return=0.01)
        decoded = predictor._decode_target_value(100.0, encoded, "excess_return", baseline_return=0.01)

        self.assertAlmostEqual(encoded, 0.025, places=6)
        self.assertAlmostEqual(decoded, 103.5, places=6)

    def test_model_weight_score_rewards_directional_skill(self):
        predictor = self._make_predictor_stub()

        weak_direction = predictor._compose_model_weight_score(0.75, 42.0)
        strong_direction = predictor._compose_model_weight_score(0.55, 61.0)

        self.assertGreater(strong_direction, weak_direction)

    def test_stabilize_ensemble_prediction_shrinks_and_leans_toward_direction_leader(self):
        predictor = self._make_predictor_stub()

        adjusted, meta = predictor._stabilize_ensemble_prediction(
            reference_price=100.0,
            ensemble_prediction=99.0,
            model_predictions={
                "xgboost": 97.0,
                "ridge": 102.0,
            },
            model_scores={
                "xgboost": 0.82,
                "ridge": 0.78,
            },
            model_direction_scores={
                "xgboost": 44.0,
                "ridge": 63.0,
            },
        )

        self.assertGreater(adjusted, 99.0)
        self.assertLess(adjusted, 102.0)
        self.assertEqual(meta["leader_model"], "ridge")
        self.assertLess(meta["shrink_factor"], 1.0)

    def test_engineer_technical_features_emits_richer_trend_and_regime_signals(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        index = pd.date_range("2026-01-01", periods=80, freq="D")
        base = pd.Series([70 + (i * 0.4) + (3 * math.sin(i / 5.0)) for i in range(80)], index=index)
        frame = pd.DataFrame(
            {
                "Close": base,
                "High": base + 1.5,
                "Low": base - 1.5,
                "Volume": [1000 + (i * 8) for i in range(80)],
            },
            index=index,
        )

        features = predictor.engineer_technical_features(frame)

        for feature_name in [
            "return_20",
            "return_60",
            "trend_slope_20",
            "volatility_ratio_5_20",
            "atr_pct_14",
            "drawdown_60",
            "price_zscore_60",
            "dollar_volume_zscore_20",
            "open_gap_pct",
            "close_location_value",
            "momentum_vol_ratio_20",
            "obv_slope_10",
        ]:
            self.assertIn(feature_name, features)
            self.assertTrue(math.isfinite(features[feature_name]))

    def test_market_context_feature_map_emits_cross_asset_regime_features(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        predictor.market_context_period = "3y"
        index = pd.date_range("2025-01-01", periods=90, freq="D")
        wti_data = pd.DataFrame({"Close": pd.Series([70 + (i * 0.25) for i in range(90)], index=index)}, index=index)

        def fake_market_series(symbol, period="3y", interval="1d"):
            offsets = {
                "BZ=F": 2.0,
                "DX-Y.NYB": 0.2,
                "^VIX": 20.0,
                "^OVX": 30.0,
                "^TNX": 4.0,
                "XLE": 10.0,
                "XOP": 8.0,
                "SPY": 15.0,
                "CLM26": 1.0,
            }
            offset = offsets.get(symbol, 0.0)
            return pd.Series([50 + offset + (i * 0.1) for i in range(90)], index=index)

        predictor._fetch_market_series = fake_market_series

        feature_map = predictor.build_market_context_feature_map(wti_data)
        latest_row = feature_map[max(feature_map.keys())]

        for feature_name in [
            "risk_off_pressure_5d",
            "macro_stress_score",
            "energy_equity_relative_20d",
            "wti_dxy_corr_20d",
            "brent_return_1d",
            "ovx_vix_ratio_zscore_20d",
            "wti_brent_corr_20d",
            "risk_off_pressure_1d",
        ]:
            self.assertIn(feature_name, latest_row)
            self.assertTrue(math.isfinite(latest_row[feature_name]))
        # The front/next term-structure family was always constant zero (its bare contract symbol
        # never resolved on Yahoo) and was removed rather than left as dead model inputs.
        self.assertFalse(any(name.startswith("term_spread") for name in latest_row))

    def test_historical_external_feature_map_uses_release_lag_without_leakage(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        predictor.use_historical_external_features_in_training = True
        predictor.eia_release_lag_days = 5
        predictor.fred_daily_release_lag_days = 1
        predictor.fred_monthly_release_lag_days = 15
        predictor._historical_external_mem_cache = {}

        index = pd.date_range("2026-01-01", periods=35, freq="D")
        wti_data = pd.DataFrame({"Close": pd.Series([70 + (i * 0.2) for i in range(35)], index=index)}, index=index)

        predictor._fetch_eia_weekly_stocks_series = lambda: pd.Series(
            [100.0, 95.0, 92.0],
            index=pd.to_datetime(["2026-01-02", "2026-01-09", "2026-01-16"]),
        )

        fred_series = {
            "INDPRO": pd.Series([100.0, 101.0, 102.0], index=pd.to_datetime(["2025-11-01", "2025-12-01", "2026-01-01"])),
            "FEDFUNDS": pd.Series([4.0, 4.1, 4.2], index=pd.to_datetime(["2025-11-01", "2025-12-01", "2026-01-01"])),
            "UMCSENT": pd.Series([70.0, 72.0, 74.0], index=pd.to_datetime(["2025-11-01", "2025-12-01", "2026-01-01"])),
            "T10Y2Y": pd.Series([0.4 + (i * 0.01) for i in range(35)], index=index),
        }
        predictor._fetch_fred_csv_series = lambda series_id, start_date=None, end_date=None: fred_series.get(series_id)

        feature_map = predictor.build_historical_external_feature_map(wti_data)

        before_eia_release = predictor._date_feature_key(pd.Timestamp("2026-01-06"))
        after_eia_release = predictor._date_feature_key(pd.Timestamp("2026-01-07"))
        after_monthly_release = predictor._date_feature_key(pd.Timestamp("2026-01-20"))

        self.assertEqual(feature_map[before_eia_release]["hist_eia_stocks_level"], 0.0)
        self.assertEqual(feature_map[after_eia_release]["hist_eia_stocks_level"], 100.0)
        self.assertGreater(feature_map[after_monthly_release]["hist_fred_fedfunds_level"], 0.0)
        self.assertIn("hist_macro_growth_vs_rates", feature_map[after_monthly_release])
        self.assertTrue(math.isfinite(feature_map[after_monthly_release]["hist_macro_growth_vs_rates"]))


class HistoricalPayloadTest(unittest.TestCase):
    """oil.get_historical_data: the production chart, built without the retracted model."""

    NY = ZoneInfo("America/New_York")

    def _frames(self):
        # The hourly window opens mid-session, so Monday's daily bar (00:00 ET) shares its session
        # with Monday's first hourly bars; spacing alone cannot tell them apart, the kind can.
        daily = pd.DataFrame({"Close": [62.0, 63.4, 63.3], "Volume": [310000, 300000, 290000]},
                             index=pd.DatetimeIndex(["2026-09-11", "2026-09-14", "2026-09-15"]).tz_localize(self.NY))
        hourly = pd.DataFrame({"Close": [63.1, float("nan"), 63.2], "Volume": [20000, 0, 20000]},
                              index=pd.DatetimeIndex(["2026-09-14 06:00", "2026-09-14 06:30", "2026-09-14 07:00"])
                              .tz_localize(self.NY))
        return {"1d": daily, "1h": hourly}

    def _chart(self, live_quote=None, frames=None):
        frames = frames or self._frames()
        with patch("backend.oil._chart_bars", side_effect=lambda period, interval: frames[interval]) as bars, \
                patch.object(PremiumWTIPredictor, "__init__", side_effect=AssertionError("model constructed")):
            chart = get_historical_data(live_quote=live_quote)
        self.assertEqual({call.args for call in bars.call_args_list}, {("10y", "1d"), ("1mo", "1h")})
        return chart["actual"]

    def test_every_chart_point_says_whether_it_is_a_daily_bar_an_hourly_bar_or_the_quote(self):
        actual = self._chart(live_quote=("2026-09-14T11:42:17Z", 63.25, 41000))
        self.assertEqual(actual["kinds"], ["1d", "1d", "1h", "1h", "live"])
        self.assertEqual(actual["timestamps"], ["2026-09-11T04:00:00Z", "2026-09-14T04:00:00Z",
                                                "2026-09-14T10:00:00Z", "2026-09-14T11:00:00Z",
                                                "2026-09-14T11:42:17Z"])
        self.assertEqual(actual["values"], [62.0, 63.4, 63.1, 63.2, 63.25])   # the NaN bar is dropped
        self.assertEqual(actual["volumes"], [310000, 300000, 20000, 20000, 41000])

    def test_the_quote_is_appended_only_when_newer_than_the_newest_bar(self):
        # A quote never displaces or interleaves with exchange bars.
        for stamp in ("2026-09-14T11:00:00Z", "2026-09-14T10:30:00Z", "not a time", None):
            with self.subTest(stamp=stamp):
                actual = self._chart(live_quote=(stamp, 63.25, 41000))
                self.assertNotIn("live", actual["kinds"])
                self.assertEqual(len(actual["values"]), 4)
        for price in (0.0, -37.63, float("nan"), None):
            with self.subTest(price=price):
                self.assertNotIn("live", self._chart(live_quote=("2026-09-14T11:42:17Z", price, 1))["kinds"])

    def test_without_hourly_bars_the_daily_history_runs_to_the_quote(self):
        frames = self._frames()
        frames["1h"] = pd.DataFrame()
        actual = self._chart(live_quote=("2026-09-15T13:00:00Z", 63.5, 0), frames=frames)
        self.assertEqual(actual["kinds"], ["1d", "1d", "1d", "live"])

    def test_download_failures_are_logged_by_type_only(self):
        secret = "https://query1.finance.yahoo.com/?crumb=provider-secret\nFAKE_SUCCESS"
        with patch("backend.oil._chart_bars", side_effect=RuntimeError(secret)), \
                self.assertLogs("backend.oil", level="WARNING") as logs:
            actual = get_historical_data(live_quote=("2026-09-15T13:00:00Z", 63.5, 0))["actual"]
        output = "\n".join(logs.output)
        self.assertIn("Daily chart history unavailable error_type=RuntimeError", output)
        self.assertIn("Hourly chart history unavailable error_type=RuntimeError", output)
        self.assertNotIn("provider-secret", output)
        # Only the quote is left: the deploy validator rejects a chart without a market bar.
        self.assertEqual(actual["kinds"], ["live"])

    def test_long_histories_are_thinned_but_keep_the_newest_point(self):
        index = pd.date_range("2010-01-01", periods=5000, freq="D", tz="UTC")
        frames = {"1d": pd.DataFrame({"Close": [50.0 + i * 0.001 for i in range(5000)], "Volume": 1}, index=index),
                  "1h": pd.DataFrame()}
        actual = self._chart(live_quote=("2030-01-01T00:00:00Z", 60.0, 0), frames=frames)
        self.assertLessEqual(len(actual["values"]), oil.CHART_MAX_POINTS)
        self.assertEqual(actual["kinds"][-1], "live")
        self.assertEqual(actual["timestamps"], sorted(actual["timestamps"]))

    def test_bars_are_cached_briefly(self):
        frame = self._frames()["1d"]
        oil._chart_cache.clear()
        self.addCleanup(oil._chart_cache.clear)
        with patch("backend.oil._yf_history_with_retry", return_value=frame) as download, \
                patch.object(oil.time, "time", side_effect=[1000.0, 1030.0, 1100.0]):
            oil._chart_bars("10y", "1d")
            oil._chart_bars("10y", "1d")              # 30 s later: cached
            oil._chart_bars("10y", "1d")              # 100 s later: downloaded again
        self.assertEqual(download.call_count, 2)
        download.assert_called_with("CL=F", period="10y", interval="1d", timeout=15)


if __name__ == "__main__":
    unittest.main()
