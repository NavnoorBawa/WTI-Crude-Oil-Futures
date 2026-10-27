# Roadmap: from 2.1.0 to a research tool a professional quant team would rely on

`docs/HANDOFF.md` is the operational file: the current state, the short backlog and the loop a
session repeats. This file is the destination: what "good" means for this project, measured, and
the stages that get there. A session picks the next unfinished item here, works it through the
loop in `HANDOFF.md`, and ticks it off below with the PR number. Keep both files current.

Written 2026-10-27 at 2.1.0. Every item is grounded in the code as it stands; where an item needs
a fact checked first, it says so. The house rules in `CLAUDE.md` outrank everything here: no item
is worth an overstated claim, a touched seal or a hand-edited artifact.

## The bar

A top quant research team relies on a tool when it can verify it without trusting its author.
Each line below is a property with a test; the project is "there" when every line holds.

| Property | Test a reviewer can run | Status at 2.1.0 |
| --- | --- | --- |
| Every number on the page is computed from the payloads | `tests/frontend` render tests; no statistic literal in copy | holds |
| The main result reproduces bit for bit from pinned inputs | Reproduce workflow on `data/vol_lab.json` | holds (weekly) |
| The forward record cannot be back-filled, and that is provable to an outsider | git seal before the window, plus an independent timestamp anchor | partial: sealed in git, no external anchor |
| The promotion test was fixed before any result arrived | a pre-registered, citable document with a persistent identifier | partial: pinned in code and README, no DOI |
| Forecast quality is judged with the right losses, at the right horizons, conditionally | QLIKE, MSE, MCS, SPA, DM today; density and conditional tests, several horizons | partial |
| The model is specified, bounded and attributable | model card, elasticities, hand-checkable arithmetic; per-call attribution | partial: no per-call coefficients yet |
| The interface is correct at every width and for every reader | 0 overflow, 0 console errors, axe clean, keyboard complete, print | holds; not yet enforced in CI |
| A reviewer can evaluate the project in ten minutes | a guide that says what to check and in what order | missing |
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

1. **Tag and release every version.** No git tag exists today: 2.0.0 and 2.1.0 are only CHANGELOG
   headings. Tag `v2.1.0` on its merge commit, create GitHub Releases from the CHANGELOG sections,
   and tag every later version. Acceptance: `git tag` lists each CHANGELOG version; a release
   workflow (or a documented manual step) makes the tag from the version in `package.json`.
2. **A persistent identifier for the pre-registration.** Enable the free Zenodo-GitHub
   integration so each release gets a DOI, put the DOI in `CITATION.cff` and METH, and make the
   promotion-test rule a section of a versioned `docs/PREREGISTRATION.md` that the release
   snapshots. The rule itself does not change (house rule); what changes is that its date and text
   become citable. Acceptance: METH cites a DOI that resolves to a snapshot containing the rule.
3. **Independent timestamp anchor for the sealed record.** Git timestamps are self-reported; the
   GitHub Actions run log is the only outside witness today and expires. After the publish job's
   seal commit, anchor the commit SHA and the record file's SHA-256 in a public transparency log:
   Sigstore Rekor (free, immediate, `rekor-cli upload --artifact <sha file>`) or OpenTimestamps
   (free, Bitcoin-anchored, proof upgrades after about an hour). Store the proof beside the record
   on `live-data` (`runtime-state/anchors/<commit>.json`), and show "anchored <time>" with a link
   on each LIVE row that has one. Constraints: additive only; a missing anchor never changes a
   row's status; the seal step and its 4-minute budget stay as they are (anchor after the push,
   in its own step, with its own failure message). Acceptance: a reviewer can verify one row's
   anchor with the public tool, following a sentence in METH.
4. **Hash chain across record entries.** Each new entry stores `previous_sha256` of the entry
   before it, computed over the entry's sealed JSON. The validator checks the chain, LIVE shows a
   broken chain as a warning. Old entries stay as they are (null link). Acceptance: a test that
   edits a sealed entry in a copy of the record makes the validator fail.
5. **Failures announce themselves.** Every scheduled workflow (refresh, price, reproduce, tests)
   opens or updates one GitHub Issue on failure and closes it on the next success, using
   `actions/github-script` or the `gh` CLI with the default token (free). A late commit on the
   record (a `late_commit` status) opens an issue too. Acceptance: force a failure on a branch
   and see the issue; the README's status badges link to the workflows.
6. **Exact environment for reproduction.** Add a `.devcontainer/devcontainer.json` (and a
   `Dockerfile` pinned by image digest) that installs the pinned requirements, so anyone can run
   the reproduce test with one click in Codespaces or locally. Acceptance: the reproduce test
   passes inside the container and METH's "Reproduce" box names it.
7. **Second-source cross-checks in the freeze.** OVX already has Yahoo vs FRED agreement
   (`ovx_source_agreement`). Add the same for the CL=F close against EIA's daily spot (committed
   `data/eia_wti_spot_daily.json`) and against the stored contract when the front month rolls;
   publish the agreement stats in METH's data-vintage table and fail the freeze on a gap beyond a
   stated tolerance. Acceptance: a deliberately shifted close in a test payload fails validation.

## Stage 2: research depth

Every item here is a new hypothesis or test: it goes into `data/research_ledger.json` with its
multiplicity adjustment, through the module that computes it, and the reproduce test stays bit for
bit (regenerate `data/vol_lab.json` with the module, never by hand). State each result at the
strength its test supports.

1. **Density and quantile evaluation of the band.** Today the 80% band is judged by coverage and
   interval score. Add the pinball loss at the 10th and 90th percentiles and the CRPS of the
   lognormal predictive distribution implied by each model's median and residual sd, over every
   out-of-sample day, with DM tests between models on each score. Add PIT histograms by OVX
   tercile to CALB. Acceptance: CALB shows the scores with their sample; the ledger has the rows.
2. **Conditional predictive ability.** Giacomini-White tests of HAR-IV against the OVX-only
   regression conditioned on lagged OVX and on the OVX-realized gap, and a Giacomini-Rossi
   fluctuation test of the loss differential over time. The honest finding so far is that the two
   are tied on average; this answers "when does the model add anything?" Acceptance: a STAB
   figure of the rolling loss differential with the fluctuation-test band.
3. **Several horizons.** The lab forecasts one horizon (5 sessions, `vf.H`). Add 1-session and
   22-session targets with horizon-consistent HAR fits and the same evaluation, so the page can
   say where OVX (a 30-calendar-day measure) helps most. Acceptance: a horizon selector on FCST
   and MODL whose titles name the horizon.
4. **Better daily proxies and models from free data.** With daily bars only, range estimators are
   the best available realized measures: add Garman-Klass and Yang-Zhang targets next to the
   existing Rogers-Satchell proxy, a HEAVY-style model (Shephard-Sheppard) and a Realized GARCH
   with the range variance as the realized measure, and a shrinkage combination. Check first
   whether Yahoo's hourly bars (about 730 days) support an intraday-RV robustness check; if so,
   report it as a check, never as the target. Acceptance: each model has a leaderboard row, a
   ledger row and an MCS/SPA rerun; the challenger stays pinned.
5. **Expected-shortfall backtests.** RISK has Kupiec, Christoffersen and DQ for VaR. Add the
   Acerbi-Szekely ES test for the 97.5% ES the vol-targeting table already reports, per grid.
   Acceptance: a RISK column and ledger rows, with the method named in the title if it changes.
6. **Vol targeting with costs.** The vol-targeting comparison is cost-free. Add turnover and a
   stated per-contract cost, report the Sharpe difference with and without costs, and keep the
   existing conclusion (risk control, not return) unless the test says otherwise.
7. **Per-call attribution.** Store the fit's coefficients and smearing factor in new record
   entries only (additive fields in `vol_record.py`, the validator and `DATA_CONTRACT.md`), then
   show an exact "what moved the call" waterfall in the hero or LIVE. Old entries and sealing are
   untouched. Acceptance: the waterfall reproduces the call to 0.05 or is not shown.
8. **Basel traffic light.** `basel_zone_last_250` is null in the payload; compute it in
   `vol_lab.py` and show it in RISK.
9. **Data-vintage audit.** Record, for each refresh, the last session each source returned, and
   plot how often each source lagged a day (the Oct 26 case). This explains the hero's "older
   close" note with data rather than prose.
10. **Multiplicity as the ledger grows.** Every new row is declared before its result is known:
    add a `declared_at` field and a test that no supported row lacks one from now on, so the
    ledger remains a pre-registration log rather than a results list.

## Stage 3: the dashboard, for skeptical readers

1. **Per-week drill-down.** Choose any out-of-sample week (FCST table row or a URL parameter):
   show its inputs (RV5, RV22, RV66, OVX), every model's forecast and loss, the band, the realized
   value and the week's rank among weeks. Acceptance: deep link `?week=2020-03-06` renders it.
2. **Error attribution view.** Decompose each week's log error into the OVX term and the realized
   terms using the fit's coefficients (needs Stage 2.7 for live calls; the lab series already
   carry `ovx_elasticity` and `rv_elasticity`).
3. **A feed of the record.** The refresh job writes `feed.xml` (Atom) with one entry per recorded
   call and per resolution, so a reader can subscribe without visiting. Static, free.
4. **"What changed" since a snapshot.** Each `data.json` carries `frozen_at`; add a small diff of
   the headline numbers between the current snapshot and the previous one, built in the refresh
   job (the previous payload is on `gh-pages`).
5. **Downloads with a codebook.** One CSV bundle per section (the CSV buttons exist per table) and
   a `docs/CODEBOOK.md` that defines every column, unit and sample.
6. **Interface checks in CI.** Playwright screenshots of the fixture payloads at 1440 and 390 with
   a pixel tolerance, axe with zero serious violations, and a budget: CLS under 0.02, main JS
   under 320 kB. These are the checks every release in this history passed by hand.
7. **The remaining interface follow-ups** in `HANDOFF.md` section 4 (clipped FCST tick at 1280 px,
   leaderboard width at 320 px, phone deep-link drift, ledger card height, forced-colors SVG text).

## Stage 4: how a reviewer reads it in ten minutes

1. **`docs/REVIEWER_GUIDE.md`.** What to open, in what order, and what each thing proves: the
   verdict lede, the sealed record and how to verify a seal, the reproduce run, the leak
   post-mortem, the ledger. Ten minutes, with links. Link it from the README's first screen.
2. **`docs/QA.md`.** The questions an interviewer asks, answered honestly from the data: why QLIKE
   and not direction accuracy; why HAR-IV is tied with OVX alone and still the model of record; why
   the challenger is not promoted; what the retracted model taught; what would change the
   conclusions.
3. **`docs/DECISIONS.md`.** Short decision records: the pinned promotion pair, the seal margin,
   the timing control, the choice of every-day scoring with grids, the 14-day pin policy. Each
   with the alternatives considered.
4. **`docs/LIMITATIONS.md`.** Daily data only; OVX is an ETF-options index, not CL options; the
   continuous contract's roll handling; a short live record; what the project does not claim.
5. **README as a research note.** Keep the computed sections, but open with one screen: the
   question, the answer, the three strongest numbers with their tests, the live record's status,
   and links to the guide, the site and the reproduce run. Badges that mean something: Tests,
   Reproduce, CodeQL, the DOI.

## Stage 5: engineering hygiene

1. **Data-contract tests across the boundary.** A test that builds `data.json` and
   `research.json` with `freeze.py` and `build_research_bundle.py` from the fixtures and renders
   the dashboard against them, so a payload change cannot break the page silently.
2. **Refresh job time budget.** The seal must land at least 5 minutes before the settlement; record
   each job's duration in the run summary and alert (Stage 1.5) when a run gets within 2 minutes
   of the budget.
3. **Test speed and flake policy.** Keep `npm test` under 60 s and the Python suite under 90 s;
   a flaky test is fixed or deleted the week it flakes, never retried in CI.
4. **Code health passes.** Dead CSS audit (`.stat-cell.is-stacked`, `.select` capitals), module
   boundaries in `src/lib` (research, assertions, verdict, tiles overlap), bundle policy in
   `vite.config.js` with a size test.
5. **Capture scripts in the repo.** The README screenshot and share image were made with scratch
   scripts; commit `scripts/capture_figures.mjs` so they regenerate from a build.

## Non-goals

These stay out, whatever a review suggests, unless the owner changes the rules:

- Paid data, paid services, analytics or tracking of readers.
- Trading signals, position recommendations, or any claim about direction. The direction model is
  retracted and stays retracted.
- Moving the promotion test, its pair, its sample size or its single look.
- Replacing the sealed record with anything regenerable.
- Marketing language. The site's strength is that it says exactly what the tests support.

## Done

Tick items here with the PR number and date when they ship, and move any new finding into
`HANDOFF.md`'s backlog.

- (none yet beyond 2.1.0)
