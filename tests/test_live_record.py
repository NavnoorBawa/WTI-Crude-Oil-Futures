"""Network-free unit tests for backend/live_record.py — the git-committed live track record.

This logic decides whether a 1W call is recorded, how it resolves a week later, when a
contract-roll makes it unscoreable, and whether it counts as a hit. It runs in CI every cycle
and was previously untested. All tests operate on plain dicts (no I/O, no network).
"""

import unittest

from backend import live_record as lr


def record(calls):
    return {"calls": calls}


def payload(pct, price=80.0, symbol="CLN26", frozen="2026-06-20T00:00:00+00:00"):
    return {
        "multi_horizon_predictions": {"percentage_changes": {"1w": pct}},
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
        self.assertEqual(lr.extract_call(payload(0.61))["stance"], "LONG")

    def test_fields_extracted(self):
        c = lr.extract_call(payload(1.0, price=75.5, symbol="CLQ26"))
        self.assertEqual(c["date"], "2026-06-20")
        self.assertEqual(c["contract"], "CLQ26")
        self.assertEqual(c["entry_price"], 75.5)
        self.assertFalse(c["resolved"])


class ResolveTest(unittest.TestCase):
    def _call(self, stance, entry=80.0, contract="CLN26", date="2026-06-01"):
        return {"date": date, "contract": contract, "entry_price": entry,
                "stance": stance, "resolved": False}

    def test_not_resolved_before_seven_days(self):
        rec = record([self._call("LONG", date="2026-06-18")])
        lr.resolve_calls(rec, "2026-06-20", "CLN26", 82.0)   # 2 days old
        self.assertFalse(rec["calls"][0]["resolved"])

    def test_long_hits_when_price_rises(self):
        rec = record([self._call("LONG", entry=80.0)])
        lr.resolve_calls(rec, "2026-06-10", "CLN26", 82.0)   # 9 days, same contract, +2.5%
        c = rec["calls"][0]
        self.assertTrue(c["resolved"])
        self.assertTrue(c["hit"])
        self.assertAlmostEqual(c["realized_pct"], 2.5, places=2)

    def test_long_misses_when_price_falls(self):
        rec = record([self._call("LONG", entry=80.0)])
        lr.resolve_calls(rec, "2026-06-10", "CLN26", 78.0)
        self.assertFalse(rec["calls"][0]["hit"])

    def test_short_hits_when_price_falls(self):
        rec = record([self._call("SHORT", entry=80.0)])
        lr.resolve_calls(rec, "2026-06-10", "CLN26", 78.0)
        self.assertTrue(rec["calls"][0]["hit"])

    def test_contract_roll_is_skipped_not_scored(self):
        # A call whose entry contract differs from the current front month spans a roll; the
        # realized move would be contaminated by roll basis, so it is marked skipped, never hit.
        rec = record([self._call("LONG", contract="CLM26")])
        lr.resolve_calls(rec, "2026-06-10", "CLN26", 82.0)
        c = rec["calls"][0]
        self.assertTrue(c["resolved"])
        self.assertTrue(c.get("skipped_contract_roll"))
        self.assertNotIn("hit", c)


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


if __name__ == "__main__":
    unittest.main()
