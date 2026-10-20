import { useState } from "react";
import { getEquityChart, getLeakDumbbell, getPurgeDiagram, getSignAgreementLadder } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import Segmented from "../components/Segmented";
import { ALPHA, leakAnatomyAssertion, leakAssertion } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtP, fmtPValue, fmtPct, fmtSignedUsd, fmtSpan, num } from "../lib/format";
import { asObject, asText, getLab, leakAnatomy, postmortemWindow } from "../lib/research";
import { Facts, Legend, ResearchUnavailable } from "./common";

const WINDOWS = [
  { value: "five_year", label: "5Y", title: "Five-year walk-forward" },
  { value: "ten_year", label: "10Y", title: "Ten-year walk-forward" },
];

/**
 * research.json leak_anatomy: the same unpurged pipeline on data with nothing to forecast. The
 * analytic ladder shows why (a training label shares returns with the test label); the
 * experiments show the result (leaked accuracy on noise matches the retracted model's). The
 * experiments' Sharpe ratios are gross and the retracted model's is after costs, so every Sharpe
 * shown carries its basis.
 */
function LeakAnatomy({ anatomy }) {
  if (!anatomy || (!anatomy.ladder.length && !anatomy.experiments.length)) return null;
  const Ladder = getSignAgreementLadder();
  const Dumbbell = getLeakDumbbell();
  const headline = anatomy.experiments.find((row) => row.isHeadline);
  const basis = anatomy.sharpeBasis;
  const gross = (value) => (value != null ? `Sharpe ${fmtNum(value, 2)}, ${basis.experiments}` : null);
  const hasBaseRate = anatomy.experiments.some((row) => !row.symmetric && row.noSkill != null);
  return (
    <div className="subsection anatomy">
      <h3 className="study-title">{leakAnatomyAssertion(anatomy) ?? "Anatomy of the leak"}</h3>
      <Facts className="facts--row" items={[
        headline && { label: "Unpurged, on noise", value: fmtPct(headline.unpurged), note: gross(headline.unpurgedSharpe) },
        headline && { label: "Purged, on noise", value: fmtPct(headline.purged), note: gross(headline.purgedSharpe) },
        anatomy.reference && {
          label: "Retracted model", value: fmtPct(anatomy.reference.accuracy),
          note: anatomy.reference.sharpe != null ? `Sharpe ${fmtNum(anatomy.reference.sharpe, 2)} ${basis.reference}, n ${fmtInt(anatomy.reference.n)}` : null,
        },
        anatomy.signAgreement != null && { label: "Sign agreement, t−1", value: fmtPct(anatomy.signAgreement), note: "analytic, random walk" },
      ]} />
      <div className="split">
        {anatomy.ladder.length > 0 && (
          <div className="split-figure">
            <h4 className="figure-title">Why: shared returns make labels agree</h4>
            <LazyMount minHeight={anatomy.ladder.length * 24 + 34} label="sign-agreement chart">
              <Ladder rows={anatomy.ladder} />
            </LazyMount>
            {anatomy.formula && <p className="prose-note is-muted">{anatomy.formula}.</p>}
          </div>
        )}
        {anatomy.experiments.length > 0 && (
          <div className="split-side">
            <h4 className="figure-title">Result: accuracy without and with the purge</h4>
            <Legend items={[
              { tone: "alt2", label: "unpurged", shape: "dot" },
              { tone: "truth", label: "purged", shape: "dot" },
              hasBaseRate && { tone: "median", label: "base rate: no skill on CL=F", shape: "tick" },
            ].filter(Boolean)} />
            <LazyMount minHeight={anatomy.experiments.length * 22 + 58} label="experiment chart">
              <Dumbbell rows={anatomy.experiments} reference={anatomy.reference} />
            </LazyMount>
          </div>
        )}
      </div>
      {anatomy.conclusion && <p className="prose-note">{anatomy.conclusion}</p>}
    </div>
  );
}

const SOURCE = {
  artifact: ["research.json → postmortem", "research.json → leak_anatomy"],
  reproduce: [REPRODUCE.walkForward, REPRODUCE.leakAnatomy],
};

/** LEAK: the retracted 1-week direction model, leaked vs purged. The integrity story. */
export default function LeakPanel({ data, research, researchStatus }) {
  const [windowKey, setWindowKey] = useState("five_year");
  const pm = postmortemWindow(research, windowKey);
  const available = WINDOWS.filter((option) => postmortemWindow(research, option.value));
  const lab = getLab(research);
  const horizon = num(lab?.config?.horizon_sessions);
  const retracted = asObject(data?.retracted_direction_model);
  const EquityChart = getEquityChart();
  const PurgeDiagram = getPurgeDiagram();
  const anatomy = leakAnatomy(research);

  if (!pm?.leaked || !pm?.purged) {
    return (
      <Panel id="leak" code="LEAK" name="Leak post-mortem" title={null}
        fallbackTitle={`1-week direction model: ${asText(retracted?.status)?.toLowerCase() ?? "retracted"}${asText(retracted?.reason) ? ` (${retracted.reason})` : ""}`}
        source={SOURCE}>
        <ResearchUnavailable status={researchStatus} what="the leaked-vs-purged comparison" />
      </Panel>
    );
  }

  const { leaked, purged } = pm;
  const runs = [
    { key: "leaked", label: "Leaked (no purge)", tone: "alt2", ...leaked },
    { key: "purged", label: "Purged (correct)", tone: "truth", ...purged },
  ];
  const label = WINDOWS.find((option) => option.value === windowKey)?.title ?? "";
  return (
    <Panel
      id="leak"
      code="LEAK"
      name="Leak post-mortem"
      title={leakAssertion(pm)}
      fallbackTitle="Leaked vs purged walk-forward"
      meta={[
        purged.n != null ? `n ${fmtInt(purged.n)} weekly trades` : null,
        fmtSpan(purged.first, purged.last),
        label.toLowerCase(),
        pm.trainWindow != null ? `train window ${fmtInt(pm.trainWindow)} days` : null,
        pm.purgeRows != null ? `purge ${pm.purgeRows} rows` : null,
      ]}
      actions={available.length > 1
        ? <Segmented label="Walk-forward window" options={available} value={windowKey} onChange={setWindowKey} />
        : null}
      caption={`Same model, data and costs; the only change is dropping the training rows whose labels overlap the forecast. ${asText(retracted?.status) ? `Status: ${retracted.status.toLowerCase()}` : "Retracted"}; the direction model is not run in production.`}
      source={SOURCE}
      resetKey={research}
      table={() => (<>
        <DataTable
          caption={`Cumulative P&L, ${label.toLowerCase()} (last 26 weekly points, USD)`}
          rowKey={(row) => row.date}
          dense
          columns={[
            { key: "date", label: "Date", rowHeader: true, render: (row) => fmtDate(row.date) },
            { key: "leaked", label: "Leaked", align: "right", render: (row) => fmtSignedUsd(row.leaked) },
            { key: "purged", label: "Purged", align: "right", render: (row) => fmtSignedUsd(row.purged) },
          ]}
          rows={purged.equity.slice(-26).reverse().map((point) => ({
            date: point.date,
            purged: point.pnl,
            leaked: leaked.equity.find((other) => other.date === point.date)?.pnl ?? null,
          }))}
        />
        {anatomy?.experiments.length > 0 && (
        <DataTable
          caption={`Leak anatomy: direction accuracy and Sharpe, ${anatomy.sharpeBasis.experiments}, without and with the purge, against the no-skill accuracy`}
          rowKey={(row) => row.key}
          dense
          columns={[
            { key: "label", label: "Data · model", rowHeader: true },
            { key: "runs", label: "Runs", align: "right", render: (row) => fmtInt(row.runs) },
            { key: "unpurged", label: "Unpurged acc.", align: "right", render: (row) => fmtPct(row.unpurged) },
            { key: "unpurgedSharpe", label: "Sharpe", title: `Mean Sharpe across runs, ${anatomy.sharpeBasis.experiments}`, align: "right", render: (row) => fmtNum(row.unpurgedSharpe, 2) },
            { key: "purged", label: "Purged acc.", align: "right", render: (row) => fmtPct(row.purged) },
            { key: "purgedSharpe", label: "Sharpe", title: `Mean Sharpe across runs, ${anatomy.sharpeBasis.experiments}`, align: "right", render: (row) => fmtNum(row.purgedSharpe, 2) },
            {
              key: "noSkill", label: "No skill", title: "50% on symmetric random-walk returns; on CL=F returns, the mean of each run's base rate (the share of the more common sign)", align: "right",
              render: (row) => (row.noSkill != null ? `${fmtPct(row.noSkill)}${row.symmetric ? "" : " base rate"}` : DASH),
            },
            {
              key: "beatBase", label: "Runs > base rate", title: "Runs whose accuracy beats their own base rate at 5% (binomial), unpurged · purged", align: "right",
              render: (row) => (row.unpurgedBeatBase != null && row.purgedBeatBase != null
                ? `${fmtInt(row.unpurgedBeatBase)}/${fmtInt(row.runs)} · ${fmtInt(row.purgedBeatBase)}/${fmtInt(row.purgedRuns ?? row.runs)}`
                : DASH),
            },
            { key: "purgedP", label: "Purged p vs 50%", title: "Pooled binomial test of the purged runs against 50%, the no-skill accuracy only on the random walk. The block-shuffled runs reuse the same returns, so their pooled test overstates the evidence", align: "right", render: (row) => fmtPValue(row.purgedP) },
          ]}
          rows={anatomy.experiments}
        />
        )}
      </>)}
    >
      <div className="split split--wide">
        <div className="split-figure">
          <Legend items={[
            { tone: "alt2", label: "leaked (unpurged)", shape: "line" },
            { tone: "truth", label: "purged", shape: "line" },
          ]} />
          <LazyMount minHeight={240} label="equity curves">
            <EquityChart leaked={leaked.equity} purged={purged.equity} />
          </LazyMount>
        </div>
        <div className="split-side">
          <DataTable
            caption="Same model, leaked vs purged"
            rowKey={(row) => row.key}
            dense
            columns={[
              { key: "label", label: "Walk-forward", rowHeader: true, render: (row) => <span className={`label-${row.tone}`}>{row.label}</span> },
              { key: "accuracy", label: "Accuracy", align: "right", render: (row) => <span className="num">{fmtPct(row.accuracy)}</span> },
              { key: "p", label: "p", align: "right", render: (row) => <span className="num">{fmtPValue(row.p)}</span> },
              { key: "sharpe", label: "Sharpe", align: "right", render: (row) => <span className="num">{fmtNum(row.sharpe, 2)}</span> },
              { key: "pnl", label: "P&L", align: "right", render: (row) => <span className="num">{fmtSignedUsd(row.pnl)}</span> },
              { key: "maxDrawdown", label: "Max DD", align: "right", render: (row) => <span className="num">{fmtSignedUsd(row.maxDrawdown == null ? null : -Math.abs(row.maxDrawdown))}</span> },
            ]}
            rows={runs}
            footnote={purged.p != null
              ? `Purged accuracy ${fmtPct(purged.accuracy)} on ${fmtInt(purged.n)} trades, ${fmtP(purged.p)}${purged.p >= ALPHA ? ": indistinguishable from a coin flip" : ""}.`
              : null}
          />
          {pm.purgeRows != null && (
            <>
              <h3 className="figure-title">The purge</h3>
              <LazyMount minHeight={180} label="purge diagram">
                <PurgeDiagram purgeRows={pm.purgeRows} horizon={horizon ?? pm.purgeRows + 1} />
              </LazyMount>
              <p className="prose-note">
                A training row dated t−k is labelled with the return over the next {horizon ?? pm.purgeRows + 1} sessions.
                The {pm.purgeRows} rows closest to the forecast time have labels that end after it, so they
                leak the future into training and are dropped.
              </p>
            </>
          )}
        </div>
      </div>
      <LeakAnatomy anatomy={anatomy} />
    </Panel>
  );
}
