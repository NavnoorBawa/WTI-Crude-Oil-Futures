"""Network-free tests for backend/leak_anatomy.py: the retracted Sharpe was the label overlap.

The centerpiece is the rule the whole post-mortem rests on: on returns with no signal at all, a
walk-forward that trains on labels maturing after the decision scores far above a coin flip, and the
purge brings it back to 50%. The committed artifact is checked against a fast subset of its runs.
"""

import json
import unittest
from unittest import mock

import numpy as np

from backend import leak_anatomy as la
from backend.safe_paths import data_json_path


def _artifact():
    return json.loads(data_json_path(la.ARTIFACT).read_text(encoding="utf-8"))


class AnalyticTest(unittest.TestCase):
    def test_sheppard_formula(self):
        self.assertAlmostEqual(la.sign_agreement_probability(0.8), 0.5 + np.arcsin(0.8) / np.pi)
        self.assertAlmostEqual(la.sign_agreement_probability(0.8), 0.79517, places=5)
        self.assertEqual(la.sign_agreement_probability(0.0), 0.5)
        self.assertEqual(la.sign_agreement_probability(1.0), 1.0)

    def test_the_rows_the_purge_drops_are_exactly_the_unmatured_labels(self):
        table = la.analytic_table()
        self.assertEqual(table[0]["label_correlation"], 0.8)          # t-1 shares 4 of 5 returns
        self.assertEqual(sum(not r["label_known_at_decision"] for r in table), la.PURGE)
        self.assertEqual(table[-1]["p_sign_agree_pct"], 50.0)         # t-5: matured, independent

    def test_purge_matches_the_backtest(self):
        from backend.backtest_walk_forward import horizon_steps_for, purge_count
        self.assertEqual(la.HORIZON, horizon_steps_for("1w"))
        self.assertEqual(la.PURGE, purge_count("1w"))

    def test_monte_carlo_agrees_with_the_formula(self):
        mc = la.monte_carlo_agreement(draws=200_000, seed=1)
        self.assertAlmostEqual(mc["gaussian"]["p_sign_agree_leaked_t_minus_1_pct"], 79.52, delta=0.5)
        self.assertAlmostEqual(mc["gaussian"]["p_sign_agree_purged_pct"], 50.0, delta=0.5)


class WalkForwardTest(unittest.TestCase):
    def test_leak_beats_a_coin_flip_on_noise_and_the_purge_removes_it(self):
        noise = la.synthetic_returns(0)
        leaked = la.walk_forward(noise, "nearest_neighbour", purge=False)
        purged = la.walk_forward(noise, "nearest_neighbour", purge=True)
        self.assertGreater(leaked["accuracy_pct"], 65.0)
        self.assertGreater(leaked["sharpe_gross"], 2.0)
        self.assertTrue(40.0 < purged["accuracy_pct"] < 60.0)

    def test_purged_training_never_holds_an_unmatured_label(self):
        sizes = []

        class Recorder:
            def fit(self, x, y):
                sizes.append(len(y))
                return self

            def predict(self, x):
                return np.array([1])

        with mock.patch.dict(la.MODELS, {"recorder": ("records fits", lambda seed: Recorder())}):
            la.walk_forward(la.synthetic_returns(0), "recorder", purge=True)
            purged_sizes = list(sizes)
            sizes.clear()
            la.walk_forward(la.synthetic_returns(0), "recorder", purge=False)
        # First decision: MIN_TRAIN rows observed; the newest PURGE labels mature only after it.
        self.assertEqual(sizes[0], la.MIN_TRAIN)
        self.assertEqual(purged_sizes[0], la.MIN_TRAIN - la.PURGE)
        self.assertEqual(max(purged_sizes), la.TRAIN_WINDOW - la.PURGE)

    def test_block_shuffle_keeps_whole_blocks(self):
        r = np.arange(100.0)
        out = la.block_shuffle(r, seed=3, block=10, n=100)
        self.assertEqual(sorted(out), list(r))
        for chunk in out.reshape(10, 10):
            self.assertTrue(np.all(np.diff(chunk) == 1.0) and chunk[0] % 10 == 0)
        self.assertFalse(np.array_equal(out, r))


class CommittedArtifactTest(unittest.TestCase):
    def setUp(self):
        self.artifact = _artifact()

    def _run_entry(self, block, model, key, seed):
        return next(r for r in block[model][key]["runs"] if r["seed"] == seed)

    def test_artifact_reproduces_a_subset_of_its_runs(self):
        snapshot = la.market_data.load_snapshot()
        all_rets, _ = la.real_returns(snapshot=snapshot)
        cases = (("synthetic_random_walk", "nearest_neighbour", la.synthetic_returns),
                 ("synthetic_random_walk", "decision_tree", la.synthetic_returns),
                 ("block_shuffled_cl", "nearest_neighbour", lambda s: la.block_shuffle(all_rets, s)))
        for block, model, series in cases:
            for purge in (False, True):
                key = "purged" if purge else "unpurged_leaked"
                got = la.walk_forward(series(0), model, purge, seed=0)
                want = self._run_entry(self.artifact[block], model, key, 0)
                with self.subTest(block=block, model=model, purge=purge):
                    self.assertEqual(got["accuracy_pct"], want["accuracy_pct"])
                    self.assertEqual(got["sharpe_gross"], want["sharpe_gross"])
                    self.assertEqual(got["n_decisions"], want["n_decisions"])

    def test_artifact_analytic_block_and_reference_are_current(self):
        self.assertEqual(self.artifact["analytic"]["by_training_row"], la.analytic_table())
        self.assertEqual(self.artifact["retracted_reference"], la.retracted_reference())
        manifest = la.market_data.load_snapshot().manifest
        self.assertEqual(self.artifact["data"]["cl_daily_sha256"], manifest["files"]["cl_daily"]["sha256"])

    def test_headline_contrast(self):
        for block, model in (("synthetic_random_walk", "nearest_neighbour"), ("synthetic_random_walk", "random_forest"),
                             ("block_shuffled_cl", "nearest_neighbour")):
            leaked = self.artifact[block][model]["unpurged_leaked"]
            purged = self.artifact[block][model]["purged"]
            self.assertGreater(leaked["accuracy_pct_range"][0], 60.0)
            self.assertLess(leaked["pooled_binomial_p_value_vs_50"], 1e-10)
            self.assertTrue(45.0 < purged["accuracy_pct_mean"] < 55.0)
            self.assertGreater(purged["pooled_binomial_p_value_vs_50"], 0.05)


if __name__ == "__main__":
    unittest.main()
