# Handoff: state, open backlog and working loop

Written on 2026-11-02, after the rounds that shipped ROADMAP 1.2 to 1.8 and 2.1 to 2.4 (#77 to
#105), on top of 2.2.0 (PRs #68 to #76). The next session continues from here. Keep this file current: at the end of every round, tick off what
shipped, add what the round found, and update the date.

The destination, measured, and the staged plan to reach it are in `docs/ROADMAP.md`; this file
holds the short-term backlog and the loop. When the backlog below is empty, take the next
unfinished roadmap item.

The goal is to keep raising the project to the standard a professional quant research team expects
from a research tool it relies on. That means correct and honest numbers, evidence a skeptic can
check, reproducible results, a fast and accessible interface, and clean engineering. The bar is
quality, never claims: the house rules in `CLAUDE.md` always come first.

## Current state (all on `main`, deployed)

- **Research.** HAR-IV (`har-iv/2`) on 4,634 out-of-sample days has QLIKE 0.370 against 0.952 for
  persistence. It beats all 6 models built from close-to-close returns (DM p < 0.05) and is
  statistically tied with an OVX-only regression (and with HAR-range, which uses no OVX). The
  challenger HAR-IV-range has pairwise DM p = 0.002 (before the search). The studentized Hansen SPA
  test of the whole field (2.2.0; arch 8.0.0 ignores its `studentize` flag, so the 0.072 published
  before was the non-studentized test, see the CHANGELOG errata) rejects "no alternative beats the
  model of record" at p = 0.011 (0.005 to 0.027 across blocks and halves; the 2008–2016 leader is
  the Combination), but not after the ledger's Benjamini-Hochberg adjustment (q = 0.084). The
  challenger runs live under the pinned 52-week, one-look promotion test. `data/vol_lab.json`
  rebuilds bit for bit (Reproduce workflow: on every PR or push to `main` that touches the pipeline,
  its inputs or its pins, and weekly). The losses' level is read against the proxy's QLIKE noise
  floor (`vol_lab.qlike_floor`, ROADMAP 2.1): at least 0.213 against the five-return proxy, more
  with heavier tails; shown on the QLIKE tile and in MODL, never subtracted from a loss or test.
  Coverage and loss by regime at the decision (`vol_lab.regimes`, ROADMAP 2.2): none of the eight
  tests of equal coverage across regimes is significant at 5%; the model of record across OVX
  thirds is inconclusive (p = 0.083 at 10 HAC lags, 0.111 at 20; q = 0.220). CALB states it, MODL
  has a "by regime" view (descriptive), FCST shows the OVX/RV5 gap; the eight rows are in the
  ledger's "forecast interval" family, not pre-specified. When the differences arise (ROADMAP 2.3,
  lab and ledger; RESULTS 2.8): Giacomini-White's conditional part is significant against
  persistence (surviving the ledger's adjustments), EWMA, HAR-RV, HAR-RS and LightGBM (none
  surviving; not significant with 40 Bartlett lags), and not against raw OVX, the OVX-only
  regression, the combination, HAR-range or the GARCH models; the fluctuation test, adjusted for five window lengths, mostly restates the DM rejections,
  is significant against HAR-range (not after the ledger's adjustment) and inconclusive against the
  OVX-only regression (p = 0.056) and the challenger (0.070, outside the family). 26 new ledger rows
  (110 in all, 107 in the family); the SPA row's q fell to 0.084 because the new rows include strong
  near-duplicates of DM rejections. STAB shows the rolling DM t with the fluctuation band, a
  cumulative view and both tests in its table view (ROADMAP 2.3, #101 and the dashboard part).
  The whole predictive distribution (ROADMAP 2.4, #104 and #105; RESULTS 2.9): seven models'
  quantiles scored on the band's 4,370 days by CRPS over 19 quantiles and the 10%/90% pinball
  losses, each against the model of record by a batch-means test (the 9-lag DM variance is far too
  small for these persistent differences), and Berkowitz on each sampling grid. No CRPS difference
  is significant (GARCH-t inconclusive, p = 0.088); on the 90% pinball loss the record is
  significantly lower than GARCH-t and EWMA (neither survives BH); Berkowitz rejects OVX-only,
  GARCH-t, GJR-t, EWMA (surviving BH) and the quantile HAR (not surviving), is inconclusive for the
  record (p = 0.092) and does not reject the challenger. The quantile HAR is a robustness
  competitor outside the field and the live pair. 25 new rows (135 in all, 129 in the family), no
  survival flag changed; the rebuild takes about seven minutes. CALB draws the PIT histogram (one
  row per model on one 0–16% scale, each bin's simulated chance range) under a computed Berkowitz
  summary, with the PIT shares and Berkowitz by grid in its table view; MODL's "CRPS" view
  (`?modl=dist`, opt-in; the title, default view and ranking stay QLIKE) shows the three scores with
  their batch-means t and p, the readings at each test's strength and the quantile HAR's QLIKE rerun
  as a footnote. The readings and both views' tables load with one lazy chunk
  (`charts/distributionCharts.jsx`, `lib/distributionScores.js`); what CALB's first render needs is
  in `lib/distribution.js`, in the entry (307,434 B of the 327,680 B budget; the first load's
  scripts 178,173 B gzipped of 184,320 B).
- **Live record.** Daily calls are recorded and sealed in git on the `live-data` branch before their
  window opens; each call since 2026-10-28 also stores its fit, so it recomputes from its own entry.
  The first three windows resolved on 2026-10-29, 10-30 and 10-31: the Oct 20 call (47.3%, band
  23.1–63.6%) against a realized 40.95%, the Oct 21 call (49.6%, band 24.3–66.6%) against 37.76% and
  the Oct 22 call (50.8%, band 24.9–68.2%) against 30.50%, all inside their band. The Oct 23 call
  (53.4%, band 26.2–71.8%) resolved on 2026-11-03 against 61.35%, inside its band too. The four
  windows overlap, so the page counts one independent week. The 2026-11-02 entry is the first that
  links to the one before it (one link, no break). `scripts/verify_record.py` passes on the record.
- **Dashboard (2.2.0).** 2.1.0's research terminal, plus: the hero splits each change between calls
  into its inputs, refit and smearing; the hand arithmetic shows again; titles name their samples;
  the live record explains every never-scored status and an overdue result; charts keep their frame
  and a reader's pan; no meta line, source footer or tile footnote starts or ends a wrapped line
  with a separator dot; print keeps every column and chart; first load is lighter (LCP 2.6–2.8 s on
  a throttled phone, from 3.8–4.4 s). Two independent verification rounds; every confirmed finding
  is fixed.
- **Evidence and alerts.** `docs/PREREGISTRATION.md` states the promotion test as a citable
  pre-registration, generated from its constants (ROADMAP 1.2; the DOI waits for the owner). The
  Health workflow (`.github/workflows/health.yml`, daily and after every Refresh and Reproduce run)
  keeps one issue labelled `health` open, assigned to the owner, while something needs a person,
  and fails while it does (ROADMAP 1.5). From the 2026-11-02 entry on, each sealed entry links to
  the one before it (`previous_sha256`, a hash chain without a key; ROADMAP 1.4), and
  `scripts/verify_record.py`, Health and LIVE report a break. After each publish the Refresh
  workflow's `anchor` job tries to attest the record (best effort), logging its SHA-256 in
  Sigstore's public Rekor log; `scripts/verify_anchors.py` reads the times back (ROADMAP 1.3,
  accepted on 2026-11-01 after two scheduled publishes with anchors; LIVE and a second anchor
  remain). From #95 the Refresh workflow's three field jobs keep the unsealed shadow log of the
  whole field on the `field-data` branch (ROADMAP 1.8; `backend/field_record.py`, #94).
- **Tests.** 670 Python (unittest), 287 frontend (node --test) and 16 browser checks (Playwright,
  the "Interface" CI job: axe, layout shift, overflow, keyboard, print, script budget). CI also runs
  CodeQL, ruff, mypy strict, bandit, pip-audit, npm audit, cog drift, figure determinism and the
  Reproduce rebuild. Releases: `scripts/bump_version.py`, and `.github/workflows/release.yml`
  publishes a GitHub Release with `SHA256SUMS` on each `vX.Y.Z` tag from 2.2.0 on.
- **Workflow shell under test.** `tests/test_health_workflow.py` and `tests/test_field_workflow.py`
  run steps with `bash -eo pipefail` against fakes on PATH. Never pipe into a reader that can stop
  early (`grep -q`, `head`): the writer's next write then fails and pipefail fails the pipeline.
  Cloud sessions set `PYTHONUNBUFFERED=1`, so a Python fake's `print` is two writes (text, then
  newline); that made the Health test flake under load until #106 read the body into a variable.
  A step cut off by `set -e` exits 1 like a deliberate failure, so a test checks the step's own
  `::error` line as well as its exit code.
- **Cloud sessions.** `.claude/hooks/session-start.sh` builds `.venv` (Python 3.12, pinned
  requirements) and installs `node_modules`, so every gate runs from the first command.
- **Reproduce environment.** `Dockerfile` (Python 3.12.14 and Node 24.21.0 images pinned by
  digest), `Makefile` (`make help`) and `.devcontainer/` (ROADMAP 1.6). `docker build -t wti-vol .`
  then `docker run --rm wti-vol make reproduce` rebuilt `data/vol_lab.json` bit for bit on an Intel
  x86-64 host on 2026-10-31; the Reproduce workflow's container job repeats it on every change to
  the lab's inputs or the container and weekly. A cloud session can run Docker: start `dockerd` in
  the background first; behind the session's TLS proxy, a local build needs the proxy's CA added in
  a scratch copy of the Dockerfile (never in the committed one).

## Open backlog, most valuable first

### 1. Watch the live record
- Look at the open `health` issue first, if any: it lists missed sessions, overdue results, a stale
  site and failed runs, each with what can still be done.
- Each refresh after a window closes resolves it (once `bars_cutoff` reaches the window's last
  session, so usually the run after 05:00 UTC). Check every new resolution against
  `scripts/verify_record.py` and the page (LIVE title, coverage count, QLIKE facts).
- **Anchors.** The first anchor ran on 2026-10-31 at 22:43 UTC (#82's merge, Rekor log index
  3615334628); the scheduled runs of 2026-10-31 23:22 and 2026-11-01 05:58 UTC anchored on the
  Public Good instance (log indexes 3615895345 and 3622033493, certificates naming
  `refresh.yml@refs/heads/main` and the `schedule` event). Now and then run
  `git fetch origin live-data && python scripts/verify_anchors.py --latest 5` (a full clone):
  entries sealed after 2026-10-31 should mostly be anchored before their window opens. One sealed
  near the five-minute seal margin, or whose run's anchor failed, can show a late anchor or none
  with nothing wrong; a failed anchor job shows in the Health issue as a recorded incident. If a
  run's log names GitHub's Sigstore instance instead of Public Good, the attestation is not in the
  public log: report that rather than working around it.
- **The shadow field log (ROADMAP 1.8, done).** The first run (2026-11-01, Refresh run
  41299948946) logged 13 forecast lines for 2026-10-30 on `field-data`; Rekor log index 3627645998.
  Now and then: `git fetch origin field-data && git show origin/field-data:vol_field_record.jsonl >
  /tmp/f.jsonl && python backend/field_record.py --validate --log /tmp/f.jsonl` should print OK, and
  each session should gain 13 lines (later runs settle windows; one across a roll gets
  `skipped_roll` at once). A run whose fresh download does not reproduce the published forecast logs
  nothing (the `field_build` log says why); a failed field job is a Health incident, and so is a log
  more than three sessions behind.
- At 10 independent weeks LIVE starts comparing the two models on the same calls; check that
  wording when it first appears (around January 2027).
- Do not touch the promotion test. It decides once, at 52 independent paired weeks (late 2027).

### 2. Dependencies held back by the 14-day policy
- **On 2026-11-10** the dated exception in `.github/audit-exceptions.json` ends and the dev audit
  fails again by itself: pin `minimatch` 11.0.1 (published 2026-10-27, the fix for
  GHSA-g3hv-75wg-m249) with an npm override if eslint passes with it, or take an eslint release that
  depends on it, and remove the exception.
Dependabot opens these PRs once each release is old enough, and from ROADMAP 1.6 also proposes
new digests for the Dockerfile's two base images (same 14-day cooldown; the container job is
their test), on CI's lines only: Python 3.12 and Node 24 (`ignore` in `.github/dependabot.yml`).
Moving to a new line means changing the workflows' `python-version`/`node-version`, the
Dockerfile and that rule together, and rebuilding `data/vol_lab.json` under the new runtime. Merge them when CI, including the
Reproduce rebuild, is green: react and react-dom 19.3.1 (eligible since 2026-10-28; no PR yet),
eslint 10.13.0 (2026-10-30), vite 8.3.5 (2026-11-04), actions/setup-python v7.1.0 (2026-11-05),
pandas 3.1.0 and coverage 7.17.0. Those were tested in scratch environments and pass, including
a bit-for-bit rebuild under pandas 3.1.0. Newer ruff (pinned 0.17.0) and hypothesis (pinned
6.169.0) releases are already old enough; Dependabot's weekly run proposes the newest eligible one,
and its CI run is the test.

### 3. Research depth (each change goes through the ledger and the reproduce test)
- **Stability of the challenger's edge (ROADMAP 2.3, done).** The fluctuation test against the
  challenger is inconclusive after the window-length adjustment (outside the family); the
  cumulative view shows how much of its full-sample advantage came in 2020. Still open: a
  rolling-window refit as robustness for both 2.3 tests, whose theory assumes bounded estimation
  windows (the lab's expand).
- **Regimes (ROADMAP 2.2, done) left two optional pieces.** A CALB dot plot of coverage by regime
  (design note's `CoverageByRegime`; CALB shows facts rows and tables instead) and a second FCST
  chart pane for the OVX/RV5 gap (it needs the forecast chart's percent formatting moved from
  `localization` to the series and a re-measured figure height). Neither changes a number.
- **The published QLIKE DMs use the 9-lag rule (from ROADMAP 2.4).** The distribution scores showed
  that persistent loss differences outrun 9 Bartlett lags, so 2.4 enters a batch-means test in the ledger.
  The published QLIKE DM p-values should gain the batch-means p as context (every published
  "significant" QLIKE verdict holds under it in the design check; this changes no number now).
- **The record's residual window (from ROADMAP 2.4).** Its outcome fell outside the trailing
  780-day residual range on 35 days against 22 expected (95th percentile 31; descriptive): a longer
  or tail-weighted window is a candidate, and the quantile HAR's upper tail (its 90% pinball edge is
  inconclusive) another.
- **Multiplicity as the ledger grows (ROADMAP 2.12, done).** The ledger has an online view (LORD++,
  SAFFRON) whose earlier decisions do not move as rows are added, and a `registered` status.
  **Every new hypothesis is registered before its result exists**, so ROADMAP 2.5 and 2.13 start
  with a registration PR (the working loop, below). Still open: asynchronous online variants
  (LORD\*, SAFFRON\*), which would not make the rows after a pending one wait; BatchBH over the
  legacy entry batches as context, which removes their within-batch order; per-row shares of the
  within-batch draws in which each legacy row is rejected.
- **Estimation error.** DM, MCS and SPA compare forecasts as produced on expanding windows; a
  rolling-window variant would give the Giacomini-White reading (ROADMAP 2.3).
- **Benchmark breadth.** On free daily data only: HAR with a jump/continuous split (range-based
  proxies); Realized GARCH and HEAVY on range variance; a Yang-Zhang or Garman-Klass target as a
  second proxy. Every new model is a new hypothesis: register it in the ledger before its result
  exists (the working loop, below) and re-run MCS and SPA. The live challenger stays pinned.

### 4. Interface follow-ups found but not done (each small; none changes a number)
- Hash landings on `#vrp` and `#ledg` at about 600 px can exceed the 0.02 layout-shift budget (as
  before 2.2.0): reserve the VRP scatter from its width; then add a 600 px hash-landing check to the
  browser tests. LEDG's strip now reserves its height from its own label layout in each band and
  is never drawn shorter (2.12), so it no longer shrinks at 600 px; from 900 to 1,279 px it can still
  grow by one label line (the wide band's reservation assumes a 1,280 px desktop).
- FCST, RISK and MKT placeholders are 16–20 px short at 320 px (layout shift stays at or below 0.011).
- The hero grows about 36 px when research.json lands after the first render; reserve its
  research-dependent lines while pending, as the tiles do.
- The hero sub-line's " · median" dot can end a line at some widths; render it as a `.sep-list`.
- KPI tile lists hide their dots from screen readers, so the clauses run together when read; give
  each a spoken separator.
- LEAK's experiment row labels lose the model name at 320 px (cut the experiment, not the model).
- The hero and the model card accept slightly different arithmetic chains, so near a rounding
  boundary (about 3% of calls) the card shows no worked example while the hero does; share one
  predicate.
- A double click restores a chart's preset but leaves a week pinned.
- The load-shift reservations in `src/styles/verdict-lede.css` and `verdict-tiles.css` are line
  counts, re-measured on the 2.2.0 text and guarded by the tile-height browser check; they can still
  drift by a line when copy changes. Prefer a measured or intrinsic reservation.
- FCST's "largest miss" uses |ln(actual/forecast)|; consider marking the largest-QLIKE week instead
  (the loss the page ranks models by). Undecided since 2.1.0.
- Polish only: at 1280 px and wider, VRP's reading column and LEAK's P&L column leave space beside
  taller neighbours; the price chart's log axis uses library tick positions.
- CALB's PIT and Berkowitz tables (ROADMAP 2.4) scroll sideways in the half-width panel even at
  1440 px (about 900 and 820 px of table in 647 px), as its regime table already did; a per-grid
  column could fold into one "p by grid" cell, and the chance rows could move to the caption.
- The PIT figure's chance band for the trailing-rank models is about 2 px tall on the shared 0–16%
  scale (by design: no zoomed axis); a second, deviation-from-10% view would make it legible.
- Model names in prose break at their hyphens ("HAR-IV-" / "range") in MODL's readings and other
  footnotes; tables use `keepHyphenated`, prose does not.
- MODL's distribution view and CALB's distribution tables (ROADMAP 2.4) reserve a measured height
  per range (LazyMount's wide, mid and narrow values, set from 1,440, 768 and 390 px), not their
  exact one: the rendered text is up to about 340 px off between 480 and 640 px, about 260 px at
  320 px, and CALB's tables about 200 px from 900 to 1,279 px, below the first screen in the
  landings measured. A reservation that follows the text, or an intrinsic one, would remove it.
- The first-load gzip headroom is about 4.2 KB after 2.12 (180,034 B of 184,320; the entry 311,863 B
  of 327,680; the strip's layout moved into the entry so LEDG can reserve its height). The CALB
  method notes (about 3 KB of the entry) could load with the lazy chunk if a later item needs the
  room.
- LEDG shows the online view (ROADMAP 2.12) only in its CSV and method notes. An on-screen online
  column or chip needs the 390 and 360 px card layout and the visual baselines; its value is low while
  no other section cites the online view.

### 5. Engineering
- **The rest of ROADMAP 1.7.** The first part (#91) pinned EIA's contracts 1 and 2 and Cushing
  spot and published `cl_source_agreement` and `roll_convention_audit`; the second (#93)
  added the freeze-time check (`vol_forecast.source_check`), report-only. After its first Refresh,
  read `source_check` in the deployed `data.json`: `spot.status` should be `pass` with `spot_last`
  a few days old. `no_spot_rows`, or a Health incident `source:spot-stale:...`, means the EIA
  download fails in CI (is `EIA_API_KEY` valid?). Still open: the roll calendar has no
  independent check (the roll audit cannot see an error in `contract_calendar.last_trade_date`; the
  acceptance test ROADMAP 1.7 names needs one, for example more known expiry dates from 2000 to
  2010 in `LastTradeDateTest`), and Stooq `CL.F` (the roadmap's second source) refuses connections
  from a cloud session; a person could add it.
- ROADMAP 1.3 also asked to bind the commit SHA; the attestation's subject is the file only (its
  provenance predicate names the workflow run and the `main` commit, not the `live-data` commit).
- **The rest of ROADMAP 1.3.** Show "anchored <time>" with a Rekor link on LIVE rows that have one
  (the site is built before the seal and the anchor, so the build would read the earlier versions'
  anchors from Rekor, failure-tolerant, without the visitor's browser calling Rekor), and add a
  second, independent anchor with OpenTimestamps (its proofs need a store outside the record, such
  as a sibling branch, and a job that can push there without touching `live-data`).
- **Pixel screenshots.** The browser checks assert properties, not pixels (font rasterization
  differs between machines); add screenshot comparison only with a tolerance and pinned fonts.
- The rest of ROADMAP Stage 5 (data contract as code, an offline pipeline rehearsal, the refresh
  time budget, test speed, code-health gates, mutation testing).

### 6. Owner-only (the session cannot do these)
- Repository Settings → Secrets and variables → Actions: keep `EIA_API_KEY`, and delete secrets the
  old pipeline used (`NEWSAPI_KEY`, `FINNHUB_API_KEY` and similar). Nothing reads them now.
- **Protect `live-data` from rewrites.** The branch has no protection, so anyone with write access
  can force-push a rewritten record history, and the hash chain alone cannot show that (it has no
  key). Settings → Rules → Rulesets → New branch ruleset: target `live-data`, turn on "Restrict
  deletions" and "Block force pushes", no bypass list. The pipeline only fast-forwards the branch,
  so nothing else changes.
- Enable the Zenodo-GitHub integration (ROADMAP 1.2) so each release gets a DOI; a session then
  names it in `docs/PREREGISTRATION.md` section 10 and `CITATION.cff`.
- **Push the version tags.** A session's git access can push branches but not tags (the push of
  `refs/tags` returns HTTP 403), so the tags wait for the owner. From a clone of `main`:

  ```bash
  git fetch origin
  git tag -a v2.0.0 48138bc -m "2.0.0"
  git tag -a v2.1.0 d637f1f -m "2.1.0"
  git tag -a v2.2.0 c0166ea -m "2.2.0"
  git push origin v2.0.0 v2.1.0 v2.2.0
  ```

  Pushing `v2.2.0` runs `.github/workflows/release.yml`, which publishes the 2.2.0 GitHub Release
  with its changelog section and `SHA256SUMS`. `v2.0.0` and `v2.1.0` predate the workflow, so they
  get tags but no Release page; create those by hand if wanted. Later releases: run
  `scripts/bump_version.py`, merge, then tag the merge commit the same way.

## The working loop (repeat every session)

1. **Ground truth.** Pull `main` and run every gate in `CLAUDE.md`. Build, serve with the live
   payloads, and render at 1920, 1440, 1024, 768, 390 and 360 px. Record console errors and overflow.
2. **Review.** Use independent lenses:
   - numbers and claims against the payloads;
   - layout at every width;
   - interaction and accessibility (axe, keyboard, palette, deep links, print);
   - data states (resolved, late, skipped, missing research.json, stale snapshot);
   - research methodology.

   A second agent must reproduce each finding before it counts.
3. **Fix.** Make small branches with a test for each fix. Run the full gates and render checks
   before every push.
4. **Ship.** Open a PR, wait for green CI, squash-merge, and confirm the deploy (GitHub's CDN caches
   pages for up to 10 minutes).
5. **Record.** Update this file: what shipped, what the round found, and what is next.

Stop a line of work when two consecutive review rounds confirm no blocking finding in it. Then move
to the next backlog item.

**Adding a hypothesis (ROADMAP 2.12).** Every new ledger row is registered before its result
exists, in two PRs, in this order:

1. **Registration PR**, merged before any PR that produces the result. It adds the row's full
   definition (hypothesis, test, p-value source, direction) where its family's rows are defined, an
   entry appended to `REGISTERED` in `backend/research_ledger.py` dated today (UTC) with the
   artifact field that will hold the result, and the rebuilt ledger, in which the row is
   `registered`. Its place in the online order is its date, then its place in `REGISTERED`: if
   another registration merged first, append this entry after it (the same date is allowed; an
   earlier date is not), so the merged one keeps its place. A model for a generated family (the
   `vol_gw_`/`vol_fluct_` rows, or the distribution rows) is registered by listing it, with the label
   its rows show while they wait, in `REGISTERED_LAB_MODELS` (or `REGISTERED_DISTRIBUTION_MODELS`)
   and each of its rows in `REGISTERED`; `vol_lab.py` does not change in this PR. A row in a new
   family also adds the family's name to the list in `test_rows_cover_every_research_family` in
   `tests/test_research_ledger.py` and to METHODOLOGY 17's Families list, in this PR, before any
   result exists; if it has a row for the challenger (`selected_after_search`), the PR also adds that
   row's kind where METHODOLOGY 17 lists the challenger's rows (the abstract counts it by itself once
   it has run).
2. **Result PR.** It rebuilds the artifact so the field exists, then the ledger: the row resolves by
   itself. A test the artifact ran but could not compute resolves it too, where the row reads the
   field as optional (`Field(optional=True)`, as the `vol_gw_`/`vol_fluct_` rows do): the row then
   takes the status of any row whose test could not be computed, inconclusive, or not supported
   when its stored estimate points the wrong way; it has no p-value and, unless it was selected after
   a search (then it is outside the online stream too), is tested online at p = 1.
   A null in a field the test does not read as optional keeps the row pending. Its definition must
   not change (the spec hash); notes and context may be added. A generated family's model moves
   from `REGISTERED_LAB_MODELS` to `LAB_COMPARED` (or to `DISTRIBUTION_MODELS`) as it joins the
   lab, which leaves the spec hash unchanged; in `DISTRIBUTION_MODELS` it takes its place in the
   artifact's `distribution.models` order (a test checks that the two agree, and the tests of the
   distribution readings read their groups from the artifact and the ledger and also run on a
   synthetic new model with values of its own, so none is edited after the result); a model whose
   PIT reads a simulated sample (the lab writes `alternatives` for it, as for GARCH) also joins
   `PARAMETRIC_DISTRIBUTION_MODELS`, so its calibration row carries the PIT-form context (a test
   checks this list against the artifact too). A lab model
   also changes three lists in the same PR:
   it joins `MODELS` in `backend/vol_lab.py` (a test checks that it and `LAB_COMPARED` agree), gets
   an entry in `MODEL_INPUTS` in `scripts/readme_blocks.py` saying what it reads at the decision
   (returns, the daily range, implied vol; a test checks that every lab model has one, and the
   no-look-ahead test reads it to know which models read the daily range; for a model
   in the field, which a timing control is not, the doc rebuild also fails without it), and, unless
   it is a timing control, joins `FIELD_KEYS` in
   `backend/field_record.py` (a test keeps it equal to `vol_lab.FEASIBLE`), so the field shadow
   log starts logging it.

A pending row blocks the online decisions after it, so resolve it promptly, or withdraw it (only
before its result exists) with a reason, which the changelog names. The rows one registry makes for
a model are withdrawn together: `vol_gw_`/`vol_fluct_` read the leaderboard entry the lab writes for
it, the score and `vol_berkowitz_` rows its distribution entry, and the entry that would hold a
pending row's result would hold its withdrawn sibling's too (the build refuses a partial
withdrawal). That retires the key in that registry: it stays in `REGISTERED_LAB_MODELS` and the lab
never adds the model to its leaderboard, or it stays in `REGISTERED_DISTRIBUTION_MODELS` and the lab
never scores the model's distribution under that key; a later attempt registers a new key. The two
registries' rows read different entries (a distribution row reads the leaderboard only as context,
and only for a model in `LAB_COMPARED`), so a model registered through both can lose its
distribution rows and keep its lab rows, or the other way round. In that second case its
distribution rows resolve when the lab scores it as a distribution only, as it scores the quantile
HAR, and the key moves to `DISTRIBUTION_MODELS` while it stays in `REGISTERED_LAB_MODELS`.
Every changed p-value or status of an existing row (an erratum, a retraction after entry, a test
that becomes computable, a lab rebuild that moves p-values) is named in `ONLINE_CORRECTIONS` in
`tests/test_research_ledger.py` with its changelog errata entry, a row selected after a search too
(it is outside the online stream, but the batch check below compares its fields). A registered
row's own result arriving is not a correction and is never named. In the stream
the correction recomputes the online view from that row on. The history test's two checks (the
append-only online view, and the batch fields while the family is unchanged) exempt the named
row's own p-value and status, and the online levels and decisions recomputed after it; the batch
check also exempts a registered row's own p-value and status when its result arrives.
