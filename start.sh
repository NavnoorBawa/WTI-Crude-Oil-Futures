#!/bin/bash
# Render start script for WTI Oil Prediction Backend
# Starts the Flask application using Gunicorn with ML timeout protection

# Get port from environment (Render provides this)
PORT=${PORT:-10000}

echo "🚀 Starting WTI Oil Backend on port $PORT"
echo "📊 ML components will load in background to avoid timeouts"

# Start Gunicorn with extended timeout for ML operations
exec gunicorn \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --worker-class sync \
    --timeout 300 \
    --graceful-timeout 30 \
    --keep-alive 2 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --preload-app \
    --log-level info \
    --access-logfile - \
    --error-logfile - \
    app:app