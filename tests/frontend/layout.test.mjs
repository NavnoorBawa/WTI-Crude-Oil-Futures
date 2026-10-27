// Responsive layout guards: rules that keep tables, headers and grids readable at the widths a
// browser review found broken. Layout itself is checked in a browser; these tests keep the rules
// that fixed it from being dropped or out-ranked by a later edit.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readSource } from "./load.mjs";

let m;
let common;
before(async () => {
  m = await loadDashboard();
  common = await m.load("/src/panels/common.jsx");
});
after(() => m.close());

const chrome = readSource("src/styles/chrome-foundation.css");
const reference = readSource("src/styles/reference-sections.css");
const surface = readSource("src/styles/reading-surface.css");
const index = readSource("src/index.css");

test("on a tablet a header's form controls take their own row, and the code chip never shrinks", () => {
  assert.match(chrome, /@media \(max-width: 960px\) \{[^}]*?\.panel-actions:has\(input, select\) \{ grid-row: 4; grid-column: 1 \/ -1;/);
  assert.match(chrome, /\.panel-eyebrow > \.chip \{ flex: none; \}/);
});

test("a phone drops STDY's interval glyph and its note, so the peak p-value column fits beside the median", () => {
  assert.match(reference, /@media \(max-width: 640px\) \{[^}]*\.interval-glyph, \.glyph-note \{ display: none; \}/);
});

test("the retraction line and the market strip each keep a full-width row on a wide screen", () => {
  assert.match(surface, /^\.strip-row > \* \{ flex-basis: 100%; \}$/m);
  assert.doesNotMatch(surface, /\.strip-row \{ display: grid;/, "no half-width columns that wrap each line");
});

test("a header label that does not sort is padded to the sort button's 28px line box", () => {
  assert.match(index, /\.sort-button \{[^}]*min-height: 28px;/);
  assert.match(index, /\.data-table thead th > \.th-label \{ display: inline-block; padding-block: calc\(\(28px - 1lh\) \/ 2\); \}/);
});

test("a phone's card table un-sticks its hidden first header, so no 112px rule floats above the cards", () => {
  // index.css keeps a sticky row header sticky and 112px wide with a 0,4,3 selector; the card rules must out-rank it.
  assert.match(index, /\.data-table\.has-sticky thead tr:not\(\.group-row\) th:first-child \{ white-space: normal; min-width: 112px;/);
  assert.match(chrome, /\.data-table\.is-cards\.has-sticky thead tr:not\(\.group-row\) th:first-child \{ position: static; min-width: 0; max-width: none; \}/);
  assert.match(chrome, /\.data-table\.is-cards\.has-sticky thead tr:not\(\.group-row\) th:first-child:not\(\[aria-sort\]\) \{ position: absolute; \}/);
});

test("from 1280px the leaderboard keeps its inputs and family on one line; below it they wrap to fit", () => {
  const models = readSource("src/styles/model-comparison.css");
  assert.match(models, /\.leaderboard \.data-table :is\(\.leader-model, \.muted-cell\) \{ white-space: normal; \}/);
  assert.match(models, /@media \(min-width: 1280px\) \{\s*\.leaderboard \.data-table \.muted-cell \{ white-space: nowrap; \}/);
});

test("at 360px RISK's data columns give up half their side padding, so its four phone columns fit", () => {
  const risk = readSource("src/styles/calibration-risk-cone.css");
  assert.match(risk, /@media \(max-width: 360px\) \{\s*#risk \.data-table\.is-dense th:not\(:first-child\),\s*#risk \.data-table\.is-dense td:not\(:first-child\) \{ padding-left: var\(--sp-1\); padding-right: var\(--sp-1\); \}/);
});

test("Facts names a count of three, four or six, and those grids take a column count that divides it", () => {
  const facts = (count, className) => renderToStaticMarkup(createElement(common.Facts, {
    className,
    items: Array.from({ length: count }, (_, index) => ({ label: `Fact ${index}`, value: String(index) })),
  })).match(/<dl class="([^"]*)">/)[1];
  assert.equal(facts(3, "facts--row"), "facts facts--3 facts--row");
  assert.equal(facts(4), "facts facts--4 ");
  assert.equal(facts(6), "facts facts--6 ");
  assert.equal(facts(5), "facts ", "an uncounted grid keeps auto-fit");
  // A missing value is not a fact, so it does not count.
  const shown = renderToStaticMarkup(createElement(common.Facts, { items: [{ label: "a", value: 1 }, { label: "b", value: null }, { label: "c", value: 2 }, { label: "d", value: 3 }] }));
  assert.match(shown, /<dl class="facts facts--3 ">/);
  assert.match(index, /\.facts--3:not\(\.facts--stack\), \.facts--6:not\(\.facts--stack\) \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \}/);
  assert.match(index, /\.facts--4:not\(\.facts--stack\) \{ grid-template-columns: repeat\(4, minmax\(0, 1fr\)\); \}/);
  assert.match(index, /@media \(max-width: 600px\) \{\s*\.facts--4:not\(\.facts--stack\), \.facts--6:not\(\.facts--stack\) \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); \}/);
});

test("each reproduce command and its copy button are one unit, and a flag never breaks at its hyphens", () => {
  const html = renderToStaticMarkup(createElement(m.panel.default, {
    id: "test", code: "TEST", name: "Test panel", title: "A claim",
    source: { artifact: "data.json → x", reproduce: ["python -m backend.backtest_walk_forward [--no-purge]", "python -m backend.leak_anatomy"] },
  }, createElement("p", null, "figure")));
  const units = [...html.matchAll(/<span class="source-command">([\s\S]*?)<\/output><\/span>/g)].map((match) => match[1]);
  assert.equal(units.length, 2);
  for (const unit of units) assert.match(unit, /<\/code><\/a><button type="button" class="copy-button copy-command" aria-label="Copy command: /);
  assert.match(units[0], /<code>python -m backend\.backtest_walk_forward <span class="nowrap">\[--no-purge\]<\/span><\/code>/);
  assert.match(index, /\.panel-source \.source-command \{ display: inline-flex; align-items: last baseline;[^}]*max-width: 100%; \}/);
});

test("STAB's model headers wrap at spaces and after a long name's first part, never inside a short one", async () => {
  const { modelHeader } = await m.load("/src/panels/StabilityPanel.jsx");
  const html = (label) => renderToStaticMarkup(createElement("span", null, modelHeader(label)));
  assert.equal(html("GJR-GARCH(1,1)-t"), '<span>GJR-<wbr/><span class="nowrap">GARCH(1,1)-t</span></span>');
  assert.equal(html("HAR-IV"), '<span><span class="nowrap">HAR-IV</span></span>');
  assert.equal(html("HAR-IV-range"), '<span><span class="nowrap">HAR-IV-range</span></span>');
  assert.equal(html("OVX-only regression"), '<span><span class="nowrap">OVX-only</span> regression</span>');
  assert.equal(html("Raw OVX"), "<span>Raw OVX</span>");
  // A wrapped sortable header keeps the same space under its last line as a one-line one.
  assert.match(index, /\.data-table thead th\.wrap-head > \.sort-button \{ min-height: 0; padding-block: calc\(\(28px - 1lh\) \/ 2\); \}/);
});

test("in print a long panel breaks between its blocks, its figures stay whole and its footer stays with its notes", () => {
  const print = surface.slice(surface.indexOf("@media print"));
  assert.doesNotMatch(print, /\.panel \{ break-inside: avoid-page; \}/, "a whole-panel avoid stranded footers on near-empty pages");
  assert.match(print, /\.panel-head, \.lw-figure, \.lazy-mount, svg\.svg-chart, img\.print-shot, \.facts \{ break-inside: avoid; \}/);
  assert.match(print, /\.panel-source \{ break-before: avoid; \}/);
});

test("on a phone the ledger's two filters share one row in equal halves under the search box", () => {
  assert.match(reference, /@media \(max-width: 640px\) \{[^}]*\.search-input \{ flex: 1 1 100%; width: auto; \}[\s\S]*?#ledg \.select \{ flex: 1 1 0; min-width: 0; \}/);
});

test("at 320px nothing widens the page: the 52-cell progress track shrinks, and a select never outgrows its row", () => {
  const tiles = readSource("src/styles/verdict-tiles.css");
  assert.match(tiles, /\.kg-progress \.kg-cell \{ flex: 1 1 0; min-width: 0;/, "52 cells x 2px plus gaps would need 155px");
  assert.match(index, /^\.select \{[^}]*max-width: 100%;/m);
});

test("the KPI tiles and their key start on the hero's and the panels' left edge", () => {
  const tiles = readSource("src/styles/verdict-tiles.css");
  assert.match(index, /\.verdict-hero \{[^}]*padding: [^;]*var\(--sp-6\)/);
  assert.match(index, /^\.kpi \{[^}]*padding: var\(--sp-4\) var\(--sp-6\);/m);
  assert.match(tiles, /\.kpi-key \{[^}]*padding: var\(--sp-3\) var\(--sp-6\);/);
});

test("wrapped footer rows are 10px apart, so stacked 16px links clear the 24px target spacing", () => {
  assert.match(index, /\.panel-source \{[^}]*gap: 10px var\(--sp-5\);/);
  assert.match(index, /\.panel-source > span \{[^}]*gap: 10px var\(--sp-2\); \}/);
});
