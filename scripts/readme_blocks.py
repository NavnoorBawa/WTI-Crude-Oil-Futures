"""Markdown blocks for README.md and docs/, generated from the committed artifacts.

Every table, key number and data-dependent verdict in the README, the model card and the datasheet is
produced here from data/*.json and inserted with cog (https://nedbatchelder.com/code/cog):
`cog -r README.md` regenerates the blocks and `cog --check README.md` (run in CI) fails if a committed
artifact and the page disagree. A sentence that states a number, a direction ("above one"), a
significance verdict or a quantifier ("every", "none") is written here, conditional on the data, so it
cannot contradict the artifact it describes. No statistic is typed by hand. Standard library only.

Sources. data/vol_lab.json is rebuilt from the pinned snapshot (data/market) and checked in CI;
data/vol_forecast_validation.json and data/wf_*.json are records of runs on downloaded data, not
rebuilt in CI. Blocks that quote them say so.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ALPHA = 0.05

# What each model reads at the decision (backend/vol_lab.py: _regression_designs and walk_forward).
# "returns": close-to-close returns; "range": the daily bar (open, high, low, close); "implied": OVX.
MODEL_INPUTS = {
    "persistence": {"returns"}, "ewma": {"returns"}, "garch": {"returns"}, "gjr": {"returns"},
    "har_rv": {"returns"}, "har_rs": {"returns"}, "har_range": {"range"},
    "raw_ovx": {"implied"}, "ovx_only": {"implied"},
    "har_iv": {"returns", "implied"}, "har_iv_sameday": {"returns", "implied"},
    "har_iv_range": {"range", "implied"}, "lgbm": {"returns", "range", "implied"},
    "combo": {"returns", "implied"},
}
# A plain-words gloss for the challenger, by key (the label alone does not say what it is).
GLOSS = {"har_iv_range": "a range-based variant of the model of record"}

SOURCE_NOTE = ("*lab*: `data/vol_lab.json`, rebuilt from the pinned snapshot and checked in CI. *production "
               "run*: `data/vol_forecast_validation.json`, a recorded run on data downloaded from Yahoo Finance "
               "and FRED, not rebuilt in CI (Yahoo revises history). *leak anatomy*: `data/leak_anatomy.json`, "
               "offline from the pinned snapshot and synthetic returns. *recorded runs*: the direction "
               "post-mortem's `data/wf_*.json`, also runs on downloaded data.")


# --------------------------------------------------------------------------- loading and formatting

def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _lab() -> dict:
    return _load("vol_lab.json")


def _prod() -> dict:
    return _load("vol_forecast_validation.json")


def _ledger() -> dict:
    return _load("research_ledger.json")


def _ledger_row(ledger: dict, key: str) -> dict:
    return next(r for r in ledger["rows"] if r["id"] == key)


def _code_constant(module: str, name: str):
    """A module-level constant read from the source with ast (no import: the module needs numpy)."""
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(f"{name} is not a constant of {module}")


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


def _peq(value) -> str:
    """'p = 0.123' or 'p < 0.001'."""
    text = _p(value)
    return f"p {text}" if text.startswith("<") else f"p = {text}"


def _sig(p, alpha: float = ALPHA) -> bool:
    return p is not None and p < alpha


def _level(p) -> str:
    """The conventional verdict for a p-value."""
    if p < 0.05:
        return "significant at 5%"
    if p < 0.10:
        return "significant at 10% but not at 5%"
    return "not significant at 10%"


def _join(items: list[str]) -> str:
    items = list(items)
    if not items:
        return "none"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _names(models: list[dict]) -> str:
    return _join([m["label"] for m in models])


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _count(n: int, total: int) -> str:
    return f"all {total}" if n == total else f"{n} of {total}"


# --------------------------------------------------------------------------- the lab's field

def _model(lab: dict, key: str) -> dict:
    return next(m for m in lab["leaderboard"]["models"] if m["key"] == key)


def _champ(lab: dict) -> dict:
    return _model(lab, lab["config"]["champion"])


def _chall(lab: dict) -> dict:
    return _model(lab, lab["config"]["challenger"])


def _feasible(lab: dict) -> list[dict]:
    """The models that use only information available at the decision (every MCS, SPA and selection)."""
    return [m for m in lab["leaderboard"]["models"] if not m.get("timing_control")]


def _controls(lab: dict) -> list[dict]:
    return [m for m in lab["leaderboard"]["models"] if m.get("timing_control")]


def _inputs(key: str) -> set[str]:
    if key not in MODEL_INPUTS:
        raise KeyError(f"scripts/readme_blocks.py MODEL_INPUTS does not describe model {key!r}")
    return MODEL_INPUTS[key]


def _worse_than_record(lab: dict, alpha: float = ALPHA) -> list[dict]:
    """Feasible models whose QLIKE is significantly above the model of record's (DM, two-sided p < alpha)."""
    return [m for m in _feasible(lab)
            if m["dm_qlike_vs_har_iv"] and m["dm_qlike_vs_har_iv"]["stat"] < 0
            and m["dm_qlike_vs_har_iv"]["p_value"] < alpha]


def _better_than_record(lab: dict, alpha: float = ALPHA) -> list[dict]:
    return [m for m in _feasible(lab)
            if m["dm_qlike_vs_har_iv"] and m["dm_qlike_vs_har_iv"]["stat"] > 0
            and m["dm_qlike_vs_har_iv"]["p_value"] < alpha]


def _gloss(lab: dict, model: dict) -> str:
    return GLOSS.get(model["key"], model["description"].rstrip(".").lower())


def _var(lab: dict, method: str, level: str) -> dict:
    return lab["var_backtest"]["summary"][f"{method}@{level}"]


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
    champ, chall = _champ(lab), _chall(lab)
    o = prod["overall"]
    ov = prod["economic"]["vol_target_overlay"]
    econ = ov["risk"]
    champion = lab["config"]["champion"]
    return {
        "n_days": lab["sample"]["n_days"], "n_weeks": lab["sample"]["n_weeks"],
        "first": lab["sample"]["first"], "last": lab["sample"]["last"],
        "n_models": len(board["models"]), "n_feasible": len(_feasible(lab)),
        "mcs_size": len(board["mcs_qlike_included"]),
        "champ_qlike": champ["qlike"], "persistence_qlike": _model(lab, "persistence")["qlike"],
        "chall_label": chall["label"], "chall_qlike": chall["qlike"],
        "chall_dm": chall["dm_qlike_vs_har_iv"]["stat"], "chall_dm_p": chall["dm_qlike_vs_har_iv"]["p_value"],
        "chall_grids": chall["in_mcs_qlike_grids"], "spa_p": board["spa_qlike_vs_har_iv"]["p_consistent"],
        "lab_noise_floor": lab["noise_floor"]["har_direction_acc_pct_mean"],
        "prod_noise_floor": o["direction_noise_floor"]["dir_acc_pct_mean"],
        "dir_acc": o["har_dir_acc_pct"],
        "coverage": lab["conformal"][champion]["coverage_pct"],
        "mz_slope": o["har_mincer_zarnowitz"]["slope"], "mz_p": o["har_mincer_zarnowitz"]["p_unbiased"],
        "target_vol": ov["target_vol_pct"], "overlay_weeks": ov["n_weeks"],
        "fc_realized_vol": econ["forecast_vol_target"]["realized_vol_pct"],
        "bh_kurt": econ["buy_hold"]["excess_kurtosis"], "fc_kurt": econ["forecast_vol_target"]["excess_kurtosis"],
        "bh_worst": econ["buy_hold"]["worst_week_pct"], "fc_worst": econ["forecast_vol_target"]["worst_week_pct"],
        "hs_ind_pass": _var(lab, "historical_simulation", "0.05")["independence_pass_grids"],
        "champ_ind_pass": _var(lab, f"{champion}:fhs", "0.05")["independence_pass_grids"],
        "ovx_coef": next(t for t in lab["har_iv_coefficients"]["terms"] if t["term"] == "log OVX"),
    }


def _sharpe_clause(test: dict) -> str:
    """'does not raise the Sharpe ratio (...)' or the significant alternative."""
    diff, p = test["difference"], test["p_value"]
    detail = f"(difference {_fmt(diff, 2, True)} vs buy-and-hold, {_peq(p)})"
    if not _sig(p):
        return f"does not change the Sharpe ratio detectably {detail}"
    return f"{'raises' if diff > 0 else 'lowers'} the Sharpe ratio {detail}"


# --------------------------------------------------------------------------- abstract and summary

def abstract() -> str:
    lab, ledger, prod = _lab(), _ledger(), _prod()
    k = key_numbers()
    board = lab["leaderboard"]
    champ, chall = _champ(lab), _chall(lab)
    feasible, controls = _feasible(lab), _controls(lab)
    leak = _load("leak_anatomy.json")["synthetic_random_walk"]["random_forest"]
    pm = _load("wf_5y_purged.json")["results"]["1w"]["pnl_metrics"]["sharpe_ratio_annualized"]
    lk = _load("wf_5y_unpurged_LEAKED.json")["results"]["1w"]["pnl_metrics"]["sharpe_ratio_annualized"]
    worse = [m for m in _worse_than_record(lab) if m["key"] != "persistence"]
    ovx_only = _model(lab, "ovx_only")
    mcs = [_model(lab, key) for key in board["mcs_qlike_included"]]
    champion = lab["config"]["champion"]
    var99 = _var(lab, f"{champion}:fhs", "0.01")
    hs99 = _var(lab, "historical_simulation", "0.01")
    summary = ledger["summary"]
    overlay = prod["economic"]["vol_target_overlay"]

    tied = not _sig(ovx_only["dm_qlike_vs_har_iv"]["p_value"])
    ovx_clause = (f"a regression on OVX alone ({_fmt(ovx_only['qlike'])}, DM "
                  f"{_peq(ovx_only['dm_qlike_vs_har_iv']['p_value'])}) is "
                  + ("statistically tied with the full model" if tied else "statistically distinguishable from it"))
    control_text = ""
    if len(controls) == 1:
        control_text = (f"; the {_ordinal(k['n_models'])}, a timing control that reads OVX after the decision, "
                        f"is excluded")
    elif controls:
        control_text = f"; {len(controls)} timing controls that read data published after the decision are excluded"
    if champ["in_mcs_qlike"]:
        record = (f"The model of record is in the set (MCS p = {_fmt(champ['mcs_qlike_p'])}) and on "
                  f"{_count(champ['in_mcs_qlike_grids'], 5)} sampling grids.")
    else:
        record = (f"The model of record is outside it (MCS p = {_fmt(champ['mcs_qlike_p'])}), and inside it on "
                  f"{champ['in_mcs_qlike_grids']} of the 5 five-session sampling grids.")
    lowest_all = chall["qlike"] == min(m["qlike"] for m in board["models"])
    lowest_feasible = chall["qlike"] == min(m["qlike"] for m in feasible)
    rank = (f"has the lowest loss of all {k['n_models']} models" if lowest_all else
            f"has the lowest loss of the {k['n_feasible']} feasible models" if lowest_feasible else
            "is the challenger")
    in_set = ("is in the set over the full sample and on " if chall["in_mcs_qlike"] else
              "is outside the set over the full sample but in it on ")
    spa = k["spa_p"]
    if _sig(spa):
        spa_text = (f"Hansen's SPA test rejects the null that no model beats the model of record "
                    f"(p = {_fmt(spa)}); the variant still runs live as a challenger, and replaces the model of "
                    f"record only under a promotion test fixed in advance.")
    else:
        spa_text = (f"Hansen's SPA test gives p = {_fmt(spa)} against the null that no model beats the model of "
                    f"record ({_level(spa)}), so the variant runs live as a challenger under a promotion test "
                    f"fixed in advance.")
    dir_row = _ledger_row(ledger, "vol_dir_vs_noise_floor")
    direction = (f"Next-week direction accuracy ({k['dir_acc']}% on the production grid) is "
                 + ("not evidence of skill" if dir_row["status"] != "supported" else "above its noise floor")
                 + f": the same pipeline, run as HAR without OVX on simulated i.i.d. returns with nothing to "
                   f"forecast, scores {k['prod_noise_floor']}%.")
    return (
        f"This project forecasts the realized volatility of front-month NYMEX WTI crude oil over the next five "
        f"sessions from free data, and keeps a public forward record of its live forecasts that scores only entries "
        f"sealed in git before the settlement that opens their window. Over {k['n_days']:,} out-of-sample days "
        f"({k['first'][:4]}–{k['last'][:4]}), a heterogeneous autoregressive model with lagged implied volatility "
        f"(HAR-IV, the model of record) has a QLIKE loss of {_fmt(k['champ_qlike'])}, against "
        f"{_fmt(k['persistence_qlike'])} for a random-walk forecast. It is significantly better than "
        f"{_names(worse)} (Diebold-Mariano p < 0.05 each). Most of that skill is the options market's: the "
        f"elasticity on OVX is {_fmt(k['ovx_coef']['coef'], 2)}, and {ovx_clause}. Of the {k['n_feasible']} models "
        f"that use only information available at the decision, the 90% Model Confidence Set keeps "
        f"{len(mcs)} ({_names(mcs)}){control_text}. {record} {chall['label']}, {_gloss(lab, chall)}, {rank} "
        f"({_fmt(k['chall_qlike'])}, DM {_peq(k['chall_dm_p'])}) and {in_set}"
        f"{_count(chall['in_mcs_qlike_grids'], 5)} grids. {spa_text} The model of record's 80% adaptive conformal "
        f"intervals cover {k['coverage']}% of outcomes, and its weekly 99% Value-at-Risk breaches "
        f"{var99['exceedances_by_grid'][0]} times against {var99['expected']} expected on the production grid, with "
        f"breaches that pass the independence test on {var99['independence_pass_grids']}/5 grids against "
        f"{hs99['independence_pass_grids']}/5 for historical simulation. In the production validation (a recorded "
        f"run on downloaded data), sizing a {k['target_vol']:g}% volatility target on the forecast over "
        f"{k['overlay_weeks']} weeks keeps realized volatility at {k['fc_realized_vol']}%, cuts excess kurtosis from "
        f"{k['bh_kurt']} to {k['fc_kurt']}, and "
        f"{_sharpe_clause(overlay['sharpe_difference_tests']['forecast_minus_buy_hold'])}. {direction}\n\n"
        f"The project began as a machine-learning direction model whose backtest Sharpe ratio of "
        f"{_fmt(lk, 2)} after costs was a look-ahead leak; purged, it is {_fmt(pm, 2)}. The same leak gives a "
        f"20-tree random forest a gross (before costs) Sharpe ratio of {_fmt(leak['unpurged_leaked']['sharpe_mean'], 2)} "
        f"on a pure random walk. All {summary['rows']} hypotheses the project has tested are listed in a research "
        f"ledger with multiplicity adjustments: {len(summary['supported_surviving_bh_5pct'])} of the "
        f"{summary['by_status']['supported']} supported ones survive Benjamini-Hochberg at 5%, and "
        f"{summary['by_status']['retracted']} are retracted."
    )


def results_at_a_glance() -> str:
    lab, prod, ledger = _lab(), _prod(), _ledger()
    k = key_numbers()
    champ, chall = _champ(lab), _chall(lab)
    champion = lab["config"]["champion"]
    persistence = _model(lab, "persistence")
    raw, ovx_only = _model(lab, "raw_ovx"), _model(lab, "ovx_only")
    var95, var99 = _var(lab, f"{champion}:fhs", "0.05"), _var(lab, f"{champion}:fhs", "0.01")
    hs95, hs99 = _var(lab, "historical_simulation", "0.05"), _var(lab, "historical_simulation", "0.01")
    tests = prod["economic"]["vol_target_overlay"]["sharpe_difference_tests"]["forecast_minus_buy_hold"]
    vrp = prod["economic"]["variance_risk_premium"]
    swap = vrp["sharpe_positive_test"]["short_vol_swap"]
    ci = vrp["mean_premium_variance_ci95"]
    leak = _load("leak_anatomy.json")
    purged = _load("wf_5y_purged.json")["results"]["1w"]["pnl_metrics"]["sharpe_ratio_annualized"]
    feasible_benchmarks = len(_feasible(lab)) - 1

    pdm = persistence["dm_qlike_vs_har_iv"]
    forecastable = "Yes" if pdm["stat"] < 0 and _sig(pdm["p_value"]) else "Not shown"
    implied = [raw, ovx_only]
    if all(m["dm_qlike_vs_har_iv"]["stat"] < 0 and _sig(m["dm_qlike_vs_har_iv"]["p_value"]) for m in implied):
        adds = "Yes"
    elif any(m["dm_qlike_vs_har_iv"]["stat"] > 0 and _sig(m["dm_qlike_vs_har_iv"]["p_value"]) for m in implied):
        adds = "No: implied volatility alone does better"
    else:
        adds = "Not detectably"
    if _sig(k["spa_p"]):
        better = f"Yes: {chall['label']}"
    elif _sig(k["chall_dm_p"]) and k["chall_dm"] > 0:
        better = f"Possibly: {chall['label']}"
    else:
        better = "Not shown"
    mz = champ["mincer_zarnowitz"]
    calibrated = "Unbiasedness not rejected" if not _sig(mz["p_unbiased"]) else "Biased (Mincer-Zarnowitz rejects)"
    clusters = (var95["independence_pass_grids"] == 5 and var99["independence_pass_grids"] == 5
                and hs95["independence_pass_grids"] + hs99["independence_pass_grids"] < 10)
    var_answer = "Yes: its breaches do not cluster" if clusters else "Partly"
    money = "No, it controls risk" if not _sig(tests["p_value"]) else (
        "Yes" if tests["difference"] > 0 else "No: it lowers the Sharpe ratio")
    vol_pts = _ledger_row(ledger, "vrp_exists")["status"] == "supported"
    var_sig = ci[0] > 0 or ci[1] < 0
    vrp_answer = (("In vol points, yes" if vol_pts else "In vol points, not shown") + "; "
                  + ("in variance, significant" if var_sig else "in variance and as a return, not significant"))
    direction = "No" if _ledger_row(ledger, "vol_dir_vs_noise_floor")["status"] != "supported" else "Above its floor"

    rows = ["| Question | Answer | Evidence | Source |", "|---|---|---|---|"]
    rows.append(f"| Is next-week WTI volatility forecastable from free data? | {forecastable} | QLIKE "
                f"{_fmt(k['champ_qlike'])} vs {_fmt(k['persistence_qlike'])} for persistence (DM "
                f"{_peq(pdm['p_value'])}); {len(_worse_than_record(lab))} of the {feasible_benchmarks} feasible "
                f"benchmarks significantly worse | lab |")
    rows.append(f"| Does it add to implied volatility? | {adds} | OVX regression {_fmt(ovx_only['qlike'])} (DM "
                f"{_peq(ovx_only['dm_qlike_vs_har_iv']['p_value'])}), raw OVX {_fmt(raw['qlike'])} (DM "
                f"{_peq(raw['dm_qlike_vs_har_iv']['p_value'])}); OVX elasticity {_fmt(k['ovx_coef']['coef'], 2)} "
                f"(HAC t {_fmt(k['ovx_coef']['t_hac'], 1)}) | lab |")
    rows.append(f"| Is anything better? | {better} | {chall['label']} QLIKE {_fmt(k['chall_qlike'])} (DM "
                f"{_peq(k['chall_dm_p'])}); model of record {'inside' if champ['in_mcs_qlike'] else 'outside'} the "
                f"90% MCS (p = {_fmt(champ['mcs_qlike_p'])}); SPA p = {_fmt(k['spa_p'])} after the search; "
                f"challenger on the live record | lab |")
    rows.append(f"| Are the forecasts calibrated? | {calibrated} | Mincer-Zarnowitz slope {_fmt(mz['slope'], 2)} "
                f"(joint p = {_p(mz['p_unbiased'])}); 80% intervals cover {k['coverage']}% | lab |")
    rows.append(f"| Does it make a better weekly VaR? | {var_answer} | Breach independence passes on "
                f"{var95['independence_pass_grids']}/5 grids at 95% and {var99['independence_pass_grids']}/5 at 99%, "
                f"vs {hs95['independence_pass_grids']}/5 and {hs99['independence_pass_grids']}/5 for historical "
                f"simulation; 99%: {var99['exceedances_by_grid'][0]} breaches vs {var99['expected']} expected on the "
                f"production grid (Kupiec passes {var99['kupiec_pass_grids']}/5) | lab |")
    rows.append(f"| Does it make money as a vol target? | {money} | Sharpe difference vs buy-and-hold "
                f"{_fmt(tests['difference'], 2, True)} ({_peq(tests['p_value'])}); excess kurtosis "
                f"{k['fc_kurt']} vs {k['bh_kurt']} | production run |")
    rows.append(f"| Is there a variance risk premium? | {vrp_answer} | IV above next-month RV in "
                f"{vrp['share_positive_pct']}% of periods; mean variance premium "
                f"{_fmt(vrp['mean_premium_variance_pts'], 0)} (95% CI {_fmt(ci[0], 0)} to {_fmt(ci[1], 0)}); short "
                f"vol-swap Sharpe {vrp['short_vol_swap_sharpe']} (one-sided {_peq(swap['p_value_one_sided'])}, "
                f"non-normal test) | production run |")
    rows.append(f"| Is direction accuracy evidence of skill? | {direction} | {k['dir_acc']}% observed vs "
                f"{k['prod_noise_floor']}% for HAR without OVX on simulated returns with nothing to forecast | "
                f"production run |")
    rows.append(f"| Was the original direction model's Sharpe real? | No, a look-ahead leak | Leaked label agrees in "
                f"sign with the test label {leak['analytic']['p_sign_agree_leaked_pct']}% of the time on a random "
                f"walk; purged, the Sharpe ratio is {_fmt(purged, 2)} | leak anatomy; recorded runs |")
    rows.append("")
    rows.append(f"Source: {SOURCE_NOTE}")
    return "\n".join(rows)


# --------------------------------------------------------------------------- design

def design_block() -> str:
    lab = _lab()
    c, smp = lab["config"], lab["sample"]
    mcs = c["mcs"]
    lgbm_weeks = _code_constant("backend/vol_lab.py", "LGBM_RETRAIN_WEEKS")
    controls = _controls(lab)
    control_text = ""
    if controls:
        keys = _join([f"`{m['key']}`" for m in controls])
        control_text = (f" The timing control {keys} reads the test day's own OVX close, published after the "
                        f"decision; it is reported but excluded from every Model Confidence Set, the SPA test, the "
                        f"out-of-sample model selection and the choice of challenger.")
    return "\n".join([
        f"- **Target.** Annualized realized volatility of the next {c['horizon_sessions']} settlement-to-settlement "
        f"log returns of `CL=F`, the front-month NYMEX WTI future.",
        f"- **Decision time.** The 14:30 ET settlement that opens the target window. Every input is published by "
        f"then; OVX, which settles at 16:15 ET, enters with a {c['ovx_lag_sessions']}-session lag, and the test "
        f"day's own high and low (which run to 17:00 ET) never enter a feature.",
        f"- **Walk-forward.** Expanding window from the first {c['min_train_rows']} rows, with a forecast on every "
        f"out-of-sample day. The log regressions are refit every day; GARCH and GJR-GARCH are re-estimated weekly "
        f"and filtered daily; LightGBM is retrained every {lgbm_weeks} weeks. For every model trained on targets, "
        f"the last {c['purge_rows']} training rows are purged, because their targets overlap the test target and "
        f"are not known at the decision.",
        f"- **Sample.** {smp['n_days']:,} out-of-sample days, {smp['first']} to {smp['last']}. Losses and "
        f"tests use every day. Each of the five {c['step']}-session sampling grids (offsets 0–4, "
        f"{smp['n_weeks']} non-overlapping weeks each; grid 0 is the production validator's) is also scored on its "
        f"own, and VaR is scored only there, since it needs independent outcomes.",
        "- **Forecasts.** Losses, VaR and risk use the variance-consistent forecast (the median times a Duan "
        "smearing factor). The direction call and the intervals use the median.",
        f"- **Inference.** Model Confidence Set at {100 - mcs['size'] * 100:g}% ({mcs['statistic']} statistic, "
        f"{mcs['reps']:,} {mcs['bootstrap']}-bootstrap replications, blocks of {mcs['block_daily']} days or "
        f"{mcs['block_weekly']} weeks, seed {mcs['seed']}); Diebold-Mariano with Newey-West variance and the "
        f"Harvey-Leybourne-Newbold correction; Hansen's SPA against the model of record.{control_text}",
    ])


# --------------------------------------------------------------------------- 2.1 the field

def leaderboard_table() -> str:
    lab = _lab()
    champ_key, chall_key = lab["config"]["champion"], lab["config"]["challenger"]
    board = lab["leaderboard"]
    rows = ["| Model | Family | QLIKE | DM vs model of record (p) | DM range over the 5 sampling grids | "
            "MCS p | In 90% MCS (grids) | Realized utility (% / yr) |",
            "|---|---|---:|---:|---:|---:|---:|---:|"]
    for m in board["models"]:
        name = m["label"]
        if m["key"] in (champ_key, chall_key):
            name = f"**{name}**" + (" *(challenger)*" if m["key"] == chall_key else "")
        dm = m["dm_qlike_vs_har_iv"]
        dm_text = "—" if dm is None else f"{_fmt(dm['stat'], 2, signed=True)} ({_p(dm['p_value'])})"
        grid = m["dm_qlike_vs_har_iv_by_grid"]
        grid_text = "—" if not grid else f"{_fmt(min(grid), 2, True)} to {_fmt(max(grid), 2, True)}"
        if m.get("timing_control"):
            mcs_p, mcs = "—", "excluded: timing control"
        else:
            mcs_p = _fmt(m["mcs_qlike_p"], 2)
            mcs = f"{'yes' if m['in_mcs_qlike'] else 'no'} ({m['in_mcs_qlike_grids']}/5)"
        rows.append(f"| {name} | {m['family']} | {_fmt(m['qlike'])} | {dm_text} | {grid_text} | {mcs_p} | {mcs} | "
                    f"{_fmt(m['realized_utility_pct'], 2)} |")
    spa = board["spa_qlike_vs_har_iv"]
    controls = _controls(lab)
    control_text = ""
    if controls:
        control_text = (f" {_names(controls)} is a timing control: it reads the test day's OVX close (16:15 ET), "
                        f"published after the 14:30 ET decision, so it keeps its losses and DM tests but is excluded "
                        f"from every Model Confidence Set, the SPA test, the out-of-sample selection and the choice "
                        f"of challenger.")
    rows.append("")
    rows.append(f"QLIKE on variance-consistent forecasts over every out-of-sample day; DM = Diebold-Mariano "
                f"(Newey-West, Harvey-Leybourne-Newbold), positive = better than the model of record; MCS = "
                f"Hansen-Lunde-Nason Model Confidence Set over the {len(_feasible(lab))} feasible models (range "
                f"statistic, stationary bootstrap, block {lab['config']['mcs']['block_daily']} days).{control_text} "
                f"Hansen's SPA test of the null that no model beats the model of record, over the "
                f"{len(spa['models'])} feasible alternatives: p = {_fmt(spa['p_consistent'])}. Realized utility: "
                f"Bollerslev, Hood, Huss and Pedersen (2018), maximum {board['realized_utility_max_pct']}. Source: lab.")
    return "\n".join(rows)


def field_findings() -> str:
    """The reading of the leaderboard: who is in the set, who is significantly worse, what MSE says."""
    lab, ledger = _lab(), _ledger()
    board = lab["leaderboard"]
    feasible = _feasible(lab)
    champ, chall = _champ(lab), _chall(lab)
    mcs = [_model(lab, key) for key in board["mcs_qlike_included"]]
    worse = {m["key"] for m in _worse_than_record(lab)}
    out = []

    uses = [m for m in mcs if "implied" in _inputs(m["key"])]
    if len(uses) == len(mcs):
        also = [m for m in mcs if "range" in _inputs(m["key"])]
        use_text = "every one of them uses implied volatility" + (
            f" ({_names(also)} also uses the daily range)" if also else "")
    else:
        use_text = f"{len(uses)} of them use implied volatility"
    para = [f"The 90% Model Confidence Set keeps {len(mcs)} of the {len(feasible)} feasible models ({_names(mcs)}), "
            f"and {use_text}."]
    row = _ledger_row(ledger, "vol_mcs_excludes_record")
    if not champ["in_mcs_qlike"]:
        bh = "survives" if row.get("survives_bh_5pct") else "does not survive"
        para.append(f"The model of record is outside it (MCS p = {_fmt(champ['mcs_qlike_p'])}; the ledger counts "
                    f"this as supported, but it {bh} the Benjamini-Hochberg adjustment) and is inside it on "
                    f"{champ['in_mcs_qlike_grids']} of the 5 sampling grids.")
    else:
        para.append(f"The model of record is in it (MCS p = {_fmt(champ['mcs_qlike_p'])}).")
    returns_only = [m for m in feasible if _inputs(m["key"]) == {"returns"}]
    if returns_only and all(m["key"] in worse for m in returns_only):
        para.append(f"Every forecaster built from close-to-close returns alone ({_names(returns_only)}) is "
                    f"significantly worse than the model of record (DM p < 0.05 each).")
    else:
        sig = [m for m in returns_only if m["key"] in worse]
        para.append(f"Of the {len(returns_only)} forecasters built from close-to-close returns alone, "
                    f"{len(sig)} are significantly worse than the model of record ({_names(sig)}).")
    lgbm = _model(lab, "lgbm")
    ldm = lgbm["dm_qlike_vs_har_iv"]
    if lgbm["key"] in worse:
        para.append(f"So is LightGBM (DM {_peq(ldm['p_value'])}), although it is given every input the HAR models "
                    f"use: the HAR terms, OVX, the range-based terms and the downside semivolatility.")
    else:
        para.append(f"LightGBM, given the HAR terms, OVX, the range-based terms and the downside semivolatility, is "
                    f"not significantly different from the model of record ({_peq(ldm['p_value'])}).")
    ovx_only = _model(lab, "ovx_only")
    op = ovx_only["dm_qlike_vs_har_iv"]["p_value"]
    if not _sig(op):
        para.append(f"The model of record and the implied-vol regression are statistically indistinguishable "
                    f"({_peq(op)}): the options market already prices most of what the realized-volatility terms "
                    f"know.")
    else:
        para.append(f"The model of record and the implied-vol regression differ significantly ({_peq(op)}).")
    out.append(" ".join(para))

    para = []
    top_q = chall["qlike"] == min(m["qlike"] for m in board["models"])
    top_u = chall["realized_utility_pct"] == max(m["realized_utility_pct"] for m in board["models"])
    udm = chall["dm_utility_vs_har_iv"]
    if top_q and top_u:
        para.append(f"{chall['label']} has the lowest QLIKE and the highest realized utility in the field, and "
                    f"beats the model of record on both (DM {_peq(chall['dm_qlike_vs_har_iv']['p_value'])} on "
                    f"QLIKE, {_peq(udm['p_value'])} on utility).")
    else:
        para.append(f"{chall['label']} has a QLIKE of {_fmt(chall['qlike'])} and a realized utility of "
                    f"{_fmt(chall['realized_utility_pct'], 2)}% (DM vs the model of record "
                    f"{_peq(chall['dm_qlike_vs_har_iv']['p_value'])} and {_peq(udm['p_value'])}).")
    mse_set = board["mcs_mse_included"]
    mdm = chall["dm_mse_vs_har_iv"]
    if len(mse_set) == len(feasible):
        para.append(f"On MSE, which a few extreme weeks dominate, nothing is distinguishable: the MSE confidence set "
                    f"keeps all {len(feasible)} feasible models, and the challenger's MSE difference from the model "
                    f"of record has DM {_peq(mdm['p_value'])}.")
    else:
        para.append(f"On MSE, which a few extreme weeks dominate, the confidence set keeps {len(mse_set)} of the "
                    f"{len(feasible)} feasible models; the challenger's MSE difference from the model of record has "
                    f"DM {_peq(mdm['p_value'])}.")
    spa = board["spa_qlike_vs_har_iv"]["p_consistent"]
    para.append(f"The variant was also found by searching this sample, which is what the SPA test discounts for "
                f"(p = {_fmt(spa)}, {_level(spa)}). That is why it competes live rather than replacing the model of "
                f"record on its historical record.")
    out.append(" ".join(para))
    return "\n\n".join(out)


# --------------------------------------------------------------------------- 2.2 coefficients

def coefficients_table() -> str:
    c = _lab()["har_iv_coefficients"]
    rows = ["| Term | Coefficient | HAC t |", "|---|---:|---:|"]
    for t in c["terms"]:
        rows.append(f"| {t['term']} | {_fmt(t['coef'], 3, True)} | {_fmt(t['t_hac'], 2, True)} |")
    rows.append("")
    rows.append(f"Full-sample fit of the model of record: {c['n_rows']:,} daily rows, R² {c['r2_log']} on log "
                f"realized vol, Newey-West t with {c['hac_lags']} lags (the five-session targets overlap). Source: lab.")
    return "\n".join(rows)


def coefficients_reading() -> str:
    lab = _lab()
    terms = {t["term"]: t for t in lab["har_iv_coefficients"]["terms"]}
    ovx = terms["log OVX"]
    realized = [terms[k] for k in ("log RV5", "log RV22", "log RV66")]
    sig = [t for t in realized if abs(t["t_hac"]) >= 1.96]
    insig = [t for t in realized if abs(t["t_hac"]) < 1.96]
    total = sum(t["coef"] for t in realized)

    def term(t: dict) -> str:
        return f"{t['term']} ({_fmt(t['coef'], 3, True)}, t {_fmt(t['t_hac'], 2, True)})"

    text = [f"The OVX elasticity is {_fmt(ovx['coef'], 2)} (HAC t {_fmt(ovx['t_hac'], 1)})."]
    if sig:
        text.append(f"Of the realized-volatility terms, {_join([term(t) for t in sig])} "
                    f"{'is' if len(sig) == 1 else 'are'} significant at 5%"
                    + (f" and {_join([term(t) for t in insig])} {'is' if len(insig) == 1 else 'are'} not." if insig
                       else "."))
    else:
        text.append("None of the realized-volatility terms is significant at 5%.")
    pair = [terms["log RV22"], terms["log RV66"]]
    if all(t in sig for t in pair) and pair[0]["coef"] * pair[1]["coef"] < 0:
        text.append(f"The significant pair has opposite signs and nearly offsets (the three realized coefficients "
                    f"sum to {_fmt(total, 3, True)}), so the realized terms enter mainly as the slope between the past "
                    f"month's and the past quarter's volatility, not as a level.")
    raw = _model(lab, "raw_ovx")
    ratio = raw["mean_ratio_actual_to_forecast"]
    text.append(f"Used directly, OVX {'overstates' if ratio < 1 else 'understates'} realized volatility: realized "
                f"averages {_fmt(ratio)} times the previous session's OVX. The constant absorbs that gap: the variance "
                f"risk premium (section 2.5) and OVX's basis to the front-month future.")
    return " ".join(text)


# --------------------------------------------------------------------------- 2.3 calibration

def calibration_block() -> str:
    lab = _lab()
    champ_key, chall_key = lab["config"]["champion"], lab["config"]["challenger"]
    conf = lab["conformal"]
    o = _prod()["overall"]
    rows = ["| Model, sample | MZ intercept | MZ slope (SE) | Joint p (unbiased) | Actual / forecast | 80% coverage | "
            "Coverage by year (min to max) | Interval score |", "|---|---:|---:|---:|---:|---:|---|---:|"]
    for key in (champ_key, chall_key):
        m, c = _model(lab, key), conf[key]
        mz = m["mincer_zarnowitz"]
        years = list(c["coverage_by_year_pct"].values())
        rows.append(f"| {m['label']}, every day | {_fmt(mz['intercept'], 3, True)} | {_fmt(mz['slope'], 2)} "
                    f"({_fmt(mz['slope_se'], 2)}) | {_p(mz['p_unbiased'])} | {_fmt(m['mean_ratio_actual_to_forecast'], 3)} | "
                    f"{c['coverage_pct']}% | {min(years)}% to {max(years)}% | {c['interval_score_pct']} |")
    mz = o["har_mincer_zarnowitz"]
    rows.append(f"| HAR-IV, production weekly grid ({o['n']} weeks; production run) | {_fmt(mz['intercept'], 3, True)} | "
                f"{_fmt(mz['slope'], 2)} ({_fmt(mz['slope_se'], 2)}) | {_p(mz['p_unbiased'])} | "
                f"{_fmt(o['har_mean_ratio_actual_to_forecast'], 3)} | — | — | — |")
    rows.append("")
    rows.append(f"Mincer-Zarnowitz regression of realized on forecast variance with a HAC covariance. Intervals: "
                f"{lab['config']['conformal']['method']}, step {lab['config']['conformal']['gamma']}, scored only "
                f"after each target has settled; interval score of Gneiting and Raftery (2007), in vol points "
                f"(lower is better). The every-day rows are from the lab; the production-grid row is from "
                f"`data/vol_forecast_validation.json`, a recorded run on downloaded data.")
    return "\n".join(rows)


def calibration_reading() -> str:
    lab, prod = _lab(), _prod()
    champ, chall = _champ(lab), _chall(lab)
    o = prod["overall"]
    samples = [(f"{champ['label']}, every day", champ["mincer_zarnowitz"]),
               (f"{chall['label']}, every day", chall["mincer_zarnowitz"]),
               ("the model of record on the production grid", o["har_mincer_zarnowitz"])]
    rejected = [name for name, mz in samples if _sig(mz["p_unbiased"])]
    ps = ", ".join(_p(mz["p_unbiased"]) for _, mz in samples)
    text = []
    if not rejected:
        text.append(f"No sample rejects unbiasedness at 5% (joint p {ps}).")
    else:
        text.append(f"Unbiasedness is rejected at 5% for {_join(rejected)} (joint p {ps}).")
    every, weekly = champ["mincer_zarnowitz"]["slope"], o["har_mincer_zarnowitz"]["slope"]
    if (every - 1) * (weekly - 1) < 0:
        text.append(f"The model of record's slope is unstable: it is {'below' if every < 1 else 'above'} one on the "
                    f"every-day sample ({_fmt(every, 2)}) and {'below' if weekly < 1 else 'above'} one on the "
                    f"production weekly grid ({_fmt(weekly, 2)}). A regression on variances is dominated by a few "
                    f"extreme weeks, and the two samples catch different ones.")
    else:
        text.append(f"The model of record's slope is {_fmt(every, 2)} on the every-day sample and {_fmt(weekly, 2)} "
                    f"on the production weekly grid.")
    c = lab["conformal"][lab["config"]["champion"]]
    years = list(c["coverage_by_year_pct"].values())
    target = c["target_coverage_pct"]
    se = (target / 100 * (1 - target / 100) / 50) ** 0.5 * 100
    text.append(f"The model of record's intervals cover {c['coverage_pct']}% of the {c['n_days']:,} scored days "
                f"against a target of {target:g}%. Coverage by calendar year ranges {min(years)}% to {max(years)}%; "
                f"with about fifty independent weeks a year, one year's coverage has a standard error of about "
                f"{se:.0f} points, so deviations of that size are expected.")
    return " ".join(text)


# --------------------------------------------------------------------------- 2.4 risk

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
    rows.append("Weekly (5-session) VaR of a long front-month position, scored on each of the five 5-session "
                "sampling grids (non-overlapping outcomes); a test passes at 5%. Kupiec (1995) unconditional "
                "coverage, Christoffersen (1998) independence and conditional coverage, Engle-Manganelli (2004) DQ. "
                "Source: lab.")
    return "\n".join(rows)


def var_reading() -> str:
    lab = _lab()
    summary = lab["var_backtest"]["summary"]
    label = {m["key"]: m["label"] for m in lab["leaderboard"]["models"]}
    levels = ("0.05", "0.01")
    hs = {lvl: summary[f"historical_simulation@{lvl}"] for lvl in levels}
    fhs = {lvl: {k.split(":")[0]: v for k, v in summary.items() if k.endswith(f":fhs@{lvl}")} for lvl in levels}
    methods = list(fhs["0.05"])
    text = [f"Historical simulation, with no volatility model, passes Kupiec's coverage test on "
            f"{hs['0.05']['kupiec_pass_grids']}/5 grids at 95% and {hs['0.01']['kupiec_pass_grids']}/5 at 99%; at 99% "
            f"it breaches {min(hs['0.01']['exceedances_by_grid'])} to {max(hs['0.01']['exceedances_by_grid'])} times "
            f"per grid against {hs['0.01']['expected']} expected. Its breaches cluster: it passes the independence "
            f"test on {hs['0.05']['independence_pass_grids']}/5 grids at 95% and {hs['0.01']['independence_pass_grids']}/5 "
            f"at 99%."]
    clean = [m for m in methods if all(fhs[lvl][m]["independence_pass_grids"] == 5 for lvl in levels)]
    if len(clean) == len(methods):
        text.append("Scaling by any of the volatility forecasts removes the clustering: every scaled method passes "
                    "the independence test on every grid at both levels.")
    else:
        rest = []
        for lvl in levels:
            fails = [f"{label.get(m, m)} {fhs[lvl][m]['independence_pass_grids']}/5" for m in methods
                     if fhs[lvl][m]["independence_pass_grids"] < 5]
            if fails:
                rest.append(f"at {100 - float(lvl) * 100:.0f}%, {_join(fails)}")
        text.append(f"Scaling by a volatility forecast removes most of the clustering: with "
                    f"{_join([label.get(m, m) for m in clean])} the breaches pass the independence test on every grid "
                    f"at both levels; the other scaled methods pass on fewer grids ({'; '.join(rest)}).")
    pairs = [(m, g) for m in methods for g in range(5)]
    over = sum(fhs["0.01"][m]["exceedances_by_grid"][g] > fhs["0.01"][m]["expected"] for m, g in pairs)
    kupiec = sum(fhs["0.01"][m]["kupiec_pass_grids"] for m in methods)
    text.append(f"At 99% the scaled methods breach more often than nominal on {over} of {len(pairs)} grid-method "
                f"pairs, and Kupiec's test passes on {kupiec} of {len(pairs)}: the empirical quantile of past "
                f"standardized outcomes tends to understate the 1% tail that followed.")
    return " ".join(text)


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
                f"({_peq(tests['forecast_minus_buy_hold']['p_value'])}), forecast minus trailing "
                f"{_fmt(tests['forecast_minus_trailing']['difference'], 2, True)} "
                f"({_peq(tests['forecast_minus_trailing']['p_value'])}). Source: production run.")
    return "\n".join(rows)


def overlay_reading() -> str:
    ov = _prod()["economic"]["vol_target_overlay"]
    r, target = ov["risk"], ov["target_vol_pct"]
    fc, tr, bh = r["forecast_vol_target"], r["trailing_vol_target"], r["buy_hold"]
    tests = ov["sharpe_difference_tests"]
    text = []
    gap = fc["realized_vol_pct"] - target
    if abs(gap) <= 1:
        text.append(f"Sized on the forecast, the book's realized volatility lands on the target ({fc['realized_vol_pct']}% "
                    f"against {target:g}%)")
    else:
        text.append(f"Sized on the forecast, the book's realized volatility is {fc['realized_vol_pct']}% against a "
                    f"{target:g}% target")
    text[-1] += (f", and excess kurtosis falls from {bh['excess_kurtosis']} to {fc['excess_kurtosis']}.")
    tgap = tr["realized_vol_pct"] - target
    if tgap > 1:
        text.append(f"Sizing on trailing volatility overshoots the target ({tr['realized_vol_pct']}%).")
    elif tgap < -1:
        text.append(f"Sizing on trailing volatility undershoots the target ({tr['realized_vol_pct']}%).")
    p1, p2 = tests["forecast_minus_buy_hold"]["p_value"], tests["forecast_minus_trailing"]["p_value"]
    if not _sig(p1) and not _sig(p2):
        text.append(f"Neither raises the Sharpe ratio detectably: the differences are small and not significant "
                    f"({_peq(p1)} against buy-and-hold, {_peq(p2)} against trailing sizing). This matches the "
                    f"evidence on volatility targeting outside equities (Harvey et al. 2018). The forecast controls "
                    f"risk; it does not create return.")
    else:
        text.append(f"The Sharpe differences have {_peq(p1)} against buy-and-hold and {_peq(p2)} against trailing "
                    f"sizing.")
    return " ".join(text)


# --------------------------------------------------------------------------- 2.5 VRP

def vrp_block() -> str:
    vrp = _prod()["economic"]["variance_risk_premium"]
    ledger = _ledger()
    t = vrp["timing_regression"]
    ci = vrp["mean_premium_variance_ci95"]
    swap, short_var = vrp["sharpe_positive_test"]["short_vol_swap"], vrp["sharpe_positive_test"]["short_variance"]
    worst = "; ".join(f"from {w['start']}, {_fmt(w['premium_vol_pts'], 1)} vol pts" for w in vrp["worst_periods"])
    exists = _ledger_row(ledger, "vrp_exists")["status"] == "supported"
    var_sig = ci[0] > 0 or ci[1] < 0
    swap_p = swap["p_value_one_sided"]
    timing_p = vrp["timing_p_value"]
    return "\n".join([
        f"- **In vol points the premium is {'there' if exists else 'not established'}.** OVX exceeded the realized "
        f"volatility of the following 20 sessions in {vrp['share_positive_pct']}% of {vrp['n_periods']} "
        f"non-overlapping periods, by {vrp['mean_premium_vol_pts']} points on average.",
        f"- **In variance units, the units an option seller is paid in, it is "
        f"{'significant' if var_sig else 'not significant'}.** The mean premium is "
        f"{_fmt(vrp['mean_premium_variance_pts'], 1)} variance points (95% Newey-West CI {_fmt(ci[0], 0)} to "
        f"{_fmt(ci[1], 0)}): the few periods where realized volatility explodes ({worst}) "
        f"{'outweigh' if vrp['mean_premium_variance_pts'] < 0 else 'do not outweigh'} the many small gains.",
        f"- **Harvesting it.** A short vol-swap proxy has an annualized Sharpe ratio of {vrp['short_vol_swap_sharpe']}, "
        f"but its period returns are skewed ({_fmt(swap['skewness'], 2)}) and fat-tailed (excess kurtosis "
        f"{swap['excess_kurtosis']}); the one-sided test of a positive Sharpe ratio with the Mertens (2002) / Opdyke "
        f"(2007) standard error, which allows for both, gives {_peq(swap_p)} ({_level(swap_p)}). The convex "
        f"short-variance position has a Sharpe ratio of {vrp['short_variance_sharpe']} ({_peq(short_var['p_value_one_sided'])}; "
        f"{vrp['short_variance_sharpe_ex_worst_year']} without {vrp['worst_year']}).",
        f"- **Timing it with the forecast {'does not help' if not _sig(t['p_value']) and not _sig(timing_p) else 'may help'}.** "
        f"Regressing short-variance P&L on OVX minus the forecast: slope {_fmt(t['slope'], 2)}, HAC t {t['t_hac']}, "
        f"{_peq(t['p_value'])} (n = {t['n']}); selling only when OVX is above the forecast against selling only "
        f"when it is below: {_peq(timing_p)}.",
        "",
        "Source: production run (`data/vol_forecast_validation.json`).",
    ])


def vrp_reading() -> str:
    vrp = _prod()["economic"]["variance_risk_premium"]
    ci = vrp["mean_premium_variance_ci95"]
    text = []
    if vrp["share_positive_pct"] > 50:
        text.append("The usual statement, that implied volatility exceeds realized most of the time, is true here. "
                    "It is also the wrong unit.")
    if vrp["mean_premium_variance_pts"] < 0 and vrp["short_variance_sharpe_ex_worst_year"] > 0:
        text.append(f"A variance seller is paid in variance, and in variance the {vrp['worst_year']} periods "
                    f"outweigh the rest of the sample.")
    elif ci[0] <= 0 <= ci[1]:
        text.append("A variance seller is paid in variance, and in variance the premium is not distinguishable "
                    "from zero.")
    text.append("Measuring the premium properly needs a long history of oil option prices, which is not freely "
                "available.")
    return " ".join(text)


# --------------------------------------------------------------------------- 2.6 direction

def noise_block() -> str:
    lab, prod, ledger = _lab(), _prod(), _ledger()
    nf = lab["noise_floor"]
    o = prod["overall"]
    pnf = o["direction_noise_floor"]
    board = lab["leaderboard"]
    mr_row = _ledger_row(ledger, "vol_dir_vs_mean_reversion")
    sim = f"Simulated {nf['process'][0].lower()}{nf['process'][1:]}, HAR without OVX"
    lines = [
        "| Pipeline run on | Source | Direction accuracy | Mean-reversion rule |", "|---|---|---:|---:|",
        f"| WTI, production weekly grid ({o['n']} weeks) | production run | {o['har_dir_acc_pct']}% | "
        f"{o['mean_reversion_dir_acc_pct']}% |",
        f"| {sim}, weekly grid, {pnf['sims']} simulations | production run | {pnf['dir_acc_pct_mean']}% "
        f"({pnf['dir_acc_pct_range'][0]}–{pnf['dir_acc_pct_range'][1]}) | {pnf['mean_reversion_dir_acc_pct_mean']}% |",
        f"| WTI, every out-of-sample day | lab | {_fmt(_champ(lab)['direction_acc_pct'], 1)}% | "
        f"{board['mean_reversion_acc_pct']}% |",
        f"| {sim}, weekly grid, {nf['sims']} simulations | lab | {nf['har_direction_acc_pct_mean']}% "
        f"({nf['har_direction_acc_pct_range'][0]}–{nf['har_direction_acc_pct_range'][1]}) | "
        f"{nf['mean_reversion_acc_pct_mean']}% |",
        "",
        f"{nf['note']} The call compares next week's realized volatility with this week's, and a five-session "
        f"realized volatility is a noisy estimate that reverts toward its mean whether or not the underlying "
        f"volatility moves. Accuracy against that comparison measures the noise in the proxy. The losses above "
        f"measure forecasting skill.",
    ]
    if pnf["dir_acc_pct_mean"] > pnf["mean_reversion_dir_acc_pct_mean"]:
        lines[-1] += (f" With nothing to forecast, HAR also beats the mean-reversion rule ({pnf['dir_acc_pct_mean']}% "
                      f"vs {pnf['mean_reversion_dir_acc_pct_mean']}%), so beating that rule is not evidence of skill "
                      f"either" + ("; the ledger retracts that claim." if mr_row["status"] == "retracted" else "."))
    return "\n".join(lines)


# --------------------------------------------------------------------------- 2.7 robustness

def robustness_block() -> str:
    lab, prod = _lab(), _prod()
    o = prod["overall"]
    board = lab["leaderboard"]
    champ, chall = _champ(lab), _chall(lab)
    controls = _controls(lab)
    sel = lab["oos_selection"]
    ret = prod["economic"]["returns"]
    qby = champ["qlike_by_grid"]
    picks = sorted(sel["pick_share_pct"].items(), key=lambda kv: -kv[1])[:3]
    range_set = board["mcs_qlike_range_proxy_included"]
    kept = _names([_model(lab, key) for key in range_set])
    chosen = ", ".join(f"{_model(lab, key)['label']} {share}%" for key, share in picks)
    rob = board["mcs_robustness_qlike"]

    def members(keys: list[str]) -> str:
        return _names([_model(lab, key) for key in keys])

    blocks = sorted((int(key.split("_")[1]), value) for key, value in rob.items() if key.startswith("block_"))
    periods = [(key, value) for key, value in rob.items() if isinstance(value, dict)]
    out = [
        f"- **Sampling grids.** The model of record's QLIKE on the five 5-session sampling grids ranges "
        f"{_fmt(min(qby))} to {_fmt(max(qby))}; the challenger is in the 90% MCS on "
        f"{chall['in_mcs_qlike_grids']}/5 and the model of record on {champ['in_mcs_qlike_grids']}/5. A "
        f"conclusion that holds on one grid and not the next is reported as fragile.",
        f"- **Confidence-set settings.** The full-sample QLIKE set (blocks of {lab['config']['mcs']['block_daily']} "
        f"days) keeps {members(board['mcs_qlike_included'])}. "
        + " ".join(f"With {size}-day blocks it keeps {members(keys)}." for size, keys in blocks) + " "
        + " ".join(f"On {key.replace('-', '–')} alone ({value['n_days']:,} days) it keeps {members(value['included'])}."
                   for key, value in periods),
    ]
    for m in controls:
        dm = m["dm_qlike_vs_har_iv"]
        out.append(f"- **Implied-vol timing.** OVX settles at 16:15 ET, after the 14:30 CL settlement that opens the "
                   f"target window, so the model uses the previous session's OVX. Using the same day's value, as the "
                   f"timing control in the table above does, {'lowers' if m['qlike'] < champ['qlike'] else 'raises'} "
                   f"QLIKE to "
                   f"{_fmt(m['qlike'])} (DM {_fmt(dm['stat'], 2, True)}, {_peq(dm['p_value'])}): the extra 1h45m of "
                   f"information is worth having, and is not available at the decision time. It is a timing control, "
                   f"excluded from every confidence set, the SPA test, the selection below and the choice of "
                   f"challenger.")
    out += [
        f"- **Roll gaps** (production run). `CL=F` books each roll's calendar spread as a return. With the "
        f"{ret['roll_days_rebuilt']} roll days through {ret['sample_end'][:4]} rebuilt as same-contract returns "
        f"from EIA's curve, the production model's QLIKE is {ret['har_qlike_on_same_contract_returns']} over the "
        f"same {ret['n_on_same_contract_returns']} weeks ({o['har_qlike']} as published).",
        f"- **Second volatility proxy.** Against the range-based realized variance of the target week (full bars, "
        f"Rogers-Satchell plus the overnight return, not rescaled to the close-to-close level) the model of "
        f"record's QLIKE is {_fmt(champ['qlike_range_proxy'])}, and the MCS on that proxy keeps {kept}"
        + ("." if champ["key"] in range_set else ", not the model of record."),
        f"- **Choosing the model out of sample.** Picking, each day, the feasible model with the lowest trailing "
        f"{lab['config']['selection_window_weeks']}-week loss scores {_fmt(sel['qlike'])} vs "
        f"{_fmt(sel['qlike_har_iv'])} for always using the model of record (DM "
        f"{_peq(sel['dm_vs_har_iv']['p_value'])}); it picks {chosen} of the time.",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------- 4. post-mortem

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
    rows.append("")
    rows.append("Recorded runs (`data/wf_*.json`) of `backend/backtest_walk_forward.py` on data downloaded from "
                "Yahoo Finance; each file's `config` gives its data window.")
    return "\n".join(rows)


def leak_mechanism() -> str:
    a = _load("leak_anatomy.json")
    an = a["analytic"]
    nn = a["synthetic_random_walk"]["nearest_neighbour"]
    u, p = nn["unpurged_leaked"], nn["purged"]
    return (f"The leak is easy to demonstrate on data with no signal at all. With jointly normal labels whose "
            f"correlation is ρ, the signs agree with probability ½ + arcsin(ρ)/π. A label sharing four of five "
            f"returns has ρ = {an['leaked_label_correlation']}, and agrees with the test label "
            f"{an['p_sign_agree_leaked_pct']}% of the time. The newest training rows are also the test row's nearest "
            f"neighbours in feature space, because price levels and their moving averages barely move in a day, so "
            f"a model that leans on near-duplicate rows copies a label that already contains most of the answer. On "
            f"a synthetic random walk, the 1-nearest-neighbour model's nearest neighbour is the newest training row "
            f"in {u['nearest_is_newest_row_pct_mean']:.0f}% of unpurged decisions, and on those it is right "
            f"{u['accuracy_when_nearest_is_newest_pct_mean']:.1f}% of the time; with the purge the newest available "
            f"row is nearest in {p['nearest_is_newest_row_pct_mean']:.0f}% of decisions and is right "
            f"{p['accuracy_when_nearest_is_newest_pct_mean']:.1f}% of the time "
            f"([`backend/leak_anatomy.py`](backend/leak_anatomy.py)):")


def leak_table() -> str:
    a = _load("leak_anatomy.json")
    names = {"nearest_neighbour": "1-nearest neighbour", "decision_tree": "Decision tree",
             "random_forest": "Random forest (20 trees)"}
    data = {"synthetic_random_walk": "i.i.d. random walk", "block_shuffled_cl": "Block-shuffled CL=F returns",
            "real_cl_5y_window": "Real CL=F, 5-year window"}
    rows = ["| Data | Model | Runs | Accuracy unpurged → purged | Gross Sharpe unpurged → purged |",
            "|---|---|---:|---|---|"]
    for dkey, dname in data.items():
        for mkey, block in a[dkey].items():
            if not (isinstance(block, dict) and "purged" in block):
                continue
            u, p = block["unpurged_leaked"], block["purged"]
            rows.append(f"| {dname} | {names.get(mkey, mkey)} | {u['n_runs']} | {u['accuracy_pct_mean']:.1f}% → "
                        f"**{p['accuracy_pct_mean']:.1f}%** | {_fmt(u['sharpe_mean'], 2)} → **{_fmt(p['sharpe_mean'], 2)}** |")
    ref = a["retracted_reference"]
    shuffled = [(names.get(mkey, mkey), block) for mkey, block in a["block_shuffled_cl"].items()
                if isinstance(block, dict) and "purged" in block]
    beat_u = _join([f"{b['unpurged_leaked']['runs_beating_base_rate_5pct']} of {b['unpurged_leaked']['n_runs']} "
                    f"({name.lower()})" for name, b in shuffled])
    beat_p = _join([f"{b['purged']['runs_beating_base_rate_5pct']} of {b['purged']['n_runs']}" for _, b in shuffled])
    rows.append("")
    rows.append(f"Sharpe ratios here are gross (no transaction costs); the retracted model's {ref['sharpe']:.2f} is "
                f"{ref['sharpe_basis']}, so the two are not like for like, and costs would only lower these. For "
                f"comparison, the retracted model: {ref['accuracy_pct']:.1f}% over {ref['n']} weeks. Same walk-forward, "
                f"same five-session labels, the purge switched off and on. The block-shuffled runs permute the same "
                f"CL=F returns, so they are not independent samples and their pooled tests overstate the evidence. "
                f"Real 5-session CL=F returns rise {a['design']['cl_5_session_up_share_pct']}% of the time, so the "
                f"no-skill accuracy on them is each run's own base rate, not 50%: leaked runs beating their base rate "
                f"at 5%: {beat_u}; purged: {beat_p}. Source: leak anatomy.")
    return "\n".join(rows)


# --------------------------------------------------------------------------- 5. side studies

def carry_table() -> str:
    c = _load("carry_signal_test.json")
    rows = ["| Sample | Months | Return above / below median carry (%/mo) | One-sided p | Rank corr. (p) | "
            "Timed vs buy-and-hold Sharpe |", "|---|---:|---|---:|---|---|"]
    for key, name in (("full_sample", "Full"), ("pre_2007", "Before 2007"), ("post_2007", "2007 on")):
        s = c[key]
        rows.append(f"| {name} ({s['span'][:4]}–{s['span'][-10:-6]}) | {s['n_months']} | "
                    f"{_fmt(s['mean_ret_above_median_carry_pct'], 2, True)} / {_fmt(s['mean_ret_below_median_carry_pct'], 2, True)} | "
                    f"{_fmt(s['spread_p_value_one_sided'])} | {_fmt(s['carry_rank_corr'], 2, True)} "
                    f"({_fmt(s['carry_rank_corr_p_value'])}) | {_fmt(s['carry_timed_sharpe'], 2)} vs {_fmt(s['buy_hold_sharpe'], 2)} |")
    pw, rc = c["power_post_2007"], c["regime_change_test"]
    rows.append("")
    rows.append(f"Second-nearby contract held over each contract cycle on EIA's futures curve (no roll gap in any "
                f"return). After 2007 the spread test (one-sided 5%) could detect only a spread of "
                f"{pw['mde_spread_pct_per_month']}%/month with 80% power, and had "
                f"{round(pw['power_vs_pre_2007_spread'] * 100)}% power against the pre-2007 spread; the rank test "
                f"(two-sided 5%) could detect a correlation of {_fmt(pw['mde_rank_corr'])} and had "
                f"{round(pw['power_vs_pre_2007_rank_corr'] * 100)}% power against the pre-2007 correlation. The change "
                f"in spread has p = {_fmt(rc['spread_difference_p_value'])}; the change in rank correlation has "
                f"p = {_fmt(rc['rank_corr_difference_p_value'])}. Offline, from the committed EIA curve.")
    return "\n".join(rows)


def carry_reading() -> str:
    c = _load("carry_signal_test.json")
    ledger = _ledger()
    full, post = c["full_sample"], c["post_2007"]
    pw, rc = c["power_post_2007"], c["regime_change_test"]
    row = _ledger_row(ledger, "carry_spread_full")
    text = []
    p = full["spread_p_value_one_sided"]
    if _sig(p):
        text.append(f"Over the full sample the spread is significant at 5% on its own (one-sided p = {_fmt(p)}), "
                    + ("and it survives" if row.get("survives_bh_5pct") else "though it does not survive")
                    + f" the ledger's multiplicity adjustment (Benjamini-Hochberg q = {_fmt(row['q_value_bh'])}; "
                      f"section 6).")
    else:
        text.append(f"Over the full sample the spread is not significant at 5% (one-sided p = {_fmt(p)}).")
    spread_power = pw["power_vs_pre_2007_spread"]
    rank_changed = _sig(rc["rank_corr_difference_p_value"])
    if not _sig(post["spread_p_value_one_sided"]) and spread_power < 0.8:
        text.append(f"Whether the effect survived 2007 is not something this sample can settle: the post-2007 spread "
                    f"test had {round(spread_power * 100)}% power against the pre-2007 spread, too little to call the "
                    f"effect dead, while the rank correlation, whose test had "
                    f"{round(pw['power_vs_pre_2007_rank_corr'] * 100)}% power against its pre-2007 value, "
                    + ("did drop significantly" if rank_changed else "did not change significantly")
                    + f" (p = {_fmt(rc['rank_corr_difference_p_value'])}).")
    ps = [c[k]["sharpe_difference"]["ledoit_wolf_p_value"] for k in ("full_sample", "pre_2007", "post_2007")]
    if not any(_sig(x) for x in ps):
        text.append(f"The timed strategy's Sharpe gain over buy-and-hold is not significant in any period "
                    f"(Ledoit and Wolf 2008: p = {', '.join(_fmt(x) for x in ps)} for the full sample, before 2007 "
                    f"and from 2007).")
    else:
        text.append(f"Sharpe gain over buy-and-hold (Ledoit and Wolf 2008): p = {', '.join(_fmt(x) for x in ps)} for "
                    f"the full sample, before 2007 and from 2007.")
    return " ".join(text)


def supply_table() -> str:
    s = _load("supply_shock_study.json")
    pl = s["placebo"]
    rows = ["| Group | n | Median peak | Median settle | p peak (unmatched / year-matched) | "
            "p settle (unmatched / year-matched) |", "|---|---:|---:|---:|---|---|"]
    rows.append(f"| Ordinary dates (placebo) | {pl['n']:,} | {_fmt(pl['median_peak_pct'], 1, True)}% | "
                f"{_fmt(pl['median_settle_pct'], 1, True)}% | — | — |")
    for g in s["groups"]:
        pv, py = g["p_value_vs_placebo"], g["p_value_vs_year_matched_placebo"]
        rows.append(f"| {g['name']} | {g['n']} | {_fmt(g['median_peak_pct'], 1, True)}% | "
                    f"{_fmt(g['median_settle_pct'], 1, True)}% | {_p(pv['peak'])} / {_p(py['peak'])} | "
                    f"{_p(pv['settle'])} / {_p(py['settle'])} |")
    bg = s["between_groups"]
    rows.append("")
    rows.append(f"Day 0 is the first session on or after the event date. Peak = the highest close over sessions 0–19; "
                f"settle = the close at session 10; both relative to the mean close of the 5 sessions before day 0, "
                f"on EIA's daily Cushing spot price. p-values: two-sided resampling tests of the group median against "
                f"groups of ordinary dates drawn from the whole span (unmatched) or from each event's own calendar "
                f"year (year-matched); the ledger supports a group only when both are below 5%. Supply removal vs "
                f"threat-only directly: p = {_fmt(bg['p_value']['peak'])} (peak), {_fmt(bg['p_value']['settle'])} "
                f"(settle). Event dates and groups were chosen with hindsight, the groups overlap, and these p-values "
                f"are not adjusted for the groups tested (the ledger adjusts them). Offline, from the committed EIA "
                f"spot series.")
    return "\n".join(rows)


def supply_reading() -> str:
    s = _load("supply_shock_study.json")
    groups = {g["key"]: g for g in s["groups"]}

    def both(g: dict, stat: str) -> tuple[float, float]:
        return g["p_value_vs_placebo"][stat], g["p_value_vs_year_matched_placebo"][stat]

    text = []
    lost = groups.get("supply_lost")
    if lost:
        for stat in ("peak", "settle"):
            pu, py = both(lost, stat)
            subject = f"The '{lost['name']}' group's {stat}" if stat == "peak" else f"Its {stat}"
            higher = lost[f"median_{stat}_pct"] > s["placebo"][f"median_{stat}_pct"]
            if _sig(pu) and _sig(py):
                text.append(f"{subject} is {'higher' if higher else 'lower'} than an ordinary date's against both "
                            f"placebos (p = {_fmt(pu, 4)} unmatched, {_fmt(py, 4)} year-matched).")
            elif _sig(pu) or _sig(py):
                text.append(f"{subject} differs from an ordinary date's against one placebo but not the other "
                            f"(p = {_fmt(pu, 4)} unmatched, {_fmt(py, 4)} year-matched), so the ledger does not "
                            f"support it.")
            else:
                text.append(f"{subject} is indistinguishable from an ordinary date's (p = {_fmt(pu, 4)} unmatched, "
                            f"{_fmt(py, 4)} year-matched).")
    threat = groups.get("threat_only")
    if threat:
        ps = [p for stat in ("peak", "settle") for p in both(threat, stat)]
        if not any(_sig(p) for p in ps):
            text.append(f"The '{threat['name']}' group is indistinguishable from an ordinary date on both measures "
                        f"and against both placebos (smallest p = {_fmt(min(ps), 3)}).")
    split = []
    for key, g in groups.items():
        if key in ("supply_lost", "threat_only"):
            continue
        for stat in ("peak", "settle"):
            pu, py = both(g, stat)
            if _sig(pu) != _sig(py):
                split.append(f"{g['name']} ({stat}: {_fmt(pu, 3)} / {_fmt(py, 3)})")
    if split:
        text.append(f"Significant against one placebo but not the other (unmatched / year-matched p): "
                    f"{'; '.join(split)}.")
    pairs = s.get("overlapping_events", {}).get("pairs", [])
    if pairs:
        listed = "; ".join(f"`{p['events'][0]}` and `{p['events'][1]}`, {p['sessions_apart']} sessions apart"
                           for p in pairs)
        text.append(f"The study lists {len(pairs)} event pairs fewer than 25 sessions apart ({listed}): their "
                    f"windows share sessions, so a group's events are not all independent.")
    text.append("The study is decision support, not a forecast, and the dashboard presents it that way.")
    return " ".join(text)


# --------------------------------------------------------------------------- 6. ledger

def ledger_block() -> str:
    led = _ledger()
    s = led["summary"]
    rows_by_id = {r["id"]: r for r in led["rows"]}
    out = [f"{s['rows']} hypotheses: {s['by_status']['supported']} supported, {s['by_status']['rejected']} rejected, "
           f"{s['by_status']['inconclusive']} inconclusive, {s['by_status']['retracted']} retracted. Of the "
           f"supported ones, {len(s['supported_surviving_bh_5pct'])} survive Benjamini-Hochberg at 5%, "
           f"{len(s['supported_surviving_holm_5pct'])} survive Holm, and {len(s['supported_clearing_hlz_t3'])} clear "
           f"the t > 3 hurdle of Harvey, Liu and Zhu (2016). By family:", "",
           "| Family | Hypotheses | Supported | Rejected | Inconclusive | Retracted | Supported, surviving BH | "
           "Supported, clearing t > 3 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for family, f in s["by_family"].items():
        b = f["by_status"]
        out.append(f"| {family} | {f['rows']} | {b['supported']} | {b['rejected']} | {b['inconclusive']} | "
                   f"{b['retracted']} | {f['supported_surviving_bh_5pct']} | {f['supported_clearing_hlz_t3']} |")
    out += ["", "The supported hypotheses that survive Benjamini-Hochberg at 5%:", "",
            "| Claim | p | BH q | Clears t > 3 |", "|---|---:|---:|:---:|"]
    for key in s["supported_surviving_bh_5pct"]:
        r = rows_by_id[key]
        out.append(f"| {r['hypothesis']} | {_p(r['p_value'])} | {_p(r.get('q_value_bh'))} | "
                   f"{'yes' if r.get('clears_hlz_t3') else 'no'} |")
    return "\n".join(out)


def ledger_reading() -> str:
    led = _ledger()
    s = led["summary"]
    leak = [r for r in led["rows"] if r["family"] == "leak anatomy" and r["status"] == "supported"]
    every = [r for r in leak if r["id"] in s["supported_surviving_bh_5pct"]
             and r["id"] in s["supported_surviving_holm_5pct"] and r["id"] in s["supported_clearing_hlz_t3"]]
    text = []
    if leak and len(every) == len(leak):
        text.append(f"{'Both' if len(leak) == 2 else f'All {len(leak)}'} supported leak demonstrations survive every "
                    f"adjustment: some of the project's most robust findings are about how its own first result was "
                    f"wrong.")
    shuffled = [r for r in leak if "block-shuffled" in r["hypothesis"]]
    if shuffled:
        text.append("The block-shuffled one pools ten runs that permute the same returns, so its p-value overstates "
                    "the evidence (section 4).")
    retracted = s["by_status"]["retracted"]
    text.append(f"The {retracted} retracted rows stay in the multiplicity count: they were tested.")
    return " ".join(text)


# --------------------------------------------------------------------------- 3. live record

def promotion_rule() -> str:
    from backend.vol_record import PROMOTION_RULE

    return f"> {PROMOTION_RULE}"


def promotion_power(min_weeks: int = 52, alpha: float = 0.10) -> str:
    """Power of the promotion test if the challenger's historical edge is its true edge.

    The per-grid Diebold-Mariano statistics are on ~n_weeks non-overlapping weeks, so the expected
    statistic after w independent weeks is about stat * sqrt(w / n_weeks)."""
    from statistics import NormalDist, median

    from backend.vol_record import PROMOTION_ALPHA, PROMOTION_WEEKS

    if (min_weeks, alpha) != (PROMOTION_WEEKS, PROMOTION_ALPHA):
        raise ValueError("promotion_power's constants no longer match vol_record.PROMOTION_WEEKS / PROMOTION_ALPHA")
    lab = _lab()
    chall = _chall(lab)
    n_weeks = lab["sample"]["n_weeks"]
    stat = median(chall["dm_qlike_vs_har_iv_by_grid"])
    z = NormalDist()
    crit = z.inv_cdf(1 - alpha)
    power = 1 - z.cdf(crit - stat * (min_weeks / n_weeks) ** 0.5)
    weeks_80 = n_weeks * ((crit + z.inv_cdf(0.80)) / stat) ** 2
    return (f"How much the single test can tell: on the sampling grids the challenger's historical edge is a "
            f"Diebold-Mariano statistic of about {stat:.1f} over {n_weeks} weeks. If that is its true edge, the test "
            f"on {min_weeks} independent weeks promotes it with probability {power * 100:.0f}%; a fixed-sample test "
            f"with 80% power would need about {weeks_80:,.0f} weeks ({weeks_80 / 52:.0f} years). The rule is built "
            f"not to promote on luck. A keep verdict on that sample does not show the challenger is worse.")


def print_block(name: str) -> None:
    """Entry point for cog: `[[[cog import scripts.readme_blocks as b; b.print_block("leaderboard_table") ]]]`."""
    import cog  # provided by cogapp at generation time

    cog.out("\n" + globals()[name]() + "\n")


# --------------------------------------------------------------------------- model card and datasheet

def model_card_metrics() -> str:
    """Evaluation summary of the model of record and the challenger (model card, section 'Metrics')."""
    lab, prod = _lab(), _prod()
    o = prod["overall"]
    board = lab["leaderboard"]
    champ, chall = _champ(lab), _chall(lab)
    ovx_only = _model(lab, "ovx_only")
    conf = lab["conformal"]
    feasible = _feasible(lab)
    k = key_numbers()
    tests = prod["economic"]["vol_target_overlay"]["sharpe_difference_tests"]["forecast_minus_buy_hold"]
    controls = _controls(lab)

    def in_set(m: dict) -> str:
        return (f"{'yes' if m['in_mcs_qlike'] else 'no'}, MCS p {_fmt(m['mcs_qlike_p'], 3)} "
                f"({m['in_mcs_qlike_grids']}/5 grids)")

    cdm = chall["dm_qlike_vs_har_iv"]
    odm = ovx_only["dm_qlike_vs_har_iv"]
    rows = ["| Metric | Model of record (HAR-IV v2) | Challenger (" + chall["label"] + ") | Reference |",
            "|---|---:|---:|---|"]
    rows.append(f"| QLIKE, every OOS day (lower is better) | {_fmt(champ['qlike'])} | {_fmt(chall['qlike'])} | "
                f"persistence {_fmt(_model(lab, 'persistence')['qlike'])}; implied-vol regression "
                f"{_fmt(ovx_only['qlike'])}; raw OVX {_fmt(_model(lab, 'raw_ovx')['qlike'])} |")
    rows.append(f"| Diebold-Mariano on QLIKE vs the model of record | — | {_fmt(cdm['stat'], 2, True)} "
                f"({_peq(cdm['p_value'])}) | implied-vol regression {_fmt(odm['stat'], 2, True)} ({_peq(odm['p_value'])}) |")
    rows.append(f"| In the 90% Model Confidence Set (full sample; sampling grids) | {in_set(champ)} | {in_set(chall)} | "
                f"{len(board['mcs_qlike_included'])} of {len(feasible)} feasible models"
                + ("; the timing control is excluded" if controls else "") + " |")
    rows.append(f"| Hansen SPA, null: no model beats the model of record | p = {_fmt(k['spa_p'])} | — | "
                f"{len(board['spa_qlike_vs_har_iv']['models'])} feasible alternatives; {_level(k['spa_p'])} |")
    rows.append(f"| Mincer-Zarnowitz slope (joint unbiasedness p) | {_fmt(champ['mincer_zarnowitz']['slope'], 2)} "
                f"({_p(champ['mincer_zarnowitz']['p_unbiased'])}) | {_fmt(chall['mincer_zarnowitz']['slope'], 2)} "
                f"({_p(chall['mincer_zarnowitz']['p_unbiased'])}) | 1 (unbiased) |")
    rows.append(f"| 80% conformal interval coverage | {conf[lab['config']['champion']]['coverage_pct']}% | "
                f"{conf[lab['config']['challenger']]['coverage_pct']}% | target 80% |")
    rows.append(f"| Realized utility (% per year) | {_fmt(champ['realized_utility_pct'], 2)} | "
                f"{_fmt(chall['realized_utility_pct'], 2)} | maximum {board['realized_utility_max_pct']} |")
    rows.append(f"| OVX elasticity (HAC t), full-sample fit | {_fmt(k['ovx_coef']['coef'], 2)} "
                f"({_fmt(k['ovx_coef']['t_hac'], 1)}) | — | 1 = proportional to implied vol |")
    rows.append(f"| Vol-target overlay: Sharpe minus buy-and-hold (p) | {_fmt(tests['difference'], 2, True)} "
                f"({_p(tests['p_value'])}) | — | production run, {k['overlay_weeks']} weeks |")
    rows.append(f"| Direction accuracy, every OOS day (not skill evidence) | "
                f"{_fmt(champ['direction_acc_pct'], 1)}% | {_fmt(chall['direction_acc_pct'], 1)}% | noise floor "
                f"{k['lab_noise_floor']}% (HAR without OVX on simulated returns with nothing to forecast); production "
                f"run: model of record {o['har_dir_acc_pct']}% on the weekly grid vs a floor of {k['prod_noise_floor']}% |")
    rows.append("")
    rows.append("Every-day rows and the SPA test are from the lab (`data/vol_lab.json`, rebuilt in CI); the overlay "
                "and production-grid direction figures are from a recorded run on downloaded data "
                "(`data/vol_forecast_validation.json`).")
    return "\n".join(rows)


def provenance_table() -> str:
    m = _lab()["generated_from"]
    rows = ["| File | Rows | First | Last | SHA-256 | Source |", "|---|---:|---|---|---|---|"]
    for entry in m["files"].values():
        rows.append(f"| `data/market/{entry['file']}` | {entry['rows']:,} | {entry['first']} | {entry['last']} | "
                    f"`{entry['sha256'][:16]}…` | {entry['source']} |")
    return "\n".join(rows)


def data_quality_block() -> str:
    """Data-quality facts of the pinned snapshot: repaired bars and the Yahoo vs FRED OVX check."""
    lab = _lab()
    q = lab["generated_from"]["cl_data_quality"]
    agree = lab["ovx_source_agreement"]
    worst = ", ".join(f"{d['date']} (Yahoo {d['yahoo']}, FRED {d['fred']})" for d in agree["largest_differences"])
    return "\n".join([
        f"- `CL=F` sessions kept: {q['sessions']:,}. Bars with a non-positive open, high or low (the session "
        f"after the 2020-04-20 negative settlement): {q['range_blanked_nonpositive_bars']}, close kept, range "
        f"blanked. Bars whose high was below the open or close: {q['repaired_high_below_open_or_close']}; "
        f"whose low was above them: {q['repaired_low_above_open_or_close']} (repaired by widening the range; "
        "closes are never changed).",
        f"- OVX, Yahoo vs FRED: {agree['common_days']:,} common days, {agree['share_within_0_05_pts'] * 100:.2f}% "
        f"within 0.05 points, correlation {agree['correlation']}, largest differences {worst}.",
        f"- Snapshot retrieved {lab['generated_from']['retrieved_at']}, finished sessions through "
        f"{lab['generated_from']['sessions_through']}.",
    ])
