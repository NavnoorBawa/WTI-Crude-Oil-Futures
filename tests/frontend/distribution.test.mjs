// The whole predictive distribution (ROADMAP 2.4): CALB's PIT histogram, its Berkowitz summary, chance note and
// table view, and MODL's opt-in distribution view. The fixture predates the block, so the committed artifacts are
// spliced in (withDistribution). Every expected value is read here from the artifacts, independently of the code
// under test; each verdict is checked at the edges of its band.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, readSource, visibleText } from "./load.mjs";
import { committedLab, committedLedger, withDistribution, withRegimes } from "./splice.mjs";

const FIXED_NOW = Date.parse("2026-10-21T12:00:00Z");
Date.now = () => FIXED_NOW;

let m;
let dist;
let scores;
let charts;
let modl;
before(async () => {
  m = await loadDashboard();
  dist = await m.load("/src/lib/distribution.js");
  scores = await m.load("/src/lib/distributionScores.js");
  charts = await m.load("/src/charts/distributionCharts.jsx");
  modl = await m.load("/src/panels/LeaderboardPanel.jsx");
});
after(() => m.close());

const data = readFixture("dashboard_data_v3.json");
const research = readFixture("research.json");
const spliced = withDistribution(research);
const lab = spliced.vol_lab;
const truth = committedLab().distribution;
const ledgerRows = committedLedger().rows;
const html = (element) => renderToStaticMarkup(element);
const renderDashboard = (payload) => html(createElement(m.dashboard.default, {
  data, research: payload, researchStatus: "ready", quote: m.quote.resolveQuote(null, data),
}));
const section = (markup, id) => markup.match(new RegExp(`<section id="${id}"[\\s\\S]*?</section>`))?.[0] ?? "";
const ledgerOf = (payload) => m.research.researchLedger(payload);
/** A ledger holding only the given Berkowitz q-values, by model key. */
const fakeLedger = (qs) => ({ byId: new Map(Object.entries(qs).map(([key, q]) => [`vol_berkowitz_${key}`, { q }])) });
const p3 = (value) => m.format.fmtPValue(value, { digits: 3 });
/** A p-value as visibleText() shows it (its no-break space read as a space). */
const shown = (value) => p3(value).replace(/\s+/g, " ");
const NB = " ";
const BANNED = /\bcalibrated\b|\bpasses\b|\bvalidated\b|\bbeats\b|\boutperforms\b/i;
/** "p = 0.088" (or "p < 0.001"), with the page's no-break spaces. */
const pEq = (value) => (/^[<>]/.test(p3(value)) ? `p${NB}${p3(value)}` : `p${NB}=${NB}${p3(value)}`);
/** A list as a sentence writes it ("A, B and C"). */
const listed = (items, word = "and") => (items.length > 1 ? `${items.slice(0, -1).join(", ")} ${word} ${items.at(-1)}` : items[0]);
/** The p-value's band: 0 below 0.05, 1 from 0.05 to 0.10, 2 from 0.10. */
const bandOf = (p) => (p < 0.05 ? 0 : p < 0.1 ? 1 : 2);
/** A table's rows (thead and tbody) as arrays of cell markup, the row header first. */
const tableRows = (markup) => [...markup.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g)]
  .map(([, row]) => [...row.matchAll(/<(th|td)\b[^>]*>([\s\S]*?)<\/\1>/g)].map(([, , cell]) => cell));
/** A cell's text as the page shows it, sr-only words included, trimmed. */
const cellText = (cell) => visibleText(cell).trim();
/** A tag's attributes as an object. */
const attrs = (tag) => Object.fromEntries([...tag.matchAll(/([\w-]+)="([^"]*)"/g)].map(([, name, value]) => [name, value]));

// ------------------------------------------------------------------------------------------ the splice and the rows

test("the splice carries the committed distribution block and the ledger's two families", () => {
  assert.deepEqual(lab.distribution, truth);
  const families = new Set(["predictive distribution", "distribution calibration"]);
  const expected = ledgerRows.filter((row) => families.has(row.family)).map((row) => row.id);
  assert.ok(expected.length > 0);
  const ledger = ledgerOf(spliced);
  for (const id of expected) assert.ok(ledger.byId.has(id), id);
  assert.equal(ledger.byId.get("vol_berkowitz_har_iv").q, ledgerRows.find((row) => row.id === "vol_berkowitz_har_iv").q_value_bh);
  assert.equal(ledgerOf(research).byId.has("vol_berkowitz_har_iv"), false, "the fixture alone has none of them");
});

test("models in the page's order: record, challenger, the rest as published, the model outside the field last and tagged", () => {
  const rows = dist.distributionModels(lab);
  const { champion, challenger } = lab.config;
  const field = new Set(lab.leaderboard.models.map((model) => model.key));
  const outside = truth.models.filter((model) => !field.has(model.key)).map((model) => model.key);
  const rest = truth.models.map((model) => model.key).filter((key) => key !== champion && key !== challenger && !outside.includes(key));
  assert.deepEqual(rows.map((row) => row.key), [champion, challenger, ...rest, ...outside]);
  assert.ok(outside.length >= 1, "the artifact scores a model outside the field");
  for (const row of rows) {
    assert.equal(row.tag, outside.includes(row.key) ? "not in the benchmark" : null, row.key);
    assert.doesNotMatch(row.label, /\((model of record|not in the benchmark)\)/, "the role is a chip or tag, not in the name");
    assert.equal(row.tone, row.key === champion ? "model" : row.key === challenger ? "alt" : "neutral");
  }
  // Order follows the payload, not a typed list: reversing the published models keeps the roles first.
  const reversed = structuredClone(lab);
  reversed.distribution.models.reverse();
  const again = dist.distributionModels(reversed).map((row) => row.key);
  assert.deepEqual(again.slice(0, 2), [champion, challenger]);
  assert.deepEqual(again.slice(2, -outside.length), [...rest].reverse());
  assert.equal(dist.hasDistribution(lab), true);
  assert.equal(dist.hasDistribution(research.vol_lab), false);
  assert.deepEqual(dist.distributionModels(research.vol_lab), []);
});

test("the PIT title names its sample in at most 90 characters", () => {
  const title = dist.pitTitle(lab);
  assert.equal(title, `PIT histogram by model, ${m.format.fmtInt(truth.n_days)} out-of-sample days (${truth.first.slice(0, 4)}–${truth.last.slice(0, 4)})`);
  assert.ok(title.length <= 90, title);
  const moved = structuredClone(lab);
  moved.distribution.n_days = 1234;
  assert.match(dist.pitTitle(moved), /1,234 out-of-sample days/);
});

// ------------------------------------------------------------------------------------------ Berkowitz summary

test("Berkowitz verdicts at the edges of each band, with the ledger's q only for a rejection that does not survive", () => {
  assert.deepEqual([0.049, 0.05, 0.099, 0.1, null].map(dist.berkowitzVerdict), ["rejects", "inconclusive", "inconclusive", "does not reject", null]);
  const at = (p, q) => {
    const copy = structuredClone(lab);
    const ovx = copy.distribution.models.find((model) => model.key === "ovx_only");
    ovx.berkowitz.p_value = p;
    ovx.berkowitz.grids_below_5pct = 3;           // a majority of the 5 grids: no grid clause
    return dist.berkowitzSummary(copy, fakeLedger({ ovx_only: q }));
  };
  const name = "the OVX-only regression";
  for (const q of [0.04, 0.05, 0.06]) {
    const rejected = at(0.049, q);
    const first = rejected.split(". It ")[0];
    assert.ok(first.includes(name), `${q}: 0.049 rejects`);
    if (q >= 0.05) assert.ok(first.includes(`${name} (p${NB}=${NB}0.049; not after the ledger's adjustment, q${NB}=${NB}${p3(q)})`), rejected);
    else assert.ok(!first.includes(`${name} (`) && !/not after/.test(first.split(name)[1].split(/,| and /)[0]), `${q}: a surviving rejection needs no q: ${rejected}`);
    for (const p of [0.05, 0.099]) {
      const text = at(p, q);
      const unclear = text.split("is inconclusive for ")[1]?.split(" and does not reject")[0] ?? "";
      assert.ok(unclear.includes(`${name} (p${NB}=${NB}${p3(p)})`), `${p} ${q}: ${text}`);
      assert.doesNotMatch(unclear, /not after/, "no adjustment clause for an inconclusive test");
      assert.ok(!text.split(". It ")[0].includes(name), `${p} is not a rejection`);
    }
    const quiet = at(0.1, q);
    assert.ok(quiet.split("does not reject ")[1].includes(`${name} (p${NB}=${NB}0.100)`), quiet);
    assert.match(quiet, / or the OVX-only regression/, "a list of models not rejected joins with 'or'");
    assert.match(quiet, /a failure to reject is not evidence of calibration\.$/);
  }
});

test("the CALB summary on the artifact: each model's verdict, and the outside model's grid, censored and randomized-tail clauses", () => {
  const ledger = ledgerOf(spliced);
  const text = dist.berkowitzSummary(lab, ledger);
  const rows = dist.distributionModels(lab);
  const byKey = new Map(truth.models.map((model) => [model.key, model]));
  const [rejects, rest] = text.split(". It ");
  for (const row of rows) {
    const p = byKey.get(row.key).berkowitz.p_value;
    const name = dist.proseName(row);
    const [unclear, quiet] = rest.split("does not reject ");
    if (p < 0.05) assert.ok(rejects.includes(name) && !rest.includes(name), `${row.key} rejected`);
    else if (p < 0.1) assert.ok(unclear.includes(`${name} (p${NB}=${NB}${p3(p)})`), `${row.key} inconclusive`);
    else assert.ok(quiet.includes(`${name} (p${NB}=${NB}${p3(p)})`), `${row.key} not rejected`);
  }
  const outside = rows.find((row) => row.tag);
  const b = byKey.get(outside.key).berkowitz;
  const q = ledger.byId.get(`vol_berkowitz_${outside.key}`).q;
  const taus = truth.config.taus;
  assert.ok(b.p_value < 0.05 && b.grids_below_5pct < truth.weeks_per_grid.length / 2 && q >= 0.05, "the artifact's case");
  assert.ok(text.includes(`${dist.proseName(outside)} (p${NB}=${NB}${p3(b.p_value)}, below 5% on ${b.grids_below_5pct} of `
    + `${truth.weeks_per_grid.length} grids, in a censored form that tests the distribution between its ${Math.round(taus[0] * 100)}% and `
    + `${Math.round(taus.at(-1) * 100)}% quantiles and the two tail masses, not independence; a randomized-tail form gives `
    + `p${NB}=${NB}${p3(b.randomized_tail.p_value)}; not after the ledger's adjustment, q${NB}=${NB}${p3(q)})`), text);
  assert.ok(text.includes(`on each of the ${truth.weeks_per_grid.length} sampling grids' ${truth.weeks_per_grid[0]} non-overlapping weeks`));
  assert.doesNotMatch(text, BANNED);
  // No model rejected: the sentence says so, counted.
  const none = structuredClone(lab);
  none.distribution.models.forEach((model) => { model.berkowitz.p_value = 0.5; });
  assert.match(dist.berkowitzSummary(none, ledger), new RegExp(`rejects none of the ${rows.length} models at 5%`));
});

test("the chance note reads each construction's simulated band and mean, never a typed width", () => {
  const rows = dist.distributionModels(lab);
  const note = dist.chanceNote(rows);
  const groups = new Map();
  for (const model of truth.models) {
    const key = `${Math.max(...model.pit_chance_band_pct).toFixed(1)}|${Math.min(...model.pit_chance_center_pct)}|${Math.max(...model.pit_chance_center_pct)}`;
    groups.set(key, [...(groups.get(key) ?? []), model]);
  }
  for (const [key] of groups) assert.ok(note.includes(`up to ±${key.split("|")[0]} points`), `${key}: ${note}`);
  const trailing = truth.models.filter((model) => model.pit_construction === "trailing_rank");
  const names = rows.filter((row) => trailing.some((model) => model.key === row.key)).map(dist.proseName);
  assert.ok(note.includes(`for ${names.slice(0, -1).join(", ")} and ${names.at(-1)}, whose PIT is a rank among trailing residuals`), note);
  assert.match(note, /for the others\. The dashed line is 10%, one bin's share of the days\. No single bin is tested\.$/);
  // Move one construction's band: the note follows the payload (weeks_per_grid plays no part).
  const moved = structuredClone(lab);
  moved.distribution.models.filter((model) => model.pit_construction === "trailing_rank").forEach((model) => { model.pit_chance_band_pct[3] = 2.3; });
  moved.distribution.weeks_per_grid = [10, 10, 10, 10, 10];
  assert.ok(dist.chanceNote(dist.distributionModels(moved)).includes("up to ±2.3 points"));
});

// ------------------------------------------------------------------------------------------ MODL readings

test("each score's reading follows its tests' bands and the ledger, and never overstates", () => {
  const ledger = ledgerOf(spliced);
  const rows = dist.distributionModels(lab);
  const name = (key) => dist.proseName(rows.find((row) => row.key === key));
  const tests = (score) => truth.models.filter((model) => model.dm_vs_har_iv && model.key !== lab.config.challenger)
    .map((model) => ({ key: model.key, ...model.dm_vs_har_iv[score] }));
  for (const score of scores.DIST_SCORES) {
    const text = scores.scoreReading(lab, ledger, score);
    assert.ok(text.startsWith(`On ${score.phrase}, `), text);
    assert.doesNotMatch(text, BANNED);
    for (const t of tests(score.key)) {
      const pText = `${name(t.key)} (p${NB}=${NB}${p3(t.p_value)})`;
      if (t.p_value < 0.05 && t.stat < 0) assert.ok(text.includes("scores significantly lower than") && text.includes(pText), `${score.key} ${t.key}`);
      if (t.p_value >= 0.05 && t.p_value < 0.1) assert.ok(text.includes("inconclusive against") && text.includes(pText), `${score.key} ${t.key}`);
      if (t.p_value >= 0.1) assert.ok(text.includes(name(t.key)) || /from any of the \d+ models/.test(text), `${score.key} ${t.key}`);
    }
  }
  // Each band's clause, its models in the page's order, worded by the sign of t: the record lower (t < 0) or the model.
  const record = rows.find((row) => row.role === "record").label;
  const clauses = (reading, score, payload = lab) => {
    const field = rows.filter((row) => !row.role)
      .map((row) => ({ row, t: payload.distribution.models.find((model) => model.key === row.key).dm_vs_har_iv?.[score] }))
      .filter(({ t }) => t?.p_value != null && t?.stat != null);
    const group = (band, sign) => field.filter(({ t }) => bandOf(t.p_value) === band && Math.sign(t.stat) === sign);
    const names = (items) => listed(items.map(({ row, t }) => `${dist.proseName(row)} (${pEq(t.p_value)})`));
    const verb = (items) => (items.length === 1 ? "scores" : "score");
    const expected = [
      [group(0, -1), (items) => `${record} scores significantly lower than ${names(items)}`],
      [group(0, 1), (items) => `${names(items)} ${verb(items)} significantly lower than ${record}`],
      [group(1, -1), (items) => `${names(items)}, where ${record} scores lower`],
      [group(1, 1), (items) => `${names(items)}, which ${verb(items)} lower than ${record}`],
    ].filter(([items]) => items.length).map(([items, clause]) => clause(items));
    for (const clause of expected) assert.ok(reading.includes(clause), `${score}: "${clause}" in ${reading}`);
    return { below: group(1, -1).length, above: group(1, 1).length };
  };
  const counts = scores.DIST_SCORES.map((score) => clauses(scores.scoreReading(lab, ledger, score), score.key));
  assert.ok(counts.some((count) => count.below) && counts.some((count) => count.above), "the artifact has an inconclusive test each way");
  // Flip GARCH's CRPS t: the same p now reads as the model lower.
  const flipped = structuredClone(lab);
  const flip = flipped.distribution.models.find((model) => model.key === "garch").dm_vs_har_iv.crps_grid;
  flip.stat = -flip.stat;
  assert.equal(clauses(scores.scoreReading(flipped, ledger, scores.DIST_SCORES[0]), "crps_grid", flipped).above, 1);
  // The 90% pinball: the two significant rows with their q-values, neither surviving.
  const p90 = scores.scoreReading(lab, ledger, scores.DIST_SCORES.find((score) => score.key === "pinball_90"));
  const sig = tests("pinball_90").filter((t) => t.p_value < 0.05);
  const qs = sig.map((t) => ledgerRows.find((row) => row.id === `vol_pinball90_${t.key}`).q_value_bh);
  assert.ok(qs.every((q) => q >= 0.05), "the artifact's case");
  assert.ok(p90.includes(`neither after the ledger's adjustment (q${NB}=${NB}${qs.map(p3).join(", ")})`), p90);
  // The CRPS's sources are quoted only for the models the record scores lower than, short of p = 0.20: the periods the
  // record was lower in, and the shares to the artifact's one decimal.
  const crps = scores.scoreReading(lab, ledger, scores.DIST_SCORES[0]);
  const behind = rows.filter((row) => !row.role)
    .map((row) => ({ row, ...truth.models.find((model) => model.key === row.key).dm_vs_har_iv?.crps_grid }))
    .filter((t) => t.stat < 0 && t.p_value < 0.2);
  const spanOf = (values, digits, suffix = "") => {
    const [lo, hi] = [Math.min(...values).toFixed(digits), Math.max(...values).toFixed(digits)];
    return lo === hi ? `${lo}${suffix}` : `${lo}${suffix} to ${hi}${suffix}`;
  };
  assert.ok(behind.length > 1, "the artifact's case");
  assert.ok(crps.includes(`Against ${listed(behind.map((t) => dist.proseName(t.row)))}, ${record} scored the lower CRPS in `
    + `${spanOf(behind.map((t) => t.periods_har_iv_lower), 0)} of ${behind[0].groups} periods, but 2020 carries `
    + `${spanOf(behind.map((t) => t.concentration.share_2020_pct), 1, "%")} of each total difference, and the 1% of days with the `
    + `largest differences ${spanOf(behind.map((t) => t.concentration.share_largest_1pct_days_pct), 1, "%")}.`), crps);
  for (const t of tests("crps_grid").filter((t) => t.concentration && !behind.some((other) => other.row.key === t.key))) {
    assert.ok(!crps.includes(`${t.concentration.share_2020_pct.toFixed(1)}%`), `${t.key}'s share is not quoted`);
  }
  // Synthetic: a survivor and a model significantly lower than the record.
  const copy = structuredClone(lab);
  const garch = copy.distribution.models.find((model) => model.key === "garch").dm_vs_har_iv.pinball_10;
  Object.assign(garch, { p_value: 0.01, stat: 3.1 });
  const fake = { byId: new Map([["vol_pinball10_garch", { q: 0.03 }]]) };
  const synthetic = scores.scoreReading(copy, fake, scores.DIST_SCORES[1]);
  assert.ok(synthetic.includes(`${name("garch")} (p${NB}=${NB}0.010) scores significantly lower than HAR-IV, surviving the ledger's adjustment`), synthetic);
});

test("the CRPS reading's shares round to RESULTS 2.9's whole percents", () => {
  // RESULTS rounds half to even and toFixed half up, so the page quotes the artifact's one decimal: each of its bounds
  // must round to RESULTS' (40.5% reads 40% there).
  const results = readSource("docs/RESULTS.md").match(/2020 carries (\d+)%(?: to (\d+)%)? of each total difference, and the 1% of days with the largest differences carry (\d+)%(?: to (\d+)%)?/);
  assert.ok(results, "RESULTS 2.9 quotes the shares");
  const crps = scores.scoreReading(lab, ledgerOf(spliced), scores.DIST_SCORES[0]);
  const page = crps.match(/2020 carries ([\d.]+)%(?: to ([\d.]+)%)? of each total difference, and the 1% of days with the largest differences ([\d.]+)%(?: to ([\d.]+)%)?/);
  assert.ok(page, crps);
  const bounds = (match) => [match[1], match[2] ?? match[1], match[3], match[4] ?? match[3]].map(Number);
  bounds(results).forEach((whole, index) => {
    assert.ok(Math.abs(whole - bounds(page)[index]) <= 0.5, `RESULTS ${whole}% against the page's ${bounds(page)[index]}%`);
  });
  assert.ok(page.slice(1).filter(Boolean).every((text) => /^\d+\.\d$/.test(text)), `the page quotes one decimal: ${page[0]}`);
});

test("the challenger's reading states its tests and that it was picked on this sample", () => {
  const ledger = ledgerOf(spliced);
  const text = scores.challengerReading(lab, ledger);
  const block = truth.models.find((model) => model.key === lab.config.challenger).dm_vs_har_iv.pinball_90;
  assert.ok(text.includes(`the challenger HAR-IV-range scores significantly lower than HAR-IV (t${NB}=${NB}${m.format.fmtSigned(block.stat, 2)}, `
    + `p${NB}=${NB}${p3(block.p_value)}, lower in ${block.periods_model_lower} of ${block.groups} periods)`), text);
  assert.match(text, /It was picked as the challenger on this sample, so this is not new evidence, and its tests are outside the ledger's adjusted family\.$/);
  // Without the ledger's flags, no claim about the family.
  assert.doesNotMatch(scores.challengerReading(lab, null), /adjusted family/);
});

test("the view's footnote and the rerun footnote are computed, naming every setting the rerun ran", () => {
  const foot = scores.distributionFootnote(lab);
  const cfg = truth.config;
  assert.ok(foot.includes(`(every out-of-sample day, ${m.format.fmtInt(lab.leaderboard.n_days)}); these scores use the ${m.format.fmtInt(truth.n_days)} days`));
  assert.ok(foot.includes(`compares ${cfg.dm.groups} consecutive periods`) && foot.includes(`t with ${cfg.dm.groups - 1} degrees of freedom`));
  assert.ok(foot.includes(`mean of ${cfg.taus.length} quantile scores`) && foot.includes(`reads ${((cfg.gaussian_ratio_k19 - 1) * 100).toFixed(1)}% above`));
  assert.ok(foot.includes("10 × (pinball 10% + pinball 90%) is the interval score of the 10% to 90% quantiles"));
  // Counted as MODL's meta counts the field: the timing control apart.
  const scored = new Set(truth.models.map((model) => model.key));
  const eligible = lab.leaderboard.models.filter((model) => !model.timing_control);
  const controls = lab.leaderboard.models.length - eligible.length;
  assert.ok(controls > 0, "the fixture's field has a timing control");
  const missing = eligible.filter((model) => !scored.has(model.key)).length;
  assert.ok(foot.includes(`${missing} of the ${eligible.length} leaderboard models have no predictive distribution here`), foot);
  assert.ok(visibleText(section(renderDashboard(spliced), "modl")).includes(`${eligible.length} models + ${controls} timing control`),
    "the panel's meta counts the same field");

  const field = truth.quantile_har_field;
  const text = scores.rerunFootnote(lab);
  const [inside, outside] = text.split(/\. With the root/)[0].split(/, and left out /);
  const pIn = (value) => `(p${NB}=${NB}${p3(value)})`;
  for (const key of Object.keys(field).filter((k) => /^mcs_block_\d+$/.test(k))) {
    const words = `with ${key.slice(10)}-day blocks ${pIn(field[key].p_quantile_har)}`;
    assert.ok((field[key].included.includes("quantile_har") ? inside : outside).includes(words), `${key}: ${text}`);
  }
  for (const key of Object.keys(field).filter((k) => /^mcs_\d{4}_\d{4}$/.test(k))) {
    const words = `in ${key.slice(4, 8)}–${key.slice(9)} ${pIn(field[key].p_quantile_har)}`;
    assert.ok((field[key].included.includes("quantile_har") ? inside : outside).includes(words), `${key}: ${text}`);
  }
  const grids = Object.values(field.mcs_by_grid);
  const gridsIn = grids.filter((grid) => grid.included.includes("quantile_har")).length;
  assert.ok(inside.includes(gridsIn === grids.length ? `on each of the ${grids.length} sampling grids` : `on ${gridsIn} of ${grids.length}`));
  assert.ok(text.includes(`leaves it out (MCS p${NB}=${NB}${p3(field.mcs.p_values.quantile_har)})`), text);
  assert.ok(text.includes(`it is ${field.alt_point_forecast.mcs_included ? "included" : "left out"} at the headline settings ${pIn(field.alt_point_forecast.mcs_p)}`));
  // The QLIKE sentence follows its p's band, at the artifact's p and at each band's edges.
  const recordName = dist.distributionModels(lab).find((row) => row.role === "record").label;
  const qlikeSentence = (p) => [
    `Its QLIKE differs significantly from ${recordName}'s (batch-means ${pEq(p)}).`,
    `Its QLIKE difference to ${recordName} is inconclusive (batch-means ${pEq(p)}).`,
    `Its QLIKE does not differ significantly from ${recordName}'s (batch-means ${pEq(p)}).`,
  ][bandOf(p)];
  assert.ok(text.includes(qlikeSentence(field.dm_qlike_vs_har_iv.batch_means.p_value)), text);
  for (const p of [0.03, 0.049, 0.05, 0.07, 0.099, 0.1]) {
    const moved = structuredClone(lab);
    moved.distribution.quantile_har_field.dm_qlike_vs_har_iv.batch_means.p_value = p;
    assert.ok(scores.rerunFootnote(moved).includes(qlikeSentence(p)), `${p}: ${scores.rerunFootnote(moved)}`);
  }
  assert.match(text, /The rerun selects nothing: the quantile HAR-IV is never eligible for the live pair\.$/);
  // The fixture's SPA p is older than the rerun's, so the sentence gives both; equal at 3 dp, it says "stays".
  assert.ok(text.includes(`The SPA p is ${p3(field.spa.p_consistent)} (${p3(lab.leaderboard.spa_qlike_vs_har_iv.p_consistent)} without it)`), text);
  const same = structuredClone(lab);
  same.leaderboard.spa_qlike_vs_har_iv.p_consistent = field.spa.p_consistent;
  assert.ok(scores.rerunFootnote(same).includes(`The SPA p stays ${p3(field.spa.p_consistent)}.`));
  // Flip one setting: it moves to the other list.
  const flipped = structuredClone(lab);
  flipped.distribution.quantile_har_field.mcs_block_10.included.push("quantile_har");
  assert.ok(scores.rerunFootnote(flipped).split(", and left out ")[0].includes("with 10-day blocks"));
  for (const sentence of [foot, text]) assert.doesNotMatch(sentence, BANNED);
});

// ------------------------------------------------------------------------------------------ rendering

test("CALB with the block: the figure's title, the summary in the entry, an exact reservation, the chance note and notes", () => {
  const markup = section(renderDashboard(spliced), "calb");
  const text = visibleText(markup);
  const rows = dist.pitRows(dist.distributionModels(lab));
  assert.ok(markup.includes(`<h3 class="figure-title">${dist.pitTitle(lab)}</h3>`));
  assert.ok(text.includes(dist.berkowitzSummary(lab, ledgerOf(spliced)).replace(/\s+/g, " ")), "the summary, in the entry");
  const height = rows.length * 46 + 22;
  assert.equal(charts.pitHeight(rows.length), height);
  assert.match(markup, new RegExp(`--lazy-h:${height}px;--lazy-h-narrow:${height}px;--lazy-h-mid:${height}px`), "the PIT placeholder reserves the figure's height at every width");
  assert.ok(text.includes("Shaded: how far each bin's share moves by chance"));
  assert.ok(text.includes("How each predictive distribution is built") && text.includes("Why a simulated null"));
  assert.match(markup, /<code>vol_lab\.distribution<\/code>|vol_lab\.distribution/, "the Source footer names the block");
  assert.doesNotMatch(text, BANNED);
  // The title, facts and caption are unchanged by the block.
  const plain = section(renderDashboard(research), "calb");
  assert.equal(markup.match(/<h2\b[^>]*>[\s\S]*?<\/h2>/)[0], plain.match(/<h2\b[^>]*>[\s\S]*?<\/h2>/)[0]);
  assert.doesNotMatch(plain, /PIT histogram|Berkowitz|How each predictive distribution/, "nothing without the block");
});

test("the PIT figure draws ten bars from zero per model on one scale, each with its chance range and a descriptive title", () => {
  const rows = dist.pitRows(dist.distributionModels(lab));
  const width = 640;
  const markup = html(createElement(charts.PitSvg, { rows, width }));
  assert.match(markup, new RegExp(`height="${charts.pitHeight(rows.length)}"`));
  const max = charts.pitScaleMax(rows);
  const top = Math.max(...truth.models.flatMap((model) => model.pit_decile_share_pct.map((share, i) => Math.max(share, model.pit_chance_center_pct[i] + model.pit_chance_band_pct[i]))));
  assert.equal(max, Math.ceil(top / 2) * 2, "one scale, from zero to the next even percent");
  const groups = markup.split('<g class="pit-row">').slice(1);
  assert.equal(groups.length, truth.models.length);
  rows.forEach((row, index) => {
    const model = truth.models.find((candidate) => candidate.key === row.key);
    const group = groups[index];
    const bars = [...group.matchAll(/<rect class="pit-bar fill-(\w+)"[^>]*y="([^"]+)"[^>]*height="([^"]+)"[^>]*><title>([^<]*)<\/title>/g)];
    assert.equal(bars.length, 10, row.key);
    const plotTop = index * 46 + 16;
    const y = (value) => plotTop + 26 - (value / max) * 26;
    bars.forEach(([, tone, by, bh, title], bin) => {
      assert.equal(tone, row.tone);
      assert.ok(Math.abs(Number(by) - y(model.pit_decile_share_pct[bin])) < 1e-6 && Math.abs(Number(by) + Number(bh) - y(0)) < 1e-6, `${row.key} ${bin} from zero`);
      const lo = Math.max(0, model.pit_chance_center_pct[bin] - model.pit_chance_band_pct[bin]);
      const hi = model.pit_chance_center_pct[bin] + model.pit_chance_band_pct[bin];
      assert.equal(title.replaceAll("&#x27;", "'"), `${row.label}, PIT ${(bin / 10).toFixed(1)}–${((bin + 1) / 10).toFixed(1)}: `
        + `${model.pit_decile_share_pct[bin].toFixed(1)}% of days (by chance ${lo.toFixed(1)}% to ${hi.toFixed(1)}%)`);
    });
    const chance = [...group.matchAll(/<rect class="pit-chance"[^>]*y="([^"]+)"[^>]*height="([^"]+)"/g)];
    assert.equal(chance.length, 10);
    chance.forEach(([, cy, ch], bin) => {
      const hi = model.pit_chance_center_pct[bin] + model.pit_chance_band_pct[bin];
      const lo = model.pit_chance_center_pct[bin] - model.pit_chance_band_pct[bin];
      assert.ok(Math.abs(Number(cy) - y(hi)) < 1e-6 && Math.abs(Number(ch) - Math.max(1, y(lo) - y(hi))) < 1e-6, `${row.key} band ${bin}`);
    });
    // Each bar and each chance range sits centred in its own tenth of the plot, between the row's axis ends.
    const axis = attrs(group.match(/<line class="stroke-axis"[^>]*>/)[0]);
    const [left, right] = [Number(axis.x1), Number(axis.x2)];
    assert.ok(left >= 0 && right <= width && right > left, `${row.key}: axis ${left} to ${right}`);
    const binW = (right - left) / 10;
    const inBin = (tag, bin, what) => {
      const a = attrs(tag);
      const [x0, x1] = [Number(a.x), Number(a.x) + Number(a.width)];
      assert.ok(x0 >= left + bin * binW - 1e-6 && x1 <= left + (bin + 1) * binW + 1e-6, `${row.key} ${what} ${bin}: ${x0} to ${x1}`);
      assert.ok(Math.abs((x0 + x1) / 2 - (left + (bin + 0.5) * binW)) < 1e-6, `${row.key} ${what} ${bin} is centred in its bin`);
    };
    [...group.matchAll(/<rect class="pit-bar[^>]*>/g)].forEach(([tag], bin) => inBin(tag, bin, "bar"));
    [...group.matchAll(/<rect class="pit-chance"[^>]*>/g)].forEach(([tag], bin) => inBin(tag, bin, "chance range"));
    const target = group.match(/<line class="stroke-target"[^>]*y1="([^"]+)"/);
    assert.ok(Math.abs(Number(target[1]) - y(10)) < 1e-6, "the dashed line at one bin's share");
    if (row.tag) assert.ok(group.includes(`· ${row.tag}`), "the tag beside the name");
  });
  assert.doesNotMatch(markup, /outside the band|significant/i, "no bin is marked as tested");
});

test("MODL's distribution view shows no QLIKE, its table holds every scored model's tests, and the default view is unchanged", async () => {
  const ledger = ledgerOf(spliced);
  const view = html(createElement(modl.DistributionView, { lab, ledger, recordName: "HAR-IV" }));
  assert.match(view, /class="leaderboard leaderboard--dist"/);
  assert.doesNotMatch(view, /mcs-matrix/);
  const table = html(createElement(charts.DistributionScores, { lab, ledger, recordName: "HAR-IV", caption: "Scores" }));
  const both = visibleText(view + table);
  for (const model of lab.leaderboard.models) {
    assert.ok(!both.includes(m.format.fmtNum(model.qlike, 4)), `${model.key}'s QLIKE is not shown`);
  }
  assert.ok(!/QLIKE (?!remains|does|difference)/.test(both.replace(/its QLIKE/gi, "")), "QLIKE is named only as the ranking and in the rerun");
  // Cell by cell, each in the column its header names: the scores, each test's t (with its p under it) and p, the
  // periods the model scored the lower CRPS in, and Berkowitz's p.
  const [header, ...bodyRows] = tableRows(table);
  const labels = header.map((cell) => cellText(cell).replace(/\s*[↕↑↓]$/u, ""));
  const at = (cells, label) => {
    assert.ok(labels.includes(label), `"${label}" in ${labels.join(" | ")}`);
    return cellText(cells[labels.indexOf(label)]);
  };
  const order = dist.distributionModels(lab);
  assert.equal(bodyRows.length, truth.models.length);
  const named = [["crps_grid", "CRPS"], ["pinball_10", "Pinball 10%"], ["pinball_90", "Pinball 90%"]];
  bodyRows.forEach((cells, index) => {
    const row = order[index];
    const model = truth.models.find((candidate) => candidate.key === row.key);
    assert.ok(at(cells, "Model").startsWith(row.label), `${row.key}: ${at(cells, "Model")}`);
    assert.equal(at(cells, "CRPS"), model.crps_grid_pct.toFixed(3), `${row.key} CRPS`);
    assert.equal(at(cells, "Pinball 10%"), model.pinball_10_pct.toFixed(3), `${row.key} pinball 10%`);
    assert.equal(at(cells, "Pinball 90%"), model.pinball_90_pct.toFixed(3), `${row.key} pinball 90%`);
    assert.equal(at(cells, "Berkowitz p"), shown(model.berkowitz.p_value), `${row.key} Berkowitz`);
    if (row.role === "record") {
      for (const label of ["Lower in", ...named.flatMap(([, name]) => [`t (${name})`, `p (${name})`])]) assert.equal(at(cells, label), "ref.", `${row.key} ${label}`);
      return;
    }
    for (const [key, name] of named) {
      const block = model.dm_vs_har_iv[key];
      assert.equal(at(cells, `t (${name})`), `${m.format.fmtSigned(block.stat, 2)} p ${shown(block.p_value)}`, `${row.key} ${name} t`);
      assert.equal(at(cells, `p (${name})`), shown(block.p_value), `${row.key} ${name} p`);
    }
    const crps = model.dm_vs_har_iv.crps_grid;
    assert.equal(at(cells, "Lower in"), `${crps.periods_model_lower}/${crps.groups}`, `${row.key}: the periods the model scored lower`);
  });
  assert.match(visibleText(table.split("<tbody>")[1]), /not in the benchmark/);
  // The readings come with the table (one lazy chunk).
  assert.ok(visibleText(table).includes("The rerun selects nothing"));
  // The panel's default view keeps its title, its matrix and its ranking; the control gains the CRPS view.
  const plain = section(renderDashboard(withRegimes(research)), "modl");
  const markup = section(renderDashboard(withDistribution(withRegimes(research))), "modl");
  const title = (text) => text.match(/<h2\b[^>]*>([\s\S]*?)<\/h2>/)[1];
  assert.equal(title(markup), title(plain));
  assert.match(markup, /class="mcs-matrix"/);
  const group = markup.match(/<div class="segmented" role="group" aria-label="([^"]*)">([\s\S]*?)<\/div>/);
  assert.equal(group[1], "Leaderboard view: all days, QLIKE by regime or the predictive distribution&#x27;s scores");
  assert.deepEqual([...group[2].matchAll(/aria-pressed="(true|false)"[^>]*>([^<]*)</g)].map((match) => match[2]),
    ["All", "OVX", "OVX/RV5", "RV5", "Roll", "CRPS"]);
  // Without the regime blocks the control holds the two views it can show.
  const only = section(renderDashboard(spliced), "modl").match(/<div class="segmented" role="group" aria-label="[^"]*">([\s\S]*?)<\/div>/)[1];
  assert.deepEqual([...only.matchAll(/aria-pressed="(true|false)"[^>]*>([^<]*)</g)].map((match) => match[2]), ["All", "CRPS"]);
  assert.equal((await m.load("/src/app/viewState.js")).VIEW_DEFAULTS.modl, "all", "the default view stays the leaderboard");
});

test("CALB's table view: PIT shares with a chance row per construction, Berkowitz by grid with its reference, p and q", () => {
  const ledger = ledgerOf(spliced);
  const rows = dist.distributionModels(lab);
  const markup = html(createElement(charts.PitTables, { rows, ledger, sample: dist.distributionSample(lab) }));
  const [pitHtml, berkHtml] = markup.split("</table>");
  const [pit, berk] = [visibleText(pitHtml), visibleText(berkHtml)];
  for (const model of truth.models) {
    for (const share of model.pit_decile_share_pct) assert.ok(pit.includes(` ${share.toFixed(1)} `), `${model.key} ${share}`);
  }
  const constructions = new Set(truth.models.map((model) => JSON.stringify([model.pit_chance_center_pct, model.pit_chance_band_pct])));
  assert.equal((pitHtml.match(/class="is-chance"/g) ?? []).length, constructions.size, "one chance row per distinct range");
  assert.match(pit, /By chance, for HAR-IV, HAR-IV-range and the OVX-only regression/);
  // Cell by cell: each model's ten shares and its coverage in their columns; each chance row's ten ranges around the
  // models' simulated mean (never around 10), naming the models whose PIT is built that way, unbroken at hyphens.
  const byKey = new Map(truth.models.map((model) => [model.key, model]));
  const [pitHead, ...pitBody] = tableRows(pitHtml);
  const pitLabels = pitHead.map(cellText);
  const binCols = pitLabels.flatMap((label, index) => (/^\d\.\d–\d\.\d$/.test(label) ? [index] : []));
  const coverageCol = pitLabels.indexOf("Inside q10–q90");
  assert.ok(binCols.length === 10 && coverageCol > 0, pitLabels.join(" | "));
  const isChance = (cells) => cellText(cells[0]).startsWith("By chance");
  pitBody.filter((cells) => !isChance(cells)).forEach((cells, index) => {
    const model = byKey.get(rows[index].key);
    assert.ok(cellText(cells[0]).startsWith(rows[index].label), cellText(cells[0]));
    assert.deepEqual(binCols.map((col) => cellText(cells[col])), model.pit_decile_share_pct.map((share) => share.toFixed(1)), model.key);
    assert.equal(cellText(cells[coverageCol]), `${model.coverage_10_90_pct.toFixed(1)}%`, `${model.key} inside q10–q90`);
  });
  assert.ok(truth.models.some((model) => model.pit_chance_center_pct.some((center) => Math.abs(center - 10) >= 0.05)),
    "the artifact has a chance range centred off 10");
  const ranges = [];
  for (const row of rows) {
    const model = byKey.get(row.key);
    const cells = model.pit_chance_center_pct.map((center, bin) => {
      const half = model.pit_chance_band_pct[bin];
      return `${Math.max(0, center - half).toFixed(1)}–${(center + half).toFixed(1)}`;
    });
    const same = ranges.find((range) => range.cells.join() === cells.join());
    if (same) same.names.push(dist.proseName(row));
    else ranges.push({ cells, names: [dist.proseName(row)] });
  }
  const chanceRows = pitBody.filter(isChance);
  assert.deepEqual(chanceRows.map((cells) => binCols.map((col) => cellText(cells[col]))), ranges.map((range) => range.cells));
  chanceRows.forEach((cells, index) => {
    assert.equal(cellText(cells[0]), `By chance, for ${listed(ranges[index].names)}`);
    assert.doesNotMatch(visibleText(cells[0].replace(/<span class="nowrap">[^<]*<\/span>/g, "")), /\w-\w/, `unbroken names: ${cells[0]}`);
    assert.equal(cellText(cells[coverageCol]), "");
  });
  for (const model of truth.models) {
    const q = ledgerRows.find((row) => row.id === `vol_berkowitz_${model.key}`).q_value_bh;
    assert.ok(berk.includes(shown(model.berkowitz.p_value)) && berk.includes(shown(q)), model.key);
    for (const grid of model.berkowitz.by_grid) assert.ok(berk.includes(`LR ${grid.lr.toFixed(2)}`), `${model.key} grid ${grid.grid}`);
    if (model.berkowitz.mc_se != null) assert.ok(berk.includes(`Monte Carlo se ${model.berkowitz.mc_se.toFixed(3)}`));
    if (model.berkowitz.p_chi2 != null) assert.ok(berk.includes(`χ²(3) p ${shown(model.berkowitz.p_chi2)}`));
  }
  assert.match(berk, /simulated[\s\S]*χ²\(3\)[\s\S]*doubly censored, χ²\(2\)/);
  // Cell by cell: each grid's p (with its LR) under its own grid, the Bonferroni p and the ledger's q.
  const [berkHead, ...berkBody] = tableRows(berkHtml);
  const berkLabels = berkHead.map(cellText);
  const gridCols = berkLabels.flatMap((label, index) => (/^Grid \d+$/.test(label) ? [index] : []));
  const tested = rows.filter((row) => byKey.get(row.key).berkowitz.p_value != null);
  assert.equal(berkBody.length, tested.length);
  berkBody.forEach((cells, index) => {
    const { key } = tested[index];
    const b = byKey.get(key).berkowitz;
    assert.equal(gridCols.length, b.by_grid.length, key);
    b.by_grid.forEach((grid, i) => assert.equal(cellText(cells[gridCols[i]]), `${shown(grid.p_value)} LR ${grid.lr.toFixed(2)}`, `${key} grid ${grid.grid}`));
    assert.equal(cellText(cells[berkLabels.indexOf("p")]), shown(b.p_value), `${key} p`);
    const q = ledgerRows.find((row) => row.id === `vol_berkowitz_${key}`).q_value_bh;
    assert.equal(cellText(cells[berkLabels.indexOf("q")]), shown(q), `${key} q`);
  });
});

test("CALB's method notes take the simulated null's size and the record's coverage from the payload", () => {
  const notes = dist.distributionNotes(lab);
  const { null_size_pct: size, reference } = truth.config.berkowitz;
  const parametric = reference.parametric.replace(/chi2\((\d+)\)/, "χ²($1)");
  assert.ok(notes.some((note) => note.includes(`it rejects ${size.simulated.toFixed(1)}% of its own draws at 5%, where ${parametric} would `
    + `reject ${size.chi2.toFixed(2)}%`)), notes.join("\n"));
  const record = dist.distributionModels(lab).find((row) => row.role === "record");
  const coverage = truth.models.find((model) => model.key === record.key).coverage_10_90_pct;
  const [lo, hi] = truth.config.pinball_taus.map((tau) => `${Math.round(tau * 100)}%`);
  assert.ok(notes.some((note) => note.includes(`${record.label}'s ${lo} and ${hi} quantiles hold ${coverage.toFixed(1)}% of outcomes between them`)),
    notes.join("\n"));
  // Without the measured size, the clause is left out rather than guessed.
  const bare = structuredClone(lab);
  delete bare.distribution.config.berkowitz.null_size_pct;
  assert.ok(dist.distributionNotes(bare).every((note) => !/of its own draws/.test(note)));
  for (const note of notes) assert.doesNotMatch(note, BANNED);
});

test("the palette offers MODL's distribution view only with the block", async () => {
  const commands = await m.load("/src/app/paletteCommands.js");
  const view = (payload, current) => commands.buildCommands({ data, research: payload, view: { modl: current } })
    .filter((item) => item.run.type === "view" && item.run.key === "modl").map((item) => [item.run.value, item.label, Boolean(item.current)]);
  assert.deepEqual(view(research, "all"), []);
  assert.deepEqual(view(spliced, "dist"), [["all", "MODL view: All days", false], ["dist", "MODL view: predictive distribution", true]]);
  assert.equal(view(withDistribution(withRegimes(research)), "all").length, 6);
});
