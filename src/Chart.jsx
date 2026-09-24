import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
  AreaSeries,
  ColorType,
  createChart,
  CrosshairMode,
  LineSeries,
  LineStyle,
  TickMarkType,
} from "lightweight-charts";
import { DASH, fmtNum, fmtSigned, fmtSignedPct, fmtUsd, num, signClass, toMs } from "./format";

// RETRACTED 2026-06-20: the 1W "edge" (Sharpe 2.44, 65.8% acc) was a look-ahead leak in the
// walk-forward (5-day targets maturing after the prediction point, no purge). Purged = coin flip
// (48-52%, p>0.2). 1D/1H never worked. So the model's output is hidden by default and, when the
// reader asks for it, drawn gray and dashed as "retracted model output" — never as a forecast.

const MONO_STACK = 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';

// One colour per series; the footer legend reads this table so its swatches always match the lines.
const SERIES_COLORS = {
  actual: "#d4d7dd",
  model: "#5cb0d6",            // only if a future purged backtest ever re-validates the signal
  retracted: "#9aa3b2",
  band: "rgba(154, 163, 178, 0.55)",
  pastOutput: "#7d8590",
};

const HOUR = 60 * 60;
const DAY = 24 * HOUR;

// Multi-month views plot one bar per session: on an index-based time axis, ~2,500 daily bars
// mixed with ~500 hourly ones would stretch the last month over a fifth of the width.
const RANGE_PRESETS = {
  "8H": { lookbackSec: 8 * HOUR, cadence: "intraday", label: "last 8 hours" },
  "1D": { lookbackSec: DAY, cadence: "intraday", label: "last day" },
  "1W": { lookbackSec: 7 * DAY, cadence: "intraday", label: "last week" },
  "1M": { lookbackSec: 30 * DAY, cadence: "intraday", label: "last month" },
  "1Y": { lookbackSec: 365 * DAY, cadence: "daily", label: "last year, daily closes" },
  ALL: { lookbackSec: null, cadence: "daily", label: "full history, daily closes" },
};
const DEFAULT_RANGE = "ALL";
const DAILY_BAR_MIN_GAP_SEC = 20 * HOUR;   // a bar this far from both neighbours is a daily bar
const PAST_OUTPUT_MIN_SPACING_SEC = DAY;
const PAST_OUTPUT_GAP_BREAK_SEC = 14 * DAY;

const round2 = (value) => Number(Number(value).toFixed(2));

// lightweight-charts renders timestamps in UTC. Shifting each one by Chicago's offset at that
// instant makes the axis, crosshair and legend read CME wall-clock time (CT), DST included.
const ctWallParts = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Chicago",
  hourCycle: "h23",
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "numeric",
});
const ctOffsetCache = new Map();
const ctOffsetSec = (unixSec) => {
  const hourStart = Math.floor(unixSec / HOUR) * HOUR; // Chicago's offset only changes on the hour
  if (!ctOffsetCache.has(hourStart)) {
    const parts = {};
    ctWallParts.formatToParts(new Date(hourStart * 1000)).forEach(({ type, value }) => {
      parts[type] = Number(value);
    });
    const wall = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour % 24, parts.minute) / 1000;
    ctOffsetCache.set(hourStart, wall - hourStart);
  }
  return ctOffsetCache.get(hourStart);
};
const toWall = (unixSec) => unixSec + ctOffsetSec(unixSec);

// CME Globex sessions open at 17:00 CT, so from 17:00 a print belongs to the NEXT trading day.
const sessionDay = (wallSec) => Math.floor((wallSec + 7 * HOUR) / DAY);
const sessionClose = (day) => day * DAY + 16 * HOUR;

const isTradingSlot = (wallSec, cadence) => {
  const date = new Date(wallSec * 1000);
  const weekday = date.getUTCDay();
  if (cadence === "daily") return weekday >= 1 && weekday <= 5;
  const hour = date.getUTCHours();
  if (weekday === 6) return false;       // Saturday: closed
  if (weekday === 0) return hour >= 17;  // Sunday evening reopen
  if (weekday === 5) return hour < 16;   // Friday close
  return hour !== 16;                    // daily maintenance break
};

const wallFormat = (options) => new Intl.DateTimeFormat("en-US", { timeZone: "UTC", ...options });
const WALL_DAY = wallFormat({ weekday: "short", month: "short", day: "numeric", year: "numeric" });
const WALL_TIME = wallFormat({ month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
const WALL_CLOCK = wallFormat({ hour: "numeric", minute: "2-digit" });
const WALL_MONTH = wallFormat({ month: "short" });

const formatWallTime = (wallSec, cadence) => {
  if (!Number.isFinite(wallSec)) return DASH;
  return cadence === "daily"
    ? WALL_DAY.format(wallSec * 1000)
    : `${WALL_TIME.format(wallSec * 1000)} CT`;
};

const tickMarkFormatter = (time, tickMarkType) => {
  const date = new Date(Number(time) * 1000);
  if (tickMarkType === TickMarkType.Year) return String(date.getUTCFullYear());
  if (tickMarkType === TickMarkType.Month) return WALL_MONTH.format(date);
  if (tickMarkType === TickMarkType.DayOfMonth) return String(date.getUTCDate());
  return WALL_CLOCK.format(date);
};

const formatCompactVolume = (value) => {
  if (!Number.isFinite(value) || value <= 0) return DASH;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return `${Math.round(value)}`;
};

const buildActualPoints = (actualPayload, fallbackValues) => {
  const values = Array.isArray(actualPayload?.values)
    ? actualPayload.values
    : (Array.isArray(fallbackValues) ? fallbackValues : []);
  const timestamps = Array.isArray(actualPayload?.timestamps) ? actualPayload.timestamps : [];
  const volumes = Array.isArray(actualPayload?.volumes) ? actualPayload.volumes : [];

  const deduped = new Map();
  values.forEach((value, index) => {
    const ms = toMs(timestamps[index]);
    const price = num(value);
    if (ms == null || price == null || price <= 0) return;
    const time = Math.floor(ms / 1000);
    deduped.set(time, { time, value: round2(price), volume: Math.max(0, Math.round(num(volumes[index]) || 0)) });
  });
  return [...deduped.values()].sort((a, b) => a.time - b.time);
};

// Two views of the same prices, both in CT wall time. Intraday keeps the hourly bars (older daily
// bars move to their session's 16:00 CT close); daily keeps the latest print of every session.
const buildPriceViews = (points) => {
  const intraday = new Map();
  const daily = new Map();
  points.forEach((point, index) => {
    const wall = toWall(point.time);
    const close = sessionClose(sessionDay(wall));
    const previous = points[index - 1];
    const next = points[index + 1];
    const isDailyBar = (!previous || point.time - previous.time >= DAILY_BAR_MIN_GAP_SEC)
      && (!next || next.time - point.time >= DAILY_BAR_MIN_GAP_SEC);
    const intradayTime = isDailyBar ? close : wall;
    intraday.set(intradayTime, { ...point, time: intradayTime });
    const bar = daily.get(close);
    daily.set(close, { ...point, time: close, volume: (bar?.volume || 0) + point.volume });
  });
  const sorted = (map) => [...map.values()].sort((a, b) => a.time - b.time);
  return { intraday: sorted(intraday), daily: sorted(daily) };
};

// Past 1W outputs whose target date has already passed (the current output is the projection).
const buildPastOutputs = (predictedPayload, lastRealTime) => {
  const issued = predictedPayload?.issued_by_horizon?.["1w"];
  const source = Array.isArray(issued?.values) && issued.values.length > 0
    ? issued
    : (predictedPayload?.historical_by_horizon?.["1w"] || predictedPayload?.historical || {});
  const values = Array.isArray(source?.values) ? source.values : [];
  const targets = Array.isArray(source?.target_timestamps) && source.target_timestamps.length > 0
    ? source.target_timestamps
    : (Array.isArray(source?.timestamps) ? source.timestamps : []);
  const issues = Array.isArray(source?.issue_timestamps) ? source.issue_timestamps : [];

  const points = values
    .map((value, index) => {
      const ms = toMs(targets[index]);
      const price = num(value);
      if (ms == null || price == null || price <= 0) return null;
      const time = Math.floor(ms / 1000);
      return { time, value: round2(price), issueTime: Math.floor((toMs(issues[index]) ?? ms) / 1000) };
    })
    .filter((point) => point && point.time <= lastRealTime)
    .sort((a, b) => (a.time - b.time) || (a.issueTime - b.issueTime));

  const collapsed = [];
  points.forEach((point) => {
    const previous = collapsed[collapsed.length - 1];
    if (previous && point.time - previous.time <= PAST_OUTPUT_MIN_SPACING_SEC) {
      collapsed[collapsed.length - 1] = point.issueTime >= previous.issueTime ? point : previous;
      return;
    }
    collapsed.push(point);
  });
  return collapsed;
};

const mapPastOutputs = (points, cadence) => {
  const mapped = new Map();
  points.forEach(({ time, value }) => {
    const wall = toWall(time);
    const slot = cadence === "daily" ? sessionClose(sessionDay(wall)) : wall;
    mapped.set(slot, { time: slot, value, realTime: time });
  });
  const sorted = [...mapped.values()].sort((a, b) => a.time - b.time);
  // Break the line across long gaps instead of drawing a straight segment through them.
  const seriesData = [];
  sorted.forEach((point, index) => {
    seriesData.push({ time: point.time, value: point.value });
    const next = sorted[index + 1];
    if (next && next.realTime - point.realTime > PAST_OUTPUT_GAP_BREAK_SEC && next.time - point.time > 2) {
      seriesData.push({ time: point.time + Math.floor((next.time - point.time) / 2) });
    }
  });
  return seriesData;
};

// Straight line from the last price to the model's 1W output, sampled at the data's own cadence
// (trading days or trading hours) so a week ahead spans a week of bars and the slope is honest.
const buildProjection = (anchor, forecast, cadence) => {
  if (!anchor || !forecast) return null;
  const snap = (wallSec) => (cadence === "daily" ? sessionClose(sessionDay(wallSec)) : wallSec);
  let end = forecast.timeSec != null ? snap(toWall(forecast.timeSec)) : null;
  if (end == null || end <= anchor.time) end = snap(anchor.time + 7 * DAY);

  const step = cadence === "daily" ? DAY : HOUR;
  const slots = [];
  let slot = cadence === "daily" ? anchor.time + DAY : (Math.floor(anchor.time / HOUR) + 1) * HOUR;
  for (; slot < end && slots.length < 400; slot += step) {
    if (isTradingSlot(slot, cadence)) slots.push(slot);
  }
  slots.push(Math.max(end, (slots[slots.length - 1] ?? anchor.time) + 1));

  const path = (target) => {
    const value = num(target);
    if (value == null || value <= 0) return [];
    return [
      { time: anchor.time, value: anchor.value },
      ...slots.map((time, index) => ({
        time,
        value: round2(anchor.value + ((value - anchor.value) * (index + 1)) / slots.length),
      })),
    ];
  };
  return {
    path: path(forecast.value),
    upper: path(forecast.upper),
    lower: path(forecast.lower),
    end: slots[slots.length - 1],
  };
};

function Chart({
  actualPayload = null,
  fallbackValues = null,
  predictedPayload = null,
  snapshotPrice = null,
  contract = null,
  feedLabel = "",
  quote = null,
  forecast = null,
  forecastRetracted = true,
  showForecast = false,
  onToggleForecast = null,
}) {
  const hostRef = useRef(null);
  const chartRef = useRef(null);
  const [selectedRange, setSelectedRange] = useState(DEFAULT_RANGE);
  const [hovered, setHovered] = useState(null);
  const cadence = RANGE_PRESETS[selectedRange].cadence;
  const showModel = Boolean(showForecast && forecast);

  const model = useMemo(() => {
    const points = buildActualPoints(actualPayload, fallbackValues);
    const snapshot = num(snapshotPrice);
    const resolved = points.length > 0
      ? points
      : (snapshot > 0 ? [{ time: Math.floor(Date.now() / 1000), value: round2(snapshot), volume: 0 }] : []);
    if (resolved.length === 0) return null;

    const views = buildPriceViews(resolved);
    const pastOutputs = buildPastOutputs(predictedPayload, resolved[resolved.length - 1].time);
    const build = (cadenceKey) => {
      const prices = views[cadenceKey];
      const anchor = prices[prices.length - 1];
      const projection = buildProjection(anchor, forecast, cadenceKey);
      const lookup = new Map(prices.map((point) => [point.time, { kind: "price", ...point }]));
      (projection?.path || []).slice(1).forEach((point) => {
        if (!lookup.has(point.time)) lookup.set(point.time, { kind: "model", ...point });
      });
      return { prices, anchor, projection, past: mapPastOutputs(pastOutputs, cadenceKey), lookup };
    };
    return { daily: build("daily"), intraday: build("intraday") };
  }, [actualPayload, fallbackValues, predictedPayload, snapshotPrice, forecast]);

  const hasData = model != null;

  // One chart instance for the component's lifetime: range clicks and data refreshes update the
  // series in place instead of tearing the canvas down.
  useEffect(() => {
    const host = hostRef.current;
    if (!hasData || !host) return undefined;

    const chart = createChart(host, {
      width: host.clientWidth || 800,
      height: host.clientHeight || 420,
      attributionLogo: true,
      layout: {
        background: { type: ColorType.Solid, color: "#000000" },
        textColor: "#8b949e",
        fontFamily: MONO_STACK,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.035)" },
        horzLines: { color: "rgba(255,255,255,0.035)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(255,255,255,0.16)", labelBackgroundColor: "#26262c", width: 1 },
        horzLine: { color: "rgba(255,255,255,0.16)", labelBackgroundColor: "#26262c", width: 1 },
      },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.08)",
        scaleMargins: { top: 0.14, bottom: 0.06 },
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.08)",
        timeVisible: false,
        secondsVisible: false,
        rightOffset: 4,
        minBarSpacing: 0.05, // lets the full daily history fit a 320px phone
        fixLeftEdge: true,
        tickMarkFormatter,
      },
      localization: {
        priceFormatter: (price) => `$${Number(price).toFixed(2)}`,
        timeFormatter: (time) => formatWallTime(Number(time), "daily"),
      },
      // Page scrolling wins over the chart: the wheel never pans or zooms; drag and pinch still do.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
    });

    const quietLine = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
    const series = {
      actual: chart.addSeries(AreaSeries, {
        lineColor: SERIES_COLORS.actual,
        topColor: "rgba(255, 255, 255, 0.05)",
        bottomColor: "rgba(255, 255, 255, 0.004)",
        lineWidth: 2,
        lastValueVisible: true,
        priceLineVisible: true,
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 4,
        crosshairMarkerBorderColor: SERIES_COLORS.actual,
        crosshairMarkerBackgroundColor: "#0c0c0e",
      }),
      past: chart.addSeries(LineSeries, {
        ...quietLine, color: SERIES_COLORS.pastOutput, lineWidth: 1, lineStyle: LineStyle.LargeDashed,
      }),
      upper: chart.addSeries(LineSeries, {
        ...quietLine, color: SERIES_COLORS.band, lineWidth: 1, lineStyle: LineStyle.Dotted,
      }),
      lower: chart.addSeries(LineSeries, {
        ...quietLine, color: SERIES_COLORS.band, lineWidth: 1, lineStyle: LineStyle.Dotted,
      }),
      projection: chart.addSeries(LineSeries, {
        ...quietLine, color: SERIES_COLORS.retracted, lineWidth: 2, lineStyle: LineStyle.Dashed,
      }),
    };

    const state = { chart, series, lookup: new Map(), cadence: "daily", viewKey: null, hoverTime: null };
    const handleCrosshairMove = (param) => {
      const time = Number(param?.time);
      const entry = param?.point && Number.isFinite(time) ? state.lookup.get(time) : null;
      const hoverTime = entry ? time : null;
      if (hoverTime === state.hoverTime) return;
      state.hoverTime = hoverTime;
      setHovered(entry ? { ...entry, cadence: state.cadence } : null);
    };
    chart.subscribeCrosshairMove(handleCrosshairMove);

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      chart.resize(Math.floor(entry.contentRect.width), Math.floor(entry.contentRect.height));
    });
    resizeObserver.observe(host);
    chartRef.current = state;

    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [hasData]);

  useEffect(() => {
    const state = chartRef.current;
    if (!state || !model) return;
    const view = model[cadence];
    const { chart, series } = state;
    const projection = showModel ? view.projection : null;

    series.actual.setData(view.prices.map(({ time, value }) => ({ time, value })));
    series.projection.setData(projection?.path || []);
    series.upper.setData(projection?.upper || []);
    series.lower.setData(projection?.lower || []);
    series.past.setData(showModel ? view.past : []);
    series.projection.applyOptions({ color: forecastRetracted ? SERIES_COLORS.retracted : SERIES_COLORS.model });
    state.lookup = view.lookup;
    state.cadence = cadence;
    state.hoverTime = null;
    setHovered(null);
    chart.applyOptions({
      timeScale: { timeVisible: cadence === "intraday" },
      localization: { timeFormatter: (time) => formatWallTime(Number(time), cadence) },
    });

    // Re-frame only when the reader changes range or toggles the model output; a background data
    // refresh keeps whatever they have panned or zoomed to.
    const viewKey = `${selectedRange}|${showModel}`;
    if (state.viewKey === viewKey) return;
    state.viewKey = viewKey;
    const { lookbackSec } = RANGE_PRESETS[selectedRange];
    if (lookbackSec == null) {
      chart.timeScale().fitContent();
      return;
    }
    const lastTime = view.anchor.time;
    const to = projection && lookbackSec >= 7 * DAY ? projection.end : lastTime;
    chart.timeScale().setVisibleRange({ from: lastTime - lookbackSec, to });
  }, [model, cadence, selectedRange, showModel, forecastRetracted]);

  const summary = useMemo(() => {
    if (!model) return "";
    const view = model[cadence];
    const { lookbackSec, label } = RANGE_PRESETS[selectedRange];
    const from = lookbackSec == null ? -Infinity : view.anchor.time - lookbackSec;
    const visible = view.prices.filter((point) => point.time >= from);
    if (visible.length === 0) return "";
    const values = visible.map((point) => point.value);
    const first = visible[0];
    return `WTI front-month futures price, ${label}, times in US Central: ${fmtUsd(first.value)} on `
      + `${formatWallTime(first.time, cadence)} to ${fmtUsd(view.anchor.value)} on `
      + `${formatWallTime(view.anchor.time, cadence)}; low ${fmtUsd(Math.min(...values))}, `
      + `high ${fmtUsd(Math.max(...values))}.`
      + (showModel ? ` A dashed gray line shows the retracted 1-week model output of ${fmtUsd(forecast.value)}, which is not a forecast.` : "");
  }, [model, cadence, selectedRange, showModel, forecast]);

  if (!model) {
    return (
      <div className="tv-chart-shell">
        <div className="tv-chart-empty">
          <div className="tv-empty-title">Waiting for market data</div>
          <div className="tv-empty-subtitle">No real prices are available yet.</div>
        </div>
      </div>
    );
  }

  const view = model[cadence];
  const legend = hovered || { kind: "price", ...view.anchor, cadence };
  const hasBand = showModel && (view.projection?.upper.length > 0 || view.projection?.lower.length > 0);
  const hasPast = showModel && view.past.filter((point) => point.value != null).length > 1;
  const forecastBand = forecast && num(forecast.lower) != null && num(forecast.upper) != null
    ? `${fmtUsd(forecast.lower)} – ${fmtUsd(forecast.upper)}`
    : null;
  const modelLabel = forecastRetracted ? "Retracted model — not a forecast" : "1W model forecast";
  const priceClass = signClass(quote?.changePct ?? quote?.change, "is-up", "is-down");

  return (
    <div className="tv-chart-shell">
      <div className="tv-chart-toolbar">
        <div className="tv-toolbar-main">
          <div className="tv-symbol-block">
            <div className="tv-symbol-chip">{contract?.symbol || DASH}</div>
            <div className="tv-symbol-copy">
              <div className="tv-symbol-title">{contract?.description || "WTI crude oil futures"}</div>
              <div className="tv-toolbar-meta">
                {feedLabel && <span>{feedLabel}</span>}
                {contract?.quote_symbol && <span>{contract.quote_symbol}</span>}
                <span>Times in CT</span>
              </div>
            </div>
          </div>

          {quote && num(quote.price) > 0 && (
            <div className="tv-price-block" title={quote.label || undefined}>
              <div className="tv-price-main">{fmtUsd(quote.price)}</div>
              <div className={`tv-price-change ${priceClass}`}>
                <span>{fmtSigned(quote.change, 2)}</span>
                <span>{fmtSignedPct(quote.changePct, 2)}</span>
              </div>
            </div>
          )}
        </div>

        {forecast && onToggleForecast && (
          <button
            type="button"
            className={`tv-model-toggle ${showForecast ? "is-on" : ""}`}
            aria-pressed={showForecast}
            onClick={onToggleForecast}
          >
            {forecastRetracted ? "Show retracted model output" : "Show 1W model forecast"}
          </button>
        )}
      </div>

      {showModel && (
        <div className="tv-model-strip">
          <span className="tv-model-tag">{modelLabel}</span>
          <span className="tv-model-value">
            1W output {fmtUsd(forecast.value)} ({fmtSignedPct(forecast.pct, 2)} vs the {fmtUsd(forecast.ref)} snapshot)
            {forecastBand && <> · band {forecastBand}</>}
          </span>
          {forecastRetracted && (
            <span className="tv-model-note">
              Its backtested edge was a look-ahead leak; purged, it is a coin flip. Shown for transparency only.
            </span>
          )}
        </div>
      )}

      <figure className="tv-chart-stage">
        <figcaption className="sr-only">{summary}</figcaption>
        <div className="tv-chart-overlays" aria-hidden="true">
          <div className="tv-legend-card">
            <div className="tv-legend-time">{formatWallTime(legend.time, legend.cadence)}</div>
            <div className="tv-legend-grid">
              {legend.kind === "model" ? (
                <span className="tv-legend-model">{modelLabel}: {fmtUsd(legend.value)}</span>
              ) : (
                <>
                  <span>PX {fmtNum(legend.value, 2)}</span>
                  <span>VOL {formatCompactVolume(legend.volume)}</span>
                </>
              )}
            </div>
          </div>

          {showModel && (
            <div className="tv-model-card">
              <div className="tv-model-card-label">{modelLabel}</div>
              <div className="tv-model-card-value">{fmtUsd(forecast.value)}</div>
              <div className="tv-model-card-range">{forecastBand ? `Band ${forecastBand}` : "Band unavailable"}</div>
            </div>
          )}
        </div>

        <div className="tv-chart-watermark" aria-hidden="true">
          <span className="tv-watermark-symbol">{contract?.symbol || "WTI"}</span>
          <span className="tv-watermark-caption">{showModel ? "Price + retracted model output" : "Price"}</span>
        </div>

        <div ref={hostRef} className="tv-chart-host" />
      </figure>

      <div className="tv-chart-footer">
        <div className="tv-range-strip" role="group" aria-label="Chart range">
          {Object.keys(RANGE_PRESETS).map((rangeKey) => (
            <button
              key={rangeKey}
              type="button"
              className={`tv-range-button ${selectedRange === rangeKey ? "is-active" : ""}`}
              aria-pressed={selectedRange === rangeKey}
              title={`Show the ${RANGE_PRESETS[rangeKey].label}`}
              onClick={() => setSelectedRange(rangeKey)}
            >
              {rangeKey}
            </button>
          ))}
        </div>

        <div className="tv-footer-copy">
          <span><i className="tv-swatch" style={{ "--swatch-color": SERIES_COLORS.actual }} />Price</span>
          {hasPast && (
            <span><i className="tv-swatch is-dashed" style={{ "--swatch-color": SERIES_COLORS.pastOutput }} />Past model outputs</span>
          )}
          {showModel && (
            <span>
              <i className="tv-swatch is-dashed" style={{ "--swatch-color": forecastRetracted ? SERIES_COLORS.retracted : SERIES_COLORS.model }} />
              {forecastRetracted ? "Retracted model output" : "1W model forecast"}
            </span>
          )}
          {hasBand && (
            <span><i className="tv-swatch is-dotted" style={{ "--swatch-color": SERIES_COLORS.band }} />Model band</span>
          )}
        </div>
      </div>
    </div>
  );
}

export default memo(Chart);
