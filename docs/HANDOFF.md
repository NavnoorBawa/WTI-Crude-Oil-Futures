# Handoff: state, open backlog and working loop

Written on 2026-10-27 at the end of the session that shipped 2.1.0 (PRs #56 and #60 to #67). The
next session continues from here. Keep this file current: at the end of every round, tick off what
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
  persistence. It beats all 6 returns-only models (DM p < 0.05) and is statistically tied with an
  OVX-only regression. The challenger HAR-IV-range has DM p = 0.002 but SPA p = 0.072, so it is
  inconclusive after the search and runs live under a pinned 52-week, one-look promotion test.
  `data/vol_lab.json` rebuilds bit for bit (Reproduce workflow).
- **Live record.** Daily calls are recorded and sealed in git on the `live-data` branch before their
  window opens. The first window resolves after the Oct 28, 2026 settlement. Since 2026-10-27
  `live-data` holds only `price.json`, `runtime-state/` and a README.
- **Dashboard (2.1.0).** The page opens with the question and a computed answer, then this week's
  call as recorded (window, sealed time, change since the previous call, the band's counted record,
  the typical miss and the hand-checkable arithmetic), then evidence tiles that each name their
  sample and an evidence index. Below are 13 sections with method notes, glossary, CSV export, URL
  view state, a jump palette (`/`, Ctrl/Cmd+K), shortcuts (`?`) and print. Three independent review
  rounds; every confirmed finding is fixed.
- **Tests.** 461 Python (unittest) and 237 frontend (node --test). CI also runs CodeQL, ruff, mypy
  strict, bandit, pip-audit, npm audit, cog drift and figure determinism.
- **Cloud sessions.** `.claude/hooks/session-start.sh` builds `.venv` (Python 3.12, pinned
  requirements) and installs `node_modules`, so every gate runs from the first command.

## Open backlog, most valuable first

### 1. Watch the live record resolve (from 2026-10-28)
- After the first resolution (the Oct 20 call, window Oct 22-28), check the live site end to end.
  Resolved rows, realized values, "in band", QLIKE facts, the LIVE title and the hero's change line
  must all render from real data. Fix any wording that only made sense with zero resolved calls.
- Confirm that calls whose window spans a contract roll show as never scored. Each row's
  `spans_roll` flag drives this, so check it against the record.
- Do not touch the promotion test. It decides once, at 52 independent paired weeks (about late 2027).

### 2. Dependencies held back by the 14-day policy
Dependabot opens these PRs once each release is old enough. Merge them when CI, including the
Reproduce rebuild, is green:
- react and react-dom 19.3.1: eligible 2026-10-28.
- eslint 10.13.0: eligible 2026-10-30.
- vite 8.3.5: eligible 2026-11-04.
- actions/setup-python v7.1.0: eligible 2026-11-05.
- pandas 3.1.0, coverage 7.17.0, and newer ruff and hypothesis: eligible 2026-11-05.

All of these were tested in scratch environments and pass, including a bit-for-bit rebuild under
pandas 3.1.0.

### 3. Research depth (each change goes through the ledger and the reproduce test)
- **Call-to-call attribution.** Record entries store inputs but not the fit's coefficients and
  smearing factor, so the page shows input changes without an exact "what moved the forecast"
  split. The change: store `coefficients` and `smearing_factor` in NEW entries only. That means
  additive optional fields in `backend/vol_record.py`, the validator and `docs/DATA_CONTRACT.md`.
  Old entries and sealing stay untouched. Then draw the waterfall in the hero or LIVE.
- **Basel traffic light.** `var_backtest` publishes `basel_zone_last_250` as null. Compute it in
  `backend/vol_lab.py`, rebuild `data/vol_lab.json` through the module, keep the reproduce test
  green, then add the zone column to RISK.
- **Benchmark breadth.** Candidates from the literature, on free daily data only:
  - HAR with jump/continuous split (range-based proxies);
  - Realized GARCH and HEAVY on range variance;
  - a Yang-Zhang or Garman-Klass target as a second proxy.

  Every new model is a new hypothesis: add it to the research ledger with its multiplicity
  adjustment, and re-run MCS and SPA. The live challenger stays pinned; a new challenger would start
  a new, separately declared test.

### 4. Interface follow-ups found but not done
- The FCST chart's top tick label is clipped at about 1280 px. Use the same fix as RISK: raise
  `rightPriceScale.scaleMargins.top` in `src/charts/ForecastChart.jsx`.
- MODL leaderboard:
  - 22 px too wide at 320 px.
  - At 641-1000 px it and METH's SHA-256 column scroll sideways. The result columns are already
    first.
  - The membership matrix scrolls sideways on phones.
- The load-shift reservations in `src/styles/verdict-lede.css` and `verdict-tiles.css` are line
  counts measured on the Oct 27 text, so they can drift by a line when copy changes. Prefer a
  measured or intrinsic reservation.
- Deep links on phones drift while background figures mount (STAB, VRP, FCST and CALB heights
  differ from their placeholders at some widths). Pass measured `{wide, narrow}` heights.
- The phone ledger is about 6,600 px for 20 cards; compact the card layout.
- At 1280 px and wider, the VRP reading column and the LEAK P&L column leave empty space beside
  taller neighbours. Polish only: a reviewer raised it and the verifier judged it taste.
- Check whether METH's Reproduce code block scrolls sideways at 390 px without being keyboard
  reachable (axe `scrollable-region-focusable`). The verifier could not reproduce it.
- Forced-colors mode: SVG chart text and the noise-zone fill are not adjusted.
- Two possible leftovers in CSS: `.stat-cell.is-stacked` may be unused, and `.select` may still
  uppercase its text.
- LIVE: the tile value repeats its footnote's first clause, and the meta and the status line both
  list the counts.
- FCST: "largest miss" uses |ln(actual/forecast)|; consider marking the largest-QLIKE week instead.
- The price chart's log axis uses library tick positions (8%, 11%, 16%...); rounder ticks need
  custom tick marks.

### 5. Engineering
- **Visual regression in CI.** Add Playwright screenshots of the fixture payloads at 1440 and 390,
  compared with a tolerance, plus axe and a Lighthouse budget (CLS, total JS).
- **Capture script.** Commit the README screenshot and share-image capture as a script in
  `scripts/`. They were made with scratch scripts this time.
- **Deferred by design, revisit only with evidence:**
  - build-time prerender with hydration;
  - linked highlighting of one model across sections;
  - per-panel PNG export;
  - a separate summary view;
  - an OVX input slider;
  - a rem-based type scale.

  The reasons are in this file's history: the 2.1.0 plan rejected them as costly for the value.

### 6. Owner-only (the session cannot do these)
- Repository Settings → Secrets and variables → Actions: keep `EIA_API_KEY`, and delete secrets the
  old pipeline used (`NEWSAPI_KEY`, `FINNHUB_API_KEY` and similar). Nothing reads them now.

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
