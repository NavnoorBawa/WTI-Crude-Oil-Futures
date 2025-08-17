#!/usr/bin/env python3
"""
Minimal test server for Render deployment debugging
"""

import os
from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app, origins=["*"])

@app.route('/')
def root():
    return jsonify({
        'service': 'WTI Test Server',
        'status': 'active',
        'message': 'Minimal test server running successfully',
        'timestamp': datetime.now().isoformat(),
        'environment': {
            'PORT': os.environ.get('PORT', 'not set'),
            'HOST': os.environ.get('HOST', 'not set'),
            'python_version': os.sys.version
        }
    })

@app.route('/data')
def test_data():
    return jsonify({
        'current_price': 62.50,
        'message': 'Test data - minimal server working',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 9000))
    host = os.environ.get("HOST", "0.0.0.0")
    app.run(host=host, port=port, debug=False)