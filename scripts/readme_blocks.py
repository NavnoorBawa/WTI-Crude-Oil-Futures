"""Markdown blocks for README.md and docs/, generated from the committed artifacts.

Every table and key number in the README is produced here from data/*.json, and inserted with cog
(https://nedbatchelder.com/code/cog): `cog -r README.md` regenerates the blocks and
`cog --check README.md` (run in CI) fails if a committed artifact and the README disagree. No
statistic is ever typed by hand. Standard library only.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _lab() -> dict:
    return _load("vol_lab.json")


def _prod() -> dict:
    return _load("vol_forecast_validation.json")


def _fmt(value, digits: int = 3, signed: bool = False) -> str:
    if value is None:
        return "—"
    text = f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"
    return text.replace("-", "−")


def _p(value) -> str:
    if value is None:
        return "—"
    if value < 0.001:
        return "< 0.001"
    return f"{value:.3f}"


def _model(lab: dict, key: str) -> dict:
    return next(m for m in lab["leaderboard"]["models"] if m["key"] == key)


def sample_line() -> str:
    lab = _lab()
    s = lab["sample"]
    m = lab["generated_from"]
    return (f"{s['n_days']:,} out-of-sample days ({s['n_weeks']} non-overlapping weeks on the production grid), "
            f"{s['first']} to {s['last']}; data snapshot of {m['sessions_through']} "
            f"(`data/market/MANIFEST.json`).")


def key_numbers() -> dict:
    """The handful of numbers the abstract quotes, keyed by name."""
    lab, prod = _lab(), _prod()
    board = lab["leaderboard"]
    champ, chall = _model(lab, lab["config"]["champion"]), _model(lab, lab["config"]["challenger"])
    persistence = _model(lab, "persistence")
    o = prod["overall"]
    econ = prod["economic"]["vol_target_overlay"]["risk"]
    var = lab["var_backtest"]["summary"]
    hs5 = var["historical_simulation@0.05"]
    champ5 = var[f"{lab['config']['champion']}:fhs@0.05"]
    return {
        "n_days": lab["sample"]["n_days"], "n_weeks": lab["sample"]["n_weeks"],
        "first": lab["sample"]["first"], "last": lab["sample"]["last"],
        "n_models": len(board["models"]), "mcs_size": len(board["mcs_qlike_included"]),
        "champ_qlike": champ["qlike"], "persistence_qlike": persistence["qlike"],
        "chall_label": chall["label"], "chall_qlike": chall["qlike"],
        "chall_dm": chall["dm_qlike_vs_har_iv"]["stat"], "chall_dm_p": chall["dm_qlike_vs_har_iv"]["p_value"],
        "chall_grids": chall["in_mcs_qlike_grids"], "spa_p": board["spa_qlike_vs_har_iv"]["p_consistent"],
        "noise_floor": lab["noise_floor"]["har_direction_acc_pct_mean"],
        "dir_acc": o["har_dir_acc_pct"],
        "coverage": lab["conformal"][lab["config"]["champion"]]["coverage_pct"],
        "mz_slope": o["har_mincer_zarnowitz"]["slope"], "mz_p": o["har_mincer_zarnowitz"]["p_unbiased"],
        "target_vol": prod["economic"]["vol_target_overlay"]["target_vol_pct"],
        "fc_realized_vol": econ["forecast_vol_target"]["realized_vol_pct"],
        "bh_kurt": econ["buy_hold"]["excess_kurtosis"], "fc_kurt": econ["forecast_vol_target"]["excess_kurtosis"],
        "bh_worst": econ["buy_hold"]["worst_week_pct"], "fc_worst": econ["forecast_vol_target"]["worst_week_pct"],
        "hs_ind_pass": hs5["independence_pass_grids"], "champ_ind_pass": champ5["independence_pass_grids"],
        "ovx_coef": next(t for t in lab["har_iv_coefficients"]["terms"] if t["term"] == "log OVX"),
    }


def leaderboard_table() -> str:
    lab = _lab()
    champ_key, chall_key = lab["config"]["champion"], lab["config"]["challenger"]
    rows = ["| Model | Family | QLIKE | DM vs model of record (p) | DM range over 5 weekday grids | "
            "MCS p | In 90% MCS (grids) | Realized utility (% / yr) |",
            "|---|---|---:|---:|---:|---:|---:|---:|"]
    for m in lab["leaderboard"]["models"]:
        name = m["label"]
        if m["key"] in (champ_key, chall_key):
            name = f"**{name}**" + (" *(challenger)*" if m["key"] == chall_key else "")
        dm = m["dm_qlike_vs_har_iv"]
        dm_text = "—" if dm is None else f"{_fmt(dm['stat'], 2, signed=True)} ({_p(dm['p_value'])})"
        grid = m["dm_qlike_vs_har_iv_by_grid"]
        grid_text = "—" if not grid else f"{_fmt(min(grid), 2, True)} to {_fmt(max(grid), 2, True)}"
        mcs = "yes" if m["in_mcs_qlike"] else "no"
        rows.append(f"| {name} | {m['family']} | {_fmt(m['qlike'])} | {dm_text} | {grid_text} | "
                    f"{_fmt(m['mcs_qlike_p'], 2)} | {mcs} ({m['in_mcs_qlike_grids']}/5) | "
                    f"{_fmt(m['realized_utility_pct'], 2)} |")
    spa = lab["leaderboard"]["spa_qlike_vs_har_iv"]
    rows.append("")
    rows.append(f"QLIKE on variance-consistent forecasts over every out-of-sample day; DM = Diebold-Mariano "
                f"(Newey-West, Harvey-Leybourne-Newbold), positive = better than the model of record; MCS = "
                f"Hansen-Lunde-Nason Model Confidence Set (range statistic, stationary bootstrap, block "
                f"{lab['config']['mcs']['block_daily']}). Hansen's SPA test of \"some model beats the model of "
                f"record\" over the whole field: p = {_fmt(spa['p_consistent'])}. Realized utility: Bollerslev, Hood, "
                f"Huss and Pedersen (2018), maximum {lab['leaderboard']['realized_utility_max_pct']}.")
    return "\n".join(rows)


def var_table() -> str:
    lab = _lab()
    summary = lab["var_backtest"]["summary"]
    label = {m["key"]: m["label"] for m in lab["leaderboard"]["models"]}
    rows = ["| Method | Level | Exceedances by grid (expected) | Kupiec | Independence | Cond. coverage | DQ |",
            "|---|---:|---|---:|---:|---:|---:|"]
    for key, s in summary.items():
        method, level = key.split("@")
        if ":normal" in method:
            continue
        name = "Historical simulation (no vol model)" if method == "historical_simulation" else \
            f"{label.get(method.split(':')[0], method)} + filtered HS"
        rows.append(f"| {name} | {float(level) * 100:.0f}% | {', '.join(map(str, s['exceedances_by_grid']))} "
                    f"({s['expected']}) | {s['kupiec_pass_grids']}/5 | {s['independence_pass_grids']}/5 | "
                    f"{s['conditional_coverage_pass_grids']}/5 | {s['dq_pass_grids']}/5 |")
    rows.append("")
    rows.append("Weekly (5-session) VaR of a long front-month position, scored on each of the five weekday grids "
                "(non-overlapping outcomes); a test passes at 5%. Kupiec (1995) unconditional coverage, "
                "Christoffersen (1998) independence and conditional coverage, Engle-Manganelli (2004) DQ.")
    return "\n".join(rows)


def overlay_table() -> str:
    prod = _prod()
    ov = prod["economic"]["vol_target_overlay"]
    names = {"buy_hold": "Buy and hold", "trailing_vol_target": "Trailing 22-day vol target",
             "forecast_vol_target": "Forecast vol target"}
    rows = [f"| Book | Sharpe | Realized vol (target {ov['target_vol_pct']}%) | Vol of vol | Excess kurtosis | "
            "Worst week | 97.5% ES (weekly) | Max drawdown |",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key, name in names.items():
        r = ov["risk"][key]
        rows.append(f"| {name} | {_fmt(r['sharpe'], 2)} | {r['realized_vol_pct']}% | {r['vol_of_vol_pct']} | "
                    f"{r['excess_kurtosis']} | {_fmt(r['worst_week_pct'], 1)}% | "
                    f"{_fmt(r['expected_shortfall_97_5_pct'], 1)}% | {r['max_drawdown_pct']}% |")
    tests = ov["sharpe_difference_tests"]
    rows.append("")
    rows.append(f"{ov['n_weeks']} weeks, {prod['economic']['returns']['sample_start']} to "
                f"{prod['economic']['returns']['sample_end']}, same-contract returns, "
                f"{ov['cost_bps_per_turn']} bp per unit of turnover. Sharpe differences (stationary bootstrap): "
                f"forecast minus buy-and-hold {_fmt(tests['forecast_minus_buy_hold']['difference'], 2, True)} "
                f"(p = {_p(tests['forecast_minus_buy_hold']['p_value'])}), forecast minus trailing "
                f"{_fmt(tests['forecast_minus_trailing']['difference'], 2, True)} "
                f"(p = {_p(tests['forecast_minus_trailing']['p_value'])}).")
    return "\n".join(rows)


def postmortem_table() -> str:
    rows = ["| Metric | 5-year: leaked → purged | 10-year rolling: leaked → purged |", "|---|---|---|"]
    runs = {}
    for label, purged, leaked in (("5y", "wf_5y_purged.json", "wf_5y_unpurged_LEAKED.json"),
                                  ("10y", "wf_10y_rolling_purged.json", "wf_10y_rolling_unpurged_LEAKED.json")):
        runs[label] = (_load(leaked)["results"]["1w"], _load(purged)["results"]["1w"])

    def cell(label, getter, fmt):
        leaked, purged = runs[label]
        return f"{fmt(getter(leaked))} → **{fmt(getter(purged))}**"

    sharpe = lambda r: r["pnl_metrics"]["sharpe_ratio_annualized"]  # noqa: E731
    acc = lambda r: r["metrics"]["ensemble"]["direction_accuracy"]  # noqa: E731
    pval = lambda r: r["metrics"]["ensemble"]["direction_p_value"]  # noqa: E731
    n = lambda r: r["samples"]  # noqa: E731
    rows.append(f"| Annualized Sharpe (after costs) | {cell('5y', sharpe, lambda v: _fmt(v, 2))} | "
                f"{cell('10y', sharpe, lambda v: _fmt(v, 2))} |")
    rows.append(f"| Direction accuracy | {cell('5y', acc, lambda v: f'{v:.1f}%')} | "
                f"{cell('10y', acc, lambda v: f'{v:.1f}%')} |")
    rows.append(f"| p vs coin flip | {cell('5y', pval, _p)} | {cell('10y', pval, _p)} |")
    rows.append(f"| Weekly calls | {cell('5y', n, str)} | {cell('10y', n, str)} |")
    return "\n".join(rows)


def provenance_table() -> str:
    m = _lab()["generated_from"]
    rows = ["| File | Rows | First | Last | SHA-256 | Source |", "|---|---:|---|---|---|---|"]
    for entry in m["files"].values():
        rows.append(f"| `data/market/{entry['file']}` | {entry['rows']:,} | {entry['first']} | {entry['last']} | "
                    f"`{entry['sha256'][:16]}…` | {entry['source']} |")
    return "\n".join(rows)


def print_block(name: str) -> None:
    """Entry point for cog: `[[[cog import scripts.readme_blocks as b; b.print_block("leaderboard_table") ]]]`."""
    import cog  # provided by cogapp at generation time

    cog.out("\n" + globals()[name]() + "\n")
