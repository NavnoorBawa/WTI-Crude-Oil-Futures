"""Network-free unit tests for backend/live_record.py — the git-committed live track record.

This logic decides whether a 1W call is recorded, when it resolves (five CME sessions later), when a
contract roll or a late run makes it unscoreable, and whether it counts as a hit. It runs in CI
every cycle. All tests operate on plain dicts or temp files (no network).

Session facts used below (backend/contract_calendar.py): CLN26 last trades 2026-06-22 and CLV26
2026-09-22. CL=F's daily bars roll to CLQ26 on 2026-06-23 and to CLX26 on 2026-09-23, but Yahoo's
live CL=F quote already moved to CLX26 on 2026-09-18 (oil.get_current_wti_contract labels the
contract actually quoted).
"""

import json
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend import contract_calendar
from backend import live_record as lr

EDGE = {"wf_pnl_win_rate": 55.0, "wf_pnl_profit_factor": 1.3}   # positive Kelly edge
NO_EDGE = {"wf_pnl_win_rate": 48.2, "wf_pnl_profit_factor": 0.786}  # the committed artifact's 1W P&L


def record(calls):
    return {"calls": calls}


def payload(
    pct,
    price=80.0,
    symbol="CLN26",
    frozen="2026-06-15T14:00:00+00:00",   # Monday 10:00 ET, market open
    significant=True,
    value=None,
    market_time=None,
    model_bar="default",
    pnl=EDGE,
    contract_basis=None,
):
    """A frozen payload as freeze.py writes it. `pct` is the one-decimal display percentage; the
    stance uses the 1W model value (default: price moved by `pct`, rounded to cents like the API)."""
    if value is None:
        value = round(price * (1 + pct / 100), 2)
    if model_bar == "default":   # the bar of the session before the quote's: a regular forecast
        session = contract_calendar.trading_date(lr._parse_utc(market_time or frozen))
        model_bar = contract_calendar.shift_business_days(session, -1).isoformat()
    contract = {"symbol": symbol}
    if market_time is not None:
        contract["market_time"] = market_time
    if contract_basis is not None:
        contract["contract_basis"] = contract_basis
    return {
        "performance_metrics": {
            "by_horizon": {"1w": {"wf_is_significant": significant, **pnl}}
        },
        "multi_horizon_predictions": {
            "predictions": {"1w": value},
            "percentage_changes": {"1w": pct},
            "market_data_sources": {"daily_history": {"symbol": "CL=F", "model_bar_date": model_bar}},
        },
        "current_price": price,
        "contract": contract,
        "frozen_at": frozen,
    }


class ExtractCallTest(unittest.TestCase):
    def test_stance_gates(self):
        self.assertEqual(lr.extract_call(payload(1.5))["stance"], "LONG")
        self.assertEqual(lr.extract_call(payload(-1.5))["stance"], "SHORT")
        self.assertEqual(lr.extract_call(payload(0.3))["stance"], "NEUTRAL")

    def test_gate_boundary_is_strict(self):
        # |move| must EXCEED 0.6, not equal it: 125.75 vs 125.00 is exactly +0.6%.
        self.assertEqual(lr.extract_call(payload(0.6, price=125.0, value=125.75))["stance"], "NEUTRAL")
        self.assertEqual(lr.extract_call(payload(0.6, price=125.0, value=125.77))["stance"], "LONG")

    def test_gate_uses_the_model_value_not_the_rounded_display(self):
        # The display value is rounded to 1 decimal: +0.64% shows as 0.6. Gating on it silently moved
        # the threshold to ~0.65; the model value against the price decides, as on the dashboard.
        self.assertEqual(lr.extract_call(payload(0.6, value=80.51))["stance"], "LONG")     # +0.64%
        self.assertEqual(lr.extract_call(payload(0.7, value=80.47))["stance"], "NEUTRAL")  # +0.59%

    def test_the_charts_issued_forecast_is_the_value_the_dashboard_uses(self):
        data = payload(0.0, value=80.0)
        data["unified_data"] = {"predicted": {"future": {"by_horizon": {"1w": {"value": 81.0}}}}}
        call = lr.extract_call(data)
        self.assertEqual(call["stance"], "LONG")
        self.assertEqual(call["forecast_pct"], 1.25)

    def test_no_lean_without_a_positive_kelly_edge(self):
        # The dashboard sizes from the 1W walk-forward P&L and shows NEUTRAL when Kelly <= 0 (profit
        # factor 0.786 here). The record and the alert must not log a LONG the site never showed.
        call = lr.extract_call(payload(1.2, pnl=NO_EDGE))
        self.assertEqual(call["stance"], "NEUTRAL")
        self.assertFalse(call["eligible_for_validation"])
        self.assertEqual(lr.extract_call(payload(1.2, pnl={}))["stance"], "NEUTRAL")   # no P&L stats, no edge
        self.assertEqual(lr.extract_call(payload(1.2, pnl={"wf_pnl_win_rate": 52, "wf_pnl_profit_factor": "1.2"}))["stance"],
                         "LONG")   # numeric strings count, as with the dashboard's num()

    def test_fields_extracted(self):
        c = lr.extract_call(payload(1.0, price=75.5, symbol="CLQ26"))
        self.assertEqual(c["date"], "2026-06-15")
        self.assertEqual(c["trading_date"], "2026-06-15")
        self.assertEqual(c["entry_at"], "2026-06-15T14:00:00+00:00")
        self.assertEqual(c["contract"], "CLQ26")
        self.assertEqual(c["entry_price"], 75.5)
        self.assertEqual(c["model_bar_date"], "2026-06-12")
        self.assertTrue(c["eligible_for_validation"])
        self.assertFalse(c["resolved"])

    def test_evening_quote_belongs_to_next_session(self):
        c = lr.extract_call(payload(1.0, frozen="2026-06-15T23:00:00+00:00"))   # 19:00 ET
        self.assertEqual(c["trading_date"], "2026-06-16")

    def test_session_comes_from_the_quote_time_not_the_freeze_time(self):
        # A late run freezes at 18:03 EDT, but Yahoo's ~10-min delayed quote is still the 10-14 close.
        c = lr.extract_call(payload(1.0, frozen="2026-10-14T22:03:00+00:00", market_time="2026-10-14T20:59:58Z"))
        self.assertEqual(c["trading_date"], "2026-10-14")
        self.assertEqual(c["entry_at"], "2026-10-14T20:59:58+00:00")
        self.assertFalse(lr.quote_is_from_open_session(c))
        c = lr.extract_call(payload(1.0, frozen="2026-10-14T14:10:00+00:00", market_time="2026-10-14T14:00:00Z"))
        self.assertTrue(lr.quote_is_from_open_session(c))

    def test_forecast_from_an_older_bar_is_flagged(self):
        # Tue 19:30 ET: the bar that settled at 17:00 is not in Yahoo's data, so the model's newest
        # bar is Monday's while the call is Wednesday's.
        evening = payload(1.0, frozen="2026-10-13T23:30:00+00:00", market_time="2026-10-13T23:20:00Z",
                          model_bar="2026-10-12")
        self.assertFalse(lr.forecast_uses_decision_bar(lr.extract_call(evening)))
        self.assertFalse(lr.forecast_uses_decision_bar(lr.extract_call(payload(1.0, model_bar=None))))
        self.assertTrue(lr.forecast_uses_decision_bar(lr.extract_call(payload(1.0))))

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
    def _call(self, stance, entry=80.0, contract="CLN26", session="2026-06-01", basis=None):
        call = {"date": session, "trading_date": session, "contract": contract,
                "entry_price": entry, "stance": stance, "resolved": False,
                "eligible_for_validation": stance != "NEUTRAL"}
        if basis:
            call["contract_basis"] = basis
        return call

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

    def test_resolution_priced_on_the_next_contract_before_the_last_trade_date_is_skipped(self):
        # Entry 09-14 on CLV26; on 09-21 (before CLV26's 09-22 last trade date) Yahoo's live CL=F is
        # CLX26, and the payload says so. Scoring it would book the CLV26/CLX26 spread as a move.
        rec = record([self._call("SHORT", contract="CLV26", entry=101.39, session="2026-09-14", basis="calendar")])
        lr.resolve_calls(rec, "2026-09-21T15:00:00+00:00", "CLX26", 92.16)
        self.assertTrue(rec["calls"][0].get("skipped_contract_roll"))
        self.assertNotIn("hit", rec["calls"][0])

    def test_call_entered_on_the_quoted_next_contract_before_the_roll_is_scored(self):
        # Entered 09-18 at a CLX26 price labelled CLX26 (checked against the quote), resolved 09-25 on
        # CLX26: one contract throughout. The calendar alone (CLV26 until 09-22) used to skip it.
        rec = record([self._call("LONG", contract="CLX26", entry=96.72, session="2026-09-18", basis="quote_match")])
        lr.resolve_calls(rec, "2026-09-25T15:00:00+00:00", "CLX26", 92.41)
        c = rec["calls"][0]
        self.assertNotIn("skipped_contract_roll", c)
        self.assertFalse(c["hit"])
        self.assertAlmostEqual(c["realized_pct"], (92.41 / 96.72 - 1) * 100, places=3)

    def test_roll_is_detected_even_when_labels_agree(self):
        # The real 2026-09-16 call: labelled CLX26 by the old heuristic but priced from CLV26. Rows
        # whose label was not checked against the quote keep the calendar check.
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

    def test_independent_hit_rate_is_over_the_independent_calls(self):
        # 90 daily LONG calls, every 5th a miss: 80% over all (overlapping) calls, but the 18
        # non-overlapping calls the gate counts are exactly the misses.
        start = date(2026, 7, 1)
        calls = []
        for i in range(90):
            entry = contract_calendar.shift_business_days(start, i)
            calls.append({"resolved": True, "hit": i % 5 != 0, "stance": "LONG", "eligible_for_validation": True,
                          "date": entry.isoformat(), "trading_date": entry.isoformat(),
                          "resolution_trading_date": contract_calendar.shift_business_days(entry, 5).isoformat()})
        s = lr.summarize(record(calls))
        self.assertEqual(s["hit_rate_pct"], 80.0)
        self.assertEqual(s["n_independent_directional"], 18)
        self.assertEqual(s["hit_rate_independent_pct"], 0.0)
        self.assertIsNone(lr.summarize(record([]))["hit_rate_independent_pct"])

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

    def _run(self, tmp, frozen, price=80.0, **kwargs):
        data_path = Path(tmp) / "data.json"
        data_path.write_text(json.dumps(payload(0.2, price=price, frozen=frozen, **kwargs)), encoding="utf-8")
        with (
            mock.patch.object(lr, "RECORD_PATH", Path(tmp) / "record.json"),
            mock.patch.object(lr, "public_json_path", return_value=data_path),
            mock.patch("sys.argv", ["live_record.py"]),
        ):
            lr.main()
        return json.loads((Path(tmp) / "record.json").read_text(encoding="utf-8"))

    def _seed(self, tmp, calls):
        (Path(tmp) / "record.json").write_text(json.dumps(record(calls)), encoding="utf-8")

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

    def test_a_finished_sessions_quote_is_neither_recorded_nor_resolved_after_the_reopen(self):
        # The run started late and froze at 18:03 EDT, with Yahoo's delayed quote still the 10-14
        # close. It used to record session 10-15's call at that price and resolve the 10-08 call
        # (due 10-15) at it: a four-session return labelled as five.
        with TemporaryDirectory() as tmp:
            due = {"date": "2026-10-08", "trading_date": "2026-10-08", "contract": "CLX26", "entry_price": 90.0,
                   "stance": "LONG", "resolved": False, "eligible_for_validation": True}
            self._seed(tmp, [due])
            rec = self._run(tmp, "2026-10-14T22:03:00+00:00", price=88.40, symbol="CLX26",
                            market_time="2026-10-14T20:59:58Z")
            self.assertEqual(rec["calls"], [due])   # nothing recorded, nothing resolved
            # The next run with a quote from the 10-15 session resolves it in that session.
            rec = self._run(tmp, "2026-10-15T14:10:00+00:00", price=89.10, symbol="CLX26",
                            market_time="2026-10-15T14:00:00Z")
            self.assertEqual(rec["calls"][0]["resolution_trading_date"], "2026-10-15")
            self.assertEqual(rec["calls"][0]["resolution_price"], 89.10)
            self.assertEqual(rec["calls"][1]["trading_date"], "2026-10-15")

    def test_evening_forecast_is_left_to_the_first_run_after_midnight(self):
        with TemporaryDirectory() as tmp:
            due = {"date": "2026-10-07", "trading_date": "2026-10-07", "contract": "CLX26", "entry_price": 90.0,
                   "stance": "NEUTRAL", "resolved": False, "eligible_for_validation": False}
            self._seed(tmp, [due])
            # Tue 19:30 ET: Wednesday's session, forecast from Monday's bar. The 10-07 call (due in
            # Wednesday's session) is resolved, but no call is recorded from this forecast.
            rec = self._run(tmp, "2026-10-13T23:30:00+00:00", price=92.10, symbol="CLX26",
                            market_time="2026-10-13T23:20:00Z", model_bar="2026-10-12")
            self.assertEqual(len(rec["calls"]), 1)
            self.assertEqual(rec["calls"][0]["resolution_trading_date"], "2026-10-14")
            # Wed 00:30 ET: Tuesday's bar is in the data; this run records Wednesday's call.
            rec = self._run(tmp, "2026-10-14T04:30:00+00:00", price=92.20, symbol="CLX26",
                            market_time="2026-10-14T04:20:00Z", model_bar="2026-10-13")
            self.assertEqual([c["trading_date"] for c in rec["calls"]], ["2026-10-07", "2026-10-14"])
            self.assertEqual(rec["calls"][1]["model_bar_date"], "2026-10-13")
            self.assertEqual(rec["calls"][1]["entry_at"], datetime(2026, 10, 14, 4, 20).isoformat() + "+00:00")


if __name__ == "__main__":
    unittest.main()
