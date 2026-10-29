import { useEffect, useMemo, useRef, useState } from "react";
import { HistogramSeries, LineSeries, LineStyle, LineType, createChart, createSeriesMarkers } from "lightweight-charts";
import ChartFigure from "./ChartFigure";
import { SCREEN_THEME, SURFACE, baseChartOptions, fontsReady, withAlpha } from "../theme/chartTheme";
import { DASH, fmtDate, fmtInt, fmtNum, fmtSigned, fmtSpan } from "../lib/format";
import { breachOf, drawnBreachCounts } from "../lib/var";
import { ANNOTATION_MIN_PX, fmtAxisPercent, frameRange, installTickFormatter, timeKey, useChartReader } from "./lwUtil";

export const VAR_RANGES = { "3Y": 3 * 365, "10Y": 10 * 365, ALL: null };
const ANNOTATE_DELAY_MS = 150;
// Room right of the last week, so the last time label is not cut by the price axis on a phone.
const ROOM_PX = 24;

/** Bar colours in a palette. Weeks before the VaR warms up are solid text-4, which keeps 3:1 on the panel. */
export const barColors = (theme) => ({
  var99: theme.status.neg,
  var95: theme.status.warn,
  ok: withAlpha(theme.text.text2, 0.55),
  none: theme.text.text4,
});

/**
 * The annotated weeks in [fromDate, toDate] (either end open when null): the worst 5-session
 * return and the latest week below the 99% VaR, each null when there is none. Pure.
 */
export const varAnnotations = (rows, fromDate = null, toDate = null) => {
  let worst = null;
  let latestBreach = null;
  for (const row of rows ?? []) {
    if (row?.ret == null || (fromDate && row.date < fromDate) || (toDate && row.date > toDate)) continue;
    if (!worst || row.ret < worst.ret) worst = row;
    if (breachOf(row) === "var99" && (!latestBreach || row.date > latestBreach.date)) latestBreach = row;
  }
  return { worst, latestBreach };
};

/**
 * The legend's note on the weeks labelled in view (the arrows on the plot carry no text, so no VaR
 * line can run through a figure): the worst week with its return and the latest 99% breach, said
 * once when they are the same week; null when there is neither. Pure.
 * @param {{worst: object|null, latestBreach: object|null}} labelled  varAnnotations() output
 */
export const varAnnotationText = ({ worst = null, latestBreach = null } = {}) => {
  const week = (row) => `${fmtDate(row.date)} (${fmtSigned(row.ret, 1, "%")})`;
  const parts = [];
  if (worst) parts.push(`worst week ${week(worst)}${latestBreach?.date === worst.date ? ", also the latest 99% breach" : ""}`);
  if (latestBreach && latestBreach.date !== worst?.date) parts.push(`latest 99% breach ${week(latestBreach)}`);
  return parts.length ? `in view: ${parts.join(" · ")}` : null;
};

const STATUS_TEXT = { var99: "breached 99% VaR", var95: "breached 95% VaR", ok: "within VaR", none: "VaR warming up" };

/**
 * Weekly 5-session log returns (bars) against the model of record's forecast-scaled VaR on the
 * same log-return scale, drawn as negative step lines in the model's colour (95% solid, 99% dashed:
 * one model, two levels); breaches are coloured and the 99% ones marked. On a wide enough plot the
 * worst week in view and the latest 99% breach get an arrow and are named in the legend row.
 *
 * @param {{rows: {date:string, ret:number, var95:number|null, var99:number|null}[], range: "3Y"|"10Y"|"ALL",
 *   frameKey?: unknown}} props  a new frameKey re-frames the chart on the preset
 */
export default function VarChart({ rows, range = "ALL", frameKey = 0 }) {
  const hostRef = useRef(null);
  const stateRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [note, setNote] = useState(null);

  const model = useMemo(() => ({
    lookup: new Map(rows.map((row) => [row.date, { ...row, breach: breachOf(row) }])),
    times: rows.map((row) => row.date),
  }), [rows]);

  const describe = (index) => {
    const row = model.lookup.get(model.times[index]);
    if (!row) return "";
    return [
      fmtDate(row.date),
      `5-session log return ${fmtSigned(row.ret, 1, "%")}`,
      `95% VaR ${row.var95 != null ? `${fmtNum(-row.var95, 1)}%` : "not yet"}`,
      `99% VaR ${row.var99 != null ? `${fmtNum(-row.var99, 1)}%` : "not yet"}`,
      STATUS_TEXT[row.breach],
    ].join("; ");
  };
  const reader = useChartReader({
    times: model.times,
    valueAt: (index) => model.lookup.get(model.times[index])?.ret ?? null,
    describe,
    frame: (chart) => frameRange(chart, model.times, VAR_RANGES[range] ?? null, 1, ROOM_PX),
    paint: (theme) => stateRef.current?.paint(theme),
  });
  const { connect, reframe, offFrame } = reader;

  useEffect(() => {
    let disposed = false;
    let teardown = null;
    fontsReady().then(() => {
      const host = hostRef.current;
      if (disposed || !host) return;
      const chart = createChart(host, {
        ...baseChartOptions(),
        width: host.clientWidth || 600,
        height: host.clientHeight || 300,
        // Headroom over the tallest bar, and no tick label drawn half outside the pane.
        rightPriceScale: { borderColor: SURFACE.rule, entireTextOnly: true, scaleMargins: { top: 0.1, bottom: 0.1 } },
        timeScale: { borderColor: SURFACE.rule, fixLeftEdge: true, minBarSpacing: 0.2 },
        localization: { ...baseChartOptions().localization, priceFormatter: fmtAxisPercent, timeFormatter: (time) => fmtDate(timeKey(time)) },
      });
      const quiet = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
      const bars = chart.addSeries(HistogramSeries, { ...quiet, base: 0 });
      const var95 = chart.addSeries(LineSeries, { ...quiet, lineWidth: 1, lineType: LineType.WithSteps });
      const var99 = chart.addSeries(LineSeries, { ...quiet, lineWidth: 1, lineType: LineType.WithSteps, lineStyle: LineStyle.Dashed });
      const state = { chart, bars, var95, var99, rows: [], theme: SCREEN_THEME, annotateTimer: 0, markers: createSeriesMarkers(bars, []) };
      // Every 99% breach gets an arrow; on a wide plot the worst week in view and the latest 99%
      // breach in view get one too and are named in the legend row (marker text has no halo, and
      // the VaR step lines would run through it).
      state.annotate = () => {
        const { theme } = state;
        const visible = chart.timeScale().getVisibleRange();
        const labelled = chart.timeScale().width() >= ANNOTATION_MIN_PX && visible
          ? varAnnotations(state.rows, timeKey(visible.from), timeKey(visible.to))
          : { worst: null, latestBreach: null };
        const named = new Set([labelled.worst?.date, labelled.latestBreach?.date].filter(Boolean));
        const marks = state.rows.filter((row) => breachOf(row) === "var99" || named.has(row.date)).map((row) => ({
          time: row.date,
          position: "belowBar",
          shape: "arrowUp",
          color: breachOf(row) === "var99" ? theme.status.neg : theme.text.text2,
          size: 0.8,
        }));
        state.markers.setMarkers(marks);
        setNote(varAnnotationText(labelled));
      };
      state.paint = (theme) => {
        state.theme = theme;
        const colors = barColors(theme);
        bars.setData(state.rows.map((row) => ({ time: row.date, value: row.ret, color: colors[breachOf(row)] })));
        var95.applyOptions({ color: theme.series.model });
        var99.applyOptions({ color: theme.series.model });
        state.annotate();
      };
      const onTimeRange = () => {
        window.clearTimeout(state.annotateTimer);
        state.annotateTimer = window.setTimeout(state.annotate, ANNOTATE_DELAY_MS);
      };
      chart.timeScale().subscribeVisibleTimeRangeChange(onTimeRange);
      const untick = installTickFormatter(chart);
      const disconnect = connect(chart, host, bars);
      stateRef.current = state;
      setReady(true);
      teardown = () => {
        disconnect();
        untick();
        chart.timeScale().unsubscribeVisibleTimeRangeChange(onTimeRange);
        window.clearTimeout(state.annotateTimer);
        chart.remove();
        stateRef.current = null;
      };
    });
    return () => {
      disposed = true;
      teardown?.();
    };
  }, [connect]);

  useEffect(() => {
    const state = stateRef.current;
    if (!ready || !state) return;
    state.rows = rows;
    state.var95.setData(rows.map((row) => (row.var95 != null ? { time: row.date, value: -row.var95 } : { time: row.date })));
    state.var99.setData(rows.map((row) => (row.var99 != null ? { time: row.date, value: -row.var99 } : { time: row.date })));
    state.paint(state.theme);
  }, [ready, rows]);

  // Re-frame on a new preset or a click on the pressed one (frameKey). New data re-frames too, unless
  // the reader has dragged or pinched away from the preset (as PriceChart does).
  const framed = useRef(null);
  useEffect(() => {
    if (!ready || !rows.length) return;
    const key = `${range}|${String(frameKey)}`;
    if (framed.current === key && offFrame) return;
    framed.current = key;
    reframe();
  }, [ready, range, frameKey, rows, offFrame, reframe]);

  const shown = (reader.activeKey && model.lookup.get(reader.activeKey)) || model.lookup.get(rows[rows.length - 1]?.date) || null;
  const { label, summary } = useMemo(() => {
    const { n, e99, e95 } = drawnBreachCounts(rows);
    const span = rows.length ? fmtSpan(rows[0].date, rows[rows.length - 1].date) : null;
    return {
      label: `Weekly log returns against the 95% and 99% VaR, ${fmtInt(rows.length)} weeks${span ? `, ${span}` : ""}`,
      summary: `Weekly 5-session log returns, ${rows.length} weeks${rows.length ? ` from ${fmtDate(rows[0].date)} to ${fmtDate(rows[rows.length - 1].date)}` : ""}, `
        + `against the forecast-scaled 95% and 99% VaR; in this series, rounded to 0.1 pt, ${e99} of ${n} weeks fell below `
        + `the 99% line and ${e95} below the 95% line (the caption gives the Kupiec counts). `
        + "Arrow keys read the weeks one by one; the Table button lists the latest weeks.",
    };
  }, [rows]);

  const legend = (
    <>
      <span className="legend-date">{shown ? fmtDate(shown.date) : DASH}</span>
      <span className="legend-item"><i className="swatch swatch-bar" />5-session log return {fmtSigned(shown?.ret, 1, "%")}</span>
      <span className="legend-item"><i className="swatch swatch-model" />−VaR 95% {shown?.var95 != null ? `${fmtNum(-shown.var95, 1)}%` : DASH}</span>
      <span className="legend-item"><i className="swatch swatch-model is-dashed" />−VaR 99% {shown?.var99 != null ? `${fmtNum(-shown.var99, 1)}%` : DASH}</span>
      {shown &&<span className={`legend-item legend-status is-${shown.breach}`}>{STATUS_TEXT[shown.breach]}</span>}
      {/* A row of its own for what holds whatever the week read: the bars' breach colours, and the weeks the arrows label. */}
      <span className="legend-key">
        <span className="legend-item"><i className="swatch swatch-bar is-var95" />95% breach</span>
        <span className="legend-item"><i className="swatch swatch-bar is-var99" />99% breach</span>
        {note && <span className="legend-item legend-annotation"><span className="legend-arrow">↑</span>{note}</span>}
      </span>
    </>
  );

  return (
    <ChartFigure
      reader={reader}
      label={label}
      description={summary}
      legend={legend}
      hint="Tap the chart to read a week"
      hostRef={hostRef}
      hostClassName="lw-host--var"
    />
  );
}
