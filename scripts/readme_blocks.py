"""Markdown blocks for README.md and docs/, generated from the committed artifacts.

Every table, result statistic and data-dependent verdict in the README and docs/RESULTS.md (the full
write-up), and every table in the model card and the datasheet, is produced here from data/*.json and inserted with cog
(https://nedbatchelder.com/code/cog): `cog -r README.md` regenerates the blocks and `cog --check
README.md` (run in CI) fails if a committed artifact and the page disagree. A sentence that states a
result, a direction ("above one"), a significance verdict or a quantifier ("every", "none") is written
here, conditional on the data, so it cannot contradict the artifact it describes; descriptive facts
about the design (the number of models, schedules) stay in the hand-written prose. Standard library
only.

Sources. data/vol_lab.json is rebuilt from the pinned snapshot (data/market) and checked in CI;
data/vol_forecast_validation.json and data/wf_*.json are records of runs on downloaded data, not
rebuilt in CI. Blocks that quote them say so. The design levels the text quotes (the number of
sampling grids, the confidence-set level, the interval level) are read from the lab's config.
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

from backend.vol_record import (HORIZON_SESSIONS, PROMOTION_ALPHA, PROMOTION_CHALLENGER, PROMOTION_MODEL_VERSION,
                                 PROMOTION_RULE, PROMOTION_WEEKS, WINDOW_NOTE, student_t_sf)

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
               "offline from the pinned snapshot and synthetic returns (the unit suite re-runs a subset). "
               "*recorded runs*: the direction post-mortem's `data/wf_*.json`, also runs on downloaded data.")


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
    """The verdict for a p-value, in the house words: significant below 5%, inconclusive from 5% to 10%."""
    if p < 0.05:
        return "significant at 5%"
    if p < 0.10:
        return "inconclusive: not significant at 5%"
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


def _word(n: int) -> str:
    """A small count in words ('five'), as prose writes it next to another number ('five 5-session grids')."""
    words = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
    return words[n] if 0 <= n < len(words) else str(n)


def _pct_level(share: float) -> str:
    """A confidence level as text: 0.9 -> '90%'."""
    return f"{round(share * 100, 6):g}%"


def _grids(lab: dict) -> int:
    """The number of sampling grids: one per offset of the forecast step (config.step sessions)."""
    return lab["config"]["step"]


def _grid_weeks(lab: dict) -> str:
    """Non-overlapping weeks per sampling grid ("926 or 927"): offset k keeps every step-th day from k."""
    step, n = _grids(lab), lab["sample"]["n_days"]
    sizes = sorted({-(-(n - k) // step) for k in range(step)})
    return str(sizes[0]) if len(sizes) == 1 else " or ".join(str(s) for s in sizes)


def _mcs_level(lab: dict) -> str:
    return _pct_level(1 - lab["config"]["mcs"]["size"])


def _interval_level(lab: dict) -> str:
    return _pct_level(1 - lab["config"]["conformal"]["alpha"])


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


def _gloss(lab: dict, model: dict) -> str:
    return GLOSS.get(model["key"], model["description"].rstrip(".").lower())


def _var(lab: dict, method: str, level: str) -> dict:
    return lab["var_backtest"]["summary"][f"{method}@{level}"]


def key_numbers() -> dict:
    """The handful of numbers the abstract and the summary tables quote, keyed by name."""
    lab, prod = _lab(), _prod()
    board = lab["leaderboard"]
    champ, chall = _champ(lab), _chall(lab)
    o = prod["overall"]
    ov = prod["economic"]["vol_target_overlay"]
    econ = ov["risk"]
    champion = lab["config"]["champion"]
    return {
        "n_days": lab["sample"]["n_days"], "first": lab["sample"]["first"], "last": lab["sample"]["last"],
        "n_models": len(board["models"]), "n_feasible": len(_feasible(lab)),
        "champ_qlike": champ["qlike"], "persistence_qlike": _model(lab, "persistence")["qlike"],
        "chall_qlike": chall["qlike"],
        "chall_dm": chall["dm_qlike_vs_har_iv"]["stat"], "chall_dm_p": chall["dm_qlike_vs_har_iv"]["p_value"],
        "spa_p": board["spa_qlike_vs_har_iv"]["p_consistent"],
        "lab_noise_floor": lab["noise_floor"]["har_direction_acc_pct_mean"],
        "prod_noise_floor": o["direction_noise_floor"]["dir_acc_pct_mean"],
        "dir_acc": o["har_dir_acc_pct"],
        "coverage": lab["conformal"][champion]["coverage_pct"],
        "target_vol": ov["target_vol_pct"], "overlay_weeks": ov["n_weeks"],
        "fc_realized_vol": econ["forecast_vol_target"]["realized_vol_pct"],
        "tr_realized_vol": econ["trailing_vol_target"]["realized_vol_pct"],
        "bh_kurt": econ["buy_hold"]["excess_kurtosis"], "fc_kurt": econ["forecast_vol_target"]["excess_kurtosis"],
        "tr_kurt": econ["trailing_vol_target"]["excess_kurtosis"],
        "ovx_coef": next(t for t in lab["har_iv_coefficients"]["terms"] if t["term"] == "log OVX"),
    }


def _scaling_share(k: dict) -> float | None:
    """The share of the forecast-sized book's cut in excess kurtosis (from buy-and-hold) that sizing on
    trailing volatility, with no forecast, already achieves."""
    cut = k["bh_kurt"] - k["fc_kurt"]
    return (k["bh_kurt"] - k["tr_kurt"]) / cut if cut > 0 else None


def _zero_premium(vrp: dict) -> tuple[dict, bool, bool]:
    """The variance risk premium's zero-premium null, and whether the share of periods with implied
    above realized vol exceeds it significantly and whether the variance-unit premium is significant."""
    null = vrp["zero_premium_null"]
    ci = vrp["mean_premium_variance_ci95"]
    return null, _sig(null["p_value_one_sided"]), ci[0] > 0 or ci[1] < 0


def _ranges_clause(lab: dict, ovx_tied: bool) -> str:
    """How HAR-range, built from daily price ranges with no OVX, compares with the model of record. A tie
    is a failure to reject, not equivalence: it only says these data do not show OVX is needed."""
    m = _model(lab, "har_range")
    dm = m["dm_qlike_vs_har_iv"]
    detail = f"(QLIKE {_fmt(m['qlike'])}, DM {_peq(dm['p_value'])})"
    if not _sig(dm["p_value"]):
        return (f"{m['label']}, from daily price ranges without OVX, is {'also ' if ovx_tied else ''}statistically "
                f"tied with the model of record {detail}, so these data do not show that OVX is needed to reach its "
                f"loss.")
    return (f"{m['label']}, from daily price ranges without OVX, is significantly "
            f"{'better' if dm['stat'] > 0 else 'worse'} than the model of record {detail}.")


def _spa_adjusted(ledger: dict) -> str:
    """', and the rejection survives ...' or ', though the rejection does not survive ... (q = ...)': the SPA row against the
    ledger's Benjamini-Hochberg adjustment over its multiplicity family, with the connective it needs."""
    row = _ledger_row(ledger, "vol_spa_beats_record")
    family = ledger["summary"]["rows_in_multiplicity_family"]
    where = f"the ledger's Benjamini-Hochberg adjustment across the {family} hypotheses in its multiplicity family"
    if row["id"] in ledger["summary"]["supported_surviving_bh_5pct"]:
        return f", and the rejection survives {where} (q = {_fmt(row['q_value_bh'])})"
    return f", though the rejection does not survive {where} (q = {_fmt(row['q_value_bh'])})"


def _spa_leader(lab: dict) -> dict | None:
    """The model with the largest studentized gain over the model of record, when one has a positive gain."""
    key = lab["leaderboard"]["spa_qlike_vs_har_iv"].get("best_model")
    return _model(lab, key) if key else None


def _spa_robustness(lab: dict) -> str:
    """' The studentized p-value is ... with other blocks and in each sub-period.' from the lab's checks."""
    checks = lab["leaderboard"].get("spa_robustness_qlike")
    if not checks:
        return ""
    block = lab["config"]["mcs"]["block_daily"]
    blocks = sorted((int(key.split("_")[1]), v) for key, v in checks.items() if key.startswith("block_"))
    periods = [(key, v) for key, v in checks.items() if not key.startswith("block_")]
    text = (" The studentized p-value is " + _join([f"{_fmt(v['p_consistent'])} with blocks of {b} days"
                                                    for b, v in blocks]) + f" (block {block} above)")
    if periods:
        text += ("; by sub-period it is " + _join([
            f"{_fmt(v['p_consistent'])} over {key.replace('-', '–')} ({v['n_days']:,} days"
            + (f"; largest gain {_model(lab, v['best_model'])['label']})" if v.get("best_model") else ")")
            for key, v in periods]))
    return text + "."


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
    grids = _grids(lab)
    var99 = _var(lab, f"{champion}:fhs", "0.01")
    hs99 = _var(lab, "historical_simulation", "0.01")
    raw99 = _var(lab, "raw_ovx:fhs", "0.01")
    summary = ledger["summary"]
    overlay = prod["economic"]["vol_target_overlay"]
    vrp = prod["economic"]["variance_risk_premium"]

    tied = not _sig(ovx_only["dm_qlike_vs_har_iv"]["p_value"])
    ovx_clause = (f"the {ovx_only['label']}, a log regression on OVX alone ({_fmt(ovx_only['qlike'])}, DM "
                  f"{_peq(ovx_only['dm_qlike_vs_har_iv']['p_value'])}), is "
                  + ("statistically tied with the full model" if tied else "statistically distinguishable from it"))
    control_text = ""
    if len(controls) == 1:
        control_text = (f"; the {_ordinal(k['n_models'])}, a timing control that reads OVX after the decision, "
                        f"is excluded")
    elif controls:
        control_text = f"; {len(controls)} timing controls that read data published after the decision are excluded"
    if champ["in_mcs_qlike"]:
        record = (f"The model of record is in the set (MCS p = {_fmt(champ['mcs_qlike_p'])}) and on "
                  f"{_count(champ['in_mcs_qlike_grids'], grids)} sampling grids.")
    else:
        record = (f"The model of record is outside it (MCS p = {_fmt(champ['mcs_qlike_p'])}), and inside it on "
                  f"{champ['in_mcs_qlike_grids']} of the {grids} non-overlapping sampling grids.")
    lowest_all = chall["qlike"] == min(m["qlike"] for m in board["models"])
    lowest_feasible = chall["qlike"] == min(m["qlike"] for m in feasible)
    rank = (f"has the lowest loss of all {k['n_models']} models" if lowest_all else
            f"has the lowest loss of the {k['n_feasible']} feasible models" if lowest_feasible else
            "is the challenger")
    in_set = ("is in the set over the full sample and on " if chall["in_mcs_qlike"] else
              "is outside the set over the full sample but in it on ")
    spa = k["spa_p"]
    if _sig(spa):
        spa_text = (f"Hansen's SPA test, which accounts for the search over the field, rejects the null that no "
                    f"model beats the model of record (studentized p = {_fmt(spa)}){_spa_adjusted(ledger)}; the "
                    f"variant runs live as a challenger, and replaces the model of record only under a promotion test "
                    f"fixed in advance.")
    else:
        spa_text = (f"Hansen's SPA test gives p = {_fmt(spa)} against the null that no model beats the model of "
                    f"record ({_level(spa)}), so the variant runs live as a challenger under a promotion test "
                    f"fixed in advance.")
    dir_row = _ledger_row(ledger, "vol_dir_vs_noise_floor")
    direction = (f"Next-week volatility-direction accuracy (whether next week's realized volatility exceeds this "
                 f"week's: {k['dir_acc']}% on the production grid) is "
                 + ("not evidence of skill" if dir_row["status"] != "supported" else "above its noise floor")
                 + f": the same pipeline, run as HAR without OVX on simulated i.i.d. returns with nothing to "
                   f"forecast, scores {k['prod_noise_floor']}%.")

    var_text = (f"its weekly 99% Value-at-Risk breaches {var99['exceedances_by_grid'][0]} times against "
                f"{var99['expected']} expected on the production grid, with breaches that pass the independence test "
                f"on {var99['independence_pass_grids']}/{grids} grids against {hs99['independence_pass_grids']}/{grids} "
                f"for historical simulation")
    if raw99["independence_pass_grids"] >= var99["independence_pass_grids"]:
        var_text += (f"; a VaR scaled by raw OVX, with nothing fitted, passes on "
                     f"{raw99['independence_pass_grids']}/{grids} too, so that gain comes from scaling by volatility "
                     f"and is not specific to this model")
    tests = overlay["sharpe_difference_tests"]
    share = _scaling_share(k)
    overlay_text = (f"In the production validation (a recorded run on downloaded data), sizing a {k['target_vol']:g}% "
                    f"volatility target on the forecast over {k['overlay_weeks']} weeks gives a realized volatility of "
                    f"{k['fc_realized_vol']}% and an excess kurtosis of {k['fc_kurt']} (buy-and-hold: {k['bh_kurt']}), "
                    f"and {_sharpe_clause(tests['forecast_minus_buy_hold'])}. Sizing on trailing 22-day volatility, "
                    f"with no forecast, gives {k['tr_realized_vol']}% and {k['tr_kurt']}")
    if share is not None and share >= 0.9:
        overlay_text += (", so most of that risk control comes from scaling by volatility at all; between the two "
                         "sized books only the Sharpe difference is tested "
                         f"({_fmt(tests['forecast_minus_trailing']['difference'], 2, True)}, "
                         f"{_peq(tests['forecast_minus_trailing']['p_value'])}).")
    else:
        overlay_text += "."
    null, share_sig, var_sig = _zero_premium(vrp)
    vrp_text = (f"OVX is above the next month's realized volatility in {vrp['share_positive_pct']}% of periods, "
                + (f"which is not evidence of a variance risk premium: with no premium, a noisy, right-skewed 20-session "
                   f"realized volatility would be below OVX in {null['share_positive_pct']}% of periods "
                   f"(one-sided {_peq(null['p_value_one_sided'])} for the difference)" if not share_sig else
                   f"more than the {null['share_positive_pct']}% a zero premium would give (one-sided "
                   f"{_peq(null['p_value_one_sided'])})")
                + ", and the premium in variance units, the relevant measure, is "
                + ("significant." if var_sig else "not significant."))
    s = summary
    selected = s.get("supported_selected_after_search", [])
    family = s["by_status"]["supported"] - len(selected)
    ledger_text = (f"All {s['rows']} hypotheses the project has tested are listed in a research ledger with "
                   f"multiplicity adjustments: of the {family} supported ones in the adjusted family, "
                   f"{len(s['supported_surviving_bh_5pct'])} survive Benjamini-Hochberg at 5%; "
                   f"{s['by_status']['not_supported']} are not supported (a failure to reject, not evidence against "
                   f"them) and {s['by_status']['retracted']} are retracted.")
    if "vol_challenger_qlike" in selected:
        ledger_text += (" The challenger's pairwise test is left out of that family, because the challenger was "
                        "picked by searching the field on the same sample; the SPA test is its search-adjusted "
                        "counterpart.")
    return (
        f"This project forecasts the realized volatility of front-month NYMEX WTI crude oil over the next five "
        f"sessions from free data, and keeps a public forward record of its live forecasts that scores only entries "
        f"sealed in git before the settlement that opens their window. Over {k['n_days']:,} out-of-sample days "
        f"({k['first'][:4]}–{k['last'][:4]}), a heterogeneous autoregressive model with lagged implied volatility "
        f"(HAR-IV, the model of record) has a QLIKE loss of {_fmt(k['champ_qlike'])}, against "
        f"{_fmt(k['persistence_qlike'])} for a random-walk forecast. It is significantly better than "
        f"{_names(worse)} (Diebold-Mariano p < 0.05 each). The "
        f"elasticity on OVX is {_fmt(k['ovx_coef']['coef'], 2)}, and {ovx_clause}. {_ranges_clause(lab, tied)}\n\n"
        f"Of the {k['n_feasible']} models "
        f"that use only information available at the decision, the {_mcs_level(lab)} Model Confidence Set keeps "
        f"{len(mcs)} ({_names(mcs)}){control_text}. {record} {chall['label']}, {_gloss(lab, chall)}, {rank} "
        f"({_fmt(k['chall_qlike'])}, pairwise DM {_peq(k['chall_dm_p'])}) and {in_set}"
        f"{_count(chall['in_mcs_qlike_grids'], grids)} grids. {spa_text}\n\n"
        f"The model of record's "
        f"{_interval_level(lab)} adaptive conformal intervals cover {k['coverage']}% of outcomes (the method steers "
        f"long-run coverage to its target), and {var_text}. {overlay_text} {direction} {vrp_text}\n\n"
        f"The project began as a machine-learning direction model whose backtest Sharpe ratio of "
        f"{_fmt(lk, 2)} after costs was a look-ahead leak; purged, it is {_fmt(pm, 2)}. The same leak gives a "
        f"20-tree random forest a gross (before costs) Sharpe ratio of {_fmt(leak['unpurged_leaked']['sharpe_mean'], 2)} "
        f"on a pure random walk. {ledger_text}"
    )


def key_findings() -> str:
    """The README's first screen: four findings, each worded at the strength its test supports."""
    lab, prod = _lab(), _prod()
    k = key_numbers()
    chall = _chall(lab)
    ovx_only = _model(lab, "ovx_only")
    returns_only = [m for m in _feasible(lab) if _inputs(m["key"]) == {"returns"}]
    worse = {m["key"] for m in _worse_than_record(lab)}
    beaten = [m for m in returns_only if m["key"] in worse]
    years = f"{k['first'][:4]}–{k['last'][:4]}"
    if len(beaten) == len(returns_only):
        beat = f"significantly better than all {len(returns_only)} models built from close-to-close returns alone"
    elif beaten:
        beat = (f"significantly better than {len(beaten)} of the {len(returns_only)} models built from close-to-close "
                f"returns alone")
    else:
        beat = "not significantly better than any model built from close-to-close returns alone"
    first = (f"- **Forecastable from free data.** Over {k['n_days']:,} out-of-sample days ({years}), the model of "
             f"record, HAR-IV (realized-volatility terms plus OVX lagged one session), has a QLIKE loss of "
             f"{_fmt(k['champ_qlike'])} against {_fmt(k['persistence_qlike'])} for a random-walk forecast, and is "
             f"{beat} (Diebold-Mariano p < {ALPHA:g} each).")
    odm = ovx_only["dm_qlike_vs_har_iv"]
    coef = k["ovx_coef"]
    if not _sig(odm["p_value"]):
        tie = f"statistically tied with the full model (DM {_peq(odm['p_value'])})"
        ovx_head = "OVX alone is statistically tied with it."
    elif odm["stat"] > 0:
        tie = f"significantly better than the full model (DM {_peq(odm['p_value'])})"
        ovx_head = "OVX alone does significantly better."
    else:
        tie = f"significantly worse than the full model (DM {_peq(odm['p_value'])})"
        ovx_head = "It adds significantly to OVX alone."
    ranges = _ranges_clause(lab, not _sig(odm["p_value"]))
    second = (f"- **{ovx_head}** The forecast's elasticity to OVX is "
              f"{_fmt(coef['coef'], 2)} (HAC t {_fmt(coef['t_hac'], 1)}), and a log regression on OVX alone scores "
              f"{_fmt(ovx_only['qlike'])}: {tie}. {ranges}")
    spa = k["spa_p"]
    lowest = chall["qlike"] == min(m["qlike"] for m in lab["leaderboard"]["models"])
    loss = (f"has the lowest loss of all {k['n_models']} models" if lowest else f"has a QLIKE of {_fmt(chall['qlike'])}")
    spa_board = lab["leaderboard"]["spa_qlike_vs_har_iv"]
    field = len(spa_board["models"])
    if _sig(spa):
        leader = _spa_leader(lab)
        named = (f"; {leader['label']} has the largest studentized gain, "
                 f"t {_fmt(spa_board['t_stats'][leader['key']], 2)}" if leader else "")
        verdict = (f"Hansen's SPA test, which accounts for the search over the {field} feasible alternatives, rejects "
                   f"the null that none of them beats the model of record (p = {_fmt(spa)}{named})"
                   f"{_spa_adjusted(_ledger())}. No test on this sample accounts for how the field was assembled.")
        head = ("One variant beats it in sample, after the search; the live test decides." if leader
                and leader["key"] == chall["key"] else
                "Some alternative beats it in sample, after the search.")
    elif spa < 0.10:
        verdict = (f"Hansen's SPA test, which accounts for the search over the {field} feasible alternatives, gives "
                   f"p = {_fmt(spa)}, so after the search the edge is inconclusive (not significant at 5%).")
        head = "One variant may be better; that is not established."
    else:
        verdict = (f"Hansen's SPA test, which accounts for the search over the {field} feasible alternatives, gives "
                   f"p = {_fmt(spa)}, so after the search the edge is not significant.")
        head = "One variant looked better; after the search that is not shown."
    third = (f"- **{head}** {chall['label']}, {_gloss(lab, chall)}, {loss} ({_fmt(chall['qlike'])}; pairwise DM "
             f"{_peq(chall['dm_qlike_vs_har_iv']['p_value'])}), but it was found by searching this sample. {verdict} "
             f"{chall['label']} runs live against the model of record under a promotion test fixed in advance: "
             f"{PROMOTION_WEEKS} independent weeks, one look.")
    c = lab["conformal"][lab["config"]["champion"]]
    by_year = list(c["coverage_by_year_pct"].values())
    overlay = prod["economic"]["vol_target_overlay"]["sharpe_difference_tests"]["forecast_minus_buy_hold"]
    skill = _ledger_row(_ledger(), "vol_dir_vs_noise_floor")["status"] == "supported"
    direction = (f"Volatility-direction accuracy ({k['dir_acc']}%) is "
                 + ("above" if skill else "not above")
                 + f" what the same pipeline scores with nothing to forecast ({k['prod_noise_floor']}%)"
                 + ("" if skill else ", so it is not evidence of skill"))
    sharpe = ("does not change the Sharpe ratio detectably" if not _sig(overlay["p_value"]) else
              f"{'raises' if overlay['difference'] > 0 else 'lowers'} the Sharpe ratio")
    fourth = (f"- **Intervals on target; no claim about direction or returns.** The {_interval_level(lab)} "
              f"adaptive conformal intervals cover {c['coverage_pct']}% of {c['n_days']:,} days, which the method "
              f"steers to its target over the long run; by calendar year, {min(by_year)}% to {max(by_year)}%. "
              f"{direction}, and a volatility target sized on the forecast {sharpe} (production run: difference "
              f"{_fmt(overlay['difference'], 2, True)} vs buy-and-hold, {_peq(overlay['p_value'])}).")
    return f"{first}\n{second}\n{third}\n{fourth}"


def results_at_a_glance() -> str:
    lab, prod, ledger = _lab(), _prod(), _ledger()
    k = key_numbers()
    champ, chall = _champ(lab), _chall(lab)
    champion = lab["config"]["champion"]
    persistence = _model(lab, "persistence")
    raw, ovx_only = _model(lab, "raw_ovx"), _model(lab, "ovx_only")
    grids, mcs_level = _grids(lab), _mcs_level(lab)
    var95, var99 = _var(lab, f"{champion}:fhs", "0.05"), _var(lab, f"{champion}:fhs", "0.01")
    hs95, hs99 = _var(lab, "historical_simulation", "0.05"), _var(lab, "historical_simulation", "0.01")
    raw95, raw99 = _var(lab, "raw_ovx:fhs", "0.05"), _var(lab, "raw_ovx:fhs", "0.01")
    sharpe_tests = prod["economic"]["vol_target_overlay"]["sharpe_difference_tests"]
    tests, vs_trailing = sharpe_tests["forecast_minus_buy_hold"], sharpe_tests["forecast_minus_trailing"]
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
    leader = _spa_leader(lab)
    if _sig(k["spa_p"]):
        better = (f"In sample, after the search: {leader['label']}; the live test decides"
                  if leader and leader["key"] == chall["key"] else "In sample, after the search: some alternative")
    elif k["spa_p"] < 0.10:
        better = f"Inconclusive after the search: {chall['label']}"
    else:
        better = "Not shown after the search"
    spa_row = _ledger_row(ledger, "vol_spa_beats_record")
    spa_bh = ((" (" + ("significant after the ledger's Benjamini-Hochberg adjustment" if spa_row["survives_bh_5pct"]
                       else "not after the ledger's Benjamini-Hochberg adjustment")
               + f", q = {_fmt(spa_row['q_value_bh'])})") if _sig(k["spa_p"]) else "")
    mz = champ["mincer_zarnowitz"]
    calibrated = "Unbiasedness not rejected" if not _sig(mz["p_unbiased"]) else "Biased (Mincer-Zarnowitz rejects)"
    clean = var95["independence_pass_grids"] == grids and var99["independence_pass_grids"] == grids
    hs_clusters = hs95["independence_pass_grids"] + hs99["independence_pass_grids"] < 2 * grids
    raw_as_good = (raw95["independence_pass_grids"] >= var95["independence_pass_grids"]
                   and raw99["independence_pass_grids"] >= var99["independence_pass_grids"])
    if clean and hs_clusters:
        var_answer = ("Better than no volatility model, but so is raw OVX: the gain is from volatility scaling"
                      if raw_as_good else "Yes: its breaches do not cluster")
    else:
        var_answer = "Partly"
    money = "No; volatility scaling controls risk" if not _sig(tests["p_value"]) else (
        "Yes" if tests["difference"] > 0 else "No: it lowers the Sharpe ratio")
    null, share_sig, var_sig = _zero_premium(vrp)
    swap_p = swap["p_value_one_sided"]
    swap_sig = _sig(swap_p)
    findings = [share_sig, var_sig, swap_sig]
    vrp_answer = (("Not established" if not any(findings) else "Partly") + ": in vol points, "
                  + ("significantly above" if share_sig else "not significantly above") + " what a zero premium gives; in variance, "
                  + ("significant" if var_sig else "not significant") + "; as a short vol-swap return, "
                  + ("significant" if swap_sig else "inconclusive" if swap_p < 0.10 else "not significant"))
    direction = "No" if _ledger_row(ledger, "vol_dir_vs_noise_floor")["status"] != "supported" else "Above its floor"

    rows = ["| Question | Answer | Evidence | Source |", "|---|---|---|---|"]
    rows.append(f"| Is next-week WTI volatility forecastable from free data? | {forecastable} | QLIKE "
                f"{_fmt(k['champ_qlike'])} vs {_fmt(k['persistence_qlike'])} for persistence (DM "
                f"{_peq(pdm['p_value'])}); {len(_worse_than_record(lab))} of the {feasible_benchmarks} feasible "
                f"benchmarks significantly worse | lab |")
    rows.append(f"| Does it add to implied volatility? | {adds} | {ovx_only['label']} {_fmt(ovx_only['qlike'])} (DM "
                f"{_peq(ovx_only['dm_qlike_vs_har_iv']['p_value'])}), {raw['label']} {_fmt(raw['qlike'])} (DM "
                f"{_peq(raw['dm_qlike_vs_har_iv']['p_value'])}); OVX elasticity {_fmt(k['ovx_coef']['coef'], 2)} "
                f"(HAC t {_fmt(k['ovx_coef']['t_hac'], 1)}) | lab |")
    rows.append(f"| Is anything better? | {better} | {chall['label']} QLIKE {_fmt(k['chall_qlike'])} (pairwise DM "
                f"{_peq(k['chall_dm_p'])}, before the search is accounted for); model of record "
                f"{'inside' if champ['in_mcs_qlike'] else 'outside'} the {mcs_level} MCS "
                f"(p = {_fmt(champ['mcs_qlike_p'])}); SPA p = {_fmt(k['spa_p'])} after the search{spa_bh}; challenger on the "
                f"live record | lab |")
    rows.append(f"| Are the forecasts calibrated? | {calibrated} | Mincer-Zarnowitz slope {_fmt(mz['slope'], 2)} "
                f"(joint p = {_p(mz['p_unbiased'])}); {_interval_level(lab)} intervals cover {k['coverage']}% "
                f"(adaptive conformal: on target by construction) | lab |")
    rows.append(f"| Does it make a better weekly VaR? | {var_answer} | Breach independence passes on "
                f"{var95['independence_pass_grids']}/{grids} grids at 95% and {var99['independence_pass_grids']}/{grids} "
                f"at 99%, vs {hs95['independence_pass_grids']}/{grids} and {hs99['independence_pass_grids']}/{grids} for "
                f"historical simulation and {raw95['independence_pass_grids']}/{grids} and "
                f"{raw99['independence_pass_grids']}/{grids} for raw OVX scaling; 99%: {var99['exceedances_by_grid'][0]} "
                f"breaches vs {var99['expected']} expected on the production grid (Kupiec passes "
                f"{var99['kupiec_pass_grids']}/{grids}) | lab |")
    rows.append(f"| Does it make money as a vol target? | {money} | Sharpe difference vs buy-and-hold "
                f"{_fmt(tests['difference'], 2, True)} ({_peq(tests['p_value'])}), vs trailing-vol sizing "
                f"{_fmt(vs_trailing['difference'], 2, True)} ({_peq(vs_trailing['p_value'])}); excess kurtosis "
                f"{k['fc_kurt']}, against {k['tr_kurt']} sized on trailing vol (difference not tested) and "
                f"{k['bh_kurt']} for buy-and-hold | production run |")
    rows.append(f"| Is there a variance risk premium? | {vrp_answer} | IV above next-month RV in "
                f"{vrp['share_positive_pct']}% of periods, vs {null['share_positive_pct']}% expected with zero premium "
                f"(one-sided bootstrap {_peq(null['p_value_one_sided'])}); mean variance premium "
                f"{_fmt(vrp['mean_premium_variance_pts'], 0)} (95% CI {_fmt(ci[0], 0)} to {_fmt(ci[1], 0)}); short "
                f"vol-swap Sharpe {vrp['short_vol_swap_sharpe']} (one-sided {_peq(swap['p_value_one_sided'])} "
                f"against a zero mean, though with no premium the vol-point gap would still average "
                f"{null['mean_premium_vol_pts']}) | production run |")
    rows.append(f"| Is volatility-direction accuracy evidence of skill? | {direction} | {k['dir_acc']}% observed vs "
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
        f"tests use every day. Each of the {_word(_grids(lab))} {c['step']}-session sampling grids (offsets "
        f"0–{_grids(lab) - 1}, {_grid_weeks(lab)} non-overlapping weeks each; grid 0 is the production validator's) is "
        f"also scored on its own, and VaR is scored only there, since it needs independent outcomes.",
        "- **Forecasts.** Losses, VaR and risk use the variance-consistent forecast (the median times a Duan "
        "smearing factor). The direction call and the intervals use the median.",
        f"- **Inference.** Model Confidence Set at {_mcs_level(lab)} ({mcs['statistic']} statistic, "
        f"{mcs['reps']:,} {mcs['bootstrap']}-bootstrap replications, blocks of {mcs['block_daily']} days or "
        f"{mcs['block_weekly']} weeks, seed {mcs['seed']}); Diebold-Mariano with Newey-West variance and the "
        f"Harvey-Leybourne-Newbold correction; Hansen's SPA against the model of record.{control_text}",
    ])


# --------------------------------------------------------------------------- 2.1 the field

def leaderboard_table() -> str:
    lab = _lab()
    champ_key, chall_key = lab["config"]["champion"], lab["config"]["challenger"]
    board = lab["leaderboard"]
    grids = _grids(lab)
    rows = [f"| Model | Family | QLIKE | DM vs model of record (p) | DM range over the {grids} sampling grids | "
            f"MCS p | In {_mcs_level(lab)} MCS (grids) | Realized utility (% / yr) |",
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
            mcs = f"{'yes' if m['in_mcs_qlike'] else 'no'} ({m['in_mcs_qlike_grids']}/{grids})"
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
                f"Hansen's SPA test (studentized) of the null that no model beats the model of record, over the "
                f"{len(spa['models'])} feasible alternatives: p = {_fmt(spa['p_consistent'])}, read with the ledger's "
                f"Benjamini-Hochberg result in section 2.1. Realized utility: "
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
    para = [f"The {_mcs_level(lab)} Model Confidence Set keeps {len(mcs)} of the {len(feasible)} feasible models "
            f"({_names(mcs)}), and {use_text}."]
    row = _ledger_row(ledger, "vol_mcs_excludes_record")
    if not champ["in_mcs_qlike"]:
        bh = "survives" if row.get("survives_bh_5pct") else "does not survive"
        para.append(f"The model of record is outside it (MCS p = {_fmt(champ['mcs_qlike_p'])}; the ledger counts "
                    f"this as supported, but it {bh} the Benjamini-Hochberg adjustment) and is inside it on "
                    f"{champ['in_mcs_qlike_grids']} of the {_grids(lab)} sampling grids.")
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
        para.append(f"The model of record and the {ovx_only['label']} are statistically indistinguishable "
                    f"({_peq(op)}): the realized-volatility terms add nothing detectable to OVX alone.")
    else:
        para.append(f"The model of record and the {ovx_only['label']} differ significantly ({_peq(op)}).")
    out.append(" ".join(para))

    para = []
    top_q = chall["qlike"] == min(m["qlike"] for m in board["models"])
    top_u = chall["realized_utility_pct"] == max(m["realized_utility_pct"] for m in board["models"])
    udm = chall["dm_utility_vs_har_iv"]
    if top_q and top_u:
        para.append(f"{chall['label']} has the lowest QLIKE and the highest realized utility in the field, and "
                    f"beats the model of record on both in a pairwise test (DM "
                    f"{_peq(chall['dm_qlike_vs_har_iv']['p_value'])} on QLIKE, {_peq(udm['p_value'])} on utility).")
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
    spa_block = board["spa_qlike_vs_har_iv"]
    spa = spa_block["p_consistent"]
    raw = spa_block["non_studentized"]["p_consistent"]
    leader = _spa_leader(lab)
    named = (f"; {leader['label']} has the largest studentized gain, t {_fmt(spa_block['t_stats'][leader['key']], 2)}"
             if leader else "")
    if _sig(spa):
        after = (f"after the search the null that no alternative beats the model of record is rejected at 5% "
                 f"(studentized p = {_fmt(spa)}{named}){_spa_adjusted(ledger)}")
    elif spa < 0.10:
        after = (f"after the search the variant's edge is inconclusive (studentized p = {_fmt(spa)}, not significant "
                 f"at 5%{named})")
    else:
        after = f"after the search the variant's edge is not significant (studentized p = {_fmt(spa)}{named})"
    # Why the two readings differ, said only when they do in the direction it explains.
    mechanism = (": on raw means the alternatives whose loss differences vary most set the bootstrap maximum"
                 if raw > spa else "")
    para.append(f"The variant was found by searching this sample, and those pairwise p-values do not account for "
                f"the search. Hansen's SPA test does: {after}. Without studentizing (White's Reality Check with "
                f"Hansen's recentring, the figure published before this correction; see the changelog's errata) the "
                f"same draws give p = {_fmt(raw)}{mechanism}.{_spa_robustness(lab)} The test accounts for the search "
                f"within the field, not for how the field was assembled, and every reading is of this sample. That is "
                f"why the variant competes live rather than replacing the model of record on its historical record.")
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
                f"realized vol, Newey-West t with {c['hac_lags']} lags (the five-session targets overlap). Units: "
                f"realized volatility annualized as a decimal, OVX in index points. Source: lab.")
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
    raw, champ = _model(lab, "raw_ovx"), _champ(lab)
    ratio, champ_ratio = raw["mean_ratio_actual_to_forecast"], champ["mean_ratio_actual_to_forecast"]
    const = terms["const"]["coef"]
    units = -ovx["coef"] * math.log(100.0)
    text.append(f"Most of the constant is a change of units: OVX enters in index points and realized volatility as a "
                f"decimal, which accounts for {_fmt(units, 3, True)} of the {_fmt(const, 3, True)}; with OVX as a decimal "
                f"the constant is {_fmt(const - units, 3, True)}.")
    if ratio >= 1:
        text.append(f"Used directly, OVX is below realized volatility in vol units: realized averages {_fmt(ratio)} "
                    f"times the previous session's OVX.")
        return " ".join(text)
    text.append(f"Used directly, OVX is above realized volatility in vol units: realized averages {_fmt(ratio)} times "
                f"the previous session's OVX. That gap does not measure the variance risk premium. A five-session "
                f"realized volatility is a noisy estimate, and its mean is below the square root of its expected "
                f"variance (Jensen's inequality), so a forecast that is right in variance is too high in vol units.")
    mz_p = champ["mincer_zarnowitz"]["p_unbiased"]
    if not _sig(mz_p) and champ_ratio < 1:
        share = math.log(champ_ratio) / math.log(ratio)
        part = "more than half" if share > 0.5 else "part"
        text.append(f"The model of record gives a measure of that effect: Mincer-Zarnowitz does not reject that its "
                    f"variance forecast is unbiased ({_peq(mz_p)}), yet realized averages {_fmt(champ_ratio)} times "
                    f"it: in logs, {part} of OVX's gap. The rest is OVX's level against the front-month future: a "
                    f"30-calendar-day horizon rather than five sessions, its basis to the future (section 9) and "
                    f"whatever premium it carries (section 2.5).")
    # Variance units, from the production-grid weeks the lab publishes (weekly series, percent).
    s = lab["series"]
    weeks = [(d, a, o) for d, a, o in zip(s["date"], s["actual"], s["ovx"]) if a is not None and o is not None]

    def var_ratio(rows: list[tuple[str, float, float]]) -> float:
        return sum(a * a for _, a, _ in rows) / sum(o * o for _, _, o in rows)

    full = var_ratio(weeks)
    years = sorted({d[:4] for d, _, _ in weeks})
    # The calendar year whose removal moves the ratio most: a ratio of means is dominated by its largest weeks.
    drop = max(years, key=lambda y: abs(var_ratio([w for w in weeks if w[0][:4] != y]) - full))
    without = var_ratio([w for w in weeks if w[0][:4] != drop])
    sign = ("has no stable sign" if (full - 1) * (without - 1) < 0 else
            "points the same way as in vol units" if full < 1 else "points the other way")
    text.append(f"In variance units, the units of the target and of QLIKE, the gap {sign}: over the {len(weeks)} "
                f"weeks of the production grid, mean realized variance is {full:.2f} times the mean squared lagged OVX, "
                f"and {without:.2f} without {drop}. In the log regression the constant and the realized terms absorb "
                f"the gap, and the smearing factor (section 1, Forecasts) returns the forecast to the variance scale.")
    return " ".join(text)


# --------------------------------------------------------------------------- 2.3 calibration

def calibration_block() -> str:
    lab = _lab()
    champ_key, chall_key = lab["config"]["champion"], lab["config"]["challenger"]
    conf = lab["conformal"]
    o = _prod()["overall"]
    rows = ["| Model, sample | MZ intercept | MZ slope (SE) | Joint p (unbiased) | Actual / forecast | "
            f"{_interval_level(lab)} coverage | Coverage by year (min to max) | Interval score |",
            "|---|---:|---:|---:|---:|---:|---|---:|"]
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
                f"{lab['config']['conformal']['method'].replace('Gibbs-Candes', 'Gibbs and Candès')}, step "
                f"{lab['config']['conformal']['gamma']}, scored only "
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
                f"against a target of {target:g}%. Adaptive conformal inference steers long-run coverage to its target "
                f"whatever the model, so that figure checks the method; the yearly coverage and the interval score "
                f"are what differ between models. Coverage by calendar year ranges {min(years)}% to {max(years)}%; "
                f"with about fifty independent weeks a year, one year's coverage has a standard error of about "
                f"{se:.0f} points, so deviations of that size are expected.")
    return " ".join(text)


# --------------------------------------------------------------------------- 2.4 risk

def var_table() -> str:
    lab = _lab()
    summary = lab["var_backtest"]["summary"]
    label = {m["key"]: m["label"] for m in lab["leaderboard"]["models"]}
    grids, c = _grids(lab), lab["config"]
    rows = ["| Method | Level | Exceedances by grid (expected on grid 0) | Kupiec | Independence | Cond. coverage | DQ |",
            "|---|---:|---|---:|---:|---:|---:|"]
    for key, s in summary.items():
        method, level = key.split("@")
        if ":normal" in method:
            continue
        name = "Historical simulation (no vol model)" if method == "historical_simulation" else \
            f"{label.get(method.split(':')[0], method)} + filtered HS"
        rows.append(f"| {name} | {float(level) * 100:.0f}% | {', '.join(map(str, s['exceedances_by_grid']))} "
                    f"({s['expected']}) | {s['kupiec_pass_grids']}/{grids} | {s['independence_pass_grids']}/{grids} | "
                    f"{s['conditional_coverage_pass_grids']}/{grids} | {s['dq_pass_grids']}/{grids} |")
    rows.append("")
    rows.append(f"Weekly ({c['horizon_sessions']}-session) VaR of a long front-month position, scored on each of the "
                f"{_word(grids)} {c['step']}-session sampling grids (non-overlapping outcomes); a test passes at 5%. Kupiec "
                f"(1995) unconditional coverage, Christoffersen (1998) independence and conditional coverage, "
                f"Engle-Manganelli (2004) DQ. Source: lab.")
    return "\n".join(rows)


def var_reading() -> str:
    lab = _lab()
    summary = lab["var_backtest"]["summary"]
    label = {m["key"]: m["label"] for m in lab["leaderboard"]["models"]}
    levels = ("0.05", "0.01")
    grids, champion = _grids(lab), lab["config"]["champion"]
    hs = {lvl: summary[f"historical_simulation@{lvl}"] for lvl in levels}
    fhs = {lvl: {k.split(":")[0]: v for k, v in summary.items() if k.endswith(f":fhs@{lvl}")} for lvl in levels}
    methods = list(fhs["0.05"])
    text = [f"Historical simulation, with no volatility model, passes Kupiec's coverage test on "
            f"{hs['0.05']['kupiec_pass_grids']}/{grids} grids at 95% and {hs['0.01']['kupiec_pass_grids']}/{grids} at "
            f"99%; at 99% it breaches {min(hs['0.01']['exceedances_by_grid'])} to "
            f"{max(hs['0.01']['exceedances_by_grid'])} times per grid against {hs['0.01']['expected']} expected. Its "
            f"breaches cluster: it passes the independence test on {hs['0.05']['independence_pass_grids']}/{grids} "
            f"grids at 95% and {hs['0.01']['independence_pass_grids']}/{grids} at 99%."]
    clean = [m for m in methods if all(fhs[lvl][m]["independence_pass_grids"] == grids for lvl in levels)]
    if len(clean) == len(methods):
        text.append("Scaling by any of the volatility forecasts removes the clustering: every scaled method passes "
                    "the independence test on every grid at both levels.")
    else:
        rest = []
        for lvl in levels:
            fails = [f"{label.get(m, m)} {fhs[lvl][m]['independence_pass_grids']}/{grids}" for m in methods
                     if fhs[lvl][m]["independence_pass_grids"] < grids]
            if fails:
                rest.append(f"at {100 - float(lvl) * 100:.0f}%, {_join(fails)}")
        text.append(f"Scaling by a volatility forecast removes most of the clustering: with "
                    f"{_join([label.get(m, m) for m in clean])} the breaches pass the independence test on every grid "
                    f"at both levels; the other scaled methods pass on fewer grids ({'; '.join(rest)}).")
    if "raw_ovx" in clean and champion in clean:
        text.append(f"{label['raw_ovx']}, with nothing fitted, does as well as the model of record here, so these pass "
                    f"counts show what scaling by volatility does; they do not rank the forecasts, and no test here "
                    f"compares the scaled methods' VaR losses directly.")
    pairs = [(m, g) for m in methods for g in range(grids)]
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
    text = [f"Sized on the forecast, the book's realized volatility is {fc['realized_vol_pct']}% against the "
            f"{target:g}% target and its excess kurtosis {fc['excess_kurtosis']}, against {bh['excess_kurtosis']} for "
            f"buy-and-hold."]
    k = {"bh_kurt": bh["excess_kurtosis"], "fc_kurt": fc["excess_kurtosis"], "tr_kurt": tr["excess_kurtosis"]}
    share = _scaling_share(k)
    text.append(f"Sizing on trailing 22-day volatility, with no forecast, gives {tr['realized_vol_pct']}% and "
                f"{tr['excess_kurtosis']}"
                + (", so most of the risk control is a property of scaling by volatility at all, not of this "
                   "forecast." if share is not None and share >= 0.9 else "."))
    closer = abs(tr["realized_vol_pct"] - target) - abs(fc["realized_vol_pct"] - target)
    kurt = tr["excess_kurtosis"] - fc["excess_kurtosis"]
    es = fc["expected_shortfall_97_5_pct"] - tr["expected_shortfall_97_5_pct"]
    dd = tr["max_drawdown_pct"] - fc["max_drawdown_pct"]
    text.append(f"Against trailing sizing, the forecast's book is {abs(closer):.1f} points "
                f"{'closer to' if closer >= 0 else 'further from'} the target, its excess kurtosis is "
                f"{abs(kurt):.2f} {'lower' if kurt >= 0 else 'higher'}, its 97.5% expected shortfall {abs(es):.1f} points "
                f"{'smaller' if es >= 0 else 'larger'} and its maximum drawdown {abs(dd):.1f} points "
                f"{'smaller' if dd >= 0 else 'larger'}; none of these differences is tested.")
    p1, p2 = tests["forecast_minus_buy_hold"]["p_value"], tests["forecast_minus_trailing"]["p_value"]
    d1, d2 = tests["forecast_minus_buy_hold"]["difference"], tests["forecast_minus_trailing"]["difference"]
    if not _sig(p1) and not _sig(p2):
        text.append(f"The Sharpe ratio is the one difference tested, and sizing on the forecast does not change it "
                    f"detectably, against buy-and-hold ({_fmt(d1, 2, True)}, {_peq(p1)}) or against trailing sizing "
                    f"({_fmt(d2, 2, True)}, {_peq(p2)}). This matches the evidence on volatility targeting outside "
                    f"equities (Harvey et al. 2018): scaling by volatility controls risk; it does not create return.")
    else:
        text.append(f"The Sharpe differences, the one comparison tested, have {_peq(p1)} against buy-and-hold and "
                    f"{_peq(p2)} against trailing sizing.")
    return " ".join(text)


# --------------------------------------------------------------------------- 2.5 VRP

def vrp_block() -> str:
    vrp = _prod()["economic"]["variance_risk_premium"]
    ledger = _ledger()
    t = vrp["timing_regression"]
    ci = vrp["mean_premium_variance_ci95"]
    swap, short_var = vrp["sharpe_positive_test"]["short_vol_swap"], vrp["sharpe_positive_test"]["short_variance"]
    worst = "; ".join(f"from {w['start']}, {_fmt(w['premium_vol_pts'], 1)} vol pts" for w in vrp["worst_periods"])
    null, share_sig, var_sig = _zero_premium(vrp)
    boot = null["bootstrap"]
    retracted = _ledger_row(ledger, "vrp_exists")["status"] == "retracted"
    swap_p = swap["p_value_one_sided"]
    timing_p = vrp["timing_p_value"]
    timing_sig = _sig(t["p_value"]) or _sig(timing_p)
    same_way = t["slope"] > 0 and vrp["timed_rich_mean_pnl"] > vrp["timed_cheap_mean_pnl"]
    return "\n".join([
        f"- **In vol points, the share of periods with OVX above realized is "
        f"{'significantly' if share_sig else 'not significantly'} above what a zero premium gives.** OVX exceeded the "
        f"realized "
        f"volatility of the following 20 sessions in {vrp['share_positive_pct']}% of {vrp['n_periods']} "
        f"non-overlapping periods, by {vrp['mean_premium_vol_pts']} points on average. With no premium (implied "
        f"variance equal to expected realized variance), OVX would still be above realized volatility in most "
        f"periods: a 20-session realized volatility is a noisy, right-skewed estimate, and as the square root of a "
        f"variance its mean is below the square root of its expected variance (Jensen's inequality). Keeping the observed shape of the per-period "
        f"ratio of realized to implied variance and rescaling it to a mean of 1, a zero premium gives OVX above "
        f"realized in {null['share_positive_pct']}% of periods, by {null['mean_premium_vol_pts']} points on average. "
        f"The observed share is {null['excess_share_pct']} points above that, one-sided {_peq(null['p_value_one_sided'])} "
        f"({_level(null['p_value_one_sided'])}; stationary block bootstrap, {boot['reps']:,} draws, mean block "
        f"{boot['mean_block_periods']} periods). Variation of the premium across periods also widens the ratios the "
        f"null is built from, which raises the null share, so the test errs on the conservative side."
        + (" The ledger retracts the earlier sign test against 50%: that is not the share a zero premium gives."
           if retracted else ""),
        f"- **In variance units, the units an option seller is paid in and the relevant measure, it is "
        f"{'significant' if var_sig else 'not significant'}.** The mean premium is "
        f"{_fmt(vrp['mean_premium_variance_pts'], 1)} variance points (95% Newey-West CI {_fmt(ci[0], 0)} to "
        f"{_fmt(ci[1], 0)}): the few periods where realized volatility explodes ({worst}) "
        f"{'outweigh' if vrp['mean_premium_variance_pts'] < 0 else 'do not outweigh'} the many small gains.",
        f"- **Harvesting it.** A short vol-swap proxy has an annualized Sharpe ratio of {vrp['short_vol_swap_sharpe']}, "
        f"but its period returns are skewed ({_fmt(swap['skewness'], 2)}) and fat-tailed (excess kurtosis "
        f"{swap['excess_kurtosis']}); the one-sided test of a positive Sharpe ratio with the Mertens (2002) / Opdyke "
        f"(2007) standard error, which allows for both, gives {_peq(swap_p)} ({_level(swap_p)}). That test is "
        f"against a zero mean, but with no premium the proxy's mean is still {null['mean_premium_vol_pts']} vol "
        f"points (the same Jensen gap), so it overstates the evidence for a premium. The convex short-variance "
        f"position has a Sharpe ratio of {_fmt(vrp['short_variance_sharpe'], 2)} ({_peq(short_var['p_value_one_sided'])}; "
        f"{_fmt(vrp['short_variance_sharpe_ex_worst_year'], 2)} without {vrp['worst_year']}).",
        f"- **Timing it with the forecast: {'a significant gain' if timing_sig else 'no significant gain'}.** "
        f"Regressing short-variance P&L on OVX minus the forecast: slope {_fmt(t['slope'], 2)}, HAC t {t['t_hac']}, "
        f"{_peq(t['p_value'])} (n = {t['n']}); short-variance P&L in periods when OVX minus the forecast is above "
        f"its trailing median against the other periods (one-sided Welch t-test): {_peq(timing_p)}."
        + (" Both estimates point the way timing would need, so this is a failure to show a gain, not evidence "
           "against one." if same_way and not timing_sig else ""),
        "",
        "Source: production run (`data/vol_forecast_validation.json`).",
    ])


def vrp_reading() -> str:
    vrp = _prod()["economic"]["variance_risk_premium"]
    null, share_sig, var_sig = _zero_premium(vrp)
    text = []
    if vrp["share_positive_pct"] > 50 and not share_sig:
        text.append(f"The usual statement, that implied volatility exceeds realized most of the time, is true here "
                    f"({vrp['share_positive_pct']}% of periods), but it is not evidence of a premium: with none, the "
                    f"noise and skew of a 20-session realized volatility would give about "
                    f"{null['share_positive_pct']}%.")
    elif vrp["share_positive_pct"] > 50:
        text.append(f"Implied volatility exceeds realized in {vrp['share_positive_pct']}% of periods, more than the "
                    f"{null['share_positive_pct']}% a zero premium would give.")
    if var_sig:
        text.append("In variance, the unit a variance seller is paid in, the premium is significant.")
    elif vrp["mean_premium_variance_pts"] < 0 and vrp["short_variance_sharpe_ex_worst_year"] > 0:
        text.append(f"The relevant measure is the premium in variance, the unit a variance seller is paid in, and in "
                    f"variance the {vrp['worst_year']} periods outweigh the rest of the sample, so the premium is not "
                    f"distinguishable from zero.")
    else:
        text.append("The relevant measure is the premium in variance, the unit a variance seller is paid in, and in "
                    "variance it is not distinguishable from zero.")
    if not share_sig and not var_sig:
        text.append("So a premium is not established here; neither test shows that there is none.")
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
    prod_sim = (pnf["sims"], pnf["dir_acc_pct_mean"], list(pnf["dir_acc_pct_range"]),
                pnf["mean_reversion_dir_acc_pct_mean"])
    lab_sim = (nf["sims"], nf["har_direction_acc_pct_mean"], list(nf["har_direction_acc_pct_range"]),
               nf["mean_reversion_acc_pct_mean"])

    def sim_row(source: str, sims: int, mean: float, span: list[float], rule: float) -> str:
        return f"| {sim}, weekly grid, {sims} simulations | {source} | {mean}% ({span[0]}–{span[1]}) | {rule}% |"

    lines = [
        "| Pipeline run on | Source | Direction accuracy | Mean-reversion rule |", "|---|---|---:|---:|",
        f"| WTI, production weekly grid ({o['n']} weeks) | production run | {o['har_dir_acc_pct']}% | "
        f"{o['mean_reversion_dir_acc_pct']}% |",
        f"| WTI, every out-of-sample day | lab | {_fmt(_champ(lab)['direction_acc_pct'], 1)}% | "
        f"{board['mean_reversion_acc_pct']}% |",
    ]
    if prod_sim == lab_sim:
        lines.append(sim_row("production run and lab (the same simulation)", *prod_sim))
    else:
        lines += [sim_row("production run", *prod_sim), sim_row("lab", *lab_sim)]
    lines += [
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
    grids = _grids(lab)
    out = [
        f"- **Sampling grids.** The model of record's QLIKE on the {_word(grids)} {lab['config']['step']}-session sampling "
        f"grids ranges {_fmt(min(qby))} to {_fmt(max(qby))}; the challenger is in the {_mcs_level(lab)} MCS on "
        f"{chall['in_mcs_qlike_grids']}/{grids} and the model of record on {champ['in_mcs_qlike_grids']}/{grids}. A "
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
        "",
        "Source: lab, except the bullet marked production run.",
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


def leak_mechanism(root: str = "") -> str:
    """`root`: the repository root relative to the page the block is in ("../" for docs/)."""
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
            f"([`backend/leak_anatomy.py`]({root}backend/leak_anatomy.py)):")


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
    ledger_rows = [r for r in _ledger()["rows"] if r["family"] == "leak anatomy" and "block-shuffled" in r["hypothesis"]]
    ledger_note = (" The ledger's block-shuffled rows test each run against its own base rate and take the largest "
                   "of the per-run p-values (section 6)." if ledger_rows and all(map(_per_run_p, ledger_rows)) else "")
    shuffled = [(names.get(mkey, mkey), block) for mkey, block in a["block_shuffled_cl"].items()
                if isinstance(block, dict) and "purged" in block]
    beat_u = _join([f"{b['unpurged_leaked']['runs_beating_base_rate_5pct']} of {b['unpurged_leaked']['n_runs']} "
                    f"({name.lower()})" for name, b in shuffled])
    beat_p = _join([f"{b['purged']['runs_beating_base_rate_5pct']} of {b['purged']['n_runs']}" for _, b in shuffled])
    real = [block for block in a["real_cl_5y_window"].values() if isinstance(block, dict) and "purged" in block]
    real_note = ""
    if real and all("runs_beating_base_rate_5pct" in b[arm] for b in real for arm in ("unpurged_leaked", "purged")):
        real_u = sum(b["unpurged_leaked"]["runs_beating_base_rate_5pct"] > 0 for b in real)
        real_p = sum(b["purged"]["runs_beating_base_rate_5pct"] > 0 for b in real)
        real_note = (f" On the real 5-year window (one run per model) the leaked run beats its base rate for {real_u} "
                     f"of {len(real)} models and the purged run for {real_p} of {len(real)}.")
    rows.append("")
    rows.append(f"Sharpe ratios here are gross (no transaction costs); the retracted model's {ref['sharpe']:.2f} is "
                f"{ref['sharpe_basis']}, so the two are not like for like, and costs would only lower these. For "
                f"comparison, the retracted model: {ref['accuracy_pct']:.1f}% over {ref['n']} weeks. Same walk-forward, "
                f"same five-session labels, the purge switched off and on. The block-shuffled runs permute the same "
                f"CL=F returns, so they are not independent samples, and a test that pools their decisions would "
                f"overstate the evidence. "
                f"Real 5-session CL=F returns rise {a['design']['cl_5_session_up_share_pct']}% of the time, so the "
                f"no-skill accuracy on them is each run's own base rate, not 50%: leaked runs beating their base rate "
                f"at 5%: {beat_u}; purged: {beat_p}.{ledger_note}{real_note} Source: leak anatomy.")
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
                f"{pw['mde_spread_pct_per_month']}%/month with {pw['target_power'] * 100:.0f}% power, and had "
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
    if not _sig(post["spread_p_value_one_sided"]) and spread_power < pw["target_power"]:
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

def _power(row: dict) -> tuple[float, float] | None:
    """(power, target power) from a ledger row's context, where the artifact records them."""
    ctx = row.get("context") or {}
    power = next((v for key, v in ctx.items() if key.startswith("power")), None)
    return (power, ctx["target_power"]) if power is not None and "target_power" in ctx else None


def ledger_block() -> str:
    led = _ledger()
    s = led["summary"]
    b = s["by_status"]
    rows_by_id = {r["id"]: r for r in led["rows"]}
    alpha = led["alpha"]
    inconclusive_below = _code_constant("backend/research_ledger.py", "INCONCLUSIVE_BELOW")
    selected = s.get("selected_after_search", [])
    family_supported = b["supported"] - len(s.get("supported_selected_after_search", []))
    text = [f"{s['rows']} hypotheses: {b['supported']} supported (p < {alpha:.2f} in the direction the hypothesis "
            f"states), {b['inconclusive']} inconclusive ({alpha:.2f} ≤ p < {inconclusive_below:.2f}), "
            f"{b['not_supported']} not supported and {b['retracted']} retracted. Not supported means "
            f"p ≥ {inconclusive_below:.2f} or an estimate pointing the other way: a failure to reject, not evidence "
            f"against the hypothesis."]
    under = [rows_by_id[key] for key in s.get("underpowered", [])]
    if under:
        def described(r: dict) -> str:
            power = _power(r)
            quoted = f"\"{r['hypothesis'].rstrip('.')}\""
            return quoted + (f" (power {power[0] * 100:.0f}% against a target of {power[1] * 100:.0f}%)" if power else "")

        text.append(f"Where the artifact records a test's power, a row whose power is below its target is marked "
                    f"underpowered. {_word(len(under)).capitalize()} not-supported row{' is' if len(under) == 1 else 's are'}: "
                    f"{'; '.join(described(r) for r in under)}.")
    text.append(f"The multiplicity adjustments run over {s['rows_in_multiplicity_family']} rows: every row with a "
                f"p-value, the retracted ones included"
                + (f", except {len(selected)} selected after a search (below)" if selected else "") + ".")
    text.append(f"Of the {family_supported} supported rows in that family, {len(s['supported_surviving_bh_5pct'])} "
                f"survive Benjamini-Hochberg at 5%, {len(s['supported_surviving_holm_5pct'])} survive Holm, and "
                f"{len(s['supported_clearing_hlz_t3'])} clear the t > 3 hurdle of Harvey, Liu and Zhu (2016). By "
                f"family:")
    out = [" ".join(text), "",
           "| Family | Hypotheses | Supported | Inconclusive | Not supported | Retracted | Supported, surviving BH | "
           "Supported, clearing t > 3 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for family, f in s["by_family"].items():
        fb = f["by_status"]
        out.append(f"| {family} | {f['rows']} | {fb['supported']} | {fb['inconclusive']} | {fb['not_supported']} | "
                   f"{fb['retracted']} | {f['supported_surviving_bh_5pct']} | {f['supported_clearing_hlz_t3']} |")
    out += ["", "The supported hypotheses in the multiplicity family that survive Benjamini-Hochberg at 5%:", "",
            "| Claim | p | BH q | Clears t > 3 |", "|---|---:|---:|:---:|"]
    for key in s["supported_surviving_bh_5pct"]:
        r = rows_by_id[key]
        out.append(f"| {r['hypothesis']} | {_p(r['p_value'])} | {_p(r.get('q_value_bh'))} | "
                   f"{'yes' if r.get('clears_hlz_t3') else 'no'} |")
    out += ["", "Source: `data/research_ledger.json`, rebuilt from the artifacts by `python -m backend.research_ledger`."]
    return "\n".join(out)


def _per_run_p(row: dict) -> bool:
    """Whether a ledger row's p-value is the largest of per-run p-values rather than a pooled test."""
    fields = (row.get("artifact") or {}).get("p_value_fields") or {}
    return len(fields) > 1 and all(".runs[" in path for path in fields)


def ledger_reading() -> str:
    led = _ledger()
    s = led["summary"]
    leak = [r for r in led["rows"] if r["family"] == "leak anatomy" and r["status"] == "supported"]
    every = [r for r in leak if r["id"] in s["supported_surviving_bh_5pct"]
             and r["id"] in s["supported_surviving_holm_5pct"] and r["id"] in s["supported_clearing_hlz_t3"]]
    text = []
    if leak and len(every) == len(leak):
        text.append(f"{'Both' if len(leak) == 2 else f'All {len(leak)}'} supported leak demonstrations survive every "
                    f"adjustment, so some of the project's strongest results are about how its own first result was "
                    f"wrong.")
    shuffled = [r for r in leak if "block-shuffled" in r["hypothesis"]]
    if shuffled:
        row = shuffled[0]
        runs = row.get("context", {}).get("runs")
        if _per_run_p(row):
            text.append(f"The block-shuffled demonstration does not pool its {runs} runs, which permute the same "
                        f"returns: it claims that every run beats its own base rate, and its p-value is the largest of "
                        f"the {runs} per-run p-values (an intersection-union test, valid however the runs depend on one "
                        f"another; section 4). The synthetic one pools runs on independent returns.")
        else:
            text.append(f"The block-shuffled one pools {runs} runs that permute the same returns, so its p-value "
                        f"overstates the evidence (section 4); the synthetic runs draw independent returns.")
    rows_by_id = {r["id"]: r for r in led["rows"]}
    spa = rows_by_id.get("vol_spa_beats_record")
    for key in s.get("supported_selected_after_search", []):
        row = rows_by_id[key]
        verdict = ""
        if spa is not None and spa.get("p_value") is not None:
            word = {"supported": "significant at 5%", "inconclusive": "inconclusive",
                    "not_supported": "not significant"}.get(spa["status"], spa["status"])
            bh = _spa_adjusted(led) if spa["status"] == "supported" else ""
            lab = _lab()
            leader = _spa_leader(lab)
            if spa["status"] == "supported" and not (leader and leader["key"] == lab["config"]["challenger"]):
                named = f" (the largest studentized gain is {leader['label']}'s, not the challenger's)" if leader else ""
                verdict = (f" The search-adjusted test, Hansen's SPA, asks whether any alternative beats the model of "
                           f"record and gives {_peq(spa['p_value'])}: some alternative does at 5%{named}{bh}; the live "
                           f"promotion test (section 3) decides whether the challenger replaces the model of record.")
            else:
                verdict = (f" The search-adjusted test of the same question, Hansen's SPA, gives "
                           f"{_peq(spa['p_value'])}, so after the search the edge is {word}{bh}; the live promotion "
                           f"test (section 3) decides whether the challenger replaces the model of record.")
        picked = ("The challenger is the lowest-loss model of the field on the same sample" if key == "vol_challenger_qlike"
                  else "The model it tests was picked as the best of a field on the same sample")
        text.append(f"One supported row is not in the table: \"{row['hypothesis'].rstrip('.')}\" "
                    f"({_peq(row['p_value'])}). {picked}, so that pairwise p-value ignores the search, and the row is "
                    f"left out of the multiplicity family.{verdict}")
    retracted = s["by_status"]["retracted"]
    vrp = rows_by_id.get("vrp_exists")
    text.append(f"The {retracted} retracted rows stay in the multiplicity count, since they were tested, but none is "
                f"counted as a survivor"
                + ("; one of them is the share of periods with OVX above realized volatility tested against 50%, the "
                   "wrong null for a premium (section 2.5)." if vrp is not None and vrp["status"] == "retracted"
                   else "."))
    return " ".join(text)


# --------------------------------------------------------------------------- 3. live record

def promotion_rule() -> str:
    return f"> {PROMOTION_RULE}"


def _t_critical(alpha: float, df: int) -> float:
    """The upper-alpha quantile of Student's t(df), by bisection on vol_record.student_t_sf (the
    function the promotion test takes its p-value from)."""
    lo, hi = 0.0, 1.0
    while student_t_sf(hi, df) > alpha:
        hi *= 2.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if student_t_sf(mid, df) > alpha else (lo, mid)
    return 0.5 * (lo + hi)


def _noncentral_t_sf(c: float, df: int, delta: float, steps: int = 2000) -> float:
    """P(T > c) for T noncentral t(df, delta) = (Z + delta) / S, S = sqrt(chi2(df) / df): the mean over
    S of P(Z > c S - delta), by Simpson's rule over S's density on mean +- 12 sd (S has mean ~1, sd
    ~1 / sqrt(2 df)). Agrees with scipy.stats.nct to ~1e-12 at the degrees of freedom used here."""
    from statistics import NormalDist

    z = NormalDist()
    sd = (2.0 * df) ** -0.5
    lo, hi = max(0.0, 1.0 - 12.0 * sd), 1.0 + 12.0 * sd
    log_const = math.log(2.0) + df / 2 * math.log(df / 2) - math.lgamma(df / 2)

    def integrand(s: float) -> float:
        if s <= 0.0:
            density = math.sqrt(2.0 / math.pi) if df == 1 else 0.0
        else:
            density = math.exp(log_const + (df - 1) * math.log(s) - df * s * s / 2)
        return density * (1.0 - z.cdf(c * s - delta))

    h = (hi - lo) / steps
    inner = sum((4 if i % 2 else 2) * integrand(lo + i * h) for i in range(1, steps))
    return (integrand(lo) + inner + integrand(hi)) * h / 3


def promotion_power(min_weeks: int = PROMOTION_WEEKS, alpha: float = PROMOTION_ALPHA) -> str:
    """Power of the promotion test if the challenger's historical edge is its true edge.

    The per-grid Diebold-Mariano statistics are on n_weeks non-overlapping weeks, so the edge per
    week, the mean loss difference over its standard deviation, is about stat / sqrt(n_weeks). With
    normal weekly differences the test's statistic on w weeks is then noncentral t with w - 1 degrees
    of freedom and noncentrality stat * sqrt(w / n_weeks), and the test rejects above the t(w - 1)
    critical value, as vol_record.promotion_test does. The defaults are the promotion rule's own
    sample size and level."""
    from statistics import median

    lab = _lab()
    chall = _chall(lab)
    n_weeks = lab["sample"]["n_weeks"]
    stat = median(chall["dm_qlike_vs_har_iv_by_grid"])
    crit = _t_critical(alpha, min_weeks - 1)
    edge = (f"How much the single test can tell: on the {_word(_grids(lab))} sampling grids the challenger's "
            f"Diebold-Mariano statistic against the model of record has a median of {_fmt(stat, 1)}, each over "
            f"{_grid_weeks(lab)} non-overlapping weeks. That edge was measured on the sample that selected the challenger, "
            f"so it is more likely to overstate the true edge than to understate it, and the power below is "
            f"correspondingly optimistic.")
    size = (f"If the challenger is no better, the test promotes it with probability at most {alpha * 100:.0f}%, "
            f"its size.")
    if stat <= 0:
        return f"{edge} That is no edge, so the test promotes it with probability at most its size, {alpha * 100:.0f}%."

    def power(weeks: int) -> float:
        return _noncentral_t_sf(_t_critical(alpha, weeks - 1), weeks - 1, stat * (weeks / n_weeks) ** 0.5)

    cap = 52 * 100                       # a century of weeks: past that, say so instead of searching on
    lo, hi = 2, min_weeks
    while power(hi) < 0.80 and hi < cap:
        lo, hi = hi, min(hi * 2, cap)
    if power(hi) < 0.80:
        weeks_80 = "more than a century of weeks"
    else:
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if power(mid) < 0.80 else (lo, mid)
        weeks_80 = f"about {hi:,} weeks ({hi / 52:.0f} years)"
    p52 = power(min_weeks)
    reading = (f"With {p52 * 100:.0f}% power, a keep verdict on that sample does not show that the challenger is no "
               f"better than the model of record." if p52 < 0.80 else
               f"With {p52 * 100:.0f}% power, a keep verdict on that sample is evidence against that edge.")
    return (f"{edge} If that is its true edge, the test on {min_weeks} independent weeks promotes it with "
            f"probability {p52 * 100:.0f}%, and a fixed-sample test with 80% power would need {weeks_80}. This treats the weekly loss differences as normal, so the statistic is "
            f"noncentral t with {min_weeks - 1} degrees of freedom, compared with the test's own critical value, "
            f"t({min_weeks - 1})'s {crit:.3f}. {size} {reading}")


# --------------------------------------------------------------------------- the pre-registration
# docs/PREREGISTRATION.md: the promotion test as registered, read from vol_record's constants and the
# lab's artifact, so its tables and figures cannot drift from the code that runs the test. Two facts
# are history, not data, and are pinned here: the record's first session (live-data; the first row
# scripts/verify_record.py prints), the SPA p-value published when the rule was fixed (commit
# 48138bc's README and data/vol_lab.json, the non-studentized test) and the one 2.2.0 published.
PREREG_FIRST_SESSION = "2026-10-20"
PREREG_PUBLISHED_SPA_P = 0.072
PREREG_STUDENTIZED_SPA_P = 0.011     # what 2.2.0 published in its place (CHANGELOG.md, 2.2.0 errata)

def _short_label(model: dict) -> str:
    """A model's label without the role the leaderboard appends to it ("HAR-IV (model of record)" -> "HAR-IV")."""
    return model["label"].split(" (")[0]


def prereg_design() -> str:
    """The registered design as a table: each element, its value and where the code fixes it."""
    from statistics import NormalDist

    lab = _lab()
    df = PROMOTION_WEEKS - 1
    crit = _t_critical(PROMOTION_ALPHA, df)
    normal = NormalDist().inv_cdf(1 - PROMOTION_ALPHA)
    rows = [
        ("Model of record", f"{_short_label(_champ(lab))}, model version `{PROMOTION_MODEL_VERSION}`",
         "`vol_record.PROMOTION_MODEL_VERSION`"),
        ("Challenger", f"{_short_label(_chall(lab))}, key `{PROMOTION_CHALLENGER}`", "`vol_record.PROMOTION_CHALLENGER`"),
        ("Loss", "QLIKE on variances: r − ln r − 1, with r the realized over the forecast variance",
         "`vol_record.qlike`"),
        ("Loss difference", "d = QLIKE(model of record) − QLIKE(challenger); positive favours the challenger",
         "`vol_record.promotion_test`"),
        ("Unit", f"one recorded forecast, scored on the realized volatility of the next {HORIZON_SESSIONS} "
                 f"settlement-to-settlement returns", "`vol_record.HORIZON_SESSIONS`"),
        ("Sample", f"the first {PROMOTION_WEEKS} resolved entries of the pair whose windows do not overlap, in "
                   f"record order", "`vol_record.PROMOTION_WEEKS`, `promotion_sample`"),
        ("Statistic", f"mean of d over its standard error (sample variance, n − 1 denominator, no autocovariance "
                      f"terms); one-sided p from Student's t with {df} degrees of freedom",
         "`vol_record.promotion_test`, `student_t_sf`"),
        ("Level", f"{PROMOTION_ALPHA:.2f}, one-sided: promote when the statistic exceeds t({df})'s upper "
                  f"{PROMOTION_ALPHA * 100:.0f}% point, {crit:.4f}", "`vol_record.PROMOTION_ALPHA`"),
        ("Looks", "one, when the sample is full; later entries never change the verdict", "`vol_record.promotion_sample`"),
    ]
    table = "| Element | Registered value | Fixed in |\n| --- | --- | --- |\n" + "\n".join(
        f"| {name} | {value} | {where} |" for name, value, where in rows)
    size = student_t_sf(normal, df)
    return (f"{table}\n\nThe Student t reference matters at this sample size: the standard normal's upper "
            f"{PROMOTION_ALPHA * 100:.0f}% point is {normal:.4f}, and rejecting above it would have a size of "
            f"{size * 100:.1f}% under t({df}), not {PROMOTION_ALPHA * 100:.0f}%.")


def prereg_window() -> str:
    """The scoring window as the record states it (vol_record.WINDOW_NOTE)."""
    return f"> {WINDOW_NOTE}"


def prereg_selection() -> str:
    """What the backtest said about the pair when the rule was fixed, and what it says now; neither enters the test."""
    lab, ledger = _lab(), _ledger()
    champ, chall = _champ(lab), _chall(lab)
    spa = lab["leaderboard"]["spa_qlike_vs_har_iv"]
    dm = chall["dm_qlike_vs_har_iv"]
    plain = spa["non_studentized"]["p_consistent"]
    now = spa["p_consistent"]
    same = round(plain, 3) == round(PREREG_PUBLISHED_SPA_P, 3)
    published = (f"When the rule was fixed it was published as p = {_fmt(PREREG_PUBLISHED_SPA_P)}; that value was the "
                 f"non-studentized test (the 2.2.0 errata in CHANGELOG.md)"
                 + ("" if same else f", which the current artifact gives as p = {_fmt(plain)}") + ".")
    return (f"The challenger was not chosen blind. On the historical sample it has the lowest QLIKE of the "
            f"{len(spa['models'])} alternatives to the model of record ({_fmt(chall['qlike'])} against "
            f"{_fmt(champ['qlike'])}; pairwise Diebold-Mariano {_peq(dm['p_value'])}), and it was picked for that "
            f"reason, so its pairwise p-value overstates the evidence. The test that accounts for the search is "
            f"Hansen's SPA over all {len(spa['models'])} alternatives. {published} "
            f"Studentized as Hansen defines it, the test gives {_peq(now)} ({_level(now)})"
            f"{_spa_adjusted(ledger) if _sig(now) else ''}. "
            f"None of these numbers enters the live test. They are why the challenger runs live instead of "
            f"replacing the model of record, and why only forecasts recorded after the rule was fixed decide.")


def prereg_duration() -> str:
    """When the sample can fill at the earliest under the record's own rules: one in-time, paired entry every
    session from the first one, each issued the morning after its session, windows across a roll never scored,
    and promotion_sample's greedy selection."""
    from datetime import date, datetime, time, timedelta, timezone

    from backend.contract_calendar import business_days_between, is_business_day, spans_roll
    from backend.vol_record import COMMIT_MARGIN, target_window

    day, chosen, last_end = date.fromisoformat(PREREG_FIRST_SESSION), [], None
    while len(chosen) < PROMOTION_WEEKS:
        if is_business_day(day):
            issued = datetime.combine(day + timedelta(days=1), time(5, 0), tzinfo=timezone.utc)
            window = target_window(issued + COMMIT_MARGIN)
            if not spans_roll(window["base_session"], window["target_end"]) and (
                    last_end is None or window["target_start"] > last_end):
                chosen.append(window)
                last_end = window["target_end"]
        day += timedelta(days=1)
    base, end = chosen[0]["base_session"], chosen[-1]["target_end"]
    sessions = business_days_between(base, end)
    months = (end - base).days / 30.44
    return (f"At the earliest, the sample is full when the window ending {end:%Y-%m-%d} resolves: "
            f"{sessions} sessions after the {base:%Y-%m-%d} settlement that opened the first window (about "
            f"{months:.0f} months), not {PROMOTION_WEEKS} × {HORIZON_SESSIONS} = {PROMOTION_WEEKS * HORIZON_SESSIONS}. "
            f"That is the case in which every session from the first entry on is recorded, sealed in time and "
            f"paired; windows that cross the monthly contract roll are never scored, so every roll costs the greedy "
            f"selection one to {_word(HORIZON_SESSIONS)} sessions. A missed, unpaired or expired session can delay it, "
            f"if it is one the selection would have taken; the next roll often absorbs a single such loss. The "
            f"record summary publishes `n_independent_paired` against `required` as the sample accrues.")


def prereg_dependence() -> str:
    """How far the backtest's weekly loss differences are from the serially uncorrelated weeks the statistic assumes."""
    from backend.vol_record import qlike

    lab = _lab()
    s = lab["series"]
    d = [qlike(a, m) - qlike(a, c) for a, m, c in zip(s["actual"], s["har_iv"], s["challenger"])
         if a and m and c]
    n = len(d)
    mean = sum(d) / n
    dev = [x - mean for x in d]
    gamma = [sum(dev[i] * dev[i - k] for i in range(k, n)) / n for k in range(n)]
    lags = int(4 * (n / 100) ** (2 / 9))
    long_run = gamma[0] + 2 * sum((1 - k / (lags + 1)) * gamma[k] for k in range(1, lags + 1))
    rho1, ratio = gamma[1] / gamma[0], long_run / gamma[0]
    correlated = abs(rho1) > 2 / math.sqrt(n)      # outside the white-noise band of the lag-1 autocorrelation
    figures = (f"On its weekly series ({n:,} non-overlapping weeks, {s['date'][0][:4]}–{s['date'][-1][:4]}, "
               f"forecasts rounded to 0.1 vol points), the same loss difference d has a lag-1 autocorrelation of "
               f"{_fmt(rho1, 2)}, and its Newey-West long-run variance ({lags} lags) is {_fmt(ratio, 2)} times the "
               f"plain variance.")
    if correlated and ratio > 1:
        reading = (f"That is an assumption, and the backtest does not fully support it. {figures} If live weeks "
                   f"behave alike, the plain standard error understates the uncertainty, and the test's actual size "
                   f"need not equal its nominal {PROMOTION_ALPHA * 100:.0f}%.")
    elif ratio > 1:
        reading = (f"That is an assumption. {figures} The lag-1 autocorrelation is within the range white noise "
                   f"gives on {n:,} weeks, so the backtest neither confirms nor clearly contradicts it.")
    else:
        reading = (f"That is an assumption. {figures} On that evidence the plain standard error does not understate "
                   f"the uncertainty, so the test is, if anything, conservative.")
    return (f"{reading} The rule was fixed with this statistic and is applied as registered; the backtest's own "
            f"Diebold-Mariano tests (sections 6 and 7) use Newey-West standard errors.")


def prereg_deviations() -> str:
    """Section 9: changes since registration, none to the test itself."""
    now = _lab()["leaderboard"]["spa_qlike_vs_har_iv"]["p_consistent"]
    later = "" if round(now, 3) == PREREG_STUDENTIZED_SPA_P else f"; the current artifact gives {_peq(now)}"
    return (f"None to the test. Since the rule was fixed, one backtest result it does not use was corrected: in "
            f"2.2.0 the SPA p-value was published studentized (p = {_fmt(PREREG_STUDENTIZED_SPA_P)}) in place of the "
            f"non-studentized {_fmt(PREREG_PUBLISHED_SPA_P)}{later} (section 7). The test's pair, sample, statistic "
            f"and level are unchanged.")


def print_block(name: str, **options: str) -> None:
    """Entry point for cog: `[[[cog import scripts.readme_blocks as b; b.print_block("leaderboard_table") ]]]`.

    `options` go to the block (a page in docs/ passes root="../" to a block that links into the repository).
    """
    import cog  # provided by cogapp at generation time

    cog.out("\n" + globals()[name](**options) + "\n")


def _implied_reading(lab: dict) -> tuple[str, bool]:
    """How the model of record compares with OVX alone and raw OVX, and whether neither differs from it."""
    implied = [_model(lab, "ovx_only"), _model(lab, "raw_ovx")]
    parts = [f"{'the ' if m['key'] == 'ovx_only' else ''}{m['label']} (DM {_peq(m['dm_qlike_vs_har_iv']['p_value'])})"
             for m in implied]
    tied = not any(_sig(m["dm_qlike_vs_har_iv"]["p_value"]) for m in implied)
    return _join(parts), tied


def non_claims(root: str = "") -> str:
    """The README's 'What it does not claim', with the data-dependent bullets worded from the artifacts."""
    lab, ledger = _lab(), _ledger()
    implied, tied = _implied_reading(lab)
    rng = _model(lab, "har_range")
    years = f"{lab['sample']['first'][:4]}–{lab['sample']['last'][:4]}"
    rdm = rng["dm_qlike_vs_har_iv"]
    if tied:
        ranges = (f"{rng['label']}, with no OVX, is statistically indistinguishable from it too "
                  f"(DM {_peq(rdm['p_value'])})" if not _sig(rdm["p_value"]) else
                  f"{rng['label']}, with no OVX, is significantly {'better' if rdm['stat'] > 0 else 'worse'} "
                  f"than it (DM {_peq(rdm['p_value'])})")
        edge = (f"- **No demonstrated edge over the options market.** The model is statistically indistinguishable, "
                f"in either direction, from {implied}; {ranges}.")
    else:
        models = [_model(lab, "ovx_only"), _model(lab, "raw_ovx")]

        def versus(m: dict) -> str:
            dm = m["dm_qlike_vs_har_iv"]            # stat > 0: the alternative has the lower loss
            how = ("statistically tied with" if not _sig(dm["p_value"]) else
                   "significantly worse than" if dm["stat"] > 0 else "significantly better than")
            return f"{how} {'the ' if m['key'] == 'ovx_only' else ''}{m['label']} (DM {_peq(dm['p_value'])})"

        beaten = any(_sig(m["dm_qlike_vs_har_iv"]["p_value"]) and m["dm_qlike_vs_har_iv"]["stat"] > 0 for m in models)
        head = "No edge over the options market." if beaten else "No edge over the options market beyond this sample."
        edge = (f"- **{head}** The model is {_join([versus(m) for m in models])}"
                + ("." if beaten else ", on the sample it was built on."))
    spa = lab["leaderboard"]["spa_qlike_vs_har_iv"]["p_consistent"]
    if _sig(spa):
        leader = _spa_leader(lab)
        named = ("; the challenger has the largest studentized gain" if leader and
                 leader["key"] == lab["config"]["challenger"] else "")
        promotion = (f"- **No promotion before the test.** After the search, Hansen's SPA test rejects the null that no "
                     f"alternative beats the model of record (p = {_fmt(spa)}{named}){_spa_adjusted(ledger)}. It was "
                     f"measured on the sample the field was built on; only the forward record can promote the "
                     f"challenger.")
    else:
        word = "inconclusive" if spa < 0.10 else "not significant"
        promotion = (f"- **No promotion before the test.** The challenger's historical edge is {word} after the "
                     f"search that found it (SPA p = {_fmt(spa)}), and only the forward record can settle it.")
    return "\n".join([
        "- **No price-direction or trading signal.** The direction model is retracted; volatility-direction "
        "accuracy is reported only beside its noise floor.",
        edge,
        promotion,
        "- **Daily data only.** Five daily returns make a noisy volatility proxy; OVX is computed from USO options, "
        f"not CL options; the sample and the field were chosen while looking at {years}. All limitations are in "
        f"[section 9 of the full results]({root}docs/RESULTS.md#9-limitations).",
        "- **Not investment advice.**",
    ])


def model_card_caveats() -> str:
    """The model card's data-dependent caveats: where the skill comes from, and the 99% VaR tail."""
    lab = _lab()
    implied, tied = _implied_reading(lab)
    coef = key_numbers()["ovx_coef"]
    rng = _model(lab, "har_range")
    if tied:
        rdm = rng["dm_qlike_vs_har_iv"]
        ranges = (f"{rng['label']}, from daily price ranges without OVX, is also statistically indistinguishable "
                  f"from it (DM {_peq(rdm['p_value'])}), so these data do not show that OVX is needed to reach its loss"
                  if not _sig(rdm["p_value"]) else
                  f"{rng['label']}, from daily price ranges without OVX, differs from it significantly "
                  f"(DM {_peq(rdm['p_value'])})")
        skill = (f"- The model is statistically indistinguishable from implied volatility alone: {implied}, with an "
                 f"OVX elasticity of {_fmt(coef['coef'], 2)} (HAC t {_fmt(coef['t_hac'], 1)}). {ranges}.")
    else:
        skill = (f"- Against implied volatility alone: {implied}; the OVX elasticity is {_fmt(coef['coef'], 2)} "
                 f"(HAC t {_fmt(coef['t_hac'], 1)}).")
    champion, grids = lab["config"]["champion"], _grids(lab)
    fhs, normal = _var(lab, f"{champion}:fhs", "0.01"), _var(lab, f"{champion}:normal", "0.01")
    counts = fhs["exceedances_by_grid"]
    above = sum(c > fhs["expected"] for c in counts)
    row = _ledger_row(_ledger(), "var_fhs_99_coverage")
    rejects = row["status"] == "supported"
    over = row["statistic"]["value"] > fhs["expected"]           # the production grid's breach count
    head = (("The 1% tail is understated." if over else "The 1% tail is overstated.") if rejects else
            "The 1% tail may be understated." if above == grids else
            "The 1% tail may be overstated." if above == 0 else "The 99% breach count is near nominal.")
    tail = (f"- **{head}** The weekly 99% Value-at-Risk (filtered historical simulation) "
            f"breaches {min(counts)} to {max(counts)} times per sampling grid against {fhs['expected']} expected, "
            f"above the expected count on {above} of {grids} grids; the grids are offsets of the same days, not "
            f"independent evidence. Kupiec's coverage test passes on {fhs['kupiec_pass_grids']}/{grids} (each grid "
            f"alone has little power at 1%; production grid {_peq(row['p_value'])}); with a normal quantile it passes "
            f"on {normal['kupiec_pass_grids']}/{grids}. Use the filtered version, and size a 99% limit with that in "
            f"mind ([full results, section 2.4](RESULTS.md#24-use-as-a-risk-input)).")
    return f"{skill}\n{tail}"


def risk_limitation() -> str:
    """The limitations bullet on risk use: how much of it any volatility scaling gives."""
    lab, k = _lab(), key_numbers()
    champion, grids = lab["config"]["champion"], _grids(lab)
    tests = ("kupiec_pass_grids", "independence_pass_grids")
    levels = ("0.05", "0.01")
    model = {lv: _var(lab, f"{champion}:fhs", lv) for lv in levels}
    raw = {lv: _var(lab, "raw_ovx:fhs", lv) for lv in levels}
    raw_as_good = all(raw[lv][t] >= model[lv][t] for lv in levels for t in tests)
    ind = "independence_pass_grids"
    passes = (f"breach independence on {raw['0.05'][ind]}/{grids} grids at 95% and {raw['0.01'][ind]}/{grids} at 99%, "
              f"against {model['0.05'][ind]}/{grids} and {model['0.01'][ind]}/{grids}")
    head = ("Risk use is mostly volatility scaling." if raw_as_good else
            "Risk use is partly volatility scaling.")
    lead = ("raw OVX, with nothing fitted, passes the VaR tests on as many grids as the model of record"
            if raw_as_good else "raw OVX, with nothing fitted, passes the VaR tests on fewer grids than the model of record")
    counts = model["0.01"]["exceedances_by_grid"]
    above = sum(c > model["0.01"]["expected"] for c in counts)
    kupiec = model["0.01"]["kupiec_pass_grids"]
    if above:
        tail = (f"At 99% the filtered VaR breaches more often than expected on {above} of {grids} grids, though "
                f"Kupiec's test passes on {kupiec}/{grids}" + (", so its 1% tail may be understated." if above == grids else "."))
    else:
        tail = f"At 99% the filtered VaR breaches no more often than expected on any grid; Kupiec's test passes on {kupiec}/{grids}."
    return (f"- **{head}** The VaR and vol-target results show what scaling by a volatility estimate does: {lead} "
            f"({passes}); sizing on trailing realized volatility, with no forecast, gives a realized volatility of "
            f"{k['tr_realized_vol']}% against {k['fc_realized_vol']}% on the forecast for a {k['target_vol']:g}% target; "
            f"and only the overlay's Sharpe differences are tested between methods (section 2.4). {tail}")


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
    grids, interval = _grids(lab), _interval_level(lab)
    version = _code_constant("backend/vol_forecast.py", "MODEL_VERSION")
    raw = _model(lab, "raw_ovx")

    def in_set(m: dict) -> str:
        return (f"{'yes' if m['in_mcs_qlike'] else 'no'}, MCS p {_fmt(m['mcs_qlike_p'], 3)} "
                f"({m['in_mcs_qlike_grids']}/{grids} grids)")

    cdm = chall["dm_qlike_vs_har_iv"]
    odm = ovx_only["dm_qlike_vs_har_iv"]
    rows = [f"| Metric | Model of record (`{version}`) | Challenger ({chall['label']}) | Reference |",
            "|---|---:|---:|---|"]
    rows.append(f"| QLIKE, every OOS day (lower is better) | {_fmt(champ['qlike'])} | {_fmt(chall['qlike'])} | "
                f"persistence {_fmt(_model(lab, 'persistence')['qlike'])}; {ovx_only['label']} "
                f"{_fmt(ovx_only['qlike'])}; {raw['label']} {_fmt(raw['qlike'])} |")
    rows.append(f"| Diebold-Mariano on QLIKE vs the model of record (pairwise) | — | {_fmt(cdm['stat'], 2, True)} "
                f"({_peq(cdm['p_value'])}; before adjusting for the search that chose it) | {ovx_only['label']} "
                f"{_fmt(odm['stat'], 2, True)} ({_peq(odm['p_value'])}) |")
    rows.append(f"| In the {_mcs_level(lab)} Model Confidence Set (full sample; sampling grids) | {in_set(champ)} | "
                f"{in_set(chall)} | {len(board['mcs_qlike_included'])} of {len(feasible)} feasible models"
                + ("; the timing control is excluded" if controls else "") + " |")
    spa_row = _ledger_row(_ledger(), "vol_spa_beats_record")
    bh = (f", {'and' if spa_row['survives_bh_5pct'] else 'not'} after the ledger's Benjamini-Hochberg adjustment "
          f"(q = {_fmt(spa_row['q_value_bh'])})" if _sig(k["spa_p"]) else "")
    rows.append(f"| Hansen SPA, null: no model beats the model of record | p = {_fmt(k['spa_p'])} | — | "
                f"{len(board['spa_qlike_vs_har_iv']['models'])} feasible alternatives; {_level(k['spa_p'])}{bh} |")
    rows.append(f"| Mincer-Zarnowitz slope (joint unbiasedness p) | {_fmt(champ['mincer_zarnowitz']['slope'], 2)} "
                f"({_p(champ['mincer_zarnowitz']['p_unbiased'])}) | {_fmt(chall['mincer_zarnowitz']['slope'], 2)} "
                f"({_p(chall['mincer_zarnowitz']['p_unbiased'])}) | 1 (unbiased) |")
    rows.append(f"| {interval} conformal interval coverage | {conf[lab['config']['champion']]['coverage_pct']}% | "
                f"{conf[lab['config']['challenger']]['coverage_pct']}% | target {interval} |")
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


def _cl_second_source_lines(lab: dict) -> list[str]:
    """CL=F against EIA's contract 1 and Cushing spot, and the roll audit (ROADMAP 1.7), from the artifact."""
    c = lab["cl_source_agreement"]["contract1"]
    tick = lab["cl_source_agreement"]["tolerance_usd"]
    since = c["since_sample_first"]
    spot = lab["cl_source_agreement"]["spot"]
    audit = lab["roll_convention_audit"]
    years = ", ".join(f"{year}: {n}" for year, n in c["misses_by_year"].items())
    worst = c["largest_gaps"][0]
    early = "; ".join(f"{m['contract']} from {m['yahoo_switch']}, {-m['offset_sessions']} session"
                      f"{'s' if -m['offset_sessions'] != 1 else ''} before the lab's roll day"
                      for m in audit["mismatches"]) or "none"
    missing = ", ".join(c["eia_sessions_missing_from_cl"]) or "none"
    dropped = ", ".join(c["nonpositive_sessions_dropped"]) or "none"
    return [
        f"- `CL=F` vs EIA's contract-1 settlement (RCLC1, the front month, final through {c['last']}): "
        f"{c['common_days']:,} common sessions from {c['first']}, {c['share_same_cent'] * 100:.2f}% equal to the "
        f"cent and {c['share_within_one_tick'] * 100:.2f}% within one tick (${tick:.2f}); {c['misses']} sessions "
        f"further apart ({years}), the largest {worst['date']} (Yahoo {worst['yahoo']}, EIA {worst['c1']}). In "
        f"the scored sample's sessions EIA covers ({since['first']} to {c['last']}): {since['misses']} of "
        f"{since['common_days']:,} further apart than a tick ({', '.join(since['miss_dates']) or 'none'}). "
        f"Sessions EIA has that the snapshot's `CL=F` lacks: {missing} (gaps in Yahoo's series), and "
        f"{dropped} (a non-positive settlement, dropped by rule).",
        f"- `CL=F` vs EIA's Cushing spot (RWTC, a physical price, so a loose check that cannot tell contracts "
        f"apart), {spot['first']} to {spot['last']}: {spot['common_days']:,} common days, median absolute log "
        f"gap {spot['median_abs_log_gap']}, 99th percentile {spot['p99_abs_log_gap']}, largest "
        f"{spot['max_abs_log_gap']} ({spot['max_gap_date']}). Later sessions get a report-only check at each "
        "freeze against a fresh spot download, for gross gaps only (`vol_forecast.source_check`).",
        f"- Contract rolls: of the {audit['contracts_checked']} contracts that expire while both `CL=F` and "
        f"EIA's contracts 1 and 2 have data ({c['first']} to {audit['eia_through']}), `CL=F` equals EIA's "
        f"contract 1 on the lab's last session before the roll and on its roll day for "
        f"{audit['on_lab_roll_day']}; it followed the next contract before the roll for: {early}; "
        f"{len(audit['indeterminate'])} cannot be told (EIA lacks a row, the two contracts are within a "
        f"tick, or `CL=F` matches neither). The {audit['roll_days_after_eia']} roll days after "
        f"{audit['eia_through']} are not checked. This shows Yahoo rolling with EIA's contract 1, not "
        "that the lab's roll calendar is right: that comes from `contract_calendar.last_trade_date`, "
        "tested against known expiry dates.",
    ]


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
        *_cl_second_source_lines(lab),
        f"- Snapshot retrieved {lab['generated_from']['retrieved_at']}, finished sessions through "
        f"{lab['generated_from']['sessions_through']}.",
    ])
