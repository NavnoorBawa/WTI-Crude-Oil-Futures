"""scripts/readme_blocks.py: the generated docs print p- and q-values as the site does.

The site's fmtPValue (src/lib/format.js) keeps a value off a threshold it is not at (0.0996 is not "0.100"); the
docs' _pthr follows the same rule, so a q-value reads the same in RESULTS and on the page.
"""

import unittest

from scripts import readme_blocks as rb


class ThresholdAwarePTest(unittest.TestCase):
    def test_three_decimals_unless_that_reaches_a_threshold(self):
        for value, text in ((0.123456, "0.123"), (0.0833, "0.083"), (0.277, "0.277"), (0.0548, "0.055"),
                            (0.05, "0.050"), (0.1, "0.100"), (0.09, "0.090")):
            with self.subTest(value=value):
                self.assertEqual(rb._pthr(value), text)

    def test_digits_are_added_to_stay_off_a_threshold(self):
        for value, text in ((0.0996, "0.0996"), (0.0496, "0.0496"), (0.0096, "0.0096"), (0.9996, "0.9996")):
            with self.subTest(value=value):
                self.assertEqual(rb._pthr(value), text)

    def test_beyond_four_digits_it_says_which_side(self):
        self.assertEqual(rb._pthr(0.00999), "< 0.01")
        self.assertEqual(rb._pthr(0.0499999), "< 0.05")
        self.assertEqual(rb._pthr(1.0 - 1e-7), "< 1")
        self.assertEqual(rb._pthr(0.10000004), "> 0.1")
        self.assertEqual(rb._pthr(0.0005), "< 0.001")
        self.assertEqual(rb._pthr(None), "—")
        self.assertEqual(rb._peq_thr(0.00999), "p < 0.01")
        self.assertEqual(rb._peq_thr(0.0996), "p = 0.0996")


if __name__ == "__main__":
    unittest.main()


class StabilityBlocksTest(unittest.TestCase):
    """RESULTS section 2.8 is generated from the artifact and the ledger, worded by the flags they hold (ROADMAP 2.3)."""

    def setUp(self):
        import copy

        self.lab = copy.deepcopy(rb._lab())
        self.ledger = copy.deepcopy(rb._ledger())

    def patched(self, function):
        from unittest import mock

        with mock.patch.object(rb, "_lab", lambda: self.lab), mock.patch.object(rb, "_ledger", lambda: self.ledger):
            return function()

    def model(self, key):
        return next(m for m in self.lab["leaderboard"]["models"] if m["key"] == key)

    def row(self, rid):
        return next(r for r in self.ledger["rows"] if r["id"] == rid)

    def test_an_artifact_without_the_block_says_nothing(self):
        del self.lab["stability"]
        self.assertEqual(self.patched(rb.stability_block), "")
        self.assertEqual(self.patched(rb.stability_reading), "")
        self.assertNotIn("Giacomini", self.patched(rb.robustness_block))

    def test_every_model_has_a_row_with_its_ledger_q_and_the_challenger_is_excluded(self):
        text = self.patched(rb.stability_block)
        for m in self.lab["leaderboard"]["models"]:
            if m["key"] == self.lab["config"]["champion"]:
                continue
            line = next(l for l in text.splitlines() if l.startswith(f"| {m['label']} |"))
            if m["key"] == self.lab["config"]["challenger"]:
                self.assertEqual(line.count("excluded"), 2)
            else:
                self.assertIn(rb._pthr(self.row(f"vol_fluct_{m['key']}")["q_value_bh"]), line)
        self.assertIn(f"±{rb._fmt(self.lab['stability']['fluctuation']['critical_values']['five_pct'], 2)}", text)
        self.assertIn("approximate", text)

    def test_each_fluctuation_reading_follows_its_flags(self):
        text = self.patched(rb.stability_reading)
        self.assertIn("OVX-only regression lower", text)
        self.assertIn("inconclusive (p = 0.056", text)
        self.assertIn("is nested in the model of record", text)
        self.assertIn("not new evidence", text)
        self.assertNotRegex(text, r"better in|better during|\bwon\b|no window shows|adds when|premium")
        # Move the OVX-only regression's adjusted p below 0.05: the reading upgrades only as far as the ledger flag.
        fl = self.model("ovx_only")["fluctuation_qlike_vs_har_iv"]
        fl["search_adjusted"]["p_value"] = 0.03
        self.row("vol_fluct_ovx_only").update(q_value_bh=0.08, survives_bh_5pct=False)
        text = self.patched(rb.stability_reading)
        self.assertIn("significant at 5% (p = 0.030; it does not survive the ledger's Benjamini-Hochberg adjustment, "
                      "q = 0.080)", text)
        # Above 0.10 it is not significant, whatever the window.
        fl["search_adjusted"]["p_value"] = 0.2
        self.assertIn("after the adjustment for the window length it is not significant (p = 0.200)",
                      self.patched(rb.stability_reading))

    def test_a_robustness_form_in_another_band_is_named(self):
        fl = self.model("raw_ovx")["fluctuation_qlike_vs_har_iv"]
        fl["robustness"]["bartlett_40"]["p_value"] = 0.04
        fl["robustness"]["bartlett_40"]["at"] = "2020-04-01"
        self.assertIn("p = 0.040 with each window's variance at 40 Bartlett lags, largest |t| in the window ending "
                      "2020-04-01", self.patched(rb.stability_reading))

    def test_the_abstract_names_the_challenger_s_three_rows(self):
        text = self.patched(rb.abstract)
        self.assertIn("The challenger's three pairwise tests (full-sample, conditional and over-time) are left out", text)
        self.assertIn("published as context and not registered", text)
        self.ledger["summary"]["selected_after_search"] = ["vol_challenger_qlike"]
        self.assertIn("The challenger's pairwise test is left out of that family", self.patched(rb.abstract))
