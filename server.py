#!/usr/bin/env python3
"""Compatibility wrapper for the backend API server."""

from backend import server as _backend_server
from backend.server import *  # noqa: F401,F403

app = _backend_server.app
run_server = _backend_server.run_server
_build_horizon_metrics = _backend_server._build_horizon_metrics


if __name__ == "__main__":
    run_server()
