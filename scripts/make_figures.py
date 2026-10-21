#!/usr/bin/env python3
"""Figures for README.md and docs/, drawn from the committed research artifacts only.

    python scripts/make_figures.py          # writes docs/figures/*.svg

Deterministic output (fixed SVG hash salt, no timestamps), so a re-run on unchanged artifacts leaves
the files byte-identical. Requires matplotlib (requirements-dev.txt).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "docs" / "figures"

INK, MUTED, RULE = "#1F2328", "#57606A", "#D0D7DE"
TRUTH, MODEL, IMPLIED, BENCH, NEG = "#6E7781", "#1F6FEB", "#D95926", "#199E70", "#CF222E"

plt.rcParams.update({
    "svg.hashsalt": "wti-vol-research", "svg.fonttype": "none", "font.family": "DejaVu Sans",
    "font.size": 9, "axes.edgecolor": RULE, "axes.labelcolor": MUTED, "axes.titlesize": 10,
    "axes.titleweight": "bold", "axes.titlecolor": INK, "axes.titlelocation": "left",
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#EEF1F4", "grid.linewidth": 0.6, "legend.frameon": False,
    "figure.facecolor": "white", "axes.facecolor": "white", "axes.axisbelow": True,
})


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _arr(values: list) -> list[float]:
    """JSON nulls (warm-up weeks) as NaN, so matplotlib leaves a gap."""
    return [float("nan") if v is None else float(v) for v in values]


def _dates(values: list[str]) -> list[date]:
    return [date.fromisoformat(v) for v in values]


def _level(share: float) -> str:
    """A confidence level from the lab's config as text: 0.9 -> '90%'."""
    return f"{round(share * 100, 6):g}%"


def _save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, format="svg", bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def forecast_vs_realized() -> None:
    lab = _load("vol_lab.json")
    s = lab["series"]
    x = _dates(s["date"])
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.fill_between(x, _arr(s["lo80"]), _arr(s["hi80"]), color=MODEL, alpha=0.14, linewidth=0,
                    label=f"{_level(1 - lab['config']['conformal']['alpha'])} conformal interval")
    ax.plot(x, _arr(s["actual"]), color=TRUTH, linewidth=0.7, label="Realized next-week vol")
    ax.plot(x, _arr(s["har_iv"]), color=MODEL, linewidth=1.1, label="HAR-IV forecast (model of record)")
    ax.plot(x, _arr(s["ovx"]), color=IMPLIED, linewidth=0.8, linestyle=(0, (3, 2)), label="OVX (implied)")
    ax.set_yscale("log")
    ax.set_yticks([10, 20, 40, 80, 160])
    ax.set_yticklabels(["10%", "20%", "40%", "80%", "160%"])
    ax.yaxis.set_minor_locator(mpl.ticker.NullLocator())
    ax.set_ylim(5, 260)
    ax.set_ylabel("annualized volatility (log scale)")
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_title(f"Out-of-sample next-week forecasts, {lab['sample']['first'][:4]}–{lab['sample']['last'][:4]} "
                 f"({lab['sample']['n_weeks']} weeks)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=4, fontsize=8)
    _save(fig, "forecast_vs_realized.svg")


def model_field() -> None:
    lab = _load("vol_lab.json")
    board = lab["leaderboard"]
    champion, challenger = lab["config"]["champion"], lab["config"]["challenger"]
    models = sorted(board["models"], key=lambda m: -m["qlike"])
    feasible = [m for m in models if not m.get("timing_control")]
    cap = 0.5
    outside = "#AFB8C1"
    fig, ax = plt.subplots(figsize=(9, 4.4))
    for i, m in enumerate(models):
        value = min(m["qlike"], cap)
        if m.get("timing_control"):
            # Not a member or a non-member: the timing control is left out of every confidence set.
            ax.barh(i, value, color="white", edgecolor=MUTED, hatch="////", linewidth=0.8, height=0.62)
        else:
            ax.barh(i, value, color=MODEL if m["in_mcs_qlike"] else outside, height=0.62)
        label = f"{m['qlike']:.3f}" + ("  (off scale)" if m["qlike"] > cap else "")
        ax.text(value + 0.004, i, label, va="center", fontsize=8, color=INK)
    names = []
    for m in models:
        tag = ("  ◆ model of record" if m["key"] == champion else "  ◆ challenger" if m["key"] == challenger
               else "  (timing control)" if m.get("timing_control") else "")
        names.append(m["label"].replace(" (model of record)", "") + tag)
    ax.set_yticks(range(len(models)))
    ax.set_yticklabels(names, fontsize=8.5)
    ax.set_xlim(0.3, cap + 0.06)
    ax.set_xlabel("QLIKE on variance-consistent forecasts, every out-of-sample day (lower is better)")
    ax.grid(axis="y", visible=False)
    handles = [mpatches.Patch(color=MODEL, label=f"in the {_level(1 - lab['config']['mcs']['size'])} MCS ({len(board['mcs_qlike_included'])} of "
                                                   f"{len(feasible)} feasible models)"),
               mpatches.Patch(color=outside, label="outside the MCS")]
    if len(feasible) < len(models):
        handles.append(mpatches.Patch(facecolor="white", edgecolor=MUTED, hatch="////",
                                      label="timing control (excluded from the MCS)"))
    ax.legend(handles=handles, loc="upper right", fontsize=8)
    ax.set_title("Benchmark field: out-of-sample QLIKE")
    _save(fig, "model_field.svg")


def var_backtest() -> None:
    lab = _load("vol_lab.json")
    s = lab["series"]
    x = _dates(s["date"])
    ret = [v if v is not None else 0.0 for v in s["ret5"]]
    var99 = s["var99"]
    fig, ax = plt.subplots(figsize=(9, 2.9))
    breach = [v99 is not None and r < -v99 for r, v99 in zip(ret, var99)]
    ax.scatter([d for d, b in zip(x, breach) if not b], [r for r, b in zip(ret, breach) if not b],
               s=3, color="#8C959F", linewidths=0, label="5-session log return")
    ax.scatter([d for d, b in zip(x, breach) if b], [r for r, b in zip(ret, breach) if b],
               s=18, color=NEG, linewidths=0, zorder=3, label="breach")
    ax.plot(x, [-v for v in _arr(var99)], color=MODEL, linewidth=1.0, drawstyle="steps-mid",
            label="−VaR 99% (HAR-IV + filtered historical simulation)")
    ax.axhline(0, color=RULE, linewidth=0.6)
    ax.set_ylim(-45, 30)
    ax.set_ylabel("5-session log return (%)")
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    summary = lab["var_backtest"]["summary"][f"{lab['config']['champion']}:fhs@0.01"]
    ax.set_title(f"Weekly 99% VaR: {summary['exceedances_by_grid'][0]} breaches vs {summary['expected']} expected "
                 f"(production grid); conditional coverage passes on "
                 f"{summary['conditional_coverage_pass_grids']}/5 grids")
    ax.legend(loc="lower left", fontsize=8, ncol=3)
    _save(fig, "var_backtest.svg")


def leak() -> None:
    purged, leaked = _load("wf_5y_purged.json"), _load("wf_5y_unpurged_LEAKED.json")
    anatomy = _load("leak_anatomy.json")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 3.3), gridspec_kw={"width_ratios": [1.6, 1], "wspace": 0.32})
    for report, color, label in ((leaked, NEG, "unpurged (leaked)"), (purged, MODEL, "purged")):
        res = report["results"]["1w"]
        total, xs, ys = 0.0, [], []
        for trade in res["trades"]:
            total += trade["pnl"]
            xs.append(date.fromisoformat(trade["t"]))
            ys.append(total / 1000)
        sharpe = res["pnl_metrics"]["sharpe_ratio_annualized"]
        ax1.plot(xs, ys, color=color, linewidth=1.2, label=f"{label}: Sharpe {sharpe:+.2f}".replace("-", "−"))
    ax1.axhline(0, color=RULE, linewidth=0.6)
    ax1.set_ylabel("cumulative P&L, $000 (one contract)")
    ax1.xaxis.set_major_locator(mdates.YearLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax1.set_title("Same 5-year backtest, purge off vs on")
    ax1.legend(loc="upper left", fontsize=8)
    ladder = anatomy["analytic"]["by_training_row"]
    ax2.bar([r["training_row"] for r in ladder], [r["p_sign_agree_pct"] for r in ladder],
            color=[NEG if not r["label_known_at_decision"] else "#AFB8C1" for r in ladder], width=0.6)
    ax2.axhline(50, color=MUTED, linewidth=0.8, linestyle=(0, (3, 2)))
    for i, r in enumerate(ladder):
        ax2.text(i, r["p_sign_agree_pct"] + 0.8, f"{r['p_sign_agree_pct']:.1f}", ha="center", fontsize=8)
    ax2.set_ylim(40, 90)
    ax2.set_ylabel("P(same sign as the test label), %")
    ax2.set_xlabel("training row (t-5 is the first known at the decision)")
    ax2.grid(axis="x", visible=False)
    ax2.set_title("Random walk: leaked label agrees with test")
    _save(fig, "leak.svg")


def main() -> int:
    forecast_vs_realized()
    model_field()
    var_backtest()
    leak()
    print(f"wrote {', '.join(sorted(p.name for p in OUT.glob('*.svg')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
