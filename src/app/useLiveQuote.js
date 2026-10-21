import { useEffect, useRef, useState } from "react";
import { BASE_URL, LIVE_PRICE_POLL_MS, LIVE_PRICE_URL, REQUEST_TIMEOUT_MS, STATIC_DATA_MODE } from "./config";
import { isQuoteUsable, normalizeQuote } from "./quote";

/**
 * Client-side price from a tiny price.json. Production points VITE_LIVE_PRICE_URL at the repo's
 * live-data branch (refreshed every 15 min without a Pages deploy); freeze.py also bakes a
 * same-origin price.json into every deploy. Both are fetched and the NEWEST quote (by exchange
 * market_time, else fetched_at) wins, but only if it is newer than the frozen snapshot itself, for
 * the same contract and within 25% of its price (isQuoteUsable); otherwise the snapshot stands.
 *
 * @param {object|null} data the current data.json snapshot
 * @returns {object|null} the newest usable normalized quote
 */
export default function useLiveQuote(data) {
  const [liveQuote, setLiveQuote] = useState(null);
  const latestDataRef = useRef(data);
  latestDataRef.current = data;

  useEffect(() => {
    if (!STATIC_DATA_MODE) return undefined; // local dev: the backend price is already live
    let isDisposed = false;
    let requestInFlight = false;
    const activeControllers = new Set();
    const priceUrls = [LIVE_PRICE_URL, `${BASE_URL}price.json`]
      .filter((url, index, urls) => url && urls.indexOf(url) === index);

    const fetchQuote = async (sourceUrl) => {
      const controller = new AbortController();
      activeControllers.add(controller);
      const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      try {
        // The query string avoids reusing an older browser cache entry; GitHub's raw CDN can
        // still serve a copy a few minutes old, which the quote's own timestamp exposes.
        const requestUrl = new URL(sourceUrl, window.location.href);
        requestUrl.searchParams.set("_", String(Date.now()));
        const response = await fetch(requestUrl, {
          cache: "no-store",
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        return response.ok ? normalizeQuote(await response.json()) : null;
      } catch {
        return null;
      } finally {
        clearTimeout(timeoutId);
        activeControllers.delete(controller);
      }
    };

    const fetchLivePrice = async () => {
      if (isDisposed || requestInFlight) return;
      requestInFlight = true;
      try {
        const quotes = await Promise.all(priceUrls.map(fetchQuote));
        if (isDisposed) return;
        const newest = quotes
          .filter((quote) => quote && (!latestDataRef.current || isQuoteUsable(quote, latestDataRef.current)))
          .reduce((best, quote) => (!best || quote.timeMs > best.timeMs ? quote : best), null);
        if (!newest) return;
        // Never replace a quote with an older one (e.g. the baked copy after a live tick).
        setLiveQuote((previous) => (previous && previous.timeMs >= newest.timeMs ? previous : newest));
      } finally {
        requestInFlight = false;
      }
    };

    fetchLivePrice();
    const id = setInterval(fetchLivePrice, LIVE_PRICE_POLL_MS);
    return () => {
      isDisposed = true;
      clearInterval(id);
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
    };
  }, []);

  return liveQuote;
}
