// Canvas and SVG charts cannot read CSS custom properties reliably (canvas not at all), so the
// palette in tokens.css is mirrored here. tests/frontend/theme.test.mjs asserts the two match.

/** Surface and text colours (tokens.css --bg-*, --rule*, --text-*). */
export const SURFACE = {
  bg0: "#0A0C10",
  bg1: "#0F1217",
  bg2: "#151922",
  rule: "#1F2530",
  ruleStrong: "#2A313C",
};

export const TEXT = {
  text1: "#E6EAF0",
  text2: "#A9B2BD",
  text3: "#7F8A96",
  text4: "#5C6672",
};

/**
 * Series colours (tokens.css --s-*). Semantic roles: truth = realized volatility, model = the model
 * of record, implied = OVX, bench = a benchmark model, alt/alt2 = challenger and other comparisons.
 */
export const SERIES = {
  truth: "#C9D1D9",
  model: "#3987E5",
  implied: "#D95926",
  bench: "#199E70",
  alt: "#9085E9",
  alt2: "#D55181",
};

/** Status colours (tokens.css --pos/--neg/--warn). Never encode rising volatility as --pos. */
export const STATUS = {
  pos: "#1AA385",
  neg: "#E0603A",
  warn: "#E0A030",
};

/** Maps a tokens.css custom-property name to its JS mirror; the sync test walks this table. */
export const CSS_VAR_MIRROR = {
  "--bg-0": SURFACE.bg0,
  "--bg-1": SURFACE.bg1,
  "--bg-2": SURFACE.bg2,
  "--rule": SURFACE.rule,
  "--rule-strong": SURFACE.ruleStrong,
  "--text-1": TEXT.text1,
  "--text-2": TEXT.text2,
  "--text-3": TEXT.text3,
  "--text-4": TEXT.text4,
  "--s-truth": SERIES.truth,
  "--s-model": SERIES.model,
  "--s-implied": SERIES.implied,
  "--s-bench": SERIES.bench,
  "--s-alt": SERIES.alt,
  "--s-alt2": SERIES.alt2,
  "--pos": STATUS.pos,
  "--neg": STATUS.neg,
  "--warn": STATUS.warn,
};

/** `#RRGGBB` + alpha (0..1) -> `rgba()`; canvas fills need explicit alpha. */
export const withAlpha = (hex, alpha) => {
  const value = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

export const MONO_FONT = '"JetBrains Mono Variable", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace';

/** Shared lightweight-charts options: calm grid, mono axis labels, page scroll wins over the chart. */
export const baseChartOptions = () => ({
  attributionLogo: true, // TradingView licence requirement; labelled for screen readers after creation
  layout: {
    background: { type: "solid", color: SURFACE.bg1 },
    textColor: TEXT.text3,
    fontFamily: MONO_FONT,
    fontSize: 11,
  },
  grid: {
    vertLines: { color: withAlpha(SURFACE.ruleStrong, 0.35) },
    horzLines: { color: withAlpha(SURFACE.ruleStrong, 0.6) },
  },
  crosshair: {
    vertLine: { color: TEXT.text4, labelBackgroundColor: SURFACE.bg2, width: 1, style: 3 },
    horzLine: { color: TEXT.text4, labelBackgroundColor: SURFACE.bg2, width: 1, style: 3 },
  },
  rightPriceScale: { borderColor: SURFACE.rule },
  timeScale: { borderColor: SURFACE.rule },
  handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
  handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
});

/** Lightweight-charts injects an unlabeled TradingView link; give it an accessible name. */
export const labelAttribution = (host) => {
  const link = host?.querySelector?.('a[href*="tradingview"]');
  if (link) {
    link.setAttribute("aria-label", "Charts by TradingView Lightweight Charts (opens tradingview.com)");
    link.setAttribute("rel", "noopener noreferrer");
  }
};

/** Resolves once web fonts are ready so canvas text measures with the final font, never later than 3 s. */
export const fontsReady = () => {
  if (typeof document === "undefined" || !document.fonts?.ready) return Promise.resolve();
  return Promise.race([document.fonts.ready, new Promise((resolve) => setTimeout(resolve, 3000))]);
};
