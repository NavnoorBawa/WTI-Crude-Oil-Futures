// The shared panel chrome: header order and names, method notes, glossary toggletips, linked
// sources and copyable commands, table regions and CSV export, URL view state, p-value and number
// formats, and the page-level research.json notice. Rendered against the fixtures.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SECTIONS } from "../../src/app/sections.js";
import { loadDashboard, readFixture, visibleText } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let Panel;
let panelModule;
let term;
let glossary;
let csv;
let view;
let config;
before(async () => {
  m = await loadDashboard();
  panelModule = await m.load("/src/components/Panel.jsx");
  Panel = panelModule.default;
  term = await m.load("/src/components/Term.jsx");
  glossary = await m.load("/src/lib/glossary.js");
  csv = await m.load("/src/lib/csv.js");
  view = await m.load("/src/app/viewState.js");
  config = await m.load("/src/app/config.js");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");

const renderDashboard = (researchPayload, status = researchPayload ? "ready" : "unavailable") => {
  const errors = [];
  const original = console.error;
  console.error = (...args) => errors.push(args.join(" "));
  try {
    const html = renderToStaticMarkup(createElement(m.dashboard.default, {
      data, research: researchPayload, researchStatus: status, quote: m.quote.resolveQuote(null, data),
    }));
    return { html, text: visibleText(html), errors };
  } finally {
    console.error = original;
  }
};
const section = (html, id) => html.match(new RegExp(`<section id="${id}"[\\s\\S]*?</section>`))?.[0] ?? "";
const renderPanel = (props, children = createElement("p", null, "figure")) =>
  renderToStaticMarkup(createElement(Panel, { id: "test", code: "TEST", name: "Test panel", title: "A claim", ...props }, children));

// ---------------------------------------------------------------------------------- the header
test("every section reads code, name and claim before its controls, and is named by its code", () => {
  const { html, errors } = renderDashboard(research);
  assert.deepEqual(errors, []);
  for (const { id, code, name } of SECTIONS) {
    const body = section(html, id);
    assert.ok(body, id);
    assert.match(body, new RegExp(`^<section id="${id}" class="panel[^"]*" aria-label="${code}, ${name}">`), `${id} landmark name`);
    assert.doesNotMatch(body.slice(0, body.indexOf(">")), /aria-labelledby/, `${id} is not named by its sentence-long title`);
    const title = body.indexOf(`<h2 id="${id}-title" class="panel-title" tabindex="-1">`);
    assert.ok(title > 0, `${id} title is focusable for deep links`);
    const actions = body.indexOf('class="panel-actions"');
    if (actions >= 0) assert.ok(title < actions, `${id}: the h2 precedes .panel-actions`);
    assert.match(body, new RegExp(`<a class="chip chip--code chip-link" href="#${id}">${code}</a>`), `${id} chip links to itself`);
    assert.ok(body.includes(`aria-label="Copy link to ${code}, ${name}"`), `${id} copy-link button`);
  }
});

test("every Table button says which section it shows, and no two share a name", () => {
  const { html } = renderDashboard(research);
  const names = [...html.matchAll(/<button type="button" class="toggle-button"[^>]*>([\s\S]*?)<\/button>/g)].map((match) => visibleText(match[1]).replace(/\s+/g, " ").trim());
  assert.ok(names.length >= 8, `${names.length} table buttons`);
  // "Table", or a panel's own tableLabel (MKT's "Closes"), then the section it shows.
  for (const name of names) assert.match(name, /^(Table|Closes) view of \S/, name);
  assert.equal(new Set(names).size, names.length, `duplicate names: ${names.join(" | ")}`);
  const fcst = SECTIONS.find((item) => item.id === "fcst");
  assert.ok(names.includes(`Table view of ${fcst.name}`));
  assert.ok(renderPanel({ table: () => null, tableLabel: "Closes" }).includes(">Closes<span class=\"sr-only\"> view of Test panel</span></button>"), "tableLabel");
});

test("controls sit above the figure, inside it, so they hide with it while the table shows", () => {
  const html = renderPanel({ controls: createElement("button", { type: "button" }, "1Y"), actions: createElement("span", null, "filter") });
  assert.match(html, /<div class="panel-figure"><div class="figure-controls"><button type="button">1Y<\/button><\/div><p>figure<\/p><\/div>/);
  assert.match(html, /<div class="panel-actions"><span>filter<\/span><\/div>/);
});

// ------------------------------------------------------------------------------- method notes
test("notes render in a closed disclosure, with their text in the markup", () => {
  const html = renderPanel({ caption: "How to read it.", notes: ["First method paragraph.", "Second, with QLIKE."] });
  const details = html.match(/<details class="disclosure panel-notes">([\s\S]*?)<\/details>/);
  assert.ok(details, "a details element with both classes");
  assert.doesNotMatch(details[0], /<details[^>]* open/, "closed by default");
  assert.match(details[1], /^<summary class="disclosure-summary">Method notes<\/summary><div class="disclosure-body"><p>First method paragraph\.<\/p><p>/);
  assert.ok(visibleText(details[1]).includes("Second, with QLIKE"), "the text is in the server-rendered DOM");
  assert.ok(html.indexOf("panel-caption") < html.indexOf("panel-notes"), "after the caption");
  const single = renderPanel({ notes: "One string." });
  assert.match(single, /<div class="disclosure-body"><p>One string\.<\/p><\/div>/);
  assert.doesNotMatch(renderPanel({ notes: [] }), /<details/, "no notes, no disclosure");
  assert.doesNotMatch(renderDashboard(research).html, /<details[^>]* open/, "nothing on the page is open by default");
});

// ----------------------------------------------------------------------------------- glossary
test("withTerms explains the first QLIKE of a panel once, and never touches a title or meta", () => {
  const html = renderPanel({
    title: "QLIKE favours the model",
    meta: ["QLIKE 0.3 vs 0.4"],
    caption: "QLIKE ranks the models; QLIKE is lower for the record. The Model Confidence Set keeps two.",
    notes: ["QLIKE again, and OVX."],
  });
  const terms = [...html.matchAll(/<button type="button" class="term" aria-expanded="false">([^<]*)<\/button>/g)].map((match) => match[1]);
  assert.deepEqual(terms, ["QLIKE", "Model Confidence Set", "OVX"], "one per glossary key per panel, caption before notes");
  const title = html.match(/<h2[^>]*>([\s\S]*?)<\/h2>/)[1];
  assert.doesNotMatch(title, /class="term"/);
  assert.doesNotMatch(html.match(/<p class="panel-meta[\s\S]*?<\/p>/)[0], /class="term"/);
  assert.doesNotMatch(html, /role="note"/, "a closed definition is not in the markup, so the sentence reads as written");
  assert.ok(visibleText(html.match(/<p class="panel-caption">[\s\S]*?<\/p>/)[0]).replace(/\s+/g, " ").includes("QLIKE ranks the models; QLIKE is lower"));
});

test("glossary patterns match whole words only, and definitions state no statistic", () => {
  const { splitTerms, GLOSSARY } = glossary;
  const keys = (text) => splitTerms(text, new Set()).filter((part) => typeof part !== "string").map((part) => part.entry.key);
  assert.deepEqual(keys("HAR-IV beats har_iv and the HARD case"), [], "no match inside HAR-IV, identifiers or longer words");
  assert.deepEqual(keys("The HAR model"), ["har"]);
  assert.deepEqual(keys("purged walk-forward, then the purge"), ["purge"], "the longer pattern wins and the key is used once");
  assert.deepEqual(keys("purged rows"), [], "purge never matches inside purged");
  assert.deepEqual(keys("OVX-only regression vs OVX"), ["ovx"]);
  assert.deepEqual(keys("q and the t > 3 hurdle"), ["hlz"]);
  assert.deepEqual(keys("QLIKE"), ["qlike"]);
  assert.deepEqual(splitTerms("QLIKE", new Set(["qlike"])), ["QLIKE"], "a key already seen in the panel is left as text");
  assert.equal(splitTerms("Gibbs-Candès", new Set()).length, 1);
  assert.equal(GLOSSARY.find((entry) => entry.key === "conformal").definition.includes("Gibbs-Candès"), true, "spelled with its accent");
  for (const entry of GLOSSARY) {
    assert.doesNotMatch(entry.definition, /\d\.\d|%|\bp\s*[=<>]/, `${entry.key} states no statistic`);
    assert.equal(entry.definition.split(/(?<=[a-z)])\. /).length, 1, `${entry.key} is one sentence`);
    assert.ok(entry.ref === null || /^stat-[a-z]+$/.test(entry.ref), entry.key);
  }
  const refs = GLOSSARY.map((entry) => entry.ref).filter(Boolean).sort();
  assert.deepEqual(refs, ["stat-bh", "stat-christoffersen", "stat-conformal", "stat-dm", "stat-dq", "stat-har", "stat-hlz",
    "stat-kupiec", "stat-mcs", "stat-mz", "stat-qlike", "stat-smearing", "stat-spa", "stat-utility"]);
  assert.equal(term.withTerms(7), 7, "non-text passes through");
});

// --------------------------------------------------------------------------------- the source
test("the FCST footer links its artifacts to data.json and research.json, and its commands to their modules", () => {
  const { html } = renderDashboard(research);
  const fcst = section(html, "fcst");
  const footer = fcst.match(/<footer class="panel-source">[\s\S]*?<\/footer>/)[0];
  const links = [...footer.matchAll(/<a class="source-item" href="([^"]+)"[^>]*><code>([^<]+)<\/code><\/a>/g)].map((match) => [match[1], match[2]]);
  assert.deepEqual(links.filter(([, text]) => /json/.test(text) || !/python/.test(text)).map(([href]) => href), [
    config.RESEARCH_URL, config.STATIC_DATA_URL, config.STATIC_DATA_URL,
  ], "research.json, data.json, and the bare entry inherits data.json");
  assert.ok(config.STATIC_DATA_URL.endsWith("data.json") && config.RESEARCH_URL.endsWith("research.json"));
  const lab = links.find(([, text]) => text === "python -m backend.vol_lab");
  assert.ok(lab, "the vol_lab command is a link");
  assert.match(lab[0], /^https:\/\/github\.com\/[^/]+\/[^/]+\/blob\/main\/backend\/vol_lab\.py$/, lab[0]);
  assert.ok(footer.includes('aria-label="Copy command: python -m backend.vol_lab"'), "copy button");
  assert.doesNotMatch(visibleText(footer), /backend\/vol_lab\.py/, "the link text stays the command, never a repository path");
  const leak = section(html, "leak");
  assert.ok(leak.includes("/blob/main/backend/backtest_walk_forward.py"), "an argument after the module does not change the path");
});

test("source and command parsing are pure and conservative", () => {
  const { sourceLinks, commandPath } = panelModule;
  assert.deepEqual(sourceLinks(["vol_lab.config"]).map((item) => item.href), [null], "no file named, no link");
  assert.deepEqual(sourceLinks(["price.json → close", "x"]).map((item) => item.file), ["price.json", "price.json"]);
  assert.equal(commandPath("python -m backend.vol_lab"), "backend/vol_lab.py");
  assert.equal(commandPath("python scripts/build_research_bundle.py"), "scripts/build_research_bundle.py");
  assert.equal(commandPath("pip install -r requirements.txt"), null);
  assert.equal(config.codeUrl("backend/x.py"), `${config.REPO_URL}/blob/main/backend/x.py`, "no build SHA in tests: main");
  assert.equal(config.RECORD_HISTORY_URL, `${config.REPO_URL}/commits/live-data/runtime-state/vol_track_record.json`);
  assert.equal(config.DATA_HISTORY_URL, `${config.REPO_URL}/commits/gh-pages/data.json`);
  assert.deepEqual(Object.keys(config.DOCS).sort(), ["changelog", "citation", "datasheet", "license", "methodology", "modelCard"]);
  for (const url of Object.values(config.DOCS)) assert.ok(url.startsWith(`${config.REPO_URL}/blob/main/`), url);
});

// ------------------------------------------------------------------------------------ tables
const Table = (props) => createElement(m.table.default, {
  caption: "Test table",
  rowKey: (row) => row.k,
  columns: [
    { key: "k", label: "Key", rowHeader: true, sortable: true },
    { key: "v", label: "Value", align: "right", sortable: true, hideBelow: 640 },
    { key: "note", label: "Note", csv: (row) => `${row.note}!` },
  ],
  rows: [{ k: "a", v: 1, note: "x, y" }, { k: "b", v: 2, note: "z" }],
  ...props,
});

test("a server-rendered table is never a tab stop until it is known to scroll", () => {
  const { html } = renderDashboard(research);
  assert.doesNotMatch(html, /tabindex="0"/);
  assert.doesNotMatch(html, /class="table-scroll[^"]*" role="region"/);
  const plain = renderToStaticMarkup(Table({ tall: true }));
  assert.doesNotMatch(plain, /tabindex|role="region"/);
});

test("DataTable options: CSV button, column groups, row ids, narrow-screen columns and cards", () => {
  const html = renderToStaticMarkup(Table({
    exportName: "test",
    groups: [{ label: "", span: 1 }, { label: "Measures", span: 2 }],
    rowId: (row) => `test-${row.k}`,
    cardsBelow: 640,
    announce: "items",
  }));
  assert.match(html, /<div class="table-caption-row"><p class="table-caption" aria-hidden="true">Test table<\/p><button type="button" class="csv-button" aria-describedby="[^"]+"><span class="sr-only">Download <\/span>CSV<\/button><\/div>/);
  assert.match(html, /<colgroup span="1"><\/colgroup><colgroup span="2"><\/colgroup>/);
  assert.match(html, /<tr class="group-row" role="row"><th scope="colgroup" colspan="1" class="group-start" role="columnheader"><\/th><th scope="colgroup" colspan="2" class="group-start" role="columnheader">Measures<\/th><\/tr>/i);
  assert.match(html, /<tr id="test-a" role="row">/);
  assert.match(html, /<td class="align-right col-wide group-start" data-label="Value" role="cell">1<\/td>/);
  assert.match(html, /<table class="data-table has-sticky is-cards has-groups" role="table">/);
  assert.match(html, /<p class="sr-only" aria-live="polite"><\/p>/, "the announcement is empty until something changes");
  const hidden = renderToStaticMarkup(Table({ exportName: "test", captionHidden: true }));
  assert.match(hidden, /<div class="table-caption-row"><button type="button" class="csv-button"/, "the button keeps its row when the caption is hidden");
  assert.doesNotMatch(renderToStaticMarkup(Table({})), /table-caption-row|csv-button|role="(table|row|cell)"/, "defaults unchanged");
  assert.match(renderToStaticMarkup(Table({})), /<th scope="col" class="align-left" aria-sort="none"><button type="button" class="sort-button">/);
});

test("CSV export: RFC 4180 quoting, a BOM, CRLF lines and raw values in the table's order", () => {
  const { toCsv, csvField, csvFileName } = csv;
  const text = toCsv([{ label: "name", value: (row) => row.name }, { label: "value, %", value: (row) => row.value }], [
    { name: "plain", value: 0.123456789 },
    { name: "comma, inside", value: -2 },
    { name: "a \"quote\"", value: null },
    { name: "two\nlines", value: Number.NaN },
  ]);
  assert.ok(text.startsWith("﻿"), "UTF-8 byte-order mark");
  assert.equal(text, "﻿name,\"value, %\"\r\nplain,0.123456789\r\n\"comma, inside\",-2\r\n\"a \"\"quote\"\"\",\r\n\"two\nlines\",\r\n");
  assert.equal(csvField("x\r"), "\"x\r\"");
  assert.equal(csvFileName("ledger", "2026-10-26T14:05:00Z"), "wti-vol_ledger_2026-10-26.csv");
  assert.equal(csvFileName("ledger", null), "wti-vol_ledger_snapshot.csv");
  const { csvColumns, orderRows } = m.table;
  const columns = [
    { key: "k", label: "Key" },
    { key: "v", label: createElement("span", null, "V"), sortValue: (row) => row.v * 10 },
    { key: "obj", label: "Obj" },
    { key: "skip", label: "Skip", csv: false },
    { key: "c", label: "C", csvLabel: "C raw", csv: (row) => row.k.toUpperCase() },
  ];
  const cols = csvColumns(columns);
  assert.deepEqual(cols.map((column) => column.label), ["Key", "v", "Obj", "C raw"]);
  assert.deepEqual(cols.map((column) => column.value({ k: "a", v: 2, obj: { x: 1 } })), ["a", 20, null, "A"]);
  const rows = [{ k: "b", v: 1 }, { k: "pinned", v: 0 }, { k: "a", v: 2 }];
  assert.deepEqual(orderRows(rows, columns, { key: "v", direction: "desc" }, (row) => row.k === "pinned").map((row) => row.k), ["a", "b", "pinned"]);
});

// ----------------------------------------------------------------------------- URL view state
test("view params round-trip through the URL, and a bad value reads as the default", () => {
  const { parseViewParams, serializeViewParams, readViewParam, parseViewList, VIEW_DEFAULTS } = view;
  const search = "?fcst=all&table=fcst,modl&q=ovx%20lag&status=supported";
  const params = parseViewParams(search);
  assert.deepEqual(params, { fcst: "all", table: "fcst,modl", q: "ovx lag", status: "supported" });
  assert.equal(serializeViewParams(params, VIEW_DEFAULTS), search, "commas stay readable, spaces are encoded");
  assert.deepEqual(parseViewParams(serializeViewParams(params, VIEW_DEFAULTS)), params);
  assert.equal(serializeViewParams({ fcst: "3Y", risk: "10y", table: "" }, VIEW_DEFAULTS), "?risk=10y", "defaults (any case) and empty values drop");
  assert.equal(serializeViewParams({ fcst: "3y" }, VIEW_DEFAULTS), "", "the bare URL is canonical");
  const ranges = ["1y", "3y", "all"];
  assert.equal(readViewParam(parseViewParams("?fcst=bogus"), "fcst", ranges, "3y"), "3y");
  assert.equal(readViewParam(parseViewParams("?fcst=ALL"), "fcst", ranges, "3y"), "all", "case-insensitive, read as the allowed spelling");
  assert.equal(readViewParam(parseViewParams("?fcst=ALL"), "fcst", ["1Y", "3Y", "ALL"], "3Y"), "ALL");
  assert.equal(readViewParam({}, "q", null, ""), "");
  assert.equal(readViewParam({ q: "free text" }, "q", null, ""), "free text");
  assert.equal(readViewParam({ var: "97" }, "var", (value) => ["99", "95"].includes(value), "99"), "99");
  assert.deepEqual(parseViewList(" fcst,,modl,fcst "), ["fcst", "modl"]);
  assert.deepEqual(parseViewParams(""), {});
  assert.deepEqual(parseViewParams(null), {});
});

// ---------------------------------------------------------------------------------- formats
test("fmtPValue with digits prints that many decimals, without moving a p-value onto a threshold", () => {
  const f = m.format;
  const spaced = (text) => text.replace(/\u00a0/g, " ");
  assert.deepEqual([0.02, 0.007, 0.27, 0.101, 0.054, 1].map((p) => f.fmtPValue(p, { digits: 3 })), ["0.020", "0.007", "0.270", "0.101", "0.054", "1.000"]);
  assert.equal(spaced(f.fmtPValue(0.0004, { digits: 3 })), "< 0.001", "below 10^-digits: the small-p style");
  assert.equal(f.fmtPValue(0.0496, { digits: 3 }), "0.0496", "0.050 would read as the 5% threshold");
  assert.equal(spaced(f.fmtPValue(0.05004, { digits: 3 })), "> 0.05");
  assert.equal(f.fmtPValue(0.0005, { digits: 4 }), "0.0005");
  assert.equal(f.fmtPValue(null, { digits: 3 }), "—");
  assert.equal(f.fmtPValue(0.02), "0.02", "the default is unchanged");
});

test("fmtNum and fmtSigned group thousands on request only", () => {
  const f = m.format;
  assert.equal(f.fmtNum(-1082.74, 1, { group: true }), "−1,082.7");
  assert.equal(f.fmtNum(1082.74, 1), "1082.7", "the default is unchanged");
  assert.equal(f.fmtNum(12345678, 0, { group: true }), "12,345,678");
  assert.equal(f.fmtSigned(-1082.74, 1, "", { group: true }), "−1,082.7");
  assert.equal(f.fmtSigned(556.1, 1, "", { group: true }), "+556.1");
  assert.equal(f.fmtNum(-0.04, 1, { group: true }), "0.0", "a value that rounds to zero is unsigned");
});

// ------------------------------------------------------------------------ research.json missing
test("without research.json one notice under the verdict names the sections, and the panels point to it", () => {
  const { html, text, errors } = renderDashboard(null, "unavailable");
  assert.deepEqual(errors, []);
  assert.equal((text.match(/research\.json did not load, so/g) ?? []).length, 1, "exactly one page-level notice");
  const { RESEARCH_SECTIONS } = m.dashboard;
  const codes = SECTIONS.filter((item) => RESEARCH_SECTIONS.includes(item.id)).map((item) => item.code);
  assert.ok(text.includes(`research.json did not load, so ${codes.length} sections below are missing figures: ${codes.join(", ")}.`));
  assert.ok(text.includes("The forecast, its validation and the live record come from data.json."));
  assert.ok(html.indexOf('id="research-notice"') < html.indexOf('class="panel-grid"'), "under the verdict, above the panels");
  assert.ok(html.indexOf('id="research-notice"') > html.indexOf("verdict"), "after the verdict");
  assert.doesNotMatch(text, /live record above/);
  assert.doesNotMatch(text, /research\.json is not available/);
  assert.ok((text.match(/research\.json did not load; see the notice under the verdict\./g) ?? []).length >= RESEARCH_SECTIONS.length - 1);
  for (const status of ["loading", "ready"]) {
    assert.doesNotMatch(renderDashboard(status === "ready" ? research : null, status).text, /did not load, so/, `no notice while ${status}`);
  }
});
