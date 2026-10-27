import { DELAY_NOTE, LIVE_BADGE_MAX_AGE_MS } from "../app/config";
import Chip from "../components/Chip";
import Sparkline from "../components/Sparkline";
import { DASH, fmtCT, fmtDate, fmtSigned, fmtSignedPct, fmtUsd, num, signClass } from "../lib/format";
import { asArray, asText } from "../lib/research";
import useNow from "../lib/useNow";

// price_change_quality values that mean the quoted contract is not the one CL=F's last settled
// close belongs to: the session is the new contract's first, so recent_closes ends on the old one.
const ROLL_QUALITIES = new Set(["daily_close_new_contract", "unavailable_contract_roll"]);

/** LIVE while the exchange quote is under 30 minutes old, else LAST / SNAPSHOT. */
function QuoteStamp({ quote }) {
  const now = useNow(30 * 1000);
  if (quote.kind === "quote" && quote.marketTimeMs != null) {
    const isLive = now - quote.marketTimeMs <= LIVE_BADGE_MAX_AGE_MS;
    return (
      <span className="quote-stamp" title={DELAY_NOTE}>
        <Chip tone={isLive ? "live" : "muted"}>{isLive ? "Live" : "Last"}</Chip>
        <span className="num">{fmtCT(quote.marketTimeMs)}</span>
        <span className="sr-only">{DELAY_NOTE}</span>
      </span>
    );
  }
  // "Quote", not "Snapshot": the time shown is the price's market time, while the footer's data
  // snapshot time is when the payload was frozen.
  const label = { backend: "Updated" }[quote.kind] || "Quote";
  const title = quote.kind === "quote" ? DELAY_NOTE : quote.kind === "backend" ? undefined
    : "Exchange time of the price in this data snapshot (exchange-delayed)";
  return (
    <span className="quote-stamp" title={title}>
      <Chip tone="muted">{label}</Chip>
      <span className="num">
        {quote.kind === "quote" ? `fetched ${fmtCT(quote.fetchedAtMs)}` : quote.timeMs != null ? fmtCT(quote.timeMs) : DASH}
      </span>
    </span>
  );
}

/**
 * The sparkline's series in words. recent_closes is CL=F, the continuous front month, unadjusted:
 * it jumps at every roll, and on a new contract's first session it ends on the expiring contract,
 * so it is then drawn faint and its title gives its last date rather than read as this contract's.
 * `title` is the sparkline's hover title and accessible label; `text` is the same in a few words.
 */
export const sparklineLabel = (data, count) => {
  const series = asText(data?.contract?.history_symbol) ?? "CL=F";
  const dates = asArray(data?.vol_forecast?.recent_closes?.date);
  const last = typeof dates[dates.length - 1] === "string" ? dates[dates.length - 1] : null;
  const rolled = ROLL_QUALITIES.has(data?.price_change_quality);
  const symbol = asText(data?.contract?.symbol);
  return {
    rolled,
    text: rolled && last ? `${series} to ${fmtDate(last, { year: false })}, before the roll` : `${series}, ${count} closes`,
    title: `${series} continuous front month, unadjusted (jumps at each roll): last ${count} daily closes`
      + (rolled ? `${last ? ` through ${fmtDate(last)}` : ""}, on the expiring contract, not ${symbol ?? "the contract quoted"}` : ""),
  };
};

const NEW_YORK_DATE = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });
const ISO_DAY = /^\d{4}-\d{2}-\d{2}/;

/**
 * Calendar days from today's New York date to the contract's last trade date, as backend/oil.py
 * counts days_to_expiry at freeze time (never below 0), so the count ages with the clock when the
 * snapshot is old. The snapshot's own days_to_expiry only when the contract carries no date. Pure.
 */
export const daysToExpiry = (contract, nowMs) => {
  const last = asText(contract?.last_trade_date) ?? asText(contract?.expiry_date);
  if (last && ISO_DAY.test(last) && Number.isFinite(nowMs)) {
    const parts = Object.fromEntries(NEW_YORK_DATE.formatToParts(nowMs).map((part) => [part.type, part.value]));
    const days = Math.round((Date.parse(last.slice(0, 10)) - Date.parse(`${parts.year}-${parts.month}-${parts.day}`)) / 86400000);
    if (Number.isFinite(days)) return Math.max(0, days);
  }
  return num(contract?.days_to_expiry);
};

/**
 * The market in one line: contract, price, change, quote time (CT) and days to expiry. The quote
 * is the snapshot's own unless a newer price.json quote for the same contract is usable. The change
 * is context on a volatility page, so it is set in the text colour with a direction arrow, not in
 * the red and green that would outweigh the forecast above it.
 */
export default function MarketStrip({ data, quote }) {
  const now = useNow(60 * 1000);
  const symbol = asText(data?.contract?.symbol);
  const days = daysToExpiry(data?.contract, now);
  const volume = asText(data?.volume_display);
  const hasPrice = num(quote.price) > 0;
  const closes = Array.isArray(data?.vol_forecast?.recent_closes?.close) ? data.vol_forecast.recent_closes.close : [];
  const spark = sparklineLabel(data, closes.length);
  const arrow = hasPrice ? signClass(quote.changePct ?? quote.change, "▲", "▼") : "";
  return (
    <section className="market-strip" aria-label="WTI futures market">
      <span className="ms-symbol">
        <Chip tone="code" title="NYMEX WTI crude oil futures, front contract">{symbol ?? "CL"}</Chip>
        <span className="sr-only">NYMEX WTI crude oil futures</span>
      </span>
      {closes.length > 1 && <Sparkline values={closes} label={spark.title} muted={spark.rolled} />}
      <span className="ms-price num">{hasPrice ? fmtUsd(quote.price) : DASH}</span>
      <span className="ms-change num">
        {arrow && <span className="ms-arrow" aria-hidden="true">{arrow}</span>}
        {hasPrice ? `${fmtSigned(quote.change, 2)} (${fmtSignedPct(quote.changePct, 2)})` : DASH}
      </span>
      <QuoteStamp quote={quote} />
      {days != null && <span className="ms-meta num">{days}d to expiry</span>}
      {volume && <span className="ms-meta num">Vol {volume}</span>}
      <a className="ms-link" href="#mkt">Price chart</a>
    </section>
  );
}
