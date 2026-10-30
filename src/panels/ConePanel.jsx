import { useMemo } from "react";
import { getConeChart } from "../charts/lazy";
import DataTable from "../components/DataTable";
import Disclosure from "../components/Disclosure";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import { withTerms } from "../components/Term";
import { coneAssertion } from "../lib/assertions";
import { LIVE_WINDOWS, coneView, markPct } from "../lib/cone";
import { DASH, fmtDate, fmtNum, fmtPct, num, ordinal } from "../lib/format";
import { getLab, getVol } from "../lib/research";
import { liveOfRecord } from "../lib/verdict";
import { Facts, Legend, ResearchUnavailable } from "./common";

const IMPLIED_HORIZON_SESSIONS = 21; // OVX is a 30-calendar-day implied vol: about 21 sessions
const CONE_HEIGHT = 300;             // ConeChart's fixed height, reserved while it loads

const SOURCE = {
  artifact: ["research.json → vol_lab.vol_cone", "data.json → vol_forecast.live", "data.json → vol_forecast.live_record"],
  reproduce: [REPRODUCE.lab, REPRODUCE.validation],
};

const day = (value) => (value ? fmtDate(value, { year: false }) : null);
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** What a mark's number means: its band of the window's history, or the lab's own percentile. */
const markNote = (mark) => (mark.source === "lab" && mark.percentile != null
  ? `${ordinal(mark.percentile)} pctile`
  : mark.bracket?.label ?? null);

/**
 * CONE: where realized volatility sits within its own history, by window length. The percentile
 * bands come from the benchmark lab (research.json); the current marks come from data.json with
 * their own date, so a lab snapshot older than the forecast is never presented as current: the
 * meta states both dates and the gap between them.
 */
export default function ConePanel({ data, research, researchStatus }) {
  const lab = getLab(research);
  const { live: snapshot, liveRecord } = getVol(data);
  // The call of record, as the hero shows it: a snapshot from an older close than the newest
  // recorded call gives way to that call's date, forecast and inputs.
  const live = useMemo(() => liveOfRecord(snapshot, liveRecord), [snapshot, liveRecord]);
  const view = useMemo(() => coneView(lab?.vol_cone, live), [lab, live]);
  const forecast = num(live?.forecast_next_week_vol_annualized_pct);
  const horizon = num(lab?.config?.horizon_sessions) ?? view.windows[0]?.window ?? null;
  const ConeChart = getConeChart();

  if (!view.windows.length) {
    return (
      <Panel id="cone" code="CONE" name="Volatility cone" title={null} fallbackTitle="Realized-volatility cone" source={SOURCE}>
        <ResearchUnavailable status={researchStatus} what="the volatility cone" />
      </Panel>
    );
  }

  const isLive = view.current === "live";
  const marksDate = isLive ? view.liveDate : view.labDate;
  // Lab marks drawn beside newer data.json marks are older: they are open ticks with their own date.
  const older = (mark) => isLive && mark.source === "lab" && view.lagDays !== 0;
  const shownWindows = isLive
    ? LIVE_WINDOWS.map(([window]) => window)
    : [view.windows[0].window, view.windows[view.windows.length - 1].window];
  const nearMonth = view.marks.reduce((best, mark) => (best == null
    || Math.abs(mark.window - IMPLIED_HORIZON_SESSIONS) < Math.abs(best.window - IMPLIED_HORIZON_SESSIONS) ? mark : best), null);
  const factMarks = view.marks.filter((mark) => shownWindows.includes(mark.window) || mark === nearMonth);
  const markTitle = (mark) => `Realized ${mark.window}-day vol, ${fmtDate(mark.date)}: ${markPct(mark)}`
    + `${markNote(mark) ? ` (${markNote(mark)})` : ""}`;
  const ovxLabel = view.ovx ? `OVX, ${day(view.ovx.date) ?? "latest"} close` : null;
  const ovxTitle = view.ovx
    ? `${ovxLabel}: ${fmtNum(view.ovx.value, 1)}, a 30-calendar-day implied vol (about ${IMPLIED_HORIZON_SESSIONS} sessions)`
    : null;
  const forecastLabel = `forecast${view.liveDate ? `, ${day(view.liveDate)}` : ""}`;
  const summary = `Volatility cone of ${plural(view.windows.length, "window")} from ${view.windows[0].window} to `
    + `${view.windows[view.windows.length - 1].window} trading days; realized vol on ${fmtDate(marksDate)}: `
    + view.marks.filter((mark) => !older(mark)).map((mark) => `${markPct(mark)} over ${mark.window} days`).join(", ")
    + `${view.ovx ? `; OVX ${fmtNum(view.ovx.value, 1)}` : ""}${forecast != null ? `; forecast ${fmtPct(forecast)}` : ""}`;

  const caption = "Percentiles of rolling close-to-close realized vol, annualized; windows in trading days.";
  const notes = [
    view.ovx && nearMonth
      ? `OVX is a 30-calendar-day implied vol, about ${IMPLIED_HORIZON_SESSIONS} sessions: it closed at ${fmtNum(view.ovx.value, 1)} on `
        + `${fmtDate(view.ovx.date)}, against realized vol of ${markPct(nearMonth)} over the last ${nearMonth.window} trading days `
        + `on ${fmtDate(nearMonth.date)}${markNote(nearMonth) ? ` (${markNote(nearMonth)})` : ""}, so the two horizons are close but not equal.`
      : null,
    isLive
      ? `The bands are the benchmark lab's percentiles of each window's history${view.since ? ` since ${fmtDate(view.since)}` : ""}`
        + `${view.labDate ? `, to ${fmtDate(view.labDate)}` : ""}. The marks are data.json's realized vol as of the ${fmtDate(view.liveDate)} close, `
        + "so each is placed in a band between two stored percentiles rather than given an interpolated percentile of its own."
      : `The bands and marks are the benchmark lab's${view.since ? `, since ${fmtDate(view.since)}` : ""}${view.labDate ? ` to ${fmtDate(view.labDate)}` : ""}; `
        + "each mark's percentile is the lab's own.",
  ].filter(Boolean);
  const seen = new Set();

  const legend = [
    { tone: "truth", label: `realized, ${day(marksDate) ?? "latest"}`, shape: "mark-tick" },
    ...(view.marks.some(older) ? [{ tone: "truth", label: `realized, ${day(view.labDate) ?? "benchmark lab"}`, shape: "mark-tick-open" }] : []),
    ...(view.ovx ? [{ tone: "implied", label: ovxLabel, shape: "diamond" }] : []),
    ...(forecast != null ? [{ tone: "model", label: forecastLabel, shape: "dot" }] : []),
  ];

  const tableRows = view.windows.map((row) => {
    const mark = isLive ? view.marks.find((item) => item.window === row.window && item.source === "live") : null;
    // Printed as stored: the 5-day value at one decimal, the recorded two-decimal inputs at two.
    return { ...row, now: mark?.shown ?? null, nowDigits: mark?.digits ?? 1, band: mark?.bracket?.label ?? null };
  });

  return (
    <Panel
      id="cone"
      code="CONE"
      name="Volatility cone"
      title={coneAssertion(lab?.vol_cone, live)}
      fallbackTitle="Realized-volatility cone"
      meta={[
        view.labDate ? `cone: benchmark lab, ${view.since ? `${fmtDate(view.since)} to ` : ""}${fmtDate(view.labDate)}` : "cone: benchmark lab",
        isLive ? `marks: data.json, ${fmtDate(view.liveDate)} close` : `marks: benchmark lab${view.labDate ? `, ${fmtDate(view.labDate)}` : ""}`,
        view.lagDays > 0 ? `cone ${plural(view.lagDays, "day")} older than the forecast` : null,
        view.lagDays < 0 && forecast != null ? `forecast ${plural(-view.lagDays, "day")} older than the cone` : null,
      ]}
      source={SOURCE}
      resetKey={research}
      table={() => (
        <DataTable
          caption={`Realized-volatility percentiles by window (percent, annualized), benchmark lab${view.labDate ? ` to ${fmtDate(view.labDate)}` : ""}`
            + `${isLive ? `; realized vol on ${fmtDate(view.liveDate)} from data.json` : ""}`}
          rowKey={(row) => String(row.window)}
          dense
          exportName="cone-percentiles"
          columns={[
            { key: "window", label: "Window", rowHeader: true, render: (row) => `${row.window} days`, csvLabel: "Window (trading days)" },
            ...["p5", "p25", "p50", "p75", "p95"].map((key) => ({
              key, label: key, align: "right", render: (row) => fmtNum(row[key], 1), hideBelow: key === "p5" || key === "p95" ? undefined : 640,
            })),
            { key: "latest", label: `Lab latest${view.labDate ? `, ${day(view.labDate)}` : ""}`, align: "right", render: (row) => fmtNum(row.latest, 1), hideBelow: isLive ? 640 : undefined },
            { key: "percentile", label: "Lab pctile", align: "right", render: (row) => ordinal(row.percentile), hideBelow: isLive ? 640 : undefined },
            ...(isLive ? [
              { key: "now", label: day(view.liveDate), title: `Realized vol on ${fmtDate(view.liveDate)} (data.json)`, csvLabel: `Realized, ${view.liveDate}`, align: "right", render: (row) => (row.now != null ? fmtNum(row.now, row.nowDigits) : DASH) },
              { key: "band", label: "Band", align: "right", render: (row) => row.band ?? DASH },
            ] : []),
          ]}
          rows={tableRows}
        />
      )}
    >
      <div className="split split--wide cone-split">
        <div className="split-figure">
          <Legend items={legend} />
          <LazyMount minHeight={CONE_HEIGHT} label="volatility cone">
            <ConeChart
              windows={view.windows}
              marks={view.marks.map((mark) => ({ window: mark.window, value: mark.value, open: older(mark), title: markTitle(mark) }))}
              ovx={view.ovx ? { value: view.ovx.value, horizon: IMPLIED_HORIZON_SESSIONS, title: ovxTitle } : null}
              forecast={forecast != null ? {
                value: forecast,
                window: horizon,
                title: `Forecast of realized vol over the next ${plural(horizon, "session")}${view.liveDate ? `, as of ${fmtDate(view.liveDate)}` : ""}: ${fmtPct(forecast)}`,
              } : null}
              keyWindows={LIVE_WINDOWS.map(([window]) => window)}
              summary={summary}
            />
          </LazyMount>
        </div>
        <div className="split-side cone-side">
          <Facts className="facts--stack" items={[
            ...factMarks.map((mark) => ({
              label: `Realized ${mark.window}d${mark.date ? `, ${day(mark.date)}` : ""}`, value: markPct(mark), note: markNote(mark),
            })),
            view.ovx && { label: ovxLabel, value: fmtNum(view.ovx.value, 1) },
            forecast != null && {
              label: `Forecast, ${horizon}d`, value: fmtPct(forecast), note: view.liveDate ? `as of the ${day(view.liveDate)} close` : null,
            },
          ]} />
          <p className="panel-caption">{withTerms(caption, seen)}</p>
          <Disclosure className="panel-notes" summary="Method notes">
            {notes.map((note, index) => <p key={index}>{withTerms(note, seen)}</p>)}
          </Disclosure>
        </div>
      </div>
    </Panel>
  );
}
