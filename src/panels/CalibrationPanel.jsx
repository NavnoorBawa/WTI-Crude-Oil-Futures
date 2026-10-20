import { useMemo } from "react";
import { getCoverageByYear, getMzForest } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel from "../components/Panel";
import { INTERVAL_LEVEL_PCT, calibrationAssertion } from "../lib/assertions";
import { fmtInt, fmtNum, fmtP, fmtPct, num } from "../lib/format";
import { asObject, coverageByYear, getLab, getRoles, getVol, leaderboardRows, modelLabels, shortLabel } from "../lib/research";
import { Facts, Legend, ResearchUnavailable } from "./common";

/** CALB: are the intervals and the forecast level calibrated? */
export default function CalibrationPanel({ data, research, researchStatus }) {
  const lab = getLab(research);
  const { validation } = getVol(data);
  const labels = useMemo(() => modelLabels(lab), [lab]);
  const { champion, challenger } = getRoles(lab, null);
  const series = useMemo(() => [
    champion && { key: champion, tone: "model", label: shortLabel(labels.get(champion) ?? champion), points: coverageByYear(lab, champion) },
    challenger && { key: challenger, tone: "alt", label: shortLabel(labels.get(challenger) ?? challenger), points: coverageByYear(lab, challenger) },
  ].filter((item) => item && item.points.length), [lab, labels, champion, challenger]);
  const mzRows = useMemo(() => leaderboardRows(lab)
    .filter((row) => row.mzSlope != null)
    .sort((a, b) => a.mzSlope - b.mzSlope)
    .map((row) => ({
      key: row.key,
      label: shortLabel(row.label),
      slope: row.mzSlope,
      se: row.mzSe,
      p: row.mzP,
      tone: row.role === "record" ? "model" : row.role === "challenger" ? "alt" : "neutral",
    })), [lab]);
  const conformal = asObject(lab?.conformal?.[champion]);
  const config = asObject(lab?.config?.conformal);
  const target = num(conformal?.target_coverage_pct) ?? INTERVAL_LEVEL_PCT;
  const challengerConformal = asObject(lab?.conformal?.[challenger]);
  const CoverageByYear = getCoverageByYear();
  const MzForest = getMzForest();
  const weeklyMz = asObject(validation?.har_mincer_zarnowitz);

  const facts = (
    <Facts items={[
      { label: "Weekly coverage", value: fmtPct(validation?.interval_80_coverage_pct), note: validation?.interval_80_n != null ? `${fmtInt(validation.interval_80_n)} wk` : null },
      conformal && { label: "Daily coverage", value: fmtPct(conformal.coverage_pct), note: `${fmtInt(conformal.n_days)} days` },
      conformal && { label: "Interval score", value: fmtNum(conformal.interval_score_pct, 2), note: challengerConformal ? `challenger ${fmtNum(challengerConformal.interval_score_pct, 2)}` : null },
      conformal && { label: "Median hi/lo ratio", value: fmtNum(conformal.median_hi_lo_ratio, 2) },
      weeklyMz && { label: "MZ slope, weekly", value: fmtNum(weeklyMz.slope, 2), note: `unbiased ${fmtP(weeklyMz.p_unbiased) ?? ""}` },
    ]} />
  );

  if (!series.length) {
    return (
      <Panel id="calb" code="CALB" name="Calibration" span="half" title={null}
        fallbackTitle={validation?.interval_80_coverage_pct != null ? `The ${INTERVAL_LEVEL_PCT}% band covered ${fmtPct(validation.interval_80_coverage_pct)} of ${fmtInt(validation.interval_80_n)} weeks` : "Calibration"}
        source={{ artifact: ["data.json → vol_forecast.validation", "research.json → vol_lab.conformal"] }}>
        {facts}
        <ResearchUnavailable status={researchStatus} what="coverage by year" />
      </Panel>
    );
  }

  const years = series[0].points;
  return (
    <Panel
      id="calb"
      code="CALB"
      name="Calibration"
      span="half"
      title={calibrationAssertion(lab, champion)}
      fallbackTitle="Interval coverage by year"
      meta={[
        conformal?.n_days != null ? `n ${fmtInt(conformal.n_days)} days` : null,
        `${years[0].year}–${years[years.length - 1].year}`,
        "adaptive conformal (Gibbs-Candès 2021)",
        config?.window_days != null ? `window ${fmtInt(config.window_days)} days` : null,
      ]}
      caption={`The band is re-sized from recent misses with 5-session delayed feedback${config?.gamma != null ? ` (γ ${fmtNum(config.gamma, 3)})` : ""}. Mincer-Zarnowitz regresses realized on forecast variance; a slope of 1 is unbiased.`}
      source={{ artifact: ["research.json → vol_lab.conformal", "vol_lab.leaderboard"] }}
      resetKey={research}
      table={() => (
        <>
          <DataTable
            caption={`${fmtNum(target, 0)}% interval coverage by year (%)`}
            rowKey={(row) => String(row.year)}
            dense
            columns={[
              { key: "year", label: "Year", rowHeader: true, sortable: true },
              ...series.map((s, index) => ({
                key: s.key, label: s.label, align: "right", sortable: true,
                sortValue: (row) => row.values[index], render: (row) => fmtNum(row.values[index], 1),
              })),
            ]}
            rows={years.map((point) => ({
              year: point.year,
              values: series.map((s) => s.points.find((p) => p.year === point.year)?.coverage ?? null),
            }))}
          />
          <DataTable
            caption="Mincer-Zarnowitz regression by model"
            rowKey={(row) => row.key}
            dense
            initialSort={{ key: "slope", direction: "asc" }}
            columns={[
              { key: "label", label: "Model", rowHeader: true, sortable: true },
              { key: "slope", label: "Slope", align: "right", sortable: true, render: (row) => fmtNum(row.slope, 3) },
              { key: "se", label: "SE", align: "right", sortable: true, render: (row) => fmtNum(row.se, 3) },
              { key: "p", label: "p (unbiased)", align: "right", sortable: true, render: (row) => fmtP(row.p) ?? "—" },
            ]}
            rows={mzRows}
          />
        </>
      )}
    >
      {facts}
      <h3 className="figure-title">Coverage by year</h3>
      <Legend items={series.map((s, index) => ({ tone: s.tone, label: s.label, shape: index === 0 ? "dot" : "ring" }))} />
      <LazyMount minHeight={220} label="coverage chart">
        <CoverageByYear series={series} target={target} />
      </LazyMount>
      {mzRows.length > 0 && (
        <>
          <h3 className="figure-title">Mincer-Zarnowitz slope, ±1.96 SE</h3>
          <LazyMount minHeight={mzRows.length * 18 + 30} label="calibration chart">
            <MzForest rows={mzRows} />
          </LazyMount>
        </>
      )}
    </Panel>
  );
}
