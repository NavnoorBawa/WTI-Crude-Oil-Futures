import { useEffect, useMemo, useRef, useState } from "react";
import { HistogramSeries, LineSeries, LineStyle, LineType, createChart, createSeriesMarkers } from "lightweight-charts";
import { SERIES, STATUS, SURFACE, TEXT, baseChartOptions, fontsReady, withAlpha } from "../theme/chartTheme";
import { DASH, fmtDate, fmtNum, fmtSigned } from "../lib/format";
import { frameRange, timeKey } from "./lwUtil";

export const VAR_RANGES = { "3Y": 3 * 365, "10Y": 10 * 365, ALL: null };

/** Classifies each week: "var99" breach, "var95" breach, or "ok" (no VaR yet -> "none"). */
export const breachOf = (row) => {
  if (row.var95 == null || row.var99 == null) return "none";
  if (row.ret < -row.var99) return "var99";
  if (row.ret < -row.var95) return "var95";
  return "ok";
};

const BAR_COLORS = {
  var99: STATUS.neg,
  var95: STATUS.warn,
  ok: withAlpha(TEXT.text2, 0.55),
  none: withAlpha(TEXT.text4, 0.45),
};

/**
 * Weekly 5-session log returns (bars) against the model of record's forecast-scaled VaR on the
 * same log-return scale, drawn as negative step lines in the model's colour (95% solid, 99% dashed:
 * one model, two levels); breaches are coloured and the 99% ones marked.
 *
 * @param {{rows: {date:string, ret:number, var95:number|null, var99:number|null}[], range: "3Y"|"10Y"|"ALL"}} props
 */
export default function VarChart({ rows, range = "ALL" }) {
  const hostRef = useRef(null);
  const stateRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [hoverDate, setHoverDate] = useState(null);

  const model = useMemo(() => {
    const lookup = new Map(rows.map((row) => [row.date, { ...row, breach: breachOf(row) }]));
    return {
      lookup,
      bars: rows.map((row) => ({ time: row.date, value: row.ret, color: BAR_COLORS[breachOf(row)] })),
      var95: rows.map((row) => (row.var95 != null ? { time: row.date, value: -row.var95 } : { time: row.date })),
      var99: rows.map((row) => (row.var99 != null ? { time: row.date, value: -row.var99 } : { time: row.date })),
      markers: rows.filter((row) => breachOf(row) === "var99")
        .map((row) => ({ time: row.date, position: "belowBar", shape: "arrowUp", color: STATUS.neg, size: 0.8 })),
    };
  }, [rows]);

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
        rightPriceScale: { borderColor: SURFACE.rule, scaleMargins: { top: 0.06, bottom: 0.1 } },
        timeScale: { borderColor: SURFACE.rule, fixLeftEdge: true, minBarSpacing: 0.2 },
        localization: { priceFormatter: (price) => `${Number(price).toFixed(0)}%` },
      });
      const quiet = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
      const bars = chart.addSeries(HistogramSeries, { ...quiet, base: 0 });
      const var95 = chart.addSeries(LineSeries, { ...quiet, color: SERIES.model, lineWidth: 1, lineType: LineType.WithSteps });
      const var99 = chart.addSeries(LineSeries, {
        ...quiet, color: SERIES.model, lineWidth: 1, lineType: LineType.WithSteps, lineStyle: LineStyle.Dashed,
      });
      const markers = createSeriesMarkers(bars, []);
      const onMove = (param) => setHoverDate(param?.point ? timeKey(param.time) : null);
      chart.subscribeCrosshairMove(onMove);
      const observer = new ResizeObserver(([entry]) => {
        if (entry) chart.resize(Math.floor(entry.contentRect.width), Math.floor(entry.contentRect.height));
      });
      observer.observe(host);
      stateRef.current = { chart, bars, var95, var99, markers, rangeKey: null };
      setReady(true);
      teardown = () => {
        chart.unsubscribeCrosshairMove(onMove);
        observer.disconnect();
        chart.remove();
        stateRef.current = null;
      };
    });
    return () => {
      disposed = true;
      teardown?.();
    };
  }, []);

  useEffect(() => {
    const state = stateRef.current;
    if (!ready || !state) return;
    state.bars.setData(model.bars);
    state.var95.setData(model.var95);
    state.var99.setData(model.var99);
    state.markers.setMarkers(model.markers);
    state.rangeKey = null;
  }, [ready, model]);

  useEffect(() => {
    const state = stateRef.current;
    if (!ready || !state || !rows.length) return;
    const key = `${range}|${rows.length}`;
    if (state.rangeKey === key) return;
    state.rangeKey = key;
    frameRange(state.chart, rows.map((row) => row.date), VAR_RANGES[range] ?? null, 1);
  }, [ready, range, rows]);

  const shown = (hoverDate && model.lookup.get(hoverDate)) || model.lookup.get(rows[rows.length - 1]?.date) || null;
  const status = { var99: "breached 99% VaR", var95: "breached 95% VaR", ok: "within VaR", none: "VaR warming up" };
  const summary = useMemo(() => {
    const scored = rows.filter((row) => row.var95 != null && row.var99 != null);
    const e99 = scored.filter((row) => breachOf(row) === "var99").length;
    const e95 = scored.filter((row) => breachOf(row) !== "ok").length;
    return `Weekly 5-session log returns, ${rows.length} weeks${rows.length ? ` from ${fmtDate(rows[0].date)} to ${fmtDate(rows[rows.length - 1].date)}` : ""}, `
      + `against the forecast-scaled 95% and 99% VaR; ${e99} of ${scored.length} weeks fell below the 99% line and ${e95} below the 95% line. `
      + "The Table button lists the latest weeks.";
  }, [rows]);

  return (
    <div className="lw-figure" role="img" aria-label={summary}>
      <div className="chart-legend" aria-hidden="true">
        <span className="legend-date">{shown ? fmtDate(shown.date) : DASH}</span>
        <span className="legend-item"><i className="swatch swatch-bar" />5-session log return {fmtSigned(shown?.ret, 1, "%")}</span>
        <span className="legend-item"><i className="swatch swatch-model" />−VaR 95% {shown?.var95 != null ? `${fmtNum(-shown.var95, 1)}%` : DASH}</span>
        <span className="legend-item"><i className="swatch swatch-model is-dashed" />−VaR 99% {shown?.var99 != null ? `${fmtNum(-shown.var99, 1)}%` : DASH}</span>
        {shown && <span className={`legend-item legend-status is-${shown.breach}`}>{status[shown.breach]}</span>}
      </div>
      <div ref={hostRef} className="lw-host lw-host--var" />
    </div>
  );
}
