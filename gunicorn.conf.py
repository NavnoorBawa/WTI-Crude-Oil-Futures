# Gunicorn configuration for WTI Oil Prediction Server
bind = "0.0.0.0:10000"
workers = 1
worker_class = "sync"
timeout = 120  # 2 minutes timeout for ML initialization
keepalive = 30
max_requests = 1000
max_requests_jitter = 100
preload_app = False  # Don't preload to allow background initialization