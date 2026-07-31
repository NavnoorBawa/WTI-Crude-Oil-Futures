import copy
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from scripts.validate_frozen_payload import validate_payload


class FrozenPayloadValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.valid = json.loads(Path("public/data.json").read_text(encoding="utf-8"))

    def test_repository_payload_satisfies_render_invariants(self):
        self.assertEqual(validate_payload(self.valid), [])

    def test_rejects_string_and_non_finite_prices(self):
        for bad_price in ("76.54", float("nan"), float("inf"), -1.0):
            payload = copy.deepcopy(self.valid)
            payload["current_price"] = bad_price
            with self.subTest(bad_price=bad_price):
                self.assertIn(
                    "current_price must be a finite positive number",
                    validate_payload(payload),
                )

    def test_rejects_misaligned_chart_arrays(self):
        payload = copy.deepcopy(self.valid)
        payload["unified_data"]["actual"]["values"].pop()
        self.assertIn(
            "actual timestamps, values, and volumes must be aligned",
            validate_payload(payload),
        )

    def test_freshness_gate_rejects_an_old_snapshot(self):
        frozen = datetime.fromisoformat(self.valid["frozen_at"].replace("Z", "+00:00"))
        errors = validate_payload(
            self.valid,
            now=frozen + timedelta(minutes=31),
            max_age_minutes=30,
        )
        self.assertIn("frozen_at is older than 30 minutes", errors)


if __name__ == "__main__":
    unittest.main()
