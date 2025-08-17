#!/usr/bin/env python3
"""
Production WSGI entry point for WTI Oil Prediction System
Optimized for Render deployment
"""

import os
import sys

# Use minimal server first to ensure deployment works
try:
    from server_minimal import app, run_server
    print("Using minimal server for reliable deployment")
except ImportError:
    # Fallback to full server
    from server import app, run_server
    print("Using full production server")

# Configure for production deployment
if __name__ == "__main__":
    # For direct execution
    port = int(os.environ.get("PORT", 9000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    # Production mode - no debug
    run_server(host=host, port=port, debug=False)
else:
    # For gunicorn WSGI server
    # gunicorn will import this module and use the 'app' object
    pass