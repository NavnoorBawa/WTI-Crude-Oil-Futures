#!/usr/bin/env python3
"""
Production WSGI entry point for WTI Oil Prediction System
Optimized for Render deployment with Gunicorn
"""

# Import the Flask app from the backend package - this is what Gunicorn will use
from .server import app

# Gunicorn will automatically use the 'app' object
