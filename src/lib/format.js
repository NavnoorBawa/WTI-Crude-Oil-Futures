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

// Rounding must never carry a p-value onto a threshold it is not at: 0.0496 is not "p = 0.05",
// 0.9996 is not "p = 1.00", and 0.0548 is not "p = 0.05" either. Digits are added until the text
// leaves the threshold, else "p < t" (from below) or "p > t" (from above).
const P_THRESHOLDS = [0.01, 0.05, 0.1, 1];
const MAX_P_DIGITS = 4;
export const fmtP = (value) => {
  const p = num(value);
  if (p == null) return null;
  if (p < 0.001) return "p < 0.001";
  const crossed = (text) => P_THRESHOLDS.find((t) => p < t && Number(text) >= t);
  const landed = (text) => P_THRESHOLDS.find((t) => p > t && Number(text) === t);
  let digits = p < 0.01 ? 3 : 2;
  let text = p.toFixed(digits);
  while ((crossed(text) != null || landed(text) != null) && digits < MAX_P_DIGITS) {
    digits += 1;
    text = p.toFixed(digits);
  }
  const below = crossed(text);
  if (below != null) return `p < ${below}`;
  const above = landed(text);
  return above != null ? `p > ${above}` : `p = ${text}`;
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
const ctHour = (ms) => Number(formatter({ timeZone: CT_ZONE, hour: "numeric", hourCycle: "h23" }).format(ms));

// Epoch ms of a whole hour of US Central wall time on a calendar date (YYYY-MM-DD), e.g. the
// 17:00 CT end of a contract's last session. Chicago is UTC-5 (CDT) or UTC-6 (CST).
export const ctWallTimeMs = (dateText, hour) => {
  const match = typeof dateText === "string" ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText) : null;
  if (!match) return null;
  const wallAsUtc = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), hour);
  const candidates = [5, 6].map((offsetHours) => wallAsUtc + offsetHours * 60 * 60 * 1000);
  return candidates.find((ms) => ctHour(ms) === hour) ?? null;
};

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

// ------------------------------------------------------------------------------ research helpers

/** Integer with thousands separators: 4370 -> "4,370". */
export const fmtInt = (value) => {
  const n = num(value);
  return n == null ? DASH : Math.round(n).toLocaleString("en-US");
};

/** A share held as a fraction (0.9988) shown as a percentage ("99.88%"). */
export const fmtShare = (value, digits = 1) => {
  const n = num(value);
  return n == null ? DASH : `${(n * 100).toFixed(digits)}%`;
};

/** p-value text without the "p = " prefix, for table cells ("0.0059", "< 0.001"). */
export const fmtPValue = (value) => {
  const text = fmtP(value);
  return text == null ? DASH : text.replace(/^p = /, "").replace(/^p ([<>]) /, "$1 ");
};

// Source prose quotes exchange times in ET while the page's clock is CT. Both zones change for
// daylight saving on the same days, so CT is always one hour behind: each "HH:MM ET" gets its twin.
export const withCentralTime = (text) =>
  typeof text === "string"
    ? text.replace(/\b(\d{1,2}):(\d{2}) ET\b/g, (match, hour, minute) => `${match} / ${(Number(hour) + 23) % 24}:${minute} CT`)
    : text;

/** Elapsed time as a compact age: "12m", "2h 14m", "3d 4h". */
export const fmtAge = (ms) => {
  const n = num(ms);
  if (n == null || n < 0) return DASH;
  const minutes = Math.floor(n / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
};

/** "2008-05-08" -> "May 2008" (calendar month, never shifted by time zone). */
export const fmtMonthYear = (value) => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}/.test(value)) return DASH;
  return formatter({ timeZone: "UTC", month: "short", year: "numeric" }).format(Date.parse(value.slice(0, 10)));
};

/** "May 2008 – Oct 2026" from two ISO dates; a missing side degrades to the other. */
export const fmtSpan = (start, end) => {
  const a = fmtMonthYear(start);
  const b = fmtMonthYear(end);
  if (a === DASH || b === DASH || a === b) return a === DASH ? b : a;
  return `${a} – ${b}`;
};

/** The calendar date `count` weekdays after an ISO date (exchange holidays are not modelled). */
export const addWeekdays = (dateText, count) => {
  if (typeof dateText !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(dateText)) return null;
  const date = new Date(`${dateText}T00:00:00Z`);
  let left = count;
  while (left > 0) {
    date.setUTCDate(date.getUTCDate() + 1);
    const day = date.getUTCDay();
    if (day !== 0 && day !== 6) left -= 1;
  }
  return date.toISOString().slice(0, 10);
};

/** Ordinal for percentile ranks: 1 -> "1st", 68.3 -> "68th", 92 -> "92nd", 13 -> "13th". */
export const ordinal = (value) => {
  const n = num(value);
  if (n == null) return DASH;
  const r = Math.round(n);
  const mod100 = r % 100;
  const suffix = mod100 >= 11 && mod100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[r % 10] ?? "th");
  return `${r}${suffix}`;
};

/** First characters of a hex digest, shown next to the full value (in a title attribute). */
export const shortHash = (value, length = 12) =>
  typeof value === "string" && /^[0-9a-f]{16,}$/i.test(value) ? value.slice(0, length) : DASH;

/** "har_iv_range" -> "Har iv range": a readable fallback label for unknown payload keys. */
export const humanizeKey = (key) => {
  if (typeof key !== "string" || !key) return DASH;
  const text = key.replace(/_pct$/, " %").replace(/_/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
};
