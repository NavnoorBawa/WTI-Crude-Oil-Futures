import { useMemo, useState } from "react";
import { useViewParam } from "../app/viewState";
import { getVarChart } from "../charts/lazy";
import Chip from "../components/Chip";
import keepHyphenated from "../components/keepHyphenated";
import DataTable from "../components/DataTable";
import Disclosure from "../components/Disclosure";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import Segmented from "../components/Segmented";
import { withTerms } from "../components/Term";
import { riskAssertion, riskDetail, volTargetAssertion, volTargetDetail } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtP, fmtPValue, fmtPct, fmtSigned, fmtSpan, num } from "../lib/format";
import {
  asArray, asText, getLab, getRoles, getVol, logToSimplePct, modelLabels, recordVar, shortLabel, varRows, varSeries,
  volTargetRisk,
} from "../lib/research";
import { EconomicUnavailable, ResearchUnavailable, economicSpan } from "./common";

// The chart range and the table's VaR level live in the URL (?risk=10y&var=95).
const RANGES = ["3y", "10y", "all"];
const LEVELS = ["99", "95"];
const RANGE_OPTIONS = RANGES.map((value) => ({ value, label: value.toUpperCase() }));
// The breach count of the last LAST_WEEKS weeks, as var_backtest.grid0 reports it.
const LAST_WEEKS = 250;
// VarChart's plot plus its legend row, as measured at 1440 and 390px (a 300px plot; on a phone a
// 240px plot under a legend that wraps).
const VAR_HEIGHT = { wide: 328, narrow: 308 };

/** Which VaR line a week's log return fell below: "99%", "95%" or "no". */
const breachText = (row) => (row.var99 != null && row.ret < -row.var99 ? "99%" : row.var95 != null && row.ret < -row.var95 ? "95%" : "no");

/** k of n grids passed, as n small squares plus the count. */
function PassMeter({ pass, total }) {
  if (pass == null || !total) return DASH;
  return (
    <span
      className={`pass-meter${pass === 0 ? " is-zero" : pass === total ? " is-all" : ""}`}
      role="img"
      aria-label={`passes on ${pass} of ${total} grids`}
    >
      {Array.from({ length: total }, (_, index) => <i key={index} className={index < pass ? "is-on" : ""} aria-hidden="true" />)}
      <span className="num" aria-hidden="true">{pass}/{total}</span>
    </span>
  );
}

const SOURCE = {
  artifact: ["research.json → vol_lab.series (ret5, var95, var99)", "vol_lab.var_backtest", "data.json → vol_forecast.economic.vol_target_overlay"],
  reproduce: [REPRODUCE.lab, REPRODUCE.validation],
};

/**
 * Grid 0's breaches over the last LAST_WEEKS weeks for each varRows() row, with the Basel traffic
 * light zone when the artifact gives one: Map of row id -> {count, zone}. Pure.
 */
export const lastWindowBreaches = (lab, rows) => {
  const grid0 = asArray(lab?.var_backtest?.grid0?.methods);
  return new Map(rows.map((row) => {
    const base = grid0.find((item) => item?.key === row.method && num(item?.level) === row.level);
    return [row.id, { count: num(base?.exceedances_last_250), zone: asText(base?.basel_zone_last_250) }];
  }));
};

/**
 * The caption's breach counts, from the backtest artifact (grid 0's Kupiec counts for the model of
 * record's VaR at 99% and 95%), so the caption and the table can never disagree. Null when either
 * level is missing. Pure.
 */
export const riskCaptionCounts = (rows) => {
  const v99 = recordVar(rows, 0.01);
  const v95 = recordVar(rows, 0.05);
  if (v99?.exceedances == null || v95?.exceedances == null || v99.n == null) return null;
  return { n: v99.n, e99: v99.exceedances, e95: v95.exceedances, quantileLabel: v99.quantileLabel };
};

/**
 * RISK: does the forecast-scaled VaR breach as often as it should, and independently? The VaR
 * series and its returns are 5-session LOG returns; the vol-targeting table below reports simple
 * returns, so the notes convert the worst week to make the two comparable. The chart opens on the
 * last three years, where ordinary weeks are readable; over the full history a few extreme weeks
 * set the scale. The backtest statistics cover the full sample whatever the range.
 */
export default function RiskPanel({ data, research, researchStatus }) {
  const [range, setRange] = useViewParam("risk", RANGES, "3y");
  const [levelPct, setLevelPct] = useViewParam("var", LEVELS, "99");
  // Bumped on every range click, the pressed one included, so the chart reframes to the preset.
  const [frameKey, setFrameKey] = useState(0);
  const lab = getLab(research);
  const labels = useMemo(() => modelLabels(lab), [lab]);
  const { champion } = getRoles(lab, null);
  const methods = useMemo(() => varRows(lab), [lab]);
  const series = useMemo(() => varSeries(lab), [lab]);
  const last250 = useMemo(() => lastWindowBreaches(lab, methods), [lab, methods]);
  const VarChart = getVarChart();

  if (!methods.length || !series.length) {
    return (
      <Panel id="risk" code="RISK" name="VaR backtest" title={null} fallbackTitle="Value-at-Risk backtest"
        source={SOURCE}>
        <ResearchUnavailable status={researchStatus} what="the VaR backtest" />
        <VolTargeting economic={getVol(data).economic} />
      </Panel>
    );
  }

  const levels = [...new Set(methods.map((row) => row.level))].filter((value) => value != null).sort((a, b) => a - b);
  const levelOf = (value) => (value != null ? String(Math.round((1 - value) * 100)) : DASH);
  const shownLevel = levels.find((value) => levelOf(value) === levelPct) ?? levels[0];
  const shown = methods.filter((row) => row.level === shownLevel);
  const record = recordVar(methods, shownLevel);
  const historical = methods.filter((row) => row.isHistorical && row.gridCount);
  const hsWorst = historical.sort((a, b) => a.independencePass - b.independencePass)[0];
  const evaluationWeeks = lab?.var_backtest?.grid0?.evaluation_weeks;
  const firstVar = series.find((row) => row.var95 != null);
  const recordName = shortLabel(labels.get(champion)) ?? "the model of record";
  const counts = riskCaptionCounts(methods);
  const worst = series.reduce((low, row) => (low == null || row.ret < low.ret ? row : low), null);
  const hasZones = shown.some((row) => last250.get(row.id)?.zone);
  const expectedLast = shownLevel != null ? LAST_WEEKS * shownLevel : null;

  const caption = counts
    ? `On grid 0, ${fmtInt(counts.e99)} of ${fmtInt(counts.n)} weeks breached the 99% VaR and ${fmtInt(counts.e95)} the 95% `
      + `(Kupiec counts, ${recordName} ${counts.quantileLabel.toLowerCase()}).`
    : null;
  const notes = [
    `Bars: weekly 5-session log returns, in percent. Lines: −VaR on the same log-return scale, from ${recordName} scaled by `
      + "filtered historical simulation (95% solid, 99% dashed); a coloured bar breached its line. "
      + "The range buttons change the chart only: the backtest statistics cover the full sample whatever the range.",
    worst
      ? `The VaR and its returns are log returns; the vol-targeting table reports simple returns. The worst week, ${fmtDate(worst.date)}, `
        + `is ${fmtSigned(worst.ret, 1, "%")} as a log return, ${fmtSigned(logToSimplePct(worst.ret), 1, "%")} as a simple return.`
      : null,
  ];
  const seen = new Set();

  return (
    <Panel
      id="risk"
      code="RISK"
      name="VaR backtest"
      title={riskAssertion(methods, labels, champion)}
      fallbackTitle="Value-at-Risk backtest"
      meta={[
        ...riskDetail(methods),
        evaluationWeeks != null ? `n ${fmtInt(evaluationWeeks)} weeks` : null,
        fmtSpan(firstVar?.date, series[series.length - 1]?.date),
        `${methods[0]?.gridCount ?? DASH} 5-session sampling grids`,
        "Kupiec 1995 · Christoffersen 1998 · DQ (Engle-Manganelli 2004)",
      ]}
      controls={(
        <Segmented
          label="VaR chart range"
          options={RANGE_OPTIONS}
          value={range}
          onChange={(value) => { setRange(value); setFrameKey((key) => key + 1); }}
        />
      )}
      source={SOURCE}
      resetKey={research}
      table={() => (
        <DataTable
          caption={`Latest 26 weeks: 5-session log return and VaR (percent, log-return scale); the CSV holds all ${fmtInt(series.length)} weeks`}
          rowKey={(row) => row.date}
          dense
          exportName="risk-weekly-var"
          exportRows={series.slice().reverse()}
          columns={[
            { key: "date", label: "Week of", rowHeader: true, render: (row) => fmtDate(row.date) },
            { key: "ret", label: "Log return", align: "right", render: (row) => fmtSigned(row.ret, 1) },
            { key: "var95", label: "−VaR 95%", align: "right", hideBelow: 640, render: (row) => (row.var95 != null ? fmtNum(-row.var95, 1) : DASH), csv: (row) => (row.var95 != null ? -row.var95 : null) },
            { key: "var99", label: "−VaR 99%", align: "right", render: (row) => (row.var99 != null ? fmtNum(-row.var99, 1) : DASH), csv: (row) => (row.var99 != null ? -row.var99 : null) },
            { key: "breach", label: "Breach", align: "center", render: breachText, csv: breachText },
          ]}
          rows={series.slice(-26).reverse()}
        />
      )}
    >
      <LazyMount minHeight={VAR_HEIGHT} label="VaR chart">
        <VarChart rows={series} range={range.toUpperCase()} frameKey={frameKey} />
      </LazyMount>
      {/* The caption and notes describe the chart, so they sit under it, before the backtest table. */}
      {caption && <p className="panel-caption">{withTerms(caption, seen)}</p>}
      <Disclosure className="panel-notes" summary="Method notes">
        {notes.filter(Boolean).map((note, index) => <p key={index}>{withTerms(note, seen)}</p>)}
      </Disclosure>

      {hsWorst && hsWorst.independencePass < hsWorst.gridCount && (
        <p className="callout">
          <Chip tone="warn">Note</Chip>
          {hsWorst.kupiecPass === hsWorst.gridCount
            ? `Historical simulation gets the ${hsWorst.confidence}% breach count right (Kupiec passes on ${hsWorst.kupiecPass}/${hsWorst.gridCount} grids), but its breaches cluster: `
            : `Historical simulation's ${hsWorst.confidence}% breaches cluster: `}
          the independence test passes on {hsWorst.independencePass}/{hsWorst.gridCount} grids. A volatility-scaled VaR moves with the regime; a trailing window of past returns does not.
        </p>
      )}

      <div className="subhead">
        <h3 className="figure-title">Backtest by method</h3>
        <Segmented
          label="VaR level"
          options={levels.map((value) => ({ value: levelOf(value), label: `${levelOf(value)}%` }))}
          value={levelOf(shownLevel)}
          onChange={setLevelPct}
        />
      </div>
      <DataTable
        caption={`VaR backtest at ${levelOf(shownLevel)}%: breaches on grid 0 (all, and the last ${LAST_WEEKS} weeks) and tests passed (p ≥ 0.05) across the 5-session sampling grids`}
        rowKey={(row) => row.id}
        rowClass={(row) => (row.id === record?.id ? "is-record" : row.isHistorical ? "is-flagged" : undefined)}
        dense
        exportName={`risk-var-backtest-${levelOf(shownLevel)}`}
        initialSort={null}
        columns={[
          {
            key: "label", label: "Method", rowHeader: true, sortable: true, className: "risk-method",
            render: (row) => <span className="model-cell"><span className="model-name">{keepHyphenated(row.label)}</span><span className="muted">{row.quantileLabel}</span></span>,
            csv: (row) => `${row.label}, ${row.quantileLabel}`,
          },
          {
            key: "exceedances", label: "Breaches", title: "Grid 0 breaches vs the expected count", align: "right", sortable: true,
            render: (row) => <span className="num">{fmtInt(row.exceedances)} <span className="muted">/ {fmtNum(row.expected, 1)}</span></span>,
            csvLabel: "Breaches (grid 0)",
          },
          {
            key: "last250", label: hasZones ? `Last ${LAST_WEEKS} wk, Basel zone` : `Last ${LAST_WEEKS} wk`,
            title: `Grid 0 breaches in the last ${LAST_WEEKS} weeks vs the expected count${hasZones ? ", with the Basel traffic-light zone the backtest assigns" : ""}`,
            align: "right", sortable: true, hideBelow: 640,
            sortValue: (row) => last250.get(row.id)?.count ?? null,
            render: (row) => {
              const entry = last250.get(row.id);
              if (entry?.count == null) return DASH;
              return (
                <span className="num">
                  {fmtInt(entry.count)} <span className="muted">/ {fmtNum(expectedLast, 1)}</span>
                  {entry.zone && <span className="zone-note">{entry.zone}</span>}
                </span>
              );
            },
            csvLabel: `Breaches, last ${LAST_WEEKS} weeks (grid 0)`,
          },
          {
            key: "range", label: "All grids", title: "Breaches across the 5-session sampling grids (min–max)", align: "right", hideBelow: 640,
            render: (row) => (row.exceedMin != null ? <span className="num muted">{fmtInt(row.exceedMin)}–{fmtInt(row.exceedMax)}</span> : DASH),
            csv: (row) => (row.exceedMin != null ? `${row.exceedMin}–${row.exceedMax}` : null),
            csvLabel: "Breaches across grids (min–max)",
          },
          {
            key: "kupiecP", label: "Kupiec p", title: "Kupiec test p-value on grid 0", align: "right", sortable: true, firstDirection: "desc", hideBelow: 640,
            render: (row) => <span className="num">{fmtPValue(row.kupiecP, { digits: 3 })}</span>,
          },
          { key: "kupiecPass", label: "Kupiec", title: "Unconditional coverage (Kupiec 1995)", sortable: true, firstDirection: "desc", render: (row) => <PassMeter pass={row.kupiecPass} total={row.gridCount} />, csvLabel: "Kupiec: grids passed" },
          {
            key: "independencePass",
            // A phone shows "Indep." (the full word stays for screen readers), so the key columns fit.
            label: <><span className="label-full">Independence</span><span className="label-short" aria-hidden="true">Indep.</span></>,
            title: "No clustering (Christoffersen 1998)", sortable: true, firstDirection: "desc",
            render: (row) => <PassMeter pass={row.independencePass} total={row.gridCount} />, csvLabel: "Independence: grids passed",
          },
          { key: "ccPass", label: "Cond. cov.", title: "Conditional coverage (Christoffersen 1998)", sortable: true, firstDirection: "desc", hideBelow: 640, render: (row) => <PassMeter pass={row.ccPass} total={row.gridCount} />, csvLabel: "Conditional coverage: grids passed" },
          { key: "dqPass", label: "DQ", title: "Dynamic quantile test (Engle-Manganelli 2004)", sortable: true, firstDirection: "desc", hideBelow: 640, render: (row) => <PassMeter pass={row.dqPass} total={row.gridCount} />, csvLabel: "DQ: grids passed" },
        ]}
        rows={shown}
      />
      <VolTargeting economic={getVol(data).economic} />
    </Panel>
  );
}

/**
 * The economic use of the forecast: sizing a long WTI position to a volatility target. The claim
 * is risk control (realized vol near target, thinner tails), not return; the Sharpe difference
 * test is shown beside it. It answers a different question from the VaR backtest, so its table is
 * one disclosure away; the claim and the tests that qualify it stay visible in the summary.
 */
function VolTargeting({ economic }) {
  const risk = volTargetRisk(economic?.vol_target_overlay);
  if (!risk?.rows.length) {
    return asText(economic?.unavailable_reason)
      ? <div className="subsection ruled"><EconomicUnavailable economic={economic} what="Volatility targeting" /></div>
      : null;
  }
  const span = economicSpan(economic);
  const test = risk.vsBuyHold;
  const trailing = risk.vsTrailing;
  const describe = (name, value) => (value?.difference != null
    ? `forecast-vol target minus ${name}: ${fmtSigned(value.difference, 2)}${value.ci?.length === 2 && value.ci.every((v) => v != null) ? ` (95% CI ${fmtSigned(value.ci[0], 2)} to ${fmtSigned(value.ci[1], 2)})` : ""}${value.p != null ? `, ${fmtP(value.p)}` : ""}`
    : null);
  const differences = [describe("buy-and-hold", test), describe("trailing-vol target", trailing)].filter(Boolean);
  const detail = volTargetDetail(risk).filter(Boolean);
  return (
    <div className="subsection ruled vol-target">
      <Disclosure
        className="study-disclosure"
        summary={(
          <>
            <h3 className="study-title">{volTargetAssertion(risk) ?? "Volatility targeting on the forecast"}</h3>
            {detail.length > 0 && (
              <span className="study-meta sep-list">{detail.map((item, index) => <span key={index}>{item}</span>)}</span>
            )}
          </>
        )}
      >
        <DataTable
          caption={`Long front-month WTI, weekly rebalanced${risk.weeks != null ? `, ${fmtInt(risk.weeks)} weeks` : ""}${span ? `, ${span.text}` : ""}${risk.target != null ? `, ${fmtNum(risk.target, 0)}% vol target` : ""}; simple returns, percent`}
          rowKey={(row) => row.key}
          rowClass={(row) => (row.key === "forecast_vol_target" ? "is-record" : undefined)}
          dense
          cardsBelow={640}
          exportName="risk-vol-targeting"
          columns={[
            { key: "label", label: "Sizing", rowHeader: true },
            { key: "vol", label: "Realized vol", align: "right", render: (row) => <span className="num">{fmtPct(row.vol)}</span> },
            { key: "volOfVol", label: "Vol of vol", align: "right", render: (row) => <span className="num">{fmtPct(row.volOfVol)}</span> },
            { key: "kurtosis", label: "Excess kurt.", align: "right", render: (row) => <span className="num">{fmtNum(row.kurtosis, 1)}</span> },
            { key: "worstWeek", label: "Worst week", align: "right", render: (row) => <span className="num">{fmtSigned(row.worstWeek, 1, "%")}</span> },
            { key: "es", label: "ES 97.5%", align: "right", render: (row) => <span className="num">{fmtSigned(row.es, 1, "%")}</span> },
            { key: "maxDrawdown", label: "Max DD", align: "right", render: (row) => <span className="num">{row.maxDrawdown != null ? `−${fmtPct(Math.abs(row.maxDrawdown))}` : DASH}</span>, csv: (row) => (row.maxDrawdown != null ? -Math.abs(row.maxDrawdown) : null) },
            { key: "sharpe", label: "Sharpe", align: "right", render: (row) => <span className="num">{fmtNum(row.sharpe, 2)}</span> },
          ]}
          rows={risk.rows}
          footnote={[
            differences.length
              ? `Sharpe difference, ${differences.join("; ")}. No return advantage is claimed; the use is risk control, and sizing on trailing realized vol is the simple alternative it must beat.`
              : null,
            span?.reason ?? null,
          ].filter(Boolean).join(" ") || null}
        />
      </Disclosure>
    </div>
  );
}
