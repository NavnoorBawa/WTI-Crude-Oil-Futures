"""scripts/readme_blocks.py: the generated docs print p- and q-values as the site does.

The site's fmtPValue (src/lib/format.js) keeps a value off a threshold it is not at (0.0996 is not "0.100"); the
docs' _pthr follows the same rule, so a q-value reads the same in RESULTS and on the page.
"""

import json
import re
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


class ModelInputsTest(unittest.TestCase):
    """Every lab model says what it reads at the decision. A result PR that adds a model to vol_lab.MODELS without an
    entry in MODEL_INPUTS fails here, naming the file; for a model in the field (every lab model but a timing control)
    the doc rebuild fails on it too, and for a timing control only this test does (docs/HANDOFF.md)."""

    def test_every_lab_model_has_its_inputs(self):
        from backend import vol_lab

        missing = [key for key in vol_lab.MODEL_KEYS if key not in rb.MODEL_INPUTS]
        self.assertEqual(missing, [], "scripts/readme_blocks.py MODEL_INPUTS does not describe these vol_lab.MODELS "
                                      "keys: add what each reads at the decision (returns, range, implied)")
        self.assertEqual(sorted(rb.MODEL_INPUTS), sorted(vol_lab.MODEL_KEYS), "MODEL_INPUTS lists a model the lab "
                                                                               "does not have")
        for key, inputs in rb.MODEL_INPUTS.items():
            with self.subTest(model=key):
                self.assertTrue(inputs)
                self.assertLessEqual(inputs, {"returns", "range", "implied"})

    def test_the_doc_rebuild_reads_the_entry_of_every_model_in_the_field(self):
        """The failure mode docs/HANDOFF.md states: without its entry, a model in the field stops the README's key
        findings; a timing control's entry is never read there, so the test above is what catches it."""
        from unittest import mock

        from backend import vol_lab

        self.assertTrue(set(vol_lab.MODEL_KEYS) - set(vol_lab.FEASIBLE))       # the lab has a timing control
        for key in vol_lab.MODEL_KEYS:
            with self.subTest(model=key), mock.patch.dict(rb.MODEL_INPUTS):
                del rb.MODEL_INPUTS[key]
                if key in vol_lab.FEASIBLE:
                    with self.assertRaisesRegex(KeyError, "scripts/readme_blocks.py MODEL_INPUTS"):
                        rb.key_findings()
                else:
                    self.assertTrue(rb.key_findings())


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

    def _record_lower(self, score, low, high):
        """The models, the challenger aside, that the model of record scores lower than on `score` with low <= p < high,
        in the artifact's order: the group the reading lists, read from the artifact."""
        chall = self.lab["config"]["challenger"]
        return [m for m in self.lab["distribution"]["models"] if m["dm_vs_har_iv"] is not None and m["key"] != chall
                and low <= m["dm_vs_har_iv"][score]["p_value"] < high and m["dm_vs_har_iv"][score]["stat"] < 0]

    def _listed(self, group, score):
        return (f"{rb._dist_names(group)} "
                f"({', '.join(rb._peq_thr(m['dm_vs_har_iv'][score]['p_value']) for m in group)})")

    def _assert_verdicts(self):
        """Each verdict at its strength, the groups and the adjustment's words built from whatever models the artifact
        scores and the ledger's rows for them (a distribution result PR can add one)."""
        text = self.patched(rb.distribution_reading)
        middle = self._record_lower("crps_grid", 0.05, 0.10)
        self.assertIn("garch", [m["key"] for m in middle])                         # the artifact's case
        self.assertIn(f"the record's lower score is inconclusive against {self._listed(middle, 'crps_grid')}", text)
        sig = self._record_lower("pinball_90", 0.0, 0.05)
        self.assertTrue(sig)                                                       # the artifact's case
        self.assertIn(f"the model of record scores significantly lower than {self._listed(sig, 'pinball_90')}", text)
        chall = self.lab["config"]["challenger"]
        others = [m for m in self.lab["distribution"]["models"] if m["dm_vs_har_iv"] is not None
                  and m["key"] not in (chall, *(x["key"] for x in sig))
                  and m["dm_vs_har_iv"]["pinball_90"]["p_value"] < 0.05]
        if not others:              # every significant row runs the way the ledger states: the plain clause
            rows = [self.row(f"vol_pinball90_{m['key']}") for m in sig]
            kept = [m for m, r in zip(sig, rows) if r["survives_bh_5pct"]]
            n = len(sig)
            if not kept:
                who = "It does not survive" if n == 1 else ("Neither survives" if n == 2 else "None survives")
            elif len(kept) == n:
                who = "It survives" if n == 1 else ("Both survive" if n == 2 else "All survive")
            else:
                who = f"Only {rb._dist_names(kept)} {'survives' if len(kept) == 1 else 'survive'}"
            self.assertIn(f"{who} the ledger's Benjamini-Hochberg adjustment "
                          f"(q = {', '.join(rb._pthr(r['q_value_bh']) for r in rows)})", text)
        self.assertIn("so this is not new evidence", text)
        self.assertIn("with its 9 Bartlett lags", text)
        # A p-value that moves below 5% moves its words: the CRPS difference against GARCH becomes significant.
        garch = self.dist("garch")["dm_vs_har_iv"]
        garch["crps_grid"]["p_value"] = 0.03
        self.row("vol_crps_garch")["survives_bh_5pct"] = False
        lower = self._record_lower("crps_grid", 0.0, 0.05)
        self.assertIn("garch", [m["key"] for m in lower])
        self.assertIn(f"On CRPS over 19 quantiles, the model of record scores significantly lower than "
                      f"{self._listed(lower, 'crps_grid')}", self.patched(rb.distribution_reading))

    def test_each_verdict_is_worded_at_its_strength(self):
        self._assert_verdicts()

    def test_a_distribution_result_for_a_new_model_is_worded_with_the_others(self):
        """Built by the ledger itself: HANDOFF's distribution result PR for a model registered through the distribution
        families, scored like GARCH and with no leaderboard entry (its key moved into DISTRIBUTION_MODELS). Its rows
        join every group they belong to, and the adjustment's words count them, with no test edited after the result."""
        import contextlib
        from unittest import mock

        from backend import research_ledger as rl
        from tests.test_research_ledger import (TEST_LABEL, TEST_MODEL, _lab_with_model, _loader, _model_registrations,
                                                _registration_day)

        self.lab = _lab_with_model(TEST_MODEL, TEST_LABEL, leaderboard=False)
        with contextlib.ExitStack() as stack:
            for patch in (mock.patch.dict(rl.REGISTERED, _model_registrations(TEST_MODEL, _registration_day(), lab=False)),
                          mock.patch.object(rl, "DISTRIBUTION_MODELS", (*rl.DISTRIBUTION_MODELS, TEST_MODEL)),
                          mock.patch.object(rl, "ORDER_DRAWS", 3)):
                stack.enter_context(patch)
            self.ledger = json.loads(json.dumps(rl.build(load=_loader(self.lab))))
        self.assertEqual(self.row(f"vol_pinball90_{TEST_MODEL}")["status"], self.row("vol_pinball90_garch")["status"])
        self.assertIn(TEST_MODEL, [m["key"] for m in self._record_lower("pinball_90", 0.0, 0.05)])
        self._assert_verdicts()

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

    def _crps(self, text):
        return text[text.index("On CRPS over 19 quantiles"):text.index("On the 10% pinball loss")]

    def _assert_context_verdicts(self):
        """The context's Berkowitz bands and log-scale groups, built from whatever models the artifact scores."""
        for key in ("garch", "gjr"):
            self.dist(key)["alternatives"]["static_t"]["berkowitz_p"] = 0.3
        self.dist("ovx_only")["dm_vs_har_iv"]["crps_grid_log"].update(stat=2.5, p_value=0.02)
        text = self.patched(rb.distribution_reading)
        static = [m for m in self.lab["distribution"]["models"] if "alternatives" in m]
        for words, low, high in (("Berkowitz rejects", 0.0, 0.05), ("Berkowitz is inconclusive for", 0.05, 0.10),
                                 ("Berkowitz does not reject at 10%", 0.10, 1.01)):
            group = [m for m in static if low <= m["alternatives"]["static_t"]["berkowitz_p"] < high]
            if group:
                ps = ", ".join(rb._peq_thr(m["alternatives"]["static_t"]["berkowitz_p"]) for m in group)
                self.assertIn(f"{words} {rb._dist_names(group)} ({ps})", text)
            if words == "Berkowitz does not reject at 10%":
                self.assertTrue({"garch", "gjr"} <= {m["key"] for m in group})       # the case set above
        self.assertNotIn("Berkowitz rejects GARCH", text)
        chall = self.lab["config"]["challenger"]
        log_mod = [m for m in self.lab["distribution"]["models"] if m["dm_vs_har_iv"] is not None and m["key"] != chall
                   and m["dm_vs_har_iv"]["crps_grid_log"]["p_value"] < 0.05 and m["dm_vs_har_iv"]["crps_grid_log"]["stat"] > 0]
        self.assertIn("ovx_only", [m["key"] for m in log_mod])                           # the case set above
        ps = ", ".join(rb._peq_thr(m["dm_vs_har_iv"]["crps_grid_log"]["p_value"]) for m in log_mod)
        self.assertIn(f"on the log scale {rb._dist_names(log_mod)} {'scores' if len(log_mod) == 1 else 'score'} a "
                      f"significantly lower CRPS than the model of record ({ps})", text)

    def test_context_verdicts_follow_their_p_values_and_sides(self):
        self._assert_context_verdicts()

    def _assert_the_registered_direction(self, crps):
        """The significant CRPS results in `crps`: the adjustment is cited only for those that run the way the ledger
        states, and names them when the sentence also lists one that does not."""
        chall = self.lab["config"]["challenger"]
        sig = [m for m in self.lab["distribution"]["models"] if m["dm_vs_har_iv"] is not None and m["key"] != chall
               and m["dm_vs_har_iv"]["crps_grid"]["p_value"] < 0.05]
        registered = [m for m in sig if rb._dist_registered(m, m["dm_vs_har_iv"]["crps_grid"])]
        opposite = [m for m in sig if m not in registered]
        if opposite:
            self.assertIn(f"The difference against {rb._dist_names(opposite)} runs opposite to the direction the ledger "
                          "states", crps)
        if not registered:
            self.assertNotIn("Benjamini-Hochberg", crps)      # no adjusted support cited for an unregistered direction
            return
        qs = ", ".join(rb._pthr(self.row(f"vol_crps_{m['key']}")["q_value_bh"]) for m in registered)
        self.assertIn(f" the ledger's Benjamini-Hochberg adjustment (q = {qs}).", crps)
        if opposite:                                          # the adjustment names its own subject
            subject = rb._dist_names(registered)
            self.assertTrue(f"The result against {subject} " in crps or f"Of the results against {subject}, " in crps
                            or f"The results against {subject} " in crps, crps)
            self.assertNotIn(" It does not survive", crps)
            self.assertNotIn(" It survives", crps)

    def _assert_a_result_against_the_registered_direction(self):
        self.dist("quantile_har")["dm_vs_har_iv"]["crps_grid"].update(stat=-2.6, p_value=0.03)
        crps = self._crps(self.patched(rb.distribution_reading))
        lower = self._record_lower("crps_grid", 0.0, 0.05)
        self.assertIn("quantile_har", [m["key"] for m in lower])                         # the case set above
        self.assertIn(f"the model of record scores significantly lower than {self._listed(lower, 'crps_grid')}", crps)
        self._assert_the_registered_direction(crps)
        # With a registered result beside it, the adjustment names its own subject.
        self.dist("garch")["dm_vs_har_iv"]["crps_grid"].update(stat=-2.5, p_value=0.03)
        crps = self._crps(self.patched(rb.distribution_reading))
        lower = self._record_lower("crps_grid", 0.0, 0.05)
        self.assertTrue({"garch", "quantile_har"} <= {m["key"] for m in lower})
        self.assertIn(f"the model of record scores significantly lower than {self._listed(lower, 'crps_grid')}", crps)
        self._assert_the_registered_direction(crps)
        self.assertIn("Benjamini-Hochberg", crps)

    def test_a_result_against_the_registered_direction_cites_no_adjusted_support(self):
        self._assert_a_result_against_the_registered_direction()

    def _tails(self):
        """The models whose lowest, highest or both outer tenths lie above their chance bands, read from the artifact."""
        groups = {"both": [], "lowest": [], "highest": []}
        for m in self.lab["distribution"]["models"]:
            share, center, band = m["pit_decile_share_pct"], m["pit_chance_center_pct"], m["pit_chance_band_pct"]
            low, high = (share[i] > center[i] + band[i] for i in (0, -1))
            if low or high:
                groups["both" if low and high else "lowest" if low else "highest"].append(m)
        return groups

    def _assert_the_tails(self, text):
        groups = self._tails()
        for m in groups["both"]:
            sh = m["pit_decile_share_pct"]
            self.assertIn(f"{rb._dist_name(m)}'s two outer tenths both lie above their chance bands ({sh[0]:.1f}% and "
                          f"{sh[-1]:.1f}% of outcomes", text)
        for side, i in (("lowest", 0), ("highest", -1)):
            group = groups[side]
            if group:
                one = len(group) == 1
                vals = " and ".join(f"{m['pit_decile_share_pct'][i]:.1f}%" for m in group)
                self.assertIn(f"the {side} {'tenth' if one else 'tenths'} of {rb._dist_names(group)} "
                              f"{'lies above its chance band' if one else 'lie above their chance bands'} ({vals})", text)
        if not any(groups.values()):
            self.assertNotIn("Descriptively (no bin is tested)", text)

    def _assert_the_tail_reading(self):
        self._assert_the_tails(self.patched(rb.distribution_reading))
        self.assertIn("ewma", [m["key"] for m in self._tails()["both"]])               # the artifact's case
        self.assertNotIn(self.lab["config"]["champion"], [m["key"] for g in self._tails().values() for m in g])
        gjr = self.dist("gjr")
        gjr["pit_decile_share_pct"] = [4.0, *gjr["pit_decile_share_pct"][1:9], 24.0]
        self.assertIn("gjr", [m["key"] for m in self._tails()["highest"]])
        self._assert_the_tails(self.patched(rb.distribution_reading))

    def test_the_tail_reading_reads_the_chance_bands(self):
        self._assert_the_tail_reading()

    def _with_a_new_model_of_its_own(self):
        """The distribution result PR for a model registered through the distribution families, its values its own (not
        GARCH's): the model of record significantly lower on CRPS, the new model significantly lower on the log scale,
        its highest tenth above its chance band (its shares still summing to 100) and its static-t Berkowitz test
        inconclusive. The ledger is built from that artifact."""
        import contextlib
        from unittest import mock

        from backend import research_ledger as rl
        from tests.test_research_ledger import (TEST_LABEL, TEST_MODEL, _lab_with_model, _loader, _model_registrations,
                                                _registration_day)

        self.lab = _lab_with_model(TEST_MODEL, TEST_LABEL, leaderboard=False)
        entry = self.dist(TEST_MODEL)
        entry["dm_vs_har_iv"]["crps_grid"].update(stat=-2.3, p_value=0.031)
        entry["dm_vs_har_iv"]["crps_grid_log"].update(stat=2.2, p_value=0.04)
        entry["alternatives"]["static_t"]["berkowitz_p"] = 0.07
        share = [10.0] * 10
        share[-1] = entry["pit_chance_center_pct"][-1] + entry["pit_chance_band_pct"][-1] + 3.0
        share[1:9] = [(100.0 - share[0] - share[-1]) / 8] * 8
        entry["pit_decile_share_pct"] = share
        with contextlib.ExitStack() as stack:
            for patch in (mock.patch.dict(rl.REGISTERED, _model_registrations(TEST_MODEL, _registration_day(), lab=False)),
                          mock.patch.object(rl, "DISTRIBUTION_MODELS", (*rl.DISTRIBUTION_MODELS, TEST_MODEL)),
                          mock.patch.object(rl, "ORDER_DRAWS", 3)):
                stack.enter_context(patch)
            self.ledger = json.loads(json.dumps(rl.build(load=_loader(self.lab))))
        self.assertEqual(self.row(f"vol_crps_{TEST_MODEL}")["status"], "supported")
        return TEST_MODEL

    def test_the_distribution_readings_for_a_new_model_with_values_of_its_own(self):
        """Every reading test above, on the artifact a distribution result PR builds: no test is edited after the
        result, whatever its values."""
        for check in (self._assert_verdicts, self._assert_context_verdicts,
                      self._assert_a_result_against_the_registered_direction, self._assert_the_tail_reading):
            with self.subTest(check=check.__name__):
                key = self._with_a_new_model_of_its_own()
                text = self.patched(rb.distribution_reading)
                self.assertIn(key, [m["key"] for m in self._record_lower("crps_grid", 0.0, 0.05)])
                self.assertIn(key, [m["key"] for m in self._tails()["highest"]])
                self.assertIn(f"Berkowitz is inconclusive for {self.dist(key)['label']} (p = 0.070)", text)
                check()

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
    model = StabilityBlocksTest.model

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

    def _assert_pending_and_withdrawn_registrations(self):
        # Synthetic rows after the committed stream, first in line: committed registrations (pending or withdrawn) are
        # set aside, so this case does not depend on which registrations are committed.
        self.ledger["rows"] = [r for r in self.ledger["rows"] if r["status"] != "registered"]
        last = max(self.ledger["rows"], key=lambda r: r["online_position"] or 0)
        position = last["online_position"]
        gone_reg = {**(last["registration"] or {}), "withdrawn": "abandoned before it ran"}
        early = {**last, "id": "reg_gone_early", "status": "registered", "online_position": position + 1,
                 "online_p": 1.0, "online_alpha_lordpp": 0.001, "online_alpha_saffron": 0.02,
                 "online_rejected_lordpp": False, "online_rejected_saffron": False, "registration": gone_reg}
        pending = {**last, "id": "reg_pending", "status": "registered", "online_position": position + 2,
                   "online_p": None, "online_alpha_lordpp": 0.001, "online_alpha_saffron": 0.06,
                   "online_rejected_lordpp": None, "online_rejected_saffron": None}
        later = {**pending, "id": "reg_later", "online_position": position + 3,
                 "online_alpha_lordpp": None, "online_alpha_saffron": None}
        withdrawn = {**later, "id": "reg_gone", "online_position": position + 4, "registration": gone_reg}
        searched = {**withdrawn, "id": "reg_gone_searched", "online_position": None, "selected_after_search": True}
        self.ledger["rows"] += [early, pending, later, withdrawn, searched]
        s = self.ledger["summary"]
        s.update(registered=["reg_pending", "reg_later"], registered_withdrawn=["reg_gone"])
        s["online"]["waiting_on"] = "reg_pending"
        s["online"]["lordpp"]["next_level"] = s["online"]["saffron"]["next_level"] = None
        text = self.patched(rb.ledger_online)
        # Its SAFFRON level, 0.06, is above the 5% target: the caveat follows the level for as long as the row waits.
        self.assertIn("2 registered rows wait for their results (reg_pending and reg_later). The levels reg_pending will "
                      "be tested at are fixed: 0.001 by LORD++ and 0.06 by SAFFRON, which is above 5% after the recent "
                      "rejections: a SAFFRON rejection with p at 5% or more would not be support. Rows after reg_pending "
                      "have no online decision until it resolves.", text)
        self.assertIn("The next registered row's level is not fixed until reg_pending resolves.", text)
        pending["online_alpha_saffron"] = 0.04
        self.assertIn("The levels reg_pending will be tested at are fixed: 0.001 by LORD++ and 0.04 by SAFFRON. Rows "
                      "after reg_pending", self.patched(rb.ledger_online))
        # The withdrawal sentence by stream (in it, or selected after a search) and by level (set, or after the
        # pending row): a withdrawn registration behind a pending one has no level yet, so it has spent nothing.
        later_spend = "will spend its online level at p = 1 once every earlier row is decided"
        both = "spend (or, after a pending row, will spend) their online level at p = 1"
        outside = "selected after a search, is outside the online stream."
        for gone, sentence in (
                (["reg_gone_early"], "1 registration was withdrawn (reg_gone_early) and spends its online level at "
                                     "p = 1 (not counted as tested)."),
                (["reg_gone"], f"1 registration was withdrawn (reg_gone) and {later_spend} (not counted as tested)."),
                (["reg_gone_early", "reg_gone"], f"2 registrations were withdrawn (reg_gone_early and reg_gone) and "
                                                 f"{both} (not counted as tested)."),
                (["reg_gone_searched"], "1 registration was withdrawn (reg_gone_searched), not counted as tested; "
                                        "selected after a search, it is outside the online stream, so it spends no "
                                        "level there."),
                (["reg_gone", "reg_gone_searched"], f"2 registrations were withdrawn (reg_gone and reg_gone_searched), "
                                                    f"not counted as tested: reg_gone {later_spend}, and "
                                                    f"reg_gone_searched, {outside}"),
                (["reg_gone_early", "reg_gone", "reg_gone_searched"],
                 f"3 registrations were withdrawn (reg_gone_early, reg_gone and reg_gone_searched), not counted as "
                 f"tested: reg_gone_early and reg_gone {both}, and reg_gone_searched, {outside}")):
            with self.subTest(withdrawn=gone):
                s["registered_withdrawn"] = gone
                text = self.patched(rb.ledger_online)
                self.assertIn(sentence, text)
                if "reg_gone" in gone:
                    self.assertNotIn("reg_gone spends its online level", text)

    def test_pending_and_withdrawn_registrations(self):
        self._assert_pending_and_withdrawn_registrations()

    def test_pending_and_withdrawn_registrations_beside_committed_withdrawals(self):
        """Built by the ledger itself: the committed ledger holds withdrawn registrations, one in the stream and one
        selected after a search (a new family's challenger row), as after HANDOFF's withdrawal PR; the synthetic
        case above does not depend on them."""
        import dataclasses
        from datetime import date, timedelta
        from unittest import mock

        from backend import research_ledger as rl

        day = (date.fromisoformat(rl.latest_legacy_entry()) + timedelta(days=1)).isoformat()

        def registration(rid, **changes):
            row = rl.Row(id=rid, family="volatility forecast", hypothesis=f"A withdrawn registration, {rid}.",
                         test="a two-sided test whose p-value the artifact stores", source=rl.LAB,
                         statistic=("statistic", f"experiment_{rid}.stat"), p=rl.Field(f"experiment_{rid}.p_value"),
                         sided="two", expects=rl.POSITIVE,
                         registration=rl.Registration(day, f"experiment_{rid}.p_value", "abandoned before it ran"))
            return dataclasses.replace(row, **changes)

        committed = [registration("reg_committed_gone"),
                     registration("reg_committed_searched", selected_after_search=True)]
        with mock.patch.object(rl, "ORDER_DRAWS", 3):
            self.ledger = json.loads(json.dumps(rl.build(rows=[*rl.ledger_rows(), *committed])))
        self.assertEqual(self.ledger["summary"]["registered_withdrawn"][-2:], [r.id for r in committed])
        self._assert_pending_and_withdrawn_registrations()

    def test_a_withdrawn_registration_behind_a_pending_one_will_spend_its_level(self):
        """Built by the ledger itself: a registration withdrawn while an earlier one waits has no level and no decision
        yet (DATA_CONTRACT), and the copy says it will spend its level, not that it has."""
        import dataclasses
        from datetime import date, timedelta
        from unittest import mock

        from backend import research_ledger as rl

        day = (date.fromisoformat(rl.latest_legacy_entry()) + timedelta(days=1)).isoformat()

        def registration(rid, withdrawn=None):
            return rl.Row(id=rid, family="volatility forecast", hypothesis=f"A registration, {rid}.",
                          test="a two-sided test whose p-value the artifact stores", source=rl.LAB,
                          statistic=("statistic", f"experiment_{rid}.stat"), p=rl.Field(f"experiment_{rid}.p_value"),
                          sided="two", expects=rl.POSITIVE,
                          registration=rl.Registration(day, f"experiment_{rid}.p_value", withdrawn))

        base = [row for row in rl.ledger_rows() if row.registration is None]
        rows = [registration("reg_waits"), registration("reg_gone", "abandoned before it ran")]
        with mock.patch.object(rl, "ORDER_DRAWS", 3):
            self.ledger = json.loads(json.dumps(rl.build(rows=[*base, *rows])))
        gone = self.row("reg_gone")
        self.assertEqual((gone["online_p"], gone["online_alpha_lordpp"], gone["online_rejected_lordpp"],
                          gone["online_alpha_saffron"], gone["online_rejected_saffron"]), (1.0, None, None, None, None))
        text = self.patched(rb.ledger_online)
        self.assertIn("1 registration was withdrawn (reg_gone) and will spend its online level at p = 1 once every "
                      "earlier row is decided (not counted as tested).", text)
        self.assertNotIn("spends its online level", text)
        # Once the earlier registration is withdrawn too, both levels are set and both are spent.
        rows[0] = dataclasses.replace(rows[0], registration=rl.Registration(day, rows[0].registration.field,
                                                                             "abandoned before it ran"))
        with mock.patch.object(rl, "ORDER_DRAWS", 3):
            self.ledger = json.loads(json.dumps(rl.build(rows=[*base, *rows])))
        self.assertIsNotNone(self.row("reg_gone")["online_alpha_lordpp"])
        self.assertIs(self.row("reg_gone")["online_rejected_lordpp"], False)
        self.assertIn("2 registrations were withdrawn (reg_waits and reg_gone) and spend their online level at p = 1 "
                      "(not counted as tested).", self.patched(rb.ledger_online))

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

    def test_a_withdrawn_registration_selected_after_a_search_spends_no_level(self):
        """Built by the ledger itself: a new family's challenger row is selected after a search, so when its
        registration is withdrawn it stays outside the online stream; the copy never says it is tested at p = 1, and
        the family sentence does not count it among the selected rows it leaves out (it has no p-value)."""
        import dataclasses
        from datetime import date, timedelta
        from unittest import mock

        from backend import research_ledger as rl

        day = (date.fromisoformat(rl.latest_legacy_entry()) + timedelta(days=1)).isoformat()

        def registration(rid, **changes):
            row = rl.Row(id=rid, family="volatility forecast", hypothesis=f"A withdrawn registration, {rid}.",
                         test="a two-sided test whose p-value the artifact stores", source=rl.LAB,
                         statistic=("statistic", f"experiment_{rid}.stat"), p=rl.Field(f"experiment_{rid}.p_value"),
                         sided="two", expects=rl.POSITIVE,
                         registration=rl.Registration(day, f"experiment_{rid}.p_value", "abandoned before it ran"))
            return dataclasses.replace(row, **changes)

        base = [row for row in rl.ledger_rows() if row.registration is None]
        plain, picked = registration("reg_gone"), registration("reg_gone_searched", selected_after_search=True)
        for rows, streamed, searched in (([picked], [], ["reg_gone_searched"]),
                                         ([plain, picked], ["reg_gone"], ["reg_gone_searched"])):
            with self.subTest(rows=[r.id for r in rows]), mock.patch.object(rl, "ORDER_DRAWS", 3):
                self.ledger = json.loads(json.dumps(rl.build(rows=[*base, *rows])))
                s = self.ledger["summary"]
                self.assertEqual(s["registered_withdrawn"], [r.id for r in rows])
                r = self.row("reg_gone_searched")
                self.assertEqual((r["status"], r["online_position"], r["online_p"], r["online_rejected_lordpp"]),
                                 ("registered", None, None, None))
                text = self.patched(rb.ledger_online)
                if not streamed:
                    self.assertIn("1 registration was withdrawn (reg_gone_searched), not counted as tested; selected "
                                  "after a search, it is outside the online stream, so it spends no level there.", text)
                else:
                    self.assertIn("2 registrations were withdrawn (reg_gone and reg_gone_searched), not counted as "
                                  "tested: reg_gone spends its online level at p = 1, and reg_gone_searched, selected "
                                  "after a search, is outside the online stream.", text)
                self.assertNotIn("spend their online level at p = 1", text)
                with_p = [x for x in s["selected_after_search"] if self.row(x)["p_two_sided"] is not None]
                self.assertIn("reg_gone_searched", s["selected_after_search"])
                self.assertNotIn("reg_gone_searched", with_p)
                self.assertIn(f"The multiplicity adjustments run over {s['rows_in_multiplicity_family']} rows: every "
                              f"row with a p-value, the retracted ones included, except {len(with_p)} selected after a "
                              "search (below).", self.patched(rb.ledger_block))

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
        self.assert_registered_uncomputed(challenger_too=False)

    def test_a_registered_uncomputed_result_beside_a_selected_one(self):
        """As above, with the challenger's fluctuation test not computed too: selected after a search, its row is
        outside the online stream, and the copy names the two apart."""
        self.assert_registered_uncomputed(challenger_too=True)

    def assert_registered_uncomputed(self, *, challenger_too):
        import contextlib
        from unittest import mock

        from backend import research_ledger as rl
        from tests.test_research_ledger import (TEST_LABEL, TEST_MODEL, _lab_with_model, _loader, _model_registrations,
                                                _not_computed, _registration_day)

        regs = _model_registrations(TEST_MODEL, _registration_day(), distribution=False)
        self.lab = _lab_with_model(TEST_MODEL, TEST_LABEL, fluctuation_computed=False)
        if challenger_too:
            entry = self.model(rl.CHALLENGER)
            entry["fluctuation_qlike_vs_har_iv"] = _not_computed(entry["fluctuation_qlike_vs_har_iv"])
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
        # This row, and any the committed ledger already holds, split by the online stream: a row selected after a
        # search is outside it.
        uncomputed = [x for x in self.ledger["rows"] if x["status"] == "inconclusive" and x["p_value"] is None]
        none = [x["id"] for x in uncomputed]
        streamed = [x["id"] for x in uncomputed if x["online_position"] is not None]
        searched = [x["id"] for x in uncomputed if x["online_position"] is None and x["selected_after_search"]]
        self.assertEqual(sorted(streamed + searched), sorted(none))
        self.assertIn(rid, streamed)
        if challenger_too:                                 # (the committed artifact may hold more such rows)
            self.assertIn(f"vol_fluct_{rl.CHALLENGER}", searched)
        n, one, k = len(none), len(none) == 1, len(streamed)
        block = self.patched(rb.ledger_block)
        self.assertIn(f"{b['inconclusive']} inconclusive ({b['inconclusive'] - n} with 0.05 ≤ p < 0.10; {n} whose test "
                      f"the artifact could not compute, with no p-value: {rb._join(none)})", block)
        self.assertNotIn(f"{b['inconclusive']} inconclusive (0.05 ≤ p < 0.10)", block)
        reading = self.patched(rb.ledger_reading)
        if not searched:
            self.assertIn(f"The Inconclusive column counts {rb._join(none)}, whose test the artifact could not compute "
                          f"({'it has' if one else 'they have'} no p-value, and in the online stream "
                          f"{'it is' if one else 'each is'} tested at p = 1).", block)
            self.assertIn("has no p-value: the artifact could not compute its test, so it is outside the multiplicity "
                          "family; in the online stream it is tested at p = 1, as a retracted row is, so it spends its "
                          "level and earns none." if one else "have no p-value: the artifact could not compute their "
                          "test, so they are outside the multiplicity family; in the online stream each is tested at "
                          "p = 1", reading)
        else:
            m = len(searched)
            self.assertIn(f"The Inconclusive column counts {rb._join(none)}, whose test the artifact could not compute "
                          f"(they have no p-value; in the online stream {rb._join(streamed)} "
                          f"{'is' if k == 1 else 'are each'} tested at p = 1, and {rb._join(searched)}, selected after a "
                          f"search, {'is' if m == 1 else 'are'} outside both the multiplicity family and the online "
                          "stream).", block)
            self.assertIn(f"{rb._join(none)} have no p-value: the artifact could not compute their test, so they are "
                          f"outside the multiplicity family; in the online stream {rb._join(streamed)} "
                          f"{'is' if k == 1 else 'are each'} tested at p = 1, as a retracted row is, so "
                          f"{'it spends its' if k == 1 else 'each spends its'} level and earns none, and "
                          f"{rb._join(searched)}, selected after a search, {'is' if m == 1 else 'are'} outside the "
                          "online stream too.", reading)
        self.assertIn(rid, reading)
        # The family sentence leaves out only the selected rows that have a p-value.
        s = self.ledger["summary"]
        with_p = [x for x in s["selected_after_search"] if self.row(x)["p_two_sided"] is not None]
        self.assertEqual(s["rows_in_multiplicity_family"], s["rows_with_p_value"] - len(with_p))
        self.assertIn(f"The multiplicity adjustments run over {s['rows_in_multiplicity_family']} rows: every row with a "
                      "p-value, the retracted ones included" + (f", except {len(with_p)} selected after a search (below)"
                                                                if with_p else "") + ".", block)
        table = self.patched(rb.stability_block)
        line = next(x for x in table.splitlines() if x.startswith(f"| {TEST_LABEL} |"))
        self.assertIn("—", line)
        self.assertIn("— marks a value the lab could not compute: ", table)
        self.assertIn(f"{TEST_LABEL}'s fluctuation test (a window's variance is not positive)", table)
        self.assertIn("; a ledger row whose test could not be computed has no p-value and no q.", table)
        for name in ("stability_reading", "ledger_online", "abstract"):                  # the other blocks still build
            self.assertTrue(self.patched(getattr(rb, name)), name)

    def _not_computed_ledger(self, keys):
        """The ledger built by the ledger itself from the lab with these models' fluctuation tests not computed (the
        lab's null form), added to whatever the committed artifacts hold."""
        from unittest import mock

        from backend import research_ledger as rl
        from tests.test_research_ledger import _not_computed

        for key in keys:
            entry = self.model(key)
            entry["fluctuation_qlike_vs_har_iv"] = _not_computed(entry["fluctuation_qlike_vs_har_iv"])
        with mock.patch.object(rl, "ORDER_DRAWS", 3):
            self.ledger = json.loads(json.dumps(rl.build(load=lambda name: self.lab if name == rl.LAB
                                                         else rl.load_artifact(name))))
        rows = [r for r in self.ledger["rows"] if r["status"] == "inconclusive" and r["p_value"] is None]
        return ([r["id"] for r in rows if r["online_position"] is not None],
                [r["id"] for r in rows if r["online_position"] is None])

    def test_a_selected_row_the_lab_could_not_compute_is_outside_the_stream(self):
        """The challenger's fluctuation test not computed: its row is inconclusive with no p-value, and it was selected
        after a search, so it is outside the online stream as well as the family. The copy says so and never says it
        is tested at p = 1."""
        from backend import research_ledger as rl

        rid = f"vol_fluct_{rl.CHALLENGER}"
        streamed, searched = self._not_computed_ledger([rl.CHALLENGER])
        r = self.row(rid)
        self.assertEqual((r["status"], r["p_value"], r["selected_after_search"], r["online_position"], r["online_p"]),
                         ("inconclusive", None, True, None, None))
        self.assertEqual(searched, [rid])
        block, reading = self.patched(rb.ledger_block), self.patched(rb.ledger_reading)
        if not streamed:                                   # the committed ledger has no other uncomputed row
            self.assertIn(f"The Inconclusive column counts {rid}, whose test the artifact could not compute (it has no "
                          "p-value; selected after a search, it is outside both the multiplicity family and the online "
                          "stream).", block)
            self.assertIn(f"{rid} has no p-value: the artifact could not compute its test, and it was selected after a "
                          "search, so it is outside both the multiplicity family and the online stream.", reading)
        for text in (block, reading):
            sentence = next(x for x in re.split(r"(?<=[.)]) ", text) if rid in x and "no p-value" in x)
            self.assertNotIn(f"{rid} is tested at p = 1", sentence)
            if not streamed:
                self.assertNotIn("p = 1", sentence)

    def test_uncomputed_rows_in_and_out_of_the_stream_are_named_apart(self):
        """One uncomputed row in the stream (garch's) and the challenger's outside it: the p = 1 clause names only the
        rows in the stream."""
        from backend import research_ledger as rl

        rid = f"vol_fluct_{rl.CHALLENGER}"
        streamed, searched = self._not_computed_ledger([rl.CHALLENGER, "garch"])
        self.assertIn("vol_fluct_garch", streamed)
        self.assertEqual(searched, [rid])
        self.assertEqual(self.row("vol_fluct_garch")["online_p"], 1.0)
        both = sorted([*streamed, *searched], key=[r["id"] for r in self.ledger["rows"]].index)
        k = len(streamed)
        self.assertIn(f"The Inconclusive column counts {rb._join(both)}, whose test the artifact could not compute (they "
                      f"have no p-value; in the online stream {rb._join(streamed)} {'is' if k == 1 else 'are each'} "
                      f"tested at p = 1, and {rid}, selected after a search, is outside both the multiplicity family "
                      "and the online stream).", self.patched(rb.ledger_block))
        self.assertIn(f"{rb._join(both)} have no p-value: the artifact could not compute their test, so they are "
                      f"outside the multiplicity family; in the online stream {rb._join(streamed)} "
                      f"{'is' if k == 1 else 'are each'} tested at p = 1, as a retracted row is, so "
                      f"{'it spends its' if k == 1 else 'each spends its'} level and earns none, and {rid}, selected "
                      "after a search, is outside the online stream too.", self.patched(rb.ledger_reading))

    def test_the_retracted_rows_spend_their_level_online(self):
        self.assertIn("in the online stream each is tested at p = 1, so it spends its level and earns none",
                      self.patched(rb.ledger_reading))

