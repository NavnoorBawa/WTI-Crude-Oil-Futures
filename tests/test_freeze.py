"""Network-free tests for freeze.py, the entry point of every dashboard deploy.

The server is stubbed: these tests pin what freeze.py itself guarantees about the files it
publishes, not the pipeline behind /data.
"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import freeze
from backend import server
from scripts.validate_frozen_payload import validate_payload, validate_research

ROOT = Path(__file__).resolve().parents[1]
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
    def _run(self, payload, out, bundle=None, bundle_error=None):
        client = mock.Mock()
        client.get.return_value = _Response(payload)
        with mock.patch.object(server, "initialize_oil_system"), \
                mock.patch.object(server.app, "test_client", return_value=client), \
                mock.patch("backend.vol_forecast.forecast_bundle",
                           side_effect=bundle_error, return_value=bundle) as build:
            self.build = build
            return freeze.freeze(out)

    def _freeze(self, payload, bundle=None, bundle_error=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._run(payload, out, bundle, bundle_error)
            files = {path.name: path.read_text(encoding="utf-8") for path in out.iterdir()}
        return files

    def test_snapshot_is_labelled_timestamped_and_valid(self):
        files = self._freeze(FIXTURE)
        data = json.loads(files["data.json"])
        self.assertEqual(data["feed_status"], "SNAPSHOT")
        self.assertTrue(data["frozen_at"].endswith("+00:00"))
        self.assertEqual(data["schema_version"], 3)
        self.assertEqual(validate_payload(data), [])

    def test_data_json_is_compact(self):
        raw = self._freeze(FIXTURE)["data.json"]
        self.assertNotIn("\n", raw)
        self.assertEqual(raw, json.dumps(json.loads(raw), separators=(",", ":")))

    def test_research_json_is_written_from_the_committed_artifacts(self):
        files = self._freeze(FIXTURE)
        self.assertEqual(set(files), {"data.json", "research.json", "price.json"})
        research = json.loads(files["research.json"])
        self.assertEqual(research["schema_version"], 1)
        self.assertEqual(validate_research(research), [])

    def test_a_failed_vol_build_fails_the_freeze_and_writes_nothing(self):
        # The forecast is the product: without it the previous good site must stay live.
        payload = copy.deepcopy(FIXTURE)
        payload["vol_forecast"] = None                      # the server could not build it either
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "public"
            with self.assertRaises(SystemExit) as raised:
                self._run(payload, out, bundle_error=RuntimeError("yfinance down"))
            self.assertFalse(out.exists() and any(out.iterdir()))
        self.assertIn("vol_forecast unavailable (error_type=RuntimeError)", str(raised.exception))
        self.build.assert_called_once_with()               # one more attempt after the server's

    def test_an_incomplete_vol_bundle_is_refused(self):
        payload = copy.deepcopy(FIXTURE)
        payload["vol_forecast"] = {"live": {"direction": "RISING"}}      # no validation block
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit) as raised:
            self._run(payload, Path(tmp))
        self.assertIn("vol_forecast is incomplete", str(raised.exception))

    def test_server_vol_card_is_reused_without_a_second_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._run(FIXTURE, Path(tmp))
        self.build.assert_not_called()
        self.assertEqual(data["vol_forecast"], FIXTURE["vol_forecast"])

    def test_vol_forecast_is_built_here_when_the_server_had_none(self):
        payload = copy.deepcopy(FIXTURE)
        payload["vol_forecast"] = None
        with tempfile.TemporaryDirectory() as tmp:
            data = self._run(payload, Path(tmp), bundle=FIXTURE["vol_forecast"])
        self.build.assert_called_once_with()
        self.assertEqual(data["vol_forecast"], FIXTURE["vol_forecast"])

    def test_a_broken_research_artifact_fails_the_freeze_before_data_json(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(freeze, "_research_builder") as builder:
            builder.return_value.write.side_effect = ValueError("research bundle failed validation")
            with self.assertRaises(ValueError):
                self._run(FIXTURE, Path(tmp))
            self.assertFalse((Path(tmp) / "data.json").exists())

    def test_an_error_payload_fails_the_freeze(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            self._run({"error": "DATA_UNAVAILABLE"}, Path(tmp))

    def test_baked_price_carries_market_time_and_honest_previous_close(self):
        price = json.loads(self._freeze(FIXTURE)["price.json"])
        self.assertEqual(price["price"], FIXTURE["current_price"])
        self.assertEqual(price["market_time"], FIXTURE["contract"]["market_time"])
        # The UI overlays a quote only on a snapshot of the same contract.
        self.assertEqual(price["contract"], FIXTURE["contract"]["symbol"])
        self.assertAlmostEqual(price["prev_close"], FIXTURE["current_price"] - FIXTURE["price_change"], places=2)

        unknown = copy.deepcopy(FIXTURE)
        unknown["price_change"] = unknown["price_change_percent"] = None
        price = json.loads(self._freeze(unknown)["price.json"])
        self.assertIsNone(price["prev_close"])              # never price - 0 == a fake flat day
        self.assertIsNone(price["change_pct"])

    def test_freeze_writes_only_to_public(self):
        result = subprocess.run(  # nosec B603 - fixed argv: this interpreter and the repository's script
            [sys.executable, "freeze.py", "--out", "dist"],
            cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Freeze failed error_type=ValueError", result.stderr)


if __name__ == "__main__":
    unittest.main()
