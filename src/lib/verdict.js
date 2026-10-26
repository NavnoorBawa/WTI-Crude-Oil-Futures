// The verdict's computed pieces: the one-sentence answer, the index of section titles, and this
// week's call exactly as the live record holds it, with its track record and arithmetic. Pure
// functions over data.json / research.json (no React, no panels), exported for the tests.
import { SECTIONS } from "../app/sections";
import {
  ALPHA, ALPHA_LOOSE, calibrationAssertion, forecastAssertion, leaderboardAssertion, liveAssertion, riskAssertion,
  stabilityAssertion, vrpAssertion,
} from "./assertions";
import { mcsRobustness } from "./comparison";
import { NBSP, fmtInt, fmtNum, fmtP, fmtPValue, fmtSigned, fmtSpan, num, toMs } from "./format";
import {
  OVX_ONLY_KEY, asArray, asObject, asText, coverageByYear, getLab, getRoles, getVol, hasMcsResults, leaderboardRows,
  liveRecordSummary, mcsLevel, modelLabels, ovxOnlyLabel, shortLabel, varRows, varSeries, yearlyQlike,
} from "./research";

/** Two payload values within this many points are the same figure (they print alike at one decimal). */
export const TOLERANCE = 0.05;
/** Titles (h2/h3) are at most this long (render.test.mjs enforces it page-wide). */
const TITLE_MAX = 90;
/** The lab key of OVX itself used as the forecast (the other "OVX alone" benchmark). */
const RAW_OVX_KEY = "raw_ovx";
/** The stability panel's comparison models after the record and challenger (StabilityPanel.jsx's COMPARISON_KEYS). */
const STABILITY_COMPARISONS = [OVX_ONLY_KEY, RAW_OVX_KEY, "gjr"];
/** The vol cone window that matches the forecast's 5-session horizon. */
const CONE_WINDOW = 5;
/** The typical miss's upper quantile: one week in five missed by more. */
const MISS_QUANTILE = 80;

const FALLBACK_ANSWER = "Next-week WTI realized volatility: forecast and evidence";

const close = (a, b) => a != null && b != null && Math.abs(a - b) < TOLERANCE;

/**
 * "≤ 0.002" for the largest of a set of p-values: rounded up, so the bound holds for every one of
 * them, with a digit added when rounding up would land on a conventional threshold it is below.
 */
export const pAtMost = (p) => {
  const value = num(p);
  if (value == null) return null;
  if (value < 0.001) return `<${NBSP}0.001`;
  for (let digits = value < 0.01 ? 3 : 2; digits <= 4; digits += 1) {
    const scale = 10 ** digits;
    const bound = Math.ceil(value * scale - 1e-9) / scale;
    if (![ALPHA, ALPHA_LOOSE].some((t) => Math.abs(bound - t) < 1e-12 && value < t)) return `≤${NBSP}${bound.toFixed(digits)}`;
  }
  return `≤${NBSP}${fmtPValue(value)}`;
};

// ------------------------------------------------------------------------------------- the answer

/**
 * The lab's comparison behind the answer: the model of record against the eligible models that use
 * returns only, and against OVX on its own (the OVX-only regression and raw OVX).
 */
const labComparison = (lab) => {
  const rows = leaderboardRows(lab);
  const record = rows.find((row) => row.role === "record") ?? null;
  if (!record) return null;
  const eligible = rows.filter((row) => row.mcsEligible && !row.timingControl);
  const returnsOnly = eligible.filter((row) => row.info.size === 0 && row !== record);
  const ovxAlone = eligible.filter((row) => row.key === OVX_ONLY_KEY || row.key === RAW_OVX_KEY);
  return { record, recordName: shortLabel(record.label), returnsOnly, ovxAlone };
};

const significant = (row) => row.dmStat != null && row.dmP != null && row.dmP < ALPHA;

/**
 * The page's answer from the benchmark lab, or null when the lab cannot support one: the record
 * beats every returns-only model (Diebold-Mariano on QLIKE, every one significant at ALPHA), then
 * how it stands against OVX alone. Positive DM t = lower loss than the record.
 */
const labAnswer = (comparison) => {
  if (!comparison) return null;
  const { recordName, returnsOnly, ovxAlone } = comparison;
  const n = returnsOnly.length;
  if (!n || !returnsOnly.every((row) => significant(row) && row.dmStat < 0)) return null;
  const head = `${recordName} beats ${n === 1 ? "the one returns-only model" : `all ${fmtInt(n)} returns-only models`}`;
  if (!ovxAlone.length || ovxAlone.some((row) => row.dmStat == null || row.dmP == null)) return head;
  let full = head;
  if (ovxAlone.every((row) => row.dmP >= ALPHA)) {
    // A p-value between 5% and 10% is inconclusive, and is said to be.
    full = ovxAlone.some((row) => row.dmP < ALPHA_LOOSE)
      ? `${head}; the comparison with OVX alone is inconclusive`
      : `${head}; it is not distinguishable from OVX alone`;
  }
  else if (ovxAlone.some((row) => significant(row) && row.dmStat > 0)) full = `${head} but loses to OVX alone`;
  else if (ovxAlone.every((row) => significant(row) && row.dmStat < 0)) full = `${head} and OVX alone`;
  return full.length <= TITLE_MAX ? full : head.length <= TITLE_MAX ? head : null;
};

/** The supporting statistics of the lab answer, with their sample. */
const labDetail = (lab, comparison) => {
  const { recordName, returnsOnly, ovxAlone } = comparison;
  const nDays = num(lab?.leaderboard?.n_days) ?? num(lab?.sample?.n_days);
  const span = fmtSpan(lab?.sample?.first, lab?.sample?.last);
  const sample = [nDays != null ? `${fmtInt(nDays)} days` : null, span !== "—" ? span : null, "benchmark lab"].filter(Boolean).join(", ");
  const stats = returnsOnly.map((row) => row.dmStat);
  const top = Math.max(...stats);
  const bottom = Math.min(...stats);
  const range = returnsOnly.length === 1 ? fmtSigned(top, 2) : `${fmtSigned(top, 2)} to ${fmtSigned(bottom, 2)}`;
  const worstP = Math.max(...returnsOnly.map((row) => row.dmP));
  const pText = returnsOnly.length === 1 ? fmtP(worstP) : `all p ${pAtMost(worstP)}`;
  const parts = [
    `returns-only models ${range} (${pText})`,
    ...ovxAlone.filter((row) => row.dmStat != null).map((row) => `${shortLabel(row.label)} ${fmtSigned(row.dmStat, 2)}${row.dmP != null ? ` (${fmtP(row.dmP)})` : ""}`),
  ];
  // No-break spaces: a wrapped line never starts with "= lower loss".
  return `Diebold-Mariano t vs ${recordName} on every out-of-sample day (${sample}; +${NBSP}=${NBSP}lower loss than ${recordName}): ${parts.join("; ")}.`;
};

/** The weekly production validation's statistics behind forecastAssertion(), with their sample. */
const productionDetail = (validation, ovxLabel) => {
  const n = num(validation?.n);
  const r2 = num(validation?.har_level_r2);
  const persistenceR2 = num(validation?.persistence_level_r2);
  const ovxQlike = num(validation?.ovx_only_qlike);
  const harQlike = num(validation?.har_qlike);
  const ovxP = num(validation?.har_vs_ovx_only_qlike_dm_p_value);
  const parts = [
    r2 != null && persistenceR2 != null ? `out-of-sample R² ${fmtNum(r2, 2)} vs ${fmtNum(persistenceR2, 2)} for persistence` : null,
    harQlike != null && ovxQlike != null
      ? `QLIKE ${fmtNum(harQlike, 3)} vs ${fmtNum(ovxQlike, 3)} for the ${ovxLabel}${ovxP != null ? `, Diebold-Mariano ${fmtP(ovxP)}` : ""}`
      : null,
  ].filter(Boolean);
  if (!parts.length) return null;
  return `Weekly production validation${n != null ? ` (${fmtInt(n)} weeks)` : ""}: ${parts.join("; ")}.`;
};

/**
 * The verdict's answer and its supporting line. From the benchmark lab when it supports the
 * statement (see labAnswer()); otherwise the forecast panel's claim on the weekly production
 * validation; otherwise a neutral line.
 * @returns {{answer: string, detail: string|null, sample: "lab"|"production"|null}}
 */
export const verdictLede = (lab, data) => {
  const comparison = labComparison(lab);
  const fromLab = labAnswer(comparison);
  if (fromLab) return { answer: fromLab, detail: labDetail(lab, comparison), sample: "lab" };
  const { validation, live } = getVol(data);
  const ovxLabel = ovxOnlyLabel(lab);
  const production = forecastAssertion(validation, live, { ovxLabel });
  if (production && production.length <= TITLE_MAX) {
    return { answer: production, detail: productionDetail(validation, ovxLabel), sample: "production" };
  }
  return { answer: FALLBACK_ANSWER, detail: null, sample: null };
};

/** The verdict's one-sentence answer (the page's h2 #verdict-title). */
export const verdictAnswer = (lab, data) => verdictLede(lab, data).answer;

// ---------------------------------------------------------------------------------- evidence list

/**
 * The section titles as an index: [{id, code, text}] in section order, each exactly the h2 its
 * panel renders (the same builder with the same arguments), skipping sections whose panel falls
 * back to a neutral title. CONTRACT: a package that changes a panel's title builder or its
 * arguments updates the matching entry here; verdict.test.mjs compares every row with its h2.
 */
export const evidenceRows = (data, research) => {
  const { live, validation, liveRecord, economic } = getVol(data);
  const lab = getLab(research);
  const labels = modelLabels(lab);
  const { champion, challenger } = getRoles(lab, null);
  const rows = leaderboardRows(lab);
  const methods = varRows(lab);

  // StabilityPanel: the yearly QLIKE of the models present in the yearly table, benchmarked on the
  // OVX-only regression.
  const yearlyKeys = new Set(Object.keys(asObject(asArray(lab?.yearly)[0]) ?? {})
    .filter((key) => key.endsWith("_qlike")).map((key) => key.slice(0, -"_qlike".length)));
  const stabilityKeys = [champion, challenger, ...STABILITY_COMPARISONS].filter((key) => key && yearlyKeys.has(key));
  const summary = liveRecordSummary(liveRecord);

  const titles = {
    fcst: forecastAssertion(validation, live, { ovxLabel: ovxOnlyLabel(lab) }),
    modl: rows.length
      ? leaderboardAssertion(rows, { level: mcsLevel(lab), available: hasMcsResults(lab), robustness: mcsRobustness(lab) })
      : null,
    stab: stabilityKeys.length
      ? stabilityAssertion(yearlyQlike(lab, stabilityKeys), { champion, challenger, bench: OVX_ONLY_KEY, labels })
      : null,
    calb: coverageByYear(lab, champion).length || coverageByYear(lab, challenger).length ? calibrationAssertion(lab, champion) : null,
    risk: methods.length && varSeries(lab).length ? riskAssertion(methods, labels, champion) : null,
    vrp: vrpAssertion(asObject(economic?.variance_risk_premium)),
    live: summary ? liveAssertion(summary) : null,
  };
  return SECTIONS
    .filter((section) => titles[section.id])
    .map((section) => ({ id: section.id, code: section.code, text: titles[section.id] }));
};

// --------------------------------------------------------------------------------- the recorded call

const liveValues = (block) => ({
  forecast: num(block.forecast_next_week_vol_annualized_pct),
  median: num(block.forecast_median_pct),
  lo: num(asObject(block.interval_80)?.low_pct),
  hi: num(asObject(block.interval_80)?.high_pct),
  challenger: num(asObject(block.challenger)?.forecast_next_week_vol_annualized_pct),
});

/** The fields a recomputation is compared on, in the order its note lists them. */
export const RECOMPUTE_FIELDS = ["forecast", "lo", "hi", "challenger"];

/**
 * This week's call as the live record holds it. `entry` is the first live_record.recent row with
 * the live block's as_of and model_version and a forecast. With an entry the values are the
 * recorded ones (source "record"); a live block issued later on the same close whose forecast,
 * band or challenger differs by TOLERANCE or more is a recomputation (`recompute`), shown but never
 * scored. When the live block is the recorded issuance itself (same issued_at, same values) its
 * fields are used, since they are the same numbers with the errors and labels attached. Without an
 * entry the live block is shown as is (source "live") and there is no target window.
 */
export const forecastOfRecord = (live, liveRecord) => {
  const block = asObject(live);
  if (!block) return null;
  const current = liveValues(block);
  const asOf = asText(block.as_of);
  const version = asText(block.model_version);
  const entry = asOf ? asArray(asObject(liveRecord)?.recent).find((row) => asObject(row) && row.as_of === asOf
    && row.model_version === version && num(row.forecast_pct) != null) ?? null : null;
  const blockInputs = asObject(block.inputs);

  if (!entry) {
    if (current.forecast == null) return null;
    return {
      source: "live", entry: null, ...current, asOf, issuedAt: asText(block.issued_at), sealedAt: null,
      targetStart: null, targetEnd: null, prob: num(block.prob_vol_rises_pct), inputs: blockInputs, recompute: null,
      realized: { value: num(block.current_realized_vol_5d_annualized_pct), recomputed: false },
      ovx: { value: num(block.implied_vol_ovx_pct), recomputed: false },
      ovxSession: asText(blockInputs?.ovx_session),
    };
  }

  const recorded = {
    forecast: num(entry.forecast_pct),
    median: num(entry.median_pct),
    lo: num(entry.lo80_pct),
    hi: num(entry.hi80_pct),
    challenger: num(entry.challenger_pct),
  };
  const changed = RECOMPUTE_FIELDS.filter((key) => recorded[key] != null && current[key] != null
    && Math.abs(recorded[key] - current[key]) >= TOLERANCE);
  const issuedMs = toMs(block.issued_at);
  const sameIssue = issuedMs != null && issuedMs === toMs(entry.issued_at);
  const useLive = sameIssue && !changed.length;
  // The live block forecasts the same median from the same close: its median-based fields hold.
  const sameForecast = close(recorded.forecast, current.forecast)
    && (recorded.median == null || current.median == null || close(recorded.median, current.median));
  const recompute = changed.length ? { fields: changed, values: current, issuedAt: asText(block.issued_at) } : null;
  const inputs = asObject(entry.inputs);
  const pick = (entryValue, blockValue) => (entryValue != null
    ? { value: entryValue, recomputed: false }
    : { value: blockValue, recomputed: blockValue != null && recompute != null });
  const value = (key) => (useLive ? current[key] ?? recorded[key] : recorded[key]);
  // The same issuance without a challenger says why (challenger_error); without a reason, the record's stands.
  const challenger = useLive && (current.challenger != null || asText(block.challenger_error))
    ? current.challenger
    : recorded.challenger;
  return {
    source: "record",
    entry,
    forecast: value("forecast"),
    median: useLive ? current.median ?? recorded.median : recorded.median ?? (sameForecast ? current.median : null),
    lo: value("lo"),
    hi: value("hi"),
    challenger,
    asOf,
    issuedAt: asText(entry.issued_at),
    sealedAt: asText(entry.sealed_at),
    targetStart: asText(entry.target_start),
    targetEnd: asText(entry.target_end),
    prob: num(entry.prob_vol_rises_pct) ?? (sameForecast ? num(block.prob_vol_rises_pct) : null),
    inputs,
    recompute,
    fromLive: useLive,
    realized: pick(num(inputs?.rv5_pct), num(block.current_realized_vol_5d_annualized_pct)),
    ovx: pick(num(inputs?.ovx), num(block.implied_vol_ovx_pct)),
    ovxSession: asText(inputs?.ovx_session) ?? asText(blockInputs?.ovx_session),
  };
};

/** The recorded calls with a forecast, newest first (by as_of, then issued_at). */
export const callHistory = (liveRecord) => asArray(asObject(liveRecord)?.recent)
  .filter((row) => asObject(row) && typeof row.as_of === "string" && num(row.forecast_pct) != null)
  .sort((a, b) => {
    if (a.as_of !== b.as_of) return a.as_of < b.as_of ? 1 : -1;
    return (toMs(b.issued_at) ?? 0) - (toMs(a.issued_at) ?? 0);
  });

/**
 * The change from the previous recorded call of the same model version, when the hero's call is
 * the newest one: {delta, previous}, delta in vol points between the two printed values.
 */
export const changeSincePrevious = (call, calls) => {
  if (!call?.entry || calls[0] !== call.entry || calls.length < 2) return null;
  const previous = calls[1];
  if (previous.model_version !== call.entry.model_version) return null;
  const now = num(call.forecast);
  const before = num(previous.forecast_pct);
  if (now == null || before == null) return null;
  return { delta: Number(now.toFixed(1)) - Number(before.toFixed(1)), previous };
};

// -------------------------------------------------------------------------------- track record

/** The production validation's weekly rows (data.json recent_oos) as {date, actual, forecast, lo, hi}. */
const recentRows = (recentOos) => {
  const recent = asObject(recentOos);
  if (!recent) return [];
  const actual = asArray(recent.actual);
  const forecast = asArray(recent.forecast);
  const lo = asArray(recent.lo80);
  const hi = asArray(recent.hi80);
  return asArray(recent.date)
    .map((date, index) => ({ date, actual: num(actual[index]), forecast: num(forecast[index]), lo: num(lo[index]), hi: num(hi[index]) }))
    .filter((row) => typeof row.date === "string");
};

const spanOf = (rows) => {
  const dates = rows.map((row) => row.date).sort();
  return { first: dates[0] ?? null, last: dates[dates.length - 1] ?? null };
};

/**
 * Where realized vol landed against each week's own 80% band, counted over the production
 * validation's recent weeks that have a realized value and a band.
 * @returns {{n:number, inside:number, below:number, above:number, first:string, last:string}|null}
 */
export const bandTrackRecord = (recentOos) => {
  const rows = recentRows(recentOos).filter((row) => row.actual != null && row.lo != null && row.hi != null);
  if (!rows.length) return null;
  const below = rows.filter((row) => row.actual < row.lo).length;
  const above = rows.filter((row) => row.actual > row.hi).length;
  return { n: rows.length, inside: rows.length - below - above, below, above, ...spanOf(rows) };
};

/**
 * The typical forecast error in vol points over the same weeks: the median of |realized − forecast|
 * and its nearest-rank MISS_QUANTILE-th percentile.
 * @returns {{n:number, median:number, upper:number, quantile:number, first:string, last:string}|null}
 */
export const typicalMiss = (recentOos) => {
  const rows = recentRows(recentOos).filter((row) => row.actual != null && row.forecast != null);
  if (!rows.length) return null;
  const errors = rows.map((row) => Math.abs(row.actual - row.forecast)).sort((a, b) => a - b);
  const n = errors.length;
  const middle = Math.floor(n / 2);
  const median = n % 2 ? errors[middle] : (errors[middle - 1] + errors[middle]) / 2;
  const upper = errors[Math.max(0, Math.ceil((MISS_QUANTILE / 100) * n) - 1)];
  return { n, median, upper, quantile: MISS_QUANTILE, ...spanOf(rows) };
};

/** The benchmark lab's 5-session realized vol distribution: {since, p50, p95}, or null. */
export const coneScale = (lab) => {
  const cone = asObject(lab?.vol_cone);
  const window = asArray(cone?.windows).find((row) => num(row?.window) === CONE_WINDOW);
  const p50 = num(window?.p50);
  const p95 = num(window?.p95);
  if (p50 == null || p95 == null) return null;
  return { since: asText(cone.since), p50, p95 };
};

// -------------------------------------------------------------------------------- the arithmetic

/** A payload decimal as printed: at most `digits` decimals, trailing zeros dropped, a real minus. */
export const plainNumber = (value, digits = 4) => {
  const n = num(value);
  if (n == null) return null;
  const text = Number(n.toFixed(digits)).toString();
  return text.startsWith("-") ? `−${text.slice(1)}` : text;
};

const COEFFICIENT_DIGITS = 4;

/**
 * The live HAR-IV forecast by hand: median = 100 × exp(b0 + b1 ln(rv5/100) + b2 ln(rv22/100) +
 * b3 ln(rv66/100) + b4 ln(ovx)) and mean = median × sqrt(smearing), as backend/vol_forecast.py
 * computes them (realized vols in decimals, OVX in index points). `median`/`mean` use the payload's
 * precision; `printed` repeats the arithmetic with the numbers as printed, so a reader with a
 * calculator gets the same answer. Null unless five coefficients and every input are usable.
 */
export const forecastDecomposition = (coefs, inputs, smearing) => {
  const b = asArray(coefs).map(num);
  const x = [num(inputs?.rv5_pct), num(inputs?.rv22_pct), num(inputs?.rv66_pct), num(inputs?.ovx)];
  const smear = num(smearing);
  if (b.length !== 5 || b.some((value) => value == null) || x.some((value) => value == null || value <= 0) || smear == null || smear <= 0) {
    return null;
  }
  const values = [x[0] / 100, x[1] / 100, x[2] / 100, x[3]];
  const median = 100 * Math.exp(b[0] + values.reduce((sum, value, k) => sum + b[k + 1] * Math.log(value), 0));
  const round = (value) => Number(value.toFixed(COEFFICIENT_DIGITS));
  const printedMedian = 100 * Math.exp(round(b[0])) * values.reduce((product, value, k) => product * value ** round(b[k + 1]), 1);
  return {
    median,
    mean: median * Math.sqrt(smear),
    printedMedian,
    smearing: smear,
    intercept: plainNumber(b[0], COEFFICIENT_DIGITS),
    factors: values.map((value, k) => ({
      base: k < 3 ? plainNumber(value, 4) : plainNumber(value, 2),
      exponent: plainNumber(b[k + 1], COEFFICIENT_DIGITS),
    })),
  };
};

/**
 * Whether a decomposition reproduces the displayed median and expected forecast (one decimal),
 * both at the payload's precision and with the printed numbers (expected = printed median ×
 * √smearing), each within TOLERANCE.
 */
export const reproduces = (decomposition, median, mean) => {
  if (!decomposition || median == null || mean == null) return false;
  const printedMean = Number(median.toFixed(1)) * Math.sqrt(decomposition.smearing);
  return close(decomposition.median, median) && close(decomposition.mean, mean)
    && close(decomposition.printedMedian, median) && close(printedMean, mean);
};

/**
 * The forecast's elasticities (log-log coefficients of the current fit), OVX first:
 * [{label, value}] or null unless all five coefficients are finite.
 */
export const elasticities = (coefs) => {
  const b = asArray(coefs).map(num);
  if (b.length !== 5 || b.some((value) => value == null)) return null;
  return [
    { label: "OVX", value: b[4] },
    { label: "5-day RV", value: b[1] },
    { label: "22-day", value: b[2] },
    { label: "66-day", value: b[3] },
  ];
};

// ---------------------------------------------------------------------------------- the notes

/**
 * The challenger's standing in pieces, for the hero's note: the every-day DM comparison with the
 * model of record, the search it was chosen from, the Hansen SPA reading (the same rule as
 * spaAssertion()) and the live promotion test. Missing data leaves its piece null.
 */
export const challengerStanding = (lab, promotion) => {
  const rows = leaderboardRows(lab);
  const record = rows.find((row) => row.role === "record") ?? null;
  const row = rows.find((item) => item.role === "challenger") ?? null;
  const recordName = record ? shortLabel(record.label) : "the model of record";
  let comparison = null;
  if (row?.dmStat != null && row.dmP != null) {
    const t = fmtSigned(row.dmStat, 2);
    if (row.dmP >= ALPHA) comparison = `mean QLIKE not distinguishable from ${recordName}'s over all out-of-sample days (DM ${t}, ${fmtP(row.dmP)})`;
    else comparison = `${row.dmStat > 0 ? "lower" : "higher"} mean QLIKE than ${recordName} over all out-of-sample days (DM ${t})`;
  }
  const spa = asObject(lab?.leaderboard?.spa_qlike_vs_har_iv);
  const alternatives = asArray(spa?.models).length;
  const p = num(spa?.p_consistent);
  let spaReading = null;
  if (p != null) {
    const level = (alpha) => `${fmtNum(alpha * 100, 0)}% level`;
    // Three decimals near the thresholds this reading turns on (0.072, not 0.07).
    const pSpa = p < 0.001 ? fmtP(p) : `p${NBSP}=${NBSP}${fmtPValue(p, { digits: 3 })}`;
    if (p < ALPHA) spaReading = { text: `Hansen SPA ${pSpa}: some alternative beats ${recordName} at the ${level(ALPHA)}`, keepsRecord: false };
    else if (p < ALPHA_LOOSE) spaReading = { text: `Hansen SPA ${pSpa} is inconclusive`, keepsRecord: true };
    else spaReading = { text: `Hansen SPA ${pSpa} finds no alternative better than ${recordName} at the ${level(ALPHA_LOOSE)}`, keepsRecord: true };
  }
  const paired = num(promotion?.paired);
  const required = num(promotion?.required);
  return {
    name: row ? shortLabel(row.label) : null,
    recordName,
    comparison,
    search: alternatives ? `chosen from a search over ${fmtInt(alternatives)} alternatives` : null,
    spa: spaReading,
    test: required != null ? { required, paired, status: promotion?.status ?? null } : null,
  };
};
