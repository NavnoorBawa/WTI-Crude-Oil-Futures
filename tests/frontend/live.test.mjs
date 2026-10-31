// LIVE, the sealed forward record: calls are counted as calls and only non-overlapping windows as
// independent weeks; no loss comparison is headlined on too few of them; the promotion test reads
// as a short state with its p-value printed once; every recorded call is drawn and tabled with
// whether it landed in its band and when it was sealed; and the record's commit history is one
// link away. Rendered against the fixtures, with the live record varied per test.
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, readSource, visibleText } from "./load.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let panel;
let chart;
let config;
before(async () => {
  m = await loadDashboard();
  panel = await m.load("/src/panels/LiveRecordPanel.jsx");
  chart = await m.load("/src/charts/LiveCalls.jsx");
  config = await m.load("/src/app/config.js");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");
/** Text with no-break spaces read as spaces (fmtP keeps "p = 0.38" on one line with them). */
const spaced = (text) => text.replace(/\u00a0/g, " ");

const render = (payload) => {
  const errors = [];
  const original = console.error;
  console.error = (...args) => errors.push(args.join(" "));
  try {
    const html = renderToStaticMarkup(createElement(m.dashboard.default, {
      data: payload, research, researchStatus: "ready", quote: m.quote.resolveQuote(null, payload),
    }));
    const live = html.match(/<section id="live"[\s\S]*?<\/section>/)[0];
    const title = spaced(visibleText(live.match(/<h2[^>]*>([\s\S]*?)<\/h2>/)[1]).trim());
    const meta = spaced(visibleText(live.match(/<p class="panel-meta[^"]*">([\s\S]*?)<\/p>/)[1]));
    const tile = html.match(/<a href="#live" class="kpi-link">[\s\S]*?<\/div>/)[0];
    const foot = spaced(visibleText(tile.match(/<dd class="kpi-foot">([\s\S]*?)<\/dd>/)?.[1] ?? "").trim());
    return { html, live, text: spaced(visibleText(live)), title, meta, foot, errors };
  } finally {
    console.error = original;
  }
};

/** A resolved recent row on the fixture's call, with its own date and outcome. */
const resolvedRow = (asOf, realized, extra = {}) => ({
  ...data.vol_forecast.live_record.recent[0], as_of: asOf, status: "resolved", realized_pct: realized, ...extra,
});

/** The fixture with `resolved` calls resolved, `independent` of them in non-overlapping windows. */
const scored = ({ resolved, independent, paired = resolved, promotion = {}, recent = null, champion = {} }) => {
  const payload = structuredClone(data);
  const record = payload.vol_forecast.live_record;
  Object.assign(record, {
    n_recorded: resolved + 1, n_resolved: resolved, n_pending: 1, n_independent: independent, next_result_due: "2026-12-04",
    champion: {
      ...record.champion, n: resolved, qlike: 0.164, interval_80_coverage_pct: 77.4, direction_hit_pct: 60.4,
      mean_forecast_pct: 45.2, mean_realized_pct: 43.9, ...champion,
    },
    challenger: { ...record.challenger, n: paired, qlike: 0.158, champion_qlike: 0.154 },
    promotion: {
      ...record.promotion, n_independent_paired: Math.min(independent, record.promotion.required),
      n_excluded_no_challenger: resolved - paired, ...promotion,
    },
  });
  if (recent) record.recent = recent;
  return payload;
};

const KEEP = { status: "keep", n_independent_paired: 52, required: 52, dm_stat: 0.3, p_value: 0.38, mean_qlike_difference: 0.0065 };

test("calls are counted as calls; only non-overlapping windows are called weeks", () => {
  const view = render(scored({ resolved: 53, independent: 11 }));
  assert.deepEqual(view.errors, []);
  assert.equal(view.title, "On 53 resolved calls (11 independent weeks), QLIKE 0.154 record vs 0.158 challenger");
  assert.doesNotMatch(view.live, /live weeks?\b/);
  assert.doesNotMatch(view.foot, /live weeks?\b|\bwk\b/);
  assert.ok(view.text.includes("53 resolved (11 independent weeks)"), "status line");
  assert.ok(view.text.includes("QLIKE, same calls 0.154 / 0.158 record / HAR-IV-range, n 53"));
  assert.ok(view.text.includes("QLIKE, record, all calls 0.164 har-iv/2, n 53"));
});

test("below ten independent weeks the title states progress, not a comparison", () => {
  const view = render(scored({ resolved: 12, independent: 3 }));
  assert.equal(view.title, "3 independent weeks resolved (12 calls); too few to compare the two models");
  assert.equal(view.foot, "12 calls resolved (3 independent weeks)", "no QLIKE pair on the tile either");
  // Nor in the facts under the title: the pair waits for the same ten independent weeks.
  assert.ok(view.text.includes("QLIKE, same calls — needs 10 independent weeks (3 so far)"), view.text);
  assert.doesNotMatch(view.text, /0\.154 \/ 0\.158/);
  assert.ok(view.text.includes("QLIKE, record, all calls 0.164 har-iv/2, n 12"), "the record's own QLIKE stays");
  assert.equal(m.research.LIVE_MIN_INDEPENDENT, 10);
  const nine = render(scored({ resolved: 40, independent: 9 }));
  assert.match(nine.title, /too few to compare/);
  assert.match(render(scored({ resolved: 40, independent: 10 })).title, /^On 40 resolved calls \(10 independent weeks\), QLIKE/);
});

test("every LIVE title stays within 90 characters in every state", () => {
  const states = [
    data,
    scored({ resolved: 1, independent: 1 }),
    scored({ resolved: 12, independent: 3 }),
    scored({ resolved: 53, independent: 11 }),
    scored({ resolved: 53, independent: 11, paired: 50 }),
    scored({ resolved: 1234, independent: 250 }),
    scored({ resolved: 1234, independent: 250, paired: 1199 }),
    scored({ resolved: 300, independent: 60, promotion: KEEP }),
    scored({ resolved: 300, independent: 60, promotion: { ...KEEP, status: "promote", dm_stat: 2.1, p_value: 0.018 } }),
  ];
  for (const payload of states) {
    const { html } = render(payload);
    const live = html.match(/<section id="live"[\s\S]*?<\/section>/)[0];
    for (const match of live.matchAll(/<(h2|h3)[^>]*>([\s\S]*?)<\/\1>/g)) {
      const title = visibleText(match[2]).trim();
      assert.ok(title.length <= 90, `${title.length}: ${title}`);
    }
  }
});

test("a decided promotion test reads as a short state, with its p-value printed once among the facts", () => {
  const payload = scored({ resolved: 300, independent: 60, promotion: KEEP });
  const view = render(payload);
  assert.equal(view.title, "Promotion test on 52 independent weeks: keep the model of record (p = 0.38)");
  assert.ok(view.meta.includes("promotion test: keep the record"), view.meta);
  assert.doesNotMatch(view.meta, /p = /, "the meta line carries the state only");
  const facts = [...view.live.matchAll(/<dl class="facts[^"]*">([\s\S]*?)<\/dl>/g)].map((match) => spaced(visibleText(match[1]))).join(" ");
  assert.match(facts, /Promotion test Keep DM \+0\.30, one-sided p = 0\.38, 52 independent weeks/);
  assert.equal(facts.match(/p = /g).length, 1, facts);
  assert.equal(view.foot, "300 calls resolved (60 independent weeks) · QLIKE 0.154 record vs 0.158 challenger"
    + " · promotion test: keep the record (p = 0.38)");
  const promote = render(scored({ resolved: 300, independent: 60, promotion: { ...KEEP, status: "promote", dm_stat: 2.1, p_value: 0.03 } }));
  assert.equal(promote.title, "Promotion test on 52 independent weeks: promote the challenger (p = 0.03)");
  assert.ok(promote.meta.includes("promotion test: promote the challenger"));
  assert.match(promote.text, /Promotion test Promote DM \+2\.10, one-sided p = 0\.03, 52 independent weeks/);
  const { promotionDetail, promotionText } = m.assertions;
  assert.equal(promotionText({ status: "accruing", paired: 3, required: 52 }), "accruing 3/52 weeks");
  assert.equal(promotionDetail({ status: "accruing", paired: 3, required: 52 }), null);
  assert.equal(promotionDetail({ status: "keep", dmStat: null, p: null, meanDifference: 0, paired: 52 }),
    "mean QLIKE difference 0.0000, 52 independent weeks", "identical losses: nothing to test");
});

test("the verdict tile's foot is liveTileFoot of the same record", () => {
  const { liveRecordSummary } = m.research;
  const { liveTileFoot } = m.assertions;
  for (const payload of [data, scored({ resolved: 12, independent: 3 }), scored({ resolved: 53, independent: 11, paired: 50 }),
    scored({ resolved: 300, independent: 60, promotion: KEEP })]) {
    assert.equal(render(payload).foot, spaced(liveTileFoot(liveRecordSummary(payload.vol_forecast.live_record))));
  }
  const due = liveRecordSummary({ ...data.vol_forecast.live_record, next_result_due: "2026-10-28" });
  assert.equal(liveTileFoot(due), "first result Oct 28, 2026 · promotion test 0/52 weeks");
  assert.equal(liveTileFoot(liveRecordSummary(data.vol_forecast.live_record)), "1 call recorded, no result due · promotion test 0/52 weeks");
  assert.equal(liveTileFoot(null), null);
});

test("the table says whether each resolved call landed in its band", () => {
  const recent = [
    { ...data.vol_forecast.live_record.recent[0], as_of: "2026-11-09", status: "pending" },
    resolvedRow("2026-11-06", 70.0),     // above the 23.3–63.9 band
    resolvedRow("2026-11-02", 40.0),     // inside it
  ];
  const view = render(scored({ resolved: 2, independent: 1, recent, champion: { interval_80_coverage_pct: 50 } }));
  const rows = [...view.live.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map((match) => visibleText(match[1]).trim());
  const row = (date) => rows.find((text) => text.startsWith(date));
  assert.match(row("Nov 6, 2026"), / 70\.0 outside band no /);
  assert.match(row("Nov 2, 2026"), / 40\.0 in band yes /);
  assert.match(row("Nov 9, 2026"), / — — /, "pending: no realized value, no verdict");
  assert.ok(view.live.includes('<span class="live-outside">no</span>'));
  assert.deepEqual(recent.map(panel.inBand), [null, "no", "yes"]);
  // Few resolved calls, all of them in the table: coverage is the count, not a percentage.
  assert.ok(view.text.includes("80% coverage 1 of 2 inside record"), view.text);
  assert.ok(render(scored({ resolved: 53, independent: 11 })).text.includes("80% coverage 77.4% record, n 53"));
});

test("a constant model version is named once in the caption instead of a column", () => {
  const view = render(data);
  const head = view.live.match(/<thead[\s\S]*?<\/thead>/)[0];
  assert.doesNotMatch(head, />Model</);
  assert.ok(view.text.includes("Latest 1 of 1 recorded call (percent, annualized); all from har-iv/2"));
  const mixed = structuredClone(data);
  const record = mixed.vol_forecast.live_record;
  record.recent = [record.recent[0], { ...record.recent[0], as_of: "2026-10-16", model_version: "har/2", status: "late_commit" }];
  record.n_recorded = 2;
  const both = render(mixed);
  assert.match(both.live.match(/<thead[\s\S]*?<\/thead>/)[0], />Model</);
  assert.doesNotMatch(both.text, /all from/);
});

test("unscored statuses are explained in visible text under the table", () => {
  const payload = structuredClone(data);
  const record = payload.vol_forecast.live_record;
  record.recent = [{ ...record.recent[0], status: "late_commit" }, { ...record.recent[0], as_of: "2026-10-16", status: "skipped_roll" }];
  const { live, text } = render(payload);
  // visibleText puts a space after every tag, so "<b>late commit</b>:" reads "late commit :".
  const footnote = visibleText(live.match(/<div class="table-footnote">([\s\S]*?)<\/div>/)[1]).replace(/ :/g, ":");
  assert.ok(footnote.includes("late commit: Not sealed in time (less than five minutes before the settlement that opens its window, or never); never scored."));
  // Why a roll prevents scoring, in words.
  assert.ok(footnote.includes("skipped: roll: The target window spans a contract roll: on the roll day CL=F's return measures"
    + " the spread between two contracts, not a price move; never scored."), footnote);
  assert.doesNotMatch(text, /late_commit|skipped_roll/, "labels, never keys");
  const scorable = structuredClone(data);
  Object.assign(scorable.vol_forecast.live_record, { next_result_due: scorable.vol_forecast.live_record.recent[0].target_end });
  assert.doesNotMatch(visibleText(render(scorable).live), /never scored\./, "no footnote while every call is scorable");
});

test("a pending call whose window spans a contract roll says it will not be scored, and no result is due", () => {
  // The fixture's only call (window Oct 21 – Oct 27) spans the CLX26 -> CLZ26 roll: next_result_due is null.
  assert.equal(data.vol_forecast.live_record.next_result_due, null);
  const row = data.vol_forecast.live_record.recent[0];
  assert.equal(m.research.callScoring(row, data.vol_forecast.live_record), "pending_roll", "inferred from next_result_due");
  assert.equal(m.research.callScoring({ ...row, spans_roll: false }, data.vol_forecast.live_record), "pending", "the row's own flag decides");
  assert.equal(m.research.callScoring({ ...row, spans_roll: true }, { next_result_due: "2026-12-01", n_pending: 2 }), "pending_roll");
  assert.equal(m.research.callScoring(row, { next_result_due: "2026-11-02", n_pending: 2 }), "pending_roll", "ends before the date due");
  assert.equal(m.research.callScoring(row, { next_result_due: row.target_end, n_pending: 2 }), "pending");
  assert.equal(m.research.callScoring({ ...row, status: "late_commit" }, null), "late_commit");
  const { text } = render(data);
  assert.ok(text.includes("No result is due: the pending call's window spans a contract roll, so it will not be scored."), text);
  assert.doesNotMatch(text, /Scores appear once the first target window closes/);
  const footnote = text.replace(/ :/g, ":");
  assert.ok(footnote.includes("pending spans roll"), "the row says so beside its status");
  assert.ok(footnote.includes("pending, spans roll: The target window spans a contract roll; it will be closed as skipped, never scored."));
  // With the row flagged false and a result due, the row reads plain pending.
  const scorable = structuredClone(data);
  Object.assign(scorable.vol_forecast.live_record, { next_result_due: row.target_end });
  scorable.vol_forecast.live_record.recent[0].spans_roll = false;
  const plain = render(scorable).text;
  assert.doesNotMatch(plain, /spans roll|No result is due/);
  assert.ok(plain.includes(`Scores appear after the ${m.format.fmtDate(row.target_end)} settlement.`));
});

test("the record's sealing can be checked from the page", () => {
  const view = render(data);
  assert.ok(view.live.includes(`href="${config.RECORD_HISTORY_URL}"`));
  assert.ok(view.text.includes("Full record and its commit history on GitHub →"));
  assert.match(config.RECORD_HISTORY_URL, /\/commits\/live-data\/runtime-state\/vol_track_record\.json$/);
  // The fixture's call was sealed 12:20:31Z; its window opens at the Oct 20 settlement, 18:30Z.
  const head = view.live.match(/<thead[\s\S]*?<\/thead>/)[0];
  assert.match(head, /title="Sealed before the git commit[^"]*"><span class="th-label">Sealed</);
  assert.ok(view.text.includes("Oct 20, 7:20 AM CT 6h 09m before its window"), view.text);
  const row = data.vol_forecast.live_record.recent[0];
  assert.equal(panel.sealLead(row), Date.parse("2026-10-20T18:30:00Z") - Date.parse(row.sealed_at));
  assert.equal(panel.sealLead({ ...row, sealed_at: null }), null);
  // Older summaries carry no seal: no Sealed column, and nothing claims a seal.
  const old = structuredClone(data);
  const { sealed_at: _sealed, recorded_at: _recorded, base_session: _base, inputs: _inputs, ...plain } = old.vol_forecast.live_record.recent[0];
  old.vol_forecast.live_record.recent = [plain];
  const before = render(old);
  assert.deepEqual(before.errors, []);
  assert.doesNotMatch(before.live.match(/<thead[\s\S]*?<\/thead>/)[0], />Sealed</);
});

test("the hash chain: silent before it exists, a muted line while it holds, a warning where it breaks", () => {
  const withChain = (chain) => {
    const payload = structuredClone(data);
    payload.vol_forecast.live_record.chain = chain;
    return render(payload);
  };
  // Summaries written before the chain carry no `chain`: nothing about it is shown.
  const before = render(data);
  assert.equal(data.vol_forecast.live_record.chain, undefined);
  assert.doesNotMatch(before.text, /hash/);
  assert.doesNotMatch(before.live, /notice is-warn/);
  // Linked and unbroken: one muted line beside the history link, no warning.
  const holds = withChain({ n_linked: 3, breaks: [] });
  assert.deepEqual(holds.errors, []);
  assert.ok(holds.text.includes("3 calls carry a hash of the sealed call before it; scripts/verify_record.py checks the chain."),
    holds.text);
  assert.doesNotMatch(holds.live, /notice is-warn/);
  assert.ok(withChain({ n_linked: 1, breaks: [] }).text.includes("1 call carries a hash"));
  assert.doesNotMatch(withChain({ n_linked: 0, breaks: [] }).text, /hash/);
  // Broken: a warning naming each break, and no reassuring line.
  const broken = withChain({ n_linked: 3, breaks: ["2026-11-03", "2026-11-05"] });
  assert.match(broken.live, /<p class="notice is-warn" role="status">/);
  assert.ok(broken.text.includes("The record's hash chain is broken at Nov 3, 2026; Nov 5, 2026: the link stored with each of those calls"), broken.text);
  assert.ok(broken.text.includes("is missing or no longer matches the call before it"), broken.text);
  const one = withChain({ n_linked: 3, breaks: ["2026-11-03"] });
  assert.ok(one.text.includes("broken at Nov 3, 2026: the link stored with that call is missing"), one.text);
  assert.ok(broken.text.includes("scripts/verify_record.py says which."), broken.text);
  assert.doesNotMatch(broken.text, /calls carry a hash/);
  // The accessor keeps only well-formed dates.
  const summary = m.research.liveRecordSummary({ ...data.vol_forecast.live_record, chain: { n_linked: 2, breaks: ["2026-11-03", "nope", 7] } });
  assert.deepEqual(summary.chain, { linked: 2, breaks: ["2026-11-03"] });
  assert.equal(m.research.liveRecordSummary(data.vol_forecast.live_record).chain, null);
});

test("the phone table keeps As of, Forecast with its band, Realized with its verdict, and Status", () => {
  const { live } = render(data);
  const head = live.match(/<thead[\s\S]*?<\/thead>/)[0];
  const wide = [...head.matchAll(/<th scope="col" class="([^"]*)"[^>]*>([\s\S]*?)<\/th>/g)]
    .filter((match) => match[1].includes("col-wide")).map((match) => visibleText(match[2]).trim());
  assert.deepEqual(wide, ["Target window", "80% band", "Challenger", "In band", "Sealed"]);
  assert.match(live, /<span class="cell-note live-band-note">23\.3–63\.9<\/span>/);
  assert.match(live, /<span class="live-date">Oct 19, 2026<\/span><span class="live-date-short">Oct 19<\/span>/);
  const resolved = render(scored({ resolved: 1, independent: 1, recent: [resolvedRow("2026-11-06", 70.0)] })).live;
  assert.match(resolved, /70\.0<span class="cell-note live-inband-note live-outside">outside band<\/span>/);
  // The seal's lead, the record's evidence, moves under the status where the Sealed column is hidden.
  const sealed = data.vol_forecast.live_record.recent[0];
  const lead = panel.sealLead(sealed);
  assert.ok(lead > 0);
  assert.ok(live.includes(`<span class="cell-note live-seal-note">sealed ${m.format.fmtAge(lead)} before its window</span>`), "seal note");
  const css = readSource("src/styles/live-record.css");
  assert.match(css, /\.live-band-note, \.live-inband-note, \.live-seal-note, \.live-date-short \{ display: none; \}/);
  assert.match(css, /@media \(max-width: 640px\) \{\s*\.live-band-note, \.live-inband-note, \.live-seal-note \{ display: block; \}/);
});

test("Status follows As of, so a tablet that scrolls the table sideways starts on each call's state", () => {
  const { live } = render(data);
  const head = live.match(/<thead[\s\S]*?<\/thead>/)[0];
  const labels = [...head.matchAll(/<th scope="col"[^>]*>([\s\S]*?)<\/th>/g)].map((match) => visibleText(match[1]).trim());
  assert.deepEqual(labels.slice(0, 2), ["As of", "Status"]);
});

test("the promotion rule is prose: no stylesheet makes .rule-note a flex container", () => {
  const sheets = ["src/index.css", ...readdirSync(new URL("../../src/styles", import.meta.url)).map((name) => `src/styles/${name}`)];
  const rules = sheets.flatMap((path) => [...readSource(path).matchAll(/^\s*\.rule-note\s*\{([^}]*)\}/gm)].map((match) => [path, match[1]]));
  assert.ok(rules.some(([path]) => path === "src/index.css"), "index.css still styles .rule-note");
  for (const [path, body] of rules) assert.doesNotMatch(body, /display:\s*(inline-)?flex/, path);
  assert.match(readSource("src/index.css"), /\.rule-note \{ display: block;/);
  // The identifiers stay inline in the sentence, in code style.
  const { live } = render(data);
  assert.match(live, /<p class="rule-note"><span class="micro">Promotion rule<\/span>The promotion test is pinned to one pair: HAR-IV v2 \(model version <code>har-iv\/2<\/code>\), the model of record/);
});

test("method notes hold the rule and the input changes, without an attribution claim", () => {
  const { inputChanges } = panel;
  const latest = { as_of: "2026-10-23", forecast_pct: 53.4, inputs: { ovx: 55.6, rv5_pct: 50.6, rv22_pct: 38.9, rv66_pct: 47.6, ovx_session: "2026-10-22" } };
  const previous = { as_of: "2026-10-22", forecast_pct: 50.8, inputs: { ovx: 53.2, rv5_pct: 48.9, rv22_pct: 37.6, rv66_pct: 47.2, ovx_session: "2026-10-21" } };
  assert.equal(inputChanges([latest, previous]),
    "Since the Oct 22 call: forecast +2.6; OVX +2.40, 5-day RV +1.70, 22-day RV +1.30, 66-day RV +0.40 (vol points, as recorded).");
  assert.equal(inputChanges([latest, { ...previous, inputs: null }]), null, "an entry recorded without inputs");
  assert.equal(inputChanges([latest]), null);
  const payload = structuredClone(data);
  const record = payload.vol_forecast.live_record;
  record.recent = [{ ...record.recent[0], ...latest }, { ...record.recent[0], ...previous }];
  const { live } = render(payload);
  const notes = visibleText(live.match(/<details class="disclosure panel-notes">([\s\S]*?)<\/details>/)[1]);
  assert.ok(notes.includes("Promotion rule The promotion test is pinned to one pair"));
  const inputs = notes.match(/Inputs, call to call (.*?\.) Direction/)?.[1];
  assert.equal(inputs, inputChanges([latest, previous]));
  assert.doesNotMatch(inputs, /because|drove|explain|due to|contribut|moved the/i, "no attribution claim");
});

test("before any result: one empty line for the scores, the promotion fact, and every call drawn", () => {
  const payload = structuredClone(data);
  payload.vol_forecast.live_record.next_result_due = "2026-10-28";
  const view = render(payload);
  assert.ok(view.text.includes("Scores appear after the Oct 28, 2026 settlement."));
  assert.ok(view.text.includes("Promotion test 0/52 independent paired weeks; accruing"));
  assert.ok(view.text.includes("0 resolved 1 pending 0 never scored"), view.text);
  assert.doesNotMatch(view.text, /QLIKE, same calls|Mean fcst/, "no row of dashes before any result");
  assert.match(view.live, /<div class="live-strip" aria-hidden="true" style="--cells:52">(<i><\/i>){52}<\/div>/);
  assert.ok(view.live.includes('class="lazy-mount"'), "the chart mounts lazily");
  assert.match(view.live, /--lazy-h:200px;--lazy-h-narrow:180px/);
});

test("LiveCalls draws one forecast dot and one band per recorded call, and each outcome", () => {
  const recent = [
    { ...data.vol_forecast.live_record.recent[0], as_of: "2026-11-09", status: "pending" },
    resolvedRow("2026-11-06", 70.0),
    resolvedRow("2026-11-02", 40.0),
    { ...data.vol_forecast.live_record.recent[0], as_of: "2026-10-30", status: "late_commit" },
  ];
  const html = renderToStaticMarkup(createElement(chart.LiveCallsPlot, {
    calls: recent, width: 640, height: 200, nextResultDue: "2026-11-16", challengerLabel: "HAR-IV-range",
  }));
  assert.equal(html.match(/class="live-forecast /g).length, recent.length);
  assert.equal(html.match(/class="live-forecast fill-model"/g).length, 3);
  assert.equal(html.match(/class="live-forecast fill-muted"/g).length, 1, "the never-scored call is drawn grey");
  assert.equal(html.match(/whisker"/g).length, recent.length);
  assert.equal(html.match(/class="live-truth"/g).length, 1);
  assert.equal(html.match(/class="live-truth is-outside"/g).length, 1);
  assert.ok(html.includes(">next result due Nov 16, 2026<"));
  assert.ok(html.includes("<title>realized 70.0%, as of Nov 6, 2026: outside the band</title>"));
  assert.ok(html.includes("<title>HAR-IV-range 48.4%, as of Nov 9, 2026</title>"));
  assert.match(html, /aria-label="4 recorded calls from Oct 30, 2026 to Nov 9, 2026: forecast and 80% band per call, 2 with the realized vol; next result due Nov 16, 2026"/);
  // Oldest first, left to right.
  const xs = [...html.matchAll(/<circle cx="([\d.]+)"[^>]*class="live-forecast/g)].map((match) => Number(match[1]));
  assert.deepEqual(xs, [...xs].sort((a, b) => a - b));
  assert.equal(renderToStaticMarkup(createElement(chart.LiveCallsPlot, { calls: [], width: 640, height: 200 })), "");
});

/**
 * A record past its first weeks, frozen Dec 18 (US Central): 36 calls, of which the latest rows are
 * only pending or resolved, the never-scored ones are older than them, and the oldest pending call
 * (window Nov 27 – Dec 3) waits for a settlement the snapshot lacks. The promotion sample stops at it.
 */
const longRecord = () => {
  const payload = structuredClone(data);
  payload.frozen_at = "2026-12-18T21:00:00+00:00";
  const record = payload.vol_forecast.live_record;
  const call = (asOf, start, end, status, realized = null) => ({
    ...record.recent[0], as_of: asOf, target_start: start, target_end: end, status, realized_pct: realized, spans_roll: false,
  });
  record.recent = [
    call("2026-12-10", "2026-12-14", "2026-12-18", "pending"),
    call("2026-12-09", "2026-12-11", "2026-12-17", "resolved", 24.0),
    call("2026-12-04", "2026-12-08", "2026-12-14", "resolved", 47.1),
    call("2026-11-25", "2026-11-30", "2026-12-04", "resolved", 19.2),
    call("2026-11-24", "2026-11-27", "2026-12-03", "pending"),
  ];
  Object.assign(record, {
    n_recorded: 36, n_resolved: 26, n_pending: 2, n_skipped_roll: 5, n_expired: 2, n_late_commit: 1, n_independent: 6,
    next_result_due: "2026-12-03",
    champion: { ...record.champion, n: 26, qlike: 0.3601, interval_80_coverage_pct: 69.2, mean_forecast_pct: 46.07, mean_realized_pct: 33.35 },
    challenger: { ...record.challenger, n: 26, qlike: 0.391, champion_qlike: 0.3601 },
    promotion: { ...record.promotion, n_independent_paired: 4 },
  });
  return payload;
};

test("every never-scored status in the record is explained under the table, from the counts, and each count prints once", () => {
  const view = render(longRecord());
  assert.deepEqual(view.errors, []);
  const footnote = visibleText(view.live.match(/<div class="table-footnote">([\s\S]*?)<\/div>/)[1]).replace(/ :/g, ":");
  // None of these calls is among the five rows shown, yet each status is explained.
  assert.ok(footnote.includes("skipped: roll (none among the 5 shown): The target window spans a contract roll"), footnote);
  assert.ok(footnote.includes("late commit (none among the 5 shown): Not sealed in time"), footnote);
  assert.ok(footnote.includes("expired (none among the 5 shown): A settlement its window needs was still missing more than"
    + ` ${panel.MAX_WAIT_SESSIONS} sessions after the window closed; never scored.`), footnote);
  // The meta line gives the total; the status line counts by state, once.
  assert.ok(view.meta.includes("36 calls recorded"), view.meta);
  assert.doesNotMatch(view.meta, /pending|skipped|expired|never scored/, view.meta);
  assert.ok(view.text.includes("26 resolved (6 independent weeks) 2 pending 8 never scored: 5 skipped at a contract roll,"
    + " 1 not sealed in time, 2 expired"), view.text);
  for (const count of ["5 skipped", "1 not sealed", "2 expired", "2 pending"]) {
    assert.equal(view.text.split(count).length - 1, 1, `"${count}" printed once`);
  }
  // A status the rows do show is explained without the note.
  const shown = longRecord();
  shown.vol_forecast.live_record.recent.push({ ...shown.vol_forecast.live_record.recent[1], as_of: "2026-11-20", status: "expired" });
  assert.match(visibleText(render(shown).live).replace(/ :/g, ":"), /expired: A settlement its window needs/);

  const { recordCounts, unscoredStatuses } = panel;
  const summary = (fields) => m.research.liveRecordSummary({ ...data.vol_forecast.live_record, ...fields });
  assert.deepEqual(recordCounts(summary({ n_resolved: 0, n_pending: 1, n_skipped_roll: 0, n_late_commit: 1, n_expired: 0 })),
    ["0 resolved", "1 pending", "1 not sealed in time (never scored)"]);
  assert.deepEqual(recordCounts(summary({ n_skipped_roll: null, n_late_commit: null, n_expired: null })), ["0 resolved", "1 pending"],
    "no count, no claim");
  assert.doesNotMatch(recordCounts(summary({ n_late_commit: 2 })).join(" "), /sealed after/, "a late commit may be sealed before its window opened");
  assert.deepEqual(unscoredStatuses(summary({ n_skipped_roll: 0, n_late_commit: 0, n_expired: 3 })), ["expired"]);
  assert.deepEqual(unscoredStatuses(summary({ recent: [{ status: "late_commit" }] })), ["late_commit"], "or from a row");
});

test("a promotion test with fewer weeks than the record says why: its sample is taken in record order", () => {
  const view = render(longRecord());
  assert.equal(view.title, "6 independent weeks resolved (26 calls); too few to compare the two models");
  assert.ok(view.text.includes("Promotion test 4/52 independent paired weeks; accruing. 2 later independent weeks wait for"
    + " the pending Nov 24 call: the sample is taken in record order."), view.text);
  const notes = visibleText(view.live.match(/<details class="disclosure panel-notes">([\s\S]*?)<\/details>/)[1]);
  assert.ok(notes.includes("No resolved call was excluded for its model version or challenger."), notes);
  assert.doesNotMatch(notes, /has been left out/);

  const { promotionGap } = panel;
  const summary = (payload) => m.research.liveRecordSummary(payload.vol_forecast.live_record);
  assert.equal(promotionGap(summary(longRecord())),
    "2 later independent weeks wait for the pending Nov 24 call: the sample is taken in record order.");
  // A pending call older than the rows shown: the page cannot name it.
  const older = longRecord();
  older.vol_forecast.live_record.n_pending = 3;
  assert.equal(promotionGap(summary(older)),
    "2 later independent weeks wait for an earlier call still pending: the sample is taken in record order.");
  // With calls left out of the pair, the gap is not all waiting.
  const excluded = longRecord();
  excluded.vol_forecast.live_record.promotion.n_excluded_no_challenger = 3;
  assert.match(promotionGap(summary(excluded)), /^2 of the record's 6 independent weeks are not in it: resolved calls left out/);
  const level = longRecord();
  level.vol_forecast.live_record.promotion.n_independent_paired = 6;
  assert.equal(promotionGap(summary(level)), null, "no gap, no sentence");
  assert.equal(promotionGap(summary(scored({ resolved: 300, independent: 60, promotion: KEEP }))), null, "decided: the sample is fixed");
});

test("a result due before the snapshot's date reads as overdue, waiting for a missing settlement", () => {
  const view = render(longRecord());
  assert.doesNotMatch(view.text, /next result due/);
  const rows = [...view.live.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map((match) => match[1]);
  const stuck = rows.find((row) => row.includes("Nov 24, 2026"));
  assert.ok(stuck.includes('title="Overdue since Dec 3, 2026: waiting for a missing settlement; it expires, never scored, if that is'
    + ` still missing more than ${panel.MAX_WAIT_SESSIONS} sessions after its window closed"`), stuck);
  assert.match(visibleText(stuck), /pending overdue since Dec 3 /);
  // The newest call's window ends on the snapshot's own date: not overdue.
  assert.doesNotMatch(visibleText(rows.find((row) => row.includes("Dec 10, 2026"))), /overdue/);
  const footnote = visibleText(view.live.match(/<div class="table-footnote">([\s\S]*?)<\/div>/)[1]).replace(/ :/g, ":");
  assert.ok(footnote.includes("pending, overdue: Its window has closed, but a settlement it needs is missing from this snapshot."), footnote);

  const html = renderToStaticMarkup(createElement(chart.LiveCallsPlot, {
    calls: longRecord().vol_forecast.live_record.recent, width: 640, height: 200, nextResultDue: "2026-12-03", overdue: true,
  }));
  assert.ok(html.includes(">result overdue since Dec 3, 2026<"), html);
  assert.match(html, /aria-label="[^"]*; result overdue since Dec 3, 2026, waiting for a missing settlement"/);
  assert.doesNotMatch(html, /next result due/);

  const { overdueSince, snapshotDay } = panel;
  assert.equal(snapshotDay({ frozen_at: "2026-12-19T03:00:00Z" }), "2026-12-18", "9 PM CT is still Dec 18 in Chicago");
  assert.equal(snapshotDay({}), null);
  const row = { status: "pending", target_end: "2026-12-03", spans_roll: false };
  assert.equal(overdueSince(row, "2026-12-04"), "2026-12-03");
  assert.equal(overdueSince(row, "2026-12-03"), null, "due on the snapshot's date is not overdue");
  assert.equal(overdueSince({ ...row, spans_roll: true }, "2026-12-18"), null, "a call that spans a roll is never scored");
  assert.equal(overdueSince({ ...row, status: "resolved" }, "2026-12-18"), null);
  assert.equal(overdueSince(row, null), null);
  // Before any result, the empty line says so too.
  const first = longRecord();
  Object.assign(first.vol_forecast.live_record, { n_resolved: 0, n_independent: 0 });
  const page = render(first);
  assert.ok(page.text.includes("The first result is overdue since Dec 3, 2026: a settlement its window needs is missing from this snapshot."));
  // The title, the evidence list and the verdict tile say overdue too, not "due".
  assert.equal(page.title, "No live forecast has resolved yet; the first is overdue since Dec 3, 2026");
  assert.match(page.foot, /^first result overdue since Dec 3, 2026 · promotion test /);
  assert.doesNotMatch(visibleText(page.html), /the first is due Dec 3/);
  // The page's wait is the record's own.
  const backend = readSource("backend/vol_record.py").match(/^MAX_WAIT_SESSIONS = (\d+)/m);
  assert.equal(Number(backend?.[1]), panel.MAX_WAIT_SESSIONS);
});
