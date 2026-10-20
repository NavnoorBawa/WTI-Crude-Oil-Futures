#!/usr/bin/env python3
"""
Term-structure CARRY test for WTI — rebuilt on roll-free futures returns.

Carry (backwardation vs contango) is one of the most robust commodity factors cross-sectionally, so
it was the most promising remaining free-data directional signal after the price-momentum direction
model turned out to be a leak. This script tests it for WTI time-series timing.

Correction (2026-09): the first version measured forward returns on Yahoo's CL=F and described that
series as back-adjusted. It is not: CL=F equals EIA's unadjusted contract-1 price on 97.7% of
2000-2024 days (median difference 2e-8, same -37.63 print on 2020-04-20). A 21-day forward return
on it almost always crosses a monthly roll, and the roll gap is ~(C2 - C1)/C1 = -carry. The test
therefore subtracted the very carry it was trying to detect: measured on the same dates (the
artifact's roll_bias_of_original_method block), the contamination is about the size of the carry
itself and strongly negatively correlated with it. That artifact is what produced the old "data
leans the opposite way (contango -> higher returns)" observation. The first version also stopped in 2004 only because the EIA API's
5000-row page limit silently truncated the history (it sorted descending and never paginated).

Design (leak-free, no roll gap can enter a return):
  - DATA: EIA's NYMEX WTI futures prices, contract 1 and 2 (RCLC1/RCLC2), 1985-2024. EIA stopped
    publishing the series on 2024-04-05, so the history is frozen and is committed to
    data/eia_wti_futures_curve.json; `--refresh` re-downloads it from EIA's public, keyless files.
  - CONTRACT CYCLE: last trade dates follow the CME rule on the exchange calendar
    (backend/contract_calendar.py). EIA's rows cannot serve as that calendar: they miss ~400
    business days and repeat prices on some holidays, which misplaced 70 expiries.
  - SIGNAL: carry = (C1 - C2) / C2 on the first trading day after each expiry (>0 = backwardation).
  - RETURN: hold the second-nearby contract from the next trading day until one day before the next
    expiry. It is one contract throughout (a one-day margin on each side absorbs any roll-date
    ambiguity), so the return is a real futures holding return with no roll gap, and holding the
    second contract avoids expiry-week delivery squeezes such as April 2020.
  - RULE (a priori, unchanged): long when carry is above the median of all PRIOR months' carry,
    else flat. Monthly windows do not overlap, so every earlier observation is fully matured.

Labels and inference (2026-10):
  - The rule's two groups are "carry above / below the median of prior months", not "backwardated /
    contango": the running median is not zero, so the two splits disagree in some months
    (sign_split.*.share_agreeing_with_median_rule_pct). Each sample block reports the split under precise names
    (mean_ret_above_median_carry_pct ...). The old keys (mean_ret_backwardated_pct,
    mean_ret_contango_pct, n_backwardated) are kept with the same values for existing consumers and
    are documented as aliases in `field_notes`. The true sign split (carry > 0 vs <= 0) is the
    robustness block `sign_split`.
  - `power_post_2007`: the minimum detectable effect of the post-2007 sample, and its power to
    detect the pre-2007 effect, for both the return spread and the rank correlation, each at the
    level its test is run (one-sided for the spread, two-sided for the rank correlation).
    `regime_change_test` asks directly whether the effect differs before and after 2007.
  - `sharpe_difference` (in each sample block): timed minus buy-and-hold Sharpe, tested with the
    Ledoit-Wolf (2008) delta method on a Newey-West covariance and with a stationary block
    bootstrap (Politis-Romano 1994) of the paired monthly returns.

Usage:
    python -m backend.carry_signal_test              # run from the committed curve, write artifact
    python -m backend.carry_signal_test --refresh    # re-download the curve first (needs xlrd)
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binom, norm, spearmanr, ttest_ind

try:
    from .contract_calendar import is_business_day, last_trade_date
except ImportError:  # Direct invocation: python backend/carry_signal_test.py
    from contract_calendar import is_business_day, last_trade_date

ROOT = Path(__file__).resolve().parent.parent
CURVE_PATH = ROOT / "data" / "eia_wti_futures_curve.json"
ARTIFACT_PATH = ROOT / "data" / "carry_signal_test.json"
EIA_HISTORY_URL = "https://www.eia.gov/dnav/pet/hist_xls/{series}d.xls"
SERIES = {"c1": "RCLC1", "c2": "RCLC2"}

MIN_TRAIN_MONTHS = 60       # five years of prior months before the first out-of-sample decision
MIN_HOLD_DAYS = 10          # skip truncated cycles (only possible at the ends of the sample)
REGIME_SPLIT = "2007-01-01"  # pre/post: the shale era and the financialization of commodities

SEED = 20261020             # the Sharpe bootstrap is seeded: the artifact is deterministic
SHARPE_BOOT_REPS = 10000
SHARPE_BOOT_BLOCK = 6       # mean block length in months for the stationary bootstrap
POWER_ALPHA = 0.05          # one-sided for the spread test, two-sided (alpha / 2 a tail) for the rank test
POWER_TARGET = 0.80
# Var(atanh(Spearman rho)) ~ 1.06 / (n - 3) (Fieller, Hartley and Pearson 1957).
SPEARMAN_FISHER_VAR = 1.06
# Precise names for the rule's split, and the legacy keys kept for existing consumers.
LEGACY_ALIASES = {
    "mean_ret_backwardated_pct": "mean_ret_above_median_carry_pct",
    "mean_ret_contango_pct": "mean_ret_below_median_carry_pct",
    "n_backwardated": "n_above_median_carry",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Only ever read the fixed EIA URL; never follow a redirect to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_curve() -> pd.DataFrame:
    """Contract-1/2 daily settlements from EIA's public history workbooks (no API key)."""
    try:
        import xlrd  # noqa: F401 - pandas needs it for legacy .xls
    except ImportError as exc:
        raise SystemExit("--refresh needs the optional 'xlrd' package: pip install xlrd") from exc
    opener = urllib.request.build_opener(_NoRedirect)
    cols = {}
    for name, series in SERIES.items():
        # The scheme, host and path are fixed; only the series name (a constant above) varies.
        with opener.open(EIA_HISTORY_URL.format(series=series), timeout=60) as resp:  # nosec B310
            raw = pd.read_excel(io.BytesIO(resp.read()), sheet_name="Data 1", skiprows=2)
        raw.columns = ["date", "value"]
        cols[name] = pd.Series(raw["value"].astype(float).to_numpy(), index=pd.to_datetime(raw["date"]))
    return pd.DataFrame(cols).dropna()


def save_curve(curve: pd.DataFrame, path: Path = CURVE_PATH) -> None:
    payload = {
        "source": "EIA NYMEX WTI futures, contract 1 and 2 (RCLC1, RCLC2), $/bbl daily settlement",
        "source_url": EIA_HISTORY_URL.format(series="RCLC1"),
        "note": "EIA discontinued these series after 2024-04-05; this history is final.",
        "fetched_at": date.today().isoformat(),
        "columns": ["date", "c1", "c2"],
        "rows": [[d.strftime("%Y-%m-%d"), round(float(a), 4), round(float(b), 4)]
                 for d, a, b in zip(curve.index, curve["c1"], curve["c2"])],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def load_curve(path: Path = CURVE_PATH) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    df = pd.DataFrame(payload["rows"], columns=payload["columns"])
    df.index = pd.to_datetime(df.pop("date"))
    return df.astype(float)


def clean_curve(curve: pd.DataFrame) -> pd.DataFrame:
    """Real settlements only, date-sorted.

    Drops non-positive prints (the 2020-04-20 -37.63 expiry) and rows EIA carries on exchange
    holidays (2018-2019 repeat the previous settlement on e.g. Thanksgiving and Christmas).
    """
    curve = curve[(curve["c1"] > 0) & (curve["c2"] > 0)].sort_index()
    return curve[[is_business_day(d) for d in curve.index.date]]


def expiry_positions(curve: pd.DataFrame) -> list[int]:
    """Row positions in a cleaned curve of each contract-1 last trade date (the day before a roll).

    Last trade dates come from the exchange calendar, not from the curve's own rows: EIA's history
    misses ~400 business days (mostly before 2007), and counting back over those holes put the
    expiry up to ten days early in 70 months, so a "same-contract" hold could span a roll. When EIA
    lacks the expiry day itself, the last row before it stands in (the new contract is front from
    the next row either way).
    """
    days = curve.index
    first, last = days[0].date(), days[-1].date()
    expiries = []
    y, m = first.year, first.month
    while (y, m) <= (last.year + 1, last.month):
        ltd = last_trade_date(y, m)
        if first < ltd < last:
            expiries.append(int(np.searchsorted(days, pd.Timestamp(ltd), side="right")) - 1)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(set(expiries))


def monthly_cycles(curve: pd.DataFrame) -> pd.DataFrame:
    """One row per contract cycle: carry signal and the same-contract holding return that follows.

    Columns: signal date, entry/exit dates, carry, ret (second-nearby, roll-free) and
    ret_unadjusted (the original method: unadjusted contract-1 return over 21 trading days, kept
    only to measure the roll bias it introduced).
    """
    curve = clean_curve(curve)
    days = curve.index
    expiries = expiry_positions(curve)

    c1, c2 = curve["c1"].to_numpy(), curve["c2"].to_numpy()
    rows = []
    for k0, k1 in zip(expiries[:-1], expiries[1:]):
        signal, entry, exit_ = k0 + 1, k0 + 2, k1 - 1
        if exit_ - entry < MIN_HOLD_DAYS:
            continue
        horizon = min(entry + 21, len(days) - 1)
        rows.append({
            "signal_date": days[signal],
            "entry_date": days[entry],
            "exit_date": days[exit_],
            "carry": (c1[signal] - c2[signal]) / c2[signal],
            "ret": c2[exit_] / c2[entry] - 1.0,
            "ret_unadjusted": c1[horizon] / c1[entry] - 1.0,
        })
    return pd.DataFrame(rows).set_index("signal_date")


def walk_forward(cycles: pd.DataFrame, min_train: int = MIN_TRAIN_MONTHS) -> pd.DataFrame:
    """Expanding-median rule: long when this month's carry beats the median of all prior months."""
    carry = cycles["carry"].to_numpy()
    long = [carry[i] > np.median(carry[:i]) for i in range(min_train, len(cycles))]
    out = cycles.iloc[min_train:].copy()
    out["long"] = long
    return out


def _sharpe_monthly(r: np.ndarray) -> float:
    r = np.asarray(r, dtype=float)
    sd = float(np.std(r, ddof=1)) if r.size > 1 else 0.0
    return round(float(np.mean(r)) / sd * np.sqrt(12.0), 2) + 0.0 if sd > 0 else 0.0


def _hc3_slope_t(x: np.ndarray, y: np.ndarray) -> float:
    """t-statistic of the OLS slope of y on x with HC3 heteroskedasticity-robust errors.

    HC3 scales each squared residual by 1 / (1 - leverage)^2. HC1 is badly anti-conservative at
    high-leverage points, and carry has them: April 2020 (carry -33%, return +48%) alone gave the
    post-2007 slope an HC1 t of -4.0 against -1.4 under HC3 (Long & Ervin 2000).
    """
    X = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    xtx_inv = np.linalg.inv(X.T @ X)
    leverage = np.einsum("ij,jk,ik->i", X, xtx_inv, X)
    meat = (X.T * (resid / (1.0 - leverage)) ** 2) @ X
    cov = xtx_inv @ meat @ xtx_inv
    return float(beta[1] / np.sqrt(cov[1, 1]))


def _hac_cov(y: np.ndarray, lags: int | None = None) -> tuple[np.ndarray, int]:
    """Newey-West (Bartlett) long-run covariance of the columns of y, with the automatic bandwidth
    floor(4 (T/100)^(2/9)) used by backend/forecast_eval.newey_west_lrv."""
    y = np.asarray(y, dtype=float)
    t = y.shape[0]
    if lags is None:
        lags = int(np.floor(4.0 * (t / 100.0) ** (2.0 / 9.0)))
    e = y - y.mean(axis=0)
    cov = e.T @ e / t
    for k in range(1, lags + 1):
        gamma = e[k:].T @ e[:-k] / t
        cov += (1.0 - k / (lags + 1.0)) * (gamma + gamma.T)
    return cov, lags


def ledoit_wolf_sharpe_test(r1: np.ndarray, r2: np.ndarray) -> dict:
    """Ledoit-Wolf (2008) test of equal Sharpe ratios for two paired return series.

    Delta method on v = (mu1, mu2, E[r1^2], E[r2^2]) with a HAC (Newey-West) covariance of the
    moment series, so non-normal, autocorrelated and cross-correlated returns are allowed. Returns
    the per-period difference SR1 - SR2, its standard error, z and the two-sided p-value.
    """
    r1, r2 = np.asarray(r1, dtype=float), np.asarray(r2, dtype=float)
    mu1, mu2 = r1.mean(), r2.mean()
    g1, g2 = np.mean(r1 ** 2), np.mean(r2 ** 2)
    v1, v2 = g1 - mu1 ** 2, g2 - mu2 ** 2
    diff = mu1 / np.sqrt(v1) - mu2 / np.sqrt(v2)
    grad = np.array([g1 / v1 ** 1.5, -g2 / v2 ** 1.5, -mu1 / (2.0 * v1 ** 1.5), mu2 / (2.0 * v2 ** 1.5)])
    psi, lags = _hac_cov(np.column_stack([r1, r2, r1 ** 2, r2 ** 2]))
    se = float(np.sqrt(max(float(grad @ psi @ grad), 0.0) / r1.size))
    z = float(diff / se) if se > 0 else 0.0
    return {"diff": float(diff), "se": se, "z": z, "p_value": float(2.0 * norm.sf(abs(z))), "lags": lags}


def stationary_bootstrap_indices(n: int, mean_block: float, reps: int, rng: np.random.Generator) -> np.ndarray:
    """Politis-Romano (1994) stationary bootstrap: blocks of geometric length (mean `mean_block`)
    starting at uniform random positions, wrapping at the end. Returns a (reps, n) index array."""
    idx = np.empty((reps, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, reps)
    new_block = rng.random((reps, n)) < 1.0 / mean_block
    starts = rng.integers(0, n, (reps, n))
    for t in range(1, n):
        idx[:, t] = np.where(new_block[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def sharpe_difference(timed: np.ndarray, buy_hold: np.ndarray, seed_key: int) -> dict:
    """Timed minus buy-and-hold Sharpe (annualized), Ledoit-Wolf HAC test and a stationary
    block bootstrap of the paired months (basic bootstrap test of a zero difference)."""
    timed, buy_hold = np.asarray(timed, dtype=float), np.asarray(buy_hold, dtype=float)
    lw = ledoit_wolf_sharpe_test(timed, buy_hold)
    rng = np.random.default_rng([SEED, seed_key])
    idx = stationary_bootstrap_indices(timed.size, SHARPE_BOOT_BLOCK, SHARPE_BOOT_REPS, rng)
    a, b = timed[idx], buy_hold[idx]
    with np.errstate(divide="ignore", invalid="ignore"):
        boot = a.mean(axis=1) / a.std(axis=1) - b.mean(axis=1) / b.std(axis=1)
    boot = boot[np.isfinite(boot)]
    p_boot = (1 + int(np.sum(np.abs(boot - lw["diff"]) >= abs(lw["diff"])))) / (boot.size + 1)
    ann = np.sqrt(12.0)
    lo, hi = np.percentile(boot, [2.5, 97.5]) * ann
    return {
        "difference_annualized": round(lw["diff"] * ann, 3) + 0.0,
        "ledoit_wolf_se_annualized": round(lw["se"] * ann, 3),
        "ledoit_wolf_z": round(lw["z"], 3) + 0.0,
        "ledoit_wolf_p_value": round(lw["p_value"], 4),
        "hac_lags": lw["lags"],
        "bootstrap_p_value": round(p_boot, 4),
        "bootstrap_ci_95_annualized": [round(float(lo), 3) + 0.0, round(float(hi), 3) + 0.0],
        "bootstrap": (f"stationary (Politis-Romano), mean block {SHARPE_BOOT_BLOCK} months, "
                      f"{SHARPE_BOOT_REPS} resamples, seed {SEED}"),
    }


def evaluate(oos: pd.DataFrame, ret_col: str = "ret", seed_key: int | None = None) -> dict:
    """Direction, return-spread and Sharpe statistics for one out-of-sample block.

    The split is the trading rule's own: months whose carry is above the median of all prior
    months (long) vs the rest (flat). It is not the backwardation/contango sign split; the legacy
    keys that called it that are kept as aliases (see LEGACY_ALIASES and sign_split).
    """
    ret = oos[ret_col].to_numpy()
    long = oos["long"].to_numpy(dtype=bool)
    up = ret > 0
    n = int(len(ret))
    hits = int(np.sum(long == up))
    base = float(max(up.mean(), 1.0 - up.mean()))
    above, below = ret[long], ret[~long]
    spread_p = float(ttest_ind(above, below, equal_var=False, alternative="greater").pvalue)
    rank = spearmanr(oos["carry"].to_numpy(), ret)
    out = {
        "n_months": n,
        "span": f"{oos.index.min().date()}..{oos.index.max().date()}",
        "direction_accuracy_pct": round(hits / n * 100.0, 1),
        "base_rate_pct": round(base * 100.0, 1),
        "direction_p_value": round(float(binom.sf(hits - 1, n, base)), 4),
        "split": "carry above vs at-or-below the median carry of all prior months (the rule's long/flat split)",
        "mean_ret_above_median_carry_pct": round(float(np.mean(above)) * 100.0, 2),
        "mean_ret_below_median_carry_pct": round(float(np.mean(below)) * 100.0, 2),
        "n_above_median_carry": int(long.sum()),
        "n_below_median_carry": int((~long).sum()),
        "spread_p_value_one_sided": round(spread_p, 4),
        "carry_slope_t_hc3": round(_hc3_slope_t(oos["carry"].to_numpy(), ret), 2),
        # Rank correlation: the slope is dominated by a handful of extreme months, the ranks are not.
        "carry_rank_corr": round(float(rank.statistic), 3) + 0.0,
        "carry_rank_corr_p_value": round(float(rank.pvalue), 4),
        "carry_timed_sharpe": _sharpe_monthly(np.where(long, ret, 0.0)),
        "buy_hold_sharpe": _sharpe_monthly(ret),
    }
    for legacy, precise in LEGACY_ALIASES.items():
        out[legacy] = out[precise]
    if seed_key is not None:
        out["sharpe_difference"] = sharpe_difference(np.where(long, ret, 0.0), ret, seed_key)
    return out


def evaluate_sign_split(oos: pd.DataFrame) -> dict:
    """Robustness: the true backwardation/contango split (carry > 0 vs <= 0), long when carry > 0.
    No estimated threshold, so it needs no training months; scored on the same months as the rule."""
    ret = oos["ret"].to_numpy()
    carry = oos["carry"].to_numpy()
    back = carry > 0
    p = float(ttest_ind(ret[back], ret[~back], equal_var=False, alternative="greater").pvalue)
    return {
        "n_months": int(len(ret)),
        "n_carry_positive": int(back.sum()),
        "n_carry_nonpositive": int((~back).sum()),
        "mean_ret_carry_positive_pct": round(float(np.mean(ret[back])) * 100.0, 2),
        "mean_ret_carry_nonpositive_pct": round(float(np.mean(ret[~back])) * 100.0, 2),
        "spread_p_value_one_sided": round(p, 4),
        "sign_timed_sharpe": _sharpe_monthly(np.where(back, ret, 0.0)),
        "buy_hold_sharpe": _sharpe_monthly(ret),
        "share_agreeing_with_median_rule_pct": round(float(np.mean(back == oos["long"].to_numpy(bool))) * 100, 1),
    }


def _spread_and_se(block: pd.DataFrame) -> tuple[float, float]:
    """Mean monthly return spread (rule long minus flat) and its Welch standard error."""
    ret = block["ret"].to_numpy()
    long = block["long"].to_numpy(dtype=bool)
    a, b = ret[long], ret[~long]
    return float(a.mean() - b.mean()), float(np.sqrt(a.var(ddof=1) / a.size + b.var(ddof=1) / b.size))


def power_analysis(pre: pd.DataFrame, post: pd.DataFrame) -> dict:
    """What the post-2007 sample could detect: the minimum detectable spread (one-sided 5%, as the
    spread test is run) and rank correlation (two-sided 5%, as the Spearman test is run), both at 80%
    power, and its power against the effect sizes measured before 2007."""
    z_a, z_a2, z_b = norm.ppf(1.0 - POWER_ALPHA), norm.ppf(1.0 - POWER_ALPHA / 2.0), norm.ppf(POWER_TARGET)
    pre_spread, _ = _spread_and_se(pre)
    post_spread, se = _spread_and_se(post)
    se_z = np.sqrt(SPEARMAN_FISHER_VAR / (len(post) - 3))
    pre_rho = float(spearmanr(pre["carry"], pre["ret"]).statistic)
    mde_spread = (z_a + z_b) * se * 100.0
    power_spread = float(norm.cdf(pre_spread / se - z_a))
    mde_rho = float(np.tanh((z_a2 + z_b) * se_z))
    shift = np.arctanh(pre_rho) / se_z
    power_rho = float(norm.cdf(shift - z_a2) + norm.cdf(-shift - z_a2))
    statement = (
        f"After 2007 ({len(post)} months) the spread test could only detect a spread of "
        f"{mde_spread:.2f}%/month or more (one-sided {POWER_ALPHA:.0%}, {POWER_TARGET:.0%} power); it had "
        f"{power_spread * 100:.0f}% power against the pre-2007 spread of {pre_spread * 100:.2f}%/month, so a "
        f"null spread there is weak evidence of decay. The rank test (two-sided {POWER_ALPHA:.0%}) could detect "
        f"a correlation of {mde_rho:.3f} and had {power_rho * 100:.0f}% power against the pre-2007 rank "
        f"correlation of {pre_rho:.3f}."
    )
    return {
        "alpha_one_sided": POWER_ALPHA,
        "alpha_two_sided_rank_corr": POWER_ALPHA,
        "target_power": POWER_TARGET,
        "n_months": int(len(post)),
        "spread_se_pct": round(se * 100.0, 3),
        "observed_spread_pct": round(post_spread * 100.0, 2),
        "mde_spread_pct_per_month": round(mde_spread, 2),
        "pre_2007_spread_pct": round(pre_spread * 100.0, 2),
        "power_vs_pre_2007_spread": round(power_spread, 3),
        "mde_rank_corr": round(mde_rho, 3),
        "pre_2007_rank_corr": round(pre_rho, 3),
        "power_vs_pre_2007_rank_corr": round(power_rho, 3),
        "statement": statement,
    }


def regime_change_test(pre: pd.DataFrame, post: pd.DataFrame) -> dict:
    """Is the effect different after 2007? Two-sided tests of equal spreads (Welch z) and equal
    rank correlations (Fisher z with the Spearman variance factor)."""
    s_pre, se_pre = _spread_and_se(pre)
    s_post, se_post = _spread_and_se(post)
    z_spread = (s_pre - s_post) / np.sqrt(se_pre ** 2 + se_post ** 2)
    r_pre = float(spearmanr(pre["carry"], pre["ret"]).statistic)
    r_post = float(spearmanr(post["carry"], post["ret"]).statistic)
    z_rank = (np.arctanh(r_pre) - np.arctanh(r_post)) / np.sqrt(
        SPEARMAN_FISHER_VAR / (len(pre) - 3) + SPEARMAN_FISHER_VAR / (len(post) - 3))
    return {
        "spread_difference_pct": round((s_pre - s_post) * 100.0, 2),
        "spread_difference_z": round(float(z_spread), 3),
        "spread_difference_p_value": round(float(2.0 * norm.sf(abs(z_spread))), 4),
        "rank_corr_difference": round(r_pre - r_post, 3),
        "rank_corr_difference_z": round(float(z_rank), 3),
        "rank_corr_difference_p_value": round(float(2.0 * norm.sf(abs(z_rank))), 4),
    }


def _verdict(full: dict, post: dict, sign: dict, power: dict, change: dict) -> str:
    sig = full["spread_p_value_one_sided"] < 0.05
    parts = [
        ("Months with carry above its prior-months median beat the rest "
         + (f"significantly over the full sample (one-sided p = {full['spread_p_value_one_sided']:.3f})"
            if sig else f"but not significantly (p = {full['spread_p_value_one_sided']:.3f})")
         + f"; the true sign split (backwardation vs contango) agrees (p = {sign['full_sample']['spread_p_value_one_sided']:.3f})."),
        (f"After 2007 the spread is not distinguishable from zero (p = {post['spread_p_value_one_sided']:.2f})"
         if post["spread_p_value_one_sided"] >= 0.05 else
         f"After 2007 the spread is still significant (p = {post['spread_p_value_one_sided']:.3f})")
        + (f", but that test had only {power['power_vs_pre_2007_spread'] * 100:.0f}% power against the pre-2007 spread"
           f" and the pre/post spread difference is not significant (p = {change['spread_difference_p_value']:.2f})"
           if change["spread_difference_p_value"] >= 0.05 else
           f" and the pre/post spread difference is significant (p = {change['spread_difference_p_value']:.3f})")
        + (f"; the rank correlation, with {power['power_vs_pre_2007_rank_corr'] * 100:.0f}% power, fell from "
           f"{power['pre_2007_rank_corr']:.2f} to {post['carry_rank_corr']:.2f} (difference p = "
           f"{change['rank_corr_difference_p_value']:.4f})."),
        (f"The Sharpe gain of timing over buy-and-hold is not significant (Ledoit-Wolf p = "
         f"{full['sharpe_difference']['ledoit_wolf_p_value']:.2f} full sample, "
         f"{post['sharpe_difference']['ledoit_wolf_p_value']:.2f} after 2007)"
         if full["sharpe_difference"]["ledoit_wolf_p_value"] >= 0.05 else
         f"Timing beats buy-and-hold on Sharpe over the full sample (Ledoit-Wolf p = "
         f"{full['sharpe_difference']['ledoit_wolf_p_value']:.3f})")
        + (", and as a sign predictor it does not beat always-long over the full sample."
           if full["direction_accuracy_pct"] <= full["base_rate_pct"] else "."),
        "No reliable modern-era edge is demonstrated; how much of the pre-2007 effect survives is not "
        "resolved by this sample.",
    ]
    return " ".join(parts)


def run(curve: pd.DataFrame | None = None) -> dict:
    curve = load_curve() if curve is None else curve
    cycles = monthly_cycles(curve)
    oos = walk_forward(cycles)
    pre, post = oos[oos.index < REGIME_SPLIT], oos[oos.index >= REGIME_SPLIT]
    original_span = oos[oos.index >= "2004-06-01"]
    full = evaluate(oos, seed_key=1)
    pre_eval, post_eval = evaluate(pre, seed_key=2), evaluate(post, seed_key=3)
    unadjusted = evaluate(oos, "ret_unadjusted")
    contamination = (oos["ret_unadjusted"] - oos["ret"]).to_numpy()
    back = oos["carry"].to_numpy() > 0
    unadj_ret, long = oos["ret_unadjusted"].to_numpy(), oos["long"].to_numpy(dtype=bool)
    sign = {"definition": "carry > 0 (backwardation) vs carry <= 0 (contango); long when carry > 0",
            "full_sample": evaluate_sign_split(oos), "pre_2007": evaluate_sign_split(pre),
            "post_2007": evaluate_sign_split(post)}
    power = power_analysis(pre, post)
    change = regime_change_test(pre, post)
    return {
        "method": ("Second-nearby NYMEX WTI holding return over each contract cycle (roll-free), "
                   "carry = (C1 - C2) / C2 on the first day after expiry, long when carry exceeds "
                   "the median of all prior months, else flat. Non-overlapping monthly windows."),
        "data": "EIA RCLC1/RCLC2 daily settlements (series discontinued after 2024-04-05)",
        "field_notes": {
            **{legacy: (f"legacy alias of {precise}: the rule's split on the median of prior months' carry, "
                        "NOT the sign of carry (see sign_split)") for legacy, precise in LEGACY_ALIASES.items()},
            "sharpe_difference": "carry-timed minus buy-and-hold Sharpe, annualized; Ledoit-Wolf (2008) HAC "
                                 "delta method and a stationary block bootstrap, two-sided",
        },
        "full_sample": full,
        "pre_2007": pre_eval,
        "post_2007": post_eval,
        "original_span_2004_2024": evaluate(original_span, seed_key=4),
        "sign_split": sign,
        "power_post_2007": power,
        "regime_change_test": change,
        "roll_bias_of_original_method": {
            "note": ("Original design: unadjusted contract-1 (== CL=F) 21-day forward returns. On the "
                     "same dates the roll gap subtracts roughly the carry itself."),
            "unadjusted_result": {
                **{k: unadjusted[k] for k in (
                    "direction_accuracy_pct", "base_rate_pct", "mean_ret_backwardated_pct",
                    "mean_ret_contango_pct", "carry_timed_sharpe", "buy_hold_sharpe")},
                # The original (retracted) reading: below-median carry beat above-median carry.
                "below_median_beats_above_p_value_one_sided": round(float(ttest_ind(
                    unadj_ret[~long], unadj_ret[long], equal_var=False, alternative="greater").pvalue), 4),
            },
            "mean_contamination_backwardation_pct": round(float(np.mean(contamination[back])) * 100, 2),
            "mean_contamination_contango_pct": round(float(np.mean(contamination[~back])) * 100, 2),
            "corr_contamination_carry": round(float(np.corrcoef(contamination, oos["carry"])[0, 1]), 2),
        },
        "verdict": _verdict(full, post_eval, sign, power, change),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Roll-free term-structure carry test for WTI.")
    ap.add_argument("--refresh", action="store_true",
                    help="re-download the EIA futures curve before running (needs xlrd)")
    args = ap.parse_args()
    if args.refresh:
        save_curve(download_curve())
    res = run()
    ARTIFACT_PATH.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    for label in ("full_sample", "pre_2007", "post_2007", "original_span_2004_2024"):
        r = res[label]
        sd = r["sharpe_difference"]
        print(f"{label:24s} n={r['n_months']:3d} {r['span']}: carry above prior median "
              f"{r['mean_ret_above_median_carry_pct']:+.2f}%/mo vs below {r['mean_ret_below_median_carry_pct']:+.2f}%/mo "
              f"(p={r['spread_p_value_one_sided']:.3f}); dir acc {r['direction_accuracy_pct']}% vs base "
              f"{r['base_rate_pct']}%; Sharpe timed {r['carry_timed_sharpe']} vs buy-hold {r['buy_hold_sharpe']} "
              f"(LW p={sd['ledoit_wolf_p_value']:.3f}, bootstrap p={sd['bootstrap_p_value']:.3f})")
    for label in ("full_sample", "pre_2007", "post_2007"):
        s = res["sign_split"][label]
        print(f"sign split {label:13s} carry>0 {s['mean_ret_carry_positive_pct']:+.2f}%/mo (n={s['n_carry_positive']}) "
              f"vs <=0 {s['mean_ret_carry_nonpositive_pct']:+.2f}%/mo (p={s['spread_p_value_one_sided']:.3f})")
    print(f"power: {res['power_post_2007']['statement']}")
    chg = res["regime_change_test"]
    print(f"pre vs post 2007: spread difference p={chg['spread_difference_p_value']:.3f}, "
          f"rank-correlation difference p={chg['rank_corr_difference_p_value']:.4f}")
    bias = res["roll_bias_of_original_method"]
    print(f"roll bias of the original method: {bias['mean_contamination_backwardation_pct']:+.2f}%/mo in "
          f"backwardation, {bias['mean_contamination_contango_pct']:+.2f}%/mo in contango "
          f"(corr with carry {bias['corr_contamination_carry']})")
    print(f"-> {res['verdict']}")


if __name__ == "__main__":
    main()
