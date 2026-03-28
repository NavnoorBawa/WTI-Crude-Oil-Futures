#!/usr/bin/env python3
"""Compatibility wrapper for the backend prediction engine."""

from backend import oil as _backend_oil
from backend.oil import *  # noqa: F401,F403

main = _backend_oil.main


if __name__ == "__main__":
    main()
