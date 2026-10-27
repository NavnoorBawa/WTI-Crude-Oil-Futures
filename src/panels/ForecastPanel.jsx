import { useMemo, useState } from "react";
import { useViewParam } from "../app/viewState";
import { getForecastChart } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import Segmented from "../components/Segmented";
import { INTERVAL_LEVEL_PCT, forecastAssertion, forecastDetail, shortModelName } from "../lib/assertions";
import { expectedRatio, lossConcentration, withErrors } from "../lib/forecastErrors";
import { DASH, fmtDate, fmtInt, fmtNum, fmtPct, fmtSigned, fmtSpan, num } from "../lib/format";
import { bandCoverage, forecastRows, getLab, getVol, ovxOnlyLabel } from "../lib/research";
import { forecastOfRecord } from "../lib/verdict";

// URL values (?fcst=) and the chart's range keys.
const RANGES = [
  { value: "1y", label: "1Y" },
  { value: "3y", label: "3Y" },
  { value: "all", label: "ALL" },
];
const RANGE_VALUES = RANGES.map((option) => option.value);
const TABLE_WEEKS = 52;
// The figure's measured height (legend, plot and, on a touch phone, the tap hint), reserved before it mounts.
const FIGURE_HEIGHTS = { wide: 368, narrow: 348 };
// The weeks counted as "the worst" in the loss-concentration note: one in a hundred.
const WORST_SHARE = 1 / 100;
const WORST_LISTED = 3;

const spanText = (span) => (span ? fmtSpan(span.start, span.end) : null);
const isInside = (row) => row.actual >= row.lo && row.actual <= row.hi;

/** "realized inside 120" style clause for one pipeline's band; null when it drew no scored week. */
const bandPart = (part, name) => (part?.n
  ? `${name} for ${fmtInt(part.n)} weeks (${[spanText(part.span), `realized inside ${fmtInt(part.covered)}`].filter(Boolean).join(", ")})`
  : null);

const bandCell = (row) => (row.lo != null && row.hi != null ? `${fmtNum(row.lo, 1)}–${fmtNum(row.hi, 1)}` : DASH);
const insideValue = (row) => (row.actual == null || row.lo == null || row.hi == null ? null : isInside(row) ? "yes" : "no");

/**
 * The FCST table's columns. The CSV (DataTable csvColumns) takes raw values: the band's two edges
 * from columns shown only there, the errors unrounded.
 * @param {string} modelName
 */
export const forecastTableColumns = (modelName) => [
  { key: "date", label: "Forecast date", rowHeader: true, render: (row) => fmtDate(row.date) },
  { key: "actual", label: "Realized", align: "right", render: (row) => fmtNum(row.actual, 1) },
  { key: "forecast", label: modelName, csvLabel: `${modelName} forecast`, align: "right", render: (row) => fmtNum(row.forecast, 1) },
  { key: "band", label: `${INTERVAL_LEVEL_PCT}% band`, align: "right", render: bandCell, csv: false, hideBelow: 640 },
  // The band's two edges as raw numbers, for the CSV only (the table shows them as one range).
  { key: "lo", label: `${INTERVAL_LEVEL_PCT}% band low`, className: "col-csv-only" },
  { key: "hi", label: `${INTERVAL_LEVEL_PCT}% band high`, className: "col-csv-only" },
  {
    key: "source", label: "Band from", hideBelow: 640,
    render: (row) => (row.lo == null ? DASH : row.source === "lab" ? "lab" : "production"),
    csv: (row) => (row.lo == null ? null : row.source === "lab" ? "benchmark lab" : "production"),
  },
  { key: "inside", label: "Inside", align: "center", render: (row) => insideValue(row) ?? DASH, csv: insideValue },
  {
    // On a phone the miss is left to the two columns it is the difference of; the band (hidden
    // there too) survives as Inside, and the week's loss as QLIKE.
    key: "miss", label: "Miss (pts)", title: "Realized minus forecast, in vol points", align: "right", hideBelow: 640,
    sortable: true, firstDirection: "desc", render: (row) => fmtSigned(row.miss, 1),
    // A difference of two one-decimal payload values: six decimals drop floating-point noise only.
    csv: (row) => (row.miss == null ? null : Number(row.miss.toFixed(6))),
  },
  {
    key: "logError", label: "Log error", title: "ln(realized / forecast)", align: "right", hideBelow: 640,
    sortable: true, firstDirection: "desc", render: (row) => fmtSigned(row.logError, 2),
  },
  {
    key: "qlike", label: "QLIKE", title: "The week's loss: (realized/forecast)² − ln((realized/forecast)²) − 1", align: "right",
    sortable: true, firstDirection: "desc", render: (row) => fmtNum(row.qlike, 3),
  },
  { key: "ovx", label: "OVX", align: "right", hideBelow: 640, render: (row) => fmtNum(row.ovx, 1) },
];

/**
 * The FCST table: every week's realized vol, forecast, band and errors (miss in vol points, log error,
 * the week's QLIKE), newest first. It lists the latest TABLE_WEEKS until "Show all" is pressed; the
 * CSV always holds every week, with raw values.
 *
 * @param {{rows: ReturnType<typeof withErrors>, modelName: string}} props  withErrors(forecastRows()) rows, oldest first
 */
export function ForecastTable({ rows, modelName }) {
  const [showAll, setShowAll] = useState(false);
  const tableRows = useMemo(() => (showAll ? rows : rows.slice(-TABLE_WEEKS)).slice().reverse(), [rows, showAll]);
  const exportRows = useMemo(() => rows.slice().reverse(), [rows]);
  const columns = useMemo(() => forecastTableColumns(modelName), [modelName]);

  return (
    <DataTable
      caption={`${showAll ? `All ${fmtInt(rows.length)}` : `Latest ${fmtInt(Math.min(TABLE_WEEKS, rows.length))} of ${fmtInt(rows.length)}`} weeks, newest first (vol in percent, annualized)`}
      rowKey={(row) => row.date}
      dense
      announce="weeks"
      totalCount={rows.length}
      exportName="fcst"
      exportRows={exportRows}
      columns={columns}
      rows={tableRows}
      footnote={rows.length > TABLE_WEEKS ? (
        <button type="button" className="table-show-all" onClick={() => setShowAll((value) => !value)}>
          {showAll ? `Show the latest ${fmtInt(TABLE_WEEKS)} weeks` : `Show all ${fmtInt(rows.length)} weeks`}
        </button>
      ) : null}
    />
  );
}

/**
 * FCST: forecast vs realized next-week volatility, with the 80% band and OVX. The title's
 * comparisons come from the weekly production validation, and the meta says so. The band drawn is
 * the production validation's wherever data.json has one (the newest ~3 years) and the benchmark
 * lab's before that; the caption counts the production weeks inside it, the notes give both
 * pipelines' counts, the expected realized/forecast ratio and how concentrated the loss is.
 */
export default function ForecastPanel({ data, research }) {
  const [rangeValue, setRange] = useViewParam("fcst", RANGE_VALUES, "3y");
  // Bumped on every range click, the pressed button included, so a click re-frames a dragged chart.
  const [frameKey, setFrameKey] = useState(0);
  const { live, validation, recentOos } = getVol(data);
  const lab = getLab(research);
  const rows = useMemo(() => forecastRows(lab, recentOos), [lab, recentOos]);
  // The live week is drawn as the live record holds it (the recorded call when it has this as-of).
  const liveRow = useMemo(() => {
    const call = forecastOfRecord(live, getVol(data).liveRecord);
    return call ? {
      date: call.asOf, forecast: call.forecast, lo: call.lo, hi: call.hi, ovx: call.ovx.value,
      targetStart: call.targetStart, targetEnd: call.targetEnd,
    } : null;
  }, [live, data]);
  const ForecastChart = getForecastChart();
  const modelName = shortModelName(live?.model) ?? shortModelName(validation?.model) ?? "Forecast";
  const ratio = num(validation?.har_mean_ratio_actual_to_forecast);
  const expected = expectedRatio(live?.residual_sd);
  const residualSd = num(live?.residual_sd);
  const r2 = num(validation?.har_level_r2);
  const persistenceR2 = num(validation?.persistence_level_r2);
  const lag = num(validation?.ovx_lag_sessions);
  const horizon = num(lab?.config?.horizon_sessions);
  const productionCoverage = num(validation?.interval_80_coverage_pct);
  const productionN = num(validation?.interval_80_n);
  const band = useMemo(() => bandCoverage(rows), [rows]);
  const validationN = num(validation?.n);
  const ovxLabel = ovxOnlyLabel(lab);
  const errorRows = useMemo(() => withErrors(rows), [rows]);
  // Loss concentration on one pipeline's weeks: the lab's history, else the production weeks.
  const concentration = useMemo(() => {
    const labRows = forecastRows(lab, null);
    const result = lossConcentration(withErrors(labRows.length ? labRows : forecastRows(null, recentOos)), WORST_SHARE);
    return result ? { ...result, sample: labRows.length ? "benchmark-lab" : "production" } : null;
  }, [lab, recentOos]);

  const { lab: labBand, live: liveBand } = band.bySource;
  const insideClause = liveBand.n
    ? `${fmtInt(liveBand.covered)} of the last ${fmtInt(liveBand.n)} production weeks fell inside`
    : labBand.n ? `${fmtInt(labBand.covered)} of ${fmtInt(labBand.n)} benchmark-lab weeks fell inside` : null;
  const caption = !rows.length ? null : `Each point is a forecast date; realized vol is measured over the following ${horizon != null ? `${fmtInt(horizon)} sessions` : "week"}.`
    + (band.n ? ` Bright points are weeks whose realized vol fell outside the ${INTERVAL_LEVEL_PCT}% band${insideClause ? `; ${insideClause}` : ""}.` : "");

  const bandNote = band.n
    ? `Band: the ${[
      bandPart(liveBand, `production ${INTERVAL_LEVEL_PCT}% interval`),
      bandPart(labBand, liveBand.n ? "the benchmark lab's before that" : `benchmark lab's ${INTERVAL_LEVEL_PCT}% interval`),
    ].filter(Boolean).join(" and ")}.`
      // The production validation's own coverage is the same band on a longer sample: say which.
      + (liveBand.n && productionCoverage != null && productionN != null
        ? ` Over all ${fmtInt(productionN)} production validation weeks that band covered ${fmtPct(productionCoverage)}; this snapshot carries it for the latest ${fmtInt(liveBand.n)}.`
        : "")
    : null;
  const fit = [
    ratio != null ? `realized averaged ${fmtNum(ratio, 2)}× the forecast` + (expected != null
      ? `; about ${fmtNum(expected, 2)} is expected for a variance-consistent forecast with log-residual sd ${fmtNum(residualSd, 2)} (exp(−sd²/2))`
      : "") : null,
    r2 != null && persistenceR2 != null ? `out-of-sample R² ${fmtNum(r2, 2)} vs ${fmtNum(persistenceR2, 2)} for persistence` : null,
  ].filter(Boolean).join("; ");
  const fitNote = fit ? `Production validation${validationN != null ? ` (${fmtInt(validationN)} weeks)` : ""}: ${fit}.` : null;
  const lossNote = concentration?.shareOfLoss != null
    ? `The worst ${fmtNum(WORST_SHARE * 100, 0)}% of ${concentration.sample} weeks (${fmtInt(concentration.k)} of ${fmtInt(concentration.n)}) carry `
      + `${fmtNum(concentration.shareOfLoss * 100, 0)}% of their total weekly QLIKE; largest: `
      + `${concentration.worst.slice(0, WORST_LISTED).map((week) => `${fmtDate(week.date)} (realized ${fmtNum(week.actual, 1)} vs forecast ${fmtNum(week.forecast, 1)})`).join(", ")}.`
    : null;
  const notes = [
    bandNote,
    fitNote,
    lossNote,
    "The vertical axis is logarithmic: equal distances are equal ratios, so a forecast half the realized vol looks as far off at 20% as at 80%.",
  ];

  const chartRange = rangeValue.toUpperCase();
  const onRange = (value) => {
    setRange(value);
    setFrameKey((key) => key + 1);
  };

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
        rows.length ? "log scale" : null,
      ]}
      controls={rows.length ? <Segmented label="Forecast chart range" options={RANGES} value={rangeValue} onChange={onRange} /> : null}
      caption={caption}
      notes={notes}
      source={{
        artifact: ["research.json → vol_lab.series", "data.json → vol_forecast.recent_oos", "vol_forecast.validation"],
        reproduce: [REPRODUCE.lab, REPRODUCE.validation],
      }}
      resetKey={data}
      table={() => <ForecastTable rows={errorRows} modelName={modelName} />}
    >
      {rows.length ? (
        <LazyMount minHeight={FIGURE_HEIGHTS} label="forecast chart">
          <ForecastChart rows={rows} live={liveRow} range={chartRange} frameKey={frameKey} modelLabel={modelName} />
        </LazyMount>
      ) : (
        <div className="figure-empty"><p className="empty-text">No out-of-sample history is in this snapshot.</p></div>
      )}
    </Panel>
  );
}
