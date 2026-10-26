// The reading surface: notation keeps its case, the loading screen cannot shift the footer, print
// gets its own stylesheet, lazy figures are not live regions, and printPage() waits for the page
// before it prints. The stylesheets are parsed (index.css plus every package sheet in src/styles,
// in the order main.jsx imports them), so the checks hold whichever file a rule lives in.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, readSource } from "./load.mjs";

let m;
before(async () => { m = await loadDashboard(); });
after(() => m.close());

// ------------------------------------------------------------------------------- a small CSS parser
const splitTopLevel = (text, separator) => {
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === "(" || text[i] === "[") depth += 1;
    else if (text[i] === ")" || text[i] === "]") depth -= 1;
    else if (text[i] === separator && depth === 0) {
      parts.push(text.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(text.slice(start));
  return parts.map((part) => part.trim()).filter(Boolean);
};

/** Every style rule as {selectors, decls, context}, where context lists the enclosing at-rules. */
const parseRules = (css, context = []) => {
  const rules = [];
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf("{", i);
    if (open < 0) break;
    let depth = 1;
    let j = open + 1;
    while (depth > 0 && j < text.length) {
      if (text[j] === "{") depth += 1;
      else if (text[j] === "}") depth -= 1;
      j += 1;
    }
    const before = text.slice(i, open);
    const prelude = before.slice(before.lastIndexOf(";") + 1).trim();
    const body = text.slice(open + 1, j - 1);
    if (/^@(media|container|supports|layer)\b/.test(prelude)) rules.push(...parseRules(body, [...context, prelude]));
    else if (!prelude.startsWith("@")) {
      const decls = {};
      for (const declaration of splitTopLevel(body, ";")) {
        const colon = declaration.indexOf(":");
        if (colon > 0) decls[declaration.slice(0, colon).trim()] = declaration.slice(colon + 1).trim();
      }
      rules.push({ selectors: splitTopLevel(prelude, ","), decls, context });
    }
    i = j;
  }
  return rules;
};

const sheetPaths = () => {
  const main = readSource("src/main.jsx");
  return [...main.matchAll(/^import "\.\/(.+\.css)";$/gm)].map((match) => `src/${match[1]}`);
};
const rules = () => parseRules(sheetPaths().map(readSource).join("\n"));
// The last compound selector (the element the rule styles), e.g. "th" in ".data-table thead th".
const subject = (selector) => selector.split(/\s*[\s>+~]\s*/).pop();

test("the stylesheets main.jsx imports include the base sheet and this package's sheet", () => {
  const paths = sheetPaths();
  assert.ok(paths.includes("src/index.css") && paths.includes("src/styles/reading-surface.css"), paths.join(", "));
  assert.ok(paths.indexOf("src/index.css") < paths.indexOf("src/styles/reading-surface.css"), "package sheets load after index.css");
});

test("labels that can hold notation are never set in forced capitals (VaR, p, t, n keep their case)", () => {
  const targets = {
    "table headers": (selector) => /\bthead\s+th\b/.test(selector) && /^th\b/.test(subject(selector)),
    "table captions": (selector) => /\.table-caption(?![\w-])/.test(subject(selector)),
    "figure titles": (selector) => /\.figure-title(?![\w-])/.test(subject(selector)),
    "fact labels": (selector) => /\.fact(?![\w-])/.test(selector) && /^dt\b/.test(subject(selector)),
  };
  const all = rules();
  for (const [name, matches] of Object.entries(targets)) {
    const styling = all.filter((rule) => rule.selectors.some(matches));
    assert.ok(styling.length > 0, `${name} are styled somewhere`);
    for (const rule of styling) {
      assert.notEqual(rule.decls["text-transform"], "uppercase", `${name}: ${rule.selectors.join(", ")}`);
    }
  }
  // The section name sits in a .micro label, whose capitals the eyebrow turns off. Several sheets
  // style that label, so the check reads the last rule that sets its case (the one that wins).
  const eyebrow = all.filter((rule) => rule.selectors.includes(".panel-eyebrow .micro") && rule.decls["text-transform"]).at(-1);
  assert.equal(eyebrow?.decls["text-transform"], "none");
  // A sort button inherits its header's case rather than setting its own.
  const sortButton = all.find((rule) => rule.selectors.includes(".sort-button"));
  assert.equal(sortButton?.decls["text-transform"], "inherit");
});

test("the loading screen fills the viewport below the bar, so the footer cannot shift into view", () => {
  const screens = rules().filter((rule) => rule.selectors.includes(".status-screen") && rule.decls["min-height"]);
  assert.ok(screens.some((rule) => rule.context.length === 0), "a base rule sets the height");
  for (const rule of screens) {
    assert.match(rule.decls["min-height"], /^calc\(100svh - var\(--topbar-h\) - \d+px\)$/, rule.context.join(" ") || "base");
  }
  // At 960px and below the bar has two rows (48px + a 40px code strip + its 1px rule).
  const twoRow = screens.find((rule) => rule.context.some((at) => /max-width:\s*960px/.test(at)));
  assert.match(twoRow?.decls["min-height"] ?? "", /- 41px\)$/);
});

test("print gets a light page without screen chrome and with full-height tables", () => {
  const print = rules().filter((rule) => rule.context.some((at) => /^@media print\b/.test(at)));
  const hidden = print.filter((rule) => /^none\b/.test(rule.decls.display ?? "")).flatMap((rule) => rule.selectors);
  for (const selector of [".codenav", ".panel-actions", ".topbar-right", ".skip-link", ".table-more", ".sort-glyph"]) {
    assert.ok(hidden.includes(selector), `${selector} is hidden in print`);
  }
  const root = print.find((rule) => rule.selectors.includes(":root"));
  for (const token of ["--bg-0", "--bg-1", "--text-1", "--rule"]) assert.match(root?.decls[token] ?? "", /^#[0-9a-f]{3,6}$/i, token);
  const scroll = print.find((rule) => rule.selectors.includes(".table-scroll.is-tall"));
  assert.equal(scroll?.decls["max-height"], "none");
  assert.equal(scroll?.decls.overflow, "visible");
  // The print overrides live in a stylesheet, not tokens.css, whose colours the chart mirror test reads.
  assert.ok(!/@media print/.test(readSource("src/theme/tokens.css")));
});

test("type: reading text is 13px at a measure of 64ch, meta lines are sans, code never drops below 11px", () => {
  const tokens = readSource("src/theme/tokens.css");
  assert.match(tokens, /--fs-body:\s*13px;/);
  assert.match(tokens, /--measure:\s*64ch;/);
  const all = rules();
  const base = (selector) => all.find((rule) => rule.context.length === 0 && rule.selectors.includes(selector))?.decls ?? {};
  for (const selector of [".panel-caption", ".prose-note", ".callout"]) {
    assert.equal(base(selector)["font-size"], "var(--fs-body)", selector);
    assert.equal(base(selector)["max-width"], "var(--measure)", selector);
  }
  for (const selector of [".panel-meta", ".study-meta"]) assert.equal(base(selector)["font-family"], "var(--font-sans)", selector);
  assert.equal(base("code")["font-size"], "max(11px, 0.92em)");
});

test("the price change is set in the text colour with a direction arrow, and the sparkline label is its title", () => {
  const data = readFixture("dashboard_data_v3.json");
  const quote = m.quote.resolveQuote(null, data);
  const html = renderToStaticMarkup(createElement(m.marketStrip.default, { data, quote }));
  const change = Number(quote.changePct ?? quote.change);
  assert.ok(Number.isFinite(change) && change !== 0, "the fixture has a price change");
  assert.ok(html.includes(`<span class="ms-arrow" aria-hidden="true">${change > 0 ? "▲" : "▼"}</span>`), html);
  assert.ok(!/is-up|is-down|ms-spark-label/.test(html));
  const closes = data.vol_forecast.recent_closes.close.length;
  assert.ok(html.includes(`title="${m.marketStrip.sparklineLabel(data, closes).title}"`), "the series is named in the sparkline's title");
  const colours = rules().filter((rule) => rule.selectors.some((selector) => /\.ms-change\b/.test(selector)) && rule.decls.color);
  assert.ok(colours.length > 0 && colours.every((rule) => rule.decls.color === "var(--text-2)"), "no gain/loss colour on the change");
});

test("a lazy figure reserves its height per breakpoint and is busy, not a live region, until it mounts", async () => {
  const { default: LazyMount, reservedHeights } = await m.load("/src/components/LazyMount.jsx");
  assert.deepEqual(reservedHeights(320), { wide: 320, narrow: 320 });
  assert.deepEqual(reservedHeights({ wide: 360, narrow: 324 }), { wide: 360, narrow: 324 });
  assert.deepEqual(reservedHeights({ wide: 200 }), { wide: 200, narrow: 200 });
  assert.deepEqual(reservedHeights(undefined), { wide: 240, narrow: 240 });
  assert.deepEqual(reservedHeights({ wide: Number.NaN, narrow: -5 }), { wide: 240, narrow: 240 });

  const html = renderToStaticMarkup(createElement(LazyMount, { minHeight: { wide: 360, narrow: 324 }, label: "forecast chart" },
    createElement("div", { className: "the-chart" })));
  assert.match(html, /^<div class="lazy-mount" aria-busy="true" style="--lazy-h:360px;--lazy-h-narrow:324px">/);
  assert.ok(html.includes('<div class="figure-placeholder">Loading forecast chart…</div>'), html);
  assert.ok(!html.includes("the-chart"), "the figure itself waits for the client");
  assert.ok(!html.includes('role="status"'));
  // The CSS reads the variables, with the narrow one at 640px and below.
  const reserve = rules().filter((rule) => rule.selectors.includes(".lazy-mount > .figure-placeholder"));
  assert.ok(reserve.some((rule) => rule.context.length === 0 && /var\(--lazy-h\b/.test(rule.decls["min-height"])));
  assert.ok(reserve.some((rule) => rule.context.some((at) => /max-width:\s*640px/.test(at)) && /var\(--lazy-h-narrow\b/.test(rule.decls["min-height"])));

  // On the whole dashboard every placeholder is in a busy wrapper, and none is a live region.
  const dashboard = renderToStaticMarkup(createElement(m.dashboard.default, {
    data: readFixture("dashboard_data_v3.json"), research: readFixture("research.json"), researchStatus: "ready",
    quote: m.quote.resolveQuote(null, readFixture("dashboard_data_v3.json")),
  }));
  const placeholders = dashboard.match(/class="figure-placeholder"/g) ?? [];
  assert.ok(placeholders.length >= 5);
  assert.equal((dashboard.match(/class="lazy-mount" aria-busy="true"/g) ?? []).length, placeholders.length);
  assert.ok(!/class="figure-placeholder"[^>]*role="status"/.test(dashboard));
});

// A stand-in browser for printPage(): records events, frames and the print call in order. The page
// reports `busyChecks` figures still loading, and its fonts are ready only once released.
const fakeBrowser = (busyChecks) => {
  const saved = { window: globalThis.window, document: globalThis.document };
  const log = [];
  let checks = 0;
  let releaseFonts;
  const fontsReady = new Promise((resolve) => { releaseFonts = resolve; }).then(() => log.push("fonts"));
  globalThis.window = {
    dispatchEvent: (event) => log.push(event.type),
    requestAnimationFrame: (callback) => setTimeout(() => { log.push("frame"); callback(); }, 0),
    setTimeout: (callback, ms) => setTimeout(callback, ms),
    print: () => log.push("print"),
  };
  globalThis.document = {
    querySelector: (selector) => {
      assert.equal(selector, ".lazy-mount[aria-busy='true']");
      checks += 1;
      return checks <= busyChecks ? {} : null;
    },
    fonts: { ready: fontsReady },
  };
  return {
    log,
    checks: () => checks,
    releaseFonts: () => releaseFonts(),
    restore: () => Object.assign(globalThis, saved),
  };
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("printPage mounts every figure and opens every note, then waits for figures, fonts and two frames", async () => {
  const { printPage } = await m.load("/src/lib/print.js");
  const browser = fakeBrowser(3);
  try {
    const printing = printPage();
    await sleep(700);
    assert.deepEqual(browser.log, ["lazy:mount-all", "notes:open-all"], "nothing more happens until the fonts are ready");
    assert.equal(browser.checks(), 4, "it polled until no figure was busy");
    browser.releaseFonts();
    await printing;
    assert.deepEqual(browser.log, ["lazy:mount-all", "notes:open-all", "fonts", "frame", "frame", "print"]);
  } finally {
    browser.restore();
  }
});

test("a figure that never finishes loading does not block printing past the timeout", async () => {
  const { figuresMounted } = await m.load("/src/lib/print.js");
  const browser = fakeBrowser(Infinity);
  try {
    const started = Date.now();
    await figuresMounted(120);
    assert.ok(browser.checks() >= 2);
    assert.ok(Date.now() - started < 1000);
  } finally {
    browser.restore();
  }
});
