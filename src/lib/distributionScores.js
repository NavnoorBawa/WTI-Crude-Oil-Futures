// The whole predictive distribution (ROADMAP 2.4), pure, the part that loads on demand with the lazy chunk of
// charts/distributionCharts.jsx: MODL's distribution view (each score's reading against the model of record at the
// strength its test supports, the challenger's, the view's footnote and the robustness rerun with the model outside
// the field) and the rows of CALB's table view. Every number comes from the payload; the wording follows the band
// its p falls in ("significant" below 0.05, "inconclusive" from 0.05 to 0.10).
import { NBSP, fmtInt, fmtNum, fmtSigned, num } from "./format";
import { asArray, asObject, asText, mcsLevel } from "./research";
import {
  ALPHA, band, capitalize, distributionBlock, distributionModels, join, p3, pct, pEq, pitRows, proseName, referenceWords, span,
  years,
} from "./distribution";

/** The three registered scores, in the table's order: payload key, ledger id prefix, name in a sentence. */
export const DIST_SCORES = [
  { key: "crps_grid", tag: "crps", name: "CRPS", phrase: "the CRPS" },
  { key: "pinball_10", tag: "pinball10", name: "Pinball 10%", phrase: "the 10% pinball loss" },
  { key: "pinball_90", tag: "pinball90", name: "Pinball 90%", phrase: "the 90% pinball loss" },
];

/** MODL's table rows: each scored model's three scores with the batch-means t, p and periods, and Berkowitz's p. */
export const distributionScoreRows = (lab) => distributionModels(lab).map((row) => ({
  ...row,
  tests: Object.fromEntries(DIST_SCORES.map((score) => {
    const test = asObject(row.dm?.[score.key]);
    return [score.key, test ? {
      t: num(test.stat), p: num(test.p_value), lower: num(test.periods_model_lower), groups: num(test.groups),
    } : null];
  })),
  berkowitzP: num(row.berkowitz.p_value),
}));

const testOf = (row, score) => asObject(row.dm?.[score]);

/** The ledger's survival of a group of significant comparisons, worded for the group. */
const survival = (group, ledger, tag) => {
  const qs = group.map((row) => num(ledger?.byId?.get(`vol_${tag}_${row.key}`)?.q));
  if (qs.some((q) => q == null)) return "";
  const kept = group.filter((_, index) => qs[index] < ALPHA);
  const list = qs.map((q) => p3(q)).join(", ");
  if (!kept.length) return `, ${group.length === 1 ? "not" : group.length === 2 ? "neither" : "none"} after the ledger's adjustment (q${NBSP}=${NBSP}${list})`;
  if (kept.length === group.length) return `, ${group.length === 1 ? "surviving" : "each surviving"} the ledger's adjustment (q${NBSP}=${NBSP}${list})`;
  return `; only ${join(kept.map(proseName))} ${kept.length === 1 ? "survives" : "survive"} the ledger's adjustment (q${NBSP}=${NBSP}${list})`;
};

/**
 * One score's reading against the model of record, at the strength each test supports: significant either way
 * with the ledger's survival, inconclusive either way, and no significant difference. The challenger is read on
 * its own (challengerReading). For the CRPS, where the record's lower score comes from (the periods it was
 * lower in, 2020's share and the largest days' share), for the models the published reading names.
 */
export const scoreReading = (lab, ledger, score) => {
  const rows = distributionModels(lab);
  const record = rows.find((row) => row.role === "record");
  const tests = rows.filter((row) => row.role !== "record" && row.role !== "challenger"
    && num(testOf(row, score.key)?.p_value) != null && num(testOf(row, score.key)?.stat) != null);
  if (!record || !tests.length) return null;
  const R = record.label;
  const p = (row) => num(testOf(row, score.key).p_value);
  const t = (row) => num(testOf(row, score.key).stat);
  const withP = (group) => join(group.map((row) => `${proseName(row)} (${pEq(p(row))})`));
  const sig = (sign) => tests.filter((row) => band(p(row)) === 0 && Math.sign(t(row)) === sign);
  const mid = (sign) => tests.filter((row) => band(p(row)) === 1 && Math.sign(t(row)) === sign);
  const rest = tests.filter((row) => band(p(row)) === 2 || t(row) === 0);
  const clauses = [];
  if (sig(-1).length) clauses.push(`${R} scores significantly lower than ${withP(sig(-1))}${survival(sig(-1), ledger, score.tag)}`);
  if (sig(1).length) {
    clauses.push(`${withP(sig(1))} ${sig(1).length === 1 ? "scores" : "score"} significantly lower than ${R}`
      + survival(sig(1), ledger, score.tag));
  }
  const unclear = [
    mid(-1).length ? `${withP(mid(-1))}, where ${R} scores lower` : null,
    mid(1).length ? `${withP(mid(1))}, which ${mid(1).length === 1 ? "scores" : "score"} lower than ${R}` : null,
  ].filter(Boolean);
  if (unclear.length) clauses.push(`the difference is inconclusive against ${unclear.join(", and ")}`);
  if (rest.length) {
    const floor = rest.length === 1 ? pEq(p(rest[0])) : `p${NBSP}≥${NBSP}${p3(Math.min(...rest.map(p)))}`;
    clauses.push(clauses.length ? `no significant difference from ${join(rest.map(proseName), "or")} (${floor})`
      : `no significant difference from any of the ${fmtInt(rest.length)} models (${floor})`);
  }
  let text = `On ${score.phrase}, ${clauses.join("; ")}.`;
  if (score.key === "crps_grid") {
    // As RESULTS 2.9 reads it: the models the record scores lower than, short of p = 0.20 (a total near zero makes
    // the shares meaningless).
    const behind = tests.filter((row) => t(row) < 0 && p(row) < 0.2)
      .map((row) => ({ row, test: testOf(row, score.key) }))
      .filter(({ test }) => asObject(test.concentration));
    if (behind.length) {
      const groups = num(testOf(behind[0].row, score.key).groups);
      text += ` Against ${join(behind.map(({ row }) => proseName(row)))}, ${R} scored the lower CRPS in `
        + `${span(behind.map(({ test }) => num(test.periods_har_iv_lower)))}${groups != null ? ` of ${fmtInt(groups)}` : ""} periods, `
        + `but 2020 carries ${span(behind.map(({ test }) => num(test.concentration.share_2020_pct)), 0, "%")} of each total `
        + `difference, and the 1% of days with the largest differences ${span(behind.map(({ test }) => num(test.concentration.share_largest_1pct_days_pct)), 0, "%")}.`;
    }
  }
  return text;
};

/**
 * The challenger's scores against the model of record, with its caveat: picked on this sample, so not new evidence,
 * and (when the ledger says so) outside the ledger's adjusted family.
 */
export const challengerReading = (lab, ledger) => {
  const rows = distributionModels(lab);
  const record = rows.find((row) => row.role === "record");
  const challenger = rows.find((row) => row.role === "challenger");
  if (!record || !challenger?.dm) return null;
  const tests = DIST_SCORES.map((score) => ({ score, test: testOf(challenger, score.key) }))
    .filter(({ test }) => num(test?.p_value) != null && num(test?.stat) != null);
  if (!tests.length) return null;
  const outside = DIST_SCORES.every((score) => ledger?.byId?.get(`vol_${score.tag}_${challenger.key}`)?.selectedAfterSearch === true);
  // The challenger is named as such once, then by its label.
  let named = false;
  const who = () => {
    const text = named ? challenger.label : `the challenger ${challenger.label}`;
    named = true;
    return text;
  };
  const parts = tests.filter(({ test }) => band(num(test.p_value)) < 2 && num(test.stat) !== 0).map(({ score, test }) => {
    const ahead = num(test.stat) > 0;
    const periods = ahead ? num(test.periods_model_lower) : num(test.periods_har_iv_lower);
    const numbers = (lower) => `t${NBSP}=${NBSP}${fmtSigned(num(test.stat), 2)}, ${pEq(num(test.p_value))}, ${lower}lower in `
      + `${fmtInt(periods)} of ${fmtInt(num(test.groups))} periods`;
    if (band(num(test.p_value)) === 1) {
      const name = who();
      return `on ${score.phrase}, the difference between ${name} and ${record.label} is inconclusive (${numbers(`${ahead ? challenger.label : record.label} `)})`;
    }
    return ahead ? `on ${score.phrase}, ${who()} scores significantly lower than ${record.label} (${numbers("")})`
      : `on ${score.phrase}, ${record.label} scores significantly lower than ${who()} (${numbers("")})`;
  });
  const rest = tests.filter(({ test }) => band(num(test.p_value)) === 2 || num(test.stat) === 0);
  if (rest.length) {
    parts.push(`on ${join(rest.map(({ score }) => score.phrase))}, ${who()} does not differ significantly from ${record.label} (`
      + `${rest.map(({ test }) => pEq(num(test.p_value))).join(", ")})`);
  }
  return `${capitalize(parts.join("; "))}. It was picked as the challenger on this sample, so this is not new evidence`
    + `${outside ? ", and its tests are outside the ledger's adjusted family" : ""}.`;
};

/**
 * The view's footnote: QLIKE stays the ranking, on another sample; the scale and sign; the batch-means test and
 * why; what the CRPS and the two pinball losses are; how many leaderboard models have no distribution here.
 */
export const distributionFootnote = (lab) => {
  const block = distributionBlock(lab);
  if (!block) return null;
  const cfg = asObject(block.config) ?? {};
  const rows = distributionModels(lab);
  const record = rows.find((row) => row.role === "record");
  const board = asArray(lab?.leaderboard?.models).map((model) => asText(model?.key)).filter(Boolean);
  const scored = new Set(rows.map((row) => row.key));
  const missing = board.filter((key) => !scored.has(key)).length;
  const taus = asArray(cfg.taus).map(num).filter((value) => value != null);
  const [lo, hi] = asArray(cfg.pinball_taus).map(num);
  const groups = num(cfg.dm?.groups);
  const ratio = num(cfg.gaussian_ratio_k19);
  const qlikeDays = num(lab?.leaderboard?.n_days);
  const n = num(block.n_days);
  const sample = years(block.first, block.last);
  const parts = [
    `QLIKE remains the ranking${qlikeDays != null ? ` (every out-of-sample day, ${fmtInt(qlikeDays)})` : ""}; these scores use the `
      + `${n != null ? `${fmtInt(n)} ` : ""}days the band is scored on${sample ? ` (${sample})` : ""}, so the two are not compared.`,
    `Scores in vol points, lower is better; a positive t means a lower score than ${record?.label ?? "the model of record"}.`,
    groups != null ? `Each test compares ${fmtInt(groups)} consecutive periods (batch means, t with ${fmtInt(groups - 1)} degrees of `
      + "freedom), because the daily score differences stay correlated for weeks." : null,
    taus.length ? `The CRPS is the mean of ${fmtInt(taus.length)} quantile scores (τ${NBSP}=${NBSP}${fmtNum(taus[0], 2)} to `
      + `${fmtNum(taus.at(-1), 2)})${ratio != null ? `; under a correct normal forecast it reads ${fmtNum((ratio - 1) * 100, 1)}% above the full CRPS` : ""}.` : null,
    lo != null && hi != null && hi > lo ? `${fmtNum(2 / (1 - (hi - lo)), 0)} × (pinball ${pct(lo)} + pinball ${pct(hi)}) is the `
      + `interval score of the ${pct(lo)} to ${pct(hi)} quantiles.` : null,
    board.length && missing > 0 ? `${fmtInt(missing)} of the ${fmtInt(board.length)} leaderboard models have no predictive distribution here.` : null,
  ];
  return parts.filter(Boolean).join(" ");
};

const SUBPERIOD = /^mcs_(\d{4})_(\d{4})$/;
const BLOCKS = /^mcs_block_(\d+)$/;

/**
 * The robustness rerun with the outside model's point forecast added to the field, from quantile_har_field: the
 * headline set's verdict on it, every other setting in or out with its p, the other point forecast, the SPA p
 * against the leaderboard's and its QLIKE test against the model of record. Null without the block.
 */
export const rerunFootnote = (lab) => {
  const block = distributionBlock(lab);
  const field = asObject(block?.quantile_har_field);
  const rows = distributionModels(lab);
  const outsider = rows.find((row) => row.tag);
  const record = rows.find((row) => row.role === "record");
  const mcs = asObject(field?.mcs);
  if (!field || !outsider || !mcs) return null;
  const key = outsider.key;
  const name = proseName(outsider);
  const level = mcsLevel(lab);
  const set = `${level != null ? `${level}% ` : ""}Model Confidence Set`;
  const included = asArray(mcs.included);
  const inHead = included.includes(key);
  const others = included.filter((model) => model !== key);
  const board = new Set(asArray(lab?.leaderboard?.mcs_qlike_included));
  const same = board.size === others.length && others.every((model) => board.has(model));
  const count = `${same ? "the same " : ""}${fmtInt(others.length)} ${inHead ? "other " : ""}model${others.length === 1 ? "" : "s"}`;
  const headP = num(mcs.p_values?.[key]);
  const pText = (value) => (value != null ? ` (${pEq(value)})` : "");
  const mcsP = headP != null ? ` (MCS ${pEq(headP)})` : "";
  const head = inHead ? `the ${set} includes it${mcsP}, with ${count}` : `the ${set} keeps ${count} and leaves it out${mcsP}`;
  const inside = [];
  const outside = [];
  const place = (inSet, text) => (inSet ? inside : outside).push(text);
  for (const [k, value] of Object.entries(field).filter(([k]) => BLOCKS.test(k)).sort(([a], [b]) => Number(BLOCKS.exec(a)[1]) - Number(BLOCKS.exec(b)[1]))) {
    place(asArray(value?.included).includes(key), `with ${BLOCKS.exec(k)[1]}-day blocks${pText(num(value?.p_quantile_har))}`);
  }
  const byGrid = Object.values(asObject(field.mcs_by_grid) ?? {});
  const gridsIn = byGrid.filter((grid) => asArray(grid?.included).includes(key)).length;
  if (gridsIn) place(true, gridsIn === byGrid.length ? `on each of the ${fmtInt(byGrid.length)} sampling grids` : `on ${fmtInt(gridsIn)} of ${fmtInt(byGrid.length)} sampling grids`);
  if (gridsIn < byGrid.length) place(false, `on ${fmtInt(byGrid.length - gridsIn)} of ${fmtInt(byGrid.length)} sampling grids`);
  for (const [k, value] of Object.entries(field).filter(([k]) => SUBPERIOD.test(k)).sort(([a], [b]) => a.localeCompare(b))) {
    const [, a, b] = SUBPERIOD.exec(k);
    place(asArray(value?.included).includes(key), `in ${a}–${b}${pText(num(value?.p_quantile_har))}`);
  }
  const settings = [
    inside.length ? `it is included ${join(inside)}` : null,
    outside.length ? `${inside.length ? "and left out" : "it is left out"} ${join(outside)}` : null,
  ].filter(Boolean).join(", ");
  const alt = asObject(field.alt_point_forecast);
  const taus = asArray(block.config?.taus).length;
  const altText = alt && typeof alt.mcs_included === "boolean"
    ? `With the root mean square of its${taus ? ` ${fmtInt(taus)}` : ""} quantiles as the point forecast, it is `
      + `${alt.mcs_included ? "included" : "left out"} at the headline settings${pText(num(alt.mcs_p))}.`
    : null;
  const spaNew = num(field.spa?.p_consistent);
  const spaOld = num(lab?.leaderboard?.spa_qlike_vs_har_iv?.p_consistent);
  const spaText = spaNew == null ? null : spaOld != null && p3(spaOld) === p3(spaNew)
    ? `The SPA p stays ${p3(spaNew)}.` : `The SPA p is ${p3(spaNew)}${spaOld != null ? ` (${p3(spaOld)} without it)` : ""}.`;
  const qlikeP = num(field.dm_qlike_vs_har_iv?.batch_means?.p_value);
  const recordName = record?.label ?? "the model of record";
  const qlikeText = qlikeP == null ? null : [
    `Its QLIKE differs significantly from ${recordName}'s (batch-means ${pEq(qlikeP)}).`,
    `Its QLIKE difference to ${recordName} is inconclusive (batch-means ${pEq(qlikeP)}).`,
    `Its QLIKE does not differ significantly from ${recordName}'s (batch-means ${pEq(qlikeP)}).`,
  ][band(qlikeP)];
  return [
    `A robustness rerun with ${name}'s point forecast added to the field, on every out-of-sample day: at the headline `
      + `settings ${head}${settings ? `; ${settings}` : ""}.`,
    altText, spaText, qlikeText,
    `The rerun selects nothing: ${name} is never eligible for the live pair.`,
  ].filter(Boolean).map(capitalize).join(" ");
};

// ------------------------------------------------------------------------------------------ CALB's tables


/** CALB's PIT table rows: each model's ten shares with the chance range of each bin, and its 10-90% coverage. */
/**
 * CALB's PIT table rows: each model's ten shares, then one row per distinct chance range (the simulated mean ± the
 * band of each bin, shared by the models whose PIT is built the same way), naming the models it holds for.
 */
export const pitTableRows = (rows) => {
  const drawn = pitRows(rows);
  const ranges = [];
  for (const row of drawn) {
    const bins = row.centers.map((center, index) => ({ lo: Math.max(0, center - row.bands[index]), hi: center + row.bands[index] }));
    const text = bins.map((bin) => `${fmtNum(bin.lo, 1)}–${fmtNum(bin.hi, 1)}`).join(" ");
    const same = ranges.find((range) => range.text === text);
    if (same) same.models.push(row);
    else ranges.push({ text, bins, models: [row] });
  }
  return [
    ...drawn.map((row) => ({ ...row, bins: row.shares.map((share) => ({ share })) })),
    ...ranges.map((range, index) => ({
      key: `chance-${index}`, chance: true, label: `By chance, for ${join(range.models.map(proseName))}`, tag: null, bins: range.bins,
    })),
  ];
};

/** CALB's Berkowitz table rows: per grid LR and p, the reference, the Bonferroni p and its context, the ledger's q. */
export const berkowitzTableRows = (rows, ledger) => rows.filter((row) => num(row.berkowitz.p_value) != null).map((row) => {
  const b = row.berkowitz;
  return {
    ...row,
    reference: referenceWords(b.reference),
    byGrid: asArray(b.by_grid).map((grid) => ({ grid: num(grid?.grid), lr: num(grid?.lr), p: num(grid?.p_value), weeks: num(grid?.n_weeks) })),
    p: num(b.p_value), mcSe: num(b.mc_se), pChi2: num(b.p_chi2),
    randomized: num(b.randomized_tail?.p_value),
    q: num(ledger?.byId?.get(`vol_berkowitz_${row.key}`)?.q),
  };
});

