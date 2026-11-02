import { useCallback, useMemo } from "react";
import { useViewParam } from "../app/viewState";
import { getCumulativeRows, getDmGridStrip, getFluctuationRows, getYearlyGapRows } from "../charts/lazy";
import DataTable from "../components/DataTable";
import keepHyphenated from "../components/keepHyphenated";
import LazyMount from "../components/LazyMount";
import Panel from "../components/Panel";
import Segmented from "../components/Segmented";
import { withTerms } from "../components/Term";
import { stabilityAssertion } from "../lib/assertions";
import { gapLimit, recentWindows, weeklyPairs, yearlyGaps } from "../lib/comparison";
import { DASH, fmtDate, fmtInt, fmtNum, fmtPValue, fmtSigned, fmtSpan } from "../lib/format";
import {
  OVX_ONLY_KEY, asArray, getLab, getRoles, leaderboardRows, modelLabels, researchLedger, shortLabel, yearlyQlike, yearsBetter,
} from "../lib/research";
import {
  STAB_VIEWS, cumulativeCaption, cumulativeRows, fluctuationCaption, fluctuationReading, fluctuationRows, fluctuationSetup,
  gwNote, stabilityTestRows,
} from "../lib/stability";
import { Facts, Legend, ResearchUnavailable } from "./common";

// The comparison models besides the challenger, in the order the yearly rows show them: the
// regression on OVX alone (the comparison that matters), raw implied vol and the best return-only
// model (GJR-GARCH). Each is shown when the payload's yearly rows carry it.
const COMPARISON_KEYS = [OVX_ONLY_KEY, "raw_ovx", "gjr"];
/** Trailing windows of the weekly series compared in the recent-windows row. */
const RECENT_WEEKS = [26, 52, 156];
const VIEW_VALUES = STAB_VIEWS.map((view) => view.value);
const p3 = (value) => fmtPValue(value, { digits: 3 });
const excluded = (row, q) => (row.challenger ? "excluded" : p3(q));

/** STAB's table view of the tests over time and by state: one row per model, the timing control last. */
export function StabilityTests({ rows }) {
  const name = { key: "label", label: "Model", rowHeader: true, render: (row) => (row.timingControl ? `${row.label} (timing control)` : row.label) };
  return (
    <>
      <DataTable
        caption="Tests against the model of record, every out-of-sample day: Giacomini-White (conditional part) and the fluctuation test"
        rowKey={(row) => row.key} dense exportName="stability-tests"
        columns={[
          name,
          { key: "dmStat", label: "DM t", align: "right", render: (row) => fmtNum(row.dmStat, 2) },
          { key: "gwP", label: "GW p", title: "Giacomini-White, conditional part (chi-squared, 3 df)", align: "right", render: (row) => p3(row.gwP) },
          { key: "gwQ", label: "q", align: "right", render: (row) => excluded(row, row.gwQ), csv: (row) => (row.challenger ? "excluded" : row.gwQ) },
          { key: "sup", label: "Max |t|", title: "Fluctuation path's largest |t| (sign: + model lower)", align: "right", render: (row) => fmtSigned(row.sup, 2) },
          { key: "at", label: "Window ending", render: (row) => (row.at ? fmtDate(row.at) : DASH) },
          { key: "level", label: "At μ = 0.2", hideBelow: 640, render: (row) => row.level ?? DASH },
          { key: "p", label: "p, μ = 0.2", align: "right", hideBelow: 640, render: (row) => p3(row.p) },
          { key: "adjusted", label: "p, adjusted", title: "Adjusted for the choice among the window lengths", align: "right", render: (row) => p3(row.adjusted) },
          { key: "flQ", label: "q", csvLabel: "q (fluctuation)", align: "right", render: (row) => excluded(row, row.flQ), csv: (row) => (row.challenger ? "excluded" : row.flQ) },
          { key: "gridsOut", label: "Grids out", align: "right", hideBelow: 640, render: (row) => (row.gridsOut == null ? DASH : `${row.gridsOut}/5`) },
        ]}
        rows={rows}
      />
      <DataTable
        caption="Context, not in the ledger: the Giacomini-White form with the constant (it mostly repeats DM), the fluctuation test's robustness forms and its other window lengths (μ = 0.1 is anti-conservative)"
        rowKey={(row) => row.key} dense exportName="stability-context"
        columns={[
          name,
          { key: "gwFullP", label: "GW with constant, p", align: "right", render: (row) => p3(row.gwFullP) },
          { key: "bartlett", label: "p, 40 Bartlett lags", align: "right", render: (row) => p3(row.bartlett) },
          { key: "fullSample", label: "p, full-sample variance", align: "right", hideBelow: 640, render: (row) => p3(row.fullSample) },
          ...["mu_0_1", "mu_0_3", "mu_0_4", "mu_0_5"].map((key) => ({
            key, label: `p, μ = ${key.slice(3).replace("_", ".")}${key === "mu_0_1" ? " †" : ""}`, align: "right", hideBelow: 640,
            render: (row) => p3(row.byMu[key]), csv: (row) => row.byMu[key] ?? null,
          })),
        ]}
        rows={rows}
      />
    </>
  );
}

/**
 * A model name as a wrapping column header: it breaks at its spaces, and a long hyphenated name only
 * after its first part ("GJR-" / "GARCH(1,1)-t"); a short one ("HAR-IV") is never split.
 */
export const modelHeader = (label) => {
  const parts = /^(\w+-)(\S{9,})$/.exec(label);
  return parts ? <>{parts[1]}<wbr /><span className="nowrap">{parts[2]}</span></> : keepHyphenated(label);
};

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
  const setup = useMemo(() => fluctuationSetup(lab), [lab]);
  const fluctRows = useMemo(() => (setup ? fluctuationRows(lab) : []), [lab, setup]);
  const cumRows = useMemo(() => cumulativeRows(lab), [lab]);
  const ledger = useMemo(() => researchLedger(research), [research]);
  const testRows = useMemo(() => stabilityTestRows(research, ledger), [research, ledger]);
  const [viewValue, setView] = useViewParam("stab", VIEW_VALUES, "test");
  const onView = useCallback((value) => setView(value), [setView]);
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
  const FluctuationRows = getFluctuationRows();
  const CumulativeRows = getCumulativeRows();
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
  // The yearly-gap caption sits under its figure, and the notes follow; one set explains each
  // glossary term once in the panel, at its first use (the caption, then the notes).
  const seen = new Set();
  const gapCaption = gapRows.length ? withTerms(`Each bar is one model's QLIKE minus ${recordName}'s in one calendar year, on one scale for every row, so a bar above its line is a year ${recordName} had the lower loss${cut ? `; ${fmtInt(scale.clipped)} bars beyond ±${fmtNum(scale.limit, 3)} are cut at the edge and marked with a break` : ""}.`, seen) : null;
  const notes = [
    `The yearly bars use every out-of-sample day of each calendar year (overlapping 5-session targets), as the leaderboard does${partial.length ? `; partial years: ${partial.map((row) => `${row.year} (${fmtInt(row.nDays)} days)`).join(", ")}, against up to ${fmtInt(fullYear)} in a full one` : ""}. Each bar's two QLIKE values are in its tooltip and in the table view.`,
    pairs.length
      ? `Recent windows use the weekly series instead: one forecast a week on the first 5-session sampling grid (grid 0), ${fmtInt(pairs.length)} weeks (${fmtSpan(pairs[0].date, pairs[pairs.length - 1].date)}), each scored with the same QLIKE, so their values differ from the every-day figures in the leaderboard.`
      : null,
    "DM t by sampling grid: the Diebold-Mariano t of each model against the record over the full sample (large dot) and on each 5-session sampling grid (small dots); each grid scores every fifth session, starting on a different day. The shaded band is |t| < 1.96, where the difference is not significant at 5%.",
    gwNote(lab, ledger),
    setup ? `The rolling test's p-value for each window length counts the share of ${fmtInt(setup.draws)} simulated random walks with a larger maximum; the ledger's p-value adjusts for the choice among the ${fmtInt(setup.lengths)} window lengths looked at. Both tests assume forecasts re-estimated on rolling windows of bounded size, while the lab's windows expand, so their p-values are approximate.` : null,
  ].filter(Boolean).map((note, index) => <p key={index}>{withTerms(note, seen)}</p>);
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
      notes={notes}
      source={{ artifact: ["research.json → vol_lab.yearly", "vol_lab.series", "vol_lab.leaderboard", "vol_lab.stability", "ledger"] }}
      resetKey={research}
      table={() => (
        <>
        <DataTable
          caption={`QLIKE by year, ${first}–${last} (lower is better)`}
          rowKey={(row) => String(row.year)}
          dense
          exportName="stability-by-year"
          columns={[
            { key: "year", label: "Year", rowHeader: true, sortable: true },
            { key: "nDays", label: "Days", align: "right", sortable: true, render: (row) => fmtInt(row.nDays) },
            // The model headers wrap (see modelHeader), so the seven columns fit the half-width panel
            // from 1280px without a sideways scroll.
            ...models.map((model) => ({
              key: model.key, label: modelHeader(model.label), csvLabel: model.label, align: "right", sortable: true, className: "wrap-head",
              sortValue: (row) => row.values[model.key], render: (row) => fmtNum(row.values[model.key], 3),
            })),
          ]}
          rows={years}
        />
        {testRows.some((row) => row.p != null || row.gwP != null) && <StabilityTests rows={testRows} />}
        </>
      )}
    >
      {gapRows.length > 0 && (
        <>
          <h3 className="figure-title">Yearly QLIKE gap to {recordName}, up = {recordName} lower</h3>
          {/* The chart's own height, yearlyGapHeight() in charts/stabilityCharts.jsx: 230 px for four rows. */}
          <LazyMount minHeight={{ wide: gapRows.length * 52 + 22, narrow: gapRows.length * 52 + 22 }} label="yearly comparison">
            <YearlyGapRows rows={gapRows} recordLabel={recordName} limit={scale.limit} />
          </LazyMount>
          <p className="panel-caption">{gapCaption}</p>
        </>
      )}
      {recent.length > 0 && (
        <>
          <h3 className="figure-title">Recent windows: weekly series (grid 0), not the every-day sample</h3>
          <Facts className="facts--row recent-windows" items={recent.map((span) => ({
            label: `Last ${span.weeks} weeks`,
            // "vs" keeps to the second mean, so a narrow column breaks the pair before it.
            value: `${fmtNum(span.record, 3)} vs\u00a0${fmtNum(span.challenger, 3)}`,
            note: `QLIKE, ${recordName} vs challenger`,
          }))} />
        </>
      )}
      {setup && fluctRows.length > 0 && (
        <>
          <div className="figure-head">
            <h3 className="figure-title">
              {viewValue === "cumulative"
                ? `Cumulative QLIKE difference to ${recordName}, up = model lower`
                : `Rolling DM t vs ${recordName}, ${fmtInt(setup.window)}-session windows, with the 5% band`}
            </h3>
            {cumRows.length > 0 && (
              <Segmented label="Over-time view: rolling test or cumulative difference" options={STAB_VIEWS}
                value={viewValue} onChange={onView} />
            )}
          </div>
          {viewValue === "cumulative" && cumRows.length > 0 ? (
            <>
              <LazyMount minHeight={{ wide: cumRows.length * 86 + 22, narrow: cumRows.length * 86 + 22 }} label="cumulative difference">
                <CumulativeRows rows={cumRows} recordLabel={recordName} endDate={lab?.stability?.cumulative?.end_date} />
              </LazyMount>
              <p className="panel-caption">{cumulativeCaption(recordName, lab?.stability?.cumulative?.end_date)}</p>
            </>
          ) : (
            <>
              {/* The chart's own height, fluctuationHeight() in charts/stabilityCharts.jsx: 280 px for three rows. */}
              <LazyMount minHeight={{ wide: fluctRows.length * 86 + 22, narrow: fluctRows.length * 86 + 22 }} label="rolling test">
                <FluctuationRows rows={fluctRows} setup={setup} recordLabel={recordName} />
              </LazyMount>
              <p className="panel-caption">{fluctuationCaption(setup, recordName)}</p>
              <ul className="fluct-readings">
                {fluctRows.map((row) => (
                  <li key={row.name}><strong>{row.label}.</strong> {fluctuationReading(row, {
                    recordName, setup, ledgerRow: ledger?.byId?.get(`vol_fluct_${row.key}`),
                  })}</li>
                ))}
              </ul>
            </>
          )}
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
