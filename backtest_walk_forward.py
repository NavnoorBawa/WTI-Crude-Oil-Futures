#!/usr/bin/env python3
"""Compatibility wrapper for the walk-forward backtest script."""

from backend import backtest_walk_forward as _backend_backtest_walk_forward
from backend.backtest_walk_forward import *  # noqa: F401,F403

main = _backend_backtest_walk_forward.main


if __name__ == "__main__":
    main()
