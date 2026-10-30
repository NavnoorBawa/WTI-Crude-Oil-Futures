import { useMemo } from "react";
import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import Panel from "../components/Panel";
import keepHyphenated from "../components/keepHyphenated";
import { ALPHA, leaderboardAssertion, leaderboardDetail, spaAssertion } from "../lib/assertions";
import { mcsRobustness, qlikeScale } from "../lib/comparison";
import { DASH, NBSP, fmtInt, fmtNum, fmtP, fmtPValue, fmtSpan, num, withCentralTime } from "../lib/format";
import {
  asArray, asObject, asText, getLab, getVol, hasMcsResults, leaderboardRows, mcsLevel, modelLabels, researchLedger, shortLabel,
  withModelLabels,
} from "../lib/research";
import { ResearchUnavailable } from "./common";

const INPUT_TEXT = (info) => {
  const parts = [];
  if (info.has("implied")) parts.push("OVX");
  if (info.has("range")) parts.push("range");
  return parts.length ? parts.join(" + ") : "returns only";
};


/** A header holding a year range ("2008–16") keeps it on one line; the header may still wrap at spaces. */
const rangeLabel = (label) => (typeof label === "string" && label.includes("–") ? <span className="nowrap">{label}</span> : label);
/** k of n sampling grids as dots and a count; one image to assistive tech. */
export function GridDots({ grids }) {
  const count = grids.filter(Boolean).length;
  return (
    <span className="grid-dots" role="img" aria-label={`in the set on ${count} of ${grids.length} sampling grids`}>
      {grids.map((inSet, index) => <i key={index} className={inSet ? "dot is-on" : "dot"} aria-hidden="true" />)}
      <span className="grid-count num" aria-hidden="true">{count}/{grids.length}</span>
    </span>
  );
}

/**
 * A model's QLIKE as a number and a dot on a shared strip (no zero baseline, see qlikeScale). A
 * value beyond the strip's cap is drawn as "›" at the track's end; its number is still printed.
 */
function QlikeDot({ row, scale }) {
  if (row.qlike == null) return DASH;
  const tone = row.role === "record" ? "is-record" : row.role === "challenger" ? "is-challenger" : row.inMcs ? "is-member" : "";
  return (
    <span className="inline-bar">
      <span className="num">{fmtNum(row.qlike, 4)}</span>
      {scale && (
        <span className="bar-track" aria-hidden="true">
          {scale.offScale(row.qlike)
            ? <span className="bar-off" title="Beyond the strip's scale">›</span>
            : <span className={`bar-dot ${tone}`.trim()} style={{ left: `${(scale.position(row.qlike) * 100).toFixed(2)}%` }} />}
        </span>
      )}
    </span>
  );
}

/** A model's name with the chips the leaderboard marks it by. */
function ModelCell({ row }) {
  return (
    <span className="model-cell" title={row.description ?? undefined}>
      <span className="model-name">{keepHyphenated(shortLabel(row.label))}</span>
      {row.role === "record" && <Chip tone="record">record</Chip>}
      {row.role === "challenger" && <Chip tone="challenger">challenger</Chip>}
      {row.timingControl && <Chip tone="warn" title="Uses OVX printed after the decision; not eligible for the MCS">timing control</Chip>}
    </span>
  );
}

/** The model's name and its marker, as the CSV holds it. */
const modelText = (row) => {
  const name = shortLabel(row.label);
  const role = row.timingControl ? "timing control" : row.role;
  return role ? `${name} (${role})` : name;
};

const NOT_ELIGIBLE = <span className="muted not-eligible">not eligible</span>;
const isPinned = (row) => row.timingControl;
const rowClass = (row) => [row.role ? `is-${row.role}` : "", row.inMcs ? "is-in-set" : "", row.timingControl ? "is-timing-control" : ""]
  .join(" ").trim() || undefined;

/** The sampling grids present in the payload (from the first row with per-grid QLIKE). */
const row0Grids = (rows) => rows.find((row) => row.qlikeByGrid.length)?.qlikeByGrid ?? [];

/**
 * MODL's table view: QLIKE on each 5-session sampling grid, the timing control kept below the
 * ranking as in the leaderboard.
 */
export function GridTable({ rows }) {
  return (
    <DataTable
      caption="QLIKE by 5-session sampling grid (each grid scores one non-overlapping weekly schedule)"
      rowKey={(row) => row.key}
      rowClass={rowClass}
      pinLast={isPinned}
      dense
      exportName="leaderboard-by-grid"
      columns={[
        { key: "label", label: "Model", rowHeader: true, render: (row) => <ModelCell row={row} />, csv: modelText },
        ...row0Grids(rows).map((_, index) => ({
          key: `g${index}`, label: `Grid ${index}`, align: "right",
          render: (row) => fmtNum(row.qlikeByGrid[index], 4), csv: (row) => row.qlikeByGrid[index] ?? null,
        })),
      ]}
      rows={rows}
    />
  );
}

/** One membership cell: ● in, ○ out, with the word for screen readers. */
function SetCell({ inSet }) {
  return (
    <span className={`set-mark${inSet ? " is-in" : ""}`}>
      <span aria-hidden="true">{inSet ? "●" : "○"}</span>
      <span className="sr-only">{inSet ? "in" : "out"}</span>
    </span>
  );
}

/**
 * The Model Confidence Set's membership under every setting research.json reports (mcsRobustness):
 * one row per eligible model in leaderboard order, one column per setting.
 */
export function McsMatrix({ robustness, level }) {
  const set = `${level != null ? `${level}% ` : ""}Model Confidence Set`;
  return (
    <DataTable
      caption={`● in the ${set}, ○ out, under each setting; Grids: in the set on k of the sampling grids`}
      rowKey={(row) => row.key}
      rowClass={(row) => (row.role ? `is-${row.role}` : undefined)}
      dense
      exportName="mcs-membership"
      columns={[
        {
          key: "label", label: "Model", rowHeader: true, csv: (row) => (row.role ? `${row.label} (${row.role})` : row.label),
          render: (row) => (
            <span className="model-cell">
              <span className="model-name">{keepHyphenated(row.label)}</span>
              {row.role === "record" && <Chip tone="record">record</Chip>}
              {row.role === "challenger" && <Chip tone="challenger">challenger</Chip>}
            </span>
          ),
        },
        ...robustness.columns.map((column) => (column.kind === "grids" ? {
          key: column.key, label: rangeLabel(column.label), title: column.title, align: "center", className: "set-col",
          csvLabel: "Grids in set",
          csv: (row) => `${row.cells[column.key].in} of ${row.cells[column.key].of}`,
          render: (row) => (
            <span className="num">
              <span aria-hidden="true">{row.cells[column.key].in}/{row.cells[column.key].of}</span>
              <span className="sr-only">in on {row.cells[column.key].in} of {row.cells[column.key].of} grids</span>
            </span>
          ),
        } : {
          key: column.key, label: rangeLabel(column.label), csvLabel: column.label, title: column.title, align: "center",
          className: "set-col", csv: (row) => (row.cells[column.key] ? "in" : "out"),
          render: (row) => <SetCell inSet={row.cells[column.key]} />,
        })),
      ]}
      rows={robustness.rows}
    />
  );
}

/**
 * The leaderboard's columns. On a phone (640px and below) the model, QLIKE, DM t, MCS p and In set
 * columns stay, so the ranking and its tests read without sideways scrolling; the rest hide. The
 * CSV holds what each cell shows ("yes", "2 of 5", "not eligible"), never a sort key.
 */
export const leaderboardColumns = ({ recordName, scale, level, mcsAvailable, utilityText }) => [
  {
    key: "label", label: "Model", rowHeader: true, sortable: true, className: "leader-model",
    render: (row) => <ModelCell row={row} />, csv: modelText,
  },
  {
    key: "qlike", label: "QLIKE", title: "Out-of-sample QLIKE loss, lower is better", align: "left", sortable: true,
    className: "leader-qlike", render: (row) => <QlikeDot row={row} scale={scale} />,
  },
  {
    key: "dmStat", label: `DM${NBSP}t`, csvLabel: "DM t", title: `Diebold-Mariano t vs ${recordName} (+ = lower loss)`, align: "right", sortable: true, firstDirection: "desc",
    className: "leader-dm",
    render: (row) => row.role === "record" ? <span className="muted">ref.</span> : (
      <span className="stat-cell">
        <span className="num">{fmtNum(row.dmStat, 2)}</span>
        {row.dmMin != null && <span className="stat-range num">[{fmtNum(row.dmMin, 1)}, {fmtNum(row.dmMax, 1)}]</span>}
      </span>
    ),
  },
  {
    key: "mcsP", label: `MCS${NBSP}p`, csvLabel: "MCS p", title: "Model Confidence Set p-value", align: "right", sortable: true, firstDirection: "desc",
    render: (row) => (row.mcsEligible
      ? <span className={`num mcs-p${row.inMcs ? " is-member" : ""}`}>{fmtPValue(row.mcsP)}</span>
      : NOT_ELIGIBLE),
  },
  {
    key: "inMcs", label: "In set", title: `In the ${level != null ? `${level}% ` : ""}Model Confidence Set on every out-of-sample day`,
    sortable: true, firstDirection: "desc", csvLabel: "In set",
    sortValue: (row) => (row.mcsEligible && mcsAvailable ? Number(row.inMcs) : null),
    csv: (row) => (!row.mcsEligible ? "not eligible" : mcsAvailable ? (row.inMcs ? "yes" : "no") : null),
    render: (row) => (!row.mcsEligible ? <span className="muted">{DASH}</span> : !mcsAvailable ? DASH
      : row.inMcs ? <span className="in-set">yes</span> : <span className="muted">no</span>),
  },
  {
    key: "gridsIn", label: "Grids", title: "In the Model Confidence Set on k of the 5-session sampling grids", sortable: true,
    firstDirection: "desc", hideBelow: 640, csvLabel: "Grids in set",
    csv: (row) => (!row.mcsEligible ? "not eligible" : row.grids.length ? `${row.grids.filter(Boolean).length} of ${row.grids.length}` : null),
    render: (row) => (!row.mcsEligible ? <span className="muted">{DASH}</span> : row.grids.length ? <GridDots grids={row.grids} /> : DASH),
  },
  {
    key: "utility", label: "Utility %", title: utilityText,
    align: "right", sortable: true, firstDirection: "desc", hideBelow: 640, render: (row) => <span className="num">{fmtNum(row.utility, 2)}</span>,
  },
  {
    key: "mzSlope", label: "MZ slope", title: "Mincer-Zarnowitz slope on every out-of-sample day (1 = unbiased)", align: "right", sortable: true,
    hideBelow: 640, render: (row) => <span className="num" title={row.mzSe != null ? `SE ${fmtNum(row.mzSe, 2)}` : undefined}>{fmtNum(row.mzSlope, 2)}</span>,
  },
  {
    key: "inputs", label: "Inputs", sortable: true, hideBelow: 1000, sortValue: (row) => INPUT_TEXT(row.info),
    render: (row) => INPUT_TEXT(row.info), className: "muted-cell",
  },
  { key: "family", label: "Family", sortable: true, hideBelow: 1000, className: "muted-cell", render: (row) => keepHyphenated(row.family) },
];

/**
 * MODL: every model on the same out-of-sample days, ranked by QLIKE, with MCS membership and its
 * robustness to the settings. A timing control (same-day OVX, printed after the decision) is
 * listed below the ranking, outside the MCS.
 */
export default function LeaderboardPanel({ data, research, researchStatus }) {
  const lab = getLab(research);
  const rows = useMemo(() => leaderboardRows(lab), [lab]);
  const robustness = useMemo(() => mcsRobustness(lab), [lab]);
  const scale = useMemo(() => qlikeScale(rows.map((row) => row.qlike)), [rows]);
  const board = asObject(lab?.leaderboard);
  const spa = asObject(board?.spa_qlike_vs_har_iv);
  const noise = asObject(lab?.noise_floor);
  const liveNoise = asObject(getVol(data).validation?.direction_noise_floor);
  const selection = asObject(lab?.oos_selection);
  const mcs = asObject(lab?.config?.mcs);
  const level = mcsLevel(lab);
  const mcsAvailable = hasMcsResults(lab);
  const controls = rows.filter((row) => row.timingControl);
  const eligibleCount = rows.filter((row) => row.mcsEligible).length;
  const utilityMax = num(board?.realized_utility_max_pct);
  const record = rows.find((row) => row.role === "record");
  const lags = num(asArray(board?.models).find((model) => model?.dm_qlike_vs_har_iv?.lags != null)?.dm_qlike_vs_har_iv?.lags);
  const recordName = record ? shortLabel(record.label) : "the model of record";
  const labels = modelLabels(lab);

  if (!rows.length) {
    return (
      <Panel id="modl" code="MODL" name="Model leaderboard" title={null}
        fallbackTitle="Model leaderboard" source={{ artifact: "research.json → vol_lab.leaderboard" }}>
        <ResearchUnavailable status={researchStatus} what="the model comparison" />
      </Panel>
    );
  }

  const spaText = spaAssertion(spa, { recordName, mcsLevel: level, ledgerRow: researchLedger(research)?.byId.get("vol_spa_beats_record") });
  const selectionP = num(selection?.dm_vs_har_iv?.p_value);
  const liveNoiseMean = num(liveNoise?.dir_acc_pct_mean);
  const utilityText = `Realized utility of a volatility-targeting investor, % per year (Bollerslev et al. 2018)${utilityMax != null ? `; perfect foresight ${fmtNum(utilityMax, 2)}%` : ""}.`;
  const gridCount = row0Grids(rows).length || Math.max(0, ...rows.map((row) => row.grids.length));
  // The matrix earns its place only with a setting beyond the leaderboard's own two (full sample, grids).
  const showMatrix = mcsAvailable && robustness && robustness.rows.length > 0
    && robustness.columns.some((column) => column.key !== "full" && column.key !== "grids");
  const mseColumn = robustness?.columns.find((column) => column.key === "mse");
  const mseKept = mseColumn ? robustness.rows.filter((row) => row.cells.mse).length : null;

  // Visible under the ranking: what qualifies the record's comparison (the SPA test) and how to
  // read DM t. Everything else is method, in the notes.
  const footnote = (
    <ul className="footnotes">
      {spaText && (
        <li>
          {spaText}
          {asText(spa?.note) ? ` The test ${spa.note}.` : ""}
        </li>
      )}
      <li>
        DM t: Diebold-Mariano t of the every-day QLIKE difference vs {recordName}; positive means lower loss than {recordName}.
        <span className="dm-range-note"> Brackets give its range across the sampling grids.</span>
      </li>
    </ul>
  );

  const notes = [
    controls.length > 0
      ? `${controls.map((row) => shortLabel(row.label)).join(", ")}: a timing control, listed below the ranking and left out of every Model Confidence Set and every count above. ${asText(board?.mcs_note)
        ? withModelLabels(withCentralTime(board.mcs_note), labels)
        : "It reads the test day's OVX close, published after the forecast is made, so it is not a feasible forecast; its losses and DM t are shown for comparison."}`
      : null,
    gridCount
      ? `Sampling grids: the ${fmtInt(gridCount)} grids each score every fifth session, starting on a different day, so each is one non-overlapping weekly schedule. The leaderboard scores every out-of-sample day, whose 5-session targets overlap.`
      : null,
    showMatrix
      ? [
        "Other settings:",
        num(mcs?.block_daily) != null ? `the full-sample set uses stationary bootstrap blocks of ${fmtInt(mcs.block_daily)} days;` : null,
        num(mcs?.block_weekly) != null ? `each grid's set uses blocks of ${fmtInt(mcs.block_weekly)} weeks;` : null,
        "the Block columns repeat the full sample with other block lengths, and the year columns keep only those years.",
        robustness.record ? `In "${recordName}: in the set under ${fmtInt(robustness.record.inCount)} of ${fmtInt(robustness.settings)} settings", each sampling grid counts as one setting.` : null,
      ].filter(Boolean).join(" ")
      : null,
    showMatrix && mseColumn
      ? `MSE loss: squared error on variances has little power to separate models when the volatility proxy is noisy (Patton 2011); its set keeps ${mseKept === robustness.rows.length ? `all ${fmtInt(mseKept)} models` : `${fmtInt(mseKept)} of ${fmtInt(robustness.rows.length)} models`}.`
      : null,
    showMatrix && robustness.columns.some((column) => column.key === "proxy")
      ? "Range proxy: QLIKE scored against realized vol measured from the daily high-low range instead of close-to-close returns."
      : null,
    `Utility: ${utilityText}`,
    "Inputs: OVX = implied vol, range = intraday high/low, returns only = neither.",
    selection && num(selection.qlike) != null
      ? `Switching each day to the model with the best trailing ${fmtInt(lab?.config?.selection_window_weeks)}-week QLIKE scores ${fmtNum(selection.qlike, 4)} vs ${fmtNum(selection.qlike_har_iv, 4)} for ${recordName}${selectionP != null ? ` (DM ${fmtP(selectionP)})` : ""}${selectionP != null && selectionP >= ALPHA ? `: not a significant gain over always using ${recordName}.` : "."}`
      : null,
    noise && num(noise.har_direction_acc_pct_mean) != null
      ? `Direction accuracy is not evidence of skill here: on simulated i.i.d. returns with nothing to forecast, the same up/down rule scores ${fmtNum(noise.har_direction_acc_pct_mean, 1)}% on average (benchmark lab, ${fmtInt(noise.sims)} simulations${liveNoiseMean != null ? `; the production validation's own ${fmtInt(liveNoise.sims)}-simulation run gives ${fmtNum(liveNoiseMean, 1)}%` : ""}; theoretical floor ${fmtNum(noise.theoretical_floor_pct, 1)}%), so it is left out of this table.`
      : null,
  ].filter(Boolean);

  return (
    <Panel
      id="modl"
      code="MODL"
      name="Model leaderboard"
      title={leaderboardAssertion(rows, { level, available: mcsAvailable, robustness })}
      fallbackTitle="Models ranked by out-of-sample QLIKE"
      meta={[
        ...leaderboardDetail(rows, { available: mcsAvailable, robustness }),
        controls.length ? `${fmtInt(eligibleCount)} models + ${fmtInt(controls.length)} timing control` : `${fmtInt(rows.length)} models`,
        board?.n_days != null ? `n ${fmtInt(board.n_days)} days` : null,
        fmtSpan(lab?.sample?.first, lab?.sample?.last),
        level != null ? `MCS ${level}%, ${fmtInt(mcs?.reps)} bootstrap reps` : null,
        lags != null ? `HAC ${lags} lags` : null,
      ]}
      caption={`QLIKE on every out-of-sample day (overlapping 5-session targets). The Model Confidence Set keeps each eligible model not significantly worse than the best eligible one${level != null ? ` at ${level}%` : ""}${mcsAvailable ? "" : "; this research.json carries no MCS results"}.`}
      notes={notes}
      source={{ artifact: "research.json → vol_lab.leaderboard" }}
      resetKey={research}
      tableLabel="By grid"
      table={() => <GridTable rows={rows} />}
    >
      <div className="leaderboard">
        <DataTable
          caption="Model leaderboard: out-of-sample QLIKE, Diebold-Mariano and Model Confidence Set"
          captionHidden
          rowKey={(row) => row.key}
          rowId={(row) => `modl-${row.key}`}
          rowClass={rowClass}
          announce="models"
          initialSort={{ key: "qlike", direction: "asc" }}
          pinLast={isPinned}
          exportName="leaderboard"
          columns={leaderboardColumns({ recordName, scale, level, mcsAvailable, utilityText })}
          rows={rows}
          footnote={footnote}
        />
      </div>
      {showMatrix && (
        <div className="mcs-matrix">
          <h3 className="figure-title">MCS membership under other settings</h3>
          <McsMatrix robustness={robustness} level={level} />
        </div>
      )}
    </Panel>
  );
}
