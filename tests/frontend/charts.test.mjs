// The canvas charts' pure parts: realized vol split by the band, the OVX series only when it is a
// line, the largest-miss and VaR annotations, axis labels (real minus, years on long spans), keyboard
// reading and framing, and the print palette mirroring the print stylesheet.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { TickMarkType } from "lightweight-charts";
import { loadDashboard, readFixture, readSource } from "./load.mjs";

let m;
let lw;
before(async () => {
  m = await loadDashboard();
  lw = await m.load("/src/charts/lwUtil.js");
});
after(() => m.close());

const research = readFixture("research.json");
const data = readFixture("dashboard_data_v3.json");

test("buildForecastData splits realized points by the band and draws OVX only as a line", () => {
  const { buildForecastData } = m.forecastChart;
  const rows = [
    { date: "2026-01-02", actual: 30, forecast: 28, lo: 20, hi: 40, ovx: null },   // inside
    { date: "2026-01-09", actual: 50, forecast: 28, lo: 20, hi: 40, ovx: null },   // above
    { date: "2026-01-16", actual: 10, forecast: 28, lo: 20, hi: 40, ovx: null },   // below
    { date: "2026-01-23", actual: 30, forecast: 28, lo: null, hi: null, ovx: null }, // no band: not a miss
    { date: "2026-01-30", actual: null, forecast: 28, lo: 20, hi: 40, ovx: null },  // not realized yet
  ];
  const model = buildForecastData(rows, { date: "2026-02-06", forecast: 30, lo: 20, hi: 45, ovx: 33 });
  const values = (series) => series.map((point) => point.value ?? null);
  assert.deepEqual(values(model.actualInside), [30, null, null, 30, null, null]);
  assert.deepEqual(values(model.actualOutside), [null, 50, 10, null, null, null]);
  assert.deepEqual(model.actualInside.map((point) => point.time), [...rows.map((row) => row.date), "2026-02-06"], "whitespace keeps every date");
  assert.equal(model.hasOvx, false, "one OVX value (the live call's) is no line");
  assert.deepEqual(model.ovx, []);
  assert.equal(model.live.isLive, true);

  const withOvx = buildForecastData(rows.map((row, i) => ({ ...row, ovx: i < 2 ? 31 + i : null })), null);
  assert.equal(withOvx.hasOvx, true);
  assert.deepEqual(values(withOvx.ovx), [31, 32, null, null, null]);

  // On the fixtures: every banded realized week lands in exactly one of the two series.
  const fixtureRows = m.research.forecastRows(research.vol_lab, data.vol_forecast.recent_oos);
  const fixture = buildForecastData(fixtureRows, null);
  const outside = fixtureRows.filter((row) => row.actual != null && row.lo != null && (row.actual < row.lo || row.actual > row.hi)).length;
  assert.equal(fixture.actualOutside.filter((point) => point.value != null).length, outside);
  assert.equal(fixture.actualInside.filter((point) => point.value != null).length, fixtureRows.filter((row) => row.actual != null).length - outside);
});

test("largestMiss picks the largest |log error| inside a date window", () => {
  const { largestMiss } = m.forecastChart;
  const rows = [
    { date: "2020-03-06", actual: 200, forecast: 50 },   // ln 4
    { date: "2024-01-05", actual: 20, forecast: 60 },    // ln 1/3: the largest from 2021 on
    { date: "2025-06-06", actual: 70, forecast: 35 },    // ln 2
    { date: "2026-01-02", actual: null, forecast: 35 },
  ];
  assert.equal(largestMiss(rows).date, "2020-03-06");
  assert.equal(largestMiss(rows, "2021-01-01").date, "2024-01-05");
  assert.equal(largestMiss(rows, "2025-01-01", "2026-12-31").date, "2025-06-06");
  assert.equal(largestMiss(rows, "2026-01-01"), null, "no realized week in the window");
  // The fixtures: the same as an independent scan.
  const fixtureRows = m.research.forecastRows(research.vol_lab, null);
  const best = fixtureRows.filter((row) => row.actual > 0 && row.forecast > 0)
    .reduce((a, b) => (Math.abs(Math.log(b.actual / b.forecast)) > Math.abs(Math.log(a.actual / a.forecast)) ? b : a));
  assert.equal(largestMiss(fixtureRows).date, best.date);
});

test("forecast axis tick labels under the OVX and model badges are left blank", () => {
  const { ticksUnderBadges } = m.forecastChart;
  // One badge (17px tall) at y = 100: a 12px tick label overlapping it is covered, one clear of it is not.
  assert.deepEqual(ticksUnderBadges([100, 112, 116, 85, 88], [100]), [true, true, false, false, true]);
  // Two badges 6px apart stack (one is pushed a badge height off the other, either way), so the
  // pair covers the '50%' tick 13px under the lower value, as at 1440px with OVX 60 and HAR-IV 58.
  assert.deepEqual(ticksUnderBadges([192, 131, 160], [173, 179]), [true, false, true]);
  // Apart, each badge covers only its own neighbourhood.
  assert.deepEqual(ticksUnderBadges([192, 150], [100, 150]), [false, true]);
  assert.deepEqual(ticksUnderBadges([100, null], [null, Number.NaN]), [false, false], "no badge value, nothing blank");
  const source = readSource("src/charts/ForecastChart.jsx");
  assert.match(source, /tickmarksPriceFormatter: \(prices\) => tickLabels\(prices\)/);
  assert.match(source, /lastValueVisible: true/, "the badges stay");
});

test("varAnnotations finds the worst week and the latest 99% breach in a window", () => {
  const { varAnnotations } = m.varChart;
  const rows = [
    { date: "2020-04-13", ret: -80, var95: 20, var99: 30 },
    { date: "2024-01-05", ret: -35, var95: 20, var99: 30 },
    { date: "2025-06-06", ret: -25, var95: 20, var99: 30 },
    { date: "2026-01-02", ret: 5, var95: null, var99: null },
  ];
  const all = varAnnotations(rows);
  assert.deepEqual([all.worst.date, all.latestBreach.date], ["2020-04-13", "2024-01-05"]);
  const recent = varAnnotations(rows, "2025-01-01");
  assert.deepEqual([recent.worst.date, recent.latestBreach], ["2025-06-06", null]);
  // The legend names them (marker text on the plot would sit under the VaR step lines).
  const { varAnnotationText } = m.varChart;
  assert.equal(varAnnotationText(all), "in view: worst week Apr 13, 2020 (−80.0%) · latest 99% breach Jan 5, 2024 (−35.0%)");
  assert.equal(varAnnotationText(varAnnotations(rows, "2024-01-01", "2024-12-31")),
    "in view: worst week Jan 5, 2024 (−35.0%), also the latest 99% breach", "one week said once");
  assert.equal(varAnnotationText(recent), "in view: worst week Jun 6, 2025 (−25.0%)");
  assert.equal(varAnnotationText({ worst: null, latestBreach: null }), null);
  assert.doesNotMatch(readSource("src/charts/VarChart.jsx"), /\btext: /, "no marker carries text");
});

test("the VaR price axis leaves headroom and draws no tick label cut by the pane edge", () => {
  const source = readSource("src/charts/VarChart.jsx");
  const scale = source.match(/rightPriceScale: \{[^\n]*\}/)?.[0] ?? "";
  assert.match(scale, /entireTextOnly: true/, "a tick label half outside the pane is not drawn");
  assert.ok(Number(scale.match(/top: ([\d.]+)/)?.[1]) >= 0.1, scale);
});

test("VaR bars before the VaR warms up are solid text-4, at least 3:1 on the panel", () => {
  const { SCREEN_THEME, TEXT, SURFACE } = m.theme;
  const colors = m.varChart.barColors(SCREEN_THEME);
  assert.equal(colors.none, TEXT.text4, "solid, no alpha");
  const luminance = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (luminance(colors.none) + 0.05) / (luminance(SURFACE.bg1) + 0.05);
  assert.ok(ratio >= 3, `${ratio.toFixed(2)}:1`);
});

test("percent axis labels use a real minus sign", () => {
  const { fmtAxisPercent } = lw;
  assert.equal(fmtAxisPercent(-20), "−20%");
  assert.equal(fmtAxisPercent(-0.3), "0%", "no minus on a label that rounds to zero");
  assert.equal(fmtAxisPercent(35.4), "35%");
  assert.equal(fmtAxisPercent(Number.NaN), "");
  for (const file of ["ForecastChart.jsx", "VarChart.jsx"]) {
    assert.ok(readSource(`src/charts/${file}`).includes("priceFormatter: fmtAxisPercent"), file);
  }
});

test("month ticks name their year when the view spans more than 400 days", () => {
  const { formatTick, spanDays, LONG_SPAN_DAYS } = lw;
  assert.equal(LONG_SPAN_DAYS, 400);
  assert.equal(formatTick("2026-03-02", TickMarkType.Month, 1200), "Mar ’26");
  assert.equal(formatTick("2026-03-02", TickMarkType.Month, 365), "Mar");
  assert.equal(formatTick({ year: 2026, month: 3, day: 2 }, TickMarkType.Month, 401), "Mar ’26");
  assert.equal(formatTick("2026-01-02", TickMarkType.Year, 3000), "2026");
  assert.equal(formatTick("2026-03-17", TickMarkType.DayOfMonth, 20), "17");
  assert.equal(formatTick(Date.UTC(2025, 10, 3) / 1000, TickMarkType.Month, 500), "Nov ’25", "unix seconds too");
  assert.equal(formatTick("2026-03-17", TickMarkType.Time, 2), null, "clock times fall back to the library");
  assert.equal(spanDays({ from: "2025-01-01", to: "2026-01-01" }), 365);
  assert.equal(spanDays({ from: 0, to: 86400 * 3 }), 3);
  assert.equal(spanDays(null), null);
});

test("a tick label or marker text that the plot's edge would cut is left out", () => {
  const { fitsInPlot, installTickFormatter, MONO_CHAR_PX } = lw;
  // A 600 px plot with one day every 10 px from 2026-01-01.
  let formatter = null;
  const chart = {
    timeScale: () => ({
      width: () => 600,
      timeToCoordinate: (time) => (Date.parse(`${time}T00:00:00Z`) - Date.parse("2026-01-01T00:00:00Z")) / 86400000 * 10,
      subscribeVisibleTimeRangeChange: () => {},
      unsubscribeVisibleTimeRangeChange: () => {},
    }),
    applyOptions: (options) => { formatter = options.timeScale.tickMarkFormatter; },
  };
  const half = (text) => (text.length * MONO_CHAR_PX) / 2;
  assert.equal(fitsInPlot(chart, "2026-01-01", "Jan"), false, "at x = 0 half the label is outside");
  assert.equal(fitsInPlot(chart, "2026-01-02", "Jan"), 10 >= half("Jan"));
  assert.equal(fitsInPlot(chart, "2026-01-30", "Jan"), true);
  assert.equal(fitsInPlot(chart, "2026-03-01", "Mar ’26"), false, "x = 590: the right edge cuts it");
  const stop = installTickFormatter(chart);
  assert.equal(formatter("2026-01-01", TickMarkType.Month), "", "cut at the left edge");
  assert.equal(formatter("2026-01-30", TickMarkType.Month), "Jan");
  assert.equal(formatter("2026-03-02", TickMarkType.Month), "", "x = 600: cut at the right edge");
  assert.equal(formatter("2026-01-30", TickMarkType.Time), null, "clock times still fall back to the library");
  stop();
});

test("keyboard reading: arrows step one row (four with Shift), Home/End jump, Escape clears", () => {
  const { nextReadIndex, visibleRows, isOffFrame, frameFor, READ_STEP } = lw;
  const visible = visibleRows({ from: 9.5, to: 104 }, 100);
  assert.deepEqual(visible, { first: 10, last: 99 });
  assert.deepEqual(nextReadIndex("ArrowLeft", { current: null, visible, count: 100 }), { index: 99 }, "starts at the last visible row");
  assert.deepEqual(nextReadIndex("ArrowLeft", { current: 50, visible, count: 100 }), { index: 49 });
  assert.deepEqual(nextReadIndex("ArrowRight", { current: 50, shift: true, visible, count: 100 }), { index: 50 + READ_STEP });
  assert.deepEqual(nextReadIndex("ArrowRight", { current: 99, visible, count: 100 }), { index: 99 }, "clamped to the data");
  assert.deepEqual(nextReadIndex("Home", { current: 50, visible, count: 100 }), { index: 10 });
  assert.deepEqual(nextReadIndex("End", { current: 50, visible, count: 100 }), { index: 99 });
  assert.deepEqual(nextReadIndex("Escape", { current: 50, visible, count: 100 }), { clear: true });
  assert.equal(nextReadIndex("Escape", { current: null, visible, count: 100 }), null, "nothing to clear: the key is not taken");
  assert.equal(nextReadIndex("a", { current: 50, visible, count: 100 }), null);
  assert.equal(visibleRows(null, 10), null);

  const frame = frameFor(["2023-01-06", "2024-01-05", "2025-01-03", "2026-01-02"], 365, 4);
  assert.deepEqual(frame, { from: 1.5, to: 7 }, "the last year plus four bars of room");
  assert.deepEqual(frameFor(["2026-01-02"], null), { from: -0.5, to: 0.5 });
  assert.equal(frameFor([], 365), null);
  assert.equal(isOffFrame({ from: 1.7, to: 7.2 }, frame), false, "within half a bar");
  assert.equal(isOffFrame({ from: 0.5, to: 6 }, frame), true, "dragged a bar to the left");
  assert.equal(isOffFrame(null, frame), false);
});

test("the print palette mirrors the print stylesheet's light tokens", () => {
  const { PRINT_THEME } = m.theme;
  const css = readSource("src/styles/reading-surface.css");
  const block = css.match(/@media print\s*\{[\s\S]*?:root\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  const tokens = Object.fromEntries([...block.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{3,6})/g)].map(([, name, value]) => [name, value]));
  assert.ok(Object.keys(tokens).length >= 8, "the print tokens are found");
  const expand = (hex) => (hex.length === 4 ? `#${[...hex.slice(1)].map((c) => c + c).join("")}` : hex).toUpperCase();
  const mirror = {
    "--bg-0": PRINT_THEME.surface.bg0, "--bg-1": PRINT_THEME.surface.bg1, "--bg-2": PRINT_THEME.surface.bg2,
    "--rule": PRINT_THEME.surface.rule, "--rule-strong": PRINT_THEME.surface.ruleStrong,
    "--text-1": PRINT_THEME.text.text1, "--text-2": PRINT_THEME.text.text2, "--text-3": PRINT_THEME.text.text3, "--text-4": PRINT_THEME.text.text4,
    "--s-truth": PRINT_THEME.series.truth, "--warn": PRINT_THEME.status.warn,
  };
  for (const [name, value] of Object.entries(tokens)) {
    if (name in mirror) assert.equal(expand(mirror[name]), expand(value), name);
  }
  assert.equal(m.theme.baseChartOptions().kineticScroll.mouse, false);
  assert.equal(m.theme.baseChartOptions().kineticScroll.touch, true, "no reduced-motion preference on the server");
});

test("every canvas chart is a keyboard-readable group that prints as an image", () => {
  const figure = readSource("src/charts/ChartFigure.jsx");
  assert.match(figure, /className=\{`lw-host \$\{hostClassName\}`\} aria-hidden="true"/);
  assert.match(figure, /<img className="print-shot" alt=""/);
  assert.match(figure, /aria-live="polite"/);
  for (const file of ["ForecastChart.jsx", "VarChart.jsx", "PriceChart.jsx"]) {
    const source = readSource(`src/charts/${file}`);
    assert.match(source, /useChartReader\(/, `${file} reads by keyboard and tap`);
    assert.match(source, /<ChartFigure/, `${file} uses the shared figure`);
    assert.match(source, /frameKey/, `${file} re-frames on frameKey`);
  }
  const util = readSource("src/charts/lwUtil.js");
  for (const call of ["setCrosshairPosition", "subscribeClick", "subscribeVisibleLogicalRangeChange", "takeScreenshot", "beforeprint", "afterprint", "dblclick"]) {
    assert.ok(util.includes(call), call);
  }
});
