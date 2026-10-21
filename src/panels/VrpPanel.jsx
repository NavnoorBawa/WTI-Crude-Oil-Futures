import { useMemo } from "react";
import { getVrpScatter } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import { ALPHA, vrpAssertion } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtSigned, fmtSpan, num, tidyProse } from "../lib/format";
import { asArray, asObject, asText, getLab, getVol, vrpPoints, vrpSummary } from "../lib/research";
import { EconomicUnavailable, Legend, ResearchUnavailable, economicSpan } from "./common";

/**
 * The VRP statistics as table rows, the premium in variance units first: it is the one that is
 * zero when there is no premium. The share of positive periods and the mean in vol points are
 * positive even then (Jensen's inequality), so each is shown beside its zero-premium value. The
 * two short-vol proxies are shown without the worst year only when both have that figure: one
 * alone would compare a convex ex-crash Sharpe with a linear full-sample one.
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
  return [
    { key: "n", label: "Periods", value: fmtInt(summary.n), note: null },
    summary.meanVariance != null && {
      key: "meanVariance", label: "Mean premium, variance units", value: `${fmtSigned(summary.meanVariance, 1)} pts²`,
      note: ["OVX² minus realized², in vol pts²; zero with no premium", ci ? `95% CI ${fmtSigned(ci[0], 1)} to ${fmtSigned(ci[1], 1)}` : null]
        .filter(Boolean).join("; "),
    },
    {
      key: "share", label: "OVX above realized", value: fmtPct(summary.share),
      note: hasNull
        ? `share of periods; ${fmtPct(summary.nullShare)} expected with no premium${summary.nullP != null ? `, test of the excess ${fmtP(summary.nullP)}` : ""}`
        : "share of periods",
    },
    {
      key: "mean", label: "Mean premium, vol points", value: `${fmtSigned(summary.meanVolPts, 1)} pts`,
      note: `OVX minus realized vol${summary.nullMeanVolPts != null ? `; ${fmtSigned(summary.nullMeanVolPts, 1)} pts expected with no premium` : ""}`,
    },
    {
      key: "worst", label: "Worst period", value: `${fmtSigned(vrp.worst_period_vol_pts, 1)} pts`,
      note: typeof worst?.start === "string" ? `from ${fmtDate(worst.start)}` : null,
    },
    {
      key: "swap", label: "Short vol-swap Sharpe", value: fmtNum(vrp.short_vol_swap_sharpe, 2),
      note: [bothEx ? `${fmtNum(swapEx, 2)} excl. ${worstYear}` : null, hasNull ? "positive mean even with no premium" : null]
        .filter(Boolean).join("; ") || null,
    },
    {
      key: "variance", label: "Short variance Sharpe", value: fmtNum(vrp.short_variance_sharpe, 2),
      note: bothEx ? `${fmtNum(varianceEx, 2)} excl. ${worstYear}` : null,
    },
    {
      key: "timing", label: "Forecast timing (rich vs cheap)", value: fmtP(vrp.timing_p_value) ?? DASH,
      note: num(vrp.timing_p_value) != null ? (num(vrp.timing_p_value) >= ALPHA ? "not significant" : "significant") : null,
    },
    regression && num(regression.t_hac) != null && {
      key: "timingRegression", label: "Timing regression", value: `t ${fmtNum(regression.t_hac, 2)}`,
      note: [fmtP(regression.p_value), regression.n != null ? `n ${fmtInt(regression.n)}` : null, "Newey-West"].filter(Boolean).join(", "),
    },
  ].filter((row) => row && row.value !== DASH && row.value !== `${DASH} pts` && row.value !== `${DASH} pts²`);
};

/**
 * The caption's reading of the table: why the share of positive periods is no evidence on its own
 * and which figure measures the premium.
 */
export const vrpReading = (vrp) => {
  const summary = vrpSummary(vrp);
  if (!summary || summary.share == null) return null;
  const base = "With no premium (OVX² equal to expected realized variance) a 20-session realized vol is still below OVX "
    + "in most periods and the gap in vol points is positive (Jensen's inequality), so neither is evidence of a premium "
    + "against 50% or zero; the premium in variance units is the measure.";
  if (summary.nullShare == null || summary.nullP == null) return base;
  return `${base} OVX was above realized vol in ${fmtPct(summary.share)} of periods against ${fmtPct(summary.nullShare)} `
    + `expected with no premium (${fmtP(summary.nullP)}, one-sided block bootstrap).`;
};

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
  const VrpScatter = getVrpScatter();

  return (
    <Panel
      id="vrp"
      code="VRP"
      name="Implied vs realized"
      span="half"
      title={vrpAssertion(vrp)}
      fallbackTitle="Implied volatility against realized"
      meta={[
        vrp?.n_periods != null ? `n ${fmtInt(vrp.n_periods)} periods` : null,
        span ? `${span.text} (${span.short})` : null,
        points.length ? `scatter: ${fmtInt(points.length)} weeks, ${fmtSpan(points[0].date, points[points.length - 1].date)}` : null,
      ]}
      caption={[
        reading,
        span?.reason ?? null,
        points.length
          ? `Each dot is a week: OVX, a 30-calendar-day implied vol, against realized vol over the following 5 sessions (log scales); ${fmtInt(below)} of ${fmtInt(points.length)} weeks (${fmtPct((below / points.length) * 100)}) fall below the diagonal, where implied exceeded realized. The horizons differ, so this gap is not the premium in the table, whose horizon its method note gives. The ring marks the latest week.`
          : "Premium statistics from the production pipeline; the weekly scatter needs research.json.",
      ].filter(Boolean).join(" ")}
      source={{
        artifact: ["data.json → vol_forecast.economic.variance_risk_premium", "research.json → vol_lab.series"],
        reproduce: [REPRODUCE.validation, REPRODUCE.lab],
      }}
      resetKey={data}
      table={points.length ? () => (
        <DataTable
          caption="Latest 26 weeks: OVX (30-calendar-day implied) and realized vol over the next 5 sessions (percent)"
          rowKey={(row) => row.date}
          dense
          columns={[
            { key: "date", label: "Week of", rowHeader: true, render: (row) => fmtDate(row.date) },
            { key: "ovx", label: "OVX", align: "right", render: (row) => fmtNum(row.ovx, 1) },
            { key: "actual", label: "Realized, 5 sessions", align: "right", render: (row) => fmtNum(row.actual, 1) },
            {
              key: "gap", label: "OVX − realized", title: "30-day implied minus 5-session realized: different horizons, so not the premium in the statistics table",
              align: "right", render: (row) => fmtSigned(row.ovx - row.actual, 1),
            },
          ]}
          rows={points.slice(-26).reverse()}
        />
      ) : null}
    >
      <div className="split split--vrp">
        <div className="split-figure">
          {points.length ? (
            <>
              <Legend items={[
                { tone: "implied", label: "implied > realized", shape: "dot" },
                { tone: "truth", label: "realized > implied", shape: "dot" },
              ]} />
              <LazyMount minHeight={300} label="scatter">
                <VrpScatter points={points} />
              </LazyMount>
            </>
          ) : <ResearchUnavailable status={researchStatus} what="the weekly scatter" />}
        </div>
        {rows.length === 0 && asText(economic?.unavailable_reason) && (
          <div className="split-side"><EconomicUnavailable economic={economic} what="The variance risk premium" /></div>
        )}
        {rows.length > 0 && (
          <div className="split-side">
            <DataTable
              caption="Variance risk premium"
              rowKey={(row) => row.key}
              dense
              footnote={typeof vrp?.method === "string" ? tidyProse(vrp.method.split(/(?<=\.)\s/)[0]) : null}
              columns={[
                { key: "label", label: "Statistic", rowHeader: true, className: "wrap-cell stat-label", render: (row) => <>{row.label}{row.note && <span className="cell-note">{row.note}</span>}</> },
                { key: "value", label: "Value", align: "right", render: (row) => <span className="num">{row.value}</span> },
              ]}
              rows={rows}
            />
          </div>
        )}
      </div>
    </Panel>
  );
}
