// Navigation: the grouped section registry, the jump palette's commands and ranking, the keyboard
// shortcut filter, and the top bar before and after the data arrives. Rendered against the fixtures.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SECTION_GROUPS, SECTIONS, startsGroup } from "../../src/app/sections.js";
import { loadDashboard, readFixture } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let commands;
let shortcuts;
let TopBar;
let research;
before(async () => {
  m = await loadDashboard();
  commands = await m.load("/src/app/commands.js");
  shortcuts = await m.load("/src/app/useShortcuts.js");
  TopBar = (await m.load("/src/app/TopBar.jsx")).default;
  research = await m.load("/src/lib/research.js");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const researchJson = readFixture("research.json");
const build = (extra = {}) => commands.buildCommands({ data, research: researchJson, ...extra });

test("every section has a group, the groups run in page order, and the page order is unchanged", () => {
  assert.deepEqual(SECTIONS.map((section) => section.id),
    ["fcst", "modl", "stab", "calb", "risk", "cone", "vrp", "live", "leak", "stdy", "ledg", "meth", "mkt"]);
  const groupIds = SECTION_GROUPS.map((group) => group.id);
  for (const section of SECTIONS) assert.ok(groupIds.includes(section.group), `${section.id} has a known group`);
  // Each group is one contiguous run, in the order SECTION_GROUPS lists them.
  const runs = SECTIONS.map((section) => section.group).filter((group, index, all) => index === 0 || all[index - 1] !== group);
  assert.deepEqual(runs, groupIds);
  assert.deepEqual(SECTIONS.filter((_, index) => startsGroup(index)).map((section) => section.id), ["cone", "live", "stdy"]);
});

test("the palette reaches all 13 sections, every leaderboard model, every ledger row and the glossary", () => {
  const items = build();
  const ids = (kind) => items.filter((item) => item.kind === kind);
  assert.deepEqual(ids("section").map((item) => item.run.target), SECTIONS.map((section) => section.id));
  const models = research.leaderboardRows(research.getLab(researchJson));
  assert.ok(models.length > 0);
  assert.deepEqual(ids("model").map((item) => item.run.target), models.map((row) => `modl-${row.key}`));
  for (const item of ids("model")) assert.equal(item.run.fallback, "modl");
  const ledger = researchJson.ledger.rows;
  assert.deepEqual(ids("hypothesis").map((item) => item.run.target), ledger.map((row) => `ledg-${row.id}`));
  for (const item of ids("hypothesis")) assert.equal(item.run.reveal, true);
  const terms = ids("term");
  assert.ok(terms.length > 0);
  for (const item of terms) assert.match(item.run.target, /^stat-/);
  // The model detail reads the payload, so it moves when the payload moves.
  const record = models.find((row) => row.role === "record");
  const recordItem = items.find((item) => item.id === `model:${record.key}`);
  assert.ok(recordItem.detail.includes(m.format.fmtNum(record.qlike, 4)) && recordItem.detail.includes("model of record"));
  // Without research.json: the sections and data.json actions only.
  const bare = commands.buildCommands({ data, research: null });
  assert.equal(bare.filter((item) => item.kind === "section").length, SECTIONS.length);
  assert.equal(bare.filter((item) => ["model", "hypothesis"].includes(item.kind)).length, 0);
  assert.ok(!bare.some((item) => item.run.type === "download" && item.run.file === "research.json"));
  assert.ok(!bare.some((item) => item.run.type === "view" && ["risk", "var", "leak"].includes(item.run.key)));
});

test("view actions mark the current value and follow the payload's choices", () => {
  const items = build({ view: { fcst: "all", table: "modl" }, tables: ["fcst", "modl"] });
  const view = (key) => items.filter((item) => item.run.type === "view" && item.run.key === key);
  assert.deepEqual(view("fcst").map((item) => [item.run.value, Boolean(item.current)]), [["1y", false], ["3y", false], ["all", true]]);
  assert.equal(view("mkt").find((item) => item.current)?.run.value, "all", "the default counts as current");
  const levels = new Set(research.varRows(research.getLab(researchJson)).map((row) => String(row.confidence)));
  assert.deepEqual(view("var").map((item) => item.run.value).sort(), ["95", "99"].filter((value) => levels.has(value)));
  const tables = items.filter((item) => item.run.type === "table");
  assert.deepEqual(tables.map((item) => item.label), ["FCST table view: on", "MODL table view: off"]);
});

test("ranking: the exact code first, then name prefixes, then word prefixes, then abbreviations", () => {
  const items = build({ tables: ["modl"] });
  const ranked = commands.rankCommands("MODL", items);
  assert.equal(ranked[0].id, "section:modl", "the mnemonic wins");
  const scores = ranked.map((item) => commands.matchScore("MODL", item));
  assert.deepEqual(scores, [...scores].sort((a, b) => a - b), "best first");
  const prefix = ranked.findIndex((item) => item.id === "table:modl");
  const subsequence = ranked.findIndex((item) => commands.matchScore("MODL", item) >= 3);
  assert.ok(prefix > 0 && subsequence > prefix, "a name prefix ranks above a subsequence match");
  assert.equal(commands.matchScore("modl", { label: "Model leaderboard" }), 3 + 5 / 1000, "m-o-d-l within 'model'");
  assert.equal(commands.matchScore("mdl", { code: "MODL", label: "Model leaderboard" }) >= 3, true);
  assert.equal(commands.matchScore("fcst all", { label: "The forecast is calibrated across all regimes and lags" }), null,
    "letters scattered across a sentence do not match");
  // The acceptance cases: a code, a model name, a ledger id and a term each come first.
  assert.equal(commands.rankCommands("QLIKE", items)[0].id, "term:qlike");
  const ledgerId = researchJson.ledger.rows.find((row) => row.id.includes("ovx"))?.id ?? researchJson.ledger.rows[0].id;
  assert.equal(commands.rankCommands(ledgerId, items)[0].id, `hypothesis:${ledgerId}`);
  const model = research.leaderboardRows(research.getLab(researchJson)).find((row) => row.role === "challenger");
  assert.equal(commands.rankCommands(research.shortLabel(model.label), items)[0].id, `model:${model.key}`);
  assert.equal(commands.rankCommands("fcst all", items)[0].id, "view:fcst:all");
  assert.ok(commands.rankCommands("e", items).length <= commands.MAX_RESULTS, "at most 30 results");
  assert.deepEqual(commands.rankCommands("zzzz", items), []);
  // An empty query lists the sections and the page actions, in their own order.
  const start = commands.rankCommands("  ", items);
  assert.deepEqual(start.slice(0, SECTIONS.length).map((item) => item.id), SECTIONS.map((section) => `section:${section.id}`));
  assert.ok(start.every((item) => item.general));
});

test("results group by kind, in the order of each kind's best match", () => {
  const items = build();
  const groups = commands.groupCommands(commands.rankCommands("QLIKE", items));
  assert.equal(groups[0].kind, "term");
  assert.equal(new Set(groups.map((group) => group.kind)).size, groups.length, "one group per kind");
});

test("the snapshot reference names the view, the data's frozen time and the build", () => {
  assert.equal(commands.snapshotReference("https://x.test/?fcst=all#modl", data.frozen_at, "abc1234"),
    `https://x.test/?fcst=all#modl · data frozen ${data.frozen_at} · build abc1234`);
  assert.equal(commands.snapshotReference("https://x.test/", data.frozen_at, null), `https://x.test/ · data frozen ${data.frozen_at}`);
});

test("shortcuts never fire while typing or with a modifier, and can be turned off", () => {
  const { keyToAction } = shortcuts;
  const body = { tagName: "BODY" };
  const key = (k, extra = {}) => keyToAction({ key: k, target: body, ...extra });
  assert.equal(key("j"), "next");
  assert.equal(key("k"), "previous");
  assert.equal(key("t"), "table");
  assert.equal(key("c"), "copy-link");
  assert.equal(key("/"), "palette");
  assert.equal(key("?", { shiftKey: true }), "shortcuts");
  assert.equal(key("/", { shiftKey: true }), "palette", "layouts that need Shift for /");
  assert.equal(key("k", { ctrlKey: true }), "palette");
  assert.equal(key("K", { metaKey: true }), "palette");
  for (const target of [{ tagName: "INPUT" }, { tagName: "textarea" }, { tagName: "SELECT" }, { tagName: "DIV", isContentEditable: true },
    { tagName: "DIV", getAttribute: (name) => (name === "role" ? "combobox" : null) }]) {
    for (const k of ["j", "k", "t", "c", "/", "?"]) assert.equal(keyToAction({ key: k, target, shiftKey: k === "?" }), null, `${k} in ${target.tagName}`);
    assert.equal(keyToAction({ key: "k", ctrlKey: true, target }), null, "not even the chord while typing");
  }
  for (const modifier of ["ctrlKey", "metaKey", "altKey"]) {
    for (const k of ["j", "t", "c", "/"]) assert.equal(key(k, { [modifier]: true }), null, `${modifier}+${k}`);
  }
  assert.equal(key("J", { shiftKey: true }), null);
  assert.equal(key("j", { shiftKey: true }), null);
  assert.equal(key("k", { ctrlKey: true, shiftKey: true }), null);
  assert.equal(key("j", { isComposing: true }), null, "an IME composition");
  assert.equal(key("j", { defaultPrevented: true }), null);
  assert.equal(key("j", { repeat: true }), "next", "holding j keeps moving");
  assert.equal(key("t", { repeat: true }), null, "holding t does not flicker the table");
  for (const k of ["j", "k", "t", "c", "/", "?"]) assert.equal(keyToAction({ key: k, target: body, shiftKey: k === "?" }, { singleKeys: false }), null, `${k} off`);
  assert.equal(keyToAction({ key: "k", ctrlKey: true, target: body }, { singleKeys: false }), "palette", "the chord stays on");
});

test("the top bar shows no section link until the dashboard is on screen", () => {
  const render = (props) => renderToStaticMarkup(createElement(TopBar, { frozenAtMs: null, lastUpdate: null, ...props }));
  const loading = render({ hasData: false });
  for (const section of SECTIONS) assert.ok(!loading.includes(`href="#${section.id}"`), `no #${section.id} link while loading`);
  assert.match(loading, /class="codenav is-reserved" aria-hidden="true"/, "the strip keeps its space");
  assert.match(loading, /class="jump-button is-reserved"[^>]*disabled=""/, "the Jump button is reserved and disabled");
  const failed = render({ hasData: true, data: { error: "SYSTEM_ERROR" } });
  for (const section of SECTIONS) assert.ok(!failed.includes(`href="#${section.id}"`), `no #${section.id} link on the error screen`);

  const ready = render({ hasData: true, data, research: researchJson, frozenAtMs: Date.parse(data.frozen_at) });
  for (const section of SECTIONS) assert.ok(ready.includes(`href="#${section.id}"`), `#${section.id}`);
  assert.equal((ready.match(/<li class="group-start">/g) ?? []).length, SECTION_GROUPS.length - 1);
  assert.match(ready, /<nav class="codenav" aria-label="Sections">/);
  assert.match(ready, /aria-label="Jump: [^"]+" aria-haspopup="dialog" aria-keyshortcuts="\/ Control\+K Meta\+K"/);
  assert.ok(!ready.includes("<dialog"), "the palette mounts only when opened");
  // The snapshot age keeps its word on phones ("3h old"); a screen reader gets one sentence.
  assert.match(ready, /<span class="snapshot-old"> old<\/span>/);
  assert.match(ready, /<span class="sr-only">Snapshot [^<]+ old, frozen [^<]+<\/span>/);
});
