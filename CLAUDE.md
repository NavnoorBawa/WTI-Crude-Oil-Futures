# WTI Volatility Research: notes for Claude Code sessions

Read `docs/HANDOFF.md` first: it holds the current state, the open backlog and the working loop.
Then `docs/ROADMAP.md`: what "good" means for this project, measured, and the staged plan to get
there. A session picks the next unfinished roadmap item and works it through the handoff loop.

## What this is

A research project and static dashboard that answers one question: how well can next-week WTI
crude realized volatility be forecast from free data? The model of record is HAR-IV (`har-iv/2`):
realized-vol terms plus OVX lagged one session, judged on QLIKE against a 14-model benchmark with
Diebold-Mariano, Model Confidence Set and Hansen SPA tests. Live forecasts are sealed in git before
their window opens and scored afterwards. Site: https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/

- `backend/`: Python research and pipeline (vol_lab, vol_forecast, vol_record, studies, ledger).
- `src/`: React 19 + Vite dashboard (panels, charts, lib); `tests/frontend/` (node --test).
- `data/`: committed research artifacts; `data/vol_lab.json` must reproduce bit for bit.
- `docs/`: METHODOLOGY, ARCHITECTURE, DATA_CONTRACT, MODEL_CARD, DATASHEET.

## Non-negotiable rules

- Honesty: every number shown is computed from `data.json`/`research.json`, never typed into copy.
  Titles make one claim in at most 90 characters and name their sample. "Significant" only for
  p < 0.05, "inconclusive" for 0.05 <= p < 0.10. Direction accuracy is not evidence of skill. No hype
  words in any copy. Never state a result more strongly than its test supports.
- Free data and free services only. No tracking. Respect the CSP in `vite.config.js`.
- Never hand-edit `data/vol_lab.json` or other artifacts; rebuild them with their module and keep
  `tests.test_vol_lab` (the reproduce test) passing.
- The live record is sealed evidence: never edit `runtime-state/` on the `live-data` branch, never
  change sealing, recording or scoring semantics, and never move the pinned promotion pair
  (`har-iv/2` vs `har_iv_range`, 52 independent weeks, one look).
- `main` is protected: work on a branch, open a PR, wait for green CI (Tests, CodeQL, Reproduce),
  squash-merge. Never force-push. Merging a change to `src/`, `backend/` or `package.json` triggers the
  Refresh workflow, which deploys GitHub Pages.
- Dependencies: pin only releases at least 14 days old (Dependabot has a 14-day cooldown).

## Commands (a cloud session's startup hook installs everything: `.venv` and `node_modules`)

- Frontend gate: `npm run check` (eslint, tests, build, `npm audit --audit-level=low`).
- Python gates (venv on PATH): `ruff check backend/ scripts/ tests/ freeze.py ./*.py --select F,E9`,
  `ruff check`, `mypy`, `bandit -q -r backend scripts freeze.py`,
  `python -B -m unittest discover -s tests`,
  `PYTHONPATH=. python -m cogapp --check README.md docs/MODEL_CARD.md docs/DATASHEET.md`,
  `python -B scripts/make_figures.py && git diff --stat -- docs/figures` (must be empty),
  `python scripts/validate_frozen_payload.py tests/fixtures/valid_frozen_payload.json`.
- Reproduce test, as `.github/workflows/reproduce.yml` runs it (about 4 minutes):
  `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_CORETYPE=Haswell
  NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR" PYTHONHASHSEED=0 PYTHONPATH=. RUN_SLOW=1
  VOL_LAB_REBUILT=/tmp/vol_lab.rebuilt.json python -B -m unittest -v
  tests.test_vol_lab.ArtifactContractTest.test_artifact_reproduces_from_the_snapshot`.
- See the UI with real data: `npm run build`, copy `dist/` somewhere, download `data.json`,
  `research.json` and `price.json` from the live site into it, serve it with `python3 -m http.server`,
  and render with Playwright (Chromium is preinstalled in cloud sessions; use locale en-US and
  timezone America/Chicago).
