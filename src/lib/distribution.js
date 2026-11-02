// The whole predictive distribution (ROADMAP 2.4), pure: the scored models in the page's order, CALB's PIT figure
// title, its Berkowitz summary, chance note and method notes, read from vol_lab.distribution, the leaderboard and the
// ledger. What only MODL's opt-in view and CALB's table view read is in ./distributionScores.js, which loads with
// their lazy chunk. Every number comes from the payload; each sentence is worded by the band its test's p falls in
// ("rejects" or "significant" below 0.05, "inconclusive" from 0.05 to 0.10), never more strongly. The thresholds are
// local so this module needs nothing from lib/assertions.js.
import { NBSP, fmtInt, fmtNum, fmtPValue, num } from "./format";
import { OVX_ONLY_KEY, asArray, asObject, asText, getRoles, shortLabel } from "./research";

export const ALPHA = 0.05;
export const INCONCLUSIVE = 0.10;

/** MODL's view of the distribution scores (the URL's modl value). */
export const DIST_VIEW = "dist";
/** The tag of a scored model that is not in the leaderboard's field. */
export const NOT_IN_BENCHMARK = "not in the benchmark";

/** 0 below 0.05, 1 from 0.05 to 0.10, 2 from 0.10: the band a p-value's wording follows. */
export const band = (p) => (p < ALPHA ? 0 : p < INCONCLUSIVE ? 1 : 2);
export const p3 = (value) => fmtPValue(value, { digits: 3 });
const stat = (name, value) => {
  const text = p3(value);
  return /^[<>]/.test(text) ? `${name}${NBSP}${text}` : `${name}${NBSP}=${NBSP}${text}`;
};
export const pEq = (value) => stat("p", value);
export const qEq = (value) => stat("q", value);
export const join = (items, word = "and") => (items.length > 1 ? `${items.slice(0, -1).join(", ")} ${word} ${items.at(-1)}` : items[0] ?? "");
export const capitalize = (text) => (text ? `${text[0].toUpperCase()}${text.slice(1)}` : text);
export const span = (values, digits = 0, suffix = "") => {
  const shown = values.filter((value) => value != null);
  if (!shown.length) return "";
  const lo = fmtNum(Math.min(...shown), digits);
  const hi = fmtNum(Math.max(...shown), digits);
  return lo === hi ? `${lo}${suffix}` : `${lo}${suffix} to ${hi}${suffix}`;
};
export const pct = (tau) => `${fmtNum(num(tau) * 100, 0)}%`;
export const years = (first, last) => {
  const a = asText(first)?.slice(0, 4);
  const b = asText(last)?.slice(0, 4);
  return a && b ? (a === b ? a : `${a}–${b}`) : null;
};

/** vol_lab.distribution when it holds a scored model, else null. */
export const distributionBlock = (lab) => {
  const block = asObject(lab?.distribution);
  return block && asArray(block.models).some((model) => asText(model?.key)) ? block : null;
};

/** Whether the payload carries the distribution scores (CALB's figure and MODL's view need them). */
export const hasDistribution = (lab) => distributionBlock(lab) != null;

// The payload appends a role to a label ("(model of record)", "(not in the benchmark)"); the page shows it as a chip.
const TAGGED = /\s*\((?:model of record|not in the benchmark)\)\s*$/i;

/**
 * The scored models in the page's order: the model of record, the challenger, the rest as published, and a model
 * outside the leaderboard's field last, tagged "not in the benchmark". Each with its tone, PIT shares and chance
 * band, scores, Berkowitz block and tests against the model of record.
 */
export const distributionModels = (lab) => {
  const block = distributionBlock(lab);
  if (!block) return [];
  const { champion, challenger } = getRoles(lab, null);
  const field = new Set(asArray(lab?.leaderboard?.models).map((model) => asText(model?.key)).filter(Boolean));
  const rank = (row) => (row.role === "record" ? 0 : row.role === "challenger" ? 1 : row.tag ? 3 : 2);
  return asArray(block.models).filter((model) => asText(model?.key)).map((model, index) => {
    const role = model.key === champion ? "record" : model.key === challenger ? "challenger" : null;
    const label = shortLabel(asText(model.label) ?? model.key).replace(TAGGED, "");
    return {
      key: model.key, index, role, label,
      tag: field.size && !field.has(model.key) ? NOT_IN_BENCHMARK : null,
      tone: role === "record" ? "model" : role === "challenger" ? "alt" : "neutral",
      construction: asText(model.pit_construction), method: asText(model.method),
      shares: asArray(model.pit_decile_share_pct).map(num),
      centers: asArray(model.pit_chance_center_pct).map(num),
      bands: asArray(model.pit_chance_band_pct).map(num),
      crps: num(model.crps_grid_pct), crpsLog: num(model.crps_grid_log),
      pinball10: num(model.pinball_10_pct), pinball90: num(model.pinball_90_pct),
      coverage: num(model.coverage_10_90_pct),
      berkowitz: asObject(model.berkowitz) ?? {},
      dm: asObject(model.dm_vs_har_iv),
    };
  }).sort((a, b) => rank(a) - rank(b) || a.index - b.index);
};

/** The rows the PIT figure draws: ten shares, ten chance means and ten chance half-widths each. */
export const pitRows = (rows) => rows.filter((row) => row.shares.length > 0 && row.shares.every((value) => value != null)
  && row.centers.length === row.shares.length && row.bands.length === row.shares.length);

/** A model's name in a sentence ("the OVX-only regression", "the quantile HAR-IV"). */
export const proseName = (row) => {
  if (row.key === OVX_ONLY_KEY) return `the ${row.label}`;
  if (row.tag && /^[A-Z][a-z]/.test(row.label)) return `the ${row.label[0].toLowerCase()}${row.label.slice(1)}`;
  return row.label;
};

/** CALB's h3 over the PIT figure: one claim, naming its sample. */
export const pitTitle = (lab) => {
  const block = distributionBlock(lab);
  const n = num(block?.n_days);
  const range = years(block?.first, block?.last);
  return `PIT histogram by model${n != null ? `, ${fmtInt(n)} out-of-sample days` : ""}${range ? ` (${range})` : ""}`;
};

/** The days every distribution score uses, as a table caption names them ("4,370 out-of-sample days with a band (2009–2026)"). */
export const distributionSample = (lab) => {
  const block = distributionBlock(lab);
  const n = num(block?.n_days);
  const range = years(block?.first, block?.last);
  return `${n != null ? `${fmtInt(n)} ` : ""}out-of-sample days with a band${range ? ` (${range})` : ""}`;
};

/** Berkowitz's verdict at the strength its p supports. */
export const berkowitzVerdict = (p) => (p == null ? null : ["rejects", "inconclusive", "does not reject"][band(p)]);

export const grids = (lab) => asArray(distributionBlock(lab)?.weeks_per_grid).map(num).filter((value) => value != null);

/**
 * What qualifies one model's Berkowitz p in the summary: the censored form and the randomized-tail check, a
 * rejection resting on fewer than half the grids, and a rejection that does not survive the ledger's adjustment.
 */
const berkowitzDetails = (row, { ledgerRow, gridCount, taus }) => {
  const b = row.berkowitz;
  const p = num(b.p_value);
  const parts = [];
  const below = num(b.grids_below_5pct);
  if (band(p) === 0 && below != null && gridCount && below < gridCount / 2) parts.push(`below 5% on ${fmtInt(below)} of ${fmtInt(gridCount)} grids`);
  if (b.registered === "censored") {
    parts.push(`in a censored form that tests the distribution between its ${taus.length ? pct(taus[0]) : "lowest"} and `
      + `${taus.length ? pct(taus.at(-1)) : "highest"} quantiles and the two tail masses, not independence`);
  }
  const head = [pEq(p), ...parts].join(", ");
  const tail = [];
  const randomized = num(b.randomized_tail?.p_value);
  if (randomized != null) tail.push(`a randomized-tail form gives ${pEq(randomized)}`);
  const q = num(ledgerRow?.q);
  if (band(p) === 0 && q != null && q >= ALPHA) tail.push(`not after the ledger's adjustment, ${qEq(q)}`);
  return { text: [head, ...tail].join("; "), plain: !parts.length && !tail.length };
};

/**
 * CALB's summary of Berkowitz's test, clause by clause: the models it rejects at 5% (with what qualifies each
 * rejection), those it is inconclusive for and those it does not reject, each with its p. Null without the block.
 */
export const berkowitzSummary = (lab, ledger) => {
  const rows = distributionModels(lab).filter((row) => num(row.berkowitz.p_value) != null);
  if (!rows.length) return null;
  const gridCount = grids(lab).length;
  const weeks = span(grids(lab));
  const taus = asArray(distributionBlock(lab)?.config?.taus).map(num).filter((value) => value != null);
  const item = (row, withP) => {
    const details = berkowitzDetails(row, { ledgerRow: ledger?.byId?.get(`vol_berkowitz_${row.key}`), gridCount, taus });
    return withP || !details.plain ? `${proseName(row)} (${details.text})` : proseName(row);
  };
  const p = (row) => num(row.berkowitz.p_value);
  const rejected = rows.filter((row) => band(p(row)) === 0);
  const mid = rows.filter((row) => band(p(row)) === 1);
  const quiet = rows.filter((row) => band(p(row)) === 2);
  const where = gridCount ? `on each of the ${fmtInt(gridCount)} sampling grids'${weeks ? ` ${weeks}` : ""} non-overlapping weeks` : "on non-overlapping weeks";
  const sentences = [rejected.length
    ? `Berkowitz's test, ${where}, rejects at 5% ${join(rejected.map((row) => item(row, false)))}.`
    : `Berkowitz's test, ${where}, rejects none of the ${fmtInt(rows.length)} models at 5%.`];
  const rest = [];
  if (mid.length) rest.push(`is inconclusive for ${join(mid.map((row) => item(row, true)))}`);
  if (quiet.length) rest.push(`does not reject ${join(quiet.map((row) => item(row, true)), "or")}`);
  const between = mid.length > 1 || quiet.length > 1 ? ", and " : " and ";
  if (rest.length) sentences.push(`It ${rest.join(between)}${quiet.length ? "; a failure to reject is not evidence of calibration" : ""}.`);
  return sentences.join(" ");
};

const CONSTRUCTION_WORDS = {
  trailing_rank: "a rank among trailing residuals",
  exact: "read from the model's own distribution",
  estimated_quantiles: "interpolated between estimated quantiles",
};

/**
 * The note under the PIT figure: how far a bin's share moves by chance under a correct forecast, from each
 * model's simulated band (pit_chance_band_pct around pit_chance_center_pct), grouped where the bands agree.
 */
export const chanceNote = (rows) => {
  const drawn = pitRows(rows);
  if (!drawn.length) return null;
  const groups = [];
  for (const row of drawn) {
    const max = fmtNum(Math.max(...row.bands), 1);
    const centers = span(row.centers, 1, "%");
    const group = groups.find((other) => other.max === max && other.centers === centers);
    if (group) {
      group.rows.push(row);
      group.constructions.add(row.construction);
    } else {
      groups.push({ max, centers, rows: [row], constructions: new Set([row.construction]) });
    }
  }
  const text = groups.map((group, index) => {
    const others = groups.length > 1 && index === groups.length - 1;
    const one = group.constructions.size === 1 ? CONSTRUCTION_WORDS[[...group.constructions][0]] : null;
    const who = others ? "the others" : join(group.rows.map(proseName));
    const how = !others && one ? `, whose PIT is ${one}` : "";
    return `up to ±${group.max} points around ${group.centers} for ${who}${how}`;
  });
  return "Shaded: how far each bin's share moves by chance under a correct forecast (±1.96 standard deviations across "
    + `simulated draws, around their mean), for each way the PIT is built: ${text.join("; ")}. The dashed line is `
    + `${fmtNum(100 / drawn[0].shares.length, 0)}%, one bin's share of the days. No single bin is tested.`;
};

// ------------------------------------------------------------------------------------------ CALB's notes

/** A Berkowitz reference as the page writes it ("chi2(3)" -> "χ²(3)", "simulated null" -> "simulated"). */
export const referenceWords = (text) => (asText(text) ?? "").replace(/chi2\((\d+)\)/g, "χ²($1)").replace(/^simulated null$/, "simulated");

/** The bins' edges as text ("0.0–0.1"). */
export const binLabel = (index, count = 10) => `${fmtNum(index / count, 1)}–${fmtNum((index + 1) / count, 1)}`;

/**
 * CALB's method notes on the distributions: how each is built (the payload's own words, grouped), why Berkowitz
 * runs on each grid, why some models need a simulated null (and what that null rejects), and that these quantiles
 * are not the band.
 */
export const distributionNotes = (lab) => {
  const block = distributionBlock(lab);
  if (!block) return [];
  const cfg = asObject(block.config) ?? {};
  const rows = distributionModels(lab);
  const taus = asArray(cfg.taus).map(num).filter((value) => value != null);
  const methods = [];
  for (const row of rows) {
    const group = methods.find((item) => item.method === row.method && item.construction === row.construction);
    if (group) group.rows.push(row);
    else methods.push({ method: row.method, construction: row.construction, rows: [row] });
  }
  // The payload's own words for each method, with the settings it names (the residual window, the simulation's draws).
  const settings = (group) => {
    if (group.construction === "trailing_rank" && num(cfg.residual_window_days) != null) {
      return ` (${fmtInt(cfg.residual_window_days)} days${num(cfg.delay_sessions) != null ? `, ${fmtInt(cfg.delay_sessions)}-session delay` : ""})`;
    }
    return /simulated/.test(group.method) && num(cfg.garch?.draws) != null ? ` (${fmtInt(cfg.garch.draws)} draws)` : "";
  };
  // The payload spells a formula in ASCII ("sigma sqrt(chi2_5 / 5)"); the page writes it in symbols.
  const words = (method) => method.replace(/\bsigma\b/g, "σ").replace(/sqrt\(chi2_(\d+) \/ (\d+)\)/g, "√(χ²($1) / $2)");
  const built = methods.filter((group) => group.method)
    .map((group) => `${join(group.rows.map(proseName))}${group.rows.some((row) => row.tag) ? ` (${NOT_IN_BENCHMARK})` : ""}: `
      + `${words(group.method)}${settings(group)}`);
  const gridCount = grids(lab).length;
  const null_ = asObject(cfg.berkowitz?.null);
  const size = asObject(cfg.berkowitz?.null_size_pct);
  const trailing = rows.filter((row) => row.construction === "trailing_rank");
  const censored = rows.filter((row) => row.berkowitz.registered === "censored");
  const record = rows.find((row) => row.role === "record");
  const reference = asObject(cfg.berkowitz?.reference) ?? {};
  const parametric = referenceWords(reference.parametric) || "the χ² reference";
  const censoredRef = /χ²\(\d+\)/.exec(referenceWords(reference.quantile_har))?.[0];
  const horizon = num(lab?.config?.horizon_sessions);
  const [lo, hi] = asArray(cfg.pinball_taus).map(num);
  const notes = [
    `How each predictive distribution is built: its ${fmtInt(taus.length)} quantiles (τ${NBSP}=${NBSP}${fmtNum(taus[0], 2)} to `
      + `${fmtNum(taus.at(-1), 2)}) for each of the ${fmtInt(num(block.n_days))} days the band is scored on. ${capitalize(built.join("; "))}. `
      + "The PIT is where the outcome fell in that distribution; the figure shows the share of days between consecutive "
      + "predicted deciles, which is descriptive.",
    "Why Berkowitz's test runs on each sampling grid: it needs independent PITs, and each day's "
      + (horizon != null && horizon > 1 ? `${fmtInt(horizon)}-session target overlaps the next ${fmtInt(horizon - 1)} days' targets` : "target overlaps the next days' targets")
      + `, so it reads each grid's non-overlapping weeks and combines the ${fmtInt(gridCount)} grids by Bonferroni, `
      + "which holds under any dependence between them.",
    trailing.length && null_ ? `Why a simulated null: for ${join(trailing.map(proseName))} the PIT is a rank among a trailing window `
      + `of residuals, which re-centres itself, so ${parametric} is not its reference. Their test is referred to a simulated null `
      + `(${fmtInt(num(null_.draws))} seeded draws${asText(null_.returns) ? `; ${null_.returns}` : ""})`
      + (size && num(size.simulated) != null && num(size.chi2) != null
        ? `: it rejects ${fmtNum(size.simulated, 1)}% of its own draws at 5%, where ${parametric} would reject ${fmtNum(size.chi2, 2)}%` : "")
      + (record && num(record.berkowitz.mc_se) != null ? `; the Monte Carlo standard error of ${record.label}'s p is ${fmtNum(record.berkowitz.mc_se, 3)}` : "")
      + `. The other models are referred to ${parametric}${censored.length ? `, except ${join(censored.map(proseName))}, whose quantiles stop at `
        + `${pct(taus[0])} and ${pct(taus.at(-1))}: its test is censored there${censoredRef ? ` (${censoredRef})` : ""} and does not test independence` : ""}.` : null,
    record?.coverage != null && lo != null && hi != null ? `These quantiles are not the band above: they keep the band's residual window `
      + `without its adaptive step, and ${record.label}'s ${pct(lo)} and ${pct(hi)} quantiles hold ${fmtNum(record.coverage, 1)}% of outcomes between them.` : null,
  ];
  return notes.filter(Boolean);
};
