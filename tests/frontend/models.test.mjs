// MODL and STAB: the leaderboard title states the MCS result with its robustness, the membership
// matrix holds exactly the payload's lists, the QLIKE strip marks an outlier off-scale, and STAB
// compares the record with the OVX-only regression, year by year and on the weekly series. Every
// expected value is computed here from the fixtures, independently of the code under test.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { escapeRegExp, loadDashboard, readFixture, visibleText } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let comparison;
let modl;
let stabilityCharts;
before(async () => {
  m = await loadDashboard();
  comparison = await m.load("/src/lib/comparison.js");
  modl = await m.load("/src/panels/LeaderboardPanel.jsx");
  stabilityCharts = await m.load("/src/charts/stabilityCharts.jsx");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");
const lab = research.vol_lab;
const board = lab.leaderboard;
const champion = lab.config.champion;
const challenger = lab.config.challenger;

const html = (element) => renderToStaticMarkup(element);
const renderDashboard = (researchPayload = research) => html(createElement(m.dashboard.default, {
  data, research: researchPayload, researchStatus: "ready", quote: m.quote.resolveQuote(null, data),
}));
const section = (markup, id) => markup.match(new RegExp(`<section id="${id}"[\\s\\S]*?</section>`))?.[0] ?? "";
const label = (key) => m.research.shortLabel(board.models.find((model) => model.key === key)?.label ?? key);
/** Variance QLIKE written out again, so the module's formula is checked, not reused. */
const qlike = (actual, forecast) => {
  const ratio = (actual * actual) / (forecast * forecast);
  return ratio - Math.log(ratio) - 1;
};

// ------------------------------------------------------------------------------------------ MODL

test("the MODL title gives the record's grid membership when it is out of the set, within 90 characters", () => {
  const rows = m.research.leaderboardRows(lab);
  const robustness = comparison.mcsRobustness(lab);
  const level = m.research.mcsLevel(lab);
  const title = m.assertions.leaderboardAssertion(rows, { level, robustness });
  const gridLists = Object.values(board.mcs_qlike_by_grid);
  const gridsIn = gridLists.filter((list) => list.includes(champion)).length;
  assert.ok(!board.mcs_qlike_included.includes(champion) && gridsIn > 0, "the fixture's case: out, but in on some grid");
  assert.ok(title.endsWith(`${label(champion)} is out, though in on ${gridsIn}/${gridLists.length} grids`), title);
  assert.ok(title.length <= 90, `${title.length}: ${title}`);
  assert.match(title, / MCS /, "the short name, since the full one would pass 90 characters");
  assert.ok(visibleText(section(renderDashboard(), "modl")).includes(title), "the panel shows it");

  // Out on every grid but in under the range proxy.
  const proxyOnly = structuredClone(lab);
  for (const key of Object.keys(proxyOnly.leaderboard.mcs_qlike_by_grid)) {
    proxyOnly.leaderboard.mcs_qlike_by_grid[key] = proxyOnly.leaderboard.mcs_qlike_by_grid[key].filter((item) => item !== champion);
  }
  assert.ok(proxyOnly.leaderboard.mcs_qlike_range_proxy_included.includes(champion));
  const proxyTitle = m.assertions.leaderboardAssertion(m.research.leaderboardRows(proxyOnly),
    { level, robustness: comparison.mcsRobustness(proxyOnly) });
  assert.ok(proxyTitle.endsWith(`${label(champion)} is out, though in under the range proxy`), proxyTitle);
  assert.ok(proxyTitle.length <= 90, proxyTitle);
  // And out under both: the plain statement.
  proxyOnly.leaderboard.mcs_qlike_range_proxy_included = proxyOnly.leaderboard.mcs_qlike_range_proxy_included.filter((item) => item !== champion);
  assert.ok(m.assertions.leaderboardAssertion(m.research.leaderboardRows(proxyOnly), { level, robustness: comparison.mcsRobustness(proxyOnly) })
    .endsWith(`${label(champion)} is not among them`));
});

test("the membership matrix holds exactly the payload's lists, column by column", () => {
  const robustness = comparison.mcsRobustness(lab);
  const robustKeys = Object.keys(board.mcs_robustness_qlike);
  assert.deepEqual(robustness.columns.map((column) => column.key),
    ["full", "grids", ...robustKeys.map((key) => `robust-${key}`), "mse", "proxy"]);
  const labels = Object.fromEntries(robustness.columns.map((column) => [column.key, column.label]));
  assert.equal(labels["robust-block_10"], "Block 10");
  assert.equal(labels["robust-2008-2016"], "2008–16");
  assert.equal(labels["robust-2017-2026"], "2017–26");
  const yearTitle = robustness.columns.find((column) => column.key === "robust-2008-2016").title;
  assert.ok(yearTitle.includes(board.mcs_robustness_qlike["2008-2016"].n_days.toLocaleString("en-US")), "the n_days of a year split");

  // Rows: the eligible models (no timing control), lowest QLIKE first.
  const eligible = board.models.filter((model) => !model.timing_control && !board.mcs_excludes.includes(model.key))
    .sort((a, b) => a.qlike - b.qlike).map((model) => model.key);
  assert.deepEqual(robustness.rows.map((row) => row.key), eligible);
  const lists = {
    full: board.mcs_qlike_included,
    mse: board.mcs_mse_included,
    proxy: board.mcs_qlike_range_proxy_included,
    ...Object.fromEntries(robustKeys.map((key) => {
      const value = board.mcs_robustness_qlike[key];
      return [`robust-${key}`, Array.isArray(value) ? value : value.included];
    })),
  };
  const grids = Object.values(board.mcs_qlike_by_grid);
  for (const row of robustness.rows) {
    for (const [column, list] of Object.entries(lists)) assert.equal(row.cells[column], list.includes(row.key), `${row.key} / ${column}`);
    assert.deepEqual(row.cells.grids, { in: grids.filter((list) => list.includes(row.key)).length, of: grids.length }, row.key);
  }

  // Rendered: one ● per membership, one ○ per absence, each with its word for screen readers.
  const markup = html(createElement(modl.McsMatrix, { robustness, level: 90 }));
  const memberships = robustness.rows.reduce((total, row) => total + Object.keys(lists).filter((column) => row.cells[column]).length, 0);
  const absences = robustness.rows.length * Object.keys(lists).length - memberships;
  assert.equal((markup.match(/<span aria-hidden="true">●<\/span><span class="sr-only">in<\/span>/g) ?? []).length, memberships);
  assert.equal((markup.match(/<span aria-hidden="true">○<\/span><span class="sr-only">out<\/span>/g) ?? []).length, absences);
  assert.match(markup, /<tr class="is-record">/, "the record's row is marked");
  assert.match(markup, /<tr class="is-challenger">/, "the challenger's row is marked");
});

test("the record's 'in the set under m of c settings' is counted from the payload", () => {
  const grids = Object.values(board.mcs_qlike_by_grid);
  const robust = Object.values(board.mcs_robustness_qlike).map((value) => (Array.isArray(value) ? value : value.included));
  const sets = [board.mcs_qlike_included, ...robust, board.mcs_mse_included, board.mcs_qlike_range_proxy_included];
  const inCount = sets.filter((list) => list.includes(champion)).length + grids.filter((list) => list.includes(champion)).length;
  const settings = sets.length + grids.length;
  const expected = `${label(champion)}: in the set under ${inCount} of ${settings} settings`;
  const robustness = comparison.mcsRobustness(lab);
  assert.equal(robustness.record.inCount, inCount);
  assert.equal(robustness.settings, settings);
  assert.ok(m.assertions.leaderboardDetail(m.research.leaderboardRows(lab), { robustness }).includes(expected));
  assert.ok(visibleText(section(renderDashboard(), "modl")).includes(expected), "shown in the MODL meta");
});

test("the per-grid table keeps the timing control below the ranking", () => {
  const rows = m.research.leaderboardRows(lab);
  // Even when the timing control comes first in the input.
  const shuffled = [...rows.filter((row) => row.timingControl), ...rows.filter((row) => !row.timingControl).reverse()];
  const markup = html(createElement(modl.GridTable, { rows: shuffled }));
  const trs = [...markup.split("<tbody>")[1].matchAll(/<tr\b([^>]*)>/g)].map((match) => match[1]);
  const controls = rows.filter((row) => row.timingControl).length;
  assert.ok(controls >= 1);
  assert.ok(trs.slice(-controls).every((attributes) => attributes.includes("is-timing-control")), "pinned last");
  assert.ok(trs.slice(0, -controls).every((attributes) => !attributes.includes("is-timing-control")));
  assert.match(markup, /chip--warn"[^>]*>timing control</, "and marked as in the leaderboard");
  // The panel names the view by what it switches to.
  assert.match(section(renderDashboard(), "modl"), />By grid<span class="sr-only"> view of Model leaderboard<\/span><\/button>/);
});

test("the QLIKE strip has no zero baseline and marks a far outlier off-scale, with its number", () => {
  const values = board.models.map((model) => model.qlike).sort((a, b) => a - b);
  const [second, worst] = values.slice(-2);
  const scale = comparison.qlikeScale(values);
  assert.ok(worst > (3 / 2) * second, "the fixture's persistence is far off the rest");
  assert.ok(Math.abs(scale.cap - (103 / 100) * second) < 1e-12);
  assert.ok(Math.abs(scale.lo - (values[0] - 1 / 200)) < 1e-12);
  assert.ok(scale.position(values[0]) > 0, "the best model is not at the track's end");
  const outlier = board.models.find((model) => model.qlike === worst);
  const markup = section(renderDashboard(), "modl");
  const rowOf = (key) => markup.match(new RegExp(`<tr id="modl-${escapeRegExp(key)}"[\\s\\S]*?</tr>`))?.[0] ?? "";
  assert.ok(rowOf(outlier.key).includes("›"), "drawn as › at the track's end");
  assert.ok(rowOf(outlier.key).includes(m.format.fmtNum(worst, 4)), "with the number still printed");
  for (const model of board.models.filter((item) => item.key !== outlier.key)) {
    assert.ok(!rowOf(model.key).includes("›") && rowOf(model.key).includes("bar-dot"), model.key);
  }
  // Without an outlier the scale ends at the worst model.
  assert.equal(comparison.qlikeScale([0.3, 0.35, 0.4]).cap, 0.4);
});

test("grid dots are one image to assistive tech", () => {
  const markup = html(createElement(modl.GridDots, { grids: [true, false, true, false, false] }));
  assert.match(markup, /^<span class="grid-dots" role="img" aria-label="in the set on 2 of 5 sampling grids">/);
});

test("the leaderboard keeps model, QLIKE, DM t and MCS columns on a phone, and its CSV holds what the cells show", () => {
  const columns = modl.leaderboardColumns({ recordName: "HAR-IV", scale: null, level: 90, mcsAvailable: true, utilityText: "" });
  for (const key of ["label", "qlike", "dmStat", "mcsP", "inMcs"]) {
    assert.equal(columns.find((column) => column.key === key).hideBelow, undefined, key);
  }
  assert.ok(columns.filter((column) => column.hideBelow === 640).length >= 4);
  const csv = m.table.csvColumns(columns);
  const value = (name, row) => csv.find((column) => column.label === name).value(row);
  const rows = m.research.leaderboardRows(lab);
  const record = rows.find((row) => row.role === "record");
  const control = rows.find((row) => row.timingControl);
  assert.equal(value("In set", record), record.inMcs ? "yes" : "no");
  assert.equal(value("Grids in set", record), `${record.grids.filter(Boolean).length} of ${record.grids.length}`);
  assert.equal(value("In set", control), "not eligible");
  assert.equal(value("Grids in set", control), "not eligible");
  assert.equal(value("Model", record), `${label(champion)} (record)`);
  assert.equal(value("QLIKE", record), record.qlike);
});

test("utility is defined on the page with its perfect-foresight maximum", () => {
  const text = visibleText(section(renderDashboard(), "modl"));
  assert.ok(text.includes(`Realized utility of a volatility-targeting investor, % per year (Bollerslev et al. 2018); perfect foresight ${m.format.fmtNum(board.realized_utility_max_pct, 2)}%.`));
});

// ------------------------------------------------------------------------------------------ STAB

test("STAB headlines the OVX-only regression, and its counts equal yearsBetter on the fixture", () => {
  const keys = [champion, challenger, "ovx_only", "raw_ovx", "gjr"];
  const years = m.research.yearlyQlike(lab, keys);
  const count = (a, b) => lab.yearly.filter((row) => row[`${a}_qlike`] < row[`${b}_qlike`]).length;
  const vsOvx = count(champion, "ovx_only");
  assert.deepEqual(m.research.yearsBetter(years, champion, "ovx_only"), { wins: vsOvx, total: lab.yearly.length });
  const title = `${label(champion)} beat the ${label("ovx_only")} in ${vsOvx} of ${lab.yearly.length} years; the challenger beat ${label(champion)} in ${count(challenger, champion)}`;
  assert.equal(m.assertions.stabilityAssertion(years, { champion, challenger, bench: "ovx_only", labels: m.research.modelLabels(lab) }), title);
  assert.ok(title.length <= 90, title);
  const text = visibleText(section(renderDashboard(), "stab"));
  assert.ok(text.includes(title), "the panel shows it");
  for (const key of ["gjr", "raw_ovx", "ovx_only"]) {
    assert.ok(text.includes(`vs ${label(key)}: ${count(champion, key)}/${lab.yearly.length} years`), key);
  }
});

test("the yearly gap rows draw one bar per year and model with both values, the challenger's included", () => {
  const keys = [champion, challenger, "ovx_only", "raw_ovx", "gjr"];
  const years = m.research.yearlyQlike(lab, keys);
  const models = ["ovx_only", "raw_ovx", "gjr", challenger].map((key) => ({ key, label: label(key), tone: key === challenger ? "alt" : "ink" }));
  const rows = comparison.yearlyGaps(years, champion, models);
  assert.deepEqual(rows.map((row) => row.key), models.map((model) => model.key));
  const finite = models.reduce((total, model) => total
    + lab.yearly.filter((row) => Number.isFinite(row[`${model.key}_qlike`]) && Number.isFinite(row[`${champion}_qlike`])).length, 0);
  const { limit, clipped } = comparison.gapLimit(rows.flatMap((row) => row.gaps.map((gap) => gap.value)));
  const markup = html(createElement(stabilityCharts.YearlyGapSvg, { rows, recordLabel: label(champion), limit, width: 600 }));
  assert.equal((markup.match(/class="gap-bar /g) ?? []).length, finite);
  assert.equal((markup.match(/class="gap-break"/g) ?? []).length, clipped, "every cut bar is marked");
  // Bars above the line (the record lower) are model blue; below it, the model's tone, never realized grey.
  assert.doesNotMatch(markup, /fill-truth/);
  const first = lab.yearly[0];
  const f = m.format.fmtNum;
  assert.ok(markup.includes(`<title>${first.year}: ${label("ovx_only")} ${f(first.ovx_only_qlike, 3)} vs ${label(champion)} ${f(first[`${champion}_qlike`], 3)}`));
  for (const row of rows) {
    assert.ok(markup.includes(`${row.label} · ${label(champion)} lower in ${count(champion, row.key)}/${lab.yearly.length}`), row.key);
  }
  // The placeholder reserves the drawn height, so mounting moves nothing below it.
  const height = stabilityCharts.yearlyGapHeight(rows.length);
  assert.match(markup, new RegExp(`^<svg width="600" height="${height}"`));
  const stab = section(renderDashboard(), "stab");
  assert.match(stab, new RegExp(`--lazy-h:${height}px;--lazy-h-narrow:${height}px`), "the STAB placeholder");
  function count(a, b) { return lab.yearly.filter((year) => year[`${a}_qlike`] < year[`${b}_qlike`]).length; }
});

test("without the OVX-only regression the STAB title states the challenger's count on its own", () => {
  const years = m.research.yearlyQlike(lab, [champion, challenger]);
  const wins = lab.yearly.filter((row) => row[`${challenger}_qlike`] < row[`${champion}_qlike`]).length;
  assert.equal(m.assertions.stabilityAssertion(years, { champion, challenger, bench: "ovx_only", labels: m.research.modelLabels(lab) }),
    `The challenger beat ${label(champion)} in ${wins} of ${lab.yearly.length} years`);
});

test("recent windows and the rolling gap are computed from the weekly series", () => {
  const s = lab.series;
  const weeks = s.date.map((date, i) => ({ actual: s.actual[i], record: s[champion][i], challenger: s.challenger[i] }))
    .filter((row) => row.actual > 0 && row.record > 0 && row.challenger > 0);
  const pairs = comparison.weeklyPairs(lab, champion);
  assert.equal(pairs.length, weeks.length);
  const windows = comparison.recentWindows(pairs, [26, 52, 156]);
  assert.deepEqual(windows.map((window) => window.weeks), [26, 52, 156]);
  for (const window of windows) {
    const tail = weeks.slice(-window.weeks);
    const mean = (key) => tail.reduce((total, row) => total + qlike(row.actual, row[key]), 0) / tail.length;
    assert.ok(Math.abs(window.record - mean("record")) < 1e-12, `record, ${window.weeks} weeks`);
    assert.ok(Math.abs(window.challenger - mean("challenger")) < 1e-12, `challenger, ${window.weeks} weeks`);
  }
  const rolling = comparison.rollingGap(pairs, 52);
  assert.equal(rolling.length, weeks.length - 51);
  const last = weeks.slice(-52).reduce((total, row) => total + qlike(row.actual, row.challenger) - qlike(row.actual, row.record), 0) / 52;
  assert.ok(Math.abs(rolling[rolling.length - 1].value - last) < 1e-9);
  // On the page: labelled as the weekly series, with each window's two means.
  const text = visibleText(section(renderDashboard(), "stab"));
  assert.ok(text.includes("weekly series (grid 0), not the every-day sample"));
  for (const window of windows) {
    assert.ok(text.includes(`Last ${window.weeks} weeks ${m.format.fmtNum(window.record, 3)} vs ${m.format.fmtNum(window.challenger, 3)}`), window.weeks);
  }
  assert.ok(text.includes(`Rolling 52-week QLIKE gap, challenger minus ${label(champion)}`));
  // Without the record's weekly forecasts nothing is compared.
  assert.deepEqual(comparison.weeklyPairs(lab, "no_such_model"), []);
  assert.deepEqual(comparison.recentWindows(pairs.slice(0, 10), [26]), []);
});

test("the DM axis notes run outward from zero and shorten under 300 px of plot", () => {
  const { dmAxisNotes } = m.svg;
  const wide = dmAxisNotes(300, 100, 500);
  assert.deepEqual(wide.map((note) => note.text), ["← worse than record", "better →"]);
  assert.deepEqual(wide.map((note) => note.anchor), ["end", "start"]);
  assert.ok(wide[0].x <= 300 && wide[1].x >= 300, "anchored at x(0), going outward");
  const narrow = dmAxisNotes(260, 140, 430);
  assert.deepEqual(narrow.map((note) => note.text), ["← worse", "better →"]);
  // The notes never overlap: the left one ends before the right one starts.
  for (const [zero, left, right] of [[260, 140, 430], [420, 140, 430], [150, 140, 430]]) {
    const [worse, better] = dmAxisNotes(zero, left, right);
    assert.ok(worse.x < better.x, `${zero}`);
    assert.ok(better.x + better.text.length * 6.6 <= right + 1e-9, "inside the plot on the right");
  }
});
