// CALB, RISK, CONE and VRP: current marks on the cone with their own date, reconciled samples, and
// captions that match their tables. Every expected value is computed here from the fixtures (or from
// a copy with one field changed), never restated, so the tests follow the payload.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, visibleText } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let cone;
before(async () => {
  m = await loadDashboard();
  cone = await m.load("/src/lib/cone.js");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");
const lab = research.vol_lab;
const champion = lab.config.champion;

const render = (payload, researchPayload = research) => {
  const errors = [];
  const original = console.error;
  console.error = (...args) => errors.push(args.join(" "));
  try {
    const html = renderToStaticMarkup(createElement(m.dashboard.default, {
      data: payload, research: researchPayload, researchStatus: researchPayload ? "ready" : "unavailable",
      quote: m.quote.resolveQuote(null, payload),
    }));
    return { html, errors };
  } finally {
    console.error = original;
  }
};
const section = (html, id) => html.match(new RegExp(`<section id="${id}"[\\s\\S]*?</section>`))?.[0] ?? "";
const titleOf = (html, id) => visibleText(section(html, id).match(/<h2[^>]*>([\s\S]*?)<\/h2>/)?.[1] ?? "").trim();
const metaOf = (html, id) => visibleText(section(html, id).match(/<p class="panel-meta[^"]*">([\s\S]*?)<\/p>/)?.[1] ?? "");
const days = (from, to) => Math.round((Date.parse(to) - Date.parse(from)) / (24 * 60 * 60 * 1000));

/** data.json with the live block moved to a later date (the cone stays on its own date). */
const withLiveOn = (asOf, inputs = {}) => {
  const payload = structuredClone(data);
  payload.vol_forecast.live.as_of = asOf;
  Object.assign(payload.vol_forecast.live.inputs, inputs);
  return payload;
};

// ------------------------------------------------------------------------------------------- CONE
test("the cone bracket is read off the stored percentiles, never interpolated", () => {
  const row = { p5: 10, p25: 20, p50: 30, p75: 40, p95: 50 };
  const cases = [
    [5, "below p5", null, 5], [10, "p5–p25", 5, 25], [15, "p5–p25", 5, 25], [20, "p5–p25", 5, 25], [25, "p25–p50", 25, 50],
    [35, "p50–p75", 50, 75], [45, "p75–p95", 75, 95], [50, "p75–p95", 75, 95], [55, "above p95", 95, null],
  ];
  for (const [value, label, low, high] of cases) {
    assert.deepEqual(cone.coneBracket(value, row), { label, low, high }, String(value));
  }
  assert.equal(cone.coneBracket(null, row), null);
  assert.equal(cone.coneBracket(30, { ...row, p50: null }), null, "a missing percentile gives no bracket");
  assert.equal(cone.coneBracket(30, { ...row, p50: 45 }), null, "percentiles out of order give no bracket");
  assert.equal(cone.daysBetween("2026-10-19", "2026-10-23"), 4);
  assert.equal(cone.daysBetween("2026-10-23", "2026-10-19"), -4);
});

test("with data.json newer than the cone, the title reads the live 5-day value, its date and its band", () => {
  const asOf = "2026-10-23";
  const window5 = lab.vol_cone.windows.find((row) => row.window === 5);
  const f = m.format;
  // A value inside each band, and one past either end, from the fixture's own percentiles.
  const values = {
    "below the 5th percentile": window5.p5 / 2,
    "in the 5th–25th percentile band": (window5.p5 + window5.p25) / 2,
    "in the 25th–50th percentile band": (window5.p25 + window5.p50) / 2,
    "in the 50th–75th percentile band": (window5.p50 + window5.p75) / 2,
    "in the 75th–95th percentile band": (window5.p75 + window5.p95) / 2,
    "above the 95th percentile": window5.p95 * 2,
  };
  for (const [phrase, rv5] of Object.entries(values)) {
    const payload = withLiveOn(asOf, { rv5_pct: rv5 });
    const title = m.assertions.coneAssertion(lab.vol_cone, payload.vol_forecast.live);
    assert.equal(title, `5-day realized vol (${f.fmtPct(rv5)}, ${f.fmtDate(asOf, { year: false })}) is ${phrase} of its history`);
    assert.ok(title.length <= 90, title);
    const { html, errors } = render(payload);
    assert.deepEqual(errors, []);
    assert.equal(titleOf(html, "cone"), title, "the panel shows the same title");
  }
  // The lab's date when data.json is not newer; no live block at all reads the lab's values too.
  const labTitle = m.assertions.coneAssertion(lab.vol_cone, data.vol_forecast.live);
  assert.ok(labTitle.startsWith(`On ${f.fmtDate(lab.vol_cone.as_of, { year: false })}, `), labTitle);
  assert.equal(m.assertions.coneAssertion(lab.vol_cone, null), labTitle);
  const older = withLiveOn("2026-10-12");
  assert.equal(m.assertions.coneAssertion(lab.vol_cone, older.vol_forecast.live), labTitle, "an older data.json never replaces the lab's values");
});

test("the cone's meta names both dates and the day gap, and every fact carries its date", () => {
  const asOf = "2026-10-23";
  const payload = withLiveOn(asOf);
  const { html } = render(payload);
  const f = m.format;
  const meta = metaOf(html, "cone");
  const gap = days(lab.vol_cone.as_of, asOf);
  assert.ok(meta.includes(`cone: benchmark lab, ${f.fmtDate(lab.vol_cone.since)} to ${f.fmtDate(lab.vol_cone.as_of)}`), meta);
  assert.ok(meta.includes(`marks: data.json, ${f.fmtDate(asOf)} close`), meta);
  assert.ok(meta.includes(`cone ${gap} days older than the forecast`), meta);
  const body = visibleText(section(html, "cone"));
  const live = payload.vol_forecast.live;
  for (const [window, field] of [[5, "rv5_pct"], [22, "rv22_pct"], [66, "rv66_pct"]]) {
    assert.ok(body.includes(`Realized ${window}d, ${f.fmtDate(asOf, { year: false })} ${f.fmtPct(live.inputs[field])}`), `${window}d fact`);
  }
  assert.ok(body.includes(`OVX, ${f.fmtDate(live.inputs.ovx_session, { year: false })} close ${f.fmtNum(live.implied_vol_ovx_pct, 1)}`), "OVX fact");
  assert.ok(body.includes(`realized, ${f.fmtDate(lab.vol_cone.as_of, { year: false })}`), "the lab's older marks keep their own date");
  // Same date: no gap is stated and no mark is called older.
  const same = visibleText(section(render(data).html, "cone"));
  assert.ok(!/older than the/.test(same));
  assert.ok(!same.includes("The hero's OVX is the previous session's close"));
  assert.ok(!visibleText(render(payload).html).includes("The hero's OVX is the previous session's close"));
});

test("CONE and VRP span the full width", () => {
  const { html } = render(data);
  assert.match(html, /<section id="cone" class="panel span-full"/);
  assert.match(html, /<section id="vrp" class="panel span-full"/);
  assert.match(section(html, "cone"), /class="split split--wide cone-split"/);
  assert.match(section(html, "vrp"), /class="split split--3 /);
});

// ------------------------------------------------------------------------------------------- CALB
test("CALB: the weekly-grid MZ slope reproduces the production slope, and both samples are shown", () => {
  const weekly = cone.mzWeekly(lab.series);
  const production = data.vol_forecast.validation.har_mincer_zarnowitz.slope;
  assert.ok(Math.abs(weekly.slope - production) < 0.01, `${weekly.slope} vs ${production}`);
  assert.equal(weekly.n, lab.series.date.filter((_, i) => lab.series.actual[i] != null && lab.series.har_iv[i] != null).length);
  const every = m.research.leaderboardRows(lab).find((row) => row.key === champion).mzSlope;
  const f = m.format;
  const body = visibleText(section(render(data).html, "calb"));
  assert.ok(body.includes(`Same forecasts, different samples: weekly grid ${f.fmtNum(weekly.slope, 2)}, every out-of-sample day ${f.fmtNum(every, 2)}.`), "caption");
  assert.ok(body.includes(`MZ slope, weekly grid ${f.fmtNum(weekly.slope, 2)}`));
  assert.ok(body.includes(`lab series, ${f.fmtInt(weekly.n)} wk; production ${f.fmtNum(production, 2)}`));
  assert.ok(body.includes(`MZ slope, every day ${f.fmtNum(every, 2)}`));
  assert.equal(cone.mzWeekly({ actual: [1, 2], har_iv: [1, 2] }), null, "too few weeks");
  assert.equal(cone.mzWeekly(null), null);
});

test("CALB: coverage by OVX tercile equals an independent count, with n and the OVX range", () => {
  const s = lab.series;
  const weeks = s.date.map((_, i) => ({ ovx: s.ovx[i], actual: s.actual[i], lo: s.lo80[i], hi: s.hi80[i] }))
    .filter((w) => [w.ovx, w.actual, w.lo, w.hi].every((v) => typeof v === "number"));
  const sorted = weeks.map((w) => w.ovx).sort((a, b) => a - b);
  const cut = (q) => sorted[Math.ceil(q * sorted.length) - 1];
  const [c1, c2] = [cut(1 / 3), cut(2 / 3)];
  const expected = [
    weeks.filter((w) => w.ovx <= c1),
    weeks.filter((w) => w.ovx > c1 && w.ovx <= c2),
    weeks.filter((w) => w.ovx > c2),
  ].map((group) => {
    const inside = group.filter((w) => w.actual >= w.lo && w.actual <= w.hi).length;
    return { n: group.length, covered: inside, low: Math.min(...group.map((w) => w.ovx)), high: Math.max(...group.map((w) => w.ovx)) };
  });
  const terciles = cone.coverageByOvxTercile(s);
  assert.equal(terciles.length, 3);
  terciles.forEach((row, i) => {
    assert.equal(row.n, expected[i].n);
    assert.equal(row.covered, expected[i].covered);
    assert.equal(row.ovxLow, expected[i].low);
    assert.equal(row.ovxHigh, expected[i].high);
    assert.ok(Math.abs(row.coverage - (100 * expected[i].covered) / expected[i].n) < 1e-9);
  });
  assert.equal(terciles.reduce((sum, row) => sum + row.n, 0), weeks.length);
  const f = m.format;
  const body = visibleText(section(render(data).html, "calb"));
  for (const row of terciles) {
    assert.ok(body.includes(`OVX ${f.fmtNum(row.ovxLow, 1)}–${f.fmtNum(row.ovxHigh, 1)} ${f.fmtPct(row.coverage)} of ${f.fmtInt(row.n)} wk`), "regime fact");
  }
  assert.deepEqual(cone.coverageByOvxTercile(null), []);
});

test("CALB keeps the mechanism in the notes and the by-construction caveat in view", () => {
  const html = section(render(data).html, "calb");
  const caption = visibleText(html.match(/<p class="panel-caption">([\s\S]*?)<\/p>/)?.[1] ?? "");
  assert.ok(caption.startsWith(`Coverage near ${m.format.fmtNum(lab.conformal[champion].target_coverage_pct, 0)}% overall is by construction`), caption);
  const notes = visibleText(html.match(/<details class="disclosure panel-notes">([\s\S]*?)<\/details>/)?.[1] ?? "");
  assert.ok(notes.includes("delayed feedback") && notes.includes("Mincer-Zarnowitz regresses realized on forecast variance"), notes);
});

// ------------------------------------------------------------------------------------------- RISK
test("RISK: the caption's breach counts are the backtest's Kupiec counts at both levels", () => {
  const grid0 = lab.var_backtest.grid0;
  const at = (level) => grid0.methods.find((row) => row.key === `${champion}:fhs` && row.level === level);
  const html = section(render(data).html, "risk");
  // (visibleText puts a space at each tag edge, e.g. after "(" before a glossary term: undo that.)
  const caption = visibleText(html.match(/<p class="panel-caption">([\s\S]*?)<\/p>/)?.[1] ?? "").replace(/\(\s+/g, "(");
  assert.ok(caption.startsWith(`On grid 0, ${at(0.01).kupiec.exceedances} of ${at(0.01).kupiec.n} weeks breached the 99% VaR `
    + `and ${at(0.05).kupiec.exceedances} the 95% (Kupiec counts`), caption);
  assert.ok(!caption.includes("rounded series"));
  // The record row of the 99% table shows the same count.
  const recordRow = html.match(/<tr class="is-record">([\s\S]*?)<\/tr>/)?.[1] ?? "";
  const recordText = visibleText(recordRow);
  assert.ok(recordText.includes(` ${at(0.01).kupiec.exceedances} / `), recordText);
});

test("RISK: a last-250-weeks column, 3-decimal Kupiec p-values, and pass meters that are images", () => {
  const html = section(render(data).html, "risk");
  assert.match(html, /<th scope="col"[^>]*><button type="button" class="sort-button"><span>Last 250 wk, Basel zone<\/span>/);
  const rows = m.research.varRows(lab).filter((row) => row.level === 0.01);
  const body = html.match(/<tbody[^>]*>([\s\S]*?)<\/tbody>/)?.[1] ?? "";
  const cells = [...body.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map((row) => [...row[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((cell) => visibleText(cell[1]).trim()));
  assert.equal(cells.length, rows.length);
  // The four test meters come right after the grid-0 breach count, so a tablet that scrolls the table
  // sideways starts on the verdicts; the last-250-weeks count, the grid range and grid-0 Kupiec p close the row.
  const head = html.match(/<thead[\s\S]*?<\/thead>/)?.[0] ?? "";
  const labels = [...head.matchAll(/<th scope="col"[^>]*>([\s\S]*?)<\/th>/g)]
    .map((match) => visibleText(match[1].replace(/<span class="label-short"[\s\S]*?<\/span>/, "")).trim().replace(/\s*[↕↑↓]$/, ""));
  assert.deepEqual(labels, ["Method", "Breaches", "Kupiec", "Independence", "Cond. cov.", "DQ", "Last 250 wk, Basel zone", "All grids", "Kupiec p"]);
  for (const row of cells) assert.match(row[7], /^(\d\.\d{3}|<\s0\.001)$/, `Kupiec p ${row[7]}`);
  for (const level of [0.01, 0.05]) {
    for (const row of m.research.varRows(lab).filter((item) => item.level === level)) {
      assert.match(m.format.fmtPValue(row.kupiecP, { digits: 3 }), /^\d\.\d{3,4}$|^<\s0\.001$/);
    }
  }
  const grid0 = lab.var_backtest.grid0.methods;
  const record = grid0.find((row) => row.key === `${champion}:fhs` && row.level === 0.01);
  const recordCells = cells[rows.findIndex((row) => row.isRecord && row.quantile === "fhs")];
  assert.ok(recordCells[5].startsWith(`${record.exceedances_last_250} / `), recordCells[5]);
  assert.match(html, /<span class="pass-meter[^"]*" role="img" aria-label="passes on \d of \d grids">/);
  // The range presets are figure controls; the vol-targeting study is a disclosure headed by its claim.
  assert.match(html, /<div class="figure-controls"><div class="segmented" role="group" aria-label="VaR chart range">/);
  assert.match(html, /<details class="disclosure study-disclosure"><summary class="disclosure-summary"><h3 class="study-title">Sizing on the forecast held/);
  // The Basel zone shows only when the artifact gives one.
  assert.ok(recordCells[5].endsWith(record.basel_zone_last_250), recordCells[5]);
  const unzoned = structuredClone(research);
  for (const row of unzoned.vol_lab.var_backtest.grid0.methods) row.basel_zone_last_250 = null;
  const plain = section(render(data, unzoned).html, "risk");
  assert.ok(!visibleText(plain).includes("Basel") && !/class="zone-note"/.test(plain), "no zone, no Basel column note");
  assert.match(plain, /<span>Last 250 wk<\/span>/);
});

// -------------------------------------------------------------------------------------------- VRP
test("VRP prints its interval with thousands separators and keeps the method in the notes", () => {
  const vrp = data.vol_forecast.economic.variance_risk_premium;
  const [lo, hi] = vrp.mean_premium_variance_ci95;
  assert.ok(Math.abs(lo) >= 1000, "the fixture's interval has a bound of a thousand or more");
  const f = m.format;
  const ci = `95% CI ${f.fmtSigned(lo, 1, "", { group: true })} to ${f.fmtSigned(hi, 1, "", { group: true })}`;
  assert.match(ci, /\d,\d{3}\.\d/);
  const html = section(render(data).html, "vrp");
  const body = visibleText(html);
  assert.ok(body.includes(ci), ci);
  assert.ok(!body.includes(f.fmtSigned(lo, 1)), "never without the separator");
  const notes = visibleText(html.match(/<details class="disclosure panel-notes">([\s\S]*?)<\/details>/)?.[1] ?? "");
  assert.ok(notes.includes("Jensen's inequality") && notes.includes("Cboe Crude Oil ETF Volatility Index"), notes);
  const caption = visibleText(html.match(/<p class="panel-caption">([\s\S]*?)<\/p>/)?.[1] ?? "").trim();
  assert.equal(caption.split(/(?<=\.)\s/).length, 1, `one sentence: ${caption}`);
  const reading = visibleText(html.match(/<div class="split-reading">([\s\S]*?)<\/div>/)?.[1] ?? "");
  assert.ok(reading.includes(`OVX was above realized vol in ${f.fmtPct(vrp.share_positive_pct)} of periods`), reading);
  assert.ok(reading.includes(`(${ci})`), "the reading quotes the same interval");
});

test("RISK: the caption gives the Kupiec counts, and says so when the rounded chart series colours fewer", async () => {
  const { riskCaption, drawnBreachCounts } = await m.load("/src/panels/RiskPanel.jsx");
  const counts = { n: 875, e99: 14, e95: 46, quantileLabel: "Filtered hist. sim." };
  // A tie after rounding: ret equals -var95, so the chart does not colour it.
  const series = [
    { ret: -12.1, var95: 10.9, var99: 20.6 },
    { ret: -10.9, var95: 10.9, var99: 20.6 },
    { ret: 3.2, var95: 10.9, var99: 20.6 },
    { ret: -25.0, var95: 10.9, var99: 20.6 },
    { ret: -30.0, var95: null, var99: null },
  ];
  assert.deepEqual(drawnBreachCounts(series), { n: 4, e99: 1, e95: 2 });
  const same = riskCaption(counts, { n: 875, e99: 14, e95: 46 }, "HAR-IV");
  assert.equal(same, "On grid 0, 14 of 875 weeks breached the 99% VaR and 46 the 95% (Kupiec counts, HAR-IV filtered hist. sim.).");
  const differ = riskCaption(counts, { n: 875, e99: 14, e95: 45 }, "HAR-IV");
  assert.ok(differ.startsWith(same), differ);
  assert.ok(differ.includes("rounded to 0.1 pt, so it colours 14 and 45"), differ);
  assert.equal(riskCaption(null, null, "HAR-IV"), null);
});
