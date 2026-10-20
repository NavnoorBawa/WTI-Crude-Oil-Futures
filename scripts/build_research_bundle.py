#!/usr/bin/env python3
"""Compose public/research.json, the dashboard's research payload, from committed artifacts.

The dashboard loads two files: data.json (the live snapshot, rebuilt every refresh) and
research.json (this file: the reproducible research, rebuilt only when an artifact changes). Every
number in research.json is copied from a committed artifact, never computed here, so the page can
never show a figure the repository cannot reproduce:

  vol_lab      data/vol_lab.json (backend/vol_lab.py on the pinned data/market snapshot)
  postmortem   data/wf_5y_purged.json, data/wf_5y_unpurged_LEAKED.json and the 10-year pair
  carry        data/carry_signal_test.json
  leak_anatomy data/leak_anatomy.json (why the leaked backtest looked skilled on a random walk)
  supply_study data/supply_shock_study.json (event study against a placebo of ordinary dates)
  ledger       data/research_ledger.json (every hypothesis tested, with multiplicity adjustments)

Standard library only (it runs in any job). Usage: python scripts/build_research_bundle.py [--out public]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCHEMA_VERSION = 1
REQUIRED_LAB_KEYS = ("sample", "config", "leaderboard", "oos_selection", "var_backtest", "conformal",
                     "yearly", "noise_floor", "har_iv_coefficients", "vol_cone", "series", "generated_from")


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _equity(trades: list[dict]) -> list[list]:
    """[[date, cumulative P&L in USD], ...] from a walk-forward run's trade list."""
    total, curve = 0.0, []
    for trade in trades:
        total += float(trade["pnl"])
        curve.append([trade["t"], round(total, 2)])
    return curve


def _run_summary(report: dict, horizon: str = "1w") -> dict:
    res = report["results"][horizon]
    pnl, ens = res["pnl_metrics"], res["metrics"]["ensemble"]
    return {
        "n": int(res["samples"]),
        "direction_acc_pct": round(float(ens["direction_accuracy"]), 1),
        "p_value": float(ens["direction_p_value"]),
        "sharpe": float(pnl["sharpe_ratio_annualized"]),
        "total_pnl_usd": float(pnl["total_pnl_usd"]),
        "max_drawdown_usd": float(pnl["max_drawdown_usd"]),
        "mae_vs_best_baseline_pct": res.get("mae_improvement_vs_best_baseline_pct"),
        "first": res.get("first_prediction_timestamp"),
        "last": res.get("last_prediction_timestamp"),
    }


def _postmortem() -> dict:
    out = {}
    for label, purged, leaked in (("five_year", "wf_5y_purged.json", "wf_5y_unpurged_LEAKED.json"),
                                  ("ten_year", "wf_10y_rolling_purged.json", "wf_10y_rolling_unpurged_LEAKED.json")):
        p, k = _load(purged), _load(leaked)
        out[label] = {
            "purged": {**_run_summary(p), "equity": _equity(p["results"]["1w"].get("trades") or [])},
            "leaked": {**_run_summary(k), "equity": _equity(k["results"]["1w"].get("trades") or [])},
            "config": {key: p["config"].get(key) for key in ("start", "end", "train_window", "purge_rows_by_horizon",
                                                              "lag_context", "features") if key in p["config"]},
        }
    return out


def _vol_lab() -> dict:
    lab = _load("vol_lab.json")
    missing = [key for key in REQUIRED_LAB_KEYS if key not in lab]
    if missing:
        raise ValueError(f"data/vol_lab.json is missing {missing}")
    var = lab["var_backtest"]
    # The page shows the production grid in full and the other four grids as pass counts.
    lab["var_backtest"] = {"grid0": var["grids"]["0"], "summary": var["summary"]}
    return lab


def _leak_anatomy() -> dict:
    """The leak anatomy, without the per-seed run lists."""
    raw = _load("leak_anatomy.json")

    def arm(block: dict) -> dict:
        keep = ("n_runs", "accuracy_pct_mean", "accuracy_pct_range", "sharpe_mean", "sharpe_range",
                "pooled_binomial_p_value_vs_50")
        return {key: block[key] for key in keep if key in block}

    experiments = {}
    for name in ("synthetic_random_walk", "block_shuffled_cl", "real_cl_5y_window"):
        experiments[name] = {model: {"unpurged": arm(v["unpurged_leaked"]), "purged": arm(v["purged"])}
                             for model, v in raw[name].items() if isinstance(v, dict) and "purged" in v}
    synthetic = experiments["synthetic_random_walk"]
    headline_model = "random_forest" if "random_forest" in synthetic else next(iter(synthetic))
    return {
        "analytic": raw["analytic"],
        "experiments": experiments,
        "retracted_reference": raw["retracted_reference"],
        "conclusion": raw["conclusion"],
        # Flat headline fields for the dashboard (the synthetic random-walk experiment).
        "headline_model": headline_model,
        "unpurged_dir_acc_pct_mean": synthetic[headline_model]["unpurged"]["accuracy_pct_mean"],
        "purged_dir_acc_pct_mean": synthetic[headline_model]["purged"]["accuracy_pct_mean"],
        "unpurged_sharpe_mean": synthetic[headline_model]["unpurged"].get("sharpe_mean"),
        "sims": synthetic[headline_model]["unpurged"].get("n_runs"),
        "analytic_sign_agreement_pct": raw["analytic"]["p_sign_agree_leaked_pct"],
    }


def _ledger() -> dict:
    """The research ledger, one compact row per hypothesis."""
    raw = _load("research_ledger.json")
    rows = []
    for row in raw["rows"]:
        stat = row.get("statistic") or {}
        rows.append({
            "id": row["id"], "date": row["date"], "family": row["family"], "hypothesis": row["hypothesis"],
            "test": row["test"], "statistic_name": stat.get("name"), "statistic_value": stat.get("value"),
            "p_value": row.get("p_value"), "q_bh": row.get("q_value_bh"), "p_holm": row.get("p_holm"),
            "survives_bh": row.get("survives_bh_5pct"), "clears_t3": row.get("clears_hlz_t3"),
            "pre_specified": row.get("pre_specified"), "status": row["status"],
            "artifact": (row.get("artifact") or {}).get("path"),
        })
    return {"method": raw["method"], "alpha": raw["alpha"], "hlz_t": raw["hlz_t"], "summary": raw["summary"],
            "rows": rows}


def build() -> dict:
    carry = _load("carry_signal_test.json")
    supply = _load("supply_shock_study.json")
    return {
        "schema_version": SCHEMA_VERSION,
        "vol_lab": _vol_lab(),
        "postmortem": _postmortem(),
        "carry": {key: carry[key] for key in ("method", "full_sample", "pre_2007", "post_2007", "verdict",
                                             "sign_split", "power_post_2007", "regime_change_test",
                                             "sharpe_difference", "field_notes") if key in carry},
        "leak_anatomy": _leak_anatomy(),
        "supply_study": {key: supply[key] for key in ("method", "placebo", "groups", "between_groups",
                                                     "priced_in", "superseded_claims", "conclusion")
                         if key in supply},
        "ledger": _ledger(),
    }


def validate(bundle: dict) -> list[str]:
    """Structural checks the dashboard relies on. Returns a list of problems (empty when valid)."""
    problems = []
    if bundle.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version")
    lab = bundle.get("vol_lab") or {}
    series = lab.get("series") or {}
    n = (lab.get("sample") or {}).get("n_weeks")
    for key, values in series.items():
        if not isinstance(values, list) or len(values) != n:
            problems.append(f"series.{key} length")
    models = (lab.get("leaderboard") or {}).get("models") or []
    if not models or any("key" not in m or "qlike" not in m for m in models):
        problems.append("leaderboard.models")
    for label in ("five_year", "ten_year"):
        run = (bundle.get("postmortem") or {}).get(label) or {}
        if not run.get("purged", {}).get("equity") or not run.get("leaked", {}).get("equity"):
            problems.append(f"postmortem.{label}.equity")
    ledger = bundle.get("ledger") or {}
    if not isinstance(ledger.get("rows"), list) or not ledger["rows"]:
        problems.append("ledger.rows")
    if not isinstance((bundle.get("supply_study") or {}).get("groups"), list):
        problems.append("supply_study.groups")
    if not isinstance((bundle.get("leak_anatomy") or {}).get("analytic_sign_agreement_pct"), (int, float)):
        problems.append("leak_anatomy")
    return problems


def write(out_dir: Path) -> Path:
    bundle = build()
    problems = validate(bundle)
    if problems:
        raise ValueError(f"research bundle failed validation: {problems}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "research.json"
    path.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    return path


def main(argv=None) -> int:
    sys.path.insert(0, str(ROOT))
    from backend.safe_paths import public_output_directory

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="public", help="output directory (must be public)")
    args = parser.parse_args(argv)
    path = write(public_output_directory(args.out))
    print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
