"""Forecast-evaluation statistics shared by the production forecaster and the research lab.

Pure functions with no project imports (numpy/scipy; statsmodels and arch are imported lazily by the
two functions that need them), each checked against cases with a known answer in tests/:

  - newey_west_lrv / hac_z      Bartlett-kernel long-run variance and HAC z-statistics.
  - qlike / mse_var             The two volatility losses that rank forecasts consistently when the
                                realized-vol proxy is noisy (Patton 2011).
  - dm_test                     Diebold-Mariano (1995) with Newey-West variance and the
                                Harvey-Leybourne-Newbold (1997) small-sample correction.
  - mincer_zarnowitz            Unbiasedness regression with a HAC Wald test.
  - model_confidence_set        Hansen-Lunde-Nason (2011) MCS via arch.bootstrap.
  - kupiec / christoffersen /   VaR backtests: unconditional coverage (Kupiec 1995), independence and
    dq_test / basel_zone        conditional coverage (Christoffersen 1998), the Dynamic Quantile test
                                (Engle-Manganelli 2004) and the Basel traffic light.
  - adaptive_conformal /        Adaptive conformal inference intervals (Gibbs and Candes 2021), and
    delayed_aci                 the same when each outcome is only observed `delay` rows later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd
from scipy import stats

if TYPE_CHECKING:
    import numpy.typing as npt

    FloatArray = npt.NDArray[np.float64]


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


def dm_test(loss_a: npt.ArrayLike, loss_b: npt.ArrayLike, horizon: int = 1) -> dict[str, Any]:
    """Diebold-Mariano test of equal expected loss, d = loss_a - loss_b (positive: b is better).

    Newey-West long-run variance (automatic bandwidth) and the Harvey-Leybourne-Newbold (1997)
    small-sample factor, compared with Student-t(n-1). Two-sided p-value.
    """
    diff = np.asarray(loss_a, float) - np.asarray(loss_b, float)
    n = diff.size
    lrv, lags = newey_west_lrv(diff)
    if n < 3 or lrv <= 0:
        return {"stat": 0.0, "p_value": 1.0, "mean_diff": float(diff.mean()) if n else 0.0, "lags": lags}
    dm = float(diff.mean() / np.sqrt(lrv / n))
    hln = np.sqrt((n + 1 - 2 * horizon + horizon * (horizon - 1) / n) / n)
    stat = dm * hln
    return {"stat": round(stat, 3), "p_value": float(2 * stats.t.sf(abs(stat), df=n - 1)),
            "mean_diff": float(diff.mean()), "lags": int(lags)}


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


def model_confidence_set(losses: pd.DataFrame, block: int = 10, size: float = 0.10,
                         reps: int = 5000, seed: int = 20261020) -> dict[str, Any]:
    """Hansen-Lunde-Nason Model Confidence Set: range statistic, arch's stationary bootstrap with
    mean block length `block`. Returns MCS p-values and the set retained at confidence 1 - size."""
    from arch.bootstrap import MCS

    mcs = MCS(losses, size=size, reps=reps, block_size=block, method="R", seed=seed)
    mcs.compute()
    pvals = mcs.pvalues["Pvalue"].to_dict()
    return {"included": sorted(mcs.included, key=lambda m: -pvals[m]),
            "p_values": {m: round(float(p), 4) for m, p in pvals.items()}}


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
