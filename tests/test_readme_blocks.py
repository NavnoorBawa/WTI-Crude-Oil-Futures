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

    def test_the_verdict_words_follow_the_house_thresholds_exactly(self):
        fl = self.model("ovx_only")["fluctuation_qlike_vs_har_iv"]
        fl["search_adjusted"]["p_value"] = 0.05
        self.assertIn("after the adjustment for the window length it is inconclusive (p = 0.050",
                      self.patched(rb.stability_reading))
        fl["search_adjusted"]["p_value"] = 0.10
        self.assertIn("after the adjustment for the window length it is not significant (p = 0.100)",
                      self.patched(rb.stability_reading))

    def test_the_conditional_part_reads_every_band_and_the_coefficient_sentence_needs_every_sign(self):
        import re

        # A conditional p between 0.05 and 0.10 is inconclusive, never left out.
        self.model("har_rv")["gw_qlike_vs_har_iv"]["conditional"]["p_value"] = 0.07
        text = self.patched(rb.stability_reading)
        self.assertRegex(text, r"It is inconclusive against HAR-RV \(p = 0\.070\): not significant at 5%")
        # One significant model whose fitted OVX association is positive: no "against all of them".
        ovx = self.model("ovx_only")["gw_qlike_vs_har_iv"]
        ovx["conditional"]["p_value"] = 0.03
        self.assertGreater(ovx["coef_levels"]["ln_ovx"], 0)
        self.assertNotIn("against all of them", self.patched(rb.stability_reading))
        self.assertFalse(re.search(r"HAR-RV \+ downside semivol \(p = 0\.070", text))

    def test_a_test_that_was_not_computed_prints_dashes(self):
        fl = self.model("combo")["fluctuation_qlike_vs_har_iv"]
        for key in ("sup", "sign", "at", "p_value"):
            fl[key] = None
        fl["search_adjusted"]["p_value"] = None
        fl["robustness"]["by_grid"] = {"outside_5pct": None}
        line = next(l for l in self.patched(rb.stability_block).splitlines() if l.startswith("| Combination |"))
        self.assertNotIn("None", line)
        self.assertTrue(line.rstrip(" |").endswith("—"), line)

    def test_a_surviving_conditional_row_is_never_counted_as_restating_dm(self):
        text = self.patched(rb.ledger_reading)
        self.assertIn("fluctuation rows among them test a model the full-sample DM test already separates", text)
        self.assertIn("The Giacomini-White conditional row against Persistence (RV5) asks a separate question", text)
        self.assertNotIn("all 9 of them", text)

    def test_a_robustness_form_in_another_band_is_named(self):
        fl = self.model("raw_ovx")["fluctuation_qlike_vs_har_iv"]
        fl["robustness"]["bartlett_40"]["p_value"] = 0.04
        fl["robustness"]["bartlett_40"]["at"] = "2020-04-01"
        self.assertIn("p = 0.040 with each window's variance at 40 Bartlett lags, largest |t| in the window ending "
                      "2020-04-01", self.patched(rb.stability_reading))

    def test_the_abstract_names_the_challenger_s_rows_by_kind(self):
        text = self.patched(rb.abstract)
        self.assertIn("The challenger's six pairwise tests (full-sample, conditional, over-time and three on the "
                      "predictive distribution) are left out", text)
        self.assertIn("and the other five have none", text)
        self.assertIn("published as context and not entered in the ledger", text)
        self.ledger["summary"]["selected_after_search"] = ["vol_challenger_qlike", "vol_gw_har_iv_range",
                                                           "vol_fluct_har_iv_range"]
        self.assertIn("The challenger's three pairwise tests (full-sample, conditional and over-time) are left out",
                      self.patched(rb.abstract))
        self.ledger["summary"]["selected_after_search"] = ["vol_challenger_qlike"]
        self.assertIn("The challenger's pairwise test is left out of that family", self.patched(rb.abstract))


class DistributionBlocksTest(StabilityBlocksTest.__base__):
    """RESULTS section 2.9 is generated from the artifact and the ledger, each verdict at its test's strength
    (ROADMAP 2.4)."""

    setUp = StabilityBlocksTest.setUp
    patched = StabilityBlocksTest.patched
    row = StabilityBlocksTest.row

    def dist(self, key):
        return next(m for m in self.lab["distribution"]["models"] if m["key"] == key)

    def test_an_artifact_without_the_block_says_nothing(self):
        del self.lab["distribution"]
        self.assertEqual(self.patched(rb.distribution_block), "")
        self.assertEqual(self.patched(rb.distribution_reading), "")
        self.assertNotIn("Section 2.9", self.patched(rb.robustness_block))
        self.assertNotIn("CRPS", self.patched(rb.abstract))

    def test_every_model_has_its_scores_tests_and_ledger_q(self):
        text = self.patched(rb.distribution_block)
        for m in self.lab["distribution"]["models"]:
            line = next(l for l in text.splitlines()[text.splitlines().index("| Model | CRPS | q | Pinball 10% | q | "
                                                                              "Pinball 90% | q | Berkowitz p (reference) | q |"):]
                        if l.startswith(f"| {m['label']} |"))
            q = rb._pthr(self.row(f"vol_berkowitz_{m['key']}")["q_value_bh"])
            self.assertTrue(line.rstrip(" |").endswith(q), line)
            if m["key"] == self.lab["config"]["challenger"]:
                self.assertEqual(line.count("excluded"), 3, line)
            elif m["key"] == self.lab["config"]["champion"]:
                self.assertIn("| — | — |", line)
        self.assertIn(f"{self.dist('har_iv')['crps_grid_pct']:.3f}", text)

    def test_each_verdict_is_worded_at_its_strength(self):
        text = self.patched(rb.distribution_reading)
        garch = self.dist("garch")["dm_vs_har_iv"]
        self.assertTrue(0.05 <= garch["crps_grid"]["p_value"] < 0.10)
        self.assertIn("the record's lower score is inconclusive against GARCH(1,1)-t", text)
        self.assertIn("the model of record scores significantly lower than GARCH(1,1)-t and EWMA (RiskMetrics)", text)
        self.assertIn("Neither survives the ledger's Benjamini-Hochberg adjustment", text)
        self.assertIn("so this is not new evidence", text)
        self.assertIn("with its 9 Bartlett lags", text)
        # A p-value that moves below 5% moves its words: the CRPS difference against GARCH becomes significant.
        garch["crps_grid"]["p_value"] = 0.03
        self.row("vol_crps_garch")["survives_bh_5pct"] = False
        self.assertIn("On CRPS over 19 quantiles, the model of record scores significantly lower than GARCH(1,1)-t "
                      "(p = 0.030)", self.patched(rb.distribution_reading))

    def test_the_challenger_s_scores_are_worded_by_direction_and_level(self):
        chall = self.dist(self.lab["config"]["challenger"])["dm_vs_har_iv"]
        self.assertIn("HAR-IV-range scores significantly lower than the model of record on the 90% pinball loss",
                      self.patched(rb.distribution_reading))
        chall["pinball_10"].update(stat=-2.4, p_value=0.03)
        chall["crps_grid"].update(stat=2.0, p_value=0.07)
        text = self.patched(rb.distribution_reading)
        self.assertIn("the model of record scores significantly lower than HAR-IV-range on the 10% pinball loss (−2.40, "
                      f"p = 0.030, lower in {chall['pinball_10']['periods_har_iv_lower']} of 12 periods)", text)
        self.assertIn("HAR-IV-range scores lower than the model of record on CRPS over 19 quantiles, inconclusively "
                      "(+2.00, p = 0.070", text)
        self.assertIn("the challenger was picked on this sample, so this is not new evidence", text)

    def test_the_calibration_reading_always_states_the_adjustment(self):
        for row in self.ledger["rows"]:
            if row["family"] == "distribution calibration":
                row["survives_bh_5pct"] = False
        text = self.patched(rb.distribution_reading)
        self.assertIn("none survives the ledger's Benjamini-Hochberg adjustment (q = ", text)
        self.assertNotIn("does not survive it", text)
        self.assertIn("The quantile HAR's rejection rests on one of five grids", text)

    def test_context_verdicts_follow_their_p_values_and_sides(self):
        for key in ("garch", "gjr"):
            self.dist(key)["alternatives"]["static_t"]["berkowitz_p"] = 0.3
        self.dist("ovx_only")["dm_vs_har_iv"]["crps_grid_log"].update(stat=2.5, p_value=0.02)
        text = self.patched(rb.distribution_reading)
        self.assertIn("Berkowitz does not reject at 10% GARCH(1,1)-t and GJR-GARCH(1,1)-t (p = 0.300, p = 0.300)", text)
        self.assertNotIn("Berkowitz rejects GARCH", text)
        self.assertIn("on the log scale OVX-only regression scores a significantly lower CRPS than the model of record", text)

    def test_a_result_against_the_registered_direction_cites_no_adjusted_support(self):
        self.dist("quantile_har")["dm_vs_har_iv"]["crps_grid"].update(stat=-2.6, p_value=0.03)
        text = self.patched(rb.distribution_reading)
        crps = text[text.index("On CRPS over 19 quantiles"):text.index("On the 10% pinball loss")]
        self.assertIn("the model of record scores significantly lower than the quantile HAR (p = 0.030)", crps)
        self.assertIn("The difference against the quantile HAR runs opposite to the direction the ledger states", crps)
        self.assertNotIn("Benjamini-Hochberg", crps)          # no adjusted support cited for an unregistered direction
        # With a registered result beside it, the adjustment names its own subject.
        self.dist("garch")["dm_vs_har_iv"]["crps_grid"].update(stat=-2.5, p_value=0.03)
        crps = (lambda t: t[t.index("On CRPS over 19 quantiles"):t.index("On the 10% pinball loss")])(
            self.patched(rb.distribution_reading))
        self.assertIn("The result against GARCH(1,1)-t does not survive the ledger's Benjamini-Hochberg adjustment", crps)
        self.assertNotIn(" It does not survive", crps)

    def test_the_tail_reading_reads_the_chance_bands(self):
        text = self.patched(rb.distribution_reading)
        self.assertIn("EWMA (RiskMetrics)'s two outer tenths both lie above their chance bands", text)
        self.assertNotIn("HAR-IV (model of record)'s two outer tenths", text)
        gjr = self.dist("gjr")
        gjr["pit_decile_share_pct"] = [4.0, *gjr["pit_decile_share_pct"][1:9], 24.0]
        self.assertIn("the highest tenth of GJR-GARCH(1,1)-t lies above its chance band (24.0%)",
                      self.patched(rb.distribution_reading))

    def test_fragile_t3_flags_are_computed(self):
        text = self.patched(rb.ledger_reading)
        self.assertIn("vol_berkowitz_garch at t = 3.044 with the PIT form chosen after the count PIT's p was seen, "
                      "which gives t = 2.996", text)
        self.assertIn("vol_berkowitz_ovx_only at t = 3.090, which one Monte Carlo standard error", text)
        self.row("vol_berkowitz_garch")["context"]["p_count_pit"] = 0.0001
        one = self.patched(rb.ledger_reading)
        self.assertNotIn("vol_berkowitz_garch at t", one)
        self.assertIn("One of the calibration rows clears the t > 3 hurdle narrowly", one)

    def test_the_ledger_reading_names_rows_that_survive_only_with_the_new_families(self):
        text = self.patched(rb.ledger_reading)
        self.assertIn("The 25 rows on the whole predictive distribution (section 2.9) add 22 to the family", text)
        self.assertNotIn("Without them", text)
        weak = self.row("carry_spread_post2007")
        weak["survives_bh_5pct"] = True                        # a fixture: it could survive only with the new rows
        self.assertIn("Without them, carry_spread_post2007 would not survive it.", self.patched(rb.ledger_reading))
        self.assertIn("Another supported row is not in the table", text)
        self.assertIn("No search-adjusted version of this test exists", text)


class LedgerOnlineBlockTest(StabilityBlocksTest.__base__):
    """RESULTS section 6's online paragraph (ROADMAP 2.12): generated from the ledger's online view, it never lets an
    online rejection read as more than the batch view and the row's own test support."""

    setUp = StabilityBlocksTest.setUp
    patched = StabilityBlocksTest.patched
    row = StabilityBlocksTest.row

    def test_a_saffron_only_rejection_is_not_counted_as_surviving_an_adjustment(self):
        text = self.patched(rb.ledger_online)
        online = self.ledger["summary"]["online"]
        bh = self.ledger["summary"]["supported_surviving_bh_5pct"]
        only = [rid for rid in online["saffron"]["rejected"] if rid not in bh]
        self.assertTrue(only)
        self.assertIn(f"({rb._join(only)}); those {len(only)} are not counted as surviving any adjustment elsewhere", text)
        self.assertIn(f"LORD++ rejects {len(online['lordpp']['rejected'])} of the {online['rows_in_stream']} rows", text)
        self.assertNotIn("significant", text)
        self.assertIn("No row's status depends on either online procedure.", text)

    def test_a_rejection_of_an_unsupported_row_is_not_support(self):
        self.assertNotIn("not supported by its own test", self.patched(rb.ledger_online))
        # An inconclusive row (p from 5% to 10%) that SAFFRON could reject once its level exceeds 5%.
        rid = next(r["id"] for r in self.ledger["rows"] if r["status"] == "inconclusive" and r["online_position"])
        self.ledger["summary"]["online"]["saffron"]["rejected"].append(rid)
        text = self.patched(rb.ledger_online)
        self.assertIn(f"1 row that a procedure rejects is not supported by its own test at 5% ({rid}): SAFFRON's level "
                      "can exceed 5% after recent rejections, and such a rejection is not support.", text)

    def test_the_legacy_order_caveat_and_the_independence_assumption(self):
        text = self.patched(rb.ledger_online)
        sens = self.ledger["summary"]["online"]["legacy_order_sensitivity"]
        self.assertIn(f"The {sens['legacy_rows']} rows in the stream entered before registration began", text)
        self.assertIn(f"LORD++ rejects between {sens['lordpp']['min']} and {sens['lordpp']['max']} of them", text)
        self.assertIn("retrospective reading, not decisions made at the time", text)
        self.assertIn("a reading under that assumption, not a guarantee for this ledger", text)

    def test_pending_and_withdrawn_registrations(self):
        last = max(self.ledger["rows"], key=lambda r: r["online_position"] or 0)
        pending = {**last, "id": "reg_pending", "status": "registered", "online_position": last["online_position"] + 1,
                   "online_p": None, "online_alpha_lordpp": 0.001, "online_alpha_saffron": 0.06,
                   "online_rejected_lordpp": None, "online_rejected_saffron": None}
        later = {**pending, "id": "reg_later", "online_position": last["online_position"] + 2,
                 "online_alpha_lordpp": None, "online_alpha_saffron": None}
        self.ledger["rows"] += [pending, later]
        s = self.ledger["summary"]
        s.update(registered=["reg_pending", "reg_later"], registered_withdrawn=["reg_gone"])
        s["online"]["waiting_on"] = "reg_pending"
        s["online"]["lordpp"]["next_level"] = s["online"]["saffron"]["next_level"] = None
        text = self.patched(rb.ledger_online)
        self.assertIn("2 registered rows wait for their results (reg_pending and reg_later); the level it will be "
                      "tested at is fixed: reg_pending at 0.001 by LORD++ and 0.06 by SAFFRON; rows after reg_pending "
                      "have no online decision until it resolves.", text)
        self.assertIn("1 registration was withdrawn (reg_gone) and counts as tested at p = 1.", text)
        self.assertIn("The next registered row's level is not fixed until reg_pending resolves.", text)

    def test_the_abstract_and_the_block_count_tested_rows(self):
        s = self.ledger["summary"]
        self.assertIn(f"All {s['rows_tested']} hypotheses the project has tested", self.patched(rb.abstract))
        self.assertNotIn("registered and wait", self.patched(rb.abstract))
        s["rows_tested"] -= 1
        s["registered"] = ["reg_pending"]
        s["by_status"]["registered"] = 1
        s["by_family"]["volatility forecast"]["by_status"]["registered"] = 1
        abstract = self.patched(rb.abstract)
        self.assertIn(f"All {s['rows_tested']} hypotheses the project has tested", abstract)
        self.assertIn("1 more is registered and waits for its result.", abstract)
        block = self.patched(rb.ledger_block)
        self.assertIn("9 retracted and 1 registered (results pending).", block)
        self.assertIn("| Retracted | Registered | Supported, surviving BH |", block)
        self.assertNotIn("Registered", self.patched(rb.ledger_block).split("| Family |")[0].replace("1 registered", ""))

    def test_the_retracted_rows_spend_their_level_online(self):
        self.assertIn("in the online stream each is tested at p = 1, so it spends its level and earns none",
                      self.patched(rb.ledger_reading))

