import copy
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from scripts.validate_frozen_payload import validate_payload


class FrozenPayloadValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = Path(__file__).parent / "fixtures" / "valid_frozen_payload.json"
        cls.valid = json.loads(fixture.read_text(encoding="utf-8"))

    def test_valid_fixture_satisfies_render_invariants(self):
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


    def test_null_daily_change_is_allowed_but_strings_are_not(self):
        payload = copy.deepcopy(self.valid)
        payload["price_change_percent"] = None
        self.assertEqual(validate_payload(payload), [])
        payload["price_change_percent"] = "-2.40"      # blanked the whole page before the UI fix
        self.assertIn("price_change_percent must be a finite number or null", validate_payload(payload))

    def test_null_unified_data_is_reported_not_a_crash(self):
        payload = copy.deepcopy(self.valid)
        payload["unified_data"] = None
        self.assertIn("unified_data.actual must be an object", validate_payload(payload))

    def test_rejects_price_inconsistent_with_chart(self):
        payload = copy.deepcopy(self.valid)
        payload["current_price"] = self.valid["current_price"] * 10
        errors = validate_payload(payload)
        self.assertIn("current_price disagrees with the latest market bar by more than 5%", errors)

    def test_chart_check_skips_the_quote_appended_as_a_live_point(self):
        # As in every deployed snapshot, the server appends the quote it forecast from (stamped when
        # it was stored) after the Yahoo bars, so the old check compared 100.30 with itself and
        # passed. The last real bar (here from a series 6% away) is what has to agree with the quote.
        payload = copy.deepcopy(self.valid)
        payload["current_price"] = 100.3
        payload["contract"]["market_time"] = "2026-09-18T21:00:00Z"
        payload["unified_data"]["actual"] = {
            "timestamps": ["2026-09-18T19:00:00Z", "2026-09-18T20:00:00Z", "2026-09-19T04:37:21.744563Z"],
            "values": [94.6, 94.3, 100.3],
            "volumes": [10, 12, 0],
        }
        errors = validate_payload(payload)
        self.assertIn("current_price disagrees with the latest market bar by more than 5%", errors)
        payload["unified_data"]["actual"]["values"][1] = 100.1     # a chart that agrees passes
        self.assertNotIn("current_price disagrees with the latest market bar by more than 5%",
                         validate_payload(payload))

    def test_chart_check_skips_live_points_by_kind(self):
        # A live quote stamped before the quote's exchange time (e.g. a stored tick from earlier in
        # the session) is still not a market bar; the payload's kinds say so.
        payload = copy.deepcopy(self.valid)
        payload["current_price"] = 100.3
        payload["contract"]["market_time"] = "2026-09-18T21:00:00Z"
        payload["unified_data"]["actual"] = {
            "timestamps": ["2026-09-18T19:00:00Z", "2026-09-18T20:00:00Z", "2026-09-18T20:30:05Z"],
            "values": [94.6, 94.3, 100.2],
            "volumes": [10, 12, 0],
            "kinds": ["1h", "1h", "live"],
        }
        self.assertIn("current_price disagrees with the latest market bar by more than 5%", validate_payload(payload))
        payload["unified_data"]["actual"]["kinds"] = ["1h", "1h", "1h"]
        self.assertNotIn("current_price disagrees with the latest market bar by more than 5%",
                         validate_payload(payload))

    def test_chart_kinds_must_be_aligned_and_known(self):
        payload = copy.deepcopy(self.valid)
        actual = payload["unified_data"]["actual"]
        for kinds in (["1d"], ["minute"] * len(actual["values"]), "1d"):
            actual["kinds"] = kinds
            with self.subTest(kinds=kinds):
                self.assertTrue(any("actual kinds" in e for e in validate_payload(payload)))
        actual["kinds"] = ["1d"] * len(actual["values"])
        self.assertFalse(any("actual kinds" in e for e in validate_payload(payload)))

    def test_chart_without_market_bars_is_rejected(self):
        payload = copy.deepcopy(self.valid)
        payload["contract"]["market_time"] = "2026-01-15T10:00:00Z"   # before every chart point
        self.assertIn("the chart has no market bar at or before the quote time", validate_payload(payload))

    def test_chart_check_needs_the_quote_time(self):
        payload = copy.deepcopy(self.valid)
        del payload["contract"]["market_time"]
        payload["current_price"] = self.valid["current_price"] * 10
        notes = []
        self.assertNotIn("current_price disagrees with the latest market bar by more than 5%",
                         validate_payload(payload, warnings=notes))
        self.assertTrue(any("the chart was not checked" in note for note in notes))

    def test_never_older_than_the_published_snapshot(self):
        # The publish job passes the live site's frozen_at: a re-run of an old run must not roll it back.
        frozen = self.valid["frozen_at"]
        self.assertEqual(validate_payload(self.valid, not_older_than=frozen), [])   # same snapshot
        self.assertEqual(validate_payload(self.valid, not_older_than="2026-01-15T11:00:00+00:00"), [])
        self.assertIn("frozen_at is older than the snapshot already published",
                      validate_payload(self.valid, not_older_than="2026-01-15T16:17:00Z"))
        self.assertIn("the published snapshot's frozen_at is not an aware ISO-8601 timestamp",
                      validate_payload(self.valid, not_older_than="2026-01-15 16:17"))

    def test_rejects_percentage_that_contradicts_its_prediction(self):
        payload = copy.deepcopy(self.valid)
        payload["multi_horizon_predictions"]["percentage_changes"]["1w"] = 25.0
        self.assertIn("percentage_changes.1w disagrees with its prediction", validate_payload(payload))

    def test_rejects_significance_flag_that_contradicts_p_value(self):
        # A flipped flag would publish a LONG/SHORT lean for a model with p = 0.71.
        payload = copy.deepcopy(self.valid)
        payload["performance_metrics"]["by_horizon"]["1w"]["wf_is_significant"] = True
        self.assertIn("wf_is_significant contradicts wf_p_value", validate_payload(payload))

    def test_market_data_freshness_uses_the_quote_time(self):
        payload = copy.deepcopy(self.valid)
        payload["contract"]["market_time"] = "2025-11-15T12:00:00Z"   # two months stale
        errors = validate_payload(payload, max_market_age_days=4)
        self.assertIn("quote is more than 4 days older than the snapshot", errors)
        self.assertEqual(validate_payload(self.valid, max_market_age_days=4), [])

    def test_vol_card_is_optional_but_validated_when_present(self):
        payload = copy.deepcopy(self.valid)
        del payload["vol_forecast"]
        notes = []
        self.assertEqual(validate_payload(payload, warnings=notes), [])
        self.assertTrue(any("vol_forecast is absent" in n for n in notes))
        payload = copy.deepcopy(self.valid)
        payload["vol_forecast"]["validation"]["har_dir_acc_pct"] = 172.0
        self.assertIn("vol_forecast.validation.har_dir_acc_pct must be a percentage",
                      validate_payload(payload))


if __name__ == "__main__":
    unittest.main()
