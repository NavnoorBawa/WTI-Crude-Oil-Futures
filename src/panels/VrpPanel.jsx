import { useMemo } from "react";
import { getVrpScatter } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import { ALPHA, ALPHA_LOOSE, vrpAssertion } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtSigned, fmtSpan, num, tidyProse } from "../lib/format";
import { asArray, asObject, asText, getLab, getVol, vrpPoints, vrpSummary } from "../lib/research";
import { EconomicUnavailable, Legend, ResearchUnavailable, economicSpan } from "./common";

// The scatter is a square of at most 420px (VrpScatter); a phone's column is narrower.
const SCATTER_HEIGHT = { wide: 420, narrow: 358 };
const GROUP = { group: true };

/** A p-value's verdict in the page's words: significant below 5%, inconclusive below 10%. */
const pVerdict = (p) => (p == null ? null : p < ALPHA ? "significant" : p < ALPHA_LOOSE ? "inconclusive" : "not significant");

/**
 * The VRP statistics as table rows, the premium in variance units first: it is the one that is
 * zero when there is no premium. The share of positive periods and the mean in vol points are
 * positive even then (Jensen's inequality), so each is shown beside its zero-premium value. The
 * two short-vol proxies are shown without the worst year only when both have that figure: one
 * alone would compare a convex ex-crash Sharpe with a linear full-sample one. `raw` is the number
 * the CSV export carries.
 */
export const vrpStatRows = (vrp) => {
  const summary = vrpSummary(vrp);
  if (!summary) return [];
  const worstYear = num(vrp.worst_year);
  const swapEx = num(vrp.short_vol_swap_sharpe_ex_worst_year);
  const varianceEx = num(vrp.short_variance_sharpe_ex_worst_year);
  const bothEx = swapEx != null && varianceEx != null && worstYear != null;
  const worst = asArray(vrp.worst_periods)[0];
  const regression = asObject(vrp.timing_regression);
  const ci = summary.varianceCi;
  const hasNull = summary.nullShare != null;
  const timingP = num(vrp.timing_p_value);
  return [
    { key: "n", label: "Periods", value: fmtInt(summary.n), raw: summary.n, note: null },
    summary.meanVariance != null && {
      key: "meanVariance", label: "Mean premium, variance units", value: `${fmtSigned(summary.meanVariance, 1, "", GROUP)} pts²`,
      raw: summary.meanVariance,
      note: ["OVX² minus realized², in vol pts²; zero with no premium", ci ? `95% CI ${fmtSigned(ci[0], 1, "", GROUP)} to ${fmtSigned(ci[1], 1, "", GROUP)}` : null]
        .filter(Boolean).join("; "),
    },
    {
      key: "share", label: "OVX above realized", value: fmtPct(summary.share), raw: summary.share,
      note: hasNull
        ? `share of periods; ${fmtPct(summary.nullShare)} expected with no premium${summary.nullP != null ? `, test of the excess ${fmtP(summary.nullP)}` : ""}`
        : "share of periods",
    },
    {
      key: "mean", label: "Mean premium, vol points", value: `${fmtSigned(summary.meanVolPts, 1)} pts`, raw: summary.meanVolPts,
      note: `OVX minus realized vol${summary.nullMeanVolPts != null ? `; ${fmtSigned(summary.nullMeanVolPts, 1)} pts expected with no premium` : ""}`,
    },
    {
      key: "worst", label: "Worst period", value: `${fmtSigned(vrp.worst_period_vol_pts, 1)} pts`, raw: num(vrp.worst_period_vol_pts),
      note: typeof worst?.start === "string" ? `from ${fmtDate(worst.start)}` : null,
    },
    {
      key: "swap", label: "Short vol-swap Sharpe", value: fmtNum(vrp.short_vol_swap_sharpe, 2), raw: num(vrp.short_vol_swap_sharpe),
      note: [bothEx ? `${fmtNum(swapEx, 2)} excl. ${worstYear}` : null, hasNull ? "positive mean even with no premium" : null]
        .filter(Boolean).join("; ") || null,
    },
    {
      key: "variance", label: "Short variance Sharpe", value: fmtNum(vrp.short_variance_sharpe, 2), raw: num(vrp.short_variance_sharpe),
      note: bothEx ? `${fmtNum(varianceEx, 2)} excl. ${worstYear}` : null,
    },
    {
      key: "timing", label: "Forecast timing (rich vs cheap)", value: fmtP(timingP) ?? DASH, raw: timingP,
      note: pVerdict(timingP),
    },
    regression && num(regression.t_hac) != null && {
      key: "timingRegression", label: "Timing regression", value: `t ${fmtNum(regression.t_hac, 2)}`, raw: num(regression.t_hac),
      note: [fmtP(regression.p_value), regression.n != null ? `n ${fmtInt(regression.n)}` : null, "Newey-West"].filter(Boolean).join(", "),
    },
  ].filter((row) => row && row.value !== DASH && row.value !== `${DASH} pts` && row.value !== `${DASH} pts²`);
};

/**
 * The reading of the statistics table: the share of positive periods against what a zero premium
 * gives (never against 50%), then the premium in variance units, the one that measures it. Null
 * without the figures to read.
 */
export const vrpReading = (vrp) => {
  const summary = vrpSummary(vrp);
  if (!summary) return null;
  const parts = [];
  if (summary.share != null && summary.nullShare != null && summary.nullP != null) {
    const verdict = summary.nullP < ALPHA ? "more often than a zero premium explains"
      : summary.nullP < ALPHA_LOOSE ? "an inconclusive excess" : "an excess not distinguishable from chance";
    parts.push(`OVX was above realized vol in ${fmtPct(summary.share)} of periods against ${fmtPct(summary.nullShare)} `
      + `expected with no premium (${fmtP(summary.nullP)}, one-sided block bootstrap): ${verdict}.`);
  }
  if (summary.meanVariance != null && summary.varianceCi) {
    const [lo, hi] = summary.varianceCi;
    const verdict = lo <= 0 && hi >= 0 ? "not distinguishable from zero" : lo > 0 ? "above zero" : "below zero";
    parts.push(`The premium in variance units, the measure, averaged ${fmtSigned(summary.meanVariance, 1, "", GROUP)} pts² `
      + `(95% CI ${fmtSigned(lo, 1, "", GROUP)} to ${fmtSigned(hi, 1, "", GROUP)}): ${verdict}.`);
  }
  return parts.length ? parts.join(" ") : null;
};

/** Why the share of positive periods and the vol-point mean are no evidence on their own (a method note). */
export const VRP_JENSEN_NOTE = "With no premium (OVX² equal to expected realized variance) realized vol is still below OVX in most "
  + "periods and the gap in vol points is positive (Jensen's inequality), so neither the share of positive periods against 50% "
  + "nor the mean gap against zero is evidence of a premium; the premium in variance units is the measure.";

/** VRP: implied (OVX) against the volatility that followed. */
export default function VrpPanel({ data, research, researchStatus }) {
  const { economic } = getVol(data);
  const vrp = asObject(economic?.variance_risk_premium);
  const lab = getLab(research);
  const points = useMemo(() => vrpPoints(lab), [lab]);
  const rows = vrpStatRows(vrp);
  const below = points.filter((point) => point.actual < point.ovx).length;
  const span = economicSpan(economic);
  const reading = vrpReading(vrp);
  const horizon = num(lab?.config?.horizon_sessions);
  const latest = points[points.length - 1];
  const VrpScatter = getVrpScatter();

  const caption = points.length
    ? `Each dot is a week: OVX, a 30-calendar-day implied vol, against realized vol over the next ${horizon != null ? `${horizon} sessions` : "week"} `
      + `(log scales); ${fmtInt(below)} of ${fmtInt(points.length)} weeks (${fmtPct((below / points.length) * 100)}) fall below the diagonal, `
      + "a gap across different horizons and so not the premium in the table."
    : "Premium statistics from the production pipeline; the weekly scatter needs research.json.";
  const method = asText(vrp?.method);
  const notes = [
    rows.length ? VRP_JENSEN_NOTE : null,
    method ? tidyProse(method).replace(/\^2\b/g, "²") : null,
    span?.reason ?? null,
  ];

  return (
    <Panel
      id="vrp"
      code="VRP"
      name="Implied vs realized"
      title={vrpAssertion(vrp)}
      fallbackTitle="Implied volatility against realized"
      meta={[
        vrp?.n_periods != null ? `n ${fmtInt(vrp.n_periods)} periods` : null,
        span ? `${span.text} (${span.short})` : null,
        points.length ? `scatter: ${fmtInt(points.length)} weeks, ${fmtSpan(points[0].date, latest.date)}` : null,
      ]}
      caption={caption}
      notes={notes}
      source={{
        artifact: ["data.json → vol_forecast.economic.variance_risk_premium", "research.json → vol_lab.series"],
        reproduce: [REPRODUCE.validation, REPRODUCE.lab],
      }}
      resetKey={data}
      table={points.length ? () => (
        <DataTable
          caption={`Latest 26 weeks: OVX (30-calendar-day implied) and realized vol over the next 5 sessions (percent); the CSV holds all ${fmtInt(points.length)} weeks`}
          rowKey={(row) => row.date}
          dense
          exportName="vrp-weekly"
          exportRows={points.slice().reverse()}
          columns={[
            { key: "date", label: "Week of", rowHeader: true, render: (row) => fmtDate(row.date) },
            { key: "ovx", label: "OVX", align: "right", render: (row) => fmtNum(row.ovx, 1) },
            { key: "actual", label: "Realized", title: "Realized vol over the next 5 sessions", align: "right", csvLabel: "Realized, next 5 sessions", render: (row) => fmtNum(row.actual, 1) },
            {
              key: "gap", label: "OVX − realized", title: "30-day implied minus 5-session realized: different horizons, so not the premium in the statistics table",
              align: "right", render: (row) => fmtSigned(row.ovx - row.actual, 1), csv: (row) => row.ovx - row.actual,
            },
          ]}
          rows={points.slice(-26).reverse()}
        />
      ) : null}
    >
      {/* Without the scatter (no research.json) the note leads and the table and reading share the row. */}
      {!points.length && <ResearchUnavailable status={researchStatus} what="the weekly scatter" />}
      <div className={points.length ? "split split--3 split--vrp vrp-split" : "split vrp-split"}>
        {points.length > 0 && (
          <div className="split-figure">
            <Legend items={[
              { tone: "implied", label: "implied > realized", shape: "dot" },
              { tone: "truth", label: "realized > implied", shape: "dot" },
              { tone: "truth", label: `latest week, ${fmtDate(latest.date, { year: false })}`, shape: "ring" },
            ]} />
            <LazyMount minHeight={SCATTER_HEIGHT} label="scatter">
              <VrpScatter points={points} />
            </LazyMount>
          </div>
        )}
        {rows.length === 0 && asText(economic?.unavailable_reason) && (
          <div className="split-side"><EconomicUnavailable economic={economic} what="The variance risk premium" /></div>
        )}
        {rows.length > 0 && (
          <div className="split-side">
            <DataTable
              caption="Variance risk premium"
              rowKey={(row) => row.key}
              dense
              exportName="vrp-statistics"
              columns={[
                {
                  key: "label", label: "Statistic", rowHeader: true, className: "wrap-cell stat-label",
                  render: (row) => <>{row.label}{row.note && <span className="cell-note">{row.note}</span>}</>,
                  csv: (row) => (row.note ? `${row.label} (${row.note})` : row.label),
                },
                { key: "value", label: "Value", align: "right", render: (row) => <span className="num">{row.value}</span>, csv: (row) => row.raw },
              ]}
              rows={rows}
            />
          </div>
        )}
        {reading && (
          <div className="split-reading">
            <h3 className="figure-title">Reading the premium</h3>
            <p className="prose-note">{reading}</p>
          </div>
        )}
      </div>
    </Panel>
  );
}
