// Pure helpers for CONE (where realized vol sits in its own history) and CALB (the weekly-grid
// Mincer-Zarnowitz slope and coverage by OVX regime). Like research.js they tolerate missing or
// malformed fields and return null / [] instead of throwing.
import { fmtPct, num } from "./format";
import { asArray, asObject } from "./research";

/** The cone's stored percentiles, lowest first, with their rank. */
export const CONE_PERCENTILES = [["p5", 5], ["p25", 25], ["p50", 50], ["p75", 75], ["p95", 95]];

/** The windows data.json measures realized vol over (vol_forecast.live.inputs), by length in sessions. */
export const LIVE_WINDOWS = [[5, "rv5_pct"], [22, "rv22_pct"], [66, "rv66_pct"]];

/**
 * The most a one-decimal and a two-decimal rounding of the same number can differ by (0.05 +
 * 0.005), with room for floating point.
 */
const ROUNDING_GAP = 0.0551;

/**
 * A realized vol as it is printed: {value, digits}. The recorded inputs (inputs.rv5_pct and the
 * like) are stored at two decimals, and a value already rounded to two decimals is never rounded
 * again to one (48.65 is 48.6 or 48.7 depending on digits it no longer has), so it prints at its two
 * decimals. A one-decimal field the backend rounded once from the unrounded value (the live block's
 * current_realized_vol_5d_annualized_pct) prints at one decimal: `oneDecimal` is used when given and
 * consistent with the two-decimal value (both roundings of one number), else the two-decimal value.
 * Null when neither is a number.
 * @returns {{value: number, digits: number}|null}
 */
export const realizedShown = (twoDecimals, oneDecimal = null) => {
  const stored = num(twoDecimals);
  const rounded = num(oneDecimal);
  if (rounded != null && (stored == null || Math.abs(rounded - stored) <= ROUNDING_GAP)) return { value: rounded, digits: 1 };
  return stored != null ? { value: stored, digits: 2 } : null;
};

/** "2026-10-23T…" or "2026-10-23" -> "2026-10-23"; anything else -> null. */
export const isoDay = (value) => (typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : null);

/** Calendar days from `from` to `to` (ISO dates), negative when `to` is earlier; null if either is missing. */
export const daysBetween = (from, to) => {
  const a = isoDay(from);
  const b = isoDay(to);
  if (!a || !b) return null;
  return Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
};

/** research.json vol_lab.vol_cone.windows, normalized and sorted by window length; rows without p5 or p95 are dropped. */
export const coneWindows = (cone) => asArray(asObject(cone)?.windows)
  .map((row) => ({
    window: num(row?.window),
    p5: num(row?.p5),
    p25: num(row?.p25),
    p50: num(row?.p50),
    p75: num(row?.p75),
    p95: num(row?.p95),
    latest: num(row?.latest),
    percentile: num(row?.latest_percentile),
  }))
  .filter((row) => row.window != null && row.p5 != null && row.p95 != null)
  .sort((a, b) => a.window - b.window);

/**
 * The band of a window's history a value falls in, read off the five stored percentiles and never
 * interpolated: "below p5", "p5–p25", "p25–p50", "p50–p75", "p75–p95" or "above p95". A value on a
 * stored percentile belongs to the band below it (p25 itself is "p5–p25"); p5 itself is inside.
 * `low` and `high` are the ranks that bound the band (null past the ends). Null when a percentile
 * is missing or they are out of order.
 *
 * @returns {{label: string, low: number|null, high: number|null}|null}
 */
export const coneBracket = (value, row) => {
  const v = num(value);
  if (v == null || !row) return null;
  const cuts = CONE_PERCENTILES.map(([key, rank]) => ({ key, rank, at: num(row[key]) }));
  if (cuts.some((cut) => cut.at == null) || cuts.some((cut, i) => i > 0 && cut.at < cuts[i - 1].at)) return null;
  if (v < cuts[0].at) return { label: `below ${cuts[0].key}`, low: null, high: cuts[0].rank };
  for (let i = 1; i < cuts.length; i += 1) {
    if (v <= cuts[i].at) return { label: `${cuts[i - 1].key}–${cuts[i].key}`, low: cuts[i - 1].rank, high: cuts[i].rank };
  }
  const top = cuts[cuts.length - 1];
  return { label: `above ${top.key}`, low: top.rank, high: null };
};

/**
 * The latest realized vol from data.json (vol_forecast.live.inputs, as of live.as_of) on the 5-,
 * 22- and 66-session windows, each bracketed against the same window of the lab's cone. Windows
 * missing from either side are left out. `value` is the stored two-decimal input, which places the
 * mark and picks its band; `shown` and `digits` are how it prints (realizedShown()): the 5-session
 * window at one decimal from the live block's own one-decimal field, the others at their stored two.
 *
 * @returns {{asOf: string|null, marks: {window: number, value: number, shown: number, digits: number,
 *   bracket: ReturnType<typeof coneBracket>}[]}}
 */
export const coneNow = (cone, live) => {
  const rows = coneWindows(cone);
  const block = asObject(live);
  const inputs = asObject(block?.inputs);
  const asOf = isoDay(block?.as_of);
  if (!inputs || !asOf) return { asOf, marks: [] };
  const oneDecimal = { 5: block.current_realized_vol_5d_annualized_pct };
  const marks = LIVE_WINDOWS.map(([window, field]) => {
    const row = rows.find((item) => item.window === window);
    const value = num(inputs[field]);
    if (!row || value == null) return null;
    const shown = realizedShown(value, oneDecimal[window] ?? null);
    return { window, value, shown: shown.value, digits: shown.digits, bracket: coneBracket(value, row) };
  }).filter(Boolean);
  return { asOf, marks };
};

/** A cone mark's realized vol as printed: "48.7%", or "37.69%" for a stored two-decimal input. */
export const markPct = (mark) => fmtPct(mark?.shown ?? mark?.value, mark?.digits ?? 1);

/**
 * Everything CONE draws, with the date of each piece. The marks are current when data.json is at
 * least as new as the lab's cone: its 5/22/66-session realized vol then replaces the lab's, and the
 * lab's own latest values fill only the other windows. When data.json is older, every mark is the
 * lab's. OVX follows the same rule. `lagDays` is the cone's age against data.json, in calendar days.
 */
export const coneView = (cone, live) => {
  const block = asObject(cone);
  const windows = coneWindows(block);
  const labDate = isoDay(block?.as_of);
  const now = coneNow(block, live);
  const lagDays = daysBetween(labDate, now.asOf);
  const useLive = now.marks.length > 0 && (labDate == null || (lagDays != null && lagDays >= 0));
  const liveWindows = new Set(useLive ? now.marks.map((mark) => mark.window) : []);
  const marks = [
    ...(useLive ? now.marks.map((mark) => ({ ...mark, date: now.asOf, source: "live" })) : []),
    // The lab's latest values are rounded once, to one decimal, from the unrounded series.
    ...windows.filter((row) => !liveWindows.has(row.window) && row.latest != null).map((row) => ({
      window: row.window, value: row.latest, shown: row.latest, digits: 1, bracket: coneBracket(row.latest, row),
      percentile: row.percentile, date: labDate, source: "lab",
    })),
  ].sort((a, b) => a.window - b.window);
  const liveBlock = asObject(live);
  const inputs = asObject(liveBlock?.inputs);
  const liveOvx = num(liveBlock?.implied_vol_ovx_pct) ?? num(inputs?.ovx);
  const ovx = useLive && liveOvx != null
    ? { value: liveOvx, date: isoDay(inputs?.ovx_session) ?? now.asOf, source: "live" }
    : num(block?.implied_ovx) != null ? { value: num(block.implied_ovx), date: labDate, source: "lab" } : null;
  return {
    windows,
    since: isoDay(block?.since),
    labDate,
    liveDate: now.asOf,
    lagDays,
    current: useLive ? "live" : "lab",
    marks,
    ovx,
  };
};

// ------------------------------------------------------------------------------------------ CALB

/** Pairs of the series' arrays where every named field is a finite number. */
const rowsWith = (series, fields) => {
  const block = asObject(series);
  if (!block) return [];
  const length = Math.max(0, ...fields.map((field) => asArray(block[field]).length));
  const rows = [];
  for (let i = 0; i < length; i += 1) {
    const row = {};
    let ok = true;
    for (const field of fields) {
      const value = num(asArray(block[field])[i]);
      if (value == null) { ok = false; break; }
      row[field] = value;
    }
    if (ok) rows.push(row);
  }
  return rows;
};

/**
 * The weekly-grid Mincer-Zarnowitz slope: OLS of realized variance (actual²) on forecast variance
 * (har_iv²) over research.json vol_lab.series, one forecast per week. Null below 3 weeks or with no
 * spread in the forecasts.
 *
 * @returns {{slope: number, n: number}|null}
 */
export const mzWeekly = (series) => {
  const rows = rowsWith(series, ["actual", "har_iv"]).map((row) => ({ x: row.har_iv ** 2, y: row.actual ** 2 }));
  if (rows.length < 3) return null;
  const mx = rows.reduce((sum, row) => sum + row.x, 0) / rows.length;
  const my = rows.reduce((sum, row) => sum + row.y, 0) / rows.length;
  let sxx = 0;
  let sxy = 0;
  for (const row of rows) {
    sxx += (row.x - mx) ** 2;
    sxy += (row.x - mx) * (row.y - my);
  }
  return sxx > 0 ? { slope: sxy / sxx, n: rows.length } : null;
};

/** The nearest-rank `q` quantile (0 < q <= 1) of ascending `sorted`. */
const nearestRank = (sorted, q) => sorted[Math.max(0, Math.ceil(q * sorted.length) - 1)];

/**
 * Coverage of the lab's weekly 80% band ([lo80, hi80]) by OVX regime: the weeks with OVX, realized
 * vol and a band, split into terciles of OVX at its nearest-rank 1/3 and 2/3 quantiles (a week on a
 * cut goes to the lower tercile). Each tercile has its OVX range, week count and coverage. [] below
 * 3 weeks.
 *
 * @returns {{tercile: number, ovxLow: number, ovxHigh: number, n: number, covered: number, coverage: number}[]}
 */
export const coverageByOvxTercile = (series) => {
  const rows = rowsWith(series, ["ovx", "actual", "lo80", "hi80"]);
  if (rows.length < 3) return [];
  const sorted = rows.map((row) => row.ovx).sort((a, b) => a - b);
  const cuts = [nearestRank(sorted, 1 / 3), nearestRank(sorted, 2 / 3)];
  const groups = [[], [], []];
  for (const row of rows) groups[row.ovx <= cuts[0] ? 0 : row.ovx <= cuts[1] ? 1 : 2].push(row);
  return groups.map((group, index) => {
    if (!group.length) return null;
    const covered = group.filter((row) => row.actual >= row.lo80 && row.actual <= row.hi80).length;
    const ovx = group.map((row) => row.ovx);
    return {
      tercile: index + 1,
      ovxLow: Math.min(...ovx),
      ovxHigh: Math.max(...ovx),
      n: group.length,
      covered,
      coverage: (covered / group.length) * 100,
    };
  }).filter(Boolean);
};
