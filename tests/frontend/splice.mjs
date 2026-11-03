// The frontend fixture (tests/fixtures/research.json) predates the lab's newer blocks. These helpers splice
// the committed artifacts' blocks into it, so a test renders what the deployed research.json carries
// (scripts/build_research_bundle.py copies the lab and maps each ledger row the same way).
import { readSource } from "./load.mjs";

const committedLab = () => JSON.parse(readSource("data/vol_lab.json"));
const committedLedger = () => JSON.parse(readSource("data/research_ledger.json"));

/** A ledger row as research.json carries it (build_research_bundle._ledger). */
export const bundleLedgerRow = (row) => ({
  id: row.id, date: row.date, family: row.family, hypothesis: row.hypothesis, test: row.test,
  statistic_name: row.statistic?.name, statistic_value: row.statistic?.value,
  p_value: row.p_value, q_bh: row.q_value_bh, p_holm: row.p_holm,
  survives_bh: row.survives_bh_5pct, clears_t3: row.clears_hlz_t3,
  pre_specified: row.pre_specified, status: row.status, artifact: row.artifact?.path,
  entered_on: row.entered_on, online_position: row.online_position,
  online_alpha_lordpp: row.online_alpha_lordpp, online_rejected_lordpp: row.online_rejected_lordpp,
  online_alpha_saffron: row.online_alpha_saffron, online_rejected_saffron: row.online_rejected_saffron,
  registration: row.registration == null ? null
    : { on: row.registration.on, field: row.registration.field, withdrawn: row.registration.withdrawn },
});

/**
 * The fixture with the lab's regime blocks (regimes, the banded models' regime keys, every model's
 * qlike_by_regime, the weekly gap arrays) and the ledger's "forecast interval" rows, from the committed files.
 */
export const withRegimes = (research) => {
  const lab = committedLab();
  const ledger = committedLedger();
  const conformal = Object.fromEntries(Object.entries(research.vol_lab.conformal).map(([key, block]) => [key, {
    ...block,
    ...Object.fromEntries(Object.entries(lab.conformal[key] ?? {}).filter(([field]) => /regime|conditional_coverage|log_interval_score/.test(field))),
  }]));
  const byKey = new Map(lab.leaderboard.models.map((model) => [model.key, model]));
  const bandRows = ledger.rows.filter((row) => row.family === "forecast interval").map(bundleLedgerRow);
  return {
    ...research,
    vol_lab: {
      ...research.vol_lab,
      regimes: lab.regimes,
      conformal,
      leaderboard: {
        ...research.vol_lab.leaderboard,
        models: research.vol_lab.leaderboard.models.map((model) => ({ ...model, qlike_by_regime: byKey.get(model.key)?.qlike_by_regime })),
      },
      series: { ...research.vol_lab.series, ovx_rv_gap: lab.series.ovx_rv_gap, ovx_rv_gap_median: lab.series.ovx_rv_gap_median },
    },
    ledger: { ...research.ledger, rows: [...research.ledger.rows, ...bandRows] },
  };
};

export { committedLab, committedLedger };

/**
 * The fixture with the lab's stability blocks (ROADMAP 2.3): every model's Giacomini-White and fluctuation blocks,
 * the top-level stability block, the weekly rolling-t and cumulative series, and the ledger's two new families.
 * `lab` and `ledger` default to the committed artifacts; the fixture's models are kept, so a lab model the fixture
 * lacks is not spliced in.
 */
export const withStability = (research, { lab = committedLab(), ledger = committedLedger() } = {}) => {
  const byKey = new Map(lab.leaderboard.models.map((model) => [model.key, model]));
  const families = new Set(["conditional predictive ability", "stability over time"]);
  const names = (prefix) => Object.keys(lab.series).filter((key) => key.startsWith(prefix));
  return {
    ...research,
    vol_lab: {
      ...research.vol_lab,
      stability: lab.stability,
      leaderboard: {
        ...research.vol_lab.leaderboard,
        models: research.vol_lab.leaderboard.models.map((model) => ({
          ...model,
          gw_qlike_vs_har_iv: byKey.get(model.key)?.gw_qlike_vs_har_iv,
          fluctuation_qlike_vs_har_iv: byKey.get(model.key)?.fluctuation_qlike_vs_har_iv,
        })),
      },
      series: { ...research.vol_lab.series, ...Object.fromEntries([...names("fluct_t_"), ...names("cum_qlike_diff_")]
        .map((key) => [key, lab.series[key]])) },
    },
    ledger: { ...research.ledger, rows: [...research.ledger.rows,
      ...ledger.rows.filter((row) => families.has(row.family)).map(bundleLedgerRow)] },
  };
};

/**
 * The fixture with the lab's distribution block (ROADMAP 2.4): vol_lab.distribution and the ledger's two new families
 * ("predictive distribution" and "distribution calibration"), with the ledger summary's list of rows selected after a
 * search extended by the challenger's score rows, as research.json carries them.
 */
export const withDistribution = (research) => {
  const lab = committedLab();
  const ledger = committedLedger();
  const families = new Set(["predictive distribution", "distribution calibration"]);
  const rows = ledger.rows.filter((row) => families.has(row.family));
  const summary = research.ledger.summary ?? {};
  return {
    ...research,
    vol_lab: { ...research.vol_lab, distribution: lab.distribution },
    ledger: {
      ...research.ledger,
      summary: {
        ...summary,
        selected_after_search: [...new Set([...(summary.selected_after_search ?? []),
          ...rows.filter((row) => row.selected_after_search).map((row) => row.id)])],
      },
      rows: [...research.ledger.rows, ...rows.map(bundleLedgerRow)],
    },
  };
};

/** The fixture with the committed ledger as research.json carries it: every row, and the online view (ROADMAP 2.12). */
export const withCommittedLedger = (research) => {
  const ledger = committedLedger();
  return {
    ...research,
    ledger: { method: ledger.method, alpha: ledger.alpha, hlz_t: ledger.hlz_t, summary: ledger.summary,
      rows: ledger.rows.map(bundleLedgerRow) },
  };
};

/**
 * The committed ledger with two synthetic registered rows (ROADMAP 2.12) appended, as the ledger would write them: one
 * withdrawn (tested online at p = 1) and, after it, one waiting for its result, which blocks the online decisions after
 * it. If a committed registration already waits, both wait behind it (no level, no decision). The summary's counts and
 * lists extend the committed ones. Test data only: no such rows exist in the committed ledger.
 */
export const withRegistered = (research) => {
  const base = withCommittedLedger(research);
  const { summary, rows } = base.ledger;
  const online = summary.online;
  const n = online.rows_in_stream;
  const blocked = online.waiting_on != null;
  const family = "volatility forecast";
  const common = {
    date: null, family, test: "a two-sided test whose p-value the artifact stores", statistic_name: "statistic",
    statistic_value: null, p_value: null, q_bh: null, p_holm: null, survives_bh: null, clears_t3: null,
    pre_specified: false, status: "registered", artifact: "data/vol_lab.json",
  };
  const withdrawn = {
    ...common, id: "reg_withdrawn", hypothesis: "A registered hypothesis whose registration was withdrawn.",
    entered_on: "2026-11-10", online_position: n + 1,
    online_alpha_lordpp: blocked ? null : online.lordpp.next_level, online_rejected_lordpp: blocked ? null : false,
    online_alpha_saffron: blocked ? null : online.saffron.next_level, online_rejected_saffron: blocked ? null : false,
    registration: { on: "2026-11-10", field: "experiment.p_value", withdrawn: "abandoned before it ran" },
  };
  const pending = {
    ...common, id: "reg_pending", hypothesis: "A registered hypothesis that waits for its result.",
    entered_on: "2026-11-11", online_position: n + 2,
    online_alpha_lordpp: blocked ? null : online.lordpp.next_level / 2, online_rejected_lordpp: null,
    online_alpha_saffron: blocked ? null : online.saffron.next_level / 2, online_rejected_saffron: null,
    registration: { on: "2026-11-11", field: "experiment2.p_value", withdrawn: null },
  };
  const familySummary = summary.by_family[family];
  return {
    ...base,
    ledger: {
      ...base.ledger,
      rows: [...rows, withdrawn, pending],
      summary: {
        ...summary,
        rows: summary.rows + 2,
        by_status: { ...summary.by_status, registered: (summary.by_status.registered ?? 0) + 2 },
        by_family: {
          ...summary.by_family,
          [family]: { ...familySummary, rows: familySummary.rows + 2,
            by_status: { ...familySummary.by_status, registered: (familySummary.by_status.registered ?? 0) + 2 } },
        },
        registered: [...(summary.registered ?? []), "reg_pending"],
        registered_withdrawn: [...(summary.registered_withdrawn ?? []), "reg_withdrawn"],
        registered_before_result: [...(summary.registered_before_result ?? []), "reg_withdrawn", "reg_pending"],
        online: {
          ...online, rows_in_stream: n + 2, rows_decided: blocked ? online.rows_decided : n + 1,
          waiting_on: blocked ? online.waiting_on : "reg_pending",
          lordpp: { ...online.lordpp, next_level: null }, saffron: { ...online.saffron, next_level: null },
        },
      },
    },
  };
};
