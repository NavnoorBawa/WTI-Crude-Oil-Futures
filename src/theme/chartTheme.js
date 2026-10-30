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

/**
 * Status colours (tokens.css --pos/--neg/--warn). Never encode rising volatility as --pos. --neg is
 * a red kept apart from the implied (OVX) orange, so a breach never reads as an OVX mark.
 */
export const STATUS = {
  pos: "#1AA385",
  neg: "#F2555A",
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
/** Advance of one character of the charts' 11px monospace text (0.6 em; 6.600 px measured in Chromium). */
export const MONO_CHAR_PX = 6.6;
/** The page's sans-serif stack (tokens.css --font-sans), for measuring sans text drawn in a chart. */
export const SANS_FONT = '"Inter Variable", "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

/** The screen palette, grouped as PRINT_THEME is. */
export const SCREEN_THEME = { surface: SURFACE, text: TEXT, series: SERIES, status: STATUS };

/**
 * The light palette a canvas chart switches to for the moment it takes its print screenshot (the
 * image replaces the canvas on paper). It mirrors the print stylesheet's tokens
 * (src/styles/reading-surface.css, @media print); tests/frontend/charts.test.mjs keeps them equal.
 */
export const PRINT_THEME = {
  surface: { bg0: "#FFFFFF", bg1: "#FFFFFF", bg2: "#F3F4F6", rule: "#DDDDDD", ruleStrong: "#BBBBBB" },
  text: { text1: "#111111", text2: "#333333", text3: "#555555", text4: "#888888" },
  series: { ...SERIES, truth: "#444444" },
  status: { ...STATUS, warn: "#9A6700" },
};

/** A chart's colour options in a palette (SCREEN_THEME or PRINT_THEME), for createChart or applyOptions. */
export const themeChartOptions = (theme = SCREEN_THEME) => ({
  layout: { background: { type: "solid", color: theme.surface.bg1 }, textColor: theme.text.text3 },
  grid: {
    vertLines: { color: withAlpha(theme.surface.ruleStrong, 0.35) },
    horzLines: { color: withAlpha(theme.surface.ruleStrong, 0.6) },
  },
  crosshair: {
    vertLine: { color: theme.text.text4, labelBackgroundColor: theme.surface.bg2 },
    horzLine: { color: theme.text.text4, labelBackgroundColor: theme.surface.bg2 },
  },
  rightPriceScale: { borderColor: theme.surface.rule },
  timeScale: { borderColor: theme.surface.rule },
});

/** True when the reader asked for reduced motion (never on the server). */
export const prefersReducedMotion = () =>
  typeof window !== "undefined" && typeof window.matchMedia === "function"
  && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Shared lightweight-charts options: calm grid, mono axis labels, page scroll wins over the chart,
 * and no kinetic (momentum) scrolling after a swipe for a reader who asked for reduced motion.
 * No logo is drawn inside the plots: the site footer carries the library's attribution notice and a
 * link to https://www.tradingview.com/, which is what its licence asks for.
 */
export const baseChartOptions = () => {
  const colours = themeChartOptions(SCREEN_THEME);
  return {
    // lightweight-charts formats axis labels with navigator.language unless told otherwise. That throws on a
    // POSIX tag ("en-US@posix") and elsewhere mixes a visitor's date style into an en-US page.
    localization: { locale: "en-US" },
    layout: { ...colours.layout, attributionLogo: false, fontFamily: MONO_FONT, fontSize: 11 },
    grid: colours.grid,
    crosshair: {
      vertLine: { ...colours.crosshair.vertLine, width: 1, style: 3 },
      horzLine: { ...colours.crosshair.horzLine, width: 1, style: 3 },
    },
    rightPriceScale: colours.rightPriceScale,
    timeScale: colours.timeScale,
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
    kineticScroll: { touch: !prefersReducedMotion(), mouse: false },
  };
};

/** Resolves once web fonts are ready so canvas text measures with the final font, never later than 3 s. */
export const fontsReady = () => {
  if (typeof document === "undefined" || !document.fonts?.ready) return Promise.resolve();
  return Promise.race([document.fonts.ready, new Promise((resolve) => setTimeout(resolve, 3000))]);
};
