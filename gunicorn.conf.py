import os

# Gunicorn configuration for WTI Oil Prediction Server
bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"
workers = 1
worker_class = "sync"
timeout = 300
graceful_timeout = 30
keepalive = 2
max_requests = 1000
max_requests_jitter = 100
preload_app = False
loglevel = "info"
accesslog = "-"
errorlog = "-"
