// The reference sections' figures (LEDG, METH), drawn as plain SVG like svgCharts.jsx and loaded in
// their own lazy chunk. Each figure is a measured wrapper around a pure SVG component that takes the
// width, so the drawing can be rendered and tested on the server at a fixed width.
import { scaleLinear, scaleUtc } from "d3-scale";
import { line } from "d3-shape";
import { fmtDate, fmtNum } from "../lib/format";
import { STRIP, ledgerStripLabel, ledgerStripLayout, placeStripLabels } from "../lib/reference";
import { MONO_CHAR_PX } from "../theme/chartTheme";
import { Frame, XAxis, YAxis, extent } from "./svgCharts.jsx";

// ------------------------------------------------------------------------------- ledger strip

// The strip's geometry and label placement live in lib/reference.js, so LEDG can reserve its height before this
// chunk arrives; placeStripLabels is re-exported for the tests.
export { placeStripLabels };

/**
 * The research ledger as one strip of cells, one per hypothesis, in the ledger's default order and
 * coloured by status (a supported claim that survives Benjamini-Hochberg is outlined in the model
 * colour), with each status group's count written under its first cell.
 *
 * @param {{strip: {cells: {id: string, kind: string}[], groups: {key: string, label: string, count: number,
 *   survivors: number, start: number}[]}, width: number}} props
 */
export function LedgerStripSvg({ strip, width }) {
  const layout = ledgerStripLayout(strip, width);
  if (!layout) return null;
  const { cells, groups } = strip;
  const { cellWidth, cellX, labels, placed, height } = layout;
  const baseline = (lineIndex) => STRIP.cellHeight + STRIP.labelTop + lineIndex * STRIP.labelLine + 9;
  const keyKind = (key) => key.replace(/_/g, "-");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="svg-chart ledger-strip" role="img" aria-label={ledgerStripLabel(groups)}>
      {cells.map((cell, index) => {
        const x = cellX(index, cell.status);
        // The survivor's outline is drawn inside its cell, so neighbouring cells keep their gap.
        const inset = cell.kind === "survivor" ? Math.min(0.75, cellWidth / 4) : 0;
        return (
          <rect key={cell.id} x={x + inset} y={inset} width={Math.max(0.5, cellWidth - 2 * inset)} height={STRIP.cellHeight - 2 * inset}
            className={`strip-cell is-${cell.kind.replace(/_/g, "-")}`} />
        );
      })}
      {labels.map(({ group, words, start }, index) => {
        const { x, line: lineIndex } = placed[index];
        return (
          <g key={group.key}>
            <line className="strip-leader" x1={start + 0.5} x2={start + 0.5} y1={STRIP.cellHeight + 1} y2={STRIP.cellHeight + STRIP.labelTop - 1} />
            <rect className={`strip-cell strip-key is-${keyKind(group.key)}`} x={x} y={baseline(lineIndex) - STRIP.key} width={STRIP.key} height={STRIP.key} />
            <text className="strip-label" x={x + STRIP.key + STRIP.keyGap} y={baseline(lineIndex)}>
              <tspan className="strip-count">{words.count}</tspan>
              <tspan>{words.label}</tspan>
              {words.survivors && <tspan className="label-model">{words.survivors}</tspan>}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/**
 * The measured strip (see LedgerStripSvg). Its frame sets no height of its own: inside LEDG's LazyMount it keeps the
 * height reserved for it (reference-sections.css), which LEDG computes from the same layout, so mounting it moves
 * nothing below it; it grows only where its labels need more lines than at the width the reservation assumed.
 */
export function LedgerStrip({ strip }) {
  return <Frame className="ledger-strip-frame" draw={(width) => <LedgerStripSvg strip={strip} width={width} />} />;
}

// ---------------------------------------------------------------------------- elasticity path

const ELASTICITY_H = 220;
const parseDay = (date) => Date.parse(`${date}T00:00:00Z`);

/** The chart's accessible summary: what is drawn, over which span, and the ranges. */
export const elasticityLabel = (path) => {
  const range = (stats, name) => (stats
    ? `${name} from ${fmtNum(stats.min, 2)} to ${fmtNum(stats.max, 2)}, last ${fmtNum(stats.last, 2)}`
    : null);
  return [
    `HAR-IV coefficients at each weekly walk-forward refit, ${fmtDate(path.first)} to ${fmtDate(path.last)}`,
    range(path.ovx, "OVX elasticity"),
    range(path.rv, "summed realized-vol elasticity"),
  ].filter(Boolean).join("; ");
};

/**
 * True when a polyline (points in px; a null breaks it) passes through a box {x0, x1, y0, y1}. A
 * segment counts by its bounding box, which errs towards a hit (weekly segments are a few px). Pure.
 * @param {{x0:number, x1:number, y0:number, y1:number}} box
 * @param {({px:number, py:number}|null)[]} points
 */
export const boxMeetsPolyline = (box, points) => {
  for (let index = 1; index < points.length; index += 1) {
    const a = points[index - 1];
    const b = points[index];
    if (!a || !b) continue;
    if (Math.max(a.px, b.px) < box.x0 || Math.min(a.px, b.px) > box.x1) continue;
    if (Math.max(a.py, b.py) < box.y0 || Math.min(a.py, b.py) > box.y1) continue;
    return true;
  }
  return false;
};

/**
 * The OVX coefficient and the summed realized-vol coefficients of HAR-IV at each walk-forward refit,
 * with the OVX extremes marked and each line labelled at its end. An extreme's label sits beside its
 * dot (max above, min below) unless a line would run through it there (the summed-RV line passes
 * just under the OVX trough); then it moves to the end-label column under the OVX value, with its year.
 *
 * @param {{path: {points: {date: string, ovx: number, rv: number|null}[], first: string, last: string,
 *   ovx: {min: number, minDate: string, max: number, maxDate: string, last: number}|null,
 *   rv: {last: number}|null}, width: number}} props
 */
export function ElasticityPathSvg({ path, width }) {
  if (!path?.points?.length || width <= 0) return null;
  const height = ELASTICITY_H;
  const m = { top: 18, right: 112, bottom: 26, left: 40 };
  const right = Math.max(m.left + 40, width - m.right);
  const x = scaleUtc().domain([parseDay(path.first), parseDay(path.last)]).range([m.left, right]);
  const values = path.points.flatMap((point) => [point.ovx, point.rv]);
  const y = scaleLinear().domain(extent([0, 1, ...values])).nice().range([height - m.bottom, m.top]);
  const draw = (key) => line()
    .defined((point) => point[key] != null)
    .x((point) => x(parseDay(point.date)))
    .y((point) => y(point[key]))(path.points);
  const clampX = (value, text) => Math.min(Math.max(value, m.left + (text.length * MONO_CHAR_PX) / 2), right - (text.length * MONO_CHAR_PX) / 2);
  const polyline = (key) => path.points.map((point) => (point[key] != null ? { px: x(parseDay(point.date)), py: y(point[key]) } : null));
  const lines = [polyline("ovx"), ...(path.rv ? [polyline("rv")] : [])];
  const marks = (path.ovx ? [
    { key: "max", date: path.ovx.maxDate, value: path.ovx.max, dy: -8 },
    { key: "min", date: path.ovx.minDate, value: path.ovx.min, dy: 16 },
  ] : []).map((mark) => {
    const label = `${mark.key} ${fmtNum(mark.value, 2)}`;
    const cx = x(parseDay(mark.date));
    const lx = clampX(cx, label);
    const half = (label.length * MONO_CHAR_PX) / 2;
    const baseline = y(mark.value) + mark.dy;
    // The label's box at the 11px font, its halo and a line's half-width included.
    const box = { x0: lx - half - 3, x1: lx + half + 3, y0: baseline - 12, y1: baseline + 5 };
    return { ...mark, label, cx, lx, inPlot: !lines.some((points) => boxMeetsPolyline(box, points)) };
  });
  const moved = marks.filter((mark) => !mark.inPlot)
    .map((mark) => `${mark.label}, ${new Date(parseDay(mark.date)).getUTCFullYear()}`);
  const ends = [
    path.ovx && { key: "ovx", name: "OVX", value: path.ovx.last, tone: "implied", notes: moved },
    path.rv && { key: "rv", name: "RV, summed", value: path.rv.last, tone: "ink", notes: [] },
  ].filter(Boolean).map((item) => ({ ...item, y: y(item.value) })).sort((a, b) => a.y - b.y);
  for (let index = 1; index < ends.length; index += 1) {
    ends[index].y = Math.max(ends[index].y, ends[index - 1].y + 26 + 13 * ends[index - 1].notes.length);
  }
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="svg-chart" role="img" aria-label={elasticityLabel(path)}>
      <YAxis scale={y} ticks={y.ticks(4)} format={(tick) => fmtNum(tick, 1)} left={m.left} right={right} />
      <line className="stroke-axis" x1={m.left} x2={right} y1={y(0)} y2={y(0)} />
      {path.rv && <path d={draw("rv")} className="stroke-ink" fill="none" />}
      <path d={draw("ovx")} className="stroke-implied line-2" fill="none" />
      {marks.map((mark) => (
        <g key={mark.key}>
          <circle cx={mark.cx} cy={y(mark.value)} r={3} className="fill-implied" />
          {mark.inPlot && <text className="axis-note halo" x={mark.lx} y={y(mark.value) + mark.dy} textAnchor="middle">{mark.label}</text>}
        </g>
      ))}
      {ends.map((item) => (
        <g key={item.key}>
          <text className={`end-label label-${item.tone}`} x={right + 8} y={item.y} dy="0.32em">{item.name}</text>
          <text className="end-value" x={right + 8} y={item.y + 13} dy="0.32em">{fmtNum(item.value, 2)}</text>
          {item.notes.map((note, index) => (
            <text key={note} className="axis-note" x={right + 8} y={item.y + 26 + 13 * index} dy="0.32em">{note}</text>
          ))}
        </g>
      ))}
      <XAxis scale={x} ticks={x.ticks(width < 520 ? 3 : 6)} format={(tick) => String(new Date(tick).getUTCFullYear())} y={height - m.bottom} />
    </svg>
  );
}

/** The measured elasticity chart (see ElasticityPathSvg). */
export function ElasticityPath({ path }) {
  return <Frame height={ELASTICITY_H} draw={(width) => <ElasticityPathSvg path={path} width={width} />} />;
}
