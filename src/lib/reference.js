// Pure helpers for the reference sections (LEDG, METH, the footer): the research ledger's order,
// search and row links; the model card's specification and evaluation lines; the data-vintage
// rows; the corrections list; and the citation built from CITATION.cff. Every value is read from
// the payloads (data.json, research.json) or from CITATION.cff; nothing here states a statistic.
import { ALPHA, shortModelName } from "./assertions";
import { NBSP, fmtNum, fmtP, fmtPValue, fmtSigned, num } from "./format";
import { asArray, asObject, asText, getLab, getRoles, getVol, leaderboardRows, mcsLevel, mcsSummary, postmortemWindow, varSeries } from "./research";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;
const isoDate = (value) => (typeof value === "string" && ISO_DATE.test(value) ? value.slice(0, 10) : null);
const latest = (values) => values.filter(Boolean).sort().at(-1) ?? null;
const plural = (count, word, many = `${word}s`) => `${count} ${count === 1 ? word : many}`;

// ------------------------------------------------------------------------------------- ledger

/** Rows shown before "Show all": enough to see every supported claim, short of a scroll trap. */
export const LEDGER_PAGE = 20;

// The default order reads best evidence first: supported claims that survive Benjamini-Hochberg,
// the other supported claims, inconclusive, not supported, and the retracted record last.
const STATUS_RANK = { supported: 1, inconclusive: 2, not_supported: 3, retracted: 4 };
const UNKNOWN_RANK = 3;

/** 0 supported and surviving BH, 1 supported, 2 inconclusive, 3 not supported (or unknown), 4 retracted. */
export const ledgerStatusRank = (row) => {
  if (row?.status === "supported" && row.survives_bh === true) return 0;
  return STATUS_RANK[row?.status] ?? UNKNOWN_RANK;
};

/**
 * The ledger's default sort value: the status rank, then q ascending within a status (a row with
 * no q, e.g. one selected after a search, sorts last in its status). q is at most 1, so q / 2
 * never reaches the next rank.
 */
export const ledgerOrderValue = (row) => ledgerStatusRank(row) + (num(row?.q) ?? 1) / 2;

const searchTerms = (query) => (typeof query === "string" ? query.toLowerCase().split(/\s+/).filter(Boolean) : []);

/**
 * Whether a ledger row matches a search: every whitespace-separated term must occur, case-
 * insensitively, in the row's hypothesis, test, id or family. `display` adds the text as the page
 * shows it (model keys as labels), so a reader can search for what they see.
 */
export const matchesLedgerQuery = (row, query, display = null) => {
  const terms = searchTerms(query);
  if (!terms.length) return true;
  const fields = [row?.hypothesis, row?.test, row?.id, row?.family];
  const shown = display ? [display(row?.hypothesis), display(row?.test)] : [];
  const haystack = [...fields, ...shown].filter((text) => typeof text === "string").join("\n").toLowerCase();
  return terms.every((term) => haystack.includes(term));
};

/** Ledger rows that pass the family and status filters ("all" passes everything) and the search. */
export const filterLedgerRows = (rows, { family = "all", status = "all", query = "" } = {}, display = null) =>
  asArray(rows).filter((row) => (family === "all" || row.family === family)
    && (status === "all" || row.status === status)
    && matchesLedgerQuery(row, query, display));

/** The anchor id of a ledger row ("ledg-vol_qlike_vs_persistence"). */
export const ledgerAnchor = (id) => `ledg-${id}`;

/** The row id a "#ledg-<id>" fragment names, or null. */
export const ledgerHashId = (hash) => {
  let text = typeof hash === "string" ? hash.replace(/^#/, "") : "";
  try {
    text = decodeURIComponent(text);
  } catch {
    // keep the raw fragment
  }
  const match = /^ledg-(.+)$/.exec(text);
  return match ? match[1] : null;
};

/**
 * The ledger view that shows the row a "#ledg-<id>" link names: search and filters cleared and every
 * row shown, with the row as the target. Null when the fragment names no row in `ids`.
 *
 * @param {{query?: string, family?: string, status?: string, all?: boolean}} view
 * @param {string} hash
 * @param {Set<string>|Map<string, unknown>} ids
 */
export const revealLedgerView = (view, hash, ids) => {
  const id = ledgerHashId(hash);
  if (!id || !ids?.has(id)) return null;
  return { ...view, query: "", family: "all", status: "all", all: true, target: id };
};

/** The rows on screen: the first LEDGER_PAGE of the ordered rows, or every row. */
export const visibleLedgerRows = (ordered, all) => (all ? ordered : ordered.slice(0, LEDGER_PAGE));

/**
 * A multiplicity flag as the ledger prints it: "excluded" for a row selected after a search (it is
 * outside the adjustment), "n/a" for a retracted row, else yes / no; null when the row has none.
 */
export const ledgerFlagText = (row, key) => {
  if (row?.selectedAfterSearch) return "excluded";
  if (row?.status === "retracted") return "n/a";
  return row?.[key] === true ? "yes" : row?.[key] === false ? "no" : null;
};

/**
 * "18 supported; 17 in the adjusted family (1 selected after a search)": the supported count and
 * the count the title's "of 17" refers to, reconciled in one phrase.
 */
export const ledgerSupportText = (ledger) => {
  if (!ledger) return null;
  const outside = ledger.supportedAfterSearch ?? 0;
  const inFamily = ledger.supported - outside;
  return outside
    ? `${ledger.supported} supported; ${inFamily} in the adjusted family (${outside} selected after a search)`
    : `${ledger.supported} supported, all in the adjusted family`;
};

// The strip's groups, in status-rank order.
const STRIP_GROUPS = [
  { key: "supported", label: "supported" },
  { key: "inconclusive", label: "inconclusive" },
  { key: "not_supported", label: "not supported" },
  { key: "retracted", label: "retracted" },
];

/**
 * The ledger as a strip of cells in the default order, one per hypothesis, each with its kind
 * ("survivor" for a supported row that survives Benjamini-Hochberg, else its status), and the
 * groups with their counts and the index of their first cell. Rows of a status outside the four
 * known ones form their own groups at the end.
 *
 * @returns {{cells: {id: string, kind: string, status: string}[], groups: {key: string, label: string,
 *   count: number, survivors: number, start: number}[]}}
 */
export const ledgerStrip = (rows) => {
  const ordered = asArray(rows)
    .map((row, index) => ({ row, index, value: ledgerOrderValue(row) }))
    .sort((a, b) => a.value - b.value || a.index - b.index)
    .map(({ row }) => row);
  const known = new Set(STRIP_GROUPS.map((group) => group.key));
  const extra = [...new Set(ordered.map((row) => row.status))].filter((status) => !known.has(status));
  const groups = [...STRIP_GROUPS, ...extra.map((key) => ({ key, label: String(key ?? "unknown").replace(/_/g, " ") }))];
  const cells = groups.flatMap((group) => ordered
    .filter((row) => row.status === group.key)
    .map((row) => ({
      id: String(row.id ?? row.hypothesis),
      status: group.key,
      kind: group.key === "supported" && row.survives_bh === true ? "survivor" : group.key,
    })));
  let start = 0;
  const counted = groups.map((group) => {
    const members = cells.filter((cell) => cell.status === group.key);
    const out = { ...group, count: members.length, survivors: members.filter((cell) => cell.kind === "survivor").length, start };
    start += members.length;
    return out;
  }).filter((group) => group.count > 0);
  return { cells, groups: counted };
};

/** The strip's accessible summary, from its groups. */
export const ledgerStripLabel = (groups) => {
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  const parts = groups.map((group) => `${group.count} ${group.label}${group.survivors
    ? ` (${group.survivors} of them survive Benjamini-Hochberg)` : ""}`);
  return `${plural(total, "hypothesis", "hypotheses")} by status: ${parts.join("; ")}`;
};

// --------------------------------------------------------------------------------- model card

/** The HAR-IV equation with the target horizon and the OVX lag of the payload. */
export const harEquation = (terms, horizon, lag) => {
  const names = asArray(terms).filter((term) => term && term !== "const");
  const regressors = (names.length ? names : ["log RV5", "log RV22", "log RV66", "log OVX"])
    .map((term, index) => `b${index + 1} ${/OVX$/.test(term) && lag != null ? `${term}(t−${lag})` : term}`);
  const target = horizon != null ? `log RV(t+1..t+${horizon})` : "log RV(next week)";
  return `${target} = b0 + ${regressors.join(" + ")} + e`;
};

/** The term names of the HAR-IV regression, from the lab's coefficient table (null without it). */
const coefficientTerms = (lab) => {
  const terms = asArray(lab?.har_iv_coefficients?.terms).map((row) => asText(row?.term)).filter(Boolean);
  return terms.length ? terms : null;
};

const DEFAULT_TERMS = ["const", "log RV5", "log RV22", "log RV66", "log OVX"];
// The live payload's input for each regressor: realized vols as decimals (the payload carries
// percent), OVX in index points, as the model is fitted (backend/vol_forecast.py _build_matrix).
const INPUT_KEYS = { "log RV5": "rv5_pct", "log RV22": "rv22_pct", "log RV66": "rv66_pct", "log OVX": "ovx" };

/**
 * The live fit (data.json live.coefficients) with each term's input for this call, and the median
 * those reproduce, exp(b0 + Σ b·ln x), next to the published median. Null without coefficients.
 */
export const liveFit = (live, lab = null) => {
  const coefficients = asArray(live?.coefficients).map(num);
  if (!coefficients.length || coefficients.some((value) => value == null)) return null;
  const allTerms = coefficientTerms(lab) ?? DEFAULT_TERMS;
  // A call made without an OVX print falls back to plain HAR, one coefficient short (the OVX term).
  const terms = coefficients.length === allTerms.length - 1 && /OVX$/.test(allTerms.at(-1)) ? allTerms.slice(0, -1) : allTerms;
  if (terms.length !== coefficients.length) return null;
  const inputs = asObject(live?.inputs) ?? {};
  const rows = terms.map((term, index) => {
    const key = INPUT_KEYS[term];
    const raw = key ? num(inputs[key]) : null;
    const input = raw == null ? null : key.endsWith("_pct") ? raw / 100 : raw;
    return { term, coef: coefficients[index], input, inputUnit: key ? (key.endsWith("_pct") ? "decimal" : "points") : null };
  });
  const complete = rows.every((row) => row.term === "const" || (row.input != null && row.input > 0));
  const median = complete
    ? Math.exp(rows.reduce((sum, row) => sum + (row.term === "const" ? row.coef : row.coef * Math.log(row.input)), 0)) * 100
    : null;
  return { rows, median, published: num(live?.forecast_median_pct), asOf: isoDate(live?.as_of) };
};

const dmClause = (stat, p) => {
  if (stat == null) return null;
  return `DM ${fmtSigned(stat, 2)}${p != null ? `, ${fmtP(p)}` : ""}`;
};

/**
 * The model card's evaluation lines, each from the benchmark lab's hash-pinned sample (every
 * out-of-sample day) and linked to the panel that shows it. DM statistics follow the page's
 * convention: positive when the named model has the lower loss than the model of record.
 *
 * @returns {{key: string, label: string, text: string, href: string}[]}
 */
export const modelCardEvaluation = (lab) => {
  const rows = leaderboardRows(lab);
  const { champion } = getRoles(lab, null);
  const record = rows.find((row) => row.key === champion) ?? rows.find((row) => row.role === "record");
  if (!record) return [];
  const lines = [];
  const versus = (key, name) => {
    const other = rows.find((row) => row.key === key);
    if (!other || record.qlike == null || other.qlike == null) return;
    const lower = other.dmStat != null && other.dmP != null && other.dmP < ALPHA;
    const verdict = lower ? (other.dmStat < 0 ? "lower loss than" : "higher loss than") : "not distinguishable from";
    lines.push({
      key,
      label: `QLIKE vs ${name}`,
      text: `${fmtNum(record.qlike, 3)} vs ${fmtNum(other.qlike, 3)}; ${dmClause(other.dmStat, other.dmP)}: ${verdict} ${name}`,
      href: "#modl",
    });
  };
  versus("persistence", "persistence");
  versus("ovx_only", "the OVX-only regression");
  const summary = mcsSummary(lab);
  const level = mcsLevel(lab);
  if (summary?.champion && summary.survivors != null) {
    const { inMcs, mcsP, gridsIn } = summary.champion;
    lines.push({
      key: "mcs",
      label: "Model Confidence Set",
      text: `${inMcs ? "inside" : "outside"} the ${level != null ? `${level}% ` : ""}set${mcsP != null ? ` (MCS ${fmtP(mcsP)})` : ""}`
        + `${gridsIn != null && summary.gridCount ? `; inside on ${gridsIn} of ${summary.gridCount} sampling grids` : ""}`,
      href: "#modl",
    });
  }
  const conformal = asObject(lab?.conformal?.[champion]);
  if (num(conformal?.coverage_pct) != null && num(conformal?.n_days) != null) {
    const target = num(conformal.target_coverage_pct);
    lines.push({
      key: "coverage",
      label: "Interval coverage",
      text: `${fmtNum(conformal.coverage_pct, 1)}% of ${num(conformal.n_days).toLocaleString("en-US")} days inside the `
        + `${target != null ? `${fmtNum(target, 0)}% ` : ""}band`,
      href: "#calb",
    });
  }
  if (record.mzSlope != null) {
    lines.push({
      key: "mz",
      label: "Mincer-Zarnowitz",
      text: `slope ${fmtNum(record.mzSlope, 2)}${record.mzSe != null ? ` (SE ${fmtNum(record.mzSe, 2)})` : ""}`
        + `${record.mzP != null ? `; unbiasedness ${record.mzP < 0.001 ? fmtP(record.mzP) : `p${NBSP}=${NBSP}${fmtPValue(record.mzP, { digits: 3 })}`}`
          + `${record.mzP >= ALPHA ? ", not rejected" : ", rejected"}` : ""}`,
      href: "#calb",
    });
  }
  return lines;
};

/**
 * The model card's facts from the payloads. Each value is null when its fields are missing (the
 * card then leaves the row out).
 */
export const modelCard = (data, research) => {
  const lab = getLab(research);
  const { live, validation, liveRecord } = getVol(data);
  const config = asObject(lab?.config) ?? {};
  const horizon = num(config.horizon_sessions);
  const lag = num(config.ovx_lag_sessions) ?? num(validation?.ovx_lag_sessions);
  const step = num(config.step);
  const minRows = num(config.min_train_rows);
  const purge = num(config.purge_rows);
  const terms = coefficientTerms(lab) ?? DEFAULT_TERMS;
  const record = leaderboardRows(lab).find((row) => row.key === getRoles(lab, live).champion);
  const floor = num(lab?.noise_floor?.har_direction_acc_pct_mean);
  const smearing = num(live?.smearing_factor);
  const median = num(live?.forecast_median_pct);
  const sample = asObject(lab?.sample);
  return {
    name: shortModelName(asText(live?.model) ?? asText(validation?.model)) ?? "HAR-IV",
    version: asText(live?.model_version),
    horizon,
    lag,
    step,
    minRows,
    purge,
    equation: harEquation(terms.slice(1), horizon, lag),
    direction: record?.directionAcc != null && floor != null ? { accuracy: record.directionAcc, floor } : null,
    mean: smearing != null && median != null ? { median, smearing, mean: median * Math.sqrt(smearing) } : null,
    ovxSource: asText(live?.inputs?.ovx_source),
    challenger: asText(live?.challenger?.label) ?? asText(liveRecord?.promotion?.challenger_key),
    evaluation: modelCardEvaluation(lab),
    sampleDays: num(sample?.n_days),
  };
};

// ------------------------------------------------------------------------------- elasticity path

/**
 * The HAR-IV walk-forward coefficients by refit date (research.json vol_lab.series, weekly): the
 * OVX coefficient b4, which in a log-log model is the forecast's elasticity to OVX, and the summed
 * realized-vol coefficients b1 + b2 + b3. Null without at least two dated points.
 */
export const elasticityPath = (lab) => {
  const series = asObject(lab?.series);
  if (!series) return null;
  const ovx = asArray(series.ovx_elasticity);
  const rv = asArray(series.rv_elasticity);
  const points = asArray(series.date)
    .map((date, index) => ({ date: isoDate(date), ovx: num(ovx[index]), rv: num(rv[index]) }))
    .filter((point) => point.date && point.ovx != null);
  if (points.length < 2) return null;
  const stats = (key) => {
    const values = points.filter((point) => point[key] != null);
    if (!values.length) return null;
    const min = values.reduce((low, point) => (point[key] < low[key] ? point : low));
    const max = values.reduce((high, point) => (point[key] > high[key] ? point : high));
    return { min: min[key], minDate: min.date, max: max[key], maxDate: max.date, last: values.at(-1)[key] };
  };
  return { points, first: points[0].date, last: points.at(-1).date, ovx: stats("ovx"), rv: stats("rv") };
};

/** METH h3: the range of the OVX weight across the walk-forward refits. */
export const elasticityAssertion = (path) => {
  if (!path?.ovx) return null;
  return `The weight on OVX ranged ${fmtNum(path.ovx.min, 2)}–${fmtNum(path.ovx.max, 2)} across walk-forward refits since ${path.first.slice(0, 4)}`;
};

// --------------------------------------------------------------------------------- data vintage

/**
 * Every as-of date the page shows, one row per artifact: the date its numbers run to (`asOf`, a
 * calendar date), when it was produced (`refreshed`, a timestamp, when the payload says), and
 * whether its inputs are pinned by the benchmark lab's SHA-256 manifest. `section` is the panel
 * that shows it. Rows whose payload is missing are left out.
 */
export const vintageRows = (data, research) => {
  const lab = getLab(research);
  const { live, validation, economic, liveRecord } = getVol(data);
  const generated = asObject(lab?.generated_from);
  const pinned = Object.keys(asObject(generated?.files) ?? {}).length > 0;
  const recent = asArray(liveRecord?.recent).filter((row) => asObject(row));
  const recordAsOf = latest(recent.map((row) => isoDate(row.as_of)));
  const recordIssued = recent.filter((row) => isoDate(row.as_of) === recordAsOf).map((row) => asText(row.issued_at)).filter(Boolean).sort().at(-1) ?? null;
  const varDates = varSeries(lab).map((row) => isoDate(row.date));
  const windows = ["five_year", "ten_year"].map((key) => postmortemWindow(research, key)).filter(Boolean);
  const postmortemLast = latest(windows.flatMap((window) => [window.purged?.last, window.leaked?.last]));
  const ledgerDates = asArray(research?.ledger?.rows).map((row) => isoDate(row?.date));
  const prices = asArray(data?.unified_data?.actual?.timestamps).filter((value) => typeof value === "string");
  const rows = [
    { key: "snapshot", artifact: "Data snapshot (data.json)", section: null, asOf: null, refreshed: asText(data?.frozen_at), pinned: false },
    { key: "live", artifact: "Live forecast", section: "fcst", asOf: isoDate(live?.as_of), refreshed: asText(live?.issued_at), pinned: false },
    { key: "validation", artifact: "Production validation", section: "fcst", asOf: isoDate(validation?.sample_end), refreshed: null, pinned: false },
    { key: "record", artifact: "Live record, latest call", section: "live", asOf: recordAsOf, refreshed: recordIssued, pinned: false },
    { key: "lab", artifact: "Benchmark lab", section: "modl", asOf: isoDate(generated?.sessions_through), refreshed: asText(generated?.retrieved_at), pinned },
    { key: "cone", artifact: "Volatility cone", section: "cone", asOf: isoDate(lab?.vol_cone?.as_of), refreshed: null, pinned },
    { key: "var", artifact: "VaR backtest series", section: "risk", asOf: latest(varDates), refreshed: null, pinned },
    { key: "economic", artifact: "Economic tests (VRP, sizing)", section: "vrp", asOf: isoDate(economic?.returns?.sample_end), refreshed: null, pinned: false },
    { key: "postmortem", artifact: "Leak post-mortem", section: "leak", asOf: postmortemLast, refreshed: null, pinned: false },
    { key: "ledger", artifact: "Research ledger, latest row", section: "ledg", asOf: latest(ledgerDates), refreshed: null, pinned: false },
    { key: "prices", artifact: "Price history", section: "mkt", asOf: isoDate(prices.at(-1)), refreshed: null, pinned: false },
  ];
  return rows.filter((row) => row.asOf || row.refreshed);
};

// ---------------------------------------------------------------------------------- corrections

/**
 * The model of record's own timing correction, from the leaderboard's timing-control row: v1 read
 * the test day's OVX close, published after the decision; v2 lags OVX. Null without both rows.
 */
export const timingCorrection = (lab) => {
  const rows = leaderboardRows(lab);
  const control = rows.find((row) => row.timingControl);
  const { champion } = getRoles(lab, null);
  const record = rows.find((row) => row.key === champion);
  if (!control || control.qlike == null || record?.qlike == null) return null;
  const lag = num(lab?.config?.ovx_lag_sessions);
  return { v1: control.qlike, v2: record.qlike, dm: control.dmStat, p: control.dmP, lag };
};

/** "v1 read the test day's OVX close: QLIKE 0.358 vs 0.370 for v2 (DM +3.99); v2 lags OVX 1 session". */
export const timingCorrectionText = (correction) => {
  if (!correction) return null;
  const { v1, v2, dm, lag } = correction;
  return `v1 read the test day's OVX close: QLIKE ${fmtNum(v1, 3)} vs ${fmtNum(v2, 3)} for v2`
    + `${dm != null ? ` (DM ${fmtSigned(dm, 2)})` : ""}${lag != null ? `; v2 lags OVX ${plural(lag, "session")}` : ""}`;
};

/** The ledger's retracted rows, newest first (each links to its row). */
export const retractedRows = (ledger) => asArray(ledger?.rows)
  .filter((row) => row.status === "retracted")
  .slice()
  .sort((a, b) => String(b.date ?? "").localeCompare(String(a.date ?? "")));

// ------------------------------------------------------------------------------------ citation

const unquote = (value) => {
  const text = String(value ?? "").trim();
  const quoted = /^"(.*)"$/.exec(text) ?? /^'(.*)'$/.exec(text);
  return quoted ? quoted[1].replace(/\\"/g, "\"") : text;
};

/**
 * The fields of CITATION.cff the page cites with: a small line parser for the top-level scalars
 * (title, version, url, license, repository-code, date-released) and the authors list. Block scalars
 * (the abstract) and other lists are skipped. Missing fields are null.
 *
 * @param {string} text
 * @returns {{title: string|null, version: string|null, url: string|null, license: string|null,
 *   repository: string|null, released: string|null, authors: {family: string, given: string|null}[]}}
 */
export const parseCitation = (text) => {
  const fields = {};
  const authors = [];
  let block = null;   // "authors", or "skip" for a block scalar or another list
  for (const raw of String(text ?? "").split(/\r?\n/)) {
    if (!raw.trim() || /^\s*#/.test(raw)) continue;
    const top = /^([A-Za-z][\w-]*):\s*(.*)$/.exec(raw);
    if (top) {
      const [, key, value] = top;
      block = null;
      if (key === "authors" && !value) block = "authors";
      else if (!value || /^[>|]/.test(value)) block = "skip";
      else fields[key] = unquote(value);
      continue;
    }
    if (block !== "authors") continue;
    const item = /^\s*(-\s+)?([\w-]+):\s*(.*)$/.exec(raw);
    if (!item) continue;
    const [, dash, key, value] = item;
    if (dash || !authors.length) authors.push({});
    authors.at(-1)[key] = unquote(value);
  }
  return {
    title: fields.title ?? null,
    version: fields.version ?? null,
    url: fields.url ?? null,
    license: fields.license ?? null,
    repository: fields["repository-code"] ?? null,
    released: isoDate(fields["date-released"]),
    authors: authors
      .map((author) => ({ family: author["family-names"] ?? author.name ?? null, given: author["given-names"] ?? null }))
      .filter((author) => author.family),
  };
};

const initials = (given) => (given ? given.split(/\s+/).map((name) => `${name.charAt(0)}.`).join(" ") : "");

/**
 * A plain citation: "Bawa, N. (2026). Title (version 2.0.0; data snapshot 2026-10-26; build abc1234).
 * https://…". The year is the snapshot's (the page cited is that snapshot), else the release year.
 */
export const citationText = (cff, { snapshot = null, build = null } = {}) => {
  if (!cff?.title) return null;
  const names = cff.authors.map((author) => `${author.family}${author.given ? `, ${initials(author.given)}` : ""}`);
  const who = names.length > 1 ? `${names.slice(0, -1).join(", ")} & ${names.at(-1)}` : names[0] ?? null;
  const year = (snapshot ?? cff.released)?.slice(0, 4) ?? null;
  const details = [cff.version ? `version ${cff.version}` : null, snapshot ? `data snapshot ${snapshot}` : null, build ? `build ${build}` : null].filter(Boolean);
  return [
    who ? `${who}${year ? ` (${year})` : ""}.` : null,
    `${cff.title}${details.length ? ` (${details.join("; ")})` : ""}.`,
    cff.url ?? cff.repository,
  ].filter(Boolean).join(" ");
};

/** A biblatex @software entry with the same fields (the snapshot and build in its note). */
export const citationBibtex = (cff, { snapshot = null, build = null } = {}) => {
  if (!cff?.title) return null;
  const year = (snapshot ?? cff.released)?.slice(0, 4) ?? null;
  const first = cff.authors[0]?.family?.toLowerCase().replace(/[^a-z]/g, "") || "anon";
  const word = cff.title.toLowerCase().match(/[a-z]+/)?.[0] ?? "page";
  const note = [snapshot ? `Data snapshot ${snapshot}` : null, build ? `build ${build}` : null].filter(Boolean).join(", ");
  const entries = [
    ["author", cff.authors.map((author) => `${author.family}${author.given ? `, ${author.given}` : ""}`).join(" and ")],
    ["title", `{${cff.title}}`],
    ["version", cff.version],
    ["year", year],
    ["url", cff.url ?? cff.repository],
    ["note", note || null],
  ].filter(([, value]) => value);
  return `@software{${first}${year ?? ""}${word},\n${entries.map(([key, value]) => `  ${key} = {${value}}`).join(",\n")}\n}`;
};

/** The METH appendix anchors, linked from the footer and the model card. */
export const METH_APPENDIX = Object.freeze({ cite: "meth-cite", corrections: "meth-corrections", reuse: "meth-reuse", docs: "meth-docs" });
