import { useMemo } from "react";
import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import Panel from "../components/Panel";
import { ALPHA, leaderboardAssertion } from "../lib/assertions";
import { DASH, fmtInt, fmtNum, fmtP, fmtPValue, fmtSpan, num } from "../lib/format";
import { asArray, asObject, getLab, leaderboardRows, shortLabel } from "../lib/research";
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
    <span className="grid-dots" aria-label={`in the set on ${count} of ${grids.length} weekday grids`}>
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

/** MODL: every model on the same out-of-sample days, ranked by QLIKE, with MCS membership. */
export default function LeaderboardPanel({ research, researchStatus }) {
  const lab = getLab(research);
  const rows = useMemo(() => leaderboardRows(lab), [lab]);
  const board = asObject(lab?.leaderboard);
  const spa = asObject(board?.spa_qlike_vs_har_iv);
  const noise = asObject(lab?.noise_floor);
  const selection = asObject(lab?.oos_selection);
  const mcs = asObject(lab?.config?.mcs);
  const size = num(mcs?.size);
  const level = size != null ? Math.round((1 - size) * 100) : null;
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

  const spaP = num(spa?.p_consistent);
  const selectionP = num(selection?.dm_vs_har_iv?.p_value);
  const footnote = (
    <ul className="footnotes">
      {spaP != null && (
        <li>
          Hansen SPA test of {recordName} against {fmtInt(spa?.models?.length)} alternatives:{" "}
          {fmtP(spaP)} (consistent; lower {fmtPValue(spa?.p_lower)}, upper {fmtPValue(spa?.p_upper)})
          {spaP >= ALPHA ? `, so no alternative significantly beats it at ${ALPHA * 100}%` : ""}.
          {typeof spa?.note === "string" ? ` The test ${spa.note}.` : ""}
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
          the same up/down rule scores {fmtNum(noise.har_direction_acc_pct_mean, 1)}% on average ({fmtInt(noise.sims)} simulations;
          theoretical floor {fmtNum(noise.theoretical_floor_pct, 1)}%), so it is left out of this table.
        </li>
      )}
      <li>DM t: Diebold-Mariano statistic of the QLIKE difference vs {recordName}; positive means lower loss. Brackets: range across the weekday grids.</li>
    </ul>
  );

  return (
    <Panel
      id="modl"
      code="MODL"
      name="Model leaderboard"
      title={leaderboardAssertion(rows)}
      fallbackTitle="Models ranked by out-of-sample QLIKE"
      meta={[
        `${fmtInt(rows.length)} models`,
        board?.n_days != null ? `n ${fmtInt(board.n_days)} days` : null,
        fmtSpan(lab?.sample?.first, lab?.sample?.last),
        level != null ? `MCS ${level}%, ${fmtInt(mcs?.reps)} bootstrap reps` : null,
        lags != null ? `HAC ${lags} lags` : null,
      ]}
      caption={`QLIKE on every out-of-sample day (overlapping 5-session targets). The Model Confidence Set keeps each model not significantly worse than the best${level != null ? ` at ${level}%` : ""}. Inputs: OVX = implied vol, range = intraday high/low, returns only = neither.`}
      source={{ artifact: "research.json → vol_lab.leaderboard" }}
      resetKey={research}
      table={() => (
        <DataTable
          caption="QLIKE by weekday grid (each grid evaluates one non-overlapping weekly schedule)"
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
        rowClass={(row) => [row.role ? `is-${row.role}` : "", row.inMcs ? "is-in-set" : ""].join(" ").trim() || undefined}
        initialSort={{ key: "qlike", direction: "asc" }}
        columns={[
          {
            key: "label", label: "Model", rowHeader: true, sortable: true,
            render: (row) => (
              <span className="model-cell" title={row.description ?? undefined}>
                <span className="model-name">{shortLabel(row.label)}</span>
                {row.role === "record" && <Chip tone="record">record</Chip>}
                {row.role === "challenger" && <Chip tone="challenger">challenger</Chip>}
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
          { key: "mcsP", label: "MCS p", title: "Model Confidence Set p-value", align: "right", sortable: true, firstDirection: "desc", render: (row) => <span className="num">{fmtPValue(row.mcsP)}</span> },
          { key: "gridsIn", label: "In MCS", title: "In the Model Confidence Set on k of the weekday grids", sortable: true, firstDirection: "desc", render: (row) => <GridDots grids={row.grids} /> },
          {
            key: "utility", label: "Utility %", title: `Realized utility of a vol-targeting investor (Bollerslev et al. 2018)${utilityMax != null ? `; perfect foresight ${fmtNum(utilityMax, 2)}%` : ""}`,
            align: "right", sortable: true, firstDirection: "desc", render: (row) => <span className="num">{fmtNum(row.utility, 2)}</span>,
          },
          {
            key: "mzSlope", label: "MZ slope", title: "Mincer-Zarnowitz slope (1 = unbiased)", align: "right", sortable: true,
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

/** The weekday grids present in the payload (from the first row with per-grid QLIKE). */
const row0Grids = (rows) => rows.find((row) => row.qlikeByGrid.length)?.qlikeByGrid ?? [];
