// The whole predictive distribution (ROADMAP 2.4): CALB's PIT histogram, one row per model, and the tables of CALB's
// table view and MODL's distribution view with its readings. Plain SVG sized to the container, sharing the frame of
// svgCharts.jsx and the row titles of stabilityCharts.jsx; colours come from CSS classes. One lazy chunk, with
// lib/distributionScores.js: every row and sentence comes from it or from lib/distribution.js.
import { useEffect, useState } from "react";
import { scaleLinear } from "d3-scale";
import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import keepHyphenated from "../components/keepHyphenated";
import { binLabel } from "../lib/distribution";
import {
  DIST_SCORES, berkowitzTableRows, challengerReading, distributionFootnote, distributionScoreRows, pitTableRows, rerunFootnote,
  scoreReading,
} from "../lib/distributionScores";
import { DASH, fmtNum, fmtPValue, fmtPct, fmtSigned } from "../lib/format";
import { fontsReady } from "../theme/chartTheme";
import { Frame } from "./svgCharts";
import { fitTitleName } from "./stabilityCharts";

// ------------------------------------------------------------------------------------------ PIT rows

const ROW_TITLE = 16;    // the row's title above its bars
const ROW_PLOT = 26;     // the bars, from zero
const ROW_GAP = 4;
const ROW_H = ROW_TITLE + ROW_PLOT + ROW_GAP;
const AXIS_H = 22;       // the PIT's tick labels under the last row
const GUTTER = 36;       // the share labels left of the first row
const RIGHT = 4;

/** The figure's height for `count` rows (46 px a row and 22 px of axis: 344 px for seven). */
export const pitHeight = (count) => count * ROW_H + AXIS_H;

/** The shared share scale's top: every share and every chance band's top, up to the next even percent. */
export const pitScaleMax = (rows) => {
  const tops = rows.flatMap((row) => row.shares.map((share, i) => Math.max(share, row.centers[i] + row.bands[i])));
  return Math.max(2, Math.ceil(Math.max(...tops) / 2) * 2);
};

/**
 * The drawing of PitRows at a given width (exported so it can be rendered without a layout). For each model the
 * share of days between consecutive predicted deciles as ten bars from zero, on one scale shared by every row;
 * behind each bar, as wide as its bin, the range its share moves by chance under a correct forecast (the model's
 * simulated mean ± its band); a dashed line at one bin's share of the days. No bin is marked as outside its range:
 * the shares are descriptive.
 *
 * @param {{rows: Array, width: number}} props  rows from lib/distribution.pitRows
 */
export function PitSvg({ rows, width }) {
  const height = pitHeight(rows.length);
  const bins = rows[0]?.shares.length ?? 0;
  const even = bins ? 100 / bins : 0;
  const x = scaleLinear().domain([0, 1]).range([GUTTER, width - RIGHT]);
  const max = pitScaleMax(rows);
  const binW = bins ? (x(1) - x(0)) / bins : 0;
  const barW = Math.max(2, binW * 0.56);
  const ticks = x(1) - x(0) < 200 ? [0, 0.5, 1] : [0, 0.2, 0.4, 0.6, 0.8, 1];
  const range = (values) => `${fmtNum(Math.min(...values), 1)}% to ${fmtNum(Math.max(...values), 1)}%`;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="svg-chart pit-chart" role="img"
      aria-label={`PIT histogram by model: the share of days in each tenth of the predictive distribution, ${fmtNum(even, 0)}% `
        + `each under a correct forecast; ${rows.map((row) => `${row.label}${row.tag ? ` (${row.tag})` : ""}, ${range(row.shares)}`).join("; ")}`}>
      {rows.map((row, index) => {
        const top = index * ROW_H + ROW_TITLE;
        const y = scaleLinear().domain([0, max]).range([top + ROW_PLOT, top]);
        const suffix = row.tag ? ` · ${row.tag}` : "";
        return (
          <g key={row.key} className="pit-row">
            <text className={`multiple-title label-${row.tone === "neutral" ? "ink" : row.tone}`} x={GUTTER} y={top - 5}>
              <title>{`${row.label}${suffix}`}</title>
              {fitTitleName(row.label, suffix, width - GUTTER - RIGHT)}
              {row.tag && <tspan className="pit-tag">{suffix}</tspan>}
            </text>
            {row.shares.map((share, bin) => {
              const left = x(bin / bins);
              const lo = Math.max(0, row.centers[bin] - row.bands[bin]);
              const hi = row.centers[bin] + row.bands[bin];
              return (
                <g key={bin}>
                  <rect className="pit-chance" x={left + 1} width={Math.max(1, binW - 2)} y={y(hi)} height={Math.max(1, y(lo) - y(hi))} />
                  <rect className={`pit-bar fill-${row.tone}`} x={left + (binW - barW) / 2} width={barW} y={y(share)} height={Math.max(0, y(0) - y(share))}>
                    <title>{`${row.label}, PIT ${binLabel(bin, bins)}: ${fmtNum(share, 1)}% of days (by chance ${fmtNum(lo, 1)}% to ${fmtNum(hi, 1)}%)`}</title>
                  </rect>
                </g>
              );
            })}
            <line className="stroke-target" x1={GUTTER} x2={width - RIGHT} y1={y(even)} y2={y(even)} />
            <line className="stroke-axis" x1={GUTTER} x2={width - RIGHT} y1={y(0)} y2={y(0)} />
            {index === 0 && (
              <text className="axis-label" x={GUTTER - 6} y={y(even)} dy="0.32em" textAnchor="end">{`${fmtNum(even, 0)}%`}</text>
            )}
          </g>
        );
      })}
      <g className="axis axis-x">
        {ticks.map((tick) => (
          <text key={tick} className="axis-label" x={x(tick)} y={height - 6}
            textAnchor={tick === 0 ? "start" : tick === 1 ? "end" : "middle"}>{fmtNum(tick, 1)}</text>
        ))}
        <text className="axis-note" x={GUTTER - 6} y={height - 6} textAnchor="end">PIT</text>
      </g>
    </svg>
  );
}

/** PitRows, sized to its container; drawn again once the title font has loaded (see stabilityCharts' titleWidth). */
export function PitRows({ rows }) {
  const [, setFontsLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    fontsReady().then(() => { if (live) setFontsLoaded(true); });
    return () => { live = false; };
  }, []);
  return <Frame height={pitHeight(rows.length)} draw={(width) => <PitSvg rows={rows} width={width} />} />;
}

// ------------------------------------------------------------------------------------------ tables

const p3 = (value) => fmtPValue(value, { digits: 3 });
const rowClass = (row) => (row.role ? `is-${row.role}` : undefined);
const nameText = (row) => (row.tag ? `${row.label} (${row.tag})` : row.role ? `${row.label} (${row.role})` : row.label);

/** A model's name with its role chip, or its tag on a muted line. */
function NameCell({ row }) {
  return (
    <span className="model-cell">
      <span className="model-name">{keepHyphenated(row.label)}</span>
      {row.role === "record" && <Chip tone="record">record</Chip>}
      {row.role === "challenger" && <Chip tone="challenger">challenger</Chip>}
      {row.tag && <span className="cell-note">{row.tag}</span>}
    </span>
  );
}

// leader-model: in MODL a chip or tag may wrap under the name, as in the leaderboard.
const nameColumn = { key: "label", label: "Model", rowHeader: true, className: "leader-model", render: (row) => <NameCell row={row} />, csv: nameText };
// CALB's tables name a model as its MZ table does: the name, and a tag on a muted line.
const plainName = {
  key: "label", label: "Model", rowHeader: true, className: "dist-wrap",
  render: (row) => (row.chance ? <span className="muted">{keepHyphenated(row.label)}</span>
    : <>{keepHyphenated(row.label)}{row.tag && <span className="cell-note">{row.tag}</span>}</>),
  csv: (row) => (row.tag ? `${row.label} (${row.tag})` : row.label),
};
const srName = (visible, hidden) => <>{visible}<span className="sr-only">{` (${hidden})`}</span></>;

/**
 * CALB's table view of the distributions: each model's share of days in every tenth of its predictive distribution
 * with the range each share moves by chance, and Berkowitz's test grid by grid with its reference, the Bonferroni p,
 * the context the payload adds and the ledger's q.
 */
export function PitTables({ rows, ledger, sample }) {
  const pitRows = pitTableRows(rows);
  const berkowitzRows = berkowitzTableRows(rows, ledger);
  const bins = pitRows[0]?.bins.length ?? 0;
  const grids = Math.max(0, ...berkowitzRows.map((row) => row.byGrid.length));
  const context = (row) => [
    row.mcSe != null ? `Monte Carlo se ${fmtNum(row.mcSe, 3)}` : null,
    row.pChi2 != null ? `χ²(3) p ${p3(row.pChi2)}` : null,
    row.randomized != null ? `randomized-tail p ${p3(row.randomized)}` : null,
  ].filter(Boolean).join("; ");
  return (
    <>
      {pitRows.length > 0 && (
        <DataTable
          caption={`Share of days between consecutive predicted deciles (%), and the range each share moves by chance under a correct forecast, ${sample}`}
          rowKey={(row) => row.key} rowClass={(row) => (row.chance ? "is-chance" : undefined)} dense exportName="calb-pit"
          columns={[
            plainName,
            ...Array.from({ length: bins }, (_, index) => ({
              key: `bin${index}`, label: binLabel(index, bins), csvLabel: `PIT ${binLabel(index, bins)} (% of days)`, align: "right",
              render: (row) => {
                const bin = row.bins[index];
                return row.chance ? <span className="num muted">{`${fmtNum(bin.lo, 1)}–${fmtNum(bin.hi, 1)}`}</span> : <span className="num">{fmtNum(bin.share, 1)}</span>;
              },
              csv: (row) => (row.chance ? `${fmtNum(row.bins[index].lo, 1)}–${fmtNum(row.bins[index].hi, 1)}` : row.bins[index].share),
            })),
            { key: "coverage", label: "Inside q10–q90", csvLabel: "Inside the 10% to 90% quantiles (%)", align: "right",
              render: (row) => (row.chance ? "" : fmtPct(row.coverage)), csv: (row) => (row.chance ? null : row.coverage) },
          ]}
          rows={pitRows}
        />
      )}
      <DataTable
        caption={`Berkowitz's test on each sampling grid's non-overlapping weeks, the Bonferroni p over the grids and the ledger's q, ${sample}`}
        rowKey={(row) => row.key} dense exportName="calb-berkowitz"
        columns={[
          plainName,
          { key: "reference", label: "Reference", className: "dist-wrap" },
          ...Array.from({ length: grids }, (_, index) => ({
            key: `grid${index}`, label: `Grid ${index}`, csvLabel: `Grid ${index} (LR; p)`, title: `Berkowitz LR and p on grid ${index}'s weeks`,
            align: "right", hideBelow: 640,
            render: (row) => {
              const grid = row.byGrid[index];
              return grid ? <><span className="num">{p3(grid.p)}</span><span className="cell-note num">LR {fmtNum(grid.lr, 2)}</span></> : DASH;
            },
            csv: (row) => (row.byGrid[index] ? `${row.byGrid[index].lr}; ${row.byGrid[index].p}` : null),
          })),
          { key: "p", label: "p", csvLabel: "p (Bonferroni over the grids)", title: "Bonferroni over the grids", align: "right", render: (row) => p3(row.p) },
          { key: "q", label: "q", csvLabel: "q (ledger)", title: "The ledger's Benjamini-Hochberg q", align: "right", render: (row) => p3(row.q) },
          { key: "context", label: "Context", hideBelow: 640, className: "muted-cell dist-wrap", render: (row) => context(row) || DASH, csv: context },
        ]}
        rows={berkowitzRows}
      />
    </>
  );
}

/**
 * MODL's distribution view: each scored model's CRPS and pinball losses in vol points, each with the batch-means t
 * and p against the model of record and the periods it scored lower in, and Berkowitz's p; under it, each score's
 * reading at the strength its test supports, the challenger's, the view's footnote and the robustness rerun. No
 * QLIKE: these scores use the band's days, not every out-of-sample day.
 */
export function DistributionScores({ lab, ledger, caption, recordName }) {
  const rows = distributionScoreRows(lab);
  const notes = [...DIST_SCORES.map((score) => scoreReading(lab, ledger, score)), challengerReading(lab, ledger),
    distributionFootnote(lab), rerunFootnote(lab)].filter(Boolean);
  const groups = rows.map((row) => row.tests.crps_grid?.groups).find((value) => value != null) ?? null;
  const ref = <span className="muted">ref.</span>;
  const test = (score, field, render) => (row) => (row.role === "record" ? ref : row.tests[score] ? render(row.tests[score][field]) : DASH);
  // A test's t, with its p under it where the p columns are hidden (below 900px).
  const tCell = (score) => (row) => (row.role === "record" ? ref : row.tests[score] ? (
    <span className="stat-cell">
      <span className="num">{fmtSigned(row.tests[score].t, 2)}</span>
      <span className="dist-p num">p {p3(row.tests[score].p)}</span>
    </span>
  ) : DASH);
  const testCsv = (score, field) => (row) => row.tests[score]?.[field] ?? null;
  const tTitle = (name) => `Batch-means t of ${recordName}'s ${name} minus the model's${groups != null ? ` over ${groups} periods` : ""} (+ = the model lower)`;
  const pinball = (score, name) => [
    { key: score, label: name, csvLabel: `${name} (vol pts)`, align: "right", sortable: true, hideBelow: 640,
      sortValue: (row) => (score === "pinball_10" ? row.pinball10 : row.pinball90),
      render: (row) => <span className="num">{fmtNum(score === "pinball_10" ? row.pinball10 : row.pinball90, 3)}</span> },
    { key: `${score}-t`, label: srName("t", name), csvLabel: `${name} t`, title: tTitle(`${name} loss`), align: "right", hideBelow: 640,
      render: tCell(score), csv: testCsv(score, "t") },
    { key: `${score}-p`, label: srName("p", name), csvLabel: `${name} p`, align: "right", hideBelow: 900,
      render: test(score, "p", (value) => <span className="num">{p3(value)}</span>), csv: testCsv(score, "p") },
  ];
  return (
    <DataTable
      caption={caption}
      rowKey={(row) => row.key} rowClass={rowClass} dense exportName="leaderboard-distribution"
      columns={[
        nameColumn,
        { key: "crps", label: "CRPS", csvLabel: "CRPS (vol pts)", title: "Continuous ranked probability score over the quantiles, vol points (lower is better)",
          align: "right", sortable: true, render: (row) => <span className="num">{fmtNum(row.crps, 3)}</span> },
        { key: "crps-t", label: srName("t", "CRPS"), csvLabel: "CRPS t", title: tTitle("CRPS"), align: "right",
          render: tCell("crps_grid"), csv: testCsv("crps_grid", "t") },
        { key: "crps-p", label: srName("p", "CRPS"), csvLabel: "CRPS p", align: "right", hideBelow: 900,
          render: test("crps_grid", "p", (value) => <span className="num">{p3(value)}</span>), csv: testCsv("crps_grid", "p") },
        { key: "crps-lower", label: "Lower in", csvLabel: "CRPS: periods the model scored lower", title: "Periods in which the model scored the lower CRPS",
          align: "right",
          render: (row) => (row.role === "record" ? ref : row.tests.crps_grid
            ? <span className="num">{`${row.tests.crps_grid.lower}/${row.tests.crps_grid.groups}`}</span> : DASH),
          csv: (row) => (row.tests.crps_grid ? `${row.tests.crps_grid.lower} of ${row.tests.crps_grid.groups}` : null) },
        ...pinball("pinball_10", "Pinball 10%"),
        ...pinball("pinball_90", "Pinball 90%"),
        { key: "berkowitzP", label: "Berkowitz p", title: "Berkowitz's test of the whole distribution, Bonferroni over the sampling grids",
          className: "dist-berkowitz", align: "right", sortable: true, firstDirection: "desc", render: (row) => <span className="num">{p3(row.berkowitzP)}</span> },
      ]}
      rows={rows}
      footnote={<ul className="footnotes dist-readings">{notes.map((text, index) => <li key={index}>{text}</li>)}</ul>}
    />
  );
}
