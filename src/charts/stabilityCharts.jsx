// STAB figures: the yearly QLIKE gap of each comparison model to the model of record, the rolling
// Diebold-Mariano t with its fluctuation band, and the cumulative QLIKE difference on the weekly series. Plain SVG sized to the container, like
// svgCharts.jsx, whose frame and axes they share; colours come from CSS classes.
import { useEffect, useState } from "react";
import { scaleBand, scaleLinear, scaleUtc } from "d3-scale";
import { line } from "d3-shape";
import { fmtDate, fmtNum, fmtSigned } from "../lib/format";
import { asArray } from "../lib/research";
import { MONO_CHAR_PX, SANS_FONT, fontsReady } from "../theme/chartTheme";
import { Frame, XAxis, extent } from "./svgCharts";

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

// ------------------------------------------------------------------- rolling test and cumulative difference

const MULTIPLE_H = 86;    // one comparison's row: its title and its plot
const MULTIPLE_TITLE = 16;
const MULTIPLE_AXIS = 22; // year labels under the last row
const MULTIPLE_LEFT = 40;

/** The rolling-test figure's height for `count` rows (280 px for three). */
export const fluctuationHeight = (count) => count * MULTIPLE_H + MULTIPLE_AXIS;

const yearFormat = (t) => String(new Date(t).getUTCFullYear());

/** The weekly points of a series as [time, value], from its first non-null value. */
const points = (dates, values) => values.map((value, index) => [Date.parse(dates[index]), value])
  .filter(([t, value]) => Number.isFinite(t) && value != null && Number.isFinite(value));

/**
 * The drawing of FluctuationRows at a given width: for each comparison, the rolling Diebold-Mariano t of the
 * model of record against it on one symmetric scale shared by every row, the band ±c in the noise-zone fill with
 * dashed edges, the path as a thin line, and each run outside the band as published (`outside_5pct`, its weekly
 * span) drawn thick in the tone of the side with the lower loss. The client never decides a crossing itself.
 *
 * @param {{rows: Array, setup: {c5:number}, recordLabel: string, width: number}} props
 */
export function FluctuationSvg({ rows, setup, recordLabel, width }) {
  const height = fluctuationHeight(rows.length);
  const all = rows.map((row) => points(row.dates, row.values));
  const times = all.flat().map(([t]) => t);
  const peak = Math.max(0, ...all.flat().map(([, value]) => Math.abs(value)));
  const limit = Math.max(1.25 * setup.c5, Math.ceil(peak));
  const x = scaleUtc().domain(extent(times)).range([MULTIPLE_LEFT, width - 6]);
  const plotH = MULTIPLE_H - MULTIPLE_TITLE - 6;
  const c = fmtNum(setup.c5, 2);
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="svg-chart" role="img"
      aria-label={`Rolling Diebold-Mariano t of ${recordLabel} against each model, with the 5% band at ±${c}: ${rows
        .map((row) => `${row.label}, ${row.block.rejects_5pct ? "outside the band" : "inside the band"}, max |t| ${fmtNum(row.block.sup, 2)}`)
        .join("; ")}`}>
      {rows.map((row, index) => {
        const top = index * MULTIPLE_H + MULTIPLE_TITLE;
        const y = scaleLinear().domain([-limit, limit]).range([top + plotH, top]);
        const path = line().x(([t]) => x(t)).y(([, value]) => y(value));
        const runs = asArray(row.block.outside_5pct).map((run) => {
          const segment = row.values.slice(run.week_from, run.week_to + 1)
            .map((value, offset) => [Date.parse(row.dates[run.week_from + offset]), value])
            .filter(([t, value]) => Number.isFinite(t) && value != null);
          return { run, segment };
        }).filter(({ segment }) => segment.length > 0);
        return (
          <g key={row.name} className="fluct-row">
            <text className={`multiple-title label-${row.challenger ? "alt" : "ink"}`} x={MULTIPLE_LEFT} y={top - 5}>
              <title>{`${row.label}: above zero ${row.label} had the lower loss`}</title>
              {fitTitleName(row.label, ` · max |t| ${fmtNum(row.block.sup, 2)}`, width - MULTIPLE_LEFT - 6)}
              {` · max |t| ${fmtNum(row.block.sup, 2)}`}
            </text>
            <rect className="fill-noise-zone" x={MULTIPLE_LEFT} width={width - 6 - MULTIPLE_LEFT}
              y={y(setup.c5)} height={y(-setup.c5) - y(setup.c5)} />
            {[setup.c5, -setup.c5].map((edge) => (
              <line key={edge} className="fluct-edge" x1={MULTIPLE_LEFT} x2={width - 6} y1={y(edge)} y2={y(edge)} />
            ))}
            {index === 0 && (
              <text className="axis-label" x={MULTIPLE_LEFT - 6} y={y(setup.c5)} dy="0.32em" textAnchor="end">{`±${c}`}</text>
            )}
            <line className="stroke-axis" x1={MULTIPLE_LEFT} x2={width - 6} y1={y(0)} y2={y(0)} />
            <path d={path(all[index]) ?? undefined} className="fluct-path" fill="none" />
            {runs.map(({ run, segment }) => (
              <path key={`${run.from}-${run.to}`} d={(segment.length > 1 ? path(segment) : `M${x(segment[0][0])},${y(segment[0][1])}h0.1`) ?? undefined}
                className={`fluct-run ${run.sign > 0 ? (row.challenger ? "stroke-alt" : "stroke-ink") : "stroke-model"}`} fill="none">
                <title>{`Outside the 5% band in windows ending ${fmtDate(run.from)} to ${fmtDate(run.to)}, `
                  + `${run.sign > 0 ? row.label : recordLabel} lower`}</title>
              </path>
            ))}
          </g>
        );
      })}
      <XAxis scale={x} ticks={x.ticks(width < 480 ? 4 : 8)} format={yearFormat} y={height - MULTIPLE_AXIS} />
    </svg>
  );
}

/** The cumulative figure's height for `count` rows. */
export const cumulativeHeight = (count) => count * MULTIPLE_H + MULTIPLE_AXIS;

/**
 * The drawing of CumulativeRows at a given width: each comparison's running sum of the model of record's daily QLIKE
 * minus the model's, read weekly, against a zero line. The rows share one scale, except one whose range is far
 * larger (persistence), which gets its own and says so in its title.
 */
export function CumulativeSvg({ rows, recordLabel, endDate, width }) {
  const height = cumulativeHeight(rows.length);
  const all = rows.map((row) => points(row.dates, row.values));
  // The number at each line's end is the every-day total (row.end), else the line's last weekly value.
  const endText = (row, series) => fmtSigned(row.end ?? series[series.length - 1]?.[1], 2);
  const right = 8 + MONO_CHAR_PX * Math.max(6, ...rows.map((row, i) => endText(row, all[i]).length));
  const x = scaleUtc().domain(extent(all.flat().map(([t]) => t))).range([MULTIPLE_LEFT, width - right]);
  const span = (series) => Math.max(1e-9, ...series.map(([, value]) => Math.abs(value)));
  const spans = all.map(span);
  const typical = Math.max(...spans.filter((value) => value <= 10 * Math.min(...spans)));
  const plotH = MULTIPLE_H - MULTIPLE_TITLE - 6;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="svg-chart" role="img"
      aria-label={`Cumulative QLIKE of ${recordLabel} minus each model, summed over every out-of-sample day${endDate
        ? ` to ${fmtDate(endDate)}` : ""}: ${rows.map((row, i) => `${row.label} ${endText(row, all[i])}`).join("; ")}; above `
        + "zero the model had the lower loss"}>
      {rows.map((row, index) => {
        const own = spans[index] > 10 * typical;
        const limit = own ? spans[index] : typical;
        const top = index * MULTIPLE_H + MULTIPLE_TITLE;
        const y = scaleLinear().domain([-limit, limit]).nice(2).range([top + plotH, top]);
        const series = all[index];
        const last = series[series.length - 1];
        return (
          <g key={row.name} className="cum-row">
            <text className={`multiple-title label-${row.challenger ? "alt" : "ink"}`} x={MULTIPLE_LEFT} y={top - 5}>
              {`${row.label}${own ? " · own scale" : ""}`}
            </text>
            <line className="stroke-axis" x1={MULTIPLE_LEFT} x2={width - right} y1={y(0)} y2={y(0)} />
            {(index === 0 || own) && y.ticks(2).filter((t) => t !== 0).map((t) => (
              <text key={t} className="axis-label" x={MULTIPLE_LEFT - 6} y={y(t)} dy="0.32em" textAnchor="end">{fmtSigned(t, 0)}</text>
            ))}
            <path d={line().x(([t]) => x(t)).y(([, value]) => y(value))(series) ?? undefined}
              className={row.challenger ? "stroke-alt" : "stroke-ink"} fill="none" />
            {last && <text className="value-label has-halo" x={x(last[0]) + 6} y={y(last[1])} dy="0.32em">{endText(row, series)}</text>}
          </g>
        );
      })}
      <XAxis scale={x} ticks={x.ticks(width < 480 ? 4 : 8)} format={yearFormat} y={height - MULTIPLE_AXIS} />
    </svg>
  );
}

/** FluctuationRows, sized to its container; drawn again once the title font has loaded. */
export function FluctuationRows({ rows, setup, recordLabel }) {
  const [, setFontsLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    fontsReady().then(() => { if (live) setFontsLoaded(true); });
    return () => { live = false; };
  }, []);
  return (
    <Frame height={fluctuationHeight(rows.length)}
      draw={(width) => <FluctuationSvg rows={rows} setup={setup} recordLabel={recordLabel} width={width} />} />
  );
}

/** CumulativeRows, sized to its container. */
export function CumulativeRows({ rows, recordLabel, endDate }) {
  return (
    <Frame height={cumulativeHeight(rows.length)}
      draw={(width) => <CumulativeSvg rows={rows} recordLabel={recordLabel} endDate={endDate} width={width} />} />
  );
}
