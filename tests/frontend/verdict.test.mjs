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

test("the target window is the recorded entry's", () => {
  const entry = data.vol_forecast.live_record.recent[0];
  const hero = squash(visibleText(heroOf(render(data, research).html)));
  const f = m.format;
  assert.ok(hero.includes(`Target window ${f.fmtDate(entry.target_start, { year: false })} – ${f.fmtDate(entry.target_end)}, scored on its settlements`), hero);
  const moved = structuredClone(data);
  Object.assign(moved.vol_forecast.live_record.recent[0], { target_start: "2026-10-22", target_end: "2026-10-28" });
  assert.ok(squash(visibleText(heroOf(render(moved, research).html))).includes("Target window Oct 22 – Oct 28, 2026"));
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
  assert.ok(math.includes(`× ${v.plainNumber(live.inputs.ovx, 2)}^${v.plainNumber(live.coefficients[4], 4)} = ${f.fmtPct(live.forecast_median_pct)}`), math);
  assert.ok(math.includes(`expected = median × √${v.plainNumber(live.smearing_factor, 4)} (Duan smearing) = ${f.fmtPct(live.forecast_next_week_vol_annualized_pct)}`));
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
