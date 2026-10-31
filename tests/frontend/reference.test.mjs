// The reference sections: the research ledger (order, search, row links, the status strip), the
// METH model card, coefficients, elasticity path, data vintage, references and appendix (cite,
// corrections, reuse), LEAK's P&L basis, STDY's grouped supply table, MKT's controls and the footer.
// Rendered against the fixtures; every expected value is read from them.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, readSource, visibleText } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let reference;
let charts;
let glossary;
let studies;
let market;
let footer;
let verdict;
before(async () => {
  m = await loadDashboard();
  reference = await m.load("/src/lib/reference.js");
  charts = await m.load("/src/charts/referenceCharts.jsx");
  glossary = await m.load("/src/lib/glossary.js");
  studies = await m.load("/src/panels/StudiesPanel.jsx");
  market = await m.load("/src/panels/MarketPanel.jsx");
  footer = await m.load("/src/app/SiteFooter.jsx");
  verdict = await m.load("/src/lib/verdict.js");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");
const spaced = (text) => String(text).replace(/\u00a0/g, " ");

const renderPage = (researchPayload = research, payload = data) => {
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
const ledgerRows = () => m.research.researchLedger(research).rows;
const defaultOrder = (rows) => m.table.orderRows(rows, [{ key: "status", sortValue: reference.ledgerOrderValue }], { key: "status", direction: "asc" });

// ---------------------------------------------------------------------------------------- LEDG
test("the default ledger order puts supported rows first, BH survivors leading, and retracted rows last", () => {
  const ordered = defaultOrder(ledgerRows());
  const ranks = ordered.map(reference.ledgerStatusRank);
  assert.deepEqual(ranks, ranks.slice().sort((a, b) => a - b), "status rank never decreases");
  const supported = ordered.filter((row) => row.status === "supported").length;
  assert.ok(ordered.slice(0, supported).every((row) => row.status === "supported"), "supported rows come first");
  const survivors = research.ledger.summary.supported_surviving_bh_5pct;
  assert.deepEqual(new Set(ordered.slice(0, survivors.length).map((row) => row.id)), new Set(survivors), "BH survivors lead");
  const retracted = ordered.filter((row) => row.status === "retracted").length;
  assert.ok(retracted > 0 && ordered.slice(-retracted).every((row) => row.status === "retracted"), "retracted rows come last");
  // Within a status, q ascends; a row with no q (selected after a search) is last in its status.
  const supportedRows = ordered.filter((row) => row.status === "supported" && !row.survives_bh);
  assert.ok(supportedRows.at(-1).selectedAfterSearch, "the row outside the family sorts last among the supported");
  // The page shows the first LEDGER_PAGE of that order, with the retracted rows behind "Show all".
  const ledg = section(renderPage().html, "ledg");
  const shownIds = [...ledg.matchAll(/<tr id="ledg-([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(shownIds, ordered.slice(0, reference.LEDGER_PAGE).map((row) => row.id));
  assert.match(ledg, new RegExp(`>Show all ${ledgerRows().length} hypotheses</button>`));
  assert.ok(visibleText(ledg).includes(`Tested hypotheses: ${reference.LEDGER_PAGE} of ${ledgerRows().length} shown; the CSV holds all ${ledgerRows().length}`),
    "the caption says the CSV holds every row, not only those shown");
  assert.doesNotMatch(ledg, /class="table-scroll is-tall/, "no nested scroller");
  assert.match(ledg, /<table class="data-table is-dense is-cards"/, "stacked cards on a phone");
});

test("search 'ovx' narrows the ledger to rows that mention it; every term must match, in any case", () => {
  const rows = ledgerRows();
  const hits = reference.filterLedgerRows(rows, { query: "ovx" });
  assert.ok(hits.length > 0 && hits.length < rows.length);
  for (const row of hits) {
    assert.ok([row.hypothesis, row.test, row.id, row.family].join(" ").toLowerCase().includes("ovx"), row.id);
  }
  assert.equal(reference.filterLedgerRows(rows, { query: "OVX" }).length, hits.length, "case-insensitive");
  const both = reference.filterLedgerRows(rows, { query: "ovx  qlike" });
  assert.ok(both.length > 0 && both.length < hits.length, "a second term narrows further");
  assert.ok(both.every((row) => /qlike/i.test([row.hypothesis, row.test, row.id, row.family].join(" "))));
  assert.equal(reference.filterLedgerRows(rows, { query: "ovx zzzz-no-such-term" }).length, 0);
  assert.equal(reference.filterLedgerRows(rows, { query: "   " }).length, rows.length, "blank search passes everything");
  // The text as the page shows it counts too (model keys as labels).
  const labels = m.research.modelLabels(research.vol_lab);
  const display = (text) => m.research.ledgerText(text, { labels });
  const range = reference.filterLedgerRows(rows, { query: "HAR-IV-range" }, display);
  assert.ok(range.some((row) => row.id === "vol_challenger_qlike"));
  // Combined with the filters, as LedgerPanel calls it.
  const family = hits[0].family;
  const filtered = reference.filterLedgerRows(rows, { family, status: "all", query: "ovx" }, display);
  assert.ok(filtered.length > 0 && filtered.every((row) => row.family === family));
});

test("a #ledg-<id> link reveals its row: search and filters cleared, every row shown", () => {
  const rows = ledgerRows();
  const ids = new Set(rows.map((row) => row.id));
  const hidden = defaultOrder(rows).at(-1);
  assert.equal(hidden.status, "retracted");
  const view = { query: "carry", family: rows.find((row) => row.family !== hidden.family).family, status: "supported", all: false };
  const before = reference.visibleLedgerRows(defaultOrder(reference.filterLedgerRows(rows, view)), view.all);
  assert.ok(!before.some((row) => row.id === hidden.id), "hidden by the search, the filters and the cap");
  const revealed = reference.revealLedgerView(view, `#ledg-${hidden.id}`, ids);
  assert.deepEqual({ ...revealed }, { query: "", family: "all", status: "all", all: true, target: hidden.id });
  const after = reference.visibleLedgerRows(defaultOrder(reference.filterLedgerRows(rows, revealed)), revealed.all);
  assert.ok(after.some((row) => row.id === hidden.id), "the retracted row is now on screen");
  assert.equal(reference.revealLedgerView(view, "#ledg-no_such_row", ids), null);
  assert.equal(reference.revealLedgerView(view, "#ledg", ids), null, "the section link is not a row link");
  assert.equal(reference.ledgerHashId(`#ledg-${encodeURIComponent(hidden.id)}`), hidden.id);
  // Each row carries its anchor and a link to it.
  const ledg = section(renderPage().html, "ledg");
  const first = defaultOrder(rows)[0];
  assert.match(ledg, new RegExp(`<tr id="ledg-${first.id}"[^>]*>`));
  assert.match(ledg, new RegExp(`<a class="ledger-id" href="#ledg-${first.id}"><code>${first.id}</code></a>`));
  assert.ok(ledg.includes(`aria-label="Copy link to hypothesis ${first.id}"`));
});

test("the ledger strip has one cell per hypothesis and its counts equal by_status", () => {
  const strip = reference.ledgerStrip(ledgerRows());
  const byStatus = research.ledger.summary.by_status;
  assert.equal(strip.cells.length, research.ledger.rows.length);
  assert.deepEqual(Object.fromEntries(strip.groups.map((group) => [group.key, group.count])), byStatus);
  const supported = strip.groups.find((group) => group.key === "supported");
  assert.equal(supported.survivors, research.ledger.summary.supported_surviving_bh_5pct.length);
  assert.deepEqual(strip.groups.map((group) => group.key), ["supported", "inconclusive", "not_supported", "retracted"]);
  for (const width of [358, 1300]) {
    const svg = renderToStaticMarkup(createElement(charts.LedgerStripSvg, { strip, width }));
    assert.equal((svg.match(/<rect [^>]*class="strip-cell is-/g) ?? []).length, research.ledger.rows.length, `${width}px: one cell per row`);
    assert.equal((svg.match(/class="strip-cell strip-key /g) ?? []).length, strip.groups.length, "a colour key per label");
    assert.equal((svg.match(/class="strip-cell is-survivor"/g) ?? []).length, supported.survivors);
    assert.match(svg, /role="img"/);
    const label = spaced(svg.match(/aria-label="([^"]+)"/)[1]);
    assert.ok(label.startsWith(`${research.ledger.rows.length} hypotheses by status: `), label);
    for (const [key, count] of Object.entries(byStatus)) assert.ok(label.includes(`${count} ${key.replace(/_/g, " ")}`), `${key} in ${label}`);
    const text = visibleText(svg);
    for (const [key, count] of Object.entries(byStatus)) assert.ok(text.includes(`${count} ${key.replace(/_/g, " ")}`), `${width}px label for ${key}`);
  }
  // Labels never overlap one another and stay inside the strip's width.
  const labels = [{ start: 0, width: 150 }, { start: 90, width: 90 }, { start: 110, width: 100 }, { start: 300, width: 70 }];
  const boxes = charts.placeStripLabels(labels, 358).map((item, index) => ({ ...item, end: item.x + labels[index].width }));
  for (const [index, box] of boxes.entries()) {
    assert.ok(box.x >= 0 && box.end <= 358, `label ${index} inside`);
    for (const other of boxes.slice(index + 1).filter((next) => next.line === box.line)) {
      assert.ok(box.end <= other.x || other.end <= box.x, `label ${index} overlaps another on line ${box.line}`);
    }
  }
  assert.equal(Math.max(...boxes.map((box) => box.line)) >= 1, true, "a narrow strip stacks its labels");
});

test("the ledger meta reconciles the supported count with the title's adjusted family", () => {
  const ledger = m.research.researchLedger(research);
  const summary = research.ledger.summary;
  const outside = summary.supported_selected_after_search.length;
  const line = reference.ledgerSupportText(ledger);
  assert.equal(line, `${summary.supported.length} supported; ${summary.supported.length - outside} in the adjusted family (${outside} selected after a search)`);
  const ledg = visibleText(section(renderPage().html, "ledg"));
  assert.ok(ledg.includes(line));
  assert.ok(ledg.includes(`of ${summary.supported.length - outside} supported claims`), "the title counts the same family");
  assert.ok(spaced(ledg).includes(`q and the t > ${research.ledger.hlz_t} hurdle adjust across ${summary.rows_in_multiplicity_family} tests`), "caption");
  assert.equal(reference.ledgerSupportText({ ...ledger, supportedAfterSearch: 0 }), `${ledger.supported} supported, all in the adjusted family`);
});

test("the ledger's CSV exports what each flag cell shows, with the id, test and status of every row", () => {
  const rows = ledgerRows();
  const afterSearch = rows.find((row) => row.selectedAfterSearch);
  const retracted = rows.find((row) => row.status === "retracted");
  const survivor = rows.find((row) => row.status === "supported" && row.survives_bh === true);
  const failing = rows.find((row) => row.status === "supported" && row.survives_bh === false);
  assert.equal(reference.ledgerFlagText(afterSearch, "survives_bh"), "excluded");
  assert.equal(reference.ledgerFlagText(retracted, "survives_bh"), "n/a");
  assert.equal(reference.ledgerFlagText(survivor, "survives_bh"), "yes");
  assert.equal(reference.ledgerFlagText(failing, "survives_bh"), "no");
  const ledg = section(renderPage().html, "ledg");
  assert.match(ledg, /<button type="button" class="csv-button"[^>]*><span class="sr-only">Download <\/span>CSV<\/button>/);
  // Export-only columns are in the markup (hidden by CSS) so the CSV carries them.
  assert.match(ledg, /<th scope="col" class="align-left csv-only"[^>]*><span class="th-label">ID<\/span><\/th>/);
  assert.match(ledg, /<th scope="col" class="align-left csv-only"[^>]*><span class="th-label">Test<\/span><\/th>/);
  assert.match(ledg, /<th scope="col" class="align-left muted-cell"[^>]*title="The date of the artifact that holds the test"[^>]*><button type="button" class="sort-button"><span>Recorded<\/span>/);
  // Status (the default sort) comes right after the hypothesis, so a tablet that scrolls the table
  // sideways starts on each row's verdict; the descriptive columns follow.
  const labels = [...(ledg.match(/<thead[\s\S]*?<\/thead>/)?.[0] ?? "").matchAll(/<th scope="col"[^>]*>([\s\S]*?)<\/th>/g)]
    .map((match) => visibleText(match[1]).trim().replace(/\s*[↕↑↓]$/, ""));
  assert.deepEqual(labels.slice(0, 3), ["Hypothesis", "Status", "Family"]);
  const preSpecified = rows.filter((row) => row.pre_specified === true);
  const shown = defaultOrder(rows).slice(0, reference.LEDGER_PAGE).filter((row) => row.pre_specified === true);
  assert.ok(preSpecified.length > 0);
  assert.equal((ledg.match(/>pre-specified<\/span>/g) ?? []).length, shown.length, "a chip on each pre-specified row shown");
  // The export-only cells stay hidden in the phone's card layout too, which sets its cells' display.
  const css = readSource("src/styles/reference-sections.css");
  assert.match(css, /\.data-table\.is-cards tbody td\.csv-only \{ display: none; \}/);
});

// ---------------------------------------------------------------------------------------- METH
test("METH's Reproduce box names the pinned container and its commands, which exist in the repository", () => {
  const text = spaced(visibleText(section(renderPage().html, "meth")));
  assert.ok(text.includes("docker build -t wti-vol . then docker run --rm wti-vol make reproduce"), text);
  const makefile = readSource("Makefile");
  assert.match(makefile, /^reproduce:/m);
  assert.match(readSource("Dockerfile"), /^FROM python:[\w.-]+@sha256:[0-9a-f]{64}$/m);
});

test("METH opens with a model card whose values come from the payloads", () => {
  const meth = section(renderPage().html, "meth");
  const text = spaced(visibleText(meth));
  const live = data.vol_forecast.live;
  const config = research.vol_lab.config;
  const first = meth.indexOf("<h3");
  assert.ok(meth.slice(first, first + 200).includes(`Model card · HAR-IV (${live.model_version})`), "the first block is the model card");
  assert.ok(text.includes(`b4 log OVX(t−${config.ovx_lag_sessions})`), "the config lag in the equation");
  assert.ok(text.includes(`log RV(t+1..t+${config.horizon_sessions})`));
  assert.ok(text.includes(`OVX lagged ${config.ovx_lag_sessions} session`), "the config lag in the timing");
  assert.ok(text.includes(`next ${config.horizon_sessions} sessions, for risk sizing`));
  assert.ok(text.includes(`at least ${config.min_train_rows} rows`) && text.includes(`the ${config.purge_rows} rows whose target overlaps`));
  assert.ok(text.includes("An expanding window of every matured row"), "the window the backend fits on");
  const floor = research.vol_lab.noise_floor.har_direction_acc_pct_mean;
  assert.ok(text.includes(`${floor.toFixed(1)}% noise floor`));
  // The accuracy is of the direction of volatility, never of price: the model makes no price forecast.
  const record = research.vol_lab.leaderboard.models.find((model) => model.key === research.vol_lab.config.champion);
  const notFor = text.match(/Not intended for (.*?) \(see MODL ?\)/)?.[1] ?? "";
  assert.ok(notFor.startsWith("Price direction: it forecasts the level of volatility, not price. Nor the direction of volatility: "), notFor);
  assert.ok(notFor.includes(`vol-direction accuracy ${record.direction_acc_pct.toFixed(1)}% on every out-of-sample day (benchmark lab), `
    + `${record.direction_acc_pct < floor ? "below" : "against"} a ${floor.toFixed(1)}% noise floor`), notFor);
  assert.doesNotMatch(notFor, /Price direction: direction accuracy/, "the vol-direction figure is never put under price direction");
  // The mean is the hero's own chain: the fitted median at two decimals times √smearing at four.
  const chain = verdict.forecastDecomposition(live.coefficients, live.inputs, live.smearing_factor);
  assert.ok(text.includes(`Median × √smearing (Duan): ${chain.shownMedian.toFixed(2)}% × √${verdict.plainNumber(live.smearing_factor, 4)} = `
    + `${chain.printedMean.toFixed(2)}% for this call`), text);
  const persistence = research.vol_lab.leaderboard.models.find((model) => model.key === "persistence");
  assert.ok(text.includes(`DM ${m.format.fmtSigned(persistence.dm_qlike_vs_har_iv.stat, 2)}`));
  assert.ok(text.includes("not distinguishable from the OVX-only regression"));
  assert.ok(/inside on \d+ of \d+ sampling grids/.test(text));
  assert.ok(text.includes(`promotion test: accruing`));
  assert.match(meth, /<a href="https:\/\/github\.com\/[^"]+\/MODEL_CARD\.md"[^>]*>Model card<span class="sr-only">/);
  assert.match(meth, /<a href="https:\/\/github\.com\/[^"]+\/METHODOLOGY\.md"[^>]*>Methodology<span class="sr-only">/);
  assert.ok(text.includes("the live record is short"), "the limitations sentence stays visible");
});

test("the live coefficients reproduce the published median, and both fits name their sample", () => {
  const live = data.vol_forecast.live;
  const fit = reference.liveFit(live, research.vol_lab);
  assert.equal(fit.rows.length, live.coefficients.length);
  assert.equal(m.format.fmtNum(fit.median, 1), m.format.fmtNum(live.forecast_median_pct, 1));
  assert.equal(fit.rows.find((row) => row.term === "log OVX").input, live.inputs.ovx);
  assert.equal(fit.rows.find((row) => row.term === "log RV5").input, live.inputs.rv5_pct / 100);
  const text = spaced(visibleText(section(renderPage().html, "meth")));
  assert.ok(text.includes(`The live coefficients reproduce this call's ${live.forecast_median_pct.toFixed(1)}% median`));
  assert.ok(text.includes(`Live fit, refit for the call as of ${m.format.fmtDate(live.as_of)}`));
  assert.ok(text.includes(`Full sample, in-sample through ${m.format.fmtDate(research.vol_lab.generated_from.sessions_through)}`));
  assert.ok(text.includes(m.format.fmtNum(live.coefficients[4], 4)) && text.includes(m.format.fmtNum(research.vol_lab.har_iv_coefficients.terms[4].coef, 4)));
  assert.equal(reference.liveFit({ ...live, coefficients: [1, 2] }, research.vol_lab), null, "terms and coefficients must pair up");
});

test("the elasticity title's min and max are the series' own", () => {
  const series = research.vol_lab.series;
  const values = series.ovx_elasticity.filter((value) => value != null);
  const path = reference.elasticityPath(research.vol_lab);
  assert.equal(path.ovx.min, Math.min(...values));
  assert.equal(path.ovx.max, Math.max(...values));
  assert.equal(path.ovx.last, values.at(-1));
  const title = reference.elasticityAssertion(path);
  assert.equal(title, `The weight on OVX ranged ${Math.min(...values).toFixed(2)}–${Math.max(...values).toFixed(2)} across walk-forward refits since ${series.date[0].slice(0, 4)}`);
  assert.ok(title.length <= 90);
  assert.ok(visibleText(section(renderPage().html, "meth")).includes(title));
  const svg = renderToStaticMarkup(createElement(charts.ElasticityPathSvg, { path, width: 900 }));
  assert.match(svg, /role="img"/);
  assert.ok(svg.includes("stroke-implied") && svg.includes("stroke-ink") && !svg.includes("stroke-truth"), "OVX in its colour, RV in ink");
  assert.ok(spaced(svg).includes(`max ${Math.max(...values).toFixed(2)}`) && spaced(svg).includes(`min ${Math.min(...values).toFixed(2)}`));
  assert.equal(reference.elasticityPath({ series: { date: ["2020-01-01"], ovx_elasticity: [1] } }), null);
});

test("an OVX extreme's label moves to the end column when a line would run through it", () => {
  const { boxMeetsPolyline } = charts;
  const box = { x0: 10, x1: 50, y0: 10, y1: 24 };
  assert.equal(boxMeetsPolyline(box, [{ px: 0, py: 30 }, { px: 60, py: 30 }]), false, "a line under the box");
  assert.equal(boxMeetsPolyline(box, [{ px: 0, py: 18 }, { px: 60, py: 18 }]), true, "a line through it");
  assert.equal(boxMeetsPolyline(box, [{ px: 0, py: 18 }, null, { px: 60, py: 18 }]), false, "a gap is no line");
  // A V-shaped OVX path; the summed RV runs 0.1 under it (as in 2014), or far below.
  const dates = Array.from({ length: 121 }, (_, i) => new Date(Date.UTC(2010, 0, 1 + 30 * i)).toISOString().slice(0, 10));
  const ovx = dates.map((_, i) => 0.55 + Math.abs(i - 60) * 0.0075);
  const build = (offset) => reference.elasticityPath({
    series: { date: dates, ovx_elasticity: ovx, rv_elasticity: ovx.map((value) => (offset == null ? 0.05 : value - offset)) },
  });
  const minYear = build(null).ovx.minDate.slice(0, 4);
  for (const width of [900, 390]) {
    const near = spaced(renderToStaticMarkup(createElement(charts.ElasticityPathSvg, { path: build(0.1), width })));
    assert.ok(near.includes(`>min 0.55, ${minYear}</text>`), `${width}: in the end column, with its year`);
    assert.doesNotMatch(near, /class="axis-note halo"[^>]*>min /, `${width}: not on the RV line`);
    assert.match(near, /class="axis-note halo"[^>]*>max 1\.00</, `${width}: the max label stays by its dot`);
    const far = spaced(renderToStaticMarkup(createElement(charts.ElasticityPathSvg, { path: build(null), width })));
    assert.match(far, /class="axis-note halo"[^>]*>min 0\.55</, `${width}: a clear spot keeps the label by its dot`);
    assert.ok(!far.includes(`min 0.55, ${minYear}`));
  }
});

test("the data-vintage rows are the payloads' own dates", () => {
  const rows = Object.fromEntries(reference.vintageRows(data, research).map((row) => [row.key, row]));
  const vol = data.vol_forecast;
  const lab = research.vol_lab;
  assert.equal(rows.snapshot.refreshed, data.frozen_at);
  assert.equal(rows.live.asOf, vol.live.as_of);
  assert.equal(rows.live.refreshed, vol.live.issued_at);
  assert.equal(rows.validation.asOf, vol.validation.sample_end);
  assert.equal(rows.record.asOf, vol.live_record.recent.map((row) => row.as_of).sort().at(-1));
  assert.equal(rows.lab.asOf, lab.generated_from.sessions_through);
  assert.equal(rows.lab.refreshed, lab.generated_from.retrieved_at);
  assert.equal(rows.cone.asOf, lab.vol_cone.as_of);
  assert.equal(rows.var.asOf, lab.series.date.filter((date, index) => lab.series.ret5[index] != null).at(-1));
  assert.equal(rows.economic.asOf, vol.economic.returns.sample_end);
  const pm = research.postmortem;
  assert.equal(rows.postmortem.asOf, [pm.five_year.purged.last, pm.ten_year.purged.last, pm.five_year.leaked.last, pm.ten_year.leaked.last].map((value) => value.slice(0, 10)).sort().at(-1));
  assert.equal(rows.ledger.asOf, research.ledger.rows.map((row) => row.date).sort().at(-1));
  assert.equal(rows.prices.asOf, data.unified_data.actual.timestamps.at(-1).slice(0, 10));
  for (const key of ["lab", "cone", "var"]) assert.equal(rows[key].pinned, true, key);
  for (const key of ["snapshot", "live", "validation", "economic", "postmortem", "ledger"]) assert.equal(rows[key].pinned, false, key);
  const meth = visibleText(section(renderPage().html, "meth"));
  assert.ok(meth.includes(`Volatility cone CONE ${m.format.fmtDate(lab.vol_cone.as_of)}`), "one date format");
  // Without research.json the lab rows are gone and nothing is called pinned.
  const bare = reference.vintageRows(data, null);
  assert.ok(!bare.some((row) => ["lab", "cone", "var", "ledger"].includes(row.key)));
  assert.ok(bare.every((row) => row.pinned === false));
});

test("every glossary reference has its row in METH, and digests are printed in full", () => {
  const meth = section(renderPage().html, "meth");
  for (const entry of glossary.GLOSSARY.filter((item) => item.ref)) {
    assert.match(meth, new RegExp(`<li id="${entry.ref}">`), `${entry.key} -> #${entry.ref}`);
  }
  for (const id of ["stat-hln", "stat-mz", "stat-bh", "stat-hlz"]) assert.match(meth, new RegExp(`<li id="${id}">`), id);
  for (const file of Object.values(research.vol_lab.generated_from.files)) {
    assert.equal(file.sha256.length, 64);
    assert.ok(meth.includes(`<code class="hash" title="${file.sha256}">${file.sha256}</code>`), file.file);
    assert.ok(meth.includes(`aria-label="Copy the SHA-256 digest of ${file.file}"`));
  }
  const css = readSource("src/index.css");
  const rule = css.match(/\n\.hash \{([^}]*)\}/)?.[1] ?? "";
  for (const decl of ["max-width: 18ch", "text-overflow: ellipsis", "user-select: all", "overflow: hidden"]) assert.ok(rule.includes(decl), decl);
  assert.ok(visibleText(meth).includes("pinned by the SHA-256 digests above"));
  const none = visibleText(section(renderPage(null).html, "meth"));
  assert.ok(!none.includes("digests above"), "no digest sentence without the manifest");
});

test("the citation and BibTeX come from CITATION.cff with the snapshot date", () => {
  const cff = readSource("CITATION.cff");
  const parsed = reference.parseCitation(cff);
  const title = cff.match(/^title:\s*"(.*)"$/m)[1];
  const version = cff.match(/^version:\s*(.*)$/m)[1].trim();
  assert.equal(parsed.title, title);
  assert.equal(parsed.version, version);
  assert.equal(parsed.license, cff.match(/^license:\s*(.*)$/m)[1].trim());
  assert.equal(parsed.authors[0].family, cff.match(/family-names:\s*(.*)$/m)[1].trim());
  const snapshot = data.frozen_at.slice(0, 10);
  const bibtex = reference.citationBibtex(parsed, { snapshot, build: "abc1234" });
  assert.ok(bibtex.includes(`title = {{${title}}}`) && bibtex.includes(`version = {${version}}`));
  assert.ok(bibtex.includes(`Data snapshot ${snapshot}`) && bibtex.includes("build abc1234"));
  assert.match(bibtex, /^@software\{[a-z0-9]+,\n/);
  const meth = section(renderPage().html, "meth");
  const text = visibleText(meth);
  assert.ok(text.includes(`Data snapshot ${snapshot}`), "the page's BibTeX names this snapshot");
  assert.ok(text.includes(`(version ${version}; data snapshot ${snapshot})`), "the plain citation");
  assert.ok(meth.includes('aria-label="Copy the citation"') && meth.includes('aria-label="Copy the BibTeX entry"'));
  assert.ok(meth.includes('aria-label="Copy the snapshot date"'));
  assert.ok(text.includes(`Code and data files: ${parsed.license} license`), "reuse reads the license from CITATION.cff");
  assert.deepEqual(reference.parseCitation("title: x\nabstract: >-\n  title: not this\nversion: 1").title, "x");
});

test("the corrections list holds one item per retracted row, each linked to its row, and the v1 timing fix", () => {
  const meth = section(renderPage().html, "meth");
  const list = meth.match(/<ul class="corrections-list">([\s\S]*?)<\/ul>/)[1];
  const retracted = research.ledger.rows.filter((row) => row.status === "retracted");
  assert.equal((list.match(/<li>/g) ?? []).length, retracted.length);
  for (const row of retracted) assert.ok(list.includes(`href="#ledg-${row.id}"`), row.id);
  const control = research.vol_lab.leaderboard.models.find((model) => model.timing_control);
  const record = research.vol_lab.leaderboard.models.find((model) => model.key === research.vol_lab.config.champion);
  const f = m.format;
  assert.ok(visibleText(meth).includes(`v1 read the test day's OVX close: QLIKE ${f.fmtNum(control.qlike, 3)} vs ${f.fmtNum(record.qlike, 3)} for v2 `
    + `(DM ${f.fmtSigned(control.dm_qlike_vs_har_iv.stat, 2)}); v2 lags OVX ${research.vol_lab.config.ovx_lag_sessions} session`));
  for (const href of ["CHANGELOG.md", "/commits/gh-pages/data.json", "/commits/live-data/runtime-state/vol_track_record.json"]) {
    assert.ok(meth.includes(href), href);
  }
  assert.match(meth, /<a href="[^"]*data\.json" download="data\.json">Download data\.json<\/a>/);
  assert.match(meth, /<a href="[^"]*research\.json" download="research\.json">Download research\.json<\/a>/);
});

test("METH and the footer link the full results and the reviewer guide first", async () => {
  const config = await m.load("/src/app/config.js");
  const hrefs = (html) => [...html.matchAll(/<a href="(https:\/\/github\.com\/[^"]+\.md)"/g)].map((match) => match[1]);
  const meth = section(renderPage().html, "meth");
  const card = meth.match(/<div class="card-row"><dt>Documents<\/dt>[\s\S]*?<\/div>/)[0];
  assert.deepEqual(hrefs(card).slice(0, 2), [config.DOCS.results, config.DOCS.reviewerGuide], "model card's documents row");
  const docs = meth.match(/<div class="method-block appendix-block" id="meth-docs">[\s\S]*?<\/div>/)[0];
  assert.deepEqual(hrefs(docs).slice(0, 2), [config.DOCS.results, config.DOCS.reviewerGuide], "the Docs block");
  assert.ok(visibleText(card).includes("Full results") && visibleText(card).includes("Reviewer guide"));
  const page = renderToStaticMarkup(createElement(footer.default, { data }));
  const nav = page.match(/<nav class="footer-links"[\s\S]*?<\/nav>/)[0];
  const links = [...nav.matchAll(/<a href="([^"]+)"[^>]*>([^<]+)/g)].map((match) => [match[1], match[2]]);
  assert.deepEqual(links.slice(0, 2), [[config.DOCS.results, "Full results"], [config.DOCS.reviewerGuide, "Reviewer guide"]]);
  assert.match(nav, /target="_blank" rel="noopener noreferrer">Full results<span class="sr-only"> \(opens in a new tab\)<\/span>/);
  // Before data.json loads the write-up is still reachable; the appendix anchors are not rendered yet.
  const empty = renderToStaticMarkup(createElement(footer.default, { data: null }));
  assert.ok(empty.includes(`href="${config.DOCS.results}"`) && empty.includes(`href="${config.DOCS.reviewerGuide}"`));
  assert.doesNotMatch(empty, /href="#meth-/);
});

test("the footer links to the METH appendix anchors and prints the page", () => {
  const html = renderToStaticMarkup(createElement(footer.default, { data }));
  const meth = section(renderPage().html, "meth");
  for (const [label, id] of [["Cite", "meth-cite"], ["Corrections", "meth-corrections"], ["Reuse", "meth-reuse"]]) {
    assert.ok(html.includes(`<a href="#${id}">${label}</a>`), label);
    assert.match(meth, new RegExp(`<div class="method-block appendix-block" id="${id}"><h3 class="study-title" tabindex="-1">`), id);
  }
  assert.match(html, /<button type="button" class="footer-print">Print \/ save PDF<\/button>/);
  assert.doesNotMatch(renderToStaticMarkup(createElement(footer.default, { data: null })), /footer-print/, "no appendix links before data loads");
  const css = readSource("src/index.css");
  assert.match(css, /\.footer-provenance a, \.footer-links a \{[^}]*min-height: 24px/);
});

// ------------------------------------------------------------------------------- LEAK, STDY, MKT
test("LEAK states its P&L basis and draws the purged run in ink", () => {
  const leak = section(renderPage().html, "leak");
  const text = visibleText(leak);
  assert.ok(text.includes("P&L per trade on one CL contract (1,000 barrels)"), "meta");
  assert.match(leak, /<th scope="col" class="align-right wrap-head" title="Total P&amp;L after costs, trading one CL contract[^"]*"[^>]*><span class="th-label">P&amp;L, USD \(1 contract\)<\/span><\/th>/);
  assert.match(leak, /<div class="panel-figure"><div class="figure-controls"><div class="segmented" role="group" aria-label="Walk-forward window">/, "the window is a figure control");
  assert.doesNotMatch(leak.match(/<div class="panel-actions">[\s\S]*?<\/div>/)?.[0] ?? "", /segmented/);
  assert.match(leak, /swatch-ink is-line/);
  assert.doesNotMatch(leak, /swatch-truth|label-truth/, "realized grey is not reused for the purged run");
  assert.match(leak, /<details class="disclosure panel-notes">[\s\S]*Why the leak works: /, "the anatomy formula is a method note");
  const svgSource = readSource("src/charts/svgCharts.jsx");
  const equity = svgSource.slice(svgSource.indexOf("export function EquityChart"), svgSource.indexOf("export function SignAgreementLadder"));
  assert.ok(equity.includes("USD, 1 contract") && equity.includes("stroke-ink") && !equity.includes("stroke-truth"));
  const purge = svgSource.slice(svgSource.indexOf("export function PurgeDiagram"));
  assert.ok(!/fill-model|stroke-model|label-model/.test(purge), "the purge diagram's test row is ink, not model blue");
});

test("STDY's supply table has a column-group header row and an interval glyph on one shared domain", () => {
  const stdy = section(renderPage().html, "stdy");
  assert.match(stdy, /<colgroup span="2"><\/colgroup><colgroup span="3"><\/colgroup><colgroup span="3"><\/colgroup>/);
  assert.match(stdy, /<tr class="group-row"><td colSpan="2" class="group-start" aria-hidden="true"><\/td><th scope="colgroup" colSpan="3" class="group-start">Peak, sessions 0–19<\/th><th scope="colgroup" colSpan="3" class="group-start">Settle, session 10<\/th><\/tr>/i);
  const study = m.research.supplyStudy(research);
  const lost = study.groups.find((group) => group.key === "supply_lost");
  const f = m.format;
  assert.ok(visibleText(stdy).includes(`${f.fmtSignedPct(lost.peak)} [${f.fmtNum(lost.ciPeak[0], 1)}, ${f.fmtNum(lost.ciPeak[1], 1)}]`), "one line");
  assert.doesNotMatch(stdy, /stat-cell is-stacked/);
  const glyphs = stdy.match(/<span class="interval-glyph" aria-hidden="true">/g) ?? [];
  assert.equal(glyphs.length, study.groups.length + 1, "one per group and the placebo row");
  assert.match(stdy, /<span class="glyph-note"> Beside each median peak, the line is its 95% bootstrap interval/, "the sentence that reads the glyph hides with it");
  const domain = studies.peakGlyphDomain(study);
  const ci = study.groups.flatMap((group) => group.ciPeak);
  assert.ok(domain[0] < Math.min(...ci) && domain[1] > Math.max(...ci), "every interval fits the shared domain");
  assert.ok(visibleText(stdy).includes(f.fmtPValue(lost.settleP, { digits: 3 })), "p columns print three decimals");
  // Method text is a note; the verdicts stay visible at full width.
  assert.match(stdy, /<p class="prose-note is-wide">Months with carry/);
  assert.match(stdy, /<details class="disclosure panel-notes">[\s\S]*Carry: Second-nearby/);
  assert.doesNotMatch(visibleText(stdy), /p_value_vs_placebo|mann_whitney|overlapping_events/, "no payload keys in the notes");
});

test("STDY's title frames both studies in one claim; each h3 carries its own study's result", () => {
  const stdy = section(renderPage().html, "stdy");
  const h2 = visibleText(stdy.match(/<h2[^>]*>([\s\S]*?)<\/h2>/)[1]).trim();
  const study = m.research.supplyStudy(research);
  const playbook = data.supply_shock_playbook;
  assert.equal(h2, m.assertions.studiesAssertion({ carry: research.carry, playbook, study }));
  assert.ok(h2.length <= 90, h2);
  assert.ok(h2.startsWith("Two descriptive studies, not traded: carry timing and "), h2);
  assert.ok(h2.includes(`${playbook.event_count} supply shocks`), h2);
  assert.doesNotMatch(h2, /;|beat|placebo|sorted/, "one framing claim, no study's result");
  const h3s = [...stdy.matchAll(/<h3[^>]*>([\s\S]*?)<\/h3>/g)].map((match) => visibleText(match[1]).trim());
  assert.deepEqual(h3s.slice(0, 2), [m.assertions.carryAssertion(research.carry), m.assertions.supplyStudyAssertion(study)]);
  // The meta keeps the samples; "descriptive; not traded" is already the title's.
  const meta = visibleText(stdy.match(/<p class="panel-meta[^"]*">([\s\S]*?)<\/p>/)[1]);
  assert.doesNotMatch(meta, /not traded/);
  assert.ok(meta.includes(`${playbook.event_count} events`), meta);
});

test("STDY's carry table puts the unit in the Above / below column's label", () => {
  const stdy = section(renderPage().html, "stdy");
  const carryTable = stdy.match(/<table[\s\S]*?<\/table>/)[0];
  const headers = [...carryTable.matchAll(/<th[^>]*scope="col"[^>]*>([\s\S]*?)<\/th>/g)].map((match) => visibleText(match[1]).trim());
  assert.ok(headers.includes("Above / below, %/month"), headers.join(" | "));
  const full = research.carry.full_sample;
  assert.ok(visibleText(carryTable).includes(`${m.format.fmtNum(full.mean_ret_above_median_carry_pct, 2)} / ${m.format.fmtNum(full.mean_ret_below_median_carry_pct, 2)}`));
});

test("STDY's supply CSV holds the 95% interval and the shared-window pairs the table shows", () => {
  const study = m.research.supplyStudy(research);
  const csv = m.table.csvColumns(studies.SUPPLY_CSV_COLUMNS);
  assert.deepEqual(csv.map((column) => column.label),
    ["Median peak 95% CI low %", "Median peak 95% CI high %", "Events whose windows share sessions (†)"]);
  for (const group of study.groups) {
    assert.deepEqual([csv[0].value(group), csv[1].value(group)], group.ciPeak, group.key);
  }
  const overlapped = study.groups.filter((group) => group.overlaps.length > 0);
  assert.ok(overlapped.length > 0, "the fixture has a group with a †");
  for (const group of overlapped) assert.match(csv[2].value(group), / and .*, \d+ sessions apart/, group.key);
  for (const group of study.groups.filter((entry) => !entry.overlaps.length)) assert.equal(csv[2].value(group), null, group.key);
  assert.deepEqual(csv.map((column) => column.value({ key: "placebo", isPlacebo: true })), [null, null, null], "the placebo row has neither");
  // In the table's header (hidden on screen), after the eight columns the groups span.
  const stdy = section(renderPage().html, "stdy");
  const supplyHead = stdy.slice(stdy.indexOf('<tr class="group-row">')).match(/<\/tr><tr>([\s\S]*?)<\/tr>/)[1];
  const headers = [...supplyHead.matchAll(/<th scope="col"([^>]*)>/g)].map((match) => match[1]);
  assert.equal(headers.length, 11);
  assert.ok(headers.slice(8).every((attrs) => /class="[^"]*\bcsv-only\b/.test(attrs)), "the last three");
  assert.ok(headers.slice(0, 8).every((attrs) => !/csv-only/.test(attrs)), "and only those");
});

test("MKT keeps its range in the URL's figure controls and shows the closes as a table", () => {
  const mkt = section(renderPage().html, "mkt");
  assert.match(mkt, /<div class="figure-controls"><div class="segmented" role="group" aria-label="Price chart range">/);
  assert.match(mkt, /aria-pressed="true"[^>]*>ALL<\/button>/, "the default range");
  assert.match(mkt, />Closes<span class="sr-only"> view of Market<\/span><\/button>/);
  assert.match(mkt, /style="--lazy-h:368px;--lazy-h-narrow:288px"/);
  const rows = market.closeRows(data);
  const closes = data.vol_forecast.recent_closes;
  assert.equal(rows.length, closes.date.length);
  assert.deepEqual(rows.at(-1), { date: closes.date.at(-1), close: closes.close.at(-1) });
  assert.deepEqual(market.closeRows({}), []);
});

test("without research.json the reference sections degrade to what data.json supports", () => {
  const { html, errors } = renderPage(null);
  assert.deepEqual(errors, []);
  const meth = visibleText(section(html, "meth"));
  assert.ok(meth.includes(`Model card · HAR-IV (${data.vol_forecast.live.model_version})`));
  assert.ok(meth.includes("Cite this page") && meth.includes("Reuse"), "the appendix needs no research.json");
  assert.ok(!meth.includes("Retracted claims"), "no ledger, no retraction list");
  assert.ok(visibleText(section(html, "ledg")).includes("research.json did not load"));
});
