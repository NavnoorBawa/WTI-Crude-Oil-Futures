#!/bin/bash
# Render start script for WTI Oil Prediction Backend
# Starts the Flask application using Gunicorn with ML timeout protection

# Get port from environment (Render provides this)
PORT=${PORT:-10000}

echo "🚀 Starting WTI Oil Backend on port $PORT"
echo "📊 ML components will load in background to avoid timeouts"

# Start Gunicorn from a single shared config so Procfile/Render/local stay aligned.
exec gunicorn backend.app:app --config gunicorn.conf.py --bind 0.0.0.0:$PORT
