// The VaR breach rule, in one place: VarChart colours its bars by it, and the RISK panel's table and
// method notes count by it, so the chart, the panel and the tests can never apply different rules.
// No chart code here, so the panel does not load the chart chunk to use it.

/**
 * Which VaR line a week's 5-session log return fell below: "var99", "var95", "ok" (neither), or
 * "none" while the VaR warms up (no VaR, or no return, that week). Pure.
 * @param {{ret: number|null, var95: number|null, var99: number|null}} row  percent; VaR as a positive loss
 * @returns {"var99"|"var95"|"ok"|"none"}
 */
export const breachOf = (row) => {
  if (row?.ret == null || row.var95 == null || row.var99 == null) return "none";
  if (row.ret < -row.var99) return "var99";
  if (row.ret < -row.var95) return "var95";
  return "ok";
};

/**
 * The breaches the chart colours, counted over the weeks that have a VaR: `e99` below the 99% line,
 * `e95` below the 95% line (the 99% breaches included). The series is stored rounded to 0.1 pt, so a
 * week whose rounded return equals its rounded VaR is not counted, while the Kupiec counts use
 * unrounded values. Pure.
 * @returns {{n: number, e99: number, e95: number}}
 */
export const drawnBreachCounts = (series) => {
  const breaches = (series ?? []).map(breachOf).filter((breach) => breach !== "none");
  return {
    n: breaches.length,
    e99: breaches.filter((breach) => breach === "var99").length,
    e95: breaches.filter((breach) => breach !== "ok").length,
  };
};
