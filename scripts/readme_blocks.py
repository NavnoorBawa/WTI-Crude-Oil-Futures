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


def _peq(value) -> str:
    """'p = 0.123' or 'p < 0.001'."""
    text = _p(value)
    return f"p {text}" if text.startswith("<") else f"p = {text}"


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


def _worse_than_record(lab: dict, alpha: float = 0.05) -> list[dict]:
    """Models whose QLIKE is significantly above the model of record's (DM, two-sided p < alpha)."""
    return [m for m in lab["leaderboard"]["models"]
            if m["dm_qlike_vs_har_iv"] and m["dm_qlike_vs_har_iv"]["stat"] < 0
            and m["dm_qlike_vs_har_iv"]["p_value"] < alpha]


def _names(models: list[dict]) -> str:
    labels = [m["label"] for m in models]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def abstract() -> str:
    lab, ledger = _lab(), _load("research_ledger.json")
    k = key_numbers()
    leak = _load("leak_anatomy.json")["synthetic_random_walk"]["random_forest"]
    pm = _load("wf_5y_purged.json")["results"]["1w"]["pnl_metrics"]["sharpe_ratio_annualized"]
    lk = _load("wf_5y_unpurged_LEAKED.json")["results"]["1w"]["pnl_metrics"]["sharpe_ratio_annualized"]
    worse = [m for m in _worse_than_record(lab) if m["key"] != "persistence"]
    ovx_only = _model(lab, "ovx_only")
    var99 = lab["var_backtest"]["summary"][f"{lab['config']['champion']}:fhs@0.01"]
    summary = ledger["summary"]
    return (
        f"This project forecasts the realized volatility of front-month NYMEX WTI crude oil over the next five "
        f"sessions from free data, and keeps a public, git-timestamped record of its live forecasts. Over "
        f"{k['n_days']:,} out-of-sample days ({k['first'][:4]}–{k['last'][:4]}), a heterogeneous autoregressive "
        f"model with lagged implied volatility (HAR-IV) has a QLIKE loss of {_fmt(k['champ_qlike'])}, against "
        f"{_fmt(k['persistence_qlike'])} for a random-walk forecast. It is significantly better than "
        f"{_names(worse)} (Diebold-Mariano p < 0.05 each). Most of that skill is the options market's: the "
        f"elasticity on OVX is {_fmt(k['ovx_coef']['coef'], 2)}, and a regression on OVX alone "
        f"({_fmt(ovx_only['qlike'])}) is statistically tied with the full model. A range-based variant has the "
        f"lowest loss of the {k['n_models']} models ({_fmt(k['chall_qlike'])}, DM {_peq(k['chall_dm_p'])}) "
        f"and stays in the 90% Model Confidence Set on all {k['chall_grids']} weekday sampling grids. Hansen's "
        f"SPA test gives p = {_fmt(k['spa_p'])} for any model in the field beating the model of record, so the "
        f"variant runs live as a challenger under a promotion rule fixed in advance. The forecast's 80% "
        f"adaptive conformal intervals cover {k['coverage']}% of outcomes. Used to size a "
        f"{k['target_vol']:g}% volatility target, it keeps realized volatility at {k['fc_realized_vol']}% and cuts "
        f"excess kurtosis from {k['bh_kurt']} to {k['fc_kurt']}. Its weekly 99% VaR breaches "
        f"{var99['exceedances_by_grid'][0]} times against {var99['expected']} expected. It does not raise the "
        f"Sharpe ratio. Next-week direction accuracy ({k['dir_acc']}%) is not evidence of skill: the same "
        f"pipeline scores {k['noise_floor']}% on simulated returns with nothing to forecast.\n\n"
        f"The project began as a machine-learning direction model whose backtest Sharpe ratio of "
        f"{_fmt(lk, 2)} was a look-ahead leak; purged, it is {_fmt(pm, 2)}. The same leak manufactures a "
        f"Sharpe ratio of {_fmt(leak['unpurged_leaked']['sharpe_mean'], 2)} from a pure random walk. All "
        f"{summary['rows']} hypotheses the project has tested are listed in a research ledger with "
        f"multiplicity adjustments; {len(summary['supported_surviving_bh_5pct'])} of the "
        f"{summary['by_status']['supported']} supported ones survive Benjamini-Hochberg at 5%."
    )


def results_at_a_glance() -> str:
    lab, prod = _lab(), _prod()
    k = key_numbers()
    champ = _model(lab, lab["config"]["champion"])
    raw = _model(lab, "raw_ovx")
    var99 = lab["var_backtest"]["summary"][f"{lab['config']['champion']}:fhs@0.01"]
    hs99 = lab["var_backtest"]["summary"]["historical_simulation@0.01"]
    tests = prod["economic"]["vol_target_overlay"]["sharpe_difference_tests"]["forecast_minus_buy_hold"]
    vrp = prod["economic"]["variance_risk_premium"]
    ci = vrp["mean_premium_variance_ci95"]
    leak = _load("leak_anatomy.json")
    rows = ["| Question | Answer | Evidence |", "|---|---|---|"]
    rows.append(f"| Is next-week WTI volatility forecastable from free data? | Yes | QLIKE {_fmt(k['champ_qlike'])} vs "
                f"{_fmt(k['persistence_qlike'])} for persistence; {len(_worse_than_record(lab))} of "
                f"{k['n_models'] - 1} benchmarks significantly worse |")
    rows.append(f"| Does it add to implied volatility? | Not detectably | Raw OVX {_fmt(raw['qlike'])} "
                f"(DM {_peq(raw['dm_qlike_vs_har_iv']['p_value'])}); OVX elasticity "
                f"{_fmt(k['ovx_coef']['coef'], 2)} (HAC t {_fmt(k['ovx_coef']['t_hac'], 1)}) |")
    rows.append(f"| Is anything better? | Possibly: a range-based variant | QLIKE {_fmt(k['chall_qlike'])}, DM "
                f"{_peq(k['chall_dm_p'])}, but SPA p = {_fmt(k['spa_p'])} after the search; challenger on the live record |")
    rows.append(f"| Are the forecasts calibrated? | Close | Mincer-Zarnowitz slope {_fmt(champ['mincer_zarnowitz']['slope'], 2)} "
                f"(joint p = {_p(champ['mincer_zarnowitz']['p_unbiased'])}); 80% intervals cover {k['coverage']}% |")
    rows.append(f"| Does it make a better weekly VaR? | Yes: breaches stop clustering | 99% VaR breaches are independent on "
                f"{var99['independence_pass_grids']}/5 weekday grids vs {hs99['independence_pass_grids']}/5 for historical "
                f"simulation; {var99['exceedances_by_grid'][0]} breaches vs {var99['expected']} expected (Kupiec passes "
                f"{var99['kupiec_pass_grids']}/5) |")
    rows.append(f"| Does it make money as a vol target? | No, it controls risk | Sharpe difference vs buy-and-hold "
                f"{_fmt(tests['difference'], 2, True)} (p = {_p(tests['p_value'])}); excess kurtosis "
                f"{k['fc_kurt']} vs {k['bh_kurt']} |")
    rows.append(f"| Is there a variance risk premium? | In vol points, yes; in variance, not significant | "
                f"IV above next-month RV in {vrp['share_positive_pct']}% of periods; mean variance premium "
                f"{_fmt(vrp['mean_premium_variance_pts'], 0)} (95% CI {_fmt(ci[0], 0)} to {_fmt(ci[1], 0)}) |")
    rows.append(f"| Is direction accuracy evidence of skill? | No | {k['dir_acc']}% observed vs {k['noise_floor']}% "
                f"with nothing to forecast |")
    rows.append(f"| Was the original direction model's Sharpe real? | No, a look-ahead leak | Leaked label agrees in "
                f"sign with the test label {leak['analytic']['p_sign_agree_leaked_pct']}% of the time on a "
                f"random walk |")
    return "\n".join(rows)


def coefficients_table() -> str:
    c = _lab()["har_iv_coefficients"]
    rows = ["| Term | Coefficient | HAC t |", "|---|---:|---:|"]
    for t in c["terms"]:
        rows.append(f"| {t['term']} | {_fmt(t['coef'], 3, True)} | {_fmt(t['t_hac'], 2, True)} |")
    rows.append("")
    rows.append(f"Full-sample fit of the model of record: {c['n_rows']:,} daily rows, R² {c['r2_log']} on log "
                f"realized vol, Newey-West t with {c['hac_lags']} lags (the five-session targets overlap).")
    return "\n".join(rows)


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
    rows.append(f"| HAR-IV, production weekly grid ({o['n']} weeks) | {_fmt(mz['intercept'], 3, True)} | "
                f"{_fmt(mz['slope'], 2)} ({_fmt(mz['slope_se'], 2)}) | {_p(mz['p_unbiased'])} | "
                f"{_fmt(o['har_mean_ratio_actual_to_forecast'], 3)} | — | — | — |")
    rows.append("")
    rows.append(f"Mincer-Zarnowitz regression of realized on forecast variance with a HAC covariance. Intervals: "
                f"{lab['config']['conformal']['method']}, step {lab['config']['conformal']['gamma']}, scored only "
                f"after each target has settled; interval score of Gneiting and Raftery (2007), in vol points "
                f"(lower is better).")
    return "\n".join(rows)


def vrp_block() -> str:
    vrp = _prod()["economic"]["variance_risk_premium"]
    t = vrp["timing_regression"]
    ci = vrp["mean_premium_variance_ci95"]
    worst = ", ".join(f"{w['start']} ({_fmt(w['premium_vol_pts'], 1)} vol pts)" for w in vrp["worst_periods"])
    return "\n".join([
        f"- **In vol points the premium is there.** OVX exceeded the realized volatility of the following 20 "
        f"sessions in {vrp['share_positive_pct']}% of {vrp['n_periods']} non-overlapping periods, by "
        f"{vrp['mean_premium_vol_pts']} points on average.",
        f"- **In variance units, the units an option seller is paid in, it is not significant.** The mean "
        f"premium is {_fmt(vrp['mean_premium_variance_pts'], 1)} variance points (95% Newey-West CI "
        f"{_fmt(ci[0], 0)} to {_fmt(ci[1], 0)}): the few periods where realized volatility explodes "
        f"({worst}) "
        f"outweigh the many small gains.",
        f"- **Harvesting it.** A short vol-swap proxy has a Sharpe ratio of {vrp['short_vol_swap_sharpe']}; "
        f"the convex short-variance position {vrp['short_variance_sharpe']} "
        f"({vrp['short_variance_sharpe_ex_worst_year']} without {vrp['worst_year']}).",
        f"- **Timing it with the forecast does not help.** Regressing short-variance P&L on OVX minus the "
        f"forecast: slope {_fmt(t['slope'], 2)}, HAC t {t['t_hac']}, {_peq(t['p_value'])} (n = {t['n']}).",
    ])


def noise_block() -> str:
    lab, prod = _lab(), _prod()
    nf = lab["noise_floor"]
    o = prod["overall"]
    board = lab["leaderboard"]
    return "\n".join([
        "| Pipeline run on | Direction accuracy | Mean-reversion rule |", "|---|---:|---:|",
        f"| WTI, production weekly grid ({o['n']} weeks) | {o['har_dir_acc_pct']}% | {o['mean_reversion_dir_acc_pct']}% |",
        f"| WTI, every out-of-sample day | {_fmt(_model(lab, lab['config']['champion'])['direction_acc_pct'], 1)}% | "
        f"{board['mean_reversion_acc_pct']}% |",
        f"| {nf['process']}, {nf['sims']} simulations | {nf['har_direction_acc_pct_mean']}% "
        f"({nf['har_direction_acc_pct_range'][0]}–{nf['har_direction_acc_pct_range'][1]}) | "
        f"{nf['mean_reversion_acc_pct_mean']}% |",
        "",
        f"{nf['note']} The call compares next week's realized volatility with this week's, and a five-session "
        f"realized volatility is a noisy estimate that reverts toward its mean whether or not the underlying "
        f"volatility moves. Accuracy against that comparison measures the noise in the proxy. The losses above "
        f"measure forecasting skill.",
    ])


def robustness_block() -> str:
    lab, prod = _lab(), _prod()
    o = prod["overall"]
    champ = _model(lab, lab["config"]["champion"])
    chall = _model(lab, lab["config"]["challenger"])
    same = _model(lab, "har_iv_sameday")
    sel = lab["oos_selection"]
    ret = prod["economic"]["returns"]
    qby = champ["qlike_by_grid"]
    picks = sorted(sel["pick_share_pct"].items(), key=lambda kv: -kv[1])[:3]
    kept = "; ".join(_model(lab, key)["label"] for key in lab["leaderboard"]["mcs_qlike_range_proxy_included"])
    chosen = ", ".join(f"{_model(lab, key)['label']} {share}%" for key, share in picks)
    return "\n".join([
        f"- **Weekday grids.** The model of record's QLIKE on the five weekly grids ranges "
        f"{_fmt(min(qby))} to {_fmt(max(qby))}; the challenger is in the 90% MCS on "
        f"{chall['in_mcs_qlike_grids']}/5 and the model of record on {champ['in_mcs_qlike_grids']}/5. A "
        f"conclusion that holds on one grid and not the next is reported as fragile.",
        f"- **Implied-vol timing.** OVX settles at 16:15 ET, after the 14:30 CL settlement that opens the "
        f"target window, so the model uses the previous session's OVX. Using the same day's value lowers QLIKE "
        f"to {_fmt(same['qlike'])} (DM {_fmt(same['dm_qlike_vs_har_iv']['stat'], 2, True)}, "
        f"{_peq(same['dm_qlike_vs_har_iv']['p_value'])}): the extra 1h45m of information is worth having, and is "
        f"not available at the decision time.",
        f"- **Roll gaps.** `CL=F` books each roll's calendar spread as a return. With the "
        f"{ret['roll_days_rebuilt']} roll days through {ret['sample_end'][:4]} rebuilt as same-contract returns "
        f"from EIA's curve, the production model's QLIKE is {ret['har_qlike_on_same_contract_returns']} over the "
        f"same {ret['n_on_same_contract_returns']} weeks ({o['har_qlike']} as published).",
        f"- **Second volatility proxy.** On a range-based realized variance (Rogers-Satchell plus the "
        f"overnight return) the model of record's QLIKE is {_fmt(champ['qlike_range_proxy'])}; the MCS on "
        f"that proxy keeps {kept}.",
        f"- **Choosing the model out of sample.** Picking, each day, the model with the lowest trailing "
        f"{lab['config']['selection_window_weeks']}-week loss scores {_fmt(sel['qlike'])} vs "
        f"{_fmt(sel['qlike_har_iv'])} for always using the model of record (DM "
        f"{_peq(sel['dm_vs_har_iv']['p_value'])}); it picks "
        f"{chosen} of the time.",
    ])


def leak_table() -> str:
    a = _load("leak_anatomy.json")
    names = {"nearest_neighbour": "1-nearest neighbour", "decision_tree": "Decision tree",
             "random_forest": "Random forest (20 trees)"}
    data = {"synthetic_random_walk": "i.i.d. random walk", "block_shuffled_cl": "Block-shuffled CL=F returns",
            "real_cl_5y_window": "Real CL=F, 5-year window"}
    rows = ["| Data | Model | Runs | Accuracy unpurged → purged | Sharpe unpurged → purged |",
            "|---|---|---:|---|---|"]
    for dkey, dname in data.items():
        for mkey, block in a[dkey].items():
            if not (isinstance(block, dict) and "purged" in block):
                continue
            u, p = block["unpurged_leaked"], block["purged"]
            rows.append(f"| {dname} | {names.get(mkey, mkey)} | {u['n_runs']} | {u['accuracy_pct_mean']:.1f}% → "
                        f"**{p['accuracy_pct_mean']:.1f}%** | {_fmt(u['sharpe_mean'], 2)} → **{_fmt(p['sharpe_mean'], 2)}** |")
    ref = a["retracted_reference"]
    rows.append("")
    rows.append(f"For comparison, the retracted model: {ref['accuracy_pct']:.1f}% and Sharpe {ref['sharpe']:.2f} over "
                f"{ref['n']} weeks. Same walk-forward, same five-session labels, the purge switched off and on; "
                f"`backend/leak_anatomy.py`.")
    return "\n".join(rows)


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
                f"return). After 2007 the spread test could detect only a spread of {pw['mde_spread_pct_per_month']}%/month "
                f"(80% power), and had {round(pw['power_vs_pre_2007_spread'] * 100)}% power against the pre-2007 "
                f"spread; the change in spread is not significant (p = {_fmt(rc['spread_difference_p_value'])}), the "
                f"change in rank correlation is (p = {_fmt(rc['rank_corr_difference_p_value'])}).")
    return "\n".join(rows)


def supply_table() -> str:
    s = _load("supply_shock_study.json")
    pl = s["placebo"]
    rows = ["| Group | n | Median peak | Median settle | p vs placebo (peak / settle) |", "|---|---:|---:|---:|---|"]
    rows.append(f"| Ordinary dates (placebo) | {pl['n']:,} | {_fmt(pl['median_peak_pct'], 1, True)}% | "
                f"{_fmt(pl['median_settle_pct'], 1, True)}% | — |")
    for g in s["groups"]:
        pv = g["p_value_vs_placebo"]
        rows.append(f"| {g['name']} | {g['n']} | {_fmt(g['median_peak_pct'], 1, True)}% | "
                    f"{_fmt(g['median_settle_pct'], 1, True)}% | {_p(pv['peak'])} / {_p(pv['settle'])} |")
    bg = s["between_groups"]
    rows.append("")
    rows.append(f"Peak = highest close in the 20 sessions after the event vs the day before; settle = 10 sessions "
                f"after the peak; EIA daily Cushing spot. Supply removal vs threat-only directly: p = "
                f"{_fmt(bg['p_value']['peak'])} (peak), {_fmt(bg['p_value']['settle'])} (settle). Event dates and "
                f"groups were chosen with hindsight and the p-values are not adjusted for the groups tested.")
    return "\n".join(rows)


def ledger_block() -> str:
    led = _load("research_ledger.json")
    s = led["summary"]
    rows_by_id = {r["id"]: r for r in led["rows"]}
    out = [f"{s['rows']} hypotheses: {s['by_status']['supported']} supported, {s['by_status']['rejected']} rejected, "
           f"{s['by_status']['inconclusive']} inconclusive, {s['by_status']['retracted']} retracted. Of the "
           f"supported ones, {len(s['supported_surviving_bh_5pct'])} survive Benjamini-Hochberg at 5%, "
           f"{len(s['supported_surviving_holm_5pct'])} survive Holm, and {len(s['supported_clearing_hlz_t3'])} clear "
           f"the t > 3 hurdle of Harvey, Liu and Zhu (2016):", "",
           "| Claim | p | BH q | Clears t > 3 |", "|---|---:|---:|:---:|"]
    for key in s["supported_surviving_bh_5pct"]:
        r = rows_by_id[key]
        out.append(f"| {r['hypothesis']} | {_p(r['p_value'])} | {_p(r.get('q_value_bh'))} | "
                   f"{'yes' if r.get('clears_hlz_t3') else 'no'} |")
    return "\n".join(out)


def design_block() -> str:
    lab = _lab()
    c, smp = lab["config"], lab["sample"]
    mcs = c["mcs"]
    return "\n".join([
        f"- **Target.** Annualized realized volatility of the next {c['horizon_sessions']} settlement-to-settlement "
        f"log returns of `CL=F`, the front-month NYMEX WTI future.",
        f"- **Decision time.** The 14:30 ET settlement that opens the target window. Every input is published by "
        f"then; OVX, which settles at 16:15 ET, enters with a {c['ovx_lag_sessions']}-session lag.",
        f"- **Walk-forward.** Expanding window from the first {c['min_train_rows']} rows, refit every "
        f"{c['step']} sessions. The last {c['purge_rows']} training rows are purged at every step, because their "
        f"targets overlap the test target and are not known at the decision.",
        f"- **Sample.** {smp['n_days']:,} out-of-sample days, {smp['first']} to {smp['last']}. Losses and "
        f"tests use every day. Each of the five weekday sampling grids ({smp['n_weeks']} non-overlapping weeks) "
        f"is also scored on its own, and VaR is scored only there, since it needs independent outcomes.",
        f"- **Forecasts.** Losses, VaR and risk use the variance-consistent forecast (the median times a Duan "
        f"smearing factor). The direction call and the intervals use the median.",
        f"- **Inference.** Model Confidence Set at {100 - mcs['size'] * 100:g}% ({mcs['statistic']} statistic, "
        f"{mcs['reps']:,} {mcs['bootstrap']}-bootstrap replications, blocks of {mcs['block_daily']} days or "
        f"{mcs['block_weekly']} weeks, seed {mcs['seed']}); Diebold-Mariano with Newey-West variance and the "
        f"Harvey-Leybourne-Newbold correction; Hansen's SPA across the whole field.",
    ])


def promotion_rule() -> str:
    from backend.vol_record import PROMOTION_RULE

    return f"> {PROMOTION_RULE}"


def promotion_power(min_weeks: int = 52, alpha: float = 0.10) -> str:
    """Power of the promotion rule if the challenger's historical edge is its true edge.

    The per-grid Diebold-Mariano statistics are on ~n_weeks non-overlapping weeks, so the expected
    statistic after w independent weeks is about stat * sqrt(w / n_weeks)."""
    from statistics import NormalDist, median

    from backend.vol_record import PROMOTION_RULE

    if str(min_weeks) not in PROMOTION_RULE or f"{alpha:.2f}" not in PROMOTION_RULE:
        raise ValueError("promotion_power's constants no longer match vol_record.PROMOTION_RULE")
    lab = _lab()
    chall = _model(lab, lab["config"]["challenger"])
    n_weeks = lab["sample"]["n_weeks"]
    stat = median(chall["dm_qlike_vs_har_iv_by_grid"])
    z = NormalDist()
    crit = z.inv_cdf(1 - alpha)
    power = 1 - z.cdf(crit - stat * (min_weeks / n_weeks) ** 0.5)
    weeks_80 = n_weeks * ((crit + z.inv_cdf(0.80)) / stat) ** 2
    return (f"How long the forward record needs: on the weekly grids the challenger's historical edge is a "
            f"Diebold-Mariano statistic of about {stat:.1f} over {n_weeks} weeks. If that is its true edge, "
            f"{min_weeks} independent weeks give the rule a {power * 100:.0f}% chance of promoting it, and 80% "
            f"power takes about {weeks_80:,.0f} weeks ({weeks_80 / 52:.0f} years). The rule is built not to "
            f"promote on luck. A challenger still unpromoted after a year has not been shown to be worse.")


def print_block(name: str) -> None:
    """Entry point for cog: `[[[cog import scripts.readme_blocks as b; b.print_block("leaderboard_table") ]]]`."""
    import cog  # provided by cogapp at generation time

    cog.out("\n" + globals()[name]() + "\n")


def model_card_metrics() -> str:
    """Evaluation summary of the model of record and the challenger (model card, section 'Metrics')."""
    lab, prod = _lab(), _prod()
    o = prod["overall"]
    champ, chall = _model(lab, lab["config"]["champion"]), _model(lab, lab["config"]["challenger"])
    conf = lab["conformal"]
    rows = ["| Metric | Model of record (HAR-IV v2) | Challenger (" + chall["label"] + ") | Reference |",
            "|---|---:|---:|---|"]
    rows.append(f"| QLIKE, every OOS day (lower is better) | {_fmt(champ['qlike'])} | {_fmt(chall['qlike'])} | "
                f"persistence {_fmt(_model(lab, 'persistence')['qlike'])}; raw OVX {_fmt(_model(lab, 'raw_ovx')['qlike'])} |")
    rows.append(f"| In the 90% Model Confidence Set | {'yes' if champ['in_mcs_qlike'] else 'no'} "
                f"({champ['in_mcs_qlike_grids']}/5 weekday grids) | {'yes' if chall['in_mcs_qlike'] else 'no'} "
                f"({chall['in_mcs_qlike_grids']}/5) | {len(lab['leaderboard']['mcs_qlike_included'])} of "
                f"{len(lab['leaderboard']['models'])} models survive |")
    rows.append(f"| Mincer-Zarnowitz slope (joint unbiasedness p) | {_fmt(champ['mincer_zarnowitz']['slope'], 2)} "
                f"({_p(champ['mincer_zarnowitz']['p_unbiased'])}) | {_fmt(chall['mincer_zarnowitz']['slope'], 2)} "
                f"({_p(chall['mincer_zarnowitz']['p_unbiased'])}) | 1 (unbiased) |")
    rows.append(f"| 80% conformal interval coverage | {conf[lab['config']['champion']]['coverage_pct']}% | "
                f"{conf[lab['config']['challenger']]['coverage_pct']}% | target 80% |")
    rows.append(f"| Realized utility (% per year) | {_fmt(champ['realized_utility_pct'], 2)} | "
                f"{_fmt(chall['realized_utility_pct'], 2)} | maximum {lab['leaderboard']['realized_utility_max_pct']} |")
    rows.append(f"| Direction accuracy (not skill evidence) | {o['har_dir_acc_pct']}% (production grid) | "
                f"{_fmt(chall['direction_acc_pct'], 1)}% (every day) | noise floor "
                f"{lab['noise_floor']['har_direction_acc_pct_mean']}% with nothing to forecast |")
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
