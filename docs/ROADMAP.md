# Roadmap: from 2.1.0 to a research tool a professional quant team would rely on

`docs/HANDOFF.md` is the operational file: the current state, the short backlog and the loop a
session repeats. This file is the destination: what "good" means for this project, measured, and
the stages that get there. A session picks the next unfinished item here, works it through the
loop in `HANDOFF.md`, and ticks it off in the Done list below with the PR number. Keep both files
current.

Written 2026-10-27 at 2.1.0 from this session's work and two independent expert reviews of the
code (research rigour; engineering and reader experience). Every item is grounded in the code as
it stands; where an item needs a fact checked first, it says so. The house rules in `CLAUDE.md`
outrank everything here: no item is worth an overstated claim, a touched seal or a hand-edited
artifact.

## The bar

A top quant research team relies on a tool when it can verify it without trusting its author.
Each line below is a property with a test; the project is "there" when every line holds.

| Property | Test a reviewer can run | Status at 2.2.0 |
| --- | --- | --- |
| Every number on the page is computed from the payloads | `tests/frontend` render tests; no statistic literal in copy | holds |
| The main result reproduces bit for bit from pinned inputs | Reproduce workflow on `data/vol_lab.json` | holds (on every change to the pipeline, its inputs or its pins, and weekly) |
| The forward record cannot be back-filled, and that is provable to an outsider | git seal before the window, plus an independent timestamp anchor | partial: sealed in git and checked from the file alone (`scripts/verify_record.py`); no external anchor |
| The promotion test was fixed before any result arrived | a pre-registered, citable document with a persistent identifier | partial: pinned in code and docs, in tagged releases; no DOI |
| Forecast quality is judged with the right losses, at the right horizons, conditionally | QLIKE, MSE, MCS, SPA, DM today; density and conditional tests, several horizons | partial: SPA studentized with robustness; density, conditional tests and horizons to do |
| The model is specified, bounded and attributable | model card, elasticities, hand-checkable arithmetic; per-call attribution | holds from 2026-10-28: each new call stores its fit and the hero splits the change between two such calls; the six earlier sealed calls have no stored fit |
| The interface is correct at every width and for every reader | 0 overflow, 0 console errors, axe clean, keyboard complete, print | partial: the Interface job checks overflow, console errors, serious and critical axe violations, keyboard and print at 1440 and 390 px; open gaps (placeholders at 320 px, hash landings at about 600 px, the hero's median dot at some widths, separators for screen readers) are in `HANDOFF.md` section 4 |
| A reviewer can evaluate the project in ten minutes | a guide that says what to check and in what order | holds: README landing page and `docs/REVIEWER_GUIDE.md` |
| Nothing depends on a paid service or a person remembering to run something | GitHub Actions only, with failures that announce themselves | partial: failures are silent |

## Stage 0: always on

These never finish; every session checks them first.

- **Live record resolution.** From 2026-10-28 the first windows resolve. Check the live page end
  to end against `runtime-state/vol_track_record.json` on `live-data`: realized values, "in band",
  QLIKE facts, the LIVE title, the hero's change line and `spans_roll` rows.
- **Dependencies.** Merge Dependabot PRs that pass CI once they are 14 days old (the cooldown now
  enforces the age). Dates for the currently held-back ones are in `HANDOFF.md`.
- **Reproduce status.** The weekly rebuild (`reproduce.yml`, Mondays) must stay green. A red run
  is a research incident: find the cause before any other work.
- **Secrets.** Owner-only: delete the unused Actions secrets (`NEWSAPI_KEY`, `FINNHUB_API_KEY` and
  similar); keep `EIA_API_KEY`.

## Stage 1: evidence an outsider can verify without trusting us

1. **Tag and release every version, with checksums.** No git tag exists today: 2.0.0 and 2.1.0 are
   only CHANGELOG headings. Add `tests/test_release_metadata.py` (the version in `package.json`,
   `pyproject.toml` and `CITATION.cff`, the CHANGELOG's top heading and its date all agree),
   `scripts/bump_version.py <major|minor|patch>` (a research-result change is MINOR with an
   Errata entry; a dashboard-only change is PATCH; a change to sealing, scoring or the pair would
   be MAJOR and is forbidden), and `.github/workflows/release.yml` on tags `v*` that creates the
   GitHub Release from the CHANGELOG section with a `SHA256SUMS` of the committed artifacts. The
   release job holds a write token, so it installs nothing and runs no third-party action; extend
   `tests/test_workflow_guards.py` to it. Tag `v2.1.0` on its merge commit first.
   Acceptance: `git tag` lists each CHANGELOG version; the metadata test fails when one file's
   version is edited alone. Refs: semver.org; keepachangelog.com.
2. **A citable pre-registration with a DOI.** Enable the free Zenodo-GitHub integration so each
   release gets a DOI; put it in `CITATION.cff` and METH. Generate `docs/PREREGISTRATION.md` by
   cog from the `PROMOTION_*` constants in `backend/vol_record.py` (the pair, the sample
   definition, the one-sided DM with Student t, alpha, the exclusion rules, `student_t_sf`), add
   the power table (`promotion_power` already produces the figure in `docs/RESULTS.md` section 3) and a "what happens
   next" section: on keep, the model of record stays and any second test is declared by new
   constants with a start date before its first entry; on promote, the swap is a maintainer PR;
   no interim decisions. Add the file to the cog gate. The rule itself never changes (house
   rule); what changes is that its text and date become citable. Acceptance: METH cites a DOI
   that resolves to a snapshot containing the rule; `cog --check docs/PREREGISTRATION.md` is in CI.
   Refs: Nosek et al. (2018) PNAS 115(11); GitHub's Zenodo citation guide.
3. **Independent timestamp anchor for the sealed record.** Git timestamps are self-reported, and
   the GitHub Actions log is the only outside witness today, with limited retention. After the
   publish job's push to `live-data`, add a failure-tolerant step that computes the SHA-256 of
   `runtime-state/vol_track_record.json` as pushed plus the commit SHA, signs that digest
   keylessly with Sigstore using the job's GitHub OIDC identity (`sigstore-python` or `cosign
   sign-blob`), which records it in the public Rekor transparency log with an integrated time,
   and stamps the same digest with OpenTimestamps (`ots stamp`; the Bitcoin attestation matures
   in hours). Store proofs outside the record (`runtime-state/anchors/<commit>.json` or a sibling
   branch), add `scripts/verify_anchors.py` (stdlib plus the public verifiers) that checks every
   anchored snapshot's time precedes the settlement that opens each sealed entry's window, show
   "anchored <time>" with a link on LIVE rows that have one, and add a sentence to METH telling a
   reviewer how to verify one. Constraints: additive only; never written into the record file;
   sealing never waits on an anchor; a missing anchor never changes a row's status; the seal
   step's 4-minute budget is untouched. Acceptance: after two scheduled publishes the verifier
   exits 0 and prints each anchor's time. Refs: docs.sigstore.dev; opentimestamps.org.
4. **Hash chain across record entries.** Each new entry stores `previous_sha256` of the sealed
   JSON of the entry before it; the validator checks the chain and LIVE shows a broken chain as a
   warning. Old entries keep a null link. Acceptance: a test that edits a sealed entry in a copy
   of the record makes the validator fail.
5. **Failures announce themselves.** A new `.github/workflows/health.yml` (daily, off-peak minute,
   plus `workflow_run` on failed Refresh and Reproduce runs; `contents: read`, `issues: write`;
   stdlib only, so the workflow guards hold) runs `scripts/record_health.py` over `git show` copies
   of `live-data`'s record and `gh-pages`'s `data.json` and opens or updates one GitHub Issue
   when: an entry is `late_commit`, `frozen_at` is older than 12 hours, a session was skipped, or
   the weekly reproduce failed; it closes the issue on the next healthy run. It never checks the
   branches out into `data/` and never calls the seal. Acceptance: `tests/test_record_health.py`
   covers each threshold with synthetic records; a `workflow_dispatch` with an injected stale
   payload opens the issue. The README's status badges link to the workflows.
6. **Exact environment for reproduction.** Add a `Dockerfile` (`python:3.12` and node images
   pinned by digest, the same `requirements*.txt` and `package-lock.json`, never a second list)
   that sets the reproduce environment variables, a `Makefile` with `lint`, `type`, `test`,
   `reproduce`, `check-frontend` and `figures` targets, and `.devcontainer/devcontainer.json`
   pointing at it, so a reviewer runs the reproduce test with one click in Codespaces or
   locally. Image digests count as dependencies (14-day policy). Acceptance: `docker build` then
   `make reproduce` prints the bit-for-bit result on an x86-64 host; METH's Reproduce box names
   it. Refs: containers.dev; Docker pull by digest.
7. **Second-source cross-checks and a roll audit for the price series.** OVX already has Yahoo vs
   FRED agreement; the series that defines the target has no second source. Add to the pinned
   snapshot the EIA Cushing spot (`data/eia_wti_spot_daily.json` exists) and Stooq `CL.F`
   (`https://stooq.com/q/d/l/?s=cl.f&i=d`), publish `cl_source_agreement` (share of days within a
   tolerance, the largest gaps with dates) and `roll_convention_mismatches` (per expiry month, the
   day Yahoo's close switches from matching EIA contract 1 to contract 2, against the lab's roll
   days), decide and document the April 20-21, 2020 negative-price return rule in METHODOLOGY,
   and fail the freeze on a gap beyond a stated tolerance. Any change to roll or negative-price
   handling changes every number downstream: rebuild through the module, refresh the cog blocks,
   and record it in the ledger as a methodology change. Acceptance: `MANIFEST.json` lists the new
   files with digests; the reproduce test is green; a test asserts the roll days against the
   EIA-derived switch days.
8. **A shadow log of the whole field, unsealed and labelled so.** Only the pinned pair is
   recorded live; in three years the question "would the MCS and SPA ranking have held out of
   sample?" cannot be answered for the other feasible models. Add `backend/field_record.py`,
   run in the build job after the live forecast, that appends one line per `vol_lab.FEASIBLE`
   model (`as_of`, `issued_at`, `model_key`, forecast, median, an inputs digest; never the timing
   control) to an append-only `vol_field_record.jsonl` on a separate `field-data` branch, first
   issuance per `as_of` wins, resolved with the same window logic as the record. The anchor step
   covers its digest too. `DATA_CONTRACT.md` says it is not sealed evidence and never feeds the
   promotion test. Acceptance: the guards prove the step writes nothing under `runtime-state/`
   and leaves the seal's inputs unchanged. Refs: White (2000) Econometrica 68(5); Hansen, Lunde,
   Nason (2011) Econometrica 79(2).

## Stage 2: research depth

Every item here is a new hypothesis or test: it goes into `data/research_ledger.json` with its
multiplicity adjustment, through the module that computes it, and the reproduce test stays bit for
bit (regenerate `data/vol_lab.json` with the module, never by hand). State each result at the
strength its test supports. Nothing here re-selects the challenger: the pair is pinned.

1. **The QLIKE floor set by proxy noise.** QLIKE 0.370 against 0.952 reads as most of the way to
   perfect, but a perfect forecast of the true weekly variance scores well above zero against a
   five-return proxy: under normal returns the floor is E[X − ln X − 1] with X ~ χ²(5)/5, which is
   −ψ(5/2) − ln(2/5), about 0.213 (verify in code). Add `qlike_floor()` to `vol_lab.py` with the
   closed form, a simulated floor on the same Student-t simulations the noise floor uses (five
   returns, and the range proxy), and a kurtosis-matched floor; publish it with sims and seed;
   show it beside the loss in MODL and the tiles, described as the proxy's noise floor, never as
   the model's error, never subtracted from any loss or test. Acceptance: the closed form equals
   −digamma(2.5) − ln(0.4) to 1e-9 and the simulation agrees within 0.01. Refs: Patton (2011) J.
   Econometrics 160(1); Hansen and Lunde (2006) J. Econometrics 131.
2. **Coverage and loss by regime.** Pooled 80% coverage hides years at 71% and 88%. Add
   `coverage_by_regime_pct`, `interval_score_by_regime` and `qlike_by_regime` keyed by OVX
   tercile at the decision (cut points from the expanding past only), the sign of
   log(OVX_{t−1}/RV5_t) against its trailing median, the RV percentile bucket and whether the
   window contains a roll; a conditional-coverage test (miss indicator on regime dummies, HAC
   Wald) with per-regime binomial p; a CALB table with n per row; a "by regime" toggle on MODL;
   the OVX-RV gap in the chart series. A dependence found here is reported, never used to retune
   the interval. Acceptance: a no-look-ahead test shows later OVX values leave regime labels up to
   t unchanged; counts sum to n_days. Refs: Christoffersen (1998) IER 39(4); Bekaert and Hoerova
   (2014) J. Econometrics 183(2).
3. **Conditional predictive ability and stability over time.** DM, MCS and SPA answer "which is
   better on average over 2008-2026". Add to `forecast_eval.py` a Giacomini-White test
   (differential on a constant, its lag and lagged instruments: log OVX, log(OVX/RV5); HAC Wald,
   χ² with q df) and a Giacomini-Rossi fluctuation test (rolling DM over a window of μ·n with
   the published 5% critical value for that μ); publish per model `gw_vs_har_iv` and the
   fluctuation path, plus weekly cumulative QLIKE differentials against persistence, the
   challenger and raw OVX. Both are deterministic, so reproduction stays bit for bit. STAB gets
   the rolling differential with the fluctuation band. Acceptance: synthetic tests show GW
   rejects when the differential is predictable and holds 5% size when it is not; the path
   equals a brute-force rolling DM. Refs: Giacomini and White (2006) Econometrica 74(6);
   Giacomini and Rossi (2010) J. Applied Econometrics 25(4).
4. **Score the whole predictive distribution.** The band is judged by coverage and interval score
   only. Add pinball loss at τ = 0.10 and 0.90, CRPS from a quantile grid (τ = 0.05 to 0.95), PIT
   histograms and the Berkowitz test, with DM tests between models on each score. Quantiles for
   HAR-IV, the challenger and OVX-only come from trailing matured log-residual quantiles with the
   same window and delay as the conformal band; GARCH-family from the fitted Student-t scaled to
   the 5-session variance; EWMA from a normal. Add a quantile-HAR competitor (`statsmodels`
   QuantReg on the purged design) as a new hypothesis with its ledger row and an MCS/SPA rerun; it
   never becomes eligible for the live pair. QLIKE stays the headline. Acceptance: CRPS of a point
   forecast equals MAE; the grid CRPS converges to the Gaussian closed form on normal data;
   Berkowitz rejects about 5% on uniform PITs. Refs: Gneiting and Raftery (2007) JASA 102(477);
   Gneiting and Ranjan (2011) JBES 29(3); Berkowitz (2001) JBES 19(4).
5. **Several horizons.** The lab forecasts one horizon (5 sessions, `vf.H`). Add 1-session and
   22-session targets with horizon-consistent HAR fits and the same evaluation, so the page can
   say where OVX (a 30-calendar-day measure) helps most. Acceptance: a horizon selector on FCST
   and MODL whose titles name the horizon.
6. **Better daily proxies and models from free data.** With daily bars only, range estimators are
   the best available realized measures: add Garman-Klass and Yang-Zhang targets next to the
   existing Rogers-Satchell proxy, a HEAVY-style model (Shephard-Sheppard) and a Realized GARCH
   with the range variance as the realized measure, and a shrinkage combination. Check first
   whether Yahoo's hourly bars (about 730 days) support an intraday-RV robustness check; if so,
   report it as a check, never as the target. Acceptance: each model has a leaderboard row, a
   ledger row and an MCS/SPA rerun.
7. **Expected-shortfall backtests.** RISK has Kupiec, Christoffersen and DQ for VaR; the 97.5% ES
   the overlay quotes is never tested. Add to `forecast_eval.py` the Acerbi-Szekely Z1 and Z2
   tests with simulation p-values under each method's own distribution (fixed seed and stated
   replications, so reproduction stays bit for bit) and the Fissler-Ziegel FZ0 joint VaR-ES score
   (Patton, Ziegel and Chen 2019) with DM comparisons between methods; per method and grid in
   `var_backtest`, with `*_pass_grids` counters as for VaR. A pass is "not rejected", never
   "validated". Acceptance: FZ0 is minimised at the true (VaR, ES) on simulated data; Z2 rejects
   about 5% under the null; RISK renders the ES columns.
8. **Vol targeting with costs.** The overlay reports Sharpe differences at one cost. Add per book
   the turnover per year, Sharpe by cost over {0, 2.5, 5, 10, 20} bp, the breakeven cost at which
   any forecast advantage over trailing-22-day sizing vanishes (null if never positive), and the
   realized-utility gain over trailing sizing with a DM test and a stationary-bootstrap CI; a
   ledger row; one computed sentence in `docs/RESULTS.md` section 2.4. Fields are additive and optional in
   the validator, since the production validation is a recorded run on downloaded data. Keep the
   existing conclusion (risk control, not return) unless the test says otherwise.
9. **Per-call attribution.** Store the fit's coefficients and smearing factor in new record
   entries only (additive fields in `vol_record.py`, the validator and `DATA_CONTRACT.md`), then
   show an exact "what moved the call" waterfall in the hero or LIVE. Old entries and sealing are
   untouched. Acceptance: the waterfall reproduces the call to 0.05 or is not shown.
10. **Basel traffic light.** `basel_zone_last_250` is null in the payload; compute it in
    `vol_lab.py` and show it in RISK.
11. **Data-vintage audit.** Record, for each refresh, the last session each source returned, and
    plot how often each source lagged a day (the Oct 26 case). This explains the hero's "older
    close" note with data rather than prose.
12. **Multiplicity as the ledger grows: online control and registered rows.** BH and Holm are
    recomputed over the whole family whenever a row is added, so an old claim's status can flip
    because of hypotheses tested later, and only 3 of 76 rows are pre-specified. Add to
    `research_ledger.py` online false-discovery control (LORD++, Ramdas et al. 2017; SAFFRON,
    Ramdas et al. 2018) at FDR 0.05 over rows ordered by date, storing per row the online alpha
    and rejection flags and a wealth path in the summary, with BH, Holm and HLZ kept as the batch
    view. Add a status `registered`: a row declared with `registered_on`, a null p-value and the
    artifact field path that will hold it; the rebuild test requires that field to exist before
    the status changes. From now on every new hypothesis is registered before its result is
    known. Acceptance: unit tests reproduce the LORD++ and SAFFRON rejection sets on a fixture
    from a reference implementation; existing rows' batch fields are unchanged.
13. **A second batch of free-data models: measurement error and shrinkage.** HARQ-lite (the RV5
    coefficient interacted with the square root of a daily realized quarticity proxy), a ridge
    HAR-IV with the penalty chosen from trailing purged QLIKE over a small declared grid, a
    loss-weighted combination shrunk toward equal weights, and a threshold HAR on the sign of the
    OVX-RV gap. Each is a new hypothesis with an MCS/SPA rerun and the no-look-ahead test. The
    live challenger stays `har_iv_range` whatever the ranking: `choose_challenger` and the live
    challenger path must not re-select. Refs: Bollerslev, Patton and Quaedvlieg (2016) HARQ.
14. **Start collecting a free implied-vol term structure now.** No free OVX term-structure
    history exists, so every uncollected day is lost. A failure-tolerant build step fetches the
    USO option chain (yfinance) for the expiries nearest 30, 60 and 90 days, computes a
    VIX-style model-free variance per expiry (or an ATM-interpolated implied vol where strikes
    are sparse), and appends rows to an append-only file on an `aux-data` branch; check whether
    CME's CVOL end-of-day values for WTI are published free. It never blocks the publish job and
    never writes under `runtime-state/`. The implied-realized gap enters the field as a declared
    hypothesis only once a year of rows exists. Acceptance: after one run the branch holds the
    day's rows with at least two expiries; a schema test; terms of use stated in the datasheet.
15. **A specification curve over the lab's fixed design choices.** The sample start, window
    type, purge length, HAR lags, retransformation and OVX lag were set while looking at the
    data, as `docs/RESULTS.md` section 9 says. `scripts/build_specification_curve.py` reruns the walk-forward
    over a grid declared in its docstring before the first run (training start, expanding vs
    rolling window, purge 4 or 9, three lag sets, smearing vs normal vs none, OVX lag 1 or 2) into
    `data/vol_lab_specifications.json`, a robustness artifact beside the pinned one, and STAB
    shows the share of specifications in which the headline holds. Never used to pick a better
    specification after the fact: the pinned settings stay. Acceptance: the artifact reproduces
    within `REPRO_REL_TOL`; the grid in the docstring equals the artifact's grid field.
    Refs: Simonsohn, Simmons and Nelson (2020) Nature Human Behaviour 4.

## Stage 3: the dashboard, for skeptical readers

1. **Per-week drill-down.** Choose any out-of-sample week (FCST table row or a URL parameter):
   show its inputs (RV5, RV22, RV66, OVX), every model's forecast and loss, the band, the realized
   value and the week's rank among weeks. Acceptance: deep link `?week=2020-03-06` renders it.
2. **Error attribution view.** Decompose each week's log error into the OVX term and the realized
   terms using the fit's coefficients (needs Stage 2.9 for live calls; the lab series already
   carry `ovx_elasticity` and `rv_elasticity`), and publish from `vol_lab.chart_series` the
   inputs and every model's forecast on grid 0 plus cumulative QLIKE differentials (record minus
   challenger, minus OVX-only, minus persistence), so a reader sees whether a gap is a few weeks
   in 2020 or a steady edge. A cumulative path is descriptive: titles count weeks, never claim
   significance.
3. **A feed of the record.** `scripts/build_feed.py` (standard library) runs in the build job
   after the record step and writes `public/feed.xml` (Atom) with one entry per recorded call and
   per resolution; the entry text says the call is recorded and links the record's commit history
   for the seal, since the seal happens in the publish job. A downloads manifest with digests
   sits beside it. Acceptance: the feed validates at the W3C feed validator; the manifest's
   digests match `sha256sum`.
4. **"What changed" since a snapshot.** In the build job, `git show origin/gh-pages:data.json`
   (read-only) and `scripts/snapshot_diff.py` produce `public/changes.json`: both `frozen_at`
   values, the as-of change, forecast and band deltas for the record and the challenger, new or
   resolved entries, a research hash change; the top bar shows it in a disclosure. Deltas are
   numbers, never judgements. Acceptance: tests cover identical snapshots, a moved forecast, a
   resolved entry, a hash change and no previous file.
5. **Downloads with a codebook.** One CSV bundle per section (the CSV buttons exist per table) and
   a `docs/CODEBOOK.md` that defines every column, unit and sample.
6. **Interface checks in CI.** Playwright screenshots of the fixture payloads at 1440 and 390 with
   a pixel tolerance, axe with zero serious violations, and a budget: CLS under 0.02, main JS
   under 320 kB. These are the checks every release in this history passed by hand.
7. **The remaining interface follow-ups** in `HANDOFF.md` section 4 (clipped FCST tick at 1280 px,
   leaderboard width at 320 px, phone deep-link drift, ledger card height, forced-colors SVG text).
8. **An offline accessibility-structure test** in `npm test`, on the server-rendered markup: one
   h1 and no skipped heading level; every section labelled by its heading; every table with a
   caption, every figure with a figcaption, every canvas container described by its table view;
   every control with a name; every scroll region keyboard reachable (the METH Reproduce block
   first). It complements axe in the browser (3.6) and runs on every PR. The accessibility
   statement claims only what is tested.

## Stage 4: how a reviewer reads it in ten minutes

1. **`docs/REVIEWER_GUIDE.md`.** Five timed steps with every statistic in a cog block (added to
   the cog gate): one minute on the results at a glance; two on the latest Reproduce run's
   summary against the committed `data/vol_lab.json` and `MANIFEST.json` digests; three on
   verifying the newest sealed entry (the `live-data` history link, the publish log, and
   `scripts/record_health.py --latest`, a read-only stdlib helper that prints the entry's
   `sealed_at`, `base_session` and the margin between them); two on the leak post-mortem; two on
   the ledger's multiplicity accounting. Link it from the README's first screen.
   Refs: ACM Artifact Review and Badging; Stodden, Leisch, Peng (2016) Science 354.
2. **`docs/QA.md`.** The questions an interviewer asks, answered honestly from the data: why QLIKE
   and not direction accuracy; why HAR-IV is tied with OVX alone and still the model of record; why
   the challenger is not promoted; what the retracted model taught; what would change the
   conclusions.
3. **`docs/DECISIONS.md`.** Short decision records: the pinned promotion pair, the seal margin,
   the timing control, every-day scoring with grids, the 14-day pin policy. Each with the
   alternatives considered.
4. **`docs/LIMITATIONS.md`.** Daily data only; OVX is an ETF-options index, not CL options; the
   continuous contract's roll handling; a short live record; what the project does not claim.
5. **README as a research note.** The README is about 740 lines and 75 kB, so a reviewer never
   reaches the system or limitations sections. Move the paper (sections 1 to 7 and the
   references) to `docs/RESULTS.md` with every cog block intact and the file added to the cog
   gate; the README keeps the title, badges, the computed abstract and results at a glance,
   Figure 1, a "verify it in ten minutes" box linking the reviewer guide, a short system section
   and the limitations. Moving a statistic out of a cog block into plain text would silently
   exempt it from the drift check: never do that. Acceptance: README under about 220 lines; a
   link-check test; badges that measure something (Tests, Reproduce, CodeQL, the DOI).
6. **Contributing guide, PR template and issue templates.** `CONTRIBUTING.md` carries the loop,
   every gate command through a cog block so it cannot drift from `CLAUDE.md`, how to add a model
   (register the hypothesis, rebuild through the module, reproduce test, a new challenger means a
   new declared test), the artifact rule, the 14-day dependency policy, the flake policy, and the
   list of things a PR may never touch. The PR template asks for the gates run and the house-rule
   check.

## Stage 5: engineering hygiene

1. **The data contract as code.** `DATA_CONTRACT.md` is prose, the validator re-encodes part of it
   by hand, and the fixtures drift from what `freeze.py` writes. Add `backend/payload_schema.py`
   (TypedDicts under mypy strict, standard library only so the publish job stays stdlib) with a
   `required_paths()` list; make `validate_frozen_payload.py` walk it instead of hand-written key
   tuples; generate the field tables in `DATA_CONTRACT.md` from it by cog; export the path list
   for a frontend test that fails when a fixture loses a required field; regenerate the fixtures
   with a script. Additive only: no record field is renamed.
2. **Pipeline rehearsal, offline.** `tests/test_pipeline_rehearsal.py`: with recorded provider
   fixtures and the network blocked, run freeze, validate, record, seal and resolve in a temp
   directory exactly as `refresh.yml` does, advance the clock six sessions, run the chain again,
   and assert the second run is a byte-identical no-op and the entry resolves. It calls the real
   seal and resolve functions unchanged and never points at the repository's `data/` or
   `runtime-state/`. Target under 20 s.
3. **Refresh job time budget.** The seal must land at least 5 minutes before the settlement; record
   each job's duration in the run summary and alert (Stage 1.5) when a run gets within 2 minutes
   of the budget.
4. **Test speed and a flake policy.** `npm test` takes about 137 s because each of the 17 suites
   starts its own Vite SSR server. Memoise the server and transformed modules in
   `tests/frontend/load.mjs` (or prebuild once with `vite build --ssr` into a cache keyed by the
   content of `src/` and `vite.config.js`) and bring it under 45 s with no test rewritten; keep
   the Python suite under 90 s; a flaky test is fixed or deleted the week it flakes, never retried.
5. **Code-health gates.** Split `src/lib/research.js` (about 900 lines) into payload accessors and
   domain modules; enforce boundaries with eslint `no-restricted-imports` (lib never imports
   panels, charts or components); a dead-CSS test over `src/styles/*.css` and `src/index.css`
   (`.stat-cell.is-stacked` and `.select` capitals are the first suspects); a per-chunk size test
   from Vite's build manifest. Compare the server-rendered markup before and after any move: no
   computed title or number may change.
6. **Capture scripts in the repo.** The README screenshot and share image were made with scratch
   scripts; commit `scripts/capture_figures.mjs` so they regenerate from a build.
7. **Mutation testing on the scoring and sealing logic.** Line coverage says nothing about
   whether a wrong boundary (a `<=` for a `<` on the seal margin) would be caught. Add `mutmut`
   (pinned, 14-day policy) over `backend/vol_record.py` and `backend/forecast_eval.py` as a
   weekly, read-only job with a recorded baseline that lists surviving mutants in the step
   summary; a survivor in sealing or scoring is a reason to add a test, never to touch the
   semantics. Property tests for the frontend formatters and URL-state parsers complete it.

## Non-goals

These stay out, whatever a review suggests, unless the owner changes the rules:

- Paid data, paid services, analytics or tracking of readers.
- Trading signals, position recommendations, or any claim about direction. The direction model is
  retracted and stays retracted.
- Moving the promotion test, its pair, its sample size or its single look. The shadow field log
  (Stage 1.8) and the density scores (Stage 2.4) never become a second promotion test by stealth.
- Replacing the sealed record with anything regenerable.
- Marketing language. The site's strength is that it says exactly what the tests support.

## Done

Tick items here with the PR number and date when they ship, and move any new finding into
`HANDOFF.md`'s backlog.

- **1.1 Tag and release every version, with checksums** (#75, 2026-10-30): `v2.0.0`, `v2.1.0` and
  `v2.2.0` tags; `scripts/bump_version.py`; `tests/test_release_metadata.py`;
  `.github/workflows/release.yml` publishes each tag's Release with `SHA256SUMS` from 2.2.0 on
  (`v2.0.0` and `v2.1.0` predate the workflow and are tags only).
- **2.9 Per-call attribution** (#71, 2026-10-28): new entries store their coefficients and smearing
  factor; the hero splits the change between calls.
- **2.10 Basel traffic light**: already computed before 2.1.0 (`basel_zone_last_250` at 99%); the
  backlog item was stale.
- **SPA correction** (#74, 2026-10-29, not a roadmap item): Hansen's SPA studentized, with
  robustness and an errata entry; two independent verification rounds.
- **3.6 Interface checks in CI** (#73, 2026-10-30): the Interface job (13 Playwright checks: axe,
  layout shift, overflow, keyboard, print, lazy-mount order, separator lists, script budget).
  Property assertions rather than pixel screenshots (font rasterization differs between machines).
- **3.7 The remaining interface follow-ups** (#73, partly): most of the 2.1.0 list and the
  eight-lens audit's confirmed findings. Still open, in `HANDOFF.md` section 4: the 600 px
  hash-landing shift on `#vrp` and `#ledg`, the 320 px placeholder shortfall, the line-count
  load-shift reservations (re-measured and guarded by a browser check, not replaced), the
  largest-QLIKE marker question and two polish items.
- **4.1 `docs/REVIEWER_GUIDE.md`** and **4.5 README as a research note** (#72, 2026-10-28).
- **5.6 Capture scripts in the repo** (#73): `scripts/capture_figures.mjs`.
