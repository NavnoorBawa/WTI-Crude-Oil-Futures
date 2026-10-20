# Methodology

How every number in the project is produced. Code references point to the implementation;
results are in the [README](../README.md) and the committed artifacts.

## 1. Data and timing

- **Prices.** Yahoo `CL=F`, the continuous front-month NYMEX WTI future, unadjusted. Its daily
  close equals EIA's contract-1 settlement on almost every day. Only finished sessions are used
  (`backend/contract_calendar.py`: a CME session runs 18:00 ET to 17:00 ET; settlement is 14:30 ET).
- **Implied volatility.** OVX, the CBOE Crude Oil ETF Volatility Index: a 30-calendar-day implied
  vol computed from options on the USO ETF (not on CL futures), settled at 16:15 ET.
- **Entry-time rule.** A forecast for session *t* may use only information available at *t*'s 14:30 ET
  settlement, which is where the target window begins. OVX's 16:15 close comes later, so the model
  uses the previous session's OVX. (Version 1 used the same-day close; the lab keeps that variant
  as a disclosed robustness row, and it is measurably better, which confirms the overlap carried
  information.)
- **Snapshot.** Research runs on `data/market/` (SHA-256-pinned; see [DATASHEET.md](DATASHEET.md)).

## 2. Target and features

- **Target.** Realized vol of the next five settlement-to-settlement log returns,
  `RV(t+1..t+5) = sqrt(252/5 · Σ r²)`, annualized.
- **Close-to-close features.** `RV5, RV22, RV66`: realized vol over the last 5, 22 and 66 sessions
  (the daily, weekly and monthly components of HAR; Corsi 2009).
- **Range-based features.** Per session, Rogers-Satchell variance
  `ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O)` plus the squared overnight return `ln(O/C_prev)²` (the
  open-to-close and close-to-open parts of a Yang-Zhang estimator; Rogers and Satchell 1991, Yang and
  Zhang 2000). Range estimators are several times more efficient than squared returns (Alizadeh,
  Brandt and Diebold 2002; Lyócsa, Molnár and Výrost 2021). On roll days the overnight gap is the
  calendar spread, so it is replaced by the trailing median overnight move.
- **Downside semivolatility** over five sessions (Patton and Sheppard 2015), for the leverage variant.

## 3. Models (`backend/vol_lab.py`)

| Key | Model |
|---|---|
| `persistence` | next week's vol = the last five sessions' realized vol |
| `raw_ovx` | OVX itself (previous session) |
| `ewma` | RiskMetrics, λ = 0.94, on all returns since 2000 |
| `garch`, `gjr` | GARCH(1,1) and GJR-GARCH(1,1) with Student-t errors (`arch`), re-estimated weekly, filtered daily |
| `har_rv`, `har_rs` | log-HAR on RV5/22/66; plus downside semivol |
| `har_range` | log-HAR on the range-based measure |
| `ovx_only` | log regression on OVX alone |
| `har_iv` | **model of record**: log-HAR on RV5/22/66 plus lagged OVX |
| `har_iv_sameday` | HAR-IV with the same-day OVX (version-1 timing; robustness only) |
| `har_iv_range` | HAR-IV on the range-based measure |
| `lgbm` | gradient-boosted trees on all features (LightGBM), retrained quarterly |
| `combo` | equal-weight pool of the variances of `har_iv`, `gjr`, `ovx_only` |

## 4. Walk-forward and purge

Expanding window; the first forecast after 250 training rows. A training row's target covers the
five sessions after it, so at test row *t* the last four rows' targets overlap the test target:
they are **purged** (only rows `< t − 4` are used). Time-series models use returns up to and
including *t*. `tests/test_vol_lab.py` proves the no-look-ahead property for every model by
replacing everything after each test day with different data and checking the forecast is
unchanged, and checks the test fails when one extra day leaks.

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
- **Second proxy.** QLIKE against the range-based realized vol of the target week, rescaled to the
  close-to-close level, as a robustness check on the proxy itself.

## 7. Inference

- **Sampling.** Forecasts are made every out-of-sample day; consecutive targets overlap by up to
  four sessions. Pooled statistics use Newey-West long-run variances (automatic bandwidth, at least
  the overlap). Because results computed on one weekday grid can differ from the next, every
  comparison is also run on each of the five non-overlapping weekday grids and the report says on
  how many it holds.
- **Diebold-Mariano** (1995) on loss differentials with the Harvey-Leybourne-Newbold (1997)
  small-sample factor and Student-t reference.
- **Model Confidence Set** (Hansen, Lunde and Nason 2011; `arch.bootstrap.MCS`, range statistic,
  stationary bootstrap, block 20 daily / 10 weekly, 5,000 replications, fixed seed): the set of
  models not significantly worse than the best at 90% confidence.
- **SPA** (Hansen 2005; `arch.bootstrap.SPA`): the probability, accounting for the whole field
  searched, that no model beats the model of record.
- **Mincer-Zarnowitz**: regress realized variance on forecast variance; an unbiased forecast has
  intercept 0 and slope 1 (HAC Wald test).
- **Coefficients** of the full-sample HAR-IV with Newey-West t-statistics (10 lags, overlapping
  targets), and their walk-forward path.

## 8. Direction accuracy and its noise floor

Direction = will next week's RV exceed this week's RV5? Both are five-observation estimates. For a
continuous i.i.d. series, calling "up" exactly when the current value is below its median is right
with probability `E[max(U, 1−U)] = 3/4` (U uniform), because the next value is independent. So a
model with **no** forecasting skill scores about 75% through this pipeline. The lab simulates it
(i.i.d. Student-t(5) returns with the sample's volatility, the identical feature builder and
walk-forward). Direction accuracy is therefore reported only next to this floor and never as
evidence of skill; skill is judged by the losses above.

## 9. Intervals

Adaptive conformal inference (Gibbs and Candès 2021) on the log forecast errors of the median:
the interval at step *i* is the `[a_i/2, 1 − a_i/2]` quantile range of the previous errors, and
`a_i` moves by `γ(α − miss)` so long-run coverage converges to `1 − α` even when the error
distribution drifts. With daily forecasts an outcome is only observed five sessions later, so the
lab uses the **delayed-feedback** version: the interval at *i* uses only errors and misses of rows
`≤ i − 5`. Intervals are also scored with the interval score (Gneiting and Raftery 2007).

## 10. Value-at-Risk backtests

Weekly (five-session) VaR of a long front-month position, `VaR = −q · σ̂₅`, with `q` the normal
quantile or (filtered historical simulation) the empirical quantile of past standardized outcomes
`r₅/σ̂₅`; unconditional historical simulation (no vol model) is the benchmark. Tests, on each weekday
grid (non-overlapping outcomes): Kupiec (1995) unconditional coverage, Christoffersen (1998)
independence and conditional coverage, Engle and Manganelli (2004) dynamic quantile, and the Basel
traffic light on the last 250 weeks.

## 11. Economic evaluation (`vol_forecast._economic_tests`)

- **Same-contract returns.** Roll days are rebuilt from EIA's contract-2 settlements (through April
  2024, when EIA stopped publishing), so no P&L books the calendar spread.
- **Vol-target overlay.** Weekly long position `min(2, 35% / forecast)`, 5 bp per unit of turnover,
  against buy-and-hold and trailing-22-day sizing. Judged on what vol targeting is for (Harvey et al.
  2018): realized vol against the target, vol of vol, kurtosis, worst week, 97.5% expected shortfall
  and drawdown; Sharpe differences with a stationary-bootstrap p-value (Ledoit and Wolf 2008).
- **Variance risk premium.** OVX against the realized vol of the next 20 sessions, non-overlapping,
  in vol points and in variance points (with a Newey-West CI); short-vol and short-variance proxies;
  timing as a regression of short-variance P&L on OVX minus the forecast.

## 12. Live forward record and governance (`backend/vol_record.py`)

One entry per session, committed to the `live-data` branch by the deploy job. Each forecast is
scored on the first five settlement-to-settlement returns that start at least an hour after it was
issued and recorded, so the git commit precedes every price it is scored on. Windows that span a
contract roll are skipped. **Promotion rule** (set before any live result exists): the challenger
replaces the model of record after at least 52 independent resolved weeks if its live QLIKE is lower
with a one-sided Diebold-Mariano p below 0.10.

## 13. The retracted direction model

A six-model ensemble forecast one-week price direction. Its backtest trained, at every step, on rows
whose five-day labels matured after the prediction date; purging them (López de Prado 2018) turned
an annualized Sharpe of 2.47 into −0.98 (`backend/backtest_walk_forward.py`, `--no-purge` reproduces
the leak). See the post-mortem section of the README and `data/leak_anatomy.json` for why the leak
produces that much apparent skill on a random walk.

## References

- Alizadeh, S., Brandt, M., Diebold, F. (2002). Range-based estimation of stochastic volatility models. *Journal of Finance* 57(3).
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
- Harvey, D., Leybourne, S., Newbold, P. (1997). Testing the equality of prediction mean squared errors. *International Journal of Forecasting* 13(2).
- Kupiec, P. (1995). Techniques for verifying the accuracy of risk measurement models. *Journal of Derivatives* 3(2).
- Ledoit, O., Wolf, M. (2008). Robust performance hypothesis testing with the Sharpe ratio. *Journal of Empirical Finance* 15(5).
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
- Lyócsa, Š., Molnár, P., Výrost, T. (2021). Stock market volatility forecasting: do we need high-frequency data? *International Journal of Forecasting* 37(3).
- Mincer, J., Zarnowitz, V. (1969). The evaluation of economic forecasts. NBER.
- Mitchell, M. et al. (2019). Model cards for model reporting. *FAT\**.
- Newey, W., West, K. (1987). A simple, positive semi-definite, heteroskedasticity and autocorrelation consistent covariance matrix. *Econometrica* 55(3).
- Patton, A. (2011). Volatility forecast comparison using imperfect volatility proxies. *Journal of Econometrics* 160(1).
- Patton, A., Sheppard, K. (2015). Good volatility, bad volatility: signed jumps and the persistence of volatility. *Review of Economics and Statistics* 97(3).
- Rogers, L. C. G., Satchell, S. (1991). Estimating variance from high, low and closing prices. *Annals of Applied Probability* 1(4).
- Yang, D., Zhang, Q. (2000). Drift-independent volatility estimation based on high, low, open, and close prices. *Journal of Business* 73(3).
