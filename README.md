# WTI Crude Oil Futures — Direction-Leak Post-Mortem and a Validated Volatility Forecaster

[![Tests](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/tests.yml/badge.svg)](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Live dashboard](https://img.shields.io/badge/live-dashboard-5cb0d6.svg)](https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/)

A machine-learning research project on WTI crude, with a clear arc. The original 1-week
**direction** signal backtested at Sharpe 2.44; a purge/embargo audit then showed the **entire
edge was look-ahead leakage**, and once corrected the signal is a coin flip. Rather than chase a
direction edge that theory says should not exist on a liquid contract, the project pivots to what
genuinely is forecastable: **volatility**. A leak-free HAR-IV model (realized vol plus OVX implied
vol) calls next-week vol direction at **about 72%** versus a 52% base rate over **924 out-of-sample
weeks, 2008–2026** (i.i.d. z ≈ 12, autocorrelation-robust z ≈ 16), above the base rate in **every
one of 19 years**, and significantly better than both a mean-reversion rule and the same model
without implied vol. So the project carries one honest negative result (direction is
unforecastable here, and the headline that said otherwise was a leak) and one honest positive
result (vol is forecastable, validated with the same purged walk-forward — though even that is a
risk/regime indicator, not a demonstrated source of trading profit). Plus honest tests of two more
candidate edges (carry, the variance risk premium), an EIA supply-shock event study, and a
zero-infrastructure deploy pipeline.

Every figure below is produced by code in this repository from committed or freely downloadable
data; the dashboard recomputes the volatility figures on each refresh, and the committed
artifacts under [`data/`](data) pin the numbers quoted here.

---

## Headline finding: the edge was a look-ahead leak

The 1-week signal originally backtested at Sharpe 2.44 (5-year) and 2.07 (10-year rolling), with
direction accuracy near 66% and p < 0.001. Those numbers were a **look-ahead leakage artifact**.

The walk-forward trained on `dataset.iloc[start:end_idx]` and predicted at `end_idx`, but each
row's label is the Close **5 bars ahead**. So the last 4 training rows at every step carried
labels that only mature *after* the prediction is made. At the real decision moment those labels
do not exist yet, so training on them feeds the future into the model. Because the model is a tree
ensemble retrained every 5 days, and the most recent training rows are near-duplicates of the test
row in feature space, it latched onto those leaked near-duplicates and effectively memorized the
answer. It was a leak detector, not a forecaster.

The fix is a standard purge/embargo (López de Prado): drop the last `horizon_steps − 1` rows from
each training window so no training label matures after the prediction point. This also makes the
backtest faithful to production, which cannot train on unmatured targets in the first place. With
the purge applied:

| Metric | 5-Year (n=199) | 10-Year rolling (n=450) |
|---|---|---|
| | unpurged → **purged** | unpurged → **purged** |
| Annualized Sharpe (after costs) | 2.44 → **−0.66** | 2.07 → **−0.11** |
| Direction accuracy | 65.8% → **48.2%** | 66.0% → **51.6%** |
| Significance vs coin-flip | p<0.001 → **p = 0.71** | p<0.001 → **p = 0.27** |
| Beats naive baselines? | (leaked) → **no** (MAE 0.62% worse) | (leaked) → **no** |

The collapse is uniform across every calendar year, on two independent out-of-sample sets, and is
many times larger than the known run-to-run noise. The conclusion is unambiguous: **as built, this
feature set has no out-of-sample directional edge in WTI.** The corrected signal is a coin flip
that loses money after costs.

Artifacts: [`data/wf_5y_purged.json`](data/wf_5y_purged.json) and
[`data/wf_10y_rolling_purged.json`](data/wf_10y_rolling_purged.json) (corrected);
[`data/wf_5y_unpurged_LEAKED.json`](data/wf_5y_unpurged_LEAKED.json) (the original leaked run,
preserved for the before/after). The fix is in
[`backend/backtest_walk_forward.py`](backend/backtest_walk_forward.py) (`purge = horizon_steps - 1`).

### What this project actually demonstrates

Not a tradeable edge. What it shows is the research discipline a desk cares about: a full
walk-forward and leakage-testing framework, and the judgment to audit it, find a fatal
look-ahead leak in its own headline, quantify the damage honestly, and retract the result rather
than ship it. The same discipline, applied again, caught two more errors of its own, both
corrected in place with the old and new numbers side by side below:
- the carry test measured returns on a series whose roll gaps subtract the carry itself;
- the event study's "priced-in" check counted day 0 inside its own "eventual peak". The supporting analyses that were built on the leaked signal (the random-strategy
skill decomposition, conviction calibration, measured effective sample size, the ex-2026 anchor,
and the macro/timing leakage A/Bs) all measured properties of leaked predictions and are retained
below only as a record of the original, now-invalidated claim.

## What does work: next-week volatility forecasting

Direction is near-unforecastable on a liquid contract, which is why the leaked direction edge was
too-good-to-be-true. **Volatility is not.** Volatility clustering and mean-reversion are among the
most replicated effects in financial econometrics, so a properly validated vol forecast has real
out-of-sample skill. Using the *same* purged walk-forward that exposed the direction leak, a
**HAR-IV** model ([`backend/vol_forecast.py`](backend/vol_forecast.py); Corsi 2009 with an implied-vol
term) forecasts next-week (5-day) realized volatility from realized vol over 5/22/66 days **plus
OVX, the free CBOE oil implied-vol index**. The sample is fixed: every week since OVX began (May
2007), so the headline only moves as new weeks accrue — 924 OOS weeks, May 2008 to September 2026:

| Metric | HAR-IV model | Same model without OVX | Mean-reversion rule | Persistence |
|---|---|---|---|---|
| Vol-direction accuracy (rise/fall vs current) | **72.1%** | 68.8% | 66.3% | — (majority class 51.8%) |
| Level forecast R² | **0.457** | 0.369 | — | 0.193 |
| Level forecast MAE (annualized vol) | **0.105** | — | — | 0.140 |
| QLIKE loss (lower is better) | **0.417** | 0.516 | — | 0.880 |

How strong is it, tested properly:

- **Against the base rate:** exact binomial p ≈ 3e-36; z ≈ 12.3 assuming independent weeks and
  z ≈ 15.7 with a Newey-West (HAC) standard error. Consecutive weekly labels share a week of
  returns, so the independence assumption is violated; the dashboard quotes the smaller z.
- **Against the smart baseline:** "vol reverts to its 66-day average" already gets 66.3%. The model
  beats it by about 6 points, and a *paired* Diebold-Mariano test on the weekly hit series gives
  z ≈ 3.9 (HAC). It wins in 15 of 19 years and loses in 2008, 2013, 2018 and 2025, which is
  reported rather than hidden.
- **Every year:** above that year's base rate in **19 of 19 years** (64.0% to 80.4%), and 2020 is
  not the driver (72.0% ex-2020 vs 72.1% overall).
- **Level:** R² 0.46 against 0.19 for persistence, and QLIKE (Patton 2011) less than half of
  persistence's. QLIKE is the loss that ranks vol forecasts consistently when realized vol is a
  noisy proxy. The level forecast is exp(log-forecast), the conditional median. That is the right
  statistic for the rise/fall call, and it makes the level slightly conservative.

Implied vol is the one free-data feature that earned its keep. The nested comparison runs on the
same rows. Without OVX the model scores 68.8% on direction, R² 0.369 and QLIKE 0.516; with it,
72.1%, 0.457 and 0.417. The direction gain is significant (paired HAC z ≈ 2.7). This is a leak-free
gain, because OVX at day *t* is known at *t*. The model falls back to pure HAR if OVX is unavailable
or too short to validate.

Discipline, shown both ways: a candidate feature has to beat the deployed model out of sample, or
it stays out. The standard HAR *leverage* term (downside realized semivolatility, so down weeks
predict higher vol) is re-tested on every validation run as a nested variant on the same rows. It
nudges direction to 72.7%, but the gain is not significant (paired HAC z ≈ 1.4), and it does not
improve R² (0.456) or QLIKE (0.419). So it is **left out**. Adding complexity that does not earn its
keep is how vol models overfit. Net: the model is HAR-IV (three realized-vol terms plus OVX),
nothing more.

Honest scope, because the lesson of the direction signal is to not oversell. This is a **clean
implementation of a known effect, not novel alpha**, and it is a **vol forecast, not a directional
return signal** — you do not make directional P&L from it. The strong, robust part is the
direction/regime call and the level ranking; level calibration is the part still worth improving.

**Does it convert to P&L? Tested, and honestly: no.** A volatility-targeting overlay scales a long
WTI position by 35% target vol divided by the forecast, capped at 2x, rebalanced weekly, with
5 bps/turn costs, 2008–2026. It did **not** beat buy-and-hold on a risk-adjusted basis:

| Strategy | Sharpe |
|---|---|
| Buy-and-hold | 0.18 |
| Trailing 22-day vol target | 0.04 |
| HAR-IV forecast vol target | 0.07 |

The forecast barely improves on trivial trailing-vol sizing. This matches the skeptical literature
on vol-managed portfolios (the benefit is fragile out of sample) and the fact that WTI's own return
premium is weak. So the forecast's honest standing is: a **validated, statistically significant
volatility/regime indicator** (useful for risk monitoring and as an options-vol input), **not** a
demonstrated source of trading profit. Reporting this negative result rather than tuning the
overlay until it looks good is the whole point. The overlay returns come from Yahoo's unadjusted
front-month series, whose monthly roll gaps are the same for all three strategies.

```bash
python -m backend.vol_forecast      # reproduces every number in this section and writes the artifact
```
Artifact: [`data/vol_forecast_validation.json`](data/vol_forecast_validation.json) (overall,
year-by-year, and the economic tests).

<details>
<summary>Original (now-invalidated) credibility section — kept for transparency</summary>

The leaked result was stress-tested against the obvious failure modes before the leak was found:

1. **Not a trending-market artifact.** On the identical window, buy-and-hold scored
   Sharpe ≈ **0.00** and naive momentum scored Sharpe ≈ **−1.03**. The market handed out no
   free trend; weekly WTI was mean-reverting, and the dumb strategies lost. The backtest also
   reports naive-last-price, drift, and seasonal baselines alongside the ensemble.
2. **Not concentrated in one event.** The ensemble is profitable in every calendar sub-period:

   | Period | Trades | Total P&L | Sharpe | Win% |
   |---|---|---|---|---|
   | 2022 (Jun–Dec) | 27 | $46,170 | 2.84 | 70.4% |
   | 2023 (full year) | 50 | $52,540 | 2.26 | 64.0% |
   | 2024 (full year) | 50 | $32,790 | 1.52 | 58.0% |
   | 2025 (full year) | 51 | $49,000 | 2.33 | 64.7% |
   | 2026 (Jan–Jun) | 21 | $112,660 | 4.61 | 66.7% |
   | **Ex-2026 (2022–2025)** | **178** | **$180,500** | **2.19** | **63.5%** |

   The number to anchor on is the **ex-2026 Sharpe of 2.19** (178 trades, 65.2% direction
   accuracy): 2026 H1 produced 38% of total P&L on 21 trades, and its 4.61 Sharpe on a
   half-year sample is noise-dominated (SE of an annualized Sharpe estimate at n=21 is ≈ 1.7). The
   three complete calendar years (2023–2025) average Sharpe 2.04; the weakest (2024, 1.52)
   is the honest stress-test — still profitable, still positive win rate, but meaningfully
   weaker. Naive momentum loses in *every* calendar year 2021–2026, so the baseline edge
   is not regime-dependent even when the ensemble's magnitude varies.
3. **Not dependent on revision-prone macro data.** The deployed model excludes FRED/EIA
   macro entirely; the headline comes from price/technical + lagged market features alone.
   A side-by-side A/B (no_macro vs with_macro, step=20, n=50 OOS samples each) showed
   with_macro at **76% accuracy / Sharpe 1.68** vs no_macro at **62% / Sharpe 1.37**
   (Sharpes at the test's own 12.6-trades/yr cadence — not comparable to the step=5
   headline; the within-test delta is the finding). The +14pp accuracy gap does **not**
   reach significance on an unpaired two-proportion test at n=50 (z = 1.5, p ≈ 0.07
   one-sided) — it is suggestive, not proven. The decision logic does not depend on
   resolving it: FRED/EIA series are latest-vintage (not ALFRED point-in-time), so the
   uplift cannot be attributed to genuine signal without a vintage audit. **If it is
   revision leakage, deploying it would inflate live expectations; if it is real alpha,
   it stays on the table until ALFRED vintages prove it.** Either branch ends in
   exclusion — that is the deliberate trade, and it costs at most the unproven uplift.
   Comparison artifact: [`data/macro_leakage_test.json`](data/macro_leakage_test.json).
4. **Not entry-time leakage from after-hours closes.** The backtest enters at the WTI
   settlement (~14:30 ET), but equity/vol context features (VIX, XLE, SPY) close at
   16:00 ET — ~90 minutes later. A full **headline-config A/B (step=5, estimators=40,
   n=199)** confirmed: same-day Sharpe 2.79 vs lagged Sharpe 2.44 (−0.35 delta; both
   p < 0.001). The lagged result (acc 65.83%, Sharpe 2.44) **matches the headline
   exactly** — the headline IS the strictly entry-time-clean feature set. The edge does
   not depend on the ~90-minute post-entry close window.
   Comparison artifact: [`data/timing_leakage_test.json`](data/timing_leakage_test.json).

</details>

Reproduce. The backtest now **purges by default** (`purge = horizon_steps − 1`), so these
commands produce the corrected coin-flip result, not the original 2.44. `--period` is relative to
today. The committed purged/unpurged pair was run on 2026-06-19 on the same window, which is what
makes it a like-for-like before/after. To re-run that exact window, pin it with `--start`/`--end`.
New reports record their `data_start`/`data_end`, so they can always be re-run. Tree ensembles
retrained per step carry some run-to-run noise, but it is far smaller than the leak.
```bash
# 5-year, corrected (1d + 1w), on the committed artifacts' window
python -m backend.backtest_walk_forward --start 2021-06-21 --end 2026-06-19 --min-train 200 --step 5 \
  --features no_macro --lag-context 1

# 10-year, production rolling 18-month window
python -m backend.backtest_walk_forward --period 10y --step 5 --features no_macro --lag-context 1 \
  --train-window 378 --horizons 1w --output data/wf_10y_rolling_purged.json
```

---

## What does NOT work (stated plainly)

- **The 1-week signal itself, once the look-ahead leak is removed.** This is the headline
  finding above: corrected, it is a coin flip (48–52% accuracy, negative Sharpe, p > 0.2) that
  loses money after costs and does not beat naive baselines. It is no longer presented as a
  tradeable signal.
- **1-Day horizon: never worked.** Direction unstable across reruns and negative P&L after costs
  even before the purge. Removed from trading use.
- **1-Hour horizon: removed.** Indistinguishable from noise; never reached enough samples to test.
- **Term-structure carry: no reliable modern-era edge (and a corrected earlier error).** Carry
  (backwardation vs contango) is a top cross-sectional commodity factor, so it was the most
  promising remaining directional signal. The first version of this test got it wrong in a way
  worth recording. It measured forward returns on Yahoo's `CL=F` and described that series as
  back-adjusted. It is not: it equals EIA's unadjusted contract-1 price on 97.7% of days, including
  the −$37.63 print. A one-month forward return on it almost always crosses a roll, and the roll gap
  is roughly *minus the carry*. So the test subtracted the effect it was looking for: about −1.5% a
  month in backwardation and +1.4% in contango, correlation −0.55 with carry. That artifact produced
  the old "data leans the opposite way" finding. Its 2004 start was also accidental: an
  unpaginated EIA API call hit a 5,000-row limit.

  The rebuild holds the second-nearby contract over each contract cycle, so every return is one
  contract's price change with no roll gap. It runs on EIA's full futures curve, 1985–2024 (EIA
  stopped publishing the series in April 2024, so the committed copy is final). The rule is
  unchanged and fixed in advance: long when carry is above the median of all prior months, else
  flat, over non-overlapping months. Results:

  | Sample (OOS months) | Backwardated months | Contango months | One-sided p | Timed Sharpe vs buy-and-hold |
  |---|---|---|---|---|
  | 1990–2024 (409) | +1.84%/mo | +0.18%/mo | 0.035 | 0.48 vs 0.29 |
  | 1990–2006 (203) | +2.85%/mo | +0.12%/mo | 0.010 | 0.76 vs 0.50 |
  | 2007–2024 (206) | +0.74%/mo | +0.23%/mo | 0.36 | 0.19 vs 0.14 |

  Correctly measured, carry sorts returns the textbook way. But the effect is concentrated before
  2007 and is not distinguishable from zero since. As a sign predictor it never beats always-long
  (52.6% direction accuracy vs a 55.5% base rate). So the conclusion survives, for the right
  reason: no reliable modern-era directional edge.
  [`backend/carry_signal_test.py`](backend/carry_signal_test.py) ·
  [`data/carry_signal_test.json`](data/carry_signal_test.json) ·
  [`data/eia_wti_futures_curve.json`](data/eia_wti_futures_curve.json).

- **Variance risk premium: real, but not a clean edge here.** Oil's implied vol (OVX, free) has
  exceeded the realized vol of the following month by **2.4 vol points on average**, in **71%** of
  non-overlapping periods (2008–2026). The VRP is genuinely there. Harvesting it is another matter:
  - A linear short-vol (vol-swap) proxy has a Sharpe of 0.49.
  - The convex variance-swap version, which is what an options seller actually holds, has a Sharpe
    of about **0.0**. The gap is the left tail: the worst month was −173 vol points in 2020, and
    2008 and 2014 were similar.
  - Timing the sale with the model's own forecast did not help (rich-vs-cheap one-sided p = 0.32).
  - Doing this cleanly needs oil options data that is not freely available with long history.

  Reported as a real market fact, not a strategy; the numbers are computed in
  [`backend/vol_forecast.py`](backend/vol_forecast.py).

The pattern across every directional test is consistent and is itself the finding: **WTI direction is
not forecastable from the signals reachable here** (momentum was a leak; carry's textbook effect has
faded to nothing measurable since 2007), and the
real, documented effects that *do* exist (volatility clustering, the variance risk premium) are either
not tradeable via the channels available (vol-targeting) or need data this project does not have
(options). Only realized-volatility *forecasting* survives as a clean, validated result. A framework
that surfaces and reports its own failures is worth more than one that hides them.

---

## From signal to position (infrastructure, not a live recommendation)

This layer was built to translate a *validated* signal into position sizing. With the edge now
retracted, it stands as **engineering, not a trade recommendation** — the sizing math is correct,
but it has no real edge to size. Kept because the plumbing is the reusable part:

- **Stance** — LONG / SHORT lean only when model conviction exceeds ±0.6%, NEUTRAL otherwise.
  With the corrected (non-significant) backtest the dashboard shows NEUTRAL and no tear sheet.
- **Kelly sizing** — full- and half-Kelly fractions derived from win rate and profit factor, plus
  a contracts-per-account translation at 2% risk. Correct given inputs; the inputs are no longer
  a real edge.
- **Live track record** — CI records one 1W call per CME trading session, only while the market
  is open, and scores it in the session five trading days later
  ([`backend/live_record.py`](backend/live_record.py),
  [current record on `live-data`](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/blob/live-data/runtime-state/live_track_record.json),
  [bootstrap snapshot on `main`](data/live_track_record.json)). Every entry and resolution
  is timestamped by a bot commit, making the forward chronology auditable. Calls spanning a
  contract roll are skipped, not scored. That check uses the exchange calendar
  ([`backend/contract_calendar.py`](backend/contract_calendar.py)), because `CL=F` follows the
  expiring contract through its last trade date. A call whose scheduled run arrives more than one
  session late is skipped rather than scored at an arbitrary later price. NEUTRAL means "no trade"
  and is never counted. Daily calls with a five-session horizon overlap, so the record also
  counts **non-overlapping** scored calls, and "too few to validate" holds until there are 18 of
  those. A GitHub Actions job also emails on stance changes
  ([`backend/signal_alert.py`](backend/signal_alert.py)).

---

## Supply-shock event study (decision-support, not alpha)

A separate layer for the scenario where ML models are *least* reliable — geopolitical supply
shocks. It makes no predictive claim; it answers the question a discretionary PM actually asks
during an event: **"how have structurally similar shocks actually resolved?"**

- **35 defined supply-shock events, 1990–2024** (wars, OPEC cuts, hurricanes, sanctions,
  strait incidents), all scored. Only the event date and barrels-at-risk are hand-entered, each
  with a source note. **Every price number — peak %, days-to-peak, settle %, trajectory — is
  computed from EIA's official daily WTI Cushing spot series (RWTC)**, not transcribed by hand.
  ([`backend/supply_shock_playbook.py`](backend/supply_shock_playbook.py))
- **The finding the dashboard leads with:** events with real barrels lost (>0.5 mbpd, n=21) peak
  at a median +12.1% and still hold +4.3% ten sessions later. Pure supply *threats* with no
  physical loss (n=10) peak at +4.0% and settle near flat (+0.5%): the market pays for
  disruption more than headlines. Bearish supply gluts (price wars, OPEC refusing to cut) and demand
  events also carry zero barrels lost, but they are not threats, so they are excluded from that
  group. An earlier version counted them, which made "threats fade" look stronger than it is.
- **"Is it already priced in?" check:** after a first-day move of at least +3% vs the previous
  close (n=7), prices rose a median **+4.0% further** over the next month. After a weaker start
  (n=28) they rose **+9.6% further**. So a strong open was not a signal of more upside; if anything
  it was already priced, though n=7 is far too small to conclude. An earlier version measured the
  "eventual peak" including day 0 itself. That builds a big peak into any big first day, and it
  reported the opposite result.
- **News-flow regime guardrail:** a recency-weighted keyword score over NewsAPI headlines
  (LOW / ELEVATED / HIGH / CRITICAL). It does exactly one job: attach an explicit caveat to the
  retracted ML output in HIGH/CRITICAL regimes, because that model is trained on normal-market data
  and would underestimate tail risk. Two limits apply:
  - It is keyword matching on whole words, not NLP: a transparent proxy, labeled as such.
  - On NewsAPI's free Developer plan, articles arrive **24 hours late**. Its "breaking news" weighting
    only becomes meaningful on a paid plan. That plan is also licensed for development only, not
    for a public deployment.

```bash
python backend/supply_shock_playbook.py   # print the full event-study table from EIA data
```

---

## Architecture

- **[`backend/oil.py`](backend/oil.py)** — data ingestion, feature engineering, 6-model
  ensemble (the retracted direction model), news-regime score, contract quote.
- **[`backend/contract_calendar.py`](backend/contract_calendar.py)** — the CME calendar for CL:
  holidays, last trade dates (3 business days before the 25th, or 4 when the 25th is not a
  business day), market hours, and the contract `CL=F` tracks on any date. It is the one source
  of truth for the dashboard's contract label, the live record's roll check and the carry test's
  contract cycles, and it is tested against ICE's published expiry table.
- **[`backend/backtest_walk_forward.py`](backend/backtest_walk_forward.py)** — the validation
  engine: walk-forward with baselines, binomial p-values, Wilson CIs, and dollar P&L (Sharpe,
  win rate, drawdown, profit factor). `--features {all,no_macro,price_only}` enables the leakage
  comparison; `--train-window` switches expanding to rolling (378 bars = the production 18-month
  window); `--horizons` and `--period` select the test set. Non-positive prices (the 2020 negative
  settle) are dropped before feature engineering. **Purges the last `horizon_steps − 1` training
  rows per step** so no label matures after the prediction point — the fix that exposed the
  headline as leakage.
- **[`backend/vol_forecast.py`](backend/vol_forecast.py)** — HAR-IV realized-volatility forecaster
  (realized vol + OVX implied vol), the project's validated signal, with its full validation:
  nested baselines, HAC and paired tests, QLIKE, the leverage-term check, and the economic tests.
  Falls back to pure HAR if OVX is unavailable.
- **[`backend/carry_signal_test.py`](backend/carry_signal_test.py)** — roll-free term-structure
  carry test on EIA's futures curve (no reliable modern-era edge).
- **[`backend/supply_shock_playbook.py`](backend/supply_shock_playbook.py)** — EIA-computed
  supply-shock event study.
- **[`backend/server.py`](backend/server.py)** — Flask API; merges live predictions with the
  walk-forward stats artifact.
- **[`backend/signal_alert.py`](backend/signal_alert.py)** — stance-change email alerts (CI),
  over a certificate-verified TLS connection.
- **[`backend/live_record.py`](backend/live_record.py)** — git-committed live track record
  (one call per trading session, scored five sessions later, contract rolls and late runs skipped).
- **[`freeze.py`](freeze.py)** + **[`.github/workflows/refresh.yml`](.github/workflows/refresh.yml)**
  — frozen snapshot deployed to GitHub Pages (no running server). The workflow is split for
  least privilege: an unprivileged job freezes, validates and builds; a separate job with the
  write token deploys, alerts and persists state, and installs or builds nothing.
- **[`.github/workflows/price.yml`](.github/workflows/price.yml)** — lightweight price refresh on
  an isolated `live-data` branch, with the frozen Pages price as fallback. The same branch stores
  generated signal/live-record state so automation never pushes to protected `main`.
- **[`src/`](src)** — React dashboard (lightweight-charts, hand-written CSS).
- **[`data/`](data)** — checked-in evidence and bootstrap artifacts: the walk-forward backtests,
  leakage comparisons, vol validation, carry test, EIA spot and futures-curve caches, live track
  record, and signal state. Current mutable state is restored from `live-data/runtime-state/`;
  per-contract runtime files remain gitignored.
- **[`tests/`](tests)** — a network-free unit suite run in CI on every branch push
  ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)). It includes:
  - a look-ahead **leak check** on the vol-forecast feature builder
  - a **purge-invariant guard** on the backtest fix
  - a synthetic curve proving no **roll gap** can enter a carry return
  - the committed carry artifact **reproducing** from the committed data
  - the **contract calendar** against published expiries
  - the live-record session, roll and lateness rules
  - the **retraction guarantee**: a non-significant model never surfaces a lean
  - **workflow guards** for pinned actions and least privilege

  Locally: `PYTHONPATH=. python -m unittest discover -s tests`.

### Models
Ensemble of Random Forest, Extra Trees, Ridge, Elastic Net, XGBoost, LightGBM, blended with
validation-aware weighting, split-conformal prediction intervals, and a drift-challenger baseline.

### Feature set (the configuration the purged backtest validated)
Technical indicators (RSI, MACD, Bollinger, momentum, volatility, OBV) + cross-asset context
(Brent–WTI spread, DXY, VIX/OVX, rates, XLE/XOP), **lagged one trading day** in both the backtest
and production so every feature is observable before the entry print. FRED/EIA macro features are
available but **off by default** (see validation notes above).

---

## Installation & usage

Requires **Python 3.12+** (numpy 2.5 does not install on older versions) and **Node 22.13+**.

```bash
# 1. Install the pinned Python and JavaScript dependencies
python -m pip install -r requirements.txt
npm ci
cp .env.example .env          # add API keys (EIA, NewsAPI, ...)

# 2. Run locally (Flask API on :9000 + Vite dashboard on :3000)
./dev.sh

# 3. Reproduce the research results (network: Yahoo Finance; the carry test is fully offline)
python -m backend.vol_forecast
python -m backend.carry_signal_test
python backend/supply_shock_playbook.py

# 4. Reproduce the purged direction backtest (leakage-proof config) and compare feature sets
python -m backend.backtest_walk_forward --period 5y --min-train 200 --step 5 --features no_macro --lag-context 1
python -m backend.backtest_walk_forward --period 5y --features all         # with macro
python -m backend.backtest_walk_forward --period 5y --features price_only  # technical only
```

### API endpoints
- `GET /` — service status; answers 503 with `Retry-After` until it can serve data
- `GET /data` — full dashboard payload (predictions + walk-forward stats + event study)
- `GET /health` — readiness check (503 until ready)
- `GET /live` — process liveness only; never calls an upstream provider

---

## Free static hosting (GitHub Pages, no server)

The dashboard runs with **zero running infrastructure** using the "frozen Flask" pattern:

1. **`freeze.py`** runs the full pipeline in-memory and renders the `/data` endpoint (via
   Flask's `test_client`) to a static `public/data.json`.
2. **`npm run build`** with `VITE_STATIC_DATA=true` produces a static site that fetches that
   frozen JSON instead of polling a live backend.
3. **`.github/workflows/refresh.yml`** is scheduled every four hours: freeze, then validate the
   payload (including the quote's own exchange time, so a stale data feed fails the deploy), then
   build and deploy to the `gh-pages` branch. If a run fails (rate limit, API outage), the
   previous good snapshot stays live.
4. **`.github/workflows/price.yml`** updates `live-data/price.json` without redeploying Pages. The
   client reads that CORS-enabled raw file and the price baked into the last snapshot. It shows
   whichever quote is newer by its exchange timestamp, never letting an older quote override a
   newer snapshot. The four-hour workflow also restores and commits generated state under
   `live-data/runtime-state/`, keeping mutable automation off `main`.

**Freshness, stated honestly.** GitHub runs scheduled workflows on a best-effort basis and
throttles frequent schedules: the price job is scheduled every 15 minutes but in practice runs
every few hours, and the four-hourly refresh is sometimes late. So every price carries the
exchange time of the quote (`market_time`), not just when a job ran. The dashboard shows each
quote's age next to it, warns when the snapshot itself is stale, and never labels a snapshot
"real-time". Yahoo's NYMEX quotes are also exchange-delayed by about 10 minutes. The price job
fails visibly once no provider has answered for a day, instead of leaving an old price looking
current.

This removes the failure modes of a live free-tier server (cold starts, spin-downs, OOM during
model training).

**One-time setup:** enable read/write workflow permissions, push to `main`, set Pages source to
the `gh-pages` branch. Add API keys as repository Secrets (`EIA_API_KEY`, `NEWSAPI_KEY`, …).

Run it locally:
```bash
python freeze.py                          # writes public/data.json
VITE_STATIC_DATA=true npm run build
cd dist && python -m http.server 8000     # open http://localhost:8000
```

---

## Honest limitations

- **No directional edge (the dominant limitation).** The headline result was a look-ahead
  leakage artifact; corrected, the 1-week signal is a coin flip that loses after costs (see
  *Headline finding*). Every analysis that was built on the leaked signal — the year-by-year
  table, the ex-2026 anchor, the random-strategy skill decomposition, the measured serial
  correlation / ESS, the conviction calibration, and the macro/timing A/Bs — measured properties
  of leaked predictions and does not establish a real edge. They are retained only as a record of
  the original claim and its retraction.
- **The live signal is noise, too.** Production cannot train on unmatured targets, so it purges
  by necessity and inherits the same coin-flip behavior. The CI still computes and emails a 1W
  stance; treat it as a pipeline demo, not a recommendation.
- **Thin live record.** The git-timestamped live out-of-sample record is still accruing (one
  call per trading session, of which only non-overlapping calls count as independent evidence)
  and is displayed separately. Given the corrected backtest, the prior expectation for it is no
  edge.
- **Continuous-contract roll gaps.** Yahoo's `CL=F` is the unadjusted front month, so its monthly
  roll day carries the contract-1/contract-2 price gap. This affects the direction backtest's
  labels, realized volatility, and the vol-targeting overlay's returns. For the vol model it was
  measured against EIA's curve over 2016–2024: the median gap is about 0.5%, and roll-adjusting
  moves direction accuracy by under one point. A roll-free correction is not possible live,
  because EIA stopped publishing the futures curve in April 2024 and Yahoo keeps no history for
  expired contracts. The carry test, where the gap *is* the effect, uses roll-free returns.
- **Macro features were excluded, and that decision is now moot.** A pre-retraction A/B
  suggested a possible macro uplift, but it ran on the same leaked pipeline and is not meaningful
  evidence. FRED/EIA are latest-vintage (not ALFRED point-in-time), so any uplift could be
  revision look-ahead regardless; macro stays out of the model. With the base signal dead, this
  is no longer a live question.
- **The news regime score is a keyword proxy**, not NLP — useful as a guardrail, labeled as
  such, and never used as a trading signal. On NewsAPI's free plan its articles are a day late.
- **Scheduling is best-effort.** Everything runs on free GitHub Actions schedules, which GitHub
  delays and thins under load; the dashboard shows data ages rather than assuming a cadence.
- **Not investment advice.** This is a research system, not a production trading desk.

---

## Data integrity policy

Real market data only — no synthetic or placeholder values, and no hand-transcribed price
moves (the event study computes every move from EIA's official series). The system fails fast
with explicit errors when real data is unavailable, and labels any degraded horizon rather
than blending it into a misleading headline number.
