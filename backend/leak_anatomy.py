#!/usr/bin/env python3
"""Leak anatomy: why the retracted direction model's Sharpe ratio was mechanical.

The retracted walk-forward trained on rows up to the decision day t, and each row's label is the
5-session forward return. The most recent training row (t-1) has the label r_t + ... + r_{t+4}; the
test label is r_{t+1} + ... + r_{t+5}. They share 4 of 5 daily returns, so even when returns are
pure noise the two labels are correlated (0.8 for i.i.d. returns), and the newest rows are also the
test row's nearest neighbours in feature space (price levels and moving averages barely move in a
day). A model that leans on near-duplicate rows copies a label that already contains most of the
answer. Purging the last horizon - 1 = 4 rows (backend/backtest_walk_forward.purge_count) removes
every label that matures after the decision, and the "edge" disappears.

This module shows it three ways, all deterministic and offline:

  (a) Analytic. For jointly normal labels with correlation rho, P(same sign) = 1/2 + arcsin(rho)/pi
      (Sheppard's formula). The leaked t-1 label has rho = 4/5: 79.5% sign agreement with the test
      label, with no information about the future at all. Checked by Monte Carlo on Gaussian and
      fat-tailed (Student-t, 3 df) i.i.d. returns.
  (b) Simulation on synthetic i.i.d. random walks (no signal exists by construction): the same
      walk-forward design as the retracted backtest (rolling 378-row window, 200-row minimum,
      a decision every 5 sessions, 5-session labels) with simple scikit-learn classifiers on
      price-level features, run with and without the purge over many seeds.
  (c) The same on block-shuffled real CL=F returns (data/market/cl_f_daily.csv via
      backend.market_data): real fat tails and volatility clustering inside 20-session blocks, any
      genuine predictability across blocks destroyed. The shuffled runs permute the same returns, so
      they are not independent of each other, and real 5-session returns rise more often than they
      fall, so each run is also tested against its own base rate rather than 50% alone. Plus (d)
      the real, unshuffled CL=F returns over the retracted 5-year window, for reference.

Sharpe ratios here are gross (no transaction costs); the retracted model's is after costs.

Usage: python -m backend.leak_anatomy   (writes data/leak_anatomy.json; about two minutes)
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np
from scipy import stats
from sklearn.ensemble import RandomForestClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.tree import DecisionTreeClassifier

try:
    from . import market_data
    from .safe_paths import data_json_path
except ImportError:  # Direct invocation: python backend/leak_anatomy.py
    import market_data
    from safe_paths import data_json_path

ARTIFACT = "leak_anatomy.json"
HORIZON = 5                  # label: 5-session forward log return (the retracted model's 1W target)
PURGE = HORIZON - 1          # rows whose labels mature after the decision (backtest purge_count("1w"))
STEP = 5                     # a decision every 5 sessions, as in the backtest
TRAIN_WINDOW = 378           # the production rolling window
MIN_TRAIN = 200              # rows before the first decision, as in the backtest
N_DAYS = 1300                # about five years: ~210 decisions per run, like the 5-year backtest (198)
DAILY_VOL = 0.02             # synthetic daily return volatility (WTI is about 2%/day)
MA_WINDOWS = (5, 10, 20)     # moving averages of the log price, beside the price itself
SHUFFLE_BLOCK = 20           # sessions per block when block-shuffling real returns
SYNTHETIC_SEEDS = tuple(range(10))
SHUFFLE_SEEDS = tuple(range(10))
FOREST_SEEDS = tuple(range(3))   # the 20-tree forest is the slow model; fewer seeds
MC_DRAWS = 1_000_000
MC_SEED = 20261020
STUDENT_T_DF = 3
PERIODS_PER_YEAR = 252.0 / STEP
REAL_WINDOW = ("2021-06-21", "2026-06-19")   # the retracted 5-year backtest's data window
RETRACTED_ARTIFACT = "wf_5y_unpurged_LEAKED.json"

MODELS = {
    "nearest_neighbour": ("1-nearest-neighbour classifier (scikit-learn KNeighborsClassifier, k=1)",
                          lambda seed: KNeighborsClassifier(n_neighbors=1)),
    "decision_tree": ("fully grown decision tree (scikit-learn DecisionTreeClassifier)",
                      lambda seed: DecisionTreeClassifier(random_state=seed)),
    "random_forest": ("20-tree random forest (scikit-learn RandomForestClassifier), the closest "
                      "of the three to the retracted tree ensemble",
                      lambda seed: RandomForestClassifier(n_estimators=20, random_state=seed, n_jobs=1)),
}


# ── (a) Analytic ─────────────────────────────────────────────────────────────
def sign_agreement_probability(rho: float) -> float:
    """P(sign(X) == sign(Y)) for zero-mean jointly normal X, Y with correlation rho."""
    return 0.5 + float(np.arcsin(rho)) / np.pi


def analytic_table(horizon: int = HORIZON) -> list[dict]:
    """Each recent training row's label vs the test label, under i.i.d. returns. Row t-k shares
    max(horizon - k, 0) of the test label's daily returns, so rho = shared / horizon."""
    rows = []
    for lag in range(1, horizon + 1):
        shared = max(horizon - lag, 0)
        rho = shared / horizon
        rows.append({
            "training_row": f"t-{lag}",
            "shared_daily_returns": shared,
            "label_correlation": round(rho, 3),
            "p_sign_agree_pct": round(100.0 * sign_agreement_probability(rho), 2),
            "label_known_at_decision": lag >= horizon,
        })
    return rows


def monte_carlo_agreement(draws: int = MC_DRAWS, seed: int = MC_SEED) -> dict:
    """Sign agreement of the t-1 label and the test label on simulated i.i.d. returns."""
    rng = np.random.default_rng(seed)
    out = {}
    for name, sample in (("gaussian", lambda size: rng.standard_normal(size)),
                         (f"student_t_{STUDENT_T_DF}df", lambda size: rng.standard_t(STUDENT_T_DF, size))):
        r = sample((draws, HORIZON + 1))
        leaked = r[:, :HORIZON].sum(axis=1)            # r_t .. r_{t+4}
        test = r[:, 1:].sum(axis=1)                     # r_{t+1} .. r_{t+5}
        purged = sample((draws, HORIZON)).sum(axis=1)   # a matured label shares nothing with the test label
        out[name] = {
            "draws": draws,
            "p_sign_agree_leaked_t_minus_1_pct": round(100.0 * float(np.mean(np.sign(leaked) == np.sign(test))), 2),
            "p_sign_agree_purged_pct": round(100.0 * float(np.mean(np.sign(purged) == np.sign(test))), 2),
        }
    return {"seed": seed, **out}


# ── (b)-(d) Walk-forward on returns with no signal ───────────────────────────
def price_features(log_price: np.ndarray) -> np.ndarray:
    """Log price and its trailing moving averages: adjacent rows are near-duplicates, as the
    retracted model's price-level features (current_price, ma_5, ma_10, ...) were."""
    n = log_price.size
    csum = np.concatenate([[0.0], np.cumsum(log_price)])
    cols = [log_price]
    for w in MA_WINDOWS:
        ma = np.full(n, np.nan)
        ma[w - 1:] = (csum[w:] - csum[:-w]) / w
        cols.append(ma)
    return np.column_stack(cols)


def walk_forward(returns: np.ndarray, model: str, purge: bool, seed: int = 0) -> dict:
    """The retracted backtest's design on a return series: rolling window, a decision every STEP
    sessions, label = sign of the HORIZON-session forward return. purge=False trains on rows whose
    labels mature after the decision (the leak); purge=True drops them."""
    log_price = np.cumsum(np.asarray(returns, dtype=float))
    x = price_features(log_price)
    n = log_price.size
    fwd = np.full(n, np.nan)
    fwd[:-HORIZON] = log_price[HORIZON:] - log_price[:-HORIZON]
    first = int(np.argmax(~np.isnan(x).any(axis=1)))
    factory = MODELS[model][1]
    hits, ups, pnl, nn_recent, nn_recent_hits = [], [], [], [], []
    for t in range(first + MIN_TRAIN, n - HORIZON, STEP):
        lo = max(first, t - TRAIN_WINDOW)
        hi = t - PURGE if purge else t
        x_train, y_train = x[lo:hi], (fwd[lo:hi] > 0).astype(int)
        mu, sd = x_train.mean(axis=0), x_train.std(axis=0)
        sd[sd == 0] = 1.0
        clf = factory(seed)
        clf.fit((x_train - mu) / sd, y_train)
        x_test = ((x[t] - mu) / sd)[None, :]
        direction = 1.0 if clf.predict(x_test)[0] == 1 else -1.0
        hit = direction * fwd[t] > 0
        hits.append(hit)
        ups.append(fwd[t] > 0)
        pnl.append(direction * fwd[t])
        if model == "nearest_neighbour":
            nearest = int(clf.kneighbors(x_test, 1, return_distance=False)[0, 0])
            recent = nearest == len(x_train) - 1          # the newest training row (t-1, or t-5 purged)
            nn_recent.append(recent)
            if recent:
                nn_recent_hits.append(hit)
    pnl = np.asarray(pnl)
    out = {
        "n_decisions": len(hits),
        "accuracy_pct": round(100.0 * float(np.mean(hits)), 2),
        "hits": int(np.sum(hits)),
        "n_up": int(np.sum(ups)),
        "sharpe_gross": round(float(pnl.mean() / pnl.std(ddof=1) * np.sqrt(PERIODS_PER_YEAR)), 3),
    }
    if nn_recent:
        out["nearest_is_newest_row_pct"] = round(100.0 * float(np.mean(nn_recent)), 1)
        out["accuracy_when_nearest_is_newest_pct"] = (round(100.0 * float(np.mean(nn_recent_hits)), 2)
                                                      if nn_recent_hits else None)
    return out


def synthetic_returns(seed: int, n: int = N_DAYS) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, DAILY_VOL, n)


def block_shuffle(returns: np.ndarray, seed: int, block: int = SHUFFLE_BLOCK, n: int = N_DAYS) -> np.ndarray:
    """Cut the series into consecutive `block`-session blocks, permute the blocks, keep the first n
    returns. Fat tails and within-block volatility clustering survive; cross-block order does not."""
    r = np.asarray(returns, dtype=float)
    nblocks = r.size // block
    blocks = r[: nblocks * block].reshape(nblocks, block)
    order = np.random.default_rng(seed).permutation(nblocks)
    return blocks[order].ravel()[:n]


def real_returns(snapshot=None):
    """Close-to-close CL=F log returns and their dates over the pinned snapshot's whole history
    (SHA-256 verified); callers select any window from the dates."""
    snapshot = market_data.load_snapshot() if snapshot is None else snapshot
    rets, dates, _, _ = market_data.research_inputs(snapshot)
    return rets, dates


def retracted_reference() -> dict:
    """The retracted run's own numbers, read from its committed artifact (never retyped)."""
    report = json.loads(data_json_path(RETRACTED_ARTIFACT).read_text(encoding="utf-8"))
    res = report["results"]["1w"]
    pnl = res["pnl_metrics"]
    return {
        "artifact": f"data/{RETRACTED_ARTIFACT}",
        "n": int(res["samples"]),
        "accuracy_pct": round(float(res["metrics"]["ensemble"]["direction_accuracy"]), 2),
        "sharpe": float(pnl["sharpe_ratio_annualized"]),
        # The simulations' Sharpe ratios are gross; this one is not, so they are not like for like.
        "sharpe_basis": "after transaction costs",
        "transaction_cost_per_trade_usd": float(pnl["transaction_cost_per_trade"]),
    }


def summarize(runs: list[dict], seeds: list[int]) -> dict:
    """Mean and range over runs, a t-test of the per-run accuracies against 50% (one-sided), a pooled
    binomial test over all decisions (labels never overlap within a run), and a binomial test of each
    run against its own base rate max(p, 1 - p), p the share of rising labels: the accuracy of always
    calling the more frequent sign. The t-test and the pooled test assume independent runs, which
    holds for synthetic seeds but not for block shuffles of the same returns."""
    acc = np.array([r["accuracy_pct"] for r in runs])
    sharpe = np.array([r["sharpe_gross"] for r in runs])
    hits = int(sum(r["hits"] for r in runs))
    total = int(sum(r["n_decisions"] for r in runs))
    out = {
        "n_runs": len(runs),
        "seeds": list(seeds),
        "decisions_per_run": sorted({r["n_decisions"] for r in runs}),
        "accuracy_pct_mean": round(float(acc.mean()), 2),
        "accuracy_pct_range": [round(float(acc.min()), 2), round(float(acc.max()), 2)],
        "sharpe_mean": round(float(sharpe.mean()), 3),
        "sharpe_range": [round(float(sharpe.min()), 3), round(float(sharpe.max()), 3)],
        "pooled_accuracy_pct": round(100.0 * hits / total, 2),
        "pooled_binomial_p_value_vs_50": float(stats.binom.sf(hits - 1, total, 0.5)),
    }
    base = [max(r["n_up"], r["n_decisions"] - r["n_up"]) / r["n_decisions"] for r in runs]
    p_base = [float(stats.binom.sf(r["hits"] - 1, r["n_decisions"], b)) for r, b in zip(runs, base)]
    out["base_rate_pct_mean"] = round(100.0 * float(np.mean(base)), 2)
    out["runs_beating_base_rate_5pct"] = int(sum(p < 0.05 for p in p_base))
    if len(runs) > 1 and acc.std(ddof=1) > 0:
        t = stats.ttest_1samp(acc, 50.0, alternative="greater")
        out["t_stat_vs_50"] = round(float(t.statistic), 2)
        out["t_test_p_value_vs_50"] = float(t.pvalue)
    nn = [r for r in runs if "nearest_is_newest_row_pct" in r]
    if nn:
        out["nearest_is_newest_row_pct_mean"] = round(float(np.mean([r["nearest_is_newest_row_pct"] for r in nn])), 1)
        rows = [r["accuracy_when_nearest_is_newest_pct"] for r in nn if r["accuracy_when_nearest_is_newest_pct"] is not None]
        out["accuracy_when_nearest_is_newest_pct_mean"] = round(float(np.mean(rows)), 2) if rows else None
    out["runs"] = [{"seed": s, **{k: r[k] for k in ("accuracy_pct", "sharpe_gross", "n_decisions")},
                    "base_rate_pct": round(100.0 * b, 2), "binomial_p_value_vs_base_rate": pb}
                   for s, r, b, pb in zip(seeds, runs, base, p_base)]
    return out


def experiment(series_for_seed, seeds_by_model: dict) -> dict:
    out = {}
    for model, seeds in seeds_by_model.items():
        out[model] = {}
        for purge in (False, True):
            runs = [walk_forward(series_for_seed(s), model, purge, seed=s) for s in seeds]
            out[model]["purged" if purge else "unpurged_leaked"] = summarize(runs, seeds)
    return out


def run(snapshot=None) -> dict:
    snapshot = market_data.load_snapshot() if snapshot is None else snapshot
    all_rets, all_dates = real_returns(snapshot=snapshot)
    in_window = (all_dates >= REAL_WINDOW[0]) & (all_dates <= REAL_WINDOW[1])
    window_rets = all_rets[in_window]
    # Share of rising 5-session returns over the whole history the shuffled runs draw from.
    up_share = 100.0 * float(np.mean(np.convolve(all_rets, np.ones(HORIZON), "valid") > 0))

    synthetic = experiment(synthetic_returns, {"nearest_neighbour": SYNTHETIC_SEEDS,
                                               "decision_tree": SYNTHETIC_SEEDS,
                                               "random_forest": FOREST_SEEDS})
    shuffled = experiment(lambda s: block_shuffle(all_rets, s), {"nearest_neighbour": SHUFFLE_SEEDS,
                                                                 "decision_tree": SHUFFLE_SEEDS})
    real = experiment(lambda s: window_rets, {"nearest_neighbour": (0,), "decision_tree": (0,),
                                              "random_forest": (0,)})
    table = analytic_table()
    leaked_rho = table[0]["label_correlation"]
    nn_syn = synthetic["nearest_neighbour"]
    rf_syn = synthetic["random_forest"]
    nn_shuf = shuffled["nearest_neighbour"]
    dt_shuf = shuffled.get("decision_tree")
    retracted = retracted_reference()
    conclusion = (
        f"Under a pure random walk the newest leaked training label agrees in sign with the test label "
        f"{table[0]['p_sign_agree_pct']:.1f}% of the time (1/2 + arcsin({leaked_rho})/pi), with no "
        f"information about the future. On synthetic i.i.d. returns a 1-nearest-neighbour model trained "
        f"without the purge scores {nn_syn['unpurged_leaked']['accuracy_pct_mean']:.1f}% "
        f"(gross Sharpe {nn_syn['unpurged_leaked']['sharpe_mean']:.2f}); its nearest neighbour is the newest row "
        f"in {nn_syn['unpurged_leaked']['nearest_is_newest_row_pct_mean']:.0f}% of decisions, and on those it "
        f"scores {nn_syn['unpurged_leaked']['accuracy_when_nearest_is_newest_pct_mean']:.1f}% (a little above "
        f"the unconditional {table[0]['p_sign_agree_pct']:.1f}%: the newest row is nearest when its one unshared "
        f"return is small, which makes the two labels even more alike). A 20-tree random forest scores "
        f"{rf_syn['unpurged_leaked']['accuracy_pct_mean']:.1f}% with a gross Sharpe of "
        f"{rf_syn['unpurged_leaked']['sharpe_mean']:.2f} on noise, against the retracted model's "
        f"{retracted['accuracy_pct']:.1f}% and {retracted['sharpe']:.2f} {retracted['sharpe_basis']}. With "
        f"the purge the same models score {nn_syn['purged']['accuracy_pct_mean']:.1f}% and "
        f"{rf_syn['purged']['accuracy_pct_mean']:.1f}%. Block-shuffled real CL=F returns give the same picture "
        f"({nn_shuf['unpurged_leaked']['accuracy_pct_mean']:.1f}% leaked vs "
        f"{nn_shuf['purged']['accuracy_pct_mean']:.1f}% purged). Real {HORIZON}-session returns rise {up_share:.1f}% "
        f"of the time, so the no-skill accuracy there is each run's base rate, not 50%. Against it, the "
        f"1-nearest-neighbour model's runs beat their own base rate at 5% in "
        f"{nn_shuf['unpurged_leaked']['runs_beating_base_rate_5pct']} of {nn_shuf['unpurged_leaked']['n_runs']} "
        f"leaked and {nn_shuf['purged']['runs_beating_base_rate_5pct']} of {nn_shuf['purged']['n_runs']} purged runs"
        + (f"; the decision tree's in {dt_shuf['unpurged_leaked']['runs_beating_base_rate_5pct']} of "
           f"{dt_shuf['unpurged_leaked']['n_runs']} and {dt_shuf['purged']['runs_beating_base_rate_5pct']} of "
           f"{dt_shuf['purged']['n_runs']}" if dt_shuf else "")
        + ". The shuffled runs reuse the same returns, so they are not "
        "independent samples and their pooled tests overstate the evidence. The retracted Sharpe was the label "
        "overlap, not a forecast."
    )
    return {
        "question": (f"Was the retracted direction model's Sharpe of {retracted['sharpe']:.2f} "
                     f"{retracted['sharpe_basis']} ({retracted['accuracy_pct']:.0f}% accuracy) mechanical?"),
        "design": {
            "label": f"sign of the {HORIZON}-session forward log return",
            "purge_rows": PURGE,
            "step": STEP, "train_window": TRAIN_WINDOW, "min_train": MIN_TRAIN, "n_days_per_run": N_DAYS,
            "features": "log price and its " + "/".join(map(str, MA_WINDOWS)) + "-session moving averages, "
                        "standardized on the training window",
            "models": {k: v[0] for k, v in MODELS.items()},
            "sharpe": (f"gross (no transaction costs): direction x {HORIZON}-session log return, annualized by "
                       f"sqrt(252/{STEP}). The retracted model's Sharpe (retracted_reference) is after costs, "
                       "so the two are not like for like; costs would only lower these."),
            "synthetic_returns": f"i.i.d. normal, daily sd {DAILY_VOL}",
            "shuffled_returns": (f"CL=F close-to-close log returns (data/market/cl_f_daily.csv), "
                                 f"{SHUFFLE_BLOCK}-session blocks permuted, first {N_DAYS} returns"),
            "independence": (f"Synthetic runs draw independent returns. The {len(SHUFFLE_SEEDS)} block-shuffled runs "
                             "are permutations of the same CL=F returns, so they are not independent: their "
                             "t-test across runs and pooled binomial test overstate the evidence. Real "
                             f"{HORIZON}-session CL=F returns rise {up_share:.1f}% of the time, so 50% is not the "
                             "no-skill accuracy on them; every run is also tested against its own base rate "
                             "max(p, 1 - p) (runs[].binomial_p_value_vs_base_rate, runs_beating_base_rate_5pct)."),
            "cl_5_session_up_share_pct": round(up_share, 1),
            "real_window": list(REAL_WINDOW),
        },
        "analytic": {
            "formula": "P(same sign) = 1/2 + arcsin(rho)/pi for jointly normal labels with correlation rho",
            "leaked_label_correlation": leaked_rho,
            "p_sign_agree_leaked_pct": table[0]["p_sign_agree_pct"],
            "by_training_row": table,
            "monte_carlo": monte_carlo_agreement(),
        },
        "synthetic_random_walk": synthetic,
        "block_shuffled_cl": shuffled,
        "real_cl_5y_window": {"n_returns": int(window_rets.size), **real},
        "retracted_reference": retracted,
        "data": {"cl_daily_sha256": snapshot.manifest["files"]["cl_daily"]["sha256"],
                 "sessions_through": snapshot.manifest.get("sessions_through")},
        "conclusion": conclusion,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    res = run()
    path = data_json_path(ARTIFACT)
    path.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    for label in ("synthetic_random_walk", "block_shuffled_cl", "real_cl_5y_window"):
        for model, block in res[label].items():
            if not isinstance(block, dict):
                continue
            leak, pur = block["unpurged_leaked"], block["purged"]
            print(f"{label:22s} {model:18s} leaked {leak['accuracy_pct_mean']:5.1f}% {leak['accuracy_pct_range']} "
                  f"Sharpe {leak['sharpe_mean']:+.2f} | purged {pur['accuracy_pct_mean']:5.1f}% "
                  f"{pur['accuracy_pct_range']} Sharpe {pur['sharpe_mean']:+.2f}")
    print(f"-> {res['conclusion']}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
