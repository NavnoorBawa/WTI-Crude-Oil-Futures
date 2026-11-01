// Coverage by regime at the decision (CALB; ROADMAP 2.2), pure: the lab's regime labels, the banded
// models' coverage and interval scores by regime, and the HAC Wald tests of equal miss rates, read from
// vol_lab.regimes and vol_lab.conformal.<model>. Every number is read from the payload; each verdict is
// worded at the strength its test supports, and no single regime's coverage ever carries one.
import { ALPHA, ALPHA_LOOSE } from "./assertions";
import { NBSP, fmtInt, fmtPValue, fmtPct, num } from "./format";
import { asArray, asObject } from "./research";

/**
 * The splits in the lab's order, with how the page names each (in a sentence, in a caption, over a column
 * group), its levels (in a table row) and its levels as column heads, and the MODL view key that shows it.
 */
export const REGIME_SPLITS = [
  { key: "ovx_tercile", view: "ovx", name: "OVX thirds", short: "OVX thirds",
    group: "OVX of the session before, thirds of its history to date", head: "OVX thirds",
    levels: { low: "OVX, low third", mid: "OVX, middle third", high: "OVX, high third" },
    columns: { low: "Low", mid: "Middle", high: "High" } },
  { key: "ovx_rv_gap", view: "gap", name: "OVX/RV5 (log) against its median", short: "OVX/RV5 halves",
    group: "OVX/RV5 (log) against its trailing median", head: "OVX/RV5 vs median",
    levels: { at_or_below: "OVX/RV5 at or below median", above: "OVX/RV5 above median" },
    columns: { at_or_below: "At or below", above: "Above" } },
  { key: "rv5_tercile", view: "rv5", name: "RV5 thirds", short: "RV5 thirds",
    group: "RV5 to the decision, thirds of its history to date", head: "RV5 thirds",
    levels: { low: "RV5, low third", mid: "RV5, middle third", high: "RV5, high third" },
    columns: { low: "Low", mid: "Middle", high: "High" } },
  { key: "target_spans_roll", view: "roll", name: "Contract roll in the target week", short: "roll windows",
    group: "Contract roll in the target week", head: "Target week",
    levels: { no: "No roll in the target week", yes: "Roll in the target week" },
    columns: { no: "No roll", yes: "Roll" } },
];

/** MODL's views: the leaderboard on all days, or its QLIKE split by one regime (the URL's modl key). */
export const MODL_VIEWS = [
  { value: "all", label: "All", name: "All days", title: "The leaderboard on every out-of-sample day" },
  ...REGIME_SPLITS.map((split) => ({
    value: split.view, label: { ovx: "OVX", gap: "OVX/RV5", rv5: "RV5", roll: "Roll" }[split.view],
    name: `by ${{ ovx: "OVX", gap: "OVX/RV5", rv5: "RV5", roll: "roll" }[split.view]}`,
    title: `QLIKE by regime: ${split.group}`, split: split.key,
  })),
];

/**
 * How the labels are made, from vol_lab.regimes (one sentence, shared by CALB and MODL). Null without the
 * block.
 */
export const regimeLabelText = (lab) => {
  const variables = asObject(lab?.regimes?.variables);
  if (!variables) return null;
  const window = num(variables.ovx_rv_gap?.window_sessions);
  return "Regimes are labelled at the decision from what was known then: OVX of the session before and RV5 of the five returns "
    + "to the decision against thirds of every earlier session's value (so a third's OVX range moves over time), "
    + `OVX/RV5 (log) against its median over the previous ${window != null ? `${fmtInt(window)} sessions` : "sessions"}, `
    + "and whether the target week holds a contract roll.";
};

/**
 * One split of MODL's regime view: its levels with the days each holds (vol_lab.regimes.n_days_by_regime),
 * or null when the payload lacks the split or no model carries its QLIKE.
 */
export const regimeLossSplit = (lab, rows, view) => {
  const split = REGIME_SPLITS.find((item) => item.view === view);
  const levels = asArray(lab?.regimes?.variables?.[split?.key]?.levels).filter((level) => typeof level === "string");
  if (!split || !levels.length || !asArray(rows).some((row) => asObject(row?.qlikeByRegime?.[split.key]))) return null;
  return {
    key: split.key,
    view: split.view,
    group: split.group,
    head: split.head,
    levels: levels.map((level) => ({
      key: level,
      label: split.columns[level] ?? level,
      n: num(lab.regimes.n_days_by_regime?.[split.key]?.[level]),
    })),
  };
};

/** A row's QLIKE in one regime, or null. */
export const regimeQlike = (row, splitKey, level) => num(row?.qlikeByRegime?.[splitKey]?.[level]);

/**
 * The footnote under MODL's regime view: it is descriptive, and the challenger's lower loss than the model of
 * record in k of n regimes (counted over every split the payload carries) repeats the full-sample comparison
 * that picked it. Null without both roles' regime losses.
 */
export const regimeLossNote = (lab, rows, { recordName, challengerName }) => {
  const record = asArray(rows).find((row) => row?.role === "record");
  const challenger = asArray(rows).find((row) => row?.role === "challenger");
  if (!record || !challenger) return null;
  let cells = 0;
  let lower = 0;
  for (const split of REGIME_SPLITS) {
    for (const level of asArray(lab?.regimes?.variables?.[split.key]?.levels)) {
      const ours = regimeQlike(challenger, split.key, level);
      const theirs = regimeQlike(record, split.key, level);
      if (ours == null || theirs == null) continue;
      cells += 1;
      if (ours < theirs) lower += 1;
    }
  }
  if (!cells) return null;
  return `QLIKE by regime is descriptive: no test is run within a regime. ${challengerName} was picked as the challenger `
    + `for its loss on this whole sample, so its lower QLIKE than ${recordName} in ${fmtInt(lower)} of ${fmtInt(cells)} regimes `
    + "repeats that comparison split by regime; it is not new evidence, and nothing here re-selects it.";
};

/**
 * One banded model's coverage by regime: per split, its levels (days, inside, coverage, misses above and
 * below, interval scores, the regime's own HAC p and the per-grid binomial range) and the split's test
 * (Wald, df, p, lags, robustness). [] when the payload carries no regime blocks for the model.
 */
export const regimeCoverage = (lab, key) => {
  const conformal = asObject(lab?.conformal?.[key]);
  const variables = asObject(lab?.regimes?.variables);
  if (!conformal || !variables || !asObject(conformal.conditional_coverage)) return [];
  return REGIME_SPLITS.map((split) => {
    const test = asObject(conformal.conditional_coverage?.[split.key]);
    const levels = asArray(variables[split.key]?.levels).filter((level) => typeof level === "string");
    if (!test || !levels.length) return null;
    const cells = new Map(asArray(test.cells).map((cell) => [cell?.regime, cell]));
    const robust = asObject(test.robustness) ?? {};
    return {
      key: split.key,
      name: split.name,
      short: split.short,
      levels: levels.map((level) => {
        const cell = asObject(cells.get(level)) ?? {};
        const binomial = asArray(cell.binomial_p_by_grid).map(num).filter((p) => p != null);
        return {
          key: level,
          label: split.levels[level] ?? level,
          n: num(conformal.n_days_by_regime?.[split.key]?.[level]),
          inside: num(cell.inside),
          coverage: num(conformal.coverage_by_regime_pct?.[split.key]?.[level]),
          above: num(conformal.miss_above_by_regime_pct?.[split.key]?.[level]),
          below: num(conformal.miss_below_by_regime_pct?.[split.key]?.[level]),
          score: num(conformal.interval_score_by_regime?.[split.key]?.[level]),
          logScore: num(conformal.log_interval_score_by_regime?.[split.key]?.[level]),
          hacP: num(cell.hac_p),
          binomial: binomial.length ? { min: Math.min(...binomial), max: Math.max(...binomial), n: binomial.length } : null,
        };
      }),
      test: {
        wald: num(test.wald), df: num(test.df), p: num(test.p_value), lags: num(test.hac_lags),
        p20: num(robust.hac_lags_20?.p_value), p40: num(robust.hac_lags_40?.p_value), pAll: num(robust.all_cells?.p_value),
        pByGrid: asArray(robust.by_grid?.p_value).map(num),
      },
    };
  }).filter(Boolean);
};

const band = (p) => (p < ALPHA ? 0 : p < ALPHA_LOOSE ? 1 : 2);

/** "p = 0.083" with three decimals, as the tables and the ledger print it (never rounded onto a threshold). */
export const pEq = (value) => {
  const text = fmtPValue(value, { digits: 3 });
  return /^[<>]/.test(text) ? `p${NBSP}${text}` : `p${NBSP}=${NBSP}${text}`;
};

/**
 * A split's verdict, worded at its test's strength: "significant at 5% (...)", "inconclusive (...)" or
 * null when nothing is detected (the caller groups those). The ledger's q and the first robustness check
 * that lands in another band are added in brackets; a robustness clause never upgrades the verdict.
 */
export const regimeVerdict = (split, ledgerRow) => {
  const { p, p20, p40, pAll } = split.test;
  if (p == null) return null;
  const parts = [pEq(p)];
  const q = num(ledgerRow?.q);
  if (band(p) < 2 && q != null) parts.push(`q${NBSP}=${NBSP}${fmtPValue(q, { digits: 3 })} after the ledger's adjustment`);
  const other = [[p20, "with 20 HAC lags"], [p40, "with 40 HAC lags"], [pAll, "with every regime tested against the target"]]
    .find(([value]) => value != null && band(value) !== band(p));
  if (other && band(p) < 2) parts.push(`${pEq(other[0])} ${other[1]}`);
  if (band(p) === 0) return `the test of equal coverage across ${split.short} is significant at 5% (${parts.join("; ")})`;
  if (band(p) === 1) return `the test of equal coverage across ${split.short} is inconclusive (${parts.join("; ")})`;
  return null;
};

/**
 * The CALB statement on regimes for the model of record: the coverage range of each split with a verdict
 * worth stating, the splits where no dependence is detected grouped with their p-values, and the rule that
 * a dependence is reported, never used to change the band. Null without the regime blocks.
 */
export const regimeCoverageAssertion = (lab, champion, ledger) => {
  const splits = regimeCoverage(lab, champion);
  if (!splits.length) return null;
  const stated = [];
  const quiet = [];
  for (const split of splits) {
    const verdict = regimeVerdict(split, ledger?.byId?.get(`band_regime_${split.key}_${champion}`));
    const coverage = split.levels.map((level) => level.coverage).filter((value) => value != null);
    if (verdict && coverage.length) {
      stated.push(`Across ${split.short} at the decision the model of record's band covered `
        + `${fmtPct(Math.min(...coverage))} to ${fmtPct(Math.max(...coverage))}; ${verdict}.`);
    } else if (split.test.p != null) {
      quiet.push(split);
    }
  }
  const names = quiet.map((split) => split.short);
  const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} or ${names.at(-1)}` : names[0];
  const none = quiet.length
    ? `${stated.length ? "No" : "For the model of record's band, no"} dependence detected across ${list} `
      + `(${quiet.map((split) => pEq(split.test.p)).join(", ")}).`
    : "";
  return [...stated, none, "A dependence found here is reported, never used to change the band."].filter(Boolean).join(" ");
};
