"""Network-free tests for backend/server.py readiness semantics and the playbook cache."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import server
from backend.oil import PremiumWTIPredictor
from scripts.validate_frozen_payload import validate_payload


class ReadinessTest(unittest.TestCase):
    def test_root_is_not_ready_while_starting(self):
        # A readiness endpoint must not answer 200 before the service can serve data.
        with (
            mock.patch.object(server, "ensure_startup_started"),
            mock.patch.object(server._startup_ready, "is_set", return_value=False),
        ):
            response = server.app.test_client().get("/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Retry-After", response.headers)
        self.assertFalse(response.get_json()["ready"])

    def test_every_not_ready_503_carries_retry_after(self):
        # Startup done, ML still warming up (the default deferred warmup): / and /health are 503,
        # and the README promises Retry-After until the service can serve data.
        with (
            mock.patch.object(server, "ensure_startup_started"),
            mock.patch.object(server._startup_ready, "is_set", return_value=True),
            mock.patch.dict(server.system_state, {"ml_ready": False, "cached_predictions": None}),
        ):
            client = server.app.test_client()
            for path in ("/", "/health"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.get_json()["status"], "INITIALIZING")
                    self.assertGreaterEqual(int(response.headers["Retry-After"]), 1)

    def test_live_never_starts_upstream_initialization(self):
        # Initialization calls Yahoo; a liveness probe must not start it (or restart it after a
        # failed attempt's cooldown), whatever state the process is in.
        with mock.patch.object(server, "ensure_startup_started") as start:
            response = server.app.test_client().get("/live")
        self.assertEqual(response.status_code, 200)
        start.assert_not_called()

    def test_timestamps_carry_a_utc_offset(self):
        with mock.patch.object(server, "ensure_startup_started"):   # no background network startup
            response = server.app.test_client().get("/live")
        self.assertTrue(response.get_json()["timestamp"].endswith("+00:00"))


class DataPayloadIntervalTest(unittest.TestCase):
    """The /data payload must satisfy the deploy validator's own invariants in degraded runs."""

    QUOTE = 90.72000122070312        # a float32-like CL=F quote from Yahoo

    def _render(self, predictions, **contract_overrides):
        contract = {
            "symbol": "CLX26", "description": "WTI CRUDE OIL FUTURES CLX26",
            "current_price": self.QUOTE, "price_change": -1.1, "price_change_percent": -1.2,
            "price_change_quality": "daily_close", "volume": 1000,
            "market_time": "2026-10-12T05:55:00Z", "contract_basis": "calendar",
            **contract_overrides,
        }
        history = {"actual": {"timestamps": ["2026-10-12T05:00:00Z", "2026-10-12T05:56:02.819484Z"],
                              "values": [90.7, self.QUOTE], "volumes": [10, 0]}}
        now = 1_791_000_000.0
        with (
            mock.patch.object(server.time, "time", return_value=now),
            mock.patch.object(server, "ensure_startup_started"),
            mock.patch.object(server._startup_ready, "is_set", return_value=True),
            mock.patch.dict(server.system_state, {"ml_ready": True, "cached_predictions": predictions,
                                                  "last_prediction_time": now - 10}),
            mock.patch.object(server, "get_current_wti_contract", return_value=contract),
            mock.patch.object(server, "get_historical_data", return_value=history),
            mock.patch.object(server, "get_prediction_accuracy_metrics", return_value={}),
            mock.patch.object(server, "_load_walk_forward_stats", return_value={}),
            mock.patch.object(server, "_load_supply_shock_playbook", return_value=None),
            mock.patch.object(server, "_load_vol_forecast", return_value=None),
            mock.patch.object(server, "_load_live_record_summary", return_value=None),
        ):
            response = server.app.test_client().get("/data")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        data["frozen_at"] = "2026-10-12T05:56:05Z"      # stamped by freeze.py
        return data

    def test_rounded_fallback_forecast_stays_inside_its_interval(self):
        # Hourly data unavailable: oil.py derives 1h from 1d and spans it to the quote, so the
        # forecast (90.69177...) IS the lower bound. Published as 90.69, it used to sit below the
        # unrounded 90.69177 bound and fail the deploy ("predictions.1h lies outside its interval").
        pred_1d = 90.4377
        pred_1h = self.QUOTE + (pred_1d - self.QUOTE) * 0.1
        predictions = {
            "prediction_1h": pred_1h, "prediction_1d": pred_1d, "prediction_1w": self.QUOTE,
            "prediction_intervals": {
                "1h": PremiumWTIPredictor._fallback_span_interval(pred_1h, self.QUOTE),
                "1d": {"lower": pred_1d - 2.6, "upper": pred_1d + 2.6},
                # point fallback: forecast == quote == both bounds
                "1w": {"lower": self.QUOTE, "upper": self.QUOTE, "interval_method": "fallback_point"},
            },
            "is_real_prediction": True, "is_full_real_prediction": False,
            "fallbacks": {"1h": True, "1d": False, "1w": False},
            "geopolitical_risk": {}, "contract_metadata": {},
        }
        data = self._render(predictions)
        multi = data["multi_horizon_predictions"]
        self.assertEqual(multi["predictions"]["1h"], 90.69)
        self.assertEqual((multi["prediction_intervals"]["1h"]["lower"],
                          multi["prediction_intervals"]["1h"]["upper"]), (90.69, 90.72))
        self.assertEqual(multi["prediction_intervals"]["1h"]["interval_method"], "fallback_span")
        self.assertEqual((multi["prediction_intervals"]["1w"]["lower"],
                          multi["prediction_intervals"]["1w"]["upper"]), (90.72, 90.72))
        self.assertEqual(validate_payload(data), [])

    @staticmethod
    def _real_predictions(quote):
        return {
            "prediction_1h": quote, "prediction_1d": quote, "prediction_1w": quote,
            "prediction_intervals": {}, "is_real_prediction": True, "is_full_real_prediction": True,
            "fallbacks": {}, "geopolitical_risk": {}, "contract_metadata": {},
        }

    def test_a_change_withheld_by_oil_is_not_rebuilt_from_the_quote_history(self):
        # Weekday evenings (and around a roll) oil.py withholds the daily change on purpose; the
        # in-memory history fallback must not turn it back into a two-session or spliced change.
        for quality in ("unavailable_previous_settlement", "unavailable_contract_roll"):
            with self.subTest(quality=quality):
                data = self._render(self._real_predictions(self.QUOTE), price_change=None,
                                    price_change_percent=None, price_change_quality=quality)
                self.assertIsNone(data["price_change"])
                self.assertEqual(data["price_change_quality"], quality)

    def test_contract_basis_reaches_the_payload(self):
        # live_record roll-checks a quote-matched label by the label alone; it reads this field.
        data = self._render(self._real_predictions(self.QUOTE), contract_basis="quote_match",
                            quote_session_date="2026-10-12")
        self.assertEqual(data["contract"]["contract_basis"], "quote_match")
        self.assertEqual(data["contract"]["quote_session_date"], "2026-10-12")


class WalkForwardConfigTest(unittest.TestCase):
    """Only a report from the live model's configuration may decide wf_is_significant."""

    def _load(self, config, significant=True):
        report = {"config": config, "results": {"1w": {"metrics": {"ensemble": {
            "samples": 199, "direction_accuracy": 58.0, "direction_p_value": 0.01,
            "direction_ci_95": [51.0, 65.0], "is_significant_5pct": significant, "mae": 1.0,
        }}}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "walk_forward_backtest_latest.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with mock.patch.object(server, "WALK_FORWARD_STATS_PATH", path):
                return server._load_walk_forward_stats()

    VALIDATED = {"feature_mode": "no_macro", "context_lag_days": 1, "train_window": 0,
                 "purge_rows_by_horizon": {"1d": 0, "1w": 4}}

    def test_validated_configurations_are_loaded(self):
        for train_window in (0, 378):           # committed 5-year expanding run; production rolling
            with self.subTest(train_window=train_window):
                stats = self._load({**self.VALIDATED, "train_window": train_window})
                self.assertTrue(stats["1w"]["is_significant_5pct"])

    def test_comparison_runs_cannot_flip_significance(self):
        # README's comparison commands write the same default file; none of them is the live model.
        for change in ({"feature_mode": "all"}, {"feature_mode": "price_only"},
                       {"context_lag_days": 0}, {"train_window": 126},
                       {"purge_rows_by_horizon": {"1d": 0, "1w": 0}},      # --no-purge: the leak
                       {"purge_rows_by_horizon": None}):                   # predates the purge record
            config = {**self.VALIDATED, **change}
            with self.subTest(change=change):
                with self.assertLogs(server.logger, "WARNING") as logs:
                    self.assertEqual(self._load(config), {})
                self.assertIn("not the validated configuration", logs.output[0])
        # Ignored stats publish no significance flag, so the dashboard keeps the 1W retraction.
        with mock.patch.object(server, "_load_walk_forward_stats", return_value={}):
            metrics, _ = server._build_horizon_metrics({}, {}, {}, {}, 18)
        self.assertIsNone(metrics["1w"]["wf_is_significant"])

    def test_the_committed_dashboard_artifact_is_accepted(self):
        report = json.loads(server.WALK_FORWARD_STATS_PATH.read_text(encoding="utf-8"))
        self.assertIsNone(server._walk_forward_config_mismatch(report["config"]))

    def test_reports_without_a_train_window_or_config_are_rejected(self):
        # e.g. the leaked, unpurged run, which predates --train-window.
        legacy = {"feature_mode": "no_macro", "context_lag_days": 1}
        for config in (legacy, None):
            with self.subTest(config=config), self.assertLogs(server.logger, "WARNING"):
                self.assertEqual(self._load(config), {})


class PlaybookCacheTest(unittest.TestCase):
    def setUp(self):
        server._PLAYBOOK_CACHE.clear()
        self.addCleanup(server._PLAYBOOK_CACHE.clear)

    def test_cache_is_keyed_on_the_drivers_it_ranks_by(self):
        calls = []

        def fake(current_drivers=None):
            calls.append(tuple(current_drivers))
            return {"ranked_for": tuple(current_drivers)}

        with mock.patch("backend.supply_shock_playbook.get_playbook_for_api", side_effect=fake):
            weather = server._load_supply_shock_playbook(["weather"])
            iran = server._load_supply_shock_playbook(["iran", "conflict"])
            again = server._load_supply_shock_playbook(["conflict", "iran"])
        self.assertEqual(weather["ranked_for"], ("weather",))
        self.assertEqual(iran["ranked_for"], ("conflict", "iran"))
        self.assertIs(again, iran)                    # same drivers, any order: cache hit
        self.assertEqual(len(calls), 2)

    def test_failures_are_cached_briefly_and_last_good_build_is_kept(self):
        with mock.patch("backend.supply_shock_playbook.get_playbook_for_api", return_value=None) as build:
            self.assertIsNone(server._load_supply_shock_playbook(["weather"]))
            self.assertIsNone(server._load_supply_shock_playbook(["weather"]))
        self.assertEqual(build.call_count, 1)         # negative cache: no retry storm on EIA

        server._PLAYBOOK_CACHE.clear()
        good = {"event_count": 35}
        with mock.patch("backend.supply_shock_playbook.get_playbook_for_api", return_value=good):
            server._load_supply_shock_playbook(["iran"])
        server._PLAYBOOK_CACHE[("iran",)]["built_at"] = 0.0          # expire it
        with mock.patch("backend.supply_shock_playbook.get_playbook_for_api", side_effect=RuntimeError):
            self.assertEqual(server._load_supply_shock_playbook(["iran"]), good)


class VolForecastCacheTest(unittest.TestCase):
    def setUp(self):
        server._VOL_CACHE.update(data=None, built_at=0.0, failed_at=0.0)
        self.addCleanup(server._VOL_CACHE.update, data=None, built_at=0.0, failed_at=0.0)

    def test_built_once_then_served_from_cache(self):
        bundle = {"live": {}, "validation": {"n": 1}}
        with mock.patch("backend.vol_forecast.forecast_bundle", return_value=bundle) as build:
            self.assertIs(server._load_vol_forecast(), bundle)
            self.assertIs(server._load_vol_forecast(), bundle)
        self.assertEqual(build.call_count, 1)

    def test_failure_backs_off_instead_of_retrying_every_request(self):
        with mock.patch("backend.vol_forecast.forecast_bundle", side_effect=RuntimeError) as build:
            self.assertIsNone(server._load_vol_forecast())
            self.assertIsNone(server._load_vol_forecast())
        self.assertEqual(build.call_count, 1)


if __name__ == "__main__":
    unittest.main()
