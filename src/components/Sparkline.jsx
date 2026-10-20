import { fmtUsd, num } from "../lib/format";

/** SVG path for a sparkline through `values` in a width x height box (y up = higher). */
export const sparkPath = (values, width, height, pad = 1.5) => {
  const points = values.map(num).filter((value) => value != null);
  if (points.length < 2) return null;
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const step = (width - 2 * pad) / (points.length - 1);
  return points
    .map((value, index) => `${index === 0 ? "M" : "L"}${(pad + index * step).toFixed(1)},${(pad + (1 - (value - lo) / span) * (height - 2 * pad)).toFixed(1)}`)
    .join(" ");
};

/**
 * A word-sized line of recent closes (data.json vol_forecast.recent_closes). Decorative for sighted
 * readers; the range is spelled out for assistive tech.
 *
 * @param {{values: number[], label?: string, width?: number, height?: number, muted?: boolean}} props
 *   muted: drawn faint, for a series that no longer matches the price beside it (after a roll)
 */
export default function Sparkline({ values, label = "recent closes", width = 72, height = 18, muted = false }) {
  const points = values.map(num).filter((value) => value != null);
  const d = sparkPath(points, width, height);
  if (!d) return null;
  return (
    <span className={`sparkline${muted ? " is-muted" : ""}`} title={label}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" focusable="false">
        <path d={d} />
      </svg>
      <span className="sr-only">{`${label}: ${points.length} sessions, low ${fmtUsd(Math.min(...points))}, high ${fmtUsd(Math.max(...points))}`}</span>
    </span>
  );
}
