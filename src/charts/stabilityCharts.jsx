// STAB figures: the yearly QLIKE gap of each comparison model to the model of record, and the
// rolling QLIKE gap of the challenger on the weekly series. Plain SVG sized to the container, like
// svgCharts.jsx, whose frame and axes they share; colours come from CSS classes.
import { useEffect, useState } from "react";
import { scaleBand, scaleLinear, scaleUtc } from "d3-scale";
import { area, line } from "d3-shape";
import { fmtDate, fmtNum, fmtSigned } from "../lib/format";
import { MONO_CHAR_PX, SANS_FONT, fontsReady } from "../theme/chartTheme";
import { Frame, XAxis, YAxis, extent } from "./svgCharts";

// ------------------------------------------------------------------------ row titles

// A row title is 12px Inter at weight 500 (.multiple-title), not the 11px mono of the axes: its
// capitals and figures run wider than the mono 6.6 px ("GJR-GARCH(1,1)-t" is about 7.1 px a
// character). Without a canvas (on the server) or before the font has loaded, each character is
// taken at TITLE_CHAR_PX, above the 6.1 to 6.4 px a whole title measures in Chromium, so a title
// fitted that way is drawn short rather than too long.
const TITLE_FONT = `500 12px ${SANS_FONT}`;
const TITLE_CHAR_PX = 7.4;
let titleContext = null;

/** The drawn width of a row title in px; figures at the width of "0", as tabular figures are drawn. */
export const titleWidth = (text) => {
  const value = String(text);
  if (typeof document !== "undefined" && document.fonts?.check?.(TITLE_FONT)) {
    titleContext ??= document.createElement("canvas").getContext("2d");
    if (titleContext) {
      titleContext.font = TITLE_FONT;
      return titleContext.measureText(value.replace(/\d/g, "0")).width;
    }
  }
  return value.length * TITLE_CHAR_PX;
};

/**
 * A row title that keeps its count: `name` followed by the fixed `suffix`, with only the name cut
 * (with an ellipsis) so the whole title fits `px`. Returns the name to draw. Pure given `measure`.
 * @param {string} name
 * @param {string} suffix  e.g. " · HAR-IV lower in 6/19"
 * @param {number} px
 * @param {(text: string) => number} [measure]
 */
export const fitTitleName = (name, suffix, px, measure = titleWidth) => {
  if (measure(`${name}${suffix}`) <= px) return name;
  for (let keep = name.length - 1; keep > 1; keep -= 1) {
    const cut = `${name.slice(0, keep).trimEnd()}…`;
    if (measure(`${cut}${suffix}`) <= px) return cut;
  }
  return `${name.slice(0, 1)}…`;
};

// ------------------------------------------------------------------------ yearly gap rows

const ROW_LABEL_H = 16;   // the row's direct label above its bars
const ROW_HALF = 16;      // bar room on each side of the zero line
const ROW_GAP = 4;
const ROW_H = ROW_LABEL_H + 2 * ROW_HALF + ROW_GAP;
const AXIS_H = 22;        // year labels under the last row
const GUTTER = 40;        // scale labels left of the first row

/** The chart's height for `count` rows (230 px for the four comparisons). */
export const yearlyGapHeight = (count) => count * ROW_H + AXIS_H;

/**
 * The years to label under a band scale: the first and the last, plus every fifth year (2010,
 * 2015, ...) that keeps a label's width clear of the others.
 */
export const yearTicks = (years, x) => {
  if (!years.length) return [];
  const width = 4 * MONO_CHAR_PX + 10;
  const centre = (year) => x(year) + x.bandwidth() / 2;
  const first = years[0];
  const last = years[years.length - 1];
  const placed = [first];
  const clear = (year) => [...placed, last].every((other) => Math.abs(centre(other) - centre(year)) >= width);
  for (const year of years) {
    if (year !== first && year !== last && year % 5 === 0 && clear(year)) placed.push(year);
  }
  if (last !== first) placed.push(last);
  return placed;
};

/**
 * The drawing of YearlyGapRows at a given width (exported so it can be rendered without a layout).
 * One row per comparison model; each bar is that model's QLIKE minus the record's in one year, on
 * one symmetric scale shared by every row: above the line the record was lower that year. A bar
 * beyond ±limit is cut at the edge and marked with a break.
 *
 * @param {{rows: {key:string,label:string,tone:string,wins:number,total:number,
 *   gaps:{year:number,value:number,model:number,record:number}[]}[], recordLabel: string,
 *   limit: number, width: number}} props
 */
export function YearlyGapSvg({ rows, recordLabel, limit, width }) {
  const years = [...new Set(rows.flatMap((row) => row.gaps.map((gap) => gap.year)))].sort((a, b) => a - b);
  const height = yearlyGapHeight(rows.length);
  const x = scaleBand().domain(years).range([GUTTER, width - 4]).paddingInner(0.25).paddingOuter(0.1);
  const tick = scaleLinear().domain([-limit, limit]).ticks(2).filter((value) => value > 0 && value <= limit).pop() ?? null;
  const offset = (value) => (Math.max(-limit, Math.min(limit, value)) / limit) * ROW_HALF;
  const span = years.length ? `${years[0]}–${years[years.length - 1]}` : "";
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="svg-chart" role="img"
      aria-label={`Yearly QLIKE of each model minus ${recordLabel}'s, ${span}: ${rows
        .map((row) => `${row.label}, ${recordLabel} lower in ${row.wins} of ${row.total} years`).join("; ")}`}>
      {rows.map((row, index) => {
        const top = index * ROW_H;
        const zero = top + ROW_LABEL_H + ROW_HALF;
        const suffix = ` · ${recordLabel} lower in ${row.wins}/${row.total}`;
        return (
          <g key={row.key} className="gap-row">
            {/* Fitted to the plot (the full title in <title>): the count stays whole, only the model name is cut. */}
            <text className={`multiple-title label-${row.tone}`} x={GUTTER} y={top + 11}>
              <title>{`${row.label}${suffix}`}</title>
              {`${fitTitleName(row.label, suffix, width - GUTTER - 4)}${suffix}`}
            </text>
            {tick != null && [tick, -tick].map((value) => (
              <line key={value} className="grid-line" x1={GUTTER} x2={width - 4} y1={zero - offset(value)} y2={zero - offset(value)} />
            ))}
            {index === 0 && tick != null && [tick, -tick].map((value) => (
              <text key={value} className="axis-label" x={GUTTER - 6} y={zero - offset(value)} dy="0.32em" textAnchor="end">
                {fmtSigned(value, 2)}
              </text>
            ))}
            {row.gaps.map((gap) => {
              const h = offset(gap.value);
              const size = Math.max(1, Math.abs(h));
              const y = h >= 0 ? zero - size : zero;
              const cut = Math.abs(gap.value) > limit;
              const tone = gap.value > 0 ? "model" : gap.value < 0 ? row.tone : "faint";
              return (
                <g key={gap.year}>
                  <rect className={`gap-bar fill-${tone}`} x={x(gap.year)} width={x.bandwidth()} y={y} height={size}>
                    <title>{`${gap.year}: ${row.label} ${fmtNum(gap.model, 3)} vs ${recordLabel} ${fmtNum(gap.record, 3)}${cut ? " (bar cut at the scale's edge)" : ""}`}</title>
                  </rect>
                  {cut && (
                    <line className="gap-break" x1={x(gap.year) - 1} x2={x(gap.year) + x.bandwidth() + 1}
                      y1={h >= 0 ? zero - size + 3 : zero + size - 3} y2={h >= 0 ? zero - size + 3 : zero + size - 3} />
                  )}
                </g>
              );
            })}
            <line className="stroke-axis" x1={GUTTER} x2={width - 4} y1={zero} y2={zero} />
          </g>
        );
      })}
      <g className="axis axis-x">
        {yearTicks(years, x).map((year) => (
          <text key={year} className="axis-label" x={x(year) + x.bandwidth() / 2} y={height - 6} textAnchor="middle">{year}</text>
        ))}
      </g>
    </svg>
  );
}

/** YearlyGapRows, sized to its container; drawn again once the title font has loaded (see titleWidth). */
export function YearlyGapRows({ rows, recordLabel, limit }) {
  const [, setFontsLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    fontsReady().then(() => { if (live) setFontsLoaded(true); });
    return () => { live = false; };
  }, []);
  return (
    <Frame
      height={yearlyGapHeight(rows.length)}
      draw={(width) => <YearlyGapSvg rows={rows} recordLabel={recordLabel} limit={limit} width={width} />}
    />
  );
}

// ----------------------------------------------------------------------------- rolling gap

const ROLLING_HEIGHT = 180;

/**
 * The drawing of RollingGap at a given width: the rolling mean QLIKE gap, challenger minus the
 * record, against a zero line. Below zero (tinted) the challenger had the lower loss over the
 * trailing window. The latest value is labelled at the line's end.
 *
 * @param {{points: {date:string, value:number}[], recordLabel: string, weeks: number, width: number}} props
 */
export function RollingGapSvg({ points, recordLabel, weeks, width }) {
  const height = ROLLING_HEIGHT;
  const rows = points.map((point) => ({ t: Date.parse(point.date), value: point.value }))
    .filter((point) => Number.isFinite(point.t) && Number.isFinite(point.value));
  if (rows.length < 2) return null;
  const last = rows[rows.length - 1];
  const latest = fmtSigned(last.value, 3);
  const m = { top: 12, right: Math.ceil(latest.length * MONO_CHAR_PX) + 14, bottom: 24, left: 44 };
  const x = scaleUtc().domain(extent(rows.map((row) => row.t))).range([m.left, width - m.right]);
  const [lo, hi] = extent(rows.map((row) => row.value));
  const y = scaleLinear().domain([Math.min(lo, 0), Math.max(hi, 0)]).nice(4).range([height - m.bottom, m.top]);
  const below = area().x((row) => x(row.t)).y0(y(0)).y1((row) => y(Math.min(0, row.value)))(rows);
  const path = line().x((row) => x(row.t)).y((row) => y(row.value))(rows);
  const yearFormat = (t) => String(new Date(t).getUTCFullYear());
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="svg-chart" role="img"
      aria-label={`Rolling ${weeks}-week mean QLIKE gap, challenger minus ${recordLabel}, ${fmtDate(points[0].date)} to `
        + `${fmtDate(points[points.length - 1].date)}: from ${fmtSigned(lo, 3)} to ${fmtSigned(hi, 3)}, latest ${latest}; `
        + `below zero the challenger had the lower loss`}>
      {/* The domain's foot sits on the year labels' baseline: its tick would run into the first year. */}
      <YAxis scale={y} ticks={y.ticks(4).filter((t) => t !== y.domain()[0])} format={(t) => fmtSigned(t, 2)} left={m.left} right={width - m.right} />
      <path d={below} className="fill-gap-better" />
      <line className="stroke-axis" x1={m.left} x2={width - m.right} y1={y(0)} y2={y(0)} />
      <path d={path} className="stroke-ink" fill="none" />
      <circle cx={x(last.t)} cy={y(last.value)} r={3} className="fill-ink" />
      <text className="value-label has-halo" x={x(last.t) + 6} y={y(last.value)} dy="0.32em">{latest}</text>
      <text className="axis-note has-halo" x={m.left + 6} y={m.top + 4} dy="0.32em">{`↑ ${recordLabel} lower`}</text>
      <text className="axis-note has-halo" x={m.left + 6} y={height - m.bottom - 6}>↓ challenger lower</text>
      <XAxis scale={x} ticks={x.ticks(width < 480 ? 4 : 8)} format={yearFormat} y={height - m.bottom} />
    </svg>
  );
}

/** RollingGap, sized to its container. */
export function RollingGap({ points, recordLabel, weeks }) {
  return (
    <Frame
      height={ROLLING_HEIGHT}
      draw={(width) => <RollingGapSvg points={points} recordLabel={recordLabel} weeks={weeks} width={width} />}
    />
  );
}
