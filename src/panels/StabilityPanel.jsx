import { useMemo } from "react";
import { getDmGridStrip, getYearlySmallMultiples } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel from "../components/Panel";
import { stabilityAssertion } from "../lib/assertions";
import { fmtInt, fmtNum } from "../lib/format";
import { asArray, getLab, getRoles, leaderboardRows, modelLabels, shortLabel, yearlyQlike } from "../lib/research";
import { Legend, ResearchUnavailable } from "./common";

// The two comparison models besides the record and the challenger: the best return-only model
// (GJR-GARCH) and raw implied vol. Shown when the payload's yearly rows carry them.
const COMPARISON_KEYS = [
  { key: "gjr", tone: "bench" },
  { key: "raw_ovx", tone: "implied" },
];

/** STAB: does the ranking hold across years and across the 5-session sampling grids? */
export default function StabilityPanel({ research, researchStatus }) {
  const lab = getLab(research);
  const labels = useMemo(() => modelLabels(lab), [lab]);
  const { champion, challenger } = getRoles(lab, null);
  const models = useMemo(() => {
    const available = new Set(Object.keys(asArray(lab?.yearly)[0] ?? {})
      .filter((key) => key.endsWith("_qlike"))
      .map((key) => key.slice(0, -"_qlike".length)));
    return [
      champion && { key: champion, tone: "model" },
      challenger && { key: challenger, tone: "alt" },
      ...COMPARISON_KEYS,
    ].filter((model) => model && available.has(model.key))
      .map((model) => ({ ...model, label: shortLabel(labels.get(model.key) ?? model.key) }));
  }, [lab, labels, champion, challenger]);
  const years = useMemo(() => yearlyQlike(lab, models.map((model) => model.key)), [lab, models]);
  // A timing control is drawn last and tagged under its name: a comparison, not a ranked model.
  const dmRows = useMemo(() => leaderboardRows(lab)
    .filter((row) => row.role !== "record" && row.dmStat != null)
    .sort((a, b) => Number(a.timingControl) - Number(b.timingControl) || b.dmStat - a.dmStat)
    .map((row) => ({
      key: row.key,
      label: shortLabel(row.label),
      tag: row.timingControl ? "timing control" : null,
      dmStat: row.dmStat,
      dmMin: row.dmMin,
      dmMax: row.dmMax,
      grid: row.dmByGrid,
      tone: row.role === "challenger" ? "alt" : row.info.size === 0 ? "neutral" : "truth",
    })), [lab]);
  const YearlySmallMultiples = getYearlySmallMultiples();
  const DmGridStrip = getDmGridStrip();

  if (!years.length || models.length < 2) {
    return (
      <Panel id="stab" code="STAB" name="Stability" span="half" title={null} fallbackTitle="Stability by year"
        source={{ artifact: "research.json → vol_lab.yearly" }}>
        <ResearchUnavailable status={researchStatus} what="the yearly comparison" />
      </Panel>
    );
  }

  const first = years[0].year;
  const last = years[years.length - 1].year;
  return (
    <Panel
      id="stab"
      code="STAB"
      name="Stability"
      span="half"
      title={stabilityAssertion(years, { champion, challenger, bench: "gjr", labels })}
      fallbackTitle="Yearly QLIKE by model"
      meta={[`${years.length} years`, `${first}–${last}`, "QLIKE per calendar year", "DM t by 5-session sampling grid"]}
      caption={`Faint ticks repeat ${models[0].label} in each panel. Below: DM t vs ${models[0].label} over the full sample (large dot) and on each 5-session sampling grid (small dots; each grid scores every fifth session from a different start day); the shaded band is |t| < 1.96.`}
      source={{ artifact: ["research.json → vol_lab.yearly", "vol_lab.leaderboard"] }}
      resetKey={research}
      table={() => (
        <DataTable
          caption={`QLIKE by year, ${first}–${last} (lower is better)`}
          rowKey={(row) => String(row.year)}
          dense
          columns={[
            { key: "year", label: "Year", rowHeader: true, sortable: true },
            { key: "nDays", label: "Days", align: "right", sortable: true, render: (row) => fmtInt(row.nDays) },
            ...models.map((model) => ({
              key: model.key, label: model.label, align: "right", sortable: true,
              sortValue: (row) => row.values[model.key], render: (row) => fmtNum(row.values[model.key], 3),
            })),
          ]}
          rows={years}
        />
      )}
    >
      <h3 className="figure-title">Yearly QLIKE</h3>
      <LazyMount minHeight={170} label="yearly comparison">
        <YearlySmallMultiples years={years} models={models} />
      </LazyMount>
      <h3 className="figure-title">DM t vs {models[0].label}, by sampling grid</h3>
      <Legend items={[
        { tone: "alt", label: "challenger", shape: "dot" },
        { tone: "truth", label: "uses OVX or range", shape: "dot" },
        { tone: "neutral", label: "returns only", shape: "dot" },
      ]} />
      <LazyMount minHeight={dmRows.length * 20 + 34} label="grid statistics">
        <DmGridStrip rows={dmRows} />
      </LazyMount>
    </Panel>
  );
}
