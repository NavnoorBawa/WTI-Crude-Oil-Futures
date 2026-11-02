"""Forecast-evaluation statistics shared by the production forecaster and the research lab.

Pure functions with no project imports (numpy/scipy; statsmodels and arch are imported lazily by the
two functions that need them), each checked against cases with a known answer in tests/:

  - newey_west_lrv / hac_z      Bartlett-kernel long-run variance and HAC z-statistics.
  - qlike / mse_var             The two volatility losses that rank forecasts consistently when the
                                realized-vol proxy is noisy (Patton 2011).
  - dm_test                     Diebold-Mariano (1995) with Newey-West variance and the
                                Harvey-Leybourne-Newbold (1997) small-sample correction; the Bartlett
                                bandwidth automatic or given.
  - nw94_bandwidth              The Newey-West (1994) data-dependent Bartlett bandwidth.
  - batch_means_test            Ibragimov-Mueller (2010) t-test on the means of consecutive periods: a
                                Diebold-Mariano test robust to persistence shorter than a period.
  - mincer_zarnowitz            Unbiasedness regression with a HAC Wald test.
  - model_confidence_set        Hansen-Lunde-Nason (2011) MCS via arch.bootstrap.
  - spa_test                    Hansen (2005) test of superior predictive ability, studentized, on
                                arch's stationary bootstrap (arch 8.0.0's own SPA ignores its
                                studentize flag; its p-value is kept as `non_studentized`).
  - kupiec / christoffersen /   VaR backtests: unconditional coverage (Kupiec 1995), independence and
    dq_test / basel_zone        conditional coverage (Christoffersen 1998), the Dynamic Quantile test
                                (Engle-Manganelli 2004) and the Basel traffic light.
  - coverage_by_group_test      HAC Wald tests of an interval's misses across groups (regimes): equal
                                miss rates, every rate at its target, and a HAC z per group.
  - hac_lrv / gw_test           Long-run covariance with the truncated or Bartlett kernel, and the
                                Giacomini-White (2006) test of equal conditional predictive ability
                                (the full test and its conditional part, the instruments' slopes).
  - rolling_dm /                The Giacomini-Rossi (2010) fluctuation test: a rolling Diebold-Mariano t,
    fluctuation_null /          each window on its own long-run variance; its two-sided null simulated on
    fluctuation_reference /     Gaussian random walks at the sample's resolution, one set for every window
    fluctuation_test            length; marginal p-values, critical values, and the p adjusted for the
                                choice among the window lengths.
  - adaptive_conformal /        Adaptive conformal inference intervals (Gibbs and Candes 2021), and
    delayed_aci                 the same when each outcome is only observed `delay` rows later.
  - pinball_loss /              Scores of a predictive distribution given by quantiles: the pinball
    crps_from_quantiles /       loss, the CRPS from a quantile grid (the weighted interval score of
    rearrange                   Bracher et al. 2021) and the monotone rearrangement of crossed quantiles
                                (Chernozhukov, Fernandez-Val and Galichon 2010).
  - trailing_quantiles /        Predictive quantiles and the mid-rank PIT from trailing matured log
    rank_pit_null               residuals (delayed_aci's window, without its adaptive step), and the
                                simulated null of that PIT's Berkowitz statistics under the 5-session
                                overlap.
  - sample_pit                  The PIT of an outcome against a simulated predictive sample (Hazen,
                                interpolated, clamped).
  - berkowitz_test /            Berkowitz (2001) LR3 on normal scores, against chi2(3) or a simulated
    berkowitz_censored          null, and a doubly censored normal LR (only the interior of the 5% and
                                95% quantiles observed).
  - bonferroni_min /            Combinations of p-values valid under any dependence (Bonferroni) and
    cauchy_combination          under weak dependence (Liu and Xie 2020).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd
from scipy import optimize, special, stats

if TYPE_CHECKING:
    import numpy.typing as npt

    FloatArray = npt.NDArray[np.float64]
    IntArray = npt.NDArray[np.int64]
    BoolArray = npt.NDArray[np.bool_]


def newey_west_lrv(x: npt.ArrayLike, lags: int | None = None) -> tuple[float, int]:
    """Bartlett-kernel long-run variance of a series around its mean, with the Newey-West (1994)
    automatic bandwidth floor(4 (n/100)^(2/9)) when lags is None. Returns (variance, lags)."""
    arr = np.asarray(x, dtype=float)
    n = arr.size
    if n < 2:
        return 0.0, 0
    if lags is None:
        lags = int(np.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))
    lags = max(0, min(int(lags), n - 1))
    e = arr - arr.mean()
    lrv = float(e @ e) / n
    for k in range(1, lags + 1):
        lrv += 2.0 * (1.0 - k / (lags + 1.0)) * float(e[k:] @ e[:-k]) / n
    return max(lrv, 0.0), lags


def hac_z(x: npt.ArrayLike, null_mean: float = 0.0) -> tuple[float, int]:
    """z-statistic for mean(x) > null_mean with a Newey-West standard error."""
    arr = np.asarray(x, dtype=float)
    lrv, lags = newey_west_lrv(arr)
    if arr.size == 0 or lrv <= 0:
        return 0.0, lags
    return float((arr.mean() - null_mean) / np.sqrt(lrv / arr.size)), lags


def qlike(actual: npt.ArrayLike, forecast: npt.ArrayLike) -> FloatArray:
    """QLIKE on variances (Patton 2011): zero iff forecast == actual, robust to proxy noise."""
    ratio = (np.asarray(actual, float) / np.asarray(forecast, float)) ** 2
    return cast("FloatArray", ratio - np.log(ratio) - 1.0)


def mse_var(actual: npt.ArrayLike, forecast: npt.ArrayLike) -> FloatArray:
    """Squared error on annualized variances (the other Patton-robust loss)."""
    return cast("FloatArray", (np.asarray(actual, float) ** 2 - np.asarray(forecast, float) ** 2) ** 2)


def dm_test(loss_a: npt.ArrayLike, loss_b: npt.ArrayLike, horizon: int = 1, lags: int | None = None) -> dict[str, Any]:
    """Diebold-Mariano test of equal expected loss, d = loss_a - loss_b (positive: b is better).

    Newey-West long-run variance and the Harvey-Leybourne-Newbold (1997) small-sample factor, compared
    with Student-t(n-1). Two-sided p-value. The Bartlett bandwidth is the automatic floor(4 (n/100)^(2/9))
    when `lags` is None (every published DM), else `lags` (capped at n - 1; e.g. nw94_bandwidth(d)).
    """
    diff = np.asarray(loss_a, float) - np.asarray(loss_b, float)
    n = diff.size
    lrv, lags = newey_west_lrv(diff, lags)
    if n < 3 or lrv <= 0:
        return {"stat": 0.0, "p_value": 1.0, "mean_diff": float(diff.mean()) if n else 0.0, "lags": lags}
    dm = float(diff.mean() / np.sqrt(lrv / n))
    hln = np.sqrt((n + 1 - 2 * horizon + horizon * (horizon - 1) / n) / n)
    stat = dm * hln
    return {"stat": round(stat, 3), "p_value": float(2 * stats.t.sf(abs(stat), df=n - 1)),
            "mean_diff": float(diff.mean()), "lags": int(lags)}


def nw94_bandwidth(diff: npt.ArrayLike) -> int:
    """Newey-West (1994) data-dependent Bartlett bandwidth of a series around its mean: pilot lags
    n_T = floor(4 (n/100)^(2/9)), autocovariances g_j = n^-1 sum_t x_t x_{t-j} (x demeaned), s0 = g_0 + 2 sum_{j<=n_T}
    g_j, s1 = 2 sum_{j<=n_T} j g_j, m = floor(1.1447 ((s1/s0)^2 n)^(1/3)), capped at n - 1. 0 when the series has
    fewer than two values or s0 <= 0 (no positive long-run variance to estimate a bandwidth for)."""
    d = np.asarray(diff, dtype=float)
    n = d.size
    if n < 2:
        return 0
    x = d - d.mean()
    pilot = min(int(np.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))), n - 1)
    g = np.array([float(x[j:] @ x[:n - j]) / n for j in range(pilot + 1)])
    s0 = g[0] + 2.0 * g[1:].sum()
    if s0 <= 0:
        return 0
    s1 = 2.0 * float(np.sum(np.arange(1, pilot + 1) * g[1:]))
    return min(int(np.floor(1.1447 * ((s1 / s0) ** 2 * n) ** (1.0 / 3.0))), n - 1)


def batch_means_test(diff: npt.ArrayLike, groups: int) -> dict[str, Any]:
    """Ibragimov and Mueller (2010): split diff into `groups` consecutive periods (np.array_split: the first
    n % groups periods one row longer), take each period's mean, and t = mean of the period means / (their sd, ddof 1,
    / sqrt(groups)), two-sided p from Student-t(groups - 1). Valid when the period means are close to independent
    and normal, so robust to persistence and heterogeneity shorter than a period. `periods_positive` counts the
    periods with a positive mean. Degenerate case, as in dm_test: when every period mean is exactly the same (sd 0),
    stat 0 and p 1."""
    d = np.asarray(diff, dtype=float)
    if groups < 2 or d.size < groups:
        raise ValueError("the batch-means test needs at least two periods with at least one row each")
    means = np.array([float(chunk.mean()) for chunk in np.array_split(d, groups)])
    sd = float(means.std(ddof=1))
    if sd <= 0:
        stat, p = 0.0, 1.0
    else:
        stat = float(means.mean() / (sd / np.sqrt(groups)))
        p = float(2 * stats.t.sf(abs(stat), df=groups - 1))
    return {"stat": stat, "p_value": p, "groups": int(groups), "period_means": [float(m) for m in means],
            "periods_positive": int(np.count_nonzero(means > 0))}


def mincer_zarnowitz(actual: npt.ArrayLike, forecast: npt.ArrayLike, maxlags: int = 6) -> dict[str, float]:
    """Regress realized variance on forecast variance; an unbiased, efficient forecast has
    intercept 0 and slope 1. HAC (Newey-West) Wald test of both; also R^2."""
    import statsmodels.api as sm

    yv = np.asarray(actual, float) ** 2
    fv = np.asarray(forecast, float) ** 2
    res = sm.OLS(yv, sm.add_constant(fv)).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    wald = res.wald_test("const = 0, x1 = 1", scalar=True)
    return {"intercept": float(res.params[0]), "slope": float(res.params[1]),
            "slope_se": float(res.bse[1]), "r2": float(res.rsquared), "p_unbiased": float(wald.pvalue)}


def model_confidence_set(losses: pd.DataFrame, *, block: int, size: float, reps: int,
                         seed: int) -> dict[str, Any]:
    """Hansen-Lunde-Nason Model Confidence Set: range statistic, arch's stationary bootstrap with
    mean block length `block`, `reps` draws seeded with `seed`. Returns MCS p-values and the set
    retained at confidence 1 - size. Every setting is the caller's (the lab records its own in the
    artifact), so none has a default here."""
    from arch.bootstrap import MCS

    mcs = MCS(losses, size=size, reps=reps, block_size=block, method="R", seed=seed)
    mcs.compute()
    pvals = mcs.pvalues["Pvalue"].to_dict()
    return {"included": sorted(mcs.included, key=lambda m: -pvals[m]),
            "p_values": {m: round(float(p), 4) for m, p in pvals.items()}}


def stationary_bootstrap_lrv(d: npt.ArrayLike, block: int) -> FloatArray:
    """Long-run variance of each column of `d` (n x k), var(sqrt(n) * mean), with the kernel implied by
    a stationary bootstrap of mean block length `block` (Politis and Romano 1994), as arch computes
    it for its SPA test: gamma_0 + 2 sum_i kappa_i gamma_i, kappa_i = (1 - i/n)(1 - p)^i + (i/n)(1 - p)^(n - i),
    p = 1/block."""
    x = np.asarray(d, dtype=float)
    n = x.shape[0]
    demeaned = x - x.mean(axis=0)
    p = 1.0 / block
    variance = np.sum(demeaned ** 2, axis=0) / n
    for i in range(1, n):
        kappa = (1.0 - i / n) * (1.0 - p) ** i + (i / n) * (1.0 - p) ** (n - i)
        variance += 2.0 * kappa * np.sum(demeaned[: n - i] * demeaned[i:], axis=0) / n
    return cast("FloatArray", variance)


def spa_test(benchmark: npt.ArrayLike, models: pd.DataFrame, *, block: int, reps: int, seed: int) -> dict[str, Any]:
    """Hansen (2005) test of superior predictive ability: does any model in `models` (n x k losses) have
    a lower expected loss than `benchmark` (n losses)? Small p: at least one does.

    With d_k = benchmark - model_k (positive: model k better), dbar_k its mean and omega_k^2 its long-run
    variance (stationary_bootstrap_lrv), the statistic is T = max(max_k sqrt(n) dbar_k / omega_k, 0).
    The null distribution comes from a stationary bootstrap of d (mean block `block`, `reps` draws,
    seeded): Z*_k = sqrt(n) (dbar*_k - g(dbar_k)) / omega_k with Hansen's three recentrings, g = max(x, 0)
    (lower), x where x >= -omega_k sqrt(2 log log n / n) else 0 (consistent), and x (upper); each p-value
    is the share of draws with max(max_k Z*_k, 0) >= T, so T = 0 (no model better on average) gives
    p = 1. Studentizing matters when the models' loss differences have different variances: without
    it the noisiest model sets the maximum.

    `non_studentized` repeats the computation on raw means (max_k dbar_k against max_k (dbar*_k - g),
    strict inequality), on the same draws: White's (2000) Reality Check with Hansen's recentring, which
    is what arch.bootstrap.SPA 8.0.0 returns whatever its `studentize` flag says.
    """
    from arch.bootstrap import StationaryBootstrap

    d = np.asarray(benchmark, dtype=float)[:, None] - models.to_numpy(dtype=float)
    n = d.shape[0]
    dbar = d.mean(axis=0)
    omega = np.sqrt(stationary_bootstrap_lrv(d, block))
    if not np.all(omega > 0):
        raise ValueError("spa_test: a model's loss difference from the benchmark has no variance")
    valid = dbar >= -omega * np.sqrt(2.0 * np.log(np.log(n)) / n)
    centres = {"lower": np.maximum(dbar, 0.0), "consistent": np.where(valid, dbar, 0.0), "upper": dbar}
    t_stats = np.sqrt(n) * dbar / omega
    statistic = max(float(t_stats.max()), 0.0)
    raw_statistic = float(dbar.max())
    exceed = dict.fromkeys(centres, 0)
    exceed_raw = dict.fromkeys(centres, 0)
    bootstrap = StationaryBootstrap(block, d, seed=seed)
    for data, _ in bootstrap.bootstrap(reps):
        mean_star = data[0].mean(axis=0)
        for name, centre in centres.items():
            z = mean_star - centre
            exceed[name] += max(float((np.sqrt(n) * z / omega).max()), 0.0) >= statistic
            exceed_raw[name] += float(z.max()) > raw_statistic
    best = int(np.argmax(t_stats))
    return {
        "statistic": round(statistic, 4),
        # The model with the largest studentized gain; None when no model is better on average (T = 0).
        "best_model": str(models.columns[best]) if statistic > 0 else None,
        "t_stats": {str(name): round(float(t), 3) for name, t in zip(models.columns, t_stats)},
        "p_consistent": round(exceed["consistent"] / reps, 4),
        "p_lower": round(exceed["lower"] / reps, 4),
        "p_upper": round(exceed["upper"] / reps, 4),
        "non_studentized": {f"p_{name}": round(count / reps, 4) for name, count in exceed_raw.items()},
    }


def kupiec(exceed: npt.ArrayLike, p: float) -> dict[str, Any]:
    """Kupiec (1995) proportion-of-failures likelihood-ratio test."""
    x = np.asarray(exceed, bool)
    n, k = x.size, int(x.sum())
    phat = k / n if n else 0.0
    ll0 = (n - k) * np.log(1 - p) + k * np.log(p)
    ll1 = ((n - k) * np.log(1 - phat) if k < n else 0.0) + (k * np.log(phat) if k > 0 else 0.0)
    lr = max(-2.0 * (ll0 - ll1), 0.0)
    return {"n": int(n), "exceedances": k, "expected": round(n * p, 1), "lr": round(float(lr), 3),
            "p_value": float(stats.chi2.sf(lr, 1))}


def christoffersen(exceed: npt.ArrayLike, p: float) -> dict[str, Any]:
    """Christoffersen (1998) independence and conditional-coverage likelihood-ratio tests."""
    x = np.asarray(exceed, int)
    a, b = x[:-1], x[1:]
    n00, n01 = int(((a == 0) & (b == 0)).sum()), int(((a == 0) & (b == 1)).sum())
    n10, n11 = int(((a == 1) & (b == 0)).sum()), int(((a == 1) & (b == 1)).sum())

    def ll(prob: float, zeros: int, ones: int) -> float:
        out = 0.0
        if zeros:
            out += zeros * np.log(1 - prob) if prob < 1 else -np.inf
        if ones:
            out += ones * np.log(prob) if prob > 0 else -np.inf
        return out

    pi0 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi1 = n11 / (n10 + n11) if n10 + n11 else 0.0
    pi = (n01 + n11) / max(len(a), 1)
    lr_ind = max(-2.0 * (ll(pi, n00 + n10, n01 + n11) - ll(pi0, n00, n01) - ll(pi1, n10, n11)), 0.0)
    lr_pof = kupiec(x, p)["lr"]
    lr_cc = lr_pof + lr_ind
    return {"lr_ind": round(float(lr_ind), 3), "p_independence": float(stats.chi2.sf(lr_ind, 1)),
            "lr_cc": round(float(lr_cc), 3), "p_conditional_coverage": float(stats.chi2.sf(lr_cc, 2)),
            "consecutive_exceedances": n11}


def dq_test(exceed: npt.ArrayLike, var_level: npt.ArrayLike, p: float, lags: int = 4) -> dict[str, Any]:
    """Engle-Manganelli (2004) Dynamic Quantile test: regress Hit_t = 1{exceed} - p on a constant,
    `lags` lagged hits and the VaR itself; under correct conditional coverage every coefficient is
    zero. DQ = b' X'X b / (p (1 - p)) ~ chi2(k)."""
    hit = np.asarray(exceed, float) - p
    n = hit.size
    rows = [np.ones(n - lags)]
    for j in range(1, lags + 1):
        rows.append(hit[lags - j: n - j])
    rows.append(np.asarray(var_level, float)[lags:])
    Xd = np.column_stack(rows)
    yd = hit[lags:]
    beta, *_ = np.linalg.lstsq(Xd, yd, rcond=None)
    dq = float(beta @ Xd.T @ Xd @ beta) / (p * (1 - p))
    return {"dq": round(dq, 3), "df": int(Xd.shape[1]), "p_value": float(stats.chi2.sf(dq, Xd.shape[1]))}


def coverage_by_group_test(miss: npt.ArrayLike, group: npt.ArrayLike, k: int, alpha: float, lags: int) -> dict[str, Any]:
    """HAC Wald tests of interval misses across groups 0..k-1 (Newey-West, Bartlett weights 1 - j/(lags + 1), no
    small-sample correction). The regression of (miss - alpha) on the k group dummies gives b_j = group j's miss rate
    - alpha. `equal`: R b = 0, chi2(k - 1): every group's miss rate the same (the band's level is pinned by its
    method, so this is the dependence test). `all_cells`: b = 0, chi2(k): every group's rate at alpha. `cells`: b_j
    / se_j with its two-sided normal p. Every group must be non-empty."""
    y = np.asarray(miss, dtype=float) - alpha
    g = np.asarray(group, dtype=int)
    dummies = np.column_stack([(g == j).astype(float) for j in range(k)])
    if dummies.sum(axis=0).min() == 0:
        raise ValueError("every group needs at least one observation")
    b = np.array([y[g == j].mean() for j in range(k)])
    u = y - dummies @ b
    n = y.size
    s = dummies * u[:, None]
    meat = s.T @ s / n
    for lag in range(1, lags + 1):
        gamma = s[lag:].T @ s[:-lag] / n
        meat += (1 - lag / (lags + 1)) * (gamma + gamma.T)
    bread = np.linalg.inv(dummies.T @ dummies / n)
    cov = bread @ meat @ bread / n
    r = np.column_stack([-np.ones(k - 1), np.eye(k - 1)])
    rb = r @ b
    w_equal = float(rb @ np.linalg.solve(r @ cov @ r.T, rb))
    w_all = float(b @ np.linalg.solve(cov, b))
    z = b / np.sqrt(np.diag(cov))
    return {"equal": {"wald": w_equal, "df": k - 1, "p_value": float(stats.chi2.sf(w_equal, k - 1))},
            "all_cells": {"wald": w_all, "df": k, "p_value": float(stats.chi2.sf(w_all, k))},
            "cells": [{"z": float(zj), "p_value": float(2 * stats.norm.sf(abs(zj)))} for zj in z],
            "lags": int(lags)}


def _kernel_weights(lags: int, kernel: str) -> FloatArray:
    """Weights of lags 1..lags: 1 ("truncated") or 1 - j/(lags + 1) ("bartlett")."""
    j = np.arange(1, lags + 1, dtype=float)
    if kernel == "truncated":
        return np.ones(lags)
    if kernel == "bartlett":
        return 1.0 - j / (lags + 1.0)
    raise ValueError(f"unknown kernel {kernel!r}")


def hac_lrv(x: npt.ArrayLike, lags: int, *, kernel: str = "truncated", centred: bool = False) -> FloatArray:
    """Long-run covariance of the rows of x (n x k; a 1-D x is one column): G0 + sum_{j=1..lags} w_j (Gj + Gj'),
    Gj = n^-1 sum_t x_t x_{t-j}'. w_j = 1 ("truncated": Giacomini-White's estimator for tau-step forecasts at
    lags = tau - 1) or 1 - j/(lags + 1) ("bartlett"). Around zero (the null imposed) unless centred. Not
    guaranteed positive semi-definite with the truncated kernel; callers check."""
    z = np.asarray(x, dtype=float)
    if z.ndim == 1:
        z = z[:, None]
    if centred:
        z = z - z.mean(axis=0)
    n = z.shape[0]
    omega = z.T @ z / n
    for j, w in zip(range(1, lags + 1), _kernel_weights(lags, kernel)):
        g = z[j:].T @ z[:-j] / n
        omega = omega + w * (g + g.T)
    return cast("FloatArray", omega)


def _wald(mean: FloatArray, omega: FloatArray, n: int) -> float:
    eig = np.linalg.eigvalsh(omega)
    if eig.min() <= 1e-12 * max(float(eig.max()), 1e-300):
        raise np.linalg.LinAlgError(f"the HAC matrix is not positive definite (smallest eigenvalue {eig.min():.3g})")
    return float(n * mean @ np.linalg.solve(omega, mean))


def gw_test(diff: npt.ArrayLike, instruments: npt.ArrayLike, *, lags: int, kernel: str = "truncated") -> dict[str, Any]:
    """Giacomini-White (2006) test of equal conditional predictive ability. diff[t] is the loss differential of the
    forecasts made at t; instruments[t] (n x k, no constant) must be known when they were made: the caller aligns
    them and drops rows without a value. Full test: Z = [1, x] * d, W = n Zbar' Omega^-1 Zbar ~ chi2(k + 1), Omega =
    hac_lrv(Z, lags, kernel) uncentred (the null imposed). Conditional part: u = (x - xbar)(d - dbar), W_c ~
    chi2(k), unchanged by adding a constant to d (but not independent of dbar). `coef`: OLS of d on [1, x]. Raises
    numpy.linalg.LinAlgError when either HAC matrix is not positive definite (smallest eigenvalue <= 1e-12 x
    largest), and ValueError on a non-finite input."""
    d = np.asarray(diff, dtype=float)
    x = np.asarray(instruments, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    if not (np.isfinite(d).all() and np.isfinite(x).all()) or d.size != x.shape[0]:
        raise ValueError("the differential and the instruments must be finite and aligned row by row")
    n, k = x.shape
    h = np.column_stack([np.ones(n), x])
    z = h * d[:, None]
    w_full = _wald(z.mean(axis=0), hac_lrv(z, lags, kernel=kernel), n)
    u = (x - x.mean(axis=0)) * (d - d.mean())[:, None]
    w_cond = _wald(u.mean(axis=0), hac_lrv(u, lags, kernel=kernel), n)
    coef, *_ = np.linalg.lstsq(h, d, rcond=None)
    return {"stat": w_full, "df": k + 1, "p_value": float(stats.chi2.sf(w_full, k + 1)), "n": int(n),
            "lags": int(lags), "kernel": kernel,
            "conditional": {"stat": w_cond, "df": k, "p_value": float(stats.chi2.sf(w_cond, k))},
            "coef": [float(c) for c in coef]}


def rolling_dm(diff: npt.ArrayLike, window: int, *, lags: int, kernel: str = "truncated") -> tuple[FloatArray, FloatArray]:
    """Diebold-Mariano t on every run of `window` consecutive values: element i is the window ending at
    i + window - 1, sum / sqrt(window * lrv_i) with lrv_i = hac_lrv(slice, lags, kernel, centred=True). No HLN
    factor. Returns (t, lrv). Raises ValueError when a window's long-run variance is not positive."""
    d = np.asarray(diff, dtype=float)
    win = np.lib.stride_tricks.sliding_window_view(d, window)
    e = win - win.mean(axis=1, keepdims=True)
    lrv = (e * e).sum(axis=1) / window
    for j, w in zip(range(1, lags + 1), _kernel_weights(lags, kernel)):
        lrv = lrv + 2.0 * w * (e[:, j:] * e[:, :-j]).sum(axis=1) / window
    if (lrv <= 0).any():
        raise ValueError("a window's long-run variance is not positive")
    return win.sum(axis=1) / np.sqrt(window * lrv), lrv


def fluctuation_null(n: int, windows: Sequence[int], *, draws: int, seed: Sequence[int],
                     batch: int = 1000) -> FloatArray:
    """(draws, len(windows)), unsorted: row b, column j = max_i |S_i - S_{i-m_j}| / sqrt(m_j) for the b-th Gaussian
    random walk S = [0, cumsum(e)] of n steps (numpy default_rng(SeedSequence(seed)), batches of `batch`, which do
    not change the draws). Column j is Giacomini and Rossi's (2010) two-sided limit sup |B(s) - B(s - mu)| / sqrt(mu),
    mu = m_j / n, at the sample's own resolution; the rows are the joint null of the window-length search."""
    rng = np.random.default_rng(np.random.SeedSequence(list(seed)))
    out = np.empty((draws, len(windows)))
    k = 0
    while k < draws:
        bb = min(batch, draws - k)
        e = rng.standard_normal((bb, n))
        c = np.concatenate([np.zeros((bb, 1)), np.cumsum(e, axis=1)], axis=1)
        for j, m in enumerate(windows):
            out[k:k + bb, j] = np.abs(c[:, m:] - c[:, :-m]).max(axis=1) / np.sqrt(m)
        k += bb
    return out


def fluctuation_reference(null: npt.ArrayLike) -> tuple[list[FloatArray], IntArray]:
    """(each column sorted, ranks) with ranks[b, j] = #{i : null[i, j] >= null[b, j]}; computed once per null."""
    arr = np.asarray(null, dtype=float)
    draws = arr.shape[0]
    cols = [np.sort(arr[:, j]) for j in range(arr.shape[1])]
    ranks = np.empty(arr.shape, dtype=np.int64)
    for j, col in enumerate(cols):
        ranks[:, j] = draws - np.searchsorted(col, arr[:, j], side="left")
    return cols, ranks


def fluctuation_test(sups: Sequence[float], null: npt.ArrayLike, reference: tuple[list[FloatArray], IntArray], *,
                     alphas: tuple[float, ...] = (0.05, 0.10)) -> dict[str, Any]:
    """sups[j] = the observed sup |t| for the null's column j. Per column: p_j = (1 + #{null[:, j] >= sups[j]}) /
    (B + 1) and the critical values c_a with sup > c_a <=> p < a (which needs (B + 1) * a to be an integer, e.g.
    49,999 draws for 5% and 10%): c_a = sorted_j[B - k], k = round(a * (B + 1)) - 1. Search-adjusted, exact under
    exchangeability: the observed sups and the B draws are B + 1 exchangeable values; draw b's marginal p in column
    j is (ranks[b, j] + [sups[j] >= null[b, j]]) / (B + 1); min_p = min_j p_j; p = (1 + #{b : min_j p_bj <= min_p})
    / (B + 1). With one column it equals the marginal p; it is never below min_p."""
    arr = np.asarray(null, dtype=float)
    cols, ranks = reference
    draws = arr.shape[0]
    obs = np.asarray(sups, dtype=float)
    if obs.size != arr.shape[1]:
        raise ValueError("one observed sup per column of the null")
    critical: list[dict[str, float]] = []
    for col in cols:
        values = {}
        for a in alphas:
            k = round(a * (draws + 1)) - 1
            if not np.isclose(a * (draws + 1), k + 1):
                raise ValueError(f"(draws + 1) * {a} must be an integer for an exact critical value")
            values[str(a)] = float(col[draws - k])
        critical.append(values)
    marginal = (1 + (arr >= obs[None, :]).sum(axis=0)) / (draws + 1)
    draw_p = (ranks + (obs[None, :] >= arr)) / (draws + 1)
    min_p = float(marginal.min())
    adjusted = (1 + int((draw_p.min(axis=1) <= min_p).sum())) / (draws + 1)
    return {"marginal_p": [float(p) for p in marginal], "critical": critical, "min_p": min_p,
            "min_at": int(np.argmin(marginal)), "p_value": float(adjusted), "draws": int(draws)}


def critical_value_mc_se(sorted_col: npt.ArrayLike, alpha: float) -> float:
    """Monte Carlo standard error of the alpha critical value of a sorted null column: half the spread of the order
    statistics one binomial standard deviation of the count either side of it."""
    col = np.asarray(sorted_col, dtype=float)
    draws = col.size
    k = round(alpha * (draws + 1)) - 1
    i = draws - k
    step = round(float(np.sqrt(draws * alpha * (1 - alpha))))
    return float((col[min(i + step, draws - 1)] - col[max(i - step, 0)]) / 2.0)


def basel_zone(exceed_last_250: int) -> str:
    """Basel traffic-light zone for 250 observations of a 99% VaR (green <= 4, yellow 5-9, red >= 10)."""
    return "green" if exceed_last_250 <= 4 else ("yellow" if exceed_last_250 <= 9 else "red")


def adaptive_conformal(log_resid: npt.ArrayLike, alpha: float = 0.20, gamma: float = 0.005,
                       window: int = 156, warmup: int = 52) -> dict[str, Any]:
    """Adaptive conformal inference (Gibbs and Candes 2021) on log forecast errors.

    At week i the interval for log(actual) - log(forecast) is the [a_i/2, 1 - a_i/2] quantile range
    of the previous `window` errors (all already observed); a_i moves by gamma * (alpha - miss_i)
    so long-run coverage converges to 1 - alpha even when the error distribution drifts.
    Returns per-week bounds (NaN during warm-up), hits, and the final state for live use.
    """
    e = np.asarray(log_resid, float)
    n = e.size
    lo, hi, hit = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    a = alpha
    for i in range(warmup, n):
        past = e[max(0, i - window): i]
        a_eff = min(max(a, 1e-3), 1 - 1e-3)
        lo[i], hi[i] = np.quantile(past, a_eff / 2), np.quantile(past, 1 - a_eff / 2)
        miss = not (lo[i] <= e[i] <= hi[i])
        hit[i] = 0.0 if miss else 1.0
        a = a + gamma * (alpha - miss)
    a_eff = min(max(a, 1e-3), 1 - 1e-3)
    past = e[max(0, n - window): n]
    return {"lo": lo, "hi": hi, "hit": hit, "alpha_next": float(a_eff),
            "next_lo": float(np.quantile(past, a_eff / 2)), "next_hi": float(np.quantile(past, 1 - a_eff / 2))}


def delayed_aci(log_resid: npt.ArrayLike, delay: int = 5, alpha: float = 0.20, gamma: float = 0.005,
                window: int = 780, warmup: int = 260) -> dict[str, Any]:
    """Adaptive conformal inference (Gibbs-Candes 2021) when an outcome is only observed `delay` rows
    after its forecast (here: 5 sessions). The interval at row i uses the errors of rows <= i - delay
    and the coverage feedback that has arrived by then (the miss of row i - delay)."""
    e = np.asarray(log_resid, float)
    n = e.size
    lo, hi, hit = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    a = alpha
    for i in range(n):
        j = i - delay                                          # newest matured row
        if j >= warmup and np.isfinite(hit[j]):
            a = a + gamma * (alpha - (1.0 - hit[j]))
        if j + 1 < warmup:
            continue
        past = e[max(0, j + 1 - window): j + 1]
        a_eff = min(max(a, 1e-3), 1 - 1e-3)
        lo[i], hi[i] = np.quantile(past, a_eff / 2), np.quantile(past, 1 - a_eff / 2)
        hit[i] = 1.0 if lo[i] <= e[i] <= hi[i] else 0.0
    a_eff = min(max(a, 1e-3), 1 - 1e-3)
    past = e[max(0, n - window): n]
    return {"lo": lo, "hi": hi, "hit": hit, "alpha_next": float(a_eff),
            "next_lo": float(np.quantile(past, a_eff / 2)), "next_hi": float(np.quantile(past, 1 - a_eff / 2))}


# --- Scoring the whole predictive distribution (ROADMAP 2.4) ----------------------------------------------------------


def pinball_loss(actual: npt.ArrayLike, quantile: npt.ArrayLike, tau: float) -> FloatArray:
    """rho_tau(y - q) = (y - q)(tau - 1{y < q}), elementwise (broadcast): half the quantile score QS_tau of Gneiting
    and Ranjan (2011), zero at y = q and minimised in expectation by the true tau-quantile."""
    u = np.asarray(actual, dtype=float) - np.asarray(quantile, dtype=float)
    return cast("FloatArray", u * (tau - (u < 0)))


def crps_from_quantiles(actual: npt.ArrayLike, quantiles: npt.ArrayLike, taus: npt.ArrayLike,
                        weights: npt.ArrayLike | None = None) -> FloatArray:
    """Per-row grid CRPS sum_k w_k QS_{tau_k}(q_k, y), QS_tau = 2 pinball_tau (Gneiting and Ranjan 2011): actual (n,)
    or a scalar, quantiles (n, K) or (K,), taus (K,). Default w_k = 1/K, which on a grid symmetric about 1/2 is the
    weighted interval score of Bracher, Ray, Gneiting and Reich (2021).

    Raises ValueError unless every w_k >= 0, sum(w) = 1 and sum(w tau) = 1/2 (to 1e-10): the two conditions under
    which a point forecast (every q_k = x) scores its absolute error exactly, since sum_k w_k 2 (y - x)(tau_k -
    1{y < x}) = 2 (y - x)(1/2 - 1{y < x}) = |y - x|; negative weights would make the score improper. A finite grid is
    not the CRPS of the distribution: under a correct Gaussian forecast the equal-weight K = 19 grid tau = 0.05,
    ..., 0.95 reads 4.83% above the closed form, and the grid [h, 1 - h] in steps of h converges to it as h -> 0."""
    y = np.asarray(actual, dtype=float)
    q = np.asarray(quantiles, dtype=float)
    t = np.asarray(taus, dtype=float)
    k = t.size
    if t.ndim != 1 or k == 0 or q.ndim == 0 or q.shape[-1] != k or not np.all((t > 0) & (t < 1)):
        raise ValueError("crps_from_quantiles: one quantile per level, every level inside (0, 1)")
    w = np.full(k, 1.0 / k) if weights is None else np.asarray(weights, dtype=float)
    if (w.shape != (k,) or bool(np.any(w < 0)) or abs(float(np.sum(w)) - 1.0) > 1e-10
            or abs(float(np.sum(w * t)) - 0.5) > 1e-10):
        raise ValueError("crps_from_quantiles: the weights must be non-negative with sum(w) = 1 and sum(w tau) = 1/2")
    u = y[..., None] - q
    pinball = u * (t - (u < 0))
    if weights is None:
        return 2.0 * np.mean(pinball, axis=-1)
    return 2.0 * np.sum(pinball * w, axis=-1)


def rearrange(quantiles: npt.ArrayLike) -> tuple[FloatArray, BoolArray]:
    """Each row (the last axis) sorted, the monotone rearrangement of Chernozhukov, Fernandez-Val and Galichon (2010),
    and whether the row crossed (some q_{k+1} < q_k) before sorting.

    Sorting never raises the equal-weight grid score: sum_k QS_{tau_k}(q_k, y) = 2 sum_k 1{y < q_k}(q_k - y)
    - 2 sum_k tau_k q_k + 2 y sum_k tau_k, where the first sum depends only on the multiset of the q_k and
    sum_k tau_k q_k is largest when the q_k are sorted like the increasing tau_k (the rearrangement inequality).
    With unequal weights the first sum is not order-free, and the guarantee is lost."""
    q = np.asarray(quantiles, dtype=float)
    crossed = np.any(np.diff(q, axis=-1) < 0, axis=-1)
    return cast("FloatArray", np.sort(q, axis=-1)), crossed


def trailing_quantiles(log_resid: npt.ArrayLike, taus: npt.ArrayLike, *, delay: int, window: int,
                       warmup: int) -> dict[str, Any]:
    """Predictive quantiles from trailing matured log residuals e. Row i, with j = i - delay the newest matured row:
    past = e[max(0, j + 1 - window) : j + 1], the slice delayed_aci uses, once j + 1 >= warmup; without
    delayed_aci's adaptive step, so q[i] = np.quantile(past, taus) (linear interpolation) at fixed levels (at
    tau = alpha/2 and 1 - alpha/2 they equal delayed_aci(..., gamma=0)'s lo and hi bit for bit). Only rows <= i -
    delay enter row i.

    Returns {"q": (n, K), NaN before the warm-up; "rank": (n,) r_i = #{past < e_i}, -1 before the warm-up;
    "n_past": (n,) the window's length, 0 before; "pit": (n,) the mid-rank PIT (r_i + 1/2)/(n_past + 1), NaN
    before}. Under the 5-session overlap this PIT re-centres itself (each week's overlapping neighbours enter the
    later windows) and |Phi^-1(pit)| <= Phi^-1(1 - 1/(2 (window + 1))), so its Berkowitz statistics need the
    simulated null of rank_pit_null, not chi2(3)."""
    e = np.asarray(log_resid, dtype=float)
    t = np.asarray(taus, dtype=float)
    if warmup < 1 or window < 1 or delay < 1:
        raise ValueError("trailing_quantiles: the warm-up and the window need a residual, and an outcome matures "
                         "at least one row after its forecast")
    n = e.size
    q = np.full((n, t.size), np.nan)
    rank = np.full(n, -1, dtype=np.int64)
    n_past = np.zeros(n, dtype=np.int64)
    for i in range(n):
        j = i - delay                                          # newest matured row
        if j + 1 < warmup:
            continue
        past = e[max(0, j + 1 - window): j + 1]
        q[i] = np.quantile(past, t)
        rank[i] = np.count_nonzero(past < e[i])
        n_past[i] = past.size
    pit = np.full(n, np.nan)
    ok = rank >= 0
    pit[ok] = (rank[ok] + 0.5) / (n_past[ok] + 1)
    return {"q": q, "rank": rank, "n_past": n_past, "pit": pit}


def _trailing_ranks(e: FloatArray, first: int, *, window: int, delay: int) -> tuple[IntArray, IntArray]:
    """trailing_quantiles' rank and n_past for rows first..n-1, vectorised: rows whose window is still growing compare
    with e[:window] under a mask, the others with rows of sliding_window_view (views, no copies)."""
    n = e.size
    rows = np.arange(first, n)
    n_past = np.minimum(rows - delay + 1, window)
    rank = np.empty(rows.size, dtype=np.int64)
    full0 = max(first, window + delay - 1)                    # first row whose window is full
    grow = rows[rows < full0]
    if grow.size:
        width = min(window, n)
        mask = np.arange(width)[None, :] < (grow - delay + 1)[:, None]
        rank[: grow.size] = np.count_nonzero((e[None, :width] < e[grow][:, None]) & mask, axis=1)
    if full0 < n:
        win = np.lib.stride_tricks.sliding_window_view(e, window)[full0 - delay + 1 - window: n - delay + 1 - window]
        rank[grow.size:] = np.count_nonzero(win < e[full0:][:, None], axis=1)
    return cast("IntArray", rank), cast("IntArray", n_past)


def _rank_deciles(e: FloatArray, rows: IntArray, rank: IntArray, n_past: IntArray, taus: FloatArray, *,
                  window: int, delay: int) -> IntArray:
    """For each row i, #{k : np.quantile(past_i, taus[k]) <= e_i} (the PIT histogram's bin when taus are the deciles),
    from the rank r_i = #{past_i < e_i}: with h = (n_past - 1) tau, np.quantile's linear position, e_i lies above the
    quantile when r_i >= floor(h) + 2 and below it when r_i <= floor(h) (no ties); the one rank in between is
    compared with np.quantile itself."""
    floor_h = np.floor((n_past - 1)[:, None] * taus[None, :])
    above = rank[:, None] >= floor_h + 2
    for r_idx, k in zip(*np.nonzero(rank[:, None] == floor_h + 1)):
        i = int(rows[r_idx])
        past = e[max(0, i - delay + 1 - window): i - delay + 1]
        above[r_idx, k] = bool(np.quantile(past, taus[k]) <= e[i])
    return cast("IntArray", above.sum(axis=1).astype(np.int64))


def rank_pit_null(n: int, first: int, grid: npt.ArrayLike, *, window: int, delay: int, warmup: int, draws: int,
                  seed: Sequence[int], bin_draws: int) -> dict[str, Any]:
    """Simulated null of the trailing-rank PIT (trailing_quantiles) under the `delay`-session overlap, on a layout of n
    rows evaluated from row `first`, with grid[i] the sampling grid of row i. Draw k: numpy default_rng(SeedSequence(
    [*seed, k])) gives n + delay i.i.d. N(0, 1) returns x; row t's target is e_t = 1/2 log of the mean of
    x_{t+1}^2, ..., x_{t+delay}^2 (overlapping, as the real RV5 targets); the mid-rank PIT is rebuilt exactly (window,
    delay, warm-up) and Berkowitz's LR3 computed on each grid's rows in date order. Ranks do not depend on the
    marginal, so one null serves every model with this construction. Vectorised (sliding_window_view, no copies;
    ~5 ms per draw on the real layout).

    Returns {"lr": (draws, G) the per-grid LR3, grids in sorted label order; "exceed": (draws,) the days outside the
    trailing window's range (rank 0 or n_past); and, over the first bin_draws draws, the shares (%) of the 10 PIT
    decile bins: "share_mean_rank" and "share_sd_rank" (ddof 1) for this construction (bins from the rank,
    _rank_deciles: e_i against np.quantile of its window at tau = 0.1, ..., 0.9), and "share_sd_exact" for an exact
    PIT (chi2 cdf of the same targets)}."""
    labels = np.asarray(grid)
    if labels.shape != (n,):
        raise ValueError("rank_pit_null: one grid label per row")
    if delay < 1 or warmup < 1 or first - delay + 1 < warmup or first >= n:
        raise ValueError("rank_pit_null: the PIT is undefined before the warm-up (and needs delay >= 1)")
    if not 2 <= bin_draws <= draws:
        raise ValueError("rank_pit_null: 2 <= bin_draws <= draws")
    sub = labels[first:]
    groups = [np.flatnonzero(sub == g) for g in np.unique(sub)]
    rows = np.arange(first, n)
    deciles = np.arange(1, 10) / 10.0
    lr = np.empty((draws, len(groups)))
    exceed = np.empty(draws, dtype=np.int64)
    share_rank = np.empty((bin_draws, 10))
    share_exact = np.empty((bin_draws, 10))
    for k in range(draws):
        x = np.random.default_rng(np.random.SeedSequence([*seed, k])).standard_normal(n + delay) ** 2
        c = np.concatenate(([0.0], np.cumsum(x)))
        v = (c[delay + 1: delay + 1 + n] - c[1: 1 + n]) / delay
        e = 0.5 * np.log(v)
        rank, n_past = _trailing_ranks(e, first, window=window, delay=delay)
        z = special.ndtri((rank + 0.5) / (n_past + 1))
        lr[k] = [_lr3(z[idx])[0] for idx in groups]
        exceed[k] = np.count_nonzero((rank == 0) | (rank == n_past))
        if k < bin_draws:
            b = _rank_deciles(e, rows, rank, n_past, deciles, window=window, delay=delay)
            share_rank[k] = np.bincount(b, minlength=10) / b.size * 100
            ux = stats.chi2.cdf(delay * v[first:], delay)
            bx = np.minimum((ux * 10).astype(np.int64), 9)
            share_exact[k] = np.bincount(bx, minlength=10) / bx.size * 100
    return {"lr": lr, "exceed": exceed, "share_sd_rank": share_rank.std(axis=0, ddof=1),
            "share_sd_exact": share_exact.std(axis=0, ddof=1), "share_mean_rank": share_rank.mean(axis=0)}


def sample_pit(sorted_draws: npt.ArrayLike, actual: float) -> float:
    """PIT of `actual` against a sorted simulated predictive sample of N draws: the Hazen CDF, (k + 1/2)/N at the k-th
    order statistic (k = 0..N-1), linear in between and clamped to [1/(2N), 1 - 1/(2N)] outside. Continuous in its
    inputs, so a last-bit change in the simulation cannot flip a count."""
    s = np.asarray(sorted_draws, dtype=float)
    if s.ndim != 1 or s.size == 0 or bool(np.any(np.diff(s) < 0)):
        raise ValueError("sample_pit: the draws must be a non-empty sorted 1-D sample")
    return float(np.interp(actual, s, (np.arange(s.size) + 0.5) / s.size))


def _lr3(z: FloatArray) -> tuple[float, float, float, float]:
    """Berkowitz's LR3 by the conditional Gaussian AR(1) likelihood: OLS of z_t on (1, z_{t-1}) in closed form (sums,
    no BLAS), s^2 = mean squared residual, LR = 2 (ll_1 - ll_0) = -m (log s^2 + 1) + sum z_t^2 over the m = n - 1
    conditional terms (the 2 pi terms cancel). Returns (lr, intercept, slope, s^2)."""
    y, x = z[1:], z[:-1]
    m = y.size
    xm, ym = float(x.mean()), float(y.mean())
    dx = x - xm
    sxx = float(np.sum(dx * dx))
    if sxx <= 0:
        raise ValueError("Berkowitz: the normal scores have no variance")
    rho = float(np.sum(dx * (y - ym))) / sxx
    c = ym - rho * xm
    resid = y - c - rho * x
    s2 = float(np.sum(resid * resid)) / m
    return float(-m * (np.log(s2) + 1.0) + np.sum(y * y)), c, rho, s2


def berkowitz_test(z: npt.ArrayLike, *, null_lr: npt.ArrayLike | None = None) -> dict[str, Any]:
    """Berkowitz (2001) LR3 on normal scores z = Phi^-1(PIT) in time order (takes z, not u): H0 z_t i.i.d. N(0, 1)
    against a Gaussian AR(1), by the conditional likelihood (closed-form OLS of z_t on (1, z_{t-1})). Run it on
    non-overlapping observations: overlapping PITs are autocorrelated under a correct forecast. p from chi2(3), or,
    when a simulated null of the statistic is given, (1 + #{null_lr >= lr}) / (len(null_lr) + 1) with the chi2(3) p
    kept as "p_chi2" (context). Returns {"lr", "p_value", "reference", "n" (the scores), "mu" = c/(1 - rho),
    "sigma" = sqrt(s^2/(1 - rho^2)), "rho"}."""
    s = np.asarray(z, dtype=float)
    if s.ndim != 1 or s.size < 4 or not bool(np.all(np.isfinite(s))):
        raise ValueError("berkowitz_test: at least four finite normal scores (z, not u)")
    lr, c, rho, s2 = _lr3(s)
    p_chi2 = float(stats.chi2.sf(lr, 3))
    out: dict[str, Any] = {"lr": lr, "p_value": p_chi2, "reference": "chi2(3)", "n": int(s.size),
                           "mu": c / (1.0 - rho), "sigma": float(np.sqrt(s2 / (1.0 - rho * rho))), "rho": rho}
    if null_lr is not None:
        ref = np.asarray(null_lr, dtype=float)
        if ref.ndim != 1 or ref.size == 0:
            raise ValueError("berkowitz_test: the simulated null is a non-empty 1-D sample of the statistic")
        out.update(p_value=(1 + int(np.count_nonzero(ref >= lr))) / (ref.size + 1), reference="simulated null",
                   p_chi2=p_chi2)
    return out


_LOG_2PI = float(np.log(2.0 * np.pi))


def berkowitz_censored(z: npt.ArrayLike, lower: float = 0.05, upper: float = 0.95) -> dict[str, Any]:
    """Doubly censored normal LR, adapted from Berkowitz's (2001) censored likelihood (which observes the tail beyond a
    VaR; this form does the reverse): z is observed only inside [Phi^-1(lower), Phi^-1(upper)] and only counted
    below and above it (pass -inf / +inf or any value outside). H0 i.i.d. N(0, 1) against i.i.d. N(mu, s^2), LR ~
    chi2(2): it tests the distribution between the two quantiles and the two tail masses, not independence. The
    likelihood uses the interior's sufficient statistics (count, sum z, sum z^2) and special.log_ndtr for the tails;
    Nelder-Mead in (mu, log s) from (0, 0), xatol = fatol = 1e-10, at most 4000 iterations (deterministic; raises
    RuntimeError if it does not converge). Returns {"lr", "p_value", "reference", "n", "below", "above", "mu",
    "sigma"}."""
    s = np.asarray(z, dtype=float)
    if s.ndim != 1 or s.size == 0 or bool(np.any(np.isnan(s))) or not 0.0 < lower < upper < 1.0:
        raise ValueError("berkowitz_censored: non-empty scores without NaN, and 0 < lower < upper < 1")
    cl, ch = float(special.ndtri(lower)), float(special.ndtri(upper))
    inner = s[(s >= cl) & (s <= ch)]
    below, above = int(np.count_nonzero(s < cl)), int(np.count_nonzero(s > ch))
    m, s1, s2 = inner.size, float(np.sum(inner)), float(np.sum(inner * inner))

    def nll(theta: FloatArray) -> float:
        mu, log_sd = float(theta[0]), float(theta[1])
        sd = float(np.exp(log_sd))
        out = 0.5 * (m * _LOG_2PI + (s2 - 2.0 * mu * s1 + m * mu * mu) / (sd * sd)) + m * log_sd
        if below:
            out -= below * float(special.log_ndtr((cl - mu) / sd))
        if above:
            out -= above * float(special.log_ndtr((mu - ch) / sd))
        return out

    fit = optimize.minimize(nll, np.zeros(2), method="Nelder-Mead",
                            options={"xatol": 1e-10, "fatol": 1e-10, "maxiter": 4000})
    if not fit.success:
        raise RuntimeError(f"berkowitz_censored: Nelder-Mead did not converge: {fit.message}")
    lr = max(0.0, 2.0 * (nll(np.zeros(2)) - float(fit.fun)))
    return {"lr": lr, "p_value": float(stats.chi2.sf(lr, 2)), "reference": "chi2(2)", "n": int(s.size),
            "below": below, "above": above, "mu": float(fit.x[0]), "sigma": float(np.exp(fit.x[1]))}


def bonferroni_min(p_values: npt.ArrayLike) -> float:
    """min(1, k * the smallest of k p-values): the test that at least one of k nulls is false, valid however the k
    tests depend on one another (here the five sampling grids, disjoint weeks of one series)."""
    p = np.asarray(p_values, dtype=float)
    if p.size == 0 or bool(np.any(np.isnan(p))):
        raise ValueError("bonferroni_min: at least one p-value, none NaN")
    return float(min(1.0, p.size * float(p.min())))


def cauchy_combination(p_values: npt.ArrayLike) -> float:
    """Liu and Xie (2020), equal weights: T = mean_i tan((1/2 - p_i) pi), p = P(Cauchy > T). Close to exact under
    arbitrary dependence for small p-values (context beside bonferroni_min). A p-value below 1e-16 enters as
    1/(p pi), its tangent's limit, as the paper recommends; any p = 0 gives 0."""
    p = np.asarray(p_values, dtype=float)
    if p.size == 0 or not bool(np.all((p >= 0) & (p <= 1))):
        raise ValueError("cauchy_combination: at least one p-value, each in [0, 1]")
    if bool(np.any(p == 0)):
        return 0.0
    t = np.where(p < 1e-16, 1.0 / (p * np.pi), np.tan((0.5 - p) * np.pi))
    return float(stats.cauchy.sf(float(np.mean(t))))
