import DataTable from "../components/DataTable";
import Panel, { REPRODUCE } from "../components/Panel";
import {
  ALPHA, carryAssertion, carryDetail, placeboVerdict, studiesAssertion, supplyPlaybookAssertion, supplyStudyAssertion,
  supplyStudyDetail,
} from "../lib/assertions";
import { DASH, NBSP, fmtInt, fmtNum, fmtP, fmtPValue, fmtPct, fmtSigned, fmtSignedPct, num, tidyProse } from "../lib/format";
import { asObject, asText, eventName, researchLedger, supplyStudy } from "../lib/research";
import { ResearchUnavailable, StudyHead } from "./common";

// Fallback labels for data.json playbook groups that carry no `label` of their own.
const SUPPLY_LABELS = {
  supply_lost: "Announced or physical supply removal >0.5 mbpd",
  opec_cut: "Announced OPEC(+) production cut",
  conflict: "Armed conflict",
  sanctions: "Sanctions",
  iran_driven: "Iran-driven",
  strait_risk: "Hormuz / transit-strait risk",
  weather: "Hurricane / weather shut-in",
  threat_only: "Geopolitical threat, no supply removed",
};
const SMALL_SAMPLE_N = 10;
const MEANINGFUL_GAP_PCT = 1.0; // a difference in medians below this is reported as "no difference"
const CARRY_SAMPLES = [
  { key: "full_sample", label: "Full sample" },
  { key: "pre_2007", label: "Before 2007" },
  { key: "post_2007", label: "2007 on" },
];

/** Supply-shock rows from data.json supply_shock_playbook.distributions (no placebo study). */
export const supplyRows = (playbook) => {
  const dist = asObject(playbook?.distributions) ?? {};
  return Object.entries(dist)
    .filter(([key, value]) => (Object.hasOwn(SUPPLY_LABELS, key) || asText(value?.label)) && num(value?.peak?.median) != null)
    .map(([key, value]) => ({
      key,
      label: asText(value.label) ?? SUPPLY_LABELS[key],
      n: num(value.n),
      peak: num(value.peak.median),
      peakMin: num(value.peak.min),
      peakMax: num(value.peak.max),
      settle: num(value.settle?.median),
    }))
    .sort((a, b) => b.peak - a.peak);
};

/** "Is a big first day already priced in?" from priced_in_stats; null when the sample is missing. */
export const pricedInNote = (playbook) => {
  const priced = asObject(playbook?.priced_in_stats) ?? {};
  const strongN = num(priced.strong_day0_n);
  const weakN = num(priced.weak_day0_n);
  const strongRise = num(priced.strong_day0_median_further_rise_pct);
  const weakRise = num(priced.weak_day0_median_further_rise_pct);
  const threshold = num(priced.threshold_pct);
  if (!(strongN > 0 && weakN > 0 && strongRise != null && weakRise != null && threshold != null)) return null;
  const gap = strongRise - weakRise;
  const verdict = gap >= MEANINGFUL_GAP_PCT ? "strong openings were followed by more upside, not a fade"
    : gap <= -MEANINGFUL_GAP_PCT ? "no evidence a strong open signals more upside"
      : "no meaningful difference either way";
  return {
    text: `After a day-0 move of at least ${fmtSigned(threshold, Number.isInteger(threshold) ? 0 : 1)}%, prices rose a median `
      + `${fmtSignedPct(strongRise)} further (n = ${strongN}) vs ${fmtSignedPct(weakRise)} after a weaker start (n = ${weakN}): `
      + `${verdict}${Math.min(strongN, weakN) < SMALL_SAMPLE_N ? "; n is small" : ""}.`,
    definition: asText(priced.definition),
  };
};

const UNMATCHED_TITLE = "resampling test of the group median vs random placebo dates";
const YEAR_MATCHED_TITLE = "the same test with each event's placebo dates drawn from its own calendar year";

/** Whether a group passes the study's rule (both placebo tests below ALPHA) on peak or on settle. */
/** "p = 0.026": the 3-decimal precision of the group table, so prose and table agree. */
const pLike = (value) => {
  const text = fmtPValue(value, { digits: 3 });
  return /^[<>]/.test(text) ? `p${NBSP}${text}` : `p${NBSP}=${NBSP}${text}`;
};

const passesRule = (row) => placeboVerdict(row.peakP, row.peakPYear) === "both"
  || placeboVerdict(row.settleP, row.settlePYear) === "both";

/** An overlapping event pair in words: "Hurricane Katrina (2005) and Hurricane Rita (2005), 19 sessions apart". */
const pairText = (pair) => `${pair.events.map(eventName).join(" and ")}${pair.apart != null ? `, ${fmtInt(pair.apart)} sessions apart` : ""}`;

/**
 * The supply study's reading, built from its fields: what an ordinary date does, which groups pass
 * only one of the two placebo tests (in either direction), and the caveats. The per-group numbers
 * are in the table.
 */
export const supplyStudyNotes = (study) => {
  if (!study) return [];
  const placebo = study.placebo;
  const notes = [];
  if (placebo?.peak != null && placebo.settle != null) {
    notes.push(`On an ordinary (placebo) date the median 20-session peak is ${fmtSignedPct(placebo.peak)} and the session-10 settle `
      + `${fmtSignedPct(placebo.settle)}${placebo.shareUp != null ? `; the peak is positive on ${fmtPct(placebo.shareUp, 0)} of ordinary dates` : ""}, `
      + "so a group's peak means something only relative to that.");
  }
  const oneSided = study.groups.filter((group) => placeboVerdict(group.peakP, group.peakPYear) === "one");
  if (oneSided.length) {
    notes.push(`Significant on peak against only one of the two placebos: ${oneSided
      .map((group) => `${group.name} (n${NBSP}=${NBSP}${fmtInt(group.n)}; ${pLike(group.peakP)} vs placebo, ${fmtPValue(group.peakPYear, { digits: 3 })} year-matched)`)
      .join("; ")}.`);
  }
  const pairs = study.overlapping?.pairs ?? [];
  notes.push(`Caveats: event dates and groupings were chosen with hindsight, and the groups overlap${pairs.length
    ? `; some events are close enough that their windows share sessions (${pairs.map(pairText).join("; ")}), so members of a group are not all independent`
    : ""}.`);
  return notes;
};

/**
 * The shared x-domain of the supply table's interval glyphs: every group's 95% interval and median
 * peak, and the ordinary-date median, with a little room at each end. Null without any value.
 */
export const peakGlyphDomain = (study) => {
  const values = [
    study?.placebo?.peak,
    ...(study?.groups ?? []).flatMap((group) => [group.peak, ...(group.ciPeak ?? [])]),
  ].filter((value) => Number.isFinite(value));
  if (!values.length) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = (hi - lo || 1) * 0.04;
  return [lo - pad, hi + pad];
};

/**
 * A 120 x 12 px glyph beside a group's median peak: its 95% interval (line), its median (dot) and
 * the ordinary-date median (dashed rule), on one domain shared by every row. The dot is in ink for
 * a group that passes the study's rule. Decorative: the numbers are in the cell's text.
 */
function IntervalGlyph({ domain, lo = null, hi = null, median = null, placebo = null, strong = false }) {
  if (!domain) return null;
  const [d0, d1] = domain;
  const at = (value) => `${(((value - d0) / (d1 - d0)) * 100).toFixed(2)}%`;
  return (
    <span className="interval-glyph" aria-hidden="true">
      {Number.isFinite(lo) && Number.isFinite(hi) && (
        <i className="ig-range" style={{ left: at(lo), width: `${(((hi - lo) / (d1 - d0)) * 100).toFixed(2)}%` }} />
      )}
      {Number.isFinite(placebo) && <i className="ig-placebo" style={{ left: at(placebo) }} />}
      {Number.isFinite(median) && <i className={`ig-dot${strong ? " is-strong" : ""}`} style={{ left: at(median) }} />}
    </span>
  );
}

/** A group's 95% bootstrap interval of the median peak, or null when an edge is missing. */
const peakInterval = (row) => (row.ciPeak?.length === 2 && row.ciPeak.every((v) => v != null) ? row.ciPeak : null);

/**
 * Columns for the CSV only (hidden on screen): the interval the peak cell draws beside the median,
 * and the event pairs behind the n cell's †, so the export holds what the table shows.
 */
export const SUPPLY_CSV_COLUMNS = [
  { key: "peakLo", label: "Median peak 95% CI low %", className: "csv-only", render: () => null, csv: (row) => peakInterval(row)?.[0] ?? null },
  { key: "peakHi", label: "Median peak 95% CI high %", className: "csv-only", render: () => null, csv: (row) => peakInterval(row)?.[1] ?? null },
  {
    key: "overlaps", label: "Events whose windows share sessions (†)", className: "csv-only", render: () => null,
    csv: (row) => (row.overlaps?.length ? row.overlaps.map(pairText).join("; ") : null),
  },
];

/** The placebo-controlled event study (research.json supply_study). */
function SupplyStudyTable({ study }) {
  const rows = [
    ...(study.placebo ? [{
      key: "placebo", name: "Ordinary date (placebo)", n: study.placebo.n, peak: study.placebo.peak,
      settle: study.placebo.settle, isPlacebo: true,
    }] : []),
    ...study.groups.slice().sort((a, b) => (a.peakPRule ?? a.peakP ?? 1) - (b.peakPRule ?? b.peakP ?? 1)),
  ];
  const pCell = (key) => (row) => (row.isPlacebo ? <span className="muted">ref.</span> : <span className="num">{fmtPValue(row[key], { digits: 3 })}</span>);
  const domain = peakGlyphDomain(study);
  const overlapping = study.overlapping;
  const anyOverlap = study.groups.some((group) => group.overlaps.length > 0);
  const alpha = `${fmtNum(ALPHA * 100, 0)}%`;
  const footnote = [
    `A group differs from an ordinary date only if both p-values of a statistic are below ${alpha} (rows marked at the left edge); the ledger's p is the larger of the two.`,
    study.between?.peakP != null
      ? `Supply removal vs threat-only directly: peak difference ${fmtSigned(study.between.peakDiff, 1)} pts, ${fmtP(study.between.peakP)}; settle ${fmtP(study.between.settleP)}.`
      : null,
    anyOverlap
      ? `† Contains events whose windows share sessions${overlapping?.definition ? ` (${overlapping.definition})` : ""}; the tests treat them as independent draws, which they are not.`
      : null,
    "p-values are not adjusted for the number of groups (see LEDG).",
  ].filter(Boolean).join(" ");
  // A phone hides the glyph (the [CI] brackets carry the interval), and with it the sentence that reads it.
  const glyphNote = domain
    ? "Beside each median peak, the line is its 95% bootstrap interval, the dot the median (solid where the group passes the rule) and the dashed rule the ordinary-date median, on one scale for every row."
    : null;
  return (
    <DataTable
      caption="Median 20-session peak and session-10 settle vs an ordinary date, tested against random and year-matched placebo dates"
      captionHidden
      rowKey={(row) => row.key}
      rowClass={(row) => (row.isPlacebo ? "is-reference" : passesRule(row) ? "passes-rule" : undefined)}
      dense
      exportName="supply-study"
      groups={[{ label: "", span: 2 }, { label: "Peak, sessions 0–19", span: 3 }, { label: "Settle, session 10", span: 3 }]}
      columns={[
        { key: "name", label: "Group", rowHeader: true, csv: (row) => row.name, className: "wrap-cell is-narrow", render: (row) => <span title={row.definition ?? undefined}>{row.name}</span> },
        {
          key: "n", label: "n", align: "right",
          render: (row) => (
            <span className="num">
              {fmtInt(row.n)}
              {row.overlaps?.length > 0 && (
                <span className="muted" title={`Windows share sessions: ${row.overlaps.map(pairText).join("; ")}`}>†</span>
              )}
            </span>
          ),
        },
        {
          key: "peak", label: "Median [95% CI]", title: "Median 20-session peak, with its 95% bootstrap interval", align: "right",
          csvLabel: "Median peak %",
          render: (row) => {
            const ci = peakInterval(row);
            return (
              <span className="peak-cell">
                <span className="num">{fmtSignedPct(row.peak)}</span>
                {ci && <span className="stat-range num">[{fmtNum(ci[0], 1)}, {fmtNum(ci[1], 1)}]</span>}
                <IntervalGlyph domain={domain} lo={ci?.[0]} hi={ci?.[1]} median={row.peak} placebo={study.placebo?.peak}
                  strong={!row.isPlacebo && passesRule(row)} />
              </span>
            );
          },
        },
        { key: "peakP", label: "vs placebo", csvLabel: "Peak p vs placebo", title: `Peak p: ${UNMATCHED_TITLE}`, align: "right", className: "wrap-head", render: pCell("peakP") },
        { key: "peakPYear", label: "year-matched", csvLabel: "Peak p year-matched", title: `Peak p: ${YEAR_MATCHED_TITLE}`, align: "right", className: "wrap-head", render: pCell("peakPYear") },
        { key: "settle", label: "Median", csvLabel: "Median settle %", title: "Median move to the session-10 close", align: "right", render: (row) => <span className="num">{fmtSignedPct(row.settle)}</span> },
        { key: "settleP", label: "vs placebo", csvLabel: "Settle p vs placebo", title: `Settle p: ${UNMATCHED_TITLE}`, align: "right", className: "wrap-head", render: pCell("settleP") },
        { key: "settlePYear", label: "year-matched", csvLabel: "Settle p year-matched", title: `Settle p: ${YEAR_MATCHED_TITLE}`, align: "right", className: "wrap-head", render: pCell("settlePYear") },
        ...SUPPLY_CSV_COLUMNS,
      ]}
      rows={rows}
      footnote={<>{footnote}{glyphNote && <span className="glyph-note">{` ${glyphNote}`}</span>}</>}
    />
  );
}

/**
 * The supply study's method as notes: the sentences that read as prose (a sentence that names a
 * payload field, e.g. p_value_vs_placebo, is left out; the table's column titles describe those).
 */
export const methodSentences = (method) => {
  const sentences = (asText(method) ?? "").split(/(?<=\.)\s+/).filter((sentence) => sentence && !/\b[a-z]+_[a-z_*]+/.test(sentence));
  return sentences.length ? `Supply shocks: ${tidyProse(sentences.join(" "))}` : null;
};

/** STDY: two side studies. Neither is a forecast; both are reported with their limits. */
export default function StudiesPanel({ data, research, researchStatus }) {
  const carry = asObject(research?.carry);
  const playbook = asObject(data?.supply_shock_playbook);
  const study = supplyStudy(research);
  const ledger = researchLedger(research);
  const rows = supplyRows(playbook);
  const priced = pricedInNote(playbook);
  const eventCount = num(playbook?.event_count);
  const firstYear = num(playbook?.first_event_year);
  const lastYear = num(playbook?.last_event_year);
  const eventSpan = firstYear != null && lastYear != null ? `${firstYear}–${lastYear}` : null;
  const carryRows = carry ? CARRY_SAMPLES
    .map((sample) => ({ ...sample, stats: asObject(carry[sample.key]) }))
    .filter((sample) => sample.stats) : [];
  const signSplit = asObject(carry?.sign_split?.full_sample);
  const hasStudy = Boolean(study?.groups.length);
  // The carry study and the placebo-controlled supply study are in research.json: while it loads, or
  // when it did not load, the panel says so where they go and claims no absence of either.
  const researchMissing = !research && (researchStatus === "loading" || researchStatus === "unavailable");

  if (!carryRows.length && !rows.length && !hasStudy && !researchMissing) return null;

  return (
    <Panel
      id="stdy"
      code="STDY"
      name="Side studies"
      title={studiesAssertion(carry, study)}
      fallbackTitle="Side studies"
      meta={[
        carryRows[0]?.stats?.n_months != null ? `carry: ${fmtInt(carryRows[0].stats.n_months)} contract months` : null,
        eventCount != null ? `supply shocks: ${fmtInt(eventCount)} events${eventSpan ? ` ${eventSpan}` : ""}` : null,
        study?.placebo?.n != null ? `${fmtInt(study.placebo.n)} placebo dates` : null,
        "descriptive; not traded",
      ]}
      notes={[
        asText(carry?.method) ? `Carry: ${tidyProse(carry.method)}` : null,
        hasStudy ? methodSentences(study.method) : null,
      ]}
      source={{
        artifact: ["research.json → carry", "research.json → supply_study", "data.json → supply_shock_playbook"],
        reproduce: [REPRODUCE.carry, REPRODUCE.supply],
      }}
      resetKey={data}
    >
      <div className="studies">
        {researchMissing && (
          <div className="study">
            <ResearchUnavailable status={researchStatus} what="the carry and placebo-controlled supply studies" />
          </div>
        )}
        {carryRows.length > 0 && (
          <div className="study">
            <StudyHead title={carryAssertion(carry) ?? "Carry"} detail={carryDetail(carry, ledger)} />
            <DataTable
              caption="Carry: second-nearby holding return when carry is above vs at or below its prior-months median"
              captionHidden
              rowKey={(row) => row.key}
              cardsBelow={640}
              dense
              exportName="carry"
              columns={[
                { key: "label", label: "Sample", rowHeader: true, render: (row) => <span title={row.stats.span ?? undefined}>{row.label}</span> },
                { key: "months", label: "Months", align: "right", csv: (row) => num(row.stats.n_months), render: (row) => fmtInt(row.stats.n_months) },
                {
                  key: "spread", label: "Above / below", title: "Mean monthly return (%) with carry above vs at or below the median of all prior months", align: "right",
                  csvLabel: "Mean return above / below median carry, %/month",
                  csv: (row) => `${num(row.stats.mean_ret_above_median_carry_pct ?? row.stats.mean_ret_backwardated_pct) ?? ""} / ${num(row.stats.mean_ret_below_median_carry_pct ?? row.stats.mean_ret_contango_pct) ?? ""}`,
                  render: (row) => (
                    <span className="num">
                      {fmtNum(row.stats.mean_ret_above_median_carry_pct ?? row.stats.mean_ret_backwardated_pct, 2)}
                      {" / "}
                      {fmtNum(row.stats.mean_ret_below_median_carry_pct ?? row.stats.mean_ret_contango_pct, 2)}
                    </span>
                  ),
                },
                { key: "p", label: "Spread p", title: "One-sided p-value of the spread", align: "right", csv: (row) => num(row.stats.spread_p_value_one_sided), render: (row) => <span className="num">{fmtPValue(row.stats.spread_p_value_one_sided)}</span> },
                { key: "rank", label: "Rank corr", title: "Rank correlation of carry with the next return", align: "right", csv: (row) => num(row.stats.carry_rank_corr), render: (row) => <span className="num">{fmtNum(row.stats.carry_rank_corr, 2)}</span> },
                {
                  key: "sharpe", label: "Sharpe vs B&H", title: "Carry-timed vs buy-and-hold Sharpe; p from the Ledoit-Wolf test of the difference", align: "right",
                  csvLabel: "Sharpe carry-timed / buy-and-hold",
                  csv: (row) => `${num(row.stats.carry_timed_sharpe) ?? ""} / ${num(row.stats.buy_hold_sharpe) ?? ""}`,
                  render: (row) => (
                    <span className="num">
                      {fmtNum(row.stats.carry_timed_sharpe, 2)} <span className="muted">/ {fmtNum(row.stats.buy_hold_sharpe, 2)}</span>
                      {row.stats.sharpe_difference?.ledoit_wolf_p_value != null && <span className="muted"> · p {fmtPValue(row.stats.sharpe_difference.ledoit_wolf_p_value)}</span>}
                    </span>
                  ),
                },
              ]}
              rows={carryRows}
              footnote={signSplit ? `By the sign of carry (backwardation vs contango) the full-sample spread is ${fmtNum(signSplit.mean_ret_carry_positive_pct, 2)} vs ${fmtNum(signSplit.mean_ret_carry_nonpositive_pct, 2)} %/month (${fmtP(signSplit.spread_p_value_one_sided)}); the two splits agree in ${fmtNum(signSplit.share_agreeing_with_median_rule_pct, 1)}% of months.` : null}
            />
            {asText(carry.verdict) && <p className="prose-note is-wide">{tidyProse(carry.verdict)}</p>}
          </div>
        )}

        {(hasStudy || rows.length > 0) && (
          <div className="study">
            <StudyHead
              title={(hasStudy ? supplyStudyAssertion(study) : null)
                ?? supplyPlaybookAssertion(playbook, { researchLoaded: Boolean(research) }) ?? "Supply shocks"}
              detail={hasStudy ? supplyStudyDetail(study) : []}
            />
            {hasStudy ? <SupplyStudyTable study={study} /> : (
              <DataTable
                caption={`Historical WTI price response by supply event type: median move across ${eventCount != null ? `${fmtInt(eventCount)} events` : "events"}${eventSpan ? ` ${eventSpan}` : ""}, from EIA daily spot`}
                captionHidden
                rowKey={(row) => row.key}
                dense
                columns={[
                  { key: "label", label: "Event type", rowHeader: true },
                  { key: "n", label: "Events", align: "right", render: (row) => fmtInt(row.n) },
                  { key: "peak", label: "Peak", align: "right", render: (row) => <span className="num">{fmtSignedPct(row.peak)}</span> },
                  {
                    key: "range", label: "Peak range", align: "right",
                    render: (row) => (row.peakMin != null ? <span className="num muted">{fmtSignedPct(row.peakMin, 0)} to {fmtSignedPct(row.peakMax, 0)}</span> : DASH),
                  },
                  { key: "settle", label: "Settled", align: "right", render: (row) => <span className="num">{fmtSignedPct(row.settle)}</span> },
                ]}
                rows={rows}
              />
            )}
            {hasStudy && <p className="prose-note is-wide">{supplyStudyNotes(study).join(" ")}</p>}
            {hasStudy && study.superseded.length > 0 && (
              <ul className="footnotes superseded">
                {study.superseded.map((text) => <li key={text}><span className="micro">Superseded</span> {text}</li>)}
              </ul>
            )}
            {priced && (
              <p className="prose-note">
                Is a big first day already priced in? {priced.text}
                {priced.definition && <span className="muted"> Definition: {priced.definition}.</span>}
              </p>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}
