// The verdict lede: the computed answer, the evidence index (each row is its section's h2), and
// this week's call exactly as the live record holds it, with its counted track record and arithmetic.
// Server-rendered against the fixtures (data.json v3 + research.json).
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SECTIONS } from "../../src/app/sections.js";
import { loadDashboard, readFixture, visibleText } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let v;
before(async () => {
  m = await loadDashboard();
  v = await m.load("/src/lib/verdict.js");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");

const render = (payload, researchPayload, status = researchPayload ? "ready" : "unavailable") => {
  const errors = [];
  const original = console.error;
  console.error = (...args) => errors.push(args.join(" "));
  try {
    const html = renderToStaticMarkup(createElement(m.dashboard.default, {
      data: payload, research: researchPayload, researchStatus: status, quote: m.quote.resolveQuote(null, payload),
    }));
    return { html, text: visibleText(html), errors };
  } finally {
    console.error = original;
  }
};
const heroOf = (html) => html.match(/<div class="verdict-hero">[\s\S]*?<dl class="kpi-grid">/)?.[0] ?? "";
const titleOf = (html) => visibleText(html.match(/<h2 id="verdict-title"[^>]*>([\s\S]*?)<\/h2>/)[1]).trim();
// visibleText() puts a space at every tag; a closing tag before punctuation is not a word break.
const squash = (text) => text.replace(/\s+/g, " ").replace(/ ([,.;:)])/g, "$1").trim();
const round1 = (value) => Number(value.toFixed(1));

test("the verdict's h2 is the computed answer, at most 90 characters, and loses 'beats all' when one comparison does", () => {
  const { html, errors } = render(data, research);
  assert.deepEqual(errors, []);
  const answer = v.verdictAnswer(research.vol_lab, data);
  assert.equal(titleOf(html), answer);
  assert.ok(answer.length <= 90, answer);
  const returnsOnly = m.research.leaderboardRows(research.vol_lab)
    .filter((row) => row.mcsEligible && !row.timingControl && row.info.size === 0 && row.role !== "record");
  assert.ok(answer.startsWith(`HAR-IV beats all ${returnsOnly.length} returns-only models`), answer);
  assert.ok(answer.endsWith("it is not distinguishable from OVX alone"), "OVX-only and raw OVX both have p >= 0.05");
  // The detail names the sample and gives the computed range.
  const stats = returnsOnly.map((row) => row.dmStat);
  const f = m.format;
  const detail = visibleText(html.match(/<p class="verdict-answer-detail">([\s\S]*?)<\/p>/)[1]);
  assert.ok(squash(detail).includes(`returns-only models ${f.fmtSigned(Math.max(...stats), 2)} to ${f.fmtSigned(Math.min(...stats), 2)}`), detail);
  assert.ok(detail.includes(`${f.fmtInt(research.vol_lab.leaderboard.n_days)} days`), "names n");
  assert.ok(detail.includes("benchmark lab"), "names the pipeline");

  const weaker = structuredClone(research);
  weaker.vol_lab.leaderboard.models.find((model) => model.key === "gjr").dm_qlike_vs_har_iv.p_value = 0.2;
  const fallback = v.verdictAnswer(weaker.vol_lab, data);
  assert.doesNotMatch(fallback, /beats all/);
  assert.equal(fallback, m.assertions.forecastAssertion(data.vol_forecast.validation, data.vol_forecast.live,
    { ovxLabel: m.research.ovxOnlyLabel(weaker.vol_lab) }));
  assert.equal(titleOf(render(data, weaker).html), fallback);

  // The OVX-alone clause follows the sign and size of both OVX comparisons.
  const loses = structuredClone(research);
  Object.assign(loses.vol_lab.leaderboard.models.find((model) => model.key === "ovx_only").dm_qlike_vs_har_iv, { stat: 2.5, p_value: 0.01 });
  assert.match(v.verdictAnswer(loses.vol_lab, data), /models but loses to OVX alone$/);
  const beats = structuredClone(research);
  for (const key of ["ovx_only", "raw_ovx"]) {
    Object.assign(beats.vol_lab.leaderboard.models.find((model) => model.key === key).dm_qlike_vs_har_iv, { stat: -2.5, p_value: 0.01 });
  }
  assert.match(v.verdictAnswer(beats.vol_lab, data), /models and OVX alone$/);
  const near = structuredClone(research);
  near.vol_lab.leaderboard.models.find((model) => model.key === "ovx_only").dm_qlike_vs_har_iv.p_value = 0.07;
  assert.match(v.verdictAnswer(near.vol_lab, data), /models; the comparison with OVX alone is inconclusive$/, "0.05 <= p < 0.10");
  const mixed = structuredClone(research);
  Object.assign(mixed.vol_lab.leaderboard.models.find((model) => model.key === "raw_ovx").dm_qlike_vs_har_iv, { stat: -2.5, p_value: 0.01 });
  assert.match(v.verdictAnswer(mixed.vol_lab, data), /returns-only models$/, "mixed: the first clause only");
});

test("every evidence row is its section's h2, linked, in section order", () => {
  const { html } = render(data, research);
  const rows = v.evidenceRows(data, research);
  assert.ok(rows.length >= 7, "all seven builders have their data in the fixtures");
  const evidence = html.match(/<section class="evidence"[\s\S]*?<\/section>/)[0];
  const items = [...evidence.matchAll(/<a href="#([a-z]+)" class="evidence-link"><span class="chip chip--code">([A-Z]+)<\/span><span class="evidence-text">([\s\S]*?)<\/span><\/a>/g)]
    .map((match) => ({ id: match[1], code: match[2], text: squash(visibleText(match[3])) }));
  assert.deepEqual(items.map((item) => item.id), rows.map((row) => row.id));
  const order = SECTIONS.map((section) => section.id);
  assert.deepEqual([...items.map((item) => item.id)].sort((a, b) => order.indexOf(a) - order.indexOf(b)), items.map((item) => item.id));
  for (const [index, item] of items.entries()) {
    const section = html.match(new RegExp(`<section id="${item.id}"[\\s\\S]*?<h2[^>]*>([\\s\\S]*?)</h2>`));
    assert.ok(section, item.id);
    assert.equal(item.text, squash(visibleText(section[1])), `${item.code} row equals its h2`);
    assert.equal(item.text, rows[index].text);
    assert.equal(item.code, SECTIONS.find((entry) => entry.id === item.id).code);
  }
});

test("a later recomputation on the same close is noted, not shown as the call; 'sealed' only with sealed_at", () => {
  const payload = structuredClone(data);
  const live = payload.vol_forecast.live;
  const entry = payload.vol_forecast.live_record.recent[0];
  assert.equal(entry.as_of, live.as_of, "the fixture records the live as-of");
  delete entry.sealed_at; // the fixture's entry is sealed; start from one recorded before sealing existed
  live.issued_at = "2026-10-21T15:10:59Z";
  live.interval_80.low_pct = round1(live.interval_80.low_pct + 0.1);
  live.interval_80.high_pct = round1(live.interval_80.high_pct + 0.1);
  live.challenger.forecast_next_week_vol_annualized_pct = round1(live.challenger.forecast_next_week_vol_annualized_pct + 0.1);
  const f = m.format;
  const hero = squash(visibleText(heroOf(render(payload, research).html)));
  assert.ok(hero.includes(`80% interval ${f.fmtNum(entry.lo80_pct, 1)}–${f.fmtNum(entry.hi80_pct, 1)}%`), "the recorded band");
  assert.ok(hero.includes(`Challenger ${f.fmtPct(entry.challenger_pct)}`), "the recorded challenger");
  assert.ok(hero.includes(`Recomputed ${f.fmtCT(Date.parse(live.issued_at))} on the same close: `
    + `band ${f.fmtNum(live.interval_80.low_pct, 1)}–${f.fmtNum(live.interval_80.high_pct, 1)}, `
    + `challenger ${f.fmtNum(live.challenger.forecast_next_week_vol_annualized_pct, 1)}. Not scored`), hero);
  assert.ok(!hero.includes("forecast " + f.fmtNum(live.forecast_next_week_vol_annualized_pct, 1)), "an unchanged field is not listed");
  assert.ok(hero.includes(`recorded ${f.fmtCT(Date.parse(entry.issued_at))}`));
  assert.doesNotMatch(hero, /sealed/i);
  // The forecast panel draws the same recorded call.
  const call = v.forecastOfRecord(live, payload.vol_forecast.live_record);
  assert.deepEqual([call.source, call.lo, call.hi, call.challenger], ["record", entry.lo80_pct, entry.hi80_pct, entry.challenger_pct]);
  assert.deepEqual(call.recompute.fields, ["lo", "hi", "challenger"]);

  entry.sealed_at = "2026-10-20T12:24:10Z";
  const sealed = squash(visibleText(heroOf(render(payload, research).html)));
  assert.ok(sealed.includes(`sealed ${f.fmtCT(Date.parse(entry.sealed_at))}`));
});

test("a live as-of the record does not hold yet shows the live values and says so", () => {
  const payload = structuredClone(data);
  const live = payload.vol_forecast.live;
  live.as_of = "2026-10-20";
  live.issued_at = "2026-10-21T12:00:00Z";
  const hero = squash(visibleText(heroOf(render(payload, research).html)));
  assert.ok(hero.includes(`${live.forecast_next_week_vol_annualized_pct} %`));
  assert.ok(hero.includes(`80% interval ${live.interval_80.low_pct}–${live.interval_80.high_pct}%`));
  assert.ok(hero.includes("Not in the live record yet"));
  assert.doesNotMatch(hero, /Target window|pts since|Recomputed/);
  assert.ok(hero.includes(`issued ${m.format.fmtCT(Date.parse(live.issued_at))}`));
});

test("a snapshot from an older close than the newest recorded call shows that call, and says so", () => {
  const payload = structuredClone(data);
  const live = payload.vol_forecast.live;
  const recent = payload.vol_forecast.live_record.recent;
  const newer = {
    ...structuredClone(recent[0]),
    as_of: "2026-10-26", issued_at: "2026-10-26T21:00:54Z", recorded_at: "2026-10-26T21:00:58Z", sealed_at: "2026-10-26T21:01:33Z",
    forecast_pct: 57.8, median_pct: 48.4, lo80_pct: 28.3, hi80_pct: 77.6, challenger_pct: 58.9,
    target_start: "2026-10-28", target_end: "2026-11-03",
    inputs: { ...(recent[0].inputs ?? {}), rv5_pct: 50.61, ovx: 57.1, ovx_session: "2026-10-23" },
  };
  recent.unshift(newer);
  // The newer window (Oct 28 – Nov 3) spans no roll, so it is the result due; the fixture's own spans one.
  Object.assign(payload.vol_forecast.live_record, { next_result_due: newer.target_end, n_pending: 2, n_recorded: 2 });
  live.issued_at = "2026-10-26T23:48:37Z";
  const call = v.forecastOfRecord(live, payload.vol_forecast.live_record);
  assert.equal(call.entry, newer);
  assert.equal(call.forecast, 57.8);
  assert.equal(call.recompute, null, "a different close is not a recomputation");
  assert.deepEqual(call.olderSnapshot, { asOf: live.as_of, issuedAt: live.issued_at });
  const hero = squash(visibleText(heroOf(render(payload, research).html)));
  assert.ok(hero.includes("57.8 %"), hero);
  assert.ok(hero.includes("80% interval 28.3–77.6%"), hero);
  assert.ok(hero.includes("Target window Oct 28 – Nov 3, 2026, scored on its settlements"), hero);
  assert.ok(hero.includes(`from an older close (${m.format.fmtDate(live.as_of, { year: false })})`), hero);
  assert.ok(hero.includes("recorded from the Oct 26 close and is the one scored"), hero);
  assert.doesNotMatch(hero, /Recomputed|Not in the live record yet/);
  // A newer entry of another model version does not displace the call.
  newer.model_version = "har-iv/3";
  assert.notEqual(v.forecastOfRecord(live, payload.vol_forecast.live_record).entry, newer);
});

test("with a snapshot from an older close, CONE and METH show the recorded call the hero shows, never the superseded one", () => {
  const payload = structuredClone(data);
  const live = payload.vol_forecast.live;
  const recent = payload.vol_forecast.live_record.recent;
  const inputs = { rv5_pct: 49.42, rv22_pct: 38.25, rv66_pct: 46.29, ovx: 60.02, ovx_session: "2026-10-23" };
  // A median the snapshot's coefficients reproduce from these inputs, and its mean, as the backend prints them.
  const fit = v.forecastDecomposition(live.coefficients, inputs, live.smearing_factor);
  const median = round1(fit.median);
  const forecast = round1(fit.mean);
  const newer = {
    ...structuredClone(recent[0]),
    as_of: "2026-10-26", issued_at: "2026-10-26T21:00:54Z", recorded_at: "2026-10-26T21:00:58Z", sealed_at: "2026-10-26T21:01:33Z",
    forecast_pct: forecast, median_pct: median, lo80_pct: 28.3, hi80_pct: 77.6, challenger_pct: 58.9,
    target_start: "2026-10-28", target_end: "2026-11-03", inputs,
  };
  recent.unshift(newer);
  live.issued_at = "2026-10-26T23:48:37Z";
  const record = v.liveOfRecord(live, payload.vol_forecast.live_record);
  assert.equal(record.as_of, "2026-10-26");
  assert.equal(record.forecast_next_week_vol_annualized_pct, forecast);
  assert.equal(record.fit_as_of, live.as_of, "the coefficients stay the snapshot's, dated");
  assert.deepEqual(record.inputs, inputs);
  assert.equal(v.liveOfRecord(data.vol_forecast.live, data.vol_forecast.live_record), data.vol_forecast.live, "the usual case is the live block itself");

  const f = m.format;
  const sectionOf = (html, id) => squash(visibleText(html.match(new RegExp(`<section id="${id}"[\\s\\S]*?</section>`))?.[0] ?? ""));
  const { html, errors } = render(payload, research);
  assert.deepEqual(errors, []);
  const cone = sectionOf(html, "cone");
  assert.ok(cone.includes(`5-day realized vol (${f.fmtPct(inputs.rv5_pct)}, Oct 26)`), cone);
  assert.ok(cone.includes(`Forecast, 5d ${f.fmtPct(forecast)} as of the Oct 26 close`), cone);
  assert.ok(cone.includes("marks: data.json, Oct 26, 2026 close"), cone);
  const lag = Math.round((Date.parse("2026-10-26") - Date.parse(research.vol_lab.vol_cone.as_of)) / 86400000);
  assert.ok(cone.includes(`cone ${lag} days older than the forecast`), cone);
  assert.ok(!cone.includes(f.fmtPct(live.forecast_next_week_vol_annualized_pct)), "the superseded forecast is not CONE's");
  const meth = sectionOf(html, "meth");
  assert.ok(meth.includes(`${f.fmtNum(median, 1)}% × √${f.fmtNum(live.smearing_factor, 3)} ≈ ${f.fmtNum(forecast, 1)}% for this call`), meth);
  assert.ok(meth.includes(`The live coefficients reproduce this call's ${f.fmtNum(median, 1)}% median`), meth);
  assert.ok(meth.includes(`Live fit as of ${f.fmtDate(live.as_of)} on every matured row, with the inputs of the call recorded from the Oct 26 close`), meth);
  assert.ok(!meth.includes(`${f.fmtNum(live.forecast_median_pct, 1)}% × √`), "the superseded median is not 'this call'");

  // Recorded values the snapshot's fit does not reproduce: METH names the snapshot computation as such.
  newer.median_pct = round1(median + 3);
  newer.forecast_pct = round1(newer.median_pct * Math.sqrt(live.smearing_factor) + 1);
  const off = sectionOf(render(payload, research).html, "meth");
  const snap = f.fmtDate(live.as_of, { year: false });
  assert.ok(off.includes(`HAR-IV coefficients: the ${snap} snapshot's fit, not the recorded Oct 26 call`), off);
  assert.ok(off.includes(`≈ ${f.fmtNum(live.forecast_median_pct * Math.sqrt(live.smearing_factor), 1)}% for the ${snap} snapshot computation, not the call recorded from the Oct 26 close`), off);
  assert.doesNotMatch(off, /for this call|reproduce this call/);
});

test("the target window is the recorded entry's", () => {
  const scorable = structuredClone(data);
  const entry = scorable.vol_forecast.live_record.recent[0];
  scorable.vol_forecast.live_record.next_result_due = entry.target_end;
  const hero = squash(visibleText(heroOf(render(scorable, research).html)));
  const f = m.format;
  assert.ok(hero.includes(`Target window ${f.fmtDate(entry.target_start, { year: false })} – ${f.fmtDate(entry.target_end)}, scored on its settlements`), hero);
  const moved = structuredClone(scorable);
  Object.assign(moved.vol_forecast.live_record.recent[0], { target_start: "2026-10-22", target_end: "2026-10-28" });
  assert.ok(squash(visibleText(heroOf(render(moved, research).html))).includes("Target window Oct 22 – Oct 28, 2026"));
});

test("a recorded call that will never be scored says why, in the hero and on the forecast chart, never 'scored' or 'pending'", () => {
  const f = m.format;
  const entry = data.vol_forecast.live_record.recent[0];
  const window = `Target window ${f.fmtDate(entry.target_start, { year: false })} – ${f.fmtDate(entry.target_end)}`;
  const heroWith = (change, record = {}) => {
    const payload = structuredClone(data);
    Object.assign(payload.vol_forecast.live_record.recent[0], change);
    Object.assign(payload.vol_forecast.live_record, record);
    return squash(visibleText(heroOf(render(payload, research).html)));
  };
  // The fixture's only call spans the CLX26 -> CLZ26 roll: no result is due for it.
  const cases = [
    [{}, {}, " spans a contract roll: recorded, never scored"],
    [{ spans_roll: true }, { next_result_due: "2026-12-01" }, " spans a contract roll: recorded, never scored"],
    [{ status: "skipped_roll", spans_roll: true }, { n_pending: 0, n_skipped_roll: 1 }, " spans a contract roll: recorded, never scored"],
    [{ status: "late_commit" }, { n_pending: 0, n_late_commit: 1 }, ", never scored: not sealed before the settlement that opens it"],
    [{ status: "expired" }, { n_pending: 0, n_expired: 1 }, ", never scored: its settlements did not arrive in time"],
    [{ spans_roll: false }, { next_result_due: entry.target_end }, ", scored on its settlements"],
  ];
  for (const [change, record, clause] of cases) {
    const hero = heroWith(change, record);
    assert.ok(hero.includes(`${window}${clause}`), `${JSON.stringify(change)}: ${hero}`);
    if (clause !== ", scored on its settlements") assert.doesNotMatch(hero, /scored on its settlements/);
  }
  // FCST's live point reads the same call: never scored, not pending.
  const fc = m.forecastChart;
  const rows = [{ date: "2026-10-12", actual: 40, forecast: 42, lo: 30, hi: 55, ovx: 50 }];
  const live = { date: entry.as_of, forecast: entry.forecast_pct, lo: entry.lo80_pct, hi: entry.hi80_pct, ovx: null };
  assert.equal(fc.liveRealizedText(fc.buildForecastData(rows, { ...live, scoring: "pending_roll" }).live), "never scored (roll)");
  assert.equal(fc.liveRealizedText(fc.buildForecastData(rows, { ...live, scoring: "late_commit" }).live), "never scored (late commit)");
  assert.equal(fc.liveRealizedText(fc.buildForecastData(rows, { ...live, scoring: "pending" }).live), "pending");
  assert.equal(fc.liveRealizedText(fc.buildForecastData(rows, live).live), "pending", "a call not in the record yet");
});

test("the challenger note says what the decided promotion test decided, and METH's card names it with a colon", () => {
  const noteOf = (payload) => {
    const html = render(payload, research).html;
    const item = heroOf(html).match(/<li[^>]*><span class="hero-note-label">Challenger[\s\S]*?<\/li>/)?.[0] ?? "";
    return squash(visibleText(item));
  };
  const meth = (payload) => squash(visibleText(render(payload, research).html.match(/<section id="meth"[\s\S]*?<\/section>/)[0]));
  const accruing = noteOf(data);
  assert.match(accruing, /replaces the model of record only through the 52-week live test \(0\/52\)\.$/, accruing);
  assert.ok(meth(data).includes("promotion test: accruing 0/52 weeks"));
  const decided = (status, extra) => {
    const payload = structuredClone(data);
    Object.assign(payload.vol_forecast.live_record.promotion, {
      status, n_independent_paired: 52, required: 52, ...extra,
    });
    return payload;
  };
  const keep = decided("keep", { dm_stat: 0.97, p_value: 0.168753, mean_qlike_difference: 0.004 });
  const kept = noteOf(keep);
  assert.match(kept, /the 52-week live test kept the model of record\.$/, kept);
  assert.doesNotMatch(kept, /replaces the model of record only through|keep the record/);
  assert.ok(meth(keep).includes("promotion test: keep the record"));
  const promote = decided("promote", { dm_stat: 2.86, p_value: 0.003041, mean_qlike_difference: 0.03 });
  const favoured = noteOf(promote);
  assert.match(favoured, /the 52-week live test favours it; the swap is a maintainer's change, not yet applied\.$/, favoured);
  assert.doesNotMatch(favoured, /replaces the model of record only through|promote the challenger/);
  // A call from another version than the pinned record's does not claim the swap is unapplied.
  promote.vol_forecast.live.model_version = "har-iv-range/1";
  promote.vol_forecast.live_record.recent[0].model_version = "har-iv-range/1";
  const other = noteOf(promote);
  assert.match(other, /favours it; the swap is a maintainer's change\.$/, other);
});

test("the change since the previous call is computed from the record, newest first", () => {
  const payload = structuredClone(data);
  const record = payload.vol_forecast.live_record;
  const entry = record.recent[0];
  const previous = { ...entry, as_of: "2026-10-16", issued_at: "2026-10-17T12:20:00Z", forecast_pct: 45.1, target_start: "2026-10-20", target_end: "2026-10-24" };
  record.recent = [previous, entry];   // out of order on purpose
  const f = m.format;
  const hero = squash(visibleText(heroOf(render(payload, research).html)));
  const delta = round1(entry.forecast_pct) - previous.forecast_pct;
  assert.ok(hero.includes(`${f.fmtSigned(delta, 1)} pts since the Oct 16 call (${f.fmtNum(previous.forecast_pct, 1)})`), hero);
  assert.ok(hero.includes(`Recorded ${f.fmtDate(entry.as_of, { year: false })} ${f.fmtNum(entry.forecast_pct, 1)} Oct 16 45.1`), "newest first");
  assert.ok(hero.includes("All recorded calls →"));
  // Another model version is not a comparable previous call.
  record.recent = [entry, { ...previous, model_version: "har-iv/1" }];
  assert.doesNotMatch(squash(visibleText(heroOf(render(payload, research).html))), /pts since/);
});

test("the band's track record and the typical miss are counts over the production weeks, naming n", () => {
  const recent = data.vol_forecast.recent_oos;
  let inside = 0, below = 0, above = 0;
  const errors = [];
  recent.date.forEach((_, i) => {
    const [actual, lo, hi, forecast] = [recent.actual[i], recent.lo80[i], recent.hi80[i], recent.forecast[i]];
    if (actual != null && lo != null && hi != null) {
      if (actual < lo) below += 1;
      else if (actual > hi) above += 1;
      else inside += 1;
    }
    if (actual != null && forecast != null) errors.push(Math.abs(actual - forecast));
  });
  errors.sort((a, b) => a - b);
  const n = inside + below + above;
  const track = v.bandTrackRecord(recent);
  assert.deepEqual([track.n, track.inside, track.below, track.above], [n, inside, below, above]);
  const miss = v.typicalMiss(recent);
  const mid = errors.length / 2;
  const median = errors.length % 2 ? errors[Math.floor(mid)] : (errors[mid - 1] + errors[mid]) / 2;
  assert.equal(miss.n, errors.length);
  assert.ok(Math.abs(miss.median - median) < 1e-9);
  assert.equal(miss.upper, errors[Math.ceil(0.8 * errors.length) - 1], "nearest-rank 80th percentile");
  const f = m.format;
  const hero = squash(visibleText(heroOf(render(data, research).html)));
  assert.ok(hero.includes(`In ${inside} of ${n} out-of-sample weeks realized vol landed inside that week's 80% band; ${below} below, ${above} above (production validation, `), hero);
  assert.ok(hero.includes(`±${f.fmtNum(median, 1)} pts median |realized − forecast|, last ${errors.length} wk; 80th percentile ${f.fmtNum(miss.upper, 1)}`), hero);
  assert.ok(hero.includes(`below: ${below} wk`) && hero.includes(`above: ${above} wk`), "the tails are labelled");
});

test("P(vol rises) is one sentence: a whole percent, not calibrated, outside the facts, never a frequency", () => {
  const { html } = render(data, research);
  const hero = heroOf(html);
  const facts = hero.match(/<dl class="hero-facts">[\s\S]*?<\/dl>/)[0];
  assert.doesNotMatch(facts, /P\(vol rises\)/);
  const line = squash(visibleText(hero.match(/<li class="hero-prob">([\s\S]*?)<\/li>/)[1]));
  const live = data.vol_forecast.live;
  const f = m.format;
  assert.ok(line.includes(`exceeds the last 5 sessions' ${f.fmtPct(live.current_realized_vol_5d_annualized_pct)}: ${Math.round(live.prob_vol_rises_pct)}% `), line);
  assert.ok(!line.includes(`${live.prob_vol_rises_pct}%`), "no decimals");
  assert.ok(line.includes(`lognormal around the ${f.fmtPct(live.forecast_median_pct)} median, log sd ${f.fmtNum(live.residual_sd, 2)}`));
  assert.match(line, /not calibrated; direction is not evidence of skill/);
  assert.doesNotMatch(line, /\b\d+ (of|in) \d+\b/, "no frequency wording");
  // The sentence reproduces the backend's number: P = 1 - Φ((ln rv5 - ln median) / sd).
  const z = (Math.log(live.current_realized_vol_5d_annualized_pct) - Math.log(live.forecast_median_pct)) / live.residual_sd;
  const erf = (x) => { const t = 1 / (1 + 0.3275911 * Math.abs(x)); const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x); return x >= 0 ? y : -y; };
  assert.equal(Math.round(100 * (1 - 0.5 * (1 + erf(z / Math.SQRT2)))), Math.round(live.prob_vol_rises_pct));
});

test("the arithmetic reproduces the median and the expected forecast, or is not shown", () => {
  const live = data.vol_forecast.live;
  const decomposition = v.forecastDecomposition(live.coefficients, live.inputs, live.smearing_factor);
  assert.ok(Math.abs(decomposition.median - live.forecast_median_pct) < 0.05);
  assert.ok(Math.abs(decomposition.mean - live.forecast_next_week_vol_annualized_pct) < 0.05);
  assert.ok(Math.abs(decomposition.printedMedian - live.forecast_median_pct) < 0.05, "with the numbers as printed");
  const { html } = render(data, research);
  assert.match(html, /class="disclosure hero-math"/);
  const math = squash(visibleText(html.match(/<details class="disclosure hero-math">[\s\S]*?<\/details>/)[0]));
  const f = m.format;
  assert.ok(math.includes(`How ${f.fmtNum(live.forecast_next_week_vol_annualized_pct, 1)} is computed`));
  assert.ok(math.includes(`× ${v.plainNumber(live.inputs.ovx, 2)}^${v.plainNumber(live.coefficients[4], 6)} = ${f.fmtPct(decomposition.shownMedian, 2)}`), math);
  assert.ok(math.includes(`median = exp(${v.plainNumber(live.coefficients[0], 6)})`), math);
  assert.ok(math.includes(`expected = ${f.fmtNum(decomposition.shownMedian, 2)} × √${v.plainNumber(live.smearing_factor, 4)} (Duan smearing) = ${f.fmtPct(decomposition.printedMean, 2)}`), math);
  assert.ok(math.includes(`Recorded to one decimal: median ${f.fmtPct(live.forecast_median_pct)}, expected ${f.fmtPct(live.forecast_next_week_vol_annualized_pct)}.`), math);
  // The two-decimal chain lands within rounding of the recorded one-decimal forecast.
  assert.ok(Math.abs(decomposition.printedMean - live.forecast_next_week_vol_annualized_pct) < 0.06);
  assert.ok(squash(visibleText(heroOf(html))).includes("Elasticity of the forecast: OVX "
    + `${f.fmtNum(live.coefficients[4], 2)}, 5-day RV ${f.fmtNum(live.coefficients[1], 2)}, 22-day ${f.fmtNum(live.coefficients[2], 2)}, `
    + `66-day ${f.fmtNum(live.coefficients[3], 2)} (log-log, current fit).`));

  const corrupt = structuredClone(data);
  corrupt.vol_forecast.live.coefficients[4] += 0.01;
  assert.doesNotMatch(render(corrupt, research).html, /hero-math/, "a coefficient that no longer reproduces the call hides the arithmetic");
  const short = structuredClone(data);
  short.vol_forecast.live.coefficients = short.vol_forecast.live.coefficients.slice(0, 4);
  const plain = render(short, research);
  assert.deepEqual(plain.errors, []);
  assert.doesNotMatch(plain.html, /hero-math|Elasticity of the forecast/, "a model without OVX has no five-coefficient line");
});

test("without research.json the answer is the forecast panel's claim and the index lists data.json sections only", () => {
  const { html, errors } = render(data, null);
  assert.deepEqual(errors, []);
  const expected = m.assertions.forecastAssertion(data.vol_forecast.validation, data.vol_forecast.live, { ovxLabel: m.research.ovxOnlyLabel(null) });
  assert.equal(titleOf(html), expected);
  assert.equal(v.verdictAnswer(null, data), expected);
  const detail = visibleText(html.match(/<p class="verdict-answer-detail">([\s\S]*?)<\/p>/)[1]);
  assert.ok(detail.startsWith(`Weekly production validation (${data.vol_forecast.validation.n} weeks)`), detail);
  const codes = v.evidenceRows(data, null).map((row) => row.code);
  for (const code of ["MODL", "STAB", "CALB", "RISK"]) assert.ok(!codes.includes(code), code);
  assert.ok(codes.includes("FCST") && codes.includes("LIVE"));
  const evidence = html.match(/<section class="evidence"[\s\S]*?<\/section>/)[0];
  assert.doesNotMatch(evidence, />(MODL|STAB|CALB|RISK)</);
  // Still loading: the lede is busy and the lab's lines keep their place.
  const loading = render(data, null, "loading").html;
  assert.match(loading, /<div class="verdict-lede" aria-busy="true">/);
  assert.match(loading, /<p class="hero-scale is-pending" aria-hidden="true"><\/p>/);
  assert.doesNotMatch(render(data, research).html, /aria-busy="true"><p class="micro">Question/);
});

test("the question is static and the hero label keeps the version's case", () => {
  const { html } = render(data, research);
  assert.match(html, /<p class="micro">Question<\/p><p class="verdict-question">How well can next-week WTI realized volatility be forecast from free data\?<\/p>/);
  assert.match(heroOf(html), /<p class="micro verdict-label">This week&#x27;s call · HAR-IV <span class="mono">har-iv\/2<\/span><\/p>/);
  // The question is the only verdict text with no number: the answer is computed (see the first test).
  assert.ok(html.indexOf('class="verdict-lede"') < html.indexOf('class="verdict-hero"'));
  assert.ok(html.indexOf('class="kpi-grid"') < html.indexOf('class="evidence"'), "the index follows the tiles");
});

// Two consecutive recorded calls that each store their fit (vol_record entries from 2.2.0 on): the
// Oct 19 call with the fixture's fit, and an Oct 20 call refit on one more day, its median and forecast
// printed from that fit as the backend prints them (each rounded from the unrounded fit).
const attributedRecord = () => {
  const payload = structuredClone(data);
  const live = payload.vol_forecast.live;
  const [previousRow] = payload.vol_forecast.live_record.recent;
  const previous = { ...previousRow, coefficients: [...live.coefficients], smearing_factor: live.smearing_factor };
  const coefficients = [-4.883566, 0.038819, 0.170853, -0.151549, 1.03495];
  const smearing = 1.4128;
  const inputs = { ovx: 50.22, ovx_session: "2026-10-19", rv5_pct: 38.85, rv22_pct: 36.93, rv66_pct: 46.31 };
  const fit = v.forecastDecomposition(coefficients, inputs, smearing);
  const median = round1(fit.median);
  const forecast = round1(fit.mean);
  const current = {
    ...structuredClone(previousRow), as_of: "2026-10-20", issued_at: "2026-10-21T05:03:50Z", recorded_at: "2026-10-21T05:03:53Z",
    sealed_at: "2026-10-21T05:04:20Z", base_session: "2026-10-21", target_start: "2026-10-22", target_end: "2026-10-28",
    forecast_pct: forecast, median_pct: median, lo80_pct: 23.1, hi80_pct: 63.6, challenger_pct: 47.6, inputs, coefficients,
    smearing_factor: smearing,
  };
  payload.vol_forecast.live_record.recent = [current, previous];
  Object.assign(live, {
    as_of: "2026-10-20", issued_at: current.issued_at, coefficients, smearing_factor: smearing,
    forecast_next_week_vol_annualized_pct: forecast, forecast_median_pct: median,
    interval_80: { ...live.interval_80, low_pct: 23.1, high_pct: 63.6 },
    inputs: { ...live.inputs, ...inputs }, current_realized_vol_5d_annualized_pct: round1(inputs.rv5_pct),
    implied_vol_ovx_pct: round1(inputs.ovx),
    challenger: { ...live.challenger, as_of: "2026-10-20", forecast_next_week_vol_annualized_pct: 47.6 },
  });
  return { payload, current, previous };
};

test("what moved the call: the stored fits split the change exactly into input moves, the refit and smearing", () => {
  const { current, previous } = attributedRecord();
  const split = v.changeAttribution(current, previous);
  assert.ok(split, "both calls reproduce from their own fits");
  assert.deepEqual(split.steps.map((step) => step.id), ["ovx", "rv5", "rv22", "rv66", "refit", "smearing"]);
  // The endpoints are each call's own forecast (within the printed precision) ...
  assert.ok(Math.abs(split.from - previous.forecast_pct) < v.TOLERANCE);
  assert.ok(Math.abs(split.to - current.forecast_pct) < v.TOLERANCE);
  // ... the steps add up to the change exactly, in points and in logs ...
  const sum = split.steps.reduce((total, step) => total + step.points, 0);
  assert.ok(Math.abs(sum - split.total) < 1e-9);
  assert.ok(Math.abs(split.to - split.from - split.total) < 1e-12);
  const logSum = split.steps.reduce((total, step) => total + step.logPct / 100, 0);
  assert.ok(Math.abs(logSum - Math.log(split.to / split.from)) < 1e-12);
  // ... and each input's step is its move valued at the previous call's coefficient.
  const ovx = split.steps[0];
  assert.deepEqual([ovx.from, ovx.to], [previous.inputs.ovx, current.inputs.ovx]);
  assert.ok(Math.abs(ovx.logPct / 100 - previous.coefficients[4] * Math.log(current.inputs.ovx / previous.inputs.ovx)) < 1e-12);
  const rv5 = split.steps[1];
  assert.ok(Math.abs(rv5.points - split.from * Math.exp(ovx.logPct / 100) * (Math.exp(rv5.logPct / 100) - 1)) < 1e-9, "steps multiply in order");
  // Unchanged fit and inputs: every step is zero.
  const same = v.changeAttribution({ ...previous, as_of: "2026-10-20" }, previous);
  assert.ok(same.steps.every((step) => Math.abs(step.points) < 1e-12));
});

test("what moved the call is shown only when both calls reproduce from their own recorded fits", () => {
  const { current, previous } = attributedRecord();
  const without = (row, key) => { const copy = structuredClone(row); delete copy[key]; return copy; };
  assert.equal(v.changeAttribution(current, without(previous, "coefficients")), null, "an entry recorded before the fit was stored");
  assert.equal(v.changeAttribution(without(current, "smearing_factor"), previous), null);
  assert.equal(v.changeAttribution(current, { ...previous, inputs: null }), null);
  assert.equal(v.changeAttribution(current, { ...previous, model_version: "har/2" }), null, "another model version");
  const off = structuredClone(current);
  off.coefficients[4] += 0.01;
  assert.equal(v.changeAttribution(off, previous), null, "a fit that no longer reproduces its call");
  assert.equal(v.changeAttribution(current, { ...previous, coefficients: previous.coefficients.slice(0, 4) }), null, "plain-HAR fit");
  assert.equal(v.changeAttribution(null, previous), null);
});

test("the hero lists what moved the call since the previous one, in points that add up to the change", () => {
  const { payload, current, previous } = attributedRecord();
  const { html, errors } = render(payload, research);
  assert.deepEqual(errors, []);
  const block = html.match(/<details class="disclosure hero-math hero-attribution">[\s\S]*?<\/details>/)?.[0];
  assert.ok(block, "the disclosure renders");
  const text = squash(visibleText(block));
  const f = m.format;
  const split = v.changeAttribution(current, previous);
  assert.ok(text.includes("What moved it since the Oct 19 call"), text);
  assert.ok(text.includes("Change in the expected forecast, Oct 19 call to Oct 20 call, in vol points"), text);
  assert.ok(text.includes(`OVX ${f.fmtNum(previous.inputs.ovx, 2)} → ${f.fmtNum(current.inputs.ovx, 2)} ${f.fmtSigned(split.steps[0].points, 2)}`), text);
  assert.ok(text.includes(`5-day RV ${f.fmtPct(previous.inputs.rv5_pct, 2)} → ${f.fmtPct(current.inputs.rv5_pct, 2)}`), text);
  assert.ok(text.includes("Refit Oct 19 fit → Oct 20 fit"), text);
  assert.ok(text.includes(`Smearing ${v.plainNumber(previous.smearing_factor, 4)} → ${v.plainNumber(current.smearing_factor, 4)}`), text);
  assert.ok(text.includes(`Expected forecast ${f.fmtNum(split.from, 2)} → ${f.fmtNum(split.to, 2)} ${f.fmtSigned(split.total, 2)}`), text);
  assert.match(block, /<caption>/);
  assert.match(block, /<th scope="row">OVX<\/th>/);
  // The change line and the split agree on the sign and, at one decimal, on the size.
  const hero = squash(visibleText(heroOf(html)));
  assert.ok(hero.includes(`${f.fmtSigned(round1(current.forecast_pct - previous.forecast_pct), 1)} pts since the Oct 19 call`), hero);
  assert.ok(Math.abs(split.total - (current.forecast_pct - previous.forecast_pct)) < 2 * v.TOLERANCE);

  // The fixture's record stores no fit: no split.
  assert.doesNotMatch(render(data, research).html, /hero-attribution/);
  // A fit that does not reproduce its call hides the split; the change line stays.
  const off = structuredClone(payload);
  off.vol_forecast.live_record.recent[1].coefficients[0] += 0.02;
  const corrupt = render(off, research);
  assert.doesNotMatch(corrupt.html, /hero-attribution/);
  assert.match(corrupt.html, /pts since the Oct 19 call/);
});

test("the arithmetic shows when only the one-decimal median times √smearing misses the forecast (the Oct 27, 2026 call)", () => {
  // 47.5 × √1.4128 = 56.46, which prints 56.5, yet the fit gives 47.45 and 56.40, recorded as 47.5 and 56.4.
  const payload = structuredClone(data);
  const live = payload.vol_forecast.live;
  const inputs = { rv5_pct: 48.65, rv22_pct: 37.69, rv66_pct: 45.95, ovx: 58.71, ovx_session: "2026-10-26" };
  Object.assign(live, {
    coefficients: [-4.883566, 0.038819, 0.170853, -0.151549, 1.03495], smearing_factor: 1.4128,
    forecast_median_pct: 47.5, forecast_next_week_vol_annualized_pct: 56.4, inputs: { ...live.inputs, ...inputs },
  });
  const decomposition = v.forecastDecomposition(live.coefficients, live.inputs, live.smearing_factor);
  assert.ok(Math.abs(47.5 * Math.sqrt(1.4128) - 56.4) > v.TOLERANCE, "the one-decimal chain misses");
  assert.equal(decomposition.shownMedian, 47.45);
  assert.ok(v.reproduces(decomposition, 47.5, 56.4));
  payload.vol_forecast.live_record.recent = [];
  const { html, errors } = render(payload, research);
  assert.deepEqual(errors, []);
  const math = squash(visibleText(html.match(/<details class="disclosure hero-math">[\s\S]*?<\/details>/)?.[0] ?? ""));
  assert.ok(math.includes("How 56.4 is computed"), math);
  assert.ok(math.includes("= 47.45%"), math);
  assert.ok(math.includes("expected = 47.45 × √1.4128 (Duan smearing) = 56.40%"), math);
});
