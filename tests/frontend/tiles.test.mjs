// The verdict's evidence tiles: one sample per headline value (the benchmark lab, every
// out-of-sample day), the weekly production figure named in each footnote, the fallbacks while
// research.json loads or when it is missing, the glyphs, and the key that names both samples.
// Rendered against the fixtures; every expected number is recomputed here from the raw payloads.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, readSource } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let Verdict;
let tiles;
let glyph;
before(async () => {
  m = await loadDashboard();
  Verdict = (await m.load("/src/panels/Verdict.jsx")).default;
  tiles = await m.load("/src/lib/tiles.js");
  glyph = await m.load("/src/components/KpiGlyph.jsx");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");
const lab = research.vol_lab;
const v = data.vol_forecast.validation;
const champion = lab.config.champion;
const recordModel = lab.leaderboard.models.find((model) => model.key === champion);
const DASH = "—";

/** Text inside one element: tags dropped (inline spans add no spaces), entities and NBSPs decoded. */
const plain = (html) => html.replace(/<[^>]+>/g, "")
  .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"").replace(/&#x27;/g, "'")
  .replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();

const renderVerdict = (payload, researchPayload, status = researchPayload ? "ready" : "unavailable") => {
  const errors = [];
  const original = console.error;
  console.error = (...args) => errors.push(args.join(" "));
  try {
    const html = renderToStaticMarkup(createElement(Verdict, { data: payload, research: researchPayload, researchStatus: status }));
    return { html, errors };
  } finally {
    console.error = original;
  }
};

/** Each tile's children in order, with their class, attributes, markup and text. */
const readTiles = (html) => [...html.matchAll(/<div class="kpi">([\s\S]*?)<\/div>/g)].map((match) => {
  const children = [...match[1].matchAll(/<(dt|dd) class="([^"]+)"([^>]*)>([\s\S]*?)<\/\1>/g)]
    .map(([, tag, cls, attrs, inner]) => ({ tag, cls, attrs, html: inner, text: plain(inner) }));
  const by = Object.fromEntries(children.map((child) => [child.cls.split(" ")[0], child]));
  return {
    children,
    label: by["kpi-label"]?.text ?? "",
    value: plain((by["kpi-value"]?.html ?? "").split("<span class=\"kpi-unit\">")[0]),
    glyph: by["kpi-glyph"],
    base: by["kpi-base"]?.text ?? "",
    baseHtml: by["kpi-base"]?.html ?? "",
    foot: by["kpi-foot"]?.text ?? "",
    raw: match[1],
  };
});
const key = (html) => plain(html.match(/<p class="kpi-key">([\s\S]*?)<\/p>/)?.[1] ?? "");

const f = () => m.format;
const coverageYears = () => Object.values(lab.conformal[champion].coverage_by_year_pct);
const grid0 = (method, level) => lab.var_backtest.grid0.methods.find((row) => row.key === method && row.level === level).kupiec;

// ------------------------------------------------------------------------------- one sample
test("each headline value is the benchmark lab's, on every out-of-sample day", () => {
  const { html, errors } = renderVerdict(data, research);
  assert.deepEqual(errors, []);
  const [qlike, mcs, coverage, varTile, mz] = readTiles(html);
  assert.equal(qlike.value, f().fmtNum(recordModel.qlike, 3), "QLIKE: the record's leaderboard row");

  const excluded = new Set(lab.leaderboard.mcs_excludes);
  const eligible = lab.leaderboard.models.filter((model) => !model.timing_control && !excluded.has(model.key));
  const survivors = eligible.filter((model) => lab.leaderboard.mcs_qlike_included.includes(model.key));
  assert.equal(mcs.value, `${survivors.length}/${eligible.length}`, "MCS: survivors of the eligible models");

  const years = coverageYears();
  assert.equal(coverage.value, `${f().fmtNum(Math.min(...years), 1)}–${f().fmtNum(Math.max(...years), 1)}%`, "coverage: the yearly range");
  assert.match(coverage.base, /by construction/);
  assert.ok(coverage.base.includes(`overall ${f().fmtPct(lab.conformal[champion].coverage_pct)}`));

  assert.equal(varTile.value, String(grid0(`${champion}:fhs`, 0.01).exceedances), "VaR: grid 0, filtered historical simulation");
  assert.equal(mz.value, f().fmtNum(recordModel.mincer_zarnowitz.slope, 2), "MZ: the record's every-day slope");
  assert.ok(mz.base.includes(`SE ${f().fmtNum(recordModel.mincer_zarnowitz.slope_se, 2)}`));
});

test("each footnote names the weekly production figure beside the lab value", () => {
  const [qlike, mcs, coverage, , mz] = readTiles(renderVerdict(data, research).html);
  assert.ok(qlike.foot.includes(`every OOS day, ${f().fmtInt(lab.leaderboard.n_days)} d`));
  assert.ok(qlike.foot.includes(`weekly validation: ${f().fmtNum(v.har_qlike, 3)} vs ${f().fmtNum(v.ovx_only_qlike, 3)} (${f().fmtInt(v.n)} wk)`));
  assert.ok(coverage.foot.includes(`weekly validation: ${f().fmtPct(v.interval_80_coverage_pct)} of ${f().fmtInt(v.interval_80_n)} wk`));
  assert.ok(mz.foot.includes(`weekly validation: ${f().fmtNum(v.har_mincer_zarnowitz.slope, 2)} (SE ${f().fmtNum(v.har_mincer_zarnowitz.slope_se, 2)})`));
  assert.equal(mcs.foot, "QLIKE, every OOS day · timing control excluded");
  // The DM statistics are the lede's; the tile does not repeat them.
  assert.doesNotMatch(qlike.foot + qlike.base, /DM/);
});

test("the QLIKE tile names both comparators as the leaderboard does", () => {
  const [qlike] = readTiles(renderVerdict(data, research).html);
  const label = m.research.ovxOnlyLabel(lab);
  const ovxOnly = lab.leaderboard.models.find((model) => model.key === m.research.OVX_ONLY_KEY);
  const persistence = lab.leaderboard.models.find((model) => model.key === "persistence");
  assert.equal(qlike.base, `vs ${f().fmtNum(ovxOnly.qlike, 3)} ${label}, ${f().fmtNum(persistence.qlike, 3)} persistence · lower is better`);
  assert.equal(qlike.label, "Forecast loss (QLIKE) →");
});

test("the MCS tile says whether the model of record is in the set, with its p and grid count", () => {
  const [, mcs] = readTiles(renderVerdict(data, research).html);
  const p = f().fmtP(recordModel.mcs_qlike_p).replace(/\u00a0/g, " ");
  const grids = Object.values(lab.leaderboard.mcs_qlike_by_grid);
  const gridsIn = grids.filter((keys) => keys.includes(champion)).length;
  assert.equal(mcs.base, `HAR-IV not in the ${m.research.mcsLevel(lab)}% set (MCS ${p}); in on ${gridsIn}/${grids.length} grids`);
  const inSet = structuredClone(research);
  inSet.vol_lab.leaderboard.mcs_qlike_included.push(champion);
  inSet.vol_lab.leaderboard.models.find((model) => model.key === champion).in_mcs_qlike = true;
  assert.match(readTiles(renderVerdict(data, inSet).html)[1].base, /^HAR-IV is in the 90% set \(MCS p = /);
});

// -------------------------------------------------------------------------------- fallbacks
test("without research.json the weekly figures become the values, and say so", () => {
  const { html, errors } = renderVerdict(data, null);
  assert.deepEqual(errors, []);
  const [qlike, mcs, coverage, varTile, mz] = readTiles(html);
  assert.equal(qlike.value, f().fmtNum(v.har_qlike, 3));
  assert.equal(coverage.value, f().fmtPct(v.interval_80_coverage_pct));
  assert.equal(mz.value, f().fmtNum(v.har_mincer_zarnowitz.slope, 2));
  for (const tile of [qlike, coverage, mz]) {
    assert.match(tile.base, /^weekly production validation/, tile.label);
    assert.equal(tile.foot, "research.json unavailable", tile.label);
  }
  for (const tile of [mcs, varTile]) {
    assert.equal(tile.value, DASH, tile.label);
    assert.equal(tile.base, "research.json unavailable", tile.label);
  }
  assert.equal(key(html), `Tiles: weekly production validation (${f().fmtInt(v.n)} weeks); research.json did not load.`);
});

test("while research.json loads, the tiles show a dash and the weekly figure waits in the footnote", () => {
  const [qlike, mcs, coverage, varTile, mz] = readTiles(renderVerdict(data, null, "loading").html);
  for (const tile of [qlike, mcs, coverage, varTile, mz]) {
    assert.equal(tile.value, DASH, tile.label);
    assert.equal(tile.base, "loading research.json", tile.label);
    assert.equal(tile.glyph.html, "", `${tile.label}: no glyph without a value`);
  }
  assert.ok(qlike.foot.includes(f().fmtNum(v.har_qlike, 3)));
  assert.ok(coverage.foot.includes(f().fmtPct(v.interval_80_coverage_pct)));
  assert.ok(mz.foot.includes(f().fmtNum(v.har_mincer_zarnowitz.slope, 2)));
});

test("a research.json without a block keeps the 'no … in research.json' wording", () => {
  const partial = structuredClone(research);
  delete partial.vol_lab.leaderboard;
  delete partial.vol_lab.conformal;
  delete partial.vol_lab.var_backtest;
  const [qlike, mcs, coverage, varTile, mz] = readTiles(renderVerdict(data, partial).html);
  assert.equal(qlike.value, f().fmtNum(v.har_qlike, 3));
  assert.equal(qlike.foot, "no model leaderboard in research.json");
  assert.equal(mcs.base, "no Model Confidence Set in research.json");
  assert.equal(coverage.foot, "no conformal coverage in research.json");
  assert.equal(varTile.base, "no VaR backtest in research.json");
  assert.equal(mz.foot, "no Mincer-Zarnowitz slope in research.json");
});

// ---------------------------------------------------------------------- comparators, counted
test("the interval's sharpness is compared with a fixed 10th–90th percentile band (nearest rank)", () => {
  const values = lab.series.actual.filter((value) => typeof value === "number" && value > 0).sort((a, b) => a - b);
  const at = (pct) => values[Math.ceil((values.length * pct) / 100) - 1];
  const band = tiles.unconditionalBand(lab.series.actual);
  assert.equal(band.lo, at(10));
  assert.equal(band.hi, at(90));
  assert.equal(band.ratio, at(90) / at(10));
  assert.equal(band.n, values.length);
  const [, , coverage] = readTiles(renderVerdict(data, research).html);
  assert.ok(coverage.foot.startsWith(`median hi/lo ${f().fmtNum(lab.conformal[champion].median_hi_lo_ratio, 2)} vs `
    + `${f().fmtNum(at(90) / at(10), 2)} for a fixed 10th–90th percentile band of weekly realized vol, `), coverage.foot);
  assert.match(coverage.foot, /\(in-sample\)/);
  // Nearest rank: the smallest value with at least pct% of the sample at or below it.
  assert.equal(tiles.nearestRank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 10), 1);
  assert.equal(tiles.nearestRank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90), 9);
  assert.equal(tiles.nearestRank([3, 7], 50), 3);
});

test("the VaR footnote counts the normal quantile and raw OVX beside the filtered simulation", () => {
  const [, , , varTile] = readTiles(renderVerdict(data, research).html);
  const normal = grid0(`${champion}:normal`, 0.01);
  const rawOvx = grid0("raw_ovx:normal", 0.01);
  const fhs = grid0(`${champion}:fhs`, 0.01);
  assert.ok(varTile.base.includes(`vs ${f().fmtNum(fhs.expected, 1)} expected · Kupiec ${f().fmtP(fhs.p_value).replace(/\u00a0/g, " ")}`));
  assert.ok(varTile.foot.includes(`normal quantile: ${normal.exceedances} (${f().fmtP(normal.p_value).replace(/\u00a0/g, " ")})`), varTile.foot);
  assert.ok(varTile.foot.includes(`Raw OVX (normal): ${rawOvx.exceedances}`), varTile.foot);
  const byGrid = lab.var_backtest.summary[`${champion}:fhs@0.01`].exceedances_by_grid;
  assert.ok(varTile.foot.includes(`${Math.min(...byGrid)}–${Math.max(...byGrid)} across grids`));
  // A missing piece is left out, never printed as a dash.
  const noRaw = structuredClone(research);
  noRaw.vol_lab.var_backtest.grid0.methods = noRaw.vol_lab.var_backtest.grid0.methods.filter((row) => row.key !== "raw_ovx:normal");
  const foot = readTiles(renderVerdict(data, noRaw).html)[3].foot;
  assert.doesNotMatch(foot, /Raw OVX|—/);
});

test("the key names both samples and, computed, whether the two pipelines' forecasts and bands agree", () => {
  const recent = data.vol_forecast.recent_oos;
  const labIndex = new Map(lab.series.date.map((date, index) => [date, index]));
  const gaps = (labKey, recentKey) => recent.date
    .map((date, row) => [lab.series[labKey][labIndex.get(date)], recent[recentKey][row]])
    .filter(([a, b]) => typeof a === "number" && typeof b === "number")
    .map(([a, b]) => Math.abs(a - b));
  const diffs = gaps("har_iv", "forecast");
  const bandDiffs = [...gaps("lo80", "lo80"), ...gaps("hi80", "hi80")];
  const agreement = tiles.pipelineAgreement(lab.series, recent);
  assert.ok(agreement.k > 0);
  assert.equal(agreement.k, diffs.length);
  assert.equal(agreement.d, Math.max(...diffs));
  assert.equal(agreement.bands.k, gaps("lo80", "lo80").length);
  assert.equal(agreement.bands.d, Math.max(...bandDiffs));
  const text = key(renderVerdict(data, research).html);
  assert.ok(text.startsWith(`Tiles (except the live record): benchmark lab, every out-of-sample day (${f().fmtInt(lab.leaderboard.n_days)} days, `
    + `${f().fmtSpan(lab.sample.first, lab.sample.last)}; inputs pinned by SHA-256). Footnotes: weekly production validation (${f().fmtInt(v.n)} weeks).`), text);
  // The forecasts agree but the fixtures' bands do not: the key says the gaps come from the days
  // scored only for loss and slope, and that each pipeline sets its own band.
  assert.ok(Math.max(...bandDiffs) > 0.05);
  assert.ok(text.includes(`Both pipelines issue the same weekly forecasts (${f().fmtInt(diffs.length)} overlapping weeks, largest difference `
    + `${f().fmtNum(Math.max(...diffs), 2)} vol pts), so the loss and slope gaps between the two come from which days are scored; `
    + `each pipeline sets its own 80% band (up to ${f().fmtNum(Math.max(...bandDiffs), 1)} vol pts apart on those weeks).`), text);
  const sameBands = structuredClone(data);
  for (const field of ["lo80", "hi80"]) {
    sameBands.vol_forecast.recent_oos[field] = sameBands.vol_forecast.recent_oos.date.map((date, row) => (labIndex.has(date)
      ? lab.series[field][labIndex.get(date)] : sameBands.vol_forecast.recent_oos[field][row]));
  }
  assert.ok(key(renderVerdict(sameBands, research).html).endsWith("vol pts), so gaps between the two come from which days are scored."));

  const drift = structuredClone(data);
  const index = drift.vol_forecast.recent_oos.date.findIndex((date) => labIndex.has(date));
  drift.vol_forecast.recent_oos.forecast[index] += 2;
  const drifted = key(renderVerdict(drift, research).html);
  assert.ok(drifted.includes(`differ by up to ${f().fmtNum(tiles.pipelineAgreement(lab.series, drift.vol_forecast.recent_oos).d, 2)} vol pts`
    + ` on ${f().fmtInt(diffs.length)} overlapping weeks`), drifted);
  assert.ok(!drifted.includes("same weekly forecasts"));

  const unpinned = structuredClone(research);
  delete unpinned.vol_lab.generated_from.files.cl_daily.sha256;
  assert.ok(!key(renderVerdict(data, unpinned).html).includes("SHA-256"), "pinned only when every input has a digest");
});

// ------------------------------------------------------------------------ markup and glyphs
test("every tile has the same five children in order, so the rows line up", () => {
  for (const [payload, status] of [[research, "ready"], [null, "loading"], [null, "unavailable"]]) {
    const all = readTiles(renderVerdict(data, payload, status).html);
    assert.equal(all.length, 6, status);
    for (const tile of all) {
      assert.deepEqual(tile.children.map((child) => `${child.tag}.${child.cls.split(" ")[0]}`),
        ["dt.kpi-label", "dd.kpi-value", "dd.kpi-glyph", "dd.kpi-base", "dd.kpi-foot"], `${status}: ${tile.label}`);
    }
  }
});

test("each tile is one link to its evidence, and the glyphs are hidden from assistive technology", () => {
  const all = readTiles(renderVerdict(data, research).html);
  assert.deepEqual(all.map((tile) => tile.raw.match(/<a href="([^"]+)" class="kpi-link">/)?.[1]), ["#modl", "#modl", "#calb", "#risk", "#calb", "#live"]);
  for (const tile of all) {
    assert.match(tile.raw, /<span class="kpi-arrow" aria-hidden="true">\u00a0→<\/span><\/a>/, tile.label);
    assert.match(tile.glyph.attrs, /aria-hidden="true"/, tile.label);
    assert.ok(tile.glyph.html.length > 0, `${tile.label} draws a glyph`);
    assert.doesNotMatch(tile.glyph.html, /<(div|svg)\b/, "spans only");
  }
  // Model names never break at their hyphen.
  assert.match(all[0].baseHtml, /<span class="nowrap">OVX-only<\/span>/);
  assert.match(all[1].baseHtml, /<span class="nowrap">HAR-IV<\/span>/);
});

test("glyphs place their marks in percent of the domain, clamped to the track", () => {
  assert.equal(glyph.glyphPct(5, [0, 10]), 50);
  assert.equal(glyph.glyphPct(-3, [0, 10]), 0);
  assert.equal(glyph.glyphPct(14, [0, 10]), 100);
  assert.equal(glyph.glyphPct(5, [10, 10]), null);
  assert.equal(glyph.glyphPct(null, [0, 10]), null);
  const [qlike, mcs, coverage, varTile, mz] = tiles.evidenceTiles(data, research, "ready");
  // QLIKE: zero to persistence, the record as a dot and OVX alone as a ring.
  const persistence = lab.leaderboard.models.find((model) => model.key === "persistence").qlike;
  assert.deepEqual(qlike.glyph.domain, [0, persistence]);
  assert.deepEqual(qlike.glyph.marks.map((mark) => [mark.type, mark.at]), [
    ["tick", persistence], ["dot", recordModel.qlike],
    ["ring", lab.leaderboard.models.find((model) => model.key === m.research.OVX_ONLY_KEY).qlike],
  ]);
  // MCS: one cell per eligible model in leaderboard (QLIKE) order; the record outlined in the model colour.
  const rows = m.research.leaderboardRows(lab).filter((row) => row.mcsEligible).sort((a, b) => a.qlike - b.qlike);
  assert.deepEqual(mcs.glyph.cells.map((cell) => cell.filled), rows.map((row) => row.inMcs));
  assert.equal(mcs.glyph.cells[rows.findIndex((row) => row.role === "record")].tone, "model");
  assert.equal(mcs.glyph.cells[rows.findIndex((row) => row.role === "challenger")].tone, "alt");
  // Coverage: the yearly range as a bar, the target as a tick, the overall coverage as a dot.
  const years = coverageYears();
  assert.deepEqual(coverage.glyph.marks.map((mark) => mark.type), ["bar", "tick", "dot"]);
  assert.deepEqual([coverage.glyph.marks[0].from, coverage.glyph.marks[0].to], [Math.min(...years), Math.max(...years)]);
  assert.ok(coverage.glyph.domain[0] <= Math.min(70, Math.min(...years) - 2) && coverage.glyph.domain[1] >= 90);
  // VaR: the axis reaches at least twice the expected count.
  assert.ok(varTile.glyph.domain[1] >= 2 * grid0(`${champion}:fhs`, 0.01).expected);
  // MZ: a whisker of ±1.96 SE round the slope, and a tick at 1.
  const { slope, slope_se: se } = recordModel.mincer_zarnowitz;
  const whisker = mz.glyph.marks.find((mark) => mark.type === "bar");
  assert.ok(Math.abs(whisker.to - whisker.from - 2 * 1.96 * se) < 1e-9);
  assert.ok(mz.glyph.marks.some((mark) => mark.type === "tick" && mark.at === 1));
  assert.ok(mz.glyph.domain[1] >= Math.max(2, slope + 1.96 * se));
});

test("the live tile draws the promotion test as one cell per required week", () => {
  const payload = structuredClone(data);
  Object.assign(payload.vol_forecast.live_record.promotion, { n_independent_paired: 7, required: 52 });
  const live = readTiles(renderVerdict(payload, research).html)[5];
  assert.equal((live.glyph.html.match(/class="kg-cell /g) ?? []).length, 52);
  assert.equal((live.glyph.html.match(/is-filled/g) ?? []).length, 7);
  assert.equal(tiles.progressGlyph(null), null);
});

test("tile labels keep their case and the footnotes are set in the sans face", () => {
  const sheets = [...readSource("src/main.jsx").matchAll(/^import "\.\/(.+\.css)";$/gm)].map((match) => `src/${match[1]}`);
  const rules = sheets.flatMap((sheet) => [...readSource(sheet).replace(/\/\*[\s\S]*?\*\//g, "")
    .matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selector, body]) => ({ selector: selector.trim(), body })));
  const last = (pattern, property) => rules
    .filter((rule) => pattern.test(rule.selector) && new RegExp(`(^|;)\\s*${property}\\s*:`).test(rule.body))
    .map((rule) => rule.body.match(new RegExp(`${property}\\s*:\\s*([^;]+)`))[1].trim())
    .at(-1);
  assert.notEqual(last(/\.kpi-label(?![\w-])/, "text-transform") ?? "none", "uppercase");
  assert.equal(last(/\.kpi-label(?![\w-])/, "letter-spacing"), "0");
  assert.ok(!/mono/.test(last(/\.kpi-foot(?![\w-])/, "font-family") ?? ""), "the footnote is not mono");
  assert.equal(last(/\.kpi-foot(?![\w-])/, "font-size"), "var(--fs-12)");
});
