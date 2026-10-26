/**
 * Plain-language definitions of the page's technical terms, shown in a toggletip at a term's first
 * use in a panel (Term.jsx). Each definition is one sentence and states no statistic (definitional
 * constants such as "30-day" are part of what a term means). `ref` is the id of the term's row in
 * METH's list of statistics used; null when the page has no reference row for it.
 *
 * Patterns are case-sensitive and match whole words only: a hyphen or underscore joins words, so
 * "HAR" never matches inside "HAR-IV" and "purge" never inside "purged" or har_iv.
 *
 * @typedef {{key: string, term: string, patterns: string[], definition: string, ref: string|null}} GlossaryEntry
 */

/** @type {readonly GlossaryEntry[]} */
export const GLOSSARY = Object.freeze([
  {
    key: "qlike", term: "QLIKE", patterns: ["QLIKE"], ref: "stat-qlike",
    definition: "A loss for variance forecasts that penalizes under-prediction more than over-prediction and ranks forecasts consistently even when realized volatility is only a noisy proxy for the truth.",
  },
  {
    key: "mcs", term: "Model Confidence Set", patterns: ["Model Confidence Set"], ref: "stat-mcs",
    definition: "The models that cannot be told apart from the best one at the chosen confidence level, found by repeatedly testing for equal loss and dropping the worst model.",
  },
  {
    key: "spa", term: "SPA", patterns: ["SPA"], ref: "stat-spa",
    definition: "Hansen's test for superior predictive ability, which asks whether any model in a searched field beats a benchmark once the search itself is taken into account.",
  },
  {
    key: "dm", term: "Diebold-Mariano", patterns: ["Diebold-Mariano"], ref: "stat-dm",
    definition: "A test of whether two forecasts have the same expected loss, computed from the time series of their loss differences.",
  },
  {
    key: "mz", term: "Mincer-Zarnowitz", patterns: ["Mincer-Zarnowitz"], ref: "stat-mz",
    definition: "A regression of the realized value on the forecast, in which an unbiased forecast has an intercept of 0 and a slope of 1.",
  },
  {
    key: "kupiec", term: "Kupiec", patterns: ["Kupiec"], ref: "stat-kupiec",
    definition: "Kupiec's test of unconditional coverage asks whether the share of weeks that breach the VaR matches the share the VaR level promises.",
  },
  {
    key: "christoffersen", term: "Christoffersen", patterns: ["Christoffersen"], ref: "stat-christoffersen",
    definition: "Christoffersen's tests ask whether VaR breaches arrive independently of one another and, together with the breach rate, whether coverage holds conditionally.",
  },
  {
    key: "dq", term: "Dynamic quantile test", patterns: ["dynamic quantile", "DQ"], ref: "stat-dq",
    definition: "Engle and Manganelli's dynamic quantile test regresses the breach indicator on its own lags and the VaR, so breaches that could have been predicted show up as a failure.",
  },
  {
    key: "conformal", term: "Adaptive conformal interval", patterns: ["adaptive conformal"], ref: "stat-conformal",
    definition: "An interval that widens after each miss and narrows after each hit so that its long-run coverage tracks the target without assuming a distribution (Gibbs-Candès).",
  },
  {
    key: "har", term: "HAR", patterns: ["HAR"], ref: "stat-har",
    definition: "The heterogeneous autoregressive model, which forecasts volatility from realized volatility averaged over a short, a medium and a long window.",
  },
  {
    key: "smearing", term: "Smearing", patterns: ["smearing"], ref: "stat-smearing",
    definition: "Duan's smearing correction rescales a forecast made in logs so that it is unbiased for the mean in levels.",
  },
  {
    key: "bh", term: "Benjamini-Hochberg", patterns: ["Benjamini-Hochberg"], ref: "stat-bh",
    definition: "A procedure that adjusts p-values across many tests so that the expected share of false discoveries among the rejections stays at the chosen rate.",
  },
  {
    key: "hlz", term: "t > 3 hurdle", patterns: ["t > 3"], ref: "stat-hlz",
    definition: "The higher bar for a t-statistic that Harvey, Liu and Zhu propose for findings chosen from many that were tried.",
  },
  {
    key: "utility", term: "Realized utility", patterns: ["realized utility"], ref: "stat-utility",
    definition: "The utility a mean-variance investor who targets a fixed volatility would have earned by sizing positions with the forecast.",
  },
  {
    key: "purge", term: "Purge", patterns: ["purged walk-forward", "purge"], ref: null,
    definition: "Dropping the training rows whose labels overlap the forecast's target window, so a model never learns from the period it is about to forecast.",
  },
  {
    key: "hac", term: "HAC standard errors", patterns: ["HAC", "Newey-West"], ref: null,
    definition: "Standard errors that stay valid when residuals are autocorrelated (as overlapping forecast windows make them) or their variance changes over time.",
  },
  {
    key: "vrp", term: "Variance risk premium", patterns: ["variance risk premium"], ref: null,
    definition: "The difference between the variance the options market implies and the variance later realized over the same horizon.",
  },
  {
    key: "ovx", term: "OVX", patterns: ["OVX"], ref: null,
    definition: "The Cboe Crude Oil ETF Volatility Index, the market's 30-day implied volatility of crude oil read from options on the United States Oil Fund.",
  },
  {
    key: "fhs", term: "Filtered historical simulation", patterns: ["filtered historical simulation"], ref: null,
    definition: "A VaR method that rescales past standardized returns by the current volatility forecast and reads the loss quantile from the rescaled history.",
  },
]);

/** Glossary entries by key. */
export const GLOSSARY_BY_KEY = Object.freeze(Object.fromEntries(GLOSSARY.map((entry) => [entry.key, entry])));

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const PATTERN_KEY = new Map(GLOSSARY.flatMap((entry) => entry.patterns.map((pattern) => [pattern, entry.key])));
// Longest patterns first, so "purged walk-forward" wins over "purge". A term must not touch a word
// character or a hyphen on either side (no lookbehind: older Safari cannot parse one).
const TERM_PATTERN = new RegExp(
  `(^|[^\\w-])(${[...PATTERN_KEY.keys()].sort((a, b) => b.length - a.length).map(escapeRegExp).join("|")})(?![\\w-])`,
  "g",
);

/**
 * Splits text at the first use of each glossary term not yet in `seen` (which it updates), e.g.
 * "QLIKE ranks; QLIKE again" -> ["", {text: "QLIKE", entry}, " ranks; QLIKE again"]. Text with no new
 * term comes back as a one-item array. Pure apart from `seen`.
 *
 * @param {string} text
 * @param {Set<string>} [seen]  glossary keys already wrapped in this panel
 * @returns {(string|{text: string, entry: GlossaryEntry})[]}
 */
export const splitTerms = (text, seen = new Set()) => {
  if (typeof text !== "string" || !text) return [text];
  const parts = [];
  let last = 0;
  for (const match of text.matchAll(TERM_PATTERN)) {
    const key = PATTERN_KEY.get(match[2]);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const start = match.index + match[1].length;
    parts.push(text.slice(last, start), { text: match[2], entry: GLOSSARY_BY_KEY[key] });
    last = start + match[2].length;
  }
  if (!parts.length) return [text];
  parts.push(text.slice(last));
  return parts;
};
