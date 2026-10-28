// Helpers shared by the lightweight-charts figures: time keys, framing, axis labels, and the reader
// hook that makes each canvas chart readable by keyboard, by tap and on paper.
import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { TickMarkType } from "lightweight-charts";
import { PRINT_THEME, SCREEN_THEME, themeChartOptions } from "../theme/chartTheme";

const DAY_MS = 24 * 60 * 60 * 1000;
const MINUS = "−";

/** lightweight-charts reports business days as "YYYY-MM-DD" or {year, month, day}. */
export const timeKey = (time) => {
  if (typeof time === "string") return time;
  if (time && typeof time === "object" && "year" in time) {
    return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
  }
  if (typeof time === "number") return new Date(time * 1000).toISOString().slice(0, 10);
  return null;
};

/** An ISO calendar date shifted by whole days. */
export const shiftDate = (dateText, days) =>
  new Date(Date.parse(`${dateText}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);

/** Bars of empty space that take `px` pixels right of `count` bars framed in a plot `width` pixels wide. Pure. */
export const padBarsFor = (count, width, px) => (width > px && px > 0 ? (px * count) / (width - px) : 0);

/**
 * The logical (bar index) range that frames the last `days` of a date-sorted series (all of it when
 * `days` is null), keeping at least `padBars` of empty space right of the last point, and at least
 * `padPx` pixels of it in a plot `width` wide (room for the last point's marker and the last time
 * label on a phone). Null for no dates. Pure.
 * @param {string[]} dates  the series' dates, ascending
 * @param {number|null} days
 * @param {number} [padBars]
 * @param {{padPx?: number, width?: number}} [room]
 * @returns {{from: number, to: number}|null}
 */
export const frameFor = (dates, days, padBars = 0.5, { padPx = 0, width = 0 } = {}) => {
  const lastIndex = dates.length - 1;
  if (lastIndex < 0) return null;
  let firstIndex = 0;
  if (days != null) {
    const from = shiftDate(dates[lastIndex], -days);
    firstIndex = Math.max(0, dates.findIndex((date) => date >= from));
  }
  const pad = Math.max(padBars, padBarsFor(lastIndex - firstIndex + 1, width, padPx));
  return { from: firstIndex - 0.5, to: lastIndex + pad };
};

/**
 * Frames a chart on the last `days` of a date-sorted series (see frameFor()), keeping `padPx`
 * pixels of room right of the last point at the plot's current width.
 * @param {import("lightweight-charts").IChartApi} chart
 */
export const frameRange = (chart, dates, days, padBars = 0.5, padPx = 0) => {
  const range = frameFor(dates, days, padBars, { padPx, width: chart.timeScale().width() });
  if (range) chart.timeScale().setVisibleLogicalRange(range);
  return range;
};

/** A percent axis label with a real minus sign: -20 -> "−20%", 35.4 -> "35%". */
export const fmtAxisPercent = (price) => {
  const value = Number(price);
  if (!Number.isFinite(value)) return "";
  const text = Math.abs(value).toFixed(0);
  return `${value < 0 && text !== "0" ? MINUS : ""}${text}%`;
};

/** Beyond this visible span a month tick names its year ("Mar ’26"), so it cannot read as the year before. */
export const LONG_SPAN_DAYS = 400;
const MONTH = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short" });

/** Days between the two ends of a visible time range ({from, to} chart times), or null. */
export const spanDays = (range) => {
  if (!range) return null;
  if (typeof range.from === "number" && typeof range.to === "number") return (range.to - range.from) / (DAY_MS / 1000);
  const from = timeKey(range.from);
  const to = timeKey(range.to);
  if (!from || !to) return null;
  const days = (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS;
  return Number.isFinite(days) ? days : null;
};

/**
 * A time-axis label: the year on a year tick, the month on a month tick (with its two-digit year when
 * the view spans more than LONG_SPAN_DAYS), the day on a day tick; null (the library's default) for
 * clock times. Pure.
 * @param {import("lightweight-charts").Time} time
 * @param {number} tickMarkType  lightweight-charts TickMarkType
 * @param {number|null} [days]   the visible span in days
 */
export const formatTick = (time, tickMarkType, days = null) => {
  const key = timeKey(time);
  const ms = key ? Date.parse(`${key}T00:00:00Z`) : NaN;
  if (!Number.isFinite(ms)) return null;
  const date = new Date(ms);
  if (tickMarkType === TickMarkType.Year) return String(date.getUTCFullYear());
  if (tickMarkType === TickMarkType.Month) {
    const month = MONTH.format(date);
    return days != null && days > LONG_SPAN_DAYS ? `${month} ’${String(date.getUTCFullYear()).slice(-2)}` : month;
  }
  if (tickMarkType === TickMarkType.DayOfMonth) return String(date.getUTCDate());
  return null;
};

/** Advance of one character of the charts' 11px monospace axis font (0.6 em). */
export const MONO_CHAR_PX = 6.6;

/**
 * Whether `text`, centred on `time`, fits inside the plot: the library centres tick labels and marker
 * text on their time and clips what runs past either edge ("’23" for "Oct ’23"). Pure given the chart.
 * @param {import("lightweight-charts").IChartApi} chart
 */
export const fitsInPlot = (chart, time, text) => {
  const x = chart.timeScale().timeToCoordinate(time);
  if (x == null) return false;
  const half = (String(text).length * MONO_CHAR_PX) / 2;
  return x - half >= 0 && x + half <= chart.timeScale().width();
};

/**
 * Keeps a chart's tick labels in step with its visible span (the year on month ticks beyond
 * LONG_SPAN_DAYS) and leaves out a label that would be cut by either edge of the plot. The library
 * caches labels, so the formatter is replaced on every visible-range change, at most once a frame.
 * Returns the unsubscribe.
 * @param {import("lightweight-charts").IChartApi} chart
 * @param {(time: unknown, tickMarkType: number, days: number|null) => string|null} [format]
 */
export const installTickFormatter = (chart, format = formatTick) => {
  let frame = null;
  const apply = (days) => {
    chart.applyOptions({
      timeScale: {
        tickMarkFormatter: (time, type) => {
          const text = format(time, type, days);
          return text && !fitsInPlot(chart, time, text) ? "" : text;
        },
      },
    });
  };
  const onRange = (range) => {
    if (frame != null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      apply(spanDays(range));
    });
  };
  apply(null);
  chart.timeScale().subscribeVisibleTimeRangeChange(onRange);
  return () => {
    if (frame != null) cancelAnimationFrame(frame);
    chart.timeScale().unsubscribeVisibleTimeRangeChange(onRange);
  };
};

// ------------------------------------------------------------------------------------- reading

/** Annotations (largest miss, worst week) are drawn only on a plot at least this wide. */
export const ANNOTATION_MIN_PX = 420;
/** Shift+Arrow moves the reading this many rows. */
export const READ_STEP = 4;
/** The view counts as moved off its preset once either edge is this many bars away. */
export const FRAME_TOLERANCE_BARS = 0.5;
const ANNOUNCE_DELAY_MS = 300;

/** The first and last row index inside a visible logical range, clamped to the data; null when none. Pure. */
export const visibleRows = (range, count) => {
  if (!range || !(count > 0)) return null;
  const first = Math.max(0, Math.ceil(range.from));
  const last = Math.min(count - 1, Math.floor(range.to));
  return first <= last ? { first, last } : null;
};

/**
 * Where a key press moves the reading: Left/Right one row (Shift: READ_STEP rows), Home/End the first
 * or last visible row; the first arrow press starts at the last visible row. Escape clears (only when
 * something is being read). Null for any other key. Pure.
 * @returns {{index: number}|{clear: true}|null}
 */
export const nextReadIndex = (key, { shift = false, current = null, visible = null, count = 0 } = {}) => {
  if (key === "Escape") return current == null ? null : { clear: true };
  if (!(count > 0)) return null;
  const last = visible?.last ?? count - 1;
  const step = shift ? READ_STEP : 1;
  let index;
  if (key === "ArrowLeft") index = current == null ? last : current - step;
  else if (key === "ArrowRight") index = current == null ? last : current + step;
  else if (key === "Home") index = visible?.first ?? 0;
  else if (key === "End") index = last;
  else return null;
  return { index: Math.min(count - 1, Math.max(0, index)) };
};

/** True when a visible logical range has moved off the preset frame by more than FRAME_TOLERANCE_BARS. Pure. */
export const isOffFrame = (range, frame) => Boolean(range && frame)
  && (Math.abs(range.from - frame.from) > FRAME_TOLERANCE_BARS || Math.abs(range.to - frame.to) > FRAME_TOLERANCE_BARS);

const coarsePointer = () =>
  typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;

/**
 * Makes a lightweight-charts figure readable without a mouse and printable:
 * - keyboard: Left/Right move the reading one row, Shift four, Home/End jump to the first/last
 *   visible row and Escape clears; the crosshair follows and, 300 ms after a key move, the legend's
 *   text is announced through a polite live region;
 * - tap or click pins a row (the same row again clears it); on a coarse pointer a hint says so
 *   until the first tap;
 * - the range preset is the frame: a drag or pinch away from it shows "Reset view", which (like a
 *   double click on the plot, or a new `frameKey` in the chart) puts the preset back;
 * - printing swaps the canvas for a screenshot taken in the light print palette.
 *
 * The chart component calls `connect(chart, host, mainSeries)` once the chart exists (it returns the
 * teardown, to run before chart.remove()) and `reframe()` whenever the preset or the data change.
 *
 * @param {object} options
 * @param {Array<string|number>} options.times  time-axis keys in order (position = logical index)
 * @param {(index: number) => number|null} options.valueAt  a value on the price scale at that row, for the crosshair
 * @param {(index: number) => string} options.describe  the legend's text for that row
 * @param {(chart: import("lightweight-charts").IChartApi) => void} options.frame  applies the range preset
 * @param {(theme: typeof SCREEN_THEME) => void} [options.paint]  recolours the chart's own series for a palette
 * @param {(time: unknown) => string|number|null} [options.keyOf]  chart time -> key (default timeKey)
 */
export function useChartReader(options) {
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  });
  const linkRef = useRef(null);
  const pinRef = useRef(null);
  const hoverRef = useRef(null);
  const [hoverKey, setHoverKey] = useState(null);
  const [pin, setPin] = useState(null);
  const [offFrame, setOffFrame] = useState(false);
  const [hint, setHint] = useState(coarsePointer);
  const [announcement, setAnnouncement] = useState("");
  const [printSrc, setPrintSrc] = useState(null);

  const setOff = useCallback((value) => {
    const link = linkRef.current;
    if (link) link.offFrame = value;
    setOffFrame(value);
  }, []);

  // The pinned row's crosshair, put back after anything that moved or cleared it (a pan, a resize,
  // the pointer leaving the plot). Never while the pointer is over the plot: it owns the crosshair.
  const placeCrosshair = useCallback(() => {
    const link = linkRef.current;
    const pinned = pinRef.current;
    if (!link || !pinned || hoverRef.current != null) return;
    const { times, valueAt } = optionsRef.current;
    const value = valueAt(pinned.index);
    const series = link.series();
    if (value == null || !series || times[pinned.index] == null) {
      link.chart.clearCrosshairPosition();
      return;
    }
    link.chart.setCrosshairPosition(value, times[pinned.index], series);
  }, []);
  const restoreCrosshair = useCallback(() => {
    const link = linkRef.current;
    if (!link || !pinRef.current || link.pinRaf) return;
    link.pinRaf = window.requestAnimationFrame(() => {
      link.pinRaf = 0;
      placeCrosshair();
    });
  }, [placeCrosshair]);

  const pinAt = useCallback((index, { announce = false } = {}) => {
    const { times, describe } = optionsRef.current;
    const next = { key: times[index], index };
    pinRef.current = next;
    setPin(next);
    placeCrosshair();
    const link = linkRef.current;
    if (link && announce) {
      window.clearTimeout(link.announceTimer);
      link.announceTimer = window.setTimeout(() => setAnnouncement(describe(index)), ANNOUNCE_DELAY_MS);
    }
  }, [placeCrosshair]);

  const unpin = useCallback(() => {
    pinRef.current = null;
    setPin(null);
    const link = linkRef.current;
    if (link) {
      window.clearTimeout(link.announceTimer);
      if (hoverRef.current == null) link.chart.clearCrosshairPosition();
    }
  }, []);

  /** Puts the range preset back (also on a new frameKey, a double click, or "Reset view"). */
  const reframe = useCallback(() => {
    const link = linkRef.current;
    if (!link) return;
    link.framing = true;
    optionsRef.current.frame(link.chart);
    window.cancelAnimationFrame(link.frameRaf);
    // The library applies a new range in its next animation frame; read the settled range after it.
    link.frameRaf = window.requestAnimationFrame(() => {
      link.frameRaf = 0;
      link.frame = link.chart.timeScale().getVisibleLogicalRange();
      link.framing = false;
      setOff(false);
    });
  }, [setOff]);

  // A pinned row that left the data (new rows, another cadence) is dropped; one that moved is followed.
  const { times } = options;
  useEffect(() => {
    const pinned = pinRef.current;
    if (!pinned) return;
    const index = times.indexOf(pinned.key);
    if (index < 0) unpin();
    else if (index !== pinned.index) {
      pinRef.current = { key: pinned.key, index };
      setPin(pinRef.current);
    }
  }, [times, unpin]);

  const connect = useCallback((chart, host, series) => {
    const link = {
      chart, host, series: () => series, frame: null, framing: false, offFrame: false, frameRaf: 0, pinRaf: 0, announceTimer: 0,
    };
    linkRef.current = link;
    const keyOf = (time) => (optionsRef.current.keyOf ?? timeKey)(time);

    const onMove = (param) => {
      if (param?.sourceEvent && param.point && param.time != null) {
        const key = keyOf(param.time);
        hoverRef.current = key;
        setHoverKey(key);
        return;
      }
      if (!param?.point) {
        hoverRef.current = null;
        setHoverKey(null);
      }
      restoreCrosshair();
    };
    const onClick = (param) => {
      setHint(false);
      const count = optionsRef.current.times.length;
      const index = param?.time != null && Number.isFinite(param?.logical) ? Math.round(param.logical) : null;
      if (index == null || index < 0 || index >= count) {
        unpin();
        return;
      }
      if (pinRef.current?.index === index) unpin();
      else pinAt(index);
    };
    const onRange = (range) => {
      if (!link.framing && link.frame) {
        const off = isOffFrame(range, link.frame);
        if (off !== link.offFrame) setOff(off);
      }
      restoreCrosshair();
    };
    const onDoubleClick = () => reframe();
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      chart.resize(Math.floor(entry.contentRect.width), Math.floor(entry.contentRect.height));
      // A resize keeps the preset in view unless the reader has moved off it.
      if (!link.offFrame) reframe();
    });
    const onBeforePrint = () => {
      const { paint } = optionsRef.current;
      let src = null;
      try {
        chart.applyOptions(themeChartOptions(PRINT_THEME));
        paint?.(PRINT_THEME);
        src = chart.takeScreenshot().toDataURL("image/png");
      } catch {
        src = null; // the canvas stays on paper instead
      } finally {
        chart.applyOptions(themeChartOptions(SCREEN_THEME));
        paint?.(SCREEN_THEME);
      }
      if (src) flushSync(() => setPrintSrc(src));
    };
    const onAfterPrint = () => setPrintSrc(null);

    chart.subscribeCrosshairMove(onMove);
    chart.subscribeClick(onClick);
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
    host.addEventListener("dblclick", onDoubleClick);
    observer.observe(host);
    window.addEventListener("beforeprint", onBeforePrint);
    window.addEventListener("afterprint", onAfterPrint);
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.unsubscribeClick(onClick);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
      host.removeEventListener("dblclick", onDoubleClick);
      observer.disconnect();
      window.removeEventListener("beforeprint", onBeforePrint);
      window.removeEventListener("afterprint", onAfterPrint);
      window.cancelAnimationFrame(link.frameRaf);
      window.cancelAnimationFrame(link.pinRaf);
      window.clearTimeout(link.announceTimer);
      if (linkRef.current === link) linkRef.current = null;
    };
  }, [pinAt, reframe, restoreCrosshair, setOff, unpin]);

  const onKeyDown = useCallback((event) => {
    const link = linkRef.current;
    if (!link || event.target !== event.currentTarget || event.altKey || event.ctrlKey || event.metaKey) return;
    const count = optionsRef.current.times.length;
    const range = link.chart.timeScale().getVisibleLogicalRange();
    const visible = visibleRows(range, count);
    const move = nextReadIndex(event.key, { shift: event.shiftKey, current: pinRef.current?.index ?? null, visible, count });
    if (!move) return;
    event.preventDefault();
    if (move.clear) {
      unpin();
      setAnnouncement("");
      return;
    }
    // Keep the row in view: scroll by whole bars when it lies past either edge.
    if (range && (move.index < range.from + FRAME_TOLERANCE_BARS || move.index > range.to - FRAME_TOLERANCE_BARS)) {
      const width = range.to - range.from;
      const from = move.index < range.from + FRAME_TOLERANCE_BARS ? move.index - 1 : move.index + 1 - width;
      link.chart.timeScale().setVisibleLogicalRange({ from, to: from + width });
    }
    pinAt(move.index, { announce: true });
  }, [pinAt, unpin]);

  return {
    /** the row under the pointer, else the pinned (tapped or keyed) row; null shows the default */
    activeKey: hoverKey ?? pin?.key ?? null,
    pinned: pin,
    offFrame,
    hint,
    announcement,
    printSrc,
    connect,
    reframe,
    figureProps: { tabIndex: 0, role: "group", "aria-roledescription": "chart", onKeyDown },
  };
}
