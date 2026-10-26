// Panel titles are assertions computed from the payload, never fixed copy: each builder states only
// what its numbers support and returns null when they are missing (the panel then falls back to a
// neutral title). A title makes one plain claim, short enough to scan (about 90 characters at
// most); the statistics behind it go in the meta line under the title, built by the matching
// *Detail function, so every figure stays computed from the payload. Every figure names its sample
// when two samples of the same statistic appear on the page.
import { NBSP, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtPValue, fmtSigned, num, ordinal } from "./format";
import { asArray, asObject, asText, coverageByYear, ovxOnlyBenchmark, recordVar, shortLabel, vrpSummary, yearsBetter } from "./research";

/** The nominal coverage of the contract's interval_80 fields (a design level, not a statistic). */
export const INTERVAL_LEVEL_PCT = 80;
/** Conventional significance level used in the wording ("significantly", "passes"). */
export const ALPHA = 0.05;
/** The looser conventional level an inconclusive p-value is read against. */
export const ALPHA_LOOSE = 0.1;

/** "HAR-IV (RV5,RV22,RV66,OVX)" -> "HAR-IV". */
export const shortModelName = (model) => {
  const text = asText(model);
  return text ? text.replace(/\s*\(.*\)\s*$/, "") || text : null;
};

const join = (parts) => parts.filter(Boolean).join("; ");
const capitalize = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : null);
const plural = (count, word) => `${fmtInt(count)} ${word}${count === 1 ? "" : "s"}`;
const pct = (value) => fmtNum(value * 100, 0);
/** "MCS p = 0.04" with no-break spaces, so a wrapped line never ends on "MCS". */
const labelledP = (label, p) => (fmtP(p) ? `${label}${NBSP}${fmtP(p)}` : null);

/**
 * FCST: the model against the weakest benchmark (persistence, on out-of-sample R²) and the
 * strongest simple one, the regression on OVX alone (Diebold-Mariano on QLIKE).
 */
export const forecastAssertion = (validation, live, { ovxLabel = "OVX-only regression" } = {}) => {
  const r2 = num(validation?.har_level_r2);
  const persistenceR2 = num(validation?.persistence_level_r2);
  const model = shortModelName(live?.model) ?? shortModelName(validation?.model) ?? "The model";
  const ovx = ovxOnlyBenchmark(validation);
  const beatsPersistence = r2 != null && persistenceR2 != null && r2 > persistenceR2;
  let versusOvx = null;
  if (ovx?.p != null && ovx.dm != null) {
    if (ovx.p >= ALPHA) versusOvx = `is not distinguishable from the ${ovxLabel}`;
    else versusOvx = ovx.dm < 0 ? `beats the ${ovxLabel}` : `loses to the ${ovxLabel}`;
  }
  if (beatsPersistence && versusOvx) {
    return `${model} beats persistence ${versusOvx.startsWith("beats") ? `and ${versusOvx.slice("beats ".length)}` : `but ${versusOvx}`}`;
  }
  if (beatsPersistence) return `${model} beats persistence on out-of-sample R²`;
  return versusOvx ? `${model} ${versusOvx}` : null;
};

/** FCST meta: the loss comparison behind the title, with its sample. */
export const forecastDetail = (validation, { ovxLabel = "OVX-only regression" } = {}) => {
  const ovx = ovxOnlyBenchmark(validation);
  const harQlike = num(validation?.har_qlike);
  const n = num(validation?.n);
  if (!ovx || harQlike == null) return [];
  return [
    `QLIKE ${fmtNum(harQlike, 3)} vs ${fmtNum(ovx.qlike, 3)} for the ${ovxLabel}${ovx.p != null ? `, DM ${fmtP(ovx.p)}` : ""}`
      + `${n != null ? ` (weekly validation, ${fmtInt(n)} weeks)` : ""}`,
  ];
};

const INFO_TEXT = { implied: "OVX", range: "the intraday range" };

/**
 * MODL: what the Model Confidence Set contains, true by construction from the rows: which inputs
 * every member uses and whether the model of record is among them. Timing controls (and anything
 * else not eligible) are left out.
 * @param {import("./research").LeaderRow[]} rows
 * @param {{level?: number|null, available?: boolean}} [options]  MCS level in percent; whether
 *   the payload carries MCS results at all
 */
export const leaderboardAssertion = (rows, { level = null, available = true } = {}) => {
  if (!Array.isArray(rows) || !available) return null;
  const eligible = rows.filter((row) => row.mcsEligible !== false && !row.timingControl);
  const survivors = eligible.filter((row) => row.inMcs);
  if (!eligible.length || !survivors.length) return null;
  const set = `${level != null ? `${level}% ` : ""}Model Confidence Set`;
  const record = eligible.find((row) => row.role === "record") ?? null;
  const recordName = record ? shortLabel(record.label) : null;

  if (survivors.length === 1) {
    const only = survivors[0];
    return `${shortLabel(only.label)}${only === record ? ", the model of record," : ""} alone is in the ${set}`;
  }
  const shared = Object.keys(INFO_TEXT).find((kind) => survivors.every((row) => row.info.has(kind)));
  const uses = shared ? INFO_TEXT[shared]
    : survivors.every((row) => row.info.size > 0) ? `${INFO_TEXT.implied} or ${INFO_TEXT.range}` : null;
  const first = uses
    ? `All ${fmtInt(survivors.length)} models in the ${set} use ${uses}`
    : `${fmtInt(survivors.length)} of ${fmtInt(eligible.length)} models are in the ${set}`;
  const recordPart = record ? `${recordName} is ${record.inMcs ? "one of them" : "not among them"}` : null;
  return join([first, recordPart]);
};

/**
 * MODL meta: the record's MCS p-value and whether the return-only models are significantly worse
 * than it (every one of them, by a Diebold-Mariano test at ALPHA).
 */
export const leaderboardDetail = (rows, { available = true } = {}) => {
  if (!Array.isArray(rows) || !available) return [];
  const eligible = rows.filter((row) => row.mcsEligible !== false && !row.timingControl);
  const record = eligible.find((row) => row.role === "record") ?? null;
  if (!record) return [];
  const recordName = shortLabel(record.label);
  const parts = [record.mcsP != null ? `${recordName} ${labelledP("MCS", record.mcsP)}` : null];
  const returnOnly = eligible.filter((row) => row.info.size === 0 && row.role !== "record");
  if (returnOnly.length) {
    const allWorse = returnOnly.every((row) => !row.inMcs && row.dmStat != null && row.dmStat < 0
      && row.dmP != null && row.dmP < ALPHA);
    if (allWorse) parts.push(`every return-only model has significantly higher QLIKE than ${recordName}`);
    else if (returnOnly.every((row) => !row.inMcs)) parts.push("no return-only model is in the set");
  }
  return parts.filter(Boolean);
};

/**
 * MODL footnote: the Hansen SPA test of the model of record, read at the level its p-value
 * supports. A p between 5% and 10% is inconclusive, and is said to be.
 */
export const spaAssertion = (spa, { recordName = "the model of record", mcsLevel = null } = {}) => {
  const p = num(asObject(spa)?.p_consistent);
  if (p == null) return null;
  const count = asArray(spa.models).length;
  const lower = num(spa.p_lower);
  const upper = num(spa.p_upper);
  const bounds = lower != null && upper != null ? ` (lower ${fmtPValue(lower)}, upper ${fmtPValue(upper)})` : "";
  const mcsAlpha = mcsLevel != null ? 1 - mcsLevel / 100 : null;
  const loose = mcsAlpha != null && Math.abs(mcsAlpha - ALPHA_LOOSE) < 1e-9 ? `${pct(ALPHA_LOOSE)}% level the Model Confidence Set uses` : `${pct(ALPHA_LOOSE)}% level`;
  let reading;
  if (p < ALPHA) reading = `some alternative beats ${recordName} at the ${pct(ALPHA)}% level`;
  else if (p < ALPHA_LOOSE) reading = `inconclusive: not significant at ${pct(ALPHA)}%, but it would reject at the ${loose}`;
  else reading = `no alternative beats ${recordName} even at the ${pct(ALPHA_LOOSE)}% level`;
  return `Hansen SPA test of ${recordName} against ${count ? plural(count, "alternative") : "the alternatives"}: consistent ${fmtP(p)}${bounds}; ${reading}.`;
};

/** STAB: does the ranking hold year by year? "Beat" means a lower QLIKE in that calendar year. */
export const stabilityAssertion = (yearly, { champion, challenger, bench, labels = new Map() } = {}) => {
  if (!Array.isArray(yearly) || !yearly.length || !champion) return null;
  const name = (key) => shortLabel(labels.get(key) ?? key);
  const parts = [];
  if (bench) {
    const { wins, total } = yearsBetter(yearly, champion, bench);
    if (total) parts.push(`${name(champion)} beat ${name(bench)} in ${wins} of ${total} years`);
  }
  if (challenger) {
    const { wins, total } = yearsBetter(yearly, challenger, champion);
    if (total) parts.push(`the challenger beat ${name(champion)} in ${wins}`);
  }
  return parts.length ? join(parts) : null;
};

/**
 * CALB: the yearly range of conformal coverage. Adaptive conformal inference steers its long-run
 * coverage to the target by construction, so the overall figure is expected; the evidence is the
 * year-by-year spread (and the interval score, shown beside it).
 */
export const calibrationAssertion = (lab, champion) => {
  const conformal = asObject(lab?.conformal?.[champion]);
  const coverage = num(conformal?.coverage_pct);
  const target = num(conformal?.target_coverage_pct) ?? INTERVAL_LEVEL_PCT;
  if (coverage == null) return null;
  const years = coverageByYear(lab, champion).map((row) => row.coverage);
  if (years.length > 1) {
    return `In every year the ${fmtNum(target, 0)}% interval covered between ${fmtPct(Math.min(...years))} and ${fmtPct(Math.max(...years))} of days`;
  }
  return `The ${fmtNum(target, 0)}% interval covered ${fmtPct(coverage)} of days, as adaptive conformal targets by construction`;
};

/** CALB meta: the overall coverage, which the method targets by construction. */
export const calibrationDetail = (lab, champion) => {
  const conformal = asObject(lab?.conformal?.[champion]);
  const coverage = num(conformal?.coverage_pct);
  const days = num(conformal?.n_days);
  if (coverage == null) return [];
  return [`overall ${fmtPct(coverage)}${days != null ? ` of ${fmtInt(days)} days` : ""}, targeted by construction`];
};

const breachRange = (row) => {
  if (row.exceedMin == null || row.exceedMax == null) return null;
  const span = row.exceedMin === row.exceedMax ? fmtInt(row.exceedMin) : `${fmtInt(row.exceedMin)}–${fmtInt(row.exceedMax)}`;
  return `${span} breaches${row.expected != null ? ` vs ${fmtNum(row.expected, 1)} expected` : ""}`;
};

const QUANTILE_WORDS = { fhs: "filtered historical simulation", normal: "normal quantile" };

/**
 * RISK: the model of record's 99% VaR passes Kupiec coverage on k of n sampling grids; when every
 * grid also has more breaches than expected, the title says so (each excess is within the test's
 * tolerance, together they lean one way).
 */
export const riskAssertion = (rows, labels = new Map(), champion = null) => {
  if (!Array.isArray(rows)) return null;
  const record99 = recordVar(rows, 0.01);
  if (record99?.kupiecPass == null || !record99.gridCount) return null;
  const name = shortLabel(labels.get(champion) ?? record99.label);
  const head = `${name} 99% VaR passes Kupiec on ${record99.kupiecPass}/${record99.gridCount} grids`;
  const over = record99.exceedMin != null && record99.expected != null && record99.exceedMin > record99.expected;
  return over ? `${head}, each with more breaches than expected` : head;
};

/**
 * RISK meta: the record's 99% breach range and method, and historical simulation's independence
 * failures, each at its own VaR level.
 */
export const riskDetail = (rows) => {
  if (!Array.isArray(rows)) return [];
  const record99 = recordVar(rows, 0.01);
  const parts = [];
  if (record99) {
    const method = QUANTILE_WORDS[record99.quantile] ?? null;
    parts.push([breachRange(record99), method].filter(Boolean).join(", "));
  }
  const failing = rows
    .filter((row) => row.isHistorical && row.independencePass != null && row.gridCount && row.confidence != null
      && row.independencePass < row.gridCount)
    .sort((a, b) => b.confidence - a.confidence)
    .map((row) => `at ${row.confidence}% on ${row.gridCount - row.independencePass}/${row.gridCount} grids`);
  if (failing.length) parts.push(`historical-simulation VaR fails the independence test ${failing.join(" and ")}`);
  return parts.filter(Boolean);
};

/** CONE: where current realized vol sits in its own history, shortest vs longest window. */
export const coneAssertion = (cone) => {
  const windows = asArray(cone?.windows)
    .map((row) => ({ window: num(row?.window), percentile: num(row?.latest_percentile), latest: num(row?.latest) }))
    .filter((row) => row.window != null && row.percentile != null)
    .sort((a, b) => a.window - b.window);
  if (!windows.length) return null;
  const short = windows[0];
  const long = windows[windows.length - 1];
  if (short === long) return `Realized vol is at the ${ordinal(short.percentile)} percentile of its ${short.window}-day history`;
  return `Realized vol is at the ${ordinal(short.percentile)} percentile on a ${short.window}-day window `
    + `and the ${ordinal(long.percentile)} on ${long.window} days`;
};

/**
 * VRP: is there a variance risk premium? Read in variance units, where a zero premium means
 * IV² = E[RV²]: the vol-point gap and the share of periods with OVX above realized vol are both
 * positive even with no premium, so they are never the evidence (see vrpSummary()).
 */
export const vrpAssertion = (vrp) => {
  const summary = vrpSummary(vrp);
  if (!summary) return null;
  if (summary.varianceCi) {
    const [lo, hi] = summary.varianceCi;
    if (lo <= 0 && hi >= 0) return "The variance risk premium, in variance units, is not distinguishable from zero";
    return lo > 0 ? "OVX carries a positive variance risk premium, in variance units"
      : "Realized variance exceeded implied variance, on average";
  }
  if (summary.share != null && summary.nullShare != null && summary.nullP != null) {
    return summary.nullP < ALPHA ? "OVX exceeded realized vol more often than a zero premium would explain"
      : "OVX exceeded realized vol about as often as it would with no premium";
  }
  return null;
};

/** The challenger's promotion status in a few words: "accruing 3/52", "keep the model of record (one-sided p = 0.41)". */
export const promotionText = (promotion) => {
  if (!promotion) return null;
  const progress = promotion.paired != null && promotion.required != null
    ? `${fmtInt(promotion.paired)}/${fmtInt(promotion.required)}`
    : null;
  if (promotion.status === "accruing") return `accruing${progress ? ` ${progress}` : ""}`;
  const p = fmtP(promotion.p);
  return `${promotion.status === "promote" ? "promote the challenger" : "keep the model of record"}${p ? ` (one-sided ${p})` : ""}`;
};

/**
 * LIVE: forward record status. Once weeks resolve, the two models are compared on the same weeks
 * only (the pinned pair's, liveRecordSummary().paired); the record's QLIKE over all its resolved
 * weeks is a different sample and appears only labelled as such, in the panel.
 */
export const liveAssertion = (summary) => {
  if (!summary) return null;
  const resolved = summary.resolved ?? 0;
  if (resolved <= 0) {
    // The start date is in the meta line (liveDetail's neighbour), so the title keeps to one claim.
    return summary.firstResult
      ? `No live forecast has resolved yet; the first is due ${fmtDate(summary.firstResult)}`
      : "No live forecast has resolved yet";
  }
  const { n, record, challenger } = summary.paired ?? {};
  if (n > 0 && record != null && challenger != null) {
    return `On the same ${plural(n, "live week")}, QLIKE ${fmtNum(record, 3)} for the model of record vs ${fmtNum(challenger, 3)} for the challenger`;
  }
  return `${plural(resolved, "live week")} resolved; none has both forecasts yet`;
};

/**
 * LIVE meta: what was recorded and why some calls are never scored. Calls skipped at a contract
 * roll, not sealed in time (late_commit: sealed less than five minutes before the settlement that
 * opens their window, or never sealed) or expired are counted as such, never as "not resolved yet".
 */
export const liveDetail = (summary) => {
  if (!summary?.started) return [];
  const recorded = summary.recorded ?? 0;
  const skipped = summary.skipped ?? 0;
  const late = summary.lateCommit ?? 0;
  const expired = summary.expired ?? 0;
  const breakdown = [
    summary.pending > 0 ? `${fmtInt(summary.pending)} pending` : null,
    skipped > 0 ? `${fmtInt(skipped)} skipped at a contract roll` : null,
    late > 0 ? `${fmtInt(late)} not sealed in time` : null,
    expired > 0 ? `${fmtInt(expired)} expired` : null,
  ].filter(Boolean);
  const unscored = skipped + late + expired;
  const never = unscored > 0 ? ` (${unscored === recorded ? "" : `${fmtInt(unscored)} `}never scored)` : "";
  return [`${plural(recorded, "call")} recorded${breakdown.length ? `: ${breakdown.join(", ")}` : ""}${never}`];
};

/** LEAK: what the leak did to the headline number. */
export const leakAssertion = (window) => {
  const leaked = window?.leaked;
  const purged = window?.purged;
  if (leaked?.sharpe == null || purged?.sharpe == null) return null;
  const coinFlip = purged.p != null && purged.p >= ALPHA;
  return coinFlip
    ? `A look-ahead leak turned a coin flip into a Sharpe of ${fmtNum(leaked.sharpe, 2)}; purged, it is ${fmtNum(purged.sharpe, 2)}`
    : `Purging a look-ahead leak cut the Sharpe ratio from ${fmtNum(leaked.sharpe, 2)} to ${fmtNum(purged.sharpe, 2)}`;
};

/** Ledger row id of the pre-2007 carry spread test (data/research_ledger.json). */
const CARRY_PRE_2007_ROW = "carry_spread_pre2007";

/** STDY carry: did the carry sort survive the post-2007 sample? */
export const carryAssertion = (carry) => {
  const pre = num(carry?.pre_2007?.spread_p_value_one_sided);
  const post = num(carry?.post_2007?.spread_p_value_one_sided);
  const full = num(carry?.full_sample?.spread_p_value_one_sided);
  if (pre != null && post != null) {
    if (pre < ALPHA && post >= ALPHA) return "Carry sorted WTI returns before 2007, not detectably after";
    if (pre < ALPHA && post < ALPHA) return "Carry sorted WTI returns both before and after 2007";
    return "Carry did not reliably sort WTI returns in either half";
  }
  if (full == null) return null;
  return full < ALPHA ? "Carry sorted WTI returns over the full sample" : "Carry did not reliably sort WTI returns";
};

/**
 * STDY carry meta: the two halves' one-sided p-values (the pre-2007 one with its multiplicity-
 * adjusted q when the ledger has it, the post-2007 one with its power when the payload's power
 * analysis says the test was underpowered), and the test of the change itself.
 */
export const carryDetail = (carry, ledger = null) => {
  const pre = num(carry?.pre_2007?.spread_p_value_one_sided);
  const post = num(carry?.post_2007?.spread_p_value_one_sided);
  const full = num(carry?.full_sample?.spread_p_value_one_sided);
  const power = num(carry?.power_post_2007?.power_vs_pre_2007_spread);
  const target = num(carry?.power_post_2007?.target_power) ?? 0.8;
  const changeP = num(carry?.regime_change_test?.spread_difference_p_value);
  const rankDropP = num(carry?.regime_change_test?.rank_corr_difference_p_value);
  if (pre == null || post == null) return full != null ? [`full sample ${fmtP(full)}`] : [];
  const preRow = ledger?.byId?.get(CARRY_PRE_2007_ROW);
  const q = preRow?.survives_bh === false && preRow.q != null ? `, q${NBSP}=${NBSP}${fmtPValue(preRow.q)} after Benjamini-Hochberg` : "";
  return [
    `before 2007 ${fmtP(pre)}${q}`,
    `2007 on ${fmtP(post)}${power != null && power < target ? `, ${fmtNum(power * 100, 0)}% power` : ""}`,
    changeP != null ? `change in the spread ${fmtP(changeP)}` : null,
    rankDropP != null && rankDropP < ALPHA ? `the rank correlation fell (${fmtP(rankDropP)})` : null,
  ].filter(Boolean);
};

/**
 * The supply study's rule for one statistic of a group: it differs from an ordinary date only if the
 * test against random placebo dates and the year-matched one both reject at ALPHA. "both", "one"
 * or "neither" reject; null without both p-values.
 */
export const placeboVerdict = (p, pYear) => {
  if (p == null || pYear == null) return null;
  if (Math.max(p, pYear) < ALPHA) return "both";
  return Math.min(p, pYear) < ALPHA ? "one" : "neither";
};

const placeboPs = (p, pYear) => `${fmtP(p)} vs placebo, ${fmtPValue(pYear)} year-matched`;

/**
 * STDY supply study (research.json supply_study): does supply removal move WTI more than an
 * ordinary (placebo) date under the study's rule (both placebo tests below ALPHA), and do
 * threat-only events? Uses the groups keyed supply_lost and threat_only when present.
 */
export const supplyStudyAssertion = (study) => {
  if (!study?.placebo || !study.groups.length) return null;
  const lost = study.groups.find((group) => group.key === "supply_lost");
  const threat = study.groups.find((group) => group.key === "threat_only");
  const placeboPeak = fmtSigned(study.placebo.peak, 1, "%");
  const lostVerdict = placeboVerdict(lost?.peakP, lost?.peakPYear);
  const threatVerdict = placeboVerdict(threat?.peakP, threat?.peakPYear);
  let head = null;
  if (lost?.peak != null && lostVerdict) {
    const peak = fmtSigned(lost.peak, 1, "%");
    head = {
      both: `Supply removal peaks ${peak} vs ${placeboPeak} on an ordinary date`,
      one: `Supply removal peaks ${peak} vs ${placeboPeak}, significant against only one placebo`,
      neither: `Supply removal peaks ${peak}, not distinguishable from ${placeboPeak} on an ordinary date`,
    }[lostVerdict];
  }
  const tail = threat && threatVerdict ? {
    both: "threat-only events differ too",
    one: "threat-only events differ against only one placebo",
    neither: "threat-only: not significant",
  }[threatVerdict] : null;
  return head || tail ? capitalize(join([head, tail])) : null;
};

/** STDY supply meta: the two placebo p-values behind each clause of the title. */
export const supplyStudyDetail = (study) => {
  if (!study?.groups?.length) return [];
  const lost = study.groups.find((group) => group.key === "supply_lost");
  const threat = study.groups.find((group) => group.key === "threat_only");
  return [
    lost?.peakP != null && lost.peakPYear != null ? `supply removal: ${placeboPs(lost.peakP, lost.peakPYear)}` : null,
    threat?.peakP != null && threat.peakPYear != null ? `threat-only: ${placeboPs(threat.peakP, threat.peakPYear)}` : null,
  ].filter(Boolean);
};

/**
 * LEAK anatomy: the same leak on pure noise. The noise experiments' Sharpe is gross and the
 * retracted model's is after costs; both, each with its basis, are in the facts beside the title.
 */
export const leakAnatomyAssertion = (anatomy) => {
  const headline = anatomy?.experiments.find((row) => row.isHeadline);
  if (!headline || headline.unpurged == null || headline.purged == null) return null;
  return `On a pure random walk an unpurged ${headline.modelLabel.toLowerCase()} scores ${fmtPct(headline.unpurged)}; purged, ${fmtPct(headline.purged)}`;
};

/**
 * LEDG: how many supported claims survive multiplicity control. Rows selected after a search are
 * outside the multiplicity family, so they are not counted among the claims that could survive it.
 */
export const ledgerAssertion = (ledger) => {
  if (!ledger) return null;
  const parts = [];
  const inFamily = ledger.supported - (ledger.supportedAfterSearch ?? 0);
  if (ledger.survivingBh != null && inFamily > 0) {
    parts.push(`${ledger.survivingBh} of ${inFamily} supported claims survive Benjamini-Hochberg at ${fmtNum((ledger.alpha ?? ALPHA) * 100, 0)}%`);
  }
  if (ledger.clearingHurdle != null && ledger.hurdle != null) parts.push(`${ledger.clearingHurdle} clear t > ${fmtNum(ledger.hurdle, 0)}`);
  return parts.length ? join(parts) : `${ledger.rows.length} hypotheses tested`;
};

/**
 * RISK vol targeting: did sizing on the forecast hold its volatility target? The comparisons
 * (tails, Sharpe against buy-and-hold and against trailing-vol sizing) are in volTargetDetail().
 */
export const volTargetAssertion = (risk) => {
  const forecast = risk?.rows.find((row) => row.key === "forecast_vol_target");
  if (!forecast || forecast.vol == null || risk.target == null) return null;
  return `Sizing on the forecast held realized vol at ${fmtPct(forecast.vol)} against a ${fmtNum(risk.target, 0)}% target`;
};

/** RISK vol targeting meta: the simple alternative it must beat, the Sharpe test and the tails. */
export const volTargetDetail = (risk) => {
  const forecast = risk?.rows.find((row) => row.key === "forecast_vol_target");
  const buyHold = risk?.rows.find((row) => row.key === "buy_hold");
  const trailing = risk?.rows.find((row) => row.key === "trailing_vol_target");
  if (!forecast) return [];
  const vsTrailing = risk.vsTrailing;
  const parts = [];
  if (trailing?.vol != null && vsTrailing?.p != null && vsTrailing.difference != null) {
    parts.push(`trailing-vol sizing held ${fmtPct(trailing.vol)}; Sharpe difference ${fmtSigned(vsTrailing.difference, 2)}, `
      + `${fmtP(vsTrailing.p)}${vsTrailing.p >= ALPHA ? ", not distinguishable" : ""}`);
  }
  const p = risk.vsBuyHold?.p;
  if (p != null) parts.push(`Sharpe vs buy-and-hold ${fmtP(p)}${p >= ALPHA ? ", not distinguishable" : ""}`);
  if (buyHold?.kurtosis != null && forecast.kurtosis != null) {
    parts.push(`excess kurtosis ${fmtNum(forecast.kurtosis, 1)} vs ${fmtNum(buyHold.kurtosis, 1)} buy-and-hold`);
  }
  return parts;
};

/**
 * STDY supply shocks without the placebo study (data.json playbook only): descriptive medians,
 * no comparison claimed. A 20-session maximum is positive on most ordinary dates, so a median
 * peak means little on its own; the placebo study is what supports a comparison.
 */
export const supplyPlaybookAssertion = (playbook) => {
  const dist = asObject(playbook?.distributions) ?? {};
  const lost = asObject(dist.supply_lost);
  const threat = asObject(dist.threat_only);
  const lostPeak = num(lost?.peak?.median);
  const threatPeak = num(threat?.peak?.median);
  if (lostPeak == null || threatPeak == null) return null;
  return `Median 20-session peak ${fmtSigned(lostPeak, 1, "%")} after supply removal, ${fmtSigned(threatPeak, 1, "%")} after threats; no placebo comparison`;
};

/** STDY: the two side studies, one short clause each. */
export const studiesAssertion = (carry, study) => {
  const pre = num(carry?.pre_2007?.spread_p_value_one_sided);
  const post = num(carry?.post_2007?.spread_p_value_one_sided);
  let carryPart = null;
  if (pre != null && post != null) {
    if (pre < ALPHA && post >= ALPHA) carryPart = "Carry sorted returns before 2007, not detectably after";
    else carryPart = pre < ALPHA && post < ALPHA ? "Carry sorted returns in both halves" : "Carry did not reliably sort returns";
  }
  const lost = study?.groups.find((group) => group.key === "supply_lost");
  const supplyPart = {
    both: "supply removal beat placebo dates",
    one: "supply removal beat only one placebo",
    neither: "supply removal did not beat placebo dates",
  }[placeboVerdict(lost?.peakP, lost?.peakPYear)] ?? null;
  return capitalize(join([carryPart, supplyPart]));
};

/**
 * METH: provenance in one line. The SHA-256 manifest pins the benchmark lab's inputs only; the
 * production validation and the post-mortem are committed records of runs on downloaded data
 * (methodDetail()).
 */
export const methodAssertion = (lab) => {
  const files = asObject(lab?.generated_from?.files);
  const count = files ? Object.keys(files).length : 0;
  if (!count) return null;
  return `Every benchmark-lab figure comes from ${count} source file${count === 1 ? "" : "s"} pinned by SHA-256 digest`;
};

/** METH meta: how far the pinned data run, and what the digests do not cover. */
export const methodDetail = (lab) => {
  const through = asText(lab?.generated_from?.sessions_through);
  return [
    through ? `sessions through ${fmtDate(through)}` : null,
    "production validation and post-mortem: committed records of runs on downloaded data",
  ].filter(Boolean);
};
