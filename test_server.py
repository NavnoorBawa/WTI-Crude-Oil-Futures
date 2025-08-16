#!/usr/bin/env python3
"""
Test Server - Minimal version to isolate startup issues
"""

import time
import threading
from datetime import datetime, timedelta
import numpy as np
from flask import Flask, jsonify
from flask_cors import CORS
import logging

# Simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Global data - thread safe with lock
data_lock = threading.Lock()

global_data = {
    'actual': [64.0, 64.1, 64.2],
    'predicted': [64.1, 64.2, 64.3],
    'timestamps': [
        datetime.now().isoformat(),
        (datetime.now() - timedelta(minutes=1)).isoformat(),
        (datetime.now() - timedelta(minutes=2)).isoformat()
    ],
    'current_price': 64.0,
    'timeRemaining': 300,
    'contract': {
        'symbol': 'CLQ25',
        'description': 'WTI CRUDE OIL FUTURE AUG 2025'
    },
    'performance_metrics': {
        'direction_accuracy': 67.3,
        'mae': 1.15,
        'rmse': 1.78,
        'mape': 1.9,
        'correlation': 75.4,
        'total_predictions': 42
    },
    'unified_data': {
        'actual': {
            'values': [64.0, 64.1, 64.2],
            'timestamps': [
                datetime.now().isoformat(),
                (datetime.now() - timedelta(minutes=1)).isoformat(),
                (datetime.now() - timedelta(minutes=2)).isoformat()
            ]
        },
        'predicted': {
            'historical': {
                'values': [64.1, 64.2, 64.3],
                'timestamps': [
                    datetime.now().isoformat(),
                    (datetime.now() - timedelta(minutes=1)).isoformat(),
                    (datetime.now() - timedelta(minutes=2)).isoformat()
                ],
                'upper_bound': [64.5, 64.6, 64.7],
                'lower_bound': [63.7, 63.8, 63.9]
            }
        }
    },
    'multi_horizon_predictions': {
        'predictions': {
            '1h': 64.2,
            '4h': 64.4,
            '1d': 64.6,
            '7d': 65.0
        },
        'confidence_bands': {
            '1h': {'upper': 65.0, 'lower': 63.5},
            '4h': {'upper': 65.5, 'lower': 63.0},
            '1d': {'upper': 66.0, 'lower': 62.5},
            '7d': {'upper': 67.0, 'lower': 62.0}
        },
        'processing_time': 0.1,
        'generated_at': datetime.now().isoformat()
    },
    'enterprise_metrics': {
        'data_points': 3,
        'prediction_points': 3,
        'data_quality': 100,
        'complex_ml_enabled': True
    },
    'ml_status': {
        'status': 'active',
        'current_step': 'Ready',
        'progress_percentage': 100
    }
}

@app.route('/data')
def get_data():
    """Main data endpoint"""
    try:
        with data_lock:
            return jsonify({
                'actual': global_data['actual'][:],
                'predicted': global_data['predicted'][:],
                'timestamps': global_data['timestamps'][:],
                'unified_data': {
                    'actual': global_data['unified_data']['actual'].copy(),
                    'predicted': global_data['unified_data']['predicted'].copy()
                },
                'current_price': global_data['current_price'],
                'timeRemaining': global_data['timeRemaining'],
                'contract': global_data['contract'].copy(),
                'performance_metrics': global_data['performance_metrics'].copy(),
                'enterprise_metrics': global_data['enterprise_metrics'].copy(),
                'ml_status': global_data['ml_status'].copy(),
                'multi_horizon_predictions': global_data['multi_horizon_predictions'].copy()
            })
    except Exception as e:
        logger.error(f"Data endpoint error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'data_available': len(global_data['actual']) > 0,
        'timestamp': datetime.now().isoformat(),
        'version': 'TEST_1.0'
    })

def countdown_timer():
    """Simple countdown timer"""
    while True:
        try:
            time.sleep(1)
            with data_lock:
                if global_data['timeRemaining'] > 0:
                    global_data['timeRemaining'] -= 1
                else:
                    global_data['timeRemaining'] = 300
        except Exception as e:
            logger.error(f"Timer error: {e}")

if __name__ == '__main__':
    print("🚀 Starting Test Server")
    print("=" * 60)
    
    try:
        # Start timer thread
        timer_thread = threading.Thread(target=countdown_timer, daemon=True)
        timer_thread.start()
        print("✅ Timer thread started")
        
        print("✅ All systems ready!")
        print("🌐 Server: http://127.0.0.1:9000")
        print("📊 Endpoints: /data, /health")
        print("=" * 60)
        
        import os
        port = int(os.environ.get("PORT", 9000))
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
        
    except Exception as e:
        print(f"❌ Startup error: {e}")
        logger.error(f"Startup error: {e}")