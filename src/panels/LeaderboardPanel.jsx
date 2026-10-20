import { useMemo } from "react";
import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import Panel from "../components/Panel";
import { ALPHA, leaderboardAssertion, spaAssertion } from "../lib/assertions";
import { DASH, fmtInt, fmtNum, fmtP, fmtPValue, fmtSpan, num, withCentralTime } from "../lib/format";
import { asArray, asObject, asText, getLab, getVol, hasMcsResults, leaderboardRows, mcsLevel, shortLabel } from "../lib/research";
import { ResearchUnavailable } from "./common";

const INPUT_TEXT = (info) => {
  const parts = [];
  if (info.has("implied")) parts.push("OVX");
  if (info.has("range")) parts.push("range");
  return parts.length ? parts.join(" + ") : "returns only";
};

function GridDots({ grids }) {
  const count = grids.filter(Boolean).length;
  return (
    <span className="grid-dots" aria-label={`in the set on ${count} of ${grids.length} sampling grids`}>
      {grids.map((inSet, index) => <i key={index} className={inSet ? "dot is-on" : "dot"} aria-hidden="true" />)}
      <span className="grid-count num" aria-hidden="true">{count}/{grids.length}</span>
    </span>
  );
}

function QlikeBar({ value, max }) {
  if (value == null) return DASH;
  const width = max > 0 ? Math.max(2, (value / max) * 100) : 0;
  return (
    <span className="inline-bar">
      <span className="num">{fmtNum(value, 4)}</span>
      <span className="bar-track" aria-hidden="true"><span className="bar-fill" style={{ width: `${width}%` }} /></span>
    </span>
  );
}

const NOT_ELIGIBLE = <span className="muted">not eligible</span>;
const isPinned = (row) => row.timingControl;

/**
 * MODL: every model on the same out-of-sample days, ranked by QLIKE, with MCS membership. A timing
 * control (same-day OVX, printed after the decision) is listed below the ranking, outside the MCS.
 */
export default function LeaderboardPanel({ data, research, researchStatus }) {
  const lab = getLab(research);
  const rows = useMemo(() => leaderboardRows(lab), [lab]);
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
  const maxQlike = Math.max(0, ...rows.map((row) => row.qlike ?? 0));
  const utilityMax = num(board?.realized_utility_max_pct);
  const record = rows.find((row) => row.role === "record");
  const lags = num(asArray(board?.models).find((model) => model?.dm_qlike_vs_har_iv?.lags != null)?.dm_qlike_vs_har_iv?.lags);
  const recordName = record ? shortLabel(record.label) : "the model of record";

  if (!rows.length) {
    return (
      <Panel id="modl" code="MODL" name="Model leaderboard" title={null}
        fallbackTitle="Model leaderboard" source={{ artifact: "research.json → vol_lab.leaderboard" }}>
        <ResearchUnavailable status={researchStatus} what="the model comparison" />
      </Panel>
    );
  }

  const spaText = spaAssertion(spa, { recordName, mcsLevel: level });
  const selectionP = num(selection?.dm_vs_har_iv?.p_value);
  const liveNoiseMean = num(liveNoise?.dir_acc_pct_mean);
  const footnote = (
    <ul className="footnotes">
      {controls.length > 0 && (
        <li>
          {controls.map((row) => shortLabel(row.label)).join(", ")}: a timing control, listed below the ranking and left out of
          every Model Confidence Set and every count above.{" "}
          {asText(board?.mcs_note)
            ? withCentralTime(board.mcs_note)
            : "It reads the test day's OVX close, published after the forecast is made, so it is not a feasible forecast; its losses and DM t are shown for comparison."}
        </li>
      )}
      {spaText && (
        <li>
          {spaText}
          {asText(spa?.note) ? ` The test ${spa.note}.` : ""}
        </li>
      )}
      {selection && num(selection.qlike) != null && (
        <li>
          Switching each day to the model with the best trailing {fmtInt(lab?.config?.selection_window_weeks)}-week QLIKE
          scores {fmtNum(selection.qlike, 4)} vs {fmtNum(selection.qlike_har_iv, 4)} for {recordName}
          {selectionP != null ? ` (DM ${fmtP(selectionP)})` : ""}{selectionP != null && selectionP >= ALPHA ? ": no gain from model picking." : "."}
        </li>
      )}
      {noise && num(noise.har_direction_acc_pct_mean) != null && (
        <li>
          Direction accuracy is not evidence of skill here: on simulated i.i.d. returns with nothing to forecast,
          the same up/down rule scores {fmtNum(noise.har_direction_acc_pct_mean, 1)}% on average (benchmark lab, {fmtInt(noise.sims)} simulations
          {liveNoiseMean != null ? `; the production validation's own ${fmtInt(liveNoise.sims)}-simulation run gives ${fmtNum(liveNoiseMean, 1)}%` : ""};
          theoretical floor {fmtNum(noise.theoretical_floor_pct, 1)}%), so it is left out of this table.
        </li>
      )}
      <li>
        DM t: Diebold-Mariano statistic of the every-day QLIKE difference vs {recordName}; positive means lower loss than it.
        Brackets: range across the 5-session sampling grids (each grid scores every fifth session, starting on a different day).
      </li>
    </ul>
  );

  return (
    <Panel
      id="modl"
      code="MODL"
      name="Model leaderboard"
      title={leaderboardAssertion(rows, { level, available: mcsAvailable })}
      fallbackTitle="Models ranked by out-of-sample QLIKE"
      meta={[
        controls.length ? `${fmtInt(eligibleCount)} models + ${fmtInt(controls.length)} timing control` : `${fmtInt(rows.length)} models`,
        board?.n_days != null ? `n ${fmtInt(board.n_days)} days` : null,
        fmtSpan(lab?.sample?.first, lab?.sample?.last),
        level != null ? `MCS ${level}%, ${fmtInt(mcs?.reps)} bootstrap reps` : null,
        lags != null ? `HAC ${lags} lags` : null,
      ]}
      caption={`QLIKE on every out-of-sample day (overlapping 5-session targets). The Model Confidence Set keeps each eligible model not significantly worse than the best eligible one${level != null ? ` at ${level}%` : ""}${mcsAvailable ? "" : "; this research.json carries no MCS results"}. Inputs: OVX = implied vol, range = intraday high/low, returns only = neither.`}
      source={{ artifact: "research.json → vol_lab.leaderboard" }}
      resetKey={research}
      table={() => (
        <DataTable
          caption="QLIKE by 5-session sampling grid (each grid scores one non-overlapping weekly schedule)"
          rowKey={(row) => row.key}
          dense
          columns={[
            { key: "label", label: "Model", rowHeader: true, render: (row) => shortLabel(row.label) },
            ...row0Grids(rows).map((_, index) => ({
              key: `g${index}`, label: `Grid ${index}`, align: "right",
              render: (row) => fmtNum(row.qlikeByGrid[index], 4),
            })),
          ]}
          rows={rows}
        />
      )}
    >
      <DataTable
        caption="Model leaderboard: out-of-sample QLIKE, Diebold-Mariano and Model Confidence Set"
        captionHidden
        rowKey={(row) => row.key}
        rowClass={(row) => [row.role ? `is-${row.role}` : "", row.inMcs ? "is-in-set" : "", row.timingControl ? "is-timing-control" : ""]
          .join(" ").trim() || undefined}
        initialSort={{ key: "qlike", direction: "asc" }}
        pinLast={isPinned}
        columns={[
          {
            key: "label", label: "Model", rowHeader: true, sortable: true,
            render: (row) => (
              <span className="model-cell" title={row.description ?? undefined}>
                <span className="model-name">{shortLabel(row.label)}</span>
                {row.role === "record" && <Chip tone="record">record</Chip>}
                {row.role === "challenger" && <Chip tone="challenger">challenger</Chip>}
                {row.timingControl && <Chip tone="warn" title="Uses OVX printed after the decision; not eligible for the MCS">timing control</Chip>}
              </span>
            ),
          },
          {
            key: "qlike", label: "QLIKE", title: "Out-of-sample QLIKE loss, lower is better", align: "left", sortable: true,
            render: (row) => <QlikeBar value={row.qlike} max={maxQlike} />,
          },
          {
            key: "dmStat", label: "DM t", title: `Diebold-Mariano t vs ${recordName} (+ = lower loss)`, align: "right", sortable: true, firstDirection: "desc",
            render: (row) => row.role === "record" ? <span className="muted">ref.</span> : (
              <span className="stat-cell">
                <span className="num">{fmtNum(row.dmStat, 2)}</span>
                {row.dmMin != null && <span className="stat-range num">[{fmtNum(row.dmMin, 1)}, {fmtNum(row.dmMax, 1)}]</span>}
              </span>
            ),
          },
          {
            key: "mcsP", label: "MCS p", title: "Model Confidence Set p-value", align: "right", sortable: true, firstDirection: "desc",
            render: (row) => (row.mcsEligible ? <span className="num">{fmtPValue(row.mcsP)}</span> : NOT_ELIGIBLE),
          },
          {
            key: "gridsIn", label: "In MCS", title: "In the Model Confidence Set on k of the 5-session sampling grids", sortable: true, firstDirection: "desc",
            render: (row) => (!row.mcsEligible ? NOT_ELIGIBLE : row.grids.length ? <GridDots grids={row.grids} /> : DASH),
          },
          {
            key: "utility", label: "Utility %", title: `Realized utility of a vol-targeting investor (Bollerslev et al. 2018)${utilityMax != null ? `; perfect foresight ${fmtNum(utilityMax, 2)}%` : ""}`,
            align: "right", sortable: true, firstDirection: "desc", render: (row) => <span className="num">{fmtNum(row.utility, 2)}</span>,
          },
          {
            key: "mzSlope", label: "MZ slope", title: "Mincer-Zarnowitz slope on every out-of-sample day (1 = unbiased)", align: "right", sortable: true,
            render: (row) => <span className="num" title={row.mzSe != null ? `SE ${fmtNum(row.mzSe, 2)}` : undefined}>{fmtNum(row.mzSlope, 2)}</span>,
          },
          { key: "inputs", label: "Inputs", sortable: true, sortValue: (row) => INPUT_TEXT(row.info), render: (row) => INPUT_TEXT(row.info), className: "muted-cell" },
          { key: "family", label: "Family", sortable: true, className: "muted-cell" },
        ]}
        rows={rows}
        footnote={footnote}
      />
    </Panel>
  );
}

/** The sampling grids present in the payload (from the first row with per-grid QLIKE). */
const row0Grids = (rows) => rows.find((row) => row.qlikeByGrid.length)?.qlikeByGrid ?? [];
