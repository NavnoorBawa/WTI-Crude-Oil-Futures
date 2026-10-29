# Model card: WTI next-week realized-volatility forecaster (HAR-IV v2)

Format after Mitchell et al. (2019), *Model Cards for Model Reporting*. Numbers in the tables are
generated from the committed artifacts (`scripts/readme_blocks.py`, checked in CI with `cog --check`).

## Model details

- **What it forecasts.** The annualized realized volatility of front-month NYMEX WTI crude oil
  (Yahoo `CL=F`, unadjusted) over the next five settlement-to-settlement returns.
- **Specification.** A heterogeneous autoregressive model in logs (Corsi 2009) with implied
  volatility: `log RV(t+1..t+5) = b0 + b1 log RV5 + b2 log RV22 + b3 log RV66 + b4 log OVX(t-1) + e`,
  fitted by OLS on every matured row (expanding window). RV is close-to-close realized vol over the
  last 5, 22 and 66 sessions; OVX is the Cboe Crude Oil ETF Volatility Index.
- **Version.** `har-iv/2`. Version 1 (June 2026) used the test day's own OVX close and published
  the median forecast; see the [changelog](../CHANGELOG.md) for why both changed.
- **Outputs.** The variance-consistent forecast (`median * sqrt(smearing)`, Duan 1983), used for
  risk and for every loss; the median, which decides the direction call; a model-implied probability
  that next week's realized vol exceeds this week's (not calibrated, and shown as such); and an
  adaptive-conformal 80% interval (Gibbs and Candès 2021).
- **Challenger.** The lab (`backend/vol_lab.py`, `choose_challenger`) picks the lowest-QLIKE model
  in the full-sample 90% Model Confidence Set of the feasible models, other than the model of
  record. The timing control, which reads OVX published after the decision, is never eligible. The
  challenger is forecast live alongside the model of record. The live promotion test is pinned to
  this challenger and this model version (`vol_record.PROMOTION_CHALLENGER`,
  `vol_record.PROMOTION_MODEL_VERSION`): a forecast from another version, such as the plain-HAR
  fallback published when no lagged OVX print is available, is recorded but never enters the test,
  and a different challenger would be a new, separately declared test.
- **Code.** `backend/vol_forecast.py` (model of record), `backend/vol_lab.py` (benchmarks and
  evaluation), `backend/forecast_eval.py` (statistics), `backend/vol_record.py` (live record).
  MIT license.

## Intended use

- A **risk input**: sizing positions to a volatility target, and setting a weekly Value-at-Risk.
  Most of that benefit comes from scaling by a volatility estimate at all: raw OVX passes the VaR
  tests as often as this model, and sizing on trailing realized volatility comes close in the
  overlay; between the sized books only the Sharpe difference is tested
  ([full results, section 2.4](RESULTS.md#24-use-as-a-risk-input)).
- **Not** a signal for whether implied volatility is rich or cheap: timing short-variance positions
  on OVX minus the forecast was tested and is not supported, a failure to reject rather than
  evidence that timing cannot work (`data/research_ledger.json`, row `vrp_timing`;
  [full results, section 2.5](RESULTS.md#25-the-variance-risk-premium)).
- **Not** a directional trading signal, and not a source of return on its own: the vol-target
  overlay's Sharpe difference from buy-and-hold is in the table below. What it is for is risk
  control (realized vol near target, thinner tails).
- Out of scope: other horizons, other contracts, intraday use, and any period or regime the
  2008–2026 sample does not contain.

## Factors

- **Timing.** Entry-time clean: every input is known at the 14:30 ET settlement that opens the
  target window. OVX enters with a one-session lag because it settles about 1h45m after CL. The live
  record scores a forecast only if it was sealed and pushed to git before the settlement that opens
  its scoring window, which starts at least an hour after the forecast was issued and recorded;
  entries that miss the seal are marked `late_commit` and never scored (`backend/vol_record.py`).
- **Contract rolls.** `CL=F` switches contracts the session after each last trade date and books
  the calendar spread as a return. The production validation reports the same model on roll-free
  returns; the live record skips windows that span a roll.
- **Regimes.** Results are reported per calendar year, per 5-session sampling grid, and for
  2008–2016 vs 2017–2026; 2020 (negative prices, record volatility) is the dominant episode.

## Metrics and evaluation

- **Losses.** QLIKE and MSE on variances, the two losses that rank volatility forecasts
  consistently when the realized-vol proxy is noisy (Patton 2011); realized utility of a
  volatility-targeting investor (Bollerslev, Hood, Huss and Pedersen 2018).
- **Comparisons.** Diebold-Mariano with Newey-West variance and the Harvey-Leybourne-Newbold
  correction; the Hansen-Lunde-Nason Model Confidence Set over the feasible models; Hansen's SPA
  test of the null that no model beats the model of record, adjusted for the search; Mincer-Zarnowitz
  unbiasedness.
- **Risk use.** Weekly 95% and 99% VaR: Kupiec, Christoffersen independence and conditional
  coverage, Engle-Manganelli DQ, on each of the five 5-session sampling grids.
- **Not a skill metric.** Direction accuracy is reported only beside its noise floor: with nothing
  to forecast, the same pipeline (run as HAR without OVX on simulated i.i.d. returns) scores about
  75%, because a five-session realized vol is a noisy estimate that reverts toward its mean.

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("model_card_metrics") ]]] -->

| Metric | Model of record (`har-iv/2`) | Challenger (HAR-IV-range) | Reference |
|---|---:|---:|---|
| QLIKE, every OOS day (lower is better) | 0.370 | 0.353 | persistence 0.952; OVX-only regression 0.368; Raw OVX 0.374 |
| Diebold-Mariano on QLIKE vs the model of record (pairwise) | — | +3.10 (p = 0.002; before adjusting for the search that chose it) | OVX-only regression +0.47 (p = 0.641) |
| In the 90% Model Confidence Set (full sample; sampling grids) | no, MCS p 0.037 (2/5 grids) | yes, MCS p 1.000 (5/5 grids) | 4 of 13 feasible models; the timing control is excluded |
| Hansen SPA, null: no model beats the model of record | p = 0.011 | — | 12 feasible alternatives; significant at 5%, not after the ledger's Benjamini-Hochberg adjustment (q = 0.090) |
| Mincer-Zarnowitz slope (joint unbiasedness p) | 0.86 (0.161) | 0.81 (0.142) | 1 (unbiased) |
| 80% conformal interval coverage | 80.0% | 80.0% | target 80% |
| Realized utility (% per year) | 3.25 | 3.30 | maximum 4.0 |
| OVX elasticity (HAC t), full-sample fit | 1.03 (13.2) | — | 1 = proportional to implied vol |
| Vol-target overlay: Sharpe minus buy-and-hold (p) | −0.12 (0.408) | — | production run, 800 weeks |
| Direction accuracy, every OOS day (not skill evidence) | 72.6% | 72.4% | noise floor 75.2% (HAR without OVX on simulated returns with nothing to forecast); production run: model of record 74.4% on the weekly grid vs a floor of 75.2% |

Every-day rows and the SPA test are from the lab (`data/vol_lab.json`, rebuilt in CI); the overlay and production-grid direction figures are from a recorded run on downloaded data (`data/vol_forecast_validation.json`).
<!-- [[[end]]] -->

## Training and evaluation data

The pinned snapshot in `data/market/` (see the [datasheet](DATASHEET.md)): `CL=F` daily OHLC since
2000 and OVX since May 2007, finished sessions only. Out-of-sample evaluation starts after 250
training rows, in May 2008, with a purged expanding walk-forward (the last four training rows,
whose five-session targets overlap the test target, are dropped at every step). The lab's
figures (`data/vol_lab.json`) are rebuilt from that snapshot in CI. The production validation
(`data/vol_forecast_validation.json`) runs the same model on data downloaded from Yahoo Finance and
FRED; it is a committed record of that run, not rebuilt in CI, and a re-run differs in detail
because Yahoo revises history.

## Quantitative analyses

The full benchmark table, VaR tests, conformal coverage by year and robustness checks are in the
[full results](RESULTS.md#2-results) and `data/vol_lab.json`; the live forward record is on the
dashboard and in `runtime-state/vol_track_record.json` on the `live-data` branch.

## Ethical considerations

Financial forecasts can cause losses when relied on. This is a research project, not investment
advice. All data come from free public sources whose terms the user must respect (Yahoo Finance's
data is for personal use).

## Caveats and recommendations

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("model_card_caveats") ]]] -->

- The model is statistically indistinguishable from implied volatility alone: the OVX-only regression (DM p = 0.641) and Raw OVX (DM p = 0.620), with an OVX elasticity of 1.03 (HAC t 13.2). HAR-range, from daily price ranges without OVX, is also statistically indistinguishable from it (DM p = 0.629), so these data do not show that OVX is needed to reach its loss.
- **The 1% tail may be understated.** The weekly 99% Value-at-Risk (filtered historical simulation) breaches 9 to 14 times per sampling grid against 8.8 expected, above the expected count on 5 of 5 grids; the grids are offsets of the same days, not independent evidence. Kupiec's coverage test passes on 5/5 (each grid alone has little power at 1%; production grid p = 0.101); with a normal quantile it passes on 1/5. Use the filtered version, and size a 99% limit with that in mind ([full results, section 2.4](RESULTS.md#24-use-as-a-risk-input)).
<!-- [[[end]]] -->
- OVX is built from options on the USO ETF, not on CL futures, so it carries a basis to the
  front-month contract.
- The challenger's historical edge was found on the same sample it is evaluated on, so its pairwise
  Diebold-Mariano p-value in the table above ignores that search; the SPA test discounts for it, and
  the research ledger leaves the pairwise test out of its multiplicity adjustments. The challenger is
  promoted only by a single one-sided Diebold-Mariano test on its first 52 independent live weeks of
  the pinned pair, with a Student t(51) reference (see the promotion rule and its power in the
  [full results, section 3](RESULTS.md#3-live-record-and-governance)). A `keep` verdict, a failure to reject, does not by itself show that the
  challenger is no better.
- Free daily data cannot measure intraday volatility; the range-based measure (Rogers-Satchell plus
  the overnight return, with the test day's bar taken only to its settlement) is the free
  substitute used here and is what the challenger uses.
