# Model card: WTI next-week realized-volatility forecaster (HAR-IV v2)

Format after Mitchell et al. (2019), *Model Cards for Model Reporting*. Numbers in the tables are
generated from the committed artifacts (`scripts/readme_blocks.py`, checked in CI with `cog --check`).

## Model details

- **What it forecasts.** The annualized realized volatility of front-month NYMEX WTI crude oil
  (Yahoo `CL=F`, unadjusted) over the next five settlement-to-settlement returns.
- **Specification.** A heterogeneous autoregressive model in logs (Corsi 2009) with implied
  volatility: `log RV(t+1..t+5) = b0 + b1 log RV5 + b2 log RV22 + b3 log RV66 + b4 log OVX(t-1) + e`,
  fitted by OLS on every matured row (expanding window). RV is close-to-close realized vol; OVX is
  the CBOE Crude Oil ETF Volatility Index.
- **Version.** `har-iv/2`. Version 1 (June 2026) used the test day's own OVX close and published
  the median forecast; see the [changelog](../CHANGELOG.md) for why both changed.
- **Outputs.** The variance-consistent forecast (`median * sqrt(smearing)`, Duan 1983), used for
  risk and for every loss; the median, which decides the direction call; the probability that next
  week's realized vol exceeds this week's; and an adaptive-conformal 80% interval
  (Gibbs and Candès 2021).
- **Challenger.** The lab (`backend/vol_lab.py`) picks the lowest-loss model in the 90% Model
  Confidence Set other than the model of record; it is forecast live alongside it.
- **Code.** `backend/vol_forecast.py` (model of record), `backend/vol_lab.py` (benchmarks and
  evaluation), `backend/forecast_eval.py` (statistics). MIT license.

## Intended use

- A **risk and regime input**: sizing positions to a volatility target, setting a weekly
  Value-at-Risk, judging whether implied volatility looks rich or cheap against the model.
- **Not** a directional trading signal, and not a source of return on its own: a vol-targeted long
  book sized on it has the same Sharpe ratio as buy-and-hold within sampling error. What it buys is
  risk control (realized vol on target, thinner tails).
- Out of scope: other horizons, other contracts, intraday use, and any period or regime the
  2008–2026 sample does not contain.

## Factors

- **Timing.** Entry-time clean: every input is known at the 14:30 ET settlement that opens the
  target window. OVX enters with a one-session lag because it settles about 1h45m after CL. The live
  record scores each forecast on a window that starts after it was published and committed.
- **Contract rolls.** `CL=F` switches contracts the session after each last trade date and books
  the calendar spread as a return. The roll effect on the forecast is small (`validate()` reports
  the same model on roll-free returns); the live record skips windows that span a roll.
- **Regimes.** Results are reported per calendar year, per weekday sampling grid, and for
  2008–2016 vs 2017–2026; 2020 (negative prices, record volatility) is the dominant episode.

## Metrics and evaluation

- **Losses.** QLIKE and MSE on variances, the two losses that rank volatility forecasts
  consistently when the realized-vol proxy is noisy (Patton 2011); realized utility of a
  volatility-targeting investor (Bollerslev, Hood, Huss and Pedersen 2018).
- **Comparisons.** Diebold-Mariano with Newey-West variance and the Harvey-Leybourne-Newbold
  correction; Hansen-Lunde-Nason Model Confidence Set; Hansen SPA (has any model beaten the model
  of record after accounting for the search?); Mincer-Zarnowitz unbiasedness.
- **Risk use.** Weekly 95% and 99% VaR: Kupiec, Christoffersen independence and conditional
  coverage, Engle-Manganelli DQ, on each of the five weekday grids.
- **Not a skill metric.** Direction accuracy is reported only beside its noise floor: with nothing
  to forecast, the same pipeline scores about 75%, because a five-session realized vol is a noisy
  estimate that reverts toward its mean.

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("model_card_metrics") ]]] -->

| Metric | Model of record (HAR-IV v2) | Challenger (HAR-IV-range) | Reference |
|---|---:|---:|---|
| QLIKE, every OOS day (lower is better) | 0.370 | 0.354 | persistence 0.952; raw OVX 0.374 |
| In the 90% Model Confidence Set | no (3/5 weekday grids) | yes (5/5) | 5 of 14 models survive |
| Mincer-Zarnowitz slope (joint unbiasedness p) | 0.86 (0.161) | 0.77 (0.059) | 1 (unbiased) |
| 80% conformal interval coverage | 80.0% | 80.0% | target 80% |
| Realized utility (% per year) | 3.25 | 3.30 | maximum 4.0 |
| Direction accuracy (not skill evidence) | 74.4% (production grid) | 72.7% (every day) | noise floor 75.2% with nothing to forecast |
<!-- [[[end]]] -->

## Training and evaluation data

The pinned snapshot in `data/market/` (see the [datasheet](DATASHEET.md)): `CL=F` daily OHLC since
2000 and OVX since May 2007, finished sessions only. Out-of-sample evaluation starts after 250
training rows, in May 2008, with a purged expanding walk-forward (the last four training rows,
whose five-session targets overlap the test target, are dropped at every step).

## Quantitative analyses

The full benchmark table, VaR tests, conformal coverage by year and robustness checks are in the
[README](../README.md#results) and `data/vol_lab.json`; the live forward record is on the
dashboard and in `runtime-state/vol_track_record.json` on the `live-data` branch.

## Ethical considerations

Financial forecasts can cause losses when relied on. This is a research project, not investment
advice. All data come from free public sources whose terms the user must respect (Yahoo Finance's
data is for personal use).

## Caveats and recommendations

- Most of the model's skill is implied volatility: OVX's elasticity is about 1, the realized terms
  nearly cancel, and an implied-vol-only regression is statistically tied with it. The options
  market already prices this information.
- OVX is built from options on the USO ETF, not on CL futures, so it carries a basis to the
  front-month contract.
- The challenger's historical edge was found on the same sample it is evaluated on; Hansen's SPA
  puts the evidence that anything beats the model of record at the 10% level, not 5%. It is
  promoted only on its forward record (see the promotion rule in the README).
- Free daily data cannot measure intraday volatility; the range-based measure (Rogers-Satchell plus
  the overnight return) is the best free proxy and is what the challenger uses.
