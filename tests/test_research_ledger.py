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

    def test_summary_lists_match_the_rows(self):
        ledger = _committed()
        by_id = {r["id"]: r for r in ledger["rows"]}
        s = ledger["summary"]
        self.assertEqual(sum(s["by_status"].values()), len(ledger["rows"]))
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


if __name__ == "__main__":
    unittest.main()
