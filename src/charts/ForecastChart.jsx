import { useEffect, useMemo, useRef, useState } from "react";
import { LineSeries, LineStyle, PriceScaleMode, createChart, createSeriesMarkers } from "lightweight-charts";
import { BandSeries } from "./bandSeries";
import ChartFigure from "./ChartFigure";
import { SCREEN_THEME, SURFACE, baseChartOptions, fontsReady, withAlpha } from "../theme/chartTheme";
import { DASH, fmtDate, fmtInt, fmtNum, fmtSpan } from "../lib/format";
import { ANNOTATION_MIN_PX, fmtAxisPercent, frameRange, installTickFormatter, timeKey, useChartReader } from "./lwUtil";

export const FORECAST_RANGES = { "1Y": 365, "3Y": 3 * 365, ALL: null };
// The legend names the pipeline behind the hovered week's band (forecastRows() `source`).
const BAND_SOURCE = { lab: " (lab)", live: " (production)" };
const SOURCE_TEXT = { lab: "benchmark lab", live: "production" };
const ANNOTATE_DELAY_MS = 150;
// Room right of the live call: enough for its dot and the last time label, and on a plot wide
// enough for the last-value labels to carry series names, enough to keep those names off the dot.
const ROOM_PX = 28;
const TITLED_ROOM_PX = 64;

/** A realized week outside its 80% band (a week without a band or a realized value is not). */
export const isOutsideBand = (row) =>
  row.actual != null && row.lo != null && row.hi != null && (row.actual < row.lo || row.actual > row.hi);

/**
 * Chart data from the weekly rows plus the live forecast, which is appended at its as-of date
 * (the next forecast date) with no realized value yet. Realized vol is split into two point series,
 * weeks inside the band (or without one) and weeks outside it, each whitespace where the other
 * applies. OVX is drawn only with at least two values: one point is no line.
 *
 * @param {{date:string, actual:number|null, forecast:number|null, lo:number|null, hi:number|null, ovx:number|null}[]} rows
 * @param {{date:string, forecast:number|null, lo:number|null, hi:number|null, ovx:number|null}|null} live
 */
export const buildForecastData = (rows, live) => {
  const all = rows.slice();
  const lastDate = all.length ? all[all.length - 1].date : null;
  const hasLive = Boolean(live?.date && live.forecast != null && (!lastDate || live.date > lastDate));
  if (hasLive) all.push({ date: live.date, actual: null, forecast: live.forecast, lo: live.lo, hi: live.hi, ovx: live.ovx, isLive: true });
  const pick = (key) => all.map((row) => (row[key] != null ? { time: row.date, value: row[key] } : { time: row.date }));
  const realized = (outside) => all.map((row) => (row.actual != null && isOutsideBand(row) === outside
    ? { time: row.date, value: row.actual }
    : { time: row.date }));
  const hasOvx = all.filter((row) => row.ovx != null).length >= 2;
  return {
    rows: all,
    lookup: new Map(all.map((row) => [row.date, row])),
    actualInside: realized(false),
    actualOutside: realized(true),
    forecast: pick("forecast"),
    ovx: hasOvx ? pick("ovx") : [],
    band: all.map((row) => (row.lo != null && row.hi != null ? { time: row.date, lo: row.lo, hi: row.hi } : { time: row.date })),
    hasOvx,
    live: hasLive ? all[all.length - 1] : null,
    liveWindow: hasLive && live.targetStart && live.targetEnd ? { start: live.targetStart, end: live.targetEnd } : null,
  };
};

/**
 * The week in [fromDate, toDate] (either end open when null) whose realized vol was furthest from its
 * forecast as a ratio, i.e. the largest |ln(actual / forecast)|; null when no week qualifies. Pure.
 */
export const largestMiss = (rows, fromDate = null, toDate = null) => {
  let best = null;
  let bestError = -1;
  for (const row of rows ?? []) {
    if (!(row?.actual > 0) || !(row?.forecast > 0)) continue;
    if ((fromDate && row.date < fromDate) || (toDate && row.date > toDate)) continue;
    const error = Math.abs(Math.log(row.actual / row.forecast));
    if (error > bestError) {
      best = row;
      bestError = error;
    }
  }
  return best;
};

/** The legend's reading of one row, as text (spoken after a keyboard move). */
const describeRow = (row, modelLabel, hasOvx) => {
  if (!row) return "";
  const parts = [
    `${fmtDate(row.date)}${row.isLive ? ", live call" : ""}`,
    `realized ${row.actual != null ? `${fmtNum(row.actual, 1)}%` : row.isLive ? "pending" : "not available"}`,
    `${modelLabel} ${fmtNum(row.forecast, 1)}%`,
    row.lo != null ? `80% band ${fmtNum(row.lo, 1)} to ${fmtNum(row.hi, 1)}%${isOutsideBand(row) ? ", realized outside it" : ""}` : "no band",
    hasOvx && row.ovx != null ? `OVX ${fmtNum(row.ovx, 1)}%` : null,
  ];
  return parts.filter(Boolean).join("; ");
};

/**
 * Forecast vs realized next-week volatility (weekly), on a log scale. Realized vol as points (bright
 * where it fell outside the 80% band), the model of record in the model blue with its band, OVX
 * dashed in the implied orange. The largest miss in view is annotated on a wide enough plot.
 *
 * @param {object} props
 * @param {Array} props.rows      forecastRows() output
 * @param {object|null} props.live the live forecast {date, forecast, lo, hi, ovx}
 * @param {"1Y"|"3Y"|"ALL"} props.range
 * @param {unknown} [props.frameKey]  a new value re-frames the chart on the preset (a click on the
 *   range button already pressed)
 * @param {string} props.modelLabel
 */
export default function ForecastChart({ rows, live, range = "3Y", frameKey = 0, modelLabel = "Forecast" }) {
  const hostRef = useRef(null);
  const stateRef = useRef(null);
  const [ready, setReady] = useState(false);
  const model = useMemo(() => buildForecastData(rows, live), [rows, live]);
  const times = useMemo(() => model.rows.map((row) => row.date), [model]);

  const reader = useChartReader({
    times,
    valueAt: (index) => model.rows[index]?.forecast ?? model.rows[index]?.actual ?? null,
    describe: (index) => describeRow(model.rows[index], modelLabel, model.hasOvx),
    frame: (chart) => {
      const titled = chart.timeScale().width() >= ANNOTATION_MIN_PX;
      frameRange(chart, times, FORECAST_RANGES[range] ?? null, 4, titled ? TITLED_ROOM_PX : ROOM_PX);
    },
    paint: (theme) => stateRef.current?.paint(theme),
  });
  const { connect, reframe } = reader;

  useEffect(() => {
    let disposed = false;
    let teardown = null;
    fontsReady().then(() => {
      const host = hostRef.current;
      if (disposed || !host) return;
      const chart = createChart(host, {
        ...baseChartOptions(),
        width: host.clientWidth || 600,
        height: host.clientHeight || 320,
        grid: { ...baseChartOptions().grid, vertLines: { visible: false } },
        rightPriceScale: {
          borderColor: SURFACE.rule, mode: PriceScaleMode.Logarithmic, scaleMargins: { top: 0.06, bottom: 0.04 },
        },
        timeScale: { borderColor: SURFACE.rule, rightOffset: 5, fixLeftEdge: true, minBarSpacing: 0.2 },
        localization: { ...baseChartOptions().localization, priceFormatter: fmtAxisPercent, timeFormatter: (time) => fmtDate(timeKey(time)) },
      });
      const quiet = { lastValueVisible: false, priceLineVisible: false };
      const points = { ...quiet, lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 2, crosshairMarkerRadius: 3 };
      const band = chart.addCustomSeries(new BandSeries(), quiet);
      const ovx = chart.addSeries(LineSeries, {
        ...quiet, lastValueVisible: true, lineWidth: 2, lineStyle: LineStyle.Dashed, crosshairMarkerVisible: false,
      });
      const forecast = chart.addSeries(LineSeries, { ...quiet, lastValueVisible: true, lineWidth: 2, crosshairMarkerRadius: 3 });
      const actualInside = chart.addSeries(LineSeries, points);
      const actualOutside = chart.addSeries(LineSeries, points);
      const state = {
        chart, band, ovx, forecast, actualInside, actualOutside, model: null, theme: SCREEN_THEME, annotateTimer: 0, titled: null, modelLabel: "",
        liveMarkers: createSeriesMarkers(forecast, []),
        insideMarkers: createSeriesMarkers(actualInside, []),
        outsideMarkers: createSeriesMarkers(actualOutside, []),
      };
      // Series names on the last-value labels, and the largest miss in view, only on a plot wide
      // enough to hold them; the live call's dot always.
      state.setTitles = () => {
        const titled = chart.timeScale().width() >= ANNOTATION_MIN_PX;
        state.titled = titled;
        forecast.applyOptions({ title: titled ? state.modelLabel : "" });
        ovx.applyOptions({ title: titled ? "OVX" : "" });
      };
      state.annotate = () => {
        const { model: current, theme } = state;
        if (!current) return;
        if ((chart.timeScale().width() >= ANNOTATION_MIN_PX) !== state.titled) state.setTitles();
        state.liveMarkers.setMarkers(current.live ? [{
          time: current.live.date, position: "inBar", shape: "circle", color: theme.series.model, size: 1.4,
        }] : []);
        const visible = chart.timeScale().getVisibleRange();
        const miss = chart.timeScale().width() >= ANNOTATION_MIN_PX && visible
          ? largestMiss(current.rows, timeKey(visible.from), timeKey(visible.to))
          : null;
        const above = miss ? miss.actual > miss.forecast : false;
        const marker = miss ? [{
          time: miss.date,
          position: above ? "aboveBar" : "belowBar",
          shape: above ? "arrowDown" : "arrowUp",
          color: theme.series.truth,
          size: 0.6,
          text: `largest miss ${fmtNum(miss.actual, 1)} vs ${fmtNum(miss.forecast, 1)}`,
        }] : [];
        const outside = miss ? isOutsideBand(miss) : false;
        state.outsideMarkers.setMarkers(outside ? marker : []);
        state.insideMarkers.setMarkers(miss && !outside ? marker : []);
      };
      state.paint = (theme) => {
        state.theme = theme;
        const { series } = theme;
        const crosshair = (color) => ({ crosshairMarkerBackgroundColor: color, crosshairMarkerBorderColor: theme.surface.bg1 });
        band.applyOptions({ fillColor: withAlpha(series.model, 0.16), edgeColor: withAlpha(series.model, 0.7) });
        ovx.applyOptions({ color: withAlpha(series.implied, 0.9) });
        forecast.applyOptions({ color: series.model, ...crosshair(series.model) });
        actualInside.applyOptions({ color: withAlpha(series.truth, 0.45), ...crosshair(series.truth) });
        actualOutside.applyOptions({ color: series.truth, ...crosshair(series.truth) });
        state.annotate();
      };
      state.paint(SCREEN_THEME);
      const onTimeRange = () => {
        window.clearTimeout(state.annotateTimer);
        state.annotateTimer = window.setTimeout(state.annotate, ANNOTATE_DELAY_MS);
      };
      chart.timeScale().subscribeVisibleTimeRangeChange(onTimeRange);
      const untick = installTickFormatter(chart);
      const disconnect = connect(chart, host, forecast);
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
    state.modelLabel = modelLabel;
    state.setTitles();
  }, [ready, modelLabel]);

  useEffect(() => {
    const state = stateRef.current;
    if (!ready || !state) return;
    state.model = model;
    state.band.setData(model.band);
    state.ovx.setData(model.ovx);
    state.forecast.setData(model.forecast);
    state.actualInside.setData(model.actualInside);
    state.actualOutside.setData(model.actualOutside);
    state.annotate();
  }, [ready, model]);

  // Re-frame on a new preset, a click on the pressed preset (frameKey) or new data.
  useEffect(() => {
    if (!ready || !times.length) return;
    reframe();
  }, [ready, range, frameKey, times, reframe]);

  const shown = (reader.activeKey && model.lookup.get(reader.activeKey)) || model.live || model.rows[model.rows.length - 1] || null;
  const { label, summary } = useMemo(() => {
    const weeks = model.rows.filter((row) => !row.isLive);
    const realized = weeks.filter((row) => row.actual != null);
    const last = realized[realized.length - 1];
    const span = weeks.length ? fmtSpan(weeks[0].date, weeks[weeks.length - 1].date) : null;
    const banded = weeks.filter((row) => row.actual != null && row.lo != null && row.hi != null);
    const outside = banded.filter(isOutsideBand).length;
    const sources = ["live", "lab"].map((source) => [source, banded.filter((row) => row.source === source).length])
      .filter(([, count]) => count > 0)
      .map(([source, count]) => `${SOURCE_TEXT[source]} for ${fmtInt(count)} weeks`);
    return {
      label: `${modelLabel} forecast vs realized weekly volatility, ${fmtInt(weeks.length)} weeks${span ? `, ${span}` : ""}, log scale`,
      summary: [
        `Weekly ${modelLabel} forecast against realized next-week volatility, ${weeks.length} weeks`
          + (weeks.length ? ` from ${fmtDate(weeks[0].date)} to ${fmtDate(weeks[weeks.length - 1].date)}` : ""),
        `with the 80% band${sources.length ? ` (${sources.join(", ")})` : ""}${model.hasOvx ? " and OVX" : ""}, on a log scale`,
        banded.length ? `realized fell outside the band in ${fmtInt(outside)} of ${fmtInt(banded.length)} weeks` : null,
        last ? `latest realized week ${fmtDate(last.date)}: realized ${fmtNum(last.actual, 1)}%, forecast ${fmtNum(last.forecast, 1)}%` : null,
        model.live
          ? `live forecast ${fmtNum(model.live.forecast, 1)}% for ${model.liveWindow
            ? `${fmtDate(model.liveWindow.start, { year: false })} – ${fmtDate(model.liveWindow.end)}`
            : `the 5 sessions after ${fmtDate(model.live.date)}`}`
          : null,
        "Arrow keys read the weeks one by one; the Table button lists every week",
      ].filter(Boolean).join("; ") + ".",
    };
  }, [model, modelLabel]);

  const legend = (
    <>
      <span className="legend-date">{shown ? `${fmtDate(shown.date)}${shown.isLive ? " · live" : ""}` : DASH}</span>
      <span className="legend-item">
        <i className="swatch swatch-truth is-dot" />
        <span>Realized<span className="legend-note"> (bright = outside band)</span> {shown?.actual != null ? fmtNum(shown.actual, 1) : shown?.isLive ? "pending" : DASH}</span>
      </span>
      <span className="legend-item"><i className="swatch swatch-model" />{modelLabel} {fmtNum(shown?.forecast, 1)}</span>
      <span className="legend-item">
        <i className="swatch swatch-band" />80% band{BAND_SOURCE[shown?.isLive ? "live" : shown?.source] ?? ""} {shown?.lo != null ? `${fmtNum(shown.lo, 1)}–${fmtNum(shown.hi, 1)}` : DASH}
      </span>
      {model.hasOvx && <span className="legend-item"><i className="swatch swatch-implied is-dashed" />OVX {fmtNum(shown?.ovx, 1)}</span>}
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
      hostClassName="lw-host--forecast"
    />
  );
}
