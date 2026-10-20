# Forecasting WTI Crude Oil Volatility with Free Data

Next-week realized volatility of front-month NYMEX WTI crude oil: forecast from free data, compared
with a field of standard benchmarks using the losses and tests of the volatility-forecasting
literature, and published every four hours to a public dashboard. Each forecast is committed to git
before the data that will score it exist.

[![Tests](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/tests.yml/badge.svg)](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/tests.yml)
[![Reproduce](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/reproduce.yml/badge.svg)](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/reproduce.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Live dashboard](https://img.shields.io/badge/live-dashboard-0969da.svg)](https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/)

**[Live dashboard](https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/)** ·
[Methodology](docs/METHODOLOGY.md) · [Model card](docs/MODEL_CARD.md) · [Datasheet](docs/DATASHEET.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Data contract](docs/DATA_CONTRACT.md) ·
[Changelog and errata](CHANGELOG.md)

![Out-of-sample next-week volatility forecasts against realized volatility and OVX](docs/figures/forecast_vs_realized.svg)

*Figure 1. Out-of-sample forecasts of the model of record (blue) with its 80% adaptive conformal
interval, realized next-week volatility (grey) and the OVX implied-volatility index (orange), on the
production weekly grid. Log scale.*

## Abstract

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("abstract") ]]] -->

This project forecasts the realized volatility of front-month NYMEX WTI crude oil over the next five sessions from free data, and keeps a public, git-timestamped record of its live forecasts. Over 4,634 out-of-sample days (2008–2026), a heterogeneous autoregressive model with lagged implied volatility (HAR-IV) has a QLIKE loss of 0.370, against 0.952 for a random-walk forecast. It is significantly better than GJR-GARCH(1,1)-t, GARCH(1,1)-t, HAR-RV, HAR-RV + semivariance, EWMA (RiskMetrics) and LightGBM (Diebold-Mariano p < 0.05 each). Most of that skill is the options market's: the elasticity on OVX is 1.03, and a regression on OVX alone (0.368) is statistically tied with the full model. A range-based variant has the lowest loss of the 14 models (0.354, DM p = 0.006) and stays in the 90% Model Confidence Set on all 5 weekday sampling grids. Hansen's SPA test gives p = 0.088 for any model in the field beating the model of record, so the variant runs live as a challenger under a promotion rule fixed in advance. The forecast's 80% adaptive conformal intervals cover 80.0% of outcomes. Used to size a 35% volatility target, it keeps realized volatility at 34.9% and cuts excess kurtosis from 58.18 to 1.82. Its weekly 99% VaR breaches 14 times against 8.8 expected. It does not raise the Sharpe ratio. Next-week direction accuracy (74.4%) is not evidence of skill: the same pipeline scores 75.2% on simulated returns with nothing to forecast.

The project began as a machine-learning direction model whose backtest Sharpe ratio of 2.47 was a look-ahead leak; purged, it is −0.98. The same leak manufactures a Sharpe ratio of 2.85 from a pure random walk. All 75 hypotheses the project has tested are listed in a research ledger with multiplicity adjustments; 12 of the 23 supported ones survive Benjamini-Hochberg at 5%.
<!-- [[[end]]] -->

## Results at a glance

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("results_at_a_glance") ]]] -->

| Question | Answer | Evidence |
|---|---|---|
| Is next-week WTI volatility forecastable from free data? | Yes | QLIKE 0.370 vs 0.952 for persistence; 7 of 13 benchmarks significantly worse |
| Does it add to implied volatility? | Not detectably | Raw OVX 0.374 (DM p = 0.620); OVX elasticity 1.03 (HAC t 13.2) |
| Is anything better? | Possibly: a range-based variant | QLIKE 0.354, DM p = 0.006, but SPA p = 0.088 after the search; challenger on the live record |
| Are the forecasts calibrated? | Close | Mincer-Zarnowitz slope 0.86 (joint p = 0.161); 80% intervals cover 80.0% |
| Does it make a better weekly VaR? | Yes: breaches stop clustering | 99% VaR breaches are independent on 5/5 weekday grids vs 1/5 for historical simulation; 14 breaches vs 8.8 expected (Kupiec passes 5/5) |
| Does it make money as a vol target? | No, it controls risk | Sharpe difference vs buy-and-hold −0.12 (p = 0.408); excess kurtosis 1.82 vs 58.18 |
| Is there a variance risk premium? | In vol points, yes; in variance, not significant | IV above next-month RV in 73.5% of periods; mean variance premium −263 (95% CI −1083 to 556) |
| Is direction accuracy evidence of skill? | No | 74.4% observed vs 75.2% with nothing to forecast |
| Was the original direction model's Sharpe real? | No, a look-ahead leak | Leaked label agrees in sign with the test label 79.52% of the time on a random walk |
<!-- [[[end]]] -->

The tables and statistics on this page are generated from the committed artifacts in
[`data/`](data) by [`scripts/readme_blocks.py`](scripts/readme_blocks.py), not typed by hand. CI
fails if the page and the artifacts disagree (`cog --check`). The artifacts are rebuilt from a hash-pinned data
snapshot, and a second workflow checks that rebuild every week.

---

## 1. Question and design

How well can the realized volatility of the front-month contract over the next five sessions be
forecast at the settlement that opens those sessions, using only data that is free and public at
that moment? The project forecasts volatility rather than price direction for a reason. Its first
model forecast direction, and that model's apparent skill turned out to be a look-ahead leak
(section 4). Volatility clustering, by contrast, is one of the best-replicated effects in finance.

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("design_block") ]]] -->

- **Target.** Annualized realized volatility of the next 5 settlement-to-settlement log returns of `CL=F`, the front-month NYMEX WTI future.
- **Decision time.** The 14:30 ET settlement that opens the target window. Every input is published by then; OVX, which settles at 16:15 ET, enters with a 1-session lag.
- **Walk-forward.** Expanding window from the first 250 rows, refit every 5 sessions. The last 4 training rows are purged at every step, because their targets overlap the test target and are not known at the decision.
- **Sample.** 4,634 out-of-sample days, 2008-05-08 to 2026-10-12. Losses and tests use every day. Each of the five weekday sampling grids (927 non-overlapping weeks) is also scored on its own, and VaR is scored only there, since it needs independent outcomes.
- **Forecasts.** Losses, VaR and risk use the variance-consistent forecast (the median times a Duan smearing factor). The direction call and the intervals use the median.
- **Inference.** Model Confidence Set at 90% (range statistic, 5,000 stationary-bootstrap replications, blocks of 20 days or 10 weeks, seed 20261020); Diebold-Mariano with Newey-West variance and the Harvey-Leybourne-Newbold correction; Hansen's SPA across the whole field.
<!-- [[[end]]] -->

**Data.** Daily `CL=F` open, high, low and close and the CBOE Crude Oil Volatility Index (OVX) from
Yahoo Finance, with OVX cross-checked against FRED. Same-contract returns come from EIA's
futures-curve history, and the supply-shock study uses EIA's Cushing spot price. The research runs
on a frozen copy of the market data whose SHA-256 digests are pinned
([datasheet](docs/DATASHEET.md), section 7).

**The field.** Fourteen forecasters, from the simplest to the most flexible:
- persistence (last week's realized volatility);
- OVX used directly as the forecast;
- RiskMetrics EWMA;
- GARCH(1,1) and GJR-GARCH(1,1) with Student-t errors, re-estimated weekly and filtered daily;
- HAR on close-to-close realized variance, on realized semivariance (Patton and Sheppard 2015) and
  on a range-based variance (Rogers and Satchell 1991, plus the overnight return);
- a regression on OVX alone;
- HAR-IV, the model of record: HAR plus the log of the previous session's OVX;
- HAR-IV-range, the same model on range-based variance;
- HAR-IV with same-day OVX, a timing control that uses information not available at the decision;
- LightGBM on the HAR and OVX features;
- an equal-weight combination of HAR-IV, GJR-GARCH and the OVX regression.

Every model sees the same rows, purge and refit schedule. The full specifications are in the
[methodology](docs/METHODOLOGY.md), section 3.

## 2. Results

### 2.1 The benchmark field

![QLIKE of every model; blue bars are in the 90% Model Confidence Set](docs/figures/model_field.svg)

*Figure 2. Out-of-sample QLIKE of each forecaster over every day. Blue: in the 90% Model Confidence
Set.*

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("leaderboard_table") ]]] -->

| Model | Family | QLIKE | DM vs model of record (p) | DM range over 5 weekday grids | MCS p | In 90% MCS (grids) | Realized utility (% / yr) |
|---|---|---:|---:|---:|---:|---:|---:|
| **HAR-IV-range** *(challenger)* | HAR | 0.354 | +2.76 (0.006) | +2.10 to +2.52 | 1.00 | yes (5/5) | 3.30 |
| HAR-IV, same-day OVX (v1 timing) | HAR | 0.358 | +3.99 (< 0.001) | +0.86 to +3.06 | 0.47 | yes (5/5) | 3.28 |
| HAR-range | HAR | 0.364 | +0.71 (0.475) | +0.25 to +0.99 | 0.13 | yes (5/5) | 3.29 |
| Implied-vol regression | implied | 0.368 | +0.47 (0.641) | +0.09 to +0.60 | 0.09 | no (5/5) | 3.26 |
| Combination | combination | 0.368 | +0.41 (0.684) | −0.13 to +1.21 | 0.17 | yes (5/5) | 3.26 |
| **HAR-IV (model of record)** | HAR | 0.370 | — | — | 0.00 | no (3/5) | 3.25 |
| Implied vol (raw OVX) | implied | 0.374 | −0.50 (0.620) | −0.65 to −0.39 | 0.17 | yes (5/5) | 3.29 |
| GJR-GARCH(1,1)-t | time-series | 0.417 | −3.17 (0.002) | −3.10 to −2.13 | 0.00 | no (0/5) | 3.15 |
| GARCH(1,1)-t | time-series | 0.423 | −3.72 (< 0.001) | −3.45 to −2.79 | 0.00 | no (0/5) | 3.13 |
| HAR-RV | HAR | 0.431 | −4.12 (< 0.001) | −4.33 to −3.21 | 0.00 | no (0/5) | 3.11 |
| HAR-RV + semivariance | HAR | 0.431 | −4.00 (< 0.001) | −4.06 to −3.22 | 0.00 | no (0/5) | 3.11 |
| EWMA (RiskMetrics) | time-series | 0.443 | −4.30 (< 0.001) | −3.86 to −3.30 | 0.00 | no (0/5) | 3.06 |
| LightGBM | machine learning | 0.462 | −5.79 (< 0.001) | −4.56 to −3.80 | 0.00 | no (0/5) | 3.07 |
| Persistence (RV5) | naive | 0.952 | −7.07 (< 0.001) | −5.72 to −4.43 | 0.00 | no (0/5) | 1.51 |

QLIKE on variance-consistent forecasts over every out-of-sample day; DM = Diebold-Mariano (Newey-West, Harvey-Leybourne-Newbold), positive = better than the model of record; MCS = Hansen-Lunde-Nason Model Confidence Set (range statistic, stationary bootstrap, block 20). Hansen's SPA test of "some model beats the model of record" over the whole field: p = 0.088. Realized utility: Bollerslev, Hood, Huss and Pedersen (2018), maximum 4.0.
<!-- [[[end]]] -->

Every model in the confidence set uses implied volatility or the daily range. Every forecaster built
from close-to-close returns alone (persistence, EWMA, GARCH, GJR-GARCH, and HAR on realized variance
or semivariance) is significantly worse than the model of record. So is gradient boosting, given
the same inputs. The model of record and the OVX regression are statistically indistinguishable: the
options market already prices most of what the realized-volatility terms know.

The range-based variant has the lowest QLIKE and the highest realized utility in the field. On MSE,
which a few extreme weeks dominate, the combination edges it. The variant was also found by
searching this sample, which is what the SPA test discounts for. That is why it competes live
rather than replacing the model of record on its historical record.

### 2.2 What the model is

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("coefficients_table") ]]] -->

| Term | Coefficient | HAC t |
|---|---:|---:|
| const | −4.876 | −14.07 |
| log RV5 | +0.039 | +1.31 |
| log RV22 | +0.172 | +2.75 |
| log RV66 | −0.152 | −2.89 |
| log OVX | +1.033 | +13.16 |

Full-sample fit of the model of record: 4,884 daily rows, R² 0.486 on log realized vol, Newey-West t with 10 lags (the five-session targets overlap).
<!-- [[[end]]] -->

The OVX elasticity is close to one, and the three realized-volatility terms nearly cancel. To a
first approximation the model is implied volatility times a constant below one, with a small
correction from recent realized volatility. The constant absorbs the variance risk premium
(section 2.5) and OVX's basis to the front-month future.

### 2.3 Calibration and intervals

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("calibration_block") ]]] -->

| Model | MZ intercept | MZ slope | Joint p (unbiased) | Actual / forecast | 80% coverage | Coverage by year (min to max) | Interval score |
|---|---:|---:|---:|---:|---:|---|---:|
| HAR-IV (model of record) | +0.038 | 0.86 | 0.161 | 0.920 | 80.0% | 71.3% to 88.0% | 54.45 |
| HAR-IV-range | +0.045 | 0.77 | 0.059 | 0.915 | 80.0% | 73.3% to 87.3% | 53.13 |

Mincer-Zarnowitz regression of realized on forecast variance with a HAC covariance. Intervals: adaptive conformal inference (Gibbs-Candes 2021) with 5-session delayed feedback, step 0.005, scored only after each target has settled; interval score of Gneiting and Raftery (2007), in vol points (lower is better).
<!-- [[[end]]] -->

The Mincer-Zarnowitz slope is below one, so the forecasts move a little more than outcomes do, but
the joint test does not reject unbiasedness at 5%. The intervals cover their nominal rate over the
full sample. Coverage by calendar year scatters around the target; with about fifty independent
weeks a year, deviations of several points are expected.

### 2.4 Use as a risk input

![Weekly 99% VaR against realized five-session returns](docs/figures/var_backtest.svg)

*Figure 3. Weekly 99% Value-at-Risk from the model of record with filtered historical simulation,
against realized five-session returns (production grid). Red: breaches.*

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("var_table") ]]] -->

| Method | Level | Exceedances by grid (expected) | Kupiec | Independence | Cond. coverage | DQ |
|---|---:|---|---:|---:|---:|---:|
| HAR-IV (model of record) + filtered HS | 5% | 46, 47, 39, 43, 43 (43.8) | 5/5 | 5/5 | 5/5 | 5/5 |
| HAR-IV-range + filtered HS | 5% | 50, 51, 47, 43, 45 (43.8) | 5/5 | 4/5 | 5/5 | 4/5 |
| GJR-GARCH(1,1)-t + filtered HS | 5% | 53, 46, 43, 47, 46 (43.8) | 5/5 | 4/5 | 5/5 | 5/5 |
| EWMA (RiskMetrics) + filtered HS | 5% | 48, 43, 45, 53, 41 (43.8) | 5/5 | 4/5 | 5/5 | 5/5 |
| Implied vol (raw OVX) + filtered HS | 5% | 47, 47, 38, 43, 45 (43.8) | 5/5 | 5/5 | 5/5 | 5/5 |
| Historical simulation (no vol model) | 5% | 49, 54, 44, 46, 46 (43.8) | 5/5 | 0/5 | 0/5 | 0/5 |
| HAR-IV (model of record) + filtered HS | 1% | 14, 12, 13, 12, 9 (8.8) | 5/5 | 5/5 | 5/5 | 5/5 |
| HAR-IV-range + filtered HS | 1% | 12, 12, 13, 13, 9 (8.8) | 5/5 | 5/5 | 5/5 | 5/5 |
| GJR-GARCH(1,1)-t + filtered HS | 1% | 14, 8, 12, 13, 12 (8.8) | 5/5 | 5/5 | 5/5 | 5/5 |
| EWMA (RiskMetrics) + filtered HS | 1% | 15, 11, 12, 13, 11 (8.8) | 5/5 | 5/5 | 5/5 | 4/5 |
| Implied vol (raw OVX) + filtered HS | 1% | 15, 11, 10, 11, 9 (8.8) | 5/5 | 5/5 | 5/5 | 5/5 |
| Historical simulation (no vol model) | 1% | 18, 15, 13, 15, 15 (8.8) | 4/5 | 1/5 | 1/5 | 0/5 |

Weekly (5-session) VaR of a long front-month position, scored on each of the five weekday grids (non-overlapping outcomes); a test passes at 5%. Kupiec (1995) unconditional coverage, Christoffersen (1998) independence and conditional coverage, Engle-Manganelli (2004) DQ.
<!-- [[[end]]] -->

Historical simulation, with no volatility model, gets roughly the right number of breaches at 95%
and too many at 99%. Worse, its breaches arrive in clusters, so it fails the independence tests on
most grids. Scaling by any of the volatility forecasts removes the clustering. At 99% the scaled
methods still breach more often than nominal on most grids, within what Kupiec's test tolerates.
Five-session WTI returns have fatter tails than one standardized-residual distribution captures.

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("overlay_table") ]]] -->

| Book | Sharpe | Realized vol (target 35.0%) | Vol of vol | Excess kurtosis | Worst week | 97.5% ES (weekly) | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Buy and hold | 0.04 | 49.3% | 30.3 | 58.18 | −55.3% | −17.8% | 97.8% |
| Trailing 22-day vol target | −0.09 | 38.0% | 6.4 | 1.96 | −23.0% | −15.5% | 95.4% |
| Forecast vol target | −0.07 | 34.9% | 6.2 | 1.82 | −19.1% | −13.5% | 93.1% |

800 weeks, 2008-05-08 to 2024-04-05, same-contract returns, 5.0 bp per unit of turnover. Sharpe differences (stationary bootstrap): forecast minus buy-and-hold −0.12 (p = 0.408), forecast minus trailing +0.01 (p = 0.764).
<!-- [[[end]]] -->

A long position scaled to a constant volatility target is the use a risk desk would make of the
forecast. Sized on the forecast, the book's realized volatility lands on the target, and the tails
shrink by an order of magnitude in kurtosis. Sizing on trailing volatility overshoots the target.
Neither raises the Sharpe ratio: the differences are small and not significant. This matches the
evidence on volatility targeting outside equities (Harvey et al. 2018). The forecast controls risk;
it does not create return.

### 2.5 The variance risk premium

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("vrp_block") ]]] -->

- **In vol points the premium is there.** OVX exceeded the realized volatility of the following 20 sessions in 73.5% of 200 non-overlapping periods, by 2.5 points on average.
- **In variance units, the units an option seller is paid in, it is not significant.** The mean premium is −263.3 variance points (95% Newey-West CI −1083 to 556): the few periods where realized volatility explodes (2020-04-13 (−158.8 vol pts), 2020-02-13 (−84.5 vol pts), 2020-03-13 (−57.3 vol pts)) outweigh the many small gains.
- **Harvesting it.** A short vol-swap proxy has a Sharpe ratio of 0.52; the convex short-variance position 0.0 (0.71 without 2020).
- **Timing it with the forecast does not help.** Regressing short-variance P&L on OVX minus the forecast: slope 0.80, HAC t 0.78, p = 0.435 (n = 188).
<!-- [[[end]]] -->

The usual statement, that implied volatility exceeds realized most of the time, is true here. It is
also the wrong unit. A variance seller is paid the premium in variance, and in variance the 2020
periods outweigh the rest of the sample. Measuring the premium properly needs a long history
of oil option prices, which is not freely available.

### 2.6 Direction accuracy is not skill

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("noise_block") ]]] -->

| Pipeline run on | Direction accuracy | Mean-reversion rule |
|---|---:|---:|
| WTI, production weekly grid (927 weeks) | 74.4% | 68.5% |
| WTI, every out-of-sample day | 72.6% | 66.9% |
| i.i.d. Student-t(5), constant volatility (nothing to forecast), 10 simulations | 75.2% (73.2–76.5) | 71.8% |

For any continuous i.i.d. series, calling 'up' exactly when the current 5-session vol is below its median is right with probability E[max(U, 1-U)] = 75%. The call compares next week's realized volatility with this week's, and a five-session realized volatility is a noisy estimate that reverts toward its mean whether or not the underlying volatility moves. Accuracy against that comparison measures the noise in the proxy. The losses above measure forecasting skill.
<!-- [[[end]]] -->

The first version of this project reported its direction accuracy, against a base rate near 50%, as
the headline evidence of skill. It is not evidence of skill, and it is retracted
([errata](CHANGELOG.md#errata-published-numbers-that-were-wrong)). The dashboard and the live record
still report direction, always next to this floor.

### 2.7 Robustness

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("robustness_block") ]]] -->

- **Weekday grids.** The model of record's QLIKE on the five weekly grids ranges 0.358 to 0.378; the challenger is in the 90% MCS on 5/5 and the model of record on 3/5. A conclusion that holds on one grid and not the next is reported as fragile.
- **Implied-vol timing.** OVX settles at 16:15 ET, after the 14:30 CL settlement that opens the target window, so the model uses the previous session's OVX. Using the same day's value lowers QLIKE to 0.358 (DM +3.99, p < 0.001): the extra 1h45m of information is worth having, and is not available at the decision time.
- **Roll gaps.** `CL=F` books each roll's calendar spread as a return. With the 283 roll days through 2024 rebuilt as same-contract returns from EIA's curve, the production model's QLIKE is 0.368 over the same 927 weeks (0.376 as published).
- **Second volatility proxy.** On a range-based realized variance (Rogers-Satchell plus the overnight return) the model of record's QLIKE is 0.189; the MCS on that proxy keeps HAR-IV, same-day OVX (v1 timing); HAR-IV-range.
- **Choosing the model out of sample.** Picking, each day, the model with the lowest trailing 156-week loss scores 0.365 vs 0.370 for always using the model of record (DM p = 0.384); it picks HAR-IV-range 37.7%, HAR-range 18.6%, Implied vol (raw OVX) 18.5% of the time.
<!-- [[[end]]] -->

Calendar-year results, the volatility cone and the per-grid tables are in `data/vol_lab.json` and
on the dashboard's research pages.

## 3. Live record and governance

Every four hours (on GitHub's best-effort schedule) the Refresh workflow downloads the latest
finished sessions, refits the model of record and the challenger on all matured rows, validates the
payload and deploys the dashboard. It then appends the session's forecast to a record on the
[`live-data`](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/blob/live-data/runtime-state/vol_track_record.json)
branch ([`backend/vol_record.py`](backend/vol_record.py)):

- **One entry per session**, stamped with the time the forecast was issued and committed by the bot,
  so the history cannot be backfilled without the commit dates showing it.
- **The scoring window starts after the commit.** Each entry is scored on the five sessions after
  the first settlement more than an hour past the later of its issue time and the moment it was
  recorded. The record is committed within minutes, so the commit that proves an entry existed comes
  before the settlement that opens its window.
- **Contract rolls are skipped, not scored.** A window that crosses a front-month roll would measure
  the calendar spread.
- **Scored on QLIKE**, for the model of record and the challenger on the same entries. Direction is
  shown next to its noise floor. Only non-overlapping weeks count as independent evidence.

The promotion rule was fixed before the first live forecast. It is stated in code
(`vol_record.PROMOTION_RULE`) and published with every record summary:

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("promotion_rule") ]]] -->

> The challenger replaces the model of record after at least 52 independent resolved weeks if its live QLIKE is lower with a one-sided Diebold-Mariano p below 0.10.
<!-- [[[end]]] -->

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("promotion_power") ]]] -->

How long the forward record needs: on the weekly grids the challenger's historical edge is a Diebold-Mariano statistic of about 2.4 over 927 weeks. If that is its true edge, 52 independent weeks give the rule a 24% chance of promoting it, and 80% power takes about 708 weeks (14 years). The rule is built not to promote on luck. A challenger still unpromoted after a year has not been shown to be worse.
<!-- [[[end]]] -->

## 4. Post-mortem: the direction model

The project began as a six-model machine-learning ensemble that forecast the sign of WTI's
next-week return. Its five-year walk-forward backtest showed a Sharpe ratio no liquid futures market
should give. An audit found the cause: at each step the walk-forward trained on rows whose five-session labels had not matured
at the decision time. The newest of those labels shares four of its five daily returns with the
test label.

![Equity curves with and without the purge; sign agreement of leaked labels by training row](docs/figures/leak.svg)

*Figure 4. Left: the same backtest with the purge off and on. Right: on a pure random walk, how
often each recent training label has the same sign as the test label. Grey: known at the decision;
red: not yet known.*

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("postmortem_table") ]]] -->

| Metric | 5-year: leaked → purged | 10-year rolling: leaked → purged |
|---|---|---|
| Annualized Sharpe (after costs) | 2.47 → **−0.98** | 2.09 → **−0.04** |
| Direction accuracy | 67.2% → **48.5%** | 64.9% → **50.9%** |
| p vs coin flip | < 0.001 → **0.691** | < 0.001 → **0.371** |
| Weekly calls | 198 → **198** | 450 → **450** |
<!-- [[[end]]] -->

The leak is easy to demonstrate on data with no signal at all. With jointly normal labels whose
correlation is ρ, the signs agree with probability ½ + arcsin(ρ)/π. A label sharing four of five
returns has ρ = 0.8, and agrees with the test label far more often than chance. Any model that weights the most recent
row, which every tree ensemble and nearest-neighbour method does, inherits that agreement
([`backend/leak_anatomy.py`](backend/leak_anatomy.py)):

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("leak_table") ]]] -->

| Data | Model | Runs | Accuracy unpurged → purged | Sharpe unpurged → purged |
|---|---|---:|---|---|
| i.i.d. random walk | 1-nearest neighbour | 10 | 72.4% → **50.4%** | 3.67 → **−0.08** |
| i.i.d. random walk | Decision tree | 10 | 65.1% → **50.5%** | 2.38 → **−0.02** |
| i.i.d. random walk | Random forest (20 trees) | 3 | 66.7% → **50.8%** | 2.85 → **−0.09** |
| Block-shuffled CL=F returns | 1-nearest neighbour | 10 | 72.9% → **49.7%** | 3.21 → **0.04** |
| Block-shuffled CL=F returns | Decision tree | 10 | 65.0% → **47.5%** | 2.04 → **−0.12** |
| Real CL=F, 5-year window | 1-nearest neighbour | 1 | 73.4% → **46.4%** | 3.19 → **−0.46** |
| Real CL=F, 5-year window | Decision tree | 1 | 59.9% → **53.1%** | 1.52 → **0.03** |
| Real CL=F, 5-year window | Random forest (20 trees) | 1 | 62.8% → **44.9%** | 1.64 → **−0.78** |

For comparison, the retracted model: 67.2% and Sharpe 2.47 over 198 weeks. Same walk-forward, same five-session labels, the purge switched off and on; `backend/leak_anatomy.py`.
<!-- [[[end]]] -->

The fix is the purge of López de Prado (2018): drop the last horizon − 1 training rows at every
step. It is now the default in the backtest engine, and an engine-level test drives the walk-forward
with a recording model to prove that no training label matures after its decision. The retracted
model no longer runs in production. Its artifacts stay in the repository, labelled, as a record of
the retraction.

## 5. Two side studies

### 5.1 Term-structure carry

Carry, the slope of the futures curve, is one of the best-documented commodity factors. The rule
was fixed in advance: long when carry is above the median of all prior months, flat otherwise. A
first version measured returns on the rolled `CL=F` series, whose roll gaps subtract the carry
itself, and its conclusion was wrong. The rebuild holds one contract per cycle
([`backend/carry_signal_test.py`](backend/carry_signal_test.py)).

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("carry_table") ]]] -->

| Sample | Months | Return above / below median carry (%/mo) | One-sided p | Rank corr. (p) | Timed vs buy-and-hold Sharpe |
|---|---:|---|---:|---|---|
| Full (1990–2024) | 409 | +1.98 / +0.04 | 0.018 | +0.10 (0.038) | 0.50 vs 0.28 |
| Before 2007 (1990–2006) | 203 | +2.92 / +0.00 | 0.007 | +0.20 (0.004) | 0.76 vs 0.48 |
| 2007 on (2007–2024) | 206 | +0.91 / +0.07 | 0.273 | −0.03 (0.665) | 0.22 vs 0.12 |

Second-nearby contract held over each contract cycle on EIA's futures curve (no roll gap in any return). After 2007 the spread test could detect only a spread of 3.47%/month (80% power), and had 67% power against the pre-2007 spread; the change in spread is not significant (p = 0.256), the change in rank correlation is (p = 0.021).
<!-- [[[end]]] -->

Over the full sample the spread is significant at 5% on its own, though not after the ledger's
multiplicity adjustment (section 6). Whether the effect survived 2007 is not something this sample
can settle:
the post-2007 spread test has too little power to call the effect dead, while the rank correlation
did drop significantly. The timed strategy's Sharpe gain over buy-and-hold is not significant in any
period (Ledoit and Wolf 2008).

### 5.2 Supply shocks against a placebo

What happens to WTI after a geopolitical or physical supply shock? It is the question a
discretionary desk asks during an event. A median peak means little without a reference, because
the highest close of any 20-session window is usually above its start. So every group is compared
with thousands of ordinary dates measured the same way
([`backend/supply_shock_playbook.py`](backend/supply_shock_playbook.py)):

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("supply_table") ]]] -->

| Group | n | Median peak | Median settle | p vs placebo (peak / settle) |
|---|---:|---:|---:|---|
| Ordinary dates (placebo) | 7,352 | +5.4% | +0.7% | — |
| Announced or physical supply removal >0.5 mbpd | 21 | +12.1% | +4.3% | < 0.001 / 0.044 |
| Geopolitical threat, no supply removed | 10 | +4.0% | +0.5% | 0.490 / 0.944 |
| Hormuz / transit-strait risk | 8 | +6.0% | +0.0% | 0.850 / 0.788 |
| Iran-driven | 12 | +6.4% | +0.6% | 0.688 / 0.969 |
| Announced OPEC(+) production cut | 7 | +12.8% | +6.1% | 0.026 / 0.076 |
| Hurricane / weather shut-in | 5 | +5.7% | −4.8% | 0.939 / 0.146 |
| Armed conflict | 15 | +8.2% | +2.2% | 0.202 / 0.496 |
| Sanctions | 6 | +6.6% | +2.2% | 0.719 / 0.624 |
| All defined events | 35 | +7.8% | +2.1% | 0.092 / 0.334 |

Peak = highest close in the 20 sessions after the event vs the day before; settle = 10 sessions after the peak; EIA daily Cushing spot. Supply removal vs threat-only directly: p = 0.031 (peak), 0.387 (settle). Event dates and groups were chosen with hindsight and the p-values are not adjusted for the groups tested.
<!-- [[[end]]] -->

Announced or physical removal of supply moves the price more than an ordinary date does. Threats
with no barrels removed are indistinguishable from an ordinary date. The study is decision support,
not a forecast, and the dashboard presents it that way.

## 6. Research ledger

Every hypothesis the project has tested, the retracted ones included, is a row in
[`data/research_ledger.json`](data/research_ledger.json), with its test statistic read from the
artifact that produced it. The rows are adjusted for multiplicity: Benjamini-Hochberg, Holm, and the
t > 3 hurdle that Harvey, Liu and Zhu (2016) propose for factor research.

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("ledger_block") ]]] -->

75 hypotheses: 23 supported, 41 rejected, 4 inconclusive, 7 retracted. Of the supported ones, 12 survive Benjamini-Hochberg at 5%, 9 survive Holm, and 9 clear the t > 3 hurdle of Harvey, Liu and Zhu (2016):

| Claim | p | BH q | Clears t > 3 |
|---|---:|---:|:---:|
| Walk-forward without the purge, a 20-tree random forest on i.i.d. random walks (no signal exists) scores above 50% direction accuracy. | < 0.001 | < 0.001 | yes |
| Walk-forward without the purge, a 1-nearest-neighbour model on block-shuffled CL=F returns (no signal exists) scores above 50% direction accuracy. | < 0.001 | < 0.001 | yes |
| Carry rank-correlates positively with the next cycle's return (1990-2006). | 0.004 | 0.019 | no |
| OVX exceeds the realized vol of the following month more often than not. | < 0.001 | < 0.001 | yes |
| HAR-IV calls vol direction better than a 'reverts to its 66-day mean' rule. | < 0.001 | < 0.001 | yes |
| HAR-IV beats persistence (last week's realized vol) on QLIKE. | < 0.001 | < 0.001 | yes |
| Adding OVX to HAR-RV (the model of record) lowers QLIKE. | < 0.001 | < 0.001 | yes |
| Same-day OVX (the v1 timing, which settles after CL and overlaps the target) lowers QLIKE vs OVX lagged one session. | < 0.001 | < 0.001 | yes |
| The challenger HAR-IV-range beats the model of record on QLIKE. | 0.006 | 0.027 | no |
| The model of record is outside the 90% Model Confidence Set on QLIKE (beaten by the best). | 0.002 | 0.020 | no |
| OVX carries information about next-week realized vol beyond the HAR terms (in-sample). | < 0.001 | < 0.001 | yes |
| Events in group 'supply_lost' move WTI differently from a random non-event date (peak). | < 0.001 | 0.003 | yes |
<!-- [[[end]]] -->

Two of the strongest results are the leak demonstrations: the project's most significant findings
are about how its own first result was wrong.

## 7. Reproduce

Requires Python 3.12+ and, for the dashboard, Node 22.13+.

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt

# The volatility research, offline, from the pinned snapshot in data/market (about three minutes)
python -m backend.vol_lab                       # writes data/vol_lab.json
RUN_SLOW=1 PYTHONPATH=. python -m unittest tests.test_vol_lab   # rebuild and compare with the committed artifact

# The production validation (downloads the latest data from Yahoo Finance and FRED)
python -m backend.vol_forecast                  # writes data/vol_forecast_validation.json

# The side studies and the ledger
python -m backend.carry_signal_test             # offline, from the committed EIA curve
python -m backend.supply_shock_playbook         # offline, from the committed EIA spot series
python -m backend.leak_anatomy                  # about two minutes
python -m backend.research_ledger               # re-reads every artifact

# This page, the model card, the datasheet and the figures
PYTHONPATH=. cog -r README.md docs/MODEL_CARD.md docs/DATASHEET.md
python scripts/make_figures.py

# The test suite (network-free)
PYTHONPATH=. python -m unittest discover -s tests
```

`python -m backend.vol_lab --refresh` downloads a new snapshot and rewrites its manifest. Under the
same numeric environment the rebuild is bit for bit. Across BLAS thread counts and CPU kernels the
iterative GARCH fits differ in the last bits. The Reproduce workflow therefore pins a numeric
environment, and `vol_lab.compare_reports` defines a match: every model set, flag and count
identical, and every number equal to 1e-4 relative.

**Provenance of the pinned snapshot:**

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("provenance_table") ]]] -->

| File | Rows | First | Last | SHA-256 | Source |
|---|---:|---|---|---|---|
| `data/market/cl_f_daily.csv` | 6,564 | 2000-08-23 | 2026-10-19 | `2e0c57f697484b46…` | Yahoo Finance CL=F daily OHLC (yfinance), finished NYMEX sessions only |
| `data/market/ovx_yahoo_daily.csv` | 4,892 | 2007-05-10 | 2026-10-19 | `b641a9753df21f21…` | Yahoo Finance ^OVX daily close (yfinance), finished sessions only |
| `data/market/ovxcls_fred.csv` | 4,893 | 2007-05-10 | 2026-10-16 | `2117700ea77ca6b1…` | FRED OVXCLS (https://fred.stlouisfed.org/series/OVXCLS), keyless fredgraph CSV |
<!-- [[[end]]] -->

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("data_quality_block") ]]] -->

- `CL=F` sessions kept: 6,564. Bars with a non-positive open, high or low (the session after the 2020-04-20 negative settlement): 1, close kept, range blanked. Bars whose high was below the open or close: 3; whose low was above them: 4 (repaired by widening the range; closes are never changed).
- OVX, Yahoo vs FRED: 4,891 common days, 99.88% within 0.05 points, correlation 0.999996, largest differences 2009-11-27 (Yahoo 40.73, FRED 43.36), 2010-04-27 (Yahoo 30.34, FRED 28.02), 2014-12-09 (Yahoo 38.53, FRED 37.56).
- Snapshot retrieved 2026-10-20T11:01:46.215851Z, finished sessions through 2026-10-19.
<!-- [[[end]]] -->

## 8. System

![The dashboard](docs/figures/dashboard.png)

*The dashboard at [navnoorbawa.github.io/WTI-Crude-Oil-Futures](https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/):
the live forecast and its interval, the benchmark field, risk, the live record, and the research
pages.*

The site runs with no server. A scheduled workflow builds a static snapshot and deploys it to GitHub
Pages ([architecture](docs/ARCHITECTURE.md)):

- **Refresh, every four hours.** An unprivileged job freezes the payload (`freeze.py`), validates it
  against the [data contract](docs/DATA_CONTRACT.md) and builds the site. A separate job that holds
  the write token installs nothing. It re-validates the exact files it publishes, refuses a build
  that is older than the live site, deploys, and persists the live record to `live-data`. The only
  secret in use is a free EIA key.
- **Price, every 15 minutes (best effort).** Updates a quote file on `live-data` that the page reads
  directly. Each quote carries its own exchange timestamp, and the page shows its age.
- **Research.** `research.json` is built only from committed artifacts, never computed at deploy
  time, so the page cannot show a figure the repository cannot reproduce.
- **Engineering.**
  - A network-free unit suite, with look-ahead tests on every model.
  - Hypothesis property tests on the statistics, the contract calendar and feature timing.
  - `mypy --strict` on the research modules, and branch coverage reported in CI.
  - Pinned dependencies, audited on every run, and GitHub Actions pinned to commit SHAs.
  - Workflow linting and secret scanning.
  - Guard tests that keep write tokens out of any job that installs packages.

| Path | Contents |
|---|---|
| [`backend/vol_forecast.py`](backend/vol_forecast.py) | Model of record: fit, validation, live forecast, economic tests |
| [`backend/vol_lab.py`](backend/vol_lab.py) | The benchmark field, Model Confidence Set, SPA, VaR, conformal intervals |
| [`backend/forecast_eval.py`](backend/forecast_eval.py) | Losses and tests: QLIKE, Diebold-Mariano, MCS, Kupiec, Christoffersen, DQ, conformal |
| [`backend/market_data.py`](backend/market_data.py) | Download, clean and pin the data snapshot |
| [`backend/vol_record.py`](backend/vol_record.py) | The live forward record (standard library only) |
| [`backend/contract_calendar.py`](backend/contract_calendar.py) | CME holidays, last trade dates, rolls, finished sessions |
| [`backend/leak_anatomy.py`](backend/leak_anatomy.py), [`backend/backtest_walk_forward.py`](backend/backtest_walk_forward.py) | The post-mortem and the purged walk-forward engine |
| [`backend/carry_signal_test.py`](backend/carry_signal_test.py), [`backend/supply_shock_playbook.py`](backend/supply_shock_playbook.py) | Side studies |
| [`backend/research_ledger.py`](backend/research_ledger.py) | The ledger of every hypothesis tested |
| [`freeze.py`](freeze.py), [`scripts/`](scripts) | Payload freeze, validation, research bundle, generated docs and figures |
| [`src/`](src) | The dashboard (React, lightweight-charts) |
| [`data/`](data) | Committed artifacts and the pinned snapshot |

Run the dashboard locally against a fresh snapshot:

```bash
npm ci
python freeze.py                                 # writes public/data.json and public/research.json
VITE_STATIC_DATA=true npm run build && (cd dist && python -m http.server 8000)
```

## 9. Limitations

- **Daily data only.** Realized volatility from five daily returns is a noisy proxy. Intraday data
  would sharpen both the forecasts and their evaluation, but it is not free over this history. The
  range-based measure is the best free substitute, and it is the challenger's input.
- **OVX is not a CL options index.** It is computed from options on the USO ETF, so it carries a
  basis to the front-month future. It is also the dominant input.
- **One sample, one search.** The field, the challenger and every robustness check were chosen
  while looking at the 2008–2026 sample. The SPA test, the ledger's multiplicity adjustments and the
  forward record are the defences, and only the forward record is fully clean.
- **Roll gaps after 2024.** EIA stopped publishing its futures curve in April 2024. Later
  same-contract corrections are not possible from free data, so the live record skips roll windows.
- **2020.** Negative prices and record volatility dominate several statistics, the variance risk
  premium above all. Results without 2020 are reported where it changes a conclusion.
- **Free schedules.** GitHub runs scheduled workflows on a best-effort basis, so updates can arrive
  hours late. The dashboard shows data ages rather than assuming a cadence.
- **Not investment advice.** A research project; the data's terms of use (Yahoo Finance's in
  particular) are the user's to respect.

## 10. References

- Bollerslev, T., Hood, B., Huss, J., Pedersen, L. H. (2018). Risk everywhere: modeling and managing volatility. *Review of Financial Studies* 31(7).
- Christoffersen, P. (1998). Evaluating interval forecasts. *International Economic Review* 39(4).
- Corsi, F. (2009). A simple approximate long-memory model of realized volatility. *Journal of Financial Econometrics* 7(2).
- Diebold, F., Mariano, R. (1995). Comparing predictive accuracy. *Journal of Business & Economic Statistics* 13(3).
- Duan, N. (1983). Smearing estimate: a nonparametric retransformation method. *Journal of the American Statistical Association* 78(383).
- Engle, R., Manganelli, S. (2004). CAViaR: conditional autoregressive value at risk by regression quantiles. *Journal of Business & Economic Statistics* 22(4).
- Gibbs, I., Candès, E. (2021). Adaptive conformal inference under distribution shift. *NeurIPS*.
- Glosten, L., Jagannathan, R., Runkle, D. (1993). On the relation between the expected value and the volatility of the nominal excess return on stocks. *Journal of Finance* 48(5).
- Gneiting, T., Raftery, A. (2007). Strictly proper scoring rules, prediction, and estimation. *Journal of the American Statistical Association* 102(477).
- Hansen, P. R. (2005). A test for superior predictive ability. *Journal of Business & Economic Statistics* 23(4).
- Hansen, P. R., Lunde, A., Nason, J. (2011). The model confidence set. *Econometrica* 79(2).
- Harvey, C., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M., Van Hemert, O. (2018). The impact of volatility targeting. *Journal of Portfolio Management* 45(1).
- Harvey, C., Liu, Y., Zhu, H. (2016). … and the cross-section of expected returns. *Review of Financial Studies* 29(1).
- Harvey, D., Leybourne, S., Newbold, P. (1997). Testing the equality of prediction mean squared errors. *International Journal of Forecasting* 13(2).
- Kupiec, P. (1995). Techniques for verifying the accuracy of risk measurement models. *Journal of Derivatives* 3(2).
- Ledoit, O., Wolf, M. (2008). Robust performance hypothesis testing with the Sharpe ratio. *Journal of Empirical Finance* 15(5).
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
- Mincer, J., Zarnowitz, V. (1969). The evaluation of economic forecasts. In *Economic Forecasts and Expectations*, NBER.
- Newey, W., West, K. (1987). A simple, positive semi-definite, heteroskedasticity and autocorrelation consistent covariance matrix. *Econometrica* 55(3).
- Patton, A. (2011). Volatility forecast comparison using imperfect volatility proxies. *Journal of Econometrics* 160(1).
- Patton, A., Sheppard, K. (2015). Good volatility, bad volatility: signed jumps and the persistence of volatility. *Review of Economics and Statistics* 97(3).
- Rogers, L. C. G., Satchell, S. (1991). Estimating variance from high, low and closing prices. *Annals of Applied Probability* 1(4).

The full list, with the data and documentation standards followed, is in the
[methodology](docs/METHODOLOGY.md#references).

## Citation

See [`CITATION.cff`](CITATION.cff) (GitHub's "Cite this repository").

## License

MIT ([LICENSE](LICENSE)). Market data belongs to its providers and is used under their terms.
