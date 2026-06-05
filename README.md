# WTI Crude Oil Futures — Forecasting & Geopolitical Risk System

A walk-forward-validated machine-learning system for WTI crude oil futures, paired with a
geopolitical-risk decision-support layer. Built and tested with an emphasis on **honest,
out-of-sample evidence** — every headline number below comes from a non-overlapping
walk-forward backtest with transaction costs, and the components that *don't* work are
labeled as such rather than hidden.

---

## Headline result (the part that is real)

**1-week horizon — statistically and economically significant**, measured on 199
non-overlapping out-of-sample predictions over 5 years (expanding-window walk-forward,
$100/contract round-trip cost, 1 contract = 1,000 bbl):

| Metric | 1-Week Signal |
|---|---|
| Direction accuracy | **62.8%** (95% CI: 55.9%–69.2%) |
| Statistical significance vs coin-flip | **p = 0.0002** |
| Annualized Sharpe (after costs) | **2.07** |
| Expected P&L per trade | **+$1,058** |
| Win rate | 62.8% |
| Profit factor | 2.13 |
| Max drawdown | $19,900 |
| Out-of-sample samples | 199 |

### Why this is credible, not luck

The result was **stress-tested against the obvious failure modes** before being believed:

1. **Not a trending-market artifact.** On the identical window, buy-and-hold scored
   Sharpe ≈ **0.00** and naive momentum scored Sharpe ≈ **−1.03**. The market handed out no
   free trend; weekly WTI was mean-reverting, and the dumb strategies lost.
2. **Not concentrated in one event.** Momentum loses in *every* calendar year 2021–2026, so
   the edge is not a single 2022 war spike.
3. **Not look-ahead leakage from revised macro data.** The signal was re-run with **all
   FRED/EIA macro features removed** (the only revision-prone data source). It did not
   weaken — it *improved* (Sharpe 2.07 vs 1.90). The deployed model therefore uses the lean,
   leakage-proof feature set (price/technical + point-in-time market data only).

Reproduce:
```bash
python -m backend.backtest_walk_forward --period 5y --min-train 200 --step 5 --features no_macro
```

---

## What does NOT work (stated plainly)

- **1-Day horizon: excluded from trading use.** 45–47% direction accuracy (below random),
  negative Sharpe. It is shown on the dashboard explicitly flagged `BELOW RANDOM — not for
  directional use`, never as a tradeable signal.
- **1-Hour horizon: removed.** Direction accuracy was indistinguishable from noise and never
  reached enough samples to test. It is not displayed as a horizon.

This is deliberate: a forecasting tool that hides its dead horizons is worse than one that
flags them. Only the 1-week signal survived honest validation.

---

## Geopolitical risk engine (decision-support, not alpha)

A separate layer designed for the scenario where ML models are *least* reliable —
geopolitical phase transitions (e.g. Middle East supply shocks). It does **not** claim
predictive accuracy; it surfaces context a discretionary trader would otherwise assemble by
hand:

- **Recency-weighted regime score** (LOW / ELEVATED / HIGH / CRITICAL) from NewsAPI headlines.
  Articles in the last 6 hours are weighted 20× over week-old background noise, so a genuine
  breaking crisis registers differently from the perpetual low-grade Iran/oil news flow.
  *(Implementation is keyword/entity matching, not deep NLP — it is a transparent proxy.)*
- **Historical analogue matching** against 13 verified supply-shock events (Gulf War 1990,
  Abqaiq 2019, Russia-Ukraine 2022, etc.) with realized WTI peak/settle moves.
- **Strait of Hormuz scenario engine** with dollar price targets. Scenario **probabilities are
  illustrative subjective priors** (a full closure has never occurred, so there is no base rate
  to calibrate) and dollar impacts cite EIA/IEA supply-elasticity references. Both are labeled
  as such in the UI and code.
- **Probability-weighted expected impact** and **edge vs current price** (analogue-implied fair
  value minus market price) as decision-support summaries.

When the regime is HIGH/CRITICAL, the dashboard raises an explicit caveat that the ML model is
trained on normal-market data and will underestimate geopolitical tail risk.

---

## Architecture

- **`backend/oil.py`** — core engine: data ingestion, feature engineering, 6-model ensemble,
  geopolitical engine.
- **`backend/server.py`** — Flask API; merges live predictions with walk-forward stats.
- **`backend/backtest_walk_forward.py`** — expanding-window walk-forward backtest with
  baselines, statistical significance (binomial p-value, Wilson CI), and dollar P&L
  (Sharpe, win rate, drawdown, profit factor). Supports `--features {all,no_macro,price_only}`
  for leakage testing.
- **`src/`** — React/Vite dashboard (Bloomberg-terminal styling).
- **`data/`** — persistent prediction/accuracy storage + `walk_forward_backtest_latest.json`.

### Models
Ensemble of Random Forest, Extra Trees, Ridge, Elastic Net, XGBoost, LightGBM, blended with
validation-aware weighting, calibrated prediction intervals, and a drift-challenger baseline.

### Feature set (deployed, leakage-proof)
Technical indicators (RSI, MACD, Bollinger, momentum, volatility, OBV) + point-in-time
cross-asset/term-structure context (Brent–WTI spread, DXY, VIX/OVX, rates, XLE/XOP, front-next
spread). FRED/EIA macro features are available but **off by default** (see validation note above).

---

## Installation & usage

```bash
# 1. Install
pip install -r requirements.txt

# 2. Run the full system (Flask API on :9000 + background updates)
python run_complete_system.py

# 3. Reproduce the validated backtest (leakage-proof config)
python -m backend.backtest_walk_forward --period 5y --min-train 200 --step 5 --features no_macro

# 4. Compare configurations (leakage test)
python -m backend.backtest_walk_forward --period 5y --features all        # with macro
python -m backend.backtest_walk_forward --period 5y --features price_only  # technical only
```

### API endpoints
- `GET /` — service status / readiness
- `GET /data` — main dashboard payload (predictions + walk-forward stats + geo risk)
- `GET /scenario` — standalone geopolitical scenario analysis
- `GET /health` — health check

---

## Honest limitations

- **Single regime.** The 5-year test window (2021–2026) covers COVID recovery, the Russian
  invasion, and OPEC+ cuts. The 1-week edge is validated on this period only; performance in a
  prolonged low-volatility, range-bound regime is unproven.
- **Macro features use latest-vintage data.** They are off by default precisely because they
  are not point-in-time (ALFRED) corrected. Re-enabling requires a vintage audit.
- **Geopolitical probabilities are subjective**, not empirically calibrated (labeled as such).
- **News latency.** The geo feed is cached 30 minutes (NewsAPI free tier) — appropriate for
  context, not for low-latency execution.
- **Not investment advice.** This is a research/portfolio system, not a production trading desk.

---

## Data integrity policy

Real market data only — no synthetic or placeholder values. The system fails fast with explicit
errors when real data is unavailable, and labels any degraded/fallback horizon rather than
blending it into a misleading headline number.
