"""Network-free tests for backend/server.py: the schema_version 3 payload, readiness, and caches."""

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from backend import oil, server
from scripts.validate_frozen_payload import validate_payload

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "valid_frozen_payload.json").read_text(encoding="utf-8")
)
# docs/DATA_CONTRACT.md data.json, as /data renders it (freeze.py adds frozen_at and sets feed_status).
CONTRACT_KEYS = {
    "schema_version", "last_update", "feed_status", "contract", "current_price", "price_change",
    "price_change_percent", "price_change_quality", "volume", "volume_display", "unified_data",
    "vol_forecast", "supply_shock_playbook", "retracted_direction_model",
}
CONTRACT_BLOCK_KEYS = {
    "symbol", "description", "expiry_date", "last_trade_date", "days_to_expiry", "market_time",
    "contract_basis", "quote_symbol", "history_symbol", "market_session_date", "quote_session_date",
}


def _contract(**overrides):
    return {
        "symbol": "CLZ26", "description": "WTI CRUDE OIL FUTURES CLZ26", "yfinance_symbol": "CL=F",
        "history_symbol": "CL=F", "current_price": 100.80000305175781, "price_change": 2.26,
        "price_change_percent": 2.29, "price_change_quality": "daily_close_new_contract", "volume": 15800,
        "expiry_date": "2026-11-20", "contract_last_trade_date": "2026-11-20", "days_to_expiry": 31,
        "market_time": "2026-10-20T11:51:46Z", "contract_basis": "quote_match",
        "market_session_date": "2026-10-20", "quote_session_date": "2026-10-20",
        **overrides,
    }


def _history(live_quote=None):
    """The fixture's chart bars, then the quote as get_historical_data appends it."""
    actual = copy.deepcopy(FIXTURE["unified_data"]["actual"])
    for name in ("values", "timestamps", "volumes", "kinds"):
        actual[name] = actual[name][:-1]
    if live_quote is not None:
        stamp, price, volume = live_quote
        actual["values"].append(round(price, 4))
        actual["timestamps"].append(stamp)
        actual["volumes"].append(volume)
        actual["kinds"].append("live")
    return {"actual": actual}


class DirectionModelNeverRunsMixin:
    """Any construction or use of the retracted model, or a NewsAPI call, fails the test."""

    def setUp(self):
        super().setUp()
        for target in (
            mock.patch.object(oil.PremiumWTIPredictor, "__init__", side_effect=AssertionError("model constructed")),
            mock.patch.object(oil.PremiumWTIPredictor, "get_multi_horizon_predictions",
                              side_effect=AssertionError("direction model run")),
            mock.patch.object(oil.PremiumWTIPredictor, "get_geopolitical_risk",
                              side_effect=AssertionError("NewsAPI called")),
            mock.patch.object(oil.requests, "get", side_effect=AssertionError("external HTTP call")),
        ):
            target.start()
            self.addCleanup(target.stop)


class DataPayloadTest(DirectionModelNeverRunsMixin, unittest.TestCase):
    """/data renders docs/DATA_CONTRACT.md data.json schema_version 3, and nothing else."""

    def setUp(self):
        super().setUp()
        server._data_request_times.clear()          # the per-client rate limit is process-wide

    def _render(self, vol=FIXTURE["vol_forecast"], playbook=FIXTURE["supply_shock_playbook"], **contract):
        history = mock.Mock(side_effect=_history)
        with (
            mock.patch.object(server, "ensure_startup_started"),
            mock.patch.object(server._startup_ready, "is_set", return_value=True),
            mock.patch.object(server, "get_current_wti_contract", return_value=_contract(**contract)),
            mock.patch.object(server, "get_historical_data", history),
            mock.patch.object(server, "_load_vol_forecast", return_value=vol),
            mock.patch.object(server, "_load_supply_shock_playbook", return_value=playbook),
        ):
            response = server.app.test_client().get("/data")
        self.assertEqual(response.status_code, 200)
        self.history_calls = history.call_args_list
        return response.get_json()

    def test_payload_is_exactly_the_v3_contract(self):
        data = self._render()
        self.assertEqual(set(data), CONTRACT_KEYS)
        self.assertEqual(set(data["contract"]), CONTRACT_BLOCK_KEYS)
        self.assertEqual(set(data["unified_data"]), {"actual"})
        self.assertEqual(data["schema_version"], 3)
        self.assertEqual(data["retracted_direction_model"],
                         {"status": "RETRACTED", "reason": "look-ahead leak; purged, the signal is a coin flip",
                          "evidence": "research.json postmortem"})
        self.assertEqual(data["vol_forecast"], FIXTURE["vol_forecast"])
        self.assertEqual(data["current_price"], 100.8)
        self.assertEqual(data["volume_display"], "15.8K")
        self.assertEqual(data["contract"]["quote_symbol"], "CL=F")
        self.assertEqual(data["contract"]["last_trade_date"], "2026-11-20")
        self.assertTrue(data["last_update"].endswith("+00:00"))

    def test_frozen_payload_passes_the_deploy_validator(self):
        data = self._render()
        data["frozen_at"] = "2026-10-20T12:02:00+00:00"      # stamped by freeze.py
        data["feed_status"] = "SNAPSHOT"
        self.assertEqual(validate_payload(data, max_market_age_days=4), [])

    def test_chart_ends_with_the_quote_at_its_exchange_time(self):
        data = self._render()
        (args, kwargs), = [tuple(call) for call in self.history_calls]
        self.assertEqual(kwargs["live_quote"], ("2026-10-20T11:51:46Z", 100.80000305175781, 15800))
        actual = data["unified_data"]["actual"]
        self.assertEqual((actual["kinds"][-1], actual["timestamps"][-1]), ("live", "2026-10-20T11:51:46Z"))

    def test_contract_basis_reaches_the_payload(self):
        data = self._render(contract_basis="calendar", quote_session_date="2026-10-21")
        self.assertEqual(data["contract"]["contract_basis"], "calendar")
        self.assertEqual(data["contract"]["quote_session_date"], "2026-10-21")

    def test_a_change_withheld_by_oil_stays_null(self):
        # Weekday evenings (and around a roll) oil.py withholds the daily change on purpose.
        for quality in ("unavailable_previous_settlement", "unavailable_contract_roll"):
            with self.subTest(quality=quality):
                data = self._render(price_change=None, price_change_percent=None, price_change_quality=quality)
                self.assertIsNone(data["price_change"])
                self.assertIsNone(data["price_change_percent"])
                self.assertEqual(data["price_change_quality"], quality)
        # A null change is never labelled as a daily close.
        data = self._render(price_change=None, price_change_percent=None, price_change_quality="daily_close")
        self.assertEqual(data["price_change_quality"], "unavailable")

    def test_optional_playbook_and_a_missing_vol_forecast_are_published_as_null(self):
        # The dev server degrades; freeze.py and the deploy validator refuse a snapshot without vol.
        data = self._render(vol=None, playbook=None)
        self.assertIsNone(data["vol_forecast"])
        self.assertIsNone(data["supply_shock_playbook"])
        data["frozen_at"] = "2026-10-20T12:02:00+00:00"
        self.assertIn("vol_forecast is required", validate_payload(data))

    def test_contract_failure_is_a_503_without_details(self):
        with (
            mock.patch.object(server, "ensure_startup_started"),
            mock.patch.object(server._startup_ready, "is_set", return_value=True),
            mock.patch.object(server, "get_current_wti_contract", side_effect=RuntimeError("secret detail")),
        ):
            response = server.app.test_client().get("/data")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"], "DATA_UNAVAILABLE")
        self.assertNotIn("secret detail", response.get_data(as_text=True))

    def test_no_direction_model_entry_point_is_left_in_the_server(self):
        for name in ("get_multi_horizon_wti_predictions", "get_prediction_accuracy_metrics",
                     "get_cached_ml_data", "update_predictions", "_load_walk_forward_stats",
                     "_load_live_record_summary", "VALIDATED_WALK_FORWARD_CONFIG"):
            self.assertFalse(hasattr(server, name), name)


class InitializationTest(DirectionModelNeverRunsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        server._VOL_CACHE.update(data=None, built_at=0.0, failed_at=0.0)
        self.addCleanup(server._VOL_CACHE.update, data=None, built_at=0.0, failed_at=0.0)
        self.addCleanup(server.system_state.update, initialized=False)

    def test_initialization_fetches_the_quote_and_builds_the_vol_forecast_only(self):
        with (
            mock.patch.object(server, "get_current_wti_contract", return_value=_contract()) as quote,
            mock.patch("backend.vol_forecast.forecast_bundle", return_value=FIXTURE["vol_forecast"]) as build,
        ):
            self.assertTrue(server.initialize_oil_system())
        quote.assert_called_once_with()
        build.assert_called_once_with()
        self.assertTrue(server.system_state["initialized"])
        self.assertIs(server._VOL_CACHE["data"], FIXTURE["vol_forecast"])

    def test_no_quote_fails_initialization(self):
        with mock.patch.object(server, "get_current_wti_contract", side_effect=RuntimeError("yahoo down")), \
                self.assertRaises(RuntimeError):
            server.initialize_oil_system()
        self.assertFalse(server.system_state["initialized"])

    def test_a_vol_failure_does_not_fail_the_dev_server_startup(self):
        with (
            mock.patch.object(server, "get_current_wti_contract", return_value=_contract()),
            mock.patch("backend.vol_forecast.forecast_bundle", side_effect=RuntimeError("ovx down")),
            self.assertLogs(server.logger, "WARNING"),
        ):
            self.assertTrue(server.initialize_oil_system())
        self.assertIsNone(server._VOL_CACHE["data"])


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        server._VOL_CACHE.update(data=None, built_at=0.0, failed_at=0.0)
        self.addCleanup(server._VOL_CACHE.update, data=None, built_at=0.0, failed_at=0.0)

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

    def test_ready_means_the_volatility_forecast_is_available(self):
        server._VOL_CACHE.update(data={"live": {}}, built_at=1_791_000_000.0)
        with (
            mock.patch.object(server, "ensure_startup_started"),
            mock.patch.object(server._startup_ready, "is_set", return_value=True),
            mock.patch.object(server, "get_current_wti_contract") as upstream,
        ):
            client = server.app.test_client()
            root, health = client.get("/"), client.get("/health")
        self.assertEqual((root.status_code, health.status_code), (200, 200))
        self.assertEqual(root.get_json()["status"], "ACTIVE")
        self.assertEqual(health.get_json()["status"], "HEALTHY")
        self.assertTrue(health.get_json()["vol_forecast_ready"])
        self.assertTrue(health.get_json()["vol_forecast_built_at"].endswith("+00:00"))
        upstream.assert_not_called()                       # readiness reads cached state only

    def test_every_not_ready_503_carries_retry_after(self):
        # Startup done, but no volatility forecast (the product): / and /health are 503 DEGRADED.
        with (
            mock.patch.object(server, "ensure_startup_started"),
            mock.patch.object(server._startup_ready, "is_set", return_value=True),
        ):
            client = server.app.test_client()
            for path in ("/", "/health"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.get_json()["status"], "DEGRADED")
                    self.assertFalse(response.get_json()["vol_forecast_ready"])
                    self.assertGreaterEqual(int(response.headers["Retry-After"]), 1)

    def test_health_has_security_headers_and_no_cors_for_other_origins(self):
        with (
            mock.patch.object(server, "ensure_startup_started"),
            mock.patch.object(server._startup_ready, "is_set", return_value=False),
        ):
            response = server.app.test_client().get("/health", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

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

    def test_background_startup_retries_after_a_cooldown(self):
        with (
            mock.patch.object(server, "initialize_oil_system", side_effect=RuntimeError("no quote")),
            mock.patch.object(server, "_startup_ready") as ready,
            mock.patch.object(server, "_startup_next_retry_at", 0.0),
            self.assertLogs(server.logger, "CRITICAL"),
        ):
            server.startup_initialization()
            ready.set.assert_not_called()
            self.assertGreater(server._startup_next_retry_at, 0.0)


class PlaybookCacheTest(unittest.TestCase):
    def setUp(self):
        server._PLAYBOOK_CACHE.update(data=None, built_at=0.0, failed_at=0.0)
        self.addCleanup(server._PLAYBOOK_CACHE.update, data=None, built_at=0.0, failed_at=0.0)

    def test_built_once_without_news_drivers_then_served_from_cache(self):
        good = {"event_count": 35}
        with mock.patch("backend.supply_shock_playbook.get_playbook_for_api", return_value=good) as build:
            self.assertIs(server._load_supply_shock_playbook(), good)
            self.assertIs(server._load_supply_shock_playbook(), good)
        build.assert_called_once_with()               # ranked by recency: no news feed is read

    def test_failures_are_cached_briefly_and_last_good_build_is_kept(self):
        with mock.patch("backend.supply_shock_playbook.get_playbook_for_api", return_value=None) as build:
            self.assertIsNone(server._load_supply_shock_playbook())
            self.assertIsNone(server._load_supply_shock_playbook())
        self.assertEqual(build.call_count, 1)         # negative cache: no retry storm on EIA

        server._PLAYBOOK_CACHE.update(failed_at=0.0)
        good = {"event_count": 35}
        with mock.patch("backend.supply_shock_playbook.get_playbook_for_api", return_value=good):
            server._load_supply_shock_playbook()
        server._PLAYBOOK_CACHE["built_at"] = 0.0      # expire it
        with mock.patch("backend.supply_shock_playbook.get_playbook_for_api", side_effect=RuntimeError):
            self.assertEqual(server._load_supply_shock_playbook(), good)


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
        with mock.patch("backend.vol_forecast.forecast_bundle", side_effect=RuntimeError) as build, \
                self.assertLogs(server.logger, "WARNING"):
            self.assertIsNone(server._load_vol_forecast())
            self.assertIsNone(server._load_vol_forecast())
        self.assertEqual(build.call_count, 1)

    def test_an_expired_bundle_is_served_while_its_rebuild_fails(self):
        bundle = {"live": {}, "validation": {"n": 1}}
        server._VOL_CACHE.update(data=bundle, built_at=0.0)      # expired
        with mock.patch("backend.vol_forecast.forecast_bundle", side_effect=RuntimeError) as build, \
                self.assertLogs(server.logger, "WARNING"):
            self.assertIs(server._load_vol_forecast(), bundle)
        build.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
