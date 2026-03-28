#!/usr/bin/env python3
"""Compatibility WSGI entry point for the backend package."""

from backend.app import app


__all__ = ["app"]
