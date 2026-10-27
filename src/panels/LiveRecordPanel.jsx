import { RECORD_HISTORY_URL } from "../app/config";
import { getLiveCalls } from "../charts/lazy";
import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import keepHyphenated from "../components/keepHyphenated";
import LazyMount from "../components/LazyMount";
import Panel from "../components/Panel";
import { INTERVAL_LEVEL_PCT, liveAssertion, liveDetail, promotionDetail, promotionText } from "../lib/assertions";
import {
  DASH, ctWallTimeMs, fmtAge, fmtCT, fmtDate, fmtInt, fmtNum, fmtSigned, num, toMs, withCentralTime,
} from "../lib/format";
import {
  asObject, asText, getLab, getRoles, getVol, liveRecordSummary, modelLabels, shortLabel, withModelLabels,
} from "../lib/research";
import { Facts, Legend } from "./common";

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
const statusLabel = (status) => STATUS_LABEL[status] ?? String(status ?? DASH);

// The chart's reserved height, at or above 640px and below (LazyMount and LiveCalls both use it).
const CALLS_HEIGHT = { wide: 200, narrow: 180 };
// Below this many resolved calls a share is shown as the count it is ("2 of 3 inside").
const COUNT_BELOW = 10;
// CL settles at 14:30 ET, which is 13:30 CT on every date (both zones change on the same days).
const SETTLEMENT_CT = { hour: 13, minute: 30 };

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

/** The resolved calls kept out of the promotion test's pinned pair, by reason, in words. */
const exclusionText = (excluded) => {
  if (!excluded) return null;
  const parts = [
    excluded.otherVersion ? `${fmtInt(excluded.otherVersion)} from another model version` : null,
    excluded.otherChallenger ? `${fmtInt(excluded.otherChallenger)} with another challenger` : null,
    excluded.noChallenger ? `${fmtInt(excluded.noChallenger)} with no challenger forecast` : null,
  ].filter(Boolean);
  if (parts.length) return `Resolved calls left out of it: ${parts.join(", ")}.`;
  return [excluded.otherVersion, excluded.otherChallenger, excluded.noChallenger].every((value) => value === 0)
    ? "No resolved call has been left out of it." : null;
};

/** "yes" / "no" for a resolved call with a band, null otherwise. Pure. */
export const inBand = (row) => {
  const realized = row?.status === "resolved" ? num(row.realized_pct) : null;
  const lo = num(row?.lo80_pct);
  const hi = num(row?.hi80_pct);
  if (realized == null || lo == null || hi == null) return null;
  return realized >= lo && realized <= hi ? "yes" : "no";
};

/** Milliseconds from a call's seal to the settlement that opens its window (negative if after). Pure. */
export const sealLead = (row) => {
  const sealed = toMs(row?.sealed_at);
  const opens = ctWallTimeMs(row?.base_session, SETTLEMENT_CT.hour);
  return sealed == null || opens == null ? null : opens + SETTLEMENT_CT.minute * 60000 - sealed;
};

const INPUT_LABELS = [["ovx", "OVX"], ["rv5_pct", "5-day RV"], ["rv22_pct", "22-day RV"], ["rv66_pct", "66-day RV"]];

/**
 * How the newest call's forecast and recorded inputs moved since the call before it, in vol points,
 * when both calls carry inputs: "Since the Oct 22 call: forecast +2.6; OVX +2.4, 5-day RV +1.7, …".
 * Descriptive only: it says nothing about how much each input moved the forecast. Pure.
 */
export const inputChanges = (recent) => {
  const [latest, previous] = recent ?? [];
  const now = asObject(latest?.inputs);
  const before = asObject(previous?.inputs);
  if (!now || !before || typeof previous.as_of !== "string") return null;
  const moves = INPUT_LABELS
    .map(([key, label]) => (num(now[key]) != null && num(before[key]) != null ? `${label} ${fmtSigned(now[key] - before[key], 1)}` : null))
    .filter(Boolean);
  if (!moves.length) return null;
  const forecast = num(latest.forecast_pct) != null && num(previous.forecast_pct) != null
    ? `forecast ${fmtSigned(latest.forecast_pct - previous.forecast_pct, 1)}; ` : "";
  return `Since the ${fmtDate(previous.as_of, { year: false })} call: ${forecast}${moves.join(", ")} (vol points, as recorded).`;
};

/**
 * The record's 80% coverage: "k of n inside" while there are few resolved calls and every one of
 * them is among the recent rows (so k is counted, not inferred); otherwise the published share.
 */
const coverageFact = (summary) => {
  const pct = num(summary.champion.interval_80_coverage_pct);
  const n = summary.recordAll.n;
  const note = n != null ? `record, n ${fmtInt(n)}` : null;
  if (pct == null) return { value: DASH, note };
  if (n != null && n > 0 && n < COUNT_BELOW) {
    const rows = summary.recent.filter((row) => row.model_version === summary.recordAll.modelVersion && inBand(row) != null);
    const inside = rows.filter((row) => inBand(row) === "yes").length;
    if (rows.length === n && Math.abs((100 * inside) / n - pct) < 0.1) return { value: `${fmtInt(inside)} of ${fmtInt(n)} inside`, note: "record" };
  }
  return { value: `${fmtNum(pct, 1)}%`, note };
};

/** Fills `paired` of `required` cells: the promotion test's progress, one cell per independent week. */
function PromotionStrip({ paired, required }) {
  if (!(required > 0) || paired == null) return null;
  const filled = Math.min(paired, required);
  return (
    <div className="live-strip" aria-hidden="true" style={{ "--cells": required }}>
      {Array.from({ length: required }, (_, index) => <i key={index} className={index < filled ? "is-filled" : undefined} />)}
    </div>
  );
}

/**
 * LIVE: the forward record of the model of record and the challenger. Each daily call is recorded
 * and sealed before its five-session window opens; the windows of consecutive calls overlap, so
 * calls are counted as calls and only non-overlapping windows as independent weeks. The two models
 * are compared on the same resolved calls only (the pinned pair's); the record's QLIKE over every
 * resolved call of its version is a separate, labelled figure.
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

  const LiveCalls = getLiveCalls();
  const promotion = summary.promotion;
  const challengerKey = promotion?.challengerKey ?? asText(summary.challenger.key);
  const challengerLabel = challengerKey
    ? shortLabel(labels.get(challengerKey) ?? (live?.challenger?.key === challengerKey ? asText(live.challenger.label) : null) ?? challengerKey)
    : null;
  const recordVersion = promotion?.modelVersion ?? summary.recordAll.modelVersion;
  const recordName = shortLabel(labels.get(getRoles(lab, null).champion)) ?? "the model of record";
  const { paired, recordAll, recent } = summary;
  const resolved = summary.resolved ?? 0;
  const recorded = summary.recorded ?? recent.length;
  const empty = recent.length === 0;
  const noiseSims = num(noise?.sims);

  const versions = new Set(recent.map((row) => asText(row.model_version)));
  const oneVersion = versions.size === 1 ? [...versions][0] : null;
  const anySealed = recent.some((row) => toMs(row.sealed_at) != null);
  const statuses = [...new Set(recent.map((row) => row.status))].filter((status) => STATUS_TITLE[status]);
  const coverage = coverageFact(summary);
  const changes = inputChanges(recent);
  const legend = [
    { tone: "model", label: "forecast", shape: "dot" },
    { tone: "model", label: `${INTERVAL_LEVEL_PCT}% band`, shape: "tick" },
    recent.some((row) => num(row.challenger_pct) != null) && { tone: "alt", label: challengerLabel ?? "challenger", shape: "ring" },
    recent.some((row) => inBand(row) === "yes") && { tone: "truth", label: "realized, inside band", shape: "bar" },
    recent.some((row) => inBand(row) === "no") && { tone: "warn", label: "realized, outside band", shape: "bar-open" },
    recent.some((row) => STATUS_TITLE[row.status]) && { tone: "neutral", label: "never scored", shape: "dash" },
  ].filter(Boolean);

  // On a phone the year (the record's own, and in the chart) gives way to the columns that matter.
  const columns = [
    {
      key: "as_of", label: "As of", rowHeader: true,
      render: (row) => (
        <>
          <span className="live-date">{fmtDate(row.as_of)}</span>
          <span className="live-date-short">{fmtDate(row.as_of, { year: false })}</span>
        </>
      ),
    },
    {
      key: "status", label: "Status", csv: (row) => statusLabel(row.status),
      render: (row) => (
        <Chip tone={`status-${row.status ?? "unknown"}`} title={STATUS_TITLE[row.status]}>{statusLabel(row.status)}</Chip>
      ),
    },
    {
      key: "window", label: "Target window", hideBelow: 640, csvLabel: "Target window (start/end)",
      csv: (row) => (row.target_start ? `${row.target_start}/${row.target_end}` : null),
      render: (row) => (row.target_start ? `${fmtDate(row.target_start, { year: false })} – ${fmtDate(row.target_end)}` : DASH),
    },
    !oneVersion && {
      key: "model_version", label: "Model", title: "Model version that issued the forecast", className: "muted-cell",
      render: (row) => asText(row.model_version) ?? DASH,
    },
    {
      key: "forecast_pct", label: "Forecast", align: "right",
      render: (row) => (
        <>
          {fmtNum(row.forecast_pct, 1)}
          {row.lo80_pct != null && <span className="cell-note live-band-note">{`${fmtNum(row.lo80_pct, 1)}–${fmtNum(row.hi80_pct, 1)}`}</span>}
        </>
      ),
    },
    {
      key: "band", label: `${INTERVAL_LEVEL_PCT}% band`, align: "right", hideBelow: 640, csvLabel: `${INTERVAL_LEVEL_PCT}% band (low/high)`,
      csv: (row) => (row.lo80_pct != null ? `${row.lo80_pct}/${row.hi80_pct}` : null),
      render: (row) => (row.lo80_pct != null ? `${fmtNum(row.lo80_pct, 1)}–${fmtNum(row.hi80_pct, 1)}` : DASH),
    },
    { key: "challenger_pct", label: "Challenger", align: "right", hideBelow: 640, render: (row) => fmtNum(row.challenger_pct, 1) },
    {
      key: "realized_pct", label: "Realized", align: "right",
      render: (row) => {
        const value = inBand(row);
        return (
          <>
            {fmtNum(row.realized_pct, 1)}
            {value && (
              <span className={`cell-note live-inband-note${value === "no" ? " live-outside" : ""}`}>
                {value === "yes" ? "in band" : "outside band"}
              </span>
            )}
          </>
        );
      },
    },
    {
      key: "in_band", label: "In band", title: `Realized vol inside the ${INTERVAL_LEVEL_PCT}% band (resolved calls only)`,
      hideBelow: 640, csv: (row) => inBand(row),
      render: (row) => {
        const value = inBand(row);
        return value ? <span className={value === "no" ? "live-outside" : undefined}>{value}</span> : DASH;
      },
    },
    anySealed && {
      key: "sealed_at", label: "Sealed", title: "Sealed before the git commit; the note says how long before the settlement that opens its window",
      hideBelow: 640, csv: (row) => asText(row.sealed_at),
      render: (row) => {
        const ms = toMs(row.sealed_at);
        if (ms == null) return DASH;
        const lead = sealLead(row);
        return (
          <>
            {fmtCT(ms)}
            {lead != null && (
              <span className="cell-note">{lead >= 0 ? `${fmtAge(lead)} before its window` : `${fmtAge(-lead)} after its window opened`}</span>
            )}
          </>
        );
      },
    },
  ].filter(Boolean);

  const promotionFact = promotion && {
    label: "Promotion test",
    value: promotion.status === "accruing"
      ? (promotion.paired != null && promotion.required != null ? `${fmtInt(promotion.paired)}/${fmtInt(promotion.required)}` : "accruing")
      : promotion.status === "promote" ? "Promote" : "Keep",
    note: promotion.status === "accruing" ? "independent paired weeks; accruing" : promotionDetail(promotion),
  };

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
        promotion ? `promotion test: ${promotionText(promotion)}` : null,
        recordVersion && challengerLabel ? `pinned pair: ${recordName} (${recordVersion}) vs ${challengerLabel}` : null,
      ]}
      caption={withStatusLabels(withCentralTime(summary.note)) ?? "Each forecast is stored when issued and scored only after its target window has closed."}
      notes={[
        summary.promotionRule && (
          <p key="rule" className="rule-note">
            <span className="micro">Promotion rule</span>
            {withCodeIdentifiers(summary.promotionRule)}
            {promotion?.meanDifference != null
              ? ` Mean QLIKE difference, record minus challenger (positive favours the challenger): ${fmtSigned(promotion.meanDifference, 4)}.`
              : ""}
            {exclusionText(promotion?.excluded) ? ` ${exclusionText(promotion.excluded)}` : ""}
          </p>
        ),
        challengerError && !live?.challenger && (
          <p key="challenger" className="rule-note">
            <span className="micro">Challenger</span>
            No challenger forecast in this snapshot, so this session&apos;s call cannot enter the promotion test.
            {" "}<span className="muted">Reason recorded: {withModelLabels(challengerError, labels)}</span>
          </p>
        ),
        changes && (
          <p key="inputs" className="rule-note">
            <span className="micro">Inputs, call to call</span>
            {keepHyphenated(changes)}
          </p>
        ),
        summary.directionNote && (
          <p key="direction" className="rule-note">
            <span className="micro">Direction{noiseSims != null ? `, production validation (${fmtInt(noiseSims)} simulations)` : ""}</span>
            {summary.directionNote}
          </p>
        ),
      ]}
      source={{ artifact: "data.json → vol_forecast.live_record", reproduce: null }}
      resetKey={data}
    >
      <p className="live-status sep-list">
        <span>{`${fmtInt(resolved)} resolved${resolved > 0 && summary.independent != null ? ` (${fmtInt(summary.independent)} independent ${summary.independent === 1 ? "week" : "weeks"})` : ""}`}</span>
        <span>{`${fmtInt(summary.pending ?? 0)} pending`}</span>
        <span>{`${fmtInt(summary.skipped ?? 0)} skipped at rolls`}</span>
        {summary.lateCommit != null && <span>{`${fmtInt(summary.lateCommit)} not sealed in time`}</span>}
        {summary.expired != null && <span>{`${fmtInt(summary.expired)} expired`}</span>}
      </p>

      <div className={`live-summary${resolved > 0 ? " has-scores" : ""}`}>
        {promotionFact && (
          <div className="live-progress">
            <Facts items={[promotionFact]} />
            <PromotionStrip paired={promotion.paired} required={promotion.required} />
          </div>
        )}
        {resolved > 0 ? (
          <Facts className="facts--row" items={[
            {
              label: "QLIKE, same calls",
              value: paired.record != null || paired.challenger != null ? `${fmtNum(paired.record, 3)} / ${fmtNum(paired.challenger, 3)}` : DASH,
              note: `record / ${challengerLabel ?? "challenger"}${paired.n != null ? `, n ${fmtInt(paired.n)}` : ""}`,
            },
            {
              label: "QLIKE, record, all calls",
              value: fmtNum(recordAll.qlike, 3),
              note: [recordAll.modelVersion, recordAll.n != null ? `n ${fmtInt(recordAll.n)}` : null].filter(Boolean).join(", ") || null,
            },
            { label: `${INTERVAL_LEVEL_PCT}% coverage`, value: coverage.value, note: coverage.note },
            {
              label: "Mean fcst / realized",
              value: summary.champion.mean_forecast_pct != null || summary.champion.mean_realized_pct != null
                ? `${fmtNum(summary.champion.mean_forecast_pct, 1)} / ${fmtNum(summary.champion.mean_realized_pct, 1)}`
                : DASH,
              note: recordAll.n != null ? `record, n ${fmtInt(recordAll.n)}` : null,
            },
          ]} />
        ) : !empty && (
          <p className="live-empty">
            {summary.firstResult
              ? `Scores appear after the ${fmtDate(summary.firstResult)} settlement.`
              : "Scores appear once the first target window closes."}
          </p>
        )}
      </div>

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
        <>
          <h3 className="figure-title">
            {`Latest ${fmtInt(recent.length)} recorded ${recent.length === 1 ? "call" : "calls"}: forecast and ${INTERVAL_LEVEL_PCT}% band, `
              + `with realized vol ${resolved > 0 ? "where resolved" : "once resolved"}`}
          </h3>
          <Legend items={legend} />
          <LazyMount minHeight={CALLS_HEIGHT} label="recorded calls">
            <LiveCalls calls={recent} nextResultDue={summary.firstResult} bandLevel={INTERVAL_LEVEL_PCT}
              challengerLabel={challengerLabel} statusLabel={statusLabel} heights={CALLS_HEIGHT} />
          </LazyMount>
          <div className="live-table">
            <DataTable
              caption={`Latest ${fmtInt(recent.length)} of ${fmtInt(recorded)} recorded ${recorded === 1 ? "call" : "calls"} (percent, annualized)`
                + `${oneVersion ? `; all from ${oneVersion}` : ""}`}
              rowKey={(row) => `${row.as_of}|${row.issued_at}`}
              dense
              exportName="live-record"
              columns={columns}
              rows={recent}
              footnote={statuses.length > 0 && (
                <ul className="footnotes">
                  {statuses.map((status) => <li key={status}><b>{statusLabel(status)}</b>: {STATUS_TITLE[status]}.</li>)}
                </ul>
              )}
            />
          </div>
        </>
      )}
      <p className="live-history">
        <a href={RECORD_HISTORY_URL} target="_blank" rel="noopener noreferrer"
          aria-label="Full record and its commit history on GitHub (opens in a new tab)">
          Full record and its commit history on GitHub →
        </a>
      </p>
    </Panel>
  );
}
