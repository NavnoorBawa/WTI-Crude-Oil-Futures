import unittest
import math
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from backend.oil import PremiumWTIPredictor, get_historical_data
from backend.server import _build_horizon_metrics


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

    def test_return_target_encoding_round_trips_back_to_price(self):
        predictor = self._make_predictor_stub()

        encoded = predictor._encode_target_value(100.0, 103.5, "return")
        decoded = predictor._decode_target_value(100.0, encoded, "return")

        self.assertAlmostEqual(encoded, 0.035, places=6)
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
        predictor._get_next_wti_contract_symbol = lambda: "CLM26"

        feature_map = predictor.build_market_context_feature_map(wti_data)
        latest_row = feature_map[max(feature_map.keys())]

        for feature_name in [
            "risk_off_pressure_5d",
            "macro_stress_score",
            "term_spread_pct",
            "energy_equity_relative_20d",
            "wti_dxy_corr_20d",
        ]:
            self.assertIn(feature_name, latest_row)
            self.assertTrue(math.isfinite(latest_row[feature_name]))


class ServerMetricSelectionTest(unittest.TestCase):
    def test_unqualified_sparse_live_accuracy_falls_back_to_backtest_display(self):
        accuracy_metrics = {
            "1h": {"total_predictions": 0, "direction_accuracy": 0.0},
            "1d": {"total_predictions": 3, "direction_accuracy": 100.0},
            "1w": {"total_predictions": 0, "direction_accuracy": 0.0},
        }
        horizon_backtests = {
            "1h": {"direction_accuracy": 45.4, "samples": 240},
            "1d": {"direction_accuracy": 39.4, "samples": 33},
            "1w": {"direction_accuracy": 42.4, "samples": 33},
        }
        horizon_confidence = {"1h": 31.7, "1d": 10.0, "1w": 10.0}
        horizon_quality = {
            "1h": {"status": "qualified", "qualified": True, "reasons": []},
            "1d": {"status": "unqualified", "qualified": False, "reasons": ["low_direction_accuracy"]},
            "1w": {"status": "unqualified", "qualified": False, "reasons": ["low_direction_accuracy"]},
        }

        metrics_by_horizon, headline_horizon = _build_horizon_metrics(
            accuracy_metrics,
            horizon_backtests,
            horizon_confidence,
            horizon_quality,
            min_live_accuracy_samples=18,
        )

        self.assertEqual(headline_horizon, "1h")
        self.assertEqual(metrics_by_horizon["1d"]["display_accuracy"], 39.4)
        self.assertEqual(metrics_by_horizon["1d"]["display_accuracy_source"], "backtest")

    def test_headline_horizon_prefers_qualified_horizon(self):
        accuracy_metrics = {
            "1h": {"total_predictions": 15, "direction_accuracy": 53.3},
            "1d": {"total_predictions": 0, "direction_accuracy": 0.0},
            "1w": {"total_predictions": 0, "direction_accuracy": 0.0},
        }
        horizon_backtests = {
            "1h": {"direction_accuracy": 45.4, "samples": 240},
            "1d": {"direction_accuracy": 39.4, "samples": 33},
            "1w": {"direction_accuracy": 39.4, "samples": 33},
        }
        horizon_confidence = {"1h": 31.7, "1d": 10.0, "1w": 10.0}
        horizon_quality = {
            "1h": {"status": "qualified", "qualified": True, "reasons": []},
            "1d": {"status": "unqualified", "qualified": False, "reasons": ["low_direction_accuracy"]},
            "1w": {"status": "unqualified", "qualified": False, "reasons": ["low_direction_accuracy"]},
        }

        metrics_by_horizon, headline_horizon = _build_horizon_metrics(
            accuracy_metrics,
            horizon_backtests,
            horizon_confidence,
            horizon_quality,
            min_live_accuracy_samples=18,
        )

        self.assertEqual(headline_horizon, "1h")
        self.assertEqual(metrics_by_horizon["1h"]["display_accuracy_source"], "live_sparse")
        self.assertEqual(metrics_by_horizon["1d"]["display_accuracy_source"], "backtest")

    def test_headline_horizon_uses_best_available_metrics_when_none_qualified(self):
        accuracy_metrics = {
            "1h": {"total_predictions": 0, "direction_accuracy": 0.0},
            "1d": {"total_predictions": 0, "direction_accuracy": 0.0},
            "1w": {"total_predictions": 0, "direction_accuracy": 0.0},
        }
        horizon_backtests = {
            "1h": {"direction_accuracy": 45.4, "samples": 240},
            "1d": {"direction_accuracy": 39.4, "samples": 33},
            "1w": {"direction_accuracy": 42.4, "samples": 33},
        }
        horizon_confidence = {"1h": 31.7, "1d": 10.0, "1w": 10.0}
        horizon_quality = {
            "1h": {"status": "unqualified", "qualified": False, "reasons": ["low_direction_accuracy"]},
            "1d": {"status": "unqualified", "qualified": False, "reasons": ["low_direction_accuracy"]},
            "1w": {"status": "unqualified", "qualified": False, "reasons": ["low_direction_accuracy"]},
        }

        metrics_by_horizon, headline_horizon = _build_horizon_metrics(
            accuracy_metrics,
            horizon_backtests,
            horizon_confidence,
            horizon_quality,
            min_live_accuracy_samples=18,
        )

        self.assertEqual(headline_horizon, "1h")
        self.assertEqual(metrics_by_horizon["1h"]["display_accuracy"], 45.4)


class HistoricalPayloadTest(unittest.TestCase):
    def test_historical_data_uses_matured_target_timestamps(self):
        class PredictorStub:
            def __init__(self):
                self.stored_actual_prices = {
                    "2026-03-28T10:00:00": {
                        "timestamp": "2026-03-28T10:00:00",
                        "price": 100.0,
                        "volume": 1200,
                    }
                }
                self.stored_predictions = {
                    "2026-03-28T08:00:00": {
                        "predictions": {"1h": 101.0, "1d": 110.0},
                        "prediction_intervals": {"1h": {"upper": 102.0, "lower": 99.0}},
                        "current_price": 99.0,
                    },
                    "2026-03-28T15:00:00": {
                        "predictions": {"1h": 102.0},
                        "prediction_intervals": {"1h": {"upper": 103.0, "lower": 100.0}},
                        "current_price": 100.0,
                    },
                }

            def get_wti_historical_data(self, period="6mo", interval="1d"):
                return pd.DataFrame()

            def _safe_parse_iso(self, value):
                return datetime.fromisoformat(value)

        with patch("backend.oil.get_premium_predictor", return_value=PredictorStub()):
            payload = get_historical_data(limit=50)

        backend_tz = datetime.now().astimezone().tzinfo
        expected_actual_timestamp = pd.Timestamp("2026-03-28T10:00:00").tz_localize(backend_tz).tz_convert("UTC").isoformat().replace("+00:00", "Z")
        expected_issue_timestamp = pd.Timestamp("2026-03-28T08:00:00").tz_localize(backend_tz).tz_convert("UTC").isoformat().replace("+00:00", "Z")
        expected_target_timestamp = pd.Timestamp("2026-03-28T09:00:00").tz_localize(backend_tz).tz_convert("UTC").isoformat().replace("+00:00", "Z")

        actual_history = payload["actual"]
        hourly_history = payload["predicted"]["historical_by_horizon"]["1h"]
        daily_history = payload["predicted"]["historical_by_horizon"]["1d"]
        issued_daily_history = payload["predicted"]["issued_by_horizon"]["1d"]
        issued_hourly_history = payload["predicted"]["issued_by_horizon"]["1h"]

        self.assertEqual(actual_history["timestamps"], [expected_actual_timestamp])
        self.assertEqual(hourly_history["issue_timestamps"], [expected_issue_timestamp])
        self.assertEqual(hourly_history["target_timestamps"], [expected_target_timestamp])
        self.assertEqual(hourly_history["timestamps"], [expected_target_timestamp])
        self.assertEqual(hourly_history["values"], [101.0])
        self.assertEqual(daily_history["values"], [])
        self.assertEqual(issued_daily_history["issue_timestamps"], [expected_issue_timestamp])
        self.assertEqual(issued_daily_history["values"], [110.0])
        self.assertEqual(len(issued_hourly_history["values"]), 2)


if __name__ == "__main__":
    unittest.main()
