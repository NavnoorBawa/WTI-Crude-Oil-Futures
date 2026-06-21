#!/usr/bin/env python3
"""
WTI Oil Prediction Server - REAL DATA ONLY
===========================================
Pure oil.py foundation - NO FALLBACKS, NO PLACEHOLDERS
Serves only real ML predictions and stored data
"""

import time
import threading
import os
import json
from datetime import datetime
from pathlib import Path
import logging
from flask import Flask, jsonify, make_response
from flask_cors import CORS

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=["*"])

# FIX #6: Global startup synchronization event
_startup_ready = threading.Event()
_startup_complete_time = None
_startup_lock = threading.Lock()
_startup_thread = None
_startup_started = False
_startup_error = None
_startup_attempts = 0
_startup_next_retry_at = 0.0
_prediction_refresh_lock = threading.Lock()

# Import oil.py functions - CRITICAL DEPENDENCY
try:
    from .oil import (
        get_current_wti_contract,
        get_multi_horizon_wti_predictions, 
        get_prediction_accuracy_metrics,
        store_actual_price_update,
        get_historical_data
    )
    logger.info("✅ Successfully imported oil.py functions")
    OIL_IMPORTS_AVAILABLE = True
except Exception as e:
    logger.critical(f"❌ CRITICAL: Cannot import oil.py functions: {e}")
    OIL_IMPORTS_AVAILABLE = False

# Global system state - REAL DATA ONLY
system_state = {
    'initialized': False,
    'ml_ready': False,
    'last_prediction_time': 0,
    'last_price_update_time': 0,
    'error_count': 0,
    'cached_predictions': None,
    'cached_accuracy': None
}

EAGER_ML_WARMUP = os.getenv('EAGER_ML_WARMUP', 'false').lower() == 'true'
_PLAYBOOK_CACHE: dict = {"data": None, "built_at": 0.0}
API_STARTUP_RETRY_SECONDS = max(2, int(os.getenv('API_STARTUP_RETRY_SECONDS', '5')))
STARTUP_RETRY_COOLDOWN_SECONDS = max(5, int(os.getenv('STARTUP_RETRY_COOLDOWN_SECONDS', '20')))
PRIMARY_DISPLAY_HORIZON = os.getenv('PRIMARY_DISPLAY_HORIZON', '1d').lower()


def json_response(payload, status_code=200, retry_after=None):
    """Standard JSON response with no-store cache semantics for live market data."""
    response = make_response(jsonify(payload), status_code)
    response.headers['Cache-Control'] = 'no-store, no-cache, max-age=0, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    if retry_after is not None:
        response.headers['Retry-After'] = str(int(retry_after))
    return response


def startup_payload(message, retry_after_seconds=None):
    retry_seconds = int(retry_after_seconds or API_STARTUP_RETRY_SECONDS)
    payload = {
        'service': 'WTI Oil Price Prediction API',
        'status': 'INITIALIZING',
        'ready': False,
        'startup_ready': False,
        'ml_ready': False,
        'error': 'SYSTEM_INITIALIZING',
        'message': message,
        'retry_after_seconds': retry_seconds,
        'server_time': datetime.now().isoformat()
    }
    if _startup_error:
        payload['startup_error'] = _startup_error
    if _startup_attempts:
        payload['startup_attempts'] = _startup_attempts
    if _startup_next_retry_at and not _startup_ready.is_set():
        payload['next_retry_at'] = datetime.fromtimestamp(_startup_next_retry_at).isoformat()
    return payload


def startup_retry_seconds():
    if _startup_next_retry_at and not _startup_ready.is_set():
        return max(1, int(round(_startup_next_retry_at - time.time())))
    return API_STARTUP_RETRY_SECONDS


def _ordered_display_horizons(preferred_horizon):
    ordered = []
    for horizon in [preferred_horizon, '1w', '1d', '1h']:
        if horizon in {'1h', '1d', '1w'} and horizon not in ordered:
            ordered.append(horizon)
    return ordered


def _load_walk_forward_stats() -> dict:
    """Load the walk-forward backtest JSON and return per-horizon ensemble metrics.
    Returns {} if file is missing or malformed — callers treat missing stats as unavailable.
    """
    try:
        path = Path(__file__).parent.parent / 'data' / 'walk_forward_backtest_latest.json'
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding='utf-8'))
        results = data.get('results', {})
        out = {}
        for horizon, hr in results.items():
            ens = hr.get('metrics', {}).get('ensemble', {})
            if not ens or not ens.get('samples'):
                continue
            pnl = hr.get('pnl_metrics', {})
            out[horizon] = {
                'direction_accuracy':  float(ens.get('direction_accuracy', 0)),
                'samples':             int(ens.get('samples', 0)),
                'direction_p_value':   float(ens.get('direction_p_value', 1.0)),
                'direction_ci_95':     ens.get('direction_ci_95', [0.0, 100.0]),
                'is_significant_5pct': bool(ens.get('is_significant_5pct', False)),
                'mae':                 float(ens.get('mae', 0)),
                'mae_improvement_pct': float(hr.get('mae_improvement_vs_best_baseline_pct', 0)),
                'pnl_sharpe':          float(pnl['sharpe_ratio_annualized']) if pnl.get('sharpe_ratio_annualized') is not None else None,
                'pnl_mean_per_trade':  float(pnl['mean_pnl_per_trade_usd']) if pnl.get('mean_pnl_per_trade_usd') is not None else None,
                'pnl_win_rate':        float(pnl['win_rate_pct']) if pnl.get('win_rate_pct') is not None else None,
                'pnl_max_drawdown':    float(pnl['max_drawdown_usd']) if pnl.get('max_drawdown_usd') is not None else None,
                'pnl_total':           float(pnl['total_pnl_usd']) if pnl.get('total_pnl_usd') is not None else None,
                'pnl_profit_factor':   float(pnl['profit_factor']) if pnl.get('profit_factor') is not None else None,
                'pnl_n_trades':        int(pnl['n_trades']) if pnl.get('n_trades') else 0,
                # Per-trade series for the OOS equity curve (present in artifacts
                # generated after the trades field was added to the backtest).
                'pnl_trades':          hr.get('trades', []),
                # Per-calendar-year Sharpe/P&L/win-rate (artifacts from Jun 2026 on).
                'yearly_breakdown':    hr.get('yearly_breakdown', {}),
            }
        return out
    except Exception as e:
        logger.warning(f'Could not load walk-forward backtest stats: {e}')
        return {}


def _load_supply_shock_playbook(current_drivers=None):
    """Load the EIA-sourced supply-shock playbook (cache-first, 6h TTL)."""
    global _PLAYBOOK_CACHE
    if _PLAYBOOK_CACHE["data"] is not None and time.time() - _PLAYBOOK_CACHE["built_at"] < 21600:
        return _PLAYBOOK_CACHE["data"]
    try:
        from .supply_shock_playbook import get_playbook_for_api
        result = get_playbook_for_api(current_drivers=current_drivers)
        if result:
            _PLAYBOOK_CACHE["data"] = result
            _PLAYBOOK_CACHE["built_at"] = time.time()
        return result
    except Exception as e:
        logger.debug(f'Supply-shock playbook unavailable: {e}')
        return _PLAYBOOK_CACHE.get("data")


def _load_live_record_summary():
    """Summary of the git-committed live 1W track record (see backend/live_record.py)."""
    try:
        path = Path(__file__).parent.parent / 'data' / 'live_track_record.json'
        if not path.exists():
            return None
        summary = json.loads(path.read_text(encoding='utf-8')).get('summary')
        return summary if isinstance(summary, dict) else None
    except Exception as e:
        logger.debug(f'Live track record unavailable: {e}')
        return None


def _build_horizon_metrics(accuracy_metrics, horizon_backtests, horizon_confidence, horizon_quality, min_live_accuracy_samples):
    # Merge in the rigorous walk-forward backtest stats (5y, 199 OOS samples) so the
    # frontend can show real p-values and confidence intervals, not just in-training diagnostics.
    wf_stats = _load_walk_forward_stats()

    by_horizon = {}
    for horizon in ['1h', '1d', '1w']:
        live_metrics = accuracy_metrics.get(horizon, {}) if isinstance(accuracy_metrics, dict) else {}
        backtest_metrics = horizon_backtests.get(horizon, {}) if isinstance(horizon_backtests, dict) else {}
        quality_metrics = horizon_quality.get(horizon, {}) if isinstance(horizon_quality, dict) else {}
        wf = wf_stats.get(horizon, {})

        live_total = int(live_metrics.get('total_predictions', 0) or 0)
        live_direction_accuracy = float(live_metrics.get('direction_accuracy', 0.0) or 0.0)

        # Prefer walk-forward stats (rigorous, 199 OOS samples) over in-training diagnostics
        wf_direction_accuracy = wf.get('direction_accuracy')
        wf_samples = int(wf.get('samples', 0) or 0)
        backtest_direction_accuracy = wf_direction_accuracy if wf_direction_accuracy is not None \
            else backtest_metrics.get('direction_accuracy')
        backtest_samples = wf_samples if wf_samples > 0 \
            else (int(backtest_metrics.get('samples', 0) or 0) if isinstance(backtest_metrics, dict) else 0)

        confidence_value = float(horizon_confidence.get(horizon, 0.0) or 0.0) if isinstance(horizon_confidence, dict) else 0.0

        display_accuracy = None
        display_accuracy_source = 'unavailable'
        if live_total >= min_live_accuracy_samples:
            display_accuracy = live_direction_accuracy
            display_accuracy_source = 'live'
        elif live_total > 0:
            use_sparse_live = isinstance(quality_metrics, dict) and bool(quality_metrics.get('qualified'))
            if use_sparse_live:
                display_accuracy = live_direction_accuracy
                display_accuracy_source = 'live_sparse'
            elif backtest_direction_accuracy is not None:
                display_accuracy = float(backtest_direction_accuracy)
                display_accuracy_source = 'backtest'
            else:
                display_accuracy = live_direction_accuracy
                display_accuracy_source = 'live_sparse'
        elif backtest_direction_accuracy is not None:
            display_accuracy = float(backtest_direction_accuracy)
            display_accuracy_source = 'backtest'

        by_horizon[horizon] = {
            'live_direction_accuracy': live_direction_accuracy,
            'live_total_predictions': live_total,
            'backtest_direction_accuracy': float(backtest_direction_accuracy) if backtest_direction_accuracy is not None else None,
            'backtest_samples': backtest_samples,
            'display_accuracy': float(display_accuracy) if display_accuracy is not None else None,
            'display_accuracy_source': display_accuracy_source,
            'confidence': confidence_value,
            'quality': quality_metrics if isinstance(quality_metrics, dict) else {},
            # Walk-forward significance stats — only present for horizons with a completed backtest
            'wf_p_value':           float(wf['direction_p_value']) if wf.get('direction_p_value') is not None else None,
            'wf_ci_95':             wf.get('direction_ci_95'),
            'wf_is_significant':    bool(wf['is_significant_5pct']) if 'is_significant_5pct' in wf else None,
            'wf_samples':           wf_samples if wf_samples > 0 else None,
            'wf_mae_improvement_pct': float(wf['mae_improvement_pct']) if wf.get('mae_improvement_pct') is not None else None,
            # Dollar P&L stats (1 contract, $100 round-trip cost)
            'wf_pnl_sharpe':        wf.get('pnl_sharpe'),
            'wf_pnl_mean_per_trade': wf.get('pnl_mean_per_trade'),
            'wf_pnl_win_rate':      wf.get('pnl_win_rate'),
            'wf_pnl_max_drawdown':  wf.get('pnl_max_drawdown'),
            'wf_pnl_total':         wf.get('pnl_total'),
            'wf_pnl_profit_factor': wf.get('pnl_profit_factor'),
            'wf_pnl_n_trades':      wf.get('pnl_n_trades', 0),
            'wf_pnl_trades':        wf.get('pnl_trades', []),
            'wf_yearly_breakdown':  wf.get('yearly_breakdown', {}),
        }

    preferred_order = _ordered_display_horizons(PRIMARY_DISPLAY_HORIZON)
    def headline_rank(horizon):
        metrics = by_horizon.get(horizon, {})
        quality = metrics.get('quality', {}) if isinstance(metrics.get('quality', {}), dict) else {}
        status = str(quality.get('status', 'unknown')).lower()
        display_accuracy = metrics.get('display_accuracy')
        confidence = float(metrics.get('confidence', 0.0) or 0.0)
        live_samples = int(metrics.get('live_total_predictions', 0) or 0)
        backtest_samples = int(metrics.get('backtest_samples', 0) or 0)
        evidence_count = max(live_samples, backtest_samples)
        accuracy_source = str(metrics.get('display_accuracy_source', 'unavailable'))

        return (
            1 if bool(quality.get('qualified')) else 0,
            1 if status == 'watch' else 0,
            1 if display_accuracy is not None else 0,
            float(display_accuracy) if display_accuracy is not None else float('-inf'),
            confidence,
            1 if accuracy_source in {'live', 'live_sparse'} else 0,
            evidence_count,
            -preferred_order.index(horizon),
        )

    headline_horizon = max(preferred_order, key=headline_rank)

    return by_horizon, headline_horizon

def test_ml_system_readiness():
    """Test if ML system is ready by calling oil.py functions"""
    try:
        # Test contract detection first
        contract_info = get_current_wti_contract()
        if not contract_info or not contract_info.get('current_price'):
            return False

        current_time = time.time()
        cached_predictions = system_state.get('cached_predictions')
        if cached_predictions and current_time - system_state.get('last_prediction_time', 0) < 300:
            predictions = cached_predictions
        else:
            with _prediction_refresh_lock:
                cached_predictions = system_state.get('cached_predictions')
                if cached_predictions and current_time - system_state.get('last_prediction_time', 0) < 300:
                    predictions = cached_predictions
                else:
                    predictions = get_multi_horizon_wti_predictions()
        if not predictions or not predictions.get('is_real_prediction'):
            return False
            
        # Cache the predictions for serving
        system_state['cached_predictions'] = predictions
        system_state['ml_ready'] = True
        system_state['last_prediction_time'] = time.time()
        
        return True
        
    except Exception as e:
        logger.debug(f"ML system not ready: {e}")
        return False

def get_cached_ml_data():
    """Get cached ML predictions and accuracy data"""
    try:
        # Use cached predictions if available and recent (less than 5 minutes old)
        current_time = time.time()
        if (system_state['cached_predictions'] and 
            current_time - system_state['last_prediction_time'] < 300):
            predictions = system_state['cached_predictions']
        else:
            with _prediction_refresh_lock:
                cached_predictions = system_state.get('cached_predictions')
                cache_age = current_time - system_state.get('last_prediction_time', 0)
                if cached_predictions and cache_age < 300:
                    predictions = cached_predictions
                else:
                    # Get fresh predictions
                    predictions = get_multi_horizon_wti_predictions()
                    if predictions and predictions.get('is_real_prediction'):
                        system_state['cached_predictions'] = predictions
                        system_state['last_prediction_time'] = current_time
                    else:
                        predictions = None
        
        # Get accuracy metrics
        accuracy_metrics = None
        try:
            accuracy_metrics = get_prediction_accuracy_metrics()
            system_state['cached_accuracy'] = accuracy_metrics
        except Exception as acc_error:
            logger.debug(f"Accuracy metrics not available: {acc_error}")
            accuracy_metrics = system_state.get('cached_accuracy')
        
        return predictions, accuracy_metrics
        
    except Exception as e:
        logger.warning(f"Failed to get ML data: {e}")
        return None, None

def initialize_oil_system():
    """Initialize the oil prediction system - REAL DATA ONLY"""
    if not OIL_IMPORTS_AVAILABLE:
        raise Exception("CRITICAL: oil.py imports not available - cannot start server")
    
    try:
        logger.info("🔧 Initializing oil prediction system...")
        
        # Test contract detection
        contract_info = get_current_wti_contract()
        logger.info(f"✅ Active contract: {contract_info['symbol']} @ ${contract_info['current_price']:.2f}")
        
        # Update lightweight startup state first so service is available quickly.
        system_state['initialized'] = True
        system_state['ml_ready'] = False
        system_state['cached_predictions'] = None
        system_state['cached_accuracy'] = None
        system_state['last_prediction_time'] = 0

        if EAGER_ML_WARMUP:
            logger.info("🔄 EAGER_ML_WARMUP enabled - generating initial predictions...")
            predictions = get_multi_horizon_wti_predictions()
            if not predictions.get('is_real_prediction'):
                raise Exception("CRITICAL: System not generating real predictions")
            logger.info("✅ Initial predictions generated:")
            logger.info(f"   1H: ${predictions['prediction_1h']:.2f}")
            logger.info(f"   1D: ${predictions['prediction_1d']:.2f}")
            logger.info(f"   1W: ${predictions['prediction_1w']:.2f}")
            system_state['ml_ready'] = True
            system_state['cached_predictions'] = predictions
            system_state['last_prediction_time'] = time.time()
        else:
            logger.info("⏳ Deferred ML warmup - service online, models load on first cycle/request")
        
        logger.info("🚀 Oil prediction system ready - REAL DATA ONLY")
        return True
        
    except Exception as e:
        logger.error(f"❌ Oil system initialization failed: {e}")
        system_state['initialized'] = False
        system_state['ml_ready'] = False
        raise Exception(f"Cannot initialize oil system: {e}")

def update_predictions():
    """Update predictions every 3 minutes - FIX #7: Corrected timing logic"""
    initialization_wait = EAGER_ML_WARMUP
    
    while True:
        try:
            time.sleep(30)  # Check every 30 seconds
            
            current_time = time.time()
            last_prediction_time = system_state.get('last_prediction_time', 0)
            
            # FIX #7: Force the first loop to refresh, regardless of startup timestamps.
            if initialization_wait:
                logger.info("🔄 First prediction update (initialization)")
                initialization_wait = False
                time_since_last = 180  # Force update
            else:
                time_since_last = current_time - last_prediction_time if last_prediction_time > 0 else 180
            
            if time_since_last >= 180:  # 3 minutes
                
                if not system_state['ml_ready']:
                    # Test if ML system became ready
                    if test_ml_system_readiness():
                        logger.info("✅ ML system is now ready")
                    else:
                        logger.debug("⚠️ ML system still not ready")
                        continue
                
                logger.info(f"🔄 Updating predictions ({time_since_last:.0f}s since last)...")
                
                # Get fresh predictions
                predictions, accuracy = get_cached_ml_data()
                
                if predictions and predictions.get('is_real_prediction'):
                    system_state['last_prediction_time'] = current_time  # Update AFTER success
                    system_state['error_count'] = 0
                    logger.info(f"✅ Predictions updated - 1H: ${predictions['prediction_1h']:.2f}")
                else:
                    raise Exception("Failed to get real predictions")
                
        except Exception as e:
            system_state['error_count'] = system_state.get('error_count', 0) + 1
            logger.error(f"❌ Prediction update failed (error #{system_state['error_count']}): {e}")
            
            if system_state['error_count'] >= 5:
                logger.critical("🚨 Too many prediction errors - ML system may be failing")
                system_state['ml_ready'] = False
            
            time.sleep(60)  # Wait longer on error

def update_price_data():
    """Update current price data every 30 seconds"""
    while True:
        try:
            time.sleep(30)
            
            if not system_state['initialized']:
                continue
            
            # Get current contract and price
            contract_info = get_current_wti_contract()
            current_price = contract_info['current_price']
            
            # Store the price update with volume snapshot for real chart volume series.
            store_actual_price_update(current_price, contract_info.get('volume'))
            
            system_state['last_price_update_time'] = time.time()
            
            logger.debug(f"📊 Price updated: ${current_price:.2f}")
            
        except Exception as e:
            logger.error(f"❌ Price update failed: {e}")
            time.sleep(60)

@app.route('/')
def root():
    """Root endpoint - server status"""
    ensure_startup_started()

    if not OIL_IMPORTS_AVAILABLE:
        return json_response({
            'service': 'WTI Oil Price Prediction API',
            'status': 'CRITICAL_ERROR',
            'error': 'oil.py imports not available',
            'message': 'Server cannot function without oil.py',
            'ready': False,
            'server_time': datetime.now().isoformat()
        }, 503)

    if not _startup_ready.is_set():
        return json_response(
            startup_payload(
                'Background startup in progress. API data will be available shortly.',
                startup_retry_seconds()
            ),
            200
        )
    
    try:
        # Test contract detection
        contract_info = get_current_wti_contract()
        if not contract_info or not contract_info.get('current_price'):
            raise Exception("Contract detection not ready")

        return json_response({
            'service': 'WTI Oil Price Prediction API',
            'status': 'ACTIVE' if system_state['ml_ready'] else 'INITIALIZING',
            'version': '4.0.0-real-data-only',
            'ml_ready': system_state['ml_ready'],
            'startup_ready': True,
            'ready': bool(system_state['ml_ready']),
            'contract': contract_info['symbol'],
            'current_price': contract_info['current_price'],
            'data_source': 'oil.py REAL DATA ONLY',
            'last_prediction_time': system_state['last_prediction_time'],
            'error_count': system_state['error_count'],
            'endpoints': {
                '/': 'Server status',
                '/data': 'Real WTI data and ML predictions',
                '/health': 'Health check'
            },
            'server_time': datetime.now().isoformat()
        })
        
    except Exception as e:
        return json_response({
            'service': 'WTI Oil Price Prediction API',
            'status': 'INITIALIZING',
            'message': 'System initializing - oil.py engine starting...',
            'error': str(e),
            'ready': False,
            'server_time': datetime.now().isoformat()
        }, 200)

@app.route('/data')
def get_data():
    """Main data endpoint - REAL DATA ONLY from oil.py"""
    ensure_startup_started()
    
    # FIX #6: Check if startup is complete before serving (prevents race condition)
    if not _startup_ready.is_set():
        return json_response(
            startup_payload('Server starting up. Please wait a few seconds...', startup_retry_seconds()),
            503,
            retry_after=startup_retry_seconds()
        )
    
    if not OIL_IMPORTS_AVAILABLE:
        return json_response({
            'error': 'CRITICAL_ERROR',
            'message': 'oil.py imports not available - cannot serve data',
            'server_time': datetime.now().isoformat()
        }, 503)
    
    try:
        # Test contract detection
        contract_info = get_current_wti_contract()
        if not contract_info or not contract_info.get('current_price'):
            return json_response(
                startup_payload('System still initializing - waiting for contract data to become available.', startup_retry_seconds()),
                503,
                retry_after=startup_retry_seconds()
            )
        
        # Test ML readiness and get predictions
        if not system_state['ml_ready']:
            system_state['ml_ready'] = test_ml_system_readiness()
        
        predictions, accuracy_metrics = get_cached_ml_data() if system_state['ml_ready'] else (None, None)
        
        # Calculate all values from REAL data
        current_price = contract_info['current_price']
        
        historical_data = get_historical_data(limit=3200)
        actual_history = historical_data.get('actual', {}) if historical_data else {}
        actual_values = actual_history.get('values', []) if isinstance(actual_history, dict) else []
        actual_timestamps = actual_history.get('timestamps', []) if isinstance(actual_history, dict) else []

        # Prefer the real day-over-day change from the daily close series (set by
        # get_current_wti_contract). It is correct in the one-shot freeze/CI deploy, where the
        # in-memory actual-price store below has no history and used to report a fake 0.00%.
        price_change = contract_info.get('price_change')
        price_change_percent = contract_info.get('price_change_percent')
        price_change_quality = 'daily_close' if price_change is not None else 'unavailable'

        try:
            if price_change is None and len(actual_values) >= 2 and len(actual_timestamps) >= 2:
                # Find a price point from roughly 24 hours ago (86400 seconds)
                current_timestamp = datetime.now().timestamp()
                target_timestamp = current_timestamp - 86400  # 24 hours ago
                
                # Find the closest historical point to 24 hours ago
                closest_price = None
                min_time_diff = float('inf')
                
                for i, timestamp_str in enumerate(actual_timestamps):
                    try:
                        point_timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).timestamp()
                        time_diff = abs(point_timestamp - target_timestamp)
                        if time_diff < min_time_diff and i < len(actual_values):
                            min_time_diff = time_diff
                            closest_price = actual_values[i]
                    except Exception:
                        continue
                
                # FIX #5: Calculate change with quality indicator
                if closest_price is not None and closest_price > 0:
                    if min_time_diff <= 3600:  # Within 1 hour
                        price_change_quality = 'precise'
                    elif min_time_diff <= 3600 * 6:  # Within 6 hours
                        price_change_quality = 'good'
                    elif min_time_diff <= 86400 * 2:  # Within 48 hours
                        price_change_quality = 'approximate'
                    else:
                        price_change_quality = 'stale'
                    
                    price_change = current_price - closest_price
                    price_change_percent = (price_change / closest_price * 100)
                    logger.debug(f"📊 Daily change calculated: {price_change:.3f} ({price_change_percent:.2f}%)")
                elif len(actual_values) > 0:
                    # Fallback: use oldest available price if 24h data not available
                    oldest_price = actual_values[0]
                    price_change = current_price - oldest_price
                    price_change_percent = (price_change / oldest_price * 100) if oldest_price > 0 else 0.0
                    price_change_quality = 'oldest_available'
                    logger.debug(f"📊 Change vs oldest data: {price_change:.3f} ({price_change_percent:.2f}%)")
        except Exception as e:
            logger.warning(f"Could not calculate daily price change: {e}")
            price_change_quality = 'error'
        
        # Use sensible defaults if still None (FIX #5)
        if price_change is None:
            price_change = 0.0
        if price_change_percent is None:
            price_change_percent = 0.0
        
        if not predictions or not bool(predictions.get('is_real_prediction', False)):
            return json_response(
                startup_payload('ML predictions are still warming up. Real forecast payload not ready yet.', startup_retry_seconds()),
                503,
                retry_after=startup_retry_seconds()
            )

        geo_raw = predictions.get('geopolitical_risk', {}) if predictions else {}

        # Infer active drivers from geo breakdown + dominant driver for playbook analogue ranking.
        _geo_breakdown = geo_raw.get('risk_breakdown', {})
        _active_drivers = [k.lower() for k, v in _geo_breakdown.items() if v and int(v) > 0]
        _dominant = str(geo_raw.get('dominant_driver', '')).lower()
        if _dominant and _dominant not in _active_drivers:
            _active_drivers.insert(0, _dominant)
        supply_shock_playbook = _load_supply_shock_playbook(current_drivers=_active_drivers)

        geopolitical_risk = {
            'score': float(geo_raw.get('geo_risk_score', 0) or 0),
            'regime': str(geo_raw.get('regime', 'UNKNOWN')),
            'dominant_driver': str(geo_raw.get('dominant_driver', 'unknown')),
            'risk_breakdown': geo_raw.get('risk_breakdown', {}),
            'top_headlines': geo_raw.get('top_headlines', []),
            'total_articles_scanned': int(geo_raw.get('total_articles_scanned', 0) or 0),
            'recent_24h_articles': int(geo_raw.get('recent_24h_articles', 0) or 0),
            'novelty_spike': bool(geo_raw.get('novelty_spike', False)),
        }
        ml_caveat = predictions.get('ml_caveat') if predictions else None

        prediction_is_real = bool(predictions.get('is_real_prediction', False)) if predictions else False
        prediction_is_full_real = bool(predictions.get('is_full_real_prediction', prediction_is_real)) if predictions else False
        prediction_fallbacks = predictions.get('fallbacks', {}) if predictions else {}
        prediction_data_quality = int(round(float(predictions.get('data_quality_score', 0) or 0))) if predictions else 0

        pred_1h = predictions['prediction_1h']
        pred_1d = predictions['prediction_1d'] 
        pred_1w = predictions['prediction_1w']
        horizon_confidence = predictions.get('horizon_confidence', {})
        horizon_drift_scores = predictions.get('horizon_drift_scores', {})
        prediction_intervals = predictions.get('prediction_intervals', {})
        horizon_backtests = predictions.get('horizon_backtests', {})
        horizon_quality = predictions.get('horizon_quality', {})
        qualified_horizons = predictions.get('quality_qualified_horizons', [])

        overall_metrics = accuracy_metrics.get('overall', {}) if accuracy_metrics else {}
        live_total_predictions = int(overall_metrics.get('total_predictions', 0) or 0)
        live_direction_accuracy = float(overall_metrics.get('direction_accuracy', 0.0) or 0.0)

        min_live_accuracy_samples = max(6, int(os.getenv('MIN_LIVE_ACCURACY_SAMPLES', '18')))
        metrics_by_horizon, headline_horizon = _build_horizon_metrics(
            accuracy_metrics or {},
            horizon_backtests or {},
            horizon_confidence or {},
            horizon_quality or {},
            min_live_accuracy_samples,
        )
        headline_metrics = metrics_by_horizon.get(headline_horizon, {})
        headline_accuracy = headline_metrics.get('display_accuracy')
        headline_accuracy_source = headline_metrics.get('display_accuracy_source', 'unavailable')
        headline_confidence = float(headline_metrics.get('confidence', 0.0) or 0.0)
        headline_quality = headline_metrics.get('quality', {}) if isinstance(headline_metrics.get('quality', {}), dict) else {}
        headline_prediction = {
            '1h': pred_1h,
            '1d': pred_1d,
            '1w': pred_1w,
        }.get(headline_horizon, pred_1d)
        headline_quality_status = str(headline_quality.get('status', 'unknown')).upper()
        qualified_horizon_count = len([h for h in qualified_horizons if h in {'1h', '1d', '1w'}])
        
        # Format volume for display
        volume = contract_info.get('volume', 0)
        if volume >= 1000000:
            volume_display = f"{volume/1000000:.1f}M"
        elif volume >= 1000:
            volume_display = f"{volume/1000:.1f}K"
        else:
            volume_display = f"{volume:.0f}" if volume > 0 else "N/A"
        
        # Calculate next ML prediction time
        time_since_last = int(time.time() - system_state.get('last_prediction_time', 0))
        next_prediction_in = max(0, 180 - time_since_last) if system_state['ml_ready'] else 0
        
        # Calculate total data points for enterprise metrics
        total_data_points = 0
        if actual_values:
            total_data_points = len(actual_values)
        
        # Add prediction count if available
        prediction_count = live_total_predictions
        
        return json_response({
            # Core price data - REAL ONLY
            'current_price': round(current_price, 2),
            'price_change': round(price_change, 3),
            'price_change_percent': round(price_change_percent, 2),
            'price_change_quality': price_change_quality,  # FIX #5: NEW - client knows data quality
            'volume': volume,
            'volume_display': volume_display,
            
            # Chart data - Get real historical data from stored prices
            'unified_data': historical_data,  # Deeper history for charting
            'actual': [],  # Legacy field - data now in unified_data
            'predicted': [],  # Legacy field - data now in unified_data  
            'timestamps': [],  # Legacy field - data now in unified_data
            
            # Multi-horizon predictions - REAL ML ONLY
            'multi_horizon_predictions': {
                'predictions': {
                    '1h': round(pred_1h, 2),
                    '1d': round(pred_1d, 2),
                    '1w': round(pred_1w, 2),
                    '7d': round(pred_1w, 2)
                },
                'percentage_changes': {
                    '1h': round((pred_1h - current_price) / current_price * 100, 1),
                    '1d': round((pred_1d - current_price) / current_price * 100, 1),
                    '1w': round((pred_1w - current_price) / current_price * 100, 1),
                    '7d': round((pred_1w - current_price) / current_price * 100, 1)
                },
                'prediction_intervals': prediction_intervals,
                'horizon_confidence': horizon_confidence,
                'horizon_drift_scores': horizon_drift_scores,
                'horizon_backtests': horizon_backtests,
                'horizon_quality': horizon_quality,
                'quality_qualified_horizons': qualified_horizons,
                'is_real_prediction': prediction_is_real,
                'is_full_real_prediction': prediction_is_full_real,
                'fallbacks': prediction_fallbacks,
                'processing_time': predictions.get('processing_time', 0) if predictions else 0,
                'feature_count': predictions.get('feature_count', 0) if predictions else 0,
                'last_update': predictions.get('timestamp', datetime.now().isoformat()) if predictions else datetime.now().isoformat(),
                'market_data_sources': predictions.get('market_data_sources', {}),
                'contract_metadata': predictions.get('contract_metadata', {}),
            },
            
            # ML system status
            'ml_prediction_timer': {
                'next_prediction_in': next_prediction_in,
                'minutes_remaining': next_prediction_in // 60,
                'seconds_remaining': next_prediction_in % 60,
                'currently_processing': False
            },
            
            # Performance metrics - REAL ONLY
            'performance_metrics': {
                'direction_accuracy': round(live_direction_accuracy, 1),
                'display_direction_accuracy': round(headline_accuracy, 1) if headline_accuracy is not None else None,
                'display_accuracy_source': headline_accuracy_source,
                'min_live_accuracy_samples': min_live_accuracy_samples,
                'confidence': round(headline_confidence, 1),
                'total_predictions': live_total_predictions,
                'headline': {
                    'horizon': headline_horizon,
                    'prediction': round(headline_prediction, 2),
                    'display_direction_accuracy': round(headline_accuracy, 1) if headline_accuracy is not None else None,
                    'display_accuracy_source': headline_accuracy_source,
                    'confidence': round(headline_confidence, 1),
                    'quality_status': headline_quality_status,
                    'quality_reasons': headline_quality.get('reasons', []),
                },
                'by_horizon': metrics_by_horizon,
            },
            
            # Contract information - REAL ONLY
            'contract': {
                'symbol': contract_info['symbol'],
                'description': contract_info['description'],
                'expiry_date': contract_info.get('expiry_date'),
                'days_to_expiry': contract_info.get('days_to_expiry'),
                'security_name': f"{contract_info['symbol']} WTI CRUDE",
                'quote_symbol': predictions.get('contract_metadata', {}).get('quote_symbol') if predictions else contract_info.get('yfinance_symbol'),
                'history_symbol': predictions.get('contract_metadata', {}).get('history_symbol') if predictions else contract_info.get('history_symbol'),
            },
            
            # System status
            'enterprise_metrics': {
                'data_quality': prediction_data_quality,
                'complex_ml_enabled': True,
                'real_data_only': True,
                'ml_ready': prediction_is_real,
                'error_count': system_state['error_count'],
                'data_points': total_data_points + prediction_count,  # Historical + prediction count
                'quality_status': headline_quality_status,
                'qualified_horizon_count': qualified_horizon_count,
            },
            
            'geopolitical_risk': geopolitical_risk,
            'ml_caveat': ml_caveat,
            'supply_shock_playbook': supply_shock_playbook,
            'live_record': _load_live_record_summary(),

            'feed_status': 'REAL-TIME' if prediction_is_full_real else ('DEGRADED' if prediction_is_real else 'INITIALIZING'),
            'status': 'ACTIVE' if prediction_is_full_real else ('DEGRADED' if prediction_is_real else 'INITIALIZING'),
            'data_source': 'oil.py ML ENGINE',
            'last_update': datetime.now().isoformat(),
            
            # Legacy compatibility fields
            'last_price': round(current_price, 2),
            'ml_prediction': round(headline_prediction, 2),
            'accuracy': f"{round(headline_accuracy)}%" if headline_accuracy is not None else '--',
            'confidence': f"{round(headline_confidence)}%" if headline_confidence > 0 else '--',
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"❌ Data endpoint error: {e}")
        return json_response({
            'error': 'DATA_UNAVAILABLE',
            'message': f'Cannot get real data from oil.py: {str(e)}',
            'server_time': datetime.now().isoformat()
        }, 500)

@app.route('/health')
def health():
    """Health check endpoint"""
    ensure_startup_started()
    try:
        if not OIL_IMPORTS_AVAILABLE:
            return json_response({
                'status': 'CRITICAL',
                'ready': False,
                'message': 'oil.py imports not available',
                'timestamp': datetime.now().isoformat()
            }, 503)

        # Keep platform health checks passing while async startup completes.
        if not _startup_ready.is_set():
            return json_response({
                'status': 'INITIALIZING',
                'ready': False,
                'startup_ready': False,
                'ml_ready': False,
                'message': 'Background startup in progress',
                'retry_after_seconds': startup_retry_seconds(),
                'startup_error': _startup_error,
                'startup_attempts': _startup_attempts,
                'timestamp': datetime.now().isoformat()
            }, 200)
        
        contract_info = None
        try:
            contract_info = get_current_wti_contract()
        except Exception as contract_error:
            logger.warning(f"Health contract probe failed: {contract_error}")
            return json_response({
                'status': 'DEGRADED',
                'ready': False,
                'startup_ready': True,
                'ml_ready': system_state['ml_ready'],
                'error': 'CONTRACT_DATA_UNAVAILABLE',
                'message': str(contract_error),
                'error_count': system_state['error_count'],
                'data_source': 'oil.py REAL DATA',
                'timestamp': datetime.now().isoformat()
            }, 200)
        
        return json_response({
            'status': 'HEALTHY' if system_state['ml_ready'] else 'INITIALIZING',
            'ready': bool(system_state['ml_ready']),
            'startup_ready': True,
            'ml_ready': system_state['ml_ready'],
            'contract': contract_info.get('symbol') if contract_info else None,
            'current_price': contract_info.get('current_price') if contract_info else None,
            'error_count': system_state['error_count'],
            'data_source': 'oil.py REAL DATA',
            'timestamp': datetime.now().isoformat()
        }, 200)
        
    except Exception as e:
        return json_response({
            'status': 'UNHEALTHY',
            'ready': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }, 200)

# Initialize system on startup
def startup_initialization():
    """Initialize system in background - FIX #6: Add threading event"""
    global _startup_complete_time, _startup_error, _startup_started, _startup_thread, _startup_next_retry_at
    try:
        logger.info("🚀 Starting oil.py system initialization...")
        time.sleep(2)  # Let server start
        
        initialize_oil_system()
        
        # Start background workers
        prediction_thread = threading.Thread(target=update_predictions, daemon=True)
        price_thread = threading.Thread(target=update_price_data, daemon=True)
        
        prediction_thread.start()
        price_thread.start()
        
        time.sleep(1)  # Give threads time to start
        
        # FIX #6: Signal that startup is complete (solves race condition)
        _startup_error = None
        _startup_next_retry_at = 0.0
        _startup_complete_time = time.time()
        _startup_ready.set()
        logger.info("✅ Startup sequence complete - system ready to serve requests")
        
    except Exception as e:
        logger.critical(f"❌ System initialization FAILED: {e}")
        system_state['initialized'] = False
        system_state['ml_ready'] = False
        system_state['cached_predictions'] = None
        system_state['cached_accuracy'] = None
        system_state['last_prediction_time'] = 0
        _startup_error = str(e)
        _startup_complete_time = None
        _startup_next_retry_at = time.time() + STARTUP_RETRY_COOLDOWN_SECONDS
        with _startup_lock:
            _startup_started = False
            _startup_thread = None

def ensure_startup_started():
    """Start background initialization only once and avoid side effects on import."""
    global _startup_thread, _startup_started, _startup_attempts
    if _startup_ready.is_set():
        return
    with _startup_lock:
        if _startup_ready.is_set():
            return
        if _startup_thread and _startup_thread.is_alive():
            return
        if _startup_started:
            return
        if time.time() < _startup_next_retry_at:
            return
        _startup_attempts += 1
        _startup_thread = threading.Thread(target=startup_initialization, daemon=True)
        _startup_thread.start()
        _startup_started = True

@app.before_request
def _ensure_startup_for_requests():
    """Guarantee startup thread is running when the app is imported by a WSGI server."""
    ensure_startup_started()

def run_server(host='0.0.0.0', port=9000, debug=False):
    """Run the Flask development server (python -m backend.server)."""
    ensure_startup_started()
    app.run(host=host, port=port, debug=debug)

logger.info("🚀 WTI Server starting - REAL DATA ONLY MODE")
logger.info("📊 Foundation: oil.py ML engine")

if __name__ == '__main__':
    run_server()
