# Changelog

All notable changes. Research results are versioned like code: when a published number was wrong,
the correction is listed under **Errata** with the old and the new value, and the artifact that
backs the new one.

## 2.0.0 — 2026-10-20

### Research

- **Benchmark field and loss-based evaluation** (`backend/vol_lab.py`, `data/vol_lab.json`): 14
  models on the production walk-forward, every out-of-sample day plus each weekday grid; QLIKE, MSE
  and realized utility on variance-consistent forecasts; Model Confidence Set, Hansen SPA,
  Diebold-Mariano, Mincer-Zarnowitz; weekly VaR backtests; delayed-feedback adaptive conformal
  intervals; a range-based second proxy; the direction noise floor; coefficient paths.
- **Reproducibility.** A hash-pinned data snapshot (`data/market/`), a bit-for-bit reproduction test,
  and a CI workflow that re-runs it.
- **Model of record v2** (`har-iv/2`): OVX lagged one session (entry-time clean); losses, risk and
  the published forecast use the variance-consistent mean (Duan smearing); the median decides the
  direction call; a probability of rising vol; an adaptive conformal 80% interval; an implied-vol-only
  comparator and a same-day-OVX robustness row inside the production validation.
- **Challenger** (`har_iv_range`, chosen by the MCS) forecast live alongside the model of record, with
  a promotion rule fixed before any live result exists.
- **Live forward record** (`backend/vol_record.py`): one git-timestamped entry per session, scored on
  a window that starts after the entry is committed.
- **Economic evaluation** judged on risk control (realized vol vs target, tails, drawdown) with
  bootstrap Sharpe-difference tests; the variance risk premium in variance units with a HAC CI.
- **Secondary studies**: a placebo null for the supply-shock event study, power and Sharpe-difference
  tests for carry, a leak-anatomy demonstration, and a research ledger of every hypothesis tested
  with Benjamini-Hochberg and Holm adjustments.

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
| Vol forecaster: "about 73% next-week vol-direction accuracy vs a 52% base rate (z ≈ 12, HAC z ≈ 16)" was the headline evidence of skill | Not evidence of skill: with nothing to forecast (i.i.d. returns) the same pipeline scores about 75%, because the five-session realized vol it compares against is itself noisy. Skill is now judged by losses, where the model beats persistence, GARCH, EWMA, HAR-RV and LightGBM but is statistically tied with implied volatility alone | `data/vol_lab.json` `noise_floor`, `leaderboard` |
| Vol forecaster QLIKE 0.422 | That scored the median forecast against a variance loss; the variance-consistent forecast (and lagged OVX) gives the figures in the README's results table | `data/vol_forecast_validation.json` `har_qlike`, `har_qlike_median_forecast` |
| "OVX at day t is known at t (no leak)" | OVX settles at 16:15 ET, about 1h45m after the CL settlement that opens the target window; the same-day print is measurably more informative, so version 2 uses the previous session's OVX | `vol_lab.json` model `har_iv_sameday`; `validation.robustness_same_day_ovx` |
| "Outscores the same model without OVX (z 2.5)" on direction | With lagged OVX the direction gain is not significant; the OVX gain is clear on QLIKE | `data/research_ledger.json` rows `vol_ovx_gain_*` |
| Supply shocks: "threat-only events peak +4.0%, settle +0.5%", read as the market reacting to headlines | A random ordinary date peaks +5.4% under the same path statistic; threat-only events are indistinguishable from it. Announced or physical supply removal does peak more than the placebo | `data/supply_shock_study.json` |
| Carry: "faded to nothing measurable since 2007" | The post-2007 spread test has 67% power and the change in spread is not significant (p = 0.26); the rank correlation did fall (p = 0.02). Columns relabelled: the split is on the prior-months median carry, not backwardation vs contango | `data/carry_signal_test.json` |
| Earlier corrections (1.x): the direction model's Sharpe 2.44/2.47 (a look-ahead leak; purged −0.98), the roll-biased carry test, the supply-shock grouping | See the README post-mortem and the 1.x history in git | `data/wf_*`, `data/leak_anatomy.json` |

## 1.x — 2026-06 to 2026-10

The direction-model post-mortem and retraction, the leak-free walk-forward engine, the first HAR-IV
forecaster, the carry and supply-shock studies, the contract calendar and the least-privilege deploy.
See the git history before this release.
