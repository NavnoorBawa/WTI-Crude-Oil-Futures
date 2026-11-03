"""scripts/readme_blocks.py: the generated docs print p- and q-values as the site does.

The site's fmtPValue (src/lib/format.js) keeps a value off a threshold it is not at (0.0996 is not "0.100"); the
docs' _pthr follows the same rule, so a q-value reads the same in RESULTS and on the page.
"""

import json
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
        if not any(m["fluctuation_qlike_vs_har_iv"].get("not_computed") for m in rb._stability_models(self.lab)):
            self.assertNotIn("— marks", self.patched(rb.stability_block))     # no dash, no legend
        fl = self.model("combo")["fluctuation_qlike_vs_har_iv"]
        for key in ("sup", "sign", "at", "p_value"):
            fl[key] = None
        fl["search_adjusted"]["p_value"] = None
        fl["robustness"]["by_grid"] = {"outside_5pct": None}
        table = self.patched(rb.stability_block)
        line = next(l for l in table.splitlines() if l.startswith("| Combination |"))
        self.assertNotIn("None", line)
        self.assertTrue(line.rstrip(" |").endswith("—"), line)
        self.assertIn("— marks a value the lab could not compute", table)
        self.assertNotIn("Combination's", table)                    # its block names no not_computed reason

    def test_a_surviving_conditional_row_is_never_counted_as_restating_dm(self):
        def separate():
            # The sentence the ledger's surviving conditional rows call for, named by the lab's labels.
            cond = [r for r in self.ledger["rows"]
                    if r["family"] == "conditional predictive ability" and r.get("survives_bh_5pct")]
            labels = [self.model(r["id"].removeprefix("vol_gw_"))["label"] for r in cond]
            self.assertTrue(labels)
            one = len(labels) == 1
            return (f"The Giacomini-White conditional {'row' if one else 'rows'} against {rb._join(labels)} "
                    f"{'asks' if one else 'ask'} a separate question")

        text = self.patched(rb.ledger_reading)
        self.assertIn("fluctuation rows among them test a model the full-sample DM test already separates", text)
        self.assertIn(separate(), text)
        self.assertNotIn("all 9 of them", text)
        # One more surviving conditional row, as a result PR that adds a lab model can bring: the sentence names it too.
        extra = next(r for r in self.ledger["rows"] if r["family"] == "conditional predictive ability"
                     and r["q_value_bh"] is not None and not r["survives_bh_5pct"])
        extra["survives_bh_5pct"] = True
        text = self.patched(rb.ledger_reading)
        self.assertIn(separate(), text)
        self.assertIn(self.model(extra["id"].removeprefix("vol_gw_"))["label"], separate())

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
        dist = [r for r in self.ledger["rows"] if r["family"] in ("predictive distribution", "distribution calibration")]
        added = [r for r in dist if r["q_value_bh"] is not None]
        self.assertIn(f"The {len(dist)} rows on the whole predictive distribution (section 2.9) add {len(added)} to the "
                      "family (the challenger's", text)
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
        # The counts run over the decided rows: all of the stream unless a committed registration waits.
        counted = (f"{online['rows_in_stream']} rows" if online["rows_decided"] == online["rows_in_stream"]
                   else f"{online['rows_decided']} decided rows")
        self.assertIn(f"LORD++ rejects {len(online['lordpp']['rejected'])} of the {counted} in its stream", text)
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
        # Git dates the batches; the order within a batch is the code's, chosen after the p-values were known.
        self.assertIn("entry batches dated from the git history; within each batch the order is the ledger's code "
                      "order, fixed after their p-values were known", text)
        self.assertNotIn("reconstructed", text)

    def test_pending_and_withdrawn_registrations(self):
        # Synthetic rows after the committed stream, first in line: committed registrations still pending are set
        # aside, so this case does not depend on which registrations are committed.
        self.ledger["rows"] = [r for r in self.ledger["rows"]
                               if not (r["status"] == "registered" and not r["registration"]["withdrawn"])]
        last = max(self.ledger["rows"], key=lambda r: r["online_position"] or 0)
        pending = {**last, "id": "reg_pending", "status": "registered", "online_position": last["online_position"] + 1,
                   "online_p": None, "online_alpha_lordpp": 0.001, "online_alpha_saffron": 0.06,
                   "online_rejected_lordpp": None, "online_rejected_saffron": None}
        later = {**pending, "id": "reg_later", "online_position": last["online_position"] + 2,
                 "online_alpha_lordpp": None, "online_alpha_saffron": None}
        self.ledger["rows"] += [pending, later]
        s = self.ledger["summary"]
        s.update(registered=["reg_pending", "reg_later"], registered_withdrawn=[*s["registered_withdrawn"], "reg_gone"])
        s["online"]["waiting_on"] = "reg_pending"
        s["online"]["lordpp"]["next_level"] = s["online"]["saffron"]["next_level"] = None
        text = self.patched(rb.ledger_online)
        # Its SAFFRON level, 0.06, is above the 5% target: the caveat follows the level for as long as the row waits.
        self.assertIn("2 registered rows wait for their results (reg_pending and reg_later). The levels reg_pending will "
                      "be tested at are fixed: 0.001 by LORD++ and 0.06 by SAFFRON, which is above 5% after the recent "
                      "rejections: a SAFFRON rejection with p at 5% or more would not be support. Rows after reg_pending "
                      "have no online decision until it resolves.", text)
        pending["online_alpha_saffron"] = 0.04
        self.assertIn("The levels reg_pending will be tested at are fixed: 0.001 by LORD++ and 0.04 by SAFFRON. Rows "
                      "after reg_pending", self.patched(rb.ledger_online))
        gone = s["registered_withdrawn"]
        if len(gone) == 1:
            self.assertIn("1 registration was withdrawn (reg_gone) and spends its online level at p = 1 (not counted "
                          "as tested).", text)
        else:
            self.assertIn(f"{len(gone)} registrations were withdrawn ({rb._join(gone)}) and spend their online level "
                          "at p = 1 (not counted as tested).", text)
        self.assertIn("The next registered row's level is not fixed until reg_pending resolves.", text)

    def test_the_abstract_and_the_block_count_tested_rows(self):
        s = self.ledger["summary"]
        self.assertIn(f"All {s['rows_tested']} hypotheses the project has tested", self.patched(rb.abstract))
        if not s["registered"]:
            self.assertNotIn("registered and wait", self.patched(rb.abstract))
        # One more pending row, added to whatever the committed ledger holds.
        s["rows"] += 1
        s["registered"] = [*s["registered"], "reg_pending"]
        s["by_status"]["registered"] += 1
        s["by_family"]["volatility forecast"]["by_status"]["registered"] += 1
        pending = len(s["registered"])
        abstract = self.patched(rb.abstract)
        self.assertIn(f"All {s['rows_tested']} hypotheses the project has tested", abstract)
        self.assertIn("1 more is registered and waits for its result." if pending == 1 else
                      f"{pending} more are registered and wait for their results.", abstract)
        block = self.patched(rb.ledger_block)
        self.assertIn(f"{s['by_status']['retracted']} retracted", block)
        self.assertIn(f"{pending} registered (results pending)", block)
        self.assertIn("| Retracted | Registered | Supported, surviving BH |", block)

    def test_a_withdrawn_registration_is_counted_the_same_way_everywhere(self):
        """Built by the ledger itself: a withdrawn registration is neither pending nor tested, in the block, the
        abstract and the online paragraph alike."""
        from datetime import date, timedelta
        from unittest import mock

        from backend import research_ledger as rl

        day = (date.fromisoformat(rl.latest_legacy_entry()) + timedelta(days=1)).isoformat()
        withdrawn = rl.Row(id="reg_gone", family="volatility forecast", hypothesis="A withdrawn registration.",
                           test="a two-sided test whose p-value the artifact stores", source=rl.LAB,
                           statistic=("statistic", "experiment_gone.stat"), p=rl.Field("experiment_gone.p_value"),
                           sided="two", expects=rl.POSITIVE,
                           registration=rl.Registration(day, "experiment_gone.p_value", "abandoned before it ran"))
        base = [row for row in rl.ledger_rows() if row.registration is None]
        with mock.patch.object(rl, "ORDER_DRAWS", 3):
            self.ledger = json.loads(json.dumps(rl.build(rows=[*base, withdrawn])))
        s = self.ledger["summary"]
        self.assertEqual((s["registered"], s["registered_withdrawn"]), ([], ["reg_gone"]))
        block = self.patched(rb.ledger_block)
        self.assertIn("1 registered and withdrawn before its result existed.", block)
        self.assertNotIn("results pending", block)
        abstract = self.patched(rb.abstract)
        self.assertIn(f"All {s['rows'] - 1} hypotheses the project has tested", abstract)
        self.assertIn("1 registration was withdrawn before its result existed.", abstract)
        self.assertNotIn("registered and wait", abstract)
        self.assertIn("1 registration was withdrawn (reg_gone) and spends its online level at p = 1 (not counted as "
                      "tested).", self.patched(rb.ledger_online))

    def test_registered_distribution_rows_are_not_counted_in_the_family(self):
        """Built by the ledger itself: a model registered through the distribution families (HANDOFF's route) adds rows
        to those families but none to the multiplicity family until its results exist; the reading counts them apart."""
        import contextlib
        from datetime import date, timedelta
        from unittest import mock

        from backend import research_ledger as rl

        key, day = "zz_test_model", max([(date.fromisoformat(rl.latest_legacy_entry()) + timedelta(days=1)).isoformat(),
                                         *(r.on for r in rl.REGISTERED.values())])
        base = f"distribution.models[key={key}]"
        regs = {f"vol_{tag}_{key}": rl.Registration(day, f"{base}.dm_vs_har_iv.{score}.p_value")
                for tag, score, _ in rl._SCORES}
        regs[f"vol_berkowitz_{key}"] = rl.Registration(day, f"{base}.berkowitz.by_grid")
        with contextlib.ExitStack() as stack:
            for patch in (mock.patch.dict(rl.REGISTERED, regs),
                          mock.patch.dict(rl.REGISTERED_DISTRIBUTION_MODELS, {key: "Test model T"}),
                          mock.patch.object(rl, "ORDER_DRAWS", 3)):
                stack.enter_context(patch)
            plain = json.loads(json.dumps(rl.build()))
            regs[f"vol_berkowitz_{key}"] = rl.Registration(day, f"{base}.berkowitz.by_grid", "abandoned")
            for rid in list(regs)[:3]:
                regs[rid] = rl.Registration(day, regs[rid].field, "abandoned")
            with mock.patch.dict(rl.REGISTERED, regs):
                gone = json.loads(json.dumps(rl.build()))
        for ledger in (plain, gone):
            self.ledger = ledger
            dist = [r for r in ledger["rows"] if r["family"] in ("predictive distribution", "distribution calibration")]
            added = [r for r in dist if r["q_value_bh"] is not None]
            picked = sum(r["selected_after_search"] for r in dist)
            registered = [r for r in dist if r["status"] == "registered"]
            waiting = sum(not r["registration"]["withdrawn"] for r in registered)
            self.assertEqual(len(dist) - len(added), picked + len(registered))     # the four added rows among them
            self.assertGreaterEqual(waiting if ledger is plain else len(registered) - waiting, 4)
            text = self.patched(rb.ledger_reading)
            self.assertIn(f"The {len(dist)} rows on the whole predictive distribution (section 2.9) add {len(added)} "
                          f"to the family (the challenger's {picked} are left out; ", text)
            if waiting:
                self.assertIn(f"; {waiting} registered rows wait for their results", text)
            if len(registered) > waiting:
                self.assertIn(f"; {len(registered) - waiting} registered rows were withdrawn before their results "
                              "existed", text)
            self.assertIn(f"The multiplicity adjustments run over {ledger['summary']['rows_in_multiplicity_family']} "
                          "rows", self.patched(rb.ledger_block))

    def test_a_surviving_row_behind_a_pending_registration_is_neither_rejected_nor_missed(self):
        """Built by the ledger itself (a reviewer's case): a model registered through the lab families waits, and a row
        registered after it already has a result that survives Benjamini-Hochberg. That row has no online decision
        yet, so it is counted as neither a rejection nor a miss, and the counts run over the decided rows."""
        import contextlib
        import copy
        from unittest import mock

        from backend import research_ledger as rl
        from tests.test_research_ledger import TEST_LABEL, TEST_MODEL, _register_model, _registration_day

        day = _registration_day()
        later = rl.Row(id="reg_later", family="volatility forecast", hypothesis="A later registered hypothesis.",
                       test="a two-sided test whose p-value the artifact stores", source=rl.LAB,
                       statistic=("statistic", "experiment_later.stat"), p=rl.Field("experiment_later.p_value"),
                       sided="two", expects=rl.POSITIVE)
        defining = rl._vol_rows

        def rows():
            return [*defining(), later]

        rows.__name__ = defining.__name__
        lab = copy.deepcopy(rl.load_artifact(rl.LAB))
        lab["experiment_later"] = {"p_value": 0.0001, "stat": 3.9}
        with contextlib.ExitStack() as stack:
            for patch in (*_register_model(TEST_MODEL, TEST_LABEL, day, distribution=False),
                          mock.patch.dict(rl.REGISTERED, {"reg_later": rl.Registration(day, "experiment_later.p_value")}),
                          mock.patch.object(rl, "_ROW_FUNCTIONS",
                                            tuple(rows if fn is defining else fn for fn in rl._ROW_FUNCTIONS)),
                          mock.patch.object(rl, "ORDER_DRAWS", 3)):
                stack.enter_context(patch)
            self.ledger = json.loads(json.dumps(rl.build(load=lambda name: lab if name == rl.LAB
                                                          else rl.load_artifact(name))))
        by_id = {r["id"]: r for r in self.ledger["rows"]}
        s, o = self.ledger["summary"], self.ledger["summary"]["online"]
        r = by_id["reg_later"]
        self.assertEqual((r["status"], r["survives_bh_5pct"], r["online_rejected_lordpp"], r["online_rejected_saffron"]),
                         ("supported", True, None, None))
        bh = s["supported_surviving_bh_5pct"]
        decided = [rid for rid in bh if by_id[rid]["online_rejected_lordpp"] is not None]
        missed = [rid for rid in decided if not by_id[rid]["online_rejected_lordpp"]]
        saff_in = [rid for rid in o["saffron"]["rejected"] if rid in bh]
        k = o["rows_in_stream"] - o["rows_decided"]
        self.assertGreater(k, 1)
        text = self.patched(rb.ledger_online)
        self.assertIn(f"LORD++ rejects {len(o['lordpp']['rejected'])} of the {o['rows_decided']} decided rows in its "
                      f"stream and SAFFRON {len(o['saffron']['rejected'])} ({k} more wait for a registered result).", text)
        self.assertNotIn(f"of the {o['rows_in_stream']} rows", text)
        self.assertIn(f"{len(missed)} supported rows that survive Benjamini-Hochberg and have an online decision are not "
                      "rejected by LORD++", text)
        self.assertIn(f"SAFFRON rejects {len(saff_in)} of the {len(decided)} supported rows that survive "
                      "Benjamini-Hochberg and have an online decision", text)
        undecided = [rid for rid in bh if rid not in decided]
        self.assertIn("reg_later", undecided)
        self.assertIn(f"{rb._join(undecided)} {'survives' if len(undecided) == 1 else 'survive'} Benjamini-Hochberg but "
                      f"{'has' if len(undecided) == 1 else 'have'} no online decision yet: "
                      f"{'it follows' if len(undecided) == 1 else 'they follow'} {o['waiting_on']} in the stream, which "
                      "waits for its result.", text)

    def test_a_registered_result_the_lab_could_not_compute_is_named_as_such(self):
        """Built by the ledger itself: a model registered through the lab families whose fluctuation test the lab could
        not compute (HANDOFF's result PR). The row is inconclusive with no p-value; the counts say so instead of giving
        it the 5%-10% band, the reading says it is tested online at p = 1, and section 2.8's dashes have a legend."""
        import contextlib
        from unittest import mock

        from backend import research_ledger as rl
        from tests.test_research_ledger import (TEST_LABEL, TEST_MODEL, _lab_with_model, _loader, _model_registrations,
                                                _registration_day)

        regs = _model_registrations(TEST_MODEL, _registration_day(), distribution=False)
        self.lab = _lab_with_model(TEST_MODEL, TEST_LABEL, fluctuation_computed=False)
        with contextlib.ExitStack() as stack:
            for patch in (mock.patch.dict(rl.REGISTERED, regs),
                          mock.patch.object(rl, "LAB_COMPARED", (*rl.LAB_COMPARED, TEST_MODEL)),
                          mock.patch.object(rl, "ORDER_DRAWS", 3)):
                stack.enter_context(patch)
            self.ledger = json.loads(json.dumps(rl.build(load=_loader(self.lab))))
        rid = f"vol_fluct_{TEST_MODEL}"
        r = self.row(rid)
        self.assertEqual((r["status"], r["p_value"], r["online_p"]), ("inconclusive", None, 1.0))
        b = self.ledger["summary"]["by_status"]
        # This row, and any the committed ledger already holds.
        none = [x["id"] for x in self.ledger["rows"] if x["status"] == "inconclusive" and x["p_value"] is None]
        self.assertIn(rid, none)
        n, one = len(none), len(none) == 1
        block = self.patched(rb.ledger_block)
        self.assertIn(f"{b['inconclusive']} inconclusive ({b['inconclusive'] - n} with 0.05 ≤ p < 0.10; {n} whose test "
                      f"the artifact could not compute, with no p-value: {rb._join(none)})", block)
        self.assertNotIn(f"{b['inconclusive']} inconclusive (0.05 ≤ p < 0.10)", block)
        self.assertIn(f"The Inconclusive column counts {rb._join(none)}, whose test the artifact could not compute "
                      f"({'it has' if one else 'they have'} no p-value, and in the online stream "
                      f"{'it is' if one else 'each is'} tested at p = 1).", block)
        reading = self.patched(rb.ledger_reading)
        self.assertIn("has no p-value: the artifact could not compute its test, so it is outside the multiplicity "
                      "family; in the online stream it is tested at p = 1, as a retracted row is, so it spends its level "
                      "and earns none." if one else "have no p-value: the artifact could not compute their test, so they "
                      "are outside the multiplicity family; in the online stream each is tested at p = 1", reading)
        self.assertIn(rid, reading)
        table = self.patched(rb.stability_block)
        line = next(x for x in table.splitlines() if x.startswith(f"| {TEST_LABEL} |"))
        self.assertIn("—", line)
        self.assertIn("— marks a value the lab could not compute: ", table)
        self.assertIn(f"{TEST_LABEL}'s fluctuation test (a window's variance is not positive)", table)
        self.assertIn("; a ledger row whose test could not be computed has no p-value and no q.", table)
        for name in ("stability_reading", "ledger_online", "abstract"):                  # the other blocks still build
            self.assertTrue(self.patched(getattr(rb, name)), name)

    def test_the_retracted_rows_spend_their_level_online(self):
        self.assertIn("in the online stream each is tested at p = 1, so it spends its level and earns none",
                      self.patched(rb.ledger_reading))

