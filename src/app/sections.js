// The section codes in the top bar, in page order. `id` is the panel's anchor. Every section with a
// code chip is listed, the price chart included: it stays last, after the research it gives context
// to, and the bar still reaches it. `group` sets the bar's divisions (a rule before the first code
// of each group) and the order the jump palette lists them in.
export const SECTION_GROUPS = [
  { id: "evidence", name: "Evidence" },
  { id: "market", name: "Market context" },
  { id: "integrity", name: "Integrity" },
  { id: "reference", name: "Reference" },
];

export const SECTIONS = [
  { id: "fcst", code: "FCST", name: "Forecast vs realized", group: "evidence" },
  { id: "modl", code: "MODL", name: "Model leaderboard", group: "evidence" },
  { id: "stab", code: "STAB", name: "Stability", group: "evidence" },
  { id: "calb", code: "CALB", name: "Calibration", group: "evidence" },
  { id: "risk", code: "RISK", name: "VaR backtest", group: "evidence" },
  { id: "cone", code: "CONE", name: "Volatility cone", group: "market" },
  { id: "vrp", code: "VRP", name: "Implied vs realized", group: "market" },
  { id: "live", code: "LIVE", name: "Live record", group: "integrity" },
  { id: "leak", code: "LEAK", name: "Leak post-mortem", group: "integrity" },
  { id: "stdy", code: "STDY", name: "Side studies", group: "reference" },
  { id: "ledg", code: "LEDG", name: "Research ledger", group: "reference" },
  { id: "meth", code: "METH", name: "Methodology", group: "reference" },
  { id: "mkt", code: "MKT", name: "Market", group: "reference" },
];

/** Whether the section opens a group in the bar (the first code of its group, after the first group). */
export const startsGroup = (index) => index > 0 && SECTIONS[index].group !== SECTIONS[index - 1].group;

/** The section entry for an id, or null. */
export const sectionById = (id) => SECTIONS.find((section) => section.id === id) ?? null;
