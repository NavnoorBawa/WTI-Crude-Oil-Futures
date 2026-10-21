// The section codes in the top bar, in page order. `id` is the panel's anchor. Every section with a
// code chip is listed, the price chart included: it stays last, after the research it gives context
// to, and the bar still reaches it.
export const SECTIONS = [
  { id: "fcst", code: "FCST", name: "Forecast vs realized" },
  { id: "modl", code: "MODL", name: "Model leaderboard" },
  { id: "stab", code: "STAB", name: "Stability" },
  { id: "calb", code: "CALB", name: "Calibration" },
  { id: "risk", code: "RISK", name: "VaR backtest" },
  { id: "cone", code: "CONE", name: "Volatility cone" },
  { id: "vrp", code: "VRP", name: "Implied vs realized" },
  { id: "live", code: "LIVE", name: "Live record" },
  { id: "leak", code: "LEAK", name: "Leak post-mortem" },
  { id: "stdy", code: "STDY", name: "Side studies" },
  { id: "ledg", code: "LEDG", name: "Research ledger" },
  { id: "meth", code: "METH", name: "Methodology" },
  { id: "mkt", code: "MKT", name: "Market" },
];
