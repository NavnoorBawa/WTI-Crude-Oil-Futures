import copy
import json
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from backend import vol_record
from scripts.validate_frozen_payload import (
    LIVE_RECORD_STATUSES,
    RETRACTED_MODEL_FIELDS,
    research_payload_path,
    validate_payload,
    validate_research,
)

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]


class FrozenPayloadValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A real schema_version 3 freeze (python freeze.py --out public) with the chart trimmed.
        cls.valid = json.loads((FIXTURES / "valid_frozen_payload.json").read_text(encoding="utf-8"))

    def mutated(self, mutate):
        payload = copy.deepcopy(self.valid)
        mutate(payload)
        return payload

    def test_valid_fixture_satisfies_render_invariants(self):
        notes = []
        self.assertEqual(validate_payload(self.valid, warnings=notes), [])
        self.assertEqual(notes, [])
        # No direction-model output is needed (or allowed): the v2 hard gate is gone.
        self.assertNotIn("multi_horizon_predictions", self.valid)

    def test_schema_version_must_be_3(self):
        for version in (None, 2, "3", 3.5):
            with self.subTest(version=version):
                payload = self.mutated(lambda p: p.update(schema_version=version))
                self.assertIn("schema_version must be 3", validate_payload(payload))

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
        self.assertEqual(validate_payload(self.valid, now=frozen + timedelta(minutes=29), max_age_minutes=30), [])

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

    def test_the_fixture_chart_ends_with_the_quote_as_a_live_point(self):
        actual = self.valid["unified_data"]["actual"]
        self.assertEqual(actual["kinds"][-1], "live")
        self.assertEqual(actual["values"][-1], self.valid["current_price"])
        self.assertEqual({"1d", "1h"}, set(actual["kinds"][:-1]))

    def test_chart_check_skips_the_quote_appended_as_a_live_point(self):
        # The server appends the quote after the Yahoo bars, so comparing the last point with the
        # quote compares 100.30 with itself. The last real bar (here from a series 6% away) is what
        # has to agree with the quote; untagged payloads tell it apart by its time.
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
        # A live quote stamped before the quote's exchange time is still not a market bar; the
        # payload's kinds say so.
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
        payload["contract"]["market_time"] = "2026-09-01T10:00:00Z"   # before every chart point
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
        moment = datetime.fromisoformat(frozen)
        self.assertEqual(validate_payload(self.valid, not_older_than=frozen), [])   # same snapshot
        self.assertEqual(validate_payload(self.valid, not_older_than=(moment - timedelta(hours=4)).isoformat()), [])
        self.assertIn("frozen_at is older than the snapshot already published",
                      validate_payload(self.valid, not_older_than=(moment + timedelta(hours=4)).isoformat()))
        self.assertIn("the published snapshot's frozen_at is not an aware ISO-8601 timestamp",
                      validate_payload(self.valid, not_older_than="2026-10-20 16:17"))

    def test_market_data_freshness_uses_the_quote_time(self):
        payload = copy.deepcopy(self.valid)
        payload["contract"]["market_time"] = "2026-08-15T12:00:00Z"   # two months stale
        errors = validate_payload(payload, max_market_age_days=4)
        self.assertIn("quote is more than 4 days older than the snapshot", errors)
        self.assertEqual(validate_payload(self.valid, max_market_age_days=4), [])

    def test_retracted_direction_model_statement_is_required(self):
        for block in (None, {}, {"status": "ACTIVE"}, "RETRACTED"):
            with self.subTest(block=block):
                payload = self.mutated(lambda p: p.update(retracted_direction_model=block))
                self.assertIn("retracted_direction_model.status must be RETRACTED", validate_payload(payload))

    def test_retracted_direction_model_output_is_never_published(self):
        # A stale renderer that still runs the retracted model must not get its forecast onto the site.
        for field in RETRACTED_MODEL_FIELDS:
            with self.subTest(field=field):
                payload = self.mutated(lambda p: p.update({field: {}}))
                self.assertIn(f"retracted direction-model output must not be published: {field}",
                              validate_payload(payload))
        payload = self.mutated(lambda p: p["unified_data"].update(predicted={"future": {}}))
        self.assertIn("retracted direction-model output must not be published: unified_data.predicted",
                      validate_payload(payload))

    def test_supply_shock_playbook_is_optional(self):
        notes = []
        payload = self.mutated(lambda p: p.pop("supply_shock_playbook"))
        self.assertEqual(validate_payload(payload, warnings=notes), [])
        self.assertTrue(any("supply_shock_playbook is absent" in note for note in notes))


class VolForecastValidationTest(unittest.TestCase):
    """The volatility forecast is the product: required, and every rendered number sane."""

    @classmethod
    def setUpClass(cls):
        cls.valid = json.loads((FIXTURES / "valid_frozen_payload.json").read_text(encoding="utf-8"))

    def errors_with(self, mutate):
        payload = copy.deepcopy(self.valid)
        mutate(payload["vol_forecast"])
        return validate_payload(payload)

    def live_with(self, **fields):
        return self.errors_with(lambda vol: vol["live"].update(fields))

    def test_the_fixture_carries_the_real_forecast(self):
        live = self.valid["vol_forecast"]["live"]
        self.assertEqual(live["model_version"], "har-iv/2")
        self.assertIsNotNone(live["interval_80"])
        # With the challenger published, the freeze says so with a null challenger_error.
        self.assertEqual((live["challenger"]["key"], live["challenger_error"]), ("har_iv_range", None))

    def test_vol_forecast_is_required(self):
        for value in (None, [], "missing"):
            with self.subTest(value=value):
                payload = copy.deepcopy(self.valid)
                payload["vol_forecast"] = value
                self.assertIn("vol_forecast is required", validate_payload(payload))
        payload = copy.deepcopy(self.valid)
        del payload["vol_forecast"]
        self.assertIn("vol_forecast is required", validate_payload(payload))

    def test_forecast_median_and_current_vol_must_be_finite_and_positive(self):
        for key in ("forecast_next_week_vol_annualized_pct", "forecast_median_pct",
                    "current_realized_vol_5d_annualized_pct"):
            for bad in (0, -1.0, float("nan"), float("inf"), "47.6", None, True):
                with self.subTest(key=key, bad=bad):
                    errors = self.live_with(**{key: bad})
                    self.assertIn(f"vol_forecast.live.{key} must be finite and positive", errors)

    def test_direction_must_be_rising_or_falling(self):
        for bad in ("UP", "rising", None, ""):
            with self.subTest(bad=bad):
                self.assertIn("vol_forecast.live.direction must be RISING or FALLING",
                              self.live_with(direction=bad))
        self.assertEqual(self.live_with(direction="FALLING"), [])

    def test_probability_must_be_a_percentage(self):
        for bad in (-0.1, 100.1, None, float("nan"), "58.3"):
            with self.subTest(bad=bad):
                self.assertIn("vol_forecast.live.prob_vol_rises_pct must be a percentage",
                              self.live_with(prob_vol_rises_pct=bad))
        for good in (0, 100, 58.3):
            with self.subTest(good=good):
                self.assertEqual(self.live_with(prob_vol_rises_pct=good), [])

    def test_interval_must_contain_the_median_when_present(self):
        median = self.valid["vol_forecast"]["live"]["forecast_median_pct"]
        for low, high in ((median + 1, median + 20), (median - 20, median - 1), (median + 5, median - 5)):
            with self.subTest(low=low, high=high):
                self.assertIn("vol_forecast.live.interval_80 must contain the median forecast",
                              self.live_with(interval_80={"low_pct": low, "high_pct": high}))
        self.assertEqual(self.live_with(interval_80={"low_pct": median, "high_pct": median}), [])
        # Without OVX the forecaster publishes no interval: allowed.
        self.assertEqual(self.live_with(interval_80=None), [])
        self.assertEqual(self.errors_with(lambda vol: vol["live"].pop("interval_80")), [])

    def test_interval_bounds_must_be_finite_and_positive(self):
        for interval in ({"low_pct": None, "high_pct": 60.0}, {"low_pct": 20.0, "high_pct": float("inf")},
                         {"low_pct": -5.0, "high_pct": 60.0}, {}, "23-64"):
            with self.subTest(interval=interval):
                self.assertIn("vol_forecast.live.interval_80 bounds must be finite and positive",
                              self.live_with(interval_80=interval))

    def test_model_version_must_be_a_string(self):
        for bad in (None, "", "  ", 2):
            with self.subTest(bad=bad):
                self.assertIn("vol_forecast.live.model_version must be a non-empty string",
                              self.live_with(model_version=bad))

    def test_as_of_must_be_a_date(self):
        for bad in (None, "yesterday", "2026-10-19T00:00:00Z", 20261019):
            with self.subTest(bad=bad):
                self.assertIn("vol_forecast.live.as_of must be an ISO date", self.live_with(as_of=bad))

    def test_as_of_at_most_four_business_days_before_the_quote_session(self):
        # The fixture's quote is Tuesday 2026-10-20 07:51 ET; its forecast is as of Monday (1 day).
        stale = "vol_forecast.live.as_of is more than 4 business days before the quote's session"
        self.assertEqual(self.live_with(as_of="2026-10-14"), [])            # Wed: 4 business days
        self.assertIn(stale, self.live_with(as_of="2026-10-13"))            # Tue: 5
        self.assertIn(stale, self.live_with(as_of="2025-10-19"))
        self.assertIn("vol_forecast.live.as_of is after the quote's session", self.live_with(as_of="2026-10-21"))

    def test_freshness_anchor_is_the_cme_session_of_the_quote_time(self):
        # 18:30 ET belongs to the next day's session, so Wednesday's forecast is fresh against it and
        # a forecast as of the previous Wednesday is now five sessions old.
        payload = copy.deepcopy(self.valid)
        payload["contract"]["market_time"] = "2026-10-20T22:30:00Z"
        payload["vol_forecast"]["live"]["as_of"] = "2026-10-21"
        self.assertNotIn("vol_forecast.live.as_of is after the quote's session", validate_payload(payload))
        payload["vol_forecast"]["live"]["as_of"] = "2026-10-14"
        self.assertIn("vol_forecast.live.as_of is more than 4 business days before the quote's session",
                      validate_payload(payload))

    def test_freshness_falls_back_to_frozen_at_without_a_quote_time(self):
        payload = copy.deepcopy(self.valid)
        del payload["contract"]["market_time"]
        payload["vol_forecast"]["live"]["as_of"] = "2026-09-01"
        notes = []
        self.assertIn("vol_forecast.live.as_of is more than 4 business days before the quote's session",
                      validate_payload(payload, warnings=notes))
        self.assertTrue(any("vol_forecast freshness was checked against frozen_at" in n for n in notes))

    def test_a_missing_challenger_or_economic_block_may_say_why(self):
        # Optional reasons: a non-empty string or null. Their absence is noted, never an error, and the
        # payload's own text is not echoed into a CI workflow command.
        def check(mutate):
            payload = copy.deepcopy(self.valid)
            mutate(payload["vol_forecast"])
            notes = []
            return validate_payload(payload, warnings=notes), notes

        no_challenger = "vol_forecast.live has no challenger forecast, so this session cannot enter the promotion test"
        errors, notes = check(lambda vol: vol["live"].update(challenger=None, challenger_error="::error::OVX stale"))
        self.assertEqual(errors, [])
        self.assertEqual(notes, [f"{no_challenger}; vol_forecast.live.challenger_error gives the reason"])
        errors, notes = check(lambda vol: vol["live"].pop("challenger"))
        self.assertEqual((errors, notes), ([], [f"{no_challenger}; the payload gives no reason"]))
        self.assertEqual(check(lambda vol: vol["live"].update(challenger_error=None)), ([], []))
        errors, notes = check(lambda vol: vol["economic"].update(unavailable_reason="ValueError"))
        self.assertEqual((errors, notes), ([], ["vol_forecast.economic is unavailable; "
                                                "vol_forecast.economic.unavailable_reason gives the reason"]))
        self.assertEqual(check(lambda vol: vol.update(economic={"unavailable_reason": None})), ([], []))
        self.assertEqual(check(lambda vol: vol.pop("economic")), ([], []))
        for bad in ("", "  ", 1, {}, ["x"]):
            with self.subTest(bad=bad):
                self.assertIn("vol_forecast.live.challenger_error must be a non-empty string or null",
                              self.live_with(challenger=None, challenger_error=bad))
                self.assertIn("vol_forecast.economic.unavailable_reason must be a non-empty string or null",
                              self.errors_with(lambda vol: vol["economic"].update(unavailable_reason=bad)))

    def test_validation_sample_size_must_be_a_positive_integer(self):
        for bad in (0, -1, 1.5, True, "927", None):
            with self.subTest(bad=bad):
                self.assertIn("vol_forecast.validation.n must be a positive integer",
                              self.errors_with(lambda vol: vol["validation"].update(n=bad)))

    def test_validation_qlike_must_be_finite(self):
        for bad in (None, float("nan"), float("inf"), "0.19"):
            with self.subTest(bad=bad):
                self.assertIn("vol_forecast.validation.har_qlike must be finite",
                              self.errors_with(lambda vol: vol["validation"].update(har_qlike=bad)))

    PAIR = {"model_version": "har-iv/2", "challenger_key": "har_iv_range", "n_excluded_other_version": 0,
            "n_excluded_other_challenger": 0, "n_excluded_no_challenger": 0}
    LIVE_RECORD = {
        "started": "2026-10-20", "n_recorded": 4, "n_resolved": 1, "n_pending": 2, "n_skipped_roll": 0,
        "n_expired": 0, "n_late_commit": 1, "n_independent": 1, "next_result_due": "2026-10-27",
        "champion": {"model_version": "har-iv/2", "n": 1, "qlike": 0.2, "interval_80_coverage_pct": 100.0,
                     "direction_hit_pct": 100.0, "mean_forecast_pct": 47.6, "mean_realized_pct": 44.0},
        "challenger": {"key": "har_iv_range", "qlike": 0.21, "n": 1, "champion_qlike": 0.2},
        "promotion": {"status": "accruing", "n_independent_paired": 1, "required": 52, "dm_stat": None,
                      "p_value": None, "mean_qlike_difference": None, **PAIR},
        "recent": [{"as_of": "2026-10-19", "issued_at": "2026-10-20T12:01:59Z", "model_version": "har-iv/2",
                    "target_start": "2026-10-21", "target_end": "2026-10-27", "forecast_pct": 47.6,
                    "median_pct": 40.0, "lo80_pct": 23.3, "hi80_pct": 63.9, "challenger_pct": 48.4,
                    "realized_pct": None, "status": "pending"},
                   {"as_of": "2026-10-16", "issued_at": "2026-10-19T17:50:00Z", "model_version": "har-iv/2",
                    "target_start": "2026-10-20", "target_end": "2026-10-26", "forecast_pct": 46.1,
                    "median_pct": 39.0, "lo80_pct": 22.0, "hi80_pct": 62.0, "challenger_pct": 47.0,
                    "realized_pct": None, "status": "late_commit"}],
        "promotion_rule": "...",
    }
    DECIDED = {"status": "keep", "n_independent_paired": 52, "required": 52, "dm_stat": 0.4078,
               "p_value": 0.342564, "mean_qlike_difference": 0.006543, **PAIR}  # p = P(T > 0.4078), T ~ t(51)

    def record_errors(self, **changes):
        record = {**copy.deepcopy(self.LIVE_RECORD), **changes}
        return self.errors_with(lambda vol: vol.update(live_record=record))

    def promotion_errors(self, **changes):
        return self.record_errors(promotion={**self.DECIDED, **changes})

    def test_live_record_is_optional_but_well_formed_when_present(self):
        self.assertNotIn("live_record", self.valid["vol_forecast"])
        self.assertEqual(self.record_errors(), [])
        empty = {"n_recorded": 0, "n_resolved": 0, "n_pending": 0, "n_late_commit": 0, "n_independent": 0,
                 "next_result_due": None, "recent": [],
                 "champion": {**self.LIVE_RECORD["champion"], "n": 0},
                 "challenger": {**self.LIVE_RECORD["challenger"], "n": 0},
                 "promotion": {**self.LIVE_RECORD["promotion"], "n_independent_paired": 0}}
        self.assertEqual(self.record_errors(**empty), [])

    def test_the_writers_summaries_pass(self):
        # What backend/vol_record.py publishes: the seeded record, one holding every status, and one
        # whose promotion test has decided.
        self.assertEqual(LIVE_RECORD_STATUSES, vol_record.STATUSES)

        def entry(week, status, realized=None, challenger=38.0, model_version="har-iv/2", key="har_iv_range"):
            base = date(2026, 11, 2) + timedelta(days=7 * week)       # Mondays; windows Tuesday to Monday
            return {
                "as_of": (base - timedelta(days=3)).isoformat(), "issued_at": f"{base}T13:00:00Z",
                "recorded_at": f"{base}T13:05:00Z", "sealed_at": f"{base}T13:15:00Z", "model_version": model_version,
                "forecast_pct": 40.0,
                "challenger": {"key": key, "forecast_pct": challenger, "median_pct": None} if challenger else None,
                "base_session": base.isoformat(), "target_start": (base + timedelta(days=1)).isoformat(),
                "target_end": (base + timedelta(days=7)).isoformat(), "status": status, "realized_pct": realized,
                "resolved_at": "2026-12-31T12:00:00Z",
                "closes": [[(base + timedelta(days=d)).isoformat(), 80.0] for d in (0, 1, 2, 3, 4, 7)],
            }

        records = {
            "seeded": [],
            "every status": [entry(week, status, 35.0 if status == "resolved" else None)
                             for week, status in enumerate(vol_record.STATUSES)],
            "decided": [entry(week, "resolved", (30.0, 36.0)[week % 2], (33.0, 34.0)[week % 2])
                        for week in range(52)],
            # Plain-HAR fallbacks (resolved and pending), another challenger and none: all left out.
            "other pairs": [entry(0, "resolved", 35.0, None, model_version="har/2"),
                            entry(1, "resolved", 35.0, key="combo"), entry(2, "resolved", 35.0, None),
                            entry(3, "resolved", 35.0), entry(4, "pending", None, None, model_version="har/2")],
        }
        due, summaries = {}, {}
        for name, entries in records.items():
            with self.subTest(name):
                summary = vol_record.summarize(vol_record.validate_record(
                    {"schema_version": 1, "started": "2026-10-20", "entries": entries}))
                self.assertEqual(self.errors_with(lambda vol: vol.update(live_record=summary)), [])
                due[name], summaries[name] = summary["next_result_due"], summary
        self.assertEqual(summaries["decided"]["promotion"]["status"], "promote")
        # The one pending entry (week 0) is the next result; with nothing pending there is none.
        self.assertEqual(due, {"seeded": None, "every status": "2026-11-09", "decided": None,
                               "other pairs": "2026-12-07"})
        other = summaries["other pairs"]
        self.assertEqual({k: other["promotion"][k] for k in self.PAIR},
                         dict(self.PAIR, n_excluded_other_version=1, n_excluded_other_challenger=1,
                              n_excluded_no_challenger=1))
        self.assertEqual((other["n_resolved"], other["champion"]["n"], other["challenger"]["n"]), (4, 3, 1))
        self.assertEqual([row["model_version"] for row in other["recent"]],
                         ["har/2", "har-iv/2", "har-iv/2", "har-iv/2", "har/2"])

    def test_next_result_due_is_required_and_a_date_or_null(self):
        required = "vol_forecast.live_record.next_result_due is required (an ISO date or null)"
        malformed = "vol_forecast.live_record.next_result_due must be an ISO date (YYYY-MM-DD) or null"
        self.assertEqual(self.record_errors(next_result_due=None), [])
        self.assertEqual(self.record_errors(next_result_due="2026-11-30"), [])
        record = {k: v for k, v in self.LIVE_RECORD.items() if k != "next_result_due"}
        self.assertIn(required, self.errors_with(lambda vol: vol.update(live_record=record)))
        for bad in ("2026-10-27T00:00:00Z", "20261027", "2026-W44-2", "2026-13-01", "2026-02-30", "",
                    "٢٠٢٦-10-27", 20261027, True, ["2026-10-27"]):
            with self.subTest(bad=bad):
                self.assertIn(malformed, self.record_errors(next_result_due=bad))
        # A date needs a pending entry to come from.
        self.assertIn("vol_forecast.live_record.next_result_due must be null with no pending entry",
                      self.record_errors(n_pending=0))
        self.assertEqual(self.record_errors(n_pending=0, next_result_due=None), [])

    def test_live_record_counts_must_be_non_negative_integers(self):
        for key in ("n_recorded", "n_resolved", "n_pending", "n_skipped_roll", "n_expired", "n_late_commit",
                    "n_independent"):
            for bad in (-1, 1.0, True, "3", None):
                with self.subTest(key=key, bad=bad):
                    record = {**self.LIVE_RECORD, key: bad}
                    self.assertIn(f"vol_forecast.live_record.{key} must be a non-negative integer",
                                  self.errors_with(lambda vol: vol.update(live_record=record)))

    def test_live_record_recent_must_be_a_list_of_objects(self):
        for bad in (None, {}, "[]", [1, 2], [{"status": "pending"}, None]):
            with self.subTest(bad=bad):
                record = {**self.LIVE_RECORD, "recent": bad}
                self.assertIn("vol_forecast.live_record.recent must be a list of objects",
                              self.errors_with(lambda vol: vol.update(live_record=record)))
        self.assertIn("vol_forecast.live_record must be an object",
                      self.errors_with(lambda vol: vol.update(live_record=[])))

    def test_live_record_recent_statuses_must_be_known(self):
        statuses = "vol_forecast.live_record.recent statuses must be one of " + ", ".join(LIVE_RECORD_STATUSES)
        for bad in ("won", None, "LATE_COMMIT"):
            with self.subTest(bad=bad):
                recent = [{**self.LIVE_RECORD["recent"][0], "status": bad}]
                self.assertIn(statuses, self.record_errors(recent=recent))
        for good in LIVE_RECORD_STATUSES:
            with self.subTest(good=good):
                self.assertEqual(self.record_errors(recent=[{**self.LIVE_RECORD["recent"][0], "status": good}]), [])

    def test_promotion_names_the_pinned_pair_and_counts_what_it_leaves_out(self):
        where = "vol_forecast.live_record.promotion"
        missing = {k: v for k, v in self.DECIDED.items() if k not in self.PAIR}
        self.assertIn(f"{where} is missing model_version, challenger_key, n_excluded_other_version, "
                      "n_excluded_other_challenger, n_excluded_no_challenger", self.record_errors(promotion=missing))
        for key in ("model_version", "challenger_key"):
            for bad in ("", "  ", None, 2):
                with self.subTest(key=key, bad=bad):
                    self.assertIn(f"{where}.{key} must be a non-empty string", self.promotion_errors(**{key: bad}))
        for key in ("n_excluded_other_version", "n_excluded_other_challenger", "n_excluded_no_challenger"):
            for bad in (-1, 1.0, True, None, "0"):
                with self.subTest(key=key, bad=bad):
                    self.assertIn(f"{where}.{key} must be a non-negative integer", self.promotion_errors(**{key: bad}))

    def test_champion_and_challenger_name_the_promotion_pair(self):
        where = "vol_forecast.live_record"
        champion, challenger = self.LIVE_RECORD["champion"], self.LIVE_RECORD["challenger"]
        champion_shape = (f"{where}.champion must be an object with a non-empty model_version and a non-negative "
                          "integer n")
        challenger_shape = f"{where}.challenger must be an object with a non-empty key and a non-negative integer n"
        cases = {
            "no champion": (dict(champion=None), champion_shape),
            "champion without n": (dict(champion={k: v for k, v in champion.items() if k != "n"}), champion_shape),
            "champion n negative": (dict(champion={**champion, "n": -1}), champion_shape),
            "champion without a version": (dict(champion={**champion, "model_version": None}), champion_shape),
            "no challenger": (dict(challenger=[]), challenger_shape),
            "challenger without a key": (dict(challenger={**challenger, "key": ""}), challenger_shape),
            "challenger n boolean": (dict(challenger={**challenger, "n": True}), challenger_shape),
        }
        pair = (f"{where}: champion.model_version and challenger.key must be the promotion test's model_version "
                "and challenger_key")
        cases.update({
            "champion of a fallback version": (dict(champion={**champion, "model_version": "har/2"}), pair),
            "another challenger": (dict(challenger={**challenger, "key": "combo"}), pair),
            "promotion of another pair": (dict(promotion={**self.LIVE_RECORD["promotion"], "challenger_key": "combo"}),
                                          pair),
        })
        for name, (changes, expected) in cases.items():
            with self.subTest(name):
                self.assertIn(expected, self.record_errors(**changes))

    def test_resolved_entries_add_up(self):
        where = "vol_forecast.live_record"
        resolved_sum = f"{where}: n_resolved must equal champion.n plus promotion.n_excluded_other_version"
        champion_sum = (f"{where}: champion.n must equal challenger.n plus promotion.n_excluded_other_challenger and "
                        "promotion.n_excluded_no_challenger")
        promotion = self.LIVE_RECORD["promotion"]
        # 4 resolved: 1 fallback, 1 with another challenger, 1 without one, 1 of the pinned pair.
        balanced = dict(n_resolved=4, champion={**self.LIVE_RECORD["champion"], "n": 3},
                        promotion={**promotion, "n_excluded_other_version": 1, "n_excluded_other_challenger": 1,
                                   "n_excluded_no_challenger": 1})
        self.assertEqual(self.record_errors(**balanced), [])
        self.assertIn(resolved_sum, self.record_errors(**dict(balanced, n_resolved=3)))
        self.assertIn(champion_sum, self.record_errors(**dict(balanced, challenger={
            **self.LIVE_RECORD["challenger"], "n": 2})))
        self.assertIn(resolved_sum, self.record_errors(promotion={**promotion, "n_excluded_other_version": 2}))
        self.assertIn(champion_sum, self.record_errors(promotion={**promotion, "n_excluded_no_challenger": 1}))

    def test_recent_rows_name_their_model_version(self):
        message = "vol_forecast.live_record.recent model_version must be a non-empty string"
        row = self.LIVE_RECORD["recent"][0]
        for bad in (None, "", 2):
            with self.subTest(bad=bad):
                self.assertIn(message, self.record_errors(recent=[{**row, "model_version": bad}]))
        self.assertIn(message, self.record_errors(recent=[{k: v for k, v in row.items() if k != "model_version"}]))
        self.assertEqual(self.record_errors(recent=[{**row, "model_version": "har/2"}]), [])

    def test_promotion_verdicts_pass(self):
        self.assertEqual(self.promotion_errors(), [])
        self.assertEqual(self.promotion_errors(status="promote", dm_stat=2.1, p_value=0.0179,
                                               mean_qlike_difference=0.031), [])
        # Identical forecasts leave nothing to test: a keep without a statistic.
        self.assertEqual(self.promotion_errors(dm_stat=None, p_value=None, mean_qlike_difference=0.0), [])

    def test_malformed_promotion_blocks_are_rejected(self):
        where = "vol_forecast.live_record.promotion"
        accruing = self.LIVE_RECORD["promotion"]
        cases = {
            "missing block": (self.record_errors(promotion=None), f"{where} must be an object"),
            "not an object": (self.record_errors(promotion=[]), f"{where} must be an object"),
            "missing key": (self.record_errors(promotion={k: v for k, v in self.DECIDED.items() if k != "p_value"}),
                            f"{where} is missing p_value"),
            "status": (self.promotion_errors(status="promoted"),
                       f"{where}.status must be one of accruing, promote, keep"),
            "required zero": (self.promotion_errors(required=0), f"{where}.required must be a positive integer"),
            "required float": (self.promotion_errors(required=52.0), f"{where}.required must be a positive integer"),
            "n negative": (self.promotion_errors(n_independent_paired=-1),
                           f"{where}.n_independent_paired must be a non-negative integer"),
            "n boolean": (self.promotion_errors(n_independent_paired=True),
                          f"{where}.n_independent_paired must be a non-negative integer"),
            "dm string": (self.promotion_errors(dm_stat="0.4"), f"{where}.dm_stat must be a finite number or null"),
            "dm infinite": (self.promotion_errors(dm_stat=float("inf")),
                            f"{where}.dm_stat must be a finite number or null"),
            "mean NaN": (self.promotion_errors(mean_qlike_difference=float("nan")),
                         f"{where}.mean_qlike_difference must be a finite number or null"),
            "p above one": (self.promotion_errors(p_value=1.5), f"{where}.p_value must be a probability or null"),
            "p negative": (self.promotion_errors(p_value=-0.1), f"{where}.p_value must be a probability or null"),
        }
        accruing_rule = f"{where}: an accruing test has fewer than the required weeks and no statistics"
        verdict_rule = (f"{where}: a verdict needs exactly the required weeks, a mean difference, and the statistic "
                        "with its p-value")
        promote_rule = f"{where}: a promote verdict needs a positive mean difference and a p-value"
        cases.update({
            "accruing with statistics": (self.record_errors(promotion={**accruing, "dm_stat": 1.0}), accruing_rule),
            "accruing with a full sample": (self.record_errors(promotion={**accruing, "n_independent_paired": 52}),
                                            accruing_rule),
            "verdict on too few weeks": (self.promotion_errors(n_independent_paired=40), verdict_rule),
            "verdict on too many weeks": (self.promotion_errors(n_independent_paired=60), verdict_rule),
            "verdict without a mean": (self.promotion_errors(mean_qlike_difference=None), verdict_rule),
            "statistic without p": (self.promotion_errors(p_value=None), verdict_rule),
            "promote on a negative mean": (self.promotion_errors(status="promote", mean_qlike_difference=-0.01),
                                           promote_rule),
            "promote without a test": (self.promotion_errors(status="promote", dm_stat=None, p_value=None),
                                       promote_rule),
        })
        for name, (errors, expected) in cases.items():
            with self.subTest(name):
                self.assertIn(expected, errors)


class ResearchValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.research = json.loads((FIXTURES / "research.json").read_text(encoding="utf-8"))

    def test_valid_research_bundle_passes(self):
        self.assertEqual(validate_research(self.research), [])

    def test_builder_checks_are_applied(self):
        broken = copy.deepcopy(self.research)
        broken["schema_version"] = 2
        broken["vol_lab"]["series"]["actual"] = broken["vol_lab"]["series"]["actual"][:-1]
        broken["postmortem"]["ten_year"]["leaked"]["equity"] = []
        self.assertEqual(validate_research(broken), [
            "research.json: schema_version",
            "research.json: series.actual length",
            "research.json: postmortem.ten_year.equity",
        ])

    def test_malformed_research_is_reported_not_a_crash(self):
        self.assertEqual(validate_research([]), ["research.json: root must be a JSON object"])
        self.assertEqual(validate_research({"schema_version": 1, "vol_lab": {"series": ["actual"]}}),
                         ["research.json: malformed (AttributeError)"])

    def test_only_the_published_or_fixture_research_path_is_accepted(self):
        self.assertEqual(research_payload_path("public/research.json").name, "research.json")
        self.assertEqual(research_payload_path("tests/fixtures/research.json").parent.name, "fixtures")
        for bad in ("../research.json", "/tmp/research.json", "public/data.json", "data/vol_lab.json"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                research_payload_path(bad)

    def _cli(self, *args):
        return subprocess.run(  # nosec B603 - fixed argv: this interpreter and the repository's script
            [sys.executable, "scripts/validate_frozen_payload.py", *args],
            cwd=ROOT, capture_output=True, text=True, timeout=60, check=False,
        )

    def test_cli_validates_both_files(self):
        result = self._cli("tests/fixtures/valid_frozen_payload.json", "--research", "tests/fixtures/research.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("data.json and research.json", result.stdout)
        refused = self._cli("tests/fixtures/valid_frozen_payload.json", "--research", "data/vol_lab.json")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("research.json is unreadable error_type=ValueError", refused.stderr)


if __name__ == "__main__":
    unittest.main()
