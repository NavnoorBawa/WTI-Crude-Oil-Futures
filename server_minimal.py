#!/usr/bin/env python3
"""
Minimal WTI Server - Guaranteed to work
"""

import os
import time
import threading
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["*"])

# Simple state tracking
server_state = {
    'startup_time': datetime.now(),
    'ml_available': False,
    'error_log': []
}

# Try to import ML components safely
try:
    from oil import get_current_wti_contract, get_multi_horizon_wti_predictions
    server_state['ml_available'] = True
    server_state['error_log'].append('ML components loaded successfully')
except Exception as e:
    server_state['error_log'].append(f'ML import failed: {str(e)}')

@app.route('/')
def root():
    """Simple root endpoint"""
    return jsonify({
        'service': 'WTI Oil Price Prediction API',
        'status': 'active',
        'version': '2.0.1-minimal',
        'ml_available': server_state['ml_available'],
        'startup_time': server_state['startup_time'].isoformat(),
        'error_log': server_state['error_log'][-5:],  # Last 5 errors
        'endpoints': {
            '/': 'API status',
            '/data': 'WTI data with ML predictions',
            '/health': 'Health check'
        },
        'server_time': datetime.now().isoformat()
    })

@app.route('/data')
def get_data():
    """Data endpoint with ML predictions"""
    try:
        if not server_state['ml_available']:
            return jsonify({
                'error': 'ML_UNAVAILABLE',
                'message': 'ML components not loaded',
                'error_log': server_state['error_log']
            }), 503
        
        # Get contract info
        contract_info = get_current_wti_contract()
        
        # Get predictions
        predictions = get_multi_horizon_wti_predictions()
        
        if not predictions:
            return jsonify({
                'error': 'NO_PREDICTIONS',
                'message': 'Could not generate predictions'
            }), 503
        
        # Simple response
        return jsonify({
            'current_price': 62.50,  # Placeholder
            'contract': contract_info.get('symbol', 'UNKNOWN'),
            'predictions': predictions,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        server_state['error_log'].append(f'Data endpoint error: {str(e)}')
        return jsonify({
            'error': 'DATA_ERROR',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/health')
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'ml_available': server_state['ml_available'],
        'uptime_seconds': (datetime.now() - server_state['startup_time']).total_seconds(),
        'timestamp': datetime.now().isoformat()
    })

def run_server(host='0.0.0.0', port=9000, debug=False):
    """Run the minimal server"""
    print(f"🚀 Starting minimal WTI server on {host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)

if __name__ == '__main__':
    run_server()