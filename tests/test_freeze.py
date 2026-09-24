"""Network-free tests for freeze.py, the entry point of every dashboard deploy.

The server is stubbed: these tests pin what freeze.py itself guarantees about the files it
publishes, not the pipeline behind /data.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import freeze
from backend import server

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "valid_frozen_payload.json").read_text(encoding="utf-8")
)


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def get_json(self):
        return copy.deepcopy(self._payload)

    def get_data(self, as_text=False):
        return json.dumps(self._payload)


class FreezeTest(unittest.TestCase):
    def _freeze(self, payload, bundle=None, bundle_error=None):
        client = mock.Mock()
        client.get.return_value = _Response(payload)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(server, "initialize_oil_system"), \
                mock.patch.object(server.app, "test_client", return_value=client), \
                mock.patch("backend.vol_forecast.forecast_bundle",
                           side_effect=bundle_error, return_value=bundle):
            out = Path(tmp)
            freeze.freeze(out)
            data = json.loads((out / "data.json").read_text(encoding="utf-8"))
            price = json.loads((out / "price.json").read_text(encoding="utf-8"))
        return data, price

    def test_snapshot_is_labelled_and_timestamped(self):
        data, _ = self._freeze(FIXTURE)
        self.assertEqual(data["feed_status"], "SNAPSHOT")
        self.assertTrue(data["frozen_at"].endswith("+00:00"))

    def test_failed_vol_build_never_publishes_a_partial_card(self):
        payload = copy.deepcopy(FIXTURE)
        payload["vol_forecast"] = None                      # the server could not build it either
        data, _ = self._freeze(payload, bundle_error=RuntimeError("yfinance down"))
        self.assertNotIn("vol_forecast", data)

    def test_server_vol_card_is_reused_without_a_second_download(self):
        with mock.patch("backend.vol_forecast.forecast_bundle") as build:
            client = mock.Mock()
            client.get.return_value = _Response(FIXTURE)
            with tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(server, "initialize_oil_system"), \
                    mock.patch.object(server.app, "test_client", return_value=client):
                data = freeze.freeze(Path(tmp))
        build.assert_not_called()
        self.assertEqual(data["vol_forecast"], FIXTURE["vol_forecast"])

    def test_baked_price_carries_market_time_and_honest_previous_close(self):
        _, price = self._freeze(FIXTURE)
        self.assertEqual(price["price"], FIXTURE["current_price"])
        self.assertEqual(price["market_time"], FIXTURE["contract"]["market_time"])
        self.assertAlmostEqual(price["prev_close"], FIXTURE["current_price"] - FIXTURE["price_change"], places=2)

        unknown = copy.deepcopy(FIXTURE)
        unknown["price_change"] = unknown["price_change_percent"] = None
        _, price = self._freeze(unknown)
        self.assertIsNone(price["prev_close"])              # never price - 0 == a fake flat day
        self.assertIsNone(price["change_pct"])


if __name__ == "__main__":
    unittest.main()
