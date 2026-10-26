import { useMemo } from "react";
import { getDmGridStrip, getRollingGap, getYearlyGapRows } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel from "../components/Panel";
import { stabilityAssertion } from "../lib/assertions";
import { gapLimit, recentWindows, rollingGap, weeklyPairs, yearlyGaps } from "../lib/comparison";
import { fmtInt, fmtNum, fmtSpan } from "../lib/format";
import {
  OVX_ONLY_KEY, asArray, getLab, getRoles, leaderboardRows, modelLabels, shortLabel, yearlyQlike, yearsBetter,
} from "../lib/research";
import { Facts, Legend, ResearchUnavailable } from "./common";

// The comparison models besides the challenger, in the order the yearly rows show them: the
// regression on OVX alone (the comparison that matters), raw implied vol and the best return-only
// model (GJR-GARCH). Each is shown when the payload's yearly rows carry it.
const COMPARISON_KEYS = [OVX_ONLY_KEY, "raw_ovx", "gjr"];
/** Trailing windows of the weekly series compared in the recent-windows row, and the rolling window. */
const RECENT_WEEKS = [26, 52, 156];
const ROLLING_WEEKS = 52;

/** STAB: does the ranking hold across years, recent weeks and the 5-session sampling grids? */
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
      ...COMPARISON_KEYS.map((key) => ({ key, tone: "ink" })),
    ].filter((model) => model && available.has(model.key))
      .map((model) => ({ ...model, label: shortLabel(labels.get(model.key) ?? model.key) }));
  }, [lab, labels, champion, challenger]);
  const years = useMemo(() => yearlyQlike(lab, models.map((model) => model.key)), [lab, models]);
  const record = models.find((model) => model.key === champion) ?? null;
  // One row per comparison, the OVX-only regression first and the challenger last.
  const gapRows = useMemo(() => (record ? yearlyGaps(years, record.key, [
    ...models.filter((model) => COMPARISON_KEYS.includes(model.key)),
    ...models.filter((model) => model.key === challenger),
  ]) : []), [years, models, record, challenger]);
  const scale = useMemo(() => gapLimit(gapRows.flatMap((row) => row.gaps.map((gap) => gap.value))), [gapRows]);
  const pairs = useMemo(() => weeklyPairs(lab, champion), [lab, champion]);
  const recent = useMemo(() => recentWindows(pairs, RECENT_WEEKS), [pairs]);
  const rolling = useMemo(() => rollingGap(pairs, ROLLING_WEEKS), [pairs]);
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
      tone: row.role === "challenger" ? "alt" : row.info.size === 0 ? "faint" : "ink",
    })), [lab]);
  const YearlyGapRows = getYearlyGapRows();
  const RollingGap = getRollingGap();
  const DmGridStrip = getDmGridStrip();

  if (!years.length || !record || models.length < 2) {
    return (
      <Panel id="stab" code="STAB" name="Stability" span="half" title={null} fallbackTitle="Stability by year"
        source={{ artifact: "research.json → vol_lab.yearly" }}>
        <ResearchUnavailable status={researchStatus} what="the yearly comparison" />
      </Panel>
    );
  }

  const recordName = record.label;
  const first = years[0].year;
  const last = years[years.length - 1].year;
  const wins = models
    .filter((model) => COMPARISON_KEYS.includes(model.key))
    .map((model) => ({ ...model, ...yearsBetter(years, record.key, model.key) }))
    .filter((model) => model.total > 0);
  const cut = scale.clipped > 0;
  // Years with markedly fewer days than a full one (the sample's first and last), named in the notes.
  const fullYear = Math.max(0, ...years.map((row) => row.nDays ?? 0));
  const partial = years.filter((row) => row.nDays != null && row.nDays < (9 / 10) * fullYear);
  return (
    <Panel
      id="stab"
      code="STAB"
      name="Stability"
      span="half"
      title={stabilityAssertion(years, { champion, challenger, bench: OVX_ONLY_KEY, labels })}
      fallbackTitle="Yearly QLIKE by model"
      meta={[
        `${years.length} years`,
        `${first}–${last}`,
        "QLIKE per calendar year",
        ...wins.map((model) => `vs ${model.label}: ${model.wins}/${model.total} years`),
      ]}
      caption={gapRows.length ? `Each bar is one model's QLIKE minus ${recordName}'s in one calendar year, on one scale for every row, so a bar above its line is a year ${recordName} had the lower loss${cut ? `; ${fmtInt(scale.clipped)} bars beyond ±${fmtNum(scale.limit, 3)} are cut at the edge and marked with a break` : ""}.` : null}
      notes={[
        `The yearly bars use every out-of-sample day of each calendar year (overlapping 5-session targets), as the leaderboard does${partial.length ? `; partial years: ${partial.map((row) => `${row.year} (${fmtInt(row.nDays)} days)`).join(", ")}, against up to ${fmtInt(fullYear)} in a full one` : ""}. Each bar's two QLIKE values are in its tooltip and in the table view.`,
        pairs.length
          ? `Recent windows and the rolling gap use the weekly series instead: one forecast a week on the first 5-session sampling grid (grid 0), ${fmtInt(pairs.length)} weeks (${fmtSpan(pairs[0].date, pairs[pairs.length - 1].date)}), each scored with the same QLIKE, so their values differ from the every-day figures in the leaderboard.`
          : null,
        "DM t by sampling grid: the Diebold-Mariano t of each model against the record over the full sample (large dot) and on each 5-session sampling grid (small dots); each grid scores every fifth session, starting on a different day. The shaded band is |t| < 1.96, where the difference is not significant at 5%.",
      ].filter(Boolean)}
      source={{ artifact: ["research.json → vol_lab.yearly", "vol_lab.series", "vol_lab.leaderboard"] }}
      resetKey={research}
      table={() => (
        <DataTable
          caption={`QLIKE by year, ${first}–${last} (lower is better)`}
          rowKey={(row) => String(row.year)}
          dense
          exportName="stability-by-year"
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
      {gapRows.length > 0 && (
        <>
          <h3 className="figure-title">Yearly QLIKE gap to {recordName}, up = {recordName} lower</h3>
          {/* The chart's own height, yearlyGapHeight() in charts/stabilityCharts.jsx: 230 px for four rows. */}
          <LazyMount minHeight={{ wide: gapRows.length * 52 + 22, narrow: gapRows.length * 52 + 22 }} label="yearly comparison">
            <YearlyGapRows rows={gapRows} recordLabel={recordName} limit={scale.limit} />
          </LazyMount>
        </>
      )}
      {recent.length > 0 && (
        <>
          <h3 className="figure-title">Recent windows: weekly series (grid 0), not the every-day sample</h3>
          <Facts className="facts--row recent-windows" items={recent.map((span) => ({
            label: `Last ${span.weeks} weeks`,
            value: `${fmtNum(span.record, 3)} vs ${fmtNum(span.challenger, 3)}`,
            note: `QLIKE, ${recordName} vs challenger`,
          }))} />
        </>
      )}
      {rolling.length > 1 && (
        <>
          <h3 className="figure-title">Rolling {ROLLING_WEEKS}-week QLIKE gap, challenger minus {recordName}</h3>
          <LazyMount minHeight={{ wide: 180, narrow: 180 }} label="rolling comparison">
            <RollingGap points={rolling} recordLabel={recordName} weeks={ROLLING_WEEKS} />
          </LazyMount>
        </>
      )}
      <h3 className="figure-title">DM t vs {recordName}, by sampling grid</h3>
      <Legend items={[
        { tone: "alt", label: "challenger", shape: "dot" },
        { tone: "ink", label: "uses OVX or range", shape: "dot" },
        { tone: "faint", label: "returns only", shape: "dot" },
      ]} />
      {/* A tagged row (the timing control) takes a second 10px line on wide screens too. */}
      <LazyMount minHeight={{ wide: dmRows.length * 20 + dmRows.filter((row) => row.tag).length * 10 + 34, narrow: dmRows.length * 22 + 40 }}
        label="grid statistics">
        <DmGridStrip rows={dmRows} />
      </LazyMount>
    </Panel>
  );
}
