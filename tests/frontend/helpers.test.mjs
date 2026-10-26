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

/** Text with its no-break spaces read as spaces (fmtP keeps "p = 0.63" on one line with them). */
const spaced = (text) => (typeof text === "string" ? text.replace(/\u00a0/g, " ") : text);

// ------------------------------------------------------------------------------------ formatting
test("format helpers handle ages, spans, ordinals and p-value cells", () => {
  const f = m.format;
  assert.equal(f.fmtAge(14 * 60000), "14m");
  assert.equal(f.fmtAge((2 * 60 + 14) * 60000), "2h 14m");
  assert.equal(f.fmtAge((50 * 60 + 5) * 60000), "2d 2h");
  assert.equal(f.fmtAge(-5), "—");
  assert.equal(f.fmtSpan("2008-05-08", "2026-10-07"), "May 2008 – Oct 2026");
  assert.equal(f.fmtSpan("2008-05-08", null), "May 2008");
  assert.equal(f.fmtSpan(null, null), "—");
  assert.deepEqual([1, 2, 3, 11, 12, 13, 22, 68.3, 91.5].map(f.ordinal), ["1st", "2nd", "3rd", "11th", "12th", "13th", "22nd", "68th", "92nd"]);
  assert.equal(f.fmtPValue(0.0059), "0.006");
  assert.equal(f.fmtPValue(0.0496), "0.0496");
  assert.equal(spaced(f.fmtPValue(0.0004)), "< 0.001");
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
  // A label too long for its column wraps onto two lines instead of losing its end (touch screens
  // cannot reach the hover title); only what still does not fit is shortened.
  const { wrapLabel } = m.svg;
  assert.deepEqual(wrapLabel("short", 200), ["short"]);
  const wrapped = wrapLabel("HAR-IV, same-day OVX (v1 timing)", 143);
  assert.equal(wrapped.length, 2);
  assert.equal(wrapped.join(" "), "HAR-IV, same-day OVX (v1 timing)");
  assert.ok(wrapped.every((line) => line.length <= Math.floor(143 / 6.6)));
  const cut = wrapLabel("a label with far too many words to fit on two short lines", 66);
  assert.equal(cut.length, 2);
  assert.ok(cut[1].endsWith("…"));
  const spread = spreadLabels([{ y: 50 }, { y: 52 }, { y: 10 }, { y: 51 }], 12);
  assert.deepEqual(spread.map((item) => item.y), [50, 74, 10, 62]);   // input order kept, pushed apart in y order
  assert.match(m.sparkline.sparkPath([1, 2, 3], 10, 10), /^M1\.5,8\.5 L5\.0,5\.0 L8\.5,1\.5$/);
  assert.equal(m.sparkline.sparkPath([1], 10, 10), null);
});

test("hyphenated tokens in titles are kept on one line", async () => {
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const keep = m.keepHyphenated.default;
  const html = renderToStaticMarkup(createElement("h2", null, keep("HAR-IV beat GJR-GARCH(1,1)-t in 16 years")));
  assert.equal(html, '<h2><span class="nowrap">HAR-IV</span> beat <span class="nowrap">GJR-GARCH(1,1)-t</span> in 16 years</h2>');
  assert.equal(keep("no hyphen here"), "no hyphen here");
  assert.equal(keep(null), null);
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
  const control = (key) => m.research.isTimingControl(board.models.find((model) => model.key === key));
  assert.equal(rows.filter((row) => row.inMcs).length, board.mcs_qlike_included.filter((key) => !control(key)).length);
  const record = byKey.get(research.vol_lab.config.champion);
  assert.equal(record.grids.filter(Boolean).length, record.gridsIn);
});

// A leaderboard in the current contract: the timing control flagged, its MCS fields null.
const flaggedLab = () => {
  const lab = structuredClone(research.vol_lab);
  const board = lab.leaderboard;
  for (const model of board.models) {
    model.timing_control = model.key === "har_iv_sameday";
    if (model.timing_control) {
      for (const key of ["mcs_qlike_p", "in_mcs_qlike", "mcs_mse_p", "in_mcs_mse", "in_mcs_qlike_grids", "in_mcs_qlike_range_proxy"]) model[key] = null;
    }
  }
  for (const key of ["mcs_qlike_included", "mcs_mse_included", "mcs_qlike_range_proxy_included"]) {
    board[key] = board[key].filter((name) => name !== "har_iv_sameday");
  }
  for (const grid of Object.keys(board.mcs_qlike_by_grid)) {
    board.mcs_qlike_by_grid[grid] = board.mcs_qlike_by_grid[grid].filter((name) => name !== "har_iv_sameday");
  }
  board.mcs_excludes = ["har_iv_sameday"];
  return lab;
};

test("a timing control is identified by its flag, and is never in the MCS or counted", () => {
  const { leaderboardRows, mcsSummary, isTimingControl } = m.research;
  assert.ok(research.vol_lab.leaderboard.models.some((model) => model.timing_control === true), "the fixture flags it");
  assert.equal(isTimingControl({ key: "har_iv_sameday", timing_control: false }), false, "the flag decides, not the key");
  assert.equal(isTimingControl({ key: "har_iv_sameday" }), false, "no flag, no timing control");
  assert.equal(isTimingControl({ key: "other", timing_control: true }), true);
  for (const lab of [research.vol_lab, flaggedLab()]) {
    const rows = leaderboardRows(lab);
    const control = rows.find((row) => row.key === "har_iv_sameday");
    assert.equal(control.timingControl, true);
    assert.equal(control.mcsEligible, false);
    assert.equal(control.inMcs, false);
    assert.equal(control.mcsP, null);
    assert.deepEqual(control.grids, []);
    const summary = mcsSummary(lab);
    assert.equal(summary.eligible, rows.length - 1);
    assert.equal(summary.survivors, rows.filter((row) => row.inMcs).length);
    assert.ok(!rows.some((row) => row.timingControl && row.inMcs));
    assert.deepEqual(summary.excluded, [control.label]);
  }
  // A key listed in mcs_excludes is ineligible even without the flag.
  const excludedLab = structuredClone(research.vol_lab);
  excludedLab.leaderboard.mcs_excludes = ["raw_ovx"];
  assert.equal(leaderboardRows(excludedLab).find((row) => row.key === "raw_ovx").inMcs, false);
});

test("without MCS fields the summary reports no survivor count, never zero", () => {
  const lab = structuredClone(research.vol_lab);
  delete lab.leaderboard.mcs_qlike_included;
  delete lab.leaderboard.mcs_qlike_by_grid;
  for (const model of lab.leaderboard.models) { delete model.in_mcs_qlike; delete model.mcs_qlike_p; delete model.in_mcs_qlike_grids; }
  const summary = m.research.mcsSummary(lab);
  assert.equal(summary.survivors, null);
  assert.equal(summary.champion, null);
  assert.equal(m.assertions.leaderboardAssertion(m.research.leaderboardRows(lab), { available: m.research.hasMcsResults(lab) }), null);
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
  assert.equal(rows[rows.length - 1].source, "live");
  assert.equal(rows[rows.length - 2].forecast, research.vol_lab.series.har_iv.at(-1), "research wins on overlap");
  assert.equal(rows[rows.length - 2].source, "lab");
  const onlyLive = m.research.forecastRows(null, data.vol_forecast.recent_oos);
  assert.equal(onlyLive.length, data.vol_forecast.recent_oos.date.length);
  assert.deepEqual(m.research.forecastRows(null, null), []);
});

test("band coverage is computed from the rows drawn and names their pipeline", () => {
  const { bandCoverage, forecastRows } = m.research;
  const series = research.vol_lab.series;
  const expected = series.date.map((_, i) => [series.actual[i], series.lo80[i], series.hi80[i]])
    .filter(([a, lo, hi]) => a != null && lo != null && hi != null);
  const inside = expected.filter(([a, lo, hi]) => a >= lo && a <= hi).length;
  const band = bandCoverage(forecastRows(research.vol_lab, data.vol_forecast.recent_oos));
  assert.equal(band.n, expected.length);
  assert.equal(band.covered, inside);
  assert.equal(m.assertions.bandSourceLabel(band), "benchmark lab");
  const live = bandCoverage(forecastRows(null, data.vol_forecast.recent_oos));
  assert.equal(live.lab, 0);
  assert.equal(m.assertions.bandSourceLabel(live), "production validation");
  const mixed = bandCoverage([{ source: "lab", actual: 1, lo: 0, hi: 2 }, { source: "live", actual: 5, lo: 0, hi: 2 }]);
  assert.deepEqual([mixed.n, mixed.covered, mixed.coverage], [2, 1, 50]);
  assert.equal(m.assertions.bandSourceLabel(mixed), "benchmark lab; production validation for the latest 1 week");
  assert.equal(bandCoverage([]).coverage, null);
});

test("live record: next_result_due is the due date, and null means no result is due", () => {
  const { liveRecordSummary } = m.research;
  const record = data.vol_forecast.live_record;
  assert.ok(Object.hasOwn(record, "next_result_due"), "the fixture follows the current contract");
  assert.equal(liveRecordSummary(record).firstResult, record.next_result_due);
  // A pending call whose window spans a roll will be skipped: the record says no result is due,
  // and nothing is estimated in its place, from the calls or from the start date.
  const rollOnly = liveRecordSummary({ ...record, next_result_due: null, recent: [{ status: "pending", target_end: "2026-10-27" }] });
  assert.equal(rollOnly.firstResult, null);
  assert.equal(liveRecordSummary({ ...record, next_result_due: null, recent: [], n_recorded: 0 }).firstResult, null);
  const withoutKey = { ...record, recent: [{ status: "pending", target_end: "2026-10-28" }] };
  delete withoutKey.next_result_due;
  assert.equal(liveRecordSummary(withoutKey).firstResult, null);
  const due = liveRecordSummary({ ...record, next_result_due: "2026-11-03", recent: [{ status: "pending", target_end: "2026-10-28" }] });
  assert.equal(due.firstResult, "2026-11-03");
  assert.equal(liveRecordSummary({ ...record, next_result_due: "soon" }).firstResult, null);
  assert.equal(liveRecordSummary(null), null);
});

/**
 * A live record whose resolved weeks are not all paired: `all` weeks resolved for the model of
 * record, `pairedN` of them with the pinned challenger. The QLIKE figures are deliberately far
 * apart, so any mix-up of the two samples shows.
 */
const mixedRecord = ({ all = 3, pairedN = 2 } = {}) => ({
  ...data.vol_forecast.live_record,
  n_recorded: all, n_resolved: all, n_pending: 0, n_independent: all, next_result_due: null,
  champion: { ...data.vol_forecast.live_record.champion, n: all, qlike: 0.815, interval_80_coverage_pct: 66.7 },
  challenger: {
    ...data.vol_forecast.live_record.challenger, n: pairedN,
    qlike: pairedN ? 0.0187 : null, champion_qlike: pairedN ? 0.0213 : null,
  },
  promotion: {
    ...data.vol_forecast.live_record.promotion, n_independent_paired: pairedN,
    n_excluded_no_challenger: all - pairedN, n_excluded_other_version: 0, n_excluded_other_challenger: 0,
  },
});

test("live record: the two models are compared on the same calls, the all-calls QLIKE kept apart", () => {
  const summary = m.research.liveRecordSummary(mixedRecord());
  const raw = mixedRecord();
  assert.deepEqual(summary.paired, { n: raw.challenger.n, record: raw.challenger.champion_qlike, challenger: raw.challenger.qlike });
  assert.deepEqual(summary.recordAll, { n: raw.champion.n, qlike: raw.champion.qlike, modelVersion: raw.champion.model_version });
  const pinned = summary.promotion;
  assert.equal(pinned.modelVersion, data.vol_forecast.live_record.promotion.model_version);
  assert.equal(pinned.challengerKey, data.vol_forecast.live_record.promotion.challenger_key);
  assert.deepEqual(pinned.excluded, { otherVersion: 0, otherChallenger: 0, noChallenger: raw.champion.n - raw.challenger.n });
});

test("research ledger, supply study, leak anatomy and vol-target selectors normalize the payload", () => {
  const ledger = m.research.researchLedger(research);
  const summary = research.ledger.summary;
  assert.equal(ledger.rows.length, research.ledger.rows.length);
  assert.equal(ledger.survivingBh, summary.supported_surviving_bh_5pct.length);
  assert.equal(m.research.researchLedger({}), null);
  // A failure to reject is "not supported", never "rejected"; the flags come from the summary lists.
  assert.ok(ledger.rows.every((row) => row.status !== "rejected"));
  assert.ok(ledger.rows.some((row) => row.status === "not_supported"), "the fixture has not-supported rows");
  assert.equal(m.research.ledgerStatusLabel("not_supported"), "not supported");
  assert.deepEqual(ledger.rows.filter((row) => row.selectedAfterSearch).map((row) => row.id), summary.selected_after_search);
  assert.deepEqual(ledger.rows.filter((row) => row.underpowered).map((row) => row.id), summary.underpowered);
  assert.equal(ledger.underpowered, summary.underpowered.length);
  assert.equal(ledger.supportedAfterSearch, summary.supported_selected_after_search.length);
  const onRow = m.research.researchLedger({ ledger: { rows: [{ id: "x", hypothesis: "h", status: "not_supported", underpowered: true }] } });
  assert.equal(onRow.rows[0].underpowered, true, "a flag on the row itself also counts");

  // Ledger prose shows display words: no snake_case name or artifact file name reaches the page.
  const words = { labels: m.research.modelLabels(research.vol_lab), mcsLevel: m.research.mcsLevel(research.vol_lab) };
  for (const row of research.ledger.rows) {
    for (const text of [row.hypothesis, row.test, row.statistic_name].filter(Boolean)) {
      assert.doesNotMatch(m.research.ledgerText(text, words), /\b[a-z0-9]+_[a-z0-9_]+\b|\.json\b/, `${row.id}: ${text}`);
    }
  }
  assert.equal(m.research.ledgerText("x; the lab's own set (mcs_members) is the 1 - mcs_size confidence set", words),
    `x; the lab's own set is the ${words.mcsLevel}% confidence set`);
  assert.equal(m.research.ledgerText("har_iv_sameday vs persistence", words),
    `${m.research.shortLabel(words.labels.get("har_iv_sameday"))} vs persistence`, "model keys become their labels");
  const note = research.vol_lab.leaderboard.mcs_note;
  assert.ok(note.includes("har_iv_sameday"), "the fixture's note names the key");
  assert.ok(!m.research.withModelLabels(note, words.labels).includes("har_iv_sameday"));

  const study = m.research.supplyStudy(research);
  const lost = study.groups.find((group) => group.key === "supply_lost");
  const raw = research.supply_study.groups.find((group) => group.key === "supply_lost");
  assert.equal(lost.peakP, raw.p_value_vs_placebo.peak, "p_value_vs_placebo is an object {peak, settle}");
  assert.equal(lost.settlePYear, raw.p_value_vs_year_matched_placebo.settle);
  assert.equal(study.placebo.peak, research.supply_study.placebo.median_peak_pct);
  // The study's rule needs both placebo tests: its p is the larger of the two, as in the ledger.
  for (const group of study.groups) {
    const source = research.supply_study.groups.find((item) => item.key === group.key);
    assert.equal(group.peakPRule, Math.max(source.p_value_vs_placebo.peak, source.p_value_vs_year_matched_placebo.peak), group.key);
    assert.equal(group.settlePRule, Math.max(source.p_value_vs_placebo.settle, source.p_value_vs_year_matched_placebo.settle), group.key);
    const ledgerRow = research.ledger.rows.find((row) => row.id === `shock_${group.key}_peak`);
    if (ledgerRow) {
      assert.equal(ledgerRow.p_value, group.peakPRule, `ledger p for ${group.key}`);
      assert.ok(ledgerRow.hypothesis.includes(group.name), `the ledger names ${group.key} as the study does`);
    }
  }
  // Overlapping event pairs are attributed to every group that contains both events.
  const pairs = research.supply_study.overlapping_events.pairs;
  assert.deepEqual(study.overlapping.pairs.map((pair) => pair.events), pairs.map((pair) => pair.events));
  for (const group of study.groups) {
    const members = new Set(research.supply_study.groups.find((item) => item.key === group.key).event_ids);
    assert.equal(group.overlaps.length, pairs.filter((pair) => pair.events.every((id) => members.has(id))).length, group.key);
  }
  assert.ok(study.groups.some((group) => group.overlaps.length > 0), "the fixture has an overlapping pair inside a group");
  assert.equal(m.research.eventName("hurricane_katrina_2005"), "Hurricane Katrina (2005)");
  assert.equal(m.research.eventName("opec_record_cut_2020"), "OPEC Record Cut (2020)");
  for (const id of pairs.flatMap((pair) => pair.events)) assert.doesNotMatch(m.research.eventName(id), /_/, id);

  const anatomy = m.research.leakAnatomy(research);
  assert.equal(anatomy.ladder.length, research.leak_anatomy.analytic.by_training_row.length);
  const headline = anatomy.experiments.filter((row) => row.isHeadline);
  assert.equal(headline.length, 1);
  assert.equal(headline[0].unpurged, research.leak_anatomy.unpurged_dir_acc_pct_mean);
  assert.equal(m.research.leakAnatomy({}), null);
  // Sharpe bases come from the artifact: gross for the experiments, after costs for the reference.
  assert.equal(anatomy.sharpeBasis.experiments, research.leak_anatomy.design.sharpe.split(":")[0].trim());
  assert.equal(anatomy.sharpeBasis.reference, research.leak_anatomy.retracted_reference.sharpe_basis);
  const bare = structuredClone(research);
  delete bare.leak_anatomy.design;
  delete bare.leak_anatomy.retracted_reference.sharpe_basis;
  assert.deepEqual(m.research.leakAnatomy(bare).sharpeBasis, { experiments: null, reference: null }, "no basis is assumed");
  // No skill is 50% on the random walk and each CL=F row's own base rate.
  for (const row of anatomy.experiments) {
    const arm = research.leak_anatomy.experiments[row.experiment][row.model].unpurged;
    assert.equal(row.baseRate, arm.base_rate_pct_mean, row.key);
    assert.equal(row.unpurgedBeatBase, arm.runs_beating_base_rate_5pct, row.key);
    assert.equal(row.noSkill, row.experiment === "synthetic_random_walk" ? 50 : arm.base_rate_pct_mean, row.key);
  }
  assert.ok(anatomy.experiments.some((row) => !row.symmetric), "the fixture has CL=F rows");

  const risk = m.research.volTargetRisk(data.vol_forecast.economic.vol_target_overlay);
  assert.equal(risk.rows.length, Object.keys(data.vol_forecast.economic.vol_target_overlay.risk).length);
  assert.equal(m.research.volTargetRisk({}), null);

  const vrp = data.vol_forecast.economic.variance_risk_premium;
  const premium = m.research.vrpSummary(vrp);
  assert.equal(premium.share, vrp.share_positive_pct);
  assert.equal(premium.nullShare, vrp.zero_premium_null.share_positive_pct);
  assert.equal(premium.nullP, vrp.zero_premium_null.p_value_one_sided);
  assert.deepEqual(premium.varianceCi, vrp.mean_premium_variance_ci95);
  assert.equal(m.research.vrpSummary(null), null);
});

test("one name and one DM sign for each benchmark, page-wide", () => {
  const v = data.vol_forecast.validation;
  // The validation signs DM so that positive favours the model of record; the page (and the
  // leaderboard) signs it so that positive means the named benchmark has the lower loss.
  assert.equal(m.research.ovxOnlyBenchmark(v).dm, -v.har_vs_ovx_only_qlike_dm);
  assert.equal(m.research.ovxOnlyBenchmark(v).p, v.har_vs_ovx_only_qlike_dm_p_value);
  assert.equal(m.research.persistenceBenchmark(v).dm, -v.har_vs_persistence_qlike_dm);
  const ovxOnly = research.vol_lab.leaderboard.models.find((model) => model.key === m.research.OVX_ONLY_KEY);
  assert.equal(m.research.ovxOnlyLabel(research.vol_lab), ovxOnly.label, "the hero and FCST use the lab's label");
  assert.equal(m.research.ovxOnlyLabel(null), "OVX-only regression");
});

// ------------------------------------------------------------------------------------ assertions
test("assertion titles state one claim each, and only what the numbers support", () => {
  const a = m.assertions;
  const f = m.format;
  const titles = [];
  const keep = (title) => { titles.push(title); return title; };

  const v = data.vol_forecast.validation;
  const ovxLabel = m.research.ovxOnlyLabel(research.vol_lab);
  const model = a.shortModelName(data.vol_forecast.live.model);
  assert.ok(v.har_level_r2 > v.persistence_level_r2 && v.har_vs_ovx_only_qlike_dm_p_value >= a.ALPHA, "the fixture's case");
  assert.equal(keep(a.forecastAssertion(v, data.vol_forecast.live, { ovxLabel })),
    `${model} beats persistence but is not distinguishable from the ${ovxLabel}`);
  const significant = { ...v, har_vs_ovx_only_qlike_dm_p_value: 0.002 };
  assert.equal(a.forecastAssertion({ ...significant, har_vs_ovx_only_qlike_dm: 3.1 }, null, { ovxLabel }), `HAR-IV beats persistence and the ${ovxLabel}`);
  assert.equal(a.forecastAssertion({ ...significant, har_vs_ovx_only_qlike_dm: -3.1 }, null, { ovxLabel }), `HAR-IV beats persistence but loses to the ${ovxLabel}`);
  assert.equal(a.forecastAssertion(null, null), null);
  assert.deepEqual(a.forecastDetail(v, { ovxLabel }).map(spaced), [
    `QLIKE ${f.fmtNum(v.har_qlike, 3)} vs ${f.fmtNum(v.ovx_only_qlike, 3)} for the ${ovxLabel}, DM ${spaced(f.fmtP(v.har_vs_ovx_only_qlike_dm_p_value))}`
      + ` (weekly validation, ${f.fmtInt(v.n)} weeks)`,
  ]);

  const pm = m.research.postmortemWindow(research, "five_year");
  assert.match(keep(a.leakAssertion(pm)), /^A look-ahead leak turned a coin flip into a Sharpe of /);
  assert.match(a.leakAssertion({ leaked: { sharpe: 2 }, purged: { sharpe: 1, p: 0.01 } }), /^Purging a look-ahead leak cut/);
  assert.equal(a.leakAssertion(null), null);

  assert.match(keep(a.coneAssertion(research.vol_lab.vol_cone)), /^Realized vol is at the \d+(st|nd|rd|th) percentile on a 5-day window/);

  // VRP: read in variance units; the share of positive periods is never the evidence on its own.
  const vrp = data.vol_forecast.economic.variance_risk_premium;
  const [lo, hi] = vrp.mean_premium_variance_ci95;
  assert.ok(lo <= 0 && hi >= 0, "the fixture's interval spans zero");
  assert.equal(keep(a.vrpAssertion(vrp)), "The variance risk premium, in variance units, is not distinguishable from zero");
  const noCi = { ...vrp };
  delete noCi.mean_premium_variance_ci95;
  assert.equal(a.vrpAssertion(noCi), vrp.zero_premium_null.p_value_one_sided < a.ALPHA
    ? "OVX exceeded realized vol more often than a zero premium would explain"
    : "OVX exceeded realized vol about as often as it would with no premium");
  assert.equal(a.vrpAssertion({ ...noCi, zero_premium_null: null }), null, "without the null the share supports no title");

  const champion = research.vol_lab.config.champion;
  const years = m.research.coverageByYear(research.vol_lab, champion).map((row) => row.coverage);
  assert.equal(keep(a.calibrationAssertion(research.vol_lab, champion)),
    `In every year the 80% interval covered between ${f.fmtPct(Math.min(...years))} and ${f.fmtPct(Math.max(...years))} of days`);
  const conformal = research.vol_lab.conformal[champion];
  assert.deepEqual(a.calibrationDetail(research.vol_lab, champion),
    [`overall ${f.fmtPct(conformal.coverage_pct)} of ${f.fmtInt(conformal.n_days)} days, targeted by construction`]);

  const study = m.research.supplyStudy(research);
  const lost = study.groups.find((group) => group.key === "supply_lost");
  const threat = study.groups.find((group) => group.key === "threat_only");
  assert.equal(a.placeboVerdict(lost.peakP, lost.peakPYear), "both");
  assert.equal(a.placeboVerdict(threat.peakP, threat.peakPYear), "neither");
  assert.equal(keep(a.supplyStudyAssertion(study)),
    `Supply removal peaks ${f.fmtSigned(lost.peak, 1, "%")} vs ${f.fmtSigned(study.placebo.peak, 1, "%")} on an ordinary date; threat-only: not significant`);
  assert.deepEqual(a.supplyStudyDetail(study).map(spaced), [
    `supply removal: ${spaced(f.fmtP(lost.peakP))} vs placebo, ${spaced(f.fmtPValue(lost.peakPYear))} year-matched`,
    `threat-only: ${spaced(f.fmtP(threat.peakP))} vs placebo, ${spaced(f.fmtPValue(threat.peakPYear))} year-matched`,
  ]);
  const studies = keep(a.studiesAssertion(research.carry, study));
  assert.match(studies, /; supply removal beat placebo dates$/);
  // A group that passes only the unmatched placebo does not pass the rule.
  const oneSided = structuredClone(research);
  oneSided.supply_study.groups.find((group) => group.key === "supply_lost").p_value_vs_year_matched_placebo.peak = 0.2;
  const oneStudy = m.research.supplyStudy(oneSided);
  assert.match(a.studiesAssertion(research.carry, oneStudy), /; supply removal beat only one placebo$/);
  assert.match(a.supplyStudyAssertion(oneStudy), /^Supply removal peaks .*, significant against only one placebo; /);
  assert.equal(a.placeboVerdict(0.01, 0.02), "both");
  assert.equal(a.placeboVerdict(0.01, 0.2), "one");
  assert.equal(a.placeboVerdict(0.3, 0.2), "neither");
  assert.equal(a.placeboVerdict(0.01, null), null);

  const anatomy = research.leak_anatomy;
  assert.equal(keep(a.leakAnatomyAssertion(m.research.leakAnatomy(research))),
    `On a pure random walk an unpurged forest scores ${f.fmtPct(anatomy.unpurged_dir_acc_pct_mean)}; purged, ${f.fmtPct(anatomy.purged_dir_acc_pct_mean)}`);

  const summary = research.ledger.summary;
  assert.equal(keep(a.ledgerAssertion(m.research.researchLedger(research))),
    `${summary.supported_surviving_bh_5pct.length} of ${summary.supported.length - summary.supported_selected_after_search.length} `
    + `supported claims survive Benjamini-Hochberg at 5%; ${summary.supported_clearing_hlz_t3.length} clear t > 3`);

  const overlay = data.vol_forecast.economic.vol_target_overlay;
  const risk = m.research.volTargetRisk(overlay);
  assert.equal(keep(a.volTargetAssertion(risk)),
    `Sizing on the forecast held realized vol at ${f.fmtPct(overlay.risk.forecast_vol_target.realized_vol_pct)} against a ${f.fmtNum(overlay.target_vol_pct, 0)}% target`);
  assert.ok(a.volTargetDetail(risk)[0].startsWith(`trailing-vol sizing held ${f.fmtPct(overlay.risk.trailing_vol_target.realized_vol_pct)}`), "the trailing-vol alternative is named");

  const files = Object.keys(research.vol_lab.generated_from.files).length;
  assert.equal(keep(a.methodAssertion(research.vol_lab)), `Every benchmark-lab figure comes from ${files} source files pinned by SHA-256 digest`);
  assert.ok(a.methodDetail(research.vol_lab).includes("production validation and post-mortem: committed records of runs on downloaded data"));

  // One claim per title, short enough to scan: the statistics are in the *Detail meta lines.
  for (const title of titles) assert.ok(title && title.length <= 90, `${title?.length}: ${title}`);

  for (const builder of ["stabilityAssertion", "calibrationAssertion", "carryAssertion", "supplyStudyAssertion", "ledgerAssertion", "volTargetAssertion", "coneAssertion", "vrpAssertion", "riskAssertion", "spaAssertion"]) {
    assert.doesNotThrow(() => a[builder](null, { labels: new Map() }), builder);
  }
});

const row = (key, info, extra = {}) => ({
  key, label: key, info: new Set(info), inMcs: false, mcsEligible: true, timingControl: false, role: null,
  mcsP: null, dmStat: null, dmP: null, ...extra,
});

test("MODL states only what the confidence set contains, and leaves timing controls out", () => {
  const { leaderboardAssertion, leaderboardDetail } = m.assertions;
  const record = row("HAR-IV", ["implied"], { role: "record", mcsP: 0.0368 });
  const base = [
    record,
    row("OVX-only", ["implied"], { inMcs: true, dmStat: 0.5, dmP: 0.6 }),
    row("HAR-IV-range", ["implied", "range"], { inMcs: true, dmStat: 3.1, dmP: 0.002 }),
    row("GARCH", [], { dmStat: -3.7, dmP: 0.0002 }),
    row("Same-day", ["implied"], { inMcs: true, timingControl: true, mcsEligible: false }),
  ];
  assert.equal(leaderboardAssertion(base, { level: 90 }), "All 2 models in the 90% Model Confidence Set use OVX; HAR-IV is not among them");
  assert.deepEqual(leaderboardDetail(base).map(spaced), ["HAR-IV MCS p = 0.04", "every return-only model has significantly higher QLIKE than HAR-IV"]);
  // Survivors that do not share an input are described by what each uses.
  const mixed = base.map((r) => (r.key === "HAR-IV-range" ? { ...r, info: new Set(["range"]) } : r));
  assert.match(leaderboardAssertion(mixed, { level: 90 }), /^All 2 models in the 90% Model Confidence Set use OVX or the intraday range;/);
  const shared = base.map((r) => (r.key === "OVX-only" ? { ...r, info: new Set(["range"]) } : r));
  assert.match(leaderboardAssertion(shared, { level: 90 }), /^All 2 models in the 90% Model Confidence Set use the intraday range;/);
  // A return-only survivor: no input claim, and no claim about return-only models.
  const garchIn = base.map((r) => (r.key === "GARCH" ? { ...r, inMcs: true } : r));
  assert.match(leaderboardAssertion(garchIn, { level: 90 }), /^3 of 4 models are in the 90% Model Confidence Set;/);
  assert.deepEqual(leaderboardDetail(garchIn).map(spaced), ["HAR-IV MCS p = 0.04"]);
  // The record inside the set, and the record alone in it.
  assert.match(leaderboardAssertion(base.map((r) => (r === record ? { ...r, inMcs: true, mcsP: 0.4 } : r))), /; HAR-IV is one of them$/);
  const alone = base.map((r) => ({ ...r, inMcs: r === record }));
  assert.equal(leaderboardAssertion(alone, { level: 90 }), "HAR-IV, the model of record, alone is in the 90% Model Confidence Set");
  // A timing control alone in the set is not a survivor.
  assert.equal(leaderboardAssertion(base.map((r) => ({ ...r, inMcs: r.timingControl })), { level: 90 }), null);
  assert.equal(leaderboardAssertion(base, { available: false }), null);
  assert.deepEqual(leaderboardDetail(base, { available: false }), []);
  assert.equal(leaderboardAssertion([]), null);

  // On the fixture: the stated count is the eligible members, and the record clause matches its flag.
  for (const lab of [research.vol_lab, flaggedLab()]) {
    const rows = m.research.leaderboardRows(lab);
    const title = leaderboardAssertion(rows, { level: m.research.mcsLevel(lab) });
    const members = rows.filter((r) => r.inMcs);
    assert.ok(title.startsWith(`All ${members.length} models in the 90% Model Confidence Set use `), title);
    const fixtureRecord = rows.find((r) => r.role === "record");
    assert.ok(title.endsWith(`${m.research.shortLabel(fixtureRecord.label)} is ${fixtureRecord.inMcs ? "one of them" : "not among them"}`), title);
    assert.ok(!members.some((r) => r.timingControl));
  }
});

test("the SPA footnote reads an inconclusive p-value as inconclusive", () => {
  const spaAssertion = (...args) => spaced(m.assertions.spaAssertion(...args));
  const spa = { p_consistent: 0.072, p_lower: 0.0548, p_upper: 0.7048, models: ["a", "b", "c"] };
  const text = spaAssertion(spa, { recordName: "HAR-IV", mcsLevel: 90 });
  assert.equal(text, "Hansen SPA test of HAR-IV against 3 alternatives: consistent p = 0.07 (lower 0.055, upper 0.70); "
    + "inconclusive: not significant at 5%, but it would reject at the 10% level the Model Confidence Set uses.");
  assert.doesNotMatch(text, /no alternative significantly beats/);
  assert.match(spaAssertion({ ...spa, p_consistent: 0.03 }, { recordName: "HAR-IV" }), /some alternative beats HAR-IV at the 5% level\.$/);
  assert.match(spaAssertion({ ...spa, p_consistent: 0.3 }, { recordName: "HAR-IV" }), /no alternative beats HAR-IV even at the 10% level\.$/);
  assert.match(spaAssertion({ ...spa, p_consistent: 0.08 }, { recordName: "HAR-IV", mcsLevel: 95 }), /would reject at the 10% level\.$/);
  const fixture = spaAssertion(research.vol_lab.leaderboard.spa_qlike_vs_har_iv, { mcsLevel: 90 });
  assert.ok(fixture.includes(`lower ${spaced(m.format.fmtPValue(research.vol_lab.leaderboard.spa_qlike_vs_har_iv.p_lower))}`));
  assert.equal(m.assertions.spaAssertion(null), null);
});

test("RISK names the VaR level of every clause and the 99% breach range across grids", () => {
  const rows = m.research.varRows(research.vol_lab);
  const labels = m.research.modelLabels(research.vol_lab);
  const champion = research.vol_lab.config.champion;
  const summary = research.vol_lab.var_backtest.summary;
  const record = summary[`${champion}:fhs@0.01`];
  const grids = record.exceedances_by_grid;
  const over = Math.min(...grids) > record.expected;
  assert.equal(m.assertions.riskAssertion(rows, labels, champion),
    `${m.research.shortLabel(labels.get(champion))} 99% VaR passes Kupiec on ${record.kupiec_pass_grids}/${grids.length} grids`
    + `${over ? ", each with more breaches than expected" : ""}`);
  const failing = [["99", summary["historical_simulation@0.01"]], ["95", summary["historical_simulation@0.05"]]]
    .filter(([, stats]) => stats.independence_pass_grids < stats.exceedances_by_grid.length)
    .map(([level, stats]) => `at ${level}% on ${stats.exceedances_by_grid.length - stats.independence_pass_grids}/${stats.exceedances_by_grid.length} grids`);
  assert.deepEqual(m.assertions.riskDetail(rows), [
    `${Math.min(...grids)}–${Math.max(...grids)} breaches vs ${m.format.fmtNum(record.expected, 1)} expected, filtered historical simulation`,
    ...(failing.length ? [`historical-simulation VaR fails the independence test ${failing.join(" and ")}`] : []),
  ]);
  assert.equal(m.assertions.riskAssertion([], labels, champion), null);
});

test("STDY carry: the title makes one claim; the halves' p-values, power and q are in the meta", () => {
  const { carryAssertion, carryDetail } = m.assertions;
  const ledger = m.research.researchLedger(research);
  const carry = research.carry;
  const pre = carry.pre_2007.spread_p_value_one_sided;
  const post = carry.post_2007.spread_p_value_one_sided;
  assert.ok(pre < m.assertions.ALPHA && post >= m.assertions.ALPHA, "the fixture's case");
  assert.equal(carryAssertion(carry), "Carry sorted WTI returns before 2007, not detectably after");
  const detail = carryDetail(carry, ledger).map(spaced);
  const preRow = ledger.byId.get("carry_spread_pre2007");
  assert.equal(detail[0], `before 2007 ${spaced(m.format.fmtP(pre))}`
    + `${preRow.survives_bh === false ? `, q = ${spaced(m.format.fmtPValue(preRow.q))} after Benjamini-Hochberg` : ""}`);
  const power = carry.power_post_2007;
  assert.equal(detail[1], `2007 on ${spaced(m.format.fmtP(post))}`
    + `${power.power_vs_pre_2007_spread < power.target_power ? `, ${Math.round(power.power_vs_pre_2007_spread * 100)}% power` : ""}`);
  assert.equal(detail[2], `change in the spread ${spaced(m.format.fmtP(carry.regime_change_test.spread_difference_p_value))}`);
  assert.doesNotMatch(carryDetail(carry, null).join(" "), /Benjamini-Hochberg/, "no ledger, no q claim");
});

test("LIVE counts unscored calls as such, and compares the two models on the same calls only", () => {
  const { liveRecordSummary } = m.research;
  const { liveAssertion, liveDetail, promotionText } = m.assertions;
  const record = data.vol_forecast.live_record;
  const fixture = liveRecordSummary(record);
  assert.equal(liveAssertion(fixture), "No live forecast has resolved yet"
    + `${fixture.firstResult ? `; the first is due ${m.format.fmtDate(fixture.firstResult)}` : ""}`);
  assert.equal(liveAssertion(liveRecordSummary({ ...record, next_result_due: "2026-10-28" })),
    "No live forecast has resolved yet; the first is due Oct 28, 2026");
  const skippedOnly = liveRecordSummary({
    started: record.started, n_recorded: 1, n_resolved: 0, n_pending: 0, n_skipped_roll: 1, n_expired: 0, next_result_due: null,
    recent: [{ status: "skipped_roll", target_end: "2026-10-27" }],
  });
  assert.equal(liveAssertion(skippedOnly), "No live forecast has resolved yet", "a call skipped at a roll dates nothing");
  assert.deepEqual(liveDetail(skippedOnly), ["1 call recorded: 1 skipped at a contract roll (never scored)"]);
  const mixed = liveRecordSummary({
    started: record.started, n_recorded: 3, n_resolved: 0, n_pending: 1, n_skipped_roll: 1, n_expired: 0, n_late_commit: 1,
    next_result_due: null,
    recent: [{ status: "pending", target_end: "2026-10-28" }, { status: "late_commit" }, { status: "skipped_roll" }],
    promotion: { status: "accruing", n_independent_paired: 0, required: 52, dm_stat: null, p_value: null, mean_qlike_difference: null },
  });
  assert.equal(mixed.lateCommit, 1);
  assert.deepEqual(liveDetail(mixed), ["3 calls recorded: 1 pending, 1 skipped at a contract roll, 1 not sealed in time (2 never scored)"]);
  assert.doesNotMatch(liveDetail(mixed).join(" "), /sealed after/, "a late commit may be sealed before its window opened");
  assert.equal(promotionText(mixed.promotion), "accruing 0/52 weeks");
  // The state only: the p-value is printed once, with the test's statistics (promotionDetail).
  assert.equal(promotionText({ status: "keep", p: 0.41 }), "keep the record");
  assert.equal(promotionText({ status: "promote", p: 0.03 }), "promote the challenger");
  assert.equal(m.research.promotionStatus({ status: "unknown" }), null);
  assert.equal(m.research.promotionStatus(null), null);

  // Few independent weeks: the title states the record's progress, never a comparison of losses.
  const raw = mixedRecord();
  assert.equal(liveAssertion(liveRecordSummary(raw)), "3 independent weeks resolved (3 calls); too few to compare the two models");
  assert.equal(liveAssertion(liveRecordSummary(mixedRecord({ all: 2, pairedN: 0 }))),
    "2 independent weeks resolved (2 calls); too few to compare the two models");
  // Enough of them, with resolved calls lacking a challenger forecast: the paired calls only.
  const enough = { ...mixedRecord({ all: 30, pairedN: 28 }), n_independent: 11 };
  const title = liveAssertion(liveRecordSummary(enough));
  assert.equal(title, `On the 28 resolved calls with both forecasts, QLIKE ${m.format.fmtNum(enough.challenger.champion_qlike, 3)} `
    + `record vs ${m.format.fmtNum(enough.challenger.qlike, 3)} challenger`);
  assert.ok(!title.includes(m.format.fmtNum(enough.champion.qlike, 3)), "the all-calls QLIKE is another sample");
  assert.equal(liveAssertion(liveRecordSummary({ ...mixedRecord({ all: 12, pairedN: 0 }), n_independent: 10 })),
    "12 calls resolved (10 independent weeks); none has both forecasts yet");
});

test("ledger statistics print with one precision per unit, and p-values like p-values", () => {
  const fmtLedgerStatistic = (value) => spaced(m.ledger.fmtLedgerStatistic(value));
  assert.equal(fmtLedgerStatistic({ statistic: 0.002, statistic_name: "MCS p-value of HAR-IV" }), "0.002");
  assert.equal(fmtLedgerStatistic({ statistic: 0.0004, statistic_name: "SPA consistent p-value" }), "< 0.001");
  assert.equal(fmtLedgerStatistic({ statistic: -7.07, statistic_name: "DM statistic" }), "−7.07");
  assert.equal(fmtLedgerStatistic({ statistic: 13.16, statistic_name: "t (HAC)" }), "13.2");
  // Percentages to one decimal, as the studies print them ("4.3", never "4.30").
  assert.equal(fmtLedgerStatistic({ statistic: 4.3, statistic_name: "median settle %" }), "4.3");
  assert.equal(fmtLedgerStatistic({ statistic: -4.8, statistic_name: "median settle %" }), "−4.8");
  assert.equal(fmtLedgerStatistic({ statistic: null }), "—");
});

test("the market sparkline names its series and is marked when it ends on the expiring contract", () => {
  const { sparklineLabel } = m.marketStrip;
  const rolled = sparklineLabel({ ...data, price_change_quality: "daily_close_new_contract" }, 40);
  assert.equal(rolled.rolled, true);
  assert.match(rolled.title, /^CL=F continuous front month, unadjusted \(jumps at each roll\): last 40 daily closes through .*, on the expiring contract, not /);
  assert.match(rolled.text, /^CL=F to [A-Z][a-z]{2} \d+, before the roll$/);
  const same = sparklineLabel({ ...data, price_change_quality: "daily_close" }, 40);
  assert.equal(same.rolled, false);
  assert.equal(same.text, "CL=F, 40 closes");
  assert.equal(sparklineLabel({ price_change_quality: "unavailable_contract_roll" }, 3).rolled, true);
});

test("logToSimplePct converts a log return in percent to a simple return in percent", () => {
  const { logToSimplePct } = m.research;
  assert.equal(logToSimplePct(0), 0);
  assert.ok(Math.abs(logToSimplePct(-80.6) - (Math.exp(-0.806) - 1) * 100) < 1e-9);
  assert.equal(logToSimplePct(null), null);
});
