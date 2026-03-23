# 📅 23 March 2026 Update: Deep Logical Audit & Resilience Upgrade

After migrating to advanced model architectures (XGBoost + LightGBM) and introducing the 7-source alternative data pipeline in January, a comprehensive **Senior-Level Logical Audit** was performed on the `oil.py` prediction engine today. 

We discovered and patched **11 critical-to-medium severity logic flaws** that were silently degrading model training, corrupting feature data, or causing API timeouts.

---

## 🚀 1. Alternative Data Pipeline Hardening

**The challenge:** Premium APIs are often flaky, slow, or return unexpected formats (like comma-separated strings), leading to corrupted inputs or silent fallbacks to neutral data.

- **EIA API Exponential Backoff (Fixed Timeout):** The EIA v2 API for weekly crude oil stocks frequently times out. We reduced the requested payload size from 20 to 10 rows and built a custom **exponential backoff retry loop (1s, 2s)** with an extended 15s timeout. *Result: The pipeline now successfully recovers from EIA network latency without failing the signal.*
- **USDA Normalization Corrected:** Agricultural impact scoring (corn prices affecting biofuel demand) was flawed. The previous divisor `(/5)` resulted in a feature scale of 0.8–1.6. We switched this to a multiplier `(*10)`, properly spreading the signal across the intended `0-100` normalization scale for tree models.
- **NewsAPI Sentiment Momentum:** Fixed a sorting logic bug where momentum (Newer articles vs Older articles) was inadvertently inverted. Furthermore, if the API fails, the system now returns a **genuinely neutral market buzz score of 50** instead of 0 (which the models were previously interpreting as a catastrophic bearish signal).
- **FRED Sensitivity Boost:** The USD economic stability formula was multiplying nominal daily changes by 10, resulting in a constant `~99.9` feature score. We increased the scalar to **2000x**, ensuring micro-fluctuations in currency pairs translate meaningfully into the model's feature space.

---

## 🧠 2. ML Engine & Training Pipeline Fixes

**The challenge:** The 18-model ensemble (6 models × 3 horizons) was occasionally dropping models silently due to ZeroDivisionErrors or feeding corrupted target data at the trailing edge of the dataset.

- **Zero-Variance Validation Guardrails:** In tight consolidation periods, TimeSeriesSplit validation folds can have zero variance (`np.var(y_val) == 0`). We added a strict guardrail to return a neutral score of `0.0` rather than triggering a silent `ZeroDivisionError` that secretly removed models from the ensemble.
- **Target Data Leakage Eliminated:** Fixed a windowing slicing bug (`i+5` clamp) that resulted in the last 4-5 training samples using the exact same future target price regardless of their input features. 
- **NaN Prediction Shielding:** Input features containing transient `NaN`s (from unpredictable API responses) were causing XGBoost/LightGBM to predict `NaN`. These NaN predictions are now explicitly blocked from the weighted ensemble averaging arrays.
- **Safe Multi-Horizon Fallbacks:** If the 1D daily training pipeline fails drastically, the 1H pipeline fallback historically crashed via `KeyError: '1d'`. This is now wrapped in a safe `current_price` fallback.

---

## 📊 3. Technical Indicator Overhaul

**The challenge:** Hand-rolled math for technical indicators can diverge significantly from industry standards (Bloomberg, TradingView), confusing models trained to recognize institutional chart setups.

- **Wilder's RSI Exponential Smoothing:** Completely re-wrote the Relative Strength Index (RSI) function. The previous `gains.mean()` logic only looked at 14 bars and threw away historical context. We implemented **J. Welles Wilder’s true exponential smoothing**, guaranteeing parity with institutional charting platforms.
- **Removed Price-Scale Contamination:** Raw Dollar values for Bollinger Bands (`bb_upper`, `bb_lower` ~ $80-$100) were leaking absolute price scale into an otherwise normalized feature space (0-1, or %). We stripped these out completely, retaining only the clean relative signals (`bb_width`, `bb_position`).
- **MACD Efficiency:** Eliminated redundant duplicate Exponential Moving Average (EMA-12, EMA-26) calculations within the divergence tracker.

---

## ⚙️ 4. System Logic & Tracking

- **Dynamic Contract Rollovers:** Fixed a synchronization bug where the system detected a live Futures Contract rollover (e.g., CLH to CLJ) but failed to update the `yfinance_symbol` for the historical data fetcher, resulting in stale training data.
- **Strict Accuracy Time Windows:** The Accuracy Tracker previously matched "predictions" to "actual prices" indefinitely. A 1H prediction could be matched to a price swing 7 days later. We enforced strict maximum match windows (e.g., 6 hours for a 1H prediction, 3 days for a 1D prediction).
- **Dependency Cleanup:** Removed orphaned imports (Lasso, GradientBoosting) mapping back to the January 24th architecture switch, optimizing memory overhead.

### 🏁 Summary
The prediction engine is now fundamentally sound and mathematically rigorous. Features are properly normalized, API failures are gracefully handled with retry/backoff loops, and the ML models receive zero leaking target data. 

**Latest Live Test:** `Exit code: 0`. 18 models successfully trained. 59 engineered features. 7/7 External APIs successfully parsed. Current Market Regime: `HIGH_VOLATILITY`.
