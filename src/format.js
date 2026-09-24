// Formatting helpers shared by the dashboard and the chart. Every payload number goes through
// num() first: data.json is produced by a Python pipeline, so a field can arrive as a string,
// null or garbage, and one bad field must render as "—" instead of throwing inside JSX.

export const DASH = "—";
const MINUS = "−";

export const num = (value) => {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

export const fmtNum = (value, digits = 1) => {
  const n = num(value);
  if (n == null) return DASH;
  const rounded = Number(n.toFixed(digits));
  return rounded < 0 ? `${MINUS}${Math.abs(rounded).toFixed(digits)}` : Math.abs(rounded).toFixed(digits);
};

// Signed number with a real minus sign; a value that rounds to zero is shown unsigned ("0.0").
export const fmtSigned = (value, digits = 1, suffix = "") => {
  const n = num(value);
  if (n == null) return DASH;
  const rounded = Number(n.toFixed(digits));
  if (rounded === 0) return `${(0).toFixed(digits)}${suffix}`;
  return `${rounded > 0 ? "+" : MINUS}${Math.abs(rounded).toFixed(digits)}${suffix}`;
};

export const fmtPct = (value, digits = 1) => {
  const n = num(value);
  return n == null ? DASH : `${n.toFixed(digits)}%`;
};

export const fmtSignedPct = (value, digits = 1) => fmtSigned(value, digits, "%");

const usd = (n, digits) =>
  `$${n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

export const fmtUsd = (value, digits = 2) => {
  const n = num(value);
  return n == null ? DASH : usd(n, digits);
};

// "+$1,250" / "−$355" — never "+$-355".
export const fmtSignedUsd = (value, digits = 0) => {
  const n = num(value);
  if (n == null) return DASH;
  const rounded = Number(n.toFixed(digits));
  const body = usd(Math.abs(rounded), digits);
  return rounded === 0 ? body : `${rounded > 0 ? "+" : MINUS}${body}`;
};

export const fmtP = (value) => {
  const p = num(value);
  if (p == null) return null;
  if (p < 0.001) return "p < 0.001";
  return `p = ${p < 0.01 ? p.toFixed(3) : p.toFixed(2)}`;
};

export const signClass = (value, up = "up", down = "down") => {
  const n = num(value);
  if (n == null || n === 0) return "";
  return n > 0 ? up : down;
};

// ISO timestamps -> epoch ms. A datetime without a zone designator is treated as UTC (the
// pipeline writes naive UTC in a few places); a bare YYYY-MM-DD already parses as UTC.
export const toMs = (value) => {
  if (typeof value !== "string" || value.trim() === "") return null;
  const text = /T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(value) ? `${value}Z` : value;
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms : null;
};

// Every clock on the page is US Central (CME's exchange time), labelled "CT".
export const CT_ZONE = "America/Chicago";
const formatterCache = new Map();
const formatter = (options) => {
  const key = JSON.stringify(options);
  if (!formatterCache.has(key)) formatterCache.set(key, new Intl.DateTimeFormat("en-US", options));
  return formatterCache.get(key);
};
const ctYear = (ms) => formatter({ timeZone: CT_ZONE, year: "numeric" }).format(ms);

// year: "auto" adds the year only when it differs from the current one (i.e. when it is ambiguous).
export const fmtCT = (ms, { date = true, time = true, seconds = false, year = "auto" } = {}) => {
  if (ms == null || !Number.isFinite(ms)) return DASH;
  const showYear = year === "auto" ? ctYear(ms) !== ctYear(Date.now()) : Boolean(year);
  const options = { timeZone: CT_ZONE };
  if (date) {
    options.month = "short";
    options.day = "numeric";
    if (showYear) options.year = "numeric";
  }
  if (time) {
    options.hour = "numeric";
    options.minute = "2-digit";
    if (seconds) options.second = "2-digit";
  }
  return `${formatter(options).format(ms)}${time ? " CT" : ""}`;
};

// Calendar dates (YYYY-MM-DD) are not instants: format them as-is, never shifted into CT.
export const fmtDate = (value, { year = true } = {}) => {
  if (typeof value !== "string") return DASH;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const options = { timeZone: "UTC", month: "short", day: "numeric" };
    if (year) options.year = "numeric";
    return formatter(options).format(Date.parse(value));
  }
  const ms = toMs(value);
  return ms == null ? DASH : fmtCT(ms, { time: false, year: year ? true : "auto" });
};
