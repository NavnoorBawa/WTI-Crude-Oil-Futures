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
 */
export const withStability = (research) => {
  const lab = committedLab();
  const ledger = committedLedger();
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
