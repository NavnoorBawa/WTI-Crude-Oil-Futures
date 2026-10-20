import { useMemo, useState } from "react";
import { getVarChart } from "../charts/lazy";
import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import Segmented from "../components/Segmented";
import { riskAssertion, volTargetAssertion } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtP, fmtPValue, fmtPct, fmtSigned, fmtSpan } from "../lib/format";
import {
  countExceedances, getLab, getRoles, getVol, logToSimplePct, modelLabels, recordVar, varRows, varSeries, volTargetRisk,
} from "../lib/research";
import { ResearchUnavailable } from "./common";

const RANGES = [
  { value: "3Y", label: "3Y" },
  { value: "10Y", label: "10Y" },
  { value: "ALL", label: "ALL" },
];

/** k of n grids passed, as n small squares plus the count. */
function PassMeter({ pass, total }) {
  if (pass == null || !total) return DASH;
  return (
    <span className={`pass-meter${pass === 0 ? " is-zero" : pass === total ? " is-all" : ""}`} aria-label={`passes on ${pass} of ${total} grids`}>
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
 * RISK: does the forecast-scaled VaR breach as often as it should, and independently? The VaR
 * series and its returns are 5-session LOG returns; the vol-targeting table below reports simple
 * returns, so the caption converts the worst week to make the two comparable.
 */
export default function RiskPanel({ data, research, researchStatus }) {
  const [range, setRange] = useState("ALL");
  const [level, setLevel] = useState("0.01");
  const lab = getLab(research);
  const labels = useMemo(() => modelLabels(lab), [lab]);
  const { champion } = getRoles(lab, null);
  const methods = useMemo(() => varRows(lab), [lab]);
  const series = useMemo(() => varSeries(lab), [lab]);
  const counts = useMemo(() => countExceedances(series), [series]);
  const VarChart = getVarChart();

  if (!methods.length || !series.length) {
    return (
      <Panel id="risk" code="RISK" name="VaR backtest" title={null} fallbackTitle="Value-at-Risk backtest"
        source={SOURCE}>
        <ResearchUnavailable status={researchStatus} what="the VaR backtest" />
        <VolTargeting overlay={getVol(data).economic?.vol_target_overlay} />
      </Panel>
    );
  }

  const levels = [...new Set(methods.map((row) => row.level))].filter((value) => value != null).sort((a, b) => a - b);
  const shownLevel = Number(level);
  const shown = methods.filter((row) => row.level === shownLevel);
  const historical = methods.filter((row) => row.isHistorical && row.gridCount);
  const hsWorst = historical.sort((a, b) => a.independencePass - b.independencePass)[0];
  const evaluationWeeks = lab?.var_backtest?.grid0?.evaluation_weeks;
  const firstVar = series.find((row) => row.var95 != null);
  const recordName = labels.get(champion)?.replace(/\s*\(model of record\)\s*$/i, "") ?? "the model of record";
  const worst = series.reduce((low, row) => (low == null || row.ret < low.ret ? row : low), null);
  const worstNote = worst
    ? ` The worst week, ${fmtDate(worst.date)}, is ${fmtSigned(worst.ret, 1, "%")} as a log return, ${fmtSigned(logToSimplePct(worst.ret), 1, "%")} as a simple return (the vol-targeting table reports simple returns).`
    : "";

  return (
    <Panel
      id="risk"
      code="RISK"
      name="VaR backtest"
      title={riskAssertion(methods, labels, champion)}
      fallbackTitle="Value-at-Risk backtest"
      meta={[
        evaluationWeeks != null ? `n ${fmtInt(evaluationWeeks)} weeks` : null,
        fmtSpan(firstVar?.date, series[series.length - 1]?.date),
        `${methods[0]?.gridCount ?? DASH} 5-session sampling grids`,
        "Kupiec 1995 · Christoffersen 1998 · DQ (Engle-Manganelli 2004)",
      ]}
      actions={<Segmented label="VaR chart range" options={RANGES} value={range} onChange={setRange} />}
      caption={`Bars: weekly 5-session log returns, in percent. Lines: −VaR on the same log-return scale, from ${recordName} scaled by filtered historical simulation. In this series ${fmtInt(counts.e99)} of ${fmtInt(counts.n)} weeks breached the 99% line and ${fmtInt(counts.e95)} the 95% line (grid 0, rounded series).${worstNote}`}
      source={SOURCE}
      resetKey={research}
      table={() => (
        <DataTable
          caption="Latest 26 weeks: 5-session log return and VaR (percent, log-return scale)"
          rowKey={(row) => row.date}
          dense
          columns={[
            { key: "date", label: "Week of", rowHeader: true, render: (row) => fmtDate(row.date) },
            { key: "ret", label: "Log return", align: "right", render: (row) => fmtSigned(row.ret, 1) },
            { key: "var95", label: "−VaR 95%", align: "right", render: (row) => (row.var95 != null ? fmtNum(-row.var95, 1) : DASH) },
            { key: "var99", label: "−VaR 99%", align: "right", render: (row) => (row.var99 != null ? fmtNum(-row.var99, 1) : DASH) },
            {
              key: "breach", label: "Breach", align: "center",
              render: (row) => (row.var99 != null && row.ret < -row.var99 ? "99%" : row.var95 != null && row.ret < -row.var95 ? "95%" : "no"),
            },
          ]}
          rows={series.slice(-26).reverse()}
        />
      )}
    >
      <LazyMount minHeight={320} label="VaR chart">
        <VarChart rows={series} range={range} />
      </LazyMount>

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
          options={levels.map((value) => ({ value: String(value), label: `${Math.round((1 - value) * 100)}%` }))}
          value={level}
          onChange={setLevel}
        />
      </div>
      <DataTable
        caption={`VaR backtest at ${Math.round((1 - shownLevel) * 100)}%: breaches on grid 0 and tests passed (p ≥ 0.05) across the 5-session sampling grids`}
        rowKey={(row) => row.id}
        rowClass={(row) => (row.id === recordVar(methods, shownLevel)?.id ? "is-record" : row.isHistorical ? "is-flagged" : undefined)}
        dense
        initialSort={null}
        columns={[
          {
            key: "label", label: "Method", rowHeader: true, sortable: true,
            render: (row) => <span className="model-cell"><span className="model-name">{row.label}</span><span className="muted">{row.quantileLabel}</span></span>,
          },
          {
            key: "exceedances", label: "Breaches", title: "Grid 0 breaches vs the expected count", align: "right", sortable: true,
            render: (row) => <span className="num">{fmtInt(row.exceedances)} <span className="muted">/ {fmtNum(row.expected, 1)}</span></span>,
          },
          {
            key: "range", label: "All grids", title: "Breaches across the 5-session sampling grids (min–max)", align: "right",
            render: (row) => (row.exceedMin != null ? <span className="num muted">{fmtInt(row.exceedMin)}–{fmtInt(row.exceedMax)}</span> : DASH),
          },
          { key: "kupiecP", label: "Kupiec p", align: "right", sortable: true, firstDirection: "desc", render: (row) => <span className="num">{fmtPValue(row.kupiecP)}</span> },
          { key: "kupiecPass", label: "Kupiec", title: "Unconditional coverage (Kupiec 1995)", sortable: true, firstDirection: "desc", render: (row) => <PassMeter pass={row.kupiecPass} total={row.gridCount} /> },
          { key: "independencePass", label: "Independence", title: "No clustering (Christoffersen 1998)", sortable: true, firstDirection: "desc", render: (row) => <PassMeter pass={row.independencePass} total={row.gridCount} /> },
          { key: "ccPass", label: "Cond. cov.", title: "Conditional coverage (Christoffersen 1998)", sortable: true, firstDirection: "desc", render: (row) => <PassMeter pass={row.ccPass} total={row.gridCount} /> },
          { key: "dqPass", label: "DQ", title: "Dynamic quantile test (Engle-Manganelli 2004)", sortable: true, firstDirection: "desc", render: (row) => <PassMeter pass={row.dqPass} total={row.gridCount} /> },
        ]}
        rows={shown}
      />
      <VolTargeting overlay={getVol(data).economic?.vol_target_overlay} />
    </Panel>
  );
}

/**
 * The economic use of the forecast: sizing a long WTI position to a volatility target. The claim
 * is risk control (realized vol near target, thinner tails), not return; the Sharpe difference
 * test is shown beside it.
 */
function VolTargeting({ overlay }) {
  const risk = volTargetRisk(overlay);
  if (!risk?.rows.length) return null;
  const test = risk.vsBuyHold;
  const trailing = risk.vsTrailing;
  const describe = (name, value) => (value?.difference != null
    ? `forecast-vol target minus ${name}: ${fmtSigned(value.difference, 2)}${value.ci?.length === 2 && value.ci.every((v) => v != null) ? ` (95% CI ${fmtSigned(value.ci[0], 2)} to ${fmtSigned(value.ci[1], 2)})` : ""}${value.p != null ? `, ${fmtP(value.p)}` : ""}`
    : null);
  const differences = [describe("buy-and-hold", test), describe("trailing-vol target", trailing)].filter(Boolean);
  return (
    <div className="subsection ruled">
      <h3 className="study-title">{volTargetAssertion(risk) ?? "Volatility targeting on the forecast"}</h3>
      <DataTable
        caption={`Long front-month WTI, weekly rebalanced${risk.weeks != null ? `, ${fmtInt(risk.weeks)} weeks` : ""}${risk.target != null ? `, ${fmtNum(risk.target, 0)}% vol target` : ""}; simple returns, percent`}
        rowKey={(row) => row.key}
        rowClass={(row) => (row.key === "forecast_vol_target" ? "is-record" : undefined)}
        dense
        columns={[
          { key: "label", label: "Sizing", rowHeader: true },
          { key: "vol", label: "Realized vol", align: "right", render: (row) => <span className="num">{fmtPct(row.vol)}</span> },
          { key: "volOfVol", label: "Vol of vol", align: "right", render: (row) => <span className="num">{fmtPct(row.volOfVol)}</span> },
          { key: "kurtosis", label: "Excess kurt.", align: "right", render: (row) => <span className="num">{fmtNum(row.kurtosis, 1)}</span> },
          { key: "worstWeek", label: "Worst week", align: "right", render: (row) => <span className="num">{fmtSigned(row.worstWeek, 1, "%")}</span> },
          { key: "es", label: "ES 97.5%", align: "right", render: (row) => <span className="num">{fmtSigned(row.es, 1, "%")}</span> },
          { key: "maxDrawdown", label: "Max DD", align: "right", render: (row) => <span className="num">{row.maxDrawdown != null ? `−${fmtPct(Math.abs(row.maxDrawdown))}` : DASH}</span> },
          { key: "sharpe", label: "Sharpe", align: "right", render: (row) => <span className="num">{fmtNum(row.sharpe, 2)}</span> },
        ]}
        rows={risk.rows}
        footnote={differences.length
          ? `Sharpe difference, ${differences.join("; ")}. No return advantage is claimed; the use is risk control, and sizing on trailing realized vol is the simple alternative it must beat.`
          : null}
      />
    </div>
  );
}
