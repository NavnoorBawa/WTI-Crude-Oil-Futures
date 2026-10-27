// Responsive layout guards: rules that keep tables, headers and grids readable at the widths a
// browser review found broken. Layout itself is checked in a browser; these tests keep the rules
// that fixed it from being dropped or out-ranked by a later edit.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readSource } from "./load.mjs";

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
