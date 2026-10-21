import { useMemo } from "react";
import { getCoverageByYear, getMzForest } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import { INTERVAL_LEVEL_PCT, calibrationAssertion, calibrationDetail } from "../lib/assertions";
import { DASH, fmtInt, fmtNum, fmtP, fmtPValue, fmtPct, num } from "../lib/format";
import { asObject, coverageByYear, getLab, getRoles, getVol, leaderboardRows, modelLabels, shortLabel } from "../lib/research";
import { Facts, Legend, ResearchUnavailable } from "./common";

const SOURCE = {
  artifact: ["research.json → vol_lab.conformal", "vol_lab.leaderboard", "data.json → vol_forecast.validation"],
  reproduce: [REPRODUCE.lab, REPRODUCE.validation],
};

/**
 * CALB: are the intervals and the forecast level calibrated? Two samples appear and each figure
 * names its own: the weekly production validation (data.json) and every out-of-sample day in the
 * benchmark lab (research.json).
 */
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
      tag: row.timingControl ? "timing control" : null,
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
  const recordMz = mzRows.find((row) => row.key === champion) ?? null;
  const labDays = num(lab?.leaderboard?.n_days) ?? num(lab?.sample?.n_days);
  const weeklyN = num(validation?.n);

  const facts = (
    <Facts items={[
      {
        label: "Weekly coverage", value: fmtPct(validation?.interval_80_coverage_pct),
        note: `${validation?.interval_80_n != null ? `${fmtInt(validation.interval_80_n)} wk, ` : ""}production validation`,
      },
      conformal && { label: "Daily coverage", value: fmtPct(conformal.coverage_pct), note: `${fmtInt(conformal.n_days)} days, benchmark lab` },
      conformal && {
        label: "Interval score", value: `${fmtNum(conformal.interval_score_pct, 2)} vol pts`,
        note: `lower is better${challengerConformal ? `; challenger ${fmtNum(challengerConformal.interval_score_pct, 2)}` : ""}`,
      },
      conformal && { label: "Median hi/lo ratio", value: fmtNum(conformal.median_hi_lo_ratio, 2) },
      weeklyMz && {
        label: "MZ slope, weekly", value: fmtNum(weeklyMz.slope, 2),
        note: `${weeklyN != null ? `${fmtInt(weeklyN)} wk, ` : ""}production; unbiased ${fmtP(weeklyMz.p_unbiased) ?? DASH}`,
      },
      recordMz && {
        label: "MZ slope, every day", value: fmtNum(recordMz.slope, 2),
        note: `${labDays != null ? `${fmtInt(labDays)} days, ` : ""}lab; unbiased ${fmtP(recordMz.p) ?? DASH}`,
      },
    ]} />
  );

  if (!series.length) {
    return (
      <Panel id="calb" code="CALB" name="Calibration" span="half" title={null}
        fallbackTitle={validation?.interval_80_coverage_pct != null ? `In the weekly production validation the ${INTERVAL_LEVEL_PCT}% band covered ${fmtPct(validation.interval_80_coverage_pct)} of ${fmtInt(validation.interval_80_n)} weeks` : "Calibration"}
        source={SOURCE}>
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
        ...calibrationDetail(lab, champion),
        `${years[0].year}–${years[years.length - 1].year}`,
        "adaptive conformal (Gibbs-Candès 2021)",
        config?.window_days != null ? `window ${fmtInt(config.window_days)} days` : null,
      ]}
      caption={`The band is re-sized from recent misses with 5-session delayed feedback${config?.gamma != null ? ` (γ ${fmtNum(config.gamma, 3)})` : ""}, which steers its long-run coverage to ${fmtNum(target, 0)}% by construction: coverage near ${fmtNum(target, 0)}% overall is expected by design, and the evidence is how evenly it holds year by year and the interval score (width plus a penalty for each miss, in vol points). Mincer-Zarnowitz regresses realized on forecast variance; a slope of 1 is unbiased. The weekly production validation and every out-of-sample day in the benchmark lab give different slopes for the same model, so each figure names its sample.`}
      source={SOURCE}
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
            caption={`Mincer-Zarnowitz regression by model, every out-of-sample day${labDays != null ? ` (${fmtInt(labDays)} days)` : ""}, benchmark lab`}
            rowKey={(row) => row.key}
            dense
            initialSort={{ key: "slope", direction: "asc" }}
            columns={[
              {
                key: "label", label: "Model", rowHeader: true, sortable: true,
                render: (row) => <>{row.label}{row.tag && <span className="cell-note">{row.tag}</span>}</>,
              },
              { key: "slope", label: "Slope", align: "right", sortable: true, render: (row) => fmtNum(row.slope, 3) },
              { key: "se", label: "SE", align: "right", sortable: true, render: (row) => fmtNum(row.se, 3) },
              { key: "p", label: "p (unbiased)", align: "right", sortable: true, render: (row) => fmtPValue(row.p) },
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
          <h3 className="figure-title">Mincer-Zarnowitz slope by model, every out-of-sample day{labDays != null ? ` (${fmtInt(labDays)} days)` : ""}, ±1.96 SE</h3>
          <LazyMount minHeight={mzRows.length * 18 + 30} label="calibration chart">
            <MzForest rows={mzRows} />
          </LazyMount>
        </>
      )}
    </Panel>
  );
}
