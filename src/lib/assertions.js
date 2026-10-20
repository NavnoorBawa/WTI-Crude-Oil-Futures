// Panel titles are assertions computed from the payload, never fixed copy: each builder states only
// what its numbers support and returns null when they are missing (the panel then falls back to a
// neutral title). Wording is deliberately plain: no claim the statistics do not carry.
import { fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtSigned, num, ordinal } from "./format";
import { asArray, asObject, asText, coverageByYear, recordVar, shortLabel, yearsBetter } from "./research";

/** The nominal coverage of the contract's interval_80 fields (a design level, not a statistic). */
export const INTERVAL_LEVEL_PCT = 80;
/** Conventional significance level used in the wording ("significantly", "passes"). */
export const ALPHA = 0.05;

/** "HAR-IV (RV5,RV22,RV66,OVX)" -> "HAR-IV". */
export const shortModelName = (model) => {
  const text = asText(model);
  return text ? text.replace(/\s*\(.*\)\s*$/, "") || text : null;
};

const join = (parts) => parts.filter(Boolean).join("; ");

/** FCST: does the band cover what it claims? */
export const forecastAssertion = (validation, live) => {
  const coverage = num(validation?.interval_80_coverage_pct);
  const n = num(validation?.interval_80_n);
  const r2 = num(validation?.har_level_r2);
  const persistenceR2 = num(validation?.persistence_level_r2);
  const model = shortModelName(live?.model) ?? shortModelName(validation?.model) ?? "The model";
  const tracks = r2 != null && persistenceR2 != null && r2 > persistenceR2
    ? `${model} tracks next-week realized vol better than persistence (R² ${fmtNum(r2, 2)} vs ${fmtNum(persistenceR2, 2)})`
    : null;
  let band = null;
  if (coverage != null && n != null) {
    const gap = coverage - INTERVAL_LEVEL_PCT;
    band = Math.abs(gap) <= 2
      ? `its ${INTERVAL_LEVEL_PCT}% band covered ${fmtPct(coverage)} of ${fmtInt(n)} weeks`
      : `its ${INTERVAL_LEVEL_PCT}% band ${gap < 0 ? "under" : "over"}-covered (${fmtPct(coverage)} of ${fmtInt(n)} weeks)`;
  }
  const text = join([tracks, band]);
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : null;
};

const INFO_WORDS = { implied: "implied-vol", range: "range-based" };

/**
 * MODL: which kinds of model survive the Model Confidence Set, and whether the return-only ones
 * (no OVX, no intraday range) are significantly worse than the model of record.
 * @param {import("./research").LeaderRow[]} rows
 */
export const leaderboardAssertion = (rows) => {
  if (!Array.isArray(rows)) return null;
  const survivors = rows.filter((row) => row.inMcs);
  if (!rows.length || !survivors.length) return null;
  const informed = survivors.every((row) => row.info.size > 0);
  const kinds = [...new Set(survivors.flatMap((row) => [...row.info]))].sort();
  let first;
  if (survivors.length === 1) {
    first = `${shortLabel(survivors[0].label)} alone survives the Model Confidence Set`;
  } else if (informed && kinds.length) {
    const words = kinds.map((kind) => INFO_WORDS[kind] ?? kind);
    const subject = words.join(" and ");
    first = `${subject.charAt(0).toUpperCase()}${subject.slice(1)} models are statistically tied at the top`;
  } else {
    first = `${survivors.length} of ${rows.length} models are statistically tied at the top`;
  }
  const returnOnly = rows.filter((row) => row.info.size === 0 && row.role !== "record");
  let second = null;
  if (returnOnly.length) {
    const allWorse = returnOnly.every((row) => !row.inMcs && row.dmStat != null && row.dmStat < 0
      && row.dmP != null && row.dmP < ALPHA);
    if (allWorse) second = "return-only models are significantly worse";
    else if (returnOnly.every((row) => !row.inMcs)) second = "no return-only model survives";
  }
  return join([first, second]);
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

/** CALB: conformal coverage overall and its yearly range. */
export const calibrationAssertion = (lab, champion) => {
  const conformal = asObject(lab?.conformal?.[champion]);
  const coverage = num(conformal?.coverage_pct);
  const target = num(conformal?.target_coverage_pct) ?? INTERVAL_LEVEL_PCT;
  const days = num(conformal?.n_days);
  if (coverage == null) return null;
  const years = coverageByYear(lab, champion).map((row) => row.coverage);
  const range = years.length > 1 ? `; by year it ranged ${fmtNum(Math.min(...years), 1)}–${fmtPct(Math.max(...years))}` : "";
  return `${fmtNum(target, 0)}% intervals covered ${fmtPct(coverage)}${days != null ? ` of ${fmtInt(days)} days` : ""}${range}`;
};

/** RISK: the model of record's 99% VaR vs historical simulation's independence failures. */
export const riskAssertion = (rows, labels = new Map(), champion = null) => {
  if (!Array.isArray(rows)) return null;
  const record99 = recordVar(rows, 0.01);
  const historical = rows
    .filter((row) => row.isHistorical && row.independencePass != null && row.gridCount)
    .sort((a, b) => a.independencePass / a.gridCount - b.independencePass / b.gridCount)[0];
  const parts = [];
  if (record99?.kupiecPass != null && record99.gridCount) {
    const name = shortLabel(labels.get(champion) ?? record99.label);
    const method = record99.quantile === "fhs" ? " (filtered hist. sim.)" : "";
    parts.push(`${name} 99% VaR${method} passes Kupiec coverage on ${record99.kupiecPass}/${record99.gridCount} grids`);
  }
  if (historical) {
    const fails = historical.gridCount - historical.independencePass;
    if (fails > 0) parts.push(`historical simulation fails independence on ${fails}/${historical.gridCount}`);
  }
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

/** LIVE: forward record status. */
export const liveAssertion = (summary) => {
  if (!summary) return null;
  const resolved = summary.resolved ?? 0;
  if (resolved <= 0) {
    if (!summary.started) return "No live forecast has resolved yet";
    const recorded = summary.recorded ?? 0;
    return join([
      `Forward record started ${fmtDate(summary.started)}`,
      recorded > 0 ? `${fmtInt(recorded)} call${recorded === 1 ? "" : "s"} recorded, none resolved yet` : "nothing has resolved yet",
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
  return join([`${head}${loss ? `: ${loss}` : ""}`, band]);
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

/**
 * STDY carry: did the carry sort survive the post-2007 sample? A null result after 2007 is only
 * called weak evidence when the payload's power analysis says the test was underpowered, and a
 * significant fall in the rank correlation is reported when the regime-change test shows one.
 */
export const carryAssertion = (carry) => {
  const pre = num(carry?.pre_2007?.spread_p_value_one_sided);
  const post = num(carry?.post_2007?.spread_p_value_one_sided);
  const full = num(carry?.full_sample?.spread_p_value_one_sided);
  const power = num(carry?.power_post_2007?.power_vs_pre_2007_spread);
  const rankDropP = num(carry?.regime_change_test?.rank_corr_difference_p_value);
  if (pre != null && post != null) {
    if (pre < ALPHA && post >= ALPHA) {
      const powerNote = power != null && power < (num(carry?.power_post_2007?.target_power) ?? 0.8) ? `, ${fmtNum(power * 100, 0)}% power` : "";
      const rank = rankDropP != null && rankDropP < ALPHA ? `; its rank correlation fell (${fmtP(rankDropP)})` : "";
      return `Carry sorted WTI returns before 2007 (${fmtP(pre)}), not detectably after (${fmtP(post)}${powerNote})${rank}`;
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

/** RISK vol targeting: did sizing on the forecast control risk, and at what Sharpe? */
export const volTargetAssertion = (risk) => {
  const forecast = risk?.rows.find((row) => row.key === "forecast_vol_target");
  const buyHold = risk?.rows.find((row) => row.key === "buy_hold");
  if (!forecast || forecast.vol == null || risk.target == null) return null;
  const tails = buyHold?.kurtosis != null && forecast.kurtosis != null
    ? ` with thinner tails than buy-and-hold (excess kurtosis ${fmtNum(forecast.kurtosis, 1)} vs ${fmtNum(buyHold.kurtosis, 1)})`
    : "";
  const p = risk.vsBuyHold?.p;
  const sharpe = p != null ? (p >= ALPHA ? `, at a Sharpe not distinguishable from it (${fmtP(p)})` : `; Sharpe difference ${fmtP(p)}`) : "";
  return `Sizing on the forecast held realized vol at ${fmtPct(forecast.vol)} against a ${fmtNum(risk.target, 0)}% target${tails}${sharpe}`;
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
  const carryPart = pre != null && post != null
    ? pre < ALPHA && post >= ALPHA ? "Carry sorted WTI returns before 2007, not detectably after"
      : pre < ALPHA && post < ALPHA ? "Carry sorted WTI returns in both halves"
        : "Carry did not reliably sort WTI returns"
    : null;
  const lost = study?.groups.find((group) => group.key === "supply_lost");
  const threat = study?.groups.find((group) => group.key === "threat_only");
  const supplyPart = lost?.peakP != null && threat?.peakP != null
    ? lost.peakP < ALPHA && threat.peakP >= ALPHA
      ? "supply removal moved price beyond an ordinary date; threats alone did not"
      : `supply-removal and threat-only peaks vs placebo: ${fmtP(lost.peakP)} and ${fmtP(threat.peakP)}`
    : null;
  const text = join([carryPart, supplyPart]);
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : null;
};

/** METH: provenance in one line. */
export const methodAssertion = (lab) => {
  const files = asObject(lab?.generated_from?.files);
  const count = files ? Object.keys(files).length : 0;
  const through = asText(lab?.generated_from?.sessions_through);
  if (!count) return null;
  return `Every research figure is computed from ${count} source file${count === 1 ? "" : "s"} with SHA-256 digests`
    + (through ? `, sessions through ${fmtDate(through)}` : "");
};
