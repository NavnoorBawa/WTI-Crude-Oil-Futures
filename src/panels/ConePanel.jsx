import { useMemo } from "react";
import { getConeChart } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel from "../components/Panel";
import { coneAssertion } from "../lib/assertions";
import { fmtDate, fmtNum, num, ordinal } from "../lib/format";
import { asArray, asObject, getLab, getVol } from "../lib/research";
import { Facts, Legend, ResearchUnavailable } from "./common";

const IMPLIED_HORIZON_SESSIONS = 21; // OVX is a 30-calendar-day implied vol: about 21 sessions

/** CONE: where current realized volatility sits within its own history, by window length. */
export default function ConePanel({ data, research, researchStatus }) {
  const lab = getLab(research);
  const cone = asObject(lab?.vol_cone);
  const { live } = getVol(data);
  const windows = useMemo(() => asArray(cone?.windows).map((row) => ({
    window: num(row?.window), p5: num(row?.p5), p25: num(row?.p25), p50: num(row?.p50), p75: num(row?.p75),
    p95: num(row?.p95), latest: num(row?.latest), percentile: num(row?.latest_percentile),
  })).filter((row) => row.window != null && row.p5 != null && row.p95 != null).sort((a, b) => a.window - b.window), [cone]);
  const implied = num(cone?.implied_ovx);
  const forecast = num(live?.forecast_next_week_vol_annualized_pct);
  const ConeChart = getConeChart();

  if (!windows.length) {
    return (
      <Panel id="cone" code="CONE" name="Volatility cone" span="half" title={null} fallbackTitle="Realized-volatility cone"
        source={{ artifact: "research.json → vol_lab.vol_cone" }}>
        <ResearchUnavailable status={researchStatus} what="the volatility cone" />
      </Panel>
    );
  }

  const nearMonth = windows.reduce((best, row) =>
    Math.abs(row.window - IMPLIED_HORIZON_SESSIONS) < Math.abs(best.window - IMPLIED_HORIZON_SESSIONS) ? row : best);
  const caption = implied != null && nearMonth.latest != null
    ? `OVX is a 30-day implied vol: ${fmtNum(implied, 1)} against ${fmtNum(nearMonth.latest, 1)} realized over ${nearMonth.window} days (${ordinal(nearMonth.percentile)} percentile). The diamond is the live 5-session forecast.`
    : "Percentiles of rolling close-to-close realized volatility, annualized.";

  return (
    <Panel
      id="cone"
      code="CONE"
      name="Volatility cone"
      span="half"
      title={coneAssertion(cone)}
      fallbackTitle="Realized-volatility cone"
      meta={[
        `${windows.length} windows`,
        typeof cone?.since === "string" ? `since ${fmtDate(cone.since)}` : null,
        typeof cone?.as_of === "string" ? `as of ${fmtDate(cone.as_of)}` : null,
        "close-to-close, annualized",
      ]}
      caption={caption}
      source={{ artifact: "research.json → vol_lab.vol_cone" }}
      resetKey={research}
      table={() => (
        <DataTable
          caption="Realized-volatility percentiles by window (percent, annualized)"
          rowKey={(row) => String(row.window)}
          dense
          columns={[
            { key: "window", label: "Window", rowHeader: true, render: (row) => `${row.window} days` },
            ...["p5", "p25", "p50", "p75", "p95"].map((key) => ({ key, label: key, align: "right", render: (row) => fmtNum(row[key], 1) })),
            { key: "latest", label: "Latest", align: "right", render: (row) => fmtNum(row.latest, 1) },
            { key: "percentile", label: "Pctile", align: "right", render: (row) => ordinal(row.percentile) },
          ]}
          rows={windows}
        />
      )}
    >
      <Facts className="facts--row" items={[
        ...[...new Set([windows[0], nearMonth, windows[windows.length - 1]])].map((row) => ({
          label: `Realized ${row.window}d`, value: `${fmtNum(row.latest, 1)}%`, note: `${ordinal(row.percentile)} pctile`,
        })),
        implied != null && { label: "OVX", value: fmtNum(implied, 1) },
        forecast != null && { label: "Forecast, 5d", value: `${fmtNum(forecast, 1)}%` },
      ]} />
      <Legend items={[
        { tone: "truth", label: "realized now", shape: "dot" },
        { tone: "cone-inner", label: "p25–p75", shape: "band" },
        { tone: "cone-outer", label: "p5–p95", shape: "band" },
        { tone: "median", label: "median", shape: "line" },
        ...(implied != null ? [{ tone: "implied", label: "OVX", shape: "dash" }] : []),
        ...(forecast != null ? [{ tone: "model", label: "forecast", shape: "diamond" }] : []),
      ]} />
      <LazyMount minHeight={300} label="volatility cone">
        <ConeChart windows={windows} implied={implied} forecast={forecast} />
      </LazyMount>
    </Panel>
  );
}
