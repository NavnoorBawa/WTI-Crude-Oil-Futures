// Responsive layout guards: rules that keep tables, headers and grids readable at the widths a
// browser review found broken. Layout itself is checked in a browser; these tests keep the rules
// that fixed it from being dropped or out-ranked by a later edit.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readSource } from "./load.mjs";

const chrome = readSource("src/styles/chrome-foundation.css");
const reference = readSource("src/styles/reference-sections.css");

test("on a tablet a header's form controls take their own row, and the code chip never shrinks", () => {
  assert.match(chrome, /@media \(max-width: 960px\) \{[^}]*?\.panel-actions:has\(input, select\) \{ grid-row: 4; grid-column: 1 \/ -1;/);
  assert.match(chrome, /\.panel-eyebrow > \.chip \{ flex: none; \}/);
});

test("a phone drops STDY's interval glyph and its note, so the peak p-value column fits beside the median", () => {
  assert.match(reference, /@media \(max-width: 640px\) \{[^}]*\.interval-glyph, \.glyph-note \{ display: none; \}/);
});
