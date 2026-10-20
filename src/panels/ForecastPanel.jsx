import { useMemo, useState } from "react";
import { getForecastChart } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import Segmented from "../components/Segmented";
import { INTERVAL_LEVEL_PCT, bandSourceLabel, forecastAssertion, shortModelName } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtPct, fmtSpan, num } from "../lib/format";
import { bandCoverage, forecastRows, getLab, getVol } from "../lib/research";

const RANGES = [
  { value: "1Y", label: "1Y" },
  { value: "3Y", label: "3Y" },
  { value: "ALL", label: "ALL" },
];
const TABLE_WEEKS = 26;

/**
 * FCST: forecast vs realized next-week volatility, with the conformal band and OVX. The meta and the
 * band's stated coverage describe the rows drawn; the R² and loss comparisons in the title come from
 * the weekly production validation and say so in the caption.
 */
export default function ForecastPanel({ data, research }) {
  const [range, setRange] = useState("3Y");
  const { live, validation, recentOos } = getVol(data);
  const lab = getLab(research);
  const rows = useMemo(() => forecastRows(lab, recentOos), [lab, recentOos]);
  const liveRow = useMemo(() => (live ? {
    date: typeof live.as_of === "string" ? live.as_of : null,
    forecast: num(live.forecast_next_week_vol_annualized_pct),
    lo: num(live.interval_80?.low_pct),
    hi: num(live.interval_80?.high_pct),
    ovx: num(live.implied_vol_ovx_pct),
  } : null), [live]);
  const ForecastChart = getForecastChart();
  const modelName = shortModelName(live?.model) ?? shortModelName(validation?.model) ?? "Forecast";
  const ratio = num(validation?.har_mean_ratio_actual_to_forecast);
  const r2 = num(validation?.har_level_r2);
  const persistenceR2 = num(validation?.persistence_level_r2);
  const lag = num(validation?.ovx_lag_sessions);
  const band = useMemo(() => bandCoverage(rows), [rows]);
  const bandSource = bandSourceLabel(band);
  const validationN = num(validation?.n);

  const fit = [
    ratio != null ? `realized averaged ${fmtNum(ratio, 2)}× the forecast` : null,
    r2 != null && persistenceR2 != null ? `vol-level out-of-sample R² ${fmtNum(r2, 2)} vs ${fmtNum(persistenceR2, 2)} for persistence` : null,
  ].filter(Boolean).join("; ");
  const caption = "Each point is a forecast date; realized vol is measured over the following 5 sessions."
    + (fit ? ` Production validation${validationN != null ? ` (${fmtInt(validationN)} weeks)` : ""}: ${fit}.` : "")
    + (bandSource ? ` The band drawn is the ${bandSource} ${INTERVAL_LEVEL_PCT}% interval${band.coverage != null ? `, which covered ${fmtPct(band.coverage)} of the ${fmtInt(band.n)} weeks shown` : ""}.` : "");

  const tableRows = rows.slice(-TABLE_WEEKS).reverse();
  return (
    <Panel
      id="fcst"
      code="FCST"
      name="Forecast vs realized"
      title={forecastAssertion(validation, live, band)}
      fallbackTitle="Next-week volatility forecast against what was realized"
      meta={[
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
