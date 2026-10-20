import DataTable from "../components/DataTable";
import Panel, { REPRODUCE } from "../components/Panel";
import { carryAssertion, studiesAssertion, supplyPlaybookAssertion, supplyStudyAssertion } from "../lib/assertions";
import { DASH, fmtInt, fmtNum, fmtP, fmtPValue, fmtSigned, fmtSignedPct, num } from "../lib/format";
import { asObject, asText, researchLedger, supplyStudy } from "../lib/research";

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

/** The placebo-controlled event study (research.json supply_study). */
function SupplyStudyTable({ study }) {
  const rows = [
    ...(study.placebo ? [{
      key: "placebo", name: "Ordinary date (placebo)", n: study.placebo.n, peak: study.placebo.peak,
      settle: study.placebo.settle, isPlacebo: true,
    }] : []),
    ...study.groups.slice().sort((a, b) => (a.peakP ?? 1) - (b.peakP ?? 1)),
  ];
  return (
    <DataTable
      caption="Median 20-session peak and session-10 settle vs an ordinary date"
      captionHidden
      rowKey={(row) => row.key}
      rowClass={(row) => (row.isPlacebo ? "is-reference" : row.peakP != null && row.peakP < 0.05 ? "is-in-set" : undefined)}
      dense
      columns={[
        { key: "name", label: "Group", rowHeader: true, className: "wrap-cell", render: (row) => <span title={row.definition ?? undefined}>{row.name}</span> },
        { key: "n", label: "n", align: "right", render: (row) => fmtInt(row.n) },
        {
          key: "peak", label: "Peak", align: "right",
          render: (row) => (
            <span className="stat-cell">
              <span className="num">{fmtSignedPct(row.peak)}</span>
              {row.ciPeak?.length === 2 && row.ciPeak.every((v) => v != null) && <span className="stat-range num">[{fmtNum(row.ciPeak[0], 1)}, {fmtNum(row.ciPeak[1], 1)}]</span>}
            </span>
          ),
        },
        { key: "peakP", label: "p (peak)", title: "Resampling test vs placebo dates", align: "right", render: (row) => (row.isPlacebo ? <span className="muted">ref.</span> : <span className="num">{fmtPValue(row.peakP)}</span>) },
        { key: "settle", label: "Settle", align: "right", render: (row) => <span className="num">{fmtSignedPct(row.settle)}</span> },
        { key: "settleP", label: "p (settle)", align: "right", render: (row) => (row.isPlacebo ? <span className="muted">ref.</span> : <span className="num">{fmtPValue(row.settleP)}</span>) },
      ]}
      rows={rows}
      footnote={study.between?.peakP != null
        ? `Supply removal vs threat-only directly: peak difference ${fmtSigned(study.between.peakDiff, 1)} pts, ${fmtP(study.between.peakP)}; settle ${fmtP(study.between.settleP)}. p-values are not adjusted for the number of groups (see LEDG).`
        : null}
    />
  );
}

/** STDY: two side studies. Neither is a forecast; both are reported with their limits. */
export default function StudiesPanel({ data, research }) {
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

  if (!carryRows.length && !rows.length && !hasStudy) return null;

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
      source={{
        artifact: ["research.json → carry", "research.json → supply_study", "data.json → supply_shock_playbook"],
        reproduce: [REPRODUCE.carry, REPRODUCE.supply],
      }}
      resetKey={data}
    >
      <div className="split split--even">
        {carryRows.length > 0 && (
          <div className="study">
            <h3 className="study-title">{carryAssertion(carry, ledger) ?? "Carry"}</h3>
            <DataTable
              caption="Carry: second-nearby holding return when carry is above vs at or below its prior-months median"
              captionHidden
              rowKey={(row) => row.key}
              dense
              columns={[
                { key: "label", label: "Sample", rowHeader: true, render: (row) => <span title={row.stats.span ?? undefined}>{row.label}</span> },
                { key: "months", label: "Months", align: "right", render: (row) => fmtInt(row.stats.n_months) },
                {
                  key: "spread", label: "Above / below", title: "Mean monthly return (%) with carry above vs at or below the median of all prior months", align: "right",
                  render: (row) => (
                    <span className="num">
                      {fmtNum(row.stats.mean_ret_above_median_carry_pct ?? row.stats.mean_ret_backwardated_pct, 2)}
                      {" / "}
                      {fmtNum(row.stats.mean_ret_below_median_carry_pct ?? row.stats.mean_ret_contango_pct, 2)}
                    </span>
                  ),
                },
                { key: "p", label: "Spread p", title: "One-sided p-value of the spread", align: "right", render: (row) => <span className="num">{fmtPValue(row.stats.spread_p_value_one_sided)}</span> },
                { key: "rank", label: "Rank corr", title: "Rank correlation of carry with the next return", align: "right", render: (row) => <span className="num">{fmtNum(row.stats.carry_rank_corr, 2)}</span> },
                {
                  key: "sharpe", label: "Sharpe vs B&H", title: "Carry-timed vs buy-and-hold Sharpe; p from the Ledoit-Wolf test of the difference", align: "right",
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
            {asText(carry.verdict) && <p className="prose-note">{carry.verdict}</p>}
            {asText(carry.method) && <p className="prose-note is-muted">{carry.method}</p>}
          </div>
        )}

        {(hasStudy || rows.length > 0) && (
          <div className="study">
            <h3 className="study-title">{(hasStudy ? supplyStudyAssertion(study) : null) ?? supplyPlaybookAssertion(playbook) ?? "Supply shocks"}</h3>
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
            {hasStudy && study.conclusion && (
              <p className="prose-note">
                {(() => {
                  // The baseline sentence and the caveats: the per-group numbers are in the table.
                  const sentences = study.conclusion.split(/(?<=\.)\s/);
                  return [sentences[0], sentences.find((sentence) => /^caveat/i.test(sentence))].filter(Boolean).join(" ");
                })()}
              </p>
            )}
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
            {hasStudy && study.method && <p className="prose-note is-muted">{study.method.split(/(?<=\.)\s/)[0]}</p>}
          </div>
        )}
      </div>
    </Panel>
  );
}
