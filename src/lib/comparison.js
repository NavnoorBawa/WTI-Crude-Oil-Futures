// Model comparison (MODL and STAB), pure: the Model Confidence Set's membership under every setting
// the payload carries, the scale of the leaderboard's QLIKE strip, the yearly QLIKE gaps to the
// model of record, and the record against the challenger on the weekly series. Every function
// tolerates a missing or malformed field and returns null or [] instead of throwing.
import { NBSP, fmtNum, num } from "./format";
import { weekQlike } from "./forecastErrors";
import { asArray, asObject, asText, leaderboardRows, shortLabel, yearsBetter } from "./research";

// ------------------------------------------------------------------------------------- QLIKE

/** One week's QLIKE from vols in percent; the same function FCST uses (src/lib/forecastErrors.js). */
export { weekQlike };

/**
 * MODL's sentence on the proxy's noise floor (research.qlikeFloor): each value with the assumption it rests
 * on, and what it is not. It never names a model's loss, never compares one with a floor and never subtracts
 * one; a part the block lacks is left out. Null without the floor.
 */
export const qlikeFloorNote = (floor, garchLabel = "GARCH(1,1)-t") => {
  if (!floor) return null;
  const f = (value) => fmtNum(value, 3);
  const returns = floor.horizon != null ? `${floor.horizon}${NBSP}daily returns` : "daily returns";
  const values = [
    `${f(floor.normal)} with normal returns of constant variance (exact, and a lower bound while the variance does not react to the week's own shocks)`,
    floor.matched != null
      ? `${f(floor.matched)} with Student-t tails matched to the ${garchLabel} fit's kurtosis${floor.matchedKurtosis != null ? ` (${fmtNum(floor.matchedKurtosis, 1)})` : ""}`
      : null,
    floor.studentT != null
      ? `${f(floor.studentT)} on the Student-t(5) returns of the direction noise-floor simulation${floor.studentTRange
        ? ` (${f(floor.studentTRange[0])}–${f(floor.studentTRange[1])}${floor.sims != null ? ` across ${floor.sims} simulations` : ""})` : ""}`
      : null,
    floor.dynamics != null ? `${f(floor.dynamics)} when the ${garchLabel}'s variance moves within the week` : null,
  ].filter(Boolean);
  const list = values.length > 1 ? `${values.slice(0, -1).join(", ")} and ${values.at(-1)}` : values[0];
  const range = floor.rangeNormal != null && floor.rangeStudentT != null
    ? ` Against the range proxy the same forecast scores ${f(floor.rangeNormal)} and ${f(floor.rangeStudentT)} on simulated bars whose proxy overstates the variance, so those two are not lower bounds.`
    : "";
  return `The proxy's noise floor: scored against realized vol from ${returns}, even a forecast equal to the true variance has an expected QLIKE above zero: ${list}. Daily closes cannot say which applies.${range} It belongs to the proxy, not to any model; no loss, DM t or confidence set on this page subtracts it.`;
};

// ------------------------------------------------------------------------- QLIKE strip (MODL)

/** The strip starts this far below the best model, so its dot never sits on the track's end. */
const STRIP_PAD = 1 / 200;
/** The worst model is drawn off the scale when it is more than this multiple of the second worst. */
const OUTLIER_RATIO = 3 / 2;
/** The scale then ends this multiple of the second worst. */
const CAP_RATIO = 103 / 100;

/**
 * The shared scale of the leaderboard's QLIKE strip: from just under the lowest QLIKE to a cap. No
 * zero baseline, so the differences between the leading models are visible. When the worst model
 * is far off the rest (above 3/2 x the second worst), the cap sits just past the second worst and
 * the outlier is marked off-scale instead of setting the scale.
 *
 * @param {Array<number|null>} values
 * @returns {{lo: number, cap: number, offScale: (v: number) => boolean, position: (v: number) => number}|null}
 *   position is 0..1 along the track
 */
export const qlikeScale = (values) => {
  const sorted = asArray(values).map(num).filter((value) => value != null).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const max = sorted[sorted.length - 1];
  const second = sorted.length > 1 ? sorted[sorted.length - 2] : max;
  const cap = sorted.length > 1 && max > OUTLIER_RATIO * second ? CAP_RATIO * second : max;
  const lo = sorted[0] - STRIP_PAD;
  const span = cap - lo;
  return {
    lo,
    cap,
    offScale: (value) => value > cap,
    position: (value) => (span > 0 ? Math.min(1, Math.max(0, (value - lo) / span)) : 1 / 2),
  };
};

// ------------------------------------------------------------------- MCS robustness (MODL)

/** "block_10" -> "Block 10"; "2008-2016" -> "2008–16"; anything else in words. */
export const robustnessLabel = (key) => {
  const block = /^block_(\d+)$/.exec(key);
  if (block) return `Block ${block[1]}`;
  const years = /^(\d{4})-(\d{4})$/.exec(key);
  if (years) return `${years[1]}–${years[2].slice(2)}`;
  const text = String(key).replace(/_/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
};

const robustnessTitle = (key, nDays, block) => {
  const blockSize = /^block_(\d+)$/.exec(key);
  if (blockSize) {
    return `Every out-of-sample day with bootstrap blocks of ${blockSize[1]} days${block != null ? ` instead of ${block}` : ""}`;
  }
  const years = /^(\d{4})-(\d{4})$/.exec(key);
  if (years) return `Out-of-sample days in ${years[1]}–${years[2]} only${nDays != null ? ` (${nDays.toLocaleString("en-US")} days)` : ""}`;
  return robustnessLabel(key);
};

/**
 * @typedef {object} McsColumn
 * @property {string} key
 * @property {string} label            column header
 * @property {string} title            what the setting changes (header tooltip)
 * @property {"set"|"grids"} kind      one confidence set, or one per sampling grid (a k/n count)
 * @property {Set<string>} [included]  kind "set": the models in it
 * @property {Set<string>[]} [grids]   kind "grids": the models in each grid's set
 * @property {number|null} [nDays]
 *
 * @typedef {object} McsRow
 * @property {string} key
 * @property {string} label
 * @property {"record"|"challenger"|null} role
 * @property {Record<string, boolean|{in: number, of: number}>} cells  by column key
 * @property {number} inCount         settings this model is in (each grid counts as one)
 * @property {number} settingCount    settings in all
 * @property {number|null} gridsIn
 * @property {number} gridCount
 * @property {boolean|null} rangeProxy  in the set under the range proxy (null when not reported)
 */

/**
 * Model Confidence Set membership under every setting research.json carries, in this column order:
 * the full sample (mcs_qlike_included), the sampling grids as a k/n count (mcs_qlike_by_grid), each
 * key of mcs_robustness_qlike (a list is the included models; an object has .included and n_days),
 * MSE loss (mcs_mse_included) and the range proxy (mcs_qlike_range_proxy_included). A column the
 * payload leaves out is left out. Rows are the eligible models (no timing control), lowest QLIKE first.
 *
 * @returns {{columns: McsColumn[], rows: McsRow[], record: McsRow|null, settings: number}|null}
 */
export const mcsRobustness = (lab) => {
  const board = asObject(lab?.leaderboard);
  if (!board) return null;
  const block = num(lab?.config?.mcs?.block_daily);
  const weeklyBlock = num(lab?.config?.mcs?.block_weekly);
  const columns = [];
  const addSet = (key, label, title, list, extra = {}) => {
    if (!Array.isArray(list)) return;
    columns.push({ key, label, title, kind: "set", included: new Set(list.filter((item) => asText(item))), ...extra });
  };

  addSet("full", "Full sample",
    `QLIKE on every out-of-sample day${block != null ? `, bootstrap blocks of ${block} days` : ""} (the leaderboard's set)`,
    board.mcs_qlike_included);
  const byGrid = asObject(board.mcs_qlike_by_grid);
  const gridKeys = byGrid ? Object.keys(byGrid).filter((key) => Array.isArray(byGrid[key])).sort((a, b) => Number(a) - Number(b)) : [];
  if (gridKeys.length) {
    columns.push({
      key: "grids",
      label: "Grids",
      title: `Each 5-session sampling grid on its own${weeklyBlock != null ? ` (blocks of ${weeklyBlock} weeks)` : ""}: in the set on k of ${gridKeys.length}`,
      kind: "grids",
      grids: gridKeys.map((key) => new Set(byGrid[key])),
    });
  }
  for (const [key, value] of Object.entries(asObject(board.mcs_robustness_qlike) ?? {})) {
    const list = Array.isArray(value) ? value : asObject(value)?.included;
    const nDays = Array.isArray(value) ? null : num(asObject(value)?.n_days);
    addSet(`robust-${key}`, robustnessLabel(key), robustnessTitle(key, nDays, block), list, { nDays });
  }
  addSet("mse", "MSE loss", "Squared error on variances instead of QLIKE, every out-of-sample day", board.mcs_mse_included);
  addSet("proxy", "Range proxy", "QLIKE scored against realized vol from the daily high-low range instead of close-to-close returns",
    board.mcs_qlike_range_proxy_included);
  if (!columns.length) return null;

  const eligible = leaderboardRows(lab)
    .filter((row) => row.mcsEligible)
    .map((row, index) => ({ row, index }))
    .sort((a, b) => (a.row.qlike ?? Infinity) - (b.row.qlike ?? Infinity) || a.index - b.index)
    .map(({ row }) => row);
  const settings = columns.reduce((total, column) => total + (column.kind === "grids" ? column.grids.length : 1), 0);
  const rows = eligible.map((row) => {
    const cells = {};
    let inCount = 0;
    for (const column of columns) {
      if (column.kind === "grids") {
        const count = column.grids.filter((set) => set.has(row.key)).length;
        cells[column.key] = { in: count, of: column.grids.length };
        inCount += count;
      } else {
        cells[column.key] = column.included.has(row.key);
        inCount += cells[column.key] ? 1 : 0;
      }
    }
    const grids = cells.grids ?? null;
    return {
      key: row.key,
      label: shortLabel(row.label),
      role: row.role,
      cells,
      inCount,
      settingCount: settings,
      gridsIn: grids ? grids.in : null,
      gridCount: grids ? grids.of : 0,
      rangeProxy: typeof cells.proxy === "boolean" ? cells.proxy : null,
    };
  });
  return { columns, rows, record: rows.find((row) => row.role === "record") ?? null, settings };
};

// ---------------------------------------------------------------------------- yearly (STAB)

/**
 * Each comparison model's yearly QLIKE minus the record's (positive: the record was lower that
 * year), with the number of years the record was lower. `years` is yearlyQlike()'s output.
 *
 * @param {{year:number, values: Record<string, number|null>}[]} years
 * @param {string} record   the model of record's key
 * @param {{key:string, label:string, tone:string}[]} models  in display order
 * @returns {{key:string, label:string, tone:string, wins:number, total:number,
 *   gaps: {year:number, value:number, model:number, record:number}[]}[]}
 */
export const yearlyGaps = (years, record, models) => asArray(models)
  .map((model) => {
    const gaps = asArray(years)
      .filter((row) => row.values?.[model.key] != null && row.values?.[record] != null)
      .map((row) => ({ year: row.year, value: row.values[model.key] - row.values[record], model: row.values[model.key], record: row.values[record] }));
    const { wins, total } = yearsBetter(asArray(years), record, model.key);
    return { ...model, wins, total, gaps };
  })
  .filter((model) => model.gaps.length > 0);

/**
 * The half-height of the yearly-gap bars' shared, symmetric scale. Normally the largest gap; when
 * that is more than twice the 90th percentile of all gaps, the 90th percentile, so a few extreme
 * years do not flatten every other bar (those bars are drawn cut at the edge and marked).
 *
 * @param {number[]} values  the gaps
 * @returns {{limit: number, clipped: number}}  clipped: how many bars exceed the limit
 */
export const gapLimit = (values) => {
  const sizes = asArray(values).map(num).filter((value) => value != null).map(Math.abs).sort((a, b) => a - b);
  if (!sizes.length) return { limit: 1, clipped: 0 };
  const max = sizes[sizes.length - 1];
  const q90 = sizes[Math.min(sizes.length - 1, Math.floor((sizes.length - 1) * (9 / 10)))];
  const limit = max > 2 * q90 && q90 > 0 ? q90 : max || 1;
  return { limit, clipped: sizes.filter((size) => size > limit).length };
};

// --------------------------------------------------------------- weekly series (grid 0, STAB)

/**
 * The weekly series (vol_lab.series, grid 0) with both forecasts: [{date, actual, record,
 * challenger}] in date order. The record's forecasts are the series named by its key; without
 * them (or the challenger's) there is nothing to compare.
 */
export const weeklyPairs = (lab, recordKey) => {
  const series = asObject(lab?.series);
  if (!series || !recordKey) return [];
  const dates = asArray(series.date);
  const actual = asArray(series.actual);
  const record = asArray(series[recordKey]);
  const challenger = asArray(series.challenger);
  if (!record.length || !challenger.length) return [];
  return dates
    .map((date, index) => ({ date, actual: num(actual[index]), record: num(record[index]), challenger: num(challenger[index]) }))
    .filter((row) => typeof row.date === "string" && [row.actual, row.record, row.challenger].every((value) => value != null && value > 0));
};

/**
 * Mean weekly QLIKE of the record and the challenger over the last n weeks of the weekly series,
 * for each window that the series is long enough to fill.
 *
 * @returns {{weeks:number, record:number, challenger:number, first:string, last:string}[]}
 */
export const recentWindows = (pairs, windows = [26, 52, 156]) => {
  const rows = asArray(pairs);
  return windows
    .filter((weeks) => Number.isInteger(weeks) && weeks > 0 && rows.length >= weeks)
    .map((weeks) => {
      const tail = rows.slice(-weeks);
      const mean = (key) => tail.reduce((total, row) => total + weekQlike(row.actual, row[key]), 0) / weeks;
      return { weeks, record: mean("record"), challenger: mean("challenger"), first: tail[0].date, last: tail[tail.length - 1].date };
    });
};

/**
 * The rolling mean, over `window` weeks, of weekQlike(challenger) − weekQlike(record): below zero
 * the challenger had the lower loss over the trailing window.
 *
 * @returns {{date:string, value:number}[]}  one point per week from the window's first full week
 */
export const rollingGap = (pairs, window = 52) => {
  const rows = asArray(pairs);
  if (!Number.isInteger(window) || window < 1 || rows.length < window) return [];
  const diffs = rows.map((row) => weekQlike(row.actual, row.challenger) - weekQlike(row.actual, row.record));
  const points = [];
  let sum = 0;
  diffs.forEach((diff, index) => {
    sum += diff;
    if (index >= window) sum -= diffs[index - window];
    if (index >= window - 1) points.push({ date: rows[index].date, value: sum / window });
  });
  return points;
};
