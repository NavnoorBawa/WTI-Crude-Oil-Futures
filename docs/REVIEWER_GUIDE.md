# Reviewer guide: check the main claims in about ten minutes

This guide is for a reader who wants to check the project without trusting its author. Each step
names a claim, the command that tests it, and what a passing result looks like. The numbers
themselves are in the [README](../README.md) and the [full results](RESULTS.md); nothing here asks
you to accept one.

**Setup (two minutes).** Python 3.12+ and git.

```bash
git clone https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures.git && cd WTI-Crude-Oil-Futures
python -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

## 1. The headline is generated from the artifacts (one minute)

Claim: every table, result statistic and data-dependent verdict in the README and the full results
is generated from `data/*.json`, including whether a result is called significant.

```bash
PYTHONPATH=. python -m cogapp --check README.md docs/RESULTS.md docs/MODEL_CARD.md docs/DATASHEET.md docs/PREREGISTRATION.md
```

Passing: one `Checking <file>` line per page and exit status 0. The sentences are written in
[`scripts/readme_blocks.py`](../scripts/readme_blocks.py) conditional on the data, so "statistically
tied" or "inconclusive: not significant at 5%" follows from the p-value in the artifact, not from the
prose. On the dashboard, each section's source line names the payload fields it reads.

## 2. The benchmark field reproduces from pinned inputs (four minutes)

Claim: `data/vol_lab.json`, the source of the model comparison, is rebuilt from a data snapshot whose
digests are pinned, with no network access.

```bash
sha256sum data/market/*.csv          # compare with the sha256 fields in data/market/MANIFEST.json
# the numeric environment the Reproduce workflow pins (single-threaded BLAS, one CPU kernel)
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_CORETYPE=Haswell \
  NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR" PYTHONHASHSEED=0 PYTHONPATH=. RUN_SLOW=1 \
  python -B -m unittest -v tests.test_vol_lab.ArtifactContractTest.test_artifact_reproduces_from_the_snapshot
```

Passing: the three digests match the manifest, and the test prints `ok`. It rebuilds the whole
field (14 forecasters, every out-of-sample day, the Model Confidence Set and SPA bootstraps with their
fixed seed) and compares it with the committed file: every model set, flag, label and count
identical, and every number within 1e-4 relative or one unit of its last published decimal (GARCH
fits differ in their last bits across CPU kernels; see
[section 7 of the full results](RESULTS.md#7-reproduce)). If you would rather not run it, the
[Reproduce workflow](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/reproduce.yml)
does the same every week and on every change to the lab's inputs.

## 3. Live forecasts were committed before the prices they are scored on (two minutes)

Claim: each live forecast is recorded and sealed before the settlement that opens its scoring
window, and only those are scored.

```bash
git fetch origin live-data
git show origin/live-data:runtime-state/vol_track_record.json | python scripts/verify_record.py -
```

Passing: a table with one row per recorded forecast and `OK: every entry passes`.
[`scripts/verify_record.py`](../scripts/verify_record.py) uses the record file alone. For each entry
it re-derives the scoring window from the issue and record times, the margin between the seal and
the 14:30 ET settlement that opens the window (it must exceed five minutes for any entry that is
scored, skipped or expired), whether the window spans a contract roll (such entries are never
scored), the realized volatility from the six closes the entry stores, its QLIKE loss, and, for
entries that store their fit, the forecast from its own coefficients and inputs.

A git commit's date is set by its committer, so the record's own timestamps are not enough. To
check one against GitHub's clock, pick an entry and compare its `sealed_at` with the time of the
push that delivered it:

```bash
git log --format='%H %cI %s' origin/live-data -- runtime-state/vol_track_record.json | head
```

then open the Refresh run that made that commit under
[Actions](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/workflows/refresh.yml): its
"Persist live vol track record" step logs the push, timestamped by GitHub. GitHub keeps run logs
for a limited period, so this check is strongest for recent entries.

## 4. A failed model is reported, not dropped (one minute)

Claim: the project's first model, a machine-learning direction forecaster, looked profitable only
because of a look-ahead leak, and it is retracted.

Read [section 4 of the full results](RESULTS.md#4-post-mortem-the-direction-model): the leaked and
purged backtests side by side, and the same leak producing a profit on a pure random walk. The
corrections to every number the project published before are in the
[errata](../CHANGELOG.md#errata-published-numbers-that-were-wrong). The direction model's code is
kept, so the post-mortem's runs can be repeated.

## 5. No result is stated beyond its test (two minutes)

Claim: every hypothesis the project tested is listed with its test and a multiplicity adjustment,
and only the forward record can promote the challenger.

```bash
python -c "import json; s = json.load(open('data/research_ledger.json'))['summary']; print(json.dumps(s['by_status'], indent=1))"
python -B -m unittest tests.test_research_ledger
```

Passing: the counts by status, and the ledger test (which rebuilds the ledger from the artifacts and
requires it to equal the committed file) prints `OK`. The
[full results' ledger section](RESULTS.md#6-research-ledger) says how many supported rows survive
Benjamini-Hochberg and Holm, and why the challenger's pairwise test is left out of that family (it
was chosen by searching the same sample; Hansen's SPA test is its search-adjusted counterpart). The
promotion rule, fixed before the first live forecast and pinned in code, is quoted in
[section 3 of the full results](RESULTS.md#3-live-record-and-governance), with the power the single
test has. [`PREREGISTRATION.md`](PREREGISTRATION.md) states it as a pre-registration (hypotheses,
sample and stopping rule, analysis, what was known when it was fixed, deviations), with its tables
and figures generated from the same constants and the commands that date it.

## What would change the conclusions

- The forward record: after 52 independent paired weeks the promotion test runs once, and its
  verdict stands (section 3 gives its power, which is low).
- Intraday data: five daily returns make a noisy proxy for weekly volatility; a cleaner target could
  separate models that this one cannot.
- A different sample: the field, the challenger and the robustness checks were all chosen while
  looking at 2008–2026; [section 9](RESULTS.md#9-limitations) lists this and the other limits.
