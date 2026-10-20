"""Network-free tests for backend/research_ledger.py.

The ledger may only state numbers its artifacts hold: every statistic and p-value input is re-read
from the artifact field the row names, the committed ledger must equal a fresh build (so a changed
artifact without a regenerated ledger fails here), and the multiplicity adjustments are checked
against statsmodels.
"""

import copy
import json
import unittest

import numpy as np
from statsmodels.stats.multitest import multipletests

from backend import research_ledger as rl
from backend.safe_paths import data_json_path


def _committed():
    return json.loads(data_json_path(rl.ARTIFACT).read_text(encoding="utf-8"))


class LedgerIsCurrentTest(unittest.TestCase):
    def test_committed_ledger_equals_a_fresh_build(self):
        fresh = json.loads(json.dumps(rl.build()))
        self.assertEqual(fresh, _committed(), "artifacts changed: run python -m backend.research_ledger")

    def test_every_number_is_read_from_its_artifact(self):
        ledger = _committed()
        self.assertEqual(len({r["id"] for r in ledger["rows"]}), len(ledger["rows"]))
        for row in ledger["rows"]:
            with self.subTest(row=row["id"]):
                doc = rl.load_artifact(row["artifact"]["path"].removeprefix("data/"))
                self.assertEqual(rl.resolve(doc, row["artifact"]["statistic_field"]), row["statistic"]["value"])
                for path, value in row["artifact"]["p_value_fields"].items():
                    self.assertEqual(rl.resolve(doc, path), value)
                for label, path in row["artifact"]["context_fields"].items():
                    self.assertEqual(rl.resolve(doc, path), row["context"][label])
                if len(row["artifact"]["p_value_fields"]) == 1 and row["p_value"] is not None:
                    stored = next(iter(row["artifact"]["p_value_fields"].values()))
                    if isinstance(stored, float) and 0.0 <= stored <= 1.0:
                        self.assertEqual(row["p_value"], stored)

    def test_rows_cover_every_research_family(self):
        families = {r["family"] for r in _committed()["rows"]}
        self.assertEqual(families, {"direction model", "leak anatomy", "carry", "variance risk premium",
                                    "volatility forecast", "value at risk", "supply shock"})

    def test_statuses_follow_the_rules(self):
        for row in _committed()["rows"]:
            with self.subTest(row=row["id"]):
                self.assertIn(row["status"], rl.STATUSES)
                if row["retraction_reason"]:
                    self.assertEqual(row["status"], "retracted")
                elif row["status"] == "supported":
                    self.assertLess(row["p_value"], rl.ALPHA)
                    self.assertLess(row["p_two_sided"], 2 * rl.ALPHA + 1e-12)


class MultiplicityTest(unittest.TestCase):
    def test_bh_and_holm_match_statsmodels(self):
        p = np.random.default_rng(0).uniform(0, 0.2, 60) ** 2
        p[[3, 7]] = p[5]                                          # ties
        np.testing.assert_allclose(rl.benjamini_hochberg(p), multipletests(p, method="fdr_bh")[1])
        np.testing.assert_allclose(rl.holm(p), multipletests(p, method="holm")[1])

    def test_committed_adjustments_are_consistent(self):
        rows = [r for r in _committed()["rows"] if r["p_two_sided"] is not None]
        p2 = np.array([r["p_two_sided"] for r in rows])
        np.testing.assert_allclose([r["q_value_bh"] for r in rows], multipletests(p2, method="fdr_bh")[1])
        np.testing.assert_allclose([r["p_holm"] for r in rows], multipletests(p2, method="holm")[1])
        for r in rows:
            self.assertEqual(r["survives_bh_5pct"], r["q_value_bh"] < rl.ALPHA)
            self.assertEqual(r["clears_hlz_t3"], r["t_equivalent"] > rl.HLZ_T)

    def test_a_wrong_way_result_is_never_a_discovery(self):
        # Two-sided p = 0.001 but the estimate points against the hypothesis.
        self.assertAlmostEqual(rl._directional_one_sided(0.001, "two", False), 0.9995)
        self.assertEqual(rl._directional_one_sided(0.001, "two", True), 0.0005)
        self.assertEqual(rl._directional_one_sided(0.03, "one", None), 0.03)

    def test_status_rules(self):
        row = rl.Row(id="x", family="f", hypothesis="h", test="t", source="s", statistic=("s", "a"), p=None, sided="two")
        self.assertEqual(rl._status(row, 0.01, True), "supported")
        self.assertEqual(rl._status(row, 0.01, False), "rejected")
        self.assertEqual(rl._status(row, 0.07, None), "inconclusive")
        self.assertEqual(rl._status(row, 0.2, None), "rejected")
        self.assertEqual(rl._status(row, None, None), "inconclusive")
        retracted = rl.Row(id="x", family="f", hypothesis="h", test="t", source="s", statistic=("s", "a"),
                           p=None, sided="two", retracted="leak")
        self.assertEqual(rl._status(retracted, 1e-9, True), "retracted")

    def test_summary_lists_match_the_rows(self):
        ledger = _committed()
        by_id = {r["id"]: r for r in ledger["rows"]}
        s = ledger["summary"]
        self.assertEqual(sum(s["by_status"].values()), len(ledger["rows"]))
        for rid in s["supported_surviving_bh_5pct"]:
            self.assertEqual(by_id[rid]["status"], "supported")
            self.assertTrue(by_id[rid]["survives_bh_5pct"])
        self.assertEqual(set(s["supported"]),
                         set(s["supported_surviving_bh_5pct"]) | set(s["supported_failing_bh_5pct"]))

    def test_family_summary_adds_up_to_the_overall_one(self):
        ledger = _committed()
        s = ledger["summary"]
        by_family = s["by_family"]
        self.assertEqual(set(by_family), {r["family"] for r in ledger["rows"]})
        for status in rl.STATUSES:
            self.assertEqual(sum(f["by_status"][status] for f in by_family.values()), s["by_status"][status])
        self.assertEqual(sum(f["rows"] for f in by_family.values()), s["rows"])
        self.assertEqual(sum(f["supported_surviving_bh_5pct"] for f in by_family.values()),
                         len(s["supported_surviving_bh_5pct"]))
        self.assertEqual(sum(f["supported_clearing_hlz_t3"] for f in by_family.values()),
                         len(s["supported_clearing_hlz_t3"]))


class RowDefinitionTest(unittest.TestCase):
    def setUp(self):
        self.rows = {r["id"]: r for r in _committed()["rows"]}

    def test_shock_groups_need_both_placebo_tests(self):
        # The study's verdict needs the unmatched and the year-matched placebo test; so does the ledger.
        shock = [r for r in self.rows.values() if r["family"] == "supply shock" and len(r["artifact"]["p_value_fields"]) > 1]
        self.assertEqual(len(shock), 16)
        for row in shock:
            with self.subTest(row=row["id"]):
                fields = row["artifact"]["p_value_fields"]
                self.assertTrue(any("year_matched" in path for path in fields))
                self.assertEqual(row["p_value"], max(fields.values()))

    def test_sharpe_rows_use_the_non_normal_standard_error(self):
        for rid in ("vrp_short_vol_swap_sharpe", "vrp_short_variance_sharpe"):
            (path,) = self.rows[rid]["artifact"]["p_value_fields"]
            self.assertIn("sharpe_positive_test", path)
            self.assertIn("excess_kurtosis", self.rows[rid]["context"])

    def test_retraction_reasons_are_filled_from_the_artifact(self):
        for row in self.rows.values():
            if row["retraction_reason"]:
                self.assertNotIn("{", row["retraction_reason"], row["id"])
        row = self.rows["vol_dir_vs_mean_reversion"]
        self.assertEqual(row["status"], "retracted")
        for label in ("noise_floor_har_pct", "noise_floor_mean_reversion_pct"):
            self.assertIn(f"{row['context'][label]}%", row["retraction_reason"])
        # The reason holds only while HAR beats the rule with nothing to forecast.
        self.assertGreater(row["context"]["noise_floor_har_pct"], row["context"]["noise_floor_mean_reversion_pct"])

    def test_two_configuration_rows_compare_the_configurations(self):
        for rid, other in (("dir_macro_uplift", "results.no_macro_1w.direction_accuracy"),
                           ("dir_same_day_context", "results.lagged_1d.direction_accuracy")):
            row = next(r for r in rl.ledger_rows() if r.id == rid)
            self.assertEqual(row.beats, other)
            self.assertIsNone(row.expects)


class ResolveTest(unittest.TestCase):
    DOC = {"a": {"list": [{"key": "x", "level": 0.05, "v": 1}, {"key": "x", "level": 0.01, "v": 2}],
                 "0": {"k": 3}}}

    def test_selectors(self):
        self.assertEqual(rl.resolve(self.DOC, "a.list[key=x,level=0.01].v"), 2)
        self.assertEqual(rl.resolve(self.DOC, "a.list[1].v"), 2)
        self.assertEqual(rl.resolve(self.DOC, "a.0.k"), 3)

    def test_missing_or_ambiguous_fields_fail_loudly(self):
        for path in ("a.missing", "a.list[key=x].v", "a.list[key=y].v", "a.list[9].v"):
            with self.subTest(path=path), self.assertRaises(rl.FieldError):
                rl.resolve(self.DOC, path)

    def test_a_renamed_artifact_field_breaks_the_build(self):
        real = rl.load_artifact

        def drop_field(name):
            doc = copy.deepcopy(real(name))
            if name == "carry_signal_test.json":
                del doc["post_2007"]["spread_p_value_one_sided"]
            return doc

        with self.assertRaises(rl.FieldError):
            rl.build(load=drop_field)

    def test_only_ledger_sources_are_readable(self):
        with self.assertRaises(ValueError):
            rl.load_artifact("../README.md")
        with self.assertRaises(ValueError):
            rl.load_artifact("signal_state.json")


if __name__ == "__main__":
    unittest.main()
