import { DELAY_NOTE, LIVE_BADGE_MAX_AGE_MS } from "../app/config";
import Chip from "../components/Chip";
import Sparkline from "../components/Sparkline";
import { DASH, fmtCT, fmtSigned, fmtSignedPct, fmtUsd, num, signClass } from "../lib/format";
import { asText } from "../lib/research";
import useNow from "../lib/useNow";

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
  const label = { quote: "Quote", backend: "Updated" }[quote.kind] || "Snapshot";
  return (
    <span className="quote-stamp" title={quote.kind === "quote" ? DELAY_NOTE : undefined}>
      <Chip tone="muted">{label}</Chip>
      <span className="num">
        {quote.kind === "quote" ? `fetched ${fmtCT(quote.fetchedAtMs)}` : quote.timeMs != null ? fmtCT(quote.timeMs) : DASH}
      </span>
    </span>
  );
}

/**
 * The market in one line: contract, price, change, quote time (CT) and days to expiry. The quote
 * is the snapshot's own unless a newer price.json quote for the same contract is usable.
 */
export default function MarketStrip({ data, quote }) {
  const symbol = asText(data?.contract?.symbol);
  const days = num(data?.contract?.days_to_expiry);
  const volume = asText(data?.volume_display);
  const hasPrice = num(quote.price) > 0;
  const closes = Array.isArray(data?.vol_forecast?.recent_closes?.close) ? data.vol_forecast.recent_closes.close : [];
  return (
    <section className="market-strip" aria-label="WTI futures market">
      <span className="ms-symbol">
        <Chip tone="code" title="NYMEX WTI crude oil futures, front contract">{symbol ?? "CL"}</Chip>
        <span className="sr-only">NYMEX WTI crude oil futures</span>
      </span>
      {closes.length > 1 && <Sparkline values={closes} label={`Last ${closes.length} daily closes`} />}
      <span className="ms-price num">{hasPrice ? fmtUsd(quote.price) : DASH}</span>
      <span className={`ms-change num ${signClass(quote.changePct ?? quote.change, "is-up", "is-down")}`}>
        {hasPrice ? `${fmtSigned(quote.change, 2)} (${fmtSignedPct(quote.changePct, 2)})` : DASH}
      </span>
      <QuoteStamp quote={quote} />
      {days != null && <span className="ms-meta num">{days}d to expiry</span>}
      {volume && <span className="ms-meta num">Vol {volume}</span>}
      <a className="ms-link" href="#mkt">Price chart</a>
    </section>
  );
}
