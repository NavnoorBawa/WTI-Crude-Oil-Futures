"""Network-free tests for backend/server.py readiness semantics and the playbook cache."""

import unittest
from unittest import mock

from backend import server


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

    def test_timestamps_carry_a_utc_offset(self):
        with mock.patch.object(server, "ensure_startup_started"):   # no background network startup
            response = server.app.test_client().get("/live")
        self.assertTrue(response.get_json()["timestamp"].endswith("+00:00"))


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
