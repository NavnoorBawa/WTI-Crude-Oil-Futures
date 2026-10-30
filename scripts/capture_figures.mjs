// Regenerates docs/figures/dashboard.png, the README's picture of the dashboard's first screen, from a
// build and a set of payloads, so the image always comes from code in this repository.
//
//   npm run build
//   node scripts/capture_figures.mjs --payloads <dir with data.json, research.json and price.json>
//
// The payloads are usually the live site's own (download data.json, research.json and price.json from
// https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/). The page is rendered by Chromium (playwright,
// a dev dependency) at 1440 x 900 in en-US and America/Chicago, with the clock set half an hour after the
// snapshot was frozen, so the picture shows a fresh snapshot whatever day it is taken.
import { readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const DIST = resolve(option("--dist", join(ROOT, "dist")));
const PAYLOADS = resolve(option("--payloads", join(ROOT, "public")));
const OUT = resolve(option("--out", join(ROOT, "docs", "figures", "dashboard.png")));
const VIEWPORT = { width: 1440, height: 900 };
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml" };

const data = JSON.parse(readFileSync(join(PAYLOADS, "data.json"), "utf8"));
const frozenAt = Date.parse(data.frozen_at);
if (!Number.isFinite(frozenAt)) throw new Error(`${PAYLOADS}/data.json has no usable frozen_at`);

const server = createServer((request, response) => {
  const path = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  const fromPayloads = ["/data.json", "/research.json", "/price.json"].includes(path);
  const base = fromPayloads ? PAYLOADS : DIST;
  const file = normalize(join(base, path === "/" ? "index.html" : path));
  if (!file.startsWith(base + sep)) {
    response.writeHead(403).end();
    return;
  }
  try {
    const body = readFileSync(file);
    response.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream" }).end(body);
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const origin = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: VIEWPORT, deviceScaleFactor: 1, locale: "en-US", timezoneId: "America/Chicago", reducedMotion: "reduce",
  });
  const page = await context.newPage();
  await page.clock.setFixedTime(new Date(frozenAt + 30 * 60 * 1000));
  await page.goto(`${origin}/`, { waitUntil: "networkidle" });
  await page.waitForSelector(".kpi-grid");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(1000);
  writeFileSync(OUT, await page.screenshot({ type: "png" }));
  console.log(`wrote ${OUT} (${VIEWPORT.width} x ${VIEWPORT.height}, snapshot ${data.frozen_at})`);
} finally {
  await browser.close();
  server.close();
}
