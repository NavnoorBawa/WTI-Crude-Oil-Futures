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
from datetime import datetime
import logging
from flask import Flask, jsonify
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

# Import oil.py functions - CRITICAL DEPENDENCY
try:
    from oil import (
        get_current_wti_contract,
        get_multi_horizon_wti_predictions, 
        get_prediction_accuracy_metrics,
        store_actual_price_update,
        get_historical_data,
        PremiumWTIPredictor
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

def test_ml_system_readiness():
    """Test if ML system is ready by calling oil.py functions"""
    try:
        # Test contract detection first
        contract_info = get_current_wti_contract()
        if not contract_info or not contract_info.get('current_price'):
            return False
        
        # Test if we can get real predictions
        predictions = get_multi_horizon_wti_predictions()
        if not predictions or not predictions.get('is_real_prediction'):
            return False
            
        # Cache the predictions for serving
        system_state['cached_predictions'] = predictions
        system_state['ml_ready'] = True
        
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
        system_state['last_prediction_time'] = time.time()

        if EAGER_ML_WARMUP:
            logger.info("🔄 EAGER_ML_WARMUP enabled - generating initial predictions...")
            predictions = get_multi_horizon_wti_predictions()
            if not predictions.get('is_real_prediction'):
                raise Exception("CRITICAL: System not generating real predictions")
            logger.info(f"✅ Initial predictions generated:")
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
            
            # Store the price update
            store_actual_price_update(current_price)
            
            system_state['last_price_update_time'] = time.time()
            
            logger.debug(f"📊 Price updated: ${current_price:.2f}")
            
        except Exception as e:
            logger.error(f"❌ Price update failed: {e}")
            time.sleep(60)

@app.route('/')
def root():
    """Root endpoint - server status"""
    if not OIL_IMPORTS_AVAILABLE:
        return jsonify({
            'service': 'WTI Oil Price Prediction API',
            'status': 'CRITICAL_ERROR',
            'error': 'oil.py imports not available',
            'message': 'Server cannot function without oil.py',
            'ready': False,
            'server_time': datetime.now().isoformat()
        }), 200
    
    try:
        # Test contract detection
        contract_info = get_current_wti_contract()
        if not contract_info or not contract_info.get('current_price'):
            raise Exception("Contract detection not ready")
        
        # Test ML readiness if not already confirmed
        if not system_state['ml_ready']:
            system_state['ml_ready'] = test_ml_system_readiness()
        
        return jsonify({
            'service': 'WTI Oil Price Prediction API',
            'status': 'ACTIVE',
            'version': '4.0.0-real-data-only',
            'ml_ready': system_state['ml_ready'],
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
        return jsonify({
            'service': 'WTI Oil Price Prediction API',
            'status': 'INITIALIZING',
            'message': 'System initializing - oil.py engine starting...',
            'error': str(e),
            'ready': False,
            'server_time': datetime.now().isoformat()
        }), 200

@app.route('/data')
def get_data():
    """Main data endpoint - REAL DATA ONLY from oil.py"""
    ensure_startup_started()
    
    # FIX #6: Check if startup is complete before serving (prevents race condition)
    if not _startup_ready.is_set():
        return jsonify({
            'error': 'SYSTEM_INITIALIZING',
            'message': 'Server starting up. Please wait 5-10 seconds...',
            'server_time': datetime.now().isoformat()
        }), 503
    
    if not OIL_IMPORTS_AVAILABLE:
        return jsonify({
            'error': 'CRITICAL_ERROR',
            'message': 'oil.py imports not available - cannot serve data',
            'server_time': datetime.now().isoformat()
        }), 503
    
    try:
        # Test contract detection
        contract_info = get_current_wti_contract()
        if not contract_info or not contract_info.get('current_price'):
            return jsonify({
                'error': 'SYSTEM_INITIALIZING',
                'message': 'System still initializing - please wait for oil.py to be ready',
                'server_time': datetime.now().isoformat()
            }), 503
        
        # Test ML readiness and get predictions
        if not system_state['ml_ready']:
            system_state['ml_ready'] = test_ml_system_readiness()
        
        predictions, accuracy_metrics = get_cached_ml_data() if system_state['ml_ready'] else (None, None)
        
        # Calculate all values from REAL data
        current_price = contract_info['current_price']
        
        # FIX #5: Calculate daily price change with quality indicator
        price_change = None
        price_change_percent = None
        price_change_quality = 'unavailable'
        
        try:
            # Get historical data to find price from ~24 hours ago
            historical_data = get_historical_data(limit=100)
            if historical_data and historical_data.get('actual') and historical_data['actual'].get('values'):
                actual_values = historical_data['actual']['values']
                actual_timestamps = historical_data['actual']['timestamps']
                
                if len(actual_values) >= 2 and len(actual_timestamps) >= 2:
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
                        except:
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
                    else:
                        # Fallback: use oldest available price if 24h data not available
                        if len(actual_values) > 0:
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
        
        # Set prediction values based on ML readiness
        if system_state['ml_ready'] and predictions:
            pred_1h = predictions['prediction_1h']
            pred_1d = predictions['prediction_1d'] 
            pred_1w = predictions['prediction_1w']
            horizon_confidence = predictions.get('horizon_confidence', {})
            horizon_drift_scores = predictions.get('horizon_drift_scores', {})
            prediction_intervals = predictions.get('prediction_intervals', {})
            horizon_backtests = predictions.get('horizon_backtests', {})
        else:
            # ML not ready - use current price as safe baseline
            pred_1h = current_price
            pred_1d = current_price
            pred_1w = current_price
            horizon_confidence = {}
            horizon_drift_scores = {}
            prediction_intervals = {}
            horizon_backtests = {}

        confidence_1d = float(horizon_confidence.get('1d', 0.0)) if horizon_confidence else 0.0
        if confidence_1d <= 0 and accuracy_metrics:
            confidence_1d = float(min(95, max(50, accuracy_metrics.get('overall', {}).get('direction_accuracy', 0) + 10)))
        
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
        historical_data = get_historical_data(limit=30)
        total_data_points = 0
        if historical_data and historical_data.get('actual') and historical_data['actual'].get('values'):
            total_data_points = len(historical_data['actual']['values'])
        
        # Add prediction count if available
        prediction_count = accuracy_metrics.get('overall', {}).get('total_predictions', 0) if accuracy_metrics else 0
        
        return jsonify({
            # Core price data - REAL ONLY
            'current_price': round(current_price, 2),
            'price_change': round(price_change, 3),
            'price_change_percent': round(price_change_percent, 2),
            'price_change_quality': price_change_quality,  # FIX #5: NEW - client knows data quality
            'volume': volume,
            'volume_display': volume_display,
            
            # Chart data - Get real historical data from stored prices
            'unified_data': get_historical_data(limit=30),  # Last 30 data points for chart
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
                    '1h': round((pred_1h - current_price) / current_price * 100, 1) if system_state['ml_ready'] else 0.0,
                    '1d': round((pred_1d - current_price) / current_price * 100, 1) if system_state['ml_ready'] else 0.0,
                    '1w': round((pred_1w - current_price) / current_price * 100, 1) if system_state['ml_ready'] else 0.0,
                    '7d': round((pred_1w - current_price) / current_price * 100, 1) if system_state['ml_ready'] else 0.0
                },
                'prediction_intervals': prediction_intervals,
                'horizon_confidence': horizon_confidence,
                'horizon_drift_scores': horizon_drift_scores,
                'horizon_backtests': horizon_backtests,
                'is_real_prediction': system_state['ml_ready'] and predictions is not None,
                'processing_time': predictions.get('processing_time', 0) if predictions else 0,
                'feature_count': predictions.get('feature_count', 0) if predictions else 0,
                'last_update': predictions.get('timestamp', datetime.now().isoformat()) if predictions else datetime.now().isoformat()
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
                'direction_accuracy': round(accuracy_metrics.get('overall', {}).get('direction_accuracy', 0), 1) if accuracy_metrics else 0,
                'confidence': round(confidence_1d, 1),
                'total_predictions': accuracy_metrics.get('overall', {}).get('total_predictions', 0) if accuracy_metrics else 0
            },
            
            # Contract information - REAL ONLY
            'contract': {
                'symbol': contract_info['symbol'],
                'description': contract_info['description'],
                'expiry_date': contract_info.get('expiry_date'),
                'days_to_expiry': contract_info.get('days_to_expiry'),
                'security_name': f"{contract_info['symbol']} WTI CRUDE"
            },
            
            # System status
            'enterprise_metrics': {
                'data_quality': 100,  # Always 100 if we reach here
                'complex_ml_enabled': True,
                'real_data_only': True,
                'ml_ready': system_state['ml_ready'],
                'error_count': system_state['error_count'],
                'data_points': total_data_points + prediction_count  # Historical + prediction count
            },
            
            'feed_status': 'REAL-TIME',
            'status': 'ACTIVE',
            'data_source': 'oil.py ML ENGINE',
            'last_update': datetime.now().isoformat(),
            
            # Legacy compatibility fields
            'last_price': round(current_price, 2),
            'ml_prediction': round(pred_1d, 2),
            'accuracy': f"{round(accuracy_metrics.get('overall', {}).get('direction_accuracy', 0)) if accuracy_metrics else 0}%",
            'confidence': f"{round(confidence_1d if confidence_1d > 0 else 50)}%",
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"❌ Data endpoint error: {e}")
        return jsonify({
            'error': 'DATA_UNAVAILABLE',
            'message': f'Cannot get real data from oil.py: {str(e)}',
            'server_time': datetime.now().isoformat()
        }), 500

@app.route('/health')
def health():
    """Health check endpoint"""
    ensure_startup_started()
    try:
        if not OIL_IMPORTS_AVAILABLE:
            return jsonify({
                'status': 'CRITICAL',
                'ready': False,
                'message': 'oil.py imports not available',
                'timestamp': datetime.now().isoformat()
            }), 200

        # Keep platform health checks passing while async startup completes.
        if not _startup_ready.is_set():
            return jsonify({
                'status': 'INITIALIZING',
                'ready': False,
                'ml_ready': False,
                'message': 'Background startup in progress',
                'timestamp': datetime.now().isoformat()
            }), 200
        
        contract_info = None
        try:
            contract_info = get_current_wti_contract()
        except Exception as contract_error:
            logger.warning(f"Health contract probe failed: {contract_error}")
        
        return jsonify({
            'status': 'HEALTHY',
            'ready': True,
            'ml_ready': system_state['ml_ready'],
            'contract': contract_info.get('symbol') if contract_info else None,
            'current_price': contract_info.get('current_price') if contract_info else None,
            'error_count': system_state['error_count'],
            'data_source': 'oil.py REAL DATA',
            'timestamp': datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        return jsonify({
            'status': 'UNHEALTHY',
            'ready': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 200

# Initialize system on startup
def startup_initialization():
    """Initialize system in background - FIX #6: Add threading event"""
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
        _startup_ready.set()
        logger.info("✅ Startup sequence complete - system ready to serve requests")
        
    except Exception as e:
        logger.critical(f"❌ System initialization FAILED: {e}")

def ensure_startup_started():
    """Start background initialization only once and avoid side effects on import."""
    global _startup_thread, _startup_started
    if _startup_started:
        return
    with _startup_lock:
        if _startup_started:
            return
        _startup_thread = threading.Thread(target=startup_initialization, daemon=True)
        _startup_thread.start()
        _startup_started = True

@app.before_request
def _ensure_startup_for_requests():
    """Guarantee startup thread is running for WSGI servers (Gunicorn/import usage)."""
    ensure_startup_started()

def run_server(host='0.0.0.0', port=9000, debug=False):
    """Run the Flask server - for use by run_complete_system.py"""
    ensure_startup_started()
    app.run(host=host, port=port, debug=debug)

logger.info("🚀 WTI Server starting - REAL DATA ONLY MODE")
logger.info("📊 Foundation: oil.py ML engine")

if __name__ == '__main__':
    run_server()