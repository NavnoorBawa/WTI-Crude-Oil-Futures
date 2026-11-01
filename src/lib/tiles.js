// The verdict's evidence tiles, computed from the payloads. Every headline value comes from one
// sample: the benchmark lab (research.json vol_lab), scored on every out-of-sample day and rebuilt in
// CI from inputs pinned by SHA-256. Each footnote ends with the same statistic from the weekly
// production validation (data.json vol_forecast.validation), a different sample, named as such.
// Without the lab figure (research.json loading, unavailable, or missing the block) the weekly
// figure becomes the value and the baseline starts "weekly production validation".
//
// Pure functions only. Text comes back as a string, or as a list of strings and {num} figures that
// Kpi sets in bold tabular digits; glyphs come back as data that KpiGlyph draws.
import { INTERVAL_LEVEL_PCT } from "./assertions";
import { DASH, NBSP, fmtInt, fmtNum, fmtP, fmtPct, fmtSpan, num, ordinal } from "./format";
import {
  OVX_ONLY_KEY, asArray, asObject, asText, coverageByYear, getLab, getRoles, getVol, leaderboardRows, mcsSummary, modelLabels,
  ovxOnlyBenchmark, ovxOnlyLabel, persistenceBenchmark, qlikeFloor, recordVar, shortLabel, varRows,
} from "./research";

const PERSISTENCE_KEY = "persistence";
const RAW_OVX_KEY = "raw_ovx";
/** The VaR level the tile reports (99%). */
const VAR_LEVEL = 0.01;
/**
 * The largest gap, in vol points, between the two pipelines' forecasts for the same week that is
 * still read as the same forecast: both payloads round forecasts to two decimals.
 */
const SAME_FORECAST_PTS = 0.05;
/** Two-sided 95% normal critical value, for the slope's whisker. */
const Z95 = 1.96;

/** What a tile says when its research.json figure is absent: loading, no file, or no such block. */
export const researchGap = (research, researchStatus, what) => {
  if (researchStatus === "loading") return "loading research.json";
  return research ? `no ${what} in research.json` : "research.json unavailable";
};

const joinDot = (parts) => {
  const kept = parts.filter(Boolean);
  return kept.length ? kept.join(" · ") : null;
};

/** The value at the nearest-rank percentile `pct` (1-100) of an ascending list. */
export const nearestRank = (sorted, pct) => {
  if (!sorted.length) return null;
  // pct * n is an integer for integer pct, so the rank is exact (no 0.1 * n rounding drift).
  const rank = Math.min(sorted.length, Math.max(1, Math.ceil((pct * sorted.length) / 100)));
  return sorted[rank - 1];
};

/**
 * A fixed (unconditional) band of weekly realized vol: the nearest-rank `lower` and `upper`
 * percentiles of every realized value, and their ratio, the hi/lo of a band that ignores the
 * forecast. It is fitted on the whole sample, so it is an in-sample comparator.
 * @returns {{lo:number, hi:number, ratio:number|null, n:number}|null}
 */
export const unconditionalBand = (actual, { lower = 10, upper = 90 } = {}) => {
  const values = asArray(actual).map(num).filter((value) => value != null && value > 0).sort((a, b) => a - b);
  if (values.length < 2) return null;
  const lo = nearestRank(values, lower);
  const hi = nearestRank(values, upper);
  return { lo, hi, ratio: lo > 0 ? hi / lo : null, n: values.length };
};

/** {k, d} for one field of the two pipelines joined on date: dates with both values, largest gap. */
const largestGap = (labIndex, series, labKeys, recentOos, recentKeys) => {
  let k = 0;
  let d = null;
  asArray(recentOos?.date).forEach((date, row) => {
    const index = labIndex.get(date);
    if (index == null) return;
    labKeys.forEach((labKey, field) => {
      const value = num(recentOos?.[recentKeys[field]]?.[row]);
      const other = num(series?.[labKey]?.[index]);
      if (value == null || other == null) return;
      if (field === 0) k += 1;
      d = Math.max(d ?? 0, Math.abs(value - other));
    });
  });
  return { k, d };
};

/**
 * Whether the two pipelines issue the same weekly forecast: research.json vol_lab.series.har_iv and
 * data.json recent_oos.forecast joined on date. `k` counts the dates with both forecasts and `d`
 * is the largest absolute difference in vol points (null when k is 0). `bands` does the same for
 * the 80% bands (lo80 and hi80; k counts the dates with both lower bounds), which each pipeline
 * sets on its own.
 */
export const pipelineAgreement = (series, recentOos) => {
  const labIndex = new Map();
  asArray(series?.date).forEach((date, index) => {
    if (typeof date === "string") labIndex.set(date, index);
  });
  return {
    ...largestGap(labIndex, series, ["har_iv"], recentOos, ["forecast"]),
    bands: largestGap(labIndex, series, ["lo80", "hi80"], recentOos, ["lo80", "hi80"]),
  };
};

/** The model of record's yearly conformal coverage: min, max and the number of years. */
export const coverageRange = (lab, key) => {
  const years = coverageByYear(lab, key).map((row) => row.coverage);
  return years.length ? { min: Math.min(...years), max: Math.max(...years), years: years.length } : null;
};

/** The days the lab scores (every out-of-sample day) and their span. */
const labSample = (lab) => ({
  nDays: num(lab?.leaderboard?.n_days) ?? num(lab?.sample?.n_days),
  span: lab?.sample ? fmtSpan(lab.sample.first, lab.sample.last) : DASH,
});

/** "vs 0.368 OVX-only regression, 0.952 persistence · lower is better" as text parts. */
const qlikeComparators = (ovx, persistence, ovxName) => {
  const named = [
    ovx != null ? [{ num: fmtNum(ovx, 3) }, `${NBSP}${ovxName}`] : null,
    persistence != null ? [{ num: fmtNum(persistence, 3) }, `${NBSP}persistence`] : null,
  ].filter(Boolean);
  if (!named.length) return ["lower is better"];
  return ["vs ", ...named.flatMap((parts, index) => (index ? [", ", ...parts] : parts)), " · lower is better"];
};

/**
 * A one-axis glyph: zero to persistence, the record as a dot and OVX alone as a ring around it. The proxy's
 * QLIKE floor stays out of it, in the foot as text: a mark would turn its lower bound into a point, and the
 * gap to the dot would read as a distance the data do not identify.
 */
const qlikeGlyph = (record, ovx, persistence) => {
  if (record == null) return null;
  const hi = persistence ?? Math.max(record, ovx ?? 0) * 2;
  if (!(hi > 0)) return null;
  return {
    kind: "axis",
    domain: [0, hi],
    marks: [
      persistence != null ? { type: "tick", at: persistence, tone: "muted" } : null,
      { type: "dot", at: record, tone: "model" },
      ovx != null ? { type: "ring", at: ovx, tone: "implied" } : null,
    ].filter(Boolean),
  };
};

function qlikeTile({ lab, rows, validation, research, researchStatus }) {
  const record = rows.find((row) => row.role === "record");
  const ovxName = ovxOnlyLabel(lab);
  const ovxRow = rows.find((row) => row.key === OVX_ONLY_KEY);
  const persistenceRow = rows.find((row) => row.key === PERSISTENCE_KEY);
  const weekly = {
    har: num(validation?.har_qlike),
    ovx: ovxOnlyBenchmark(validation)?.qlike ?? null,
    persistence: persistenceBenchmark(validation)?.qlike ?? null,
    n: num(validation?.n),
  };
  const weeklyText = weekly.har != null
    ? `weekly validation: ${fmtNum(weekly.har, 3)}${weekly.ovx != null ? ` vs ${fmtNum(weekly.ovx, 3)}` : ""}`
      + `${weekly.n != null ? ` (${fmtInt(weekly.n)}${NBSP}wk)` : ""}`
    : null;
  const tile = { id: "qlike", label: "Forecast loss (QLIKE)", href: "#modl" };

  if (record?.qlike != null) {
    const { nDays } = labSample(lab);
    const floor = qlikeFloor(lab)?.lo ?? null;
    return {
      ...tile,
      value: fmtNum(record.qlike, 3),
      glyph: qlikeGlyph(record.qlike, ovxRow?.qlike, persistenceRow?.qlike),
      baseline: qlikeComparators(ovxRow?.qlike, persistenceRow?.qlike, ovxName),
      foot: joinDot([`every OOS day${nDays != null ? `, ${fmtInt(nDays)}${NBSP}d` : ""}`,
        floor != null ? `proxy's noise floor ≥${NBSP}${fmtNum(floor, 3)}` : null, weeklyText]),
    };
  }
  if (researchStatus === "loading" || weekly.har == null) {
    return { ...tile, value: DASH, glyph: null, baseline: researchGap(research, researchStatus, "model leaderboard"), foot: weeklyText };
  }
  return {
    ...tile,
    value: fmtNum(weekly.har, 3),
    glyph: qlikeGlyph(weekly.har, weekly.ovx, weekly.persistence),
    baseline: [`weekly production validation${weekly.n != null ? `, ${fmtInt(weekly.n)}${NBSP}wk` : ""} · `,
      ...qlikeComparators(weekly.ovx, weekly.persistence, ovxName)],
    foot: researchGap(research, researchStatus, "model leaderboard"),
  };
}

/** One cell per eligible model in leaderboard order (QLIKE, lowest first); members filled. */
const mcsGlyph = (rows) => {
  const eligible = rows
    .filter((row) => row.mcsEligible)
    .map((row, index) => ({ row, index }))
    .sort((a, b) => (a.row.qlike ?? Infinity) - (b.row.qlike ?? Infinity) || a.index - b.index)
    .map(({ row }) => row);
  if (!eligible.length) return null;
  const tone = (row) => (row.role === "record" ? "model" : row.role === "challenger" ? "alt" : "text");
  return { kind: "cells", cells: eligible.map((row) => ({ tone: tone(row), filled: row.inMcs })) };
};

function mcsTile({ lab, rows, research, researchStatus }) {
  const mcs = mcsSummary(lab);
  const tile = { id: "mcs", label: "Model Confidence Set", href: "#modl" };
  if (mcs?.survivors == null) {
    return { ...tile, value: DASH, glyph: null, baseline: researchGap(research, researchStatus, "Model Confidence Set"), foot: null };
  }
  const set = mcs.level != null ? `the ${mcs.level}% set` : "the set";
  const champion = mcs.champion;
  const mcsP = champion?.mcsP != null ? ` (MCS${NBSP}${fmtP(champion.mcsP)})` : "";
  const grids = champion?.gridsIn != null && mcs.gridCount ? `; in on ${fmtInt(champion.gridsIn)}/${fmtInt(mcs.gridCount)} grids` : "";
  let baseline = `models in ${set} on QLIKE`;
  if (champion) {
    baseline = champion.inMcs
      ? `${champion.label} is in ${set}${mcsP}${grids}`
      : `${champion.label} not in ${set}${mcsP}${grids}`;
  }
  const excluded = mcs.excluded.length
    ? `${mcs.excluded.length === 1 ? "timing control" : `${fmtInt(mcs.excluded.length)} timing controls`} excluded`
    : null;
  return {
    ...tile,
    value: `${mcs.survivors}/${mcs.eligible}`,
    unit: "models",
    glyph: mcsGlyph(rows),
    baseline,
    foot: joinDot(["QLIKE, every OOS day", excluded]),
  };
}

/** A target tick, the yearly range as a bar and the overall coverage as a dot, around the target. */
const coverageGlyph = (target, value, range) => {
  if (value == null && !range) return null;
  const low = Math.min(target - 10, (range?.min ?? value) - 2);
  const high = Math.max(target + 10, (range?.max ?? value) + 2);
  return {
    kind: "range",
    domain: [low, high],
    marks: [
      range ? { type: "bar", from: range.min, to: range.max, tone: "faint" } : null,
      { type: "tick", at: target, tone: "muted" },
      value != null ? { type: "dot", at: value, tone: "model" } : null,
    ].filter(Boolean),
  };
};

function coverageTile({ lab, rows, validation, research, researchStatus }) {
  const champion = rows.find((row) => row.role === "record")?.key ?? getRoles(lab, null).champion;
  const conformal = asObject(lab?.conformal?.[champion]);
  const target = num(conformal?.target_coverage_pct) ?? INTERVAL_LEVEL_PCT;
  const range = coverageRange(lab, champion);
  const weekly = { coverage: num(validation?.interval_80_coverage_pct), n: num(validation?.interval_80_n) };
  const weeklyText = weekly.coverage != null
    ? `weekly validation: ${fmtPct(weekly.coverage)}${weekly.n != null ? ` of ${fmtInt(weekly.n)}${NBSP}wk` : ""}`
    : null;
  const construction = `near ${fmtNum(target, 0)}% by construction`;
  // The label is the lab's while research.json loads, so it does not change when the value arrives.
  const tile = { id: "coverage", label: `${INTERVAL_LEVEL_PCT}% interval, coverage by year`, href: "#calb" };

  if (range) {
    const overall = num(conformal?.coverage_pct);
    const days = num(conformal?.n_days);
    const ratio = num(conformal?.median_hi_lo_ratio);
    // The comparator: a band fixed at the same central percentiles (10th-90th for 80%).
    const tail = Math.round((100 - target) / 2);
    const series = asObject(lab?.series);
    const band = unconditionalBand(series?.actual, { lower: tail, upper: 100 - tail });
    // The span of the weeks the band is fitted on (positive realized values, as unconditionalBand keeps).
    const dated = asArray(series?.date).filter((date, index) => typeof date === "string" && (num(series?.actual?.[index]) ?? 0) > 0);
    let sharpness = null;
    if (ratio != null) {
      sharpness = band?.ratio != null
        ? `median hi/lo ${fmtNum(ratio, 2)} vs ${fmtNum(band.ratio, 2)} for a fixed ${ordinal(tail)}–${ordinal(100 - tail)} percentile band`
          + ` of weekly realized vol, ${fmtSpan(dated[0], dated[dated.length - 1])} (in-sample)`
        : `median hi/lo ${fmtNum(ratio, 2)}`;
    }
    return {
      ...tile,
      value: `${fmtNum(range.min, 1)}–${fmtNum(range.max, 1)}%`,
      glyph: coverageGlyph(target, overall, range),
      baseline: overall != null
        ? `overall ${fmtPct(overall)}${days != null ? ` of ${fmtInt(days)} days` : ""}; ${construction}`
        : construction,
      foot: joinDot([sharpness, weeklyText]),
    };
  }
  if (researchStatus === "loading" || weekly.coverage == null) {
    return { ...tile, value: DASH, glyph: null, baseline: researchGap(research, researchStatus, "conformal coverage"), foot: weeklyText };
  }
  return {
    ...tile,
    label: `${INTERVAL_LEVEL_PCT}% interval coverage`,
    value: fmtPct(weekly.coverage),
    glyph: coverageGlyph(INTERVAL_LEVEL_PCT, weekly.coverage, null),
    baseline: `weekly production validation${weekly.n != null ? `, ${fmtInt(weekly.n)}${NBSP}wk` : ""}; near ${INTERVAL_LEVEL_PCT}% by construction`,
    foot: researchGap(research, researchStatus, "conformal coverage"),
  };
}

/** Breaches from zero: the expected count as a tick, the range across grids as a bar, grid 0 as a dot. */
const varGlyph = (row) => {
  const hi = Math.max(row.exceedMax ?? row.exceedances, (row.expected ?? 0) * 2, row.exceedances);
  if (!(hi > 0)) return null;
  return {
    kind: "count",
    domain: [0, hi],
    marks: [
      row.exceedMin != null && row.exceedMax != null ? { type: "bar", from: row.exceedMin, to: row.exceedMax, tone: "faint" } : null,
      row.expected != null ? { type: "tick", at: row.expected, tone: "muted" } : null,
      { type: "dot", at: row.exceedances, tone: "model" },
    ].filter(Boolean),
  };
};

function varTile({ lab, research, researchStatus }) {
  const rows = varRows(lab).filter((row) => row.level === VAR_LEVEL);
  const record = recordVar(rows, VAR_LEVEL);
  const tile = { id: "var", label: "99% VaR breaches", href: "#risk" };
  if (record?.exceedances == null) {
    return { ...tile, value: DASH, glyph: null, baseline: researchGap(research, researchStatus, "VaR backtest"), foot: null };
  }
  const kupiec = fmtP(record.kupiecP);
  const normal = record.quantile !== "normal"
    ? rows.find((row) => row.model === record.model && row.quantile === "normal" && row.exceedances != null)
    : null;
  const rawOvx = rows.find((row) => row.model === RAW_OVX_KEY && row.quantile === "normal" && row.exceedances != null);
  const rawName = shortLabel(modelLabels(lab).get(RAW_OVX_KEY)) ?? "Raw OVX";
  const counted = (row) => `${fmtInt(row.exceedances)}${fmtP(row.kupiecP) ? ` (${fmtP(row.kupiecP)})` : ""}`;
  const head = `${record.quantileLabel}, grid 0${record.n != null ? `, ${fmtInt(record.n)}${NBSP}wk` : ""}`
    + `${record.exceedMin != null && record.exceedMax != null ? `; ${fmtInt(record.exceedMin)}–${fmtInt(record.exceedMax)} across grids` : ""}`;
  return {
    ...tile,
    value: fmtInt(record.exceedances),
    glyph: varGlyph(record),
    baseline: [
      ...(record.expected != null ? ["vs ", { num: fmtNum(record.expected, 1) }, " expected"] : []),
      ...(kupiec ? [`${record.expected != null ? " · " : ""}Kupiec${NBSP}${kupiec}`] : []),
    ],
    foot: joinDot([
      head,
      normal ? `normal quantile: ${counted(normal)}` : null,
      rawOvx ? `${rawName} (normal): ${fmtInt(rawOvx.exceedances)}` : null,
    ]),
  };
}

/** Slope against 1 (unbiased): a tick at 1, the slope as a dot with its 95% interval as a whisker. */
const mzGlyph = (slope, se) => {
  if (slope == null) return null;
  const reach = se != null ? slope + Z95 * se : slope;
  return {
    kind: "whisker",
    domain: [0, Math.max(2, reach)],
    marks: [
      se != null ? { type: "bar", from: slope - Z95 * se, to: slope + Z95 * se, tone: "faint" } : null,
      { type: "tick", at: 1, tone: "muted" },
      { type: "dot", at: slope, tone: "model" },
    ].filter(Boolean),
  };
};

function mzTile({ rows, validation, research, researchStatus }) {
  const record = rows.find((row) => row.role === "record");
  const mz = asObject(validation?.har_mincer_zarnowitz);
  const weekly = { slope: num(mz?.slope), se: num(mz?.slope_se), n: num(validation?.n) };
  const weeklyText = weekly.slope != null
    ? `weekly validation: ${fmtNum(weekly.slope, 2)}${weekly.se != null ? ` (SE ${fmtNum(weekly.se, 2)})` : ""}`
    : null;
  const tile = { id: "mz", label: "Mincer-Zarnowitz slope", href: "#calb" };
  const unbiased = (se) => `1${NBSP}=${NBSP}unbiased${se != null ? ` · SE${NBSP}${fmtNum(se, 2)}` : ""}`;

  if (record?.mzSlope != null) {
    return {
      ...tile,
      value: fmtNum(record.mzSlope, 2),
      glyph: mzGlyph(record.mzSlope, record.mzSe),
      baseline: unbiased(record.mzSe),
      foot: joinDot(["every OOS day", weeklyText ? `${weeklyText}; see CALB` : null]),
    };
  }
  if (researchStatus === "loading" || weekly.slope == null) {
    return { ...tile, value: DASH, glyph: null, baseline: researchGap(research, researchStatus, "Mincer-Zarnowitz slope"), foot: weeklyText };
  }
  return {
    ...tile,
    value: fmtNum(weekly.slope, 2),
    glyph: mzGlyph(weekly.slope, weekly.se),
    baseline: `weekly production validation${weekly.n != null ? `, ${fmtInt(weekly.n)}${NBSP}wk` : ""} · ${unbiased(weekly.se)}`,
    foot: researchGap(research, researchStatus, "Mincer-Zarnowitz slope"),
  };
}

/**
 * The five evidence tiles before the live record's, in page order: forecast loss, Model Confidence
 * Set, interval coverage, 99% VaR breaches and the Mincer-Zarnowitz slope. Each is
 * {id, label, href, value, unit?, glyph, baseline, foot}.
 */
export const evidenceTiles = (data, research, researchStatus) => {
  const lab = getLab(research);
  const { validation } = getVol(data);
  const rows = leaderboardRows(lab);
  const context = { lab, rows, validation, research, researchStatus };
  return [qlikeTile(context), mcsTile(context), coverageTile(context), varTile(context), mzTile(context)];
};

/** The live record's promotion test as a strip: one cell per required week, the paired ones filled. */
export const progressGlyph = (promotion) => {
  const total = num(promotion?.required);
  if (total == null || total <= 0 || !Number.isInteger(total)) return null;
  const filled = Math.min(total, Math.max(0, num(promotion?.paired) ?? 0));
  return { kind: "progress", cells: Array.from({ length: total }, (_, index) => ({ tone: "text", filled: index < filled })) };
};

/** Every input file of the lab carries a SHA-256 digest. */
const inputsPinned = (lab) => {
  const files = Object.values(asObject(lab?.generated_from?.files) ?? {});
  return files.length > 0 && files.every((file) => /^[0-9a-f]{64}$/i.test(asText(file?.sha256) ?? ""));
};

/**
 * The key under the tiles: which sample the values come from (the lab) and which the footnotes
 * quote (the weekly production validation), and, computed, whether the two pipelines issue the
 * same weekly forecasts, so a gap between a loss or slope and its footnote comes from the days
 * scored. The 80% bands are compared too: each pipeline sets its own, and the key says so when
 * they differ, since coverage then also depends on the band.
 */
export const tilesKey = (data, research, researchStatus) => {
  const lab = getLab(research);
  const { validation, recentOos } = getVol(data);
  const weeks = num(validation?.n);
  const weekly = `weekly production validation${weeks != null ? ` (${fmtInt(weeks)} weeks)` : ""}`;
  if (!lab) {
    if (researchStatus === "loading") {
      return `Tiles (except the live record): benchmark lab, every out-of-sample day, once research.json loads. Footnotes: ${weekly}.`;
    }
    return `Tiles: ${weekly}; ${research ? "research.json has no benchmark lab" : "research.json did not load"}.`;
  }
  const { nDays, span } = labSample(lab);
  const sample = [nDays != null ? `${fmtInt(nDays)} days` : null, span !== DASH ? span : null].filter(Boolean).join(", ");
  const pinned = inputsPinned(lab) ? "inputs pinned by SHA-256" : null;
  const detail = [sample, pinned].filter(Boolean).join("; ");
  const champion = getRoles(lab, null).champion;
  const bandDays = num(asObject(lab?.conformal?.[champion])?.n_days);
  const varWeeks = num(lab?.var_backtest?.grid0?.evaluation_weeks);
  const exceptions = [
    bandDays != null && bandDays !== nDays ? `coverage on the ${fmtInt(bandDays)} days with a band` : null,
    varWeeks != null ? `VaR on weekly grid 0 (${fmtInt(varWeeks)} weeks)` : null,
  ].filter(Boolean);
  const head = `Tiles (except the live record): benchmark lab, every out-of-sample day${detail ? ` (${detail})` : ""}`
    + `${exceptions.length ? `, except ${exceptions.join(" and ")}` : ""}. Footnotes: ${weekly}.`;
  const { k, d, bands } = pipelineAgreement(lab.series, recentOos);
  if (!k) return head;
  if (d > SAME_FORECAST_PTS) {
    return `${head} The two pipelines' weekly forecasts differ by up to ${fmtNum(d, 2)} vol pts on ${fmtInt(k)} overlapping weeks.`;
  }
  const same = `${head} Both pipelines issue the same weekly forecasts (${fmtInt(k)} overlapping weeks, largest difference `
    + `${fmtNum(d, 2)} vol pts), so`;
  if (bands.k && bands.d > SAME_FORECAST_PTS) {
    const where = bands.k === k ? "on those weeks" : `on ${fmtInt(bands.k)} of them`;
    return `${same} the loss and slope gaps between the two come from which days are scored; each pipeline sets its own `
      + `${INTERVAL_LEVEL_PCT}% band (up to ${fmtNum(bands.d, 1)} vol pts apart ${where}).`;
  }
  return `${same} gaps between the two come from which days are scored.`;
};
