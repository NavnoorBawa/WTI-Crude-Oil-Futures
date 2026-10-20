// Small static figures drawn as plain SVG with d3-scale / d3-shape. They size to their container
// (useMeasure) so labels stay at 11px on a phone. Colours come from tokens.css through CSS classes
// (.fill-model, .stroke-implied, ...), so SVG never duplicates a hex value. One lazy chunk.
import { scaleLinear, scaleLog, scaleUtc } from "d3-scale";
import { area, line } from "d3-shape";
import useMeasure from "../lib/useMeasure";
import { fmtNum, fmtSigned, fmtSignedUsd } from "../lib/format";

const MARGIN = { top: 12, right: 16, bottom: 26, left: 44 };

/** A chart frame that measures its container and renders `draw(width)` once the width is known. */
function Frame({ height, className = "", draw }) {
  const [ref, width] = useMeasure();
  return (
    <div ref={ref} className={`svg-frame ${className}`} style={{ minHeight: height }}>
      {width > 0 && draw(width)}
    </div>
  );
}

function YAxis({ scale, ticks, format, left, right }) {
  return (
    <g className="axis axis-y">
      {ticks.map((tick) => (
        <g key={tick} transform={`translate(0,${scale(tick)})`}>
          <line className="grid-line" x1={left} x2={right} />
          <text className="axis-label" x={left - 6} dy="0.32em" textAnchor="end">{format(tick)}</text>
        </g>
      ))}
    </g>
  );
}

function XAxis({ scale, ticks, format, y, anchor = "middle" }) {
  return (
    <g className="axis axis-x" transform={`translate(0,${y})`}>
      {ticks.map((tick, index) => (
        <text key={index} className="axis-label" x={scale(tick)} dy="1.1em" textAnchor={anchor}>{format(tick)}</text>
      ))}
    </g>
  );
}

const extent = (values) => {
  const finite = values.filter((value) => Number.isFinite(value));
  return finite.length ? [Math.min(...finite), Math.max(...finite)] : [0, 1];
};

// 11px JetBrains Mono advances about 6.6px per character.
const MONO_CHAR_PX = 6.6;
/** Shortens a row label to fit `px`, with an ellipsis; the full text goes in a <title>. */
export const fitLabel = (text, px) => {
  const max = Math.max(4, Math.floor(px / MONO_CHAR_PX));
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`;
};

/** Pushes label y positions apart so none is closer than `gap` px to the previous one. */
export const spreadLabels = (items, gap = 12) => {
  const sorted = items.map((item, index) => ({ ...item, index })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < sorted.length; i += 1) sorted[i].y = Math.max(sorted[i].y, sorted[i - 1].y + gap);
  return sorted.sort((a, b) => a.index - b.index);
};

// --------------------------------------------------------------------------------------- CONE

/**
 * Realized-volatility cone: p5-p95 and p25-p75 bands and the median by window length, the latest
 * realized vol per window, OVX as a dashed level and the live forecast at the 5-session window.
 *
 * @param {{windows: {window:number,p5:number,p25:number,p50:number,p75:number,p95:number,latest:number}[],
 *   implied: number|null, forecast: number|null}} props
 */
export function ConeChart({ windows, implied = null, forecast = null }) {
  const height = 300;
  const rows = windows.filter((row) => [row.window, row.p5, row.p95].every(Number.isFinite));
  return (
    <Frame
      height={height}
      draw={(width) => {
        const m = { ...MARGIN, right: 68 };
        const x = scaleLog().domain(extent(rows.map((row) => row.window))).range([m.left + 8, width - m.right - 8]);
        const top = Math.max(...rows.map((row) => Math.max(row.p95, row.latest ?? 0)), implied ?? 0, forecast ?? 0);
        const y = scaleLinear().domain([0, top * 1.08]).nice().range([height - m.bottom, m.top]);
        const band = (lo, hi) => area().x((row) => x(row.window)).y0((row) => y(row[lo])).y1((row) => y(row[hi]))(rows);
        const path = (key) => line().x((row) => x(row.window)).y((row) => y(row[key])).defined((row) => Number.isFinite(row[key]))(rows);
        const last = rows[rows.length - 1];
        const first = rows[0];
        return (
          <svg width={width} height={height} className="svg-chart" role="img"
            aria-label={`Volatility cone over ${rows.length} windows from ${first?.window} to ${last?.window} sessions`}>
            <YAxis scale={y} ticks={y.ticks(5)} format={(t) => `${t}%`} left={m.left} right={width - m.right} />
            <path d={band("p5", "p95")} className="fill-cone-outer" />
            <path d={band("p25", "p75")} className="fill-cone-inner" />
            <path d={path("p50")} className="stroke-median" fill="none" />
            {implied != null && (
              <line className="stroke-implied dashed" x1={m.left} x2={x(last.window)} y1={y(implied)} y2={y(implied)} />
            )}
            <path d={path("latest")} className="stroke-truth" fill="none" />
            {rows.map((row) => Number.isFinite(row.latest) && (
              <circle key={row.window} cx={x(row.window)} cy={y(row.latest)} r={3.5} className="fill-truth" />
            ))}
            {forecast != null && first && (
              <g>
                <rect x={x(first.window) - 4.5} y={y(forecast) - 4.5} width={9} height={9}
                  transform={`rotate(45 ${x(first.window)} ${y(forecast)})`} className="fill-model" />
                <text className="label-model" x={x(first.window) + 9} y={y(forecast)} dy="0.32em">forecast {fmtNum(forecast, 1)}</text>
              </g>
            )}
            {last && spreadLabels([
              ...["p95", "p75", "p50", "p25", "p5"].map((key) => ({ key, text: key, y: y(last[key]), className: "axis-label" })),
              ...(implied != null ? [{ key: "ovx", text: `OVX ${fmtNum(implied, 1)}`, y: y(implied), className: "label-implied" }] : []),
            ]).map((item) => (
              <text key={item.key} className={item.className} x={x(last.window) + 8} y={item.y} dy="0.32em">{item.text}</text>
            ))}
            <XAxis scale={x} ticks={rows.map((row) => row.window)} format={(t) => `${t}d`} y={height - m.bottom} />
          </svg>
        );
      }}
    />
  );
}

// --------------------------------------------------------------------------- YEARLY (STAB)

/**
 * Small multiples of yearly QLIKE: one panel per model on a shared y scale. Every panel but the
 * first also draws the first model's yearly values as faint ticks, so each is read against it.
 *
 * @param {{years: {year:number, values: Record<string, number|null>}[],
 *   models: {key:string, label:string, tone:string}[]}} props
 */
export function YearlySmallMultiples({ years, models }) {
  const panelHeight = 170;
  return (
    <Frame
      height={panelHeight}
      className="multiples"
      draw={(width) => {
        const columns = width < 560 ? 2 : models.length;
        const rowsOfPanels = Math.ceil(models.length / columns);
        const gap = 16;
        const panelWidth = (width - gap * (columns - 1)) / columns;
        const height = rowsOfPanels * (panelHeight + 18) - 18;
        const all = years.flatMap((row) => models.map((model) => row.values[model.key]));
        const y = scaleLinear().domain([0, extent(all)[1] * 1.05]).nice().range([panelHeight - 22, 18]);
        const yearExtent = extent(years.map((row) => row.year));
        const reference = models[0];
        return (
          <svg width={width} height={height} className="svg-chart" role="img"
            aria-label={`Yearly QLIKE ${yearExtent[0]}–${yearExtent[1]} for ${models.map((m) => m.label).join(", ")}`}>
            {models.map((model, index) => {
              const col = index % columns;
              const row = Math.floor(index / columns);
              const left = col * (panelWidth + gap);
              const top = row * (panelHeight + 18);
              const axisWidth = col === 0 ? 34 : 6;
              const x = scaleLinear().domain(yearExtent).range([left + axisWidth + 4, left + panelWidth - 6]);
              return (
                <g key={model.key} transform={`translate(0,${top})`}>
                  <text className={`multiple-title label-${model.tone}`} x={left + axisWidth + 4} y={10}>{model.label}</text>
                  {y.ticks(4).map((tick) => (
                    <g key={tick}>
                      <line className="grid-line" x1={left + axisWidth} x2={left + panelWidth} y1={y(tick)} y2={y(tick)} />
                      {col === 0 && <text className="axis-label" x={left + axisWidth - 4} y={y(tick)} dy="0.32em" textAnchor="end">{fmtNum(tick, 1)}</text>}
                    </g>
                  ))}
                  {index > 0 && years.map((yr) => Number.isFinite(yr.values[reference.key]) && (
                    <line key={`r${yr.year}`} className="stroke-reference" x1={x(yr.year) - 3} x2={x(yr.year) + 3}
                      y1={y(yr.values[reference.key])} y2={y(yr.values[reference.key])} />
                  ))}
                  {years.map((yr) => Number.isFinite(yr.values[model.key]) && (
                    <circle key={yr.year} cx={x(yr.year)} cy={y(yr.values[model.key])} r={2.6} className={`fill-${model.tone}`}>
                      <title>{`${model.label} ${yr.year}: QLIKE ${fmtNum(yr.values[model.key], 3)}`}</title>
                    </circle>
                  ))}
                  <text className="axis-label" x={x(yearExtent[0])} y={panelHeight - 6} textAnchor="start">{yearExtent[0]}</text>
                  <text className="axis-label" x={x(yearExtent[1])} y={panelHeight - 6} textAnchor="end">{yearExtent[1]}</text>
                </g>
              );
            })}
          </svg>
        );
      }}
    />
  );
}

// ---------------------------------------------------------------------- DM BY GRID (STAB)

/**
 * Diebold-Mariano t of each model vs the model of record, full sample (large dot) and on each of
 * the weekday grids (small dots on the min-max range). Positive = lower loss than the record.
 *
 * @param {{rows: {key:string,label:string,dmStat:number|null,dmMin:number|null,dmMax:number|null,
 *   grid:number[], tone:string}[], critical?: number}} props
 */
export function DmGridStrip({ rows, critical = 1.96 }) {
  const rowHeight = 20;
  const height = rows.length * rowHeight + 34;
  return (
    <Frame
      height={height}
      draw={(width) => {
        const labelWidth = Math.min(220, width * 0.4);
        const values = rows.flatMap((row) => [row.dmStat, row.dmMin, row.dmMax, ...row.grid]);
        const [lo, hi] = extent([...values, -critical, critical]);
        const x = scaleLinear().domain([Math.floor(lo - 0.25), Math.ceil(hi + 0.25)]).range([labelWidth + 10, width - 12]);
        const bottom = rows.length * rowHeight + 8;
        return (
          <svg width={width} height={height} className="svg-chart" role="img"
            aria-label="Diebold-Mariano statistics versus the model of record by weekday grid">
            <rect x={x(-critical)} width={x(critical) - x(-critical)} y={0} height={bottom} className="fill-noise-zone" />
            {[-critical, 0, critical].map((value) => (
              <line key={value} className={value === 0 ? "stroke-axis" : "stroke-reference dashed"} x1={x(value)} x2={x(value)} y1={0} y2={bottom} />
            ))}
            {rows.map((row, index) => {
              const cy = index * rowHeight + rowHeight / 2 + 2;
              return (
                <g key={row.key}>
                  <text className={`row-label label-${row.tone}`} x={labelWidth} y={cy} dy="0.32em" textAnchor="end">
                    <title>{row.label}</title>{fitLabel(row.label, labelWidth)}
                  </text>
                  {row.dmMin != null && row.dmMax != null && (
                    <line className="stroke-range" x1={x(row.dmMin)} x2={x(row.dmMax)} y1={cy} y2={cy} />
                  )}
                  {row.grid.map((value, gridIndex) => Number.isFinite(value) && (
                    <circle key={gridIndex} cx={x(value)} cy={cy} r={2} className="fill-grid-dot" />
                  ))}
                  {row.dmStat != null && <circle cx={x(row.dmStat)} cy={cy} r={4} className={`fill-${row.tone}`}>
                    <title>{`${row.label}: t = ${fmtNum(row.dmStat, 2)}`}</title>
                  </circle>}
                </g>
              );
            })}
            <XAxis scale={x} ticks={x.ticks(6)} format={(t) => fmtSigned(t, 0)} y={bottom} />
            <text className="axis-note" x={x(x.domain()[0])} y={height - 2} textAnchor="start">← worse than record</text>
            <text className="axis-note" x={x(x.domain()[1])} y={height - 2} textAnchor="end">better →</text>
          </svg>
        );
      }}
    />
  );
}

// ---------------------------------------------------------------------- COVERAGE (CALB)

/**
 * Conformal interval coverage by year around the target level.
 * @param {{series: {key:string,label:string,tone:string,points:{year:number,coverage:number}[]}[],
 *   target: number}} props
 */
export function CoverageByYear({ series, target }) {
  const height = 220;
  return (
    <Frame
      height={height}
      draw={(width) => {
        const all = series.flatMap((s) => s.points.map((p) => p.coverage));
        const [lo, hi] = extent([...all, target]);
        const y = scaleLinear().domain([Math.floor(lo - 3), Math.ceil(hi + 3)]).nice().range([height - MARGIN.bottom, MARGIN.top]);
        const years = extent(series.flatMap((s) => s.points.map((p) => p.year)));
        const x = scaleLinear().domain(years).range([MARGIN.left + 8, width - MARGIN.right - 8]);
        const step = width < 520 ? 4 : 2;
        const yearTicks = [];
        for (let year = years[0]; year <= years[1]; year += step) yearTicks.push(year);
        return (
          <svg width={width} height={height} className="svg-chart" role="img"
            aria-label={`Interval coverage by year, ${years[0]}–${years[1]}, against a ${target}% target`}>
            <YAxis scale={y} ticks={y.ticks(5)} format={(t) => `${t}%`} left={MARGIN.left} right={width - MARGIN.right} />
            <line className="stroke-target" x1={MARGIN.left} x2={width - MARGIN.right} y1={y(target)} y2={y(target)} />
            <text className="label-target" x={MARGIN.left + 4} y={y(target) - 5} textAnchor="start">target {target}%</text>
            {series.map((s, index) => (
              <g key={s.key}>
                {index === 0 && (
                  <path className={`stroke-${s.tone} thin`} fill="none"
                    d={line().x((p) => x(p.year)).y((p) => y(p.coverage))(s.points)} />
                )}
                {s.points.map((p) => (
                  // The second series is an open ring larger than the first's dot, so equal values show both.
                  <circle key={p.year} cx={x(p.year)} cy={y(p.coverage)} r={index === 0 ? 3 : 5.5}
                    className={index === 0 ? `fill-${s.tone}` : `ring-${s.tone} is-open`}>
                    <title>{`${s.label} ${p.year}: ${fmtNum(p.coverage, 1)}%`}</title>
                  </circle>
                ))}
              </g>
            ))}
            <XAxis scale={x} ticks={yearTicks} format={String} y={height - MARGIN.bottom} />
          </svg>
        );
      }}
    />
  );
}

// ---------------------------------------------------------------------------- MZ (CALB)

/**
 * Mincer-Zarnowitz slope with a ±1.96 SE whisker per model; 1 = unbiased scaling.
 * @param {{rows: {key:string,label:string,slope:number,se:number|null,tone:string}[]}} props
 */
export function MzForest({ rows }) {
  const rowHeight = 18;
  const height = rows.length * rowHeight + 30;
  return (
    <Frame
      height={height}
      draw={(width) => {
        const labelWidth = Math.min(220, width * 0.4);
        const values = rows.flatMap((row) => (row.se != null ? [row.slope - 1.96 * row.se, row.slope + 1.96 * row.se] : [row.slope]));
        const [lo, hi] = extent([...values, 1]);
        const x = scaleLinear().domain([Math.min(0, lo), hi]).nice().range([labelWidth + 8, width - 12]);
        const bottom = rows.length * rowHeight + 6;
        return (
          <svg width={width} height={height} className="svg-chart" role="img" aria-label="Mincer-Zarnowitz slopes with 95% intervals">
            <line className="stroke-target" x1={x(1)} x2={x(1)} y1={0} y2={bottom} />
            {rows.map((row, index) => {
              const cy = index * rowHeight + rowHeight / 2;
              return (
                <g key={row.key}>
                  <text className={`row-label label-${row.tone}`} x={labelWidth} y={cy} dy="0.32em" textAnchor="end">
                    <title>{row.label}</title>{fitLabel(row.label, labelWidth)}
                  </text>
                  {row.se != null && (
                    <line className={`stroke-${row.tone} whisker`} x1={x(row.slope - 1.96 * row.se)} x2={x(row.slope + 1.96 * row.se)} y1={cy} y2={cy} />
                  )}
                  <circle cx={x(row.slope)} cy={cy} r={3.5} className={`fill-${row.tone}`}>
                    <title>{`${row.label}: slope ${fmtNum(row.slope, 2)}${row.se != null ? ` ± ${fmtNum(1.96 * row.se, 2)}` : ""}`}</title>
                  </circle>
                </g>
              );
            })}
            <XAxis scale={x} ticks={x.ticks(5)} format={(t) => fmtNum(t, 1)} y={bottom} />
            <text className="axis-note" x={x(1)} y={height - 1} textAnchor="middle">1 = unbiased</text>
          </svg>
        );
      }}
    />
  );
}

// ------------------------------------------------------------------------------------ VRP

/**
 * OVX vs the realized vol of the following week, log-log with the y = x diagonal. Points below the
 * diagonal are weeks where implied vol exceeded what was realized.
 * @param {{points: {date:string, ovx:number, actual:number}[]}} props
 */
export function VrpScatter({ points }) {
  return (
    <Frame
      height={300}
      draw={(width) => {
        const size = Math.min(width, 420);
        const height = size;
        const m = { top: 10, right: 12, bottom: 30, left: 44 };
        const values = points.flatMap((p) => [p.ovx, p.actual]);
        const [lo, hi] = extent(values);
        const domain = [Math.max(1, lo * 0.9), hi * 1.1];
        const x = scaleLog().domain(domain).range([m.left, size - m.right]);
        const y = scaleLog().domain(domain).range([height - m.bottom, m.top]);
        const ticks = [5, 10, 20, 50, 100, 200, 500].filter((t) => t >= domain[0] && t <= domain[1]);
        const latest = points[points.length - 1];
        return (
          <svg width={size} height={height} className="svg-chart" role="img"
            aria-label={`Scatter of ${points.length} weeks: OVX against the following week's realized volatility`}>
            {ticks.map((t) => (
              <g key={t}>
                <line className="grid-line" x1={m.left} x2={size - m.right} y1={y(t)} y2={y(t)} />
                <line className="grid-line" x1={x(t)} x2={x(t)} y1={m.top} y2={height - m.bottom} />
                <text className="axis-label" x={m.left - 6} y={y(t)} dy="0.32em" textAnchor="end">{t}</text>
                <text className="axis-label" x={x(t)} y={height - m.bottom} dy="1.1em" textAnchor="middle">{t}</text>
              </g>
            ))}
            <line className="stroke-axis dashed" x1={x(domain[0])} y1={y(domain[0])} x2={x(domain[1])} y2={y(domain[1])} />
            {points.map((p) => (
              <circle key={p.date} cx={x(p.ovx)} cy={y(p.actual)} r={2}
                className={p.actual < p.ovx ? "dot-premium" : "dot-loss"} />
            ))}
            {latest && <circle cx={x(latest.ovx)} cy={y(latest.actual)} r={5} className="ring-truth" />}
            <text className="axis-note" x={size - m.right} y={height - 2} textAnchor="end">OVX (implied, %) →</text>
            <text className="axis-note" x={m.left - 36} y={m.top + 2} transform={`rotate(-90 ${m.left - 36} ${m.top + 2})`} textAnchor="end">next-week realized (%) →</text>
            <text className="axis-note" x={m.left + 8} y={m.top + 12} textAnchor="start">realized &gt; implied</text>
            <text className="axis-note" x={size - m.right - 4} y={height - m.bottom - 8} textAnchor="end">implied &gt; realized</text>
          </svg>
        );
      }}
    />
  );
}

// ----------------------------------------------------------------------------------- LEAK

const fmtK = (value) => {
  if (!Number.isFinite(value)) return "";
  if (value === 0) return "$0";
  return `${value < 0 ? "−" : ""}$${Math.abs(value / 1000).toFixed(0)}k`;
};

/**
 * Cumulative P&L of the same 1-week direction model, leaked vs purged walk-forward.
 * @param {{leaked: {date:string,pnl:number}[], purged: {date:string,pnl:number}[]}} props
 */
export function EquityChart({ leaked, purged }) {
  const height = 240;
  return (
    <Frame
      height={height}
      draw={(width) => {
        const m = { ...MARGIN, left: 52, right: 104 };
        const parse = (d) => Date.parse(`${d}T00:00:00Z`);
        const all = [...leaked, ...purged];
        const x = scaleUtc().domain(extent(all.map((p) => parse(p.date)))).range([m.left, width - m.right]);
        const y = scaleLinear().domain(extent([0, ...all.map((p) => p.pnl)])).nice().range([height - m.bottom, m.top]);
        const path = line().x((p) => x(parse(p.date))).y((p) => y(p.pnl));
        const end = (series) => series[series.length - 1];
        const ticks = x.ticks(width < 520 ? 3 : 6);
        const labels = [
          { series: leaked, tone: "alt2", name: "Leaked" },
          { series: purged, tone: "truth", name: "Purged" },
        ].filter((item) => item.series.length);
        // Keep the two end labels at least 14px apart.
        const placed = labels.map((item) => ({ ...item, ly: y(end(item.series).pnl) })).sort((a, b) => a.ly - b.ly);
        for (let i = 1; i < placed.length; i += 1) placed[i].ly = Math.max(placed[i].ly, placed[i - 1].ly + 26);
        return (
          <svg width={width} height={height} className="svg-chart" role="img"
            aria-label={`Cumulative P&L, leaked ${fmtSignedUsd(end(leaked)?.pnl)} vs purged ${fmtSignedUsd(end(purged)?.pnl)}`}>
            <YAxis scale={y} ticks={y.ticks(5)} format={fmtK} left={m.left} right={width - m.right} />
            <line className="stroke-axis" x1={m.left} x2={width - m.right} y1={y(0)} y2={y(0)} />
            {leaked.length > 1 && <path d={path(leaked)} className="stroke-alt2 line-2" fill="none" />}
            {purged.length > 1 && <path d={path(purged)} className="stroke-truth line-2" fill="none" />}
            {placed.map((item) => (
              <g key={item.name}>
                <text className={`end-label label-${item.tone}`} x={width - m.right + 8} y={item.ly} dy="0.32em">{item.name}</text>
                <text className="end-value" x={width - m.right + 8} y={item.ly + 13} dy="0.32em">{fmtSignedUsd(end(item.series).pnl)}</text>
              </g>
            ))}
            <XAxis scale={x} ticks={ticks} format={(t) => String(new Date(t).getUTCFullYear())} y={height - m.bottom} />
          </svg>
        );
      }}
    />
  );
}

/**
 * Why the leak works: the chance that a training row's label has the same sign as the test label,
 * by how many daily returns the two labels share. The coin-flip baseline is 50%.
 * @param {{rows: {row:string, shared:number|null, correlation:number|null, agree:number, known:boolean}[]}} props
 */
export function SignAgreementLadder({ rows }) {
  const rowHeight = 24;
  const height = rows.length * rowHeight + 34;
  return (
    <Frame
      height={height}
      draw={(width) => {
        const labelWidth = Math.min(150, width * 0.4);
        const top = Math.max(...rows.map((row) => row.agree));
        const x = scaleLinear().domain([50, Math.ceil((top + 5) / 5) * 5]).range([labelWidth + 8, width - 48]);
        const bottom = rows.length * rowHeight + 4;
        return (
          <svg width={width} height={height} className="svg-chart" role="img"
            aria-label={`Sign agreement of training and test labels: ${rows.map((row) => `${row.row} ${fmtNum(row.agree, 1)}%`).join(", ")}`}>
            {x.ticks(4).map((tick) => (
              <line key={tick} className="grid-line" x1={x(tick)} x2={x(tick)} y1={0} y2={bottom} />
            ))}
            {rows.map((row, index) => {
              const cy = index * rowHeight + rowHeight / 2;
              const leaked = !row.known;
              return (
                <g key={row.row}>
                  <text className={`row-label${leaked ? "" : " label-neutral"}`} x={labelWidth} y={cy} dy="0.32em" textAnchor="end">
                    {row.row} · {row.shared ?? "?"} shared
                  </text>
                  <rect x={x(50)} y={cy - 6} height={12} width={Math.max(1, x(row.agree) - x(50))}
                    className={leaked ? "fill-alt2" : "fill-muted"} />
                  <text className="axis-label value-label" x={x(row.agree) + 6} y={cy} dy="0.32em">{fmtNum(row.agree, 1)}%</text>
                </g>
              );
            })}
            <line className="stroke-axis" x1={x(50)} x2={x(50)} y1={0} y2={bottom} />
            <XAxis scale={x} ticks={x.ticks(4)} format={(t) => `${t}%`} y={bottom} />
            <text className="axis-note" x={x(50)} y={height - 2} textAnchor="start">50% = coin flip</text>
          </svg>
        );
      }}
    />
  );
}

/**
 * Leaked vs purged direction accuracy for each experiment and model, as dumbbells, against the
 * coin flip and the retracted model's own (leaked) accuracy.
 * @param {{rows: {key:string,label:string,unpurged:number,purged:number,isHeadline:boolean}[],
 *   reference: {accuracy:number|null}|null}} props
 */
export function LeakDumbbell({ rows, reference }) {
  const rowHeight = 22;
  const height = rows.length * rowHeight + 58;
  return (
    <Frame
      height={height}
      draw={(width) => {
        const labelWidth = Math.min(170, width * 0.42);
        const values = rows.flatMap((row) => [row.unpurged, row.purged]);
        const [lo, hi] = extent([...values, 50, reference?.accuracy ?? 50]);
        const x = scaleLinear().domain([Math.floor((lo - 2) / 5) * 5, Math.ceil((hi + 2) / 5) * 5]).range([labelWidth + 10, width - 14]);
        const bottom = rows.length * rowHeight + 18;
        return (
          <svg width={width} height={height} className="svg-chart" role="img"
            aria-label="Direction accuracy without and with the purge, by experiment and model">
            {x.ticks(5).map((tick) => <line key={tick} className="grid-line" x1={x(tick)} x2={x(tick)} y1={14} y2={bottom} />)}
            <line className="stroke-axis" x1={x(50)} x2={x(50)} y1={14} y2={bottom} />
            <text className="axis-note" x={x(50)} y={height - 1} textAnchor="start">50% = coin flip</text>
            {reference?.accuracy != null && (
              <g>
                <line className="stroke-alt2 dashed" x1={x(reference.accuracy)} x2={x(reference.accuracy)} y1={14} y2={bottom} />
                <text className="label-alt2" x={x(reference.accuracy)} y={10}
                  textAnchor={x(reference.accuracy) > width - 60 ? "end" : "middle"}>retracted model {fmtNum(reference.accuracy, 1)}%</text>
              </g>
            )}
            {rows.map((row, index) => {
              const cy = 18 + index * rowHeight + rowHeight / 2;
              return (
                <g key={row.key}>
                  <text className={`row-label${row.isHeadline ? " is-strong" : ""}`} x={labelWidth} y={cy} dy="0.32em" textAnchor="end">
                    <title>{row.label}</title>{fitLabel(row.label, labelWidth)}
                  </text>
                  <line className="stroke-range thin-range" x1={x(row.purged)} x2={x(row.unpurged)} y1={cy} y2={cy} />
                  <circle cx={x(row.purged)} cy={cy} r={4} className="fill-truth"><title>{`${row.label}, purged: ${fmtNum(row.purged, 1)}%`}</title></circle>
                  <circle cx={x(row.unpurged)} cy={cy} r={4} className="fill-alt2"><title>{`${row.label}, unpurged: ${fmtNum(row.unpurged, 1)}%`}</title></circle>
                </g>
              );
            })}
            <XAxis scale={x} ticks={x.ticks(5)} format={(t) => `${t}%`} y={bottom} />
          </svg>
        );
      }}
    />
  );
}

/**
 * How the purge works: each training row at day d is labelled with the return over d+1..d+h. Rows
 * whose label window ends after the forecast time T overlap the test target and are dropped.
 * @param {{purgeRows: number, horizon: number, shown?: number}} props
 */
export function PurgeDiagram({ purgeRows, horizon, shown = 8 }) {
  const rowHeight = 15;
  const rows = Array.from({ length: shown }, (_, index) => index - shown); // offsets -shown..-1
  const height = (shown + 1) * rowHeight + 40;
  return (
    <Frame
      height={height}
      draw={(width) => {
        const m = { top: 8, right: 12, bottom: 30, left: 76 };
        const x = scaleLinear().domain([-shown - 0.5, horizon + 0.5]).range([m.left, width - m.right]);
        const cell = Math.max(2, x(1) - x(0));
        const tY = shown * rowHeight + m.top;
        return (
          <svg width={width} height={height} className="svg-chart" role="img"
            aria-label={`Purge diagram: the ${purgeRows} training rows whose ${horizon}-day labels end after the forecast time are dropped`}>
            <line className="stroke-model dashed" x1={x(0)} x2={x(0)} y1={m.top - 4} y2={tY + rowHeight} />
            {rows.map((offset, index) => {
              const cy = m.top + index * rowHeight + rowHeight / 2;
              const purged = -offset <= purgeRows;
              return (
                <g key={offset} className={purged ? "purge-row is-purged" : "purge-row"}>
                  <text className="row-label" x={m.left - 8} y={cy} dy="0.32em" textAnchor="end">
                    {purged ? "purged" : "train"} t{offset}
                  </text>
                  <circle cx={x(offset)} cy={cy} r={2.5} className={purged ? "fill-neg" : "fill-muted"} />
                  <rect x={x(offset) + 4} width={cell * horizon - 4} y={cy - 3} height={6}
                    className={purged ? "bar-purged" : "bar-train"} />
                </g>
              );
            })}
            <g>
              <text className="row-label label-model" x={m.left - 8} y={tY + rowHeight / 2} dy="0.32em" textAnchor="end">test t</text>
              <circle cx={x(0)} cy={tY + rowHeight / 2} r={3} className="fill-model" />
              <rect x={x(0) + 4} width={cell * horizon - 4} y={tY + rowHeight / 2 - 3} height={6} className="fill-model" />
            </g>
            <text className="axis-note" x={x(0)} y={height - 16} textAnchor="middle">forecast time t</text>
            <text className="axis-note" x={x(horizon)} y={height - 4} textAnchor="end">{horizon}-day label window</text>
          </svg>
        );
      }}
    />
  );
}
