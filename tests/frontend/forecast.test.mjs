// FCST: the band drawn is the validated production band wherever one exists, the caption reads as
// sentences with computed counts, the notes print the expected realized/forecast ratio beside the
// observed one, and the per-week errors (miss, log error, QLIKE) agree with the validation's loss.
// Rendered against the fixtures.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, visibleText } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let errors;
let panel;
before(async () => {
  m = await loadDashboard();
  errors = await m.load("/src/lib/forecastErrors.js");
  panel = await m.load("/src/panels/ForecastPanel.jsx");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");

const fcstSection = (dataPayload, researchPayload) => {
  const html = renderToStaticMarkup(createElement(m.dashboard.default, {
    data: dataPayload,
    research: researchPayload,
    researchStatus: researchPayload ? "ready" : "unavailable",
    quote: m.quote.resolveQuote(null, dataPayload),
  }));
  return html.match(/<section id="fcst"[\s\S]*?<\/section>/)[0];
};
const caption = (section) => visibleText(section.match(/<p class="panel-caption">[\s\S]*?<\/p>/)[0]).trim();
const notes = (section) => visibleText(section.match(/<details class="disclosure panel-notes">[\s\S]*?<\/details>/)?.[0] ?? "").trim();

test("forecastRows prefers the production band on weeks both pipelines cover", () => {
  const recent = data.vol_forecast.recent_oos;
  const series = research.vol_lab.series;
  const rows = m.research.forecastRows(research.vol_lab, recent);
  const byDate = new Map(rows.map((row) => [row.date, row]));
  const labIndex = new Map(series.date.map((date, i) => [date, i]));
  let overlapping = 0;
  recent.date.forEach((date, i) => {
    const row = byDate.get(date);
    if (recent.lo80[i] == null || recent.hi80[i] == null) return;
    assert.deepEqual([row.lo, row.hi, row.source], [recent.lo80[i], recent.hi80[i], "live"], date);
    const j = labIndex.get(date);
    if (j == null) return;
    overlapping += 1;
    assert.equal(row.forecast, series.har_iv[j], `${date}: the forecast is the lab's`);
    assert.equal(row.actual, series.actual[j], `${date}: realized is the lab's`);
    assert.equal(row.ovx, series.ovx[j], `${date}: OVX is the lab's`);
  });
  assert.ok(overlapping > 0, "the fixtures overlap");
  // Weeks only the lab covers keep the lab's band.
  const first = rows[0];
  assert.equal(first.source, "lab");
  assert.equal(first.lo, series.lo80[0]);
});

test("bandCoverage counts each pipeline's weeks and their span", () => {
  const rows = m.research.forecastRows(research.vol_lab, data.vol_forecast.recent_oos);
  const band = m.research.bandCoverage(rows);
  for (const source of ["lab", "live"]) {
    const weeks = rows.filter((row) => row.source === source && row.actual != null && row.lo != null && row.hi != null);
    const part = band.bySource[source];
    assert.equal(part.n, weeks.length, source);
    assert.equal(part.covered, weeks.filter((row) => row.actual >= row.lo && row.actual <= row.hi).length, source);
    assert.deepEqual(part.span, weeks.length ? { start: weeks[0].date, end: weeks.at(-1).date } : null, source);
  }
  assert.equal(band.n, band.bySource.lab.n + band.bySource.live.n);
  assert.equal(band.covered, band.bySource.lab.covered + band.bySource.live.covered);
});

test("the caption is two sentences with computed counts, with and without research.json", () => {
  for (const researchPayload of [research, null]) {
    const section = fcstSection(data, researchPayload);
    const text = caption(section);
    const rows = m.research.forecastRows(researchPayload?.vol_lab ?? null, data.vol_forecast.recent_oos);
    const { live } = m.research.bandCoverage(rows).bySource;
    const horizon = researchPayload?.vol_lab.config.horizon_sessions;
    assert.equal(text, `Each point is a forecast date; realized vol is measured over the following ${horizon != null ? `${horizon} sessions` : "week"}. `
      + `Bright points are weeks whose realized vol fell outside the 80% band; ${live.covered} of the last ${live.n} production weeks fell inside.`);
    assert.doesNotMatch(text, /lab; production|1 week 80%/);
    assert.ok(text.split(/\s+/).length <= 45, `${text.split(/\s+/).length} words`);
  }
});

test("the notes name both bands, print the expected ratio beside the observed one, and the loss concentration", () => {
  const section = fcstSection(data, research);
  const text = notes(section).replace(/\s+;/g, ";").replace(/\s+\./g, ".");
  const v = data.vol_forecast.validation;
  const sd = data.vol_forecast.live.residual_sd;
  const expected = errors.expectedRatio(sd);
  assert.ok(Math.abs(expected - Math.exp(-(sd ** 2) / 2)) < 1e-12);
  assert.ok(text.includes(`realized averaged ${v.har_mean_ratio_actual_to_forecast.toFixed(2)}× the forecast; about ${expected.toFixed(2)} is expected`
    + ` for a variance-consistent forecast with log-residual sd ${sd.toFixed(2)}`), text);
  const rows = m.research.forecastRows(research.vol_lab, data.vol_forecast.recent_oos);
  const { lab, live } = m.research.bandCoverage(rows).bySource;
  assert.ok(text.includes(`Band: the production 80% interval for ${live.n} weeks (`), text);
  assert.ok(text.includes(`realized inside ${live.covered}) and the benchmark lab's before that for ${lab.n} weeks (`), text);
  assert.ok(text.includes(`Over all ${v.interval_80_n} production validation weeks that band covered ${v.interval_80_coverage_pct.toFixed(1)}%;`
    + ` this snapshot carries it for the latest ${live.n}.`), "the longer production sample is named beside the drawn one");
  const concentration = errors.lossConcentration(errors.withErrors(m.research.forecastRows(research.vol_lab, null)));
  assert.ok(text.includes(`The worst 1% of benchmark-lab weeks (${concentration.k} of ${concentration.n}) carry ${Math.round(concentration.shareOfLoss * 100)}% of their total weekly QLIKE`), text);
  assert.ok(text.includes("logarithmic"));
  assert.match(section, /<p class="panel-meta sep-list">[\s\S]*<span>log scale<\/span>/);
});

test("weekly QLIKE recomputed from the lab series matches the validation's within 0.005", () => {
  const series = research.vol_lab.series;
  const losses = series.actual.map((a, i) => errors.weekQlike(a, series.har_iv[i])).filter((q) => q != null);
  assert.ok(losses.length > 100);
  const mean = losses.reduce((sum, q) => sum + q, 0) / losses.length;
  assert.ok(Math.abs(mean - data.vol_forecast.validation.har_qlike) < 0.005, `${mean} vs ${data.vol_forecast.validation.har_qlike}`);
  assert.equal(errors.weekQlike(30, 30), 0, "a perfect forecast loses nothing");
  assert.equal(errors.weekQlike(0, 30), null);
  assert.equal(errors.weekQlike(30, null), null);
});

test("withErrors and lossConcentration match an independent computation", () => {
  const rows = errors.withErrors([
    { date: "2026-01-02", actual: 40, forecast: 20 },
    { date: "2026-01-09", actual: 10, forecast: 20 },
    { date: "2026-01-16", actual: 20, forecast: 20 },
    { date: "2026-01-23", actual: null, forecast: 20 },
  ]);
  assert.deepEqual(rows.map((row) => row.miss), [20, -10, 0, null]);
  assert.ok(Math.abs(rows[0].logError - Math.log(2)) < 1e-12);
  assert.equal(rows[3].qlike, null);
  const q = (r) => r * r - Math.log(r * r) - 1;
  assert.ok(Math.abs(rows[0].qlike - q(2)) < 1e-12 && Math.abs(rows[1].qlike - q(0.5)) < 1e-12);

  const labRows = errors.withErrors(m.research.forecastRows(research.vol_lab, null));
  const result = errors.lossConcentration(labRows);
  const losses = labRows.map((row) => row.qlike).filter((value) => value != null).sort((a, b) => b - a);
  const k = Math.floor(losses.length / 100);
  const share = losses.slice(0, k).reduce((s, v) => s + v, 0) / losses.reduce((s, v) => s + v, 0);
  assert.deepEqual([result.k, result.n], [k, losses.length]);
  assert.ok(Math.abs(result.shareOfLoss - share) < 1e-12);
  assert.equal(result.worst.length, 5);
  assert.ok(result.worst.every((week, i) => i === 0 || week.qlike <= result.worst[i - 1].qlike), "worst first");
  assert.equal(result.worst[0].qlike, losses[0]);
  assert.equal(errors.lossConcentration([]), null);
  assert.equal(errors.lossConcentration(errors.withErrors([{ date: "2026-01-02", actual: 40, forecast: 20 }])).k, 1, "at least one week");
  assert.equal(errors.expectedRatio(null), null);
  assert.equal(errors.expectedRatio(0), 1);
});

test("the table view lists the latest weeks newest first, with miss, log error and QLIKE, and a CSV button", () => {
  const rows = errors.withErrors(m.research.forecastRows(research.vol_lab, data.vol_forecast.recent_oos));
  const html = renderToStaticMarkup(createElement(panel.ForecastTable, { rows, modelName: "HAR-IV" }));
  const headers = [...html.matchAll(/<th scope="col"[^>]*>([\s\S]*?)<\/th>/g)].map((match) => visibleText(match[1]).replace(/[↕↑↓]/g, "").trim());
  for (const label of ["Forecast date", "Realized", "HAR-IV", "80% band", "Band from", "Inside", "Miss (pts)", "Log error", "QLIKE", "OVX"]) {
    assert.ok(headers.includes(label), `${label} in ${headers.join(" | ")}`);
  }
  for (const label of ["Miss (pts)", "Log error", "QLIKE"]) {
    assert.match(html, new RegExp(`aria-sort="none"[^>]*><button type="button" class="sort-button"><span>${label.replace(/[()]/g, "\\$&")}</span>`), `${label} sorts`);
  }
  assert.match(html, /class="csv-button"[^>]*><span class="sr-only">Download <\/span>CSV<\/button>/);
  const bodyRows = [...html.matchAll(/<tr><th scope="row"[^>]*>([^<]*)<\/th>/g)].map((match) => match[1]);
  assert.equal(bodyRows.length, 52);
  assert.equal(bodyRows[0], m.format.fmtDate(rows.at(-1).date), "newest first");
  assert.ok(html.includes(`Show all ${rows.length.toLocaleString("en-US")} weeks`));
  assert.ok(visibleText(html).includes(`Latest 52 of ${rows.length.toLocaleString("en-US")} weeks, newest first`));
  // The CSV gets raw values: the band's two edges (columns hidden on screen) and unrounded errors.
  assert.match(html.match(/<thead[\s\S]*?<\/thead>/)[0], /class="align-left col-csv-only">80% band low</);
  const csv = m.table.csvColumns(panel.forecastTableColumns("HAR-IV"));
  assert.deepEqual(csv.map((column) => column.label), [
    "Forecast date", "Realized", "HAR-IV forecast", "80% band low", "80% band high", "Band from", "Inside",
    "Miss (pts)", "Log error", "QLIKE", "OVX",
  ]);
  const row = rows.find((week) => week.qlike != null && week.lo != null);
  const values = Object.fromEntries(csv.map((column) => [column.label, column.value(row)]));
  assert.deepEqual([values["80% band low"], values["80% band high"], values["Miss (pts)"], values.QLIKE, values["Log error"]],
    [row.lo, row.hi, Number((row.actual - row.forecast).toFixed(6)), row.qlike, Math.log(row.actual / row.forecast)]);
  assert.equal(values.Inside, row.actual >= row.lo && row.actual <= row.hi ? "yes" : "no");
});
