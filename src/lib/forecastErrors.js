// Per-week forecast errors for the FCST figure and table: the miss in vol points, the log error and
// the week's QLIKE loss, plus how much of the total loss the worst weeks carry. Pure functions over
// forecastRows() output; every figure they return is computed from the payload.
import { num } from "./format";

/**
 * One week's QLIKE loss on the variance scale, from annualized vols: with r = (actual / forecast)²,
 * QLIKE = r − ln r − 1 (0 when the forecast equals the realized value). The validation's QLIKE is the mean of these
 * over its weeks. Null unless both vols are positive.
 */
export const weekQlike = (actual, forecast) => {
  const a = num(actual);
  const f = num(forecast);
  if (a == null || f == null || a <= 0 || f <= 0) return null;
  const ratio = (a / f) ** 2;
  return ratio - Math.log(ratio) - 1;
};

/**
 * The rows with their errors added: `miss` = actual − forecast (vol points, signed), `logError` =
 * ln(actual / forecast) and `qlike` (weekQlike). Each is null when the week has no realized value
 * or no forecast.
 *
 * @template {{actual: number|null, forecast: number|null}} T
 * @param {T[]} rows
 * @returns {(T & {miss: number|null, logError: number|null, qlike: number|null})[]}
 */
export const withErrors = (rows) => (Array.isArray(rows) ? rows : []).map((row) => {
  const qlike = weekQlike(row.actual, row.forecast);
  return {
    ...row,
    miss: row.actual != null && row.forecast != null ? row.actual - row.forecast : null,
    logError: qlike == null ? null : Math.log(row.actual / row.forecast),
    qlike,
  };
});

/**
 * How concentrated the loss is: the worst `share` of scored weeks (at least one) and the fraction
 * of the total QLIKE they carry, plus the five worst weeks. Null when no week can be scored.
 *
 * @param {{date: string, actual: number|null, forecast: number|null, qlike?: number|null}[]} rows
 * @param {number} [share]  fraction of weeks counted as "the worst" (default one in a hundred)
 * @returns {{k: number, n: number, shareOfLoss: number|null,
 *   worst: {date: string, actual: number, forecast: number, qlike: number}[]}|null}
 */
export const lossConcentration = (rows, share = 1 / 100) => {
  const scored = (Array.isArray(rows) ? rows : [])
    .map((row) => ({ row, qlike: row.qlike !== undefined ? num(row.qlike) : weekQlike(row.actual, row.forecast) }))
    .filter((item) => item.qlike != null);
  const n = scored.length;
  if (!n) return null;
  const byDate = (a, b) => (a.row.date < b.row.date ? -1 : a.row.date > b.row.date ? 1 : 0);
  const ordered = scored.slice().sort((a, b) => b.qlike - a.qlike || byDate(a, b));
  const k = Math.min(n, Math.max(1, Math.floor(n * share)));
  const total = ordered.reduce((sum, item) => sum + item.qlike, 0);
  const top = ordered.slice(0, k).reduce((sum, item) => sum + item.qlike, 0);
  return {
    k,
    n,
    shareOfLoss: total > 0 ? top / total : null,
    worst: ordered.slice(0, 5).map(({ row, qlike }) => ({ date: row.date, actual: row.actual, forecast: row.forecast, qlike })),
  };
};

/**
 * The ratio of realized to forecast vol expected for a variance-consistent forecast when log
 * residuals are normal with standard deviation `sd`: E[e^ε] / √E[e^(2ε)] = exp(−sd²/2). A mean
 * ratio below 1 is what such a forecast should produce, not a bias. Null without a valid sd.
 */
export const expectedRatio = (sd) => {
  const value = num(sd);
  return value == null || value < 0 ? null : Math.exp(-(value ** 2) / 2);
};
