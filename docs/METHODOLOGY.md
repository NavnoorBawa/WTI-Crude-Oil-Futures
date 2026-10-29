# Methodology

How every number in the project is produced. Code references point to the implementation;
results are in the [full results](RESULTS.md) (summarised in the [README](../README.md)) and the
committed artifacts. This page states methods only; numbers that depend on the data are generated
into those pages from the artifacts.

## 1. Data, timing and provenance

- **Prices.** Yahoo `CL=F`, the continuous front-month NYMEX WTI future, unadjusted. Its daily
  close equals EIA's contract-1 settlement on almost every day. Only finished sessions are used
  (`backend/contract_calendar.py`: a CME session runs 18:00 ET to 17:00 ET; settlement is 14:30 ET).
  The daily high and low run to 17:00 ET, two and a half hours after the settlement.
- **Implied volatility.** OVX, the Cboe Crude Oil ETF Volatility Index: a 30-calendar-day implied
  vol computed from options on the USO ETF (not on CL futures), settled at 16:15 ET.
- **Entry-time rule.** A forecast for session *t* may use only information available at *t*'s 14:30 ET
  settlement, which is where the target window begins. OVX's 16:15 close comes later, so the models
  use the previous session's OVX, and day *t*'s own high and low never enter a feature (section 2).
  Version 1 used the same-day OVX close. The lab keeps that variant as a timing control: its losses
  and Diebold-Mariano tests are reported, and it is excluded from every Model Confidence Set, the
  SPA test, the out-of-sample selection and the choice of challenger. It is measurably better, which
  confirms that the overlap carried information.
- **Provenance.** Each artifact comes from one of three kinds of input, and CI checks them
  differently:
  - `data/vol_lab.json` (the benchmark field) and `data/leak_anatomy.json` read the hash-pinned
    snapshot in `data/market/` (Yahoo `CL=F`, Yahoo OVX, FRED OVX; see [DATASHEET.md](DATASHEET.md)).
    The Reproduce workflow rebuilds `data/vol_lab.json` in full on changes to its inputs and every
    week; the unit suite re-runs a subset of the leak anatomy's runs.
  - `data/carry_signal_test.json` and `data/supply_shock_study.json` read committed EIA files
    (`data/eia_wti_futures_curve.json`, `data/eia_wti_spot_daily.json`), which are not in the
    snapshot's manifest. The unit suite rebuilds both and requires them to equal the committed files,
    and rebuilds `data/research_ledger.json` from the artifacts.
  - `data/vol_forecast_validation.json` (the production validation) and `data/wf_*.json` (the
    direction post-mortem) are records of runs on data downloaded from Yahoo Finance (and FRED).
    Yahoo revises history, so a re-run gives close but not identical numbers; CI does not
    regenerate them.

## 2. Target and features

- **Target.** Realized vol of the next five settlement-to-settlement log returns,
  `RV(t+1..t+5) = sqrt(252/5 · Σ r²)`, annualized.
- **Close-to-close features.** `RV5, RV22, RV66`: realized vol over the last 5, 22 and 66 sessions
  up to and including *t*, the weekly, monthly and quarterly components of the HAR model (Corsi 2009
  uses daily, weekly and monthly; this target is weekly).
- **Range-based variance of a full session.** Rogers-Satchell variance
  `ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O)` plus the squared overnight return `ln(O/C_prev)²`: the
  open-to-close and close-to-open parts of a Yang-Zhang estimator (Rogers and Satchell 1991, Yang and
  Zhang 2000). Range estimators are several times more efficient than squared returns (Alizadeh,
  Brandt and Diebold 2002; Lyócsa, Molnár and Výrost 2021). On roll days the overnight gap is the
  calendar spread, so it is replaced by the median squared overnight return over the non-roll
  sessions among the previous 250 sessions (`vol_lab._overnight_variance`: a rolling 250-session
  window ending the session before, with roll days left out of the median; at least 20 non-roll
  sessions are required, and the term is zero before that). Where a bar has no usable open, high or
  low, the squared close-to-close return is used.
- **Range-based features at the decision** (`vol_lab.range_features`). Session *t*'s full bar is
  not known at its 14:30 ET settlement, because its high and low run to 17:00 ET. Range-based RV5,
  RV22 and RV66 at *t* therefore sum the full-bar variance of the *w* − 1 earlier sessions (each
  closed before session *t* opened) and, for session *t* itself, only the part known at the
  settlement: the squared overnight return plus the squared open-to-settlement return
  `ln(C/O)²` (`vol_lab.settlement_range_variance`). The full-bar variance of the sessions after *t*
  is the second realized proxy (section 6), never a feature.
- **Downside semivolatility** over the last five sessions (Patton and Sheppard 2015), the extra
  term of `har_rs` and one of LightGBM's features.

## 3. Models (`backend/vol_lab.py`)

| Key | Model |
|---|---|
| `persistence` | next week's vol = the last five sessions' realized vol |
| `raw_ovx` | Raw OVX: OVX itself (previous session), no fitting |
| `ewma` | RiskMetrics, λ = 0.94, recursive on all returns since 2000 |
| `garch`, `gjr` | GARCH(1,1) and GJR-GARCH(1,1) with Student-t errors (`arch`) on all returns since 2000, re-estimated weekly (warm-started, optimizer tolerance 1e-9) and filtered daily with the latest estimates |
| `har_rv` | log-HAR on close-to-close RV5, RV22, RV66 |
| `har_rs` | `har_rv` plus one term, the five-session downside semivolatility (after Patton and Sheppard 2015) |
| `har_range` | log-HAR on the range-based RV5, RV22, RV66 as known at the settlement |
| `ovx_only` | the OVX-only regression: log regression on the previous session's OVX alone |
| `har_iv` | **model of record**: log-HAR on close-to-close RV5, RV22, RV66 plus the previous session's OVX |
| `har_iv_sameday` | `har_iv` with the test day's own OVX close: the timing control (section 1) |
| `har_iv_range` | `har_iv` with the range-based terms in place of the close-to-close ones |
| `lgbm` | gradient-boosted trees (LightGBM) on the logs of the close-to-close terms, OVX, the range-based terms and the downside semivolatility; retrained every 13 weeks |
| `combo` | equal-weight pool of the variances of `har_iv`, `gjr`, `ovx_only` |

The challenger is the lowest-QLIKE feasible model in the full-sample 90% Model Confidence Set
other than the model of record (`vol_lab.choose_challenger`; the timing control is never
eligible). It is chosen on the same sample it is evaluated on, so its pairwise Diebold-Mariano test
against the model of record ignores the search: the SPA test (section 7) is the search-adjusted one,
the research ledger leaves the pairwise test out of its multiplicity family (section 17), and the
challenger is promoted only on its forward record (section 12), whose test is pinned to it.

## 4. Walk-forward and purge

Expanding window; the first forecast after 250 training rows, then a forecast on every
out-of-sample day. A training row's target covers the five sessions after it, so at test row *t*
the last four rows' targets overlap the test target: for every model trained on targets they are
**purged** (only rows `< t − 4` are used). Refit cadence differs by model: the log regressions are
refit every day; GARCH and GJR-GARCH are re-estimated weekly (on the days of sampling grid 0) and
filtered daily; LightGBM is retrained every 13 weeks; EWMA, persistence and raw OVX need no fit.
Time-series models use returns up to and including *t*. The production validator
(`backend/vol_forecast.py`) runs the same model of record on one sampling grid, a forecast every
fifth session (`STEP = 5`): that is the production weekly grid.

`tests/test_vol_lab.py` checks the no-look-ahead property on a synthetic market. It takes a
mid-sample test day *t*, replaces every return after *t* with different data, triples OVX after
*t*, rescales every later bar, and widens day *t*'s own high and low (its trading after the
settlement); every model's forecast for every day up to and including *t* must be unchanged, and
the targets after *t* must change, so the perturbation has bite. Separate tests check that the
range-based features read day *t*'s bar only up to its settlement (widening day *t − 1*'s bar
does move them) and that the timing control is the only model that reads day *t*'s OVX close.

## 5. Variance-consistent forecasts

A regression in logs estimates the conditional **median** of realized vol. QLIKE and MSE on variance
are minimized by the conditional **mean** of the variance, so scoring a median against a GARCH
mean rewards bias, not information. Each log model is retransformed with Duan's (1983) smearing
estimator from its own purged training residuals: `vol_mean = exp(μ̂) · sqrt(mean(exp(2ê)))`
(`exp(2s²)` if the errors are normal). The median is kept for the direction call (P(next > current)
> ½ iff median > current) and for quantile intervals. LightGBM's smearing factor comes from its own
matured out-of-sample errors once a year of them exists, since a boosted model's in-sample
residuals understate its error.

## 6. Losses

- **QLIKE** `y²/f² − ln(y²/f²) − 1` and **MSE** `(y² − f²)²` on variances: the losses whose ranking
  is robust to noise in the realized-vol proxy (Patton 2011).
- **Realized utility** of a mean-variance investor sizing by the forecast,
  `(SR²/γ)(y/f − (y/f)²/2)` with SR = 0.4, γ = 2 (Bollerslev, Hood, Huss and Pedersen 2018);
  maximized when the forecast equals the outcome.
- **Second proxy.** QLIKE against the range-based realized vol of the target week (the full-bar
  range variance of the five target sessions, not rescaled), as a robustness check on the proxy
  itself. Its level differs from the close-to-close proxy's, so only rankings within one proxy
  are compared.

## 7. Inference

- **Sampling.** Forecasts are made every out-of-sample day; consecutive targets overlap by up to
  four sessions. Pooled statistics use Newey-West long-run variances (automatic bandwidth, at least
  the overlap). Because results computed on one sampling grid can differ from the next, the QLIKE
  comparisons (each model's loss, its Diebold-Mariano test against the model of record and its
  Model Confidence Set membership) are also run on each of the five non-overlapping 5-session
  sampling grids (offsets 0–4; each grid's dates fall on every weekday), and the report says on how
  many a result holds. The VaR backtests (section 10) run only on the grids. Every other statistic
  is computed over every out-of-sample day unless its section says otherwise.
- **Diebold-Mariano** (1995) on loss differentials with the Harvey-Leybourne-Newbold (1997)
  small-sample factor and Student-t reference.
- **Model Confidence Set** (Hansen, Lunde and Nason 2011; `arch.bootstrap.MCS`, range statistic,
  stationary bootstrap, block 20 daily / 10 weekly, 5,000 replications, fixed seed), computed over
  the feasible models only: the set of models not significantly worse than the best at 90%
  confidence. MCS p-values depend on the whole field, so a model that uses information from after
  the decision would push feasible models out; the timing control is left out of every set.
  Robustness: blocks of 10 and 40 days, and 2008–2016 and 2017 onward separately.
- **SPA** (Hansen 2005; `forecast_eval.spa_test`, stationary bootstrap from `arch`, block 20,
  5,000 replications, fixed seed): the p-value of the null hypothesis that no model in the field
  (the timing control excluded) has a lower expected QLIKE than the model of record, adjusted for
  the search over the whole field. The statistic is studentized, as Hansen defines it: each model's
  mean loss difference is divided by its long-run standard deviation, so a noisy model cannot set
  the maximum by its noise alone. The consistent p-value is reported, with the lower and upper ones
  and each model's t-statistic in the artifact. `arch.bootstrap.SPA` 8.0.0 ignores its `studentize`
  flag and returns the non-studentized test (White's Reality Check with Hansen's recentring); its
  p-values on the same bootstrap draws are kept in the artifact as `non_studentized`, and they are
  what releases up to 2.1.0 published as the SPA p-value (CHANGELOG, errata). Hansen's lower
  p-value is a liberal bound and the upper one a conservative bound; the consistent one is between.
  Robustness: blocks of 10 and 40 days, and 2008–2016 and 2017 onward separately
  (`spa_robustness_qlike`). A rejection says that some alternative in the field beats the model of
  record; the model with the largest studentized gain (`best_model`) is the one whose t-statistic
  sets the test statistic.
- **Forecasts as produced.** Diebold-Mariano, the Model Confidence Set and the SPA test compare the
  forecasts each method produced on its expanding window, treating them as given: the error in the
  estimated parameters is not accounted for (West 1996), so these are tests of forecasting methods
  as run here, not of population models.
- **Out-of-sample selection.** Each day, the feasible model (persistence excluded) with the lowest
  QLIKE over the previous 156 weeks of matured outcomes; the model of record for the first 52 weeks.
- **Mincer-Zarnowitz**: regress realized variance on forecast variance; an unbiased forecast has
  intercept 0 and slope 1 (HAC Wald test).
- **Coefficients** of the full-sample HAR-IV with Newey-West t-statistics (10 lags, overlapping
  targets), and their walk-forward path.

## 8. Direction accuracy and its noise floor

Direction = will next week's RV exceed this week's RV5? Both are five-observation estimates. For a
continuous i.i.d. series, calling "up" exactly when the current value is below its median is right
with probability `E[max(U, 1−U)] = 3/4` (U uniform), because the next value is independent. So a
model with **no** forecasting skill scores about 75% through this pipeline. Both the lab and the
production validator simulate it: i.i.d. Student-t(5) returns with the sample's volatility, a world
with nothing to forecast and no implied volatility, run through the identical feature builder and
the production weekly walk-forward with **HAR without OVX** (`vol_forecast.noise_floor`, which the
lab calls as well, so both report the same simulations). The same simulations score the mean-reversion rule, and HAR beats it there
too, so beating that rule is not evidence of skill either. Direction accuracy is therefore reported
only next to this floor and never as evidence of skill; skill is judged by the losses above.

## 9. Intervals

Adaptive conformal inference (Gibbs and Candès 2021) on the log forecast errors of the median:
the interval at step *i* is the `[a_i/2, 1 − a_i/2]` quantile range of the previous errors, and
`a_i` moves by `γ(α − miss)` so long-run coverage converges to `1 − α` even when the error
distribution drifts. With daily forecasts an outcome is only observed five sessions later, so the
lab uses the **delayed-feedback** version: the interval at *i* uses only errors and misses of rows
`≤ i − 5`. Intervals are also scored with the interval score (Gneiting and Raftery 2007).

## 10. Value-at-Risk backtests

Weekly (five-session) VaR of a long front-month position, `VaR = −q · σ̂₅`, for the model of
record, the challenger, GJR-GARCH, EWMA and raw OVX, with `q` the normal quantile or (filtered
historical simulation) the empirical quantile of the grid's past standardized outcomes `r₅/σ̂₅`
after 52 weeks; unconditional historical simulation (the trailing quantile of past five-session log
returns, no vol model) is the benchmark. Tests, on each sampling grid (non-overlapping outcomes):
Kupiec (1995) unconditional coverage, Christoffersen (1998) independence and conditional coverage,
Engle and Manganelli (2004) dynamic quantile, and the Basel traffic light on the last 250 weeks.
The outcome `r₅` is the five-session log return of the `CL=F` continuous series, the series the
forecast's own target (next-week realized volatility) is measured on; the vol-target overlay
(section 11) uses same-contract returns instead. About a quarter of the windows contain a roll day, whose return
books the spread between the expiring and the next contract (section 2.7 of the full results
measures that gap for the production run); a same-contract position would see slightly different
outcomes in those windows. The VaR backtest does not rebuild them, because EIA's curve ends in April
2024 and a series that is roll-free before that date and spliced after it would be inconsistent.

## 11. Economic evaluation (`vol_forecast._economic_tests`)

These figures are part of the production validation (section 1: a record of a run on downloaded
data).

- **Same-contract returns.** Roll days are rebuilt from EIA's contract-2 settlements (through April
  2024, when EIA stopped publishing), so no P&L books the calendar spread.
- **Vol-target overlay.** Weekly long position `min(2, 35% / forecast)`, 5 bp per unit of turnover,
  against buy-and-hold and trailing-22-day sizing. Judged on what vol targeting is for (Harvey et al.
  2018): realized vol against the target, vol of vol, kurtosis, worst week, 97.5% expected shortfall
  and drawdown, reported for each book; the Sharpe differences, with a stationary-bootstrap p-value,
  are the only differences tested. Trailing-22-day sizing separates what scaling by volatility does
  from what the forecast adds.
- **Variance risk premium.** OVX against the realized vol of the next 20 sessions, non-overlapping,
  in variance points (with a Newey-West CI), the unit a variance seller is paid in and the measure
  that matters, and in vol points. In vol points the null is a zero premium, not 50%
  (`vol_forecast._zero_premium_null`). With a zero premium, `IV² = E[RV²]`, a 20-session realized
  vol is still noisy and right-skewed and `E[RV] < sqrt(E[RV²])` (Jensen's inequality), so OVX
  exceeds RV in more than half the periods and `E[IV − RV] > 0`. The null keeps the observed shape
  of the per-period ratios `RV²/IV²` and rescales them to mean 1: the share of rescaled ratios below
  1 is the share of periods with `IV > RV` expected with no premium, and `IV (1 − sqrt(ratio))` the
  vol-point gap. The observed share is tested against it with a one-sided stationary block bootstrap
  of the rescaled ratios that re-estimates the null share in every draw. Variation of the premium
  across periods widens the observed ratios and raises the null share, so the test errs on the
  conservative side. Short-vol proxies: a vol swap, `IV − RV`, and a variance swap per unit of
  vega, `(IV² − RV²) / (2 IV)`. Each proxy's Sharpe ratio is tested for being positive with the
  standard error of Mertens (2002) and Opdyke (2007), which allows for the skewness and kurtosis of
  i.i.d. non-normal returns, `sqrt((1 − skew·SR + (kurt − 1)/4 · SR²) / n)` with SR per period and
  raw kurtosis; the Lo (2002) normal-returns standard error would understate it for these returns.
  The vol-swap proxy's mean has the same Jensen bias, so its test against zero overstates the
  evidence for a premium.
  Timing: short-variance P&L regressed on OVX minus the forecast (Newey-West t), and a one-sided
  Welch t-test of the P&L in periods when that spread is above its trailing median against the rest.

## 12. Live forward record and governance (`backend/vol_record.py`)

- **Recording.** One entry per session (`as_of`, the last completed session the forecast uses),
  written by the Refresh workflow's build job; the first issuance recorded for a session stands,
  and a forecast built from stale data is refused. Nothing recorded is changed or deleted; settling
  an entry only adds its outcome.
- **Window.** A forecast counts as issued at the later of its own `issued_at` and the moment it was
  recorded, plus one hour (`COMMIT_MARGIN`). Its window opens at the first CL settlement (14:30 ET)
  after that moment, session `k0` (`base_session`), and it is scored on the realized vol of the five
  settlement-to-settlement returns ending at `k0+1 .. k0+5`. That is one session or more later than
  the backtest's window, because the forecast is published hours after the settlement it uses.
- **Seal.** The publish job, which holds the write token, runs `vol_record.py --seal` immediately
  before it commits the record and pushes it to the `live-data` branch. The seal stamps `sealed_at`
  on every entry not yet sealed. Seal, commit and push run in one workflow step that GitHub stops
  after four minutes, inside `SEAL_MARGIN` (five minutes). An entry whose window-opening settlement
  is not later than `sealed_at + SEAL_MARGIN` may have been pushed after that settlement, so it is
  marked `late_commit` (with `closed_at`) and never scored. An entry still unsealed when its window
  is about to open was never pushed by the seal step (for example, written by hand) and is closed as
  `late_commit` too. Only entries sealed in time can be resolved, skipped or expired.
- **Evidence of timing.** A git commit's author and committer dates are set by whoever makes the
  commit, so they are not evidence. GitHub's own records are: the Actions run log of the publish
  job and the push event on `live-data`, both timestamped by GitHub (and kept for a limited period).
- **Resolution.** An entry is resolved once the closes of `k0 .. k0+5` are all available as
  finished sessions. A window that spans a CL contract roll is `skipped_roll` and never scored; an
  entry whose closes are still missing ten sessions after `k0+5` is `expired`.
- **Scoring.** QLIKE for the model of record and, on the entries where it was published, the
  challenger; 80% interval coverage; direction (reported beside its noise floor). The summary's
  model-of-record statistics (`champion`, with its count `n`) cover the pinned model version only,
  so a plain-HAR fallback forecast is counted but not pooled with them, and its challenger
  comparison (`challenger`) covers the entries of the pinned pair. Overlapping windows are reported,
  but `n_independent` counts only the resolved entries whose windows do not overlap (greedy in time
  order). The summary's `next_result_due` is the earliest window end
  (`target_end`) among pending entries whose windows do not span a roll, or null: the earliest date
  a pending entry can be scored.
- **Promotion.** One test, run once, on a fixed sample (`vol_record.promotion_test`), pinned to
  one pair: the model of record at version `vol_record.PROMOTION_MODEL_VERSION` against the
  challenger `vol_record.PROMOTION_CHALLENGER`. An entry of another model version, with another
  challenger or with no challenger forecast is recorded and counted by reason
  (`promotion.n_excluded_other_version`, `n_excluded_other_challenger`, `n_excluded_no_challenger`)
  but never enters the sample, so it can neither hold the sample back nor restart it; a different
  pair is a new test, declared by changing the two constants, and a unit test fails if the live
  model version or the lab's challenger changes without that declaration. In record order, take
  the resolved entries of the pinned pair, keep those whose windows do not overlap, and stop at the
  first entry of the pair still pending (it may still resolve, and it precedes everything after
  it). The first `PROMOTION_WEEKS` (52) entries selected are the sample. On it, a one-sided Diebold-Mariano test of
  the QLIKE differences, model of record minus challenger: the mean difference over its standard
  error from the sample variance (n − 1 denominator, no autocovariance terms, because the windows do
  not overlap; for one-step forecasts this equals the Harvey-Leybourne-Newbold statistic). As
  Harvey, Leybourne and Newbold (1997) recommend, the p-value is from Student's t with n − 1 = 51
  degrees of freedom (`vol_record.student_t_sf`, a closed-form series in the standard library), not
  the standard normal: the normal's 10% critical value, 1.2816, is below t(51)'s 1.2984, and
  rejecting above it would have a size of about 10.3%. The verdict is `promote` if the mean
  difference is positive and p < `PROMOTION_ALPHA` (0.10), else `keep`; it is `accruing` until the
  sample is full, and entries added later never change it. Applying a `promote` verdict, which swaps
  the models in code, is a maintainer's change. The rule's text is `vol_record.PROMOTION_RULE`, fixed
  before the first live forecast and published with every record summary, together with the test's
  pair, status, sample size, statistic, p-value, mean difference and excluded counts. Its power at
  the challenger's historical edge is computed in the full results (section 3) from the noncentral t with
  the same 51 degrees of freedom.

## 13. The retracted direction model

A six-model ensemble forecast one-week price direction. Its backtest trained, at every step, on rows
whose five-day labels matured after the prediction date; purging them (López de Prado 2018) turned
an annualized Sharpe of 2.47 after costs into −0.98 (`backend/backtest_walk_forward.py`: a
378-bar rolling training window by default, a decision every 5 rows; `--no-purge` reproduces the
leak). The runs are committed as `data/wf_*.json`, records of runs on downloaded data; the unpurged
ones are named `*_LEAKED.json`.

## 14. Leak anatomy (`backend/leak_anatomy.py`)

Why the leak manufactures skill on data with no signal, three ways, offline and deterministic:

- **Analytic.** For jointly normal labels with correlation ρ, P(same sign) = ½ + arcsin(ρ)/π
  (Sheppard 1899). The newest leaked training label shares four of five daily returns with the test
  label, ρ = 0.8; the table by training row goes back to *t* − 5, the first label known at the
  decision. Checked by Monte Carlo on Gaussian and Student-t(3) i.i.d. returns.
- **Simulation.** The retracted backtest's design (rolling 378-row window, 200-row minimum, a
  decision every 5 sessions, sign of the 5-session forward log return as the label, features: the
  log price and its 5-, 10- and 20-session moving averages, standardized on the training window)
  with three scikit-learn classifiers (1-nearest neighbour, a fully grown decision tree, a 20-tree
  random forest), with and without the purge. Data: i.i.d. normal returns (independent seeds); the
  `CL=F` returns from the pinned snapshot, permuted in 20-session blocks; and the real, unshuffled
  `CL=F` returns over the retracted model's 5-year window. For the nearest-neighbour model the
  artifact records how often the nearest training row is the newest one, and the accuracy on those
  decisions: the newest rows are the test row's nearest neighbours in feature space because price
  levels and moving averages barely move in a day.
- **Caveats recorded with the results.** Sharpe ratios are gross (direction times the 5-session log
  return, no costs); the retracted 2.47 is after costs. The block-shuffled runs permute the same
  returns, so they are not independent, and a test that pools their decisions would overstate the
  evidence. Real 5-session returns rise more often than they fall, so each run is also tested
  against its own base rate `max(p, 1 − p)` (one-sided exact binomial), not only against 50%. The
  ledger's rows on the block-shuffled runs use those per-run tests, not the pooled one (section 17).

## 15. Term-structure carry (`backend/carry_signal_test.py`)

- **Returns.** EIA's contract-1 and contract-2 settlements (RCLC1, RCLC2), committed in
  `data/eia_wti_futures_curve.json` because EIA stopped the series in April 2024. Contract cycles
  follow the CME last-trade rule on the exchange calendar. Each month, the second-nearby contract is
  held from the day after an expiry to the day before the next, so no roll gap enters a return.
- **Signal and rule** (fixed in advance). Carry = (C1 − C2) / C2 on the first trading day after
  each expiry; long when carry is above the median of all prior months' carry, flat otherwise.
- **Tests.** The return spread above vs at-or-below the median (one-sided Welch t-test); the
  Spearman rank correlation of carry with the next cycle's return (two-sided); the sign split,
  carry > 0 vs ≤ 0 (one-sided Welch); direction accuracy against the majority-class base rate
  (one-sided binomial); the
  timed-minus-buy-and-hold Sharpe difference, by the Ledoit and Wolf (2008) delta method on a
  Newey-West covariance and by a stationary block bootstrap (Politis and Romano 1994). Each on the
  full sample, before 2007 and from 2007.
- **Power and change.** The post-2007 sample's minimum detectable spread and rank correlation at
  80% power, and its power against the pre-2007 effect, each at the level its test is run
  (one-sided for the spread, two-sided for the rank correlation, via Fisher's z). The change between
  periods: a z-test on the spread difference and on the difference of Fisher-transformed rank
  correlations.

## 16. Supply-shock event study (`backend/supply_shock_playbook.py`)

- **Events and paths.** A hand-curated list of dated supply events, each with groups (supply
  removed above 0.5 mbpd, threat only, strait risk, Iran-driven, OPEC cut, weather, conflict,
  sanctions). Day 0 is the first session on or after the event date. On EIA's daily Cushing spot
  price (`data/eia_wti_spot_daily.json`), **peak** is the highest close over sessions 0–19 and
  **settle** the close at session 10, both relative to the mean close of the 5 sessions before
  day 0.
- **Placebo.** The same statistics on every non-event session between the first and last event,
  excluding sessions within 19 of an event. A 20-session maximum is positive on most dates, so a
  group's median peak means something only against this reference.
- **Tests.** Two-sided resampling tests of the group median against groups of random placebo dates
  of the same size, drawn from the whole span (unmatched) and from each event's own calendar year
  (year-matched, which controls for the volatility regime); 20,000 draws, fixed seed. The ledger
  counts a group as supported only when both are below 5% (it takes the larger p-value). Also a
  rank test against all placebo dates (approximate: placebo paths overlap), bootstrap CIs of the
  group median, a permutation test of supply removal against threat-only, and a test of whether a
  strong day-0 move changes the further rise.
- **Caveats recorded with the results.** Event dates and groups were chosen with hindsight; the
  groups overlap; event pairs fewer than 25 sessions apart share sessions and are listed, so group
  members are not all independent; the study's p-values are not adjusted for the groups tested
  (the ledger adjusts them).

## 17. Research ledger (`backend/research_ledger.py`)

One row per tested hypothesis, stated as the claim the project made or examined. Every statistic
and p-value is read from a committed artifact by its JSON field path, or computed by a standard
formula from fields read the same way (an exact binomial test from a stored accuracy and sample
size, a normal p-value from a stored z, the larger of two stored p-values when a claim needs both);
nothing is typed in by hand, and the unit suite rebuilds the ledger and requires it to equal the
committed file. Runs that reuse the same data are not pooled: the leak anatomy's block-shuffled
`CL=F` rows claim that every run beats its own base rate and take the largest of the per-run
p-values, an intersection-union test (Berger 1982) whose level holds however the runs depend on one
another. That test replaced a pooled one after the runs were seen, so those rows are not marked
pre-specified.

- **Status**, from the test as run: supported (p < 0.05 in the hypothesis's direction),
  inconclusive (0.05 ≤ p < 0.10), not supported (p ≥ 0.10, or the estimate points the other way),
  or retracted (the evidence was invalid when produced: the leaked walk-forward, roll-contaminated
  returns, or a test whose null was wrong). Not supported is a failure to reject the null, not
  evidence against the hypothesis. Where the artifact records the test's power, `underpowered` is
  true when it is below the artifact's target power (null where no power figure exists). Retracted
  rows keep their p-values and stay in the multiplicity count, but are never counted as survivors.
- **Multiplicity**, across the rows with a p-value, on the two-sided p (a one-sided p doubled,
  capped at 1): Benjamini-Hochberg (1995) q-values, Holm (1979) adjusted p-values, and the
  Harvey, Liu and Zhu (2016) hurdle t > 3 on the normal quantile of that p. A row marked
  `selected_after_search` tests a model picked as the best of a field on the same sample (the
  challenger against the model of record): its pairwise p-value ignores the search, so it is left
  out of the family, its adjusted fields are null, and its note names the search-adjusted test of
  the same question (the SPA row).
- `pre_specified` is true only where the code documents the test as fixed before the data were
  seen; most of the project was exploratory.

## References

- Alizadeh, S., Brandt, M., Diebold, F. (2002). Range-based estimation of stochastic volatility models. *Journal of Finance* 57(3).
- Benjamini, Y., Hochberg, Y. (1995). Controlling the false discovery rate: a practical and powerful approach to multiple testing. *Journal of the Royal Statistical Society B* 57(1).
- Berger, R. L. (1982). Multiparameter hypothesis testing and acceptance sampling. *Technometrics* 24(4).
- Bollerslev, T., Hood, B., Huss, J., Pedersen, L. H. (2018). Risk everywhere: modeling and managing volatility. *Review of Financial Studies* 31(7).
- Christoffersen, P. (1998). Evaluating interval forecasts. *International Economic Review* 39(4).
- Corsi, F. (2009). A simple approximate long-memory model of realized volatility. *Journal of Financial Econometrics* 7(2).
- Diebold, F., Mariano, R. (1995). Comparing predictive accuracy. *Journal of Business & Economic Statistics* 13(3).
- Duan, N. (1983). Smearing estimate: a nonparametric retransformation method. *JASA* 78(383).
- Engle, R., Manganelli, S. (2004). CAViaR: conditional autoregressive value at risk by regression quantiles. *JBES* 22(4).
- Gebru, T. et al. (2021). Datasheets for datasets. *Communications of the ACM* 64(12).
- Gibbs, I., Candès, E. (2021). Adaptive conformal inference under distribution shift. *NeurIPS*.
- Glosten, L., Jagannathan, R., Runkle, D. (1993). On the relation between the expected value and the volatility of the nominal excess return on stocks. *Journal of Finance* 48(5).
- Gneiting, T., Raftery, A. (2007). Strictly proper scoring rules, prediction, and estimation. *JASA* 102(477).
- Hansen, P. R. (2005). A test for superior predictive ability. *JBES* 23(4).
- Hansen, P. R., Lunde, A., Nason, J. (2011). The model confidence set. *Econometrica* 79(2).
- Harvey, C., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M., Van Hemert, O. (2018). The impact of volatility targeting. *Journal of Portfolio Management* 45(1).
- Harvey, C., Liu, Y., Zhu, H. (2016). … and the cross-section of expected returns. *Review of Financial Studies* 29(1).
- Harvey, D., Leybourne, S., Newbold, P. (1997). Testing the equality of prediction mean squared errors. *International Journal of Forecasting* 13(2).
- Holm, S. (1979). A simple sequentially rejective multiple test procedure. *Scandinavian Journal of Statistics* 6(2).
- Kupiec, P. (1995). Techniques for verifying the accuracy of risk measurement models. *Journal of Derivatives* 3(2).
- Ledoit, O., Wolf, M. (2008). Robust performance hypothesis testing with the Sharpe ratio. *Journal of Empirical Finance* 15(5).
- Lo, A. (2002). The statistics of Sharpe ratios. *Financial Analysts Journal* 58(4).
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
- Lyócsa, Š., Molnár, P., Výrost, T. (2021). Stock market volatility forecasting: do we need high-frequency data? *International Journal of Forecasting* 37(3).
- Mertens, E. (2002). Comments on variance of the IID estimator in Lo (2002). Working paper, University of Basel.
- Mincer, J., Zarnowitz, V. (1969). The evaluation of economic forecasts. NBER.
- Mitchell, M. et al. (2019). Model cards for model reporting. *FAT\**.
- Newey, W., West, K. (1987). A simple, positive semi-definite, heteroskedasticity and autocorrelation consistent covariance matrix. *Econometrica* 55(3).
- Opdyke, J. D. (2007). Comparing Sharpe ratios: so where are the p-values? *Journal of Asset Management* 8(5).
- Patton, A. (2011). Volatility forecast comparison using imperfect volatility proxies. *Journal of Econometrics* 160(1).
- Patton, A., Sheppard, K. (2015). Good volatility, bad volatility: signed jumps and the persistence of volatility. *Review of Economics and Statistics* 97(3).
- Politis, D., Romano, J. (1994). The stationary bootstrap. *JASA* 89(428).
- Rogers, L. C. G., Satchell, S. (1991). Estimating variance from high, low and closing prices. *Annals of Applied Probability* 1(4).
- Sheppard, W. F. (1899). On the application of the theory of error to cases of normal distribution and normal correlation. *Philosophical Transactions of the Royal Society A* 192.
- Yang, D., Zhang, Q. (2000). Drift-independent volatility estimation based on high, low, open, and close prices. *Journal of Business* 73(3).
