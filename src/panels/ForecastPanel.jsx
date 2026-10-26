import { useMemo, useState } from "react";
import { getForecastChart } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import Segmented from "../components/Segmented";
import { INTERVAL_LEVEL_PCT, bandSourceLabel, forecastAssertion, forecastDetail, shortModelName } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtPct, fmtSpan, num } from "../lib/format";
import { bandCoverage, forecastRows, getLab, getVol, ovxOnlyLabel } from "../lib/research";
import { forecastOfRecord } from "../lib/verdict";

const RANGES = [
  { value: "1Y", label: "1Y" },
  { value: "3Y", label: "3Y" },
  { value: "ALL", label: "ALL" },
];
const TABLE_WEEKS = 26;

/**
 * FCST: forecast vs realized next-week volatility, with the conformal band and OVX. The title's
 * comparisons come from the weekly production validation, and the meta and caption say so. The
 * band's stated coverage counts every week drawn with a band, whatever range is on screen; the
 * hero's coverage is the production validation's own band, a different sample, and is named as such.
 */
export default function ForecastPanel({ data, research }) {
  const [range, setRange] = useState("3Y");
  const { live, validation, recentOos } = getVol(data);
  const lab = getLab(research);
  const rows = useMemo(() => forecastRows(lab, recentOos), [lab, recentOos]);
  // The live week is drawn as the live record holds it (the recorded call when it has this as-of).
  const liveRow = useMemo(() => {
    const call = forecastOfRecord(live, getVol(data).liveRecord);
    return call ? { date: call.asOf, forecast: call.forecast, lo: call.lo, hi: call.hi, ovx: call.ovx.value } : null;
  }, [live, data]);
  const ForecastChart = getForecastChart();
  const modelName = shortModelName(live?.model) ?? shortModelName(validation?.model) ?? "Forecast";
  const ratio = num(validation?.har_mean_ratio_actual_to_forecast);
  const r2 = num(validation?.har_level_r2);
  const persistenceR2 = num(validation?.persistence_level_r2);
  const lag = num(validation?.ovx_lag_sessions);
  const band = useMemo(() => bandCoverage(rows), [rows]);
  const bandSource = bandSourceLabel(band);
  const validationN = num(validation?.n);
  const ovxLabel = ovxOnlyLabel(lab);
  const banded = rows.filter((row) => row.actual != null && row.lo != null && row.hi != null);
  const bandSpan = banded.length ? fmtSpan(banded[0].date, banded[banded.length - 1].date) : null;
  const productionCoverage = num(validation?.interval_80_coverage_pct);
  const productionN = num(validation?.interval_80_n);

  const fit = [
    ratio != null ? `realized averaged ${fmtNum(ratio, 2)}× the forecast` : null,
    r2 != null && persistenceR2 != null ? `vol-level out-of-sample R² ${fmtNum(r2, 2)} vs ${fmtNum(persistenceR2, 2)} for persistence` : null,
  ].filter(Boolean).join("; ");
  const caption = "Each point is a forecast date; realized vol is measured over the following 5 sessions."
    + (fit ? ` Production validation${validationN != null ? ` (${fmtInt(validationN)} weeks)` : ""}: ${fit}.` : "")
    + (bandSource ? ` The band drawn is the ${bandSource} ${INTERVAL_LEVEL_PCT}% interval${band.coverage != null
      ? `, which covered ${fmtPct(band.coverage)} of the ${fmtInt(band.n)} weeks with a band${bandSpan ? ` (${bandSpan}, every range)` : ""}` : ""}`
      + `${live ? "; the live week's band is the production forecast's" : ""}.` : "")
    + (productionCoverage != null && band.lab > 0
      ? ` The verdict's ${fmtPct(productionCoverage)}${productionN != null ? ` over ${fmtInt(productionN)} weeks` : ""} is the production validation's own band.`
      : "");

  const tableRows = rows.slice(-TABLE_WEEKS).reverse();
  return (
    <Panel
      id="fcst"
      code="FCST"
      name="Forecast vs realized"
      title={forecastAssertion(validation, live, { ovxLabel })}
      fallbackTitle="Next-week volatility forecast against what was realized"
      meta={[
        ...forecastDetail(validation, { ovxLabel }),
        rows.length ? `${fmtInt(rows.length)} weeks drawn` : null,
        rows.length ? fmtSpan(rows[0].date, rows[rows.length - 1].date) : null,
        "purged walk-forward, out of sample",
        lag != null ? `OVX lagged ${lag} session${lag === 1 ? "" : "s"}` : null,
      ]}
      actions={<Segmented label="Forecast chart range" options={RANGES} value={range} onChange={setRange} />}
      caption={caption}
      source={{
        artifact: ["research.json → vol_lab.series", "data.json → vol_forecast.recent_oos", "vol_forecast.validation"],
        reproduce: [REPRODUCE.lab, REPRODUCE.validation],
      }}
      resetKey={data}
      table={() => (
        <DataTable
          caption={`Latest ${Math.min(TABLE_WEEKS, rows.length)} of ${fmtInt(rows.length)} weeks (percent, annualized)`}
          rowKey={(row) => row.date}
          dense
          columns={[
            { key: "date", label: "Forecast date", rowHeader: true, render: (row) => fmtDate(row.date) },
            { key: "actual", label: "Realized", align: "right", render: (row) => fmtNum(row.actual, 1) },
            { key: "forecast", label: modelName, align: "right", render: (row) => fmtNum(row.forecast, 1) },
            { key: "band", label: `${INTERVAL_LEVEL_PCT}% band`, align: "right", render: (row) => (row.lo != null ? `${fmtNum(row.lo, 1)}–${fmtNum(row.hi, 1)}` : DASH) },
            { key: "source", label: "Band from", render: (row) => (row.source === "lab" ? "lab" : "production") },
            {
              key: "inside", label: "Inside", align: "center",
              render: (row) => (row.actual == null || row.lo == null ? DASH : row.actual >= row.lo && row.actual <= row.hi ? "yes" : "no"),
            },
            { key: "ovx", label: "OVX", align: "right", render: (row) => fmtNum(row.ovx, 1) },
          ]}
          rows={tableRows}
        />
      )}
    >
      {rows.length ? (
        <LazyMount minHeight={360} label="forecast chart">
          <ForecastChart rows={rows} live={liveRow} range={range} modelLabel={modelName} bandLabel={bandSource} />
        </LazyMount>
      ) : (
        <div className="figure-empty"><p className="empty-text">No out-of-sample history is in this snapshot.</p></div>
      )}
    </Panel>
  );
}
