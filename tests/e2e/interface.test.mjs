// Browser checks of the built dashboard (run `npm run build` first; `npm run test:e2e`). Chromium renders
// dist/ against the fixture payloads at a desktop and a phone width, with every figure mounted, and the
// page must show: no console error, no sideways page scroll, no serious or critical axe violation, a
// layout that does not shift while it loads, a working keyboard path, and a bounded first-load script.
// These are the checks every release was given by hand; property assertions rather than pixel diffs, so
// font rasterization on another machine cannot fail them.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, sep } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { chromium } from "playwright";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const DIST = join(ROOT, "dist");
const FIXTURES = join(ROOT, "tests", "fixtures");
const AXE = readFileSync(join(ROOT, "node_modules", "axe-core", "axe.min.js"), "utf8");
// The payloads the page fetches from its own origin, served from the checked-in fixtures.
const PAYLOADS = { "/data.json": "dashboard_data_v3.json", "/research.json": "research.json", "/price.json": "price.json" };
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".txt": "text/plain" };
const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "phone", width: 390, height: 844 },
];
// Budgets (docs/ROADMAP.md, Stage 3.6): the layout shift of the first screen while the page loads, and
// the scripts the first screen needs (the entry module and what it preloads), raw and gzipped.
const CLS_BUDGET = 0.02;
// The fixture's snapshot time plus half an hour: the page as a visitor sees a fresh snapshot. Without it
// the browser's own clock makes the 2026 fixture stale, which a test below checks on purpose.
const FRESH = Date.parse(JSON.parse(readFileSync(join(FIXTURES, PAYLOADS["/data.json"]), "utf8")).frozen_at) + 30 * 60 * 1000;
const ENTRY_JS_BUDGET = 320 * 1024;
const FIRST_LOAD_GZIP_BUDGET = 180 * 1024;

let server;
let browser;
let origin;

const send = (response, status, type, body) => {
  response.writeHead(status, { "content-type": type, "cache-control": "no-store" });
  response.end(body);
};

before(async () => {
  server = createServer((request, response) => {
    const path = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    if (PAYLOADS[path]) return send(response, 200, TYPES[".json"], readFileSync(join(FIXTURES, PAYLOADS[path])));
    const file = normalize(join(DIST, path === "/" ? "index.html" : path));
    if (!file.startsWith(DIST + sep)) return send(response, 403, TYPES[".txt"], "forbidden");
    let body;
    try {
      body = readFileSync(file);                      // one read: a missing file or a directory throws
    } catch {
      return send(response, 404, TYPES[".txt"], "not found");
    }
    return send(response, 200, TYPES[extname(file)] ?? "application/octet-stream", body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
  await new Promise((resolve) => server?.close(resolve));
});

/** A page at `viewport` with console errors, page errors and failed requests collected, and the first
 * screen's layout shifts summed from navigation on (shifts right after input do not count, as in CLS). */
const openPage = async (viewport, { now = FRESH, path = "/", research = null, before = null } = {}) => {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height }, locale: "en-US", timezoneId: "America/Chicago",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  if (now != null) await page.clock.setFixedTime(new Date(now));     // Date only; timers keep running
  const problems = [];
  page.on("console", (message) => { if (message.type() === "error") problems.push(`console: ${message.text()}`); });
  page.on("pageerror", (error) => problems.push(`page error: ${error.message}`));
  page.on("requestfailed", (request) => problems.push(`request failed: ${request.url()}`));
  await page.addInitScript(() => {
    window.__cls = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (!entry.hadRecentInput) window.__cls += entry.value;
    }).observe({ type: "layout-shift", buffered: true });
  });
  // A payload other than the fixture (e.g. with blocks newer than it), served at the same path.
  if (research != null) await page.route("**/research.json", (route) => route.fulfill({ contentType: TYPES[".json"], body: research }));
  if (before) await before(page);
  await page.goto(`${origin}${path}`, { waitUntil: "networkidle" });
  return { context, page, problems };
};

// The canvas charts (lightweight-charts): they mount near the viewport, to print, or last in the background.
const CANVAS_SECTIONS = ["fcst", "risk", "mkt"];
const busyIn = (page, id) => page.evaluate((section) => document.querySelector(`#${section} .lazy-mount`)?.getAttribute("aria-busy") ?? null, id);

/** Scrolls to the bottom and back so every lazily mounted figure is drawn. */
const mountEverything = async (page) => {
  await page.evaluate(async () => {
    for (let y = 0; y < document.documentElement.scrollHeight; y += window.innerHeight * 0.8) {
      window.scrollTo(0, y);
      await new Promise((resolve) => setTimeout(resolve, 60));
    }
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(500);
};

for (const viewport of VIEWPORTS) {
  test(`${viewport.name} (${viewport.width} px): no errors, no sideways scroll, no serious axe violation, stable first screen`, async () => {
    const { context, page, problems } = await openPage(viewport);
    try {
      await page.waitForSelector("#verdict-title");
      await page.waitForTimeout(1500);                      // research.json lands and the page settles
      const cls = await page.evaluate(() => window.__cls);
      assert.ok(cls < CLS_BUDGET, `first-screen layout shift ${cls.toFixed(4)} is over ${CLS_BUDGET}`);

      await mountEverything(page);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert.ok(overflow <= 0, `the page scrolls sideways by ${overflow}px`);
      // Every sideways-scrolling region can be reached and scrolled from the keyboard.
      const unreachable = await page.evaluate(() => [...document.querySelectorAll("body *")].filter((el) => {
        const style = getComputedStyle(el);
        return /(auto|scroll)/.test(style.overflowX) && el.scrollWidth > el.clientWidth + 1 && el.tabIndex < 0
          && !el.querySelector("a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])");
      }).map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].join(".")}`));
      assert.deepEqual(unreachable, [], "sideways scroll regions without keyboard access");

      await page.evaluate(AXE);
      const violations = await page.evaluate(async () => {
        const result = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
        return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`);
      });
      assert.deepEqual(violations, [], "serious or critical axe violations");
      assert.deepEqual(problems, [], "console errors, page errors or failed requests");
    } finally {
      await context.close();
    }
  });
}

// The regime blocks (ROADMAP 2.2) are newer than the fixture, so the committed artifacts' blocks are spliced in
// (tests/frontend/splice.mjs). MODL's regime view and CALB's regime tables must fit a phone and pass axe, and
// the view control must write the URL.
test("MODL by regime and CALB's regime tables fit 390 and 320 px, pass axe, and the control writes ?modl=", async () => {
  const { withRegimes } = await import("../frontend/splice.mjs");
  const research = JSON.stringify(withRegimes(JSON.parse(readFileSync(join(FIXTURES, PAYLOADS["/research.json"]), "utf8"))));
  for (const width of [390, 320]) {
    for (const [path, view] of [["/?modl=ovx&table=calb#modl", "OVX"], ["/?modl=roll#calb", "Roll"]]) {
      const { context, page, problems } = await openPage({ width, height: 844 }, { path, research });
      try {
        await page.waitForSelector("#modl .segmented");
        await mountEverything(page);
        const state = await page.evaluate(() => ({
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          pressed: [...document.querySelectorAll("#modl .segmented-button[aria-pressed='true']")].map((button) => button.textContent),
          caption: document.querySelector("#modl caption")?.textContent ?? "",
          calbNote: [...document.querySelectorAll("#calb .prose-note")].some((note) => /Across OVX thirds|dependence detected/.test(note.textContent)),
          calbTables: [...document.querySelectorAll("#calb caption")].map((caption) => caption.textContent),
          h2: document.querySelector("#modl h2")?.textContent ?? "",
          matrix: Boolean(document.querySelector("#modl .mcs-matrix")),
          footnote: /it is not new evidence/.test(document.querySelector("#modl .leaderboard--regime")?.textContent ?? ""),
          // The split's group head starts where its first regime column starts (the spans count every visible column).
          groupOffset: (() => {
            const group = document.querySelector("#modl .group-row th[scope='colgroup']");
            const first = [...document.querySelectorAll("#modl thead tr:not(.group-row) th")].find((th) => /, [\d,]+\s?d/.test(th.textContent));
            return group && first ? Math.round(group.getBoundingClientRect().left - first.getBoundingClientRect().left) : null;
          })(),
        }));
        assert.ok(state.overflow <= 0, `${width}px ${path}: the page scrolls sideways by ${state.overflow}px`);
        assert.deepEqual(state.pressed, [view], `${width}px ${path}: the URL's view is the pressed one`);
        assert.match(state.caption, /^QLIKE by regime at the decision: /);
        assert.ok(state.footnote, "the regime view says it is descriptive and not new evidence");
        assert.equal(state.matrix, false, "the MCS matrix belongs to the all-days view");
        assert.equal(state.groupOffset, 0, `${width}px ${path}: the split's head starts at its first regime column`);
        if (path.includes("table=calb")) {
          assert.ok(state.calbTables.some((caption) => /coverage by regime at the decision/.test(caption)), "CALB's per-regime table");
          assert.ok(state.calbTables.some((caption) => /Tests of equal coverage across regimes/.test(caption)), "CALB's tests table");
        } else {
          assert.ok(state.calbNote, "CALB states the regime result");
        }
        await page.evaluate(AXE);
        const violations = await page.evaluate(async () => {
          const result = await axe.run({ include: [["#modl"], ["#calb"]] }, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
          return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
            .map((v) => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`);
        });
        assert.deepEqual(violations, [], `${width}px ${path}: serious or critical axe violations`);
        // A reader's sort belongs to its split: sort a regime column, switch, and the new split starts on all days.
        await page.locator("#modl thead th").filter({ hasText: /, [\d,]+\s?d/ }).last().locator("button").click();
        await page.getByRole("button", { name: "RV5", exact: true }).click();
        assert.match(page.url(), /[?&]modl=rv5\b/);
        assert.match(await page.locator("#modl caption").first().textContent(), /RV5 to the decision/);
        assert.equal(await page.locator("#modl thead th[aria-sort='ascending']").textContent().then((text) => text.replace(/[↕↑↓]/g, "").trim()),
          "All days", "each split starts from its own initial sort");
        // "All" returns to the bare URL, the leaderboard and its matrix, under the same title.
        await page.getByRole("button", { name: "All", exact: true }).click();
        assert.doesNotMatch(page.url(), /modl=/);
        assert.ok(await page.locator("#modl-har_iv").count(), "the leaderboard is back");
        assert.equal(await page.locator("#modl h2").textContent(), state.h2, "the title does not change with the view");
        assert.ok(await page.locator("#modl .mcs-matrix").count(), "the matrix is back");
        assert.deepEqual(problems, [], "console errors, page errors or failed requests");
      } finally {
        await context.close();
      }
    }
  }
});

// STAB's rolling test with its band (ROADMAP 2.3) is newer than the fixture: the committed blocks are spliced in.
test("STAB's rolling test and cumulative view fit 390 and 320 px, pass axe, and the control writes ?stab=", async () => {
  const { withStability } = await import("../frontend/splice.mjs");
  const research = JSON.stringify(withStability(JSON.parse(readFileSync(join(FIXTURES, PAYLOADS["/research.json"]), "utf8"))));
  for (const width of [390, 320]) {
    const { context, page, problems } = await openPage({ width, height: 844 }, { path: "/#stab", research });
    try {
      await page.waitForSelector("#stab .fluct-readings");
      await mountEverything(page);
      const state = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        rows: document.querySelectorAll("#stab .fluct-row").length,
        readings: document.querySelectorAll("#stab .fluct-readings li").length,
        pressed: [...document.querySelectorAll("#stab .segmented-button[aria-pressed='true']")].map((b) => b.textContent),
      }));
      assert.ok(state.overflow <= 0, `${width}px: the page scrolls sideways by ${state.overflow}px`);
      assert.ok(state.rows >= 1 && state.rows === state.readings, `${width}px: a reading per drawn row (${state.rows}, ${state.readings})`);
      assert.deepEqual(state.pressed, ["Rolling test"]);
      await page.evaluate(AXE);
      const violations = await page.evaluate(async () => {
        const result = await axe.run({ include: [["#stab"]] }, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
        return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`);
      });
      assert.deepEqual(violations, [], `${width}px: serious or critical axe violations`);
      await page.getByRole("button", { name: "Cumulative", exact: true }).click();
      assert.match(page.url(), /[?&]stab=cumulative\b/);
      await page.waitForSelector("#stab .cum-row");
      const after = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert.ok(after <= 0, `${width}px cumulative: sideways scroll ${after}px`);
      await page.getByRole("button", { name: "Rolling test", exact: true }).click();
      assert.doesNotMatch(page.url(), /stab=/);
      assert.deepEqual(problems, [], "console errors, page errors or failed requests");
    } finally {
      await context.close();
    }
  }
});

// The distribution block (ROADMAP 2.4) is newer than the fixture: the committed block is spliced in. MODL's
// distribution view and CALB's PIT figure and tables must fit a phone and pass axe; the PIT figure must fill exactly
// the height its placeholder reserved; the view shows no QLIKE and no MCS matrix; the control writes ?modl=dist.
test("MODL's distribution view and CALB's PIT figure and tables fit 390 and 320 px, pass axe, and keep their reservation", async () => {
  const { withDistribution } = await import("../frontend/splice.mjs");
  const fixture = JSON.parse(readFileSync(join(FIXTURES, PAYLOADS["/research.json"]), "utf8"));
  const research = JSON.stringify(withDistribution(fixture));
  const qlikes = fixture.vol_lab.leaderboard.models.map((model) => model.qlike.toFixed(4));
  for (const width of [390, 320]) {
    for (const path of ["/?modl=dist#modl", "/?table=calb#calb"]) {
      const { context, page, problems } = await openPage({ width, height: 844 }, { path, research });
      try {
        await page.waitForSelector("#modl .segmented");
        await mountEverything(page);
        await page.evaluate(() => window.dispatchEvent(new Event("lazy:mount-all")));
        if (path.includes("modl=dist")) await page.waitForSelector("#modl .leaderboard--dist table");
        else await page.waitForSelector("#calb .panel-table table caption >> text=/Berkowitz's test/");
        const state = await page.evaluate((values) => {
          const dist = document.querySelector("#modl .leaderboard--dist");
          const pit = document.querySelector("#calb .pit-chart");
          const mount = pit?.closest(".lazy-mount");
          return {
            overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            pressed: [...document.querySelectorAll("#modl .segmented-button[aria-pressed='true']")].map((b) => b.textContent),
            matrix: Boolean(document.querySelector("#modl .mcs-matrix")),
            leaderboard: Boolean(document.querySelector("#modl-har_iv")),
            distRows: dist ? dist.querySelectorAll("tbody tr").length : null,
            qlikeShown: dist ? values.filter((value) => dist.textContent.includes(value)) : [],
            readings: dist ? /The rerun selects nothing/.test(dist.textContent) : null,
            reserved: mount ? parseFloat(getComputedStyle(mount).getPropertyValue(window.innerWidth <= 640 ? "--lazy-h-narrow" : "--lazy-h")) : null,
            drawn: pit ? pit.getBoundingClientRect().height : null,
            mounted: mount ? mount.getBoundingClientRect().height : null,
            calbTables: [...document.querySelectorAll("#calb caption")].map((caption) => caption.textContent),
          };
        }, qlikes);
        assert.ok(state.overflow <= 0, `${width}px ${path}: the page scrolls sideways by ${state.overflow}px`);
        if (path.includes("modl=dist")) {
          assert.deepEqual(state.pressed, ["CRPS"], `${width}px: the URL's view is the pressed one`);
          assert.equal(state.matrix, false, "the MCS matrix belongs to the all-days view");
          assert.equal(state.leaderboard, false, "the QLIKE table gives way to the distribution's");
          assert.deepEqual(state.qlikeShown, [], "no QLIKE number in the distribution view");
          assert.ok(state.distRows >= 1 && state.readings, `${width}px: the table and its readings (${state.distRows})`);
          // The PIT figure (in CALB's figure view here) fills exactly the height its placeholder reserved.
          assert.ok(state.reserved > 0 && state.drawn === state.reserved && state.mounted === state.reserved,
            `${width}px: reserved ${state.reserved}px, drawn ${state.drawn}px, mount ${state.mounted}px`);
        } else {
          assert.ok(state.calbTables.some((caption) => /^Share of days between consecutive predicted deciles/.test(caption)), "CALB's PIT table");
          assert.ok(state.calbTables.some((caption) => /^Berkowitz's test on each sampling grid/.test(caption)), "CALB's Berkowitz table");
        }
        await page.evaluate(AXE);
        const violations = await page.evaluate(async () => {
          const result = await axe.run({ include: [["#modl"], ["#calb"]] }, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
          return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
            .map((v) => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`);
        });
        assert.deepEqual(violations, [], `${width}px ${path}: serious or critical axe violations`);
        if (path.includes("modl=dist")) {
          // "All" returns to the bare URL, the leaderboard and its matrix; "CRPS" writes the view again.
          const title = await page.locator("#modl h2").textContent();
          await page.getByRole("button", { name: "All", exact: true }).click();
          assert.doesNotMatch(page.url(), /modl=/);
          assert.ok(await page.locator("#modl-har_iv").count(), "the leaderboard is back");
          assert.ok(await page.locator("#modl .mcs-matrix").count(), "the matrix is back");
          assert.equal(await page.locator("#modl h2").textContent(), title, "the title does not change with the view");
          await page.getByRole("button", { name: "CRPS", exact: true }).click();
          assert.match(page.url(), /[?&]modl=dist\b/);
          await page.waitForSelector("#modl .leaderboard--dist table");
        }
        assert.deepEqual(problems, [], "console errors, page errors or failed requests");
      } finally {
        await context.close();
      }
    }
  }
});

// The price axis settles on its width after the first frames; a chart nobody has touched must still
// sit on its range preset, so no "Reset view" is offered.
for (const viewport of VIEWPORTS) {
  test(`${viewport.name} (${viewport.width} px): an untouched forecast chart offers no "Reset view"`, async () => {
    const { context, page, problems } = await openPage(viewport, { path: "/#fcst" });
    try {
      await page.waitForSelector("#fcst .lw-host canvas");
      await page.waitForTimeout(3000);
      assert.equal(await page.locator("#fcst .reset-view").count(), 0, "Reset view shown with no input");
      assert.deepEqual(problems, []);
    } finally {
      await context.close();
    }
  });
}

test("separator lists: no text is clipped at a wrap, and no wrapped line starts or ends with a dot", async () => {
  for (const width of [320, 480, 768]) {
    const { context, page, problems } = await openPage({ name: `w${width}`, width, height: 900 });
    try {
      await page.waitForSelector("#verdict-title");
      await mountEverything(page);
      const report = await page.evaluate(() => {
        const clipped = [];
        const dangling = [];
        for (const list of document.querySelectorAll(".sep-list")) {
          const left = list.getBoundingClientRect().left;
          const walker = document.createTreeWalker(list, NodeFilter.SHOW_TEXT);
          for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            if (!node.textContent.trim() || node.parentElement.closest(".sep, .sr-only")) continue;
            const range = document.createRange();
            range.selectNodeContents(node);
            for (const rect of range.getClientRects()) {
              if (rect.width && rect.left < left - 0.5) clipped.push(`${list.className}: "${node.textContent.trim().slice(0, 30)}" at ${Math.round(rect.left - left)}px`);
            }
          }
          // A visible dot sits inside the list's box; one at a line start would be outside it (clipped).
          const items = [...list.children];
          items.forEach((item, index) => {
            const next = items[index + 1];
            if (!next) return;
            const a = item.getBoundingClientRect();
            const b = next.getBoundingClientRect();
            // The next item starts a new line: its dot must be left of the list (clipped), never visible.
            if (b.top > a.top + 2 && b.left - 0.5 > left - 1 && getComputedStyle(next, "::before").content !== "none") {
              const dot = next.getBoundingClientRect().left;
              if (dot >= left) dangling.push(`${list.className}: line starts with a dot before "${next.textContent.trim().slice(0, 24)}"`);
            }
          });
        }
        return { clipped, dangling };
      });
      assert.deepEqual(report.clipped, [], `text clipped at ${width}px`);
      assert.deepEqual(report.dangling, [], `dangling dots at ${width}px`);
      assert.deepEqual(problems, []);
    } finally {
      await context.close();
    }
  }
});

test("source footers keep their label on the first line; tiles keep their height when research.json is late", async () => {
  for (const width of [1440, 1024, 768, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, locale: "en-US", timezoneId: "America/Chicago" });
    const page = await context.newPage();
    await page.clock.setFixedTime(new Date(FRESH));
    // Hold research.json until the pending tiles have rendered, then let it through.
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    await page.route("**/research.json", async (route) => { await held; await route.continue(); });
    await page.goto(`${origin}/`);
    await page.waitForSelector(".kpi-grid.is-pending");
    const pending = await page.evaluate(() => [...document.querySelectorAll(".kpi-grid > .kpi")].map((kpi) => kpi.getBoundingClientRect().height));
    release();
    await page.waitForSelector(".kpi-grid:not(.is-pending)");
    await page.waitForTimeout(300);
    const ready = await page.evaluate(() => [...document.querySelectorAll(".kpi-grid > .kpi")].map((kpi) => kpi.getBoundingClientRect().height));
    // Each row of tiles takes its tallest tile; a row may not grow when the lab text arrives.
    const rows = (heights) => [0, 3].map((start) => Math.max(...heights.slice(start, start + 3)));
    if (width > 640) {
      rows(ready).forEach((height, row) => assert.ok(height <= rows(pending)[row] + 1,
        `${width}px: tile row ${row + 1} grew from ${rows(pending)[row]} to ${height}px when research.json arrived`));
    }
    await page.evaluate(() => window.dispatchEvent(new Event("lazy:mount-all")));
    const misplaced = await page.evaluate(() => [...document.querySelectorAll(".panel-source > .source-files")].filter((group) => {
      const label = group.querySelector(".micro")?.getBoundingClientRect();
      const first = group.querySelector(".sep-list > *")?.getBoundingClientRect();
      return label && first && Math.abs(first.top - label.top) > 4;
    }).map((group) => group.closest("section")?.id));
    assert.deepEqual(misplaced, [], `${width}px: a source list dropped below its label`);
    await context.close();
  }
});

test("a stale snapshot's notice does not shift the page when the data arrives", async () => {
  const { context, page, problems } = await openPage(VIEWPORTS[1], { now: null });
  try {
    await page.waitForSelector("#verdict-title");
    assert.match(await page.locator("#main > .notice.is-warn").innerText(), /This snapshot is \d+ (days|hours) old/);
    await page.waitForTimeout(1500);
    const cls = await page.evaluate(() => window.__cls);
    assert.ok(cls < CLS_BUDGET, `layout shift ${cls.toFixed(4)} with the stale notice is over ${CLS_BUDGET}`);
    assert.deepEqual(problems, []);
  } finally {
    await context.close();
  }
});

test("keyboard: the skip link comes first, and the jump palette opens, filters, navigates and returns focus", async () => {
  const { context, page, problems } = await openPage(VIEWPORTS[0]);
  try {
    await page.waitForSelector("#verdict-title");
    await page.keyboard.press("Tab");
    assert.equal(await page.evaluate(() => document.activeElement?.className), "skip-link");
    await page.evaluate(() => document.activeElement.blur());
    await page.keyboard.press("/");
    const dialog = page.locator("dialog[open]");
    await dialog.waitFor();
    await page.keyboard.type("ledger");
    await page.keyboard.press("Enter");
    await dialog.waitFor({ state: "detached" }).catch(() => dialog.waitFor({ state: "hidden" }));
    await page.waitForFunction(() => {
      const target = document.getElementById("ledg");
      return target && Math.abs(target.getBoundingClientRect().top) < window.innerHeight / 2;
    });
    await page.keyboard.press("Control+k");
    await dialog.waitFor();
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "hidden" });
    assert.notEqual(await page.evaluate(() => document.activeElement?.tagName), "DIALOG");
    assert.deepEqual(problems, []);
  } finally {
    await context.close();
  }
});

test("phone: SVG figures mount in the background first; canvas charts wait for a scroll, print or the end of the queue", async () => {
  const { context, page, problems } = await openPage(VIEWPORTS[1]);
  try {
    await page.waitForSelector("#verdict-title");
    // The background queue starts 3 s after the load event and mounts one figure per idle period; the
    // canvas charts join it only once it is empty. Each wrapper's mount time is recorded as it loses
    // aria-busy, and the page is read in the same callback in which #risk mounts, so the queue cannot
    // race the check.
    await page.evaluate((canvas) => {
      window.__mounted = new Map();
      window.__atRisk = null;
      const isCanvas = (node) => canvas.some((id) => node.closest(`#${id}`));
      new MutationObserver((records) => {
        for (const record of records) {
          const node = record.target;
          if (!node.classList?.contains("lazy-mount") || node.hasAttribute("aria-busy") || window.__mounted.has(node)) continue;
          window.__mounted.set(node, performance.now());
          if (!window.__atRisk && node.closest("#risk")) {
            window.__atRisk = {
              mkt: document.querySelector("#mkt .lazy-mount")?.getAttribute("aria-busy") ?? null,
              figuresQueued: [...document.querySelectorAll(".lazy-mount[aria-busy]")].some((other) => !isCanvas(other)),
            };
          }
        }
      }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["aria-busy"] });
    }, CANVAS_SECTIONS);
    // Scrolled to while SVG figures are still queued, a canvas chart mounts at once; the others wait.
    await page.locator("#risk .lazy-mount").scrollIntoViewIfNeeded();
    await page.waitForFunction(() => window.__atRisk);
    const atRisk = await page.evaluate(() => window.__atRisk);
    assert.ok(atRisk.figuresQueued, "the SVG queue was done before #risk mounted, so this run cannot tell a scroll mount from a background one");
    assert.equal(atRisk.mkt, "true", "only the chart near the viewport mounts at once");
    // Left alone, the rest mount in the background: every SVG figure before any canvas chart.
    await page.waitForFunction(() => !document.querySelector(".lazy-mount[aria-busy]"), null, { timeout: 30000 });
    const mounts = await page.evaluate((canvas) => [...window.__mounted.entries()]
      .filter(([node]) => !node.closest("#risk"))
      .map(([node, time]) => ({ canvas: canvas.some((id) => node.closest(`#${id}`)), time })), CANVAS_SECTIONS);
    const canvasTimes = mounts.filter((mount) => mount.canvas).map((mount) => mount.time);
    const lastFigure = Math.max(...mounts.filter((mount) => !mount.canvas).map((mount) => mount.time));
    assert.equal(canvasTimes.length, CANVAS_SECTIONS.length - 1, "a canvas chart did not mount in the background");
    assert.ok(Math.min(...canvasTimes) > lastFigure,
      `a canvas chart mounted at ${Math.round(Math.min(...canvasTimes))} ms, before the last SVG figure (${Math.round(lastFigure)} ms)`);
    assert.deepEqual(problems, []);
  } finally {
    await context.close();
  }
});

test("phone: with no scroll, the canvas charts still mount last in the background, so Ctrl+P prints them", async () => {
  const { context, page, problems } = await openPage(VIEWPORTS[1]);
  try {
    await page.waitForSelector("#verdict-title");
    await page.waitForFunction(() => !document.querySelector(".lazy-mount[aria-busy]"), null, { timeout: 30000 });
    await page.waitForTimeout(500);
    // What a print snapshot captures: the canvases as they are painted when "beforeprint" returns.
    const painted = await page.evaluate((ids) => {
      window.dispatchEvent(new Event("beforeprint"));
      const inked = (canvas) => {
        const data = canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height).data ?? [];
        for (let i = 3; i < data.length; i += 4) if (data[i] !== 0) return true;
        return false;
      };
      return ids.map((id) => [...document.querySelectorAll(`#${id} canvas`)].some(inked));
    }, CANVAS_SECTIONS);
    assert.deepEqual(painted, CANVAS_SECTIONS.map(() => true), "a canvas chart is blank when the print snapshot is taken");
    assert.deepEqual(problems, []);
  } finally {
    await context.close();
  }
});

test("phone: a data.json refresh that returns the same snapshot leaves a panned chart where the reader put it", async () => {
  const { context, page, problems } = await openPage(VIEWPORTS[1]);
  let refetches = 0;
  page.on("request", (request) => { if (new URL(request.url()).pathname === "/data.json") refetches += 1; });
  try {
    await page.waitForSelector("#verdict-title");
    await page.locator("#fcst .lazy-mount").scrollIntoViewIfNeeded();
    await page.waitForFunction(() => !document.querySelector("#fcst .lazy-mount[aria-busy]"));
    await page.waitForTimeout(500);
    if (await page.locator("#fcst .reset-view").count()) await page.locator("#fcst .reset-view").click();
    await page.waitForTimeout(300);
    const box = await page.locator("#fcst .lw-host").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 150, box.y + box.height / 2, { steps: 8 });
    await page.mouse.up();
    await page.mouse.move(2, 2);
    await page.locator("#fcst .reset-view").waitFor();                      // the reader has moved off the preset
    refetches = 0;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));   // the 15-minute poll's path
    await page.waitForTimeout(1500);
    assert.equal(refetches, 1, "data.json was fetched again");
    assert.equal(await page.locator("#fcst .reset-view").count(), 1, "the pan survived: the identical snapshot was not set again");
    assert.deepEqual(problems, []);
  } finally {
    await context.close();
  }
});

test("the jump palette loads on demand, and a failed load closes it with a notice and retries on the next open", async () => {
  // The palette's chunk fails to load until the first open has failed: the idle preload's request and the open's own
  // (or, in a browser that caches a failed module fetch, the cached failure). The next open loads it.
  let blocked = true;
  let aborted = 0;
  const before = (page) => page.route(/CommandPalette-[^/]*\.js/, (route) => {
    if (blocked) {
      aborted += 1;
      return route.abort();
    }
    return route.continue();
  });
  const { context, page, problems } = await openPage(VIEWPORTS[0], { before });
  try {
    await page.waitForSelector("#verdict-title");
    // The first screen is index.html's entry and module preloads; the palette's chunk is not among them.
    const html = await (await page.request.get(`${origin}/`)).text();
    assert.doesNotMatch(html, /CommandPalette-/, "the palette's chunk is not part of the first screen");
    await page.waitForTimeout(1500);                                      // the idle preload has run
    await page.keyboard.press("/");
    await page.waitForFunction(() => /could not load/.test(document.querySelector(".nav-toast")?.textContent ?? ""));
    assert.equal(await page.locator("dialog[open]").count(), 0, "no half-open palette");
    assert.ok(await page.locator("#verdict-title").count(), "the page is still there");
    assert.ok(aborted >= 1, "the chunk's request was blocked");
    blocked = false;
    await page.keyboard.press("/");
    await page.waitForSelector("dialog[open]");
    await page.keyboard.press("Escape");
    // The blocked requests (each failed resource, and the import it failed) are the only failures expected here.
    const expected = (problem) => /CommandPalette|Failed to fetch dynamically imported module|\[palette\]/.test(problem)
      || problem === "console: Failed to load resource: net::ERR_FAILED";
    assert.ok(problems.filter((problem) => problem.includes("net::ERR_FAILED")).length <= aborted, problems.join("\n"));
    assert.deepEqual(problems.filter((problem) => !expected(problem)), []);
  } finally {
    await context.close();
  }
});

test("a ?table= link loads without an announcement; a reader's sort is announced", async () => {
  const { context, page, problems } = await openPage(VIEWPORTS[0], { path: "/?table=fcst" });
  const region = page.locator("#fcst .table-wrap p[aria-live]");
  try {
    await page.waitForSelector("#fcst table");
    await page.waitForTimeout(1500);                                      // research.json lands, the rows change
    assert.equal(await region.textContent(), "", "nothing is spoken on load");
    await page.evaluate(() => window.dispatchEvent(new Event("online")));   // a refresh, no reader input
    await page.waitForTimeout(1000);
    assert.equal(await region.textContent(), "");
    await page.locator("#fcst th button.sort-button", { hasText: "QLIKE" }).click();
    await page.waitForFunction(() => document.querySelector("#fcst .table-wrap p[aria-live]")?.textContent !== "");
    assert.match(await region.textContent(), /^Showing 52 of [\d,]+ weeks, sorted by QLIKE descending$/);
    assert.deepEqual(problems, []);
  } finally {
    await context.close();
  }
});

test("the first screen's scripts stay within budget", () => {
  const html = readFileSync(join(DIST, "index.html"), "utf8");
  const entry = [...html.matchAll(/<script[^>]+type="module"[^>]+src="([^"]+)"/g)].map((m) => m[1]);
  const preloads = [...html.matchAll(/<link[^>]+rel="modulepreload"[^>]+href="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(entry.length, 1, "one entry module");
  const file = (url) => readFileSync(join(DIST, new URL(url, "http://x/").pathname));
  const entryBytes = file(entry[0]).length;
  const gzip = [...entry, ...preloads].reduce((sum, url) => sum + gzipSync(file(url)).length, 0);
  assert.ok(entryBytes <= ENTRY_JS_BUDGET, `entry module ${(entryBytes / 1024).toFixed(0)} kB over ${ENTRY_JS_BUDGET / 1024} kB`);
  assert.ok(gzip <= FIRST_LOAD_GZIP_BUDGET, `first-load scripts ${(gzip / 1024).toFixed(0)} kB gzipped over ${FIRST_LOAD_GZIP_BUDGET / 1024} kB`);
});
