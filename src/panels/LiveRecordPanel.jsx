import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import Panel from "../components/Panel";
import { INTERVAL_LEVEL_PCT, liveAssertion, liveDetail, promotionText } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtSigned, num, withCentralTime } from "../lib/format";
import { asObject, asText, getLab, getRoles, getVol, liveRecordSummary, modelLabels, shortLabel, withModelLabels } from "../lib/research";
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

// Identifiers quoted in the rule's prose (a model version such as har-iv/2, a model key such as
// har_iv_range) are set in code style, as identifiers are elsewhere on the page.
const IDENTIFIER = /(\b[a-z0-9-]+(?:[_/][a-z0-9-]+)+\b)/;
const withCodeIdentifiers = (text) =>
  text.split(IDENTIFIER).map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part));

/** Record prose with each status key ("late_commit") in the words its chip uses ("late commit"). */
export const withStatusLabels = (text) =>
  typeof text === "string"
    ? text.replace(/\b(skipped_roll|late_commit)\b/g, (key) => `"${STATUS_LABEL[key]}"`)
    : text;

/** The resolved weeks kept out of the promotion test's pinned pair, by reason, in words. */
const exclusionText = (excluded) => {
  if (!excluded) return null;
  const parts = [
    excluded.otherVersion ? `${fmtInt(excluded.otherVersion)} from another model version` : null,
    excluded.otherChallenger ? `${fmtInt(excluded.otherChallenger)} with another challenger` : null,
    excluded.noChallenger ? `${fmtInt(excluded.noChallenger)} with no challenger forecast` : null,
  ].filter(Boolean);
  if (parts.length) return `Resolved weeks left out of it: ${parts.join(", ")}.`;
  return [excluded.otherVersion, excluded.otherChallenger, excluded.noChallenger].every((value) => value === 0)
    ? "No resolved week has been left out of it." : null;
};

/**
 * LIVE: the forward record of the model of record and the challenger. The two are compared on the
 * same resolved weeks only (the promotion test's pinned pair); the record's QLIKE over every
 * resolved week of its version is a separate, labelled figure.
 */
export default function LiveRecordPanel({ data, research }) {
  const { liveRecord, live, validation } = getVol(data);
  const summary = liveRecordSummary(liveRecord);
  const noise = asObject(validation?.direction_noise_floor);
  const lab = getLab(research);
  const labels = modelLabels(lab);
  const challengerError = asText(live?.challenger_error);

  if (!summary) {
    return (
      <Panel id="live" code="LIVE" name="Live record" title={null}
        fallbackTitle="No live record is in this snapshot" source={{ artifact: "data.json → vol_forecast.live_record", reproduce: null }}>
        <div className="figure-empty"><p className="empty-text">This snapshot carries no live_record block.</p></div>
      </Panel>
    );
  }

  const champion = summary.champion;
  const promotion = summary.promotion;
  const challengerKey = promotion?.challengerKey ?? asText(summary.challenger.key);
  const challengerLabel = challengerKey
    ? shortLabel(labels.get(challengerKey) ?? (live?.challenger?.key === challengerKey ? asText(live.challenger.label) : null) ?? challengerKey)
    : null;
  const recordVersion = promotion?.modelVersion ?? summary.recordAll.modelVersion;
  const recordName = shortLabel(labels.get(getRoles(lab, null).champion)) ?? "the model of record";
  const empty = summary.recent.length === 0;
  const noiseSims = num(noise?.sims);
  const { paired, recordAll } = summary;
  return (
    <Panel
      id="live"
      code="LIVE"
      name="Live record"
      title={liveAssertion(summary)}
      fallbackTitle="Live forward record"
      meta={[
        summary.started ? `since ${fmtDate(summary.started)}` : null,
        ...liveDetail(summary),
        `${fmtInt(summary.resolved ?? 0)} resolved`,
        `${fmtInt(summary.independent ?? 0)} independent`,
        recordVersion && challengerLabel ? `pinned pair: ${recordName} (${recordVersion}) vs ${challengerLabel}` : null,
        promotion ? `promotion ${promotionText(promotion)}` : null,
      ]}
      caption={withStatusLabels(withCentralTime(summary.note)) ?? "Each forecast is stored when issued and scored only after its target window has closed."}
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
        {
          label: "QLIKE, same weeks",
          value: paired.record != null || paired.challenger != null ? `${fmtNum(paired.record, 3)} / ${fmtNum(paired.challenger, 3)}` : DASH,
          note: `record / ${challengerLabel ?? "challenger"}${paired.n != null ? `, n ${fmtInt(paired.n)}` : ""}`,
        },
        {
          label: "QLIKE, record, all weeks",
          value: fmtNum(recordAll.qlike, 3),
          note: [recordAll.modelVersion, recordAll.n != null ? `n ${fmtInt(recordAll.n)}` : null].filter(Boolean).join(", ") || null,
        },
        { label: `${INTERVAL_LEVEL_PCT}% coverage`, value: fmtPct(champion.interval_80_coverage_pct), note: recordAll.n != null ? `record, n ${fmtInt(recordAll.n)}` : null },
        {
          label: "Mean fcst / realized",
          value: champion.mean_forecast_pct != null || champion.mean_realized_pct != null
            ? `${fmtNum(champion.mean_forecast_pct, 1)} / ${fmtNum(champion.mean_realized_pct, 1)}`
            : DASH,
          note: recordAll.n != null ? `record, n ${fmtInt(recordAll.n)}` : null,
        },
      ]} />

      {empty ? (
        <div className="figure-empty is-compact">
          <p className="empty-title">No calls recorded yet</p>
          <p className="empty-text">
            {summary.started ? `The record opened ${fmtDate(summary.started)}. ` : ""}
            {summary.firstResult
              ? `The first forecast resolves after the settlement on ${fmtDate(summary.firstResult)}.`
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
            { key: "model_version", label: "Model", title: "Model version that issued the forecast", className: "muted-cell", render: (row) => asText(row.model_version) ?? DASH },
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

      {challengerError && !live?.challenger && (
        <p className="rule-note">
          <span className="micro">Challenger</span>
          No challenger forecast in this snapshot, so this session&apos;s call cannot enter the promotion test.
          <span className="muted">Reason recorded: {withModelLabels(challengerError, labels)}</span>
        </p>
      )}
      {summary.promotionRule && (
        <p className="rule-note">
          <span className="micro">Promotion rule</span>
          {withCodeIdentifiers(summary.promotionRule)}
          {promotion?.meanDifference != null
            ? ` Mean QLIKE difference, record minus challenger (positive favours the challenger): ${fmtSigned(promotion.meanDifference, 4)}.`
            : ""}
          {exclusionText(promotion?.excluded) ? ` ${exclusionText(promotion.excluded)}` : ""}
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
