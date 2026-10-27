// What the jump palette can reach, how a query ranks it, and the in-page moves it shares with the
// keyboard shortcuts and the code bar. buildCommands and rankCommands are pure (tests run them on
// the fixtures); the functions after them touch the DOM and run only in the browser.
import { fmtNum } from "../lib/format";
import { GLOSSARY } from "../lib/glossary";
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
// How long a revealed ledger row may take to appear before the jump settles for its section.
const REVEAL_WAIT_MS = 250;

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

// --------------------------------------------------------------------------- moving in the page
// The section the code bar marks as current (TopBar keeps it up to date), for the shortcuts.
let primarySection = null;
export const setPrimarySection = (id) => { primarySection = id ?? null; };
export const getPrimarySection = () => primarySection;

/** Tells the code bar which section the reader just moved to, before the scroll settles. */
export const pinSection = (id) => {
  if (id) window.dispatchEvent(new CustomEvent("nav:pin", { detail: { id } }));
};

const isShown = (element) => Boolean(element) && !element.closest("[hidden]");
const sectionOf = (element) => element?.closest("section.panel[id]") ?? null;
const headingIn = (element) => element?.querySelector("h2[tabindex]") ?? null;

/**
 * Focuses where a jump landed, without moving the view: a section's heading, or else the element
 * itself (a model or ledger row, a reference item), so a screen reader announces it and the next
 * Tab continues from there instead of from the section's top. An element that cannot take focus
 * gets tabindex -1 for as long as it has it.
 */
const focusTarget = (node) => {
  if (!node) return;
  if (node.matches("section")) {
    headingIn(node)?.focus({ preventScroll: true });
    return;
  }
  if (!node.hasAttribute("tabindex")) {
    node.setAttribute("tabindex", "-1");
    node.addEventListener("blur", () => node.removeAttribute("tabindex"), { once: true });
  }
  node.focus({ preventScroll: true });
};

/**
 * Scrolls a section (or an element in one) to the top of the view, under the bar, and focuses
 * the section's heading (or the element). Smooth through the page's CSS, instant under reduced motion.
 */
export function focusSection(element, { block = "start" } = {}) {
  if (!element) return;
  element.scrollIntoView({ block });
  pinSection((sectionOf(element) ?? element).id);
  focusTarget(element);
}

/**
 * Goes to an element by id, through the URL's #hash (so the browser, the hash landing, the method
 * notes and the ledger all react as they do to a link), then focuses it (a section, its heading). When
 * the element is missing or hidden it goes to `fallback` instead; with `reveal`, the hash is set
 * first and the fallback is used only if nothing showed the element within a moment (the ledger
 * shows a filtered-out row when the hash names it).
 *
 * @returns {boolean} whether there was anywhere to go
 */
export function jumpTo(target, { fallback = null, reveal = false } = {}) {
  const element = document.getElementById(target);
  const landOn = (id) => {
    const node = document.getElementById(id);
    if (!node) return false;
    if (window.location.hash === `#${id}`) {
      focusSection(node, { block: node.matches("section") ? "start" : "center" });
    } else {
      pinSection(sectionOf(node)?.id ?? id);
      window.location.hash = id;
      // The browser scrolls to it. A section's heading takes focus on the hashchange (useHashLanding).
      if (!node.matches("section")) focusTarget(node);
    }
    return true;
  };
  if (isShown(element)) return landOn(target);
  if (reveal && fallback) {
    pinSection(fallback);
    window.location.hash = target;
    window.setTimeout(() => {
      const shown = document.getElementById(target);
      if (isShown(shown)) {
        focusTarget(shown);
        return;
      }
      const url = `${window.location.pathname}${window.location.search}#${fallback}`;
      try { window.history.replaceState(window.history.state, "", url); } catch { /* sandboxed frame */ }
      focusSection(document.getElementById(fallback));
    }, REVEAL_WAIT_MS);
    return true;
  }
  return fallback ? landOn(fallback) : false;
}

/** The bar's height plus the scroll padding: where a section's top lands after a jump. */
const landingLine = () => {
  const padding = parseFloat(window.getComputedStyle(document.documentElement).scrollPaddingTop);
  return Number.isFinite(padding) ? padding : 0;
};

/**
 * The next (step 1) or previous (step -1) section from the current one, or, before any section is
 * current, the first one below the landing line (or the last one above it). Returns its id, or
 * null at either end.
 */
export function goToAdjacentSection(step) {
  const nodes = SECTIONS.map((section) => document.getElementById(section.id)).filter(isShown);
  if (!nodes.length) return null;
  const index = nodes.findIndex((node) => node.id === primarySection);
  const line = landingLine();
  let target;
  if (index >= 0) target = nodes[index + step] ?? null;
  else if (step > 0) target = nodes.find((node) => node.getBoundingClientRect().top > line + 1) ?? null;
  else target = [...nodes].reverse().find((node) => node.getBoundingClientRect().top < line - 1) ?? null;
  if (!target) return null;
  focusSection(target);
  return target.id;
}

/** A link to one section in the current view: the page's address, its view query and #id. */
export const sectionUrl = (id) => `${window.location.origin}${window.location.pathname}${window.location.search}${id ? `#${id}` : ""}`;

/** Saves a same-origin file under its own name (no new request beyond the file itself). */
export function downloadFile(href, name) {
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}
