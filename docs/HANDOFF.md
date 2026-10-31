# Handoff: state, open backlog and working loop

Written on 2026-10-31, after the round that shipped the pre-registration (#77) and the Health
check (#78), on top of 2.2.0 (PRs #68 to #76). The next session continues from here. Keep this file current: at the end of every round, tick off what
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
  the Combination), but not after the ledger's Benjamini-Hochberg adjustment (q = 0.090). The
  challenger runs live under the pinned 52-week, one-look promotion test. `data/vol_lab.json`
  rebuilds bit for bit (Reproduce workflow: on every PR or push to `main` that touches the pipeline,
  its inputs or its pins, and weekly).
- **Live record.** Daily calls are recorded and sealed in git on the `live-data` branch before their
  window opens; each call since 2026-10-28 also stores its fit, so it recomputes from its own entry.
  The first three windows resolved on 2026-10-29, 10-30 and 10-31: the Oct 20 call (47.3%, band
  23.1–63.6%) against a realized 40.95%, the Oct 21 call (49.6%, band 24.3–66.6%) against 37.76% and
  the Oct 22 call (50.8%, band 24.9–68.2%) against 30.50%, all inside their band. The windows
  overlap, so the page counts one independent week. `scripts/verify_record.py` passes on the record.
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
  workflow's `anchor` job attests the persisted record, logging its SHA-256 in Sigstore's public
  Rekor log; `scripts/verify_anchors.py` reads the times back (ROADMAP 1.3).
- **Tests.** 518 Python (unittest), 264 frontend (node --test) and 13 browser checks (Playwright,
  the "Interface" CI job: axe, layout shift, overflow, keyboard, print, script budget). CI also runs
  CodeQL, ruff, mypy strict, bandit, pip-audit, npm audit, cog drift, figure determinism and the
  Reproduce rebuild. Releases: `scripts/bump_version.py`, and `.github/workflows/release.yml`
  publishes a GitHub Release with `SHA256SUMS` on each `vX.Y.Z` tag from 2.2.0 on.
- **Cloud sessions.** `.claude/hooks/session-start.sh` builds `.venv` (Python 3.12, pinned
  requirements) and installs `node_modules`, so every gate runs from the first command.

## Open backlog, most valuable first

### 1. Watch the live record
- Look at the open `health` issue first, if any: it lists missed sessions, overdue results, a stale
  site and failed runs, each with what can still be done.
- Each refresh after a window closes resolves it (once `bars_cutoff` reaches the window's last
  session, so usually the run after 05:00 UTC). Check every new resolution against
  `scripts/verify_record.py` and the page (LIVE title, coverage count, QLIKE facts).
- **The first anchors.** On the first Refresh runs after the anchor job ships, open the `anchor`
  job's log: it must say "Attestation signed using certificate from Public Good Sigstore instance"
  and print a Rekor search link. If it names GitHub's instance instead, the attestation is not in
  the public log and `scripts/verify_anchors.py` finds nothing; report that rather than working
  around it. Then run `git fetch origin live-data && python scripts/verify_anchors.py --latest 5`:
  every entry recorded after the job shipped should be anchored before its window opened (ROADMAP
  1.3's acceptance: two scheduled publishes).
- At 10 independent weeks LIVE starts comparing the two models on the same calls; check that
  wording when it first appears (around January 2027).
- Do not touch the promotion test. It decides once, at 52 independent paired weeks (late 2027).

### 2. Dependencies held back by the 14-day policy
- **On 2026-11-10** the dated exception in `.github/audit-exceptions.json` ends and the dev audit
  fails again by itself: pin `minimatch` 11.0.1 (published 2026-10-27, the fix for
  GHSA-g3hv-75wg-m249) with an npm override if eslint passes with it, or take an eslint release that
  depends on it, and remove the exception.
Dependabot opens these PRs once each release is old enough. Merge them when CI, including the
Reproduce rebuild, is green: react and react-dom 19.3.1 (eligible since 2026-10-28; no PR yet),
eslint 10.13.0 (2026-10-30), vite 8.3.5 (2026-11-04), actions/setup-python v7.1.0 (2026-11-05),
pandas 3.1.0 and coverage 7.17.0. Those were tested in scratch environments and pass, including
a bit-for-bit rebuild under pandas 3.1.0. Newer ruff (pinned 0.17.0) and hypothesis (pinned
6.169.0) releases are already old enough; Dependabot's weekly run proposes the newest eligible one,
and its CI run is the test.

### 3. Research depth (each change goes through the ledger and the reproduce test)
- **Stability of the challenger's edge.** The SPA rejection holds in each half, but the leader
  differs (Combination in 2008–2016) and finer splits are weaker (by thirds: 0.051, 0.367, 0.010).
  A Giacomini-Rossi fluctuation test or a cumulative loss-difference view (ROADMAP 2.3) would show
  when the edge appears; report it, do not let it move the pinned test.
- **Estimation error.** DM, MCS and SPA compare forecasts as produced on expanding windows; a
  rolling-window variant would give the Giacomini-White reading (ROADMAP 2.3).
- **Benchmark breadth.** On free daily data only: HAR with a jump/continuous split (range-based
  proxies); Realized GARCH and HEAVY on range variance; a Yang-Zhang or Garman-Klass target as a
  second proxy. Every new model is a new hypothesis: add it to the ledger with its multiplicity
  adjustment and re-run MCS and SPA. The live challenger stays pinned.

### 4. Interface follow-ups found but not done (each small; none changes a number)
- Hash landings on `#vrp` and `#ledg` at about 600 px can exceed the 0.02 layout-shift budget (as
  before 2.2.0): reserve the VRP scatter from its width and give the ledger strip breakpoints where
  it really wraps; then add a 600 px hash-landing check to the browser tests.
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

### 5. Engineering
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
