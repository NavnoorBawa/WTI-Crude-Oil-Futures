# WTI Crude Oil Futures — Direction-Leak Post-Mortem and a Validated Volatility Forecaster

[![Tests](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/tests.yml/badge.svg)](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Live dashboard](https://img.shields.io/badge/live-dashboard-5cb0d6.svg)](https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/)

A machine-learning research project on WTI crude, with a clear arc. The original 1-week
**direction** signal backtested at Sharpe 2.44; a purge/embargo audit then showed the **entire
edge was look-ahead leakage**, and once corrected the signal is a coin flip. Rather than chase a
direction edge that theory says should not exist on a liquid contract, the project pivots to what
genuinely is forecastable: **volatility**. A leak-free HAR-IV model (realized vol plus OVX implied
vol) calls next-week vol direction at **72.7%** versus a 51.7% base rate (z ≈ 8.5, p < 1e-15),
stable across every year of a decade. So the project carries one honest negative result (direction
is unforecastable here, and the headline that said otherwise was a leak) and one honest positive
result (vol is forecastable, validated with the same purged walk-forward — though even that is a
risk/regime indicator, not a demonstrated source of trading profit). Plus honest tests of two more
candidate edges (carry, the variance risk premium), an EIA supply-shock event study, and a
zero-infrastructure deploy pipeline.

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
than ship it. The supporting analyses that were built on the leaked signal (the random-strategy
skill decomposition, conviction calibration, measured effective sample size, the ex-2026 anchor,
and the macro/timing leakage A/Bs) all measured properties of leaked predictions and are retained
below only as a record of the original, now-invalidated claim.

## What does work: next-week volatility forecasting

Direction is near-unforecastable on a liquid contract, which is why the leaked direction edge was
too-good-to-be-true. **Volatility is not.** Volatility clustering and mean-reversion are among the
most replicated effects in financial econometrics, so a properly validated vol forecast has real
out-of-sample skill. Using the *same* purged walk-forward that exposed the direction leak, a
**HAR-IV** model ([`backend/vol_forecast.py`](backend/vol_forecast.py)) forecasts next-week
(5-day) realized volatility from realized vol over 5/22/66 days **plus OVX, the free CBOE oil
implied-vol index**. Validated over 10 years, leak-free, n=439 OOS:

| Metric | HAR-IV model | Baselines |
|---|---|---|
| Vol-direction accuracy (rise/fall vs current) | **72.7%** | 65.1% mean-reversion · 51.7% majority |
| Vol-direction, ex-2020 (n=389) | **74.0%** | — |
| Level forecast R² | **0.50** | 0.32 persistence |
| Level forecast MAE | **0.120** | 0.160 persistence (25% worse) |

The direction call is stable in **every** year of the decade (62% to 86%), and 2020 is the
*weakest* year, not the driver — removing COVID *raises* the accuracy to 74%. It also beats a
smart mean-reversion baseline by about 7 points in nearly every year, so the model captures more
than just "vol reverts to its average." Against the 51.7% majority-class base rate the result is
overwhelmingly significant: **z ≈ 8.5, p < 1e-15** (binomial, n=439).

Adding OVX is the one principled free-data upgrade that earned its keep: implied vol is
forward-looking, so the level R² nearly **doubled (0.30 → 0.50, and 0.23 → 0.40 ex-2020), improving
in almost every individual year** — a real, leak-free gain (OVX at day *t* is known at *t*), not a
tuning artifact. The model falls back to pure HAR if OVX is ever unavailable.

Discipline, shown both ways (the deliberate non-mistakes): every candidate feature had to beat the
existing model out-of-sample or it was rejected. The standard HAR *leverage* enhancement — downside
realized semivariance, so down days predict higher future vol — was tested and did **not** improve
out-of-sample (71.5% vs 72.0% on the base HAR, R² 0.300 vs 0.304), so it was **left out**. OVX
*was* tested and clearly helped, so it was **kept**. A feature gets in only when it earns its keep;
adding complexity that does not is exactly how vol models overfit. Net: the model is HAR-IV (three
realized-vol terms plus OVX), nothing more.

Honest scope, because the lesson of the direction signal is to not oversell. This is a **clean
implementation of a known effect, not novel alpha**, and it is a **vol forecast, not a directional
return signal** — you do not make directional P&L from it. The strong, robust part is the
direction/regime call; the level forecast only ties the naive baseline on R² (it beats it on MAE),
so level calibration is the part still worth improving.

**Does it convert to P&L? Tested, and honestly: no.** A volatility-targeting overlay (scale a long
WTI position inversely to forecast vol, 5 bps/turn costs, 2016–2026) did **not** beat buy-and-hold
on a risk-adjusted basis (Sharpe 0.36 buy-hold vs 0.28 naive-lagged-vol target vs **0.27** HAR
target), and the HAR forecast did **not** beat the trivial naive-lagged-vol sizing. This matches the
skeptical literature on vol-managed portfolios (the benefit is fragile out-of-sample) and the fact
that WTI's own return premium is weak. So the forecast's honest standing is: a **validated,
statistically significant volatility/regime indicator** (useful for risk monitoring and as an
options-vol input), **not** a demonstrated source of trading profit. Reporting this negative result
rather than tuning the overlay until it looks good is the whole point.

```bash
python -m backend.vol_forecast      # reproduces the table above and writes the artifact
```
Artifact: [`data/vol_forecast_validation.json`](data/vol_forecast_validation.json).

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
commands produce the corrected coin-flip result, not the original 2.44:
```bash
# 5-year, corrected (1d + 1w)
python -m backend.backtest_walk_forward --period 5y --min-train 200 --step 5 --features no_macro --lag-context 1

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
- **Term-structure carry: no directional edge.** Carry (backwardation vs contango) is a top
  cross-sectional commodity factor, so it was the most promising remaining directional signal. Tested
  honestly on free EIA futures-curve data (RCLC1/RCLC2) with returns from the back-adjusted continuous
  series — to avoid the spurious roll-down of the raw front-contract series — and a purged monthly
  walk-forward (2004–2024, n=896): **48.1% direction accuracy vs a 55.4% base rate (p = 1.0)**, and a
  carry-timed strategy returned Sharpe −0.03 vs 0.25 buy-and-hold. The data even leans *opposite* to the
  textbook factor (contango → higher forward returns), but that is the crash-recovery cycle, not a robust
  signal, and trading the reverse would be reverse-engineering the in-sample answer.
  [`backend/carry_signal_test.py`](backend/carry_signal_test.py) ·
  [`data/carry_signal_test.json`](data/carry_signal_test.json).

- **Variance risk premium: real, but not a clean edge here.** Oil's implied vol (OVX, free) exceeds
  subsequent realized vol by ~2.2 points on average (positive 71% of months, 2007–2026) — the VRP is
  genuinely there. But conditioning the short-vol capture on my realized-vol forecast did **not** improve
  it (the "implied-rich" half did no better than the "cheap" half), the proxy Sharpe (~0.38) **ignores the
  severe left-tail** of short-vol (2008/2014/2020 vol explosions), and harvesting it cleanly needs oil
  options data that is not freely available with long history. Reported as a real market fact, not a
  strategy.

The pattern across every directional test is consistent and is itself the finding: **WTI direction is
not forecastable from the signals reachable here** (momentum was a leak; carry has no edge), and the
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
- **Live track record** — CI records one 1W call per day and scores it when it resolves a
  week later ([`backend/live_record.py`](backend/live_record.py), record in
  [`data/live_track_record.json`](data/live_track_record.json)). **Every entry and every
  resolution is timestamped by a bot commit, so the record cannot be back-dated.** Calls
  spanning a contract roll are skipped, not scored; NEUTRAL means "no trade" and is never
  counted. Displayed separately from the backtest and flagged too-few-to-validate until
  n ≥ 18. A GitHub Actions job also emails on stance changes
  ([`backend/signal_alert.py`](backend/signal_alert.py)).

---

## Supply-shock event study (decision-support, not alpha)

A separate layer for the scenario where ML models are *least* reliable — geopolitical supply
shocks. It makes no predictive claim; it answers the question a discretionary PM actually asks
during an event: **"how have structurally similar shocks actually resolved?"**

- **36 defined supply-shock events, 1990–2024** (wars, OPEC cuts, hurricanes, sanctions,
  strait incidents); the dashboard shows the **35 with a complete computed forward price
  response** (the most recent event lacks enough forward data to score). Only the event date and
  barrels-at-risk are hand-entered, each with a source note. **Every price number — peak %,
  days-to-peak, settle %, trajectory — is computed from EIA's official daily WTI Cushing spot
  series (RWTC)**, not transcribed by hand.
  ([`backend/supply_shock_playbook.py`](backend/supply_shock_playbook.py))
- **The finding the dashboard leads with:** events with real barrels lost (>0.5 mbpd) hold
  their gains into settlement; threat-only events with no physical supply loss spike and fade.
  The market pays for disruption, not headlines.
- **Day-0 momentum check:** a strong first-day reaction (≥ +3%) historically led to a *higher*
  median eventual peak than a muted open — an empirical test of the "it's already priced in"
  reflex before anyone trades on it.
- **News-flow regime guardrail:** a recency-weighted score over NewsAPI headlines
  (LOW / ELEVATED / HIGH / CRITICAL; the last 6 hours weighted ~20× over week-old background
  noise). It does exactly two jobs: flag breaking-news novelty spikes, and attach an explicit
  caveat to the ML forecast in HIGH/CRITICAL regimes — the model is trained on normal-market
  data and will underestimate tail risk. *(Implementation is keyword matching, not deep NLP —
  it is a transparent proxy and labeled as such.)*

```bash
python backend/supply_shock_playbook.py   # print the full event-study table from EIA data
```

---

## Architecture

- **[`backend/oil.py`](backend/oil.py)** — data ingestion, feature engineering, 6-model
  ensemble, news-regime score.
- **[`backend/backtest_walk_forward.py`](backend/backtest_walk_forward.py)** — the validation
  engine: walk-forward with baselines, binomial p-values, Wilson CIs, and dollar P&L (Sharpe,
  win rate, drawdown, profit factor). `--features {all,no_macro,price_only}` enables the leakage
  comparison; `--train-window` switches expanding to rolling (378 bars = the production 18-month
  window); `--horizons` and `--period` select the test set. Non-positive prices (the 2020 negative
  settle) are dropped before feature engineering. **Purges the last `horizon_steps − 1` training
  rows per step** so no label matures after the prediction point — the fix that exposed the
  headline as leakage.
- **[`backend/vol_forecast.py`](backend/vol_forecast.py)** — HAR-IV realized-volatility forecaster
  (realized vol + OVX implied vol), the project's validated signal (next-week vol direction 72.7%
  OOS, p < 1e-15, leak-free). Falls back to pure HAR if OVX is unavailable.
- **[`backend/carry_signal_test.py`](backend/carry_signal_test.py)** — documented NEGATIVE result:
  term-structure carry (EIA futures curve) does not time WTI direction.
- **[`backend/supply_shock_playbook.py`](backend/supply_shock_playbook.py)** — EIA-computed
  supply-shock event study.
- **[`backend/server.py`](backend/server.py)** — Flask API; merges live predictions with the
  walk-forward stats artifact.
- **[`backend/signal_alert.py`](backend/signal_alert.py)** — stance-change email alerts (CI).
- **[`backend/live_record.py`](backend/live_record.py)** — git-committed live track record
  (record daily, resolve weekly, skip contract rolls).
- **[`freeze.py`](freeze.py)** + **[`.github/workflows/refresh.yml`](.github/workflows/refresh.yml)**
  — hourly frozen snapshot deployed to GitHub Pages (no running server).
- **[`src/`](src)** — React dashboard (lightweight-charts, hand-written CSS).
- **[`data/`](data)** — evidence artifacts only: the walk-forward backtest, the macro- and
  timing-leakage comparisons, the EIA spot cache, the live track record, and the signal
  state. Per-contract runtime files are gitignored.
- **[`tests/`](tests)** — 39 unit tests, network-free, run in CI on every code push
  ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)). They include a look-ahead **leak
  check** on the vol-forecast feature builder, a **purge-invariant guard** on the backtest fix, the
  live-record resolution/contract-roll logic, and the **retraction guarantee** (a non-significant
  model never surfaces a lean). Locally: `PYTHONPATH=. python -m unittest discover -s tests`.

### Models
Ensemble of Random Forest, Extra Trees, Ridge, Elastic Net, XGBoost, LightGBM, blended with
validation-aware weighting, calibrated prediction intervals, and a drift-challenger baseline.

### Feature set (deployed, leakage-proof)
Technical indicators (RSI, MACD, Bollinger, momentum, volatility, OBV) + cross-asset/
term-structure context (Brent–WTI spread, DXY, VIX/OVX, rates, XLE/XOP, front-next spread),
**lagged one trading day** so every feature is observable before the entry print. FRED/EIA
macro features are available but **off by default** (see validation notes above).

---

## Installation & usage

```bash
# 1. Install
pip install -r requirements.txt
cp .env.example .env          # add API keys (EIA, NewsAPI, ...)

# 2. Run locally (Flask API on :9000 + Vite dashboard on :3000)
./dev.sh

# 3. Reproduce the validated backtest (leakage-proof config)
python -m backend.backtest_walk_forward --period 5y --min-train 200 --step 5 --features no_macro --lag-context 1

# 4. Compare feature configurations (leakage test)
python -m backend.backtest_walk_forward --period 5y --features all         # with macro
python -m backend.backtest_walk_forward --period 5y --features price_only  # technical only
```

### API endpoints
- `GET /` — service status / readiness
- `GET /data` — full dashboard payload (predictions + walk-forward stats + event study)
- `GET /health` — health check

---

## Free static hosting (GitHub Pages, no server)

The dashboard runs with **zero running infrastructure** using the "frozen Flask" pattern:

1. **`freeze.py`** runs the full pipeline in-memory and renders the `/data` endpoint (via
   Flask's `test_client`) to a static `public/data.json`.
2. **`npm run build`** with `VITE_STATIC_DATA=true` produces a static site that fetches that
   frozen JSON instead of polling a live backend.
3. **`.github/workflows/refresh.yml`** runs hourly on GitHub's servers: freeze → build → deploy
   to the `gh-pages` branch. If a run fails (rate limit, API outage), the previous good
   snapshot stays live.

This removes the failure modes of a live free-tier server (cold starts, spin-downs, OOM during
model training) at the cost of data being as fresh as the last hourly refresh — appropriate for
a 1-week forecast. The UI shows an honest `DATA AS OF <time>` label in this mode.

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
  resolved call per week) and is displayed separately. Given the corrected backtest, the prior
  expectation for it is no edge.
- **Macro features were excluded, and that decision is now moot.** A pre-retraction A/B
  suggested a possible macro uplift, but it ran on the same leaked pipeline and is not meaningful
  evidence. FRED/EIA are latest-vintage (not ALFRED point-in-time), so any uplift could be
  revision look-ahead regardless; macro stays out of the model. With the base signal dead, this
  is no longer a live question.
- **The news regime score is a keyword proxy**, not NLP — useful as a guardrail and novelty
  flag, labeled as such, and never used as a trading signal.
- **News latency.** The geo feed is cached 30 minutes (NewsAPI free tier) — appropriate for
  context, not for low-latency execution.
- **Not investment advice.** This is a research system, not a production trading desk.

---

## Data integrity policy

Real market data only — no synthetic or placeholder values, and no hand-transcribed price
moves (the event study computes every move from EIA's official series). The system fails fast
with explicit errors when real data is unavailable, and labels any degraded horizon rather
than blending it into a misleading headline number.
