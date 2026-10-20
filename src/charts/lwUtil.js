// Helpers shared by the lightweight-charts figures.

const DAY_MS = 24 * 60 * 60 * 1000;

/** lightweight-charts reports business days as "YYYY-MM-DD" or {year, month, day}. */
export const timeKey = (time) => {
  if (typeof time === "string") return time;
  if (time && typeof time === "object" && "year" in time) {
    return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
  }
  if (typeof time === "number") return new Date(time * 1000).toISOString().slice(0, 10);
  return null;
};

/** An ISO calendar date shifted by whole days. */
export const shiftDate = (dateText, days) =>
  new Date(Date.parse(`${dateText}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);

/**
 * Frames a chart on the last `days` of a date-sorted series (all of it when `days` is null), in
 * logical (bar index) units so `padBars` of empty space can be kept right of the last point.
 * @param {import("lightweight-charts").IChartApi} chart
 * @param {string[]} dates  the series' dates, ascending
 * @param {number|null} days
 * @param {number} [padBars]
 */
export const frameRange = (chart, dates, days, padBars = 0.5) => {
  const lastIndex = dates.length - 1;
  if (lastIndex < 0) return;
  let firstIndex = 0;
  if (days != null) {
    const from = shiftDate(dates[lastIndex], -days);
    firstIndex = Math.max(0, dates.findIndex((date) => date >= from));
  }
  chart.timeScale().setVisibleLogicalRange({ from: firstIndex - 0.5, to: lastIndex + padBars });
};
