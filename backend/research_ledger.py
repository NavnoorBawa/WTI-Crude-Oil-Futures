#!/usr/bin/env python3
"""Research ledger: every hypothesis this project has tested, with multiplicity adjustments.

One row per tested hypothesis, each stated as the claim (the alternative) the project made or
examined. Every statistic and p-value is READ from a committed artifact by its JSON field path, or
computed by a standard formula from fields read the same way (an exact binomial test from a stored
accuracy and sample size, a normal p-value from a stored z, the larger of two stored p-values when a
claim needs both tests, the largest of the per-run p-values when runs share their data and so cannot
be pooled, the Bonferroni bound min(1, k x the smallest) when any one of k runs may reject); nothing
is typed in by hand. The row records which fields it used, so each number can be
checked against its artifact.

Status, from the test as the project ran it (one- or two-sided as recorded in `sided`):
  supported      p < 0.05 and the estimate points the way the hypothesis says
  inconclusive   0.05 <= p < 0.10, or no p-value but the estimate points the right way
  not_supported  p >= 0.10, or the estimate points the wrong way. This is a failure to reject the
                 null, not evidence against the hypothesis: a test with little power fails to reject
                 a real effect too. Where the artifact holds the test's power, the row records it in
                 its context and `underpowered` is true when it is below the artifact's target power
                 (null where no power figure exists).
  retracted      the evidence was invalid when produced: it came from the leaked walk-forward,
                 from roll-contaminated returns, or from a test whose null was wrong. The p-value is
                 kept as a record, and the row stays in the multiplicity count (it was tested).
  registered     declared, with its complete test and the artifact field that will hold its result,
                 and merged before that field existed; no result yet (or withdrawn before one
                 existed). It has no p-value and no batch decision; a pending one has no online
                 decision (its online level is fixed once every earlier row is decided), and a
                 withdrawn one is tested online at p = 1 but not counted as tested (one selected
                 after a search is outside the online stream, as every such row is, pending or
                 withdrawn). Its status changes
                 by itself on the first rebuild after the field holds its result. A stored null is no
                 result yet, unless the declared test reads that field as optional (Field(optional=
                 True)): then the null is the result, a test the artifact could not compute, and the
                 row takes the status of any such row (inconclusive, or not_supported when its stored
                 estimate points the wrong way). `registered` means fixed before the result existed;
                 `pre_specified` means fixed before the data were seen.

Multiplicity. Across the rows with a p-value: Benjamini-Hochberg (1995) q-values and Holm (1979)
adjusted p-values, both computed on `p_two_sided`: the directional one-sided p doubled (capped at
1), so one- and two-sided tests are treated alike and a result in the wrong direction never counts
as a discovery. `t_equivalent` is the normal quantile of that two-sided p, and `clears_hlz_t3` flags
the Harvey-Liu-Zhu (2016) hurdle t > 3 for a new factor claim. `pre_specified` is true only where the
code documents the test as fixed before the data were seen; most of this project was exploratory.
A row marked `selected_after_search` tests a model picked as the best of a field on the same sample:
its pairwise p-value ignores that search, so the row is left out of the multiplicity family and its
q-value, Holm p-value and survival flags are null; its note names the search-adjusted test where one
exists (the SPA row adjusts the full-sample comparison) or says none does. A row whose artifact
could not compute its test (a stored p-value of null) has no p-value, is inconclusive (not_supported
when its stored estimate points the wrong way), and is outside the family.

Online control (ROADMAP 2.12). The batch view above is recomputed over the whole family whenever a
row is added, so an old claim's survival can change because of hypotheses tested later. The online
view runs LORD++ (Ramdas, Yang, Wainwright and Jordan 2017) and SAFFRON (Ramdas, Zrnic, Wainwright
and Jordan 2018) at a target false discovery rate of ALPHA over the rows in the order they entered
the ledger, with the defaults of onlineFDR 2.21.3 (ONLINE_REFERENCE); neither gamma sequence is
normalised to a horizon, so a row's level depends only on the rows before it and appending rows never
changes an earlier decision. The stream is every row of the batch family, tested on `p_two_sided`,
plus rows whose test could not be computed; retracted and uncomputed rows enter at p = 1 (they spend
their level and can neither be rejected nor fund later tests), rows selected after a search stay
out, and registered rows follow in the order of REGISTERED (registration order, which a new
registration extends), so a registered row holds the position it was given when it was registered.
A pending registered row in the stream blocks the decisions after it until it resolves or is
withdrawn (then it enters at p = 1). The rows a registry of generated families (REGISTERED_LAB_MODELS,
REGISTERED_DISTRIBUTION_MODELS) makes for a model are withdrawn together, which retires the key in
that registry: they read one entry the lab writes whole (leaderboard.models[key=...] or
distribution.models[key=...]), so the entry that would hold a pending row's result would hold its
withdrawn sibling's too, and a withdrawn row's result may never exist. The two registries' rows read
different entries (a distribution row reads the leaderboard only as context, and only for a model in
LAB_COMPARED), so each is withdrawn on its own, and a model's distribution rows resolve with no
leaderboard entry under their key: after its lab rows are withdrawn, or when its distribution is
registered again under a new key. Both procedures assume independent null
p-values, which these rows do not have, and no status reads either online view. The rows that
entered before registration began have no order fixed in advance: their entry batches are dated
from the git history, but their order within each batch is the code's, fixed after their p-values
were known, so the summary records how the rejection counts move when it is drawn at random (a fixed
seed).

Provenance. `entered_on` (LEGACY_ENTRY, the UTC date of the squash merge on main that first held a
group of rows) and the registration dates (REGISTERED) are provenance metadata taken from the git
history and checked against it by tests/test_research_ledger.py; they are not statistics, so the rule
that no statistic is typed in by hand still holds.

Usage: python -m backend.research_ledger   (rewrites data/research_ledger.json from the artifacts)
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable, TypedDict, cast

import numpy as np
from scipy import stats

if TYPE_CHECKING:
    # A parsed JSON artifact, and a p-value source: called on an artifact, it returns the p-value and
    # the fields it read (field path -> value).
    Artifact = dict[str, Any]
    PValueSource = Callable[[Artifact], tuple[float | None, dict[str, Any]]]

    # _power's result: context labels (label -> field path) and the underpowered_if pair, or neither.
    class Power(TypedDict, total=False):
        context: dict[str, str]
        underpowered_if: tuple[str, str]

try:
    from .safe_paths import DATA_ROOT, data_json_path
except ImportError:  # Direct invocation: python backend/research_ledger.py
    from safe_paths import DATA_ROOT, data_json_path  # type: ignore[import-not-found, no-redef]

ARTIFACT = "research_ledger.json"
ALPHA = 0.05
INCONCLUSIVE_BELOW = 0.10
HLZ_T = 3.0
STATUSES = ("supported", "not_supported", "retracted", "inconclusive", "registered")

# Online false-discovery control (ROADMAP 2.12): the defaults of the reference implementation, onlineFDR 2.21.3.
LORD_W0 = ALPHA / 10                  # LORD++ initial wealth (the paper suggests alpha/2)
LORD_GAMMA_C = 0.07908197             # Javanmard-Montanari (2018) eq. 31 constant, onlineFDR >= 2.21.2
SAFFRON_W0 = ALPHA / 2                # SAFFRON initial wealth
SAFFRON_LAMBDA = 0.5                  # SAFFRON candidate threshold
SAFFRON_GAMMA_C = 0.4374901658        # 1 / zeta(1.6)
SAFFRON_GAMMA_S = 1.6
ONLINE_REFERENCE = "onlineFDR 2.21.3 (Bioconductor; commit e3a5a68): LORD(version = '++') and SAFFRON defaults"
ORDER_DRAWS = 1000                    # legacy order sensitivity: random orders within each entry batch
ORDER_SEED = 212                      # fixed in code before the production build
ONLINE_ORDER = ("entered_on (the UTC date the row entered main; registered_on for registered rows), then, for "
                "registered rows, their place in REGISTERED and, for the others, their place in ledger_rows()")


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
    """A p-value stored in the artifact. With `optional`, a stored null (a test the artifact could not compute)
    reads as no p-value."""
    path: str
    optional: bool = False

    def __call__(self, doc: Artifact) -> tuple[float | None, dict[str, Any]]:
        value = resolve(doc, self.path)
        if value is None and self.optional:
            return None, {self.path: None}
        return float(value), {self.path: value}


@dataclass(frozen=True)
class Binomial:
    """Exact one-sided binomial test that an accuracy exceeds `null` (a rate, or a field holding a
    percentage). Hits = round(accuracy% x n / 100). Used where the stored p-value is rounded to 0."""
    n: str
    acc_pct: str
    null: float | str = 0.5

    def __call__(self, doc: Artifact) -> tuple[float, dict[str, Any]]:
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

    def __call__(self, doc: Artifact) -> tuple[float, dict[str, Any]]:
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

    def __call__(self, doc: Artifact) -> tuple[float, dict[str, Any]]:
        z = float(resolve(doc, self.path))
        p = stats.norm.sf(z) if self.sided == "one" else 2 * stats.norm.sf(abs(z))
        return float(p), {self.path: z}


@dataclass(frozen=True)
class LargestOf:
    """The largest of several stored p-values, for a claim that needs every one of its tests to
    reject (e.g. an event group against both the unmatched and the year-matched placebo)."""
    paths: tuple[str, ...]

    def __call__(self, doc: Artifact) -> tuple[float, dict[str, Any]]:
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

    def __call__(self, doc: Artifact) -> tuple[float, dict[str, Any]]:
        runs = resolve(doc, self.runs)
        if not isinstance(runs, list) or not runs:
            raise FieldError(f"{self.runs}: not a non-empty list of runs")
        paths = [f"{self.runs}[{i}].{self.p}" for i in range(len(runs))]
        used = {path: resolve(doc, path) for path in paths}
        return max(float(v) for v in used.values()), used


@dataclass(frozen=True)
class BonferroniOverRuns:
    """Union-intersection test that at least one run rejects: min(1, k x the smallest of the k runs' stored p-values,
    `{runs}[i].{p}`). Valid however the runs depend on one another (here the five sampling grids, disjoint weeks of
    one series), where LargestOverRuns tests that every run rejects."""
    runs: str
    p: str

    def __call__(self, doc: Artifact) -> tuple[float, dict[str, Any]]:
        runs = resolve(doc, self.runs)
        if not isinstance(runs, list) or not runs:
            raise FieldError(f"{self.runs}: not a non-empty list of runs")
        paths = [f"{self.runs}[{i}].{self.p}" for i in range(len(runs))]
        used = {path: resolve(doc, path) for path in paths}
        return min(1.0, len(paths) * min(float(v) for v in used.values())), used


@dataclass(frozen=True)
class SignTest:
    """One-sided exact sign test that a share of periods (percent) exceeds one half."""
    share_pct: str
    n: str

    def __call__(self, doc: Artifact) -> tuple[float, dict[str, Any]]:
        share, n = float(resolve(doc, self.share_pct)), int(resolve(doc, self.n))
        k = int(round(share * n / 100.0))
        return float(stats.binom.sf(k - 1, n, 0.5)), {self.share_pct: share, self.n: n}


# The p-value sources a registered row may declare (its declared test is hashed from the source's fields).
P_SOURCES = (Field, Binomial, TwoProportion, NormalZ, LargestOf, LargestOverRuns, BonferroniOverRuns, SignTest)


# ── Rows ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Registration:
    """A hypothesis declared before its result exists (ROADMAP 2.12): its row holds its complete test, and its
    status stays `registered` until `field`, the artifact field its test reads the result from, holds the result
    (result_exists)."""
    on: str                       # YYYY-MM-DD (UTC): the day the registration PR is prepared; at most its merge day
    field: str                    # the artifact field that will hold the result (p-value path, or the runs list path)
    withdrawn: str | None = None  # reason; allowed only while the result does not exist


@dataclass(frozen=True)
class Row:
    id: str
    family: str
    hypothesis: str
    test: str
    source: str                         # artifact file name under data/
    statistic: tuple[str, str]          # (label, field path)
    p: PValueSource | None              # p-value source, or None when the artifact holds no test
    sided: str                          # "one" or "two": how the stored/computed p-value is defined
    expects: Callable[[float], bool] | None = None   # the direction the hypothesis claims
    beats: str | None = None            # or: the statistic must exceed this field to point the right way
    pre_specified: bool = False
    retracted: str | None = None        # reason, when the evidence was invalid when produced;
                                        # "{label}" inserts that context value
    note: str = ""
    context: dict[str, str] = field(default_factory=dict)  # label -> field path, shown beside the statistic
    names: dict[str, str] = field(default_factory=dict)    # label -> field path of a display name; "{label}"
                                                           # in the hypothesis inserts it
    pending_names: dict[str, str] = field(default_factory=dict)  # label -> the display text a registered row
                                                                 # shows while it waits and its artifact lacks the name
    underpowered_if: tuple[str, str] | None = None  # (power, target power) context labels
    selected_after_search: bool = False             # the tested model was chosen as the best of a field
    entered_on: str | None = None                   # stamped by ledger_rows(): when the row entered the ledger
    registration: Registration | None = None        # stamped by ledger_rows() from REGISTERED
    registered_model: tuple[str, str] | None = None  # set by a generated family: (the registry, REGISTERED_LAB_MODELS
                                                     # or REGISTERED_DISTRIBUTION_MODELS, and the key of the model
                                                     # registered there whose row this is); the rows one registry makes
                                                     # for a model are withdrawn together


VOL = "vol_forecast_validation.json"
LAB = "vol_lab.json"
CARRY = "carry_signal_test.json"
SHOCK = "supply_shock_study.json"
LEAK = "leak_anatomy.json"
LEAKED_PIPELINE = ("look-ahead leak: the walk-forward trained on rows whose 5-session labels matured "
                   "after the decision (see data/leak_anatomy.json)")
ABOVE_50: Callable[[float], bool] = lambda s: s > 50.0            # noqa: E731
POSITIVE: Callable[[float], bool] = lambda s: s > 0.0             # noqa: E731
NEGATIVE: Callable[[float], bool] = lambda s: s < 0.0             # noqa: E731
# The named directions a registered row may declare (by identity), so its declared direction can be hashed.
DIRECTIONS: dict[str, Callable[[float], bool]] = {"positive": POSITIVE, "negative": NEGATIVE, "above_50": ABOVE_50}
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
            claim: dict[str, Any]               # the Row fields that state the claim and its test
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


def _power(tag: str, test: str) -> Power:
    """For the post-2007 sample: the power of its `test` against the pre-2007 effect size, with the
    target power, as context, and the row marked underpowered when it falls short."""
    if tag != "post2007":
        return {}
    label = f"power_vs_pre_2007_{test}"
    return {"context": {label: f"power_post_2007.{label}", "target_power": "power_post_2007.target_power"},
            "underpowered_if": (label, "target_power")}


def _carry_rows() -> list[Row]:
    samples = (("full", "full_sample", "1990-2024"), ("pre2007", "pre_2007", "1990-2006"),
               ("post2007", "post_2007", "2007-2024"))
    rows = []
    for tag, block, span in samples:
        spread_power, rank_power = _power(tag, "spread"), _power(tag, "rank_corr")
        rows += [
            Row(id=f"carry_spread_{tag}", family="carry",
                hypothesis=f"Months with carry above its prior-months median earn more than the rest ({span}).",
                test="one-sided Welch t-test of the monthly return spread", source=CARRY,
                statistic=("above-median-carry return %/month", f"{block}.mean_ret_above_median_carry_pct"),
                p=Field(f"{block}.spread_p_value_one_sided"), sided="one",
                pre_specified=(tag == "full"),
                note=("Rule fixed a priori (carry_signal_test.py docstring); the 2007 split was chosen later."
                      if tag == "full" else ""),
                context={"below_median_pct": f"{block}.mean_ret_below_median_carry_pct", "n_months": f"{block}.n_months",
                         **spread_power.get("context", {})},
                underpowered_if=spread_power.get("underpowered_if")),
            Row(id=f"carry_rank_{tag}", family="carry",
                hypothesis=f"Carry rank-correlates positively with the next cycle's return ({span}).",
                test="Spearman rank correlation, two-sided", source=CARRY,
                statistic=("rank correlation", f"{block}.carry_rank_corr"),
                p=Field(f"{block}.carry_rank_corr_p_value"), sided="two", expects=POSITIVE,
                context=rank_power.get("context", {}), underpowered_if=rank_power.get("underpowered_if")),
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
    null = f"{vrp}.zero_premium_null"

    def sharpe_row(name: str, hypothesis: str, note: str = "", extra: dict[str, str] | None = None) -> Row:
        test = f"{vrp}.sharpe_positive_test.{name}"
        return Row(id=f"vrp_{name}_sharpe", family="variance risk premium", hypothesis=hypothesis,
                   test=("one-sided test of Sharpe > 0 with the Mertens (2002) / Opdyke (2007) standard error, "
                         "which allows for the P&L's skewness and kurtosis (p stored in the artifact)"),
                   source=VOL, statistic=("Sharpe (annualized)", f"{vrp}.{name}_sharpe"),
                   p=Field(f"{test}.p_value_one_sided"), sided="one", expects=POSITIVE, note=note,
                   context={"skewness": f"{test}.skewness", "excess_kurtosis": f"{test}.excess_kurtosis",
                            "n_periods": f"{vrp}.n_periods", **(extra or {})})

    return [
        Row(id="vrp_exists", family="variance risk premium",
            hypothesis="OVX exceeds the realized vol of the following month more often than not.",
            test="one-sided exact sign test over non-overlapping ~20-session periods", source=VOL,
            statistic=("share of periods with a positive premium %", f"{vrp}.share_positive_pct"),
            p=SignTest(f"{vrp}.share_positive_pct", f"{vrp}.n_periods"), sided="one", expects=ABOVE_50,
            retracted=("invalid null: read as evidence of a variance risk premium, but with zero premium a "
                       "20-session realized vol, noisy and right-skewed, still falls below implied vol in about "
                       "{zero_premium_share_pct}% of periods (economic.variance_risk_premium.zero_premium_null), "
                       "not 50%; vrp_exists_vs_zero_premium tests the share against that null"),
            context={"mean_premium_vol_pts": f"{vrp}.mean_premium_vol_pts",
                     "zero_premium_share_pct": f"{null}.share_positive_pct"}),
        Row(id="vrp_exists_vs_zero_premium", family="variance risk premium",
            hypothesis=("OVX exceeds the realized vol of the following month in more periods than it would with "
                        "no variance risk premium."),
            test=("one-sided stationary block bootstrap of the share of periods with OVX above realized vol against "
                  "the share expected with zero premium (the per-period ratios RV^2 / IV^2 rescaled to mean 1, the "
                  "null share re-estimated in every draw; p stored in the artifact)"), source=VOL,
            statistic=("share of periods with a positive premium %", f"{vrp}.share_positive_pct"),
            p=Field(f"{null}.p_value_one_sided"), sided="one", beats=f"{null}.share_positive_pct",
            context={"zero_premium_share_pct": f"{null}.share_positive_pct", "n_periods": f"{vrp}.n_periods",
                     "mean_premium_vol_pts": f"{vrp}.mean_premium_vol_pts",
                     "zero_premium_mean_premium_vol_pts": f"{null}.mean_premium_vol_pts"}),
        sharpe_row("short_vol_swap", "A linear short-vol (vol-swap) proxy earns a positive Sharpe ratio.",
                   note=("OVX is a variance-swap-style rate: with zero variance premium E[IV - RV] > 0 (Jensen's "
                         "inequality), so this proxy's mean is positive even with no premium "
                         "(zero_premium_mean_premium_vol_pts) and a Sharpe test against 0 overstates the evidence "
                         "for a premium."),
                   extra={"mean_premium_vol_pts": f"{vrp}.mean_premium_vol_pts",
                          "zero_premium_mean_premium_vol_pts": f"{null}.mean_premium_vol_pts"}),
        sharpe_row("short_variance",
                   "A short variance-swap proxy (what an options seller holds) earns a positive Sharpe ratio."),
        Row(id="vrp_timing", family="variance risk premium",
            hypothesis="Selling variance only when OVX is rich vs the model's forecast earns more.",
            test="one-sided Welch t-test, rich vs cheap periods", source=VOL,
            statistic=("rich-period mean P&L", f"{vrp}.timed_rich_mean_pnl"), p=Field(f"{vrp}.timing_p_value"),
            sided="one", beats=f"{vrp}.timed_cheap_mean_pnl", context={"cheap_mean_pnl": f"{vrp}.timed_cheap_mean_pnl"}),
    ]


def _vol_rows() -> list[Row]:
    def lab_dm(rid: str, model: str, metric: str, hypothesis: str, expects: Callable[[float], bool], note: str = "",
               retracted: str | None = None) -> Row:
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
               "HAR-IV beats the OVX-only regression on QLIKE.", NEGATIVE),
        lab_dm("vol_vs_raw_ovx", "raw_ovx", "qlike",
               "HAR-IV beats raw OVX used directly as the forecast on QLIKE.", NEGATIVE),
        lab_dm("vol_same_day_ovx", "har_iv_sameday", "qlike",
               "Same-day OVX (the v1 timing, which settles after CL and overlaps the target) lowers QLIKE vs "
               "OVX lagged one session.", POSITIVE,
               note="An information advantage the deployed model cannot use: the reason v2 lags OVX."),
        replace(lab_dm("vol_challenger_qlike", "har_iv_range", "qlike",
                       "The challenger HAR-IV-range beats the model of record on QLIKE.", POSITIVE,
                       note=("Selected after a search: the challenger is the lowest-QLIKE model of the field on this "
                             "same sample (vol_lab.choose_challenger), so this pairwise p-value ignores the search. "
                             "The row is left out of the multiplicity family (no q-value, Holm p-value or t > 3 "
                             "flag); the search-adjusted test of the same question is vol_spa_beats_record.")),
                selected_after_search=True),
        lab_dm("vol_challenger_mse", "har_iv_range", "mse",
               "The challenger HAR-IV-range beats the model of record on MSE of variance.", POSITIVE),
        Row(id="vol_spa_beats_record", family="volatility forecast",
            hypothesis="Some benchmark model beats the model of record on QLIKE (Hansen SPA, data-snooping robust).",
            test="Hansen (2005) SPA, studentized, consistent p-value, stationary bootstrap", source=LAB,
            statistic=("SPA consistent p-value", "leaderboard.spa_qlike_vs_har_iv.p_consistent"),
            p=Field("leaderboard.spa_qlike_vs_har_iv.p_consistent"), sided="one",
            note=("Studentized, as Hansen (2005) defines it and as the original call asked (studentize=True); "
                  "arch 8.0.0 ignored the flag, and releases up to 2.1.0 published the non-studentized p-value "
                  "(non_studentized_p; CHANGELOG errata)."),
            context={"p_lower": "leaderboard.spa_qlike_vs_har_iv.p_lower",
                     "non_studentized_p": "leaderboard.spa_qlike_vs_har_iv.non_studentized.p_consistent"}),
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
            hypothesis=("HAR-IV's variance forecast is biased (its Mincer-Zarnowitz intercept differs from 0 or its "
                        "slope from 1)."),
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
    """Groups are named in the hypotheses by the study's own display names (groups[].name); the row
    ids keep the study's group keys."""
    rows = []
    for key in ("supply_lost", "threat_only", "opec_cut", "weather", "conflict", "sanctions", "iran_driven",
                "strait_risk"):
        g = f"groups[key={key}]"
        for metric, word in (("peak", "peak"), ("settle", "settled move")):
            rows.append(Row(
                id=f"shock_{key}_{metric}", family="supply shock",
                hypothesis="Events in group '{group}' move WTI differently from a random non-event date (" + word + ").",
                names={"group": f"{g}.name"},
                test=(f"two-sided resampling tests of the group's median {metric} vs n random placebo dates and vs "
                      "placebo dates from each event's own year (supply_shock_study.json method); p is the larger "
                      "of the two, as the study's verdict needs both below 5% (year matching controls for the "
                      "volatility regime, which inflates a 20-session maximum)"),
                source=SHOCK, statistic=(f"median {metric} %", f"{g}.median_{metric}_pct"),
                p=LargestOf((f"{g}.p_value_vs_placebo.{metric}", f"{g}.p_value_vs_year_matched_placebo.{metric}")),
                sided="two", context={"n": f"{g}.n", "placebo_median_pct": f"placebo.median_{metric}_pct"}))
    # between_groups compares these two groups (between_groups.groups), first minus second.
    pair = {"removal": "groups[key=supply_lost].name", "threat": "groups[key=threat_only].name"}
    rows += [
        Row(id="shock_removal_vs_threat_peak", family="supply shock",
            hypothesis="Events in group '{removal}' peak higher than events in group '{threat}'.",
            test="two-sided permutation test of the difference in medians", source=SHOCK,
            statistic=("median peak difference %", "between_groups.median_peak_difference_pct"),
            p=Field("between_groups.p_value.peak"), sided="two", expects=POSITIVE, names=pair),
        Row(id="shock_removal_vs_threat_settle", family="supply shock",
            hypothesis="Events in group '{removal}' keep more of their move than events in group '{threat}' (settle).",
            test="two-sided permutation test of the difference in medians", source=SHOCK,
            statistic=("median settle difference %", "between_groups.median_settle_difference_pct"),
            p=Field("between_groups.p_value.settle"), sided="two", expects=POSITIVE, names=pair),
        Row(id="shock_priced_in", family="supply shock",
            hypothesis="A strong first-day move (>= 3%) changes the further rise that follows.",
            test="two-sided Mann-Whitney test, strong vs weak day-0 events", source=SHOCK,
            statistic=("strong day-0 median further rise %", "priced_in.strong_day0_median_further_rise_pct"),
            p=Field("priced_in.strong_vs_weak_mann_whitney_p"), sided="two",
            context={"weak_day0_median_further_rise_pct": "priced_in.weak_day0_median_further_rise_pct",
                     "strong_day0_n": "priced_in.strong_day0_n", "weak_day0_n": "priced_in.weak_day0_n"}),
    ]
    return rows


def _band_rows() -> list[Row]:
    """The 80% band's coverage across regimes at the decision (ROADMAP 2.2): one HAC Wald test of equal miss rates
    per banded model and regime split. Non-directional (two-sided), so no `expects`; the per-regime HAC z and
    per-grid binomials stay in the artifact as context, as the per-grid DM statistics do."""
    splits = (
        ("ovx_tercile", ("low", "mid", "high"),
         "misses at different rates across thirds of OVX at the decision (cut points from earlier sessions only)."),
        ("ovx_rv_gap", ("at_or_below", "above"),
         "misses at different rates across days with OVX/RV5 above and at or below its median over the previous "
         "{window} sessions."),
        ("rv5_tercile", ("low", "mid", "high"),
         "misses at different rates across thirds of RV5 at the decision (cut points from earlier sessions only)."),
        ("target_spans_roll", ("no", "yes"),
         "misses at different rates between target windows that contain a contract roll and those that do not."),
    )
    rows = []
    for model, subject in (("har_iv", "The model of record's 80% band"),
                           ("har_iv_range", "The challenger HAR-IV-range's 80% band")):
        for var, levels, claim in splits:
            cc = f"conformal.{model}.conditional_coverage.{var}"
            rows.append(Row(
                id=f"band_regime_{var}_{model}", family="forecast interval", source=LAB,
                hypothesis=f"{subject} {claim}",
                test=("HAC Wald test of equal miss rates across regimes: the miss indicator on a constant and regime "
                      "dummies, Newey-West (Bartlett) with the lags in context; chi-squared with K-1 df; two-sided"),
                statistic=("Wald chi2", f"{cc}.wald"), p=Field(f"{cc}.p_value"), sided="two",
                context={"df": f"{cc}.df", "hac_lags": f"{cc}.hac_lags", "n_days": f"conformal.{model}.n_days",
                         **{f"coverage_{level}_pct": f"conformal.{model}.coverage_by_regime_pct.{var}.{level}"
                            for level in levels},
                         "p_hac_lags_20": f"{cc}.robustness.hac_lags_20.p_value",
                         "p_hac_lags_40": f"{cc}.robustness.hac_lags_40.p_value",
                         "p_all_cells": f"{cc}.robustness.all_cells.p_value"},
                names={"window": "regimes.variables.ovx_rv_gap.window_sessions"} if var == "ovx_rv_gap" else {},
                note=("Regime definitions and the test's form were fixed after prototype runs on this sample "
                      "(docs/METHODOLOGY.md section 9). A dependence is reported, never used to change the band.")))
    return rows


# The lab's models other than the model of record, in its order (backend/vol_lab.py MODELS; the ledger never
# imports the lab, and a test checks the two agree).
LAB_COMPARED = ("persistence", "raw_ovx", "ewma", "garch", "gjr", "har_rv", "har_rs", "har_range", "ovx_only",
                "har_iv_sameday", "har_iv_range", "lgbm", "combo")
# Models whose conditional and stability rows are registered before the lab produces them (ROADMAP 2.12): model key ->
# the display label the rows show while they wait. Each row (vol_gw_<key>, vol_fluct_<key>) is in REGISTERED like any
# other. In the result PR the model joins vol_lab's MODELS and moves from here to LAB_COMPARED, which leaves every
# row's definition, and so its spec hash, unchanged. Both rows registered here for a model are withdrawn together (they
# read the one leaderboard entry the lab writes for it), and the model then stays here for good: its key is retired
# here, and the lab never adds the model to its leaderboard. Its distribution rows (REGISTERED_DISTRIBUTION_MODELS)
# read another entry and are withdrawn, or not, on their own: kept, they resolve when the lab scores the model as a
# distribution only, as it scores the quantile HAR. A result PR also adds a new lab model to the other lists
# docs/HANDOFF.md names (scripts/readme_blocks.py MODEL_INPUTS among them).
REGISTERED_LAB_MODELS: dict[str, str] = {}
CHALLENGER = "har_iv_range"
_APPROXIMATE = ("The theory assumes forecasts re-estimated on rolling windows of bounded size, which is also what lets it "
                "compare nested models; the lab's windows expand and HAR-RV and the OVX-only regression are nested in "
                "HAR-IV, so the p-value is approximate.")


def _conditional_rows() -> list[Row]:
    """Conditional predictive ability and stability over time (ROADMAP 2.3): for every model against the model of
    record, the Giacomini-White conditional part and the Giacomini-Rossi fluctuation test adjusted for the window
    lengths. Two-sided; no `expects`. The challenger's two rows are selected after a search (it was picked on this
    sample) and stay out of the family. The chi2(4) Giacomini-White form, which includes the full-sample mean the DM
    rows already test, and every robustness variant stay in the artifact as context."""
    rows = []
    for m in (*LAB_COMPARED, *REGISTERED_LAB_MODELS):
        pending = {"model": REGISTERED_LAB_MODELS[m]} if m in REGISTERED_LAB_MODELS else {}
        model = ("REGISTERED_LAB_MODELS", m) if m in REGISTERED_LAB_MODELS else None
        gw = f"{_LM.format(m)}.gw_qlike_vs_har_iv"
        fl = f"{_LM.format(m)}.fluctuation_qlike_vs_har_iv"
        dm_p = f"{_LM.format(m)}.dm_qlike_vs_har_iv.p_value"
        picked = m == CHALLENGER
        rows.append(Row(
            id=f"vol_gw_{m}", family="conditional predictive ability", source=LAB,
            hypothesis=("The QLIKE difference between the model of record and {model} moves with what is known at the "
                        "decision: its last matured value, OVX and the OVX/RV5 gap."),
            test=("Giacomini-White (2006), conditional part: the QLIKE difference on its value 5 sessions earlier, ln "
                  "OVX of the session before and ln(OVX/RV5), around their means; uncentred HAC, truncated kernel at "
                  "4 lags (the 5-session overlap); chi-squared with 3 df; two-sided. " + _APPROXIMATE),
            statistic=("Wald chi2", f"{gw}.conditional.stat"), p=Field(f"{gw}.conditional.p_value", optional=True),
            sided="two",
            context={"df": f"{gw}.conditional.df", "p_with_constant": f"{gw}.p_value", "dm_p": dm_p,
                     "p_bartlett_10": f"{gw}.robustness.bartlett_10.conditional_p_value",
                     "p_bartlett_40": f"{gw}.robustness.bartlett_40.conditional_p_value", "n_days": f"{gw}.n_days"},
            names={"model": f"{_LM.format(m)}.label"}, pending_names=pending, registered_model=model,
            note=(("Selected after a search: the challenger is the lowest-QLIKE model of the field on this same "
                   "sample. This statistic is unchanged by a constant shift of the difference, but its moments move "
                   "with the mean the challenger was picked on whenever the difference's variance moves with the "
                   "instruments, so its p-value ignores the search. The row is left out of the multiplicity family; "
                   "no search-adjusted version of this test exists.") if picked else
                  ("The instruments, the kernel and its lags were fixed after prototype runs on this sample "
                   "(docs/METHODOLOGY.md section 7). The test with the constant (context p_with_constant) mostly "
                   "repeats the full-sample DM test, so the ledger enters the conditional part.")),
            selected_after_search=picked))
        rows.append(Row(
            id=f"vol_fluct_{m}", family="stability over time", source=LAB,
            hypothesis=("Given what was known at each decision, the model of record and {model} were not equally "
                        "accurate in expectation at every date: in some window of the out-of-sample days their "
                        "expected QLIKE difference is not zero."),
            test=("Giacomini-Rossi (2010) fluctuation test as a rolling Diebold-Mariano t over windows of 0.1, 0.2, "
                  "0.3, 0.4 and 0.5 of the out-of-sample days, each standardized by its own long-run variance "
                  "(truncated kernel, 4 lags: under the null the only autocorrelation is the 5-session overlap); sup "
                  "|t| for each window length against the test's limit, simulated with a fixed seed at this sample's "
                  "length; the p-value is the smallest of the five, adjusted for the choice on the same simulation; "
                  "two-sided. " + _APPROXIMATE),
            statistic=("smallest p over window lengths", f"{fl}.search_adjusted.min_p"),
            p=Field(f"{fl}.search_adjusted.p_value", optional=True), sided="two",
            context={"sign": f"{fl}.sign", "window_end": f"{fl}.at", "window_start": f"{fl}.window_start",
                     "sup": f"{fl}.sup", "p_mu_0_2": f"{fl}.p_value",
                     "critical_5pct": "stability.fluctuation.critical_values.five_pct",
                     **{f"p_mu_0_{k}": f"{fl}.by_mu.mu_0_{k}.p_value" for k in (1, 3, 4, 5)},
                     "p_bartlett_40": f"{fl}.robustness.bartlett_40.p_value",
                     "p_full_sample_variance": f"{fl}.robustness.full_sample_variance.p_value",
                     "grids_outside_5pct": f"{fl}.robustness.by_grid.outside_5pct", "dm_p": dm_p},
            names={"model": f"{_LM.format(m)}.label"}, pending_names=pending, registered_model=model,
            note=(("Selected after a search: the challenger is the lowest-QLIKE model of the field on this same "
                   "sample, and this test's null includes that mean, so its p-value ignores the search. The row is "
                   "left out of the multiplicity family. No search-adjusted fluctuation test exists; "
                   "vol_spa_beats_record adjusts the full-sample comparison for the search.") if picked else
                  ("The window lengths (mu = 0.1-0.5), the per-window variance and the simulated critical values were "
                   "fixed after prototype runs on this sample; the p-value adjusts for the choice among the five "
                   "window lengths, and the seed was fixed before the production build. The null includes equal "
                   "average loss, so where the full-sample DM test rejects (context dm_p) the row mostly repeats it. "
                   "Each window is scaled by its own variability, so the window of the largest |t| is where the "
                   "difference was most consistent, not where it was largest. The mu = 0.1 p in context is "
                   "anti-conservative under the null (docs/METHODOLOGY.md section 7).")),
            selected_after_search=picked))
    return rows


_LD = "distribution.models[key={}]"
# The models whose whole predictive distribution the lab scores (ROADMAP 2.4), in the artifact's order. The quantile
# HAR is a robustness competitor outside LAB_COMPARED: it is never ranked, banded or selected.
DISTRIBUTION_MODELS = ("har_iv_range", "ovx_only", "garch", "gjr", "ewma", "quantile_har")
# Models whose distribution rows are registered before the lab scores them (ROADMAP 2.12), as REGISTERED_LAB_MODELS: key
# -> the label shown while the rows wait; in the result PR the model moves from here to DISTRIBUTION_MODELS. The four
# rows registered here for a model read the one distribution entry the lab writes for it, so they are withdrawn
# together, which retires the key here: the lab never scores the model's distribution under that key (whatever its lab
# rows do), and a later attempt registers a new key. The rows read the leaderboard only for context, and only for a
# model in LAB_COMPARED, so they resolve for a model with no leaderboard entry (lab rows withdrawn, or a new key).
REGISTERED_DISTRIBUTION_MODELS: dict[str, str] = {}
_SCORES = (("crps", "crps_grid", "CRPS over 19 quantiles"), ("pinball10", "pinball_10", "the 10% pinball loss"),
           ("pinball90", "pinball_90", "the 90% pinball loss"))
_DIST_FORKS = ("Chosen after the scores were seen, and published beside the ledger's choices: the vol-point scale "
               "(the log scale is context), equal weights on the 19 quantiles, the batch-means variance and its 12 "
               "periods (8 and 16, dm_test's 9 lags, Newey-West (1994) and each sampling grid on its own are context), "
               "and entering all three scores (docs/METHODOLOGY.md section 7).")


def _distribution_rows() -> list[Row]:
    """The whole predictive distribution (ROADMAP 2.4). Family "predictive distribution": each model's grid CRPS and
    its 10% and 90% pinball losses against the model of record's, by a batch-means Diebold-Mariano test (the
    differences stay correlated for weeks, beyond what 9 Bartlett lags see); two-sided, a positive statistic means
    the named model scores lower. Family "distribution calibration": Berkowitz's LR3 on each sampling grid, Bonferroni
    over the five grids, against the reference that fits each PIT construction; two-sided, no `expects`. The
    challenger's three score rows are selected after a search and stay out of the family; the log-scale CRPS, the
    CRPS Model Confidence Set and the quantile HAR's field rerun stay in the artifact as context."""
    rows = []
    for m in (*DISTRIBUTION_MODELS, *REGISTERED_DISTRIBUTION_MODELS):
        base = _LD.format(m)
        picked = m == CHALLENGER
        pending = {"model": REGISTERED_DISTRIBUTION_MODELS[m]} if m in REGISTERED_DISTRIBUTION_MODELS else {}
        model = ("REGISTERED_DISTRIBUTION_MODELS", m) if m in REGISTERED_DISTRIBUTION_MODELS else None
        for tag, score, phrase in _SCORES:
            dm = f"{base}.dm_vs_har_iv.{score}"
            if picked:
                hypothesis = "The challenger {model}'s predictive distribution beats the model of record's on " + phrase + "."
            elif m == "quantile_har":
                hypothesis = f"A quantile HAR-IV regression's predictive distribution beats the model of record's on {phrase}."
            else:
                hypothesis = "The model of record's predictive distribution beats {model}'s on " + phrase + "."
            context = {"score_model": f"{base}.{score}_pct", "score_har_iv": f"{_LD.format('har_iv')}.{score}_pct",
                       "periods_model_lower": f"{dm}.periods_model_lower", "p_groups_8": f"{dm}.groups_8.p_value",
                       "p_groups_16": f"{dm}.groups_16.p_value", "p_dm_test_9": f"{dm}.dm_test.p_value",
                       "p_nw94": f"{dm}.nw94.p_value", "p_grid_bonferroni": f"{dm}.grid_bonferroni_p",
                       "n_days": "distribution.n_days", "method": f"{base}.method"}
            if score == "crps_grid":
                context["p_log_scale"] = f"{base}.dm_vs_har_iv.crps_grid_log.p_value"
                context["share_2020_pct"] = f"{dm}.concentration.share_2020_pct"
            if m == "quantile_har":
                field = "distribution.quantile_har_field"
                if score == "crps_grid":
                    context.update({"qlike_batch_means_p": f"{field}.dm_qlike_vs_har_iv.batch_means.p_value",
                                    "qlike_mcs_p": f"{field}.mcs.p_values.quantile_har",
                                    "spa_p_with_it": f"{field}.spa.p_consistent"})
            elif m in LAB_COMPARED:
                # Context from the leaderboard only for a model the lab compares there: a model scored only as a
                # distribution (one whose lab rows were withdrawn, or a distribution registered again under a new
                # key) has no leaderboard entry, and its rows read nothing but its distribution entry.
                context["dm_qlike_p"] = f"{_LM.format(m)}.dm_qlike_vs_har_iv.p_value"
            if picked:
                note = ("Selected after a search: the challenger is the lowest-QLIKE model of the field on this same "
                        "sample, so this pairwise p-value ignores the search. The row is left out of the multiplicity "
                        "family; no search-adjusted version of this test exists (the SPA test searches the field on "
                        "QLIKE). " + _DIST_FORKS)
            elif m == "quantile_har":
                note = ("A robustness competitor outside the benchmark field: never ranked, banded, selected or "
                        "eligible for the live pair. Its design (HAR-IV's terms), its quarterly refits, the exact "
                        "linear-program solver and its point forecast were chosen after prototype runs on this sample. "
                        + _DIST_FORKS)
            else:
                note = _DIST_FORKS
            rows.append(Row(
                id=f"vol_{tag}_{m}", family="predictive distribution", source=LAB, hypothesis=hypothesis,
                test=("Batch-means Diebold-Mariano (Ibragimov and Mueller 2010) on the daily score difference: its "
                      "mean in 12 consecutive periods of about 17 months, t-test with 11 df, two-sided; a positive "
                      "statistic means the named model scores lower than HAR-IV. Scores in vol points on the "
                      "conformal band's days."),
                statistic=("batch-means t", f"{dm}.stat"), p=Field(f"{dm}.p_value"), sided="two",
                expects=POSITIVE if picked or m == "quantile_har" else NEGATIVE,
                context=context, names={} if m == "quantile_har" else {"model": f"{base}.label"},
                pending_names=pending, registered_model=model, note=note, selected_after_search=picked))
    for m in ("har_iv", *DISTRIBUTION_MODELS, *REGISTERED_DISTRIBUTION_MODELS):
        base = _LD.format(m)
        pending = {"model": REGISTERED_DISTRIBUTION_MODELS[m]} if m in REGISTERED_DISTRIBUTION_MODELS else {}
        model = ("REGISTERED_DISTRIBUTION_MODELS", m) if m in REGISTERED_DISTRIBUTION_MODELS else None
        berk = f"{base}.berkowitz"
        context = {f"p_grid_{g}": f"{berk}.by_grid[{g}].p_value" for g in range(5)}
        context.update({"grids_below_5pct": f"{berk}.grids_below_5pct", "p_cauchy": f"{berk}.p_cauchy",
                        "reference": f"{berk}.reference", "mu_grid_0": f"{berk}.by_grid[0].mu",
                        "sigma_grid_0": f"{berk}.by_grid[0].sigma", "pit_construction": f"{base}.pit_construction"})
        if m in ("har_iv", "har_iv_range", "ovx_only"):
            context.update({"rho_grid_0": f"{berk}.by_grid[0].rho", "p_chi2": f"{berk}.p_chi2", "mc_se": f"{berk}.mc_se",
                            "window_exceedance_days": f"{berk}.window_exceedances.days",
                            "exceedance_null_mean": f"{berk}.window_exceedances.null_mean",
                            "exceedance_null_q95": f"{berk}.window_exceedances.null_q95"})
            hypothesis = ("{model}'s log forecast errors are not distributed like its own matured past errors: on "
                          "non-overlapping weeks its trailing-rank PITs depart from what i.i.d. errors would give under "
                          "this construction (a simulated null).")
            test = ("Berkowitz (2001) LR3 on the normal scores of each sampling grid's weeks (non-overlapping), "
                    "against a seeded simulated null of this PIT construction (trailing ranks under the 5-session "
                    "overlap; chi-squared with 3 df is far too conservative for it); Bonferroni over the five grids; "
                    "two-sided.")
            note = ("The PIT is the outcome's mid-rank among the trailing residuals, which re-centres itself across "
                    "overlapping weeks and caps |z| near 3.2, so its LR is referred to a simulated null; the null was "
                    "adopted after the chi-squared p-values (context p_chi2) were seen. The p-value counts simulated "
                    "draws (Monte Carlo standard error in context mc_se): near a threshold a few draws can move it "
                    "across, and the ledger reading names any row whose t > 3 flag would move.")
        elif m == "quantile_har":
            context.update({"p_randomized_tail": f"{berk}.randomized_tail.p_value",
                            "randomized_tail_seed_min": f"{berk}.randomized_tail.seed_min",
                            "randomized_tail_seed_max": f"{berk}.randomized_tail.seed_max"})
            hypothesis = ("A quantile HAR-IV regression's predictive distribution is miscalibrated between and beyond "
                          "its 5% and 95% quantiles (a doubly censored normal test; it does not test independence).")
            test = ("Doubly censored normal likelihood ratio adapted from Berkowitz's (2001) censored likelihood: the "
                    "normal score is observed between the 5% and 95% quantiles and only counted beyond them; i.i.d. "
                    "N(mu, sigma^2) against N(0, 1), chi-squared with 2 df, on each sampling grid; Bonferroni over the "
                    "five grids; two-sided.")
            note = ("Its quantiles say nothing beyond the 5% and 95% levels, so the tails are censored. A seeded "
                    "randomized-tail PIT was tried first (context, with its spread over 100 seeds); the censored form "
                    "was adopted after it because its p-value does not depend on a seed. Context grids_below_5pct "
                    "says on how many grids the test rejects.")
        else:
            if m in ("garch", "gjr"):
                context.update({"rho_grid_0": f"{berk}.by_grid[0].rho", "p_count_pit": f"{berk}.p_count_pit",
                                "clamp_days": f"{berk}.clamp_days", "extreme_days": f"{berk}.extreme_days",
                                "p_static_t": f"{base}.alternatives.static_t.berkowitz_p"})
                note = ("The PIT reads the simulated sample's Hazen CDF, continuous in its inputs, chosen for "
                        "reproduction stability after the count PIT's p (context p_count_pit) was seen; where the two "
                        "forms' p-values sit on either side of the t > 3 hurdle, the flag depends on that choice, and the "
                        "ledger reading names such rows. The literal reading, a Student-t on the expected variance path, "
                        "is context (p_static_t).")
            else:
                context["rho_grid_0"] = f"{berk}.by_grid[0].rho"
                note = "The normal score of a far-tail week comes from the survival function, so it stays finite."
            hypothesis = ("{model}'s predictive distribution is miscalibrated: on non-overlapping weeks its PITs are "
                          "not independent standard uniforms.")
            test = ("Berkowitz (2001) LR3 on the normal scores of each sampling grid's weeks (non-overlapping), "
                    "chi-squared with 3 df; Bonferroni over the five grids; two-sided.")
        rows.append(Row(
            id=f"vol_berkowitz_{m}", family="distribution calibration", source=LAB, hypothesis=hypothesis, test=test,
            statistic=("grids below 5%", f"{berk}.grids_below_5pct"), p=BonferroniOverRuns(f"{berk}.by_grid", "p_value"),
            sided="two", context=context, names={} if m == "quantile_har" else {"model": f"{base}.label"},
            pending_names=pending, registered_model=model,
            note=note + " The reference per PIT construction and the Bonferroni combination were fixed after "
                        "prototype runs on this sample (docs/METHODOLOGY.md section 7)."))
    return rows


# The functions that define the rows, in the artifact's row order.
_ROW_FUNCTIONS: tuple[Callable[[], list[Row]], ...] = (
    _direction_rows, _leak_rows, _carry_rows, _vrp_rows, _vol_rows, _var_rows, _band_rows, _conditional_rows,
    _distribution_rows, _shock_rows)

# When each group of rows entered the ledger: the UTC date of the squash merge on main that first held it, and its PR.
# Provenance from the git history, not a statistic; tests/test_research_ledger.py checks it against that history.
# Every row these functions define after registration began must be in REGISTERED instead.
LEGACY_ENTRY = {"_direction_rows": ("2026-10-21", "#54"), "_leak_rows": ("2026-10-21", "#54"),
                "_carry_rows": ("2026-10-21", "#54"), "_vrp_rows": ("2026-10-21", "#54"),
                "_vol_rows": ("2026-10-21", "#54"), "_var_rows": ("2026-10-21", "#54"),
                "_band_rows": ("2026-11-01", "#99"), "_conditional_rows": ("2026-11-02", "#101"),
                "_distribution_rows": ("2026-11-02", "#104"), "_shock_rows": ("2026-10-21", "#54")}

# Rows registered before their results exist, in registration order (ROADMAP 2.12). A new registration is appended,
# dated no earlier than the entry before it (the same day is allowed). Each one's place in the online order is fixed
# here: its date, then its place in this table, so a later registration never moves an earlier one, wherever its row
# is defined. The row is defined with its complete test wherever its family's rows are defined (a generated family's
# model through REGISTERED_LAB_MODELS or REGISTERED_DISTRIBUTION_MODELS, with every row generated for it here). A
# withdrawal sets `withdrawn` and keeps the entry; the rows one of those registries makes for a model are withdrawn
# together.
REGISTERED: dict[str, Registration] = {}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def latest_legacy_entry() -> str:
    """The latest entry date of a row that entered before registration began: every registration is later."""
    return max(on for on, _ in LEGACY_ENTRY.values())


def _defined_rows() -> list[tuple[str, Row]]:
    """(defining function, row) for every row, in the artifact's order."""
    out = [(fn.__name__, row) for fn in _ROW_FUNCTIONS for row in fn()]
    seen: set[str] = set()
    for _, row in out:
        if row.id in seen:
            raise ValueError(f"{row.id} is defined twice (a model in both LAB_COMPARED and REGISTERED_LAB_MODELS?)")
        seen.add(row.id)
    return out


def legacy_batches() -> dict[str, str]:
    """Row id -> the PR of its entry batch, for the rows that entered before registration began."""
    return {row.id: LEGACY_ENTRY[fn][1] for fn, row in _defined_rows() if row.id not in REGISTERED}


def ledger_rows() -> list[Row]:
    """Every row, stamped with when it entered the ledger: its registration date if it is in REGISTERED, else the
    entry date of the function that defines it. Every row generated for a registered model must be registered, and
    the rows one registry makes for a model are withdrawn together: they read one entry the lab writes whole, so the
    entry that would hold a pending row's result would hold its withdrawn sibling's too. A model registered through
    both registries has two such groups, which read different entries (leaderboard and distribution; a distribution
    row reads the leaderboard only as context, and only for a model in LAB_COMPARED), so each is withdrawn on its
    own."""
    out = []
    models: dict[tuple[str, str], list[str]] = {}
    for fn, row in _defined_rows():
        registration = REGISTERED.get(row.id)
        if row.registered_model is not None:
            if registration is None:
                registry, key = row.registered_model
                raise ValueError(f"{row.id} is generated for the model {key!r} registered in {registry}: register it "
                                 "in REGISTERED")
            models.setdefault(row.registered_model, []).append(row.id)
        if registration is not None:
            out.append(replace(row, registration=registration, entered_on=registration.on))
        elif fn in LEGACY_ENTRY:
            out.append(replace(row, entered_on=LEGACY_ENTRY[fn][0]))
        else:
            raise ValueError(f"{row.id} is defined in {fn}, which has no entry date: register the row in REGISTERED")
    orphans = sorted(set(REGISTERED) - {row.id for row in out})
    if orphans:
        raise ValueError(f"registrations without a row: {orphans}")
    for (registry, key), ids in models.items():
        withdrawn = [rid for rid in ids if REGISTERED[rid].withdrawn]
        if withdrawn and len(withdrawn) != len(ids):
            raise ValueError(f"{registry} model {key!r}: {withdrawn} withdrawn but not "
                             f"{[rid for rid in ids if rid not in withdrawn]}: the rows a registry makes for a model "
                             "read one entry and are withdrawn together, which retires the key there")
    return out


def source_names() -> set[str]:
    """The artifacts the ledger reads: fixed in code, never taken from the command line."""
    return {row.source for row in ledger_rows()}


def load_artifact(name: str) -> Artifact:
    """Read one of the ledger's own source artifacts from data/."""
    if name not in source_names():
        raise ValueError(f"{name} is not a research-ledger source")
    path = DATA_ROOT / name
    if path.parent != DATA_ROOT or path.name != name:
        raise ValueError(f"{name} is not a file in data/")
    return cast("Artifact", json.loads(path.read_text(encoding="utf-8")))


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


# ── Online false-discovery control ───────────────────────────────────────────
@dataclass(frozen=True)
class OnlineRun:
    """One online procedure over a stream, position by position. A pending entry (p None) gets its level; it and
    every later position get None for the decision (and later positions for the level too). `wealth` runs to the
    last decided position."""
    alpha: list[float | None]          # alpha_t per position
    rejected: list[bool | None]
    candidate: list[bool | None]       # SAFFRON: p_t <= lambda; empty for LORD++
    wealth: list[float]                # W(1..d), d = positions decided


def lord_gamma(j: int, c: float = LORD_GAMMA_C) -> float:
    """LORD++'s gamma_j = c log(max(j, 2)) / (j e^sqrt(log j)) (Javanmard and Montanari 2018, eq. 31), j >= 1. It does
    not depend on the stream's length."""
    return c * math.log(max(j, 2)) / (j * math.exp(math.sqrt(math.log(j))))


def saffron_gamma(j: int, c: float = SAFFRON_GAMMA_C, s: float = SAFFRON_GAMMA_S) -> float:
    """SAFFRON's gamma_j = c / j^s, j >= 1 (c = 1 / zeta(1.6)); not normalised to a horizon."""
    return float(c / (j ** s))


def _check_online(p: Sequence[float | None], alpha: float, w0: float, lam: float | None = None) -> None:
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if not 0.0 <= w0 <= alpha:
        raise ValueError(f"w0 must be in [0, alpha], got {w0}")
    if lam is not None and not 0.0 < lam < 1.0:
        raise ValueError(f"lambda must be in (0, 1), got {lam}")
    for t, x in enumerate(p, start=1):
        if x is not None and not 0.0 <= x <= 1.0:
            raise ValueError(f"p-value at position {t} must be None or in [0, 1], got {x}")


def lord_plus_plus(p: Sequence[float | None], alpha: float = ALPHA, w0: float = LORD_W0,
                   gamma_c: float = LORD_GAMMA_C) -> OnlineRun:
    """LORD++ (Ramdas, Yang, Wainwright and Jordan 2017, eq. 5), as onlineFDR's LORD(version = '++'):
    alpha_t = gamma_t W0 + (alpha - W0) gamma_{t - tau_1} + alpha sum_{j >= 2} gamma_{t - tau_j} over the rejection
    times tau_j < t; reject when p_t <= alpha_t. The wealth W(t) = W(t-1) - alpha_t + R_t b_t (b_t = alpha - W0 at the
    first rejection, alpha after; W(0) = W0) is a diagnostic: alpha_t never reads it."""
    _check_online(p, alpha, w0)
    n = len(p)
    g = [0.0] + [lord_gamma(j, gamma_c) for j in range(1, n + 1)]    # 1-based
    taus: list[int] = []
    alphas: list[float | None] = []
    rejected: list[bool | None] = []
    wealth: list[float] = []
    w = w0
    blocked = False
    for t in range(1, n + 1):
        if blocked:
            alphas.append(None)
            rejected.append(None)
            continue
        a = g[t] * w0
        if taus:
            a += (alpha - w0) * g[t - taus[0]]
            a += alpha * sum(g[t - tj] for tj in taus[1:])
        alphas.append(a)
        x = p[t - 1]
        if x is None:                     # pending: its level is published, everything after it waits
            rejected.append(None)
            blocked = True
            continue
        r = x <= a
        b = (alpha - w0) if not taus else alpha
        w = w - a + int(r) * b
        if r:
            taus.append(t)
        rejected.append(r)
        wealth.append(w)
    return OnlineRun(alpha=alphas, rejected=rejected, candidate=[], wealth=wealth)


def saffron(p: Sequence[float | None], alpha: float = ALPHA, w0: float = SAFFRON_W0, lam: float = SAFFRON_LAMBDA,
            gamma_c: float = SAFFRON_GAMMA_C, gamma_s: float = SAFFRON_GAMMA_S) -> OnlineRun:
    """SAFFRON with a constant lambda (Ramdas, Zrnic, Wainwright and Jordan 2018, section 2.3; onlineFDR's SAFFRON):
    with candidates C_i = 1{p_i <= lambda} and C_{j+} the candidates after tau_j and before t,
    alpha_t = min{lambda, (1 - lambda) [W0 gamma_{t - C_{0+}} + (alpha - W0) gamma_{t - tau_1 - C_{1+}}
    + alpha sum_{j >= 2} gamma_{t - tau_j - C_{j+}}]}; reject when p_t <= alpha_t. The wealth W(0) = (1 - lambda) W0,
    W(t) = W(t-1) - (1 - C_t) alpha_t + R_t (1 - lambda) b_t is a diagnostic."""
    _check_online(p, alpha, w0, lam)
    n = len(p)
    g = [0.0] + [saffron_gamma(j, gamma_c, gamma_s) for j in range(1, n + 1)]    # 1-based
    taus: list[int] = []
    after: list[int] = []                 # candidates since each rejection (C_{j+}), counted incrementally
    since_start = 0                       # C_{0+}
    alphas: list[float | None] = []
    rejected: list[bool | None] = []
    candidate: list[bool | None] = []
    wealth: list[float] = []
    w = (1 - lam) * w0
    blocked = False
    for t in range(1, n + 1):
        if blocked:
            alphas.append(None)
            rejected.append(None)
            candidate.append(None)
            continue
        acc = w0 * g[t - since_start]
        if taus:
            acc += (alpha - w0) * g[t - taus[0] - after[0]]
            acc += alpha * sum(g[t - tj - cj] for tj, cj in zip(taus[1:], after[1:]))
        a = min(lam, (1 - lam) * acc)
        alphas.append(a)
        x = p[t - 1]
        if x is None:
            rejected.append(None)
            candidate.append(None)
            blocked = True
            continue
        c = x <= lam
        r = x <= a
        reward = (1 - lam) * ((alpha - w0) if not taus else alpha)
        w = w - (1 - int(c)) * a + int(r) * reward
        if c:
            since_start += 1
            after = [k + 1 for k in after]
        if r:
            taus.append(t)
            after.append(0)
        rejected.append(r)
        candidate.append(c)
        wealth.append(w)
    return OnlineRun(alpha=alphas, rejected=rejected, candidate=candidate, wealth=wealth)


def _directional_one_sided(p: float, sided: str, direction_ok: bool | None) -> float:
    if sided == "one":
        return p
    if direction_ok is False:
        return 1.0 - p / 2.0
    return p / 2.0


def _status(row: Row, p: float | None, direction_ok: bool | None, *, pending: bool = False) -> str:
    if pending:
        return "registered"
    if row.retracted:
        return "retracted"
    if direction_ok is False:
        return "not_supported"
    if p is None:
        return "inconclusive"
    if p < ALPHA:
        return "supported"
    if p < INCONCLUSIVE_BELOW:
        return "inconclusive"
    return "not_supported"


def _artifact_date(doc: Artifact) -> str | None:
    for path in ("generated_at", "timestamp", "generated_from.retrieved_at"):
        try:
            return str(resolve(doc, path))[:10]
        except FieldError:
            continue
    return None


def _num(x: Any) -> Any:
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x)
    return x


def _by_family(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per family: rows by status, and how many supported rows survive BH at 5% and clear t > 3."""
    out: dict[str, dict[str, Any]] = {}
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


def spec_sha256(row: Row) -> str:
    """SHA-256 of everything in a registered row's definition that decides its p-value, direction and status, with its
    hypothesis as written (a template's names by their field paths). Notes, context, the underpowered pair, a
    retraction, a withdrawal and the text shown while pending may change after the result; nothing hashed may."""
    if row.registration is None:
        raise ValueError(f"{row.id} has no registration")
    if row.p is None or not isinstance(row.p, P_SOURCES):
        raise ValueError(f"{row.id}: a registered row declares one of the module's p-value sources")
    direction = None
    if row.expects is not None:
        direction = next((name for name, fn in DIRECTIONS.items() if fn is row.expects), None)
        if direction is None:
            raise ValueError(f"{row.id}: a registered row's direction must be one of DIRECTIONS")
    spec = {"id": row.id, "family": row.family, "hypothesis": row.hypothesis, "names": dict(row.names),
            "test": row.test, "source": row.source,
            "statistic": {"label": row.statistic[0], "path": row.statistic[1]}, "sided": row.sided,
            "p": {"type": type(row.p).__name__, **dataclasses.asdict(row.p)}, "direction": direction,
            "beats": row.beats, "pre_specified": row.pre_specified, "selected_after_search": row.selected_after_search,
            "registration": {"on": row.registration.on, "field": row.registration.field}}
    text = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_registration(row: Row) -> None:
    """The constraints on a registered row that do not need its artifact (ROADMAP 2.12)."""
    reg = row.registration
    if reg is None:
        return
    if not _DATE.match(reg.on) or reg.on <= latest_legacy_entry():
        raise ValueError(f"{row.id}: registered on {reg.on!r}, which must be a YYYY-MM-DD date later than the latest "
                         f"legacy entry ({latest_legacy_entry()})")
    spec_sha256(row)                      # the declared p source and direction can be hashed


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith((prefix + ".", prefix + "["))


def result_exists(row: Row, doc: Artifact | None) -> bool:
    """Whether a registered row's result exists in its artifact `doc` (None: the file does not exist yet): its declared
    field resolves to a value, or to a null its test reads as a result. That is an optional Field reading exactly that
    field, where a null means the artifact ran the test and could not compute it (the row then takes the status of any
    such row: inconclusive, or not_supported when its stored estimate points the wrong way). Any other null is a
    placeholder: no result yet."""
    reg = row.registration
    if reg is None:
        raise ValueError(f"{row.id} has no registration")
    if doc is None:
        return False
    try:
        value = resolve(doc, reg.field)
    except FieldError:
        return False
    return value is not None or (isinstance(row.p, Field) and row.p.optional and row.p.path == reg.field)


def _names_while_pending(row: Row, doc: Artifact | None) -> dict[str, str]:
    """A pending (or withdrawn) registered row's display names: each from its artifact where the name already exists
    there, else the text the row declares for it (pending_names)."""
    extra = sorted(set(row.pending_names) - set(row.names))
    if extra:
        raise ValueError(f"{row.id}: pending_names {extra} name no field in names {sorted(row.names)}")
    out = {}
    for label, path in row.names.items():
        try:
            if doc is None:
                raise FieldError(path)
            out[label] = str(resolve(doc, path))
        except FieldError:
            if label not in row.pending_names:
                raise ValueError(f"{row.id}: a registered row declares the text its name {label!r} shows until the "
                                 f"artifact holds it at {path} (pending_names)") from None
            out[label] = row.pending_names[label]
    return out


ONLINE_FIELDS = ("online_position", "online_p", "online_alpha_lordpp", "online_rejected_lordpp", "online_alpha_saffron",
                 "online_candidate_saffron", "online_rejected_saffron")


def _online_p(r: dict[str, Any]) -> float | None:
    """The p a stream row is tested on: none while a registration is pending; 1 for a withdrawn registration, a
    retracted row or a test the artifact could not compute; else p_two_sided."""
    reg = r["registration"]
    if r["status"] == "registered":
        return 1.0 if reg is not None and reg["withdrawn"] else None
    if r["status"] == "retracted" or r["p_two_sided"] is None:
        return 1.0
    return float(r["p_two_sided"])


def _legacy_rejections(stream: list[dict[str, Any]], batches: dict[str, str]) -> tuple[list[str], list[str]]:
    """The legacy rows' LORD++ and SAFFRON rejections, in stream order (the legacy rows are the stream's prefix)."""
    legacy = [r for r in stream if r["id"] in batches]
    p = [cast(float, r["online_p"]) for r in legacy]
    lord, saff = lord_plus_plus(p), saffron(p)
    return ([r["id"] for r, x in zip(legacy, lord.rejected) if x], [r["id"] for r, x in zip(legacy, saff.rejected) if x])


def _order_sensitivity(stream: list[dict[str, Any]], batches: dict[str, str]) -> dict[str, Any]:
    """How the legacy rows' rejections move when their order is drawn at random within each entry batch (batches in
    their order, a fixed seed). Registered rows sort after every legacy row, so they never affect these counts."""
    legacy = [r for r in stream if r["id"] in batches]
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in legacy:
        groups.setdefault(batches[r["id"]], []).append(r)
    base_lord, base_saff = (set(ids) for ids in _legacy_rejections(stream, batches))
    rng = np.random.default_rng(ORDER_SEED)
    counts: dict[str, list[int]] = {"lordpp": [], "saffron": []}
    same = {"lordpp": 0, "saffron": 0}
    for _ in range(ORDER_DRAWS):
        order = [group[i] for group in groups.values() for i in rng.permutation(len(group))]
        p = [cast(float, r["online_p"]) for r in order]
        for name, run, base in (("lordpp", lord_plus_plus(p), base_lord), ("saffron", saffron(p), base_saff)):
            got = {r["id"] for r, x in zip(order, run.rejected) if x}
            counts[name].append(len(got))
            same[name] += got == base
    return {"draws": ORDER_DRAWS, "seed": ORDER_SEED,
            "scope": "the legacy rows' order drawn at random within each entry batch; registered rows keep their place",
            "legacy_rows": len(legacy),
            **{name: {"min": min(counts[name]), "max": max(counts[name]), "same_set_share": same[name] / ORDER_DRAWS}
               for name in ("lordpp", "saffron")}}


def _stream_key(rows: list[dict[str, Any]]) -> Callable[[dict[str, Any]], tuple[str, bool, int]]:
    """The online order (ONLINE_ORDER): the entry date, then a registered row's place in REGISTERED (registration
    order) or another row's place in `rows` (the code's order). Registration dates are later than every legacy entry,
    so the middle term only keeps the two kinds apart; a registered row outside REGISTERED (a test's synthetic row)
    follows those in it, in its place in `rows`."""
    index = {r["id"]: i for i, r in enumerate(rows)}
    registered = {rid: i for i, rid in enumerate(REGISTERED)}
    return lambda r: (r["entered_on"], r["registration"] is not None,
                      registered.get(r["id"], len(registered) + index[r["id"]]))


def _online(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill each row's online fields (LORD++ and SAFFRON over the stream in entry order) and return summary.online."""
    stream = sorted((r for r in rows if not r["selected_after_search"]), key=_stream_key(rows))
    for r in rows:
        r.update(dict.fromkeys(ONLINE_FIELDS))
    for t, r in enumerate(stream, start=1):
        r["online_position"] = t
        r["online_p"] = _online_p(r)
    p = [r["online_p"] for r in stream]
    lord, saff = lord_plus_plus([*p, None]), saffron([*p, None])     # the extra entry: the next row's level
    for t, r in enumerate(stream):
        r.update({"online_alpha_lordpp": lord.alpha[t], "online_rejected_lordpp": lord.rejected[t],
                  "online_alpha_saffron": saff.alpha[t], "online_candidate_saffron": saff.candidate[t],
                  "online_rejected_saffron": saff.rejected[t]})
    decided = len(lord.wealth)
    in_stream = {r["id"] for r in stream}
    batches = {rid: pr for rid, pr in legacy_batches().items() if rid in in_stream}
    legacy_entry = {pr: on for on, pr in LEGACY_ENTRY.values()}
    order: dict[str, int] = {}
    for r in stream:
        if r["id"] in batches:
            order[batches[r["id"]]] = order.get(batches[r["id"]], 0) + 1
    return {
        "alpha": ALPHA,
        "reference": ONLINE_REFERENCE,
        "order": ONLINE_ORDER,
        "rows_in_stream": len(stream),
        "rows_decided": decided,
        "waiting_on": next((r["id"] for r in stream if r["online_p"] is None), None),
        "legacy_batches": [{"entered_on": legacy_entry[pr], "pr": pr, "rows_in_stream": n} for pr, n in order.items()],
        "lordpp": {"w0": LORD_W0, "gamma_c": LORD_GAMMA_C, "gamma": "c log(max(j,2)) / (j exp(sqrt(log j)))",
                   "rejected": [r["id"] for r, x in zip(stream, lord.rejected) if x],
                   "wealth": lord.wealth, "next_level": lord.alpha[-1]},
        "saffron": {"w0": SAFFRON_W0, "lambda": SAFFRON_LAMBDA, "gamma_c": SAFFRON_GAMMA_C, "gamma_s": SAFFRON_GAMMA_S,
                    "candidates": sum(bool(x) for x in saff.candidate[:-1]),
                    "rejected": [r["id"] for r, x in zip(stream, saff.rejected) if x],
                    "wealth": saff.wealth, "next_level": saff.alpha[-1]},
        "legacy_order_sensitivity": _order_sensitivity(stream, batches),
    }


def build(load: Callable[[str], Artifact] | None = None, rows: list[Row] | None = None) -> dict[str, Any]:
    """The ledger from the committed artifacts (`load(name)` returns a parsed artifact). `rows` replaces the row
    definitions (tests); by default ledger_rows()."""
    load = load or load_artifact
    defs = ledger_rows() if rows is None else rows
    docs: dict[str, Artifact | None] = {}
    out: list[dict[str, Any]] = []
    for row in defs:
        reg = row.registration
        _check_registration(row)
        entered_on = reg.on if reg is not None else row.entered_on
        if entered_on is None:
            raise ValueError(f"{row.id} has no entry date: register it in REGISTERED")
        if row.source not in docs:
            try:
                docs[row.source] = load(row.source)
            except FileNotFoundError:
                if reg is None:
                    raise
                docs[row.source] = None       # a registered row's artifact may not exist yet
        doc = docs[row.source]
        stat_label, stat_path = row.statistic
        exists = True if reg is None else result_exists(row, doc)
        if reg is not None and reg.withdrawn and exists:
            raise ValueError(f"{row.id}: withdrawn, but its result exists at {reg.field} in {row.source}")
        registration = None if reg is None else {"on": reg.on, "field": reg.field, "withdrawn": reg.withdrawn,
                                                 "spec_sha256": spec_sha256(row)}
        if reg is not None and (reg.withdrawn or not exists):
            shown = _names_while_pending(row, doc)
            out.append({
                "id": row.id, "date": None, "entered_on": entered_on, "family": row.family,
                "hypothesis": row.hypothesis.format(**shown) if shown else row.hypothesis,
                "test": row.test,
                "statistic": {"name": stat_label, "value": None}, "context": {},
                "p_value": None, "sided": row.sided, "p_two_sided": None,
                "pre_specified": row.pre_specified, "selected_after_search": row.selected_after_search,
                "status": _status(row, None, None, pending=True), "underpowered": None, "retraction_reason": None,
                "registration": registration, "note": row.note,
                "artifact": {"path": f"data/{row.source}", "statistic_field": stat_path, "p_value_fields": {},
                             "context_fields": dict(row.context), "name_fields": dict(row.names)},
            })
            continue
        if doc is None:                       # only a registered row's artifact may be missing (it is pending)
            raise FileNotFoundError(f"data/{row.source} (read by {row.id})")
        stat = resolve(doc, stat_path)
        p, p_inputs = (None, {}) if row.p is None else row.p(doc)
        if reg is not None and not (reg.field in p_inputs
                                    or (p_inputs and all(_under(path, reg.field) for path in p_inputs))):
            raise FieldError(f"{row.id}: registered field not read by its test ({reg.field})")
        direction_ok = None
        if p is None and stat is None:
            pass                              # not computed, its statistic null too: no direction (inconclusive)
        elif row.beats is not None:
            direction_ok = float(stat) > float(resolve(doc, row.beats))
        elif row.expects is not None:
            direction_ok = bool(row.expects(float(stat)))
        p_one = None if p is None else _directional_one_sided(p, row.sided, direction_ok)
        context = {k: _num(resolve(doc, v)) for k, v in row.context.items()}
        names = {k: str(resolve(doc, v)) for k, v in row.names.items()}
        underpowered = None
        if row.underpowered_if is not None:
            power, target = row.underpowered_if
            underpowered = bool(context[power] < context[target])
        out.append({
            "id": row.id,
            "date": _artifact_date(doc),
            "entered_on": entered_on,
            "family": row.family,
            "hypothesis": row.hypothesis.format(**names) if names else row.hypothesis,
            "test": row.test,
            "statistic": {"name": stat_label, "value": _num(stat)},
            "context": context,
            "p_value": None if p is None else float(p),
            "sided": row.sided,
            "p_two_sided": None if p_one is None else float(min(1.0, 2.0 * p_one)),
            "pre_specified": row.pre_specified,
            "selected_after_search": row.selected_after_search,
            "status": _status(row, p, direction_ok),
            "underpowered": underpowered,
            "retraction_reason": row.retracted.format(**context) if row.retracted else None,
            "registration": registration,
            "note": row.note,
            "artifact": {"path": f"data/{row.source}", "statistic_field": stat_path,
                         "p_value_fields": {k: _num(v) for k, v in p_inputs.items()},
                         "context_fields": dict(row.context), "name_fields": dict(row.names)},
        })
    rows_out = out

    for r in rows_out:
        p2 = r["p_two_sided"]
        r.update({"q_value_bh": None, "p_holm": None,
                  "t_equivalent": None if p2 is None else round(float(stats.norm.isf(max(p2, 1e-300) / 2.0)), 2),
                  "survives_bh_5pct": None, "survives_holm_5pct": None, "clears_hlz_t3": None})
    # The multiplicity family: every row with a p-value except those selected after a search, whose
    # pairwise p-value ignores the search (their adjusted fields stay null).
    family = [r for r in rows_out if r["p_two_sided"] is not None and not r["selected_after_search"]]
    p2 = np.array([r["p_two_sided"] for r in family])
    q, h = benjamini_hochberg(p2), holm(p2)
    for r, qv, hv in zip(family, q, h):
        r["q_value_bh"] = float(qv)
        r["p_holm"] = float(hv)
        r["survives_bh_5pct"] = bool(qv < ALPHA)
        r["survives_holm_5pct"] = bool(hv < ALPHA)
        r["clears_hlz_t3"] = bool(r["t_equivalent"] > HLZ_T)

    supported = [r for r in rows_out if r["status"] == "supported"]
    in_family = [r for r in supported if not r["selected_after_search"]]
    by_status = {s: sum(r["status"] == s for r in rows_out) for s in STATUSES}
    summary = {
        "rows": len(rows_out),
        "rows_with_p_value": sum(r["p_two_sided"] is not None for r in rows_out),
        "rows_in_multiplicity_family": len(family),
        "by_status": by_status,
        "pre_specified_rows": sum(r["pre_specified"] for r in rows_out),
        "selected_after_search": [r["id"] for r in rows_out if r["selected_after_search"]],
        "underpowered": [r["id"] for r in rows_out if r["underpowered"]],
        "supported": [r["id"] for r in supported],
        "supported_surviving_bh_5pct": [r["id"] for r in in_family if r["survives_bh_5pct"]],
        "supported_surviving_holm_5pct": [r["id"] for r in in_family if r["survives_holm_5pct"]],
        "supported_clearing_hlz_t3": [r["id"] for r in in_family if r["clears_hlz_t3"]],
        "supported_failing_bh_5pct": [r["id"] for r in in_family if not r["survives_bh_5pct"]],
        "supported_selected_after_search": [r["id"] for r in supported if r["selected_after_search"]],
        "by_family": _by_family(rows_out),
        "rows_tested": len(rows_out) - by_status["registered"],
        "registered": [r["id"] for r in rows_out if r["status"] == "registered" and not r["registration"]["withdrawn"]],
        "registered_withdrawn": [r["id"] for r in rows_out
                                 if r["status"] == "registered" and r["registration"]["withdrawn"]],
        "registered_before_result": [r["id"] for r in rows_out if r["registration"] is not None],
    }
    summary["online"] = _online(rows_out)
    return {
        "method": ("One row per tested hypothesis; statistics and p-values read from the named artifact "
                   "fields (or computed from them by the formula in `test`). Status uses the p-value as the "
                   "test was run: supported below alpha 0.05, inconclusive from 0.05 to 0.10, not_supported "
                   "at 0.10 or above or when the estimate points the wrong way (a failure to reject, not "
                   "evidence against the hypothesis; underpowered marks a test whose recorded power is below "
                   "its target). Benjamini-Hochberg q-values and Holm adjusted p-values are computed across "
                   "the rows with a p-value, on p_two_sided (the directional one-sided p doubled, capped at "
                   "1), leaving out rows selected_after_search (a model picked as the best of a field on the "
                   "same sample; their adjusted fields are null and their note names the search-adjusted test "
                   "where one exists or says none does) and rows whose test the artifact could not compute. "
                   "t_equivalent = normal quantile of p_two_sided; clears_hlz_t3 applies the Harvey-Liu-Zhu "
                   "(2016) t > 3 hurdle. Online control: LORD++ and SAFFRON at FDR 0.05 with onlineFDR 2.21.3's "
                   "defaults, over the rows in the order they entered the ledger, on p_two_sided, with retracted "
                   "rows and rows whose test could not be computed taken as p = 1, leaving out rows selected after "
                   "a search, and assuming independent null p-values. Registered rows: declared before their "
                   "result exists, with null p and batch fields until the declared field holds their result."),
        "alpha": ALPHA,
        "hlz_t": HLZ_T,
        "summary": summary,
        "rows": rows_out,
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
                                              ("T", r["clears_hlz_t3"]), ("L", r["online_rejected_lordpp"]),
                                              ("S", r["online_rejected_saffron"])) if on)
        t = "   " if r["online_position"] is None else f"{r['online_position']:3d}"
        print(f"{r['id']:34s} {r['status']:13s} p={p} q={q} t={t} {flags:5s} {'pre' if r['pre_specified'] else ''}")
    s = ledger["summary"]
    o = s["online"]
    in_family = len(s["supported"]) - len(s["supported_selected_after_search"])
    print(f"\n{s['rows']} rows ({s['by_status']}); supported surviving BH: {len(s['supported_surviving_bh_5pct'])}"
          f"/{in_family} in the multiplicity family; clearing t > 3: {len(s['supported_clearing_hlz_t3'])}; "
          f"selected after a search (outside the family): {', '.join(s['selected_after_search']) or 'none'}")
    print(f"online (B, H, T: batch; L: LORD++, S: SAFFRON; t: stream position): {o['rows_decided']} of "
          f"{o['rows_in_stream']} stream rows decided, LORD++ rejects {len(o['lordpp']['rejected'])}, SAFFRON "
          f"{len(o['saffron']['rejected'])}; waiting on {o['waiting_on'] or 'nothing'}; registered pending "
          f"{len(s['registered'])}, withdrawn {len(s['registered_withdrawn'])}")
    print(f"wrote data/{ARTIFACT}")


if __name__ == "__main__":
    main()
