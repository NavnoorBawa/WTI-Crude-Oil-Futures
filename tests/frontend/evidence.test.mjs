// CALB, RISK, CONE and VRP: current marks on the cone with their own date, reconciled samples, and
// captions that match their tables. Every expected value is computed here from the fixtures (or from
// a copy with one field changed), never restated, so the tests follow the payload.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, visibleText } from "./load.mjs";
import { committedLab, committedLedger, withRegimes } from "./splice.mjs";

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
    // Only the recorded two-decimal input, no one-decimal field: it prints at its two decimals.
    const payload = withLiveOn(asOf, { rv5_pct: Number(rv5.toFixed(2)) });
    delete payload.vol_forecast.live.current_realized_vol_5d_annualized_pct;
    const title = m.assertions.coneAssertion(lab.vol_cone, payload.vol_forecast.live);
    assert.equal(title, `5-day realized vol (${f.fmtPct(rv5, 2)}, ${f.fmtDate(asOf, { year: false })}) is ${phrase} of its history`);
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
  // The 5-day value is the live block's one-decimal field; the 22- and 66-day ones are the stored
  // two-decimal inputs, printed at two decimals.
  assert.ok(body.includes(`Realized 5d, ${f.fmtDate(asOf, { year: false })} ${f.fmtPct(live.current_realized_vol_5d_annualized_pct)}`), "5d fact");
  for (const [window, field] of [[22, "rv22_pct"], [66, "rv66_pct"]]) {
    assert.ok(body.includes(`Realized ${window}d, ${f.fmtDate(asOf, { year: false })} ${f.fmtPct(live.inputs[field], 2)}`), `${window}d fact`);
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

test("CALB: coverage by regime at the decision is read from the artifact, and no regime is named by an OVX range", () => {
  const spliced = withRegimes(research);
  const regimesLib = m.regimes;
  const art = committedLab();
  const c = art.conformal[champion];
  const html = section(render(data, spliced).html, "calb");
  const body = visibleText(html);
  const f = m.format;
  assert.ok(body.includes(`Coverage by regime at the decision, ${f.fmtInt(c.n_days)} days with a band`), body);
  for (const [split, levels] of Object.entries({ ovx_tercile: ["low", "mid", "high"], ovx_rv_gap: ["at_or_below", "above"],
    rv5_tercile: ["low", "mid", "high"], target_spans_roll: ["no", "yes"] })) {
    assert.equal(levels.reduce((sum, level) => sum + c.n_days_by_regime[split][level], 0), c.n_days, split);
    for (const level of levels) {
      const label = regimesLib.REGIME_SPLITS.find((s) => s.key === split).levels[level];
      assert.ok(body.includes(`${label} ${f.fmtPct(c.coverage_by_regime_pct[split][level])} of ${f.fmtInt(c.n_days_by_regime[split][level])}`),
        `${split} ${level}`);
    }
  }
  // The statement, rebuilt here from the artifact and the ledger.
  const cc = c.conditional_coverage;
  const ledger = committedLedger();
  const q = ledger.rows.find((row) => row.id === `band_regime_ovx_tercile_${champion}`).q_value_bh;
  const ovx = Object.values(c.coverage_by_regime_pct.ovx_tercile);
  const pq = (v) => { const text = f.fmtPValue(v, { digits: 3 }); return /^[<>]/.test(text) ? `p ${text}` : `p = ${text}`; };
  const expected = `Across OVX thirds at the decision the model of record's band covered ${f.fmtPct(Math.min(...ovx))} to `
    + `${f.fmtPct(Math.max(...ovx))}; the test of equal coverage across OVX thirds is inconclusive (${pq(cc.ovx_tercile.p_value)}; `
    + `q = ${f.fmtPValue(q, { digits: 3 })} after the ledger's adjustment; ${pq(cc.ovx_tercile.robustness.hac_lags_20.p_value)} with 20 HAC lags). `
    + `No dependence detected across OVX/RV5 halves, RV5 thirds or roll windows (${pq(cc.ovx_rv_gap.p_value)}, `
    + `${pq(cc.rv5_tercile.p_value)}, ${pq(cc.target_spans_roll.p_value)}). `
    + "A dependence found here is reported, never used to change the band.";
  assert.ok(body.includes(expected.replace(/\u00a0/g, " ")), body);
  assert.doesNotMatch(body, /OVX \d+(\.\d)?–\d/, "a third's OVX range moves over time, so no fixed range is shown");
  assert.doesNotMatch(body, /hides|regime-dependent|evidence of|suggests|calibrated in every|premium/i);
  // Without the blocks (the fixture predates them) the section is absent.
  assert.ok(!visibleText(section(render(data).html, "calb")).includes("Coverage by regime"));
});

test("regime verdicts follow the house words at every p and never upgrade on robustness", () => {
  const verdict = (p, p20, q = 0.3) => m.regimes.regimeVerdict({ short: "OVX thirds", test: { p, p20, p40: p20, pAll: p20 } }, { q });
  assert.match(verdict(0.03, 0.04), /is significant at 5% \(p.=.0\.030; q.=.0\.300 after the ledger's adjustment\)$/);
  assert.match(verdict(0.03, 0.07), /is significant at 5% .*; p.=.0\.070 with 20 HAC lags\)$/);
  assert.match(verdict(0.07, 0.07), /is inconclusive \(p.=.0\.070; q.=.0\.300 after the ledger's adjustment\)$/);
  assert.match(verdict(0.07, 0.2), /is inconclusive .*; p.=.0\.200 with 20 HAC lags\)$/);
  assert.equal(verdict(0.2, 0.03), null, "a robustness p never turns 'not detected' into a verdict");
  // The thresholds are strict: p = 0.05 is inconclusive, p = 0.10 detects nothing.
  assert.match(verdict(0.05, 0.05), /is inconclusive/);
  assert.equal(verdict(0.1, 0.1), null);
  // A robustness p in a stronger band is reported too, without changing the verdict.
  assert.match(verdict(0.07, 0.03), /is inconclusive .*; p.=.0\.030 with 20 HAC lags\)$/);
  // The all-regimes form is named when only it lands in another band.
  const allCells = m.regimes.regimeVerdict({ short: "OVX thirds", test: { p: 0.07, p20: 0.08, p40: 0.09, pAll: 0.2 } }, { q: 0.3 });
  assert.match(allCells, /; p.=.0\.200 with every regime tested against the target\)$/);
  // A significant split says whether it survives the ledger's Benjamini-Hochberg adjustment.
  const flagged = (survives) => m.regimes.regimeVerdict({ short: "OVX thirds", test: { p: 0.03, p20: 0.04, p40: 0.04, pAll: 0.04 } },
    { q: 0.21, survives_bh: survives });
  assert.match(flagged(false), /is significant at 5% \(p.=.0\.030; it does not survive the ledger's Benjamini-Hochberg adjustment, q.=.0\.210\)$/);
  assert.match(flagged(true), /it survives the ledger's Benjamini-Hochberg adjustment, q.=.0\.210\)$/);
  assert.equal(m.regimes.regimeCoverage(null, "har_iv").length, 0);
  assert.equal(m.regimes.regimeCoverageAssertion(research.vol_lab, champion, null), null);
});

test("CALB keeps the mechanism in the notes and the by-construction caveat in view", () => {
  const html = section(render(data).html, "calb");
  const caption = visibleText(html.match(/<p class="panel-caption">([\s\S]*?)<\/p>/)?.[1] ?? "");
  assert.ok(caption.startsWith(`Coverage near ${m.format.fmtNum(lab.conformal[champion].target_coverage_pct, 0)}% overall is by construction`), caption);
  const notes = visibleText(html.match(/<details class="disclosure panel-notes">([\s\S]*?)<\/details>/)?.[1] ?? "");
  assert.ok(notes.includes("delayed feedback") && notes.includes("Mincer-Zarnowitz regresses realized on forecast variance"), notes);
});

test("CALB's coverage-by-year heading names the sample it plots: the days with a band, and their years", () => {
  const html = section(render(data).html, "calb");
  const conformal = lab.conformal[champion];
  const years = Object.keys(conformal.coverage_by_year_pct).map(Number).sort((x, y) => x - y);
  const sample = `${m.format.fmtInt(conformal.n_days)} out-of-sample days with a band (${years[0]}–${years.at(-1)})`;
  const headings = [...html.matchAll(/<h3 class="figure-title">([\s\S]*?)<\/h3>/g)].map((match) => visibleText(match[1]).trim());
  assert.ok(headings.includes(`Coverage by year, ${sample}`), headings.join(" | "));
  assert.ok(conformal.n_days < lab.sample.n_days, "the band starts later than the first out-of-sample day");
  assert.ok(!headings.some((text) => text.startsWith("Coverage by year, every out-of-sample day")), "not the full out-of-sample sample");
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

test("RISK: the caption gives the Kupiec counts; the method notes say when the rounded chart series colours fewer", async () => {
  const { riskCaption, riskCaptionCounts, roundingNote } = await m.load("/src/panels/RiskPanel.jsx");
  // The rule VarChart colours by and the panel counts by, from one module.
  const { breachOf, drawnBreachCounts } = await m.load("/src/lib/var.js");
  const counts = { n: 875, e99: 14, e95: 46, quantileLabel: "Filtered hist. sim." };
  // A tie after rounding: ret equals -var95, so the chart does not colour it.
  const series = [
    { ret: -12.1, var95: 10.9, var99: 20.6 },
    { ret: -10.9, var95: 10.9, var99: 20.6 },
    { ret: 3.2, var95: 10.9, var99: 20.6 },
    { ret: -25.0, var95: 10.9, var99: 20.6 },
    { ret: -30.0, var95: null, var99: null },
  ];
  assert.deepEqual(series.map(breachOf), ["var95", "ok", "ok", "var99", "none"]);
  assert.deepEqual(drawnBreachCounts(series), { n: 4, e99: 1, e95: 2 });
  const caption = riskCaption(counts, "HAR-IV");
  assert.equal(caption, "On grid 0, 14 of 875 weeks breached the 99% VaR and 46 the 95% (Kupiec counts, HAR-IV filtered hist. sim.).");
  assert.equal(riskCaption(null, "HAR-IV"), null);
  assert.equal(roundingNote(counts, { n: 875, e99: 14, e95: 46 }), null, "the chart agrees: no note");
  const note = roundingNote(counts, { n: 875, e99: 14, e95: 45 });
  assert.ok(note.includes("rounded to 0.1 pt, so it colours 14 weeks below the 99% line and 45 below the 95% line"), note);
  assert.ok(note.includes("Kupiec counts are 14 and 46"), note);
  assert.equal(roundingNote(null, null), null);

  // On the page: the caption is the Kupiec sentence alone; the notes carry the rounding sentence
  // exactly when the fixture's rounded series colours other counts.
  const html = section(render(data).html, "risk");
  const shown = visibleText(html.match(/<p class="panel-caption">([\s\S]*?)<\/p>/)?.[1] ?? "").trim();
  assert.ok(shown.startsWith("On grid 0, "), shown);
  assert.doesNotMatch(shown, /rounded/, shown);
  const expected = roundingNote(riskCaptionCounts(m.research.varRows(lab)), drawnBreachCounts(m.research.varSeries(lab)));
  const notes = visibleText(html.match(/<div class="disclosure-body">([\s\S]*?)<\/div>/)?.[1] ?? "");
  assert.equal(notes.includes("rounded to 0.1 pt"), expected != null, notes);
});

test("CALB's regime tables hold the artifact's values for each model, and the ledger's q for each model's row", () => {
  const spliced = withRegimes(research).vol_lab;
  const art = committedLab();
  const ledger = committedLedger();
  const challenger = art.config.challenger;
  const rows = m.regimes.regimeCoverageRows(spliced, champion, challenger);
  const splits = Object.entries(art.regimes.variables).map(([key, variable]) => [key, variable.levels]);
  assert.deepEqual(rows.map((row) => row.id), splits.flatMap(([key, levels]) => levels.map((level) => `${key}:${level}`)));
  for (const row of rows) {
    const [split, level] = row.id.split(":");
    const ours = art.conformal[champion];
    const cell = ours.conditional_coverage[split].cells.find((c) => c.regime === level);
    assert.equal(row.n, ours.n_days_by_regime[split][level], row.id);
    assert.equal(row.inside, cell.inside, row.id);
    assert.equal(row.coverage, ours.coverage_by_regime_pct[split][level], row.id);
    assert.equal(row.above, ours.miss_above_by_regime_pct[split][level], row.id);
    assert.equal(row.below, ours.miss_below_by_regime_pct[split][level], row.id);
    assert.equal(row.score, ours.interval_score_by_regime[split][level], row.id);
    assert.equal(row.logScore, ours.log_interval_score_by_regime[split][level], row.id);
    assert.equal(row.hacP, cell.hac_p, `${row.id}: the regime's own p, not its z`);
    assert.deepEqual([row.binomial.min, row.binomial.max, row.binomial.n],
      [Math.min(...cell.binomial_p_by_grid), Math.max(...cell.binomial_p_by_grid), cell.binomial_p_by_grid.length], row.id);
    assert.equal(row.challenger, art.conformal[challenger].coverage_by_regime_pct[split][level], `${row.id}: the challenger's own coverage`);
  }
  const bundled = m.research.researchLedger(withRegimes(research));
  const tests = m.regimes.regimeTestRows(spliced, bundled, [[champion, "record"], [challenger, "challenger"]]);
  assert.equal(tests.length, 2 * splits.length);
  for (const row of tests) {
    const [model, split] = row.id.split(":");
    const cc = art.conformal[model].conditional_coverage[split];
    assert.deepEqual([row.test.wald, row.test.df, row.test.p, row.test.lags], [cc.wald, cc.df, cc.p_value, cc.hac_lags], row.id);
    assert.deepEqual([row.test.p20, row.test.p40, row.test.pAll],
      [cc.robustness.hac_lags_20.p_value, cc.robustness.hac_lags_40.p_value, cc.robustness.all_cells.p_value], row.id);
    assert.equal(row.q, ledger.rows.find((r) => r.id === `band_regime_${split}_${model}`).q_value_bh, `${row.id}: that model's own q`);
    assert.equal(row.model, model === champion ? "record" : "challenger");
  }
});

test("CALB's notes say what each regime's p tests, count the per-grid binomials, and read every setting from the payload", () => {
  const spliced = withRegimes(research);
  spliced.vol_lab = structuredClone(spliced.vol_lab);
  spliced.vol_lab.regimes.variables.ovx_rv_gap.window_sessions = 300;
  spliced.vol_lab.regimes.test.hac_lags = 12;
  const notes = visibleText(section(render(data, spliced).html, "calb").match(/<details class="disclosure panel-notes">([\s\S]*?)<\/details>/)?.[1] ?? "");
  const art = committedLab();
  const cc = art.conformal[champion].conditional_coverage;
  const tests = Object.values(cc).reduce((sum, split) => sum + split.cells.reduce((n, cell) => n + cell.binomial_p_by_grid.length, 0), 0);
  const grids = Math.max(...Object.values(cc).flatMap((split) => split.cells.map((cell) => cell.binomial_p_by_grid.length)));
  assert.ok(notes.includes("over the previous 300 sessions"), "the gap's window is read");
  assert.match(notes, /\(\s*HAC Wald, 12 lags\); a single regime's coverage carries no verdict\./, "the lags are read");
  const target = m.format.fmtInt(art.conformal[champion].target_coverage_pct);
  assert.ok(notes.includes(`Each regime's own HAC p tests that regime's coverage against the ${target}% target, and the binomial range `
    + `gives the lowest and highest exact binomial p of the regime's ${grids} non-overlapping sampling grids, each treating its weeks as `
    + `independent: ${tests} such tests per model, so values below 0.05 are expected by chance. Neither carries a verdict.`), notes);
  assert.ok(notes.includes("so it does not compare one regime with another; the log interval score scores the same band on the log scale"), notes);
  assert.doesNotMatch(notes, /compares the two models within a regime/);
  // Without binomials, the HAC p's sentence stands alone.
  assert.equal(m.regimes.regimeCellCaveat([{ binomial: null }], 80), "Each regime's own HAC p tests that regime's coverage against the 80% target; it carries no verdict.");
  assert.equal(m.regimes.regimeCellCaveat([], 80), null);
});
