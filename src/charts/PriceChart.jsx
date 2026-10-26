import { memo, useEffect, useMemo, useRef, useState } from "react";
import { AreaSeries, CrosshairMode, createChart } from "lightweight-charts";
import ChartFigure from "./ChartFigure";
import { DASH, fmtInt, fmtNum, fmtUsd, num, toMs } from "../lib/format";
import { SCREEN_THEME, SURFACE, baseChartOptions, fontsReady, withAlpha } from "../theme/chartTheme";
import { formatTick, installTickFormatter, useChartReader } from "./lwUtil";

// The CL=F continuous front-month price (unadjusted; it jumps at each roll). Context only: no model
// output is drawn on it. Times are US Central (CME wall clock).

const HOUR = 60 * 60;
const DAY = 24 * HOUR;

// Multi-month views plot one bar per session: on an index-based time axis, ~2,500 daily bars
// mixed with ~500 hourly ones would stretch the last month over a fifth of the width.
export const PRICE_RANGES = {
  "1W": { lookbackSec: 7 * DAY, cadence: "intraday", label: "last week" },
  "1M": { lookbackSec: 30 * DAY, cadence: "intraday", label: "last month" },
  "1Y": { lookbackSec: 365 * DAY, cadence: "daily", label: "last year, daily closes" },
  ALL: { lookbackSec: null, cadence: "daily", label: "full history, daily closes" },
};
const DAILY_BAR_MIN_GAP_SEC = 20 * HOUR;   // daily bars sit a day apart; hourly bars an hour
const DAILY_BAR_WALL_SEC = 23 * HOUR;      // Yahoo stamps daily CL=F bars 00:00 New York = 23:00 CT
const HOUR_MS = HOUR * 1000;
const POINT_KINDS = new Set(["1d", "1h", "live"]);  // unified_data.actual.kinds (backend get_historical_data)

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

const wallFormat = (options) => new Intl.DateTimeFormat("en-US", { timeZone: "UTC", ...options });
const WALL_DAY = wallFormat({ month: "short", day: "numeric", year: "numeric" });
const WALL_TIME = wallFormat({ month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
const WALL_CLOCK = wallFormat({ hour: "numeric", minute: "2-digit" });

const formatWallTime = (wallSec, cadence) => {
  if (!Number.isFinite(wallSec)) return DASH;
  return cadence === "daily"
    ? WALL_DAY.format(wallSec * 1000)
    : `${WALL_TIME.format(wallSec * 1000)} CT`;
};

// Wall-clock seconds read as UTC dates, so the shared formatter gives year, month ("Mar ’26" over a
// long span) and day ticks; clock ticks print the wall time.
const tickMarkFormatter = (time, tickMarkType, days) =>
  formatTick(Number(time), tickMarkType, days) ?? WALL_CLOCK.format(new Date(Number(time) * 1000));

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
  // The backend tags every point ('1d' daily bar, '1h' hourly bar, 'live' stored quote); older
  // payloads do not, and fall back to inferring the kind from the timestamp grid.
  const kinds = Array.isArray(actualPayload?.kinds) && actualPayload.kinds.length === values.length
    ? actualPayload.kinds
    : null;

  const deduped = new Map();
  values.forEach((value, index) => {
    const ms = toMs(timestamps[index]);
    const price = num(value);
    if (ms == null || price == null || price <= 0) return;
    const time = Math.floor(ms / 1000);
    const kind = kinds && POINT_KINDS.has(kinds[index]) ? kinds[index] : null;
    deduped.set(time, {
      time,
      value: round2(price),
      volume: Math.max(0, Math.round(num(volumes[index]) || 0)),
      kind,
      // Without a kind: Yahoo's daily and hourly bars sit exactly on the hour. Anything else is a
      // stored live quote, whose volume is the session's running total, not one bar's.
      isBar: kind ? kind !== "live" : ms % HOUR_MS === 0,
    });
  });
  return [...deduped.values()].sort((a, b) => a.time - b.time);
};

// The payload is daily bars, then about a month of hourly bars, then stored live quotes
// (backend get_historical_data), each tagged with its kind. Payloads from before the tags fall back
// to inference: daily bars are the leading run of bars spaced a day apart, and the last one can
// share its session with the first hourly bars (the hourly window opens mid-session), so a bar at
// 23:00 CT, where Yahoo stamps daily bars, still counts when an hourly bar follows it. That guess
// cannot tell a daily bar from an hourly window that opens at exactly 00:00 ET; the tags can.
const findDailyBars = (points) => {
  if (points.length && points.every((point) => point.kind)) {
    return new Set(points.filter((point) => point.kind === "1d").map((point) => point.time));
  }
  const bars = points.filter((point) => point.isBar);
  const daily = new Set();
  for (let index = 0; index < bars.length; index += 1) {
    const { time } = bars[index];
    const clearBefore = index === 0 || time - bars[index - 1].time >= DAILY_BAR_MIN_GAP_SEC;
    const clearAfter = index === bars.length - 1 || bars[index + 1].time - time >= DAILY_BAR_MIN_GAP_SEC;
    const dailyStamp = toWall(time) % DAY === DAILY_BAR_WALL_SEC;
    if (!clearBefore || !(clearAfter || dailyStamp)) break; // the hourly bars have started
    daily.add(time);
  }
  return daily;
};

// Two views of the same prices, both in CT wall time, built per CME session. Intraday plots the
// hourly bars and live quotes; a daily bar stands in, at its 16:00 CT close, only for a session
// without them. Daily keeps the latest print of every session. A session's volume is its daily
// bar's or latest live quote's running total, or the sum of its hourly bars, whichever is larger:
// running totals already include the hours, so they are never added to them.
const buildPriceViews = (points) => {
  const dailyBars = findDailyBars(points);
  const sessions = new Map();
  points.forEach((point) => {
    const wall = toWall(point.time);
    const close = sessionClose(sessionDay(wall));
    if (!sessions.has(close)) sessions.set(close, []);
    sessions.get(close).push({ ...point, wall, isDaily: dailyBars.has(point.time) });
  });

  const intraday = new Map();
  const daily = [];
  sessions.forEach((sessionPoints, close) => {
    const hasIntraday = sessionPoints.some((point) => !point.isDaily);
    let hourlyVolume = 0;
    let runningVolume = 0;
    sessionPoints.forEach(({ value, volume, wall, isDaily, isBar }) => {
      if (isBar && !isDaily) hourlyVolume += volume;
      else runningVolume = Math.max(runningVolume, volume);
      if (!isDaily) intraday.set(wall, { time: wall, value, volume });
      else if (!hasIntraday) intraday.set(close, { time: close, value, volume });
    });
    const latest = sessionPoints[sessionPoints.length - 1];
    daily.push({ time: close, value: latest.value, volume: Math.max(hourlyVolume, runningVolume) });
  });
  return {
    intraday: [...intraday.values()].sort((a, b) => a.time - b.time),
    daily: daily.sort((a, b) => a.time - b.time),
  };
};

/** The legend's reading of one price point, as text (spoken after a keyboard move). */
const describePoint = (point, cadence) => (point
  ? `${formatWallTime(point.time, cadence)}; price ${fmtUsd(point.value)}; volume ${formatCompactVolume(point.volume)}`
  : "");

/**
 * @param {object} props
 * @param {object|null} props.actualPayload  data.json unified_data.actual
 * @param {number|null} props.snapshotPrice  data.json current_price (used when there is no history)
 * @param {"1W"|"1M"|"1Y"|"ALL"} props.range
 * @param {unknown} [props.frameKey]  a new value re-frames the chart on the preset (a click on the
 *   range button already pressed)
 */
function PriceChart({ actualPayload = null, snapshotPrice = null, range = "ALL", frameKey = 0 }) {
  const hostRef = useRef(null);
  const chartRef = useRef(null);
  const [ready, setReady] = useState(false);
  const preset = PRICE_RANGES[range] ?? PRICE_RANGES.ALL;
  const cadence = preset.cadence;

  const model = useMemo(() => {
    const points = buildActualPoints(actualPayload, null);
    const snapshot = num(snapshotPrice);
    const resolved = points.length > 0
      ? points
      : (snapshot > 0 ? [{ time: Math.floor(Date.now() / 1000), value: round2(snapshot), volume: 0 }] : []);
    if (resolved.length === 0) return null;
    const views = buildPriceViews(resolved);
    const build = (key) => {
      const prices = views[key];
      return {
        prices,
        times: prices.map((point) => point.time),
        anchor: prices[prices.length - 1],
        lookup: new Map(prices.map((point) => [point.time, point])),
      };
    };
    return { daily: build("daily"), intraday: build("intraday") };
  }, [actualPayload, snapshotPrice]);

  const hasData = model != null;
  const view = model ? model[cadence] : null;
  const reader = useChartReader({
    times: view?.times ?? [],
    keyOf: (time) => Number(time),
    valueAt: (index) => view?.prices[index]?.value ?? null,
    describe: (index) => describePoint(view?.prices[index], cadence),
    frame: (chart) => {
      if (!view) return;
      if (preset.lookbackSec == null) {
        chart.timeScale().fitContent();
        return;
      }
      const lastTime = view.anchor.time;
      chart.timeScale().setVisibleRange({ from: lastTime - preset.lookbackSec, to: lastTime });
    },
    paint: (theme) => chartRef.current?.paint(theme),
  });
  const { connect, reframe, offFrame } = reader;

  // One chart instance for the component's lifetime: range clicks and data refreshes update the
  // series in place instead of tearing the canvas down.
  useEffect(() => {
    if (!hasData) return undefined;
    let disposed = false;
    let teardown = null;
    fontsReady().then(() => {
      const host = hostRef.current;
      if (disposed || !host) return;
      const chart = createChart(host, {
        ...baseChartOptions(),
        width: host.clientWidth || 800,
        height: host.clientHeight || 320,
        crosshair: { ...baseChartOptions().crosshair, mode: CrosshairMode.Normal },
        rightPriceScale: { borderColor: SURFACE.rule, scaleMargins: { top: 0.1, bottom: 0.06 } },
        timeScale: {
          borderColor: SURFACE.rule,
          timeVisible: false,
          secondsVisible: false,
          rightOffset: 4,
          minBarSpacing: 0.05, // lets the full daily history fit a 320px phone
          fixLeftEdge: true,
        },
        localization: {
          ...baseChartOptions().localization,
          priceFormatter: (price) => `$${Number(price).toFixed(2)}`,
          timeFormatter: (time) => formatWallTime(Number(time), "daily"),
        },
      });
      const series = chart.addSeries(AreaSeries, {
        lineWidth: 1.5,
        // No last-value tag on the price axis: it covers the nearest tick label, and the legend and
        // the panel's meta already give the latest price.
        lastValueVisible: false,
        priceLineVisible: true,
        crosshairMarkerRadius: 3,
      });
      const state = {
        chart,
        series,
        paint: (theme) => {
          const truth = theme.series.truth;
          series.applyOptions({
            lineColor: truth,
            topColor: withAlpha(truth, 0.08),
            bottomColor: withAlpha(truth, 0),
            priceLineColor: withAlpha(truth, 0.4),
            crosshairMarkerBorderColor: theme.surface.bg1,
            crosshairMarkerBackgroundColor: truth,
          });
        },
      };
      state.paint(SCREEN_THEME);
      const untick = installTickFormatter(chart, tickMarkFormatter);
      const disconnect = connect(chart, host, series);
      chartRef.current = state;
      setReady(true);
      teardown = () => {
        disconnect();
        untick();
        chart.remove();
        chartRef.current = null;
      };
    });
    return () => {
      disposed = true;
      teardown?.();
    };
  }, [hasData, connect]);

  useEffect(() => {
    const state = chartRef.current;
    if (!ready || !state || !view) return;
    state.series.setData(view.prices.map(({ time, value }) => ({ time, value })));
    state.chart.applyOptions({
      timeScale: { timeVisible: cadence === "intraday" },
      localization: { timeFormatter: (time) => formatWallTime(Number(time), cadence) },
    });
  }, [ready, view, cadence]);

  // Re-frame on a new range or a click on the pressed one (frameKey). A background data refresh
  // re-frames too, unless the reader has dragged or pinched away from the preset.
  const framed = useRef(null);
  useEffect(() => {
    if (!ready || !view) return;
    const key = `${range}|${String(frameKey)}`;
    if (framed.current === key && offFrame) return;
    framed.current = key;
    reframe();
  }, [ready, view, range, frameKey, offFrame, reframe]);

  const { label, summary } = useMemo(() => {
    if (!view) return { label: "", summary: "" };
    const from = preset.lookbackSec == null ? null : view.anchor.time - preset.lookbackSec;
    const shown = from == null ? view.prices : view.prices.filter((point) => point.time >= from);
    const first = shown[0] ?? view.anchor;
    const noun = cadence === "daily" ? "sessions" : "prices";
    return {
      label: `WTI front-month futures price, ${preset.label}: ${fmtInt(shown.length)} ${noun}, `
        + `${formatWallTime(first.time, "daily")} to ${formatWallTime(view.anchor.time, "daily")}`,
      summary: `WTI front-month futures price, ${preset.label}: latest ${fmtNum(view.anchor.value, 2)} dollars on `
        + `${formatWallTime(view.anchor.time, cadence)}. Arrow keys read the prices one by one.`,
    };
  }, [view, preset, cadence]);

  if (!view) {
    return (
      <div className="figure-empty">
        <p className="empty-title">Waiting for market data</p>
        <p className="empty-text">No price history is in this snapshot.</p>
      </div>
    );
  }

  const point = (reader.activeKey != null && view.lookup.get(reader.activeKey)) || view.anchor;
  const legend = (
    <>
      <span className="legend-date">{formatWallTime(point.time, cadence)}</span>
      <span className="legend-item"><i className="swatch swatch-truth" />Price {fmtUsd(point.value)}</span>
      <span className="legend-item">Volume {formatCompactVolume(point.volume)}</span>
    </>
  );
  return (
    <ChartFigure
      reader={reader}
      label={label}
      description={summary}
      legend={legend}
      hint="Tap the chart to read a price"
      hostRef={hostRef}
      hostClassName="lw-host--price"
    />
  );
}

export default memo(PriceChart);
