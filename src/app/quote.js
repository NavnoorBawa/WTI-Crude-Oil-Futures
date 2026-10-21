// Which price the header shows: the snapshot's own quote, or a newer price.json quote for the same
// contract. Pure functions, unit-tested in tests/frontend/dashboard.test.mjs.
import { ctWallTimeMs, fmtCT, num, toMs } from "../lib/format";
import { MAX_CLOCK_SKEW_MS, MAX_QUOTE_DEVIATION, STATIC_DATA_MODE } from "./config";

// price.json (live-data branch, or the same-origin copy baked into each deploy) -> a header quote.
// market_time is the exchange's own quote time; older files only carry fetched_at. contract is the
// CL contract the quote belongs to (older files have none).
export const normalizeQuote = (raw) => {
  const price = num(raw?.price);
  const marketTimeMs = toMs(raw?.market_time);
  const fetchedAtMs = toMs(raw?.fetched_at);
  const timeMs = marketTimeMs ?? fetchedAtMs;
  if (price == null || price <= 0 || timeMs == null || timeMs > Date.now() + MAX_CLOCK_SKEW_MS) {
    return null;
  }
  const previousClose = num(raw?.prev_close);
  const change = previousClose > 0 ? Number((price - previousClose).toFixed(2)) : null;
  const changePct = num(raw?.change_pct)
    ?? (previousClose > 0 ? ((price - previousClose) / previousClose) * 100 : null);
  const contract = typeof raw?.contract === "string" && raw.contract.trim() ? raw.contract.trim() : null;
  return { price, change, changePct, contract, marketTimeMs, fetchedAtMs, timeMs };
};

// The snapshot price's own time: the exchange time of its quote when the payload carries it,
// else when it was frozen (a Saturday freeze still holds Friday's close).
export const snapshotQuoteMs = (data) => toMs(data?.contract?.market_time) ?? toMs(data?.frozen_at);

// The header labels the price with the snapshot's contract, so a quote must be for that contract.
// A file without a contract is accepted only until the snapshot contract's last session ends
// (17:00 CT on its last trade date); after that CL=F quotes the next contract.
const isSnapshotContract = (quote, data) => {
  const symbol = data?.contract?.symbol;
  if (quote.contract && typeof symbol === "string" && symbol.trim()) return quote.contract === symbol.trim();
  const sessionEndMs = ctWallTimeMs(data?.contract?.last_trade_date, 17);
  return sessionEndMs == null || quote.timeMs < sessionEndMs;
};

// A quote may replace the snapshot price only when it is NEWER than the snapshot's quote, for the
// same contract, and plausible (within 25% of it): a stale CDN copy, the next contract's price or
// a bad tick must never override the snapshot.
export const isQuoteUsable = (quote, data) => {
  if (!quote) return false;
  const snapshotMs = snapshotQuoteMs(data);
  if (snapshotMs != null && quote.timeMs <= snapshotMs) return false;
  if (!isSnapshotContract(quote, data)) return false;
  const snapshotPrice = num(data?.current_price);
  return !(snapshotPrice > 0 && Math.abs(quote.price / snapshotPrice - 1) > MAX_QUOTE_DEVIATION);
};

// A quote's daily change may splice two contracts on the first session after a roll. It is shown
// only when it cannot: never while the snapshot withholds its own change for a roll, and never from
// an older price.json (no contract, so its change was not checked for a roll) when the snapshot
// says the session is the new contract's first. A snapshot frozen on a weekday evening reports
// 'unavailable_previous_settlement': that is about the snapshot's own Yahoo frame, not the quote,
// whose previous close is the settlement that just happened, so the quote keeps its change.
const ROLL_CHANGE_QUALITIES = new Set(["daily_close_new_contract", "unavailable_contract_roll"]);
export const resolveQuote = (liveQuote, data) => {
  if (liveQuote && isQuoteUsable(liveQuote, data)) {
    const quality = typeof data?.price_change_quality === "string" ? data.price_change_quality : "";
    const withheld = quality === "unavailable_contract_roll"
      || (!liveQuote.contract && ROLL_CHANGE_QUALITIES.has(quality));
    return { kind: "quote", ...liveQuote, ...(withheld ? { change: null, changePct: null } : {}) };
  }
  return {
    kind: STATIC_DATA_MODE ? "snapshot" : "backend",
    price: num(data?.current_price),
    change: num(data?.price_change),
    changePct: num(data?.price_change_percent),
    timeMs: snapshotQuoteMs(data) ?? toMs(data?.last_update),
  };
};

export const describeQuoteTime = (quote) => {
  if (quote.kind === "quote") {
    return quote.marketTimeMs != null
      ? `Exchange quote ${fmtCT(quote.marketTimeMs)}, delayed ~10 min`
      : `Quote fetched ${fmtCT(quote.fetchedAtMs)}`;
  }
  const label = quote.kind === "backend" ? "Updated" : "Snapshot";
  return quote.timeMs != null ? `${label} ${fmtCT(quote.timeMs)}` : label;
};
