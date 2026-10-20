import { useMemo } from "react";
import { getVrpScatter } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import { ALPHA, vrpAssertion } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtSigned, fmtSpan, num } from "../lib/format";
import { asArray, asObject, getLab, getVol, vrpPoints } from "../lib/research";
import { Legend, ResearchUnavailable } from "./common";

/**
 * The VRP statistics as table rows. The two short-vol proxies are shown without the worst year only
 * when both have that figure: one alone would compare a convex ex-crash Sharpe with a linear
 * full-sample one.
 */
export const vrpStatRows = (vrp) => {
  if (!vrp) return [];
  const worstYear = num(vrp.worst_year);
  const swapEx = num(vrp.short_vol_swap_sharpe_ex_worst_year);
  const varianceEx = num(vrp.short_variance_sharpe_ex_worst_year);
  const bothEx = swapEx != null && varianceEx != null && worstYear != null;
  const worst = asArray(vrp.worst_periods)[0];
  const varianceCi = asArray(vrp.mean_premium_variance_ci95).map(num);
  const regression = asObject(vrp.timing_regression);
  return [
    { key: "n", label: "Periods", value: fmtInt(vrp.n_periods), note: null },
    { key: "mean", label: "Mean premium", value: `${fmtSigned(vrp.mean_premium_vol_pts, 1)} pts`, note: "OVX minus realized vol" },
    num(vrp.mean_premium_variance_pts) != null && {
      key: "meanVariance", label: "Mean premium, variance units", value: `${fmtSigned(vrp.mean_premium_variance_pts, 1)} pts²`,
      note: ["OVX² minus realized², in vol pts²", varianceCi.length === 2 && varianceCi.every((v) => v != null)
        ? `95% CI ${fmtSigned(varianceCi[0], 1)} to ${fmtSigned(varianceCi[1], 1)}` : null].filter(Boolean).join("; "),
    },
    { key: "share", label: "Premium positive", value: fmtPct(vrp.share_positive_pct), note: null },
    {
      key: "worst", label: "Worst period", value: `${fmtSigned(vrp.worst_period_vol_pts, 1)} pts`,
      note: typeof worst?.start === "string" ? `from ${fmtDate(worst.start)}` : null,
    },
    {
      key: "swap", label: "Short vol-swap Sharpe", value: fmtNum(vrp.short_vol_swap_sharpe, 2),
      note: bothEx ? `${fmtNum(swapEx, 2)} excl. ${worstYear}` : null,
    },
    {
      key: "variance", label: "Short variance Sharpe", value: fmtNum(vrp.short_variance_sharpe, 2),
      note: bothEx ? `${fmtNum(varianceEx, 2)} excl. ${worstYear}` : null,
    },
    {
      key: "timing", label: "Forecast timing (rich vs cheap)", value: fmtP(vrp.timing_p_value) ?? DASH,
      note: num(vrp.timing_p_value) != null ? (num(vrp.timing_p_value) >= ALPHA ? "no timing skill" : "significant") : null,
    },
    regression && num(regression.t_hac) != null && {
      key: "timingRegression", label: "Timing regression", value: `t ${fmtNum(regression.t_hac, 2)}`,
      note: [fmtP(regression.p_value), regression.n != null ? `n ${fmtInt(regression.n)}` : null, "Newey-West"].filter(Boolean).join(", "),
    },
  ].filter((row) => row && row.value !== DASH && row.value !== `${DASH} pts` && row.value !== `${DASH} pts²`);
};

/** VRP: implied (OVX) against the volatility that followed. */
export default function VrpPanel({ data, research, researchStatus }) {
  const { economic } = getVol(data);
  const vrp = asObject(economic?.variance_risk_premium);
  const returns = asObject(economic?.returns);
  const lab = getLab(research);
  const points = useMemo(() => vrpPoints(lab), [lab]);
  const rows = vrpStatRows(vrp);
  const below = points.filter((point) => point.actual < point.ovx).length;
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
        fmtSpan(returns?.sample_start, returns?.sample_end),
        points.length ? `scatter: ${fmtInt(points.length)} weeks` : null,
      ]}
      caption={points.length
        ? `Each dot is a week: OVX, a 30-calendar-day implied vol, against realized vol over the following 5 sessions (log scales); ${fmtInt(below)} of ${fmtInt(points.length)} weeks (${fmtPct((below / points.length) * 100)}) fall below the diagonal, where implied exceeded realized. The horizons differ, so this gap is not the premium in the table, whose horizon its method note gives. The ring marks the latest week.`
        : "Premium statistics from the production pipeline; the weekly scatter needs research.json."}
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
        {rows.length > 0 && (
          <div className="split-side">
            <DataTable
              caption="Variance risk premium"
              rowKey={(row) => row.key}
              dense
              footnote={typeof vrp?.method === "string" ? vrp.method.split(/(?<=\.)\s/)[0] : null}
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
