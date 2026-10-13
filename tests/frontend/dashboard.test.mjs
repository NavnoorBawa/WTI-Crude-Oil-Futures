// Network-free tests of the dashboard's logic: quote selection around contract rolls, chart
// aggregation, number formatting, sizing, and the wording rules. Run: npm test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let dashboard;
before(async () => { dashboard = await loadDashboard(); });
after(() => dashboard.close());

const text = (html) => html.replace(/<[^>]+>/g, "").replace(/\s+/g, " ");
const fixture = JSON.parse(readFileSync(new URL("../fixtures/chart_actual_2026-09-24.json", import.meta.url), "utf8"));

// ------------------------------------------------------------------------------------ formatting
test("fmtP never prints a p-value as a threshold it is below", () => {
  const { fmtP } = dashboard.format;
  assert.equal(fmtP(0.0496), "p = 0.0496");
  assert.equal(fmtP(0.04999), "p < 0.05");
  assert.equal(fmtP(0.9996), "p = 0.9996");
  assert.equal(fmtP(0.00999), "p < 0.01");
  assert.equal(fmtP(0.05), "p = 0.05");
  assert.equal(fmtP(0.0004), "p < 0.001");
  assert.equal(fmtP(null), null);
  for (let i = 1000; i < 1_000_000; i += 7) {
    const p = i / 1_000_000;
    const out = fmtP(p);
    const shown = Number(out.replace(/^p [=<] /, ""));
    for (const threshold of [0.01, 0.05, 0.1, 1]) {
      assert.ok(!(p < threshold && out.startsWith("p =") && shown >= threshold), `fmtP(${p}) = ${out}`);
    }
  }
});

test("fmtNum renders a zero Sharpe as 0.00, never -0.00", () => {
  const { fmtNum } = dashboard.format;
  assert.equal(fmtNum(0, 2), "0.00");
  assert.equal(fmtNum(-0, 2), "0.00");
  assert.equal(fmtNum(-0.001, 2), "0.00");
  assert.equal(fmtNum(-0.07, 2), "−0.07");
});

test("ctWallTimeMs places 17:00 CT correctly in CDT, CST and on both DST days", () => {
  const { ctWallTimeMs } = dashboard.format;
  assert.equal(ctWallTimeMs("2026-10-20", 17), Date.parse("2026-10-20T22:00:00Z"));
  assert.equal(ctWallTimeMs("2026-12-17", 17), Date.parse("2026-12-17T23:00:00Z"));
  assert.equal(ctWallTimeMs("2026-03-08", 17), Date.parse("2026-03-08T22:00:00Z"));
  assert.equal(ctWallTimeMs("2026-11-01", 17), Date.parse("2026-11-01T23:00:00Z"));
  assert.equal(ctWallTimeMs("not a date", 17), null);
});

// ----------------------------------------------------------------------------------------- chart
const views = (actual) => dashboard.chart.buildPriceViews(dashboard.chart.buildActualPoints(actual, null));

test("a live quote's running session volume is not added to the hourly volumes", () => {
  const { daily } = views(fixture.actual);
  assert.equal(daily[daily.length - 1].volume, fixture.volume);   // the header's session volume
});

test("live ticks every 30 s in backend mode do not accumulate into the session volume", () => {
  const actual = { timestamps: fixture.actual.timestamps.slice(0, -1), values: fixture.actual.values.slice(0, -1),
                   volumes: fixture.actual.volumes.slice(0, -1) };
  let running = 7678;
  for (let i = 0; i < 60; i += 1) {
    running += 40;
    actual.timestamps.push(new Date(Date.parse("2026-09-24T01:30:00.123Z") + i * 30000).toISOString());
    actual.values.push(91.5);
    actual.volumes.push(running);
  }
  const { daily, intraday } = views(actual);
  assert.equal(daily[daily.length - 1].volume, running);
  assert.equal(intraday[intraday.length - 1].volume, running);
});

// The hourly window opens mid-session (Mon 2026-09-14 10:00Z): Monday's daily bar (04:00Z, 23:00 CT
// Sunday) shares its session with Monday's own hourly bars.
const overlap = () => {
  const actual = { timestamps: [], values: [], volumes: [], kinds: [] };
  const push = (t, v, vol, kind) => { actual.timestamps.push(t); actual.values.push(v); actual.volumes.push(vol); actual.kinds.push(kind); };
  push("2026-09-10T04:00:00Z", 62.0, 310000, "1d");
  push("2026-09-11T04:00:00Z", 62.5, 290000, "1d");
  push("2026-09-14T04:00:00Z", 63.4, 300000, "1d");
  for (let h = 10; h <= 20; h += 1) push(`2026-09-14T${String(h).padStart(2, "0")}:00:00Z`, 63 + h / 100, 20000, "1h");
  return actual;
};

test("a daily bar overlapping the hourly window is not drawn at 11 PM the night before", () => {
  for (const tagged of [true, false]) {
    const actual = overlap();
    if (!tagged) delete actual.kinds;               // pre-tag payloads: inferred from the grid
    const { intraday, daily } = views(actual);
    const labels = intraday.map((p) => dashboard.chart.formatWallTime(p.time, "intraday"));
    assert.ok(!labels.includes("Sep 13, 2026, 11:00 PM CT"), `tagged=${tagged}: ${labels.slice(0, 4)}`);
    assert.ok(!intraday.some((p) => p.value === 63.4), `tagged=${tagged}`);
    const monday = daily.find((p) => dashboard.chart.formatWallTime(p.time, "daily") === "Mon, Sep 14, 2026");
    assert.equal(monday.volume, 300000, `tagged=${tagged}`);
  }
});

test("with kinds, an hourly window opening at exactly 00:00 ET keeps its first hourly bar", () => {
  // Grid inference reads a 23:00 CT bar a day after the last daily bar as daily; the kind is exact.
  const actual = { timestamps: [], values: [], volumes: [], kinds: [] };
  const push = (t, v, vol, kind) => { actual.timestamps.push(t); actual.values.push(v); actual.volumes.push(vol); actual.kinds.push(kind); };
  push("2026-09-10T04:00:00Z", 62.0, 310000, "1d");
  push("2026-09-11T04:00:00Z", 62.5, 290000, "1d");
  for (let h = 4; h <= 8; h += 1) push(`2026-09-14T${String(h).padStart(2, "0")}:00:00Z`, 63 + h / 100, 20000, "1h");
  const { intraday, daily } = views(actual);
  assert.ok(intraday.some((p) => p.value === 63.04), "the 00:00 ET hourly bar is plotted");
  const monday = daily.find((p) => dashboard.chart.formatWallTime(p.time, "daily") === "Mon, Sep 14, 2026");
  assert.equal(monday.volume, 5 * 20000);
});

// ---------------------------------------------------------------------------------------- quotes
const snapshotOn = (contract, extra = {}) => ({
  current_price: 88.4, price_change: 0.3, price_change_percent: 0.34, price_change_quality: "daily_close",
  frozen_at: "2026-10-20T18:05:00+00:00",
  contract: { symbol: contract, last_trade_date: "2026-10-20", market_time: "2026-10-20T18:00:00Z" },
  ...extra,
});

test("a quote for the next contract never takes the snapshot contract's label", () => {
  const { normalizeQuote, resolveQuote } = dashboard.app;
  const data = snapshotOn("CLX26");
  const next = normalizeQuote({ price: 89.35, prev_close: 88.4, market_time: "2026-10-20T23:30:00Z", contract: "CLZ26" });
  assert.equal(resolveQuote(next, data).kind, "snapshot");
  const unstampedAfter = normalizeQuote({ price: 89.35, prev_close: 88.4, market_time: "2026-10-20T23:30:00Z" });
  assert.equal(resolveQuote(unstampedAfter, data).kind, "snapshot");   // after 17:00 CT on the last trade date
  const unstampedBefore = normalizeQuote({ price: 88.6, prev_close: 88.1, market_time: "2026-10-20T21:30:00Z" });
  assert.equal(resolveQuote(unstampedBefore, data).kind, "quote");
  const same = normalizeQuote({ price: 88.6, prev_close: 88.1, market_time: "2026-10-20T21:30:00Z", contract: "CLX26" });
  assert.equal(resolveQuote(same, data).kind, "quote");
});

test("a quote's change is withheld for roll reasons only", () => {
  const { normalizeQuote, resolveQuote } = dashboard.app;
  const rollDay = snapshotOn("CLX26", {
    current_price: 91.9, price_change: null, price_change_percent: null,
    price_change_quality: "unavailable_contract_roll", frozen_at: "2026-09-23T15:05:00+00:00",
    contract: { symbol: "CLX26", last_trade_date: "2026-10-20", market_time: "2026-09-23T15:00:00Z" },
  });
  const unstamped = normalizeQuote({ price: 92.1, prev_close: 94.59, change_pct: -2.63, market_time: "2026-09-23T16:30:00Z" });
  const stamped = normalizeQuote({ price: 92.1, prev_close: 90.52, change_pct: 1.75, market_time: "2026-09-23T16:30:00Z", contract: "CLX26" });
  assert.equal(resolveQuote(unstamped, rollDay).changePct, null);
  assert.equal(resolveQuote(stamped, rollDay).changePct, null);
  const firstSession = { ...rollDay, price_change_percent: 1.52, price_change_quality: "daily_close_new_contract" };
  assert.equal(resolveQuote(unstamped, firstSession).changePct, null);   // unchecked file, new contract
  assert.equal(resolveQuote(stamped, firstSession).changePct, 1.75);     // the script checked the roll
  // A weekday-evening snapshot lacks its own previous settlement; the next morning's quote does not.
  const evening = snapshotOn("CLX26", {
    price_change: null, price_change_percent: null, price_change_quality: "unavailable_previous_settlement",
    frozen_at: "2026-10-14T00:20:00+00:00",
    contract: { symbol: "CLX26", last_trade_date: "2026-10-20", market_time: "2026-10-14T00:15:00Z" },
  });
  const morning = normalizeQuote({ price: 88.9, prev_close: 88.4, change_pct: 0.57, market_time: "2026-10-14T14:00:00Z", contract: "CLX26" });
  assert.equal(resolveQuote(morning, evening).changePct, 0.57);
});

// --------------------------------------------------------------------------------- sizing/record
test("a break-even profit factor is no Kelly edge", () => {
  const { computeSizing } = dashboard.app;
  for (const winRate of [48, 51, 55, 60]) {
    assert.equal(computeSizing(winRate, 1.0, 10).noEdge, true, `win rate ${winRate}`);
    assert.equal(computeSizing(winRate, 1.001, 10).noEdge, false, `win rate ${winRate}`);
  }
});

test("the live-record verdict pairs the independent count with the independent hit rate", () => {
  const { describeLiveRecord } = dashboard.app;
  const base = { n_calls: 120, n_resolved_directional: 90, n_independent_directional: 18, hit_rate_pct: 80.0 };
  assert.equal(describeLiveRecord({ ...base, hit_rate_independent_pct: 0.0 }).verdict, "0.0% hit rate on 18 independent calls");
  assert.equal(describeLiveRecord(base).verdict, "80.0% hit rate over 90 overlapping calls (18 independent)");
  assert.equal(describeLiveRecord({ ...base, n_independent_directional: 5 }).verdict, "too few to validate (need ≥18 independent)");
});

// ------------------------------------------------------------------------------------- wording
const playbook = (lost, threat) => ({
  event_count: 31, first_event_year: 1990, last_event_year: 2024,
  distributions: {
    supply_lost: { n: 21, peak: { median: lost[0] }, settle: { median: lost[1] } },
    threat_only: { n: 10, peak: { median: threat[0] }, settle: { median: threat[1] } },
  },
});
const supplyText = (pb) => text(renderToStaticMarkup(createElement(dashboard.app.SupplySection,
  { playbook: pb, noveltySpike: false, open: true, onToggle: () => {} })));

test("the supply-shock takeaway applies one retention test to both groups", () => {
  const live = supplyText(playbook([12.1, 4.3], [4.0, 0.5]));
  assert.ok(!/held their gains/.test(live), live);
  assert.match(live, /kept more of their move: \+4\.3% median settle of a \+12\.1% median peak \(n = 21\), vs \+0\.5% of \+4\.0% for threats/);
  assert.ok(!/kept more/.test(supplyText(playbook([12.1, 2.0], [4.0, 1.5]))));   // physical kept a smaller share
  assert.ok(!/kept more/.test(supplyText(playbook([12.1, -1.0], [4.0, -2.0]))));  // nothing was kept
});

const volText = (economic) => text(renderToStaticMarkup(createElement(dashboard.app.VolatilityCard, { vol: {
  live: { direction: "RISING", as_of: "2026-10-12", model: "HAR-IV" },
  validation: { har_dir_acc_pct: 72.7, majority_class_pct: 52.1, har_dir_p_value_vs_base_rate: 0.047, har_dir_z_score: 1.7, n: 926 },
  economic,
} })));

test("the vol card states the roll-free window and compares the two VRP proxies like for like", () => {
  const economic = {
    returns: { sample_start: "2008-05-07", sample_end: "2024-04-05" },
    vol_target_overlay: { sharpe_buy_hold: 0.0, sharpe_trailing_vol_target: -0.11, sharpe_forecast_vol_target: -0.07 },
    variance_risk_premium: {
      mean_premium_vol_pts: 2.5, share_positive_pct: 72.5, worst_period_vol_pts: -157.9, short_vol_swap_sharpe: 0.52,
      short_variance_sharpe: 0.01, worst_year: 2020, short_vol_swap_sharpe_ex_worst_year: 1.3,
      short_variance_sharpe_ex_worst_year: 0.8, timing_p_value: 0.327,
    },
  };
  const out = volText(economic);
  assert.ok(out.includes("A vol-targeting overlay on roll-free returns (2008–2024) did NOT beat buy-and-hold (Sharpe −0.07 vs 0.00; trailing-vol sizing −0.11)"), out);
  assert.ok(out.includes("short vol-swap Sharpe 0.52, short variance 0.01 (1.30 and 0.80 excluding 2020)"), out);
  assert.ok(out.includes("p = 0.047"), out);
  // Only one proxy's ex-worst-year figure: neither is shown (a convex ex-crash vs a linear full sample).
  const half = { ...economic.variance_risk_premium };
  delete half.short_vol_swap_sharpe_ex_worst_year;
  assert.ok(!/excluding/.test(volText({ ...economic, variance_risk_premium: half })));
  const legacy = volText({ vol_target_overlay: economic.vol_target_overlay, variance_risk_premium: { mean_premium_vol_pts: 2.4, short_variance_sharpe: 0.0 } });
  assert.ok(!/roll-free|excluding/.test(legacy) && legacy.includes("A vol-targeting overlay did NOT beat"), legacy);
});
