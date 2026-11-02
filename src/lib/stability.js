// STAB's tests over time and by state (ROADMAP 2.3), pure: the rolling Diebold-Mariano t with its fluctuation
// band, the cumulative QLIKE differences, and the Giacomini-White and fluctuation results, read from
// vol_lab.stability, the leaderboard's per-model blocks and the ledger. Every number comes from the payload; each
// sentence is worded by the flags the payload holds, at the strength its test supports.
import { ALPHA, ALPHA_LOOSE } from "./assertions";
import { NBSP, fmtDate, fmtInt, fmtNum, fmtPValue, fmtSigned, num } from "./format";
import { asArray, asObject, asText, getLab, leaderboardRows, modelLabels, shortLabel } from "./research";

/** STAB's views of the over-time figure: the rolling test with its band, or the cumulative difference (?stab=). */
export const STAB_VIEWS = [
  { value: "test", label: "Rolling test", name: "rolling test" },
  { value: "cumulative", label: "Cumulative", name: "cumulative difference" },
];

const band = (p) => (p < ALPHA ? 0 : p < ALPHA_LOOSE ? 1 : 2);
const pEq = (value) => {
  const text = fmtPValue(value, { digits: 3 });
  return /^[<>]/.test(text) ? `p${NBSP}${text}` : `p${NBSP}=${NBSP}${text}`;
};

/** The fluctuation setup: window, band, the published table value and the simulated null; null without it. */
export const fluctuationSetup = (lab) => {
  const block = asObject(lab?.stability?.fluctuation);
  const c5 = num(block?.critical_values?.five_pct);
  if (!block || c5 == null) return null;
  return {
    c5, c10: num(block.critical_values.ten_pct), mcSe: num(block.critical_values.mc_se_five_pct),
    window: num(block.window_sessions), mu: num(block.mu), nDays: num(block.n_days), draws: num(block.null?.draws),
    gr2010: num(block.gr2010_table1?.five_pct), firstWindowEnd: asText(block.first_window_end),
    lengths: asArray(block.search?.over_mu).length,
  };
};

const modelKeyOf = (lab, name) => (name === "challenger" ? asText(lab?.config?.challenger) : name);

/**
 * The drawn comparisons, in the payload's order (vol_lab.stability.series): each with its model, its weekly path
 * (`fluct_t_<name>` or `cum_qlike_diff_<name>`), and, for the rolling test, its leaderboard block.
 */
const seriesRows = (lab, kind) => {
  const labels = modelLabels(lab);
  const blocks = new Map(asArray(lab?.leaderboard?.models).map((model) => [model?.key, model]));
  const dates = asArray(lab?.series?.date);
  return asArray(lab?.stability?.series?.[kind]).map((name) => {
    const key = modelKeyOf(lab, name);
    const values = asArray(lab?.series?.[`${kind}_${name}`]).map(num);
    if (!key || values.length !== dates.length || !values.some((value) => value != null)) return null;
    return {
      name, key, label: shortLabel(labels.get(key) ?? key), challenger: name === "challenger", dates, values,
      block: asObject(blocks.get(key)?.fluctuation_qlike_vs_har_iv),
    };
  }).filter(Boolean);
};

/** The rolling-test rows whose block was computed, the challenger last (the comparisons that matter come first). */
export const fluctuationRows = (lab) => seriesRows(lab, "fluct_t").filter((row) => row.block && num(row.block.p_value) != null)
  .sort((a, b) => Number(a.challenger) - Number(b.challenger));

/** The cumulative rows, each with its every-day total. */
export const cumulativeRows = (lab) => seriesRows(lab, "cum_qlike_diff")
  .map((row) => ({ ...row, end: num(lab?.stability?.cumulative?.end_all_days?.[row.name]) }))
  .sort((a, b) => Number(a.challenger) - Number(b.challenger));

/** The first robustness p (40-lag Bartlett window variances, then the full-sample variance) in another band. */
const robustClause = (p, robustness) => {
  for (const [key, label] of [["bartlett_40", "with each window's variance at 40 Bartlett lags"],
    ["full_sample_variance", "with Giacomini and Rossi's full-sample variance"]]) {
    const value = num(robustness?.[key]?.p_value);
    if (value != null && band(value) !== band(p)) return `; ${pEq(value)} ${label}`;
  }
  return "";
};

/**
 * One drawn comparison in words, worded by its flags: the drawn window's result with the first robustness check in
 * another band, the registered (window-adjusted) verdict with the ledger's q, the window means when the peak is not
 * the largest, a crossing on both sides, and the challenger's caveat.
 */
export const fluctuationReading = (row, { recordName, setup, ledgerRow }) => {
  const fl = row.block;
  const p = num(fl.p_value);
  const sup = fmtNum(num(fl.sup), 2);
  const tail = `${pEq(p)} for ${fmtInt(setup.window)}-session windows${robustClause(p, fl.robustness)}`;
  const lower = num(fl.sign) > 0 ? row.label : recordName;
  const parts = [];
  if (fl.rejects_5pct) {
    parts.push(`Outside the 5% band: max |t| ${sup} in the window ending ${fmtDate(fl.at)} (from `
      + `${fmtDate(fl.window_start)}), ${lower} lower (${tail}).`);
  } else if (fl.rejects_10pct) {
    parts.push(`Between the 10% and 5% bands: inconclusive for this window length (max |t| ${sup}, ${tail}).`);
  } else {
    parts.push(`Inside the band (max |t| ${sup}, ${tail}): equal accuracy at every date is not rejected for this window `
      + "length, which is not evidence that the two were equally accurate throughout.");
  }
  const adjusted = num(fl.search_adjusted?.p_value);
  if (adjusted != null) {
    const head = `Adjusted for the ${fmtInt(setup.lengths)} window lengths looked at, ${pEq(adjusted)}`;
    const q = num(ledgerRow?.q);
    const qText = q == null ? "" : `q${NBSP}=${NBSP}${fmtPValue(q, { digits: 3 })}`;
    if (band(adjusted) === 0) {
      const survives = ledgerRow?.survives_bh === true ? "survives" : "does not survive";
      parts.push(`${head}: significant at 5%${row.challenger ? " (left out of the ledger's adjusted family)"
        : qText ? `; it ${survives} the ledger's Benjamini-Hochberg adjustment (${qText})` : ""}.`);
    } else if (band(adjusted) === 1) {
      parts.push(`${head}: inconclusive${row.challenger ? " (left out of the ledger's adjusted family)" : qText ? ` (${qText})` : ""}.`);
    } else {
      parts.push(`${head}: not significant.`);
    }
  }
  const same = asObject(fl.largest_same_sign_window);
  if (same && same.window_end !== fl.at) {
    parts.push(`The largest mean difference in that direction is ${fmtSigned(num(same.mean_diff), 3)} a day, in the window `
      + `ending ${fmtDate(same.window_end)} (t${NBSP}=${NBSP}${fmtNum(num(same.t), 2)}); at the peak it is `
      + `${fmtSigned(num(fl.peak_window_mean_diff), 3)}.`);
  }
  if (fl.crosses_both_sides) parts.push("The path leaves the band on both sides.");
  if (row.challenger) parts.push("Picked as the challenger on this sample, so not new evidence; nothing here re-selects it.");
  return parts.join(" ");
};

/** The figure's caption, from the setup. */
export const fluctuationCaption = (setup, recordName) => `Each point is the Diebold-Mariano t of ${recordName}'s daily `
  + `QLIKE minus the model's over the ${fmtInt(setup.window)} out-of-sample sessions (about ${fmtNum(setup.window / 252, 1)} `
  + `years) ending that week, each window scaled by its own variability; above zero the model had the lower loss. The `
  + `band, ±${fmtNum(setup.c5, 2)}, holds for the whole path at once (Giacomini and Rossi 2010, simulated for these `
  + `${fmtInt(setup.nDays)} days; their published value for this window is ${fmtNum(setup.gr2010, 3)}). One crossing `
  + "anywhere rejects, for this window length, the null that neither model was expected to be more accurate at any "
  + "date given what was known at the time; a line inside the band is not evidence that the two were equally accurate "
  + "throughout. Because each window is scaled by its own variability, a calm window with a small, steady difference "
  + "can sit further out than a turbulent window with a larger one: read the size of a difference in the cumulative view.";

/** The cumulative view's caption. */
export const cumulativeCaption = (recordName) => `Sum of ${recordName}'s daily QLIKE minus the model's from the first `
  + "out-of-sample day, read weekly: a line rises while the model has the lower loss. Descriptive: no test reads its "
  + "level or slope.";

const join = (names) => (names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0] ?? "");

/**
 * STAB's note on the Giacomini-White test: where its conditional part is significant, which of those survive the
 * ledger's adjustment, which no longer are with 40 Bartlett lags, where it is inconclusive, and that the form with
 * the constant is context. Null without the blocks.
 */
export const gwNote = (lab, ledger) => {
  const rows = leaderboardRows(lab).filter((row) => row.role !== "record");
  const gw = new Map(asArray(lab?.leaderboard?.models).map((model) => [model?.key, asObject(model?.gw_qlike_vs_har_iv)]));
  const tested = rows.filter((row) => num(gw.get(row.key)?.conditional?.p_value) != null);
  if (!tested.length) return null;
  const p = (row) => num(gw.get(row.key).conditional.p_value);
  const name = (row) => shortLabel(row.label);
  const sig = tested.filter((row) => band(p(row)) === 0);
  const mid = tested.filter((row) => band(p(row)) === 1);
  const survive = sig.filter((row) => ledger?.byId?.get(`vol_gw_${row.key}`)?.survives_bh === true);
  const weak = sig.filter((row) => band(num(gw.get(row.key).robustness?.bartlett_40?.conditional_p_value) ?? 0) > 0);
  const parts = ["Giacomini-White asks whether the loss difference moves with what is known at the decision (its value "
    + "5 sessions earlier, OVX and OVX's gap to RV5)."];
  if (sig.length) {
    let text = `Its conditional part is significant at 5% against ${fmtInt(sig.length)} of ${fmtInt(tested.length)} models `
      + `(${join(sig.map((row) => `${name(row)}, ${pEq(p(row))}`))})`;
    text += survive.length ? `; ${join(survive.map(name))} ${survive.length === 1 ? "survives" : "survive"} the ledger's adjustment`
      : "; none survives the ledger's adjustment";
    if (weak.length) {
      text += `; with 40 Bartlett lags ${weak.length === sig.length ? "all of them" : `${fmtInt(weak.length)} of the ${fmtInt(sig.length)}`} `
        + `(${join(weak.map(name))}) ${weak.length === 1 ? "is" : "are"} no longer significant at 5%`;
    }
    parts.push(`${text}.`);
  } else {
    parts.push(`Its conditional part is not significant at 5% against any of the ${fmtInt(tested.length)} models.`);
  }
  if (mid.length) parts.push(`It is inconclusive against ${join(mid.map((row) => `${name(row)} (${pEq(p(row))})`))}.`);
  parts.push(`Against the other ${fmtInt(tested.length - sig.length - mid.length)} it is not significant; a failure to `
    + "reject is not evidence that the difference does not move with the state. With the constant included the test "
    + "mostly repeats the DM test, so that form is context and not in the ledger.");
  return parts.join(" ");
};

/** The tests table's rows: every compared model, the timing control last (as the leaderboard keeps it). */
export const stabilityTestRows = (research, ledger) => {
  const lab = getLab(research);
  const blocks = new Map(asArray(lab?.leaderboard?.models).map((model) => [model?.key, model]));
  return leaderboardRows(lab).filter((row) => row.role !== "record")
    .sort((a, b) => Number(a.timingControl) - Number(b.timingControl))
    .map((row) => {
      const model = blocks.get(row.key) ?? {};
      const gw = asObject(model.gw_qlike_vs_har_iv) ?? {};
      const fl = asObject(model.fluctuation_qlike_vs_har_iv) ?? {};
      const p = num(fl.p_value);
      return {
        key: row.key, label: shortLabel(row.label), timingControl: row.timingControl, challenger: row.role === "challenger",
        dmStat: row.dmStat, gwP: num(gw.conditional?.p_value), gwQ: num(ledger?.byId?.get(`vol_gw_${row.key}`)?.q),
        gwFullP: num(gw.p_value),
        sup: num(fl.sup) == null ? null : num(fl.sup) * (num(fl.sign) ?? 1), at: asText(fl.at),
        level: p == null ? null : p < ALPHA ? "outside 5%" : p < ALPHA_LOOSE ? "inconclusive" : "inside",
        p, adjusted: num(fl.search_adjusted?.p_value), flQ: num(ledger?.byId?.get(`vol_fluct_${row.key}`)?.q),
        gridsOut: num(fl.robustness?.by_grid?.outside_5pct),
        bartlett: num(fl.robustness?.bartlett_40?.p_value), fullSample: num(fl.robustness?.full_sample_variance?.p_value),
        byMu: Object.fromEntries(Object.entries(asObject(fl.by_mu) ?? {}).map(([key, value]) => [key, num(value?.p_value)])),
      };
    });
};
