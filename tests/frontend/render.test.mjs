// Server-renders the whole dashboard against the fixtures (data.json v3 + research.json): the key
// text must appear, every panel must survive a missing research.json and an empty live record, and
// no statistic may be hardcoded in the source (each one must move when the payload moves).
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, visibleText } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
before(async () => { m = await loadDashboard(); });
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");
const quoteFor = (payload) => m.quote.resolveQuote(null, payload);

const render = (payload, researchPayload, status = researchPayload ? "ready" : "unavailable") => {
  const errors = [];
  const original = console.error;
  console.error = (...args) => errors.push(args.join(" "));
  try {
    const html = renderToStaticMarkup(createElement(m.dashboard.default, {
      data: payload, research: researchPayload, researchStatus: status, quote: quoteFor(payload),
    }));
    return { html, text: visibleText(html), errors };
  } finally {
    console.error = original;
  }
};

test("the full dashboard renders every section with its computed title", () => {
  const { html, text, errors } = render(data, research);
  assert.deepEqual(errors, [], "no section fell back to its error boundary");
  assert.ok(!/could not be displayed/.test(text), text.match(/[^.]*could not be displayed[^.]*/)?.[0]);
  for (const id of ["fcst", "modl", "stab", "calb", "risk", "cone", "vrp", "live", "leak", "stdy", "ledg", "meth", "mkt"]) {
    assert.match(html, new RegExp(`<section id="${id}" class="panel`), `section ${id}`);
  }
  const live = data.vol_forecast.live;
  assert.match(text, new RegExp(`${live.forecast_next_week_vol_annualized_pct} %`), "hero forecast");
  assert.ok(text.includes(`${live.interval_80.low_pct}–${live.interval_80.high_pct}%`), "80% interval");
  const lab = research.vol_lab;
  const modl = m.assertions.leaderboardAssertion(m.research.leaderboardRows(lab), { level: m.research.mcsLevel(lab) });
  assert.ok(modl && text.includes(modl), "the MODL title");
  const purged = research.postmortem.five_year.purged;
  assert.ok(text.includes(`purged accuracy ${purged.direction_acc_pct}%`), "retraction line");
  assert.ok(text.includes("RETRACTED") || text.includes("Retracted"));
  assert.ok(text.includes("CL=F continuous front month (unadjusted; rolls at expiry)"));
  assert.ok(text.includes("Research project") === false, "the footer is outside the dashboard body");
  // Section anatomy: chips, assertion titles, source footers, table toggles.
  assert.equal((html.match(/class="chip chip--code"/g) ?? []).length >= 13, true);
  assert.ok((html.match(/aria-pressed="false"[^>]*>Table<\/button>/g) ?? []).length >= 8, "table toggles");
  // Each panel names the command that rebuilds what it shows, not the lab's by default.
  const reproduce = (id) => html.match(new RegExp(`<section id="${id}"[\\s\\S]*?</section>`))?.[0].match(/<span class="micro">Reproduce<\/span>(.*?)<\/span>/s)?.[1] ?? "";
  for (const [id, commands] of Object.entries({
    fcst: ["backend.vol_lab", "backend.vol_forecast"],
    modl: ["backend.vol_lab"],
    risk: ["backend.vol_lab", "backend.vol_forecast"],
    vrp: ["backend.vol_forecast", "backend.vol_lab"],
    leak: ["backend.backtest_walk_forward", "backend.leak_anatomy"],
    stdy: ["backend.carry_signal_test", "backend.supply_shock_playbook"],
    ledg: ["backend.research_ledger"],
  })) {
    for (const command of commands) assert.ok(reproduce(id).includes(`python -m ${command}`), `${id} reproduces with ${command}`);
  }
  for (const id of ["leak", "stdy", "ledg"]) assert.ok(!reproduce(id).includes("backend.vol_lab"), `${id} is not built by vol_lab`);
  // Accessibility: one h2 per panel, KPI tiles in a dl.
  assert.equal((html.match(/<h2 /g) ?? []).length, 14);   // verdict + 13 panels
  assert.match(html, /<dl class="kpi-grid">(<div class="kpi">.*?<\/div>){6}<\/dl>/s);
});

test("the timing control is listed below the ranking, outside the MCS and every count", () => {
  const { html, text } = render(data, research);
  const modl = html.match(/<section id="modl"[\s\S]*?<\/section>/)[0];
  const bodies = [...modl.matchAll(/<tbody>([\s\S]*?)<\/tbody>/g)].map((match) => match[1]);
  const rows = [...bodies[bodies.length - 1].matchAll(/<tr( class="([^"]*)")?>/g)].map((match) => match[2] ?? "");
  const controls = rows.map((cls, index) => (cls.includes("is-timing-control") ? index : -1)).filter((index) => index >= 0);
  assert.ok(controls.length >= 1, "the timing control row is shown");
  assert.deepEqual(controls, controls.map((_, k) => rows.length - controls.length + k), "and pinned after the ranked rows");
  assert.match(modl, /chip--warn"[^>]*>timing control</);
  assert.match(modl, />not eligible</);
  const summary = m.research.mcsSummary(research.vol_lab);
  assert.ok(text.includes(`${summary.survivors}/${summary.eligible} models`), "the MCS tile counts eligible models only");
  assert.ok(text.includes("timing control excluded"));
});

test("the direction probability is demoted and labelled uncalibrated", () => {
  const { html } = render(data, research);
  const hero = html.match(/<div class="verdict-hero">[\s\S]*?<dl class="kpi-grid">/)[0];
  assert.match(hero, /<div class="fact fact--minor"><dt>P\(vol rises\)<\/dt><dd class="num">[^<]*<span class="fact-note">model-implied, uncalibrated<\/span>/);
  assert.doesNotMatch(hero, /accent-model/, "no accent colour");
  assert.doesNotMatch(hero, /[↑↓]/, "no direction arrow");
});

test("degraded payloads say exactly what is missing", () => {
  const noMcs = structuredClone(research);
  delete noMcs.vol_lab.leaderboard.mcs_qlike_included;
  delete noMcs.vol_lab.leaderboard.mcs_qlike_by_grid;
  for (const model of noMcs.vol_lab.leaderboard.models) { delete model.in_mcs_qlike; delete model.mcs_qlike_p; delete model.in_mcs_qlike_grids; }
  delete noMcs.vol_lab.var_backtest;
  const partial = render(data, noMcs);
  assert.deepEqual(partial.errors, []);
  assert.ok(partial.text.includes("no Model Confidence Set in research.json"));
  assert.ok(partial.text.includes("no VaR backtest in research.json"));
  assert.ok(!partial.text.includes("research.json unavailable"), "research.json did load");
  assert.doesNotMatch(partial.text, /Model Confidence Set 0\//, "never a count of zero it did not compute");

  const none = render(data, null);
  const retraction = visibleText(none.html.match(/<p class="retracted-line">[\s\S]*?<\/p>/)[0]);
  assert.equal((retraction.match(/look-ahead leak/g) ?? []).length, 1, "the retraction line says it once");
  const recent = data.vol_forecast.recent_oos.date.length;
  const fcst = none.html.match(/<section id="fcst"[\s\S]*?<\/section>/)[0];
  assert.ok(visibleText(fcst).includes(`${recent} weeks drawn`), "FCST meta describes the rows drawn");
  assert.ok(!visibleText(fcst).includes(`${data.vol_forecast.validation.n} weeks drawn`));
  assert.match(visibleText(fcst), /band drawn \(production validation\) covered/);

  const noLive = structuredClone(data);
  delete noLive.vol_forecast.live;
  const empty = render(noLive, research);
  assert.deepEqual(empty.errors, []);
  assert.ok(empty.text.includes("No live forecast in this snapshot"));
  assert.doesNotMatch(empty.html.match(/<div class="verdict-hero">[\s\S]*?<dl class="kpi-grid">/)[0], /hero-value/);
});

test("late commits and the promotion status render, and degrade when absent", () => {
  const payload = structuredClone(data);
  const record = payload.vol_forecast.live_record;
  record.recent = [{ ...record.recent[0], status: "late_commit" }];
  Object.assign(record, { n_late_commit: 1, n_skipped_roll: 0, promotion: {
    status: "accruing", n_independent_paired: 0, required: 52, dm_stat: null, p_value: null, mean_qlike_difference: null,
  } });
  const { html, text } = render(payload, research);
  const live = html.match(/<section id="live"[\s\S]*?<\/section>/)[0];
  assert.match(live, /chip--status-late_commit"[^>]*>late commit</);
  assert.match(live, /title="Not sealed in time \(less than five minutes before the settlement that opens its window, or never\); never scored"/);
  assert.ok(visibleText(live).includes("promotion accruing 0/52"));
  assert.ok(text.includes("1 not sealed in time (never scored)"));
  assert.doesNotMatch(text, /sealed after/);
  assert.ok(text.includes("promotion accruing 0/52 "), "the verdict tile carries the promotion status");
  const withoutPromotion = structuredClone(data);
  delete withoutPromotion.vol_forecast.live_record.promotion;
  const plain = render(withoutPromotion, research);
  assert.deepEqual(plain.errors, []);
  assert.ok(!/promotion accruing/.test(plain.text), "no promotion block, no promotion status");
  assert.ok(plain.text.includes("13:30 CT"), "the scoring note's ET time also reads in CT");
});

test("LEAK labels each Sharpe with its basis and shows the anatomy's caveats", () => {
  const { html } = render(data, research);
  const leak = visibleText(html.match(/<section id="leak"[\s\S]*?<\/section>/)[0]);
  const anatomy = research.leak_anatomy;
  const gross = anatomy.design.sharpe.split(":")[0].trim();
  assert.ok(leak.includes(`Sharpe ${anatomy.unpurged_sharpe_mean.toFixed(2)}, ${gross}`), "the noise tile is gross");
  assert.ok(leak.includes(`Sharpe ${anatomy.retracted_reference.sharpe.toFixed(2)} ${anatomy.retracted_reference.sharpe_basis}`), "the retracted tile is after costs");
  assert.ok(leak.includes(anatomy.conclusion), "the artifact's conclusion, caveats included");
  // CL=F rows mark their own base rate; 50% is named as the random walk's no-skill line only.
  const svgSource = m.svg.LeakDumbbell.toString();
  assert.match(svgSource, /50% = no skill on the random walk/);
  assert.doesNotMatch(svgSource, /coin flip/);
});

test("STDY shows both placebo tests and highlights a group only when both reject", () => {
  const { html } = render(data, research);
  const stdy = html.match(/<section id="stdy"[\s\S]*?<\/section>/)[0];
  const text = visibleText(stdy);
  assert.equal((stdy.match(/<th scope="col" class="align-right wrap-head" title="(Peak|Settle) p: [^"]*">vs placebo</g) ?? []).length, 2, "peak and settle, unmatched placebo");
  assert.equal((stdy.match(/<th scope="col" class="align-right wrap-head" title="(Peak|Settle) p: [^"]*">year-matched</g) ?? []).length, 2, "peak and settle, year-matched placebo");
  const study = m.research.supplyStudy(research);
  const passing = study.groups.filter((group) => m.assertions.placeboVerdict(group.peakP, group.peakPYear) === "both"
    || m.assertions.placeboVerdict(group.settleP, group.settlePYear) === "both");
  assert.ok(passing.length > 0, "the fixture has a group that passes both placebos");
  assert.equal((stdy.match(/<tr class="passes-rule"/g) ?? []).length, passing.length);
  const unmatchedOnly = study.groups.find((group) => m.assertions.placeboVerdict(group.peakP, group.peakPYear) === "one");
  assert.ok(unmatchedOnly, "the fixture has a group that passes one placebo only");
  const sentence = research.supply_study.conclusion.split(/(?<=\.)\s/).find((item) => /not the year-matched/.test(item));
  assert.ok(sentence && text.includes(sentence), "the groups that pass only the unmatched placebo are named");
  assert.ok(text.includes(research.supply_study.overlapping_events.definition), "the overlap caveat");
  assert.match(stdy, /title="Windows share sessions: /);
});

test("canvas charts carry an accessible summary of what they draw", async () => {
  const { createElement: h } = await import("react");
  const rows = m.research.forecastRows(research.vol_lab, data.vol_forecast.recent_oos);
  const forecast = renderToStaticMarkup(h(m.forecastChart.default, { rows, live: null, modelLabel: "HAR-IV", bandLabel: "benchmark lab" }));
  assert.match(forecast, new RegExp(`role="img" aria-label="Weekly HAR-IV forecast against realized next-week volatility, ${rows.length} weeks from `));
  assert.match(forecast, /with the 80% band \(benchmark lab\) and OVX/);
  const series = m.research.varSeries(research.vol_lab);
  const counts = m.research.countExceedances(series);
  const varChart = renderToStaticMarkup(h(m.varChart.default, { rows: series }));
  assert.match(varChart, new RegExp(`role="img" aria-label="Weekly 5-session log returns, ${series.length} weeks`));
  assert.ok(varChart.includes(`${counts.e99} of ${counts.n} weeks fell below the 99% line and ${counts.e95} below the 95% line`));
  assert.match(varChart, /5-session log return/);
});

test("the retired direction model has no output, overlay or sizing anywhere", () => {
  const { text } = render(data, research);
  assert.doesNotMatch(text, /Kelly|half-Kelly|LONG LEAN|SHORT LEAN|Show retracted model output/i);
  assert.doesNotMatch(text, /billion|institutional-grade|generates alpha|novel alpha/i, "no hype words");
});

test("without research.json every panel still renders, with an explicit note", () => {
  for (const status of ["unavailable", "loading"]) {
    const { text, errors } = render(data, null, status);
    assert.deepEqual(errors, []);
    assert.ok(!/could not be displayed/.test(text));
    assert.ok(text.includes(`${data.vol_forecast.live.forecast_next_week_vol_annualized_pct} %`), "the hero still comes from data.json");
    assert.ok(text.includes(status === "loading" ? "Loading research.json" : "research.json is not available"));
    assert.ok(text.includes("Forward record started"), "the live record needs only data.json");
  }
});

test("an empty live record says when the first result is due instead of showing a blank table", () => {
  const empty = structuredClone(data);
  Object.assign(empty.vol_forecast.live_record, { n_recorded: 0, n_skipped_roll: 0, recent: [], next_result_due: null });
  const none = render(empty, research).text;
  assert.ok(none.includes("No calls recorded yet"));
  assert.ok(none.includes("Results appear once the first target window closes."), "next_result_due null: no date");
  assert.doesNotMatch(none, /first result due/);
  empty.vol_forecast.live_record.next_result_due = "2026-10-27";
  assert.match(render(empty, research).text, /first result due Oct 27, 2026/, "the record's own date, not an estimate");
  delete empty.vol_forecast.live_record.next_result_due;
  const { text } = render(empty, research);
  assert.match(text, /first result due about [A-Z][a-z]{2} \d+, \d{4}/, "older payloads: estimated from the start date");
  const missing = structuredClone(data);
  delete missing.vol_forecast.live_record;
  assert.ok(render(missing, research).text.includes("No live record is in this snapshot"));
});

test("a malformed payload costs one section, not the page", () => {
  const broken = structuredClone(data);
  broken.vol_forecast.recent_oos = { date: "not-an-array" };
  broken.supply_shock_playbook = "garbage";
  broken.vol_forecast.economic = null;
  const { text } = render(broken, { vol_lab: { leaderboard: { models: "nope" } }, postmortem: 7 });
  assert.ok(text.includes(`${data.vol_forecast.live.forecast_next_week_vol_annualized_pct} %`));
});

// ------------------------------------------------------------------------- no hardcoded stats
/** Every number in a payload scaled by `factor` (integers stay integers); schema versions kept. */
const perturb = (value, factor, key = "") => {
  if (typeof value === "number") {
    if (key === "schema_version") return value;
    const scaled = value * factor;
    return Number.isInteger(value) ? Math.round(scaled) : Number(scaled.toPrecision(6));
  }
  if (Array.isArray(value)) return value.map((item) => perturb(item, factor, key));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, perturb(v, factor, k)]));
  }
  return value;
};

test("every headline statistic moves with the payload (none is hardcoded)", () => {
  const live = data.vol_forecast.live;
  const v = data.vol_forecast.validation;
  const pm = research.postmortem.five_year;
  const headline = [
    `${live.forecast_next_week_vol_annualized_pct} %`,
    `${v.har_qlike}`,
    `${v.interval_80_coverage_pct}%`,
    `purged accuracy ${pm.purged.direction_acc_pct}%`,
    `Sharpe of ${pm.leaked.sharpe.toFixed(2)}`,
    `${research.supply_study.placebo.median_peak_pct}% on an ordinary date`,
    // The computed anatomy title (the artifact's conclusion prose, also shown, quotes the same figure).
    `unpurged forest scores ${research.leak_anatomy.unpurged_dir_acc_pct_mean.toFixed(1)}%`,
    `${research.ledger.summary.supported_surviving_bh_5pct.length} of ${research.ledger.summary.supported.length} supported claims`,
  ];
  const original = render(data, research).text;
  for (const snippet of headline) assert.ok(original.includes(snippet), `fixture render shows "${snippet}"`);
  const movedResearch = perturb(research, 1.37);
  // Ledger counts are list lengths, which scaling numbers does not change: shorten the lists.
  movedResearch.ledger.summary.supported = movedResearch.ledger.summary.supported.slice(2);
  movedResearch.ledger.summary.supported_surviving_bh_5pct = movedResearch.ledger.summary.supported_surviving_bh_5pct.slice(1);
  const moved = render(perturb(data, 1.37), movedResearch).text;
  for (const snippet of headline) assert.ok(!moved.includes(snippet), `"${snippet}" did not move with the payload`);
});

test("no source file restates a statistic from the fixtures as a literal", () => {
  // Collect the distinctive decimals in the fixtures (e.g. 47.6, 0.376, 2.466) ...
  // Only distinctive values count: at least three significant digits (47.6, 0.376, 2.47), so
  // generic geometry and opacity constants (0.4, 1.5) are not mistaken for statistics.
  const decimals = new Set();
  const significant = (text) => text.replace(/^-?0*\.?0*/, "").replace(".", "").length;
  const walk = (value) => {
    if (typeof value === "number" && !Number.isInteger(value)) {
      for (const digits of [1, 2, 3]) {
        const text = value.toFixed(digits).replace(/^-/, "");
        if (!/\.0+$/.test(text) && significant(text) >= 3) decimals.add(text);
      }
    } else if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === "object") Object.values(value).forEach(walk);
  };
  walk(data.vol_forecast.live); walk(data.vol_forecast.validation); walk(data.vol_forecast.economic);
  walk(research.postmortem.five_year.leaked.sharpe); walk(research.postmortem.five_year.purged);
  walk(research.vol_lab.leaderboard.spa_qlike_vs_har_iv); walk(research.leak_anatomy.unpurged_dir_acc_pct_mean);
  walk(research.supply_study.placebo);
  // ... and fail on any of them written as a numeric literal in src/ (design constants are integers
  // or listed here with their reason).
  const allowed = new Set([
    "1.96", // the two-sided 5% critical value of the normal distribution, used for reference lines
  ]);
  const root = fileURLToPath(new URL("../../src", import.meta.url));
  const files = [];
  const collect = (dir) => readdirSync(dir).forEach((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) collect(path);
    else if (/\.(jsx?|mjs)$/.test(name)) files.push(path);
  });
  collect(root);
  const offenders = [];
  for (const file of files) {
    // Comments and quoted strings (SVG path data, CSS values) are not code numbers.
    const code = readFileSync(file, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/.*$/gm, "$1")
      .replace(/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/g, "\"\"");
    for (const match of code.matchAll(/(?<![\w.#-])(\d+\.\d+)(?![\w.])/g)) {
      if (decimals.has(match[1]) && !allowed.has(match[1])) offenders.push(`${file.slice(root.length)}: ${match[1]}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test("the VRP table compares the two short-vol proxies like for like", () => {
  const vrp = data.vol_forecast.economic.variance_risk_premium;
  const rows = m.vrp.vrpStatRows(vrp);
  const swap = rows.find((row) => row.key === "swap");
  assert.ok(swap.note.includes(String(vrp.worst_year)));
  const half = { ...vrp };
  delete half.short_vol_swap_sharpe_ex_worst_year;
  assert.ok(m.vrp.vrpStatRows(half).every((row) => !String(row.note ?? "").includes("excl.")), "one proxy alone is not shown ex-worst-year");
});

test("the ledger filter narrows by family and status", () => {
  const rows = research.ledger.rows;
  const { filterLedger } = m.ledger;
  assert.equal(filterLedger(rows, "all", "all").length, rows.length);
  const retracted = filterLedger(rows, "all", "retracted");
  assert.ok(retracted.length > 0 && retracted.every((row) => row.status === "retracted"));
  const family = rows[0].family;
  assert.ok(filterLedger(rows, family, "all").every((row) => row.family === family));
});
