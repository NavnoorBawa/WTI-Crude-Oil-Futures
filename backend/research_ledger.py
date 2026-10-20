#!/usr/bin/env python3
"""Research ledger: every hypothesis this project has tested, with multiplicity adjustments.

One row per tested hypothesis, each stated as the claim (the alternative) the project made or
examined. Every statistic and p-value is READ from a committed artifact by its JSON field path, or
computed by a standard formula from fields read the same way (an exact binomial test from a stored
accuracy and sample size, a normal p-value from a stored z, the larger of two stored p-values when a
claim needs both tests, the largest of the per-run p-values when runs share their data and so cannot
be pooled); nothing is typed in by hand. The row records which fields it used, so each number can be
checked against its artifact.

Status, from the test as the project ran it (one- or two-sided as recorded in `sided`):
  supported      p < 0.05 and the estimate points the way the hypothesis says
  inconclusive   0.05 <= p < 0.10, or no p-value but the estimate points the right way
  rejected       p >= 0.10, or the estimate points the wrong way
  retracted      the evidence was invalid when produced: it came from the leaked walk-forward,
                 from roll-contaminated returns, or from a test whose null was wrong. The p-value is
                 kept as a record, and the row stays in the multiplicity count (it was tested).

Multiplicity. Across all rows with a p-value: Benjamini-Hochberg (1995) q-values and Holm (1979)
adjusted p-values, both computed on `p_two_sided`: the directional one-sided p doubled (capped at
1), so one- and two-sided tests are treated alike and a result in the wrong direction never counts
as a discovery. `t_equivalent` is the normal quantile of that two-sided p, and `clears_hlz_t3` flags
the Harvey-Liu-Zhu (2016) hurdle t > 3 for a new factor claim. `pre_specified` is true only where the
code documents the test as fixed before the data were seen; most of this project was exploratory.

Usage: python -m backend.research_ledger   (rewrites data/research_ledger.json from the artifacts)
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from scipy import stats

try:
    from .safe_paths import DATA_ROOT, data_json_path
except ImportError:  # Direct invocation: python backend/research_ledger.py
    from safe_paths import DATA_ROOT, data_json_path

ARTIFACT = "research_ledger.json"
ALPHA = 0.05
INCONCLUSIVE_BELOW = 0.10
HLZ_T = 3.0
STATUSES = ("supported", "rejected", "retracted", "inconclusive")


# ── Reading artifacts by field path ──────────────────────────────────────────
_TOKEN = re.compile(r"\[([^\]]*)\]|([^.\[\]]+)")


class FieldError(KeyError):
    """An artifact field a ledger row reads is missing: the artifact changed shape."""


def resolve(doc: Any, path: str) -> Any:
    """Read `path` from a JSON document. Dots separate keys; `[3]` indexes a list; `[k=v,k2=v2]`
    selects the list item whose fields match (compared as strings)."""
    cur = doc
    for selector, key in _TOKEN.findall(path):
        try:
            if key:
                cur = cur[key]
            elif selector.lstrip("-").isdigit():
                cur = cur[int(selector)]
            else:
                wanted = dict(part.split("=", 1) for part in selector.split(","))
                matches = [item for item in cur
                           if all(str(item.get(k)) == v for k, v in wanted.items())]
                if len(matches) != 1:
                    raise KeyError(selector)
                cur = matches[0]
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise FieldError(f"{path}: cannot resolve {selector or key!r}") from exc
    return cur


# ── p-value sources (each returns the p-value and the fields it read) ─────────
@dataclass(frozen=True)
class Field:
    """A p-value stored in the artifact."""
    path: str

    def __call__(self, doc):
        return float(resolve(doc, self.path)), {self.path: resolve(doc, self.path)}


@dataclass(frozen=True)
class Binomial:
    """Exact one-sided binomial test that an accuracy exceeds `null` (a rate, or a field holding a
    percentage). Hits = round(accuracy% x n / 100). Used where the stored p-value is rounded to 0."""
    n: str
    acc_pct: str
    null: float | str = 0.5

    def __call__(self, doc):
        n = int(resolve(doc, self.n))
        acc = float(resolve(doc, self.acc_pct))
        hits = int(round(acc * n / 100.0))
        null = self.null if isinstance(self.null, float) else float(resolve(doc, self.null)) / 100.0
        used = {self.n: n, self.acc_pct: acc}
        if not isinstance(self.null, float):
            used[self.null] = null * 100.0
        return float(stats.binom.sf(hits - 1, n, null)), used


@dataclass(frozen=True)
class TwoProportion:
    """One-sided pooled two-proportion z-test that accuracy a exceeds accuracy b."""
    acc_a: str
    n_a: str
    acc_b: str
    n_b: str

    def __call__(self, doc):
        pa, pb = float(resolve(doc, self.acc_a)) / 100.0, float(resolve(doc, self.acc_b)) / 100.0
        na, nb = int(resolve(doc, self.n_a)), int(resolve(doc, self.n_b))
        pooled = (pa * na + pb * nb) / (na + nb)
        z = (pa - pb) / np.sqrt(pooled * (1 - pooled) * (1 / na + 1 / nb))
        used = {self.acc_a: pa * 100, self.n_a: na, self.acc_b: pb * 100, self.n_b: nb}
        return float(stats.norm.sf(z)), used


@dataclass(frozen=True)
class NormalZ:
    """p-value of a stored z (or HAC z) statistic: one-sided upper tail, or two-sided."""
    path: str
    sided: str = "one"

    def __call__(self, doc):
        z = float(resolve(doc, self.path))
        p = stats.norm.sf(z) if self.sided == "one" else 2 * stats.norm.sf(abs(z))
        return float(p), {self.path: z}


@dataclass(frozen=True)
class DieboldMariano:
    """Two-sided p-value of a stored Harvey-Leybourne-Newbold DM statistic against t(n - 1)."""
    path: str
    n: str

    def __call__(self, doc):
        stat, n = float(resolve(doc, self.path)), int(resolve(doc, self.n))
        return float(2 * stats.t.sf(abs(stat), df=n - 1)), {self.path: stat, self.n: n}


@dataclass(frozen=True)
class LargestOf:
    """The largest of several stored p-values, for a claim that needs every one of its tests to
    reject (e.g. an event group against both the unmatched and the year-matched placebo)."""
    paths: tuple[str, ...]

    def __call__(self, doc):
        used = {path: resolve(doc, path) for path in self.paths}
        return max(float(v) for v in used.values()), used


@dataclass(frozen=True)
class LargestOverRuns:
    """Intersection-union test (Berger 1982) that every run beats its own null: the largest of the
    runs' stored p-values, `{runs}[i].{p}` for each run. Its level holds however the runs depend on
    one another, so it fits runs that reuse the same data, which a pooled test would count as
    independent samples."""
    runs: str
    p: str

    def __call__(self, doc):
        runs = resolve(doc, self.runs)
        if not isinstance(runs, list) or not runs:
            raise FieldError(f"{self.runs}: not a non-empty list of runs")
        paths = [f"{self.runs}[{i}].{self.p}" for i in range(len(runs))]
        used = {path: resolve(doc, path) for path in paths}
        return max(float(v) for v in used.values()), used


@dataclass(frozen=True)
class SignTest:
    """One-sided exact sign test that a share of periods (percent) exceeds one half."""
    share_pct: str
    n: str

    def __call__(self, doc):
        share, n = float(resolve(doc, self.share_pct)), int(resolve(doc, self.n))
        k = int(round(share * n / 100.0))
        return float(stats.binom.sf(k - 1, n, 0.5)), {self.share_pct: share, self.n: n}


# ── Rows ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Row:
    id: str
    family: str
    hypothesis: str
    test: str
    source: str                         # artifact file name under data/
    statistic: tuple[str, str]          # (label, field path)
    p: Callable | None                  # p-value source, or None when the artifact holds no test
    sided: str                          # "one" or "two": how the stored/computed p-value is defined
    expects: Callable[[float], bool] | None = None   # the direction the hypothesis claims
    beats: str | None = None            # or: the statistic must exceed this field to point the right way
    pre_specified: bool = False
    retracted: str | None = None        # reason, when the evidence was invalid when produced;
                                        # "{label}" inserts that context value
    note: str = ""
    context: dict = field(default_factory=dict)     # label -> field path, shown beside the statistic


VOL = "vol_forecast_validation.json"
LAB = "vol_lab.json"
CARRY = "carry_signal_test.json"
SHOCK = "supply_shock_study.json"
LEAK = "leak_anatomy.json"
LEAKED_PIPELINE = ("look-ahead leak: the walk-forward trained on rows whose 5-session labels matured "
                   "after the decision (see data/leak_anatomy.json)")
ABOVE_50 = lambda s: s > 50.0                                     # noqa: E731
POSITIVE = lambda s: s > 0.0                                      # noqa: E731
NEGATIVE = lambda s: s < 0.0                                      # noqa: E731
_LM = "leaderboard.models[key={}]"


def _direction_rows() -> list[Row]:
    rows = []
    for rid, source, window, horizon, retract in (
        ("dir_1w_5y_leaked", "wf_5y_unpurged_LEAKED.json", "5-year, unpurged as originally run", "1w", LEAKED_PIPELINE),
        ("dir_1w_10y_leaked", "wf_10y_rolling_unpurged_LEAKED.json", "10-year rolling, unpurged", "1w", LEAKED_PIPELINE),
        ("dir_1w_5y_purged", "wf_5y_purged.json", "5-year, purged", "1w", None),
        ("dir_1w_10y_purged", "wf_10y_rolling_purged.json", "10-year rolling, purged", "1w", None),
        ("dir_1d_5y_every_fifth_day", "wf_5y_purged.json", "5-year, scored every fifth day", "1d", None),
        ("dir_1d_5y_every_day", "wf_5y_purged_1d_every_day.json", "5-year, scored every day", "1d", None),
    ):
        res = f"results.{horizon}"
        rows.append(Row(
            id=rid, family="direction model",
            hypothesis=f"The {horizon.upper()} direction model beats a coin flip ({window}).",
            test="one-sided exact binomial test of direction accuracy vs 50%",
            source=source, statistic=("direction accuracy %", f"{res}.metrics.ensemble.direction_accuracy"),
            p=Binomial(f"{res}.samples", f"{res}.metrics.ensemble.direction_accuracy"), sided="one",
            expects=ABOVE_50, retracted=retract,
            context={"n": f"{res}.samples", "sharpe_after_costs": f"{res}.pnl_metrics.sharpe_ratio_annualized"},
        ))
    rows += [
        Row(id="dir_macro_uplift", family="direction model",
            hypothesis="Adding FRED/EIA macro features raises direction accuracy (step-20 A/B).",
            test="one-sided pooled two-proportion z-test, with_macro vs no_macro",
            source="macro_leakage_test.json", statistic=("with_macro accuracy %", "results.with_macro_1w.direction_accuracy"),
            p=TwoProportion("results.with_macro_1w.direction_accuracy", "results.with_macro_1w.samples",
                            "results.no_macro_1w.direction_accuracy", "results.no_macro_1w.samples"),
            sided="one", beats="results.no_macro_1w.direction_accuracy", retracted=LEAKED_PIPELINE,
            context={"no_macro_accuracy_pct": "results.no_macro_1w.direction_accuracy"}),
        Row(id="dir_same_day_context", family="direction model",
            hypothesis="Same-day (16:00 ET) context closes inflate the edge vs context lagged one day.",
            test="one-sided pooled two-proportion z-test, same_day vs lagged_1d",
            source="timing_leakage_test.json", statistic=("same-day accuracy %", "results.same_day.direction_accuracy"),
            p=TwoProportion("results.same_day.direction_accuracy", "results.same_day.samples",
                            "results.lagged_1d.direction_accuracy", "results.lagged_1d.samples"),
            sided="one", beats="results.lagged_1d.direction_accuracy", retracted=LEAKED_PIPELINE,
            context={"lagged_accuracy_pct": "results.lagged_1d.direction_accuracy"}),
        Row(id="dir_hit_rate_at_ess", family="direction model",
            hypothesis="The 1W hit rate stays significant at its serial-correlation effective sample size.",
            test="one-sided z at the Bartlett effective sample size", source="serial_correlation_check.json",
            statistic=("z at ESS", "z_at_ess"), p=Field("p_one_sided_at_ess"), sided="one", expects=POSITIVE,
            retracted=LEAKED_PIPELINE, context={"hit_rate_pct": "hit_rate_pct", "ess": "ess_bartlett_L5"}),
    ]
    return rows


def _leak_rows() -> list[Row]:
    """Synthetic random-walk runs draw independent returns with a 50% no-skill rate, so they are pooled.
    Runs on CL=F returns (block-shuffled permutations of the same series) are not independent, and real
    5-session returns rise more often than they fall, so their no-skill rate is each run's base rate:
    those rows take the largest of the runs' own p-values against their base rates."""
    analytic = "Predicted analytically before the simulation: 1/2 + arcsin(0.8)/pi = 79.5% for the newest leaked label."
    per_run = (" The shuffled runs permute the same CL=F returns, so they are not independent samples and a test "
               "pooled over them (pooled_binomial_p_value_vs_50) would overstate the evidence; real 5-session "
               "returns rise more often than they fall, so each run's no-skill accuracy is its base rate, not 50%. "
               "The p-value is therefore the largest of the runs' own binomial p-values against their base rates. "
               "This test replaced the pooled one after the runs were seen, so the row is not marked pre-specified.")
    rows = []
    for rid, block, desc, pooled in (
        ("leak_forest_noise", "synthetic_random_walk.random_forest",
         "a 20-tree random forest on i.i.d. random walks (no signal exists by construction)", True),
        ("leak_nn_shuffled_cl", "block_shuffled_cl.nearest_neighbour",
         "a 1-nearest-neighbour model on block-shuffled CL=F returns (20-session blocks permuted, so structure "
         "longer than a block is destroyed and little or no signal is expected)", False),
    ):
        for purged in (False, True):
            b = f"{block}.{'purged' if purged else 'unpurged_leaked'}"
            setting = f"Walk-forward {'with' if purged else 'without'} the purge, {desc}"
            if pooled:
                claim = dict(
                    hypothesis=f"{setting} scores above 50% direction accuracy.",
                    test=("one-sided exact binomial test on all decisions pooled over seeds (independent runs; "
                          "labels never overlap within a run)"),
                    p=Field(f"{b}.pooled_binomial_p_value_vs_50"), expects=ABOVE_50, note=analytic)
            else:
                claim = dict(
                    hypothesis=(f"{setting} beats its base rate, the accuracy of always calling the more frequent "
                                "direction, in every run."),
                    test=("one-sided exact binomial test of each run's accuracy against that run's base rate; p is "
                          "the largest of the per-run p-values (intersection-union test, valid however the runs "
                          "depend on one another)"),
                    p=LargestOverRuns(f"{b}.runs", "binomial_p_value_vs_base_rate"),
                    beats=f"{b}.base_rate_pct_mean", note=analytic + per_run)
            rows.append(Row(
                id=f"{rid}_{'purged' if purged else 'unpurged'}", family="leak anatomy",
                source=LEAK, statistic=("mean accuracy %", f"{b}.accuracy_pct_mean"), sided="one",
                pre_specified=pooled,
                context={"gross_sharpe_mean": f"{b}.sharpe_mean", "runs": f"{b}.n_runs",
                         "base_rate_pct_mean": f"{b}.base_rate_pct_mean",
                         "runs_beating_base_rate": f"{b}.runs_beating_base_rate_5pct"},
                **claim,
            ))
    return rows


def _carry_rows() -> list[Row]:
    samples = (("full", "full_sample", "1990-2024"), ("pre2007", "pre_2007", "1990-2006"),
               ("post2007", "post_2007", "2007-2024"))
    rows = []
    for tag, block, span in samples:
        rows += [
            Row(id=f"carry_spread_{tag}", family="carry",
                hypothesis=f"Months with carry above its prior-months median earn more than the rest ({span}).",
                test="one-sided Welch t-test of the monthly return spread", source=CARRY,
                statistic=("above-median-carry return %/month", f"{block}.mean_ret_above_median_carry_pct"),
                p=Field(f"{block}.spread_p_value_one_sided"), sided="one",
                pre_specified=(tag == "full"),
                note=("Rule fixed a priori (carry_signal_test.py docstring); the 2007 split was chosen later."
                      if tag == "full" else ""),
                context={"below_median_pct": f"{block}.mean_ret_below_median_carry_pct", "n_months": f"{block}.n_months"}),
            Row(id=f"carry_rank_{tag}", family="carry",
                hypothesis=f"Carry rank-correlates positively with the next cycle's return ({span}).",
                test="Spearman rank correlation, two-sided", source=CARRY,
                statistic=("rank correlation", f"{block}.carry_rank_corr"),
                p=Field(f"{block}.carry_rank_corr_p_value"), sided="two", expects=POSITIVE),
            Row(id=f"carry_sign_predictor_{tag}", family="carry",
                hypothesis=f"The carry rule calls the sign of the next cycle's return better than always-long ({span}).",
                test="one-sided exact binomial test vs the majority-class base rate", source=CARRY,
                statistic=("direction accuracy %", f"{block}.direction_accuracy_pct"),
                p=Field(f"{block}.direction_p_value"), sided="one",
                context={"base_rate_pct": f"{block}.base_rate_pct"}),
            Row(id=f"carry_sharpe_vs_buy_hold_{tag}", family="carry",
                hypothesis=f"Carry timing has a higher Sharpe ratio than buy-and-hold ({span}).",
                test="Ledoit-Wolf (2008) HAC test of equal Sharpe ratios, two-sided", source=CARRY,
                statistic=("Sharpe difference (annualized)", f"{block}.sharpe_difference.difference_annualized"),
                p=Field(f"{block}.sharpe_difference.ledoit_wolf_p_value"), sided="two", expects=POSITIVE,
                context={"bootstrap_p_value": f"{block}.sharpe_difference.bootstrap_p_value"}),
        ]
    for tag, block, span in (("full", "full_sample", "1990-2024"), ("post2007", "post_2007", "2007-2024")):
        rows.append(Row(
            id=f"carry_sign_split_{tag}", family="carry",
            hypothesis=f"Backwardated months (carry > 0) earn more than contango months ({span}).",
            test="one-sided Welch t-test, true sign split (robustness)", source=CARRY,
            statistic=("carry > 0 return %/month", f"sign_split.{block}.mean_ret_carry_positive_pct"),
            p=Field(f"sign_split.{block}.spread_p_value_one_sided"), sided="one",
            context={"carry_nonpositive_pct": f"sign_split.{block}.mean_ret_carry_nonpositive_pct"}))
    rows += [
        Row(id="carry_spread_changed_after_2007", family="carry",
            hypothesis="The carry return spread differs before and after 2007.",
            test="two-sided Welch z-test of equal spreads", source=CARRY,
            statistic=("pre minus post spread %/month", "regime_change_test.spread_difference_pct"),
            p=Field("regime_change_test.spread_difference_p_value"), sided="two",
            context={"post_2007_power_vs_pre_spread": "power_post_2007.power_vs_pre_2007_spread"}),
        Row(id="carry_rank_changed_after_2007", family="carry",
            hypothesis="The carry rank correlation differs before and after 2007.",
            test="two-sided Fisher z-test of equal Spearman correlations", source=CARRY,
            statistic=("pre minus post rank correlation", "regime_change_test.rank_corr_difference"),
            p=Field("regime_change_test.rank_corr_difference_p_value"), sided="two"),
        Row(id="carry_original_contango_beats", family="carry",
            hypothesis="Below-median-carry months beat above-median months (original test on CL=F).",
            test="one-sided Welch t-test on unadjusted front-month 21-day returns", source=CARRY,
            statistic=("below-median return %/month", "roll_bias_of_original_method.unadjusted_result.mean_ret_contango_pct"),
            p=Field("roll_bias_of_original_method.unadjusted_result.below_median_beats_above_p_value_one_sided"),
            sided="one", retracted="roll-gap contamination: the unadjusted front-month return subtracts the carry itself",
            context={"corr_contamination_carry": "roll_bias_of_original_method.corr_contamination_carry"}),
    ]
    return rows


def _vrp_rows() -> list[Row]:
    vrp = "economic.variance_risk_premium"

    def sharpe_row(name, hypothesis):
        test = f"{vrp}.sharpe_positive_test.{name}"
        return Row(id=f"vrp_{name}_sharpe", family="variance risk premium", hypothesis=hypothesis,
                   test=("one-sided test of Sharpe > 0 with the Mertens (2002) / Opdyke (2007) standard error, "
                         "which allows for the P&L's skewness and kurtosis (p stored in the artifact)"),
                   source=VOL, statistic=("Sharpe (annualized)", f"{vrp}.{name}_sharpe"),
                   p=Field(f"{test}.p_value_one_sided"), sided="one", expects=POSITIVE,
                   context={"skewness": f"{test}.skewness", "excess_kurtosis": f"{test}.excess_kurtosis",
                            "n_periods": f"{vrp}.n_periods"})

    return [
        Row(id="vrp_exists", family="variance risk premium",
            hypothesis="OVX exceeds the realized vol of the following month more often than not.",
            test="one-sided exact sign test over non-overlapping ~20-session periods", source=VOL,
            statistic=("share of periods with a positive premium %", f"{vrp}.share_positive_pct"),
            p=SignTest(f"{vrp}.share_positive_pct", f"{vrp}.n_periods"), sided="one", expects=ABOVE_50,
            context={"mean_premium_vol_pts": f"{vrp}.mean_premium_vol_pts"}),
        sharpe_row("short_vol_swap", "A linear short-vol (vol-swap) proxy earns a positive Sharpe ratio."),
        sharpe_row("short_variance",
                   "A short variance-swap proxy (what an options seller holds) earns a positive Sharpe ratio."),
        Row(id="vrp_timing", family="variance risk premium",
            hypothesis="Selling variance only when OVX is rich vs the model's forecast earns more.",
            test="one-sided Welch t-test, rich vs cheap periods", source=VOL,
            statistic=("rich-period mean P&L", f"{vrp}.timed_rich_mean_pnl"), p=Field(f"{vrp}.timing_p_value"),
            sided="one", beats=f"{vrp}.timed_cheap_mean_pnl", context={"cheap_mean_pnl": f"{vrp}.timed_cheap_mean_pnl"}),
    ]


def _vol_rows() -> list[Row]:
    def lab_dm(rid, model, metric, hypothesis, expects, note="", retracted=None):
        base = f"{_LM.format(model)}.dm_{metric}_vs_har_iv"
        return Row(id=rid, family="volatility forecast", hypothesis=hypothesis,
                   test=f"Diebold-Mariano on daily {metric.upper()} losses (Newey-West, HLN), two-sided; "
                        "a positive statistic means the named model beats HAR-IV",
                   source=LAB, statistic=("DM statistic", f"{base}.stat"), p=Field(f"{base}.p_value"), sided="two",
                   expects=expects, note=note, retracted=retracted,
                   context={"qlike_model": f"{_LM.format(model)}.qlike", "qlike_har_iv": f"{_LM.format('har_iv')}.qlike"})

    ov, ovl = "overall", "economic.vol_target_overlay"
    return [
        Row(id="vol_dir_vs_base_rate", family="volatility forecast",
            hypothesis="HAR-IV calls the direction of next week's realized vol better than the majority class.",
            test="one-sided exact binomial test vs the base rate (weekly grid)", source=VOL,
            statistic=("direction accuracy %", f"{ov}.har_dir_acc_pct"), p=Field(f"{ov}.har_dir_p_value_vs_base_rate"),
            sided="one", retracted=("invalid null: with constant volatility the same pipeline scores about 75% "
                                    "(overall.direction_noise_floor; vol_lab.json noise_floor)"),
            context={"base_rate_pct": f"{ov}.majority_class_pct", "z_hac": f"{ov}.har_dir_z_score_hac"}),
        Row(id="vol_dir_vs_noise_floor", family="volatility forecast",
            hypothesis="HAR-IV's vol-direction accuracy exceeds the constant-volatility noise floor.",
            test=("one-sided binomial vs the simulated noise-floor mean (approximate: consecutive weekly labels "
                  "share a week, so the true p is larger)"), source=VOL,
            statistic=("direction accuracy %", f"{ov}.har_dir_acc_pct"),
            p=Binomial(f"{ov}.n", f"{ov}.har_dir_acc_pct", null=f"{ov}.direction_noise_floor.dir_acc_pct_mean"),
            sided="one", context={"noise_floor_pct": f"{ov}.direction_noise_floor.dir_acc_pct_mean"}),
        Row(id="vol_dir_vs_mean_reversion", family="volatility forecast",
            hypothesis="HAR-IV calls vol direction better than a 'reverts to its 66-day mean' rule.",
            test="one-sided paired HAC z-test on weekly hits", source=VOL,
            statistic=("paired HAC z", f"{ov}.har_vs_mean_reversion_z_hac"), p=Field(f"{ov}.har_vs_mean_reversion_p_value"),
            sided="one", expects=POSITIVE,
            retracted=("invalid null: with nothing to forecast (i.i.d. returns, constant volatility) the same "
                       "pipeline's HAR already beats this rule, {noise_floor_har_pct}% vs "
                       "{noise_floor_mean_reversion_pct}% (overall.direction_noise_floor; vol_lab.json noise_floor "
                       "shows the same gap), so a positive z is expected without any forecasting skill"),
            context={"har_pct": f"{ov}.har_dir_acc_pct", "mean_reversion_pct": f"{ov}.mean_reversion_dir_acc_pct",
                     "noise_floor_har_pct": f"{ov}.direction_noise_floor.dir_acc_pct_mean",
                     "noise_floor_mean_reversion_pct": f"{ov}.direction_noise_floor.mean_reversion_dir_acc_pct_mean"}),
        lab_dm("vol_qlike_vs_persistence", "persistence", "qlike",
               "HAR-IV beats persistence (last week's realized vol) on QLIKE.", NEGATIVE),
        lab_dm("vol_ovx_gain_qlike", "har_rv", "qlike",
               "Adding OVX to HAR-RV lowers QLIKE: HAR-IV (the model of record) beats HAR-RV.", NEGATIVE),
        Row(id="vol_ovx_gain_direction", family="volatility forecast",
            hypothesis="Adding OVX raises vol-direction accuracy (HAR-IV vs HAR without OVX).",
            test="one-sided paired HAC z-test on weekly hits", source=VOL,
            statistic=("paired HAC z", f"{ov}.ovx_gain_dir_z_hac"), p=NormalZ(f"{ov}.ovx_gain_dir_z_hac"),
            sided="one", expects=POSITIVE,
            context={"har_iv_pct": f"{ov}.har_dir_acc_pct", "har_no_ovx_pct": f"{ov}.har_no_ovx_dir_acc_pct"}),
        Row(id="vol_leverage_term", family="volatility forecast",
            hypothesis="Adding the leverage (downside semivolatility) term raises vol-direction accuracy.",
            test="one-sided paired HAC z-test on weekly hits", source=VOL,
            statistic=("paired HAC z", f"{ov}.leverage_gain_dir_z_hac"), p=NormalZ(f"{ov}.leverage_gain_dir_z_hac"),
            sided="one", expects=POSITIVE, context={"leverage_qlike": f"{ov}.leverage_variant_qlike",
                                                    "har_iv_qlike": f"{ov}.har_qlike"}),
        lab_dm("vol_vs_implied_regression", "ovx_only", "qlike",
               "HAR-IV beats a regression on implied vol (OVX) alone on QLIKE.", NEGATIVE),
        lab_dm("vol_vs_raw_ovx", "raw_ovx", "qlike",
               "HAR-IV beats raw OVX used directly as the forecast on QLIKE.", NEGATIVE),
        lab_dm("vol_same_day_ovx", "har_iv_sameday", "qlike",
               "Same-day OVX (the v1 timing, which settles after CL and overlaps the target) lowers QLIKE vs "
               "OVX lagged one session.", POSITIVE,
               note="An information advantage the deployed model cannot use: the reason v2 lags OVX."),
        lab_dm("vol_challenger_qlike", "har_iv_range", "qlike",
               "The challenger HAR-IV-range beats the model of record on QLIKE.", POSITIVE,
               note="One pairwise test chosen after seeing the leaderboard; the SPA row controls for the search."),
        lab_dm("vol_challenger_mse", "har_iv_range", "mse",
               "The challenger HAR-IV-range beats the model of record on MSE of variance.", POSITIVE),
        Row(id="vol_spa_beats_record", family="volatility forecast",
            hypothesis="Some benchmark model beats the model of record on QLIKE (Hansen SPA, data-snooping robust).",
            test="Hansen (2005) SPA, consistent p-value, stationary bootstrap", source=LAB,
            statistic=("SPA consistent p-value", "leaderboard.spa_qlike_vs_har_iv.p_consistent"),
            p=Field("leaderboard.spa_qlike_vs_har_iv.p_consistent"), sided="one",
            context={"p_lower": "leaderboard.spa_qlike_vs_har_iv.p_lower"}),
        Row(id="vol_mcs_excludes_record", family="volatility forecast",
            hypothesis=("The model of record is significantly worse than the best model on QLIKE (excluded from "
                        "the Model Confidence Set)."),
            test=("Hansen-Lunde-Nason MCS p-value of HAR-IV (range statistic), judged at the ledger's 5% level; "
                  "the lab's own set (mcs_members) is the 1 - mcs_size confidence set"), source=LAB,
            statistic=("MCS p-value of HAR-IV", f"{_LM.format('har_iv')}.mcs_qlike_p"),
            p=Field(f"{_LM.format('har_iv')}.mcs_qlike_p"), sided="one",
            context={"mcs_members": "leaderboard.mcs_qlike_included", "mcs_size": "config.mcs.size"}),
        Row(id="vol_oos_model_selection", family="volatility forecast",
            hypothesis="Choosing each week's model by its trailing loss beats the model of record on QLIKE.",
            test="Diebold-Mariano on daily QLIKE, two-sided", source=LAB,
            statistic=("DM statistic", "oos_selection.dm_vs_har_iv.stat"), p=Field("oos_selection.dm_vs_har_iv.p_value"),
            sided="two", expects=POSITIVE),
        Row(id="vol_record_biased", family="volatility forecast",
            hypothesis="HAR-IV's variance forecast is biased (Mincer-Zarnowitz intercept 0, slope 1 rejected).",
            test="HAC Wald test of the Mincer-Zarnowitz regression", source=LAB,
            statistic=("MZ slope", f"{_LM.format('har_iv')}.mincer_zarnowitz.slope"),
            p=Field(f"{_LM.format('har_iv')}.mincer_zarnowitz.p_unbiased"), sided="two",
            context={"intercept": f"{_LM.format('har_iv')}.mincer_zarnowitz.intercept"}),
        Row(id="vol_ovx_coefficient", family="volatility forecast",
            hypothesis="OVX carries information about next-week realized vol beyond the HAR terms (in-sample).",
            test="HAC t-statistic of the log-OVX coefficient, full sample, two-sided", source=LAB,
            statistic=("t (HAC)", "har_iv_coefficients.terms[term=log OVX].t_hac"),
            p=NormalZ("har_iv_coefficients.terms[term=log OVX].t_hac", sided="two"), sided="two", expects=POSITIVE,
            note="In-sample; the out-of-sample version is vol_ovx_gain_qlike."),
        Row(id="vol_target_overlay", family="volatility forecast",
            hypothesis="Sizing a long WTI position by the forecast (vol targeting) beats buy-and-hold on Sharpe.",
            test=("stationary block bootstrap test of equal Sharpe ratios, forecast vol target minus buy-and-hold "
                  "(weekly, after costs), two-sided"), source=VOL,
            statistic=("Sharpe difference (annualized)",
                       f"{ovl}.sharpe_difference_tests.forecast_minus_buy_hold.difference"),
            p=Field(f"{ovl}.sharpe_difference_tests.forecast_minus_buy_hold.p_value"), sided="two", expects=POSITIVE,
            note=("Vol targeting is a risk control: its effect on realized vol, kurtosis and drawdown is in "
                  "economic.vol_target_overlay.risk."),
            context={"forecast_vol_target_sharpe": f"{ovl}.sharpe_forecast_vol_target",
                     "buy_hold_sharpe": f"{ovl}.sharpe_buy_hold",
                     "trailing_vol_target_sharpe": f"{ovl}.sharpe_trailing_vol_target",
                     "difference_ci95": f"{ovl}.sharpe_difference_tests.forecast_minus_buy_hold.ci95"}),
    ]


def _var_rows() -> list[Row]:
    grid = "var_backtest.grids.0.methods[key={},level={}]"
    out = []
    for rid, key, level, test, hyp in (
        ("var_normal_99_coverage", "har_iv:normal", "0.01", "kupiec.p_value",
         "Normal-quantile HAR-IV 99% VaR has the wrong exceedance rate (production grid)."),
        ("var_fhs_99_coverage", "har_iv:fhs", "0.01", "kupiec.p_value",
         "Filtered-historical-simulation HAR-IV 99% VaR has the wrong exceedance rate (production grid)."),
        ("var_fhs_95_dq", "har_iv:fhs", "0.05", "dq.p_value",
         "FHS HAR-IV 95% VaR exceedances are predictable (Engle-Manganelli DQ, production grid)."),
        ("var_hs_95_clustering", "historical_simulation", "0.05", "christoffersen.p_independence",
         "Unconditional historical-simulation 95% VaR exceedances cluster (production grid)."),
    ):
        m = grid.format(key, level)
        out.append(Row(id=rid, family="value at risk", hypothesis=hyp,
                       test={"kupiec.p_value": "Kupiec (1995) unconditional coverage LR",
                             "dq.p_value": "Engle-Manganelli (2004) dynamic quantile test",
                             "christoffersen.p_independence": "Christoffersen (1998) independence LR"}[test],
                       source=LAB, statistic=("exceedances", f"{m}.kupiec.exceedances"), p=Field(f"{m}.{test}"),
                       sided="two", context={"expected": f"{m}.kupiec.expected"}))
    return out


def _shock_rows() -> list[Row]:
    rows = []
    for key in ("supply_lost", "threat_only", "opec_cut", "weather", "conflict", "sanctions", "iran_driven",
                "strait_risk"):
        g = f"groups[key={key}]"
        for metric, word in (("peak", "peak"), ("settle", "settled move")):
            rows.append(Row(
                id=f"shock_{key}_{metric}", family="supply shock",
                hypothesis=f"Events in group '{key}' move WTI differently from a random non-event date ({word}).",
                test=(f"two-sided resampling tests of the group's median {metric} vs n random placebo dates and vs "
                      "placebo dates from each event's own year (supply_shock_study.json method); p is the larger "
                      "of the two, as the study's verdict needs both below 5% (year matching controls for the "
                      "volatility regime, which inflates a 20-session maximum)"),
                source=SHOCK, statistic=(f"median {metric} %", f"{g}.median_{metric}_pct"),
                p=LargestOf((f"{g}.p_value_vs_placebo.{metric}", f"{g}.p_value_vs_year_matched_placebo.{metric}")),
                sided="two", context={"n": f"{g}.n", "placebo_median_pct": f"placebo.median_{metric}_pct"}))
    rows += [
        Row(id="shock_removal_vs_threat_peak", family="supply shock",
            hypothesis="Supply-removal events peak higher than threat-only events.",
            test="two-sided permutation test of the difference in medians", source=SHOCK,
            statistic=("median peak difference %", "between_groups.median_peak_difference_pct"),
            p=Field("between_groups.p_value.peak"), sided="two", expects=POSITIVE),
        Row(id="shock_removal_vs_threat_settle", family="supply shock",
            hypothesis="Supply-removal events keep more of their move than threat-only events (settle).",
            test="two-sided permutation test of the difference in medians", source=SHOCK,
            statistic=("median settle difference %", "between_groups.median_settle_difference_pct"),
            p=Field("between_groups.p_value.settle"), sided="two", expects=POSITIVE),
        Row(id="shock_priced_in", family="supply shock",
            hypothesis="A strong first-day move (>= 3%) changes the further rise that follows.",
            test="two-sided Mann-Whitney test, strong vs weak day-0 events", source=SHOCK,
            statistic=("strong day-0 median further rise %", "priced_in.strong_day0_median_further_rise_pct"),
            p=Field("priced_in.strong_vs_weak_mann_whitney_p"), sided="two",
            context={"weak_day0_median_further_rise_pct": "priced_in.weak_day0_median_further_rise_pct",
                     "strong_day0_n": "priced_in.strong_day0_n", "weak_day0_n": "priced_in.weak_day0_n"}),
    ]
    return rows


def ledger_rows() -> list[Row]:
    return _direction_rows() + _leak_rows() + _carry_rows() + _vrp_rows() + _vol_rows() + _var_rows() + _shock_rows()


def source_names() -> set[str]:
    """The artifacts the ledger reads: fixed in code, never taken from the command line."""
    return {row.source for row in ledger_rows()}


def load_artifact(name: str) -> dict:
    """Read one of the ledger's own source artifacts from data/."""
    if name not in source_names():
        raise ValueError(f"{name} is not a research-ledger source")
    path = DATA_ROOT / name
    if path.parent != DATA_ROOT or path.name != name:
        raise ValueError(f"{name} is not a file in data/")
    return json.loads(path.read_text(encoding="utf-8"))


# ── Multiplicity ─────────────────────────────────────────────────────────────
def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """BH (1995) q-values: min over j >= i of p_(j) m / j, capped at 1, in the input order."""
    p = np.asarray(p, dtype=float)
    m = p.size
    order = np.argsort(p, kind="mergesort")
    scaled = p[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(scaled[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1.0)
    return out


def holm(p: np.ndarray) -> np.ndarray:
    """Holm (1979) step-down adjusted p-values, in the input order."""
    p = np.asarray(p, dtype=float)
    m = p.size
    order = np.argsort(p, kind="mergesort")
    scaled = p[order] * (m - np.arange(m))
    adj = np.maximum.accumulate(np.minimum(scaled, 1.0))
    out = np.empty(m)
    out[order] = adj
    return out


def _directional_one_sided(p: float, sided: str, direction_ok: bool | None) -> float:
    if sided == "one":
        return p
    if direction_ok is False:
        return 1.0 - p / 2.0
    return p / 2.0


def _status(row: Row, p: float | None, direction_ok: bool | None) -> str:
    if row.retracted:
        return "retracted"
    if direction_ok is False:
        return "rejected"
    if p is None:
        return "inconclusive"
    if p < ALPHA:
        return "supported"
    if p < INCONCLUSIVE_BELOW:
        return "inconclusive"
    return "rejected"


def _artifact_date(doc: dict) -> str | None:
    for path in ("generated_at", "timestamp", "generated_from.retrieved_at"):
        try:
            return str(resolve(doc, path))[:10]
        except FieldError:
            continue
    return None


def _num(x):
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x)
    return x


def _by_family(rows: list[dict]) -> dict:
    """Per family: rows by status, and how many supported rows survive BH at 5% and clear t > 3."""
    out = {}
    for fam in dict.fromkeys(r["family"] for r in rows):
        members = [r for r in rows if r["family"] == fam]
        supported = [r for r in members if r["status"] == "supported"]
        out[fam] = {
            "rows": len(members),
            "by_status": {s: sum(r["status"] == s for r in members) for s in STATUSES},
            "supported_surviving_bh_5pct": sum(bool(r["survives_bh_5pct"]) for r in supported),
            "supported_clearing_hlz_t3": sum(bool(r["clears_hlz_t3"]) for r in supported),
        }
    return out


def build(load=None) -> dict:
    """The ledger from the committed artifacts (`load(name)` returns a parsed artifact)."""
    load = load or load_artifact
    docs: dict[str, dict] = {}
    rows = []
    for row in ledger_rows():
        doc = docs.setdefault(row.source, load(row.source))
        stat_label, stat_path = row.statistic
        stat = resolve(doc, stat_path)
        p, p_inputs = (None, {}) if row.p is None else row.p(doc)
        direction_ok = None
        if row.beats is not None:
            direction_ok = float(stat) > float(resolve(doc, row.beats))
        elif row.expects is not None:
            direction_ok = bool(row.expects(float(stat)))
        p_one = None if p is None else _directional_one_sided(p, row.sided, direction_ok)
        context = {k: _num(resolve(doc, v)) for k, v in row.context.items()}
        rows.append({
            "id": row.id,
            "date": _artifact_date(doc),
            "family": row.family,
            "hypothesis": row.hypothesis,
            "test": row.test,
            "statistic": {"name": stat_label, "value": _num(stat)},
            "context": context,
            "p_value": None if p is None else float(p),
            "sided": row.sided,
            "p_two_sided": None if p_one is None else float(min(1.0, 2.0 * p_one)),
            "pre_specified": row.pre_specified,
            "status": _status(row, p, direction_ok),
            "retraction_reason": row.retracted.format(**context) if row.retracted else None,
            "note": row.note,
            "artifact": {"path": f"data/{row.source}", "statistic_field": stat_path,
                         "p_value_fields": {k: _num(v) for k, v in p_inputs.items()},
                         "context_fields": dict(row.context)},
        })

    tested = [r for r in rows if r["p_two_sided"] is not None]
    p2 = np.array([r["p_two_sided"] for r in tested])
    q, h = benjamini_hochberg(p2), holm(p2)
    for r, qv, hv in zip(tested, q, h):
        r["q_value_bh"] = float(qv)
        r["p_holm"] = float(hv)
        r["t_equivalent"] = round(float(stats.norm.isf(max(r["p_two_sided"], 1e-300) / 2.0)), 2)
        r["survives_bh_5pct"] = bool(qv < ALPHA)
        r["survives_holm_5pct"] = bool(hv < ALPHA)
        r["clears_hlz_t3"] = bool(r["t_equivalent"] > HLZ_T)
    for r in rows:
        if r["p_two_sided"] is None:
            r.update({"q_value_bh": None, "p_holm": None, "t_equivalent": None, "survives_bh_5pct": None,
                      "survives_holm_5pct": None, "clears_hlz_t3": None})

    supported = [r for r in rows if r["status"] == "supported"]
    summary = {
        "rows": len(rows),
        "rows_with_p_value": len(tested),
        "by_status": {s: sum(r["status"] == s for r in rows) for s in STATUSES},
        "pre_specified_rows": sum(r["pre_specified"] for r in rows),
        "supported": [r["id"] for r in supported],
        "supported_surviving_bh_5pct": [r["id"] for r in supported if r["survives_bh_5pct"]],
        "supported_surviving_holm_5pct": [r["id"] for r in supported if r["survives_holm_5pct"]],
        "supported_clearing_hlz_t3": [r["id"] for r in supported if r["clears_hlz_t3"]],
        "supported_failing_bh_5pct": [r["id"] for r in supported if not r["survives_bh_5pct"]],
        "by_family": _by_family(rows),
    }
    return {
        "method": ("One row per tested hypothesis; statistics and p-values read from the named artifact "
                   "fields (or computed from them by the formula in `test`). Status uses the p-value as the "
                   "test was run (alpha 0.05; 0.05-0.10 inconclusive). Benjamini-Hochberg q-values and Holm "
                   "adjusted p-values are computed across every row with a p-value, on p_two_sided (the "
                   "directional one-sided p doubled, capped at 1). t_equivalent = normal quantile of "
                   "p_two_sided; clears_hlz_t3 applies the Harvey-Liu-Zhu (2016) t > 3 hurdle."),
        "alpha": ALPHA,
        "hlz_t": HLZ_T,
        "summary": summary,
        "rows": rows,
        "generated_by": "python -m backend.research_ledger",
    }


def main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    ledger = build()
    path = data_json_path(ARTIFACT)
    path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    for r in ledger["rows"]:
        p = "   n/a  " if r["p_value"] is None else f"{r['p_value']:.2e}"
        q = "   n/a  " if r["q_value_bh"] is None else f"{r['q_value_bh']:.2e}"
        flags = "".join(flag for flag, on in (("B", r["survives_bh_5pct"]), ("H", r["survives_holm_5pct"]),
                                              ("T", r["clears_hlz_t3"])) if on)
        print(f"{r['id']:34s} {r['status']:12s} p={p} q={q} {flags:3s} {'pre' if r['pre_specified'] else ''}")
    s = ledger["summary"]
    print(f"\n{s['rows']} rows ({s['by_status']}); supported surviving BH: {len(s['supported_surviving_bh_5pct'])}"
          f"/{len(s['supported'])}; clearing t > 3: {len(s['supported_clearing_hlz_t3'])}")
    print(f"wrote data/{ARTIFACT}")


if __name__ == "__main__":
    main()
