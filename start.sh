#!/bin/bash
# Render start script for WTI Oil Prediction Backend
# Starts the Flask application using Gunicorn

# Get port from environment (Render provides this)
PORT=${PORT:-10000}

echo "🚀 Starting WTI Oil Backend on port $PORT"
echo "📊 Loading ML components..."

# Start Gunicorn with production settings
exec gunicorn \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --worker-class sync \
    --timeout 120 \
    --keep-alive 2 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --log-level info \
    --access-logfile - \
    --error-logfile - \
    app:app