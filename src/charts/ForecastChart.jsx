import { useEffect, useMemo, useRef, useState } from "react";
import { LineSeries, LineStyle, createChart, createSeriesMarkers } from "lightweight-charts";
import { BandSeries } from "./bandSeries";
import { SERIES, SURFACE, baseChartOptions, fontsReady, withAlpha } from "../theme/chartTheme";
import { DASH, fmtDate, fmtNum } from "../lib/format";
import { frameRange, timeKey } from "./lwUtil";

export const FORECAST_RANGES = { "1Y": 365, "3Y": 3 * 365, ALL: null };
// The legend names the pipeline behind the hovered week's band (forecastRows() `source`).
const BAND_SOURCE = { lab: " (lab)", live: " (production)" };

/**
 * Chart data from the weekly rows plus the live forecast, which is appended at its as-of date
 * (the next forecast date) with no realized value yet.
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
  return {
    rows: all,
    lookup: new Map(all.map((row) => [row.date, row])),
    actual: pick("actual"),
    forecast: pick("forecast"),
    ovx: pick("ovx"),
    band: all.map((row) => (row.lo != null && row.hi != null ? { time: row.date, lo: row.lo, hi: row.hi } : { time: row.date })),
    hasOvx: all.some((row) => row.ovx != null),
    live: hasLive ? all[all.length - 1] : null,
  };
};

/**
 * Forecast vs realized next-week volatility (weekly). Realized in light ink (the truth), the model
 * of record in the model blue with its 80% conformal band, OVX dashed in the implied orange.
 *
 * @param {object} props
 * @param {Array} props.rows      forecastRows() output
 * @param {object|null} props.live the live forecast {date, forecast, lo, hi, ovx}
 * @param {"1Y"|"3Y"|"ALL"} props.range
 * @param {string} props.modelLabel
 * @param {string|null} [props.bandLabel]  which pipeline's band is drawn (bandSourceLabel())
 */
export default function ForecastChart({ rows, live, range = "3Y", modelLabel = "Forecast", bandLabel = null }) {
  const hostRef = useRef(null);
  const stateRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [hoverDate, setHoverDate] = useState(null);
  const model = useMemo(() => buildForecastData(rows, live), [rows, live]);

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
        rightPriceScale: { borderColor: SURFACE.rule, scaleMargins: { top: 0.06, bottom: 0.04 } },
        timeScale: { borderColor: SURFACE.rule, rightOffset: 5, fixLeftEdge: true, minBarSpacing: 0.2 },
        localization: { priceFormatter: (price) => `${Number(price).toFixed(0)}%` },
      });
      const quiet = { lastValueVisible: false, priceLineVisible: false };
      const band = chart.addCustomSeries(new BandSeries(), { ...quiet, fillColor: withAlpha(SERIES.model, 0.16) });
      const ovx = chart.addSeries(LineSeries, {
        ...quiet, color: SERIES.implied, lineWidth: 1, lineStyle: LineStyle.Dashed, crosshairMarkerVisible: false,
      });
      const actual = chart.addSeries(LineSeries, {
        ...quiet, color: withAlpha(SERIES.truth, 0.7), lineWidth: 1, crosshairMarkerRadius: 3,
        crosshairMarkerBackgroundColor: SERIES.truth, crosshairMarkerBorderColor: SURFACE.bg1,
      });
      const forecast = chart.addSeries(LineSeries, {
        ...quiet, color: SERIES.model, lineWidth: 2, crosshairMarkerRadius: 3,
        crosshairMarkerBackgroundColor: SERIES.model, crosshairMarkerBorderColor: SURFACE.bg1,
      });
      const markers = createSeriesMarkers(forecast, []);
      const onMove = (param) => setHoverDate(param?.point ? timeKey(param.time) : null);
      chart.subscribeCrosshairMove(onMove);
      const observer = new ResizeObserver(([entry]) => {
        if (entry) chart.resize(Math.floor(entry.contentRect.width), Math.floor(entry.contentRect.height));
      });
      observer.observe(host);
      stateRef.current = { chart, band, ovx, actual, forecast, markers, rangeKey: null };
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
    state.band.setData(model.band);
    state.ovx.setData(model.ovx);
    state.actual.setData(model.actual);
    state.forecast.setData(model.forecast);
    state.markers.setMarkers(model.live ? [{
      time: model.live.date, position: "inBar", shape: "circle", color: SERIES.model, size: 1.4, text: "live",
    }] : []);
    state.rangeKey = null; // re-frame after new data
  }, [ready, model]);

  useEffect(() => {
    const state = stateRef.current;
    if (!ready || !state || !model.rows.length) return;
    const key = `${range}|${model.rows.length}`;
    if (state.rangeKey === key) return;
    state.rangeKey = key;
    frameRange(state.chart, model.rows.map((row) => row.date), FORECAST_RANGES[range] ?? null, 4);
  }, [ready, range, model]);

  const shown = (hoverDate && model.lookup.get(hoverDate)) || model.live || model.rows[model.rows.length - 1] || null;
  const summary = useMemo(() => {
    const weeks = model.rows.filter((row) => !row.isLive);
    const realized = weeks.filter((row) => row.actual != null);
    const last = realized[realized.length - 1];
    return [
      `Weekly ${modelLabel} forecast against realized next-week volatility, ${weeks.length} weeks`
        + (weeks.length ? ` from ${fmtDate(weeks[0].date)} to ${fmtDate(weeks[weeks.length - 1].date)}` : ""),
      `with the 80% band${bandLabel ? ` (${bandLabel})` : ""}${model.hasOvx ? " and OVX" : ""}`,
      last ? `latest realized week ${fmtDate(last.date)}: realized ${fmtNum(last.actual, 1)}%, forecast ${fmtNum(last.forecast, 1)}%` : null,
      model.live ? `live forecast ${fmtNum(model.live.forecast, 1)}% for the week after ${fmtDate(model.live.date)}` : null,
      "The Table button lists the latest weeks",
    ].filter(Boolean).join("; ") + ".";
  }, [model, modelLabel, bandLabel]);

  return (
    <div className="lw-figure" role="img" aria-label={summary}>
      <div className="chart-legend" aria-hidden="true">
        <span className="legend-date">{shown ? `${fmtDate(shown.date)}${shown.isLive ? " · live" : ""}` : DASH}</span>
        <span className="legend-item"><i className="swatch swatch-truth" />Realized {shown?.actual != null ? fmtNum(shown.actual, 1) : shown?.isLive ? "pending" : DASH}</span>
        <span className="legend-item"><i className="swatch swatch-model" />{modelLabel} {fmtNum(shown?.forecast, 1)}</span>
        <span className="legend-item"><i className="swatch swatch-band" />80% band{BAND_SOURCE[shown?.isLive ? "live" : shown?.source] ?? ""} {shown?.lo != null ? `${fmtNum(shown.lo, 1)}–${fmtNum(shown.hi, 1)}` : DASH}</span>
        {model.hasOvx && <span className="legend-item"><i className="swatch swatch-implied is-dashed" />OVX {fmtNum(shown?.ovx, 1)}</span>}
      </div>
      <div ref={hostRef} className="lw-host lw-host--forecast" />
    </div>
  );
}
