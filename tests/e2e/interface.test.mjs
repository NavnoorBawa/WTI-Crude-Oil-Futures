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
const openPage = async (viewport, { now = FRESH, path = "/" } = {}) => {
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
    // The background queue starts 3 s after the load event and mounts one figure per idle period.
    await page.waitForFunction((canvas) => [...document.querySelectorAll(".lazy-mount[aria-busy]")]
      .every((node) => canvas.some((id) => node.closest(`#${id}`))), CANVAS_SECTIONS, { timeout: 20000 });
    await page.waitForTimeout(1500);
    for (const id of CANVAS_SECTIONS) assert.equal(await busyIn(page, id), "true", `${id} mounted before the SVG figures were done`);
    await page.locator("#risk .lazy-mount").scrollIntoViewIfNeeded();
    await page.waitForFunction(() => !document.querySelector("#risk .lazy-mount[aria-busy]"));
    assert.equal(await busyIn(page, "mkt"), "true", "only the chart near the viewport mounts at once");
    await page.evaluate(() => window.dispatchEvent(new Event("lazy:mount-all")));     // the Print button's path
    await page.waitForFunction(() => !document.querySelector(".lazy-mount[aria-busy]"));
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
