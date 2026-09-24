"""Thread safety, performance and per-session storage of the live forecast/quote stores (backend.oil)."""

import random
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.oil import PremiumWTIPredictor, get_historical_data, horizon_target_time

ET = ZoneInfo("America/New_York")


def make_store_predictor(directory):
    """A predictor with empty stores persisted under `directory` (no network, no __init__)."""
    predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
    predictor.storage_timezone = timezone.utc
    predictor.market_timezone = ZoneInfo("America/Chicago")
    predictor.actual_quote_heartbeat_seconds = 300
    predictor.store_retention_days = 90
    predictor.accuracy_metrics = {}
    base = Path(directory)
    predictor.predictions_file = base / "predictions.json"
    predictor.actual_prices_file = base / "actual_prices.json"
    predictor.accuracy_file = base / "accuracy.json"
    for horizon in ("1h", "1d", "1w"):
        setattr(predictor, f"predictions_{horizon}_file", base / f"predictions_{horizon}.json")
        setattr(predictor, f"predictions_{horizon}", {})
    predictor.stored_predictions = {}
    predictor.stored_actual_prices = {}
    predictor._init_runtime_state()
    return predictor


def iso(moment):
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def horizon_row(timestamp, prediction, current_price):
    return {
        "timestamp": timestamp,
        "prediction": prediction,
        "current_price": current_price,
        "interval_lower": prediction - 1.0,
        "interval_upper": prediction + 1.0,
    }


def run_record(timestamp, current_price, predictions):
    record = {"timestamp": timestamp, "current_price": current_price, "predictions": dict(predictions)}
    rows = {h: horizon_row(timestamp, value, current_price) for h, value in predictions.items()}
    return record, rows


class BusinessDayTargetTest(unittest.TestCase):
    def test_daily_targets_land_on_the_next_business_days_at_the_same_wall_time(self):
        friday = datetime(2026, 9, 18, 10, tzinfo=ET)
        self.assertEqual(horizon_target_time(friday, "1d"), datetime(2026, 9, 21, 10, tzinfo=ET))
        self.assertEqual(horizon_target_time(friday, "1w"), datetime(2026, 9, 25, 10, tzinfo=ET))
        self.assertEqual(horizon_target_time(friday, "1h"), friday + timedelta(hours=1))
        # Thanksgiving (2026-11-26) is skipped.
        wednesday = datetime(2026, 11, 25, 10, tzinfo=ET)
        self.assertEqual(horizon_target_time(wednesday, "1d"), datetime(2026, 11, 27, 10, tzinfo=ET))
        # Across the DST change the exchange wall-clock time is kept (10:00 EDT -> 10:00 EST).
        before_dst_end = datetime(2026, 10, 30, 14, tzinfo=timezone.utc)  # 10:00 EDT
        self.assertEqual(horizon_target_time(before_dst_end, "1d"), datetime(2026, 11, 2, 15, tzinfo=timezone.utc))


class ForecastStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.predictor = make_store_predictor(self._tmp.name)

    def test_one_forecast_per_horizon_per_session_and_exact_reruns_are_skipped(self):
        predictor = self.predictor
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(days=3)
        # Twenty reruns three minutes apart: the reference price moves, the models do not.
        for step in range(20):
            timestamp = iso(start + timedelta(minutes=3 * step))
            record, rows = run_record(timestamp, 90.0 + step * 0.01, {"1h": 90.1, "1d": 90.5, "1w": 91.0})
            predictor._store_prediction_record(timestamp, record, rows)

        # All twenty runs fall in one clock hour (and so one CME session, which starts on the hour).
        self.assertEqual(len(predictor.predictions_1h), 1)
        self.assertEqual(len(predictor.predictions_1d), 1)
        self.assertEqual(len(predictor.predictions_1w), 1)
        self.assertEqual(list(predictor.predictions_1d), [iso(start)])  # the session's first call is kept
        # The chart's record store keeps the latest run of the hour.
        self.assertEqual(len(predictor.stored_predictions), 1)
        self.assertAlmostEqual(next(iter(predictor.stored_predictions.values()))["current_price"], 90.19)

        # Hours later with the market closed, the same inputs rerun: the first rerun is a new hourly
        # forecast (new reference price), every identical rerun after it is stored nowhere.
        latest_record = predictor._latest_time_item(predictor.stored_predictions)[1]
        first_rerun = iso(start + timedelta(hours=5))
        record, rows = run_record(first_rerun, latest_record["current_price"], latest_record["predictions"])
        changed = predictor._store_prediction_record(first_rerun, record, rows)
        self.assertNotIn("predictions", changed)
        self.assertIn("1h", changed)
        for hours in (6, 7, 8):
            rerun = iso(start + timedelta(hours=hours))
            record, rows = run_record(rerun, latest_record["current_price"], latest_record["predictions"])
            self.assertEqual(predictor._store_prediction_record(rerun, record, rows), [])
        self.assertEqual(len(predictor.predictions_1h), 2)

        # The next trading session gets its own 1d/1w forecast.
        next_session = iso(start + timedelta(days=1, hours=1))
        record, rows = run_record(next_session, 92.0, {"1h": 92.1, "1d": 92.5, "1w": 93.0})
        self.assertIn("1d", predictor._store_prediction_record(next_session, record, rows))
        self.assertTrue(predictor.predictions_1d_file.exists())

    def test_stores_are_pruned_to_the_retention_window_on_save(self):
        predictor = self.predictor
        old = iso(datetime.now(timezone.utc) - timedelta(days=200))
        recent = iso(datetime.now(timezone.utc) - timedelta(days=1))
        predictor.predictions_1w.update({old: horizon_row(old, 90, 89), recent: horizon_row(recent, 91, 90)})
        predictor._save_horizon_predictions("1w")
        self.assertEqual(list(predictor.predictions_1w), [recent])

    def test_non_finite_quotes_are_never_stored(self):
        self.assertFalse(self.predictor.store_actual_price(float("nan"), 10))
        self.assertFalse(self.predictor.store_actual_price(float("inf"), 10))
        self.assertTrue(self.predictor.store_actual_price(90.5, 10))
        self.assertEqual(len(self.predictor.stored_actual_prices), 1)


class AccuracyPerformanceTest(unittest.TestCase):
    def _seed(self, predictor, n_prices, n_predictions):
        rng = random.Random(7)
        start = datetime(2026, 8, 3, tzinfo=timezone.utc)
        for i in range(n_prices):
            stamp = iso(start + timedelta(minutes=5 * i))
            predictor.stored_actual_prices[stamp] = {"timestamp": stamp, "price": 80 + rng.random() * 10, "volume": i}
        for horizon in ("1h", "1d", "1w"):
            store = getattr(predictor, f"predictions_{horizon}")
            for i in range(n_predictions):
                stamp = iso(start + timedelta(minutes=10 * i + 1))
                store[stamp] = horizon_row(stamp, 80 + rng.random() * 10, 80 + rng.random() * 10)

    def test_indexed_lookup_matches_a_brute_force_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            predictor = make_store_predictor(tmp)
            self._seed(predictor, 500, 50)
            rows = [(predictor._safe_parse_iso(k), v["price"]) for k, v in predictor.stored_actual_prices.items()]
            window = timedelta(days=3)
            for target in [datetime(2026, 8, 3, 7, 3, tzinfo=timezone.utc), datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
                           datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 9, 30, tzinfo=timezone.utc)]:
                candidates = [(t - target, price) for t, price in rows if timedelta(0) <= t - target <= window]
                expected = min(candidates)[1] if candidates else None
                self.assertEqual(predictor._find_closest_actual_price(target, window), expected)

    def test_accuracy_over_10k_quotes_and_2k_forecasts_is_fast_and_then_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            predictor = make_store_predictor(tmp)
            self._seed(predictor, 10_000, 2_000)

            started = time.perf_counter()
            metrics = predictor.calculate_prediction_accuracy()
            elapsed = time.perf_counter() - started
            # ~0.1 s here; the old per-forecast rescan of every quote took minutes.
            self.assertLess(elapsed, 1.5, f"accuracy took {elapsed:.2f}s")
            self.assertEqual(metrics["1d"]["total_predictions"], 2_000)
            self.assertGreater(metrics["1w"]["total_predictions"], 0)

            started = time.perf_counter()
            again = predictor.calculate_prediction_accuracy()
            self.assertLess(time.perf_counter() - started, 0.2)
            self.assertEqual(again, metrics)

            cached_versions = predictor._accuracy_cache[0]
            predictor.store_actual_price(123.0, 1)  # any store change invalidates the cache
            predictor.calculate_prediction_accuracy()
            self.assertNotEqual(predictor._accuracy_cache[0], cached_versions)


class ConcurrentStoreTest(unittest.TestCase):
    def test_price_thread_writes_while_accuracy_and_chart_readers_iterate(self):
        with tempfile.TemporaryDirectory() as tmp:
            predictor = make_store_predictor(tmp)
            now = datetime.now(timezone.utc)
            for i in range(300):
                stamp = iso(now - timedelta(days=12) + timedelta(minutes=20 * i))
                predictor.stored_actual_prices[stamp] = {"timestamp": stamp, "price": 85 + i * 0.01, "volume": i}
                for horizon in ("1h", "1d", "1w"):
                    getattr(predictor, f"predictions_{horizon}")[stamp] = horizon_row(stamp, 86.0, 85.5)
                predictor.stored_predictions[stamp] = {"predictions": {"1h": 86.0, "1d": 86.0, "1w": 86.0}, "current_price": 85.5}

            errors = []
            stop = threading.Event()

            def guarded(target):
                def runner():
                    try:
                        target()
                    except Exception as exc:  # pragma: no cover - the assertion reports it
                        errors.append(repr(exc))
                        stop.set()
                return runner

            def writer():
                for i in range(150):
                    if stop.is_set():
                        return
                    predictor.store_actual_price(90.0 + i * 0.001, i)
                    if i % 25 == 0:
                        stamp = iso(datetime.now(timezone.utc) + timedelta(hours=i))
                        record, rows = run_record(stamp, 90.0 + i, {"1h": 91.0 + i, "1d": 92.0 + i, "1w": 93.0 + i})
                        predictor._store_prediction_record(stamp, record, rows)
                stop.set()

            def accuracy_reader():
                while not stop.is_set():
                    predictor.calculate_prediction_accuracy()
                    predictor._get_recent_realized_abs_errors("1w", relative=True)
                    time.sleep(0.001)

            def chart_reader():
                from unittest.mock import patch
                with patch("backend.oil.get_premium_predictor", return_value=predictor), \
                        patch.object(predictor, "get_wti_historical_data", side_effect=RuntimeError("offline")):
                    while not stop.is_set():
                        get_historical_data(limit=50)
                        time.sleep(0.001)

            threads = [threading.Thread(target=guarded(fn)) for fn in (writer, accuracy_reader, chart_reader)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)

            self.assertEqual(errors, [])
            self.assertEqual(len(predictor.stored_actual_prices), 300 + 150)


if __name__ == "__main__":
    unittest.main()
