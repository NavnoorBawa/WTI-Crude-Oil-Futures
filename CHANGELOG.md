# Changelog

All notable changes. Research results are versioned like code: when a published number was wrong,
the correction is listed under **Errata** with the old and the new value, and the artifact that
backs the new one.

## Unreleased

### Research

- **Hansen's SPA test is now studentized, as Hansen (2005) defines it.** `arch.bootstrap.SPA` 8.0.0
  ignores its `studentize` flag: it maximizes raw mean loss differences, so the noisiest model's
  bootstrap swings set the maximum. `forecast_eval.spa_test` divides each model's mean loss
  difference by its long-run standard deviation (arch's own stationary-bootstrap kernel), on the
  same bootstrap draws, and is checked against arch where arch is right (the variance kernel, and
  the non-studentized p-values to the last digit), against a second, vectorized implementation of the
  studentized p-values on the same draws, on a designed case and on one known answer (T = 0 gives
  p = 1). The artifact keeps the
  old figures as `non_studentized`, and adds the test at blocks of 10 and 40 days and over 2008–2016
  and 2017 onward (`spa_robustness_qlike`). The promotion test, the model of record and the live
  record are unchanged.
- **Wording.** A p-value from 0.05 to 0.10 now reads "inconclusive" everywhere it is generated, as
  the project's rules say, not "significant at 10%". A tie is worded as a tie: the findings no
  longer say most of the skill is the options market's, and they add that HAR-range, from daily
  price ranges without OVX, is statistically tied with the model of record too, so these data do
  not show that OVX is needed to reach its loss. Every sentence that states the SPA rejection
  carries the ledger's Benjamini-Hochberg result. The README's "What it does not claim", the model
  card's caveats (with the 99% VaR's breach counts, which may understate the 1% tail although
  Kupiec's test does not reject) and the limitation on risk use are now
  generated from the artifacts, like the rest of the results.

### Errata (published numbers that were wrong)

| Claim as published | Correction | Evidence |
|---|---|---|
| Hansen SPA, null that no model beats the model of record: p = 0.072 (artifact `p_lower` 0.045, `p_upper` 0.7048), "significant at 10% but not at 5%", so the challenger's edge was "inconclusive" after the search | That was the non-studentized test (White's Reality Check with Hansen's recentring); arch 8.0.0 returns it whatever its `studentize` flag says. The studentized test on the same draws gives p = 0.011 (lower 0.0104, upper 0.012), significant at 5%, with HAR-IV-range the model with the largest studentized gain (t 2.91); 0.005 to 0.027 across block lengths and sub-periods. It does not survive the ledger's Benjamini-Hochberg adjustment (q = 0.090), and the challenger still replaces the model of record only through the pinned 52-week live test | `data/vol_lab.json` `leaderboard.spa_qlike_vs_har_iv` (`p_consistent`, `non_studentized`, `t_stats`) and `leaderboard.spa_robustness_qlike`; `data/research_ledger.json` row `vol_spa_beats_record` |

### Live record

- **Each call stores its fit.** New record entries also store the coefficients and the smearing
  factor that produced the forecast (additive, optional fields; the entries already sealed are
  untouched, and recording, sealing, resolution and scoring never read them). A sealed call can now
  be recomputed from its own entry: median = 100·exp(b0 + Σ bk·ln xk), forecast = median·√smearing.

### Dashboard

- **What moved the call.** When the newest call and the one before it both stored their fit, the
  hero splits the change between them into each input's move (at the previous call's coefficients),
  the refit and the smearing factor, in vol points that add up to the change. It is shown only when
  both calls reproduce from their own recorded fits.

### Documentation and verification

- **A README a reviewer can read in two minutes.** The README (740 lines) becomes a 150-line landing
  page: the answer in four computed findings, each at the strength its test supports, the results
  at a glance, how to check each claim, how the system works and what it does not claim. The full
  write-up moves intact, every generated block included, to `docs/RESULTS.md`, which joins the
  `cog --check` gate.
- **`docs/REVIEWER_GUIDE.md`.** Five checks in about ten minutes, each with its command and what a
  passing run prints.
- **`scripts/verify_record.py`.** Checks the sealed live record from the record file alone: each
  entry's scoring window, the margin between its seal and the settlement that opens the window,
  the roll rule, the realized volatility recomputed from its stored closes, its QLIKE, and the
  forecast recomputed from its stored fit. Standard library only.
- **A link check.** A test resolves every relative link and heading anchor in the README and the docs.

## 2.1.0 — 2026-10-27

A release of the dashboard. No research result changed: the models, the benchmark lab, the ledger
and every committed artifact are as in 2.0.0, and the live record is recorded, sealed and scored as
before.

### Dashboard

- **The answer first.** The page opens on the research question and a one-line answer computed from
  the benchmark lab's Diebold-Mariano tests, with the statistics and the sample behind it. Below the
  tiles, an index lists the computed headlines of FCST, MODL, STAB, CALB, RISK, VRP and LIVE, each
  linked to its section; a test keeps every row equal to its section's heading.
- **This week's call, as recorded.** The hero shows the call the live record holds: its target
  window, when it was recorded and sealed, the change since the previous call, the last calls, the
  80% band with the region outside it, how many past weeks landed inside, below and above the band,
  and the typical miss. A recomputation is labelled not scored, the probability that volatility
  rises is one sentence labelled not calibrated, and the arithmetic is shown only when it reproduces
  the number.
- **Tiles** each pin one sample, with the weekly production figure beside it; a footnote names the
  exceptions (coverage on the days with a band, VaR on weekly grid 0).
- **Sections.** Thirteen, in four groups: evidence, market context, integrity and reference. FCST
  draws the validated band and marks the weeks realized volatility fell outside it, and its table
  gives each week's miss, log error and QLIKE. STAB compares each year with the OVX-only regression.
  MODL shows Model Confidence Set membership with its robustness to the test's settings and the
  sampling grids. CALB, CONE, RISK and VRP have dated marks, reconciled samples, and captions that
  match their tables. LIVE draws the recent recorded calls with their bands and a 52-cell strip for
  the promotion test, counts calls and independent weeks separately, and links the record's commit
  history. LEDG is searchable and linkable. METH holds a model card, the data vintage, and how to
  cite, correct and reuse the work.
- **Reading tools.** Method notes in closed disclosures, which open for print, find-in-page and
  links; glossary notes for QLIKE, MCS, SPA, DM, MZ, Kupiec, Christoffersen, DQ, conformal intervals
  and more; source lines that link the payload files, and reproduce commands that link their
  modules, with copy buttons; a copy-link button on each section; table views of the charts;
  client-side CSV export (RFC 4180); view state in the URL, of which only reserved keys are read;
  section codes grouped in the top bar, tracking the current section; a jump palette (`/` or
  Ctrl/Cmd+K) and keyboard shortcuts (`?`), whose single-key shortcuts can be switched off; a print
  stylesheet.
- **Accessibility and loading.** Chart marks meet 3:1 contrast, the page supports forced colors, the
  canvas charts can be read point by point from the keyboard, and labels with notation (VaR, p) are
  no longer set in capitals. Lazy charts and the footer no longer shift the layout (cumulative
  layout shift was 0.20 to 0.26); `data.json` and the fonts are preloaded.
- **Errors.** A missing `research.json` shows a notice with Retry, and the sections that need it say
  so; an invalid `data.json` gets a specific error.

### Fixed

- The forecast and VaR charts format their time-axis labels in en-US, like the rest of the page.
  They used the visitor's `navigator.language`, which threw on a POSIX locale tag (`en-US@posix`)
  and left both charts blank. The price chart, which formats its own labels, carries the same
  locale.
- **The newest recorded call.** The hero matched calls on the snapshot's as-of date, so the Oct 26
  refresh, which got data only through Oct 23, showed the Oct 23 call as this week's call while the
  record already held one from the Oct 26 close. When the newest recorded call of the same model
  version is from a later close than the snapshot, the hero, the forecast chart's live point, CONE
  and METH now show that call, with a note that the snapshot is older; a call of another model
  version is never substituted.
- **One count per quantity.** RISK's 95% breach count was 46 in the caption (Kupiec) and 45 in the
  chart summary (rounded series). The caption gives the Kupiec counts and, when rounding makes them
  differ, the drawn counts and why. SPA and Mincer-Zarnowitz p-values, ledger t-statistics, counts,
  %/month spreads and supply-study p-values each keep one precision across sections. The RISK title
  names the VaR method (filtered historical simulation), and the model card names the sample of its
  direction accuracy.
- **Review findings.** A review of the live page through three lenses (layout at seven widths,
  interaction and accessibility, data states) confirmed 41 findings, each reproduced by an
  independent check; all are fixed. Among them: tablet tables show their result columns first; chart
  badges and markers no longer cover labels or lines; cold deep links to model rows scroll to the
  row; glossary popups no longer widen the phone layout; printing early shows the charts; the hero
  says when a call will never be scored, for example because its window spans a roll; LIVE hides
  QLIKE values it calls too few to compare; promotion wording follows the verdict; days to expiry
  age with the snapshot.

### Pipeline and docs

- The live record summary's `recent` rows repeat more of what each entry already stores (recording
  and seal times, base session, the probability that volatility rises, the recorded inputs) and say
  whether the window spans a roll (`spans_roll`, the test resolution already applies), so the page
  can show each seal against the settlement that opens its window. The data contract and
  `validate_frozen_payload.py` cover the new optional fields.
- zod 4.6.7 (GHSA-mrhx-c7xc-m3w5; a dev-only transitive dependency).
- The README's dashboard screenshot and the link-preview image are regenerated from the new page,
  and the README and `docs/ARCHITECTURE.md` describe it.

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
  within one unit of the last decimal it is published with (`vol_lab.compare_reports`). The
  GARCH-family refits converge to a 1e-9 tolerance, so across BLAS thread counts and CPU kernels
  every figure stays within that bound; a rounded figure can still move by one unit in its last
  decimal (a rebuild on the default, unpinned AVX-512 BLAS and NumPy kernels gives one yearly GJR-GARCH QLIKE as 0.5471 against 0.5472). The unit
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
  bootstrap Sharpe-difference tests; the variance risk premium in variance units with a HAC CI, the
  vol-point comparison against a zero-premium null, and a non-normal test of whether
  short-volatility Sharpe ratios are positive.
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
  (`vol_record.promotion_test`) whose status and statistics the record summary publishes. Its
  p-value is from Student's t with 51 degrees of freedom (the Harvey-Leybourne-Newbold reference for
  52 one-step differences), not the normal, whose 10% critical value (1.2816, against 1.2984) would
  reject with a size of about 10.3%. The summary also gives the earliest date a pending entry can
  be scored (`next_result_due`).
- **Carry.** The rank-correlation power uses the two-sided level of the test it describes.
- **Leak anatomy.** The block-shuffled `CL=F` runs permute the same returns and are not
  independent, and their base rate is not 50%: both are stated, with per-run tests against each
  run's own base rate; its Sharpe ratios are labelled gross (the retracted 2.47 is after costs). The
  ledger's rows on those runs no longer pool them: each claims that every run beats its own base
  rate, with the largest of the per-run p-values (an intersection-union test, valid however the runs
  depend on one another), and is marked not pre-specified because that test replaced the pooled one
  after the runs were seen.
- **Supply study.** Event pairs fewer than 25 sessions apart are listed as overlapping, a stated
  caveat.
- **Ledger statuses.** The status "rejected" is renamed "not supported" and defined as p ≥ 0.10 or
  an estimate pointing the other way: a failure to reject, not evidence against the hypothesis.
  Where the artifact records a test's power, the row says whether it was underpowered (the post-2007
  carry spread test is, at 67% against an 80% target). The model card's "timing was tested and
  rejected" now reads "not supported".
- **Post-selection test out of the multiplicity family.** The challenger's pairwise
  Diebold-Mariano test against the model of record (p = 0.002) was listed among the claims that
  survive Benjamini-Hochberg (q = 0.010) and clear t > 3, although the challenger was picked as the
  lowest-loss model of the field on the same sample. The row is now marked selected after a search
  and left out of the adjustments; the search-adjusted test of the same question, Hansen's SPA,
  gives p = 0.072, so after the search the challenger's edge is inconclusive.
- **Coefficient reading.** Realized volatility averages 0.865 times the previous session's OVX; the
  README attributed that to the variance risk premium plus OVX's basis. More than half of it, in
  logs, is the Jensen gap of a noisy five-session realized volatility, which a forecast unbiased in
  variance shows too (the model of record: 0.920), and in variance units the gap has no stable sign.
- **Risk use.** The abstract and the summary table credited the model of record with cutting the
  overlay's kurtosis and with a better VaR. Both are properties of scaling by volatility: sizing on
  trailing realized volatility gets most of the way (excess kurtosis 1.96 against 1.82), raw OVX
  passes the VaR tests as often, and only the overlay's Sharpe differences are tested.
- **Promotion pair pinned.** The promotion test took its pair from the newest record entry, so a
  plain-HAR fallback forecast or a re-chosen challenger would restart the sample and drop a finished
  verdict from the summary. The pair is now pinned in `vol_record` (`PROMOTION_MODEL_VERSION`,
  `PROMOTION_CHALLENGER`); entries of another pair are recorded and counted by reason but never
  enter the test, the model-of-record statistics cover the pinned version only, and a unit test
  fails if the live model version or the lab's challenger changes without a new declaration.
- **Comparators on variance-consistent forecasts.** In the production validation, the HAR-without-OVX
  and leverage variants were scored on median forecasts against a variance loss; they now use the
  smeared forecast, as the model of record does (QLIKE 0.524 → 0.432 and 0.44 → 0.375), and the
  yearly level R² is on the smeared forecast too.
- **One direction noise floor.** The lab and the production validation run the same simulation
  (`vol_forecast.noise_floor`, 10 draws); the production figure moves from 75.5% on 5 draws to
  75.2%.
- **Fallbacks are reported.** When the OHLC bars, an OVX source, the economic tests or the challenger
  fail, the forecast logs a warning and records the reason (`live.ovx_error`,
  `live.challenger_error`, `economic.unavailable_reason`), which the validator checks and the
  dashboard shows.
- **Names.** The OVX-only regression and raw OVX each have one name across the README, the
  dashboard and the ledger (they were "Implied-vol regression" and "Implied vol (raw OVX)"), and
  ledger rows name supply-shock groups by their display names, not their keys.
- **Dashboard.** Headlines are conditional on the data, labelled by sample and compared with the
  strongest simple baseline (the OVX-only regression, trailing-vol targeting); the direction
  probability is demoted and labelled model-implied and uncalibrated.
- **Documentation.** Every table, result statistic and data-dependent verdict in the README, and
  every table in the model card and the datasheet, is generated from the artifacts by
  `scripts/readme_blocks.py`, and CI fails if they disagree; descriptive facts about the design (the
  number of models, schedules, time limits) are written by hand. Reproducibility is described
  artifact by artifact; "weekday grids" are 5-session sampling grids (each grid's dates fall on
  every weekday). The lab's `HAR-RV + semivariance` is relabelled `HAR-RV + downside semivol`: it
  adds one five-session downside semivolatility term to HAR-RV.

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
| Vol forecaster QLIKE 0.422 | That scored the median forecast against a variance loss; the variance-consistent forecast (and lagged OVX) gives 0.376 on the production run ([full results, robustness](docs/RESULTS.md#27-robustness)) and the lab figures in the [benchmark field](docs/RESULTS.md#21-the-benchmark-field) | `data/vol_forecast_validation.json` `har_qlike`, `har_qlike_median_forecast` |
| "OVX at day t is known at t (no leak)" | OVX settles at 16:15 ET, about 1h45m after the CL settlement that opens the target window; the same-day print is measurably more informative, so version 2 uses the previous session's OVX | `vol_lab.json` model `har_iv_sameday`; `validation.robustness_same_day_ovx` |
| "Outscores the same model without OVX (z 2.5)" on direction | With lagged OVX the direction gain is not significant; the OVX gain is clear on QLIKE | `data/research_ledger.json` rows `vol_ovx_gain_*` |
| Variance risk premium "real" and "genuinely there": OVX above the following month's realized vol in 72.5% of periods (73.5% on the current sample), by 2.5 vol points on average | Read against the wrong null. With zero premium a 20-session realized vol, noisy and right-skewed, still falls below implied vol in about 69.5% of periods, and the vol-point gap averages about 1.5 points (Jensen's inequality); the observed share is not significantly above that (one-sided bootstrap p = 0.318). In variance units, the relevant measure, the premium is not significant either. A premium is not established; nothing shows it is zero | `data/vol_forecast_validation.json` `economic.variance_risk_premium.zero_premium_null`; `data/research_ledger.json` rows `vrp_exists` (retracted) and `vrp_exists_vs_zero_premium` |
| Supply shocks: "threat-only events peak +4.0%, settle +0.5%", read as the market reacting to headlines | A random ordinary date peaks higher under the same path statistic; threat-only events are indistinguishable from it. Announced or physical supply removal does peak more than the placebo | `data/supply_shock_study.json` |
| Carry: "faded to nothing measurable since 2007" | The post-2007 spread test is underpowered and the change in spread is not significant, while the rank correlation did fall; see [the carry section of the full results](docs/RESULTS.md#51-term-structure-carry) for the figures. Columns relabelled: the split is on the prior-months median carry, not backwardation vs contango | `data/carry_signal_test.json` |
| Earlier corrections (1.x): the direction model's Sharpe 2.44/2.47 (a look-ahead leak; purged −0.98), the roll-biased carry test, the supply-shock grouping | See the [post-mortem](docs/RESULTS.md#4-post-mortem-the-direction-model) and the 1.x history in git | `data/wf_*`, `data/leak_anatomy.json` |

## 1.x — 2026-06 to 2026-10

The direction-model post-mortem and retraction, the leak-free walk-forward engine, the first HAR-IV
forecaster, the carry and supply-shock studies, the contract calendar and the least-privilege deploy.
See the git history before this release.
