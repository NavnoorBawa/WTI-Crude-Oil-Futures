import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import Panel from "../components/Panel";
import { INTERVAL_LEVEL_PCT, liveAssertion, promotionText } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtSigned, num, withCentralTime } from "../lib/format";
import { asObject, getVol, liveRecordSummary } from "../lib/research";
import { Facts } from "./common";

const STATUS_LABEL = {
  pending: "pending",
  resolved: "resolved",
  skipped_roll: "skipped: roll",
  expired: "expired",
  late_commit: "late commit",
};
const STATUS_TITLE = {
  skipped_roll: "The target window spans a contract roll; never scored",
  late_commit: "Not sealed in time (less than five minutes before the settlement that opens its window, or never); never scored",
  expired: "No settlement data arrived in time; never scored",
};

/** LIVE: the forward record of the model of record and the challenger. */
export default function LiveRecordPanel({ data }) {
  const { liveRecord, live, validation } = getVol(data);
  const summary = liveRecordSummary(liveRecord);
  const noise = asObject(validation?.direction_noise_floor);
  const challengerLabel = typeof live?.challenger?.label === "string" ? live.challenger.label
    : typeof liveRecord?.challenger?.key === "string" ? liveRecord.challenger.key : null;

  if (!summary) {
    return (
      <Panel id="live" code="LIVE" name="Live record" title={null}
        fallbackTitle="No live record is in this snapshot" source={{ artifact: "data.json → vol_forecast.live_record", reproduce: null }}>
        <div className="figure-empty"><p className="empty-text">This snapshot carries no live_record block.</p></div>
      </Panel>
    );
  }

  const champion = summary.champion;
  const challenger = summary.challenger;
  const empty = summary.recent.length === 0;
  const promotion = summary.promotion;
  const noiseSims = num(noise?.sims);
  return (
    <Panel
      id="live"
      code="LIVE"
      name="Live record"
      title={liveAssertion(summary)}
      fallbackTitle="Live forward record"
      meta={[
        summary.started ? `since ${fmtDate(summary.started)}` : null,
        `${fmtInt(summary.recorded ?? 0)} recorded`,
        `${fmtInt(summary.resolved ?? 0)} resolved`,
        `${fmtInt(summary.independent ?? 0)} independent`,
        promotion ? `promotion ${promotionText(promotion)}` : null,
        typeof champion.model_version === "string" ? `record ${champion.model_version}` : null,
      ]}
      caption={withCentralTime(summary.note) ?? "Each forecast is stored when issued and scored only after its target window has closed."}
      source={{ artifact: "data.json → vol_forecast.live_record", reproduce: null }}
      resetKey={data}
    >
      <Facts className="facts--row" items={[
        { label: "Pending", value: fmtInt(summary.pending ?? 0) },
        { label: "Skipped at rolls", value: fmtInt(summary.skipped ?? 0), note: summary.skipped ? "never scored" : null },
        summary.lateCommit != null && { label: "Not sealed in time", value: fmtInt(summary.lateCommit), note: summary.lateCommit ? "never scored" : null },
        summary.expired != null && { label: "Expired", value: fmtInt(summary.expired) },
        promotion && {
          label: "Promotion test",
          value: promotion.status === "accruing" && promotion.paired != null && promotion.required != null
            ? `${fmtInt(promotion.paired)}/${fmtInt(promotion.required)}`
            : promotionText(promotion),
          note: promotion.status === "accruing"
            ? "independent paired weeks; accruing"
            : [promotion.dmStat != null ? `DM ${fmtSigned(promotion.dmStat, 2)}` : null, promotion.p != null ? `one-sided ${fmtP(promotion.p)}` : null]
              .filter(Boolean).join(", ") || null,
        },
        { label: "QLIKE, record", value: fmtNum(champion.qlike, 3) },
        {
          label: "QLIKE, challenger", value: fmtNum(challenger.qlike, 3),
          note: [challengerLabel, challenger.n != null ? `n ${fmtInt(challenger.n)}` : null].filter(Boolean).join(" · ") || null,
        },
        { label: `${INTERVAL_LEVEL_PCT}% coverage`, value: fmtPct(champion.interval_80_coverage_pct) },
        {
          label: "Mean fcst / realized",
          value: champion.mean_forecast_pct != null || champion.mean_realized_pct != null
            ? `${fmtNum(champion.mean_forecast_pct, 1)} / ${fmtNum(champion.mean_realized_pct, 1)}`
            : DASH,
        },
      ]} />

      {empty ? (
        <div className="figure-empty is-compact">
          <p className="empty-title">No calls recorded yet</p>
          <p className="empty-text">
            {summary.started ? `The record opened ${fmtDate(summary.started)}. ` : ""}
            {summary.firstResult
              ? `The first forecast resolves after the settlement on ${summary.firstResultIsEstimate ? "about " : ""}${fmtDate(summary.firstResult)}${summary.firstResultIsEstimate ? " (weekdays counted; exchange holidays not)" : ""}.`
              : "Results appear once the first target window closes."}
          </p>
        </div>
      ) : (
        <DataTable
          caption="Most recent live calls (percent, annualized)"
          rowKey={(row) => `${row.as_of}|${row.issued_at}`}
          dense
          columns={[
            { key: "as_of", label: "As of", rowHeader: true, render: (row) => fmtDate(row.as_of) },
            { key: "window", label: "Target window", render: (row) => (row.target_start ? `${fmtDate(row.target_start, { year: false })} – ${fmtDate(row.target_end)}` : DASH) },
            { key: "forecast_pct", label: "Forecast", align: "right", render: (row) => fmtNum(row.forecast_pct, 1) },
            { key: "band", label: `${INTERVAL_LEVEL_PCT}% band`, align: "right", render: (row) => (row.lo80_pct != null ? `${fmtNum(row.lo80_pct, 1)}–${fmtNum(row.hi80_pct, 1)}` : DASH) },
            { key: "challenger_pct", label: "Challenger", align: "right", render: (row) => fmtNum(row.challenger_pct, 1) },
            { key: "realized_pct", label: "Realized", align: "right", render: (row) => fmtNum(row.realized_pct, 1) },
            {
              key: "status", label: "Status",
              render: (row) => (
                <Chip tone={`status-${row.status ?? "unknown"}`} title={STATUS_TITLE[row.status]}>
                  {STATUS_LABEL[row.status] ?? String(row.status ?? DASH)}
                </Chip>
              ),
            },
          ]}
          rows={summary.recent}
        />
      )}

      {summary.promotionRule && (
        <p className="rule-note">
          <span className="micro">Promotion rule</span>
          {summary.promotionRule}
          {promotion?.meanDifference != null
            ? ` Mean QLIKE difference, record minus challenger (positive favours the challenger): ${fmtSigned(promotion.meanDifference, 4)}.`
            : ""}
        </p>
      )}
      {summary.directionNote && (
        <p className="rule-note">
          <span className="micro">Direction{noiseSims != null ? `, production validation (${fmtInt(noiseSims)} simulations)` : ""}</span>
          {summary.directionNote}
        </p>
      )}
    </Panel>
  );
}
