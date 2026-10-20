// Network-free tests of the dashboard's data logic: quote selection around contract rolls, price
// chart aggregation and number formatting. Run: npm test
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { loadDashboard, readFixture } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let dashboard;
before(async () => { dashboard = await loadDashboard(); });
after(() => dashboard.close());

const fixture = readFixture("chart_actual_2026-09-24.json");

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
    const shown = Number(out.replace(/^p [=<>] /, ""));
    for (const threshold of [0.01, 0.05, 0.1, 1]) {
      assert.ok(!(p < threshold && out.startsWith("p =") && shown >= threshold), `fmtP(${p}) = ${out}`);
    }
  }
});

test("fmtP never rounds a p-value down onto a threshold it is above", () => {
  const { fmtP, fmtPValue } = dashboard.format;
  assert.equal(fmtP(0.0548), "p = 0.055");      // the SPA lower bound that printed as "0.05"
  assert.equal(fmtP(0.1048), "p = 0.105");
  assert.equal(fmtP(0.0104), "p = 0.0104");
  assert.equal(fmtP(0.05004), "p > 0.05");       // still on the threshold at four digits
  assert.equal(fmtP(0.1), "p = 0.10");           // exactly at a threshold is printed as such
  assert.equal(fmtP(0.0368), "p = 0.04");
  assert.equal(fmtPValue(0.05004), "> 0.05");
  for (let i = 1000; i < 1_000_000; i += 7) {
    const p = i / 1_000_000;
    const out = fmtP(p);
    if (!out.startsWith("p =")) continue;
    const shown = Number(out.slice(4));
    for (const threshold of [0.01, 0.05, 0.1]) {
      assert.ok(!(p > threshold && shown === threshold), `fmtP(${p}) = ${out}`);
    }
  }
});

test("withCentralTime gives every ET clock time its CT twin", () => {
  const { withCentralTime } = dashboard.format;
  assert.equal(withCentralTime("the first CL settlement (14:30 ET) after"), "the first CL settlement (14:30 ET / 13:30 CT) after");
  assert.equal(withCentralTime("00:15 ET and 16:15 ET"), "00:15 ET / 23:15 CT and 16:15 ET / 15:15 CT");
  assert.equal(withCentralTime("no times here"), "no times here");
  assert.equal(withCentralTime(null), null);
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
  const { normalizeQuote, resolveQuote } = dashboard.quote;
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

test("a stale, implausible or future-stamped quote never replaces the snapshot", () => {
  const { normalizeQuote, isQuoteUsable } = dashboard.quote;
  const data = snapshotOn("CLX26");
  assert.equal(isQuoteUsable(normalizeQuote({ price: 88.6, market_time: "2026-10-20T17:00:00Z", contract: "CLX26" }), data), false);
  assert.equal(isQuoteUsable(normalizeQuote({ price: 120.0, market_time: "2026-10-20T19:00:00Z", contract: "CLX26" }), data), false);
  assert.equal(normalizeQuote({ price: 88.6, market_time: "2026-10-22T19:00:00Z" }), null);
  assert.equal(normalizeQuote({ price: -1, market_time: "2026-10-20T19:00:00Z" }), null);
});

test("a quote's change is withheld for roll reasons only", () => {
  const { normalizeQuote, resolveQuote } = dashboard.quote;
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
