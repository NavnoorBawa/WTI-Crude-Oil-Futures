// Every figure is code-split: the canvas charts share the lightweight-charts chunk, the SVG figures
// share one d3 chunk. Each getter returns the current lazy component (see app/lazyChunk.js).
import { retryingLazy } from "../app/lazyChunk";

export const getForecastChart = retryingLazy(() => import("./ForecastChart.jsx"));
export const getVarChart = retryingLazy(() => import("./VarChart.jsx"));
export const getPriceChart = retryingLazy(() => import("./PriceChart.jsx"));

const svg = () => import("./svgCharts.jsx");
export const getConeChart = retryingLazy(svg, "ConeChart");
export const getYearlySmallMultiples = retryingLazy(svg, "YearlySmallMultiples");
export const getDmGridStrip = retryingLazy(svg, "DmGridStrip");
export const getCoverageByYear = retryingLazy(svg, "CoverageByYear");
export const getMzForest = retryingLazy(svg, "MzForest");
export const getVrpScatter = retryingLazy(svg, "VrpScatter");
export const getEquityChart = retryingLazy(svg, "EquityChart");
export const getPurgeDiagram = retryingLazy(svg, "PurgeDiagram");
export const getSignAgreementLadder = retryingLazy(svg, "SignAgreementLadder");
export const getLeakDumbbell = retryingLazy(svg, "LeakDumbbell");
