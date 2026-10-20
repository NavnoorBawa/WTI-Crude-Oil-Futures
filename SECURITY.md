# Security policy

## Supported versions

The `main` branch and the latest release.

## Reporting a vulnerability

Please report privately through GitHub: **Security → Report a vulnerability** on this repository.
Do not open a public issue. Expect an acknowledgement within 7 days and a fix or advisory within 30
days for confirmed issues.

## Scope

In scope: the GitHub Actions workflows (`.github/workflows/`), the deploy and state-persistence
path (`gh-pages` and `live-data` branches), the payload validator, and the dashboard (`src/`).

Design choices that bound the impact of a compromise, enforced by `tests/test_workflow_guards.py`:

- Jobs that hold a write token install and build nothing; they run only GitHub's own actions
  (pinned to full commit SHAs), git, and this repository's standard-library scripts.
- Third-party code (pip and npm packages) runs only in jobs with a read-only token.
- The only secret the workflows use is `EIA_API_KEY`, in the freeze step of the build job; the
  write-token jobs use only the token GitHub issues to each run.
- Deploys refuse payloads that fail validation, builds more than four hours old or older than the
  live site, and builds superseded by a newer run.
- The live record on `live-data` is validated before any job uses it, persisted only on top of the
  state the build restored, and sealed immediately before each push; an entry not sealed in time is
  never scored (`backend/vol_record.py`).
- The dashboard is static, ships a build-time Content-Security-Policy (`script-src 'self'`,
  `font-src 'self'`), and loads no third-party scripts or fonts.

Dependencies are pinned and audited in CI (`pip-audit`, `npm audit`), and Dependabot proposes
updates that must pass the full test suite.
