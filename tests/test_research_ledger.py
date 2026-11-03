"""Network-free tests for backend/research_ledger.py.

The ledger may only state numbers its artifacts hold: every statistic and p-value input is re-read
from the artifact field the row names, the committed ledger must equal a fresh build (so a changed
artifact without a regenerated ledger fails here), and the multiplicity adjustments are checked
against statsmodels. The online procedures (ROADMAP 2.12) are checked against onlineFDR's published
output (tests/fixtures/online_fdr_reference.json), their invariants on random streams, the stream on
the committed ledger, the registered status's lifecycle, and, where the git history is available,
the entry dates, the registrations and the append-only online view against the versions on main.

The committed-equals-fresh test compares floats exactly, the online levels included; it assumes the
platform's libm (Linux and glibc, as CI and the cloud sessions run), where math.log and math.exp are
the same as when the artifact was built.
"""

import copy
import dataclasses
import json
import math
import os
import re
import shutil
import subprocess
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np
from statsmodels.stats.multitest import multipletests

from backend import research_ledger as rl
from backend.safe_paths import data_json_path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _committed():
    return json.loads(data_json_path(rl.ARTIFACT).read_text(encoding="utf-8"))


def _pending(row):
    """A committed row registered and still without a result (or withdrawn before one existed)."""
    return row["status"] == "registered"


class LedgerIsCurrentTest(unittest.TestCase):
    def test_committed_ledger_equals_a_fresh_build(self):
        fresh = json.loads(json.dumps(rl.build()))
        self.assertEqual(fresh, _committed(), "artifacts changed: run python -m backend.research_ledger")

    def test_every_number_is_read_from_its_artifact(self):
        ledger = _committed()
        self.assertEqual(len({r["id"] for r in ledger["rows"]}), len(ledger["rows"]))
        for row in ledger["rows"]:
            if _pending(row):
                continue              # no result to read yet: RegistrationTest checks that its field does not exist
            with self.subTest(row=row["id"]):
                doc = rl.load_artifact(row["artifact"]["path"].removeprefix("data/"))
                self.assertEqual(rl.resolve(doc, row["artifact"]["statistic_field"]), row["statistic"]["value"])
                for path, value in row["artifact"]["p_value_fields"].items():
                    self.assertEqual(rl.resolve(doc, path), value)
                for label, path in row["artifact"]["context_fields"].items():
                    self.assertEqual(rl.resolve(doc, path), row["context"][label])
                for path in row["artifact"]["name_fields"].values():
                    self.assertIn(str(rl.resolve(doc, path)), row["hypothesis"])          # as the ledger formats it
                if len(row["artifact"]["p_value_fields"]) == 1 and row["p_value"] is not None:
                    stored = next(iter(row["artifact"]["p_value_fields"].values()))
                    if isinstance(stored, float) and 0.0 <= stored <= 1.0:
                        self.assertEqual(row["p_value"], stored)

    def test_rows_cover_every_research_family(self):
        families = {r["family"] for r in _committed()["rows"]}
        self.assertEqual(families, {"direction model", "leak anatomy", "carry", "variance risk premium",
                                    "volatility forecast", "value at risk", "forecast interval",
                                    "conditional predictive ability", "stability over time", "predictive distribution",
                                    "distribution calibration", "supply shock"})

    def test_statuses_follow_the_rules(self):
        for row in _committed()["rows"]:
            with self.subTest(row=row["id"]):
                self.assertIn(row["status"], rl.STATUSES)
                if row["status"] == "registered":
                    self.assertIsNone(row["p_value"])
                    self.assertIsNotNone(row["registration"])
                elif row["retraction_reason"]:
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
        ledger = _committed()
        rows = [r for r in ledger["rows"] if r["p_two_sided"] is not None and not r["selected_after_search"]]
        self.assertEqual(len(rows), ledger["summary"]["rows_in_multiplicity_family"])
        p2 = np.array([r["p_two_sided"] for r in rows])
        np.testing.assert_allclose([r["q_value_bh"] for r in rows], multipletests(p2, method="fdr_bh")[1])
        np.testing.assert_allclose([r["p_holm"] for r in rows], multipletests(p2, method="holm")[1])
        for r in rows:
            self.assertEqual(r["survives_bh_5pct"], r["q_value_bh"] < rl.ALPHA)
            self.assertEqual(r["clears_hlz_t3"], r["t_equivalent"] > rl.HLZ_T)

    def test_a_model_selected_after_a_search_is_outside_the_family(self):
        # The challenger is the best of the field on the same sample: its pairwise p ignores the search.
        ledger = _committed()
        selected = [r for r in ledger["rows"] if r["selected_after_search"]]
        expected = ["vol_challenger_qlike", "vol_gw_har_iv_range", "vol_fluct_har_iv_range", "vol_crps_har_iv_range",
                    "vol_pinball10_har_iv_range", "vol_pinball90_har_iv_range"]
        self.assertEqual([r["id"] for r in selected], expected)
        self.assertEqual(ledger["summary"]["selected_after_search"], expected)
        s = ledger["summary"]
        for row in selected:
            for key in ("q_value_bh", "p_holm", "survives_bh_5pct", "survives_holm_5pct", "clears_hlz_t3"):
                self.assertIsNone(row[key], key)
            # Each names the search-adjusted test where one exists, or says none does.
            if row["id"] not in ("vol_challenger_qlike", "vol_fluct_har_iv_range"):
                self.assertIn("no search-adjusted version of this test exists", row["note"])
                self.assertNotIn("vol_spa_beats_record", row["note"])
            else:
                self.assertIn("vol_spa_beats_record", row["note"])
            for key in ("supported_surviving_bh_5pct", "supported_surviving_holm_5pct", "supported_clearing_hlz_t3",
                        "supported_failing_bh_5pct"):
                self.assertNotIn(row["id"], s[key], key)
        self.assertEqual(s["rows_in_multiplicity_family"], s["rows_with_p_value"] - len(selected))

    def test_a_wrong_way_result_is_never_a_discovery(self):
        # Two-sided p = 0.001 but the estimate points against the hypothesis.
        self.assertAlmostEqual(rl._directional_one_sided(0.001, "two", False), 0.9995)
        self.assertEqual(rl._directional_one_sided(0.001, "two", True), 0.0005)
        self.assertEqual(rl._directional_one_sided(0.03, "one", None), 0.03)

    def test_status_rules(self):
        row = rl.Row(id="x", family="f", hypothesis="h", test="t", source="s", statistic=("s", "a"), p=None, sided="two")
        self.assertEqual(rl._status(row, 0.01, True), "supported")
        self.assertEqual(rl._status(row, 0.01, False), "not_supported")
        self.assertEqual(rl._status(row, 0.07, None), "inconclusive")
        self.assertEqual(rl._status(row, 0.2, None), "not_supported")
        self.assertEqual(rl._status(row, None, None), "inconclusive")
        retracted = rl.Row(id="x", family="f", hypothesis="h", test="t", source="s", statistic=("s", "a"),
                           p=None, sided="two", retracted="leak")
        self.assertEqual(rl._status(retracted, 1e-9, True), "retracted")
        # A pending registration comes first; a null p without one is still inconclusive.
        self.assertEqual(rl._status(retracted, None, None, pending=True), "registered")
        self.assertEqual(rl._status(row, None, None, pending=True), "registered")
        self.assertEqual(rl._status(row, None, None, pending=False), "inconclusive")

    def test_summary_lists_match_the_rows(self):
        ledger = _committed()
        by_id = {r["id"]: r for r in ledger["rows"]}
        s = ledger["summary"]
        self.assertEqual(sum(s["by_status"].values()), len(ledger["rows"]))
        self.assertEqual(s["rows_tested"], s["rows"] - s["by_status"]["registered"])
        self.assertEqual(list(s["by_status"]), list(rl.STATUSES))
        self.assertEqual(sorted(s["registered"] + s["registered_withdrawn"]),
                         sorted(r["id"] for r in ledger["rows"] if r["status"] == "registered"))
        self.assertEqual(s["registered_before_result"], [r["id"] for r in ledger["rows"] if r["registration"]])
        for rid in s["supported_surviving_bh_5pct"]:
            self.assertEqual(by_id[rid]["status"], "supported")
            self.assertTrue(by_id[rid]["survives_bh_5pct"])
        self.assertEqual(set(s["supported"]),
                         set(s["supported_surviving_bh_5pct"]) | set(s["supported_failing_bh_5pct"])
                         | set(s["supported_selected_after_search"]))

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

    def test_runs_on_cl_returns_are_never_pooled(self):
        # Block-shuffled (and real) CL=F runs reuse the same returns and have a base rate above 50%:
        # their rows take the largest per-run p against each run's base rate, over every run.
        leak = [r for r in self.rows.values() if r["family"] == "leak anatomy"]
        on_cl = [r for r in leak if r["artifact"]["statistic_field"].startswith(("block_shuffled_cl", "real_cl"))]
        self.assertEqual({r["id"] for r in on_cl}, {"leak_nn_shuffled_cl_unpurged", "leak_nn_shuffled_cl_purged"})
        doc = rl.load_artifact("leak_anatomy.json")
        for row in on_cl:
            with self.subTest(row=row["id"]):
                fields = row["artifact"]["p_value_fields"]
                self.assertEqual(len(fields), row["context"]["runs"])
                self.assertTrue(all(path.endswith("].binomial_p_value_vs_base_rate") for path in fields))
                self.assertEqual(row["p_value"], max(fields.values()))
                self.assertIn("largest of the per-run p-values", row["test"])
                self.assertFalse(row["pre_specified"])                    # the test was replaced after the runs
                block = rl.resolve(doc, row["artifact"]["statistic_field"].rsplit(".", 1)[0])
                self.assertEqual(sorted(fields.values()),
                                 sorted(run["binomial_p_value_vs_base_rate"] for run in block["runs"]))
        self.assertEqual(self.rows["leak_nn_shuffled_cl_unpurged"]["status"], "supported")
        self.assertEqual(self.rows["leak_nn_shuffled_cl_purged"]["status"], "not_supported")
        # Synthetic random-walk runs are independent with a 50% no-skill rate: pooled.
        for rid in ("leak_forest_noise_unpurged", "leak_forest_noise_purged"):
            (path,) = self.rows[rid]["artifact"]["p_value_fields"]
            self.assertTrue(path.startswith("synthetic_random_walk.") and path.endswith("pooled_binomial_p_value_vs_50"))

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

    def test_vol_point_premium_is_tested_against_the_zero_premium_null(self):
        # With no variance premium a noisy, right-skewed realized vol still falls below implied vol in most
        # periods, so the 50% sign test is retracted and the share is tested against the zero-premium share.
        old, new = self.rows["vrp_exists"], self.rows["vrp_exists_vs_zero_premium"]
        self.assertEqual(old["status"], "retracted")
        self.assertIn(f"{old['context']['zero_premium_share_pct']}%", old["retraction_reason"])
        self.assertGreater(old["context"]["zero_premium_share_pct"], 50.0)
        (path,) = new["artifact"]["p_value_fields"]
        self.assertEqual(path, "economic.variance_risk_premium.zero_premium_null.p_value_one_sided")
        self.assertEqual(new["context"]["zero_premium_share_pct"], old["context"]["zero_premium_share_pct"])
        self.assertIn("zero_premium_mean_premium_vol_pts", self.rows["vrp_short_vol_swap_sharpe"]["context"])

    def test_power_is_attached_where_the_artifact_holds_it(self):
        doc = rl.load_artifact("carry_signal_test.json")["power_post_2007"]
        for rid, label in (("carry_spread_post2007", "power_vs_pre_2007_spread"),
                           ("carry_rank_post2007", "power_vs_pre_2007_rank_corr")):
            row = self.rows[rid]
            self.assertEqual(row["context"][label], doc[label])
            self.assertEqual(row["underpowered"], doc[label] < doc["target_power"], rid)
        self.assertTrue(self.rows["carry_spread_post2007"]["underpowered"])
        with_power = {"carry_spread_post2007", "carry_rank_post2007"}
        for rid, row in self.rows.items():
            if rid not in with_power:
                self.assertIsNone(row["underpowered"], rid)
        self.assertEqual(_committed()["summary"]["underpowered"],
                         [rid for rid in self.rows if self.rows[rid]["underpowered"]])

    def test_supply_groups_are_named_as_the_study_names_them(self):
        study = rl.load_artifact("supply_shock_study.json")
        names = {g["key"]: g["name"] for g in study["groups"]}
        self.assertEqual(study["between_groups"]["groups"], ["supply_lost", "threat_only"])
        for row in self.rows.values():
            if row["family"] != "supply shock":
                continue
            with self.subTest(row=row["id"]):
                self.assertFalse(any(f"'{key}'" in row["hypothesis"] for key in names), row["hypothesis"])
        self.assertIn(f"'{names['supply_lost']}'", self.rows["shock_supply_lost_peak"]["hypothesis"])
        for rid in ("shock_removal_vs_threat_peak", "shock_removal_vs_threat_settle"):
            self.assertIn(names["supply_lost"], self.rows[rid]["hypothesis"])
            self.assertIn(names["threat_only"], self.rows[rid]["hypothesis"])

    def test_two_configuration_rows_compare_the_configurations(self):
        for rid, other in (("dir_macro_uplift", "results.no_macro_1w.direction_accuracy"),
                           ("dir_same_day_context", "results.lagged_1d.direction_accuracy")):
            row = next(r for r in rl.ledger_rows() if r.id == rid)
            self.assertEqual(row.beats, other)
            self.assertIsNone(row.expects)


class BandRegimeRowsTest(unittest.TestCase):
    """ROADMAP 2.2's rows: the 80% band's equal-coverage tests across regimes, both banded models, four splits."""

    def test_band_regime_rows(self):
        ledger = _committed()
        lab = rl.load_artifact(rl.LAB)
        rows = {r["id"]: r for r in ledger["rows"] if r["family"] == "forecast interval"}
        expected = {f"band_regime_{var}_{model}" for var in ("ovx_tercile", "ovx_rv_gap", "rv5_tercile",
                                                             "target_spans_roll") for model in ("har_iv", "har_iv_range")}
        self.assertEqual(set(rows), expected)
        for rid, row in rows.items():
            model = "har_iv_range" if rid.endswith("har_iv_range") else "har_iv"
            var = rid[len("band_regime_"):-len(model) - 1]
            cc = lab["conformal"][model]["conditional_coverage"][var]
            with self.subTest(rid):
                self.assertEqual(row["p_value"], cc["p_value"])
                self.assertEqual(row["p_two_sided"], cc["p_value"])
                self.assertFalse(row["pre_specified"])
                self.assertFalse(row["selected_after_search"])
                self.assertIsNone(row["underpowered"])
                self.assertEqual(row["context"]["n_days"], lab["conformal"][model]["n_days"])
                self.assertEqual(row["context"]["df"], len(lab["regimes"]["variables"][var]["levels"]) - 1)
                self.assertIn("never used to change the band", row["note"])
        gap = rows["band_regime_ovx_rv_gap_har_iv"]["hypothesis"]
        self.assertIn(f"previous {lab['regimes']['variables']['ovx_rv_gap']['window_sessions']} sessions", gap)
        self.assertNotIn("{", gap)


class OptionalFieldTest(unittest.TestCase):
    def test_a_stored_null_reads_as_no_p_value_only_where_optional(self):
        with self.assertRaises(TypeError):
            rl.Field("a")({"a": None})
        self.assertEqual(rl.Field("a", optional=True)({"a": None}), (None, {"a": None}))
        self.assertEqual(rl.Field("a", optional=True)({"a": 0.2}), (0.2, {"a": 0.2}))
        self.assertEqual(rl.Field("a")({"a": 0.2}), (0.2, {"a": 0.2}))


class ConditionalAndStabilityRowsTest(unittest.TestCase):
    """ROADMAP 2.3's rows: the Giacomini-White conditional part and the window-length-adjusted fluctuation test,
    every model against the model of record."""

    def test_the_rows_read_the_registered_forms(self):
        from backend import vol_lab

        ledger = _committed()
        lab = rl.load_artifact(rl.LAB)
        self.assertEqual(list(rl.LAB_COMPARED), [m for m in vol_lab.MODEL_KEYS if m != vol_lab.CHAMPION])
        self.assertEqual(rl.CHALLENGER, lab["config"]["challenger"])
        models = {m["key"]: m for m in lab["leaderboard"]["models"]}
        rows = {r["id"]: r for r in ledger["rows"]}
        gw_rows = [r for r in ledger["rows"] if r["family"] == "conditional predictive ability"]
        fl_rows = [r for r in ledger["rows"] if r["family"] == "stability over time"]
        self.assertEqual([r["id"] for r in gw_rows], [f"vol_gw_{m}" for m in rl.LAB_COMPARED])
        self.assertEqual([r["id"] for r in fl_rows], [f"vol_fluct_{m}" for m in rl.LAB_COMPARED])
        for m in rl.LAB_COMPARED:
            gw, fl = rows[f"vol_gw_{m}"], rows[f"vol_fluct_{m}"]
            block_gw, block_fl = models[m]["gw_qlike_vs_har_iv"], models[m]["fluctuation_qlike_vs_har_iv"]
            with self.subTest(m):
                self.assertEqual(gw["p_value"], block_gw["conditional"]["p_value"])
                self.assertEqual(fl["p_value"], block_fl["search_adjusted"]["p_value"])
                for row in (gw, fl):
                    self.assertEqual(row["p_two_sided"], row["p_value"])
                    self.assertFalse(row["pre_specified"])
                    self.assertEqual(row["selected_after_search"], m == rl.CHALLENGER)
                    self.assertEqual(row["context"]["dm_p"], models[m]["dm_qlike_vs_har_iv"]["p_value"])
                    self.assertIn(models[m]["label"], row["hypothesis"])
                    self.assertIn("approximate", row["test"])
                self.assertEqual(gw["context"]["df"], 3)
                self.assertEqual(gw["context"]["p_with_constant"], block_gw["p_value"])
                self.assertEqual(fl["context"]["p_mu_0_2"], block_fl["p_value"])
                self.assertEqual(fl["context"]["critical_5pct"], lab["stability"]["fluctuation"]["critical_values"]["five_pct"])
                self.assertGreaterEqual(fl["p_value"], 1 / (lab["stability"]["fluctuation"]["null"]["draws"] + 1))


class DistributionRowsTest(unittest.TestCase):
    """ROADMAP 2.4's rows: each model's predictive distribution against the model of record's on three scores, by a
    batch-means test, and each model's calibration by Berkowitz on the sampling grids, Bonferroni over them."""

    def test_the_rows_read_the_registered_forms(self):
        ledger = _committed()
        lab = rl.load_artifact(rl.LAB)
        models = {m["key"]: m for m in lab["distribution"]["models"]}
        self.assertEqual(list(models), ["har_iv", *rl.DISTRIBUTION_MODELS])
        self.assertNotIn("quantile_har", rl.LAB_COMPARED)
        rows = {r["id"]: r for r in ledger["rows"]}
        score_rows = [r["id"] for r in ledger["rows"] if r["family"] == "predictive distribution"]
        self.assertEqual(score_rows, [f"vol_{tag}_{m}" for m in rl.DISTRIBUTION_MODELS for tag, _, _ in rl._SCORES])
        calibration = [r["id"] for r in ledger["rows"] if r["family"] == "distribution calibration"]
        self.assertEqual(calibration, [f"vol_berkowitz_{m}" for m in ("har_iv", *rl.DISTRIBUTION_MODELS)])
        for m in rl.DISTRIBUTION_MODELS:
            for tag, score, _ in rl._SCORES:
                row, dm = rows[f"vol_{tag}_{m}"], models[m]["dm_vs_har_iv"][score]
                with self.subTest(row=row["id"]):
                    self.assertEqual(row["p_value"], dm["p_value"])           # the batch-means p, not dm_test's
                    self.assertEqual(row["statistic"]["value"], dm["stat"])
                    self.assertEqual(row["context"]["p_dm_test_9"], dm["dm_test"]["p_value"])
                    self.assertEqual(row["selected_after_search"], m == rl.CHALLENGER)
                    self.assertFalse(row["pre_specified"])
                    self.assertIn("Chosen after the scores were seen", row["note"])
                    # The record's hypothesis expects a negative t (the model scores higher); the others a positive one.
                    positive = m in (rl.CHALLENGER, "quantile_har")
                    wrong_way = (dm["stat"] < 0) == positive
                    self.assertEqual(row["p_two_sided"], 1.0 if wrong_way else row["p_value"])
        for m in ("har_iv", *rl.DISTRIBUTION_MODELS):
            row, berk = rows[f"vol_berkowitz_{m}"], models[m]["berkowitz"]
            with self.subTest(row=row["id"]):
                grid_p = [g["p_value"] for g in berk["by_grid"]]
                self.assertEqual(row["p_value"], min(1.0, len(grid_p) * min(grid_p)))
                self.assertAlmostEqual(row["p_value"], berk["p_value"], places=15)     # the lab's own Bonferroni
                self.assertEqual(len(row["artifact"]["p_value_fields"]), len(grid_p))
                self.assertEqual(row["p_two_sided"], row["p_value"])
                self.assertFalse(row["selected_after_search"])
                self.assertIn("censored" if m == "quantile_har" else "LR3", row["test"])

    def test_bonferroni_over_runs(self):
        doc = {"runs": [{"p": 0.04}, {"p": 0.3}, {"p": 0.012}]}
        p, used = rl.BonferroniOverRuns("runs", "p")(doc)
        self.assertAlmostEqual(p, 0.036)
        self.assertEqual(list(used), ["runs[0].p", "runs[1].p", "runs[2].p"])
        self.assertEqual(rl.BonferroniOverRuns("runs", "p")({"runs": [{"p": 0.5}, {"p": 0.6}]})[0], 1.0)
        with self.assertRaises(rl.FieldError):
            rl.BonferroniOverRuns("runs", "p")({"runs": []})

    def test_adding_the_rows_changes_no_survival_flag(self):
        # The rows join the family, so every q-value moves; whether a row survives must not.
        ledger = _committed()
        rows = [r for r in ledger["rows"] if r["q_value_bh"] is not None]
        new = {"predictive distribution", "distribution calibration"}
        old = [r for r in rows if r["family"] not in new]
        q_old = rl.benjamini_hochberg(np.array([r["p_two_sided"] for r in old]))
        h_old = rl.holm(np.array([r["p_two_sided"] for r in old]))
        for r, q, h in zip(old, q_old, h_old):
            self.assertEqual(r["survives_bh_5pct"], bool(q < rl.ALPHA), r["id"])
            self.assertEqual(r["survives_holm_5pct"], bool(h < rl.ALPHA), r["id"])


class ResolveTest(unittest.TestCase):
    DOC = {"a": {"list": [{"key": "x", "level": 0.05, "v": 1}, {"key": "x", "level": 0.01, "v": 2}],
                 "0": {"k": 3}}}

    def test_selectors(self):
        self.assertEqual(rl.resolve(self.DOC, "a.list[key=x,level=0.01].v"), 2)
        self.assertEqual(rl.resolve(self.DOC, "a.list[1].v"), 2)
        self.assertEqual(rl.resolve(self.DOC, "a.0.k"), 3)

    def test_largest_over_runs_is_the_intersection_union_p(self):
        doc = {"x": {"runs": [{"p": 0.01}, {"p": 0.2}, {"p": 0.003}]}, "empty": {"runs": []}}
        p, used = rl.LargestOverRuns("x.runs", "p")(doc)
        self.assertEqual(p, 0.2)
        self.assertEqual(used, {"x.runs[0].p": 0.01, "x.runs[1].p": 0.2, "x.runs[2].p": 0.003})
        for bad in (rl.LargestOverRuns("empty.runs", "p"), rl.LargestOverRuns("x", "p"),
                    rl.LargestOverRuns("x.runs", "missing")):
            with self.subTest(source=bad), self.assertRaises(rl.FieldError):
                bad(doc)

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


# ── Online false-discovery control (ROADMAP 2.12) ───────────────────────────────────────────────────────────────────

def _reference():
    return json.loads((FIXTURES / "online_fdr_reference.json").read_text(encoding="utf-8"))


def _run(case):
    params = case["params"]
    if case["procedure"] == "LORD++":
        return rl.lord_plus_plus(case["pvals"], alpha=params["alpha"], w0=params["w0"], gamma_c=params["gamma_c"])
    return rl.saffron(case["pvals"], alpha=params["alpha"], w0=params["w0"], lam=params["lambda"],
                      gamma_c=params["gamma_c"], gamma_s=params["gamma_s"])


def _rejections(run):
    return [t for t, r in enumerate(run.rejected, start=1) if r]


class OnlineReferenceTest(unittest.TestCase):
    """Acceptance (ROADMAP 2.12): both procedures reproduce the rejection sets, and every printed level, of the
    reference implementation, onlineFDR (R/Bioconductor): its rendered examples (2.21.3 and 2.19.1) and its testthat
    vectors. The fixture carries each case's provenance: URL, the exact R call, the version and the SHA-256 of the page
    the printed output was parsed from. The full-precision levels and the wealth paths in the fixture were computed by
    the fixture's own Python reference (a line-by-line port of the package's C++ and an implementation from the papers,
    which agree); the printed levels and the rejections are onlineFDR's own output."""

    def setUp(self):
        self.fixture = _reference()
        self.cases = {c["id"]: c for c in self.fixture["cases"]}

    def test_the_reference_cases_reproduce(self):
        reference = [c for c in self.fixture["cases"] if c["origin"] in ("published", "published-testthat")]
        self.assertEqual(len(reference), 18)
        self.assertEqual(sum(c["origin"] == "published" for c in reference), 12)
        for case in reference:
            with self.subTest(case=case["id"]):
                run = _run(case)
                self.assertEqual(_rejections(run), case["expected_rejections_1based"])
                self.assertEqual([int(bool(r)) for r in run.rejected], case["expected_R"])
                printed = case.get("expected_alpha_printed_as_float")
                for t, alpha in enumerate(run.alpha):
                    if printed is not None:
                        self.assertLessEqual(abs(alpha - printed[t]), case["alpha_abs_tol"][t], t + 1)
                    else:
                        self.assertTrue(math.isclose(alpha, case["expected_alpha_full"][t], rel_tol=1e-12), t + 1)
                if "expected_alpha_1" in case:
                    self.assertTrue(math.isclose(run.alpha[0], case["expected_alpha_1"], rel_tol=1e-12))
                if "expected_wealth_full" in case:
                    self.assertEqual(len(run.wealth), len(case["pvals"]))
                    for got, want in zip(run.wealth, case["expected_wealth_full"]):
                        self.assertTrue(math.isclose(got, want, rel_tol=1e-12))

    def test_the_derived_cases_regress(self):
        """Regression checks against the fixture's Python reference, not reference evidence: 80-step streams with a
        run of consecutive rejections and, for SAFFRON, a small lambda whose cap binds."""
        derived = [c for c in self.fixture["cases"] if c["origin"] == "derived"]
        self.assertEqual(len(derived), 3)
        for case in derived:
            with self.subTest(case=case["id"]):
                run = _run(case)
                self.assertEqual(_rejections(run), case["expected_rejections_1based"])
                for got, want in zip(run.alpha, case["expected_alpha_full"]):
                    self.assertTrue(math.isclose(got, want, rel_tol=1e-12))
                for got, want in zip(run.wealth, case["expected_wealth_full"]):
                    self.assertTrue(math.isclose(got, want, rel_tol=1e-12))
                binds = case["lambda_cap_binds_at_1based"]
                if binds:
                    lam = case["params"]["lambda"]
                    self.assertEqual([t for t, a in enumerate(run.alpha, start=1) if a == lam], binds)
        self.assertGreaterEqual(len(self.cases["saffron_derived_stress80_lambda_cap"]["lambda_cap_binds_at_1based"]), 20)
        self.assertGreaterEqual(max(_consecutive(c["expected_rejections_1based"]) for c in derived), 8)

    def test_the_gamma_sequences(self):
        check = self.fixture["gamma_check"]
        for key, gamma in (("lord_c_0.07908197_j1_16", lambda j: rl.lord_gamma(j, 0.07908197)),
                           ("lord_c_0.07720838_j1_16", lambda j: rl.lord_gamma(j, 0.07720838)),
                           ("saffron_j1_16", rl.saffron_gamma)):
            for j, want in enumerate(check[key], start=1):
                self.assertTrue(math.isclose(gamma(j), want, rel_tol=1e-14), (key, j))

    def test_the_module_runs_the_reference_defaults(self):
        lord = self.cases["lordpp_sample_ordered_v2213"]["params"]
        saff = self.cases["saffron_sample_ordered_v2213"]["params"]
        self.assertEqual((rl.ALPHA, rl.LORD_W0, rl.LORD_GAMMA_C), (lord["alpha"], lord["w0"], lord["gamma_c"]))
        self.assertEqual((rl.ALPHA, rl.SAFFRON_W0, rl.SAFFRON_LAMBDA, rl.SAFFRON_GAMMA_C, rl.SAFFRON_GAMMA_S),
                         (saff["alpha"], saff["w0"], saff["lambda"], saff["gamma_c"], saff["gamma_s"]))
        self.assertIn("onlineFDR 2.21.3", rl.ONLINE_REFERENCE)
        self.assertEqual(self.fixture["reference_implementation"]["version"], "2.21.3")
        case = self.cases["lordpp_sample_ordered_v2213"]
        self.assertEqual(_rejections(rl.lord_plus_plus(case["pvals"])), case["expected_rejections_1based"])
        case = self.cases["saffron_sample_ordered_v2213"]
        self.assertEqual(_rejections(rl.saffron(case["pvals"])), case["expected_rejections_1based"])

    def test_the_constant_changes_a_published_rejection_set(self):
        new = self.cases["lordpp_sample_seed1_a10_w05_v2213"]["expected_rejections_1based"]
        old = self.cases["lordpp_sample_seed1_a10_w05_v2191"]["expected_rejections_1based"]
        self.assertIn(15, new)
        self.assertNotIn(15, old)

    def test_bad_inputs_raise(self):
        for call in (lambda: rl.lord_plus_plus([0.5], w0=0.1), lambda: rl.lord_plus_plus([1.5]),
                     lambda: rl.saffron([0.5], lam=1.0), lambda: rl.saffron([float("nan")]),
                     lambda: rl.saffron([-0.1]), lambda: rl.lord_plus_plus([0.5], alpha=0.0)):
            with self.assertRaises(ValueError):
                call()


def _consecutive(ts):
    best = run = 0
    for i, t in enumerate(ts):
        run = run + 1 if i and t == ts[i - 1] + 1 else 1
        best = max(best, run)
    return best


try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st
except ImportError:  # pragma: no cover - a dev dependency (requirements-dev.txt)
    given = None

if given is not None:
    _PROPERTY = settings(max_examples=int(os.environ.get("HYPOTHESIS_MAX_EXAMPLES", "40")), derandomize=True,
                         database=None, deadline=None, print_blob=False, suppress_health_check=[HealthCheck.too_slow])
    _P = st.one_of(st.floats(min_value=0.0, max_value=1.0, allow_nan=False),           # nulls
                   st.floats(min_value=0.0, max_value=1.0, allow_nan=False).map(lambda u: u ** 10),   # Beta(0.1, 1)
                   st.sampled_from([0.0, 1.0, rl.SAFFRON_LAMBDA]))
    _STREAMS = st.lists(_P, min_size=1, max_size=200)

    def _procedures():
        return (("LORD++", rl.lord_plus_plus), ("SAFFRON", rl.saffron))

    class OnlineInvariantsTest(unittest.TestCase):
        """What must hold for every stream (Hypothesis, derandomized as in tests/test_properties.py)."""

        def assert_prefix(self, short, full, k):
            self.assertEqual(short.alpha, full.alpha[:k])
            self.assertEqual(short.rejected, full.rejected[:k])
            self.assertEqual(short.candidate, full.candidate[:k])
            self.assertEqual(short.wealth, full.wealth[:k])

        @_PROPERTY
        @given(_STREAMS, st.data())
        def test_earlier_decisions_never_depend_on_later_rows(self, p, data):
            """Catches: a gamma normalised to the stream's length, or a level that reads later p-values."""
            k = data.draw(st.integers(min_value=1, max_value=len(p)), label="k")
            extra = data.draw(_P, label="appended")
            for _, procedure in _procedures():
                full = procedure(p)
                self.assert_prefix(procedure(p[:k]), full, k)
                self.assert_prefix(full, procedure([*p, extra]), len(p))

        @_PROPERTY
        @given(_STREAMS)
        def test_wealth_and_the_estimated_fdp(self, p):
            """Catches: a wrong reward, a level that overspends, a rejection outside SAFFRON's candidates."""
            lam = rl.SAFFRON_LAMBDA
            for name, procedure in _procedures():
                run = procedure(p)
                self.assertTrue(all(w >= -1e-15 for w in run.wealth), name)
                rejections, spent = 0, 0.0
                for t, (a, r) in enumerate(zip(run.alpha, run.rejected)):
                    rejections += bool(r)
                    spent += a if name == "LORD++" else (a / (1 - lam) if p[t] > lam else 0.0)
                    self.assertLessEqual(spent / max(rejections, 1), rl.ALPHA + 1e-12, (name, t))
                if name == "SAFFRON":
                    self.assertTrue(all(a <= lam for a in run.alpha))
                    self.assertTrue(all(c for c, r in zip(run.candidate, run.rejected) if r))
                    self.assertEqual(run.candidate, [x <= lam for x in p])

        @_PROPERTY
        @given(_STREAMS, st.data())
        def test_a_p_value_equal_to_its_level_is_rejected(self, p, data):
            k = data.draw(st.integers(min_value=0, max_value=len(p) - 1), label="k")
            for name, procedure in _procedures():
                tied = list(p)
                tied[k] = procedure(p).alpha[k]                   # alpha_k reads only p_1 .. p_{k-1}
                self.assertTrue(procedure(tied).rejected[k], name)

        @_PROPERTY
        @given(_STREAMS, st.data())
        def test_a_pending_entry_blocks_what_follows_it(self, p, data):
            k = data.draw(st.integers(min_value=0, max_value=len(p) - 1), label="k")
            for name, procedure in _procedures():
                full = procedure(p)
                pending = procedure([*p[:k], None, *p[k + 1:]])
                self.assertEqual(pending.alpha[:k + 1], full.alpha[:k + 1], name)    # its own level is published
                self.assertEqual(pending.rejected[:k], full.rejected[:k], name)
                self.assertEqual(pending.wealth, full.wealth[:k], name)
                self.assertTrue(all(x is None for x in pending.alpha[k + 1:]), name)
                self.assertTrue(all(x is None for x in pending.rejected[k:]), name)
                if name == "SAFFRON":
                    self.assertEqual(pending.candidate[:k], full.candidate[:k])
                    self.assertTrue(all(x is None for x in pending.candidate[k:]))


class OnlineStreamTest(unittest.TestCase):
    """The online view on the committed ledger, recomputed from the artifact (no git needed)."""

    @classmethod
    def setUpClass(cls):
        cls.ledger = _committed()
        cls.rows = cls.ledger["rows"]
        cls.online = cls.ledger["summary"]["online"]
        cls.stream = sorted((r for r in cls.rows if r["online_position"] is not None), key=lambda r: r["online_position"])

    def test_the_online_view_recomputes_from_the_artifact(self):
        # The online counterpart of the statsmodels check of the batch view.
        p = [r["online_p"] for r in self.stream]
        for key, procedure in (("lordpp", rl.lord_plus_plus), ("saffron", rl.saffron)):
            run = procedure([*p, None])                      # the extra entry is the next row's level
            for t, r in enumerate(self.stream):
                with self.subTest(procedure=key, row=r["id"]):
                    self.assertEqual(r[f"online_alpha_{key}"], run.alpha[t])
                    self.assertEqual(r[f"online_rejected_{key}"], run.rejected[t])
                    if key == "saffron":
                        self.assertEqual(r["online_candidate_saffron"], run.candidate[t])
            block = self.online[key]
            self.assertEqual(block["rejected"], [r["id"] for r, x in zip(self.stream, run.rejected) if x])
            self.assertEqual(block["wealth"], run.wealth)
            self.assertEqual(block["next_level"], run.alpha[-1])
        self.assertEqual(self.online["saffron"]["candidates"],
                         sum(bool(r["online_candidate_saffron"]) for r in self.stream))
        self.assertEqual(self.online["rows_in_stream"], len(self.stream))
        self.assertEqual(self.online["rows_decided"], len(self.online["lordpp"]["wealth"]))
        self.assertEqual(self.online["rows_decided"], sum(r["online_rejected_lordpp"] is not None for r in self.stream))
        self.assertEqual(self.online["waiting_on"], next((r["id"] for r in self.stream if r["online_p"] is None), None))
        self.assertEqual((self.online["alpha"], self.online["reference"]), (rl.ALPHA, rl.ONLINE_REFERENCE))
        self.assertEqual((self.online["lordpp"]["w0"], self.online["lordpp"]["gamma_c"]), (rl.LORD_W0, rl.LORD_GAMMA_C))
        saff = self.online["saffron"]
        self.assertEqual((saff["w0"], saff["lambda"], saff["gamma_c"], saff["gamma_s"]),
                         (rl.SAFFRON_W0, rl.SAFFRON_LAMBDA, rl.SAFFRON_GAMMA_C, rl.SAFFRON_GAMMA_S))

    def test_every_prefix_of_the_committed_stream_reproduces_the_full_run(self):
        p = [r["online_p"] for r in self.stream]
        for procedure in (rl.lord_plus_plus, rl.saffron):
            full = procedure(p)
            for k in range(1, len(p)):
                part = procedure(p[:k])
                self.assertEqual((part.alpha, part.rejected), (full.alpha[:k], full.rejected[:k]), k)

    def test_membership_and_the_p_tested(self):
        for r in self.rows:
            with self.subTest(row=r["id"]):
                if r["selected_after_search"]:                # out of the stream, as out of the batch family
                    for key in rl.ONLINE_FIELDS:
                        self.assertIsNone(r[key], key)
                    continue
                self.assertIsNotNone(r["online_position"])
                if r["status"] == "registered":
                    expected = 1.0 if r["registration"]["withdrawn"] else None
                elif r["status"] == "retracted" or r["p_two_sided"] is None:
                    expected = 1.0                            # spends its level, can neither be rejected nor fund
                else:
                    expected = r["p_two_sided"]
                self.assertEqual(r["online_p"], expected)
                if expected == 1.0:
                    self.assertFalse(r["online_rejected_lordpp"])
                    self.assertFalse(r["online_rejected_saffron"])
        self.assertEqual([r["online_position"] for r in self.stream], list(range(1, len(self.stream) + 1)))
        family = {r["id"] for r in self.rows if r["q_value_bh"] is not None}
        self.assertLessEqual(family, {r["id"] for r in self.stream})
        retracted = [r for r in self.stream if r["status"] == "retracted"]
        self.assertEqual(len(retracted), self.ledger["summary"]["by_status"]["retracted"])
        self.assertTrue(all(r["online_p"] == 1.0 and r["p_two_sided"] is not None for r in retracted))
        rejected = set(self.online["lordpp"]["rejected"]) | set(self.online["saffron"]["rejected"])
        self.assertFalse(rejected & {r["id"] for r in retracted})

    def test_the_order_rule(self):
        index = {row.id: i for i, row in enumerate(rl.ledger_rows())}
        expected = sorted((r for r in self.rows if not r["selected_after_search"]),
                          key=lambda r: (r["entered_on"], index[r["id"]]))
        self.assertEqual([r["id"] for r in self.stream], [r["id"] for r in expected])
        self.assertEqual([r["id"] for r in self.rows], list(index))          # the artifact keeps its row order
        # The rows that entered before registration began are frozen; any other row must be registered.
        legacy = json.loads((FIXTURES / "ledger_legacy_entry.json").read_text(encoding="utf-8"))["rows"]
        unregistered = sorted((r for r in self.rows if r["registration"] is None),
                              key=lambda r: (r["entered_on"], index[r["id"]]))
        self.assertEqual([(r["id"], r["entered_on"]) for r in unregistered],
                         [(r["id"], r["entered_on"]) for r in legacy],
                         "a row that entered after registration began must be registered (REGISTERED)")
        self.assertEqual(rl.legacy_batches(), {r["id"]: r["pr"] for r in legacy})
        self.assertLessEqual(set(rl.LEGACY_ENTRY), {fn.__name__ for fn in rl._ROW_FUNCTIONS})
        self.assertTrue(all(rl._DATE.match(on) for on, _ in rl.LEGACY_ENTRY.values()))
        latest = rl.latest_legacy_entry()
        today = datetime.now(timezone.utc).date().isoformat()
        for r in self.rows:
            if r["registration"] is not None:
                with self.subTest(row=r["id"]):
                    self.assertEqual(r["entered_on"], r["registration"]["on"])
                    self.assertGreater(r["entered_on"], latest)
                    self.assertLessEqual(r["entered_on"], today)
        dates = [registration.on for registration in rl.REGISTERED.values()]
        self.assertEqual(dates, sorted(dates), "REGISTERED is in registration order")
        batches = rl.legacy_batches()
        counts: dict[str, int] = {}
        for r in self.stream:
            if r["id"] in batches:
                counts[batches[r["id"]]] = counts.get(batches[r["id"]], 0) + 1
        entry = {pr: on for on, pr in rl.LEGACY_ENTRY.values()}
        self.assertEqual(self.online["legacy_batches"],
                         [{"entered_on": entry[pr], "pr": pr, "rows_in_stream": n} for pr, n in counts.items()])
        self.assertEqual({(b["entered_on"], b["pr"]) for b in self.online["legacy_batches"]},
                         set(rl.LEGACY_ENTRY.values()))

    def test_dropping_the_last_entry_batch_moves_no_earlier_online_decision(self):
        """Removing the newest batch is what appending it undoes: no earlier online field may move, while the batch
        view's q-values all move with the family's size (today without changing a survival flag)."""
        last = {row.id for row in rl._distribution_rows()}
        full = {r["id"]: r for r in self.rows}
        kept = [full[rid]["online_position"] for rid in full if rid not in last and full[rid]["online_position"]]
        dropped = [full[rid]["online_position"] for rid in last if full[rid]["online_position"]]
        self.assertLess(max(kept), min(dropped))              # the batch is a suffix of the stream
        with mock.patch.object(rl, "ORDER_DRAWS", 5):
            partial = json.loads(json.dumps(rl.build(rows=[r for r in rl.ledger_rows() if r.id not in last])))
        for r in partial["rows"]:
            for key in rl.ONLINE_FIELDS:
                self.assertEqual(r[key], full[r["id"]][key], (r["id"], key))
        moved = [r["id"] for r in partial["rows"]
                 if r["q_value_bh"] is not None and r["q_value_bh"] != full[r["id"]]["q_value_bh"]]
        self.assertTrue(moved)
        for r in partial["rows"]:
            self.assertEqual(r["survives_bh_5pct"], full[r["id"]]["survives_bh_5pct"], r["id"])

    def test_the_order_sensitivity_recomputes(self):
        block = self.online["legacy_order_sensitivity"]
        self.assertEqual((block["draws"], block["seed"]), (rl.ORDER_DRAWS, rl.ORDER_SEED))
        batches = rl.legacy_batches()
        self.assertEqual(rl._order_sensitivity(copy.deepcopy(self.stream), batches), block)
        self.assertEqual(block["legacy_rows"], sum(r["id"] in batches for r in self.stream))
        for key in ("lordpp", "saffron"):
            published = sum(rid in batches for rid in self.online[key]["rejected"])
            self.assertLessEqual(block[key]["min"], published)
            self.assertGreaterEqual(block[key]["max"], published)
            self.assertGreater(block[key]["same_set_share"], 0.0)          # the published order is among those drawn

    def test_no_status_reads_the_online_view(self):
        with mock.patch.object(rl, "_online", lambda rows: {}):
            plain = json.loads(json.dumps(rl.build()))
        committed = {r["id"]: r for r in self.rows}
        for r in plain["rows"]:
            for key in ("status", *LedgerHistoryTest.BATCH_FIELDS):
                self.assertEqual(r[key], committed[r["id"]][key], (r["id"], key))


def _day(k):
    """k days after the latest legacy entry date, so a synthetic registration never ages."""
    return (date.fromisoformat(rl.latest_legacy_entry()) + timedelta(days=k)).isoformat()


def _fake(rid="reg_fake", field="experiment.p_value", day=1, withdrawn=None, **changes):
    """A synthetic registered row on the lab's artifact: its test reads `field`, its statistic the sibling `stat`."""
    block = field.rsplit(".", 1)[0]
    row = rl.Row(id=rid, family="volatility forecast", hypothesis="A registered hypothesis, written out in full.",
                 test="a two-sided test whose p-value the artifact stores", source=rl.LAB,
                 statistic=("statistic", f"{block}.stat"), p=rl.Field(field), sided="two", expects=rl.POSITIVE,
                 registration=rl.Registration(_day(day), field, withdrawn))
    return dataclasses.replace(row, **changes)


class RegistrationTest(unittest.TestCase):
    """The registered status (ROADMAP 2.12) through synthetic rows: a row declared with its test and the artifact field
    that will hold its result keeps the status `registered` until that field exists, and then resolves by itself."""

    @classmethod
    def setUpClass(cls):
        patch = mock.patch.object(rl, "ORDER_DRAWS", 3)          # the sensitivity block is not under test here
        patch.start()
        cls.addClassCleanup(patch.stop)
        cls.docs = {}
        cls.plain = cls.build_with([])
        cls.n = cls.plain["summary"]["online"]["rows_in_stream"]

    @classmethod
    def load(cls, name, extra=None, missing=()):
        if name in missing:
            raise FileNotFoundError(name)
        if name not in cls.docs:
            cls.docs[name] = rl.load_artifact(name)
        doc = cls.docs[name]
        return {**doc, **extra} if name == rl.LAB and extra else doc

    @classmethod
    def build_with(cls, rows, extra=None, missing=()):
        ledger = rl.build(load=lambda name: cls.load(name, extra, missing), rows=[*rl.ledger_rows(), *rows])
        return json.loads(json.dumps(ledger))

    def assert_pending(self, ledger, rid, *, withdrawn=False):
        rows = {r["id"]: r for r in ledger["rows"]}
        r, s = rows[rid], ledger["summary"]
        self.assertEqual(r["status"], "registered")
        for key in ("date", "p_value", "p_two_sided", "underpowered", "retraction_reason", "q_value_bh", "p_holm",
                    "t_equivalent", "survives_bh_5pct", "survives_holm_5pct", "clears_hlz_t3"):
            self.assertIsNone(r[key], key)
        self.assertEqual((r["statistic"]["value"], r["context"], r["artifact"]["p_value_fields"]), (None, {}, {}))
        self.assertEqual(r["entered_on"], r["registration"]["on"])
        self.assertEqual(r["registration"]["withdrawn"] is not None, withdrawn)
        self.assertEqual(s["rows_in_multiplicity_family"], self.plain["summary"]["rows_in_multiplicity_family"])
        self.assertEqual(s["rows_tested"], self.plain["summary"]["rows_tested"])
        self.assertEqual(s["by_status"]["registered"], 1)
        self.assertEqual(r["online_position"], self.n + 1)
        if withdrawn:
            self.assertEqual((r["online_p"], r["online_rejected_lordpp"], r["online_rejected_saffron"]),
                             (1.0, False, False))
            self.assertEqual((s["registered"], s["registered_withdrawn"]), ([], [rid]))
        else:
            online = self.plain["summary"]["online"]
            self.assertIsNone(r["online_p"])
            self.assertEqual(r["online_alpha_lordpp"], online["lordpp"]["next_level"])      # its level is fixed now
            self.assertEqual(r["online_alpha_saffron"], online["saffron"]["next_level"])
            for key in ("online_rejected_lordpp", "online_rejected_saffron", "online_candidate_saffron"):
                self.assertIsNone(r[key], key)
            self.assertEqual((s["registered"], s["online"]["waiting_on"]), ([rid], rid))
        for key in ("supported", "supported_surviving_bh_5pct", "supported_surviving_holm_5pct",
                    "supported_clearing_hlz_t3", "supported_failing_bh_5pct"):
            self.assertNotIn(rid, s[key], key)
        self.assertNotIn(rid, s["online"]["lordpp"]["rejected"] + s["online"]["saffron"]["rejected"])
        plain = {x["id"]: x for x in self.plain["rows"]}
        for x in ledger["rows"]:
            if x["id"] != rid:
                self.assertEqual(x, plain[x["id"]], x["id"])          # every other row, batch and online fields alike

    def test_a_missing_field_keeps_the_row_registered(self):
        self.assert_pending(self.build_with([_fake()]), "reg_fake")

    def test_a_null_field_keeps_the_row_registered(self):
        self.assert_pending(self.build_with([_fake()], {"experiment": {"p_value": None, "stat": None}}), "reg_fake")

    def test_a_missing_artifact_keeps_the_row_registered(self):
        row = _fake(source="registered_experiment.json")
        self.assert_pending(self.build_with([row], missing=("registered_experiment.json",)), "reg_fake")
        with self.assertRaises(FileNotFoundError):              # any other row's missing artifact still raises
            self.build_with([], missing=("carry_signal_test.json",))

    def test_the_status_changes_by_itself_once_the_field_exists(self):
        row = _fake()
        lord_level = self.plain["summary"]["online"]["lordpp"]["next_level"]
        p = lord_level / 2
        ledger = self.build_with([row], {"experiment": {"p_value": p, "stat": 2.5}})
        rows, s = {r["id"]: r for r in ledger["rows"]}, ledger["summary"]
        r = rows["reg_fake"]
        self.assertEqual((r["status"], r["p_value"], r["p_two_sided"]), ("supported", p, p))
        self.assertEqual(r["artifact"]["p_value_fields"], {"experiment.p_value": p})
        self.assertEqual(s["rows_in_multiplicity_family"], self.plain["summary"]["rows_in_multiplicity_family"] + 1)
        self.assertIsNotNone(r["q_value_bh"])
        self.assertEqual((r["online_position"], r["online_p"]), (self.n + 1, p))
        self.assertEqual(r["online_alpha_lordpp"], lord_level)
        self.assertTrue(r["online_rejected_lordpp"])
        self.assertEqual(r["registration"]["spec_sha256"], rl.spec_sha256(row))
        self.assertEqual((s["registered"], s["registered_before_result"]), ([], ["reg_fake"]))
        self.assertEqual(s["rows_tested"], self.plain["summary"]["rows_tested"] + 1)
        plain = {x["id"]: x for x in self.plain["rows"]}
        for x in ledger["rows"]:
            if x["id"] != "reg_fake":
                for key in rl.ONLINE_FIELDS:
                    self.assertEqual(x[key], plain[x["id"]][key], (x["id"], key))

    def test_a_runs_test_may_declare_the_runs_list(self):
        row = _fake(field="experiment.runs", p=rl.LargestOverRuns("experiment.runs", "p"),
                    statistic=("statistic", "experiment.stat"))
        ledger = self.build_with([row], {"experiment": {"runs": [{"p": 0.01}, {"p": 0.2}], "stat": 1.0}})
        self.assertEqual(next(r for r in ledger["rows"] if r["id"] == "reg_fake")["p_value"], 0.2)

    def test_the_test_must_read_the_declared_field(self):
        row = _fake(field="experiment.other", statistic=("statistic", "experiment.stat"),
                    p=rl.Field("experiment.p_value"))
        with self.assertRaises(rl.FieldError):
            self.build_with([row], {"experiment": {"p_value": 0.01, "stat": 1.0, "other": 0.5}})

    def test_a_pending_row_blocks_the_rows_after_it(self):
        first, second = _fake("reg_first", day=1), _fake("reg_second", "experiment2.p_value", day=2)
        ledger = self.build_with([first, second], {"experiment2": {"p_value": 0.02, "stat": 1.5}})
        rows, s = {r["id"]: r for r in ledger["rows"]}, ledger["summary"]
        self.assertEqual(rows["reg_first"]["status"], "registered")
        late = rows["reg_second"]
        self.assertEqual(late["status"], "supported")                   # its batch status is computed as usual
        self.assertIsNotNone(late["q_value_bh"])
        self.assertEqual(late["online_position"], self.n + 2)
        for key in ("online_alpha_lordpp", "online_rejected_lordpp", "online_alpha_saffron", "online_rejected_saffron"):
            self.assertIsNone(late[key], key)
        self.assertEqual(rows["reg_first"]["online_alpha_lordpp"], self.plain["summary"]["online"]["lordpp"]["next_level"])
        self.assertEqual(s["online"]["waiting_on"], "reg_first")
        self.assertEqual(s["online"]["rows_decided"], self.n)
        self.assertIsNone(s["online"]["lordpp"]["next_level"])

    def test_withdrawal_unblocks_and_costs_its_level(self):
        first = _fake("reg_first", day=1, withdrawn="the experiment was abandoned before it ran")
        second = _fake("reg_second", "experiment2.p_value", day=2)
        ledger = self.build_with([first, second], {"experiment2": {"p_value": 0.02, "stat": 1.5}})
        rows, s = {r["id"]: r for r in ledger["rows"]}, ledger["summary"]
        w = rows["reg_first"]
        self.assertEqual((w["status"], w["online_p"], w["online_rejected_lordpp"], w["online_candidate_saffron"]),
                         ("registered", 1.0, False, False))
        self.assertIsNotNone(rows["reg_second"]["online_rejected_lordpp"])
        self.assertIsNotNone(rows["reg_second"]["online_alpha_saffron"])
        self.assertEqual((s["registered"], s["registered_withdrawn"], s["online"]["waiting_on"]),
                         ([], ["reg_first"], None))
        self.assertEqual(s["registered_before_result"], ["reg_first", "reg_second"])
        self.assert_pending(self.build_with([first]), "reg_first", withdrawn=True)
        with self.assertRaises(ValueError):                     # withdrawing a result that exists would withhold it
            self.build_with([first], {"experiment": {"p_value": 0.3, "stat": 1.0}})

    def test_constraint_violations_raise(self):
        latest = rl.latest_legacy_entry()
        for bad in (_fake(names={"model": "leaderboard.models[0].label"}),
                    _fake(expects=lambda s: s > 0),
                    _fake(p=None),
                    _fake(p=lambda doc: (0.5, {})),
                    _fake(registration=rl.Registration(latest, "experiment.p_value")),
                    _fake(registration=rl.Registration(_day(1).replace("-", "/"), "experiment.p_value"))):
            with self.subTest(row=bad), self.assertRaises(ValueError):
                self.build_with([bad])
        with mock.patch.dict(rl.REGISTERED, {"no_such_row": rl.Registration(_day(1), "experiment.p_value")}):
            with self.assertRaises(ValueError):
                rl.ledger_rows()

    def test_registrations_are_stamped_from_the_table(self):
        row = dataclasses.replace(_fake(), registration=None)

        def _registered_rows():
            return [row]

        with mock.patch.object(rl, "_ROW_FUNCTIONS", (*rl._ROW_FUNCTIONS, _registered_rows)):
            with self.assertRaises(ValueError):                 # a new row must be registered
                rl.ledger_rows()
            with mock.patch.dict(rl.REGISTERED, {"reg_fake": rl.Registration(_day(1), "experiment.p_value")}):
                stamped = rl.ledger_rows()[-1]
                self.assertEqual((stamped.registration, stamped.entered_on), (rl.REGISTERED["reg_fake"], _day(1)))
                self.assertNotIn("reg_fake", rl.legacy_batches())

    def test_the_spec_hash_covers_the_declared_test(self):
        row = _fake()
        digest = rl.spec_sha256(row)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        for changed in (_fake(test="another test"), _fake(field="experiment.q"), _fake(p=rl.Field("experiment.q")),
                        _fake(expects=rl.NEGATIVE), _fake(sided="one"), _fake(hypothesis="Another hypothesis.")):
            self.assertNotEqual(rl.spec_sha256(changed), digest)
        for same in (_fake(note="a note added after the result"), _fake(context={"n": "experiment.n"}),
                     _fake(withdrawn="a reason"), _fake(retracted="a reason")):
            self.assertEqual(rl.spec_sha256(same), digest)

    def test_committed_registrations(self):
        """Every committed registration: a pending row's field does not exist yet (else the ledger was not rebuilt), a
        resolved row's test reads its declared field, and its spec hash is today's definition's."""
        definitions = {row.id: row for row in rl.ledger_rows()}
        s = _committed()["summary"]
        for r in _committed()["rows"]:
            reg = r["registration"]
            if reg is None:
                continue
            with self.subTest(row=r["id"]):
                try:
                    doc = rl.load_artifact(r["artifact"]["path"].removeprefix("data/"))
                except FileNotFoundError:
                    doc = None
                exists = rl._result_exists(doc, reg["field"])
                self.assertEqual(reg["spec_sha256"], rl.spec_sha256(definitions[r["id"]]))
                if r["status"] == "registered":
                    self.assertFalse(exists, f"the result of {r['id']} exists in {r['artifact']['path']}: rebuild "
                                             "the ledger")
                    for key in ("q_value_bh", "p_holm", "t_equivalent", "survives_bh_5pct", "survives_holm_5pct",
                                "clears_hlz_t3"):
                        self.assertIsNone(r[key], key)
                    for key in ("online_rejected_lordpp", "online_rejected_saffron"):
                        self.assertIn(r[key], (None, False) if reg["withdrawn"] else (None,), key)
                    for key in ("supported_surviving_bh_5pct", "supported_surviving_holm_5pct"):
                        self.assertNotIn(r["id"], s[key])
                    self.assertNotIn(r["id"], s["online"]["lordpp"]["rejected"] + s["online"]["saffron"]["rejected"])
                else:
                    self.assertTrue(exists)
                    fields = r["artifact"]["p_value_fields"]
                    self.assertTrue(reg["field"] in fields or all(rl._under(k, reg["field"]) for k in fields))


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


# Rows whose online p-value was corrected after they entered the online stream: a correction recomputes that row and
# every later one (docs/METHODOLOGY.md section 17). Each entry names its CHANGELOG errata entry.
ONLINE_CORRECTIONS: dict[str, str] = {}
ONLINE_COMPARED = ("online_p", "online_alpha_lordpp", "online_rejected_lordpp", "online_alpha_saffron",
                   "online_candidate_saffron", "online_rejected_saffron")


def _append_only_violations(old_rows, new_rows, corrections):
    """How a later ledger breaks an earlier version's online view: a row lost or moved its position, a new row sits
    before an old one, or an online field changed (a null may become non-null) other than downstream of a corrected p
    named in `corrections`."""
    new = {r["id"]: r for r in new_rows}
    problems = [f"{r['id']} left the ledger" for r in old_rows if r["id"] not in new]
    if problems:
        return problems
    old_ids = {r["id"] for r in old_rows}
    last = max((r["online_position"] for r in old_rows if r["online_position"] is not None), default=0)
    for r in old_rows:
        if r["online_position"] != new[r["id"]]["online_position"]:
            problems.append(f"{r['id']} moved from position {r['online_position']} to {new[r['id']]['online_position']}")
    for r in new_rows:
        if r["id"] not in old_ids and r["online_position"] is not None and r["online_position"] <= last:
            problems.append(f"{r['id']} entered at position {r['online_position']}, before the version's last row")
    for r in sorted((r for r in old_rows if r["online_position"] is not None), key=lambda r: r["online_position"]):
        now = new[r["id"]]
        changed = [key for key in ONLINE_COMPARED if r[key] is not None and r[key] != now[key]]
        if changed:
            if r["online_p"] == now["online_p"] or r["id"] not in corrections:
                problems.append(f"{r['id']}: {changed} changed without a corrected p-value named in ONLINE_CORRECTIONS")
            break                                                 # rows after a correction are recomputed
    return problems


class AppendOnlyCheckTest(unittest.TestCase):
    """The history test's comparison, on synthetic versions (main has no version with online fields before 2.12)."""

    def test_the_comparison(self):
        rows = copy.deepcopy(_committed()["rows"])
        self.assertEqual(_append_only_violations(rows, rows, {}), [])
        stream = sorted((r for r in rows if r["online_position"]), key=lambda r: r["online_position"])
        later = copy.deepcopy(rows)
        later.append({**copy.deepcopy(stream[-1]), "id": "appended", "online_position": len(stream) + 1})
        self.assertEqual(_append_only_violations(rows, later, {}), [])
        moved = copy.deepcopy(rows)
        target = next(r for r in moved if r["id"] == stream[3]["id"])
        target["online_alpha_lordpp"] *= 1.5                       # a level moved by a code change
        self.assertTrue(_append_only_violations(rows, moved, {}))
        corrected = copy.deepcopy(rows)
        target = next(r for r in corrected if r["id"] == stream[3]["id"])
        target["online_p"] = 0.5
        nxt = next(r for r in corrected if r["id"] == stream[4]["id"])
        nxt["online_alpha_lordpp"] *= 1.5                          # downstream of the correction: exempt
        self.assertTrue(_append_only_violations(rows, corrected, {}))
        self.assertEqual(_append_only_violations(rows, corrected, {stream[3]["id"]: "CHANGELOG errata: test"}), [])
        pending = copy.deepcopy(rows)
        for r in pending:
            if r["id"] == stream[-1]["id"]:
                r.update(online_p=None, online_rejected_lordpp=None, online_rejected_saffron=None,
                         online_candidate_saffron=None)
        self.assertEqual(_append_only_violations(pending, rows, {}), [])   # a pending row resolving is allowed
        self.assertTrue(_append_only_violations(rows, pending, {}))
        inserted = copy.deepcopy(later)
        inserted[-1]["online_position"] = 2
        self.assertTrue(_append_only_violations(rows, inserted, {}))


class LedgerHistoryTest(unittest.TestCase):
    """Checks against every version of data/research_ledger.json on main (git log --first-parent): the legacy entry
    dates, the registrations' integrity, the append-only online view, and the batch view's stability. Skipped without
    git or without full history (a shallow clone: git fetch --unshallow); CI sets LEDGER_HISTORY_REQUIRED=1 so it
    fails there instead of skipping."""

    BATCH_FIELDS = ("p_value", "sided", "p_two_sided", "status", "q_value_bh", "p_holm", "t_equivalent",
                    "survives_bh_5pct", "survives_holm_5pct", "clears_hlz_t3", "selected_after_search", "pre_specified")

    @classmethod
    def setUpClass(cls):
        reason = None
        if shutil.which("git") is None:
            reason = "git is not installed"
        else:
            try:
                if _git("rev-parse", "--is-shallow-repository").strip() == "true":
                    reason = "the clone is shallow (git fetch --unshallow)"
            except subprocess.CalledProcessError:
                reason = "not a git repository"
        base = None
        if reason is None:
            for ref in ("origin/main", "main", "HEAD"):
                try:
                    _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
                    base = ref
                    break
                except subprocess.CalledProcessError:
                    continue
            if base is None:
                reason = "no main branch"
        if reason is not None:
            if os.environ.get("LEDGER_HISTORY_REQUIRED") == "1":
                raise AssertionError(f"LEDGER_HISTORY_REQUIRED is set, but {reason}")
            raise unittest.SkipTest(reason)
        cls.base = base
        cls.versions = []                                      # oldest first: (sha, UTC date, PR, ledger)
        log = _git("log", "--first-parent", "--reverse", "--format=%H%x09%ct%x09%s", base, "--",
                   "data/research_ledger.json")
        for line in log.splitlines():
            sha, stamp, subject = line.split("\t", 2)
            day = datetime.fromtimestamp(int(stamp), tz=timezone.utc).strftime("%Y-%m-%d")   # %ct: never the +05:30 dates
            pr = re.search(r"\(#(\d+)\)\s*$", subject)
            cls.versions.append((sha, day, f"#{pr.group(1)}" if pr else None,
                                 json.loads(_git("show", f"{sha}:data/research_ledger.json"))))
        cls.first = {}
        for sha, day, pr, doc in cls.versions:
            for r in doc["rows"]:
                cls.first.setdefault(r["id"], (sha, day, pr, r))
        cls.current = _committed()

    def test_the_legacy_entry_dates_are_the_history(self):
        legacy = json.loads((FIXTURES / "ledger_legacy_entry.json").read_text(encoding="utf-8"))["rows"]
        current = {r["id"]: r for r in self.current["rows"]}
        for row in legacy:
            with self.subTest(row=row["id"]):
                self.assertIn(row["id"], self.first, "a legacy row must be on main")
                _, day, pr, _ = self.first[row["id"]]
                self.assertEqual((row["entered_on"], row["pr"]), (day, pr))
                self.assertEqual(current[row["id"]]["entered_on"], day)

    def test_registrations_reached_main_before_their_results(self):
        current_doc = {}
        for r in self.current["rows"]:
            reg = r["registration"]
            if reg is None:
                continue
            source = r["artifact"]["path"]
            with self.subTest(row=r["id"]):
                if r["id"] not in self.first:
                    # New in this change: a registration may not deliver its result in the same change.
                    self.assertEqual(r["status"], "registered", "a registered row reaches main before its result")
                    if source not in current_doc:
                        current_doc[source] = self._artifact_at(self.base, source)
                    self.assertFalse(rl._result_exists(current_doc[source], reg["field"]),
                                     f"the result of {r['id']} already exists on main")
                    continue
                sha, day, _, first = self.first[r["id"]]
                self.assertEqual(first["status"], "registered")
                self.assertEqual(first["registration"]["spec_sha256"], reg["spec_sha256"],
                                 "the declared test changed after the row reached main")
                self.assertLessEqual(reg["on"], day)
                self.assertFalse(rl._result_exists(self._artifact_at(sha, source), reg["field"]),
                                 "the result existed on main when the row was registered")
                if reg["withdrawn"]:
                    for _, _, _, doc in self.versions:
                        old = next((x for x in doc["rows"] if x["id"] == r["id"]), None)
                        if old is not None:
                            self.assertEqual(old["status"], "registered")

    def _artifact_at(self, sha, path):
        try:
            return json.loads(_git("show", f"{sha}:{path}"))
        except subprocess.CalledProcessError:
            return None                                        # a missing file holds no result

    def test_the_online_view_is_append_only(self):
        for sha, _, _, doc in self.versions:
            if not doc["rows"] or "online_position" not in doc["rows"][0]:
                continue
            with self.subTest(version=sha[:7]):
                self.assertEqual(_append_only_violations(doc["rows"], self.current["rows"], ONLINE_CORRECTIONS), [])

    def test_existing_rows_batch_fields_are_unchanged_while_the_family_is(self):
        """Acceptance (ROADMAP 2.12), permanent form: against the newest version on main, if the batch family's
        (id, p_two_sided) set is unchanged, so is every batch field of every row in that version."""
        sha, _, _, newest = self.versions[-1]

        def family(doc):
            return {(r["id"], r["p_two_sided"]) for r in doc["rows"]
                    if r["p_two_sided"] is not None and not r["selected_after_search"]}

        if family(newest) != family(self.current):
            self.skipTest("the batch family changed, so the batch fields may move")
        current = {r["id"]: r for r in self.current["rows"]}
        for r in newest["rows"]:
            for key in self.BATCH_FIELDS:
                self.assertEqual(current[r["id"]][key], r[key], (sha[:7], r["id"], key))


if __name__ == "__main__":
    unittest.main()
