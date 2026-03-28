import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from oil import PremiumWTIPredictor, get_historical_data
from server import _build_horizon_metrics


class OilLogicGuardsTest(unittest.TestCase):
    def _make_predictor_stub(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        predictor.actual_quote_heartbeat_seconds = 300
        predictor.market_timezone = ZoneInfo("America/Chicago")
        predictor.min_live_quality_samples = 10
        predictor.min_live_direction_accuracy = 50.0
        predictor.min_backtest_direction_accuracy = 45.0
        predictor.min_backtest_samples = 30
        predictor.min_quality_confidence = 15.0
        predictor.max_quality_drift_score = 3.0
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


class ServerMetricSelectionTest(unittest.TestCase):
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

        with patch("oil.get_premium_predictor", return_value=PredictorStub()):
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
