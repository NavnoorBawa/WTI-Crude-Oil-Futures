// The whole predictive distribution (ROADMAP 2.4): CALB's PIT histogram, its Berkowitz summary, chance note and
// table view, and MODL's opt-in distribution view. The fixture predates the block, so the committed artifacts are
// spliced in (withDistribution). Every expected value is read here from the artifacts, independently of the code
// under test; each verdict is checked at the edges of its band.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loadDashboard, readFixture, visibleText } from "./load.mjs";
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
  for (const q of [0.04, 0.06]) {
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
  // The 90% pinball: the two significant rows with their q-values, neither surviving.
  const p90 = scores.scoreReading(lab, ledger, scores.DIST_SCORES.find((score) => score.key === "pinball_90"));
  const sig = tests("pinball_90").filter((t) => t.p_value < 0.05);
  const qs = sig.map((t) => ledgerRows.find((row) => row.id === `vol_pinball90_${t.key}`).q_value_bh);
  assert.ok(qs.every((q) => q >= 0.05), "the artifact's case");
  assert.ok(p90.includes(`neither after the ledger's adjustment (q${NB}=${NB}${qs.map(p3).join(", ")})`), p90);
  // The CRPS shares are quoted only for the models the record scores lower than, short of p = 0.20.
  const crps = scores.scoreReading(lab, ledger, scores.DIST_SCORES[0]);
  const behind = tests("crps_grid").filter((t) => t.stat < 0 && t.p_value < 0.2);
  const pct = (values) => `${Math.round(Math.min(...values))}% to ${Math.round(Math.max(...values))}%`;
  assert.ok(crps.includes(`but 2020 carries ${pct(behind.map((t) => t.concentration.share_2020_pct))} of each total difference`), crps);
  assert.ok(crps.includes(pct(behind.map((t) => t.concentration.share_largest_1pct_days_pct))));
  for (const t of tests("crps_grid").filter((t) => !behind.some((other) => other.key === t.key))) {
    assert.ok(!crps.includes(`${Math.round(t.concentration.share_2020_pct)}%`), `${t.key}'s share is not quoted`);
  }
  // Synthetic: a survivor and a model significantly lower than the record.
  const copy = structuredClone(lab);
  const garch = copy.distribution.models.find((model) => model.key === "garch").dm_vs_har_iv.pinball_10;
  Object.assign(garch, { p_value: 0.01, stat: 3.1 });
  const fake = { byId: new Map([["vol_pinball10_garch", { q: 0.03 }]]) };
  const synthetic = scores.scoreReading(copy, fake, scores.DIST_SCORES[1]);
  assert.ok(synthetic.includes(`${name("garch")} (p${NB}=${NB}0.010) scores significantly lower than HAR-IV, surviving the ledger's adjustment`), synthetic);
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
  const scored = new Set(truth.models.map((model) => model.key));
  const missing = lab.leaderboard.models.filter((model) => !scored.has(model.key)).length;
  assert.ok(foot.includes(`${missing} of the ${lab.leaderboard.models.length} leaderboard models have no predictive distribution here`));

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
  assert.ok(text.includes(`batch-means p${NB}=${NB}${p3(field.dm_qlike_vs_har_iv.batch_means.p_value)}`));
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
  assert.match(markup, new RegExp(`--lazy-h:${height}px;--lazy-h-narrow:${height}px`), "the PIT placeholder reserves the figure's height");
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
  const body = visibleText(table.split("<tbody>")[1]);
  const rowCount = (table.split("<tbody>")[1].split("</tbody>")[0].match(/<tr\b/g) ?? []).length;
  assert.equal(rowCount, truth.models.length);
  for (const model of truth.models) {
    assert.ok(body.includes(` ${model.crps_grid_pct.toFixed(3)} `), `${model.key} CRPS`);
    if (model.dm_vs_har_iv) assert.ok(body.includes(m.format.fmtSigned(model.dm_vs_har_iv.crps_grid.stat, 2)), `${model.key} t`);
    assert.ok(body.includes(shown(model.berkowitz.p_value)), `${model.key} Berkowitz`);
  }
  assert.match(body, /not in the benchmark/);
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
  for (const model of truth.models) {
    const q = ledgerRows.find((row) => row.id === `vol_berkowitz_${model.key}`).q_value_bh;
    assert.ok(berk.includes(shown(model.berkowitz.p_value)) && berk.includes(shown(q)), model.key);
    for (const grid of model.berkowitz.by_grid) assert.ok(berk.includes(`LR ${grid.lr.toFixed(2)}`), `${model.key} grid ${grid.grid}`);
    if (model.berkowitz.mc_se != null) assert.ok(berk.includes(`Monte Carlo se ${model.berkowitz.mc_se.toFixed(3)}`));
    if (model.berkowitz.p_chi2 != null) assert.ok(berk.includes(`χ²(3) p ${shown(model.berkowitz.p_chi2)}`));
  }
  assert.match(berk, /simulated[\s\S]*χ²\(3\)[\s\S]*doubly censored, χ²\(2\)/);
});

test("the palette offers MODL's distribution view only with the block", async () => {
  const commands = await m.load("/src/app/paletteCommands.js");
  const view = (payload, current) => commands.buildCommands({ data, research: payload, view: { modl: current } })
    .filter((item) => item.run.type === "view" && item.run.key === "modl").map((item) => [item.run.value, item.label, Boolean(item.current)]);
  assert.deepEqual(view(research, "all"), []);
  assert.deepEqual(view(spliced, "dist"), [["all", "MODL view: All days", false], ["dist", "MODL view: predictive distribution", true]]);
  assert.equal(view(withDistribution(withRegimes(research)), "all").length, 6);
});
