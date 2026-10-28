import { useMemo } from "react";
import { getCoverageByYear, getMzForest } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import { INTERVAL_LEVEL_PCT, calibrationAssertion, calibrationDetail } from "../lib/assertions";
import { coverageByOvxTercile, mzWeekly } from "../lib/cone";
import { DASH, NBSP, fmtInt, fmtNum, fmtPValue, fmtPct, num } from "../lib/format";
import { asObject, coverageByYear, getLab, getRoles, getVol, leaderboardRows, modelLabels, shortLabel } from "../lib/research";
import { Facts, Legend, ResearchUnavailable } from "./common";

const SOURCE = {
  artifact: ["research.json → vol_lab.conformal", "vol_lab.leaderboard", "vol_lab.series", "data.json → vol_forecast.validation"],
  reproduce: [REPRODUCE.lab, REPRODUCE.validation],
};

// Heights the lazy figures reserve: CoverageByYear is fixed; MzForest grows by row, and a tagged row
// ("timing control") takes one more line. On a phone the wrapped model names and the tag add about
// three lines in all (measured at 390px).
const COVERAGE_HEIGHT = 220;
const MZ_ROW = 18;
const MZ_LINE = 12;
const mzHeight = (rows) => ({
  wide: rows.length * MZ_ROW + 30 + rows.filter((row) => row.tag).length * MZ_LINE,
  narrow: rows.length * MZ_ROW + 30 + 3 * MZ_LINE,
});

/** "p = 0.161" (or "p < 0.001"): the 3 decimals of the MZ table, so a fact and its table row agree. */
const pText = (value) => {
  const text = fmtPValue(value, { digits: 3 });
  if (text === DASH) return DASH;
  return /^[<>]/.test(text) ? `p${NBSP}${text}` : `p${NBSP}=${NBSP}${text}`;
};

/**
 * CALB: are the intervals and the forecast level calibrated? Three samples appear and each figure
 * names its own: the weekly production validation (data.json), the lab's weekly series (one
 * forecast per week, the same forecasts the validation scores) and every out-of-sample day in the
 * benchmark lab (research.json). The same forecasts give different Mincer-Zarnowitz slopes on the
 * weekly grid and on every day, and the caption shows both.
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
  const weeklyLab = useMemo(() => mzWeekly(lab?.series), [lab]);
  const terciles = useMemo(() => coverageByOvxTercile(lab?.series), [lab]);
  const conformal = asObject(lab?.conformal?.[champion]);
  const config = asObject(lab?.config?.conformal);
  const horizon = num(lab?.config?.horizon_sessions);
  const target = num(conformal?.target_coverage_pct) ?? INTERVAL_LEVEL_PCT;
  const challengerConformal = asObject(lab?.conformal?.[challenger]);
  const CoverageByYear = getCoverageByYear();
  const MzForest = getMzForest();
  const weeklyMz = asObject(validation?.har_mincer_zarnowitz);
  const recordMz = mzRows.find((row) => row.key === champion) ?? null;
  const labDays = num(lab?.leaderboard?.n_days) ?? num(lab?.sample?.n_days);
  const weeklyN = num(validation?.n);
  const tercileWeeks = terciles.reduce((sum, row) => sum + row.n, 0);

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
      weeklyLab ? {
        label: "MZ slope, weekly grid", value: fmtNum(weeklyLab.slope, 2),
        note: `lab series, ${fmtInt(weeklyLab.n)} wk${weeklyMz?.slope != null ? `; production ${fmtNum(weeklyMz.slope, 2)}` : ""}`
          + `${num(weeklyMz?.p_unbiased) != null ? `, unbiased ${pText(weeklyMz.p_unbiased)}` : ""}`,
      } : weeklyMz && {
        label: "MZ slope, weekly grid", value: fmtNum(weeklyMz.slope, 2),
        note: `${weeklyN != null ? `${fmtInt(weeklyN)} wk, ` : ""}production; unbiased ${pText(weeklyMz.p_unbiased)}`,
      },
      recordMz && {
        label: "MZ slope, every day", value: fmtNum(recordMz.slope, 2),
        note: `${labDays != null ? `${fmtInt(labDays)} days, ` : ""}lab; unbiased ${pText(recordMz.p)}`,
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
  // The sample the coverage chart plots: the out-of-sample days that have a band (the first band
  // needs a window of earlier misses), not every out-of-sample day, with the years it spans. The
  // count is given when every plotted model's band covers the same number of days.
  const dayCounts = series.map((s) => num(lab?.conformal?.[s.key]?.n_days));
  const bandDays = dayCounts.every((count) => count != null && count === dayCounts[0]) ? dayCounts[0] : null;
  const yearSpan = `${years[0].year}–${years[years.length - 1].year}`;
  const coverageSample = `${bandDays != null ? `${fmtInt(bandDays)} ` : ""}out-of-sample days with a band (${yearSpan})`;
  const weeklySlope = weeklyLab?.slope ?? num(weeklyMz?.slope);
  const caption = `Coverage near ${fmtNum(target, 0)}% overall is by construction; the evidence is the year-by-year spread and the interval score.`
    + (weeklySlope != null && recordMz
      ? ` Same forecasts, different samples: weekly grid ${fmtNum(weeklySlope, 2)}, every out-of-sample day ${fmtNum(recordMz.slope, 2)}.`
      : "");
  const notes = [
    `The band is re-sized from recent misses${horizon != null ? ` with ${horizon}-session delayed feedback` : ""}`
      + `${config?.gamma != null ? ` (γ ${fmtNum(config.gamma, 3)}${config?.window_days != null ? `, a ${fmtInt(config.window_days)}-day window` : ""})` : ""}, `
      + `which steers its long-run coverage to ${fmtNum(target, 0)}% by construction. The interval score is the band's width plus a penalty for each miss, in vol points.`,
    "Mincer-Zarnowitz regresses realized on forecast variance; a slope of 1 is unbiased. "
      + `The weekly-grid slope uses one forecast per week from the lab series${weeklyLab ? ` (${fmtInt(weeklyLab.n)} weeks)` : ""}`
      + `${weeklyMz?.slope != null ? `, the forecasts the production validation scores${weeklyN != null ? ` over ${fmtInt(weeklyN)} weeks` : ""}` : ""}; `
      + `the every-day slope uses every out-of-sample day in the benchmark lab${labDays != null ? ` (${fmtInt(labDays)} days)` : ""}. `
      + "The two samples score the same forecasts on different days, so the slopes differ; each figure names its sample.",
    terciles.length
      ? `OVX regimes: the lab's ${fmtInt(tercileWeeks)} weeks with OVX, realized vol and a band, split at the nearest-rank thirds of OVX; `
        + `each regime's coverage is the share of its weeks whose realized vol fell inside the weekly ${fmtNum(target, 0)}% band.`
      : null,
  ];

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
        yearSpan,
        "adaptive conformal (Gibbs-Candès 2021)",
      ]}
      caption={caption}
      notes={notes}
      source={SOURCE}
      resetKey={research}
      table={() => (
        <>
          <DataTable
            caption={`${fmtNum(target, 0)}% interval coverage by year, ${coverageSample} (%)`}
            rowKey={(row) => String(row.year)}
            dense
            exportName="calb-coverage-by-year"
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
          {terciles.length > 0 && (
            <DataTable
              caption={`${fmtNum(target, 0)}% band coverage by OVX tercile, lab weekly series (${fmtInt(tercileWeeks)} weeks)`}
              rowKey={(row) => String(row.tercile)}
              dense
              exportName="calb-coverage-by-ovx"
              columns={[
                { key: "tercile", label: "OVX range", rowHeader: true, render: (row) => `${fmtNum(row.ovxLow, 1)}–${fmtNum(row.ovxHigh, 1)}`, csv: (row) => `${row.ovxLow}–${row.ovxHigh}` },
                { key: "n", label: "Weeks", align: "right", render: (row) => fmtInt(row.n) },
                { key: "covered", label: "Inside", align: "right", render: (row) => fmtInt(row.covered) },
                { key: "coverage", label: "Coverage", align: "right", render: (row) => fmtPct(row.coverage), csvLabel: "Coverage (%)" },
              ]}
              rows={terciles}
            />
          )}
          <DataTable
            caption={`Mincer-Zarnowitz regression by model, every out-of-sample day${labDays != null ? ` (${fmtInt(labDays)} days)` : ""}, benchmark lab`}
            rowKey={(row) => row.key}
            dense
            exportName="calb-mz-every-day"
            initialSort={{ key: "slope", direction: "asc" }}
            columns={[
              {
                key: "label", label: "Model", rowHeader: true, sortable: true,
                render: (row) => <>{row.label}{row.tag && <span className="cell-note">{row.tag}</span>}</>,
                csv: (row) => (row.tag ? `${row.label} (${row.tag})` : row.label),
              },
              { key: "slope", label: "Slope", align: "right", sortable: true, render: (row) => fmtNum(row.slope, 2) },
              { key: "se", label: "SE", align: "right", sortable: true, render: (row) => fmtNum(row.se, 2) },
              { key: "p", label: "p (unbiased)", align: "right", sortable: true, render: (row) => fmtPValue(row.p, { digits: 3 }) },
            ]}
            rows={mzRows}
          />
        </>
      )}
    >
      {facts}
      <h3 className="figure-title">Coverage by year, {coverageSample}</h3>
      <Legend items={series.map((s, index) => ({ tone: s.tone, label: s.label, shape: index === 0 ? "dot" : "ring" }))} />
      <LazyMount minHeight={COVERAGE_HEIGHT} label="coverage chart">
        <CoverageByYear series={series} target={target} />
      </LazyMount>
      {terciles.length > 0 && (
        <>
          <h3 className="figure-title">Coverage by OVX regime, lab weekly band ({fmtInt(tercileWeeks)} weeks)</h3>
          <Facts className="facts--row calb-regimes" items={terciles.map((row) => ({
            label: <>OVX <span className="nowrap">{fmtNum(row.ovxLow, 1)}–{fmtNum(row.ovxHigh, 1)}</span></>,
            value: fmtPct(row.coverage),
            note: `of ${fmtInt(row.n)} wk`,
          }))} />
        </>
      )}
      {mzRows.length > 0 && (
        <>
          <h3 className="figure-title">Mincer-Zarnowitz slope by model, every out-of-sample day{labDays != null ? ` (${fmtInt(labDays)} days)` : ""}, ±1.96 SE</h3>
          <LazyMount minHeight={mzHeight(mzRows)} label="calibration chart">
            <MzForest rows={mzRows} />
          </LazyMount>
        </>
      )}
    </Panel>
  );
}
