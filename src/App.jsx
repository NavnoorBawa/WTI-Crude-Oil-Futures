import React, { useState, useEffect, useRef } from "react";
import Chart from "./Chart";

const clampMilliseconds = (rawValue, fallback, minimum, maximum) => {
  const parsed = Number(rawValue);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
};

const pollIntervalMs = clampMilliseconds(
  import.meta.env.VITE_POLL_INTERVAL_MS,
  15000,
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
const LIVE_PRICE_FRESH_MS = 25 * 60 * 1000;
const LIVE_PRICE_MAX_AGE_MS = 3 * 60 * 60 * 1000;
const MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;

// RETRACTED 2026-06-20: the 1W "edge" (Sharpe 2.44/2.07, 65.8% acc) was a look-ahead leak —
// the walk-forward trained on rows whose 5-day targets matured after the prediction point. After a
// purge/embargo the signal is a coin flip (48-52% acc, p>0.2, negative Sharpe). No tradeable edge.
// Dashboard shows the retraction notice; stance renders NEUTRAL (backtest is no longer significant).


function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [loadingMessage, setLoadingMessage] = useState("Connecting to Real-Time Data Feeds");
  const [lastUpdate, setLastUpdate] = useState(new Date());
  const [currentTime, setCurrentTime] = useState(new Date());
  const [geoOpen, setGeoOpen] = useState(false);
  const [livePrice, setLivePrice] = useState(null);
  const [livePricePct, setLivePricePct] = useState(null);
  const [livePriceChange, setLivePriceChange] = useState(null);
  const [livePriceFresh, setLivePriceFresh] = useState(false);
  const configuredApiBase = import.meta.env.VITE_API_BASE_URL;
  // Static-snapshot mode (GitHub Pages): the React app reads a frozen data.json produced by
  // freeze.py in CI instead of polling a live backend. BASE_URL handles the Pages sub-path.
  const staticDataMode = import.meta.env.VITE_STATIC_DATA === "true";
  const staticDataUrl = `${import.meta.env.BASE_URL}data.json`;
  const latestDataRef = useRef(null);
  const liveQuoteMetaRef = useRef(null);

  latestDataRef.current = data;

  // In static mode the "endpoint" is just the frozen JSON file shipped alongside the site.
  const buildRequestUrl = (apiBase) =>
    apiBase.endsWith(".json") ? apiBase : `${apiBase}/data`;

  const getApiBaseCandidates = () => {
    if (staticDataMode) {
      return [staticDataUrl];
    }

    if (configuredApiBase) {
      return [configuredApiBase];
    }

    // Local dev default; production builds use VITE_STATIC_DATA or VITE_API_BASE_URL.
    return ["http://127.0.0.1:9000"];
  };

  useEffect(() => {
    // Set title
    document.title = "WTI Crude Oil Futures · Quant Forecast & Geo Risk";
    let isDisposed = false;
    let requestInFlight = false;
    let retryPending = false;
    let initialAttemptCount = 0;
    let retryTimeoutId = null;
    const activeControllers = new Set();

    // Update time every second
    const timeInterval = setInterval(() => {
      if (!isDisposed) setCurrentTime(new Date());
    }, 1000);

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
          setLoadingMessage("Connecting to Real-Time Data Feeds");
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

            if (responseParseError || responsePayload == null) {
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
        setLastUpdate(new Date());
        setError(null);
        setLoading(false);
        setLoadingMessage("Connecting to Real-Time Data Feeds");
        
      } catch (err) {
        if (isDisposed) return;

        const isStarting = err?.code === 'SYSTEM_INITIALIZING';
        const userMessage = isStarting
          ? err.message || 'Backend is warming up the model.'
          : err?.code === 'REQUEST_TIMEOUT' || err?.name === 'AbortError'
            ? 'Server timeout - Please wait and refresh'
            : err?.name === 'TypeError' && err.message.includes('Failed to fetch')
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
            ? `LIVE DATA DELAY: ${userMessage}. Showing last good market snapshot.`
            : userMessage
        );
        setLoading(false);
      } finally {
        requestInFlight = false;
      }
    }

    // Initial fetch
    fetchData(true);

    // Backend prediction cadence is minutes, so lower polling frequency cuts load.
    const interval = setInterval(() => {
      if (latestDataRef.current) fetchData(false);
    }, pollIntervalMs);

    return () => {
      isDisposed = true;
      clearRetryTimeout();
      clearInterval(interval);
      clearInterval(timeInterval);
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
    };
    // getApiBaseCandidates is a render-local helper whose only reactive input, configuredApiBase,
    // is already a dependency; adding the function itself would re-run this fetch loop every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configuredApiBase, staticDataMode, staticDataUrl]);

  // Client-side live price — reads a tiny snapshot from VITE_LIVE_PRICE_URL. Production
  // points this at the repo's dedicated live-data branch, so a price tick does not trigger
  // a full GitHub Pages deployment. freeze.py also bakes a same-origin price.json into every
  // full deploy; that is the fallback when the live-data branch/CDN is unavailable.
  // The "LIVE" badge shows only for a genuine live tick (price.yml's Yahoo source) fresh
  // enough to account for GitHub's short raw-content cache (<25 min);
  // the freeze baseline snapshot shows its price but is never badged. An older-but-valid
  // quote still updates the price (no badge), and a truly stale one (>3h) is ignored so
  // the frozen data.json price takes over. The header's "Data as of" carries the honesty.
  useEffect(() => {
    if (!staticDataMode) return; // local dev: the backend price is already live
    let isDisposed = false;
    let requestInFlight = false;
    let expiryTimeoutId = null;
    let freshnessTimeoutId = null;
    const activeControllers = new Set();

    const clearQuoteTimers = () => {
      if (expiryTimeoutId) clearTimeout(expiryTimeoutId);
      if (freshnessTimeoutId) clearTimeout(freshnessTimeoutId);
      expiryTimeoutId = null;
      freshnessTimeoutId = null;
    };

    const clearLiveOverlay = () => {
      liveQuoteMetaRef.current = null;
      clearQuoteTimers();
      if (isDisposed) return;
      setLivePrice(null);
      setLivePricePct(null);
      setLivePriceChange(null);
      setLivePriceFresh(false);
    };

    const scheduleQuoteTimers = () => {
      clearQuoteTimers();
      const meta = liveQuoteMetaRef.current;
      if (!meta || isDisposed) return;

      const now = Date.now();
      const remainingLifetime = meta.expiresAtMs - now;
      if (remainingLifetime <= 0) {
        clearLiveOverlay();
        return;
      }

      expiryTimeoutId = setTimeout(clearLiveOverlay, remainingLifetime);
      const remainingFreshness = (meta.freshUntilMs || 0) - now;
      setLivePriceFresh(remainingFreshness > 0);
      if (remainingFreshness > 0) {
        freshnessTimeoutId = setTimeout(() => {
          freshnessTimeoutId = null;
          if (!isDisposed) setLivePriceFresh(false);
        }, remainingFreshness);
      }
    };

    const asFiniteNumber = (value) => {
      if (
        value == null ||
        (typeof value !== 'number' && typeof value !== 'string') ||
        (typeof value === 'string' && value.trim() === '')
      ) {
        return null;
      }
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    };

    const normalizeQuote = (rawQuote) => {
      const now = Date.now();
      const price = asFiniteNumber(rawQuote?.price);
      const fetchedAtMs = new Date(rawQuote?.fetched_at).getTime();
      const ageMs = now - fetchedAtMs;
      if (
        price == null ||
        price <= 0 ||
        !Number.isFinite(fetchedAtMs) ||
        ageMs < -MAX_CLOCK_SKEW_MS ||
        ageMs > LIVE_PRICE_MAX_AGE_MS
      ) {
        return null;
      }

      const effectiveFetchedAtMs = Math.min(fetchedAtMs, now);
      const changePct = asFiniteNumber(rawQuote?.change_pct);
      const previousClose = asFiniteNumber(rawQuote?.prev_close);
      const isLiveTick =
        typeof rawQuote?.source === "string" &&
        rawQuote.source.toLowerCase().startsWith("yahoo");
      const rawChange = previousClose != null && previousClose > 0
        ? price - previousClose
        : null;

      return {
        price,
        changePct,
        change: Number.isFinite(rawChange)
          ? Number(rawChange.toFixed(2))
          : null,
        fetchedAtMs: effectiveFetchedAtMs,
        expiresAtMs: effectiveFetchedAtMs + LIVE_PRICE_MAX_AGE_MS,
        freshUntilMs: isLiveTick
          ? effectiveFetchedAtMs + LIVE_PRICE_FRESH_MS
          : null,
      };
    };

    const fetchLivePrice = async () => {
      if (isDisposed || requestInFlight) return;
      requestInFlight = true;

      try {
        const fallbackUrl = `${import.meta.env.BASE_URL}price.json`;
        const priceUrls = [import.meta.env.VITE_LIVE_PRICE_URL, fallbackUrl]
          .filter((url, index, urls) => url && urls.indexOf(url) === index);
        let quote = null;

        for (const sourceUrl of priceUrls) {
          if (isDisposed) return;
          const controller = new AbortController();
          activeControllers.add(controller);
          const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

          try {
            // The query string avoids reusing an older browser cache entry. GitHub's raw
            // CDN can still cache for a few minutes, which is included in the freshness gate.
            const requestUrl = new URL(sourceUrl, window.location.href);
            requestUrl.searchParams.set("_", String(Date.now()));
            const response = await fetch(requestUrl, {
              cache: 'no-store',
              signal: controller.signal,
              headers: { 'Accept': 'application/json' },
            });
            if (!response.ok) continue;
            const candidate = normalizeQuote(await response.json());
            if (!candidate) continue;
            quote = candidate;
            break;
          } catch {
            // Try the baked same-origin snapshot next.
          } finally {
            clearTimeout(timeoutId);
            activeControllers.delete(controller);
          }
        }

        if (isDisposed) return;
        if (!quote) {
          if (
            liveQuoteMetaRef.current &&
            liveQuoteMetaRef.current.expiresAtMs <= Date.now()
          ) {
            clearLiveOverlay();
          }
          return;
        }

        // Never replace a still-valid tick with an older fallback snapshot.
        const previousMeta = liveQuoteMetaRef.current;
        if (
          previousMeta &&
          previousMeta.expiresAtMs > Date.now() &&
          previousMeta.fetchedAtMs > quote.fetchedAtMs
        ) {
          scheduleQuoteTimers();
          return;
        }

        liveQuoteMetaRef.current = quote;
        setLivePrice(quote.price);
        setLivePricePct(quote.changePct);
        setLivePriceChange(quote.change);
        scheduleQuoteTimers();
      } finally {
        requestInFlight = false;
      }
    };

    // Restore expiry/freshness timers if React StrictMode remounts this effect.
    scheduleQuoteTimers();
    fetchLivePrice();
    const id = setInterval(fetchLivePrice, LIVE_PRICE_POLL_MS);
    return () => {
      isDisposed = true;
      clearInterval(id);
      clearQuoteTimers();
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
    };
  }, [staticDataMode]);

  // Loading screen
  if (loading && !data) {
    return (
      <div className="tv-app tv-center">
        <div style={{ textAlign: 'center' }}>
          <div className="tv-spinner" />
          <div style={{ color: '#f0f3fa', fontSize: 15, fontWeight: 700, marginTop: 18 }}>WTI Crude Oil Futures</div>
          <div style={{ color: '#6e7681', fontSize: 12, marginTop: 6 }}>{loadingMessage}</div>
        </div>
      </div>
    );
  }

  // Error screen - System designed to fail rather than show placeholder data
  if (error && !data) {
    return (
      <div className="tv-app tv-center">
        <div style={{ textAlign: 'center', maxWidth: 460, padding: 24 }}>
          <div style={{ color: '#f85149', fontSize: 16, fontWeight: 700, marginBottom: 8 }}>Data connection unavailable</div>
          <div style={{ color: '#8b949e', fontSize: 12.5, lineHeight: 1.5 }}>{error}</div>
          <div style={{ color: '#565d68', fontSize: 11, marginTop: 12 }}>Real data only — no placeholder values are shown.</div>
        </div>
      </div>
    );
  }
  
  // Check if data indicates system error
  if (data?.error) {
    return (
      <div className="tv-app tv-center">
        <div style={{ textAlign: 'center', maxWidth: 460, padding: 24 }}>
          <div style={{ color: '#f85149', fontSize: 16, fontWeight: 700, marginBottom: 8 }}>System error</div>
          <div style={{ color: '#8b949e', fontSize: 12.5, lineHeight: 1.5 }}>{data.error}</div>
          {data.message && <div style={{ color: '#565d68', fontSize: 11, marginTop: 8 }}>{data.message}</div>}
        </div>
      </div>
    );
  }

  // Main interface - USE REAL API DATA ONLY
  // ml_caveat: backend flags HIGH/CRITICAL geo regimes where the ensemble is out of
  // its training distribution; everything else geo comes from the EIA event study.
  const mlCaveat = data?.ml_caveat || null;

  // EIA-sourced supply-shock playbook
  const playbook = data?.supply_shock_playbook || {};
  const playbookDist = playbook.distributions || {};
  const playbookEventCount = Number(playbook.event_count || 0);
  const playbookPricedIn = playbook.priced_in_stats || {};
  const geoNoveltySpike = Boolean(data?.geopolitical_risk?.novelty_spike);

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
  const supplyRows = Object.entries(playbookDist)
    .filter(([k, v]) => SUPPLY_LABELS[k] && v?.peak?.median != null)
    .map(([k, v]) => ({ key: k, label: SUPPLY_LABELS[k], n: v.n, peak: v.peak.median, settle: v.settle?.median ?? null }))
    .sort((a, b) => b.peak - a.peak);

  const currentPrice = data?.current_price || 0;
  const priceChange = data?.price_change || 0;
  const priceChangePercent = data?.price_change_percent || 0;
  // Header price + change must share one source. Gate the change on whether a live PRICE
  // exists (not whether the live change exists), so a live price with no change shows
  // "--" instead of the frozen day's change computed against a different reference.
  const hasHeaderLive = livePrice != null && livePrice > 0;
  const headerPrice = hasHeaderLive ? livePrice : currentPrice;
  const headerPct = hasHeaderLive ? livePricePct : priceChangePercent;
  const contractInfo = data?.contract || { symbol: 'CLV25', description: 'WTI CRUDE OIL FUTURES' };
  const activeMetrics = data?.performance_metrics?.by_horizon?.['1w'] || {};

  // Walk-forward out-of-sample stats. NOTE: wfIsSignificant is now False (purged backtest is a
  // coin flip), so the tear sheet and stance render as retracted/NEUTRAL. Kept for the data shape.
  const wfIsSignificant = activeMetrics?.wf_is_significant ?? null;
  const wfCi95          = activeMetrics?.wf_ci_95 ?? null;
  const wfSharpe        = activeMetrics?.wf_pnl_sharpe ?? null;
  const wfSamples       = activeMetrics?.wf_samples ?? null;
  const wfWinRate       = activeMetrics?.wf_pnl_win_rate ?? null;
  const wfProfitFactor  = activeMetrics?.wf_pnl_profit_factor ?? null;
  const wfMeanPnl       = activeMetrics?.wf_pnl_mean_per_trade ?? null;
  const wfMaxDrawdown   = activeMetrics?.wf_pnl_max_drawdown ?? null;
  const wfYearly        = activeMetrics?.wf_yearly_breakdown ?? null;

  // Volatility forecast — the project's validated signal (backend/vol_forecast.py).
  const vol = data?.vol_forecast || null;

  // Live record — git-committed daily calls, resolved after 1 week (backend/live_record.py).
  // Every entry/resolution is timestamped by a bot commit, so the record can't be back-dated.
  const lr = data?.live_record || null;
  const liveN   = lr ? Number(lr.n_resolved_directional ?? 0) : Number(activeMetrics?.live_total_predictions ?? 0);
  const liveAcc = lr ? Number(lr.hit_rate_pct ?? 0) : Number(activeMetrics?.live_direction_accuracy ?? 0);
  const livePending = lr ? Number(lr.n_pending ?? 0) : 0;
  const liveRecord = liveN === 0
    ? `Live: 0 resolved${livePending > 0 ? ` · ${livePending} pending` : ''} — each call settles after 1 week`
    : liveN < 18
    ? `Live: ${liveN} resolved · ${Math.round(liveAcc)}% — too few to validate (need ≥18)`
    : `Live: ${liveN} resolved · ${Math.round(liveAcc)}% hit rate`;

  // OOS equity curve from the walk-forward per-trade series (cumulative net P&L).
  const wfTrades = Array.isArray(activeMetrics?.wf_pnl_trades) ? activeMetrics.wf_pnl_trades : [];
  const equityCurve = (() => {
    if (wfTrades.length < 10) return null;
    let cum = 0;
    const pts = wfTrades.map((t) => { cum += Number(t.pnl) || 0; return cum; });
    const lo = Math.min(0, ...pts);
    const hi = Math.max(...pts);
    const span = hi - lo || 1;
    const W = 560, H = 64;
    const x = (i) => (i / (pts.length - 1)) * W;
    const y = (v) => H - ((v - lo) / span) * H;
    const line = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const area = `${line} L${W},${H} L0,${H} Z`;
    return {
      line, area, W, H,
      zeroY: y(0),
      first: wfTrades[0].t,
      last: wfTrades[wfTrades.length - 1].t,
      total: pts[pts.length - 1],
    };
  })();

  // Kelly position sizing from walk-forward win rate + profit factor
  const sizing = (wfIsSignificant === true && wfWinRate && wfProfitFactor && wfMeanPnl)
    ? (() => {
        const p = wfWinRate / 100;
        const pf = wfProfitFactor;
        const b = pf * (1 - p) / p;
        const fullKelly = p - (1 - p) / b;
        const avgLoss = Math.abs(wfMeanPnl / ((pf - 1) * (1 - p)));
        return {
          fullKelly: Math.round(fullKelly * 100),
          halfKelly: Math.round(fullKelly * 50),
          avgLoss: Math.round(avgLoss),
          acctPer1: Math.round(avgLoss / 0.02 / 5000) * 5000,
        };
      })()
    : null;

  const fmtPct = (v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;
  const fc1wRaw = Number(data?.multi_horizon_predictions?.percentage_changes?.['1w'] ?? 0);
  const fc1wPct = Math.abs(fc1wRaw) < 0.05 ? 0 : fc1wRaw;
  const deskCall = (() => {
    let stance = 'NEUTRAL', tone = 'neutral';
    if (wfIsSignificant === true && fc1wPct > 0.6)  { stance = 'LONG LEAN';  tone = 'up'; }
    if (wfIsSignificant === true && fc1wPct < -0.6) { stance = 'SHORT LEAN'; tone = 'down'; }
    const hk = sizing ? `~${sizing.halfKelly}% of capital` : 'half-Kelly';
    const text = stance === 'LONG LEAN'
      ? `1W forecast ${fmtPct(fc1wPct)}. Model leans long — size to half-Kelly (${hk}). No live track record yet.`
      : stance === 'SHORT LEAN'
      ? `1W forecast ${fmtPct(fc1wPct)}. Model leans short — size to half-Kelly (${hk}). No live track record yet.`
      : `1W model output ${fmtPct(fc1wPct)} (reference only). The backtested edge was a look-ahead leak and is retracted; there is no validated directional signal.`;
    return { stance, tone, text };
  })();

  return (
    <div className="tv-app">
      {/* Top bar */}
      <div className="tv-topbar">
        <div className="tv-brand">
          <span className="tv-brand-mark">WTI</span>
          <span className="tv-brand-text">
            <span className="tv-brand-title">WTI Crude Oil Futures</span>
            <span className="tv-brand-sub">1W Direction Model · edge retracted (look-ahead leak) · research post-mortem</span>
          </span>
        </div>
        <div className="tv-topbar-right">
          <span className="tv-topbar-time tv-num">
            {currentTime.toLocaleTimeString('en-US', { hour12: false, timeZone: 'America/Chicago' })} CT
          </span>
          <span className="tv-topbar-asof">
            {data?.frozen_at
              ? `Data as of ${new Date(data.frozen_at).toLocaleString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} CT`
              : `Updated ${Math.floor((currentTime - lastUpdate) / 1000)}s ago`}
          </span>
          <a
            className="tv-topbar-link"
            href="https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures"
            target="_blank"
            rel="noopener noreferrer"
          >
            GitHub ↗
          </a>
        </div>
      </div>

      {mlCaveat && <div className="tv-caveat">⚠ {mlCaveat}</div>}

      {error && data && (
        <div className="tv-caveat info">{error}</div>
      )}

      {/* Desk header — price · the call · sizing */}
      <div className="tv-desk">
        <div>
          <div className="tv-market-symbol">
            <span className="tv-chip">{contractInfo.symbol || 'CLN26'}</span>
            <span className="tv-market-name">WTI Crude · NYMEX</span>
          </div>
          <div className="tv-desk-pricewrap">
            <span className="tv-desk-px">
              {headerPrice > 0 ? `$${headerPrice.toFixed(2)}` : '--'}
            </span>
            <span className={`tv-desk-chg ${headerPct > 0 ? 'is-up' : headerPct < 0 ? 'is-down' : ''}`}>
              {headerPrice > 0 && headerPct != null
                ? `${headerPct > 0 ? '+' : ''}${headerPct.toFixed(2)}%`
                : '--'}
            </span>
            {livePrice && livePriceFresh && <span className="tv-live-badge">LIVE</span>}
          </div>
          <div className="tv-market-meta">
            {contractInfo.days_to_expiry != null && <>{contractInfo.days_to_expiry}d to expiry</>}
            {contractInfo.days_to_expiry != null && data?.volume_display && ' · '}
            {data?.volume_display && <>Vol {data.volume_display}</>}
          </div>
        </div>

        <div className="tv-desk-call">
          <div className="tv-desk-label">1W Direction Signal</div>
          <div className={`tv-desk-stance tone-${deskCall.tone}`}>{deskCall.stance}</div>
          <div className="tv-desk-text">{deskCall.text}</div>
        </div>

        <div>
          <div className="tv-desk-label">Position Sizing</div>
          {sizing ? (
            <>
              <div className="tv-sizing-kelly">
                <div><b>{sizing.halfKelly}%</b><span>Half-Kelly</span></div>
                <div><b>{sizing.fullKelly}%</b><span>Full Kelly</span></div>
              </div>
              <div className="tv-sizing-note">
                1 contract per ~${sizing.acctPer1.toLocaleString()} account at 2% risk
                <span className="muted"> · ${sizing.avgLoss.toLocaleString()} avg loss/contract · backtest basis</span>
              </div>
            </>
          ) : (
            <div className="tv-desk-text muted">Sized only when the model shows a directional edge.</div>
          )}
        </div>
      </div>

      {/* Performance tear sheet — out-of-sample walk-forward */}
      {wfIsSignificant === true && (
        <div className="tv-tearsheet">
          <div className="tv-tearsheet-head">
            <span className="tv-desk-label">5-Year Walk-Forward Backtest · Out-of-Sample</span>
            <span className="tv-tearsheet-live">{liveRecord}</span>
          </div>
          <div className="tv-tearsheet-grid">
            <div><b>{wfWinRate?.toFixed(1)}%</b><span>Hit Rate</span></div>
            <div><b>{wfSharpe?.toFixed(2)}</b><span>Sharpe</span></div>
            <div><b>{wfProfitFactor?.toFixed(2)}×</b><span>Profit Factor</span></div>
            <div><b className="up">+${Math.round(wfMeanPnl).toLocaleString()}</b><span>Expectancy / trade</span></div>
            <div><b className="down">−${Math.round(wfMaxDrawdown).toLocaleString()}</b><span>Max Drawdown</span></div>
            <div><b>{wfSamples}</b><span>OOS Trades</span></div>
          </div>
          {equityCurve && (
            <div className="tv-equity">
              <div className="tv-equity-head">
                <span>OOS equity curve · {wfTrades.length} trades · {equityCurve.first} → {equityCurve.last}</span>
                <span className={equityCurve.total >= 0 ? 'up' : 'down'}>
                  {equityCurve.total >= 0 ? '+' : '−'}${Math.abs(Math.round(equityCurve.total)).toLocaleString()} net
                </span>
              </div>
              <svg
                viewBox={`0 0 ${equityCurve.W} ${equityCurve.H}`}
                preserveAspectRatio="none"
                className="tv-equity-svg"
                role="img"
                aria-label="Cumulative out-of-sample P&L"
              >
                <path d={equityCurve.area} fill="rgba(92,176,214,0.10)" />
                <line x1="0" y1={equityCurve.zeroY} x2={equityCurve.W} y2={equityCurve.zeroY}
                      stroke="#30363d" strokeWidth="1" strokeDasharray="3,4" />
                <path d={equityCurve.line} fill="none" stroke="#5cb0d6" strokeWidth="1.6" />
              </svg>
            </div>
          )}
          {wfYearly && Object.keys(wfYearly).length > 0 && (
            <div className="tv-yearly">
              {Object.entries(wfYearly).map(([year, d]) => (
                <div key={year}>
                  <span className="tv-yearly-year">{year}</span>
                  <b className={d.sharpe >= 0 ? 'up' : 'down'}>{Number(d.sharpe).toFixed(2)}</b>
                  <span className="tv-yearly-sub">{d.n_trades} trades · {Math.round(d.win_rate_pct)}% win</span>
                </div>
              ))}
            </div>
          )}
          <div className="tv-tearsheet-foot">
            95% CI [{wfCi95?.[0]}, {wfCi95?.[1]}] · p &lt; 0.001 (holds at measured ESS 176/199) · expanding-window walk-forward · 50.4 trades/yr annualization · $100/trade costs · no macro · context lagged 1d (entry-time-clean)
          </div>
        </div>
      )}

      {/* Signal-retraction notice — always shown. The original backtest (Sharpe 2.44 / 2.07) was a
          look-ahead leak; purged, the signal is a coin flip. This card replaces the headline metrics
          with the honest finding so the dashboard never implies a tradeable edge. */}
      <div className="tv-tearsheet">
        <div className="tv-tearsheet-head">
          <span className="tv-desk-label">Signal status · retracted (look-ahead leak)</span>
          <span className="tv-tearsheet-live">research post-mortem</span>
        </div>
        <div className="tv-tearsheet-foot">
          The original 1-week backtest (Sharpe 2.44 over 5y, 2.07 over 10y) was a look-ahead leakage
          artifact: the walk-forward trained on rows whose 5-day targets matured after the prediction
          point. After a standard purge/embargo, direction accuracy falls to 48–52% (p &gt; 0.2) and
          the strategy loses money after costs on both windows. As built, the signal has no
          out-of-sample edge, so no stance or sizing is shown as actionable. The dashboard and
          deploy pipeline are kept as engineering; the trading claim is retracted. See the README
          headline finding for the full post-mortem.
        </div>
      </div>

      {/* Method note — addressed to a hedge-fund PM. Where the effort went + the noise-floor point:
          signals that the author knows what is and is not tradeable, which is risk thinking, not model hype. */}
      <div className="tv-tearsheet">
        <div className="tv-tearsheet-head">
          <span className="tv-desk-label">Method · where the effort actually went</span>
          <span className="tv-tearsheet-live">for a PM reading this</span>
        </div>
        <div className="tv-tearsheet-foot">
          If you run a book, read this as risk discipline, not a model pitch. The effort here was evaluation
          and data integrity first, model training last: a leak-free purged walk-forward, a look-ahead leak
          caught in my own signal, and CI tests that keep it caught, on top of point-in-time, entry-time-clean
          data. The model is deliberately simple (HAR-IV plus a small ensemble), and that is the point. No model
          can beat the noise floor set by the mutual information between features and target. Weekly price
          direction carries almost none, which is why the purged direction signal is a coin flip; realized
          volatility is persistent, which is why a simple forecaster extracts real skill. The work that mattered
          was lowering and honestly measuring that floor, not tuning the model — the same judgment that separates
          a tradeable signal from noise before capital is at risk.
        </div>
      </div>

      {/* Volatility forecast — the project's validated signal (backend/vol_forecast.py). */}
      {vol?.live && vol?.validation && (
        <div className="tv-tearsheet">
          <div className="tv-tearsheet-head">
            <span className="tv-desk-label">Volatility Forecast · validated signal (HAR-IV: realized vol + OVX)</span>
            <span className="tv-tearsheet-live">next-week, leak-free</span>
          </div>
          <div className="tv-tearsheet-grid">
            <div>
              <b className={vol.live.direction === 'RISING' ? 'up' : 'down'}>{vol.live.direction}</b>
              <span>Next-week vol</span>
            </div>
            <div><b>{vol.live.current_realized_vol_5d_annualized_pct}%</b><span>Current RV (5d)</span></div>
            <div><b>{vol.live.forecast_next_week_vol_annualized_pct}%</b><span>Forecast (ann.)</span></div>
            {vol.live.implied_vol_ovx_pct != null
              ? <div><b>{vol.live.implied_vol_ovx_pct}%</b><span>Implied (OVX)</span></div>
              : <div><b>{vol.validation.majority_class_pct}%</b><span>Base Rate</span></div>}
            <div><b className="up">{vol.validation.har_dir_acc_pct}%</b><span>OOS Dir. Acc.</span></div>
            <div><b>{vol.validation.n}</b><span>OOS Weeks</span></div>
          </div>
          <div className="tv-tearsheet-foot">
            Next-week realized-volatility direction, 10y purged walk-forward: {vol.validation.har_dir_acc_pct}% accuracy
            vs a {vol.validation.majority_class_pct}% base rate (z ≈ 8.5, p &lt; 1e-15; ex-2020 {vol.validation.ex_2020_har_dir_acc_pct}%, stable every year).
            Beats a mean-reversion baseline ({vol.validation.mean_reversion_dir_acc_pct}%). A clean implementation of a known
            effect (vol clustering/mean-reversion), not novel alpha — a validated volatility/regime indicator, not a directional
            trade. A vol-targeting overlay was tested and did NOT beat buy-and-hold (Sharpe 0.27 vs 0.36), so no P&L is claimed.
            Level R² {vol.validation.har_level_r2} (persistence {vol.validation.persistence_level_r2}).
          </div>
        </div>
      )}

      {/* Roadmap — forward plan if funded. This build is deliberately 100% free-resource; the card
          makes the next steps explicit so a reader sees the long-term direction, not just the ceiling. */}
      <div className="tv-tearsheet">
        <div className="tv-tearsheet-head">
          <span className="tv-desk-label">Roadmap · what a funded version would add</span>
          <span className="tv-tearsheet-live">free-tier ceiling reached · plan, not built</span>
        </div>
        <div className="tv-roadmap">
          <div className="tv-roadmap-item">
            <span className="tv-roadmap-tag">Data</span>
            <div className="tv-roadmap-body">
              <b>Tick-level realized variance</b>
              <span>Intraday realized variance from a paid futures tick feed (CME DataMine / Nasdaq Data Link) replaces close-to-close RV — the exact input the HAR model was designed for, and a materially sharper volatility estimate.</span>
            </div>
          </div>
          <div className="tv-roadmap-item">
            <span className="tv-roadmap-tag">Signal</span>
            <div className="tv-roadmap-body">
              <b>Full CL options surface</b>
              <span>Strike-by-strike implied vol, not just the single OVX index, yields a model-free variance risk premium (implied variance minus realized) — a signal real volatility desks actually trade.</span>
            </div>
          </div>
          <div className="tv-roadmap-item">
            <span className="tv-roadmap-tag">System</span>
            <div className="tv-roadmap-body">
              <b>Persistent live backend</b>
              <span>A small cloud server and database replace the 4-hour frozen snapshot: sub-minute prices, rolling model retrains, and a tamper-evident live track record instead of a static file.</span>
            </div>
          </div>
          <div className="tv-roadmap-item">
            <span className="tv-roadmap-tag">Breadth</span>
            <div className="tv-roadmap-body">
              <b>Energy-complex coverage</b>
              <span>Extend the same forecaster from WTI to Brent, natural gas and heating oil. By Grinold&apos;s law a small edge applied across ~10 uncorrelated instruments scales the information ratio by roughly sqrt(10) — the jump from a single-instrument study toward a portfolio.</span>
            </div>
          </div>
        </div>
        <div className="tv-tearsheet-foot">
          Today&apos;s system is intentionally 100% free-resource (Yahoo Finance, EIA, CBOE OVX; static GitHub Pages, auto-refreshed every 4h). Each item above is a costed next step, listed so the engineering and research direction is explicit — not a present capability.
        </div>
      </div>

      {/* Supply Risk Context — EIA historical price response data */}
      {data && (playbookDist.supply_lost || playbookDist.threat_only) && (
        <div className="tv-section">
          <button className="tv-geo-bar" onClick={() => setGeoOpen((o) => !o)}>
            <span className="tv-geo-bar-title">Supply Risk Context</span>
            <span className="tv-geo-bar-summary">
              <span className="muted">EIA · {playbookEventCount} events 1990–2024</span>
              {playbookDist.supply_lost?.peak && (
                <><span className="dot">·</span>
                <span>Physical loss <strong className="up">+{playbookDist.supply_lost.peak.median}% peak</strong></span></>
              )}
              {playbookDist.threat_only?.peak && (
                <><span className="dot">·</span>
                <span>Threat-only <strong>+{playbookDist.threat_only.peak.median}% peak</strong></span></>
              )}
              {geoNoveltySpike && <span className="tv-flash">⚡ breaking</span>}
            </span>
            <span className="tv-geo-bar-toggle">{geoOpen ? 'Hide —' : 'Show +'}</span>
          </button>
          {geoOpen && (
            <div className="tv-supply-section">
              <div className="tv-card-label">
                Historical WTI Price Response by Supply Event Type
                <span className="muted">median move across {playbookEventCount} events 1990–2024 · computed from EIA daily spot</span>
              </div>

              {playbookDist.supply_lost?.settle && playbookDist.threat_only?.settle != null && (
                <div className="tv-supply-takeaway">
                  Physical supply losses hold their gains
                  {' '}(<strong className="up">+{playbookDist.supply_lost.settle.median}%</strong> median settle);
                  {' '}pure threats with no barrels lost fade
                  {' '}(<strong className={playbookDist.threat_only.settle.median >= 0 ? 'up2' : 'down'}>{playbookDist.threat_only.settle.median >= 0 ? '+' : ''}{playbookDist.threat_only.settle.median}%</strong>)
                  {' '}— the market pays for real disruption, not headlines.
                </div>
              )}

              <div className="tv-supply-table">
                <div className="tv-supply-row tv-supply-header">
                  <span>Event type</span><span>Events</span><span>Peak</span><span>Settled</span>
                </div>
                {supplyRows.map((r) => (
                  <div className="tv-supply-row" key={r.key}>
                    <span className="tv-supply-cat">{r.label}</span>
                    <span className="muted">{r.n}</span>
                    <span className={r.peak >= 0 ? 'up' : 'down'}>{r.peak >= 0 ? '+' : ''}{r.peak}%</span>
                    <span className={r.settle == null ? 'muted' : r.settle >= 0 ? 'up2' : 'down'}>
                      {r.settle == null ? '—' : `${r.settle >= 0 ? '+' : ''}${r.settle}%`}
                    </span>
                  </div>
                ))}
              </div>

              {playbookPricedIn.strong_day0_n > 0 && (
                <div className="tv-pricedin-note">
                  Momentum, not fade: a strong day-0 reaction (≥+3%) leads to a median eventual peak of{' '}
                  <strong className="up">+{playbookPricedIn.strong_day0_median_peak}%</strong>
                  {' '}vs <strong>+{playbookPricedIn.weak_day0_median_peak}%</strong> for a muted open
                  {' '}(n={playbookPricedIn.strong_day0_n} / {playbookPricedIn.weak_day0_n}).
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Main chart */}
      <div className="bloomberg-window" style={{
        flex: '1 1 auto',
        minHeight: '680px',
        height: 'calc(100vh - 150px)',
        borderTop: '1px solid #1c2230',
        margin: '0'
      }}>
        <Chart
          actualArray={data?.actual || []}
          multiHorizonPredictions={data?.multi_horizon_predictions}
          unifiedData={data?.unified_data}
          currentPrice={currentPrice}
          contractInfo={contractInfo}
          priceChange={priceChange}
          priceChangePercent={priceChangePercent}
          livePrice={livePrice}
          livePriceChange={livePriceChange}
          livePricePct={livePricePct}
          feedStatus={data?.feed_status || 'UNKNOWN'}
        />
      </div>

    </div>
  );
}

export default App;
