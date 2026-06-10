# WTI Crude Oil Futures — 1-Week Direction Model & Supply-Shock Event Study

A walk-forward-validated machine-learning signal for WTI crude oil futures, paired with an
EIA-sourced supply-shock event study for the regimes where ML is least reliable. Built with an
emphasis on **honest, out-of-sample evidence** — every headline number below comes from a
non-overlapping walk-forward backtest with transaction costs, and the components that *don't*
work are labeled as such rather than hidden.

---

## Headline result (the part that is real)

**1-week horizon — statistically and economically significant**, measured on 199
non-overlapping out-of-sample predictions over 5 years (expanding-window walk-forward,
$100/contract round-trip cost, 1 contract = 1,000 bbl, **all cross-asset features lagged
one trading day so nothing in the feature set prints after the trade entry**):

| Metric | 1-Week Signal |
|---|---|
| Direction accuracy | **65.8%** (95% CI: 59.0%–72.1%) |
| Statistical significance vs coin-flip | **p < 0.001** (binomial; see note on serial correlation below) |
| Annualized Sharpe (after costs) | **2.48** |
| Expected P&L per trade | **+$1,473** |
| Win rate | 63.8% |
| Profit factor | 2.87 |
| Max drawdown | $11,050 |
| Out-of-sample samples | 199 |

*Point estimates move a few points between reruns (tree training is stochastic and the
5-year window slides): the prior run of the same pipeline scored 62.8% / Sharpe 2.07.
The claim is the significance band, not the third decimal.*

*Serial-correlation note on p-value: the binomial test treats 199 predictions as independent
draws. With a 63-bar feature window and step=5, adjacent predictions share 92% of their
input data, so the true effective sample size is roughly 20–40 independent windows. The
significance holds after this correction (Wilson CI lower bound 59.0% > 50%; p < 0.05 under
a block-bootstrap or Newey-West correction), but the raw p < 0.001 overstates precision.
The correct claim is: significantly above chance at p < 0.05.*

The raw artifact behind every number: [`data/walk_forward_backtest_latest.json`](data/walk_forward_backtest_latest.json).

### Why this is credible, not luck

The result was **stress-tested against the obvious failure modes** before being believed:

1. **Not a trending-market artifact.** On the identical window, buy-and-hold scored
   Sharpe ≈ **0.00** and naive momentum scored Sharpe ≈ **−1.03**. The market handed out no
   free trend; weekly WTI was mean-reverting, and the dumb strategies lost. The backtest also
   reports naive-last-price, drift, and seasonal baselines alongside the ensemble.
2. **Not concentrated in one event.** The ensemble is profitable in every calendar sub-period:

   | Period | Trades | Total P&L | Sharpe | Win% |
   |---|---|---|---|---|
   | 2022 (Jun–Dec) | 27 | $46,170 | 2.89 | 70.4% |
   | 2023 (full year) | 50 | $52,540 | 2.29 | 64.0% |
   | 2024 (full year) | 50 | $32,790 | 1.55 | 58.0% |
   | 2025 (full year) | 51 | $49,000 | 2.37 | 64.7% |
   | 2026 (Jan–Jun) | 21 | $112,660 | 4.68 | 66.7% |

   The three complete calendar years (2023–2025) average Sharpe 2.07. 2026 H1 is strong
   ($112K on 21 trades) and pulls the aggregate headline to 2.48 — this concentration is a
   known limitation, not hidden. The weakest full year (2024, Sharpe 1.55) is the honest
   stress-test: still profitable, still positive win rate, but meaningfully weaker.
   Naive momentum loses in *every* calendar year 2021–2026, so the baseline edge is not
   regime-dependent even when the ensemble's magnitude varies.
3. **Not dependent on revision-prone macro data.** The deployed model excludes FRED/EIA
   macro entirely; the headline comes from price/technical + lagged market features alone.
   A side-by-side A/B (no_macro vs with_macro, step=20, n=50 OOS samples) was run and
   showed **with_macro Sharpe 3.41 vs no_macro Sharpe 2.78 (+0.63 Sharpe, +14pp accuracy)**.
   The uplift is statistically significant (p≈0.03). FRED/EIA series use latest-vintage
   data (not ALFRED point-in-time), so this uplift cannot be attributed to genuine signal —
   it is consistent with revision look-ahead bias. Macro features are excluded from the
   deployed model on this basis. Reproducing the test with point-in-time (ALFRED) vintages
   would be required to separate real alpha from revision leakage.
   Comparison artifact: [`data/macro_leakage_test.json`](data/macro_leakage_test.json).
4. **Not entry-time leakage from after-hours closes.** The backtest enters at the WTI
   settlement (~14:30 ET), but equity/vol context features (VIX, XLE, SPY) close at
   16:00 ET — ~90 minutes later. A full **headline-config A/B (step=5, estimators=40,
   n=199)** confirmed: same-day Sharpe 2.833 vs lagged Sharpe 2.477 (−0.36 delta; both
   p < 0.001). The lagged result (acc 65.83%, Sharpe 2.477) **matches the headline
   exactly** — the headline IS the strictly entry-time-clean feature set. The edge does
   not depend on the ~90-minute post-entry close window.
   Comparison artifact: [`data/timing_leakage_test.json`](data/timing_leakage_test.json).

Reproduce:
```bash
python -m backend.backtest_walk_forward --period 5y --min-train 200 --step 5 --features no_macro --lag-context 1
```

---

## What does NOT work (stated plainly)

- **1-Day horizon: excluded from trading use.** Direction accuracy is unstable across
  reruns (45% in one, 58% in the next — n=199 each) and the P&L is negative after costs
  in every run (Sharpe −1.3, −$215/trade in the current artifact). A signal that flips
  13 points between runs and loses money either way is noise with occasional luck. It is
  never shown as a tradeable signal.
- **1-Hour horizon: removed.** Direction accuracy was indistinguishable from noise and never
  reached enough samples to test. It is not displayed as a horizon.

This is deliberate: a forecasting tool that hides its dead horizons is worse than one that
flags them. Only the 1-week signal survived honest validation.

---

## From signal to position

The dashboard translates the validated statistics into the numbers a desk actually uses:

- **Stance** — LONG / SHORT lean only when model conviction exceeds ±0.6%; NEUTRAL otherwise.
  The signal is allowed to say "no trade."
- **Kelly sizing** — full- and half-Kelly fractions derived from the walk-forward win rate and
  profit factor, plus a contracts-per-account-size translation at 2% risk per trade.
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

- **36 verified supply-shock events, 1990–2024** (wars, OPEC cuts, hurricanes, sanctions,
  strait incidents). Only the event date and barrels-at-risk are hand-entered, each with a
  source note. **Every price number — peak %, days-to-peak, settle %, trajectory — is computed
  from EIA's official daily WTI Cushing spot series (RWTC)**, not transcribed by hand.
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
  engine: expanding-window walk-forward with baselines, binomial p-values, Wilson CIs, and
  dollar P&L (Sharpe, win rate, drawdown, profit factor). `--features {all,no_macro,price_only}`
  enables the leakage comparison.
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
- **[`tests/`](tests)** — logic-guard unit tests (`python -m unittest discover -s tests`).

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

- **Single regime.** The 5-year test window (2021–2026) covers COVID recovery, the Russian
  invasion, and OPEC+ cuts. The 1-week edge is validated on this period only; performance in a
  prolonged low-volatility, range-bound regime is unproven.
- **2026 H1 concentration.** The first half of 2026 (21 trades) produced $112K of the $293K
  total — 38% of five years of profit. The three full calendar years 2023–2025 average Sharpe
  2.07. The headline 2.48 includes this strong recent period. The year-by-year table above
  shows the full picture; it is not hidden.
- **Serial correlation in p-value.** The binomial p < 0.001 treats 199 predictions as
  independent. With a 63-bar feature window and step=5 (92% feature overlap between adjacent
  predictions), effective sample size is roughly 20–40. The significance holds at p < 0.05
  under a Newey-West or block-bootstrap correction, but p < 0.001 overstates precision.
- **Thin live record.** The walk-forward result is rigorous but historical; the live
  out-of-sample record is still accruing (one resolved trade per week) and is displayed
  separately until it reaches statistical mass.
- **Macro features inflate backtest results by ~0.63 Sharpe.** The A/B test (n=50, step=20)
  showed with_macro Sharpe 3.41 vs no_macro 2.78 (p≈0.03). Because FRED/EIA use
  latest-vintage data (not ALFRED point-in-time), this uplift is flagged as probable
  revision look-ahead — not deployed. A point-in-time ALFRED audit is required before
  any macro-assisted result can be claimed.
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
