# Changelog

All notable changes. Research results are versioned like code: when a published number was wrong,
the correction is listed under **Errata** with the old and the new value, and the artifact that
backs the new one.

## 2.0.0 — 2026-10-20

### Research

- **Benchmark field and loss-based evaluation** (`backend/vol_lab.py`, `data/vol_lab.json`): 14
  models on the production walk-forward, every out-of-sample day plus each 5-session sampling grid;
  QLIKE, MSE and realized utility on variance-consistent forecasts; Model Confidence Set, Hansen SPA,
  Diebold-Mariano, Mincer-Zarnowitz; weekly VaR backtests; delayed-feedback adaptive conformal
  intervals; a range-based second proxy; the direction noise floor; coefficient paths.
- **Reproducibility.** A hash-pinned snapshot of the Yahoo and FRED data (`data/market/`), a test
  that rebuilds `data/vol_lab.json` from it, and a CI workflow (Reproduce) that runs that test under
  a pinned numeric environment on changes to the lab's inputs and every week. A rebuild matches when
  every model set, flag, label and count is identical and every number is within 1e-4 relative or
  within one unit of the last decimal it is published with; the GARCH-family refits converge to a
  1e-9 tolerance, so BLAS thread counts and CPU kernels do not move published figures. The unit
  suite rebuilds the carry and supply-shock studies from committed EIA files, the research ledger,
  and a subset of the leak anatomy's runs. The production validation
  (`data/vol_forecast_validation.json`) and the direction post-mortem (`data/wf_*.json`) are records
  of runs on downloaded data and are not regenerated in CI.
- **Model of record v2** (`har-iv/2`): OVX lagged one session (entry-time clean); losses, risk and
  the published forecast use the variance-consistent mean (Duan smearing); the median decides the
  direction call; a model-implied probability of rising vol; an adaptive conformal 80% interval; an
  implied-vol-only comparator and a same-day-OVX robustness row inside the production validation.
- **Challenger** (`har_iv_range`, chosen by rule: the lowest-QLIKE feasible model in the Model
  Confidence Set other than the model of record) forecast live alongside the model of record, with a
  promotion test fixed before any live result exists.
- **Live forward record** (`backend/vol_record.py`): one entry per session, sealed by the publish
  job immediately before it is committed and pushed to `live-data`, and scored on a window that
  opens at least an hour after the forecast was issued and recorded. An entry not sealed more than
  five minutes before the settlement that opens its window is `late_commit` and never scored.
- **Economic evaluation** judged on risk control (realized vol vs target, tails, drawdown) with
  bootstrap Sharpe-difference tests; the variance risk premium in variance units with a HAC CI, and
  a non-normal test of whether short-volatility Sharpe ratios are positive.
- **Secondary studies**: a placebo null for the supply-shock event study, power and Sharpe-difference
  tests for carry, a leak-anatomy demonstration, and a research ledger of every hypothesis tested
  with Benjamini-Hochberg and Holm adjustments.

### Corrections made in review, before release

- **Range features at the settlement.** Session *t*'s high and low run to 17:00 ET, after the 14:30 ET
  decision, so the range-based features read part of the first target return. They now use the full
  bars of earlier sessions and, for session *t*, only the overnight and open-to-settlement returns
  (`har_range`, the challenger `har_iv_range`, LightGBM). The look-ahead test also widens day *t*'s
  post-settlement range.
- **Model Confidence Set on feasible models only.** The same-day-OVX variant uses information
  published after the decision. It is now a timing control: its losses and Diebold-Mariano tests are
  reported, and it is excluded from every confidence set (its MCS fields are null), the SPA test,
  the out-of-sample selection and the choice of challenger. The model of record is outside the
  full-sample 90% set.
- **Ledger.** The short vol-swap Sharpe row uses the Mertens (2002) / Opdyke (2007) non-normal test
  and is inconclusive; supply-shock rows need both the unmatched and the year-matched placebo (the
  larger p-value), so settle after supply removal and the OPEC-cut peak are no longer supported;
  "HAR-IV beats the mean-reversion rule on direction" is retracted (with nothing to forecast HAR
  already beats that rule); per-family counts.
- **Live record.** The seal and the `late_commit` status replace the claim that each forecast is
  committed before the data that will score it: the window was anchored on the build job's record
  time and nothing checked the commit.
- **Promotion.** The rule is now implemented as a single fixed-sample test
  (`vol_record.promotion_test`) whose status and statistics the record summary publishes.
- **Carry.** The rank-correlation power uses the two-sided level of the test it describes.
- **Leak anatomy.** The block-shuffled `CL=F` runs permute the same returns and are not
  independent, and their base rate is not 50%: both are stated, with per-run tests against each
  run's own base rate; its Sharpe ratios are labelled gross (the retracted 2.47 is after costs).
- **Supply study.** Event pairs fewer than 25 sessions apart are listed as overlapping, a stated
  caveat.
- **Dashboard.** Headlines are conditional on the data, labelled by sample and compared with the
  strongest simple baseline (the OVX-only regression, trailing-vol targeting); the direction
  probability is demoted and labelled model-implied and uncalibrated.
- **Documentation.** Every sentence in the README that states a number or a verdict is generated
  from the artifacts; reproducibility is described artifact by artifact; "weekday grids" are
  5-session sampling grids (each grid's dates fall on every weekday).

### Pipeline and dashboard

- The retracted direction model no longer runs in production; the deploy is gated on the volatility
  forecast instead (the freeze went from about 115 s to about 15 s).
- `data.json` v3 and a separate `research.json` built only from committed artifacts
  ([docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md)).
- NewsAPI and the other optional providers are no longer called (the free NewsAPI plan is not
  licensed for production); the only secret the workflows use is the EIA key.
- The direction model's live record and e-mail alert are retired.
- The dashboard is rebuilt as a research terminal around the validated forecaster.

### Errata (published numbers that were wrong)

| Claim as published | Correction | Evidence |
|---|---|---|
| Vol forecaster: "about 73% next-week vol-direction accuracy vs a 52% base rate (z ≈ 12, HAC z ≈ 16)" was the headline evidence of skill | Not evidence of skill: with nothing to forecast (i.i.d. returns) the same pipeline, run as HAR without OVX, scores about 75%, because the five-session realized vol it compares against is itself noisy. Skill is now judged by losses, where the model beats persistence, GARCH, EWMA, HAR-RV and LightGBM but is statistically tied with implied volatility alone | `data/vol_lab.json` `noise_floor`, `leaderboard` |
| Vol forecaster QLIKE 0.422 | That scored the median forecast against a variance loss; the variance-consistent forecast (and lagged OVX) gives the figures in the README's results table | `data/vol_forecast_validation.json` `har_qlike`, `har_qlike_median_forecast` |
| "OVX at day t is known at t (no leak)" | OVX settles at 16:15 ET, about 1h45m after the CL settlement that opens the target window; the same-day print is measurably more informative, so version 2 uses the previous session's OVX | `vol_lab.json` model `har_iv_sameday`; `validation.robustness_same_day_ovx` |
| "Outscores the same model without OVX (z 2.5)" on direction | With lagged OVX the direction gain is not significant; the OVX gain is clear on QLIKE | `data/research_ledger.json` rows `vol_ovx_gain_*` |
| Supply shocks: "threat-only events peak +4.0%, settle +0.5%", read as the market reacting to headlines | A random ordinary date peaks higher under the same path statistic; threat-only events are indistinguishable from it. Announced or physical supply removal does peak more than the placebo | `data/supply_shock_study.json` |
| Carry: "faded to nothing measurable since 2007" | The post-2007 spread test is underpowered and the change in spread is not significant, while the rank correlation did fall; see the README's carry section for the figures. Columns relabelled: the split is on the prior-months median carry, not backwardation vs contango | `data/carry_signal_test.json` |
| Earlier corrections (1.x): the direction model's Sharpe 2.44/2.47 (a look-ahead leak; purged −0.98), the roll-biased carry test, the supply-shock grouping | See the README post-mortem and the 1.x history in git | `data/wf_*`, `data/leak_anatomy.json` |

## 1.x — 2026-06 to 2026-10

The direction-model post-mortem and retraction, the leak-free walk-forward engine, the first HAR-IV
forecaster, the carry and supply-shock studies, the contract calendar and the least-privilege deploy.
See the git history before this release.
