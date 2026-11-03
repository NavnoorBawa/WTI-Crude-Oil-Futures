// Pure helpers for the reference sections (LEDG, METH, the footer): the research ledger's order,
// search and row links; the model card's specification and evaluation lines; the data-vintage
// rows; the corrections list; and the citation built from CITATION.cff. Every value is read from
// the payloads (data.json, research.json) or from CITATION.cff; nothing here states a statistic.
import { ALPHA, shortModelName } from "./assertions";
import { NBSP, fmtNum, fmtP, fmtPValue, fmtSigned, num } from "./format";
import { asArray, asObject, asText, getLab, getRoles, getVol, leaderboardRows, mcsLevel, mcsSummary, postmortemWindow, varSeries } from "./research";
import { callArithmetic, forecastDecomposition, forecastOfRecord, liveOfRecord, reproduces } from "./verdict";
import { MONO_CHAR_PX } from "../theme/metrics";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;
const isoDate = (value) => (typeof value === "string" && ISO_DATE.test(value) ? value.slice(0, 10) : null);
const latest = (values) => values.filter(Boolean).sort().at(-1) ?? null;
const plural = (count, word, many = `${word}s`) => `${count} ${count === 1 ? word : many}`;

// ------------------------------------------------------------------------------------- ledger

/** Rows shown before "Show all": enough to see every supported claim, short of a scroll trap. */
export const LEDGER_PAGE = 20;

// The default order reads best evidence first: supported claims that survive Benjamini-Hochberg,
// the other supported claims, inconclusive, not supported, the retracted record, and registered rows
// (no result yet) last.
const STATUS_RANK = { supported: 1, inconclusive: 2, not_supported: 3, retracted: 4, registered: 5 };
const UNKNOWN_RANK = 3;

/**
 * 0 supported and surviving BH, 1 supported, 2 inconclusive, 3 not supported (or unknown), 4 retracted, 5 registered
 * (no result yet).
 */
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
 * outside the adjustment), "n/a" for a retracted row, "pending" for a registered row whose result does
 * not exist yet ("withdrawn" if its registration was withdrawn), else yes / no; null when the row has none.
 */
export const ledgerFlagText = (row, key) => {
  if (row?.selectedAfterSearch) return "excluded";
  if (row?.status === "retracted") return "n/a";
  if (row?.status === "registered") return row.registration?.withdrawn ? "withdrawn" : "pending";
  return row?.[key] === true ? "yes" : row?.[key] === false ? "no" : null;
};

/**
 * An online decision (procedure "lordpp" or "saffron") as the ledger's CSV prints it: "excluded" outside the
 * online stream (a row selected after a search), "n/a" for a retracted row (tested at p = 1), "pending" while
 * the row, or an earlier one, waits for its result, else yes / no; null for a ledger without an online view.
 */
export const ledgerOnlineText = (row, procedure) => {
  if (row?.selectedAfterSearch) return "excluded";
  if (row?.status === "retracted") return "n/a";
  const rejected = (procedure === "saffron" ? row?.onlineSaffron : row?.onlineLord)?.rejected;
  if (rejected === true) return "yes";
  if (rejected === false) return "no";
  return row?.onlinePosition != null ? "pending" : null;
};

/**
 * LEDG's note on the online view, from the ledger's summary.online: both procedures' counts over the decided rows
 * of the stream (a pending registered row and the rows after it have no decision yet), the independence they
 * assume, the legacy rows' order sensitivity and that no status reads them. Null for a ledger without an online view.
 */
export const ledgerOnlineNote = (ledger) => {
  const online = asObject(ledger?.online);
  const lord = asArray(online?.lordpp?.rejected);
  const saffron = asArray(online?.saffron?.rejected);
  const stream = num(online?.rows_in_stream);
  if (!online || stream == null) return null;
  const alpha = num(online.alpha) ?? ALPHA;
  const decided = Math.min(num(online.rows_decided) ?? stream, stream);
  const undecided = stream - decided;
  const counted = undecided
    ? `of the ${decided} decided rows in the stream, and ${undecided} more ${undecided === 1 ? "waits" : "wait"} for a registered result`
    : `of the ${stream} rows in the stream`;
  let text = `Online control: in the order rows entered the ledger, at a ${fmtNum(alpha * 100, 0)}% false discovery rate, `
    + `LORD++ rejects ${lord.length} and SAFFRON ${saffron.length} ${counted} (rows selected after a search stay out; `
    + "retracted rows and rows whose test could not be computed are tested at p = 1). Both assume independent p-values, "
    + "which rows on shared data do not have, and the page's other verdicts cite the batch view (q and t > 3) instead.";
  const sens = asObject(online.legacy_order_sensitivity);
  if (sens && num(sens.legacy_rows)) {
    text += ` For the ${sens.legacy_rows} rows that entered before registration began, the decisions are a retrospective `
      + `reading: with their order drawn at random within each entry batch, LORD++ rejects ${sens.lordpp?.min} to `
      + `${sens.lordpp?.max} of them and SAFFRON ${sens.saffron?.min} to ${sens.saffron?.max}.`;
  }
  // The first undecided row waits for its result; rows follow it only when more than it are undecided.
  const waiting = asText(online.waiting_on);
  if (waiting) {
    text += undecided > 1
      ? ` Rows after ${waiting}, which waits for its result, have no online decision yet.`
      : ` ${waiting} waits for its result and has no online decision yet.`;
  }
  return `${text} A row's online decision never changes its status; the CSV holds each row's levels and decisions.`;
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
  { key: "registered", label: "registered" },
];

/**
 * The ledger as a strip of cells in the default order, one per hypothesis, each with its kind
 * ("survivor" for a supported row that survives Benjamini-Hochberg, else its status), and the
 * groups with their counts and the index of their first cell. Rows of a status outside the five
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

// The strip's geometry, here rather than in its lazy chunk (charts/referenceCharts.jsx draws it) so LEDG can
// reserve the strip's height before the chunk arrives. Labels sit under the strip on as many lines as they need.
export const STRIP = {
  cellHeight: 16,
  labelLine: 14,
  labelTop: 6,
  // A label may sit this far right of its group's first cell before it moves to a lower line.
  maxShift: 40,
  labelGap: 12,
  // Each label starts with a key in its group's colour, so a label that had to move to a lower line
  // still says which cells it counts.
  key: 8,
  keyGap: 4,
};

/** The visible words of a strip group's label (the count is set apart from the status). */
export const stripGroupWords = (group) => ({
  count: String(group.count),
  label: ` ${group.label}`,
  survivors: group.survivors ? `, ${group.survivors} survive BH` : "",
});

/**
 * Places each group's label under the strip, as near its first cell as it fits: on the first line
 * where it neither overlaps the label before it nor sits more than STRIP.maxShift px right of its
 * group, and never past the right edge. Pure.
 *
 * @param {{start: number, width: number}[]} labels  x of the group's first cell and the label's width
 * @param {number} width
 * @returns {{x: number, line: number}[]}
 */
export const placeStripLabels = (labels, width) => {
  const ends = [];
  return labels.map((label) => {
    for (let index = 0; index < labels.length; index += 1) {
      const end = ends[index] ?? -Infinity;
      const x = Math.max(0, Math.min(Math.max(label.start, end + STRIP.labelGap), width - label.width));
      if (x >= end + STRIP.labelGap - 0.5 && x - label.start <= STRIP.maxShift) {
        ends[index] = x + label.width;
        return { x, line: index };
      }
    }
    const line = ends.length;
    ends[line] = Math.min(label.start, width - label.width) + label.width;
    return { x: Math.max(0, Math.min(label.start, width - label.width)), line };
  });
};

/**
 * The strip drawn `width` px wide (see ledgerStrip): the gap between cells, the cells' width and x, each
 * group's label (its words, the x of its first cell and its width) and where it is placed, the number of
 * label lines and the strip's height. Null when there is nothing to draw. Pure.
 */
export const ledgerStripLayout = (strip, width) => {
  const cells = asArray(strip?.cells);
  const groups = asArray(strip?.groups);
  const n = cells.length;
  if (!n || !(width > 0)) return null;
  const gap = width / n >= 8 ? 2 : 1;
  const groupGap = gap * 2;
  const cellWidth = Math.max(1, (width - (n - 1) * gap - (groups.length - 1) * groupGap) / n);
  const groupIndex = new Map(groups.map((group, index) => [group.key, index]));
  const cellX = (index, status) => index * (cellWidth + gap) + (groupIndex.get(status) ?? 0) * groupGap;
  const labels = groups.map((group) => {
    const words = stripGroupWords(group);
    const text = `${words.count}${words.label}${words.survivors}`;
    return { group, words, start: cellX(group.start, group.key), width: STRIP.key + STRIP.keyGap + text.length * MONO_CHAR_PX };
  });
  const placed = placeStripLabels(labels, width);
  const lines = Math.max(1, ...placed.map((item) => item.line + 1));
  return { gap, cellWidth, cellX, labels, placed, lines, height: STRIP.cellHeight + STRIP.labelTop + lines * STRIP.labelLine };
};

/** The strip's height when drawn `width` px wide (one label line when there is nothing to draw). */
export const ledgerStripHeight = (strip, width) => ledgerStripLayout(strip, width)?.height
  ?? STRIP.cellHeight + STRIP.labelTop + STRIP.labelLine;

// The strip's width at the narrowest viewport of each band LazyMount reserves for, as measured in Chromium (the strip
// is the viewport less 32 px up to 640 px, less 82 to 98 px above, and at most 1342 px wide), less a classic scrollbar
// where a desktop has one: 1280 px for `wide` (the common desktops; from 900 to 1279 px the strip can still need one
// more label line), 641 px for `mid` and 360 px phones for `narrow`.
export const LEDGER_STRIP_RESERVE_WIDTH = { wide: 1160, mid: 540, narrow: 328 };

/**
 * The heights LEDG reserves for the strip before its chunk arrives, from its own label layout at each band's
 * LEDGER_STRIP_RESERVE_WIDTH. The mounted strip is never drawn shorter (reference-sections.css), and wider strips in a
 * band need no more lines, so mounting it moves nothing below it.
 *
 * @returns {{wide: number, mid: number, narrow: number}}
 */
export const ledgerStripReserve = (strip) => Object.fromEntries(Object.entries(LEDGER_STRIP_RESERVE_WIDTH)
  .map(([band, width]) => [band, ledgerStripHeight(strip, width)]));

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
 * those reproduce, exp(b0 + Σ b·ln x), next to the published median. `fitAsOf` is the close the
 * coefficients were fitted for (live.fit_as_of, set by liveOfRecord(), else the call's own as_of);
 * `reproduces` says whether the median prints as the published one. Null without coefficients.
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
  const published = num(live?.forecast_median_pct);
  const asOf = isoDate(live?.as_of);
  return {
    rows, median, published, asOf, fitAsOf: isoDate(live?.fit_as_of) ?? asOf,
    reproduces: median != null && published != null && fmtNum(median, 1) === fmtNum(published, 1),
  };
};

/**
 * METH's live fit for the call of record (liveOfRecord()). For a snapshot computed from an older
 * close than the newest recorded call, that call's inputs under the snapshot's coefficients when
 * they reproduce its median; when they do not, the snapshot's own computation, with `superseded`
 * set to the recorded call's close so it is never labelled as the call.
 */
export const recordFit = (data, lab = null) => {
  const { live, liveRecord } = getVol(data);
  const block = liveOfRecord(live, liveRecord);
  const fit = liveFit(block, lab);
  if (block === live || fit?.reproduces) return fit;
  const own = liveFit(live, lab);
  return own ? { ...own, superseded: isoDate(block?.as_of) } : null;
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
  // Median × √smearing for the call of record, as the hero's arithmetic prints it (callArithmetic()):
  // the fitted median at two decimals and the smearing factor at four, so the chain is exact and
  // lands on the call's one-decimal forecast. The recorded one-decimal median is never the factor: it
  // can miss (47.5 × √1.4128 = 56.46, which prints 56.5, for a call of 56.4). A chain that does not
  // land on the forecast at one decimal is not printed. For a snapshot from an older close whose fit
  // does not reproduce the recorded call, the snapshot's own chain, with `superseded` naming the call
  // it is not.
  const chain = (math, forecast, asOf) => (math && forecast != null && fmtNum(math.printedMean, 1) === fmtNum(forecast, 1)
    ? { median: math.shownMedian, smearing: math.smearing, mean: math.printedMean, forecast, asOf }
    : null);
  const call = forecastOfRecord(live, liveRecord);
  let mean = call ? chain(callArithmetic(call, live).math, call.forecast, isoDate(call.asOf)) : null;
  if (!mean && call?.olderSnapshot) {
    const own = forecastDecomposition(live?.coefficients, live?.inputs, live?.smearing_factor);
    const forecast = num(live?.forecast_next_week_vol_annualized_pct);
    const snapshot = reproduces(own, num(live?.forecast_median_pct), forecast) ? chain(own, forecast, isoDate(live?.as_of)) : null;
    mean = snapshot ? { ...snapshot, superseded: isoDate(call.asOf) } : null;
  }
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
    mean,
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
  // The ledger's latest entry (entered_on) where the ledger records it, else its latest artifact date.
  const ledgerEntered = latest(asArray(research?.ledger?.rows).map((row) => isoDate(row?.entered_on)));
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
    {
      key: "ledger", artifact: ledgerEntered ? "Research ledger, latest entry" : "Research ledger, latest row", section: "ledg",
      asOf: ledgerEntered ?? latest(ledgerDates), refreshed: null, pinned: false,
    },
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
