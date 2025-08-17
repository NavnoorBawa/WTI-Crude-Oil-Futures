#!/usr/bin/env python3
"""
Production WSGI entry point for WTI Oil Prediction System
Optimized for Render deployment
"""

import os
import sys

# Use test server temporarily to debug Render deployment
try:
    from test_server import app
    print("Using test server for deployment debugging")
except ImportError:
    # Fallback to main server
    from server import app, run_server
    print("Using main server")

# Configure for production deployment
if __name__ == "__main__":
    # For direct execution
    port = int(os.environ.get("PORT", 9000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    # Production mode - no debug
    if 'test_server' in sys.modules:
        app.run(host=host, port=port, debug=False)
    else:
        run_server(host=host, port=port, debug=False)
else:
    # For gunicorn WSGI server
    # gunicorn will import this module and use the 'app' object
    pass