// Pure helpers: formatting, payload selectors, assertion-title builders, table sorting and SVG
// label layout. Statistics are read from the fixtures, never restated here, so the tests follow
// the payload when it is regenerated.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { loadDashboard, readFixture } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
before(async () => { m = await loadDashboard(); });
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");

// ------------------------------------------------------------------------------------ formatting
test("format helpers handle ages, spans, weekdays, ordinals and p-value cells", () => {
  const f = m.format;
  assert.equal(f.fmtAge(14 * 60000), "14m");
  assert.equal(f.fmtAge((2 * 60 + 14) * 60000), "2h 14m");
  assert.equal(f.fmtAge((50 * 60 + 5) * 60000), "2d 2h");
  assert.equal(f.fmtAge(-5), "—");
  assert.equal(f.fmtSpan("2008-05-08", "2026-10-07"), "May 2008 – Oct 2026");
  assert.equal(f.fmtSpan("2008-05-08", null), "May 2008");
  assert.equal(f.fmtSpan(null, null), "—");
  assert.equal(f.addWeekdays("2026-10-16", 1), "2026-10-19");     // Friday -> Monday
  assert.equal(f.addWeekdays("2026-10-20", 5), "2026-10-27");
  assert.equal(f.addWeekdays("nope", 1), null);
  assert.deepEqual([1, 2, 3, 11, 12, 13, 22, 68.3, 91.5].map(f.ordinal), ["1st", "2nd", "3rd", "11th", "12th", "13th", "22nd", "68th", "92nd"]);
  assert.equal(f.fmtPValue(0.0059), "0.006");
  assert.equal(f.fmtPValue(0.0496), "0.0496");
  assert.equal(f.fmtPValue(0.0004), "< 0.001");
  assert.equal(f.fmtPValue(null), "—");
  assert.equal(f.fmtInt(4370), "4,370");
  assert.equal(f.fmtShare(0.9988, 2), "99.88%");
  assert.equal(f.shortHash("2e0c57f697484b466eac5945c755a76c"), "2e0c57f69748");
  assert.equal(f.shortHash("not-a-hash"), "—");
  assert.equal(f.humanizeKey("supply_lost"), "Supply lost");
});

// --------------------------------------------------------------------------------------- tables
test("sortRows is stable and keeps missing values last in both directions", () => {
  const { sortRows, nextSort, compareValues } = m.table;
  const rows = [{ k: "a", v: 3 }, { k: "b", v: null }, { k: "c", v: 1 }, { k: "d", v: 3 }, { k: "e", v: Number.NaN }, { k: "f", v: 2 }];
  const order = (direction) => sortRows(rows, (row) => row.v, direction).map((row) => row.k).join("");
  assert.equal(order("asc"), "cfadbe");
  assert.equal(order("desc"), "adfcbe");
  assert.ok(compareValues("HAR-IV 2", "HAR-IV 10") < 0, "numeric collation");
  assert.deepEqual(nextSort({ key: "q", direction: "asc" }, { key: "q" }), { key: "q", direction: "desc" });
  assert.deepEqual(nextSort({ key: "q", direction: "asc" }, { key: "u", firstDirection: "desc" }), { key: "u", direction: "desc" });
  assert.deepEqual(nextSort(null, { key: "q" }), { key: "q", direction: "asc" });
});

test("DataTable renders a caption, scoped headers and aria-sort on the sorted column", async () => {
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const html = renderToStaticMarkup(createElement(m.table.default, {
    caption: "Test table",
    rowKey: (row) => row.k,
    initialSort: { key: "v", direction: "desc" },
    columns: [
      { key: "k", label: "Key", rowHeader: true, sortable: true },
      { key: "v", label: "Value", align: "right", sortable: true },
      { key: "note", label: "Note" },
    ],
    rows: [{ k: "a", v: 1 }, { k: "b", v: 2 }],
  }));
  assert.match(html, /<caption[^>]*>Test table<\/caption>/);
  assert.match(html, /<th scope="col"[^>]*aria-sort="descending"[^>]*>/);
  assert.match(html, /<th scope="col"[^>]*aria-sort="none"[^>]*>/);
  assert.match(html, /<th scope="row"[^>]*>b<\/th>.*<th scope="row"[^>]*>a<\/th>/s);
  assert.ok(!/Note[^<]*<\/button>/.test(html), "unsortable columns get no sort button");
});

// --------------------------------------------------------------------------------------- theme
test("SVG label helpers fit labels and keep stacked labels apart", () => {
  const { fitLabel, spreadLabels } = m.svg;
  assert.equal(fitLabel("short", 200), "short");
  const fitted = fitLabel("HAR-IV, same-day OVX (v1 timing)", 120);
  assert.ok(fitted.endsWith("…") && fitted.length <= Math.floor(120 / 6.6));
  const spread = spreadLabels([{ y: 50 }, { y: 52 }, { y: 10 }, { y: 51 }], 12);
  assert.deepEqual(spread.map((item) => item.y), [50, 74, 10, 62]);   // input order kept, pushed apart in y order
  assert.match(m.sparkline.sparkPath([1, 2, 3], 10, 10), /^M1\.5,8\.5 L5\.0,5\.0 L8\.5,1\.5$/);
  assert.equal(m.sparkline.sparkPath([1], 10, 10), null);
});

// ------------------------------------------------------------------------------------ selectors
test("leaderboard rows classify information sets and mark the record and challenger", () => {
  const rows = m.research.leaderboardRows(research.vol_lab);
  const board = research.vol_lab.leaderboard;
  assert.equal(rows.length, board.models.length);
  const byKey = new Map(rows.map((row) => [row.key, row]));
  assert.equal(byKey.get(research.vol_lab.config.champion).role, "record");
  assert.equal(byKey.get(research.vol_lab.config.challenger).role, "challenger");
  for (const key of ["persistence", "ewma", "garch", "gjr", "har_rv", "har_rs"]) {
    if (byKey.has(key)) assert.equal(byKey.get(key).info.size, 0, `${key} is return-only`);
  }
  if (byKey.has("raw_ovx")) assert.ok(byKey.get("raw_ovx").info.has("implied"));
  if (byKey.has("har_range")) assert.deepEqual([...byKey.get("har_range").info], ["range"]);
  if (byKey.has("combo")) assert.ok(byKey.get("combo").info.has("implied"), "a combination inherits its members");
  assert.equal(rows.filter((row) => row.inMcs).length, board.mcs_qlike_included.length);
  const record = byKey.get(research.vol_lab.config.champion);
  assert.equal(record.grids.filter(Boolean).length, record.gridsIn);
});

test("mcsSummary and recordVar read the set size, level and the record's FHS VaR", () => {
  const summary = m.research.mcsSummary(research.vol_lab);
  assert.equal(summary.total, research.vol_lab.leaderboard.models.length);
  assert.equal(summary.level, Math.round((1 - research.vol_lab.config.mcs.size) * 100));
  const rows = m.research.varRows(research.vol_lab);
  const var99 = m.research.recordVar(rows, 0.01);
  assert.equal(var99.model, research.vol_lab.config.champion);
  assert.equal(var99.quantile, "fhs");
  const grid0 = research.vol_lab.var_backtest.grid0.methods.find((row) => row.key === `${var99.model}:fhs` && row.level === 0.01);
  assert.equal(var99.exceedances, grid0.kupiec.exceedances);
  assert.equal(m.research.recordVar([], 0.01), null);
  assert.equal(m.research.mcsSummary(null), null);
});

test("forecastRows merges research history with newer live weeks, sorted and de-duplicated", () => {
  const recent = { date: ["2026-10-07", "2026-10-14"], actual: [1, 2], forecast: [3, 4], lo80: [0, 0], hi80: [9, 9], rv5: [1, 1] };
  const rows = m.research.forecastRows(research.vol_lab, recent);
  assert.equal(rows.length, research.vol_lab.series.date.length + 1);
  assert.equal(rows[rows.length - 1].date, "2026-10-14");
  assert.equal(rows[rows.length - 2].forecast, research.vol_lab.series.har_iv.at(-1), "research wins on overlap");
  const onlyLive = m.research.forecastRows(null, data.vol_forecast.recent_oos);
  assert.equal(onlyLive.length, data.vol_forecast.recent_oos.date.length);
  assert.deepEqual(m.research.forecastRows(null, null), []);
});

test("live record: an estimate only when nothing is on record; none when calls exist but none pend", () => {
  const { liveRecordSummary, estimateTargetEnd } = m.research;
  assert.equal(estimateTargetEnd("2026-10-20"), "2026-10-27");     // the first call's real target_end
  assert.equal(estimateTargetEnd("2026-10-17"), "2026-10-26");     // a Saturday start issues Monday
  const empty = liveRecordSummary({ ...data.vol_forecast.live_record, recent: [], n_recorded: 0 });
  assert.equal(empty.firstResult, estimateTargetEnd(data.vol_forecast.live_record.started));
  assert.equal(empty.firstResultIsEstimate, true);
  const skippedOnly = liveRecordSummary({ ...data.vol_forecast.live_record, recent: [{ status: "skipped_roll", target_end: "2026-10-27" }] });
  assert.equal(skippedOnly.firstResult, null);
  const pending = liveRecordSummary({ started: "2026-10-20", recent: [{ status: "pending", target_end: "2026-10-28" }, { status: "pending", target_end: "2026-10-27" }] });
  assert.equal(pending.firstResult, "2026-10-27");
  assert.equal(pending.firstResultIsEstimate, false);
  assert.equal(liveRecordSummary(null), null);
});

test("research ledger, supply study, leak anatomy and vol-target selectors normalize the payload", () => {
  const ledger = m.research.researchLedger(research);
  assert.equal(ledger.rows.length, research.ledger.rows.length);
  assert.equal(ledger.survivingBh, research.ledger.summary.supported_surviving_bh_5pct.length);
  assert.equal(m.research.researchLedger({}), null);

  const study = m.research.supplyStudy(research);
  const lost = study.groups.find((group) => group.key === "supply_lost");
  const raw = research.supply_study.groups.find((group) => group.key === "supply_lost");
  assert.equal(lost.peakP, raw.p_value_vs_placebo.peak, "p_value_vs_placebo is an object {peak, settle}");
  assert.equal(study.placebo.peak, research.supply_study.placebo.median_peak_pct);

  const anatomy = m.research.leakAnatomy(research);
  assert.equal(anatomy.ladder.length, research.leak_anatomy.analytic.by_training_row.length);
  const headline = anatomy.experiments.filter((row) => row.isHeadline);
  assert.equal(headline.length, 1);
  assert.equal(headline[0].unpurged, research.leak_anatomy.unpurged_dir_acc_pct_mean);
  assert.equal(m.research.leakAnatomy({}), null);

  const risk = m.research.volTargetRisk(data.vol_forecast.economic.vol_target_overlay);
  assert.equal(risk.rows.length, Object.keys(data.vol_forecast.economic.vol_target_overlay.risk).length);
  assert.equal(m.research.volTargetRisk({}), null);
});

// ------------------------------------------------------------------------------------ assertions
test("assertion titles state only what the numbers support", () => {
  const a = m.assertions;
  const rows = m.research.leaderboardRows(research.vol_lab);
  assert.equal(a.leaderboardAssertion(rows), "Implied-vol and range-based models are statistically tied at the top; return-only models are significantly worse");
  // A return-only model inside the set removes the claim about return-only models.
  const withGarch = rows.map((row) => (row.key === "garch" ? { ...row, inMcs: true } : row));
  assert.doesNotMatch(a.leaderboardAssertion(withGarch), /significantly worse/);
  assert.match(a.leaderboardAssertion(withGarch), /of 14 models are statistically tied/);
  assert.equal(a.leaderboardAssertion([]), null);

  const v = data.vol_forecast.validation;
  assert.match(a.forecastAssertion(v, data.vol_forecast.live), new RegExp(`band covered ${v.interval_80_coverage_pct}% of ${v.interval_80_n} weeks`));
  assert.match(a.forecastAssertion({ ...v, interval_80_coverage_pct: 70.1 }, null), /band under-covered \(70\.1% of/);
  assert.equal(a.forecastAssertion(null, null), null);

  const pm = m.research.postmortemWindow(research, "five_year");
  assert.match(a.leakAssertion(pm), /^A look-ahead leak turned a coin flip into a Sharpe of /);
  assert.match(a.leakAssertion({ leaked: { sharpe: 2 }, purged: { sharpe: 1, p: 0.01 } }), /^Purging a look-ahead leak cut/);
  assert.equal(a.leakAssertion(null), null);

  assert.match(a.coneAssertion(research.vol_lab.vol_cone), /^Realized vol is at the \d+(st|nd|rd|th) percentile on a 5-day window/);
  assert.match(a.vrpAssertion(data.vol_forecast.economic.variance_risk_premium), /^OVX exceeded subsequent realized vol in /);
  const hsGrids = research.vol_lab.var_backtest.summary["historical_simulation@0.05"].exceedances_by_grid.length;
  assert.match(a.riskAssertion(m.research.varRows(research.vol_lab), m.research.modelLabels(research.vol_lab), research.vol_lab.config.champion), new RegExp(`historical simulation fails independence on ${hsGrids}/${hsGrids}`));
  assert.match(a.carryAssertion(research.carry), /^Carry sorted WTI returns before 2007 .* not detectably after .*power\)/);
  assert.match(a.supplyStudyAssertion(m.research.supplyStudy(research)), /indistinguishable from one$/);
  assert.match(a.leakAnatomyAssertion(m.research.leakAnatomy(research)), /^On a pure random walk an unpurged forest scores /);
  assert.match(a.ledgerAssertion(m.research.researchLedger(research)), /^\d+ of \d+ supported claims survive Benjamini-Hochberg at 5%; \d+ clear t > 3$/);
  assert.match(a.volTargetAssertion(m.research.volTargetRisk(data.vol_forecast.economic.vol_target_overlay)), new RegExp(`against a ${data.vol_forecast.economic.vol_target_overlay.target_vol_pct}% target with thinner tails`));
  assert.match(a.methodAssertion(research.vol_lab), new RegExp(`^Every research figure is computed from ${Object.keys(research.vol_lab.generated_from.files).length} source files`));

  const record = m.research.liveRecordSummary({ ...data.vol_forecast.live_record, recent: [], n_recorded: 0 });
  assert.match(a.liveAssertion(record), /nothing has resolved yet; first result due about /);
  const resolved = a.liveAssertion({ resolved: 3, champion: { qlike: 0.31, interval_80_coverage_pct: 66.7 }, challenger: { qlike: 0.29 } });
  assert.equal(resolved, "3 live weeks resolved: QLIKE 0.310 for the model of record vs 0.290 for the challenger; 80% band covered 66.7%");

  for (const builder of ["stabilityAssertion", "calibrationAssertion", "carryAssertion", "supplyStudyAssertion", "ledgerAssertion", "volTargetAssertion", "coneAssertion", "vrpAssertion"]) {
    assert.doesNotThrow(() => a[builder](null, { labels: new Map() }), builder);
  }
});
