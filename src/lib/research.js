// Pure selectors over data.json (schema 3) and research.json (schema 1). See docs/DATA_CONTRACT.md.
// Every function tolerates a missing or malformed field and returns null / [] instead of throwing:
// research.json is optional, and one bad field must cost a figure, not the page.
import { ctIsoDate, num, toMs } from "./format";

export const asObject = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : null);
export const asArray = (value) => (Array.isArray(value) ? value : []);
export const asText = (value) => (typeof value === "string" && value.trim() ? value.trim() : null);
const finite = (values) => values.map(num).filter((value) => value != null);

/** research.json -> vol_lab, or null. */
export const getLab = (research) => asObject(asObject(research)?.vol_lab);

/** data.json -> vol_forecast pieces (each may be null). */
export const getVol = (data) => {
  const vol = asObject(data?.vol_forecast) ?? {};
  return {
    live: asObject(vol.live),
    validation: asObject(vol.validation),
    economic: asObject(vol.economic),
    recentOos: asObject(vol.recent_oos),
    liveRecord: asObject(vol.live_record),
  };
};

/** Model of record and challenger keys: research config first, then the live payload. */
export const getRoles = (lab, live) => ({
  champion: asText(lab?.config?.champion),
  challenger: asText(lab?.config?.challenger) ?? asText(live?.challenger?.key),
});

/** key -> display label, from vol_lab.models and the leaderboard rows. */
export const modelLabels = (lab) => {
  const labels = new Map();
  for (const model of [...asArray(lab?.models), ...asArray(lab?.leaderboard?.models)]) {
    const key = asText(model?.key);
    const label = asText(model?.label);
    if (key && label && !labels.has(key)) labels.set(key, label);
  }
  return labels;
};

/** A short label without the "(model of record)" suffix the payload appends for the champion. */
export const shortLabel = (label) => (typeof label === "string" ? label.replace(/\s*\(model of record\)\s*$/i, "") : label);

// ------------------------------------------------------------------------------ information sets

const INFO_IMPLIED = "implied";
const INFO_RANGE = "range";

/**
 * Which information beyond close-to-close returns a model uses: "implied" (OVX) and/or "range"
 * (intraday high/low). The contract carries no inputs field, so this is inferred from the model's
 * key and family: an "iv"/"ovx" token or the implied family means OVX; a "range" token means the
 * range-based measure; the machine-learning model is documented as using every feature; a
 * combination inherits its members (vol_lab.config.combo_members). An empty set = return-only.
 *
 * @param {{key:string, family?:string}} model
 * @param {{comboMembers?: string[], byKey?: Map<string, object>}} [context]
 * @returns {Set<string>}
 */
export const informationSet = (model, { comboMembers = [], byKey = new Map() } = {}, depth = 0) => {
  const key = asText(model?.key) ?? "";
  const family = (asText(model?.family) ?? "").toLowerCase();
  const info = new Set();
  const tokens = key.toLowerCase().split(/[_\s-]+/);
  if (family === "implied" || tokens.includes("iv") || tokens.includes("ovx")) info.add(INFO_IMPLIED);
  if (tokens.includes("range")) info.add(INFO_RANGE);
  if (family === "machine learning") { info.add(INFO_IMPLIED); info.add(INFO_RANGE); }
  if (family === "combination" && depth === 0) {
    for (const member of comboMembers) {
      const memberModel = byKey.get(member) ?? { key: member };
      informationSet(memberModel, { comboMembers, byKey }, depth + 1).forEach((item) => info.add(item));
    }
  }
  return info;
};

// ------------------------------------------------------------------------------------ leaderboard

/**
 * A timing control (leaderboard timing_control: true) uses information published after the
 * decision (same-day OVX): its losses are reported for comparison, but it is not a feasible
 * forecast, so it takes no part in the Model Confidence Set or in any "best model" statement.
 */
export const isTimingControl = (model) => model?.timing_control === true;

/** The lab key of the regression on OVX alone, the strongest simple benchmark on the page. */
export const OVX_ONLY_KEY = "ovx_only";

/**
 * The OVX-only regression's display name: the benchmark lab's label for it, so the hero, the
 * forecast panel and the leaderboard name it alike (the weekly validation fits the same model).
 */
export const ovxOnlyLabel = (lab) => shortLabel(modelLabels(lab).get(OVX_ONLY_KEY) ?? "OVX-only regression");

/**
 * Replaces each snake_case model key the lab knows ("har_iv_sameday") with its display label
 * wherever it appears as a word in payload prose. Single-word keys ("persistence") are ordinary
 * words in prose and are left alone.
 */
export const withModelLabels = (text, labels) => {
  if (typeof text !== "string" || !labels?.size) return text;
  const keys = [...labels.keys()].filter((key) => key.includes("_")).sort((a, b) => b.length - a.length);
  if (!keys.length) return text;
  const pattern = new RegExp(`\\b(${keys.map((key) => key.replace(/[^\w]/g, "\\$&")).join("|")})\\b`, "g");
  return text.replace(pattern, (key) => shortLabel(labels.get(key)));
};

/** The Model Confidence Set's confidence level in percent (90 for size 0.10), or null. */
export const mcsLevel = (lab) => {
  const size = num(lab?.config?.mcs?.size);
  return size != null && size > 0 && size < 1 ? Math.round((1 - size) * 100) : null;
};

/**
 * @typedef {object} LeaderRow
 * @property {string} key
 * @property {string} label
 * @property {string|null} family
 * @property {string|null} description
 * @property {number|null} qlike           full-sample daily QLIKE (lower is better)
 * @property {number|null} dmStat          Diebold-Mariano t vs the model of record (+ = better than it)
 * @property {number|null} dmP
 * @property {number|null} dmMin           min/max DM t across the 5-session sampling grids
 * @property {number|null} dmMax
 * @property {number|null} mcsP
 * @property {boolean} timingControl      see isTimingControl(); never in the MCS
 * @property {boolean} mcsEligible        false for timing controls and leaderboard.mcs_excludes
 * @property {boolean} inMcs              in the full-sample QLIKE Model Confidence Set
 * @property {boolean[]} grids            MCS membership on each sampling grid
 * @property {number|null} gridsIn
 * @property {number|null} utility        realized utility, % (higher is better)
 * @property {number|null} mzSlope
 * @property {number|null} mzSe
 * @property {"record"|"challenger"|null} role
 * @property {Set<string>} info           see informationSet()
 */

/** @returns {LeaderRow[]} */
export const leaderboardRows = (lab) => {
  const board = asObject(lab?.leaderboard);
  if (!board) return [];
  const { champion, challenger } = getRoles(lab, null);
  const models = asArray(board.models).filter((model) => asText(model?.key));
  const byKey = new Map(models.map((model) => [model.key, model]));
  const comboMembers = asArray(lab?.config?.combo_members).filter((key) => typeof key === "string");
  const included = new Set(asArray(board.mcs_qlike_included));
  const excluded = new Set(asArray(board.mcs_excludes));
  const byGrid = asObject(board.mcs_qlike_by_grid) ?? {};
  const gridKeys = Object.keys(byGrid).sort((a, b) => Number(a) - Number(b));
  return models.map((model) => {
    const timingControl = isTimingControl(model);
    const eligible = !timingControl && !excluded.has(model.key);
    const grids = eligible ? gridKeys.map((grid) => asArray(byGrid[grid]).includes(model.key)) : [];
    const dmByGrid = finite(asArray(model.dm_qlike_vs_har_iv_by_grid));
    return {
      key: model.key,
      label: asText(model.label) ?? model.key,
      family: asText(model.family),
      description: asText(model.description),
      qlike: num(model.qlike),
      dmStat: num(model.dm_qlike_vs_har_iv?.stat),
      dmP: num(model.dm_qlike_vs_har_iv?.p_value),
      dmMin: dmByGrid.length ? Math.min(...dmByGrid) : null,
      dmMax: dmByGrid.length ? Math.max(...dmByGrid) : null,
      dmByGrid: asArray(model.dm_qlike_vs_har_iv_by_grid).map(num),
      timingControl,
      mcsEligible: eligible,
      mcsP: eligible ? num(model.mcs_qlike_p) : null,
      inMcs: eligible && (model.in_mcs_qlike === true || included.has(model.key)),
      grids,
      gridsIn: eligible ? num(model.in_mcs_qlike_grids) ?? (gridKeys.length ? grids.filter(Boolean).length : null) : null,
      utility: num(model.realized_utility_pct),
      mzSlope: num(model.mincer_zarnowitz?.slope),
      mzSe: num(model.mincer_zarnowitz?.slope_se),
      mzP: num(model.mincer_zarnowitz?.p_unbiased),
      directionAcc: num(model.direction_acc_pct),
      qlikeByGrid: asArray(model.qlike_by_grid).map(num),
      role: model.key === champion ? "record" : model.key === challenger ? "challenger" : null,
      info: informationSet(model, { comboMembers, byKey }),
    };
  });
};

/**
 * Whether the leaderboard carries its Model Confidence Set (mcs_qlike_included). Without it no
 * survivor count is stated: an absent set is never reported as an empty one.
 */
export const hasMcsResults = (lab) => Array.isArray(asObject(lab?.leaderboard)?.mcs_qlike_included);

/**
 * Model Confidence Set headline numbers over the eligible models. `survivors` is null when the
 * payload carries no MCS results (never a count of zero it did not compute).
 * @returns {{total:number, eligible:number, survivors:number|null, level:number|null, gridCount:number,
 *   excluded: string[], champion: {label:string, inMcs:boolean, gridsIn:number|null, mcsP:number|null}|null}|null}
 */
export const mcsSummary = (lab) => {
  const rows = leaderboardRows(lab);
  if (!rows.length) return null;
  const eligible = rows.filter((row) => row.mcsEligible);
  const record = rows.find((row) => row.role === "record") ?? null;
  const available = hasMcsResults(lab);
  return {
    total: rows.length,
    eligible: eligible.length,
    survivors: available ? eligible.filter((row) => row.inMcs).length : null,
    level: mcsLevel(lab),
    gridCount: Math.max(0, ...eligible.map((row) => row.grids.length)),
    excluded: rows.filter((row) => !row.mcsEligible).map((row) => shortLabel(row.label)),
    champion: record && available
      ? { label: shortLabel(record.label), inMcs: record.inMcs, gridsIn: record.gridsIn, mcsP: record.mcsP }
      : null,
  };
};

// ------------------------------------------------------------------------------------------- VaR

const QUANTILE_LABELS = { fhs: "Filtered hist. sim.", normal: "Normal" };

/**
 * One row per VaR method x level from var_backtest.summary (pass counts over the 5-session
 * sampling grids), joined with grid 0's own test statistics.
 */
export const varRows = (lab) => {
  const backtest = asObject(lab?.var_backtest);
  const summary = asObject(backtest?.summary);
  if (!summary) return [];
  const labels = modelLabels(lab);
  const grid0 = asArray(backtest?.grid0?.methods);
  const { champion } = getRoles(lab, null);
  return Object.entries(summary).map(([id, stats]) => {
    const [method, levelText] = id.split("@");
    const level = num(levelText);
    const [model, quantile] = method.includes(":") ? method.split(":") : [method, null];
    const base = grid0.find((row) => row?.key === method && num(row?.level) === level) ?? null;
    const byGrid = finite(asArray(stats?.exceedances_by_grid));
    return {
      id,
      method,
      model,
      quantile,
      level,
      confidence: level != null ? Math.round((1 - level) * 100) : null,
      isRecord: model === champion,
      isHistorical: quantile == null && /historical/i.test(model),
      label: quantile == null ? (/historical/i.test(model) ? "Historical simulation" : model) : shortLabel(labels.get(model) ?? model),
      quantileLabel: quantile ? (QUANTILE_LABELS[quantile] ?? quantile) : "Empirical",
      expected: num(stats?.expected) ?? num(base?.kupiec?.expected),
      exceedances: num(base?.kupiec?.exceedances),
      n: num(base?.kupiec?.n),
      kupiecP: num(base?.kupiec?.p_value),
      independenceP: num(base?.christoffersen?.p_independence),
      dqP: num(base?.dq?.p_value),
      exceedMin: byGrid.length ? Math.min(...byGrid) : null,
      exceedMax: byGrid.length ? Math.max(...byGrid) : null,
      gridCount: byGrid.length || null,
      kupiecPass: num(stats?.kupiec_pass_grids),
      independencePass: num(stats?.independence_pass_grids),
      ccPass: num(stats?.conditional_coverage_pass_grids),
      dqPass: num(stats?.dq_pass_grids),
    };
  });
};

/** The model of record's VaR at a level: filtered historical simulation first (the series' method). */
export const recordVar = (rows, level) => {
  const candidates = rows.filter((row) => row.isRecord && row.level === level);
  return candidates.find((row) => row.quantile === "fhs") ?? candidates[0] ?? null;
};

// ---------------------------------------------------------------------------------------- series

/**
 * Weekly forecast-vs-realized rows. research.json vol_lab.series (2008-) is the history; data.json
 * recent_oos (the latest ~3 years, from the live pipeline) fills any newer weeks and is the whole
 * series when research.json is unavailable. `date` is the forecast date; `actual` is realized vol
 * over the following 5 sessions. Realized, forecast, OVX and the challenger come from the lab where
 * it has the week. The band is the production validation's wherever recent_oos has one, so the band
 * drawn on the newest weeks is the one the production coverage counts; `source` names the band's
 * pipeline: "lab" (the benchmark lab's adaptive conformal band) or "live" (the production validation's).
 *
 * @returns {{date:string, source:"lab"|"live", actual:number|null, forecast:number|null, lo:number|null,
 *   hi:number|null, ovx:number|null, challenger:number|null, rv5:number|null}[]}
 */
export const forecastRows = (lab, recentOos) => {
  const rows = new Map();
  const series = asObject(lab?.series);
  if (series) {
    asArray(series.date).forEach((date, index) => {
      if (typeof date !== "string") return;
      rows.set(date, {
        date,
        source: "lab",
        actual: num(series.actual?.[index]),
        forecast: num(series.har_iv?.[index]),
        lo: num(series.lo80?.[index]),
        hi: num(series.hi80?.[index]),
        ovx: num(series.ovx?.[index]),
        challenger: num(series.challenger?.[index]),
        rv5: num(series.rv5?.[index]),
      });
    });
  }
  const recent = asObject(recentOos);
  if (recent) {
    asArray(recent.date).forEach((date, index) => {
      if (typeof date !== "string") return;
      const lo = num(recent.lo80?.[index]);
      const hi = num(recent.hi80?.[index]);
      const actual = num(recent.actual?.[index]);
      const forecast = num(recent.forecast?.[index]);
      const rv5 = num(recent.rv5?.[index]);
      const row = rows.get(date);
      if (!row) {
        rows.set(date, { date, source: "live", actual, forecast, lo, hi, ovx: null, challenger: null, rv5 });
        return;
      }
      if (lo != null && hi != null) Object.assign(row, { lo, hi, source: "live" });
      if (row.actual == null) row.actual = actual;
      if (row.forecast == null) row.forecast = forecast;
      if (row.rv5 == null) row.rv5 = rv5;
    });
  }
  return [...rows.values()].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
};

/**
 * Coverage of the band as drawn: the share of forecastRows() weeks with a realized value and a band
 * whose realized vol fell inside it, overall and for each pipeline's band (`lab` and `live` are the
 * per-pipeline week counts; `bySource` adds how many were covered and the first and last date).
 * @returns {{n:number, covered:number, coverage:number|null, lab:number, live:number,
 *   bySource: Record<"lab"|"live", {n:number, covered:number, span:{start:string, end:string}|null}>}}
 */
export const bandCoverage = (rows) => {
  const scored = asArray(rows).filter((row) => row.actual != null && row.lo != null && row.hi != null);
  const inside = (row) => row.actual >= row.lo && row.actual <= row.hi;
  const covered = scored.filter(inside).length;
  const part = (source) => {
    const weeks = scored.filter((row) => row.source === source);
    return {
      n: weeks.length,
      covered: weeks.filter(inside).length,
      span: weeks.length ? { start: weeks[0].date, end: weeks[weeks.length - 1].date } : null,
    };
  };
  const bySource = { lab: part("lab"), live: part("live") };
  return {
    n: scored.length,
    covered,
    coverage: scored.length ? (covered / scored.length) * 100 : null,
    lab: bySource.lab.n,
    live: bySource.live.n,
    bySource,
  };
};

/**
 * Weekly 5-session returns with the forecast-scaled VaR (vol_lab.series ret5/var95/var99). `ret`
 * is a LOG return in percent (a sum of daily log returns), as is the VaR it is tested against.
 */
export const varSeries = (lab) => {
  const series = asObject(lab?.series);
  if (!series) return [];
  return asArray(series.date)
    .map((date, index) => ({
      date,
      ret: num(series.ret5?.[index]),
      var95: num(series.var95?.[index]),
      var99: num(series.var99?.[index]),
    }))
    .filter((row) => typeof row.date === "string" && row.ret != null);
};

/** A log return in percent as the simple return it implies, in percent (−80.6 log -> −55.3 simple). */
export const logToSimplePct = (value) => {
  const n = num(value);
  return n == null ? null : Math.expm1(n / 100) * 100;
};

/** Pairs of (OVX, next-week realized) for the VRP scatter. */
export const vrpPoints = (lab) => {
  const series = asObject(lab?.series);
  if (!series) return [];
  return asArray(series.date)
    .map((date, index) => ({ date, ovx: num(series.ovx?.[index]), actual: num(series.actual?.[index]) }))
    .filter((row) => row.ovx != null && row.actual != null && row.ovx > 0 && row.actual > 0);
};

// --------------------------------------------------------------------------------------- yearly

/** Yearly QLIKE for the given model keys: [{year, values: {key: qlike}}]. */
export const yearlyQlike = (lab, keys) =>
  asArray(lab?.yearly)
    .filter((row) => num(row?.year) != null)
    .map((row) => ({
      year: num(row.year),
      nDays: num(row.n_days),
      values: Object.fromEntries(keys.map((key) => [key, num(row[`${key}_qlike`])])),
    }));

/** How many years model `a` had lower QLIKE than model `b` (years where both exist). */
export const yearsBetter = (yearly, a, b) => {
  const both = yearly.filter((row) => row.values[a] != null && row.values[b] != null);
  return { wins: both.filter((row) => row.values[a] < row.values[b]).length, total: both.length };
};

/** Conformal coverage by year for a model: [{year, coverage}]. */
export const coverageByYear = (lab, key) => {
  const byYear = asObject(lab?.conformal?.[key]?.coverage_by_year_pct);
  if (!byYear) return [];
  return Object.entries(byYear)
    .map(([year, coverage]) => ({ year: num(year), coverage: num(coverage) }))
    .filter((row) => row.year != null && row.coverage != null)
    .sort((a, b) => a.year - b.year);
};

// ------------------------------------------------------------------------------------ live record

const PROMOTION_STATUSES = new Set(["accruing", "promote", "keep"]);

/**
 * live_record.promotion, normalized: where the challenger stands against the promotion rule.
 * `paired` independent resolved weeks have both forecasts; the test runs once `required` accrue.
 * The test is pinned to one pair, the model of record at `modelVersion` against the challenger
 * `challengerKey`; resolved weeks of any other version or challenger, or with no challenger
 * forecast, are counted in `excluded` and never enter it.
 */
export const promotionStatus = (raw) => {
  const promotion = asObject(raw);
  const status = asText(promotion?.status);
  if (!status || !PROMOTION_STATUSES.has(status)) return null;
  return {
    status,
    paired: num(promotion.n_independent_paired),
    required: num(promotion.required),
    dmStat: num(promotion.dm_stat),
    p: num(promotion.p_value),
    meanDifference: num(promotion.mean_qlike_difference),
    modelVersion: asText(promotion.model_version),
    challengerKey: asText(promotion.challenger_key),
    excluded: {
      otherVersion: num(promotion.n_excluded_other_version),
      otherChallenger: num(promotion.n_excluded_other_challenger),
      noChallenger: num(promotion.n_excluded_no_challenger),
    },
  };
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

/**
 * Independent (non-overlapping) resolved weeks the live record needs before the page compares the
 * two models' losses: below this many independent weeks a loss comparison is noise-dominated.
 */
export const LIVE_MIN_INDEPENDENT = 10;

/**
 * The record's hash chain (backend/vol_record.py chain_breaks): how many entries carry a link to the
 * entry before them, and the as_of of each broken link. Null when the summary predates the chain.
 */
const chainStatus = (raw) => {
  const chain = asObject(raw);
  if (!chain) return null;
  const breaks = asArray(chain.breaks).filter((day) => typeof day === "string" && ISO_DATE.test(day));
  return { linked: num(chain.n_linked), breaks: breaks.map((day) => day.slice(0, 10)) };
};

/**
 * The live forward record (data.json vol_forecast.live_record), normalized. `firstResult` is the
 * record's next_result_due: the earliest target_end of a pending call whose window does not span
 * a contract roll, or null when no result is due.
 *
 * Two samples of QLIKE appear. `paired` compares the two models on the same resolved calls (the
 * pinned pair's, challenger.n of them); `champion` scores the model of record on every resolved
 * call of its pinned version (champion.n), a larger sample, and is only ever shown labelled as such.
 * Calls are daily and their five-session windows overlap: `independent` (n_independent) counts the
 * resolved calls whose windows do not, the effective sample, and only it is ever called "weeks".
 * `recent` rows pass through as published (newest first), with the audit fields of
 * vol_record._recent when the payload has them.
 */
export const liveRecordSummary = (record) => {
  const lr = asObject(record);
  if (!lr) return null;
  const recent = asArray(lr.recent).filter((row) => asObject(row));
  const started = asText(lr.started);
  const due = asText(lr.next_result_due);
  const firstResult = due && ISO_DATE.test(due) ? due.slice(0, 10) : null;
  const champion = asObject(lr.champion) ?? {};
  const challenger = asObject(lr.challenger) ?? {};
  return {
    started,
    recorded: num(lr.n_recorded),
    resolved: num(lr.n_resolved),
    pending: num(lr.n_pending),
    skipped: num(lr.n_skipped_roll),
    expired: num(lr.n_expired),
    lateCommit: num(lr.n_late_commit),
    independent: num(lr.n_independent),
    champion,
    challenger,
    paired: {
      n: num(challenger.n),
      record: num(challenger.champion_qlike),
      challenger: num(challenger.qlike),
    },
    recordAll: { n: num(champion.n), qlike: num(champion.qlike), modelVersion: asText(champion.model_version) },
    recent,
    promotion: promotionStatus(lr.promotion),
    chain: chainStatus(lr.chain),
    promotionRule: asText(lr.promotion_rule),
    note: asText(lr.note),
    directionNote: asText(lr.direction_note),
    firstResult,
  };
};

/**
 * Whether a recorded call (a live_record.recent row) is or will be scored. Its status, except that a
 * pending call whose window spans a contract roll is "pending_roll": resolution will close it as
 * skipped_roll, never scored. The row's own spans_roll (vol_record._recent) decides when present;
 * for rows without it, the record's next_result_due is the earliest target_end of a pending call
 * whose window does not span a roll, so with calls pending and none due every pending window spans
 * one, and a pending call that ends before the date due spans one too. Null without a status.
 *
 * @returns {"pending"|"pending_roll"|"resolved"|"skipped_roll"|"late_commit"|"expired"|null}
 */
export const callScoring = (row, record = null) => {
  const status = asText(row?.status);
  if (status !== "pending") return status;
  if (typeof row.spans_roll === "boolean") return row.spans_roll ? "pending_roll" : "pending";
  const lr = asObject(record);
  if (!lr || !("next_result_due" in lr)) return "pending";
  const due = asText(lr.next_result_due);
  const end = asText(row.target_end);
  if (due == null) return num(lr.n_pending) > 0 ? "pending_roll" : "pending";
  return ISO_DATE.test(due) && end && ISO_DATE.test(end) && end.slice(0, 10) < due.slice(0, 10) ? "pending_roll" : "pending";
};

// ---------------------------------------------------------------------------------- leak anatomy

const EXPERIMENT_LABELS = {
  synthetic_random_walk: "Random walk",
  block_shuffled_cl: "Shuffled CL=F",
  real_cl_5y_window: "Real CL=F, 5y",
};
const MODEL_LABELS = { nearest_neighbour: "1-NN", decision_tree: "Tree", random_forest: "Forest" };
// Experiments on symmetric i.i.d. returns, where 50% is the no-skill accuracy. On CL=F returns it is
// each run's base rate (the share of the more common sign), reported per arm as base_rate_pct_mean.
const SYMMETRIC_EXPERIMENTS = new Set(["synthetic_random_walk"]);

/**
 * research.json leak_anatomy, normalized: the analytic sign-agreement ladder by training row and
 * one row per experiment x model with unpurged and purged accuracy / Sharpe. The experiments'
 * Sharpe ratios are gross and the retracted model's is after costs; `sharpeBasis` carries both
 * labels as the artifact states them (design.sharpe, retracted_reference.sharpe_basis).
 */
export const leakAnatomy = (research) => {
  const anatomy = asObject(research?.leak_anatomy);
  if (!anatomy) return null;
  const ladder = asArray(anatomy.analytic?.by_training_row)
    .filter((row) => asText(row?.training_row) && num(row?.p_sign_agree_pct) != null)
    .map((row) => ({
      row: row.training_row,
      shared: num(row.shared_daily_returns),
      correlation: num(row.label_correlation),
      agree: num(row.p_sign_agree_pct),
      known: row.label_known_at_decision === true,
    }));
  const experiments = [];
  for (const [experiment, models] of Object.entries(asObject(anatomy.experiments) ?? {})) {
    for (const [model, runs] of Object.entries(asObject(models) ?? {})) {
      const unpurged = asObject(runs?.unpurged);
      const purged = asObject(runs?.purged);
      if (!unpurged || !purged) continue;
      const symmetric = SYMMETRIC_EXPERIMENTS.has(experiment);
      experiments.push({
        key: `${experiment}:${model}`,
        experiment,
        model,
        label: `${EXPERIMENT_LABELS[experiment] ?? experiment} · ${MODEL_LABELS[model] ?? model}`,
        experimentLabel: EXPERIMENT_LABELS[experiment] ?? experiment,
        modelLabel: MODEL_LABELS[model] ?? model,
        runs: num(unpurged.n_runs),
        unpurged: num(unpurged.accuracy_pct_mean),
        unpurgedRange: asArray(unpurged.accuracy_pct_range).map(num),
        unpurgedSharpe: num(unpurged.sharpe_mean),
        purged: num(purged.accuracy_pct_mean),
        purgedRange: asArray(purged.accuracy_pct_range).map(num),
        purgedSharpe: num(purged.sharpe_mean),
        purgedP: num(purged.pooled_binomial_p_value_vs_50),
        baseRate: num(unpurged.base_rate_pct_mean ?? purged.base_rate_pct_mean),
        unpurgedBeatBase: num(unpurged.runs_beating_base_rate_5pct),
        purgedBeatBase: num(purged.runs_beating_base_rate_5pct),
        purgedRuns: num(purged.n_runs),
        // The accuracy a model with no skill expects: 50% on symmetric noise, the base rate on CL=F.
        noSkill: symmetric ? 50 : num(unpurged.base_rate_pct_mean ?? purged.base_rate_pct_mean),
        symmetric,
        isHeadline: experiment === "synthetic_random_walk" && model === anatomy.headline_model,
      });
    }
  }
  const reference = asObject(anatomy.retracted_reference);
  const design = asObject(anatomy.design);
  // design.sharpe opens with the basis ("gross (no transaction costs): direction x ..."); the rest is
  // the formula.
  const sharpeText = asText(design?.sharpe);
  const designBasis = sharpeText?.includes(":") ? asText(sharpeText.split(":")[0]) : null;
  return {
    ladder,
    experiments,
    sharpeBasis: {
      experiments: designBasis,
      reference: asText(reference?.sharpe_basis),
    },
    upSharePct: num(design?.cl_5_session_up_share_pct),
    formula: asText(anatomy.analytic?.formula),
    headlineModel: asText(anatomy.headline_model),
    unpurgedAcc: num(anatomy.unpurged_dir_acc_pct_mean),
    purgedAcc: num(anatomy.purged_dir_acc_pct_mean),
    unpurgedSharpe: num(anatomy.unpurged_sharpe_mean),
    sims: num(anatomy.sims),
    signAgreement: num(anatomy.analytic_sign_agreement_pct),
    reference: reference ? { accuracy: num(reference.accuracy_pct), sharpe: num(reference.sharpe), n: num(reference.n) } : null,
    conclusion: asText(anatomy.conclusion),
  };
};

// --------------------------------------------------------------------------------- supply study

const EVENT_WORDS = { opec: "OPEC", irgc: "IRGC", us: "US", uk: "UK", uae: "UAE", jan: "Jan", feb: "Feb", mar: "Mar",
  apr: "Apr", jun: "Jun", jul: "Jul", aug: "Aug", sep: "Sep", oct: "Oct", nov: "Nov", dec: "Dec" };

/** An event id as a name: "hurricane_katrina_2005" -> "Hurricane Katrina (2005)". */
export const eventName = (id) => {
  if (typeof id !== "string" || !id) return id;
  const words = id.split("_").filter(Boolean);
  const year = /^\d{4}$/.test(words[words.length - 1] ?? "") ? words.pop() : null;
  const name = words.map((word) => EVENT_WORDS[word] ?? `${word.charAt(0).toUpperCase()}${word.slice(1)}`).join(" ");
  return year ? `${name} (${year})` : name;
};

/** research.json supply_study, normalized: the placebo and each event group vs that placebo. */
export const supplyStudy = (research) => {
  const study = asObject(research?.supply_study);
  if (!study) return null;
  const placebo = asObject(study.placebo);
  const pv = (value, side) => (asObject(value) ? num(value[side]) : num(value));
  const groups = asArray(study.groups)
    .filter((group) => asObject(group) && asText(group.key))
    .map((group) => ({
      key: group.key,
      name: asText(group.name) ?? group.key,
      definition: asText(group.definition),
      n: num(group.n),
      peak: num(group.median_peak_pct),
      settle: num(group.median_settle_pct),
      ciPeak: asArray(group.ci_peak).map(num),
      excessPeak: num(group.excess_peak_vs_placebo_pct),
      peakP: pv(group.p_value_vs_placebo, "peak"),
      settleP: pv(group.p_value_vs_placebo, "settle"),
      peakPYear: pv(group.p_value_vs_year_matched_placebo, "peak"),
      settlePYear: pv(group.p_value_vs_year_matched_placebo, "settle"),
      eventIds: asArray(group.event_ids).filter((id) => asText(id)),
    }))
    .map((group) => ({
      ...group,
      // The study's rule: a group differs from an ordinary date only if both placebo tests reject,
      // so its p is the larger of the two (as in the research ledger). Null unless both exist.
      peakPRule: group.peakP != null && group.peakPYear != null ? Math.max(group.peakP, group.peakPYear) : null,
      settlePRule: group.settleP != null && group.settlePYear != null ? Math.max(group.settleP, group.settlePYear) : null,
    }));
  const overlap = asObject(study.overlapping_events);
  const pairs = asArray(overlap?.pairs)
    .map((pair) => ({ events: asArray(pair?.events).filter((id) => asText(id)), apart: num(pair?.sessions_apart) }))
    .filter((pair) => pair.events.length === 2);
  for (const group of groups) {
    const members = new Set(group.eventIds);
    group.overlaps = pairs.filter((pair) => pair.events.every((id) => members.has(id)));
  }
  const between = asObject(study.between_groups);
  return {
    method: asText(study.method),
    placebo: placebo ? {
      n: num(placebo.n),
      peak: num(placebo.median_peak_pct),
      settle: num(placebo.median_settle_pct),
      shareUp: num(placebo.share_peak_positive_pct),
    } : null,
    groups,
    between: between ? {
      groups: asArray(between.groups),
      peakDiff: num(between.median_peak_difference_pct),
      peakP: pv(between.p_value, "peak"),
      settleP: pv(between.p_value, "settle"),
    } : null,
    overlapping: overlap ? { definition: asText(overlap.definition), pairs } : null,
    superseded: Object.values(asObject(study.superseded_claims) ?? {}).filter((text) => asText(text)),
    conclusion: asText(study.conclusion),
  };
};

/** The US Central calendar date the snapshot was frozen on ("YYYY-MM-DD"), or null. Pure. */
export const snapshotDay = (data) => ctIsoDate(toMs(data?.frozen_at));

// --------------------------------------------------------------------------------------- ledger

/**
 * research.json ledger: every tested hypothesis with multiplicity-adjusted p-values. A row
 * `selectedAfterSearch` tests a model chosen as the best of a field on the same sample: it is left
 * out of the multiplicity family, so its q, Holm p and survival flags are null by design. A row
 * `underpowered` failed to reject with a test whose recorded power is below its target.
 */
export const researchLedger = (research) => {
  const ledger = asObject(research?.ledger);
  const summary = asObject(ledger?.summary) ?? {};
  // The flags are read from each row, or from the summary's lists of row ids.
  const afterSearch = new Set(asArray(summary.selected_after_search));
  const underpowered = new Set(asArray(summary.underpowered));
  const rows = asArray(ledger?.rows)
    .filter((row) => asObject(row) && (asText(row.id) || asText(row.hypothesis)))
    .map((row) => ({
      ...row,
      statistic: num(row.statistic_value),
      p: num(row.p_value),
      q: num(row.q_bh),
      holm: num(row.p_holm),
      selectedAfterSearch: row.selected_after_search === true || afterSearch.has(row.id),
      underpowered: row.underpowered === true || underpowered.has(row.id),
    }));
  if (!rows.length) return null;
  return {
    rows,
    byId: new Map(rows.filter((row) => asText(row.id)).map((row) => [row.id, row])),
    method: asText(ledger?.method),
    alpha: num(ledger?.alpha),
    hurdle: num(ledger?.hlz_t),
    byFamily: asObject(summary.by_family),
    byStatus: asObject(summary.by_status) ?? rows.reduce((acc, row) => ({ ...acc, [row.status]: (acc[row.status] ?? 0) + 1 }), {}),
    supported: asArray(summary.supported).length || rows.filter((row) => row.status === "supported").length,
    // Supported rows outside the multiplicity family (selected after a search): never counted as
    // surviving an adjustment they did not take part in.
    supportedAfterSearch: rows.filter((row) => row.status === "supported" && row.selectedAfterSearch).length,
    survivingBh: Array.isArray(summary.supported_surviving_bh_5pct) ? summary.supported_surviving_bh_5pct.length : null,
    clearingHurdle: Array.isArray(summary.supported_clearing_hlz_t3) ? summary.supported_clearing_hlz_t3.length : null,
    preSpecified: num(summary.pre_specified_rows),
    underpowered: rows.filter((row) => row.underpowered).length,
  };
};

/** Ledger statuses as words ("not_supported" -> "not supported"). */
export const ledgerStatusLabel = (status) => (typeof status === "string" ? status.replace(/_/g, " ") : status);

// Internal names in the ledger's test and statistic text, with the words the page uses for them.
const LEDGER_TERMS = [
  [/\bwith_macro vs no_macro\b/g, "with vs without macro features"],
  [/\bwith_macro accuracy\b/g, "accuracy with macro features"],
  [/\bsame_day vs lagged_1d\b/g, "same-day vs one-day-lagged context"],
  [/\s*\(supply_shock_study\.json method\)/g, " (the supply study's method)"],
];

/**
 * Ledger prose (hypothesis, test or statistic name) in display words: the terms above, the Model
 * Confidence Set's level in place of its formula, each lab model key as its label, and any other
 * snake_case name with spaces.
 */
export const ledgerText = (text, { labels = new Map(), mcsLevel = null } = {}) => {
  if (typeof text !== "string") return text;
  const named = LEDGER_TERMS.reduce((out, [pattern, words]) => out.replace(pattern, words), text)
    .replace(/\s*\(mcs_members\) is the 1 - mcs_size confidence set/g,
      mcsLevel != null ? ` is the ${mcsLevel}% confidence set` : " is its own confidence set");
  return withModelLabels(named, labels).replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (key) => key.replace(/_/g, " "));
};

// ---------------------------------------------------------------------------------- vol target

/** data.json vol_forecast.economic.vol_target_overlay: risk profile of each sizing rule. */
export const volTargetRisk = (overlay) => {
  const risk = asObject(overlay?.risk);
  if (!risk) return null;
  const LABELS = { buy_hold: "Buy and hold", trailing_vol_target: "Trailing-vol target", forecast_vol_target: "Forecast-vol target" };
  const rows = Object.entries(risk)
    .filter(([, value]) => asObject(value))
    .map(([key, value]) => ({
      key,
      label: LABELS[key] ?? key,
      sharpe: num(value.sharpe),
      vol: num(value.realized_vol_pct),
      volOfVol: num(value.vol_of_vol_pct),
      kurtosis: num(value.excess_kurtosis),
      worstWeek: num(value.worst_week_pct),
      es: num(value.expected_shortfall_97_5_pct),
      maxDrawdown: num(value.max_drawdown_pct),
    }));
  const tests = asObject(overlay?.sharpe_difference_tests) ?? {};
  const test = (key) => {
    const value = asObject(tests[key]);
    return value ? { difference: num(value.difference), p: num(value.p_value), ci: asArray(value.ci95).map(num) } : null;
  };
  return {
    rows,
    target: num(overlay?.target_vol_pct),
    weeks: num(overlay?.n_weeks),
    method: asText(overlay?.method),
    vsBuyHold: test("forecast_minus_buy_hold"),
    vsTrailing: test("forecast_minus_trailing"),
  };
};

/**
 * A Diebold-Mariano t from the weekly validation (har_vs_*_qlike_dm, positive when the model of
 * record has the lower loss) in the page's convention, the leaderboard's: positive when the named
 * benchmark has the lower loss than the model of record.
 */
const versusRecord = (value) => {
  const n = num(value);
  return n == null ? null : -n;
};

/**
 * The strongest simple benchmark in the weekly validation: a regression on OVX alone. `dm` is
 * the Diebold-Mariano t in the page's convention (positive = lower loss than the model of record).
 */
export const ovxOnlyBenchmark = (validation) => {
  const qlike = num(validation?.ovx_only_qlike);
  if (qlike == null) return null;
  return { qlike, dm: versusRecord(validation?.har_vs_ovx_only_qlike_dm), p: num(validation?.har_vs_ovx_only_qlike_dm_p_value) };
};

/** Persistence in the weekly validation, its DM t in the page's convention (see ovxOnlyBenchmark()). */
export const persistenceBenchmark = (validation) => {
  const qlike = num(validation?.persistence_qlike);
  if (qlike == null) return null;
  return { qlike, dm: versusRecord(validation?.har_vs_persistence_qlike_dm) };
};

/**
 * data.json vol_forecast.economic.variance_risk_premium with its zero-premium null. With no
 * premium a 20-session realized vol still falls below implied vol in most periods and the mean
 * gap in vol points is positive (Jensen's inequality), so the share of positive periods is read
 * against `nullShare` (with its one-sided test `nullP`), never against 50%. The premium in
 * variance units, with its interval, is the one that measures a premium directly.
 */
export const vrpSummary = (vrp) => {
  const block = asObject(vrp);
  if (!block) return null;
  const zero = asObject(block.zero_premium_null);
  const ci = asArray(block.mean_premium_variance_ci95).map(num);
  return {
    n: num(block.n_periods),
    share: num(block.share_positive_pct),
    meanVolPts: num(block.mean_premium_vol_pts),
    meanVariance: num(block.mean_premium_variance_pts),
    varianceCi: ci.length === 2 && ci.every((value) => value != null) ? ci : null,
    nullShare: num(zero?.share_positive_pct),
    nullMeanVolPts: num(zero?.mean_premium_vol_pts),
    nullP: num(zero?.p_value_one_sided),
    excessShare: num(zero?.excess_share_pct),
    caveat: asText(block.vol_points_caveat),
    worst: num(block.worst_period_vol_pts),
  };
};

// ------------------------------------------------------------------------------------ postmortem

/** research.json postmortem window ("five_year" | "ten_year") -> {leaked, purged, config} or null. */
export const postmortemWindow = (research, windowKey) => {
  const window = asObject(asObject(research?.postmortem)?.[windowKey]);
  if (!window) return null;
  const run = (raw) => {
    const value = asObject(raw);
    if (!value) return null;
    return {
      n: num(value.n),
      accuracy: num(value.direction_acc_pct),
      p: num(value.p_value),
      sharpe: num(value.sharpe),
      pnl: num(value.total_pnl_usd),
      maxDrawdown: num(value.max_drawdown_usd),
      first: asText(value.first)?.slice(0, 10) ?? null,
      last: asText(value.last)?.slice(0, 10) ?? null,
      equity: asArray(value.equity)
        .filter((point) => Array.isArray(point) && typeof point[0] === "string" && num(point[1]) != null)
        .map(([date, pnl]) => ({ date: date.slice(0, 10), pnl: num(pnl) })),
    };
  };
  return {
    leaked: run(window.leaked),
    purged: run(window.purged),
    purgeRows: num(window.config?.purge_rows_by_horizon?.["1w"]),
    trainWindow: num(window.config?.train_window),
  };
};
