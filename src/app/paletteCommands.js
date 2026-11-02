// What the jump palette can reach and how a query ranks it. Pure (tests run it on the fixtures). It
// loads with the palette, on demand, so the first screen's script does not carry it; the in-page
// moves the palette shares with the shortcuts and the code bar are in ./commands.js.
import { fmtNum } from "../lib/format";
import { GLOSSARY } from "../lib/glossary";
import { DIST_VIEW, hasDistribution } from "../lib/distribution";
import { MODL_VIEWS, regimeLossSplit } from "../lib/regimes";
import { STAB_VIEWS, cumulativeRows, fluctuationSetup } from "../lib/stability";
import {
  asText, getLab, leaderboardRows, ledgerStatusLabel, ledgerText, mcsLevel, modelLabels, postmortemWindow, researchLedger,
  shortLabel, varRows,
} from "../lib/research";
import { SECTION_GROUPS, SECTIONS, sectionById } from "./sections";
import { parseViewList, VIEW_DEFAULTS } from "./viewState";

/** Result groups, in the order they are listed when nothing ranks them. */
export const COMMAND_KINDS = [
  { kind: "section", label: "Sections" },
  { kind: "action", label: "Actions" },
  { kind: "model", label: "Models (MODL)" },
  { kind: "hypothesis", label: "Ledger hypotheses (LEDG)" },
  { kind: "term", label: "Glossary (METH)" },
];
const KIND_ORDER = new Map(COMMAND_KINDS.map(({ kind }, index) => [kind, index]));

export const MAX_RESULTS = 30;

/**
 * The view settings the palette can change, with the labels the panels' own controls show. Keys
 * and values are viewState's reserved ones (see the header of viewState.js).
 */
const VIEW_SETTINGS = [
  { key: "fcst", section: "fcst", setting: "range", detail: "Forecast chart", values: [["1y", "1Y"], ["3y", "3Y"], ["all", "All"]] },
  { key: "risk", section: "risk", setting: "range", detail: "VaR chart", values: [["3y", "3Y"], ["10y", "10Y"], ["all", "All"]] },
  { key: "var", section: "risk", setting: "VaR level", detail: "Backtest by method", values: [["99", "99%"], ["95", "95%"]] },
  { key: "leak", section: "leak", setting: "window", detail: "Walk-forward window", values: [["5y", "5Y"], ["10y", "10Y"]] },
  { key: "mkt", section: "mkt", setting: "range", detail: "Price chart", values: [["1w", "1W"], ["1m", "1M"], ["1y", "1Y"], ["all", "All"]] },
  { key: "modl", section: "modl", setting: "view", detail: "Leaderboard: all days, QLIKE by regime, or the predictive distribution's scores",
    values: MODL_VIEWS.map((view) => [view.value, view.name]) },
  { key: "stab", section: "stab", setting: "over-time view", detail: "Rolling test with its band, or the cumulative difference",
    values: STAB_VIEWS.map((view) => [view.value, view.name]) },
];
const LEAK_WINDOWS = { "5y": "five_year", "10y": "ten_year" };

/** Which values of a view setting the payloads can show (a setting with fewer than two is left out). */
const availableValues = (setting, { research }) => {
  const values = setting.values;
  if (setting.key === "leak") return values.filter(([value]) => postmortemWindow(research, LEAK_WINDOWS[value]));
  if (setting.key === "var") {
    const levels = new Set(varRows(getLab(research)).map((row) => String(row.confidence)));
    return values.filter(([value]) => levels.has(value));
  }
  if (setting.key === "risk") return getLab(research)?.var_backtest ? values : [];
  if (setting.key === "stab") return fluctuationSetup(getLab(research)) && cumulativeRows(getLab(research)).length ? values : [];
  if (setting.key === "modl") {
    const lab = getLab(research);
    const rows = leaderboardRows(lab);
    return values.filter(([value]) => value === "all" || (value === DIST_VIEW ? hasDistribution(lab) : regimeLossSplit(lab, rows, value)));
  }
  return values;
};

const sameText = (a, b) => String(a ?? "").toLowerCase() === String(b ?? "").toLowerCase();

/**
 * Everything the palette can jump to or do, as plain data: {id, kind, label, detail?, code?, mono?,
 * current?, general?, keywords?, run}. `run` says what happens ({type: "jump", target, fallback,
 * reveal?}, {type: "view", key, value, section}, {type: "table", section}, or one of copy-view,
 * copy-reference, notes, print, download, shortcuts, github). Pure.
 *
 * @param {object} input
 * @param {object|null} input.data          data.json
 * @param {object|null} input.research     research.json, or null
 * @param {Record<string, string>} [input.titles]  each section's current h2 text, by id
 * @param {string[]} [input.tables]        sections that have a table view
 * @param {Record<string, string>} [input.view]    the URL's view parameters (parseViewParams)
 * @returns {object[]}
 */
export function buildCommands({ data = null, research = null, titles = {}, tables = [], view = {} } = {}) {
  const commands = [];
  const groupName = new Map(SECTION_GROUPS.map((group) => [group.id, group.name]));

  for (const section of SECTIONS) {
    commands.push({
      id: `section:${section.id}`,
      kind: "section",
      code: section.code,
      label: section.name,
      detail: asText(titles[section.id]) ?? groupName.get(section.group) ?? null,
      keywords: groupName.get(section.group) ?? "",
      general: true,
      run: { type: "jump", target: section.id },
    });
  }

  for (const setting of VIEW_SETTINGS) {
    const values = availableValues(setting, { data, research });
    if (values.length < 2) continue;
    const { code } = sectionById(setting.section);
    const current = view[setting.key] ?? VIEW_DEFAULTS[setting.key];
    for (const [value, shown] of values) {
      commands.push({
        id: `view:${setting.key}:${value}`,
        kind: "action",
        label: `${code} ${setting.setting}: ${shown}`,
        detail: setting.detail,
        current: sameText(current, value),
        run: { type: "view", key: setting.key, value, section: setting.section },
      });
    }
  }

  const open = new Set(parseViewList(view.table));
  for (const id of tables) {
    const section = sectionById(id);
    if (!section) continue;
    commands.push({
      id: `table:${id}`,
      kind: "action",
      label: `${section.code} table view: ${open.has(id) ? "off" : "on"}`,
      detail: `${section.name}: ${open.has(id) ? "back to the figure" : "the numbers as a table"}`,
      keywords: "table toggle",
      run: { type: "table", section: id },
    });
  }

  const frozenAt = asText(data?.frozen_at);
  commands.push(
    { id: "do:copy-view", kind: "action", general: true, label: "Copy link to this view", detail: "The address with its ranges, tables and section", keywords: "share url", run: { type: "copy-view" } },
    ...(frozenAt ? [{ id: "do:copy-reference", kind: "action", general: true, label: "Copy snapshot reference", detail: "The link, when the data was frozen, and the build", keywords: "cite citation", run: { type: "copy-reference" } }] : []),
    { id: "do:notes", kind: "action", general: true, label: "Open all method notes", detail: "Every section's method disclosure", keywords: "expand details", run: { type: "notes" } },
    { id: "do:print", kind: "action", general: true, label: "Print or save as PDF", detail: "Every figure mounted, every note open", keywords: "pdf", run: { type: "print" } },
    ...(data ? [{ id: "do:data", kind: "action", general: true, label: "Download data.json", detail: "Forecast, validation and live record", keywords: "file json", run: { type: "download", file: "data.json" } }] : []),
    ...(research ? [{ id: "do:research", kind: "action", general: true, label: "Download research.json", detail: "Benchmark lab, post-mortem and ledger", keywords: "file json", run: { type: "download", file: "research.json" } }] : []),
    { id: "do:shortcuts", kind: "action", general: true, label: "Keyboard shortcuts", detail: "And the switch for single-key shortcuts", keywords: "keys help", run: { type: "shortcuts" } },
    { id: "do:github", kind: "action", general: true, label: "Source on GitHub", detail: "Code, data history and issues, in a new tab", keywords: "repository code", run: { type: "github" } },
  );

  const lab = getLab(research);
  for (const row of leaderboardRows(lab)) {
    const role = row.role === "record" ? "model of record" : row.role === "challenger" ? "challenger" : null;
    commands.push({
      id: `model:${row.key}`,
      kind: "model",
      label: shortLabel(row.label),
      detail: [row.qlike != null ? `QLIKE ${fmtNum(row.qlike, 4)}` : null, row.family, role, row.timingControl ? "timing control" : null]
        .filter(Boolean).join(" · "),
      keywords: row.key,
      run: { type: "jump", target: `modl-${row.key}`, fallback: "modl" },
    });
  }

  const ledger = researchLedger(research);
  if (ledger) {
    const words = { labels: modelLabels(lab), mcsLevel: mcsLevel(lab) };
    for (const row of ledger.rows) {
      const id = asText(row.id);
      if (!id) continue;
      commands.push({
        id: `hypothesis:${id}`,
        kind: "hypothesis",
        label: ledgerText(asText(row.hypothesis) ?? id, words),
        mono: id,
        detail: [ledgerStatusLabel(row.status), asText(row.family)].filter(Boolean).join(" · "),
        keywords: id,
        run: { type: "jump", target: `ledg-${id}`, fallback: "ledg", reveal: true },
      });
    }
  }

  for (const entry of GLOSSARY) {
    if (!entry.ref) continue;
    commands.push({
      id: `term:${entry.key}`,
      kind: "term",
      label: entry.term,
      detail: entry.definition,
      matchDetail: false, // a definition mentions too many other words to search
      keywords: entry.patterns.join(" "),
      run: { type: "jump", target: entry.ref, fallback: "meth" },
    });
  }
  return commands;
}

// ------------------------------------------------------------------------------------- ranking
const fold = (text) => String(text ?? "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
const compact = (text) => fold(text).replace(/[^a-z0-9%]+/g, "");
const wordsOf = (text) => fold(text).split(/[^a-z0-9%]+/).filter(Boolean);
// A subsequence match may spread over at most this many times the query's length.
const SUBSEQUENCE_STRETCH = 3;

/** The span of the shortest left-to-right match of `needle`'s characters in `hay`, or null. */
const subsequenceSpan = (needle, hay) => {
  let best = null;
  for (let start = hay.indexOf(needle[0]); start !== -1; start = hay.indexOf(needle[0], start + 1)) {
    let at = start;
    for (let index = 1; index < needle.length && at !== -1; index += 1) at = hay.indexOf(needle[index], at + 1);
    if (at === -1) break;
    const span = at - start + 1;
    if (best == null || span < best) best = span;
  }
  return best;
};

/**
 * How well one command matches a query, lower is better; null when it does not match. Tiers:
 * 0 an exact code, name or id (the mnemonic); 1 a prefix of the code, name or id; 2 every query
 * word starts a word of the command (2.5 when that needs its detail text); 3 the query's
 * characters in order within the code and name, spread over at most three times the query's
 * length (closer together ranks higher). Pure.
 *
 * @param {string} query
 * @param {{code?: string, label: string, mono?: string, keywords?: string, detail?: string}} item
 * @returns {number|null}
 */
export const matchScore = (query, item) => {
  const needle = compact(query);
  if (!needle) return null;
  const names = [item.code, item.label, item.mono].filter(Boolean).map(compact);
  if (names.some((name) => name === needle)) return 0;
  if (names.some((name) => name.startsWith(needle))) return 1;
  const tokens = wordsOf(query);
  const own = wordsOf([item.code, item.label, item.mono, item.keywords].filter(Boolean).join(" "));
  const startsAWord = (words) => tokens.every((token) => words.some((word) => word.startsWith(token)));
  if (startsAWord(own)) return 2;
  if (item.detail && item.matchDetail !== false && startsAWord([...own, ...wordsOf(item.detail)])) return 2.5;
  // An abbreviation ("mdl" for MODL) stays compact; letters scattered across a sentence do not count.
  const span = subsequenceSpan(needle, compact([item.code, item.label].filter(Boolean).join(" ")));
  return span == null || span > needle.length * SUBSEQUENCE_STRETCH ? null : 3 + span / 1000;
};

/**
 * The commands a query reaches, best first, at most `limit`. An empty query lists the sections
 * and the general actions in their own order. Ties keep the kind order (sections, actions,
 * models, hypotheses, terms), then the list order. Pure.
 *
 * @param {string} query
 * @param {object[]} items  buildCommands output
 * @param {number} [limit]
 */
export function rankCommands(query, items, limit = MAX_RESULTS) {
  if (!compact(query)) return items.filter((item) => item.general).slice(0, limit);
  return items
    .map((item, index) => ({ item, index, score: matchScore(query, item) }))
    .filter((entry) => entry.score != null)
    .sort((a, b) => a.score - b.score
      || (KIND_ORDER.get(a.item.kind) ?? 9) - (KIND_ORDER.get(b.item.kind) ?? 9)
      || a.index - b.index)
    .slice(0, limit)
    .map((entry) => entry.item);
}

/**
 * Ranked commands in groups of one kind each, the groups in the order of their best match:
 * [{kind, label, items}]. Pure.
 */
export const groupCommands = (ranked) => {
  const groups = [];
  for (const item of ranked) {
    let group = groups.find((entry) => entry.kind === item.kind);
    if (!group) {
      group = { kind: item.kind, label: COMMAND_KINDS.find((entry) => entry.kind === item.kind)?.label ?? item.kind, items: [] };
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
};

/** "{url} · data frozen {frozen_at} · build {sha}": what a citation of this exact view needs. Pure. */
export const snapshotReference = (url, frozenAt, gitSha) =>
  [url, frozenAt ? `data frozen ${frozenAt}` : null, gitSha ? `build ${gitSha}` : null].filter(Boolean).join(" · ");
