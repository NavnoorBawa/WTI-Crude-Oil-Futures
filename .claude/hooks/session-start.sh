#!/bin/bash
# SessionStart hook for Claude Code cloud sessions: installs what the repo's checks need, so
# `npm run check`, the Python gates and the tests work as soon as a session starts. Idempotent.
set -euo pipefail

# Only in a Claude Code cloud session; a local machine manages its own environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

# Python: CI runs 3.12 and pyproject requires >=3.12 (numpy 2.5 has no wheels for older versions).
PYTHON=""
for candidate in python3.12 python3.13 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "session-start: no Python >= 3.12 found; skipping the Python environment" >&2
else
  if [ ! -x .venv/bin/python ]; then
    "$PYTHON" -m venv .venv
  fi
  .venv/bin/python -m pip install --quiet --disable-pip-version-check --upgrade pip
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt -r requirements-dev.txt
  # Every later command in the session uses the venv's python, ruff, mypy, cog and bandit.
  if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    {
      echo "export VIRTUAL_ENV=\"$PWD/.venv\""
      echo "export PATH=\"$PWD/.venv/bin:\$PATH\""
      echo "export PYTHONPATH=\".\""
    } >> "$CLAUDE_ENV_FILE"
  fi
fi

# Node: the lockfile pins every package; lifecycle scripts stay off, as in CI.
npm install --ignore-scripts --no-audit --no-fund
