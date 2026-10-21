// tokens.css is the source of truth for colour; canvas charts read the JS mirror in chartTheme.js.
// This keeps the two from drifting apart.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readSource } from "./load.mjs";
import * as theme from "../../src/theme/chartTheme.js";

const tokens = readSource("src/theme/tokens.css");
const cssVars = Object.fromEntries([...tokens.matchAll(/(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map((match) => [match[1], match[2]]));

test("every colour token in tokens.css has an identical JS mirror, and vice versa", () => {
  const mirror = theme.CSS_VAR_MIRROR;
  assert.ok(Object.keys(cssVars).length >= 18, "tokens.css defines the palette");
  for (const [name, value] of Object.entries(cssVars)) {
    assert.ok(name in mirror, `${name} has no mirror in chartTheme.js`);
    assert.equal(mirror[name].toUpperCase(), value.toUpperCase(), `${name} drifted`);
  }
  for (const name of Object.keys(mirror)) assert.ok(name in cssVars, `${name} is mirrored but not defined in tokens.css`);
});

test("text tokens used for words meet WCAG AA (4.5:1) on every surface", () => {
  const luminance = (hex) => {
    const channel = (offset) => {
      const c = parseInt(hex.slice(offset, offset + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
  };
  const contrast = (a, b) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  for (const text of ["--text-1", "--text-2", "--text-3"]) {
    for (const surface of ["--bg-0", "--bg-1", "--bg-2"]) {
      assert.ok(contrast(cssVars[text], cssVars[surface]) >= 4.5, `${text} on ${surface}`);
    }
  }
});

test("withAlpha converts a hex colour to rgba", () => {
  assert.equal(theme.withAlpha("#3987E5", 0.5), "rgba(57, 135, 229, 0.5)");
});

test("no logo is drawn inside the charts, and the footer carries the library's notice and link", () => {
  // lightweight-charts reads the option from `layout`; a top-level key would be ignored.
  assert.equal(theme.baseChartOptions().layout.attributionLogo, false);
  assert.ok(!("attributionLogo" in theme.baseChartOptions()));
  const footer = readSource("src/app/SiteFooter.jsx");
  assert.match(footer, /TradingView Lightweight Charts™, Copyright © \d{4} TradingView,\s+Inc\./);
  assert.match(footer, /href="https:\/\/www\.tradingview\.com\/"/);
});
