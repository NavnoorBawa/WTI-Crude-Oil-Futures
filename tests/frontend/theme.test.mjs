// tokens.css is the source of truth for colour; canvas charts read the JS mirror in chartTheme.js.
// This keeps the two from drifting apart.
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
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

// The options object literal of each createChart call in a source file, found by brace matching.
const createChartOptions = (source) =>
  [...source.matchAll(/createChart\(\s*\w+,\s*\{/g)].map((match) => {
    let depth = 0;
    for (let i = match.index + match[0].length - 1; i < source.length; i += 1) {
      if (source[i] === "{") depth += 1;
      else if (source[i] === "}" && --depth === 0) return source.slice(match.index, i + 1);
    }
    throw new Error("unbalanced createChart options");
  });

test("every chart formats with the page's en-US locale, not the visitor's navigator.language", () => {
  // Without a locale lightweight-charts calls toLocaleString(navigator.language), and a POSIX tag throws.
  assert.throws(() => new Date(0).toLocaleString("en-US@posix"), RangeError);
  assert.equal(theme.baseChartOptions().localization.locale, "en-US");
  const files = readdirSync(new URL("../../src/charts/", import.meta.url)).filter((file) =>
    readSource(`src/charts/${file}`).includes("createChart("),
  );
  assert.ok(files.length >= 3, "the lightweight-charts figures are found");
  for (const file of files) {
    const source = readSource(`src/charts/${file}`);
    const calls = createChartOptions(source);
    assert.equal(calls.length, source.match(/createChart\(/g).length, `${file} passes createChart an inline options object`);
    for (const options of calls) {
      assert.match(options, /\.\.\.baseChartOptions\(\),/, `${file} starts from the shared options`);
      // A chart that sets its own localization replaces the shared one, so it must spread it back in.
      if (/\blocalization\b/.test(options)) {
        assert.match(
          options,
          /localization: \{\s*\.\.\.baseChartOptions\(\)\.localization,/,
          `${file} keeps the shared locale: write localization: { ...baseChartOptions().localization, ... }`,
        );
      }
    }
  }
});

test("meaning-bearing chart marks meet 3:1 on the panel once their opacity is composited (WCAG 1.4.11)", () => {
  // The marks' colours and opacities are read from the stylesheets, so a palette or opacity change
  // that drops a mark below 3:1 fails here. Grid lines and soft fills are decorative and exempt;
  // each soft fill carries an edge that is checked instead. Every stylesheet main.jsx imports is
  // read in order, and a later rule for the same selector overrides an earlier one, as in the page.
  const sheets = [...readSource("src/main.jsx").matchAll(/^import "\.\/(.+\.css)";$/gm)].map((match) => `src/${match[1]}`);
  assert.ok(sheets.includes("src/index.css"));
  const css = sheets.map(readSource).join("\n").replace(/\/\*[\s\S]*?\*\//g, "");
  const decls = (selector) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const bodies = [...css.matchAll(new RegExp(`(?:^|[}\\s])${escaped}\\s*\\{([^}]*)\\}`, "gm"))].map((match) => match[1]);
    assert.ok(bodies.length, `${selector} is styled`);
    return Object.fromEntries(bodies.flatMap((body) => body.split(";").filter((part) => part.includes(":")).map((part) => {
      const colon = part.indexOf(":");
      return [part.slice(0, colon).trim(), part.slice(colon + 1).trim()];
    })));
  };
  const rgb = (hex) => [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16));
  const token = (value) => {
    const name = /^var\((--[a-z0-9-]+)\)$/.exec(value)?.[1];
    assert.ok(name && cssVars[name], `${value} is a colour token`);
    return rgb(cssVars[name]);
  };
  const luminance = (channels) => {
    const [r, g, b] = channels.map((value) => {
      const c = value / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const panel = rgb(cssVars["--bg-1"]);
  // A mark drawn at `alpha` over the panel, measured against the panel.
  const contrastOnPanel = (channels, alpha) => {
    const composite = channels.map((value, i) => alpha * value + (1 - alpha) * panel[i]);
    const [hi, lo] = [luminance(composite), luminance(panel)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const rgbaEdge = (value) => {
    const match = /rgb\((\d+) (\d+) (\d+) \/ ([\d.]+)\)/.exec(value ?? "");
    assert.ok(match, `${value} is an rgb() edge with alpha`);
    return { channels: match.slice(1, 4).map(Number), alpha: Number(match[4]) };
  };

  const marks = [];
  const premium = decls(".dot-premium");
  marks.push(["VRP premium dots", token(premium.fill), Number(premium["fill-opacity"])]);
  marks.push(["stability range bars", token(decls(".stroke-range").stroke), 1]);
  const coneEdge = decls(".stroke-cone-edge");
  marks.push(["cone band edges", token(coneEdge.stroke), Number(coneEdge["stroke-opacity"])]);
  // .thin dims whichever series stroke it is combined with.
  const thin = Number(decls(".thin")["stroke-opacity"]);
  for (const series of ["truth", "model", "implied", "bench", "alt", "alt2"]) {
    marks.push([`thin ${series} line`, token(decls(`.stroke-${series}`).stroke), thin]);
  }
  // The legend keys of the soft fills carry their plot's edge: the series' own channels.
  for (const [selector, series] of [[".swatch-band", "--s-model"], [".swatch-cone-inner", "--s-truth"], [".swatch-cone-outer", "--s-truth"]]) {
    const edge = rgbaEdge(decls(selector).border);
    assert.deepEqual(edge.channels, rgb(cssVars[series]), `${selector} edge is ${series}`);
    marks.push([`${selector} edge`, edge.channels, edge.alpha]);
  }
  // The forecast chart's 80% band edge is drawn in canvas, as the model colour at 70%.
  marks.push(["80% band edge (model at 70%)", rgb(cssVars["--s-model"]), 0.7]);

  for (const [name, channels, alpha] of marks) {
    assert.ok(Number.isFinite(alpha) && alpha > 0 && alpha <= 1, `${name} opacity ${alpha}`);
    const ratio = contrastOnPanel(channels, alpha);
    assert.ok(ratio >= 3, `${name}: ${ratio.toFixed(2)}:1 on --bg-1`);
  }
});
