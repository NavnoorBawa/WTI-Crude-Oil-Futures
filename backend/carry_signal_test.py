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
therefore subtracted the very carry it was trying to detect: measured on the same dates, the
contamination averages -1.6%/month in backwardation and +1.4% in contango (correlation -0.52 with
carry). That artifact is what produced the old "data leans the opposite way (contango -> higher
returns)" observation. The first version also stopped in 2004 only because the EIA API's
5000-row page limit silently truncated the history (it sorted descending and never paginated).

Design (leak-free, no roll gap can enter a return):
  - DATA: EIA's NYMEX WTI futures prices, contract 1 and 2 (RCLC1/RCLC2), 1985-2024. EIA stopped
    publishing the series on 2024-04-05, so the history is frozen and is committed to
    data/eia_wti_futures_curve.json; `--refresh` re-downloads it from EIA's public, keyless files.
  - CONTRACT CYCLE: last trade dates follow the CME rule (backend/contract_calendar.py) evaluated on
    the data's own trading-day calendar, so historical holidays and closures are handled exactly.
  - SIGNAL: carry = (C1 - C2) / C2 on the first trading day after each expiry (>0 = backwardation).
  - RETURN: hold the second-nearby contract from the next trading day until one day before the next
    expiry. It is one contract throughout (a one-day margin on each side absorbs any roll-date
    ambiguity), so the return is a real futures holding return with no roll gap, and holding the
    second contract avoids expiry-week delivery squeezes such as April 2020.
  - RULE (a priori, unchanged): long when carry is above the median of all PRIOR months' carry,
    else flat. Monthly windows do not overlap, so every earlier observation is fully matured.

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
from scipy.stats import binom, ttest_ind

try:
    from .contract_calendar import last_trade_date
except ImportError:  # Direct invocation: python backend/carry_signal_test.py
    from contract_calendar import last_trade_date

ROOT = Path(__file__).resolve().parent.parent
CURVE_PATH = ROOT / "data" / "eia_wti_futures_curve.json"
ARTIFACT_PATH = ROOT / "data" / "carry_signal_test.json"
EIA_HISTORY_URL = "https://www.eia.gov/dnav/pet/hist_xls/{series}d.xls"
SERIES = {"c1": "RCLC1", "c2": "RCLC2"}

MIN_TRAIN_MONTHS = 60       # five years of prior months before the first out-of-sample decision
MIN_HOLD_DAYS = 10          # skip truncated cycles (only possible at the ends of the sample)
REGIME_SPLIT = "2007-01-01"  # pre/post: the shale era and the financialization of commodities


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


def monthly_cycles(curve: pd.DataFrame) -> pd.DataFrame:
    """One row per contract cycle: carry signal and the same-contract holding return that follows.

    Columns: signal date, entry/exit dates, carry, ret (second-nearby, roll-free) and
    ret_unadjusted (the original method: unadjusted contract-1 return over 21 trading days, kept
    only to measure the roll bias it introduced).
    """
    curve = curve[(curve["c1"] > 0) & (curve["c2"] > 0)].sort_index()
    days = curve.index
    trading = set(days.date)

    def is_trading(d: date) -> bool:
        return d in trading

    first, last = days[0].date(), days[-1].date()
    expiries = []
    y, m = first.year, first.month
    while (y, m) <= (last.year + 1, last.month):
        # Only contracts whose whole count-back window lies inside the data: the observed calendar
        # knows nothing about days before the first or after the last settlement.
        prior_25th = date(y - 1, 12, 25) if m == 1 else date(y, m - 1, 25)
        if first + pd.Timedelta(days=14) <= prior_25th <= last:
            ltd = last_trade_date(y, m, is_trading)
            if ltd in trading:
                expiries.append(days.get_loc(pd.Timestamp(ltd)))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    expiries = sorted(set(expiries))

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


def _hc1_slope_t(x: np.ndarray, y: np.ndarray) -> float:
    """t-statistic of the OLS slope of y on x with heteroskedasticity-robust (HC1) errors."""
    X = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    xtx_inv = np.linalg.inv(X.T @ X)
    cov = xtx_inv @ (X.T * resid ** 2) @ X @ xtx_inv * len(y) / (len(y) - 2)
    return float(beta[1] / np.sqrt(cov[1, 1]))


def evaluate(oos: pd.DataFrame, ret_col: str = "ret") -> dict:
    """Direction, return-spread and Sharpe statistics for one out-of-sample block."""
    ret = oos[ret_col].to_numpy()
    long = oos["long"].to_numpy(dtype=bool)
    up = ret > 0
    n = int(len(ret))
    hits = int(np.sum(long == up))
    base = float(max(up.mean(), 1.0 - up.mean()))
    backwardated, contango = ret[long], ret[~long]
    spread_p = float(ttest_ind(backwardated, contango, equal_var=False, alternative="greater").pvalue)
    return {
        "n_months": n,
        "span": f"{oos.index.min().date()}..{oos.index.max().date()}",
        "direction_accuracy_pct": round(hits / n * 100.0, 1),
        "base_rate_pct": round(base * 100.0, 1),
        "direction_p_value": round(float(binom.sf(hits - 1, n, base)), 4),
        "mean_ret_backwardated_pct": round(float(np.mean(backwardated)) * 100.0, 2),
        "mean_ret_contango_pct": round(float(np.mean(contango)) * 100.0, 2),
        "n_backwardated": int(long.sum()),
        "spread_p_value_one_sided": round(spread_p, 4),
        "carry_slope_t_hc1": round(_hc1_slope_t(oos["carry"].to_numpy(), ret), 2),
        "carry_timed_sharpe": _sharpe_monthly(np.where(long, ret, 0.0)),
        "buy_hold_sharpe": _sharpe_monthly(ret),
    }


def run(curve: pd.DataFrame | None = None) -> dict:
    curve = load_curve() if curve is None else curve
    cycles = monthly_cycles(curve)
    oos = walk_forward(cycles)
    full = evaluate(oos)
    pre, post = oos[oos.index < REGIME_SPLIT], oos[oos.index >= REGIME_SPLIT]
    original_span = oos[oos.index >= "2004-06-01"]
    unadjusted = evaluate(oos, "ret_unadjusted")
    contamination = (oos["ret_unadjusted"] - oos["ret"]).to_numpy()
    back = oos["carry"].to_numpy() > 0

    significant_full = full["spread_p_value_one_sided"] < 0.05
    post_eval = evaluate(post)
    verdict = (
        "Carry sorts returns the textbook way (backwardated months beat contango months) "
        + ("significantly over the full sample, " if significant_full else "but not significantly, ")
        + ("yet the effect is absent after 2007" if post_eval["spread_p_value_one_sided"] >= 0.10
           else "and it persists after 2007")
        + "; as a sign predictor it does not beat always-long. No reliable modern-era edge."
    )
    return {
        "method": ("Second-nearby NYMEX WTI holding return over each contract cycle (roll-free), "
                   "carry = (C1 - C2) / C2 on the first day after expiry, long when carry exceeds "
                   "the median of all prior months, else flat. Non-overlapping monthly windows."),
        "data": "EIA RCLC1/RCLC2 daily settlements (series discontinued after 2024-04-05)",
        "full_sample": full,
        "pre_2007": evaluate(pre),
        "post_2007": post_eval,
        "original_span_2004_2024": evaluate(original_span),
        "roll_bias_of_original_method": {
            "note": ("Original design: unadjusted contract-1 (== CL=F) 21-day forward returns. On the "
                     "same dates the roll gap subtracts roughly the carry itself."),
            "unadjusted_result": {k: unadjusted[k] for k in (
                "direction_accuracy_pct", "base_rate_pct", "mean_ret_backwardated_pct",
                "mean_ret_contango_pct", "carry_timed_sharpe", "buy_hold_sharpe")},
            "mean_contamination_backwardation_pct": round(float(np.mean(contamination[back])) * 100, 2),
            "mean_contamination_contango_pct": round(float(np.mean(contamination[~back])) * 100, 2),
            "corr_contamination_carry": round(float(np.corrcoef(contamination, oos["carry"])[0, 1]), 2),
        },
        "verdict": verdict,
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
        print(f"{label:24s} n={r['n_months']:3d} {r['span']}: backwardated {r['mean_ret_backwardated_pct']:+.2f}%/mo "
              f"vs contango {r['mean_ret_contango_pct']:+.2f}%/mo (p={r['spread_p_value_one_sided']:.3f}); "
              f"dir acc {r['direction_accuracy_pct']}% vs base {r['base_rate_pct']}%; "
              f"Sharpe timed {r['carry_timed_sharpe']} vs buy-hold {r['buy_hold_sharpe']}")
    bias = res["roll_bias_of_original_method"]
    print(f"roll bias of the original method: {bias['mean_contamination_backwardation_pct']:+.2f}%/mo in "
          f"backwardation, {bias['mean_contamination_contango_pct']:+.2f}%/mo in contango "
          f"(corr with carry {bias['corr_contamination_carry']})")
    print(f"-> {res['verdict']}")


if __name__ == "__main__":
    main()
