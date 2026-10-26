// LIVE: every recorded call in live_record.recent as it was recorded, oldest to newest. One column
// per call: the forecast (dot), its 80% band (whisker), the challenger (ring) and, once resolved,
// the realized vol (a bar, filled when it fell inside the band, open with a warn stroke when
// outside). Calls that are never scored (skipped at a roll, not sealed in time, expired) are drawn
// dashed and grey. Plain SVG sized to its container (useMeasure), like the figures in svgCharts.jsx.
import { scaleLinear, scalePoint } from "d3-scale";
import useMeasure from "../lib/useMeasure";
import { fmtDate, fmtNum, num } from "../lib/format";

// Heights at or above 640px and below, the breakpoint LazyMount's placeholder uses for the same values.
const DEFAULT_HEIGHTS = { wide: 200, narrow: 180 };
const NARROW_QUERY = "(max-width: 640px)";
const MARGIN = { top: 22, right: 12, bottom: 24, left: 40 };
// 11px JetBrains Mono advances about 6.6px per character; a date tick ("Oct 23") needs about 52px.
const TICK_PX = 52;
const CAP_PX = 4;
const UNSCORED = new Set(["skipped_roll", "late_commit", "expired"]);

const isNarrow = () => typeof window !== "undefined" && typeof window.matchMedia === "function"
  && window.matchMedia(NARROW_QUERY).matches;

/** A call's numbers, or null when it has nothing to draw. Pure. */
export const callMarks = (row) => {
  const forecast = num(row?.forecast_pct);
  if (forecast == null || typeof row?.as_of !== "string") return null;
  const lo = num(row.lo80_pct);
  const hi = num(row.hi80_pct);
  const band = lo != null && hi != null && lo <= hi ? { lo, hi } : null;
  const realized = row.status === "resolved" ? num(row.realized_pct) : null;
  return {
    asOf: row.as_of,
    status: typeof row.status === "string" ? row.status : null,
    unscored: UNSCORED.has(row.status),
    forecast,
    band,
    challenger: num(row.challenger_pct),
    realized,
    inside: realized != null && band ? realized >= band.lo && realized <= band.hi : null,
  };
};

/** The calls that can be drawn, oldest first. Pure. */
export const orderedMarks = (calls) =>
  (calls ?? []).map(callMarks).filter(Boolean).sort((a, b) => (a.asOf < b.asOf ? -1 : a.asOf > b.asOf ? 1 : 0));

/**
 * The plot at a known size (the default export measures its container and renders this).
 *
 * @param {{calls: object[], width: number, height: number, nextResultDue?: string|null, bandLevel?: number,
 *   challengerLabel?: string|null, statusLabel?: (status: string|null) => string}} props
 */
export function LiveCallsPlot({
  calls, width, height, nextResultDue = null, bandLevel = 80, challengerLabel = null, statusLabel = (s) => s ?? "",
}) {
  const marks = orderedMarks(calls);
  if (!marks.length || !(width > 0)) return null;
  const m = MARGIN;
  // Anchored at zero, so the width of each band and the distance to the outcome read honestly.
  const top = Math.max(1, ...marks.flatMap((c) => [c.forecast, c.band?.hi, c.challenger, c.realized]).filter((v) => v != null));
  const y = scaleLinear().domain([0, top * 1.08]).nice(4).range([height - m.bottom, m.top]);
  const x = scalePoint().domain(marks.map((_, index) => index)).range([m.left, width - m.right]).padding(0.5);
  // Date labels thinned to fit, counted back from the newest call so it is always labelled.
  const room = Math.max(1, Math.floor((width - m.left - m.right) / TICK_PX));
  const step = Math.max(1, Math.ceil(marks.length / room));
  const due = typeof nextResultDue === "string" ? fmtDate(nextResultDue) : null;
  const resolvedCount = marks.filter((c) => c.realized != null).length;
  const challengerName = challengerLabel ?? "challenger";
  const label = `${marks.length} recorded ${marks.length === 1 ? "call" : "calls"} from ${fmtDate(marks[0].asOf)} to `
    + `${fmtDate(marks[marks.length - 1].asOf)}: forecast and ${bandLevel}% band per call`
    + `${resolvedCount ? `, ${resolvedCount} with the realized vol` : ", none resolved yet"}${due ? `; next result due ${due}` : ""}`;
  return (
    <svg width={width} height={height} className="svg-chart live-calls" role="img" aria-label={label}>
      <g className="axis axis-y">
        {y.ticks(4).map((tick) => (
          <g key={tick} transform={`translate(0,${y(tick)})`}>
            <line className="grid-line" x1={m.left} x2={width - m.right} />
            <text className="axis-label" x={m.left - 6} dy="0.32em" textAnchor="end">{`${tick}%`}</text>
          </g>
        ))}
      </g>
      {due && (
        <text className="axis-note live-due" x={width - m.right} y={m.top - 10} textAnchor="end">{`next result due ${due}`}</text>
      )}
      {marks.map((c, index) => {
        const cx = x(index);
        const when = `as of ${fmtDate(c.asOf)}`;
        const muted = c.unscored;
        return (
          <g key={`${c.asOf}-${index}`} className={`live-call${muted ? " is-unscored" : ""}`}>
            {c.band && (
              <g className={`${muted ? "stroke-neutral dashed" : "stroke-model"} whisker`}>
                <title>{`${bandLevel}% band ${fmtNum(c.band.lo, 1)}–${fmtNum(c.band.hi, 1)}%, ${when}${muted ? ` (${statusLabel(c.status)}, never scored)` : ""}`}</title>
                <line x1={cx} x2={cx} y1={y(c.band.lo)} y2={y(c.band.hi)} />
                <line x1={cx - CAP_PX} x2={cx + CAP_PX} y1={y(c.band.lo)} y2={y(c.band.lo)} />
                <line x1={cx - CAP_PX} x2={cx + CAP_PX} y1={y(c.band.hi)} y2={y(c.band.hi)} />
              </g>
            )}
            {c.challenger != null && (
              <circle cx={cx} cy={y(c.challenger)} r={5} className={muted ? "ring-muted" : "ring-alt is-open"}>
                <title>{`${challengerName} ${fmtNum(c.challenger, 1)}%, ${when}`}</title>
              </circle>
            )}
            <circle cx={cx} cy={y(c.forecast)} r={3} className={`live-forecast ${muted ? "fill-muted" : "fill-model"}`}>
              <title>{`forecast ${fmtNum(c.forecast, 1)}%, ${when} (${statusLabel(c.status)})`}</title>
            </circle>
            {c.realized != null && (
              <rect x={cx - 7} y={y(c.realized) - 2.5} width={14} height={5}
                className={`live-truth${c.inside === false ? " is-outside" : ""}`}>
                <title>{`realized ${fmtNum(c.realized, 1)}%, ${when}: ${c.inside == null ? "no band to compare" : c.inside ? "inside the band" : "outside the band"}`}</title>
              </rect>
            )}
          </g>
        );
      })}
      <g className="axis axis-x" transform={`translate(0,${height - m.bottom})`}>
        {marks.map((c, index) => ((marks.length - 1 - index) % step === 0 ? (
          <text key={index} className="axis-label" x={x(index)} dy="1.1em" textAnchor="middle">{fmtDate(c.asOf, { year: false })}</text>
        ) : null))}
      </g>
    </svg>
  );
}

/**
 * @param {{calls: object[], nextResultDue?: string|null, bandLevel?: number, challengerLabel?: string|null,
 *   statusLabel?: (status: string|null) => string, heights?: {wide: number, narrow: number}}} props
 *   `calls` are live_record.recent rows in any order. `heights` must match the LazyMount
 *   placeholder's, so nothing shifts when the chart mounts.
 */
export default function LiveCalls({ heights = DEFAULT_HEIGHTS, ...props }) {
  const [ref, width] = useMeasure();
  const height = width > 0 && isNarrow() ? heights.narrow : heights.wide;
  return (
    <div ref={ref} className="svg-frame live-calls-frame" style={{ minHeight: height }}>
      <LiveCallsPlot {...props} width={width} height={height} />
    </div>
  );
}
