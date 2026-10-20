// Panel titles are assertions computed from the payload, never fixed copy: each builder states only
// what its numbers support and returns null when they are missing (the panel then falls back to a
// neutral title). Wording is deliberately plain: no claim the statistics do not carry, and every
// figure names its sample when two samples of the same statistic appear on the page.
import { fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtPValue, fmtSigned, num, ordinal } from "./format";
import { asArray, asObject, asText, coverageByYear, ovxOnlyBenchmark, recordVar, shortLabel, yearsBetter } from "./research";

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

/**
 * Which pipeline produced the band drawn on the forecast chart (see bandCoverage()): the benchmark
 * lab's adaptive conformal band, the production validation's, or the lab's with newer production weeks.
 */
export const bandSourceLabel = (band) => {
  if (!band?.n) return null;
  if (band.lab && !band.live) return "benchmark lab";
  if (band.live && !band.lab) return "production validation";
  return `benchmark lab; production validation for the latest ${plural(band.live, "week")}`;
};

/**
 * FCST: the model against the weakest benchmark (persistence) and the strongest simple one (a
 * regression on OVX alone), then the coverage of the band as drawn. `band` is bandCoverage() of the
 * plotted rows, so the stated coverage is always that of the band on the chart.
 */
export const forecastAssertion = (validation, live, band = null) => {
  const r2 = num(validation?.har_level_r2);
  const persistenceR2 = num(validation?.persistence_level_r2);
  const model = shortModelName(live?.model) ?? shortModelName(validation?.model) ?? "The model";
  const ovx = ovxOnlyBenchmark(validation);
  const harQlike = num(validation?.har_qlike);
  const tracks = r2 != null && persistenceR2 != null && r2 > persistenceR2
    ? `${model} tracks next-week realized vol better than persistence (vol-level out-of-sample R² ${fmtNum(r2, 2)} vs ${fmtNum(persistenceR2, 2)})`
    : null;
  let versusOvx = null;
  if (ovx && ovx.p != null && harQlike != null) {
    const losses = `QLIKE ${fmtNum(harQlike, 3)} vs ${fmtNum(ovx.qlike, 3)}, ${fmtP(ovx.p)}`;
    if (ovx.p >= ALPHA) versusOvx = `is not distinguishable from an OVX-only regression (${losses})`;
    else if (ovx.dm != null) versusOvx = `${ovx.dm > 0 ? "beats" : "loses to"} an OVX-only regression (${losses})`;
  }
  const comparison = tracks && versusOvx ? `${tracks} but ${versusOvx}` : tracks ?? (versusOvx ? `${model} ${versusOvx}` : null);
  const source = bandSourceLabel(band);
  const coverage = band?.coverage != null && source
    ? `the ${INTERVAL_LEVEL_PCT}% band drawn (${source}) covered ${fmtPct(band.coverage)} of ${plural(band.n, "week")}`
    : null;
  return capitalize(join([comparison, coverage]));
};

const INFO_TEXT = { implied: "implied vol (OVX)", range: "the intraday range" };

/**
 * MODL: what the Model Confidence Set contains, true by construction from the rows: which inputs
 * every member uses, whether the model of record is in it, and whether the return-only models are
 * significantly worse than it. Timing controls (and anything else not eligible) are left out.
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

  let first;
  if (survivors.length === 1) {
    const only = survivors[0];
    first = `${shortLabel(only.label)}${only === record ? ", the model of record," : ""} alone is in the ${set}`;
  } else {
    const shared = Object.keys(INFO_TEXT).find((kind) => survivors.every((row) => row.info.has(kind)));
    const uses = shared ? INFO_TEXT[shared]
      : survivors.every((row) => row.info.size > 0) ? `${INFO_TEXT.implied} or ${INFO_TEXT.range}` : null;
    first = uses
      ? `All ${fmtInt(survivors.length)} models in the ${set} use ${uses}`
      : `${fmtInt(survivors.length)} of ${fmtInt(eligible.length)} models are in the ${set}`;
  }
  const recordPart = record && !(survivors.length === 1 && survivors[0] === record)
    ? `${recordName}, the model of record, is ${record.inMcs ? "in" : "outside"} it${record.mcsP != null ? ` (MCS ${fmtP(record.mcsP)})` : ""}`
    : null;

  const returnOnly = eligible.filter((row) => row.info.size === 0 && row.role !== "record");
  let third = null;
  if (returnOnly.length && record) {
    const allWorse = returnOnly.every((row) => !row.inMcs && row.dmStat != null && row.dmStat < 0
      && row.dmP != null && row.dmP < ALPHA);
    if (allWorse) third = `every return-only model has significantly higher QLIKE than ${recordName}`;
    else if (returnOnly.every((row) => !row.inMcs)) third = "no return-only model is in it";
  }
  return join([first, recordPart, third]);
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

/** STAB: does the ranking hold year by year? */
export const stabilityAssertion = (yearly, { champion, challenger, bench, labels = new Map() } = {}) => {
  if (!Array.isArray(yearly) || !yearly.length || !champion) return null;
  const name = (key) => shortLabel(labels.get(key) ?? key);
  const parts = [];
  if (bench) {
    const { wins, total } = yearsBetter(yearly, champion, bench);
    if (total) parts.push(`${name(champion)} had lower QLIKE than ${name(bench)} in ${wins} of ${total} years`);
  }
  if (challenger) {
    const { wins, total } = yearsBetter(yearly, challenger, champion);
    if (total) parts.push(`the challenger beat it in ${wins}`);
  }
  return parts.length ? join(parts) : null;
};

/**
 * CALB: conformal coverage overall and its yearly range. Adaptive conformal inference steers its
 * long-run coverage to the target by construction, so coverage near the target is expected; the
 * evidence is in the year-by-year spread (and the interval score, shown beside it).
 */
export const calibrationAssertion = (lab, champion) => {
  const conformal = asObject(lab?.conformal?.[champion]);
  const coverage = num(conformal?.coverage_pct);
  const target = num(conformal?.target_coverage_pct) ?? INTERVAL_LEVEL_PCT;
  const days = num(conformal?.n_days);
  if (coverage == null) return null;
  const years = coverageByYear(lab, champion).map((row) => row.coverage);
  const range = years.length > 1 ? `; year by year coverage ranged ${fmtNum(Math.min(...years), 1)}–${fmtPct(Math.max(...years))}` : "";
  return `${fmtNum(target, 0)}% intervals covered ${fmtPct(coverage)}${days != null ? ` of ${fmtInt(days)} days` : ""}, `
    + `which adaptive conformal targets by construction${range}`;
};

const breachRange = (row) => {
  if (row.exceedMin == null || row.exceedMax == null) return null;
  const span = row.exceedMin === row.exceedMax ? fmtInt(row.exceedMin) : `${fmtInt(row.exceedMin)}–${fmtInt(row.exceedMax)}`;
  return `${span} breaches${row.expected != null ? ` vs ${fmtNum(row.expected, 1)} expected` : ""}`;
};

const QUANTILE_WORDS = { fhs: "filtered historical simulation", normal: "normal quantile" };

/**
 * RISK: the model of record's 99% VaR (coverage and its breach range across the sampling grids)
 * and historical simulation's independence failures, each clause at its own VaR level.
 */
export const riskAssertion = (rows, labels = new Map(), champion = null) => {
  if (!Array.isArray(rows)) return null;
  const record99 = recordVar(rows, 0.01);
  const parts = [];
  if (record99?.kupiecPass != null && record99.gridCount) {
    const name = shortLabel(labels.get(champion) ?? record99.label);
    const method = QUANTILE_WORDS[record99.quantile] ? ` (${QUANTILE_WORDS[record99.quantile]})` : "";
    const breaches = breachRange(record99);
    parts.push(`${name} 99% VaR${method} passes Kupiec coverage on ${record99.kupiecPass}/${record99.gridCount} grids`
      + (breaches ? `, ${breaches}` : ""));
  }
  const failing = rows
    .filter((row) => row.isHistorical && row.independencePass != null && row.gridCount && row.confidence != null
      && row.independencePass < row.gridCount)
    .sort((a, b) => b.confidence - a.confidence)
    .map((row) => `at ${row.confidence}% on ${row.gridCount - row.independencePass}/${row.gridCount} grids`);
  if (failing.length) parts.push(`historical-simulation VaR fails the independence test ${failing.join(" and ")}`);
  return parts.length ? join(parts) : null;
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

/** VRP: how often implied vol exceeded what was realized, and the tail of that bet. */
export const vrpAssertion = (vrp) => {
  const share = num(vrp?.share_positive_pct);
  const n = num(vrp?.n_periods);
  const worst = num(vrp?.worst_period_vol_pts);
  if (share == null) return null;
  const head = `OVX exceeded subsequent realized vol in ${fmtPct(share)}${n != null ? ` of ${fmtInt(n)} periods` : " of periods"}`;
  return worst != null && worst < 0 ? `${head}; the worst period missed by ${fmtNum(Math.abs(worst), 1)} vol pts` : head;
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
 * LIVE: forward record status. Calls skipped at a contract roll, sealed after their window opened
 * (late_commit) or expired are never scored; they are counted as such, never as "not resolved yet".
 */
export const liveAssertion = (summary) => {
  if (!summary) return null;
  const resolved = summary.resolved ?? 0;
  if (resolved <= 0) {
    if (!summary.started) return "No live forecast has resolved yet";
    const recorded = summary.recorded ?? 0;
    const skipped = summary.skipped ?? 0;
    const late = summary.lateCommit ?? 0;
    const expired = summary.expired ?? 0;
    const breakdown = [
      summary.pending > 0 ? `${fmtInt(summary.pending)} pending` : null,
      skipped > 0 ? `${fmtInt(skipped)} skipped at a contract roll` : null,
      late > 0 ? `${fmtInt(late)} sealed after ${late === 1 ? "its" : "their"} window opened` : null,
      expired > 0 ? `${fmtInt(expired)} expired` : null,
    ].filter(Boolean);
    const unscored = skipped + late + expired;
    let calls = "nothing has resolved yet";
    if (recorded > 0) {
      const never = unscored > 0 ? ` (${unscored === recorded ? "" : `${fmtInt(unscored)} `}never scored)` : "";
      calls = join([`${plural(recorded, "call")} recorded${breakdown.length ? `: ${breakdown.join(", ")}` : ""}${never}`, "nothing has resolved yet"]);
    }
    return join([
      `Forward record started ${fmtDate(summary.started)}`,
      calls,
      summary.firstResult ? `first result due ${summary.firstResultIsEstimate ? "about " : ""}${fmtDate(summary.firstResult)}` : null,
    ]);
  }
  const championQ = num(summary.champion?.qlike);
  const challengerQ = num(summary.challenger?.qlike);
  const coverage = num(summary.champion?.interval_80_coverage_pct);
  const head = `${fmtInt(resolved)} live week${resolved === 1 ? "" : "s"} resolved`;
  const loss = championQ != null && challengerQ != null
    ? `QLIKE ${fmtNum(championQ, 3)} for the model of record vs ${fmtNum(challengerQ, 3)} for the challenger`
    : championQ != null ? `QLIKE ${fmtNum(championQ, 3)}` : null;
  const band = coverage != null ? `${INTERVAL_LEVEL_PCT}% band covered ${fmtPct(coverage)}` : null;
  const promotion = promotionText(summary.promotion);
  return join([`${head}${loss ? `: ${loss}` : ""}`, band, promotion ? `promotion test: ${promotion}` : null]);
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

/**
 * STDY carry: did the carry sort survive the post-2007 sample? A null result after 2007 is only
 * called weak evidence when the payload's power analysis says the test was underpowered; the
 * pre/post change is reported with its own test, and the pre-2007 spread with its multiplicity-
 * adjusted q-value when the ledger is available.
 */
export const carryAssertion = (carry, ledger = null) => {
  const pre = num(carry?.pre_2007?.spread_p_value_one_sided);
  const post = num(carry?.post_2007?.spread_p_value_one_sided);
  const full = num(carry?.full_sample?.spread_p_value_one_sided);
  const power = num(carry?.power_post_2007?.power_vs_pre_2007_spread);
  const changeP = num(carry?.regime_change_test?.spread_difference_p_value);
  const rankDropP = num(carry?.regime_change_test?.rank_corr_difference_p_value);
  if (pre != null && post != null) {
    if (pre < ALPHA && post >= ALPHA) {
      const powerNote = power != null && power < (num(carry?.power_post_2007?.target_power) ?? 0.8) ? `, ${fmtNum(power * 100, 0)}% power` : "";
      const preRow = ledger?.byId?.get(CARRY_PRE_2007_ROW);
      const bh = preRow?.survives_bh === false && preRow.q != null
        ? `; q = ${fmtPValue(preRow.q)} after Benjamini-Hochberg across the ledger` : "";
      const change = changeP == null ? null
        : changeP >= ALPHA ? `the change in the spread is not itself significant (${fmtP(changeP)})`
          : `the spread fell significantly (${fmtP(changeP)})`;
      const rank = rankDropP != null && rankDropP < ALPHA ? `its rank correlation fell (${fmtP(rankDropP)})` : null;
      return join([`Carry sorted WTI returns before 2007 (${fmtP(pre)}${bh}), not detectably after (${fmtP(post)}${powerNote})`, change, rank]);
    }
    if (pre < ALPHA && post < ALPHA) return `Carry sorted WTI returns before and after 2007 (${fmtP(pre)}, ${fmtP(post)})`;
    return `Carry did not reliably sort WTI returns in either half (${fmtP(pre)}, ${fmtP(post)})`;
  }
  return full != null ? `Carry spread over the full sample: ${fmtP(full)}` : null;
};

/**
 * STDY supply study (research.json supply_study): which event groups differ from an ordinary
 * (placebo) date. Uses the groups keyed supply_lost and threat_only when present.
 */
export const supplyStudyAssertion = (study) => {
  if (!study?.placebo || !study.groups.length) return null;
  const lost = study.groups.find((group) => group.key === "supply_lost");
  const threat = study.groups.find((group) => group.key === "threat_only");
  const placeboPeak = fmtSigned(study.placebo.peak, 1, "%");
  const parts = [];
  if (lost?.peak != null && lost.peakP != null) {
    parts.push(lost.peakP < ALPHA
      ? `Supply removal peaks ${fmtSigned(lost.peak, 1, "%")} vs ${placeboPeak} on an ordinary date (${fmtP(lost.peakP)})`
      : `Supply removal peaks ${fmtSigned(lost.peak, 1, "%")}, not distinguishable from ${placeboPeak} on an ordinary date (${fmtP(lost.peakP)})`);
  }
  if (threat?.peak != null && threat.peakP != null) {
    parts.push(threat.peakP >= ALPHA
      ? `threat-only events (${fmtSigned(threat.peak, 1, "%")}) are indistinguishable from one`
      : `threat-only events differ (${fmtSigned(threat.peak, 1, "%")}, ${fmtP(threat.peakP)})`);
  }
  return parts.length ? join(parts) : null;
};

/** LEAK anatomy: the same leak on pure noise reproduces the retracted model's numbers. */
export const leakAnatomyAssertion = (anatomy) => {
  const headline = anatomy?.experiments.find((row) => row.isHeadline);
  const reference = anatomy?.reference;
  if (!headline || headline.unpurged == null || headline.purged == null) return null;
  const sharpe = headline.unpurgedSharpe != null ? `, Sharpe ${fmtNum(headline.unpurgedSharpe, 2)}` : "";
  const versus = reference?.accuracy != null
    ? ` (the retracted model: ${fmtPct(reference.accuracy)}${reference.sharpe != null ? `, ${fmtNum(reference.sharpe, 2)}` : ""})`
    : "";
  return `On a pure random walk an unpurged ${headline.modelLabel.toLowerCase()} scores ${fmtPct(headline.unpurged)}${sharpe}${versus}; purged, ${fmtPct(headline.purged)}`;
};

/** LEDG: how many supported claims survive multiplicity control. */
export const ledgerAssertion = (ledger) => {
  if (!ledger) return null;
  const parts = [];
  if (ledger.survivingBh != null && ledger.supported) {
    parts.push(`${ledger.survivingBh} of ${ledger.supported} supported claims survive Benjamini-Hochberg at ${fmtNum((ledger.alpha ?? ALPHA) * 100, 0)}%`);
  }
  if (ledger.clearingHurdle != null && ledger.hurdle != null) parts.push(`${ledger.clearingHurdle} clear t > ${fmtNum(ledger.hurdle, 0)}`);
  return parts.length ? join(parts) : `${ledger.rows.length} hypotheses tested`;
};

/**
 * RISK vol targeting: did sizing on the forecast control risk, at what Sharpe, and did it do any
 * better than the simple alternative of sizing on trailing realized vol?
 */
export const volTargetAssertion = (risk) => {
  const forecast = risk?.rows.find((row) => row.key === "forecast_vol_target");
  const buyHold = risk?.rows.find((row) => row.key === "buy_hold");
  const trailing = risk?.rows.find((row) => row.key === "trailing_vol_target");
  if (!forecast || forecast.vol == null || risk.target == null) return null;
  const tails = buyHold?.kurtosis != null && forecast.kurtosis != null
    ? `, with thinner tails than buy-and-hold (excess kurtosis ${fmtNum(forecast.kurtosis, 1)} vs ${fmtNum(buyHold.kurtosis, 1)})`
    : "";
  const p = risk.vsBuyHold?.p;
  const sharpe = p != null ? (p >= ALPHA ? ` at a Sharpe not distinguishable from it (${fmtP(p)})` : `; Sharpe difference ${fmtP(p)}`) : "";
  const head = `Sizing on the forecast held realized vol at ${fmtPct(forecast.vol)} against a ${fmtNum(risk.target, 0)}% target${tails}${sharpe}`;
  const vsTrailing = risk.vsTrailing;
  let versus = null;
  if (trailing?.vol != null && vsTrailing?.p != null && vsTrailing.difference != null) {
    const difference = `${fmtSigned(vsTrailing.difference, 2)}, ${fmtP(vsTrailing.p)}`;
    versus = vsTrailing.p >= ALPHA
      ? `trailing-vol sizing held ${fmtPct(trailing.vol)} with a Sharpe not distinguishable from the forecast's (${difference})`
      : `against trailing-vol sizing (${fmtPct(trailing.vol)} realized) the Sharpe difference is ${difference}`;
  }
  return join([head, versus]);
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
  return `Median 20-session peak: ${fmtSigned(lostPeak, 1, "%")} after supply removal (n = ${fmtInt(lost.n)}), `
    + `${fmtSigned(threatPeak, 1, "%")} after threats (n = ${fmtInt(threat.n)}); no placebo comparison in this snapshot`;
};

/** STDY: the two side studies in one line. */
export const studiesAssertion = (carry, study) => {
  const pre = num(carry?.pre_2007?.spread_p_value_one_sided);
  const post = num(carry?.post_2007?.spread_p_value_one_sided);
  const changeP = num(carry?.regime_change_test?.spread_difference_p_value);
  let carryPart = null;
  if (pre != null && post != null) {
    if (pre < ALPHA && post >= ALPHA) {
      carryPart = `Carry sorted WTI returns before 2007, not detectably after${changeP != null && changeP >= ALPHA ? ", though the change is not itself significant" : ""}`;
    } else {
      carryPart = pre < ALPHA && post < ALPHA ? "Carry sorted WTI returns in both halves" : "Carry did not reliably sort WTI returns";
    }
  }
  const lost = study?.groups.find((group) => group.key === "supply_lost");
  const threat = study?.groups.find((group) => group.key === "threat_only");
  const supplyPart = lost?.peakP != null && threat?.peakP != null
    ? lost.peakP < ALPHA && threat.peakP >= ALPHA
      ? "supply removal moved price beyond an ordinary date; threat-only events were not distinguishable from an ordinary date"
      : `supply-removal and threat-only peaks vs placebo: ${fmtP(lost.peakP)} and ${fmtP(threat.peakP)}`
    : null;
  return capitalize(join([carryPart, supplyPart]));
};

/**
 * METH: provenance in one line. The SHA-256 manifest pins the benchmark lab's inputs only; the
 * production validation and the post-mortem are committed records of runs on downloaded data.
 */
export const methodAssertion = (lab) => {
  const files = asObject(lab?.generated_from?.files);
  const count = files ? Object.keys(files).length : 0;
  const through = asText(lab?.generated_from?.sessions_through);
  if (!count) return null;
  return `Every benchmark-lab figure is computed from ${count} pinned source file${count === 1 ? "" : "s"} with SHA-256 digests`
    + `${through ? `, sessions through ${fmtDate(through)}` : ""}; the production validation and the post-mortem are committed records of runs on downloaded data`;
};
