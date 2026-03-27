# WTI Oil Price Prediction System

A complete real-time WTI crude oil futures price prediction system using machine learning. **NO FALLBACK DATA - REAL VALUES ONLY**.

## System Overview

This system provides:
- **Real-time WTI futures price fetching** from yfinance with automatic contract switching
- **Multi-horizon ML predictions** (1 hour, 1 day, 1 week) using ensemble methods
- **Persistent storage** of predictions and actual prices for accuracy tracking
- **REST API server** for frontend integration
- **Complete orchestration** with automatic background updates

## Key Features

### ✅ Real Data Only
- No random values, no placeholders, no fallback data
- System fails fast with clear errors if real data unavailable
- Automatic WTI futures contract detection and switching

### ✅ ML Predictions
- Ensemble of tree and linear models (Random Forest, Extra Trees, Elastic Net, Ridge, XGBoost, LightGBM)
- Multi-horizon predictions: 1H, 1D, 1W
- Real accuracy tracking and confidence calculation
- Adaptive interval calibration that responds to realized coverage gaps
- External data integration (economic indicators, sentiment, weather)
- Cross-asset and term-structure context features (Brent-WTI spread, DXY, VIX/OVX, rates, XLE, front-next spread)

### ✅ Data Storage
- JSON-based persistent storage in `data/` directory
- Contract-specific files (e.g., `CLV25_predictions.json`)
- Historical accuracy metrics tracking
- Automatic contract switching without data loss

### ✅ API Integration
- RESTful API for frontend consumption
- Real-time data updates every 3 minutes
- All required frontend fields calculated correctly

## Files Structure

- **`oil.py`** - Core prediction engine with ML models
- **`server.py`** - Flask API server for frontend integration
- **`run_complete_system.py`** - Complete system orchestrator
- **`backtest_walk_forward.py`** - Expanding-window walk-forward backtest with baselines
- **`data/`** - Persistent storage directory
- **`requirements.txt`** - Python dependencies

## Frontend Data Fields

The system correctly calculates and provides all these fields:

```json
{
  "security": "CLV25",
  "security_full_name": "WTI CRUDE CLV25", 
  "last_price": 62.80,
  "change": -0.340,
  "percent_change": -0.54,
  "volume": 251652,
  "ml_prediction": 63.87,
  "accuracy": "75%",
  "confidence": "80%",
  "multi_horizon_predictions": {
    "prediction_1h": 63.87,
    "prediction_1d": 63.87, 
    "prediction_1w": 63.87,
    "is_real_prediction": true
  },
  "horizon_changes": [
    {"period": "1H", "value": "+1.7%"},
    {"period": "1D", "value": "+1.7%"},
    {"period": "1W", "value": "+1.7%"}
  ],
  "data_points": 50,
  "feed_status": "REAL-TIME",
  "status": "ACTIVE"
}
```

## Installation & Usage

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Complete System
```bash
python run_complete_system.py
```

### 3. Run Individual Components

**Test predictions only:**
```bash
python run_complete_system.py --test
```

**Validate system:**
```bash
python run_complete_system.py --validate
```

**Run server only:**
```bash
python server.py
```

**Run walk-forward backtest (1D/1W + baselines):**
```bash
python backtest_walk_forward.py --period 18mo --min-train 140 --step 5 --estimators 40
```

### 4. API Endpoints

- `GET /` - Service status and readiness
- `GET /data` - Main data endpoint for frontend
- `GET /health` - Health check

## Render Deployment

This repo now includes [render.yaml](/Users/navnoorbawa/Downloads/RESUME%20PROJECTS/WTI%20Crude%20Oil%20Futures%20Prediction/WTI-Crude-Oil-Futures-main/render.yaml) so frontend/backend service wiring is defined in code instead of drifting in the Render dashboard.

### Frontend environment

- `VITE_API_BASE_URL=https://wti-crude-oil-backend.onrender.com`
- `VITE_POLL_INTERVAL_MS=15000`
- `VITE_STARTUP_RETRY_MS=5000`

### Backend environment

- `EAGER_ML_WARMUP=false`
- `API_STARTUP_RETRY_SECONDS=5`

### Startup behavior

- `GET /` and `GET /health` report initialization state consistently.
- `GET /data` can return `SYSTEM_INITIALIZING` with `retry_after_seconds` while Render wakes the free instance.
- The frontend treats this as a loading/warm-up state and retries instead of showing a hard failure page.

## Contract Management

### Automatic Contract Switching
- Detects current active WTI futures contract
- Uses generic `CL=F` symbol for maximum reliability  
- Auto-switches 5 days before contract expiry
- Handles contract transitions seamlessly

### Current Contract: Dynamic (auto-detected)
- Symbol: Active WTI contract (e.g., CLK26)
- YFinance Symbol: CL=F
- Expiry: Calculated from active contract month/year
- Days to expiry: Calculated automatically

## ML System Details

### Models Used
- Random Forest
- Extra Trees
- Elastic Net
- Ridge
- XGBoost
- LightGBM

The system blends model outputs with validation-aware weighting and applies calibrated
prediction intervals plus drift-aware confidence adjustment per horizon.

### Features
- Technical indicators (RSI, MACD, Bollinger Bands)
- Price momentum and volatility
- External economic data
- Market sentiment analysis
- Weather data integration

### Accuracy Tracking
- Real-time accuracy calculation
- Historical performance metrics
- Confidence scoring based on model consensus, interval width, and feature drift
- No predictions accepted below quality thresholds
- Horizon-level rolling metrics and interval coverage tracking
- Walk-forward backtest script to compare ensemble vs naive/drift/seasonal baselines

## Error Handling

### Critical Failures
System fails fast with clear messages for:
- No real WTI data available
- Invalid contract symbols
- ML prediction failures  
- Data quality issues

### No Fallback Policy
- No random data generation
- No placeholder values
- No approximations or estimates
- Real data or system failure

## Data Storage

### File Structure
```
data/
├── CLV25_predictions.json      # ML predictions
├── CLV25_actual_prices.json    # Historical prices
├── CLV25_accuracy_metrics.json # Accuracy tracking
└── CLV25_daily_metrics.json    # Daily statistics
```

### Automatic Migration
When contracts switch (e.g., CLU25 → CLV25), new files are created automatically without losing historical data.

## System Monitoring

### Health Checks
- Contract validity monitoring
- Data quality validation
- Prediction accuracy tracking
- Error count monitoring

### Real-time Updates
- Predictions updated every 3 minutes
- Accuracy metrics recalculated continuously
- Contract expiry monitoring
- Automatic failover handling

## Production Ready

✅ **Robust Error Handling** - Fails fast, clear error messages
✅ **Real Data Only** - No shortcuts or approximations  
✅ **Scalable Architecture** - Modular, maintainable code
✅ **Persistent Storage** - No data loss on restarts
✅ **API Integration** - Ready for frontend consumption
✅ **Contract Management** - Automatic futures handling
✅ **ML Quality** - Ensemble methods, accuracy tracking

This system is production-ready and designed to provide reliable, real-time WTI oil price predictions without any placeholder or fallback data.
