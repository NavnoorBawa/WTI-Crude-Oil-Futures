# Forecasting WTI Crude Oil Volatility with Free Data

How well can next-week realized volatility of front-month NYMEX WTI crude oil be forecast from free,
public data? This project answers with a field of fourteen forecasters judged by the losses and tests
of the volatility-forecasting literature, rebuilt in CI from a hash-pinned data snapshot, and with a
live forward record whose forecasts are sealed in git before the settlement that opens their scoring
window. A public dashboard is refreshed from GitHub Actions every four hours, on GitHub's best-effort
schedule.

[![Tests](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/tests.yml/badge.svg)](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/tests.yml)
[![Reproduce](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/reproduce.yml/badge.svg)](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/reproduce.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Live dashboard](https://img.shields.io/badge/live-dashboard-0969da.svg)](https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/)

**[Live dashboard](https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/)** ·
**[Full results](docs/RESULTS.md)** · [Reviewer guide](docs/REVIEWER_GUIDE.md) ·
[Methodology](docs/METHODOLOGY.md) · [Model card](docs/MODEL_CARD.md) · [Datasheet](docs/DATASHEET.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Data contract](docs/DATA_CONTRACT.md) ·
[Changelog and errata](CHANGELOG.md)

## The answer

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("key_findings") ]]] -->

- **Forecastable from free data.** Over 4,634 out-of-sample days (2008–2026), the model of record, HAR-IV (realized-volatility terms plus OVX lagged one session), has a QLIKE loss of 0.370 against 0.952 for a random-walk forecast, and is significantly better than all 6 models built from returns alone (Diebold-Mariano p < 0.05 each).
- **Most of that skill is the options market's.** The forecast's elasticity to OVX is 1.03 (HAC t 13.2), and a log regression on OVX alone scores 0.368: statistically tied with the full model (DM p = 0.641).
- **One variant may be better; that is not established.** HAR-IV-range, a range-based variant of the model of record, has the lowest loss of all 14 models (0.353; pairwise DM p = 0.002), but it was found by searching this sample. Hansen's SPA test, which accounts for that search, gives p = 0.072 (significant at 10% but not at 5%), so the edge is inconclusive. It runs live against the model of record under a promotion test fixed in advance: 52 independent weeks, one look.
- **Intervals on target; no claim about direction or returns.** The 80% adaptive conformal intervals cover 80.0% of 4,370 days, which the method steers to its target over the long run; by calendar year, 71.3% to 88.0%. Volatility-direction accuracy (74.4%) is not above what the same pipeline scores with nothing to forecast (75.2%), so it is not evidence of skill, and a volatility target sized on the forecast does not change the Sharpe ratio detectably (production run: difference −0.12 vs buy-and-hold, p = 0.408).
<!-- [[[end]]] -->

![Out-of-sample next-week volatility forecasts against realized volatility and OVX](docs/figures/forecast_vs_realized.svg)

*Figure 1. Out-of-sample forecasts of the model of record (blue) with its 80% adaptive conformal
interval, realized next-week volatility (grey) and the OVX implied-volatility index (orange), on the
production weekly grid. Log scale.*

## Results at a glance

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("results_at_a_glance") ]]] -->

| Question | Answer | Evidence | Source |
|---|---|---|---|
| Is next-week WTI volatility forecastable from free data? | Yes | QLIKE 0.370 vs 0.952 for persistence (DM p < 0.001); 7 of the 12 feasible benchmarks significantly worse | lab |
| Does it add to implied volatility? | Not detectably | OVX-only regression 0.368 (DM p = 0.641), Raw OVX 0.374 (DM p = 0.620); OVX elasticity 1.03 (HAC t 13.2) | lab |
| Is anything better? | Possibly: HAR-IV-range | HAR-IV-range QLIKE 0.353 (pairwise DM p = 0.002, before the search is accounted for); model of record outside the 90% MCS (p = 0.037); SPA p = 0.072 after the search; challenger on the live record | lab |
| Are the forecasts calibrated? | Unbiasedness not rejected | Mincer-Zarnowitz slope 0.86 (joint p = 0.161); 80% intervals cover 80.0% (adaptive conformal: on target by construction) | lab |
| Does it make a better weekly VaR? | Better than no volatility model, but so is raw OVX: the gain is from volatility scaling | Breach independence passes on 5/5 grids at 95% and 5/5 at 99%, vs 0/5 and 1/5 for historical simulation and 5/5 and 5/5 for raw OVX scaling; 99%: 14 breaches vs 8.8 expected on the production grid (Kupiec passes 5/5) | lab |
| Does it make money as a vol target? | No; volatility scaling controls risk | Sharpe difference vs buy-and-hold −0.12 (p = 0.408), vs trailing-vol sizing +0.01 (p = 0.764); excess kurtosis 1.82, against 1.96 sized on trailing vol (difference not tested) and 58.18 for buy-and-hold | production run |
| Is there a variance risk premium? | Not established: in vol points, not significantly above what a zero premium gives; in variance, not significant; as a short vol-swap return, not significant | IV above next-month RV in 73.5% of periods, vs 69.5% expected with zero premium (one-sided bootstrap p = 0.318); mean variance premium −263 (95% CI −1083 to 556); short vol-swap Sharpe 0.52 (one-sided p = 0.070 against a zero mean, though with no premium the vol-point gap would still average 1.5) | production run |
| Is volatility-direction accuracy evidence of skill? | No | 74.4% observed vs 75.2% for HAR without OVX on simulated returns with nothing to forecast | production run |
| Was the original direction model's Sharpe real? | No, a look-ahead leak | Leaked label agrees in sign with the test label 79.52% of the time on a random walk; purged, the Sharpe ratio is −0.98 | leak anatomy; recorded runs |

Source: *lab*: `data/vol_lab.json`, rebuilt from the pinned snapshot and checked in CI. *production run*: `data/vol_forecast_validation.json`, a recorded run on data downloaded from Yahoo Finance and FRED, not rebuilt in CI (Yahoo revises history). *leak anatomy*: `data/leak_anatomy.json`, offline from the pinned snapshot and synthetic returns (the unit suite re-runs a subset). *recorded runs*: the direction post-mortem's `data/wf_*.json`, also runs on downloaded data.
<!-- [[[end]]] -->

Every table, result statistic and data-dependent verdict on this page and in the
[full results](docs/RESULTS.md) is generated from the committed artifacts in [`data/`](data) by
[`scripts/readme_blocks.py`](scripts/readme_blocks.py), and CI fails if a page and the artifacts
disagree (`cog --check`). The full results give the design, each result with its test, the
post-mortem of the retracted direction model, two side studies and the research ledger of all
hypotheses tested, with multiplicity adjustments.

## Check it yourself

Each claim a skeptic would ask about has a check that does not rely on the author. The
[reviewer guide](docs/REVIEWER_GUIDE.md) walks through them in about ten minutes, with what each
should print.

| Claim | Check |
|---|---|
| The benchmark field reproduces from pinned inputs | The weekly [Reproduce](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/reproduce.yml) workflow rebuilds `data/vol_lab.json` from the SHA-256-pinned snapshot and compares it with the committed file; locally, `python -m backend.vol_lab` (about four minutes) |
| Every number on the page comes from an artifact | `PYTHONPATH=. cog --check README.md docs/RESULTS.md`; on the dashboard, each section's source line names the payload fields it reads |
| Each live forecast was committed before the prices it is scored on | `git show origin/live-data:runtime-state/vol_track_record.json \| python scripts/verify_record.py -` re-derives every entry's window, seal margin, realized volatility and loss from the record alone |
| No result is stated beyond its test | The [research ledger](data/research_ledger.json) lists every hypothesis tested with Benjamini-Hochberg and Holm adjustments; the dashboard's LEDG section filters it |
| Failures are reported, not dropped | The direction model this project began with is retracted, with its [post-mortem](docs/RESULTS.md#4-post-mortem-the-direction-model) and [errata](CHANGELOG.md#errata-published-numbers-that-were-wrong) |

## How it works

- **Data.** Daily `CL=F` open, high, low and close and the OVX implied-volatility index from Yahoo
  Finance, OVX cross-checked against FRED, and EIA's futures curve and spot price. The benchmark
  field runs on a frozen copy whose SHA-256 digests are pinned ([datasheet](docs/DATASHEET.md)).
- **Benchmark field.** Fourteen forecasters, from persistence, EWMA and GARCH-family models through
  HAR variants and OVX itself to LightGBM and a combination, forecast the same target on every
  out-of-sample day of a purged, expanding walk-forward. They are compared on QLIKE and MSE with
  Diebold-Mariano tests, the Model Confidence Set and Hansen's SPA test, and the model of record is
  also checked for calibration (Mincer-Zarnowitz, adaptive conformal intervals) and as a VaR input
  (Kupiec, Christoffersen, dynamic quantile) ([methodology](docs/METHODOLOGY.md)).
- **Live record.** Every refresh records the session's forecast. The job that holds the write token
  seals each new entry immediately before it pushes the record to the
  [`live-data`](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/blob/live-data/runtime-state/vol_track_record.json)
  branch; an entry is scored only if its seal precedes the settlement that opens its window by more
  than five minutes, windows across a contract roll are never scored, and the challenger can replace
  the model of record only through a single pre-declared test
  ([rules](docs/RESULTS.md#3-live-record-and-governance)).
- **Dashboard.** A static React site with no server: every statistic is read or computed from three
  files the pipeline publishes, each section names its sample, and the charts switch to tables that
  download as CSV ([architecture](docs/ARCHITECTURE.md)).

![The first screen of the dashboard: the research question and its computed answer, this week's call as recorded, and the evidence tiles](docs/figures/dashboard.png)

## What it does not claim

- **No price-direction or trading signal.** The direction model is retracted; volatility-direction
  accuracy is reported only beside its noise floor.
- **No demonstrated edge over the options market.** The model and a regression on OVX alone are
  statistically indistinguishable, in either direction; most of the model's skill is implied
  volatility's.
- **No promotion before the test.** The challenger's historical edge is inconclusive after the
  search that found it, and only the forward record can settle it.
- **Daily data only.** Five daily returns make a noisy volatility proxy; OVX is computed from USO
  options, not CL options; the sample and the field were chosen while looking at 2008–2026. All
  limitations are in [section 9 of the full results](docs/RESULTS.md#9-limitations).
- **Not investment advice.**

## Repository

| Path | Contents |
|---|---|
| [`backend/vol_forecast.py`](backend/vol_forecast.py) | Model of record: fit, validation, live forecast, economic tests |
| [`backend/vol_lab.py`](backend/vol_lab.py), [`backend/forecast_eval.py`](backend/forecast_eval.py) | The benchmark field and its losses and tests (DM, MCS, SPA, VaR tests, conformal) |
| [`backend/vol_record.py`](backend/vol_record.py), [`backend/contract_calendar.py`](backend/contract_calendar.py) | The live forward record, its seal and the promotion test; the CME calendar (standard library only) |
| [`backend/market_data.py`](backend/market_data.py) | Download, clean and pin the data snapshot |
| [`backend/research_ledger.py`](backend/research_ledger.py) | Every hypothesis tested, with multiplicity adjustments |
| [`backend/leak_anatomy.py`](backend/leak_anatomy.py), [`backend/backtest_walk_forward.py`](backend/backtest_walk_forward.py) | The post-mortem of the retracted direction model |
| [`freeze.py`](freeze.py), [`scripts/`](scripts) | Payload freeze and validation, record verification, research bundle, generated docs and figures |
| [`src/`](src) | The dashboard (React; canvas charts with lightweight-charts, SVG charts with d3) |
| [`data/`](data) | Committed artifacts and the pinned snapshot |
| [`docs/`](docs) | Full results, reviewer guide, methodology, model card, datasheet, architecture, data contract |

## Reproduce

Requires Python 3.12+ and, for the dashboard, Node 22.13+.

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m backend.vol_lab                                       # the benchmark field, offline (about four minutes)
RUN_SLOW=1 PYTHONPATH=. python -m unittest tests.test_vol_lab   # rebuild and compare with data/vol_lab.json
PYTHONPATH=. python -m unittest discover -s tests               # the test suite (network-free)
npm ci && npm run check                                         # dashboard lint, tests, build and audit
```

Every command, including the side studies and the runs on downloaded data, is in
[section 7 of the full results](docs/RESULTS.md#7-reproduce), with the numeric environment the
Reproduce workflow pins.

## Citation

See [`CITATION.cff`](CITATION.cff) (GitHub's "Cite this repository").

## License

MIT ([LICENSE](LICENSE)). Market data belongs to its providers and is used under their terms.
