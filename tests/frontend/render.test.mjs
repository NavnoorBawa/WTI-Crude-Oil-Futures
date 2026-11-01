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
import { SECTIONS } from "../../src/app/sections.js";
import { loadDashboard, readFixture, readSource, visibleText } from "./load.mjs";

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
  assert.equal((html.match(/class="chip chip--code[ "]/g) ?? []).length >= 13, true);
  assert.ok((html.match(/aria-pressed="false"[^>]*>(?:Table|By grid)<span class="sr-only"> view of [^<]+<\/span><\/button>/g) ?? []).length >= 8, "table toggles");
  // Each panel names the command that rebuilds what it shows, not the lab's by default.
  const reproduce = (id) => html.match(new RegExp(`<section id="${id}"[\\s\\S]*?</section>`))?.[0].match(/<span class="micro">Reproduce<\/span>(.*?)<\/footer>/s)?.[1] ?? "";
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
  // The leaderboard is the section's first table; the membership matrix under it lists eligible models only.
  const bodies = [...modl.matchAll(/<tbody>([\s\S]*?)<\/tbody>/g)].map((match) => match[1]);
  const rows = [...bodies[0].matchAll(/<tr\b([^>]*)>/g)].map((match) => /class="([^"]*)"/.exec(match[1])?.[1] ?? "");
  const controls = rows.map((cls, index) => (cls.includes("is-timing-control") ? index : -1)).filter((index) => index >= 0);
  assert.ok(controls.length >= 1, "the timing control row is shown");
  assert.deepEqual(controls, controls.map((_, k) => rows.length - controls.length + k), "and pinned after the ranked rows");
  assert.ok(bodies.slice(1).every((body) => !/timing control/.test(body)), "and left out of the membership matrix");
  assert.match(modl, /chip--warn"[^>]*>timing control</);
  assert.match(modl, />not eligible</);
  const summary = m.research.mcsSummary(research.vol_lab);
  assert.ok(text.includes(`${summary.survivors}/${summary.eligible} models`), "the MCS tile counts eligible models only");
  assert.ok(text.includes("timing control excluded"));
});

test("the direction probability is demoted and labelled uncalibrated", () => {
  const { html } = render(data, research);
  const hero = html.match(/<div class="verdict-hero">[\s\S]*?<dl class="kpi-grid">/)[0];
  // Not a fact beside the forecast: one sentence in the hero's notes, its model stated.
  assert.doesNotMatch(hero.match(/<dl class="hero-facts">[\s\S]*?<\/dl>/)[0], /P\(vol rises\)/);
  const line = visibleText(hero.match(/<li class="hero-prob">[\s\S]*?<\/li>/)[0]);
  assert.match(line, /P\(vol rises\): model-implied chance .*: \d+% \(lognormal .*not calibrated; direction is not evidence of skill\)/);
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
  assert.match(visibleText(fcst), /Bright points are weeks whose realized vol fell outside the 80% band; \d+ of the last \d+ production weeks fell inside\./);
  assert.doesNotMatch(visibleText(fcst), /lab; production|1 week 80%/, "no pipeline label spliced into a phrase");

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
  assert.ok(visibleText(live).includes("promotion test: accruing 0/52 weeks"));
  assert.ok(text.includes("1 not sealed in time (never scored)"));
  assert.doesNotMatch(text, /sealed after/);
  assert.ok(text.includes("promotion test 0/52 weeks"), "the verdict tile carries the promotion status");
  const withoutPromotion = structuredClone(data);
  delete withoutPromotion.vol_forecast.live_record.promotion;
  const plain = render(withoutPromotion, research);
  assert.deepEqual(plain.errors, []);
  assert.ok(!/accruing|promotion test \d|Promotion test \d/.test(plain.text), "no promotion block, no promotion status");
  assert.ok(plain.text.includes("13:30 CT"), "the scoring note's ET time also reads in CT");
});

test("LEAK labels each Sharpe with its basis and shows the anatomy's caveats", () => {
  const { html } = render(data, research);
  const leak = visibleText(html.match(/<section id="leak"[\s\S]*?<\/section>/)[0]);
  const anatomy = research.leak_anatomy;
  const gross = anatomy.design.sharpe.split(":")[0].trim();
  assert.ok(leak.includes(`Sharpe ${anatomy.unpurged_sharpe_mean.toFixed(2)}, ${gross}`), "the noise tile is gross");
  assert.ok(leak.includes(`Sharpe ${anatomy.retracted_reference.sharpe.toFixed(2)} ${anatomy.retracted_reference.sharpe_basis}`), "the retracted tile is after costs");
  assert.ok(leak.includes(m.format.tidyProse(anatomy.conclusion).replace(/\s+/g, " ")), "the artifact's conclusion, caveats included");
  // CL=F rows mark their own base rate; 50% is named as the random walk's no-skill line only.
  const svgSource = m.svg.LeakDumbbell.toString();
  assert.match(svgSource, /50% = no skill on the random walk/);
  assert.doesNotMatch(svgSource, /coin flip/);
});

test("STDY shows both placebo tests and highlights a group only when both reject", () => {
  const { html } = render(data, research);
  const stdy = html.match(/<section id="stdy"[\s\S]*?<\/section>/)[0];
  const text = visibleText(stdy);
  assert.equal((stdy.match(/<th scope="col" class="align-right wrap-head" title="(Peak|Settle) p: [^"]*"><span class="th-label">vs placebo</g) ?? []).length, 2, "peak and settle, unmatched placebo");
  assert.equal((stdy.match(/<th scope="col" class="align-right wrap-head" title="(Peak|Settle) p: [^"]*"><span class="th-label">year-matched</g) ?? []).length, 2, "peak and settle, year-matched placebo");
  const study = m.research.supplyStudy(research);
  const passing = study.groups.filter((group) => m.assertions.placeboVerdict(group.peakP, group.peakPYear) === "both"
    || m.assertions.placeboVerdict(group.settleP, group.settlePYear) === "both");
  assert.ok(passing.length > 0, "the fixture has a group that passes both placebos");
  assert.equal((stdy.match(/<tr class="passes-rule"/g) ?? []).length, passing.length);
  const oneSided = study.groups.filter((group) => m.assertions.placeboVerdict(group.peakP, group.peakPYear) === "one");
  assert.ok(oneSided.length > 0, "the fixture has a group that passes one placebo only");
  for (const group of oneSided) {
    const p3 = m.format.fmtPValue(group.peakP, { digits: 3 });
    const pText = /^[<>]/.test(p3) ? `p ${p3}` : `p = ${p3}`;
    assert.ok(text.includes(`${group.name} (n = ${group.n}; ${pText.replace(/\s/g, " ")} vs placebo`), `${group.key} is named with its n and p`);
  }
  assert.ok(text.includes(research.supply_study.overlapping_events.definition), "the overlap caveat");
  assert.match(stdy, /title="Windows share sessions: /);
  // The overlapping events are named, not keyed, and the study's p-values print like the table's.
  for (const pair of research.supply_study.overlapping_events.pairs) {
    assert.ok(text.includes(pair.events.map(m.research.eventName).join(" and ")), pair.events.join(" "));
    for (const id of pair.events) assert.ok(!text.includes(id), id);
  }
  assert.doesNotMatch(text, /p = 0\.\d{4}|data\/research_ledger\.json/);
});

test("canvas charts carry an accessible summary of what they draw", async () => {
  const { createElement: h } = await import("react");
  const rows = m.research.forecastRows(research.vol_lab, data.vol_forecast.recent_oos);
  // A focusable group with a short name, described by the full summary; the plot host is hidden.
  const description = (html) => {
    const id = html.match(/aria-describedby="([^"]+)"/)?.[1];
    assert.ok(id, "the figure is described by its summary");
    return html.match(new RegExp(`<p id="${id.replace(/[^\w-]/g, "\\$&")}" class="sr-only">([^<]*)</p>`))?.[1] ?? "";
  };
  const forecast = renderToStaticMarkup(h(m.forecastChart.default, { rows, live: null, modelLabel: "HAR-IV" }));
  assert.match(forecast, /<div class="lw-figure" tabindex="0" role="group" aria-roledescription="chart" aria-label="HAR-IV forecast vs realized weekly volatility, [\d,]+ weeks, /);
  assert.match(forecast, /class="lw-host lw-host--forecast" aria-hidden="true"/);
  assert.match(description(forecast), new RegExp(`^Weekly HAR-IV forecast against realized next-week volatility, ${rows.length} weeks from `));
  assert.match(description(forecast), /with the 80% band \(production for \d+ weeks, benchmark lab for \d+ weeks\) and OVX/);
  const series = m.research.varSeries(research.vol_lab);
  const counts = (await m.load("/src/lib/var.js")).drawnBreachCounts(series);
  const varChart = renderToStaticMarkup(h(m.varChart.default, { rows: series }));
  assert.match(varChart, /role="group" aria-roledescription="chart" aria-label="Weekly log returns against the 95% and 99% VaR, /);
  assert.match(description(varChart), new RegExp(`^Weekly 5-session log returns, ${series.length} weeks`));
  assert.ok(description(varChart).includes(`${counts.e99} of ${counts.n} weeks fell below the 99% line and ${counts.e95} below the 95% line`));
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
    assert.ok(text.includes(status === "loading" ? "Loading research.json" : "research.json did not load; see the notice under the verdict."));
    assert.ok(text.includes("No live forecast has resolved yet"), "the live record needs only data.json");
  }
});

test("an empty live record says when the first result is due instead of showing a blank table", () => {
  const empty = structuredClone(data);
  Object.assign(empty.vol_forecast.live_record, { n_recorded: 0, n_skipped_roll: 0, recent: [], next_result_due: null });
  const none = render(empty, research).text;
  assert.ok(none.includes("No calls recorded yet"));
  assert.ok(none.includes("Results appear once the first target window closes."), "next_result_due null: no date");
  assert.doesNotMatch(none, /the first is due/);
  empty.vol_forecast.live_record.next_result_due = "2026-10-27";
  assert.match(render(empty, research).text, /the first is due Oct 27, 2026/, "the record's own date, not an estimate");
  delete empty.vol_forecast.live_record.next_result_due;
  assert.doesNotMatch(render(empty, research).text, /the first is due/, "no date is estimated");
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
    `${research.ledger.summary.supported_surviving_bh_5pct.length} of ${research.ledger.summary.supported.length
      - research.ledger.summary.supported_selected_after_search.length} supported claims`,
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
  walk(JSON.parse(readSource("data/vol_lab.json")).qlike_floor);       // the proxy's noise floor (0.213 and the rest)
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

test("the ledger filter narrows by family and status", async () => {
  const rows = research.ledger.rows;
  // As LedgerPanel calls it: the filters, the search and the text as the page shows it.
  const { filterLedgerRows } = await m.load("/src/lib/reference.js");
  const labels = m.research.modelLabels(research.vol_lab);
  const display = (text) => m.research.ledgerText(text, { labels });
  const filter = (family, status, query = "") => filterLedgerRows(rows, { family, status, query }, display);
  assert.equal(filter("all", "all").length, rows.length);
  const retracted = filter("all", "retracted");
  assert.ok(retracted.length > 0 && retracted.every((row) => row.status === "retracted"));
  const family = rows[0].family;
  assert.ok(filter(family, "all").every((row) => row.family === family));
  // A search narrows within the filters: every term must match the hypothesis, test, id or family.
  const searched = filter(family, "all", "qlike");
  assert.ok(searched.length < filter(family, "all").length);
  assert.ok(searched.every((row) => row.family === family && /qlike/i.test([row.hypothesis, row.test, row.id].join(" "))));
});

const section = (html, id) => html.match(new RegExp(`<section id="${id}"[\\s\\S]*?</section>`))?.[0] ?? "";

test("every section title makes one claim, short enough to scan", () => {
  const { html } = render(data, research);
  const titles = [...html.matchAll(/<(h2|h3)[^>]*>([\s\S]*?)<\/\1>/g)].map((match) => visibleText(match[2]).trim());
  assert.ok(titles.length >= 20);
  for (const title of titles) assert.ok(title.length <= 90, `${title.length}: ${title}`);
});

test("the ledger shows a failure to reject as not supported, and keeps the post-selection row out of the adjustment", async () => {
  const reference = await m.load("/src/lib/reference.js");
  const { html } = render(data, research);
  const ledg = section(html, "ledg");
  const summary = research.ledger.summary;
  const text = visibleText(ledg);
  assert.doesNotMatch(text, /\brejected\b/);
  // The first rows of the default order are on screen (the rest behind "Show all"), each status as its label.
  const ledger = m.research.researchLedger(research);
  const order = [{ key: "status", sortValue: reference.ledgerOrderValue }];
  const shown = m.table.orderRows(ledger.rows, order, { key: "status", direction: "asc" }).slice(0, reference.LEDGER_PAGE);
  for (const [status, label] of [["supported", "supported"], ["inconclusive", "inconclusive"], ["not_supported", "not supported"], ["retracted", "retracted"]]) {
    const chips = (ledg.match(new RegExp(`class="chip chip--[a-z_-]+"[^>]*>${label}<`, "g")) ?? []).length;
    assert.equal(chips, shown.filter((row) => row.status === status).length, label);
    assert.ok(text.includes(`${summary.by_status[status]} ${label}`), `the meta counts every ${label} row`);
  }
  assert.equal((ledg.match(/>underpowered</g) ?? []).length, shown.filter((row) => summary.underpowered.includes(row.id)).length);
  assert.ok(text.includes(`${summary.underpowered.length} underpowered`));
  assert.match(text, /a failure to reject, not evidence against the hypothesis/);
  for (const id of summary.selected_after_search) {
    const rowHtml = ledg.match(new RegExp(`<tr[^>]*>(?:(?!</tr>)[\\s\\S])*?>${id}</code>[\\s\\S]*?</tr>`))?.[0];
    assert.ok(rowHtml, id);
    assert.match(rowHtml, />selected after search</);
    assert.equal((rowHtml.match(/>excluded</g) ?? []).length, 2, "no BH or t > 3 verdict");
    assert.doesNotMatch(rowHtml, />yes</, "never shown as surviving an adjustment it was not in");
  }
  // One phrase reconciles the supported count with the title's "of N supported claims".
  const outside = summary.supported_selected_after_search.length;
  assert.ok(text.includes(`${summary.supported.length} supported; ${summary.supported.length - outside} in the adjusted family (${outside} selected after a search)`));
});

test("the hero names the OVX-only regression as the leaderboard does, with the leaderboard's DM sign", () => {
  const { html, text } = render(data, research);
  const label = m.research.ovxOnlyLabel(research.vol_lab);
  const ovxOnly = m.research.leaderboardRows(research.vol_lab).find((row) => row.key === m.research.OVX_ONLY_KEY);
  const f = m.format;
  const tile = visibleText(html.match(/<div class="kpi">[\s\S]*?<\/div>/)[0]);
  assert.ok(tile.includes(`${f.fmtNum(ovxOnly.qlike, 3)} ${label}`), "the QLIKE tile's comparator name");
  assert.doesNotMatch(tile, /DM/, "the DM statistics are the lede's, not repeated in the tile");
  // The lede detail (src/panels/VerdictLede.jsx) carries the DM t in the leaderboard's sign: + = lower loss than the record.
  if (readdirSync(fileURLToPath(new URL("../../src/panels", import.meta.url))).includes("VerdictLede.jsx")) {
    const detail = html.match(/<p class="verdict-answer-detail">([\s\S]*?)<\/p>/)?.[1];
    assert.ok(detail, "the lede detail renders");
    assert.ok(visibleText(detail).includes(`${label} ${f.fmtSigned(ovxOnly.dmStat, 2)}`), "lede DM sign");
  }
  assert.ok(text.includes(`not distinguishable from the ${label}`), "FCST title");
  assert.ok(visibleText(section(html, "modl")).includes(label), "the same name in the leaderboard");
});

test("LIVE compares the two models on the same calls and labels the all-calls QLIKE as its own sample", () => {
  const payload = structuredClone(data);
  const record = payload.vol_forecast.live_record;
  Object.assign(record, {
    n_recorded: 30, n_resolved: 30, n_pending: 0, n_independent: 11, next_result_due: null,
    champion: { ...record.champion, n: 30, qlike: 0.815, interval_80_coverage_pct: 66.7, mean_forecast_pct: 40.1, mean_realized_pct: 44.2 },
    challenger: { ...record.challenger, n: 29, qlike: 0.0187, champion_qlike: 0.0213 },
    promotion: { ...record.promotion, n_independent_paired: 10, n_excluded_no_challenger: 1 },
  });
  const { html, text } = render(payload, research);
  const live = visibleText(section(html, "live"));
  const f = m.format;
  const [a, b] = [f.fmtNum(record.challenger.champion_qlike, 3), f.fmtNum(record.challenger.qlike, 3)];
  // One resolved call has no challenger forecast, so the title names the paired calls, not every resolved one.
  assert.ok(live.includes(`On the ${record.challenger.n} resolved calls with both forecasts, QLIKE ${a} record vs ${b} challenger`), "title");
  assert.ok(live.includes(`QLIKE, same calls ${a} / ${b} record / HAR-IV-range, n ${record.challenger.n}`), "paired fact");
  assert.ok(live.includes(`QLIKE, record, all calls ${f.fmtNum(record.champion.qlike, 3)} ${record.champion.model_version}, n ${record.champion.n}`), "all-calls fact");
  assert.ok(live.includes(`pinned pair: HAR-IV (${record.promotion.model_version}) vs HAR-IV-range`));
  assert.ok(live.includes("Resolved calls left out of it: 1 with no challenger forecast."));
  assert.ok(text.includes(`${record.n_resolved} calls resolved (${record.n_independent} independent weeks) · QLIKE ${a} record vs ${b} challenger, same ${record.challenger.n} calls`), "verdict tile");
  assert.ok(!text.includes(`QLIKE ${f.fmtNum(record.champion.qlike, 3)} for the`), "never the mixed-sample pair");
  assert.doesNotMatch(live, /live weeks?\b/);
});

test("a missing challenger, OVX source or economic block says why in one neutral note", () => {
  const payload = structuredClone(data);
  Object.assign(payload.vol_forecast.live, {
    challenger: null, challenger_error: "RuntimeError: CL=F OHLC bars unavailable",
    implied_vol_ovx_pct: null, ovx_error: "Yahoo ^OVX: timeout; FRED OVXCLS: timeout",
  });
  payload.vol_forecast.economic = { unavailable_reason: "no same-contract returns (HTTPError)" };
  // The record entry of this same issuance carries the inputs it was computed from: no OVX either.
  const entry = payload.vol_forecast.live_record.recent.find((row) => row.issued_at === payload.vol_forecast.live.issued_at);
  if (entry?.inputs) Object.assign(entry.inputs, { ovx: null, ovx_session: null });
  const { html, text, errors } = render(payload, research);
  assert.deepEqual(errors, []);
  assert.ok(text.includes("Challenger — not published this session"), "hero challenger");
  assert.ok(text.includes("no OVX source this session; forecast without OVX"), "hero OVX");
  const live = visibleText(section(html, "live"));
  assert.ok(live.includes("No challenger forecast in this snapshot, so this session's call cannot enter the promotion test."));
  assert.ok(live.includes("Reason recorded: RuntimeError: CL=F OHLC bars unavailable"));
  for (const [id, what] of [["risk", "Volatility targeting"], ["vrp", "The variance risk premium"]]) {
    const body = visibleText(section(html, id));
    assert.ok(body.includes(`${what} is not in this snapshot`), id);
    assert.ok(body.includes("The economic tests did not run: no same-contract returns (HTTPError)."), id);
  }
});

test("VRP reads the share of positive periods against the zero-premium null, and the premium in variance units", () => {
  const { html } = render(data, research);
  const vrp = data.vol_forecast.economic.variance_risk_premium;
  const body = visibleText(section(html, "vrp"));
  const f = m.format;
  const pFor = f.fmtP(vrp.zero_premium_null.p_value_one_sided).replace(/\s/g, " ");
  assert.ok(body.includes(`OVX was above realized vol in ${f.fmtPct(vrp.share_positive_pct)} of periods against `
    + `${f.fmtPct(vrp.zero_premium_null.share_positive_pct)} expected with no premium (${pFor}, one-sided block bootstrap)`), "caption");
  assert.ok(body.includes("the premium in variance units is the measure"));
  const rows = m.vrp.vrpStatRows(vrp);
  assert.equal(rows[1].key, "meanVariance", "the variance-unit premium leads the statistics");
  assert.ok(rows.find((row) => row.key === "share").note.includes(`${f.fmtPct(vrp.zero_premium_null.share_positive_pct)} expected with no premium`));
  assert.ok(body.includes("Cboe Crude Oil ETF Volatility Index") && !body.includes("CBOE"), "the index's own spelling");
});

test("the economic tests state their span and why it ends before the other panels", () => {
  const { html } = render(data, research);
  const returns = data.vol_forecast.economic.returns;
  const span = m.format.fmtSpan(returns.sample_start, returns.sample_end);
  const vrp = visibleText(section(html, "vrp"));
  assert.ok(vrp.includes(`${span} (same-contract returns end with the committed EIA curve)`), "VRP meta");
  const risk = visibleText(section(html, "risk"));
  assert.ok(risk.includes(`${data.vol_forecast.economic.vol_target_overlay.n_weeks} weeks, ${span}`), "sizing caption");
  for (const body of [vrp, risk]) assert.ok(body.includes(`The sample ends in ${m.format.fmtMonthYear(returns.sample_end)} because these tests use same-contract returns`));
});

test("every section with a code chip is in the top-bar navigation, in page order", () => {
  const { html } = render(data, research);
  const ids = [...html.matchAll(/<section id="([a-z]+)" class="panel/g)].map((match) => match[1]);
  assert.deepEqual(SECTIONS.map((item) => item.id), ids);
});

test("no internal identifier or repository path is shown as text", () => {
  const payload = structuredClone(data);
  payload.vol_forecast.live_record.recent = [{ ...payload.vol_forecast.live_record.recent[0], status: "late_commit" }];
  const text = render(payload, research).text.replace(/\b(?:dir|vol|carry|leak|var|vrp|shock)_[a-z0-9_]+\b/g, "");   // ledger row ids
  for (const token of ["har_iv_sameday", "late_commit", "skipped_roll", "hurricane_katrina_2005", "with_macro", "same_day vs",
    "supply_shock_study.json", "mcs_size", "mcs_members", "data/research_ledger.json", "Gibbs-Candes"]) {
    assert.ok(!text.includes(token), token);
  }
});

test("the share image is a static 1200x630 PNG behind absolute URLs", () => {
  const head = readSource("index.html");
  const meta = (attr, name) => head.match(new RegExp(`<meta\\s+${attr}="${name}"\\s+content="([^"]+)"`))?.[1];
  const site = "https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/";
  assert.equal(meta("property", "og:image"), `${site}og-image.png`);
  assert.equal(meta("name", "twitter:image"), `${site}og-image.png`);
  assert.equal(meta("name", "twitter:card"), "summary_large_image");
  assert.deepEqual([meta("property", "og:image:width"), meta("property", "og:image:height")], ["1200", "630"]);
  const png = readFileSync(new URL("../../public/og-image.png", import.meta.url));
  assert.equal(png.subarray(1, 4).toString("latin1"), "PNG");
  assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20)], [1200, 630], "IHDR width and height");
  assert.ok(png.length < 200 * 1024, `${png.length} bytes`);
});
