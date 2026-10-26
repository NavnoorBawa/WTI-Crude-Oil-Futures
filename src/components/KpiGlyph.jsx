/**
 * The small comparison under a verdict tile's value, drawn from the glyph data src/lib/tiles.js
 * computes. Spans only (no SVG, no div): positions are percentages of the domain on a 100%-wide,
 * 14px track, so dots stay round at every width and the tile's markup keeps one <div>. Decorative
 * for assistive technology: the tile's text states every value the glyph draws.
 */

/** A value's position on the track, in percent of the domain, clamped to 0-100. */
export const glyphPct = (value, domain) => {
  const [lo, hi] = Array.isArray(domain) ? domain : [];
  if (![value, lo, hi].every(Number.isFinite) || hi <= lo) return null;
  return Math.min(100, Math.max(0, ((value - lo) / (hi - lo)) * 100));
};

const pct = (value) => `${value.toFixed(2)}%`;

function Mark({ mark, domain }) {
  const tone = `kg-${mark.tone ?? "text"}`;
  if (mark.type === "bar") {
    const from = glyphPct(Math.min(mark.from, mark.to), domain);
    const to = glyphPct(Math.max(mark.from, mark.to), domain);
    if (from == null || to == null) return null;
    return <span className={`kg-bar ${tone}`} style={{ left: pct(from), width: pct(to - from) }} />;
  }
  const at = glyphPct(mark.at, domain);
  if (at == null) return null;
  if (mark.type === "tick") return <span className={`kg-tick ${tone}`} style={{ left: pct(at) }} />;
  return <span className={`kg-dot ${tone}${mark.type === "ring" ? " is-ring" : ""}`} style={{ left: pct(at) }} />;
}

/**
 * @param {object} props
 * @param {"axis"|"range"|"count"|"whisker"|"cells"|"progress"} props.kind
 * @param {[number, number]} [props.domain]   the track's value range (axis kinds)
 * @param {{type:"tick"|"bar"|"dot"|"ring", at?:number, from?:number, to?:number, tone?:string}[]} [props.marks]
 * @param {{tone:string, filled:boolean}[]} [props.cells]  one cell each (cells, progress)
 */
export default function KpiGlyph({ kind, domain, marks = [], cells = [] }) {
  if (kind === "cells" || kind === "progress") {
    if (!cells.length) return null;
    return (
      <span className={`kpi-glyph-track kg-cells kg-${kind}`}>
        {cells.map((cell, index) => (
          <span key={index} className={`kg-cell kg-${cell.tone ?? "text"}${cell.filled ? " is-filled" : ""}`} />
        ))}
      </span>
    );
  }
  if (glyphPct(domain?.[0], domain) == null) return null;
  return (
    <span className={`kpi-glyph-track kg-axis kg-${kind}`}>
      {marks.map((mark, index) => <Mark key={index} mark={mark} domain={domain} />)}
    </span>
  );
}
