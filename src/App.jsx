import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ErrorBoundary from "./ErrorBoundary";
import useNow from "./useNow";
import {
  DASH,
  fmtCT,
  fmtDate,
  fmtNum,
  fmtP,
  fmtPct,
  fmtSigned,
  fmtSignedPct,
  fmtSignedUsd,
  fmtUsd,
  num,
  signClass,
  toMs,
} from "./format";

const clampMilliseconds = (rawValue, fallback, minimum, maximum) => {
  const parsed = Number(rawValue);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
};

// Static-snapshot mode (GitHub Pages): the React app reads a frozen data.json produced by
// freeze.py in CI instead of polling a live backend. BASE_URL handles the Pages sub-path.
const STATIC_DATA_MODE = import.meta.env.VITE_STATIC_DATA === "true";
const CONFIGURED_API_BASE = import.meta.env.VITE_API_BASE_URL;
const STATIC_DATA_URL = `${import.meta.env.BASE_URL}data.json`;
const REPO_URL = "https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures";
// The chart library is about half the bundle and the chart sits below the fold, so it loads in its
// own chunk after the numbers above it have rendered.
const Chart = lazy(() => import("./Chart"));

// A frozen snapshot changes every ~4h, so it is re-checked every 15 min; a live backend polls faster.
const pollIntervalMs = clampMilliseconds(
  import.meta.env.VITE_POLL_INTERVAL_MS,
  STATIC_DATA_MODE ? 15 * 60 * 1000 : 15000,
  5000,
  24 * 60 * 60 * 1000
);
const startupRetryMs = clampMilliseconds(
  import.meta.env.VITE_STARTUP_RETRY_MS,
  5000,
  2000,
  60 * 1000
);
const REQUEST_TIMEOUT_MS = 30 * 1000;
const MAX_INITIAL_FETCH_ATTEMPTS = 5;
const LIVE_PRICE_POLL_MS = 3 * 60 * 1000;
const LIVE_BADGE_MAX_AGE_MS = 30 * 60 * 1000;      // exchange quote time, not fetch time
const STALE_SNAPSHOT_MS = 12 * 60 * 60 * 1000;     // GitHub often runs the 4h schedule 4-9h apart
const MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;
const MAX_QUOTE_DEVIATION = 0.25;                  // a quote >25% from the snapshot is a bad tick
const MIN_INDEPENDENT_CALLS = 18;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const INITIAL_LOADING_MESSAGE = STATIC_DATA_MODE
  ? "Loading the latest market snapshot"
  : "Connecting to the data service";
const DELAY_NOTE = "Futures quotes come from Yahoo Finance and are exchange-delayed by about 10 minutes.";

// RETRACTED 2026-06-20: the 1W "edge" (Sharpe 2.44/2.07, 65.8% acc) was a look-ahead leak —
// the walk-forward trained on rows whose 5-day targets matured after the prediction point. After a
// purge/embargo the signal is a coin flip (48-52% acc, p>0.2, negative Sharpe). No tradeable edge.
// Dashboard shows the retraction notice; the stance reads RETRACTED and the model's raw output is
// hidden unless the reader explicitly asks for it (then drawn gray, labelled "not a forecast").

// In static mode the "endpoint" is just the frozen JSON file shipped alongside the site.
const buildRequestUrl = (apiBase) =>
  apiBase.endsWith(".json") ? apiBase : `${apiBase}/data`;

const getApiBaseCandidates = () => {
  if (STATIC_DATA_MODE) return [STATIC_DATA_URL];
  if (CONFIGURED_API_BASE) return [CONFIGURED_API_BASE];
  // Local dev default only; vite.config.js refuses a production build that would ship it.
  return ["http://127.0.0.1:9000"];
};

// price.json (live-data branch, or the same-origin copy baked into each deploy) -> a header quote.
// market_time is the exchange's own quote time; older files only carry fetched_at.
const normalizeQuote = (raw) => {
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
  return { price, change, changePct, marketTimeMs, fetchedAtMs, timeMs };
};

// The snapshot price's own time: the exchange time of its quote when the payload carries it,
// else when it was frozen (a Saturday freeze still holds Friday's close).
const snapshotQuoteMs = (data) => toMs(data?.contract?.market_time) ?? toMs(data?.frozen_at);

// A quote may replace the snapshot price only when it is NEWER than the snapshot's quote and
// plausible (within 25% of it): a stale CDN copy or a bad tick must never override fresher data.
const isQuoteUsable = (quote, data) => {
  if (!quote) return false;
  const snapshotMs = snapshotQuoteMs(data);
  if (snapshotMs != null && quote.timeMs <= snapshotMs) return false;
  const snapshotPrice = num(data?.current_price);
  return !(snapshotPrice > 0 && Math.abs(quote.price / snapshotPrice - 1) > MAX_QUOTE_DEVIATION);
};

const describeQuoteTime = (quote) => {
  if (quote.kind === "quote") {
    return quote.marketTimeMs != null
      ? `Exchange quote ${fmtCT(quote.marketTimeMs)}, delayed ~10 min`
      : `Quote fetched ${fmtCT(quote.fetchedAtMs)}`;
  }
  const label = quote.kind === "backend" ? "Updated" : "Snapshot";
  return quote.timeMs != null ? `${label} ${fmtCT(quote.timeMs)}` : label;
};

// The (retracted) 1W model output. Its % change is always measured against the snapshot's
// current_price, so the desk and the chart can never disagree about it.
const getModelTarget = (data) => {
  const future = data?.unified_data?.predicted?.future?.by_horizon?.["1w"];
  const predictions = data?.multi_horizon_predictions;
  const value = num(future?.value) ?? num(predictions?.predictions?.["1w"]);
  if (value == null || value <= 0) return null;
  const issuedMs = toMs(predictions?.last_update) ?? toMs(data?.frozen_at);
  const timeMs = toMs(future?.timestamp) ?? (issuedMs != null ? issuedMs + WEEK_MS : null);
  const ref = num(data?.current_price);
  return {
    value,
    upper: num(future?.upper) ?? num(predictions?.prediction_intervals?.["1w"]?.upper),
    lower: num(future?.lower) ?? num(predictions?.prediction_intervals?.["1w"]?.lower),
    timeSec: timeMs != null ? Math.floor(timeMs / 1000) : null,
    ref,
    pct: ref > 0 ? ((value - ref) / ref) * 100 : num(predictions?.percentage_changes?.["1w"]),
  };
};

// Kelly sizing from the walk-forward hit rate + profit factor. Clamped at zero: a negative Kelly
// fraction means "no positive edge", never a negative position size.
const computeSizing = (winRatePct, profitFactor, meanPnl) => {
  const winRate = num(winRatePct);
  const pf = num(profitFactor);
  if (winRate == null || pf == null || winRate <= 0 || winRate >= 100 || pf <= 0) return null;
  const p = winRate / 100;
  const payoff = (pf * (1 - p)) / p; // average win / average loss
  const fullKelly = p - (1 - p) / payoff;
  if (!Number.isFinite(fullKelly) || fullKelly <= 0) return { noEdge: true, winRate, pf };
  // mean = (1-p)·avgLoss·(pf-1), so avgLoss = mean / ((1-p)(pf-1)); pf > 1 whenever Kelly > 0.
  const mean = num(meanPnl);
  const avgLoss = mean != null ? mean / ((1 - p) * (pf - 1)) : null;
  const hasLoss = Number.isFinite(avgLoss) && avgLoss > 0;
  return {
    noEdge: false,
    winRate,
    pf,
    fullKellyPct: fullKelly * 100,
    halfKellyPct: fullKelly * 50,
    avgLoss: hasLoss ? Math.round(avgLoss) : null,
    acctPer1: hasLoss ? Math.max(5000, Math.round(avgLoss / 0.02 / 5000) * 5000) : null,
  };
};

// Live record — git-committed daily calls, resolved after 1 week (backend/live_record.py).
// Every entry/resolution is timestamped by a bot commit, so the record can't be back-dated. Calls
// are recorded once per trading day and overlap for a week, so the gate counts INDEPENDENT calls.
const describeLiveRecord = (lr) => {
  if (!lr || typeof lr !== "object") return null;
  const calls = num(lr.n_calls);
  const scored = num(lr.n_resolved_directional);
  const independentRaw = num(lr.n_independent_directional);
  const independent = independentRaw ?? scored;
  const hitRate = num(lr.hit_rate_pct);
  const pending = num(lr.n_pending);
  const neutral = num(lr.n_neutral);
  const skipped = num(lr.n_skipped_roll);
  const ineligible = num(lr.n_ineligible_directional);
  const since = typeof lr.first_call_date === "string" ? ` since ${fmtDate(lr.first_call_date)}` : "";

  const parts = [];
  if (calls != null) parts.push(`${calls} call${calls === 1 ? "" : "s"} recorded${since} (one per trading day)`);
  if (scored != null) {
    parts.push(`${scored} scored directional${independentRaw != null ? ` (${independentRaw} independent)` : ""}`);
  }
  if (pending) parts.push(`${pending} pending`);
  if (neutral) parts.push(`${neutral} neutral (no trade)`);
  if (skipped) parts.push(`${skipped} skipped at contract rolls`);
  if (ineligible) parts.push(`${ineligible} directional excluded from scoring (retracted model)`);
  if (parts.length === 0) return null;

  let verdict = null;
  if (independent != null && independent < MIN_INDEPENDENT_CALLS) {
    verdict = `too few to validate (need ≥${MIN_INDEPENDENT_CALLS} independent)`;
  } else if (independent != null && hitRate != null) {
    verdict = `${fmtPct(hitRate)} hit rate on ${independent} independent calls`;
  }
  return { text: parts.join(" · "), verdict };
};

// OOS equity curve from the walk-forward per-trade series (cumulative net P&L).
const buildEquityCurve = (trades) => {
  if (trades.length < 10) return null;
  let cum = 0;
  const pts = trades.map((t) => { cum += num(t?.pnl) || 0; return cum; });
  const lo = Math.min(0, ...pts);
  const hi = Math.max(...pts);
  const span = hi - lo || 1;
  const W = 560, H = 64;
  const x = (i) => (i / (pts.length - 1)) * W;
  const y = (v) => H - ((v - lo) / span) * H;
  const line = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return {
    line,
    area: `${line} L${W},${H} L0,${H} Z`,
    W, H,
    zeroY: y(0),
    first: typeof trades[0]?.t === "string" ? trades[0].t : null,
    last: typeof trades[trades.length - 1]?.t === "string" ? trades[trades.length - 1].t : null,
    total: pts[pts.length - 1],
  };
};

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [loadingMessage, setLoadingMessage] = useState(INITIAL_LOADING_MESSAGE);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [geoOpen, setGeoOpen] = useState(false);
  const [liveQuote, setLiveQuote] = useState(null);
  const [showRetracted, setShowRetracted] = useState(false);
  const latestDataRef = useRef(null);

  latestDataRef.current = data;

  useEffect(() => {
    let isDisposed = false;
    let requestInFlight = false;
    let retryPending = false;
    let initialAttemptCount = 0;
    let retryTimeoutId = null;
    const activeControllers = new Set();

    const clearRetryTimeout = () => {
      if (retryTimeoutId) {
        clearTimeout(retryTimeoutId);
        retryTimeoutId = null;
      }
    };

    const retryDelayMs = (requestedDelayMs) =>
      clampMilliseconds(requestedDelayMs, startupRetryMs, 2000, 60 * 1000);

    const scheduleInitialRetry = (requestedDelayMs, message) => {
      if (
        isDisposed ||
        latestDataRef.current ||
        initialAttemptCount >= MAX_INITIAL_FETCH_ATTEMPTS
      ) {
        return false;
      }

      const nextAttempt = initialAttemptCount + 1;
      retryPending = true;
      clearRetryTimeout();
      setLoading(true);
      setError(null);
      setLoadingMessage(
        `${message} Retrying (${nextAttempt}/${MAX_INITIAL_FETCH_ATTEMPTS})…`
      );
      retryTimeoutId = setTimeout(() => {
        retryTimeoutId = null;
        retryPending = false;
        if (!isDisposed) fetchData(true);
      }, retryDelayMs(requestedDelayMs));
      return true;
    };

    // Fetch data function
    async function fetchData(isInitial = false) {
      if (isDisposed || requestInFlight) return;
      if (!isInitial && retryPending && !latestDataRef.current) return;

      requestInFlight = true;
      if (isInitial && !latestDataRef.current) initialAttemptCount += 1;

      try {
        if (isInitial && !latestDataRef.current) {
          setLoading(true);
          setError(null);
          setLoadingMessage(INITIAL_LOADING_MESSAGE);
        }

        const apiCandidates = getApiBaseCandidates();
        let result = null;
        let lastAttemptError = null;

        for (const apiBase of apiCandidates) {
          if (isDisposed) return;

          const controller = new AbortController();
          activeControllers.add(controller);
          let requestTimedOut = false;
          const timeoutId = setTimeout(() => {
            requestTimedOut = true;
            controller.abort();
          }, REQUEST_TIMEOUT_MS);

          try {
            const attempt = await fetch(buildRequestUrl(apiBase), {
              signal: controller.signal,
              method: 'GET',
              // The Pages CDN caches data.json for minutes; revalidate so a re-poll sees a new deploy.
              cache: STATIC_DATA_MODE ? 'no-cache' : 'default',
              headers: {
                'Accept': 'application/json',
              },
            });

            let responsePayload = null;
            let responseParseError = null;
            try {
              responsePayload = await attempt.json();
            } catch (parseError) {
              responseParseError = parseError;
            }

            const retryAfterHeader = Number(attempt.headers.get('Retry-After'));
            if (responsePayload?.error === 'SYSTEM_INITIALIZING') {
              const startupError = new Error(responsePayload.message || 'Backend is waking up');
              startupError.code = 'SYSTEM_INITIALIZING';
              const retryAfterSeconds = Number(responsePayload.retry_after_seconds);
              startupError.retryAfterMs = Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0
                ? retryAfterSeconds * 1000
                : Number.isFinite(retryAfterHeader) && retryAfterHeader > 0
                  ? retryAfterHeader * 1000
                  : startupRetryMs;
              throw startupError;
            }

            if (!attempt.ok) {
              const errorMessage = responsePayload?.message || responsePayload?.error || attempt.statusText || 'Request failed';
              throw new Error(`HTTP ${attempt.status}: ${errorMessage}`);
            }

            if (
              responseParseError ||
              responsePayload == null ||
              typeof responsePayload !== 'object' ||
              Array.isArray(responsePayload)
            ) {
              throw new Error('The data endpoint returned invalid JSON');
            }

            result = responsePayload;
            break;
          } catch (attemptErr) {
            if (isDisposed) return;
            if (requestTimedOut && attemptErr?.name === 'AbortError') {
              const timeoutError = new Error('Server timeout - Please wait and refresh');
              timeoutError.code = 'REQUEST_TIMEOUT';
              lastAttemptError = timeoutError;
            } else {
              lastAttemptError = attemptErr;
            }
          } finally {
            clearTimeout(timeoutId);
            activeControllers.delete(controller);
          }
        }

        if (isDisposed) return;
        if (!result) {
          throw lastAttemptError || new Error("No reachable API endpoint found");
        }

        // Update state in correct order
        clearRetryTimeout();
        retryPending = false;
        initialAttemptCount = 0;
        setData(result);
        setLastUpdate(Date.now());
        setError(null);
        setLoading(false);
        setLoadingMessage(INITIAL_LOADING_MESSAGE);

      } catch (err) {
        if (isDisposed) return;

        const isStarting = err?.code === 'SYSTEM_INITIALIZING';
        const userMessage = isStarting
          ? err.message || 'Backend is warming up the model.'
          : err?.code === 'REQUEST_TIMEOUT' || err?.name === 'AbortError'
            ? 'Server timeout - Please wait and refresh'
            : err?.name === 'TypeError' && String(err.message).includes('Failed to fetch')
              ? 'Cannot connect to the data service'
              : `Network error: ${err?.message || 'Unknown request failure'}`;

        if (!latestDataRef.current) {
          const scheduled = scheduleInitialRetry(
            isStarting ? err.retryAfterMs : startupRetryMs,
            userMessage
          );
          if (scheduled) return;
        }

        retryPending = false;
        setError(
          latestDataRef.current
            ? STATIC_DATA_MODE
              ? `Snapshot refresh failed: ${userMessage}. Showing the snapshot loaded earlier.`
              : `LIVE DATA DELAY: ${userMessage}. Showing last good market snapshot.`
            : userMessage
        );
        setLoading(false);
      } finally {
        requestInFlight = false;
      }
    }

    // Initial fetch
    fetchData(true);

    const interval = setInterval(() => {
      if (latestDataRef.current) fetchData(false);
    }, pollIntervalMs);

    return () => {
      isDisposed = true;
      clearRetryTimeout();
      clearInterval(interval);
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
    };
  }, []);

  // Client-side price — reads a tiny price.json. Production points VITE_LIVE_PRICE_URL at the
  // repo's live-data branch (refreshed every 15 min without a Pages deploy); freeze.py also bakes a
  // same-origin price.json into every deploy. Both are fetched and the NEWEST quote (by exchange
  // market_time, else fetched_at) wins, but only if it is newer than the frozen snapshot itself and
  // within 25% of its price — otherwise the snapshot price stands. The LIVE badge is keyed on the
  // exchange quote time (see QuoteStamp), never on when a CI job happened to fetch it.
  useEffect(() => {
    if (!STATIC_DATA_MODE) return undefined; // local dev: the backend price is already live
    let isDisposed = false;
    let requestInFlight = false;
    const activeControllers = new Set();
    const priceUrls = [import.meta.env.VITE_LIVE_PRICE_URL, `${import.meta.env.BASE_URL}price.json`]
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
          cache: 'no-store',
          signal: controller.signal,
          headers: { 'Accept': 'application/json' },
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

  // Header price + change must share one source: either the usable quote or the snapshot.
  const quote = useMemo(() => {
    const resolved = liveQuote && isQuoteUsable(liveQuote, data)
      ? { kind: "quote", ...liveQuote }
      : {
          kind: STATIC_DATA_MODE ? "snapshot" : "backend",
          price: num(data?.current_price),
          change: num(data?.price_change),
          changePct: num(data?.price_change_percent),
          timeMs: snapshotQuoteMs(data) ?? toMs(data?.last_update),
        };
    return { ...resolved, label: describeQuoteTime(resolved) };
  }, [liveQuote, data]);
  const modelTarget = useMemo(() => getModelTarget(data), [data]);
  const toggleRetracted = useCallback(() => setShowRetracted((shown) => !shown), []);
  const toggleGeo = useCallback(() => setGeoOpen((open) => !open), []);

  const frozenAtMs = toMs(data?.frozen_at);
  let body;
  if (loading && !data) {
    body = (
      <div className="tv-status-screen" role="status">
        <div className="tv-spinner" aria-hidden="true" />
        <div className="tv-status-title">WTI Crude Oil Futures</div>
        <div className="tv-status-text">{loadingMessage}</div>
      </div>
    );
  } else if (error && !data) {
    // Error screen - System designed to fail rather than show placeholder data
    body = (
      <div className="tv-status-screen" role="alert">
        <div className="tv-status-title is-error">Data connection unavailable</div>
        <div className="tv-status-text">{error}</div>
        <div className="tv-status-text">Real data only — no placeholder values are shown.</div>
      </div>
    );
  } else if (data?.error) {
    body = (
      <div className="tv-status-screen" role="alert">
        <div className="tv-status-title is-error">System error</div>
        <div className="tv-status-text">{String(data.error)}</div>
        {data.message && <div className="tv-status-text">{String(data.message)}</div>}
      </div>
    );
  } else if (data) {
    body = (
      <Dashboard
        data={data}
        error={error}
        quote={quote}
        modelTarget={modelTarget}
        showRetracted={showRetracted}
        onToggleRetracted={toggleRetracted}
        geoOpen={geoOpen}
        onToggleGeo={toggleGeo}
      />
    );
  }

  return (
    <div className="tv-app">
      <TopBar frozenAtMs={frozenAtMs} lastUpdate={lastUpdate} />
      {STATIC_DATA_MODE && frozenAtMs != null && <StaleSnapshotBanner frozenAtMs={frozenAtMs} />}
      <main className="tv-main">{body}</main>
      <footer className="tv-footer">
        Research project, not investment advice. Prices: Yahoo Finance (exchange-delayed) and EIA;
        implied volatility: CBOE OVX. All times US Central (CT).{" "}
        <a href={REPO_URL} target="_blank" rel="noopener noreferrer">Source and methodology on GitHub</a>.
      </footer>
    </div>
  );
}

function TopBar({ frozenAtMs, lastUpdate }) {
  return (
    <header className="tv-topbar">
      <div className="tv-brand">
        <span className="tv-brand-mark" aria-hidden="true">WTI</span>
        <div className="tv-brand-text">
          <h1 className="tv-brand-title">WTI Crude Oil Futures</h1>
          <span className="tv-brand-sub">Volatility forecaster · direction-leak post-mortem · supply-shock study</span>
        </div>
      </div>
      <div className="tv-topbar-right">
        <TopStatus frozenAtMs={frozenAtMs} lastUpdate={lastUpdate} />
        <a
          className="tv-topbar-link"
          href={REPO_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="GitHub repository (opens in a new tab)"
        >
          GitHub ↗
        </a>
      </div>
    </header>
  );
}

// The only component that ticks every second, so the clock never re-renders the dashboard.
function TopStatus({ frozenAtMs, lastUpdate }) {
  const now = useNow(1000);
  return (
    <>
      <span className="tv-topbar-time tv-num">{fmtCT(now, { seconds: true })}</span>
      <span className="tv-topbar-asof">
        {frozenAtMs != null
          ? `Data as of ${fmtCT(frozenAtMs)}`
          : lastUpdate != null
            ? `Updated ${Math.max(0, Math.round((now - lastUpdate) / 1000))}s ago`
            : "Loading data…"}
      </span>
    </>
  );
}

function StaleSnapshotBanner({ frozenAtMs }) {
  const now = useNow(60 * 1000);
  const ageMs = now - frozenAtMs;
  if (ageMs <= STALE_SNAPSHOT_MS) return null;
  const hours = Math.floor(ageMs / (60 * 60 * 1000));
  const age = hours >= 48 ? `${Math.floor(hours / 24)} days` : `${hours} hours`;
  return (
    <div className="tv-caveat warn" role="status">
      This snapshot is {age} old (frozen {fmtCT(frozenAtMs)}). Scheduled refreshes are running late, so
      prices, model and event-study figures may be out of date.
    </div>
  );
}

function QuoteStamp({ quote }) {
  const now = useNow(30 * 1000);
  if (quote.kind === "quote" && quote.marketTimeMs != null) {
    const isLive = now - quote.marketTimeMs <= LIVE_BADGE_MAX_AGE_MS;
    return (
      <div className="tv-quote-stamp" title={DELAY_NOTE}>
        <span className={isLive ? "tv-live-badge" : "tv-last-badge"}>{isLive ? "LIVE" : "LAST"}</span>
        <span>{fmtCT(quote.marketTimeMs)}</span>
        <span className="sr-only">{DELAY_NOTE}</span>
      </div>
    );
  }
  return (
    <div className="tv-quote-stamp" title={quote.kind === "quote" ? DELAY_NOTE : undefined}>
      <span className="tv-last-badge">{{ quote: "QUOTE", backend: "UPDATED" }[quote.kind] || "SNAPSHOT"}</span>
      <span>
        {quote.kind === "quote" ? `fetched ${fmtCT(quote.fetchedAtMs)}` : quote.timeMs != null ? fmtCT(quote.timeMs) : DASH}
      </span>
    </div>
  );
}

function Dashboard({ data, error, quote, modelTarget, showRetracted, onToggleRetracted, geoOpen, onToggleGeo }) {
  // Main interface - USE REAL API DATA ONLY
  // ml_caveat: backend flags HIGH/CRITICAL geo regimes where models trained on normal markets are
  // out of distribution. Its wording is the dashboard's own: the retracted point forecast is never
  // something to "weigh", so the banner points at the event study instead.
  const mlCaveat = Boolean(data.ml_caveat);
  const geoRegime = typeof data.geopolitical_risk?.regime === "string" ? data.geopolitical_risk.regime : null;

  const rawContract = data.contract && typeof data.contract === "object" ? data.contract : {};
  const contractText = (value) => (typeof value === "string" && value.trim() ? value : null);
  const contract = {
    symbol: contractText(rawContract.symbol),
    description: contractText(rawContract.description),
    quote_symbol: contractText(rawContract.quote_symbol),
    days_to_expiry: rawContract.days_to_expiry,
  };
  const activeMetrics = data.performance_metrics?.by_horizon?.["1w"] || {};
  // Walk-forward out-of-sample stats. NOTE: wf_is_significant is False (the purged backtest is a
  // coin flip), so the stance reads RETRACTED and no tear sheet or sizing is shown. The significant
  // branch is kept as the reusable sizing layer the README describes, and is correct if ever used.
  const modelSignificant = activeMetrics.wf_is_significant === true;

  return (
    <>
      {mlCaveat && (geoRegime === "UNKNOWN" ? (
        <div className="tv-caveat info" role="note">
          News-flow regime unavailable: the headline feed could not be read, so the tail-risk guardrail
          cannot assess current conditions. The supply-shock event study below still shows how similar
          shocks have resolved.
        </div>
      ) : (
        <div className="tv-caveat" role="note">
          ⚠ News-flow regime{geoRegime ? `: ${geoRegime}` : " elevated"}. Models trained on normal markets
          understate tail risk here.{modelSignificant
            ? " That includes the 1W model;"
            : " The 1W direction model is retracted, so do not use its output at all;"} the supply-shock
          event study below shows how similar shocks have resolved.
        </div>
      ))}

      {error && (
        <div className="tv-caveat info" role="status">{error}</div>
      )}

      <ErrorBoundary label="The market snapshot" resetKey={data}>
        <DeskSection
          data={data}
          contract={contract}
          quote={quote}
          modelTarget={modelTarget}
          showRetracted={showRetracted}
          metrics={activeMetrics}
        />
      </ErrorBoundary>

      {/* Performance tear sheet — out-of-sample walk-forward (only if a purged backtest re-validates) */}
      {modelSignificant && (
        <ErrorBoundary label="The backtest tear sheet" resetKey={data}>
          <BacktestTearSheet metrics={activeMetrics} liveRecordSummary={data.live_record} />
        </ErrorBoundary>
      )}

      {/* The validated result leads; the retracted direction model's post-mortem follows it. */}
      {data.vol_forecast?.live && data.vol_forecast?.validation && (
        <ErrorBoundary label="The volatility forecast" resetKey={data}>
          <VolatilityCard vol={data.vol_forecast} />
        </ErrorBoundary>
      )}

      <ErrorBoundary label="The signal-status card" resetKey={data}>
        <RetractionCard metrics={activeMetrics} liveRecordSummary={data.live_record} />
      </ErrorBoundary>

      {/* Main chart */}
      <section className="tv-chart-section" aria-labelledby="chart-heading">
        <h2 id="chart-heading" className="sr-only">Price chart</h2>
        <ErrorBoundary label="The price chart" resetKey={data}>
          <Suspense fallback={<div className="tv-chart-placeholder" role="status">Loading chart…</div>}>
          <Chart
            actualPayload={data.unified_data?.actual}
            fallbackValues={data.actual}
            predictedPayload={data.unified_data?.predicted}
            snapshotPrice={data.current_price}
            contract={contract}
            feedLabel={STATIC_DATA_MODE ? "Snapshot · delayed quotes" : String(data.feed_status || "")}
            quote={quote}
            forecast={modelTarget}
            forecastRetracted={!modelSignificant}
            showForecast={showRetracted}
            onToggleForecast={onToggleRetracted}
          />
          </Suspense>
        </ErrorBoundary>
      </section>

      <ErrorBoundary label="The supply-risk section" resetKey={data}>
        <SupplySection
          playbook={data.supply_shock_playbook}
          noveltySpike={Boolean(data.geopolitical_risk?.novelty_spike)}
          open={geoOpen}
          onToggle={onToggleGeo}
        />
      </ErrorBoundary>

      {/* Method note — addressed to a hedge-fund PM. Where the effort went + the noise-floor point:
          signals that the author knows what is and is not tradeable, which is risk thinking, not model hype. */}
      <section className="tv-tearsheet" aria-labelledby="method-heading">
        <div className="tv-tearsheet-head">
          <h2 id="method-heading" className="tv-desk-label">Method · where the effort actually went</h2>
          <span className="tv-tearsheet-live">for a PM reading this</span>
        </div>
        <p className="tv-tearsheet-body">
          If you run a book, read this as risk discipline, not a model pitch. The effort here was evaluation
          and data integrity first, model training last: a leak-free purged walk-forward, a look-ahead leak
          caught in my own signal, and CI tests that keep it caught, on top of point-in-time, entry-time-clean
          data. The model is deliberately simple (HAR-IV plus a small ensemble), and that is the point. No model
          can beat the noise floor set by the mutual information between features and target. Weekly price
          direction carries almost none, which is why the purged direction signal is a coin flip; realized
          volatility is persistent, which is why a simple forecaster extracts real skill. The work that mattered
          was lowering and honestly measuring that floor, not tuning the model — the same judgment that separates
          a tradeable signal from noise before capital is at risk.
        </p>
      </section>

      {/* Roadmap — forward plan if funded. This build is deliberately 100% free-resource; the card
          makes the next steps explicit so a reader sees the long-term direction, not just the ceiling. */}
      <section className="tv-tearsheet" aria-labelledby="roadmap-heading">
        <div className="tv-tearsheet-head">
          <h2 id="roadmap-heading" className="tv-desk-label">Roadmap · what a funded version would add</h2>
          <span className="tv-tearsheet-live">free-tier ceiling reached · plan, not built</span>
        </div>
        <ul className="tv-roadmap">
          <li className="tv-roadmap-item">
            <span className="tv-roadmap-tag">Data</span>
            <div className="tv-roadmap-body">
              <b>Tick-level realized variance</b>
              <span>Intraday realized variance from a paid futures tick feed (CME DataMine / Nasdaq Data Link) replaces close-to-close RV — the exact input the HAR model was designed for, and a materially sharper volatility estimate.</span>
            </div>
          </li>
          <li className="tv-roadmap-item">
            <span className="tv-roadmap-tag">Signal</span>
            <div className="tv-roadmap-body">
              <b>Full CL options surface</b>
              <span>Strike-by-strike implied vol, not just the single OVX index, yields a model-free variance risk premium (implied variance minus realized) — a signal real volatility desks actually trade.</span>
            </div>
          </li>
          <li className="tv-roadmap-item">
            <span className="tv-roadmap-tag">System</span>
            <div className="tv-roadmap-body">
              <b>Persistent live backend</b>
              <span>A small cloud server and database replace the 4-hour frozen snapshot: sub-minute prices, rolling model retrains, and a tamper-evident live track record instead of a static file.</span>
            </div>
          </li>
          <li className="tv-roadmap-item">
            <span className="tv-roadmap-tag">Breadth</span>
            <div className="tv-roadmap-body">
              <b>Energy-complex coverage</b>
              <span>Run the same volatility forecaster on Brent, heating oil and natural gas. Brent and heating oil move closely with WTI, so this is mainly an out-of-sample robustness test with modest diversification — not the √N breadth gain Grinold&apos;s law describes for independent bets — and there is no directional edge to scale. Natural gas is the one genuinely different market.</span>
            </div>
          </li>
        </ul>
        <p className="tv-tearsheet-foot">
          Today&apos;s system is intentionally 100% free-resource (Yahoo Finance, EIA, CBOE OVX; static GitHub Pages, refreshed on a best-effort 4-hour GitHub schedule). Each item above is a costed next step, listed so the engineering and research direction is explicit — not a present capability.
        </p>
      </section>
    </>
  );
}

// Desk header — price · the (retracted) direction model · sizing. Its own component so a bad field
// here is caught by the section's error boundary instead of taking down the page.
function DeskSection({ data, contract, quote, modelTarget, showRetracted, metrics }) {
  const daysToExpiry = num(contract.days_to_expiry);
  const volumeDisplay = typeof data.volume_display === "string" ? data.volume_display : null;
  const modelSignificant = metrics.wf_is_significant === true;
  const sizing = modelSignificant
    ? computeSizing(metrics.wf_pnl_win_rate, metrics.wf_pnl_profit_factor, metrics.wf_pnl_mean_per_trade)
    : null;
  const liveRecord = describeLiveRecord(data.live_record);
  const fcPct = num(modelTarget?.pct);

  const deskCall = (() => {
    if (!modelSignificant) {
      return {
        stance: "RETRACTED",
        tone: "retracted",
        text: "The backtested edge was a look-ahead leak; purged, the signal is a coin flip. No stance is taken and nothing is sized.",
        detail: modelTarget
          ? showRetracted
            ? `Retracted model output (not a forecast): ${fmtUsd(modelTarget.value)} in one week, ${fmtSignedPct(fcPct, 2)} vs the ${fmtUsd(modelTarget.ref)} snapshot.`
            : "Its raw output is hidden; the chart's “Show retracted model output” control reveals it for inspection."
          : null,
      };
    }
    const hasEdge = sizing && !sizing.noEdge;
    const lean = hasEdge && fcPct != null ? (fcPct > 0.6 ? "long" : fcPct < -0.6 ? "short" : null) : null;
    const record = liveRecord?.verdict ? ` Live record: ${liveRecord.verdict}.` : "";
    const forecastText = fcPct != null
      ? `1W forecast ${fmtSignedPct(fcPct, 1)} vs the ${fmtUsd(modelTarget.ref)} snapshot.`
      : "No 1W forecast in this snapshot.";
    if (lean) {
      return {
        stance: lean === "long" ? "LONG LEAN" : "SHORT LEAN",
        tone: lean === "long" ? "up" : "down",
        text: `${forecastText} Model leans ${lean} — size to half-Kelly (~${fmtNum(sizing.halfKellyPct, 1)}% of capital).${record}`,
      };
    }
    return {
      stance: "NEUTRAL",
      tone: "neutral",
      text: hasEdge
        ? `${forecastText} Inside the ±0.6% conviction gate, so no position.${record}`
        : `${forecastText} The backtest shows no positive edge after costs, so no position.${record}`,
    };
  })();

  return (
    <section className="tv-desk" aria-labelledby="desk-heading">
      <h2 id="desk-heading" className="sr-only">Market snapshot and model status</h2>
      <div>
        <div className="tv-market-symbol">
          <span className="tv-chip">{contract.symbol || DASH}</span>
          <span className="tv-market-name">WTI Crude · NYMEX</span>
        </div>
        <div className="tv-desk-pricewrap">
          <span className="tv-desk-px">{num(quote.price) > 0 ? fmtUsd(quote.price) : DASH}</span>
          <span className={`tv-desk-chg ${signClass(quote.changePct, "is-up", "is-down")}`}>
            {num(quote.price) > 0 ? fmtSignedPct(quote.changePct, 2) : DASH}
          </span>
        </div>
        <QuoteStamp quote={quote} />
        <div className="tv-market-meta">
          {daysToExpiry != null && <>{daysToExpiry}d to expiry</>}
          {daysToExpiry != null && volumeDisplay && ' · '}
          {volumeDisplay && <>Vol {volumeDisplay}</>}
        </div>
      </div>

      <div className="tv-desk-call">
        <h3 className="tv-desk-label">1W Direction Model</h3>
        <div className={`tv-desk-stance tone-${deskCall.tone}`}>{deskCall.stance}</div>
        <p className="tv-desk-text">{deskCall.text}</p>
        {deskCall.detail && <p className="tv-desk-text muted">{deskCall.detail}</p>}
      </div>

      <div>
        <h3 className="tv-desk-label">Position Sizing</h3>
        {!modelSignificant ? (
          <p className="tv-desk-text muted">Not sized: the direction model is retracted, so there is no edge to size.</p>
        ) : !sizing ? (
          <p className="tv-desk-text muted">Sizing unavailable: the backtest payload lacks a hit rate or profit factor.</p>
        ) : sizing.noEdge ? (
          <p className="tv-desk-text muted">
            No positive edge: Kelly is ≤ 0 at a {fmtPct(sizing.winRate)} hit rate and {fmtNum(sizing.pf, 2)}× profit
            factor, so the position size is zero.
          </p>
        ) : (
          <>
            <div className="tv-sizing-kelly">
              <div><b>{fmtNum(sizing.halfKellyPct, 1)}%</b><span>Half-Kelly</span></div>
              <div><b>{fmtNum(sizing.fullKellyPct, 1)}%</b><span>Full Kelly</span></div>
            </div>
            {sizing.acctPer1 != null && (
              <div className="tv-sizing-note">
                1 contract per ~{fmtUsd(sizing.acctPer1, 0)} account at 2% risk
                <span className="muted"> · {fmtUsd(sizing.avgLoss, 0)} avg loss/contract · backtest basis</span>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

// Signal-retraction notice — always shown. The original backtest (Sharpe 2.44 / 2.07) was a
// look-ahead leak; purged, the signal is a coin flip. This card replaces the headline metrics with
// the honest finding (current purged figures from the payload) plus the live record.
function RetractionCard({ metrics, liveRecordSummary }) {
  const liveRecord = describeLiveRecord(liveRecordSummary);
  const accuracy = num(metrics.backtest_direction_accuracy) ?? num(metrics.wf_pnl_win_rate);
  const pText = fmtP(metrics.wf_p_value);
  const samples = num(metrics.wf_samples);
  const sharpe = num(metrics.wf_pnl_sharpe);
  const stats = [pText, samples != null ? `n = ${samples}` : null].filter(Boolean).join(", ");
  const purged = accuracy != null
    ? `the walk-forward's direction accuracy is ${fmtPct(accuracy)}${stats ? ` (${stats})` : ""}`
      + (sharpe != null ? ` with an after-cost Sharpe of ${fmtSigned(sharpe, 2)}` : "")
    : "direction accuracy falls to 48–52% (p > 0.2)";
  const losesMoney = sharpe == null || sharpe < 0;

  return (
    <section className="tv-tearsheet is-retraction" aria-labelledby="retraction-heading">
      <div className="tv-tearsheet-head">
        <h2 id="retraction-heading" className="tv-desk-label">Signal status · retracted (look-ahead leak)</h2>
        <span className="tv-tearsheet-live">research post-mortem</span>
      </div>
      <p className="tv-tearsheet-body">
        The original 1-week backtest (Sharpe 2.44 over 5y, 2.07 over 10y) was a look-ahead leakage
        artifact: the walk-forward trained on rows whose 5-day targets matured after the prediction
        point. After a standard purge/embargo, {purged}{losesMoney ? ", and the strategy loses money after costs" : ""}.
        As built, the signal has no out-of-sample edge, so no stance or sizing is shown as actionable.
        The dashboard and deploy pipeline are kept as engineering; the trading claim is retracted. See
        the README headline finding for the full post-mortem.
      </p>
      {liveRecord && (
        <p className="tv-live-record">
          <span className="tv-live-record-label">Live record</span>
          {liveRecord.text}{liveRecord.verdict ? ` — ${liveRecord.verdict}` : ""}.
        </p>
      )}
    </section>
  );
}

function BacktestTearSheet({ metrics, liveRecordSummary }) {
  const liveRecord = describeLiveRecord(liveRecordSummary);
  const winRate = num(metrics.wf_pnl_win_rate);
  const sharpe = num(metrics.wf_pnl_sharpe);
  const profitFactor = num(metrics.wf_pnl_profit_factor);
  const meanPnl = num(metrics.wf_pnl_mean_per_trade);
  const maxDrawdown = num(metrics.wf_pnl_max_drawdown);
  const samples = num(metrics.wf_samples);
  const ci = Array.isArray(metrics.wf_ci_95) ? metrics.wf_ci_95.map(num) : [];
  const trades = Array.isArray(metrics.wf_pnl_trades) ? metrics.wf_pnl_trades : [];
  const equityCurve = buildEquityCurve(trades);
  const yearly = metrics.wf_yearly_breakdown && typeof metrics.wf_yearly_breakdown === "object"
    ? Object.entries(metrics.wf_yearly_breakdown).filter(([, d]) => d && typeof d === "object")
    : [];
  const span = equityCurve?.first && equityCurve?.last ? ` · ${equityCurve.first} → ${equityCurve.last}` : "";
  const foot = [
    ci[0] != null && ci[1] != null ? `95% CI of hit rate [${fmtPct(ci[0])}, ${fmtPct(ci[1])}]` : null,
    fmtP(metrics.wf_p_value),
    samples != null ? `${samples} out-of-sample trades` : null,
    "purged walk-forward · after costs",
  ].filter(Boolean).join(" · ");

  return (
    <section className="tv-tearsheet" aria-labelledby="tearsheet-heading">
      <div className="tv-tearsheet-head">
        <h2 id="tearsheet-heading" className="tv-desk-label">Walk-Forward Backtest · Out-of-Sample{span}</h2>
        <span className="tv-tearsheet-live">
          {liveRecord?.verdict ? `Live record: ${liveRecord.verdict}` : "No live record yet"}
        </span>
      </div>
      <div className="tv-tearsheet-grid">
        <div><b>{fmtPct(winRate)}</b><span>Hit Rate</span></div>
        <div><b>{fmtNum(sharpe, 2)}</b><span>Sharpe</span></div>
        <div><b>{profitFactor != null ? `${fmtNum(profitFactor, 2)}×` : DASH}</b><span>Profit Factor</span></div>
        <div><b className={signClass(meanPnl)}>{fmtSignedUsd(meanPnl)}</b><span>Expectancy / trade</span></div>
        <div><b className={maxDrawdown ? "down" : ""}>{maxDrawdown != null ? fmtSignedUsd(-Math.abs(maxDrawdown)) : DASH}</b><span>Max Drawdown</span></div>
        <div><b>{samples ?? DASH}</b><span>OOS Trades</span></div>
      </div>
      {equityCurve && (
        <div className="tv-equity">
          <div className="tv-equity-head">
            <span>OOS equity curve · {trades.length} trades{span}</span>
            <span className={signClass(equityCurve.total)}>{fmtSignedUsd(equityCurve.total)} net</span>
          </div>
          <svg
            viewBox={`0 0 ${equityCurve.W} ${equityCurve.H}`}
            preserveAspectRatio="none"
            className="tv-equity-svg"
            role="img"
            aria-label={`Cumulative out-of-sample P&L, ${fmtSignedUsd(equityCurve.total)} net over ${trades.length} trades`}
          >
            <path d={equityCurve.area} fill="rgba(92,176,214,0.10)" />
            <line x1="0" y1={equityCurve.zeroY} x2={equityCurve.W} y2={equityCurve.zeroY}
                  stroke="#30363d" strokeWidth="1" strokeDasharray="3,4" />
            <path d={equityCurve.line} fill="none" stroke="#5cb0d6" strokeWidth="1.6" />
          </svg>
        </div>
      )}
      {yearly.length > 0 && (
        <div className="tv-yearly">
          {yearly.map(([year, d]) => (
            <div key={year}>
              <span className="tv-yearly-year">{year}</span>
              <b className={signClass(d?.sharpe)}>{fmtNum(d?.sharpe, 2)}</b>
              <span className="tv-yearly-sub">{num(d?.n_trades) ?? DASH} trades · {fmtPct(d?.win_rate_pct, 0)} win</span>
            </div>
          ))}
        </div>
      )}
      <p className="tv-tearsheet-foot">{foot}</p>
    </section>
  );
}

// Volatility forecast — the project's validated signal (backend/vol_forecast.py).
function VolatilityCard({ vol }) {
  const live = vol.live || {};
  // Significance + robustness come from the live validation payload, never hardcoded, so they
  // cannot drift out of sync with the accuracy rendered beside them.
  const volV = vol.validation || {};
  // Quote the SMALLER of the i.i.d. and autocorrelation-robust (Newey-West) z-scores, so the
  // headline never leans on an independence assumption the overlapping weekly labels violate.
  const volZ = [num(volV.har_dir_z_score), num(volV.har_dir_z_score_hac)]
    .filter((z) => z != null)
    .reduce((lo, z) => (lo == null || z < lo ? z : lo), null);
  const volP = num(volV.har_dir_p_value_vs_base_rate);
  const volPBound = (volP != null && volP > 0)
    ? `1e-${Math.max(0, Math.ceil(-Math.log10(volP)) - 1)}`
    : (volP === 0 ? '1e-16' : null);  // older payloads: 1 - cdf underflowed to 0 below ~1e-16
  const volSpan = (volV.sample_start && volV.sample_end)
    ? `${String(volV.sample_start).slice(0, 4)}–${String(volV.sample_end).slice(0, 4)} `
    : '';
  const yearsTotal = num(volV.years_total);
  const yearMin = num(volV.yearly_acc_min_pct);
  const yearMax = num(volV.yearly_acc_max_pct);
  const yearsAbove = num(volV.years_above_base_rate);
  const yearsBeatMr = num(volV.years_beating_mean_reversion);
  const volYearly = (yearMin != null && yearMax != null && yearsTotal)
    ? `; ${yearsAbove != null ? `above the base rate in ${yearsAbove}/${yearsTotal} years, ` : ''}${yearMin}–${yearMax}% by year${yearsBeatMr != null ? `, beats mean-reversion in ${yearsBeatMr}/${yearsTotal}` : ''}`
    : '';
  const volOverlay = vol.economic?.vol_target_overlay || null;
  const vrp = vol.economic?.variance_risk_premium || null;

  const accuracy = num(volV.har_dir_acc_pct);
  const baseRate = num(volV.majority_class_pct);
  const ex2020 = num(volV.ex_2020_har_dir_acc_pct);
  const n = num(volV.n);
  // "Validated" only when the payload says so: p < 0.05 AND the conservative z clears 1.645.
  const significance = volP == null
    ? "unknown"
    : volP < 0.05 && (volZ == null || volZ >= 1.645) ? "significant" : "not-significant";
  const isSignificant = significance === "significant";
  const pText = volP == null ? null : volP < 0.001 && volPBound ? `p < ${volPBound}` : fmtP(volP);
  const testClause = isSignificant
    ? `${volZ != null ? `z ≥ ${volZ}, ` : ''}${pText}`
    : [significance === 'unknown' ? 'significance unavailable' : 'not significant',
      volZ != null ? `conservative z = ${volZ}` : null, pText].filter(Boolean).join(', ');

  const direction = typeof live.direction === "string" ? live.direction.toUpperCase() : null;
  const directionClass = isSignificant && direction === "RISING" ? "up"
    : isSignificant && direction === "FALLING" ? "down" : "";
  const implied = num(live.implied_vol_ovx_pct);
  const liveModel = typeof live.model === "string" ? live.model : null;
  const isNoOvxFallback = liveModel ? !/OVX/i.test(liveModel) : implied == null;

  const mrAccuracy = num(volV.mean_reversion_dir_acc_pct);
  const mrZ = num(volV.har_vs_mean_reversion_z_hac);
  const mrP = num(volV.har_vs_mean_reversion_p_value);
  const beatsMr = accuracy != null && mrAccuracy != null && accuracy > mrAccuracy && (mrP == null || mrP < 0.05);
  const noOvxAccuracy = num(volV.har_no_ovx_dir_acc_pct);
  const noOvxR2 = num(volV.har_no_ovx_level_r2);
  const levelR2 = num(volV.har_level_r2);
  const persistenceR2 = num(volV.persistence_level_r2);
  const qlike = num(volV.har_qlike);
  const persistenceQlike = num(volV.persistence_qlike);

  const overlayForecast = num(volOverlay?.sharpe_forecast_vol_target);
  const overlayBuyHold = num(volOverlay?.sharpe_buy_hold);
  const overlayTrailing = num(volOverlay?.sharpe_trailing_vol_target);
  const trailingClause = overlayTrailing != null ? `; trailing-vol sizing ${fmtNum(overlayTrailing, 2)}` : '';
  const overlaySentence = overlayForecast != null && overlayBuyHold != null
    ? overlayForecast <= overlayBuyHold
      ? ` A vol-targeting overlay did NOT beat buy-and-hold (Sharpe ${fmtNum(overlayForecast, 2)} vs ${fmtNum(overlayBuyHold, 2)}${trailingClause}), so no P&L is claimed.`
      : ` A vol-targeting overlay scored Sharpe ${fmtNum(overlayForecast, 2)} vs ${fmtNum(overlayBuyHold, 2)} for buy-and-hold${trailingClause}; the difference is untested, so no P&L is claimed.`
    : ' No trading P&L is claimed.';

  const vrpMean = num(vrp?.mean_premium_vol_pts);
  const vrpShare = num(vrp?.share_positive_pct);
  const vrpWorst = num(vrp?.worst_period_vol_pts);
  const vrpTimingP = num(vrp?.timing_p_value);
  const vrpSwapSharpe = num(vrp?.short_vol_swap_sharpe);
  const vrpVarSharpe = num(vrp?.short_variance_sharpe);
  const vrpSharpes = [
    vrpSwapSharpe != null ? `short vol-swap Sharpe ${fmtNum(vrpSwapSharpe, 2)}` : null,
    vrpVarSharpe != null ? `short variance ${fmtNum(vrpVarSharpe, 2)}` : null,
  ].filter(Boolean).join(', ');

  return (
    <section className="tv-tearsheet" aria-labelledby="vol-heading">
      <div className="tv-tearsheet-head">
        <h2 id="vol-heading" className="tv-desk-label">
          Volatility Forecast · {isSignificant ? 'validated signal' : significance === 'unknown' ? 'significance unavailable' : 'not significant'}
        </h2>
        <span className="tv-tearsheet-live">
          {typeof volV.model === 'string' ? volV.model : 'HAR-IV: realized vol + OVX'} · next-week, leak-free
          {typeof live.as_of === 'string' ? ` · as of ${fmtDate(live.as_of)}` : ''}
          {isNoOvxFallback && ` · OVX unavailable: ${liveModel || 'pure-HAR'} fallback`}
        </span>
      </div>
      <div className="tv-tearsheet-grid">
        <div>
          <b className={directionClass}>{direction || DASH}</b>
          <span>Next-week vol</span>
        </div>
        <div><b>{fmtPct(live.current_realized_vol_5d_annualized_pct)}</b><span>Current RV (5d)</span></div>
        <div><b>{fmtPct(live.forecast_next_week_vol_annualized_pct)}</b><span>Forecast (ann.)</span></div>
        {implied != null
          ? <div><b>{fmtPct(implied)}</b><span>Implied (OVX)</span></div>
          : <div><b>{fmtPct(baseRate)}</b><span>Base Rate</span></div>}
        <div><b className={isSignificant ? 'up' : ''}>{fmtPct(accuracy)}</b><span>OOS Dir. Acc.</span></div>
        <div><b>{n ?? DASH}</b><span>OOS Weeks</span></div>
      </div>
      <p className="tv-tearsheet-body">
        Next-week realized-volatility direction, {volSpan}purged walk-forward: {fmtPct(accuracy)} accuracy
        vs a {fmtPct(baseRate)} base rate ({testClause}{ex2020 != null ? `; ex-2020 ${fmtPct(ex2020)}` : ''}{volYearly}).
        {mrAccuracy != null && (
          <>
            {' '}{beatsMr ? 'Beats' : 'Does not clearly beat'} a mean-reversion baseline ({fmtPct(mrAccuracy)}{mrZ != null ? `, paired z ≈ ${mrZ}` : ''})
            {noOvxAccuracy != null
              ? `${accuracy != null && accuracy > noOvxAccuracy ? ', and outscores' : '; compare'} the same model without OVX (${fmtPct(noOvxAccuracy)}${noOvxR2 != null ? `, R² ${noOvxR2}` : ''})`
              : ''}.
          </>
        )}
        {isSignificant
          ? ' A clean implementation of a known effect (vol clustering/mean-reversion), not novel alpha — a validated volatility/regime indicator, not a directional trade.'
          : ' On this sample it is not statistically distinguishable from the base rate, so it is not presented as a validated signal.'}
        {overlaySentence}
        {levelR2 != null && (
          <>
            {' '}Level R² {levelR2}{persistenceR2 != null ? ` (persistence ${persistenceR2})` : ''}
            {qlike != null && persistenceQlike != null ? `; QLIKE ${qlike} vs ${persistenceQlike} (lower is better)` : ''}.
          </>
        )}
      </p>
      {vrpMean != null && (
        <p className="tv-tearsheet-foot">
          Variance risk premium: OVX exceeded subsequent realized vol by {fmtNum(vrpMean, 1)} vol pts on average
          {vrpShare != null ? ` (positive in ${fmtPct(vrpShare, 0)} of periods)` : ''}
          {vrpWorst != null ? `, but the worst period was ${fmtSigned(vrpWorst, 1)} vol pts` : ''}
          {vrpSharpes ? `; ${vrpSharpes}` : ''}
          {vrpTimingP != null
            ? vrpTimingP >= 0.05
              ? `. The forecast does not time it (${fmtP(vrpTimingP)}).`
              : `. Forecast-based timing: ${fmtP(vrpTimingP)}.`
            : '.'}
        </p>
      )}
    </section>
  );
}

// Event-study rows: every driver with a computed move, sorted by peak magnitude.
const SUPPLY_LABELS = {
  supply_lost: 'Physical supply loss >0.5 mbpd',
  opec_cut:    'OPEC production cut',
  conflict:    'Armed conflict',
  sanctions:   'Sanctions',
  iran_driven: 'Iran-driven',
  strait_risk: 'Hormuz / transit-strait risk',
  weather:     'Weather / hurricane',
  threat_only: 'Threat only — no physical loss',
};
const SMALL_SAMPLE_N = 10;
const MEANINGFUL_GAP_PCT = 1.0; // difference in medians below this is reported as "no difference"

// EIA-sourced supply-shock event study. Every sentence that draws a conclusion is gated on the
// payload's numbers; when they do not support the conclusion the text only reports them.
function SupplySection({ playbook, noveltySpike, open, onToggle }) {
  const dist = playbook?.distributions && typeof playbook.distributions === "object" ? playbook.distributions : {};
  if (!dist.supply_lost && !dist.threat_only) return null;

  const eventCount = num(playbook.event_count);
  const firstYear = num(playbook.first_event_year);
  const lastYear = num(playbook.last_event_year);
  const span = firstYear != null && lastYear != null
    ? (firstYear === lastYear ? `${firstYear}` : `${firstYear}–${lastYear}`)
    : null;
  const eventsLabel = `${eventCount != null ? `${eventCount} events` : 'events'}${span ? ` ${span}` : ''}`;

  const rows = Object.entries(dist)
    .filter(([k, v]) => Object.hasOwn(SUPPLY_LABELS, k) && num(v?.peak?.median) != null)
    .map(([k, v]) => ({
      key: k,
      label: SUPPLY_LABELS[k],
      n: num(v.n),
      peak: num(v.peak.median),
      settle: num(v.settle?.median),
    }))
    .sort((a, b) => b.peak - a.peak);

  const lost = dist.supply_lost || {};
  const threat = dist.threat_only || {};
  const lostPeak = num(lost.peak?.median);
  const lostSettle = num(lost.settle?.median);
  const lostN = num(lost.n);
  const threatPeak = num(threat.peak?.median);
  const threatSettle = num(threat.settle?.median);
  const threatN = num(threat.n);
  const lostHolds = lostSettle != null && lostSettle > 0;
  const threatFaded = threatSettle != null && threatPeak != null && threatPeak > 0 && threatSettle < threatPeak / 2;
  const smallSettleSample = [lostN, threatN].some((value) => value != null && value < SMALL_SAMPLE_N);
  const nText = (value) => (value != null ? `, n = ${value}` : '');
  const nParen = (value) => (value != null ? ` (n = ${value})` : '');

  const priced = playbook.priced_in_stats || {};
  const strongN = num(priced.strong_day0_n);
  const weakN = num(priced.weak_day0_n);
  const strongRise = num(priced.strong_day0_median_further_rise_pct);
  const weakRise = num(priced.weak_day0_median_further_rise_pct);
  const threshold = num(priced.threshold_pct) ?? 3;
  const hasPriced = strongN > 0 && weakN > 0 && strongRise != null && weakRise != null;
  const riseGap = hasPriced ? strongRise - weakRise : null;
  const pricedVerdict = !hasPriced ? null
    : riseGap >= MEANINGFUL_GAP_PCT ? 'strong openings were followed by more upside, not a fade'
    : riseGap <= -MEANINGFUL_GAP_PCT ? 'no evidence a strong open signals more upside'
    : 'no meaningful difference either way';
  const smallPricedSample = hasPriced && Math.min(strongN, weakN) < SMALL_SAMPLE_N;

  return (
    <section className="tv-section" aria-labelledby="supply-heading">
      <h2 id="supply-heading" className="tv-geo-heading">
        <button
          type="button"
          className="tv-geo-bar"
          aria-expanded={open}
          aria-controls="supply-panel"
          onClick={onToggle}
        >
          <span className="tv-geo-bar-title">Supply Risk Context</span>
          <span className="tv-geo-bar-summary">
            <span className="muted">EIA · {eventsLabel}</span>
            {lostPeak != null && (
              <><span className="dot" aria-hidden="true">·</span>
              <span>Physical loss <strong className={signClass(lostPeak)}>{fmtSignedPct(lostPeak)} peak</strong></span></>
            )}
            {threatPeak != null && (
              <><span className="dot" aria-hidden="true">·</span>
              <span>Threat-only <strong>{fmtSignedPct(threatPeak)} peak</strong></span></>
            )}
            {noveltySpike && <span className="tv-flash">⚡ breaking</span>}
          </span>
          <span className="tv-geo-bar-toggle" aria-hidden="true">{open ? 'Hide —' : 'Show +'}</span>
        </button>
      </h2>
      <div id="supply-panel" className="tv-supply-section" hidden={!open}>
        {lostSettle != null && threatSettle != null && (
          <p className="tv-supply-takeaway">
            {lostHolds && threatFaded && lostSettle > threatSettle ? (
              <>
                Physical supply losses have held their gains
                {' '}(<strong className="up">{fmtSignedPct(lostSettle)}</strong> median settle{nText(lostN)});
                {' '}threats with no barrels lost gave most of theirs back
                {' '}({fmtSignedPct(threatPeak)} median peak, <strong className={signClass(threatSettle, 'up2', 'down')}>{fmtSignedPct(threatSettle)}</strong> settle{nText(threatN)})
                {' '}— historically the market paid for real disruption, not headlines.
              </>
            ) : (
              <>
                Median settle: <strong className={signClass(lostSettle, 'up2', 'down')}>{fmtSignedPct(lostSettle)}</strong> after
                physical supply losses{nParen(lostN)} vs <strong className={signClass(threatSettle, 'up2', 'down')}>{fmtSignedPct(threatSettle)}</strong> after
                threats with no barrels lost{nParen(threatN)}.
              </>
            )}
            {smallSettleSample && ' Small samples.'}
          </p>
        )}

        <table className="tv-supply-table">
          <caption className="tv-card-label">
            Historical WTI price response by supply event type
            <span className="muted">median move across {eventsLabel} · computed from EIA daily spot</span>
          </caption>
          <thead>
            <tr>
              <th scope="col">Event type</th>
              <th scope="col">Events</th>
              <th scope="col">Peak</th>
              <th scope="col">Settled</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <th scope="row" className="tv-supply-cat">{r.label}</th>
                <td className="muted">{r.n ?? DASH}</td>
                <td className={signClass(r.peak)}>{fmtSignedPct(r.peak)}</td>
                <td className={r.settle == null ? 'muted' : signClass(r.settle, 'up2', 'down')}>{fmtSignedPct(r.settle)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {hasPriced && (
          <p className="tv-pricedin-note">
            Is a big first day already priced in? After a ≥{fmtSigned(threshold, Number.isInteger(threshold) ? 0 : 1)}% day 0,
            prices rose a median <strong>{fmtSignedPct(strongRise)}</strong> further (n = {strongN}) vs
            {' '}<strong>{fmtSignedPct(weakRise)}</strong> after a weaker start (n = {weakN}) — {pricedVerdict}
            {smallPricedSample ? '; n is small' : ''}.
            {typeof priced.definition === 'string' && <span className="muted"> Definition: {priced.definition}.</span>}
          </p>
        )}
      </div>
    </section>
  );
}

export default App;
