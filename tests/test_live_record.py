"""Network-free unit tests for backend/live_record.py — the git-committed live track record.

This logic decides whether a 1W call is recorded, when it resolves (five CME sessions later), when a
contract roll or a late run makes it unscoreable, and whether it counts as a hit. It runs in CI
every cycle. All tests operate on plain dicts or temp files (no network).

Session facts used below (backend/contract_calendar.py): CLN26 last trades 2026-06-22 and CLV26
2026-09-22, so CL=F rolls to CLQ26 on 2026-06-23 and to CLX26 on 2026-09-23.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend import live_record as lr


def record(calls):
    return {"calls": calls}


def payload(
    pct,
    price=80.0,
    symbol="CLN26",
    frozen="2026-06-15T14:00:00+00:00",   # Monday 10:00 ET, market open
    significant=True,
    exact=None,
):
    mh = {"percentage_changes": {"1w": pct}}
    if exact is not None:
        mh["percentage_changes_exact"] = {"1w": exact}
    return {
        "performance_metrics": {
            "by_horizon": {"1w": {"wf_is_significant": significant}}
        },
        "multi_horizon_predictions": mh,
        "current_price": price,
        "contract": {"symbol": symbol},
        "frozen_at": frozen,
    }


class ExtractCallTest(unittest.TestCase):
    def test_stance_gates(self):
        self.assertEqual(lr.extract_call(payload(1.5))["stance"], "LONG")
        self.assertEqual(lr.extract_call(payload(-1.5))["stance"], "SHORT")
        self.assertEqual(lr.extract_call(payload(0.3))["stance"], "NEUTRAL")

    def test_gate_boundary_is_strict(self):
        # |fc| must EXCEED 0.6, not equal it.
        self.assertEqual(lr.extract_call(payload(0.6))["stance"], "NEUTRAL")
        self.assertEqual(lr.extract_call(payload(0.7, exact=0.61))["stance"], "LONG")

    def test_gate_uses_unrounded_forecast(self):
        # The display value is rounded to 1 decimal: 0.64 shows as 0.6. Gating on the rounded value
        # silently moved the threshold to ~0.65; the exact value must decide.
        self.assertEqual(lr.extract_call(payload(0.6, exact=0.64))["stance"], "LONG")
        self.assertEqual(lr.extract_call(payload(0.6, exact=0.6))["stance"], "NEUTRAL")

    def test_fields_extracted(self):
        c = lr.extract_call(payload(1.0, price=75.5, symbol="CLQ26"))
        self.assertEqual(c["date"], "2026-06-15")
        self.assertEqual(c["trading_date"], "2026-06-15")
        self.assertEqual(c["entry_at"], "2026-06-15T14:00:00+00:00")
        self.assertEqual(c["contract"], "CLQ26")
        self.assertEqual(c["entry_price"], 75.5)
        self.assertTrue(c["eligible_for_validation"])
        self.assertFalse(c["resolved"])

    def test_evening_quote_belongs_to_next_session(self):
        c = lr.extract_call(payload(1.0, frozen="2026-06-15T23:00:00+00:00"))   # 19:00 ET
        self.assertEqual(c["trading_date"], "2026-06-16")

    def test_non_significant_forecast_is_neutral_and_ineligible(self):
        c = lr.extract_call(payload(4.5, significant=False))
        self.assertEqual(c["stance"], "NEUTRAL")
        self.assertFalse(c["eligible_for_validation"])

    def test_truthy_but_not_true_significance_is_not_significant(self):
        c = lr.extract_call(payload(4.5, significant="yes"))
        self.assertEqual(c["stance"], "NEUTRAL")


class LoadRecordTest(unittest.TestCase):
    def test_corrupt_record_is_not_silently_replaced(self):
        with TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "record.json"
            record_path.write_text("{broken", encoding="utf-8")
            with (
                mock.patch.object(lr, "RECORD_PATH", record_path),
                self.assertRaisesRegex(ValueError, "Refusing to overwrite"),
            ):
                lr.load_record()


class ResolveTest(unittest.TestCase):
    def _call(self, stance, entry=80.0, contract="CLN26", session="2026-06-01"):
        return {"date": session, "trading_date": session, "contract": contract,
                "entry_price": entry, "stance": stance, "resolved": False,
                "eligible_for_validation": stance != "NEUTRAL"}

    def test_not_resolved_before_five_sessions(self):
        rec = record([self._call("LONG")])
        lr.resolve_calls(rec, "2026-06-05T14:00:00+00:00", "CLN26", 82.0)   # 4 sessions later
        self.assertFalse(rec["calls"][0]["resolved"])

    def test_weekend_does_not_count_toward_the_horizon(self):
        rec = record([self._call("LONG", session="2026-06-04")])            # Thursday
        lr.resolve_calls(rec, "2026-06-10T14:00:00+00:00", "CLN26", 82.0)   # 4 sessions later
        self.assertFalse(rec["calls"][0]["resolved"])
        lr.resolve_calls(rec, "2026-06-11T14:00:00+00:00", "CLN26", 82.0)   # 5th session
        self.assertTrue(rec["calls"][0]["resolved"])
        self.assertEqual(rec["calls"][0]["resolution_trading_date"], "2026-06-11")

    def test_long_hits_when_price_rises(self):
        rec = record([self._call("LONG", entry=80.0)])
        lr.resolve_calls(rec, "2026-06-08T14:00:00+00:00", "CLN26", 82.0)
        c = rec["calls"][0]
        self.assertTrue(c["resolved"])
        self.assertTrue(c["hit"])
        self.assertAlmostEqual(c["realized_pct"], 2.5, places=2)

    def test_long_misses_when_price_falls(self):
        rec = record([self._call("LONG", entry=80.0)])
        lr.resolve_calls(rec, "2026-06-08T14:00:00+00:00", "CLN26", 78.0)
        self.assertFalse(rec["calls"][0]["hit"])

    def test_short_hits_when_price_falls(self):
        rec = record([self._call("SHORT", entry=80.0)])
        lr.resolve_calls(rec, "2026-06-08T14:00:00+00:00", "CLN26", 78.0)
        self.assertTrue(rec["calls"][0]["hit"])

    def test_one_session_late_is_still_scored_two_is_skipped(self):
        rec = record([self._call("LONG"), self._call("LONG", session="2026-06-02")])
        lr.resolve_calls(rec, "2026-06-10T14:00:00+00:00", "CLN26", 82.0)
        first, second = rec["calls"]
        self.assertTrue(first["skipped_late"])        # 7 sessions after 06-01
        self.assertNotIn("hit", first)
        self.assertTrue(second["hit"])                # 6 sessions after 06-02: one late, allowed

    def test_contract_roll_is_skipped_not_scored(self):
        # Entry on CLN26's session, resolution after it expired (06-22): roll basis would
        # contaminate the realized move, so it is skipped, never scored.
        rec = record([self._call("LONG", session="2026-06-19")])
        lr.resolve_calls(rec, "2026-06-26T14:00:00+00:00", "CLQ26", 82.0)
        c = rec["calls"][0]
        self.assertTrue(c["resolved"])
        self.assertTrue(c.get("skipped_contract_roll"))
        self.assertNotIn("hit", c)

    def test_roll_is_detected_even_when_labels_agree(self):
        # The real 2026-09-16 call: labelled CLX26 but priced from CLV26 (CL=F follows the
        # expiring contract through 09-22). The calendar, not the label, must catch it.
        rec = record([self._call("LONG", contract="CLX26", entry=104.61, session="2026-09-16")])
        lr.resolve_calls(rec, "2026-09-23T14:00:00+00:00", "CLX26", 91.40)
        c = rec["calls"][0]
        self.assertTrue(c.get("skipped_contract_roll"))
        self.assertNotIn("hit", c)

    def test_legacy_rows_without_trading_date_still_resolve(self):
        rec = record([{"date": "2026-06-01", "entry_at": "2026-06-01T04:50:00+00:00",
                       "contract": "CLN26", "entry_price": 80.0, "stance": "NEUTRAL",
                       "resolved": False}])
        lr.resolve_calls(rec, "2026-06-08T14:00:00+00:00", "CLN26", 81.0)
        self.assertTrue(rec["calls"][0]["resolved"])
        self.assertAlmostEqual(rec["calls"][0]["realized_pct"], 1.25, places=3)


class SummarizeTest(unittest.TestCase):
    def test_counts_only_resolved_directional_calls(self):
        rec = record([
            {"resolved": True, "hit": True, "stance": "LONG", "date": "2026-06-01"},
            {"resolved": True, "hit": False, "stance": "SHORT", "date": "2026-06-02"},
            {"resolved": True, "stance": "NEUTRAL", "date": "2026-06-03"},   # not scored
            {"resolved": False, "stance": "LONG", "date": "2026-06-09"},     # pending
        ])
        s = lr.summarize(rec)
        self.assertEqual(s["n_resolved_directional"], 2)
        self.assertEqual(s["n_hits"], 1)
        self.assertEqual(s["hit_rate_pct"], 50.0)
        self.assertEqual(s["n_pending"], 1)
        self.assertEqual(s["n_neutral"], 1)

    def test_overlapping_calls_are_not_independent_evidence(self):
        def scored(entry, resolved):
            return {"resolved": True, "hit": True, "stance": "LONG", "eligible_for_validation": True,
                    "date": entry, "trading_date": entry, "resolution_trading_date": resolved}
        rec = record([
            scored("2026-07-01", "2026-07-09"),
            scored("2026-07-02", "2026-07-10"),   # overlaps the first
            scored("2026-07-09", "2026-07-16"),   # starts when the first resolves
        ])
        s = lr.summarize(rec)
        self.assertEqual(s["n_resolved_directional"], 3)
        self.assertEqual(s["n_independent_directional"], 2)

    def test_preserves_timestamp_when_record_is_semantically_unchanged(self):
        rec = {
            "calls": [],
            "summary": {"updated_at": "2026-06-20T00:00:00+00:00"},
        }

        summary = lr.summarize(rec)

        self.assertEqual(summary["updated_at"], "2026-06-20T00:00:00+00:00")

    def test_retracted_legacy_calls_remain_auditable_but_are_not_scored(self):
        rec = record([
            {
                "resolved": True,
                "hit": True,
                "stance": "LONG",
                "date": "2026-06-10",
            }
        ])
        summary = lr.summarize(rec)
        self.assertEqual(summary["n_resolved_directional"], 0)
        self.assertEqual(summary["n_ineligible_directional"], 1)
        self.assertIsNone(summary["hit_rate_pct"])


class MainTest(unittest.TestCase):
    """End-to-end runs of main() against temp files: one call per session, none while closed."""

    def _run(self, tmp, frozen, price=80.0):
        data_path = Path(tmp) / "data.json"
        data_path.write_text(json.dumps(payload(0.2, price=price, frozen=frozen)), encoding="utf-8")
        with (
            mock.patch.object(lr, "RECORD_PATH", Path(tmp) / "record.json"),
            mock.patch.object(lr, "public_json_path", return_value=data_path),
            mock.patch("sys.argv", ["live_record.py"]),
        ):
            lr.main()
        return json.loads((Path(tmp) / "record.json").read_text(encoding="utf-8"))

    def test_one_call_per_session_and_none_on_weekends(self):
        with TemporaryDirectory() as tmp:
            rec = self._run(tmp, "2026-06-12T14:00:00+00:00")                 # Friday, open
            self.assertEqual(len(rec["calls"]), 1)
            rec = self._run(tmp, "2026-06-12T18:00:00+00:00")                 # same session
            self.assertEqual(len(rec["calls"]), 1)
            rec = self._run(tmp, "2026-06-13T14:00:00+00:00", price=81.0)     # Saturday, closed
            self.assertEqual(len(rec["calls"]), 1)                            # no stale re-record
            rec = self._run(tmp, "2026-06-15T14:00:00+00:00")                 # Monday, open
            self.assertEqual([c["trading_date"] for c in rec["calls"]], ["2026-06-12", "2026-06-15"])
            self.assertEqual(rec["summary"]["n_calls"], 2)


if __name__ == "__main__":
    unittest.main()
