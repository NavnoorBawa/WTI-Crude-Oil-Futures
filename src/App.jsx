import React, { useState, useEffect, useRef } from "react";
import Chart from "./Chart";

// 1W is the only walk-forward validated signal: 62.8% direction accuracy, p=0.0002, n=199 OOS.
// 1D: dead (p=0.92). 1H: same feature class, never walk-forward tested. Both removed.

const humanizeReason = (value) => {
  if (!value) return "";
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
};

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [loadingMessage, setLoadingMessage] = useState("Connecting to Real-Time Data Feeds");
  const [lastUpdate, setLastUpdate] = useState(new Date());
  const [currentTime, setCurrentTime] = useState(new Date());
  const [geoOpen, setGeoOpen] = useState(false);
  const pollIntervalMs = Number(import.meta.env.VITE_POLL_INTERVAL_MS || 15000);
  const startupRetryMs = Number(import.meta.env.VITE_STARTUP_RETRY_MS || 5000);
  const configuredApiBase = import.meta.env.VITE_API_BASE_URL;
  // Static-snapshot mode (GitHub Pages): the React app reads a frozen data.json produced by
  // freeze.py in CI instead of polling a live backend. BASE_URL handles the Pages sub-path.
  const staticDataMode = import.meta.env.VITE_STATIC_DATA === "true";
  const staticDataUrl = `${import.meta.env.BASE_URL}data.json`;
  const latestDataRef = useRef(null);
  const requestInFlightRef = useRef(false);
  const startupRetryPendingRef = useRef(false);

  latestDataRef.current = data;

  // In static mode the "endpoint" is just the frozen JSON file shipped alongside the site.
  const buildRequestUrl = (apiBase) =>
    apiBase.endsWith(".json") ? apiBase : `${apiBase}/data`;

  const getApiBaseCandidates = () => {
    const hostname = window.location.hostname;
    const isLocalHost = hostname === "localhost" || hostname === "127.0.0.1";

    if (staticDataMode) {
      return [staticDataUrl];
    }

    if (configuredApiBase) {
      return [configuredApiBase];
    }

    if (isLocalHost) {
      return ["http://127.0.0.1:9000"];
    }

    // Production default if frontend env var is missing.
    return ["https://wti-crude-oil-backend.onrender.com"];
  };

  useEffect(() => {
    // Set title
    document.title = "WTI Crude Oil Futures · Quant Forecast & Geo Risk";
    let isDisposed = false;
    let retryTimeoutId = null;

    // Update time every second
    const timeInterval = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);

    const clearRetryTimeout = () => {
      if (retryTimeoutId) {
        clearTimeout(retryTimeoutId);
        retryTimeoutId = null;
      }
    };

    const scheduleRetry = (retryAfterSeconds) => {
      const delayMs = Math.max(
        2000,
        Number.isFinite(Number(retryAfterSeconds))
          ? Number(retryAfterSeconds) * 1000
          : startupRetryMs
      );

      clearRetryTimeout();
      retryTimeoutId = setTimeout(() => {
        if (!isDisposed) {
          fetchData(true);
        }
      }, delayMs);
    };

    // Fetch data function
    const fetchData = async (isInitial = false) => {
      let didStartRequest = false;
      try {
        if (isInitial) {
          setLoading(true);
          setError(null);
          setLoadingMessage("Connecting to Real-Time Data Feeds");
        }

        if (requestInFlightRef.current) {
          return;
        }
        if (!isInitial && startupRetryPendingRef.current && !latestDataRef.current) {
          return;
        }

        requestInFlightRef.current = true;
        didStartRequest = true;
        
        const apiCandidates = getApiBaseCandidates();
        let response = null;
        let lastAttemptError = null;

        for (const apiBase of apiCandidates) {
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 30000);

          try {
            const attempt = await fetch(buildRequestUrl(apiBase), {
              signal: controller.signal,
              method: 'GET',
              headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
              },
            });

            clearTimeout(timeoutId);

            let responsePayload = null;
            const responseType = attempt.headers.get('content-type') || '';
            const canParseJson = responseType.includes('application/json');

            if (!attempt.ok || attempt.status === 503) {
              if (canParseJson) {
                try {
                  responsePayload = await attempt.json();
                } catch {
                  responsePayload = null;
                }
              }
            }

            const retryAfterHeader = Number(attempt.headers.get('Retry-After'));
            if (responsePayload?.error === 'SYSTEM_INITIALIZING') {
              const startupError = new Error(responsePayload.message || 'Backend is waking up');
              startupError.code = 'SYSTEM_INITIALIZING';
              startupError.retryAfterSeconds = Number(responsePayload.retry_after_seconds) || retryAfterHeader || (startupRetryMs / 1000);
              throw startupError;
            }

            if (attempt.ok) {
              response = attempt;
              break;
            }

            const errorMessage = responsePayload?.message || responsePayload?.error || attempt.statusText || 'Request failed';
            lastAttemptError = new Error(`HTTP ${attempt.status}: ${errorMessage}`);
          } catch (attemptErr) {
            clearTimeout(timeoutId);
            lastAttemptError = attemptErr;
          }
        }

        if (!response) {
          throw lastAttemptError || new Error("No reachable API endpoint found");
        }

        const result = await response.json();

        if (result?.error === 'SYSTEM_INITIALIZING') {
          const startupError = new Error(result.message || 'Backend is waking up');
          startupError.code = 'SYSTEM_INITIALIZING';
          startupError.retryAfterSeconds = Number(result.retry_after_seconds) || (startupRetryMs / 1000);
          throw startupError;
        }
        
        // Update state in correct order
        clearRetryTimeout();
        startupRetryPendingRef.current = false;
        setData(result);
        setLastUpdate(new Date());
        setError(null);
        setLoading(false);
        setLoadingMessage("Connecting to Real-Time Data Feeds");
        
      } catch (err) {
        if (err.code === 'SYSTEM_INITIALIZING') {
          const retryAfterSeconds = Number(err.retryAfterSeconds) || (startupRetryMs / 1000);
          const startupMessage = err.message || 'Backend is waking up on Render. This can take 10-30 seconds on the free tier.';
          startupRetryPendingRef.current = true;

          if (isInitial || !latestDataRef.current) {
            setLoading(true);
            setError(null);
            setLoadingMessage(startupMessage);
          } else {
            setError(`LIVE DATA DELAY: ${startupMessage} Showing last good market snapshot.`);
          }

          scheduleRetry(retryAfterSeconds);
          return;
        } else if (err.name === 'AbortError') {
          setError('Server timeout - Please wait and refresh');
        } else if (err.name === 'TypeError' && err.message.includes('Failed to fetch')) {
          setError('Cannot connect to backend API - verify VITE_API_BASE_URL in frontend environment');
        } else {
          setError(`Network error: ${err.message}`);
        }
        startupRetryPendingRef.current = false;
        setLoading(false);
      } finally {
        if (didStartRequest) {
          requestInFlightRef.current = false;
        }
      }
    };

    // Initial fetch
    fetchData(true);

    // Backend prediction cadence is minutes, so lower polling frequency cuts load.
    const interval = setInterval(() => fetchData(false), pollIntervalMs);

    return () => {
      isDisposed = true;
      clearRetryTimeout();
      clearInterval(interval);
      clearInterval(timeInterval);
    };
  }, [configuredApiBase, pollIntervalMs, startupRetryMs]);

  const headlineMetrics = data?.performance_metrics?.headline || {};

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
  // scenario_analysis is kept only for mlCaveat; all geo signal numbers from it
  // (edge_usd, edge_pct, ev_impact_usd, signal, signal_strength) were computed from
  // fake hand-entered IRAN_GEOPOLITICAL_EVENTS in oil.py and are NOT displayed.
  const scenario = data?.scenario_analysis || {};
  const mlCaveat = scenario?.ml_caveat || null;

  // EIA-sourced supply-shock playbook (replaces old fake scenario engine)
  const playbook = data?.supply_shock_playbook || {};
  const playbookAnalogues = Array.isArray(playbook.analogues) ? playbook.analogues : [];
  const playbookDist = playbook.distributions || {};
  const playbookEventCount = Number(playbook.event_count || 0);
  const playbookPricedIn = playbook.priced_in_stats || {};

  const geoRisk = data?.geopolitical_risk || {};
  const geoScore = Number(geoRisk.score || 0);
  const geoRegime = String(geoRisk.regime || 'UNKNOWN');
  const geoDominantDriver = String(geoRisk.dominant_driver || 'unknown').toUpperCase();
  const geoBreakdown = geoRisk.risk_breakdown || {};
  const geoHeadlines = Array.isArray(geoRisk.top_headlines) ? geoRisk.top_headlines : [];
  const geoArticles = Number(geoRisk.total_articles_scanned || 0);
  const geoRecent24h = Number(geoRisk.recent_24h_articles || 0);
  const geoNoveltySpike = Boolean(geoRisk.novelty_spike);
  const geoRegimeColor =
    geoRegime === 'CRITICAL' ? 'text-bloomberg-red' :
    geoRegime === 'HIGH'     ? '#ff6600' :
    geoRegime === 'ELEVATED' ? 'text-bloomberg-alert' :
    geoRegime === 'LOW'      ? 'text-bloomberg-positive' :
                               'text-gray-400';
  const geoScoreBarColor =
    geoRegime === 'CRITICAL' ? '#ff5c5c' :
    geoRegime === 'HIGH'     ? '#ff8c42' :
    geoRegime === 'ELEVATED' ? '#d6a93a' :
    geoRegime === 'LOW'      ? '#16c784' : '#6e7681';

  const currentPrice = data?.current_price || 0;
  const priceChange = data?.price_change || 0;
  const priceChangePercent = data?.price_change_percent || 0;
  const contractInfo = data?.contract || { symbol: 'CLV25', description: 'WTI CRUDE OIL FUTURES' };
  const activeHorizonKey = '1w';
  const metricsByHorizon = data?.performance_metrics?.by_horizon || {};
  const activeMetrics = metricsByHorizon?.[activeHorizonKey] || {};
  const activeQuality = activeMetrics?.quality || {};
  const currentPrediction = Number(
    data?.multi_horizon_predictions?.predictions?.[activeHorizonKey]
    ?? headlineMetrics?.prediction
    ?? 0
  ) || 0;

  const totalEvaluatedPredictions = Number(data?.performance_metrics?.total_predictions || 0);
  const liveDirectionAccuracy = Number(data?.performance_metrics?.direction_accuracy || 0);
  const displayDirectionAccuracyRaw = activeMetrics?.display_accuracy ?? headlineMetrics?.display_direction_accuracy ?? data?.performance_metrics?.display_direction_accuracy;
  const displayAccuracySource = activeMetrics?.display_accuracy_source || headlineMetrics?.display_accuracy_source || data?.performance_metrics?.display_accuracy_source || 'unavailable';
  const minLiveSamples = Number(data?.performance_metrics?.min_live_accuracy_samples || 18);
  const modelConfidence = activeMetrics?.confidence ?? headlineMetrics?.confidence;
  const headlineQualityStatus = String(activeQuality?.status || headlineMetrics?.quality_status || data?.enterprise_metrics?.quality_status || 'UNKNOWN').toUpperCase();
  const headlineQualityReasons = Array.isArray(activeQuality?.reasons)
    ? activeQuality.reasons.map(humanizeReason)
    : (Array.isArray(headlineMetrics?.quality_reasons) ? headlineMetrics.quality_reasons.map(humanizeReason) : []);
  const isRealPrediction = Boolean(data?.multi_horizon_predictions?.is_real_prediction);
  const isFullRealPrediction = Boolean(data?.multi_horizon_predictions?.is_full_real_prediction);
  const fallbackHorizons = Object.entries(data?.multi_horizon_predictions?.fallbacks || {})
    .filter(([, used]) => Boolean(used))
    .map(([horizon]) => horizon.toUpperCase());

  const effectiveAccuracy = (displayDirectionAccuracyRaw !== undefined && displayDirectionAccuracyRaw !== null)
    ? Number(displayDirectionAccuracyRaw)
    : (totalEvaluatedPredictions > 0 ? liveDirectionAccuracy : null);

  // Walk-forward significance stats for the active horizon
  const wfPValue          = activeMetrics?.wf_p_value ?? null;
  const wfCi95            = activeMetrics?.wf_ci_95 ?? null;
  const wfIsSignificant   = activeMetrics?.wf_is_significant ?? null;
  const wfSamples         = activeMetrics?.wf_samples ?? null;
  const wfMaeImprovement  = activeMetrics?.wf_mae_improvement_pct ?? null;
  const wfSharpe          = activeMetrics?.wf_pnl_sharpe ?? null;
  const wfMeanPnl         = activeMetrics?.wf_pnl_mean_per_trade ?? null;
  const wfWinRate         = activeMetrics?.wf_pnl_win_rate ?? null;
  const wfMaxDrawdown     = activeMetrics?.wf_pnl_max_drawdown ?? null;
  const wfProfitFactor    = activeMetrics?.wf_pnl_profit_factor ?? null;
  const wfNTrades         = activeMetrics?.wf_pnl_n_trades ?? null;

  const displayAccuracy = !Number.isFinite(effectiveAccuracy)
    ? '--'
    : `${Math.round(effectiveAccuracy)}${displayAccuracySource === 'backtest' ? '%*' : '%'}`;

  const accuracyClassName = wfIsSignificant === true
    ? 'text-bloomberg-positive'
    : wfIsSignificant === false
    ? 'text-bloomberg-negative'
    : headlineQualityStatus === 'UNQUALIFIED'
    ? 'text-bloomberg-negative'
    : ((displayAccuracySource === 'live' || displayAccuracySource === 'live_sparse')
      ? 'text-bloomberg-positive'
      : 'text-bloomberg-blue');

  const fallbackConfidence = data?.performance_metrics?.confidence;
  const displayConfidence = Number.isFinite(modelConfidence)
    ? `${Math.round(modelConfidence)}%`
    : (fallbackConfidence !== undefined && fallbackConfidence !== null
      ? `${Math.round(fallbackConfidence)}%`
      : (data?.confidence || '--'));

  const fc1wPct = Number(data?.multi_horizon_predictions?.percentage_changes?.['1w'] ?? 0);
  const deskCall = (() => {
    let stance = 'NEUTRAL', tone = 'neutral';
    if (wfIsSignificant === true && fc1wPct > 0.6)  { stance = 'LONG LEAN';  tone = 'up'; }
    if (wfIsSignificant === true && fc1wPct < -0.6) { stance = 'SHORT LEAN'; tone = 'down'; }
    const text = stance === 'LONG LEAN'
      ? `1W forecast: +${fc1wPct.toFixed(1)}%. Model leans long — size to half-Kelly (~17% of capital). No live track record yet.`
      : stance === 'SHORT LEAN'
      ? `1W forecast: ${fc1wPct.toFixed(1)}%. Model leans short — size to half-Kelly (~17% of capital). No live track record yet.`
      : `1W forecast: ${fc1wPct >= 0 ? '+' : ''}${fc1wPct.toFixed(1)}%. No directional edge — signal activates when model conviction exceeds ±0.6%.`;
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
            <span className="tv-brand-sub">1W Direction Model · Walk-Forward Validated · n=199 OOS</span>
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
        </div>
      </div>

      {mlCaveat && <div className="tv-caveat">⚠ {mlCaveat}</div>}

      {error && data && (
        <div className="tv-caveat info">{error}</div>
      )}

      {/* Desk header — price · the call · the track record */}
      <div className="tv-desk">
        <div>
          <div className="tv-market-symbol">
            <span className="tv-chip">{contractInfo.symbol || 'CLN26'}</span>
            <span className="tv-market-name">WTI Crude · NYMEX</span>
          </div>
          <div className="tv-desk-pricewrap">
            <span className="tv-desk-px">{currentPrice > 0 ? `$${currentPrice.toFixed(2)}` : '--'}</span>
            <span className={`tv-desk-chg ${priceChange >= 0 ? 'is-up' : 'is-down'}`}>
              {currentPrice > 0 ? `${priceChangePercent >= 0 ? '+' : ''}${priceChangePercent.toFixed(2)}%` : '--'}
            </span>
          </div>
          <div className="tv-market-meta">Vol {data?.volume_display || 'N/A'}</div>
        </div>

        <div className="tv-desk-call">
          <div className="tv-desk-label">1W Direction Signal</div>
          <div className={`tv-desk-stance tone-${deskCall.tone}`}>{deskCall.stance}</div>
          <div className="tv-desk-text">{deskCall.text}</div>
        </div>

        <div>
          <div className="tv-desk-label">5-Year Backtest (OOS)</div>
          {wfIsSignificant === true ? (
            <>
              <div className="tv-record-grid">
                <div><b>{Number.isFinite(effectiveAccuracy) ? `${Math.round(effectiveAccuracy)}%` : '--'}</b><span>direction</span></div>
                <div><b>{wfCi95 ? `[${wfCi95[0]}, ${wfCi95[1]}]` : '--'}</b><span>95% CI</span></div>
                <div><b>{wfSharpe?.toFixed(2)}</b><span>Sharpe</span></div>
                <div><b>{wfSamples}</b><span>OOS trades</span></div>
              </div>
              <div className="tv-record-note">walk-forward expanding window · p&lt;0.001 · $100/trade cost · no macro features</div>
              <div className="tv-record-live">
                {(() => {
                  const n1w = Number(activeMetrics?.live_total_predictions ?? 0);
                  const acc1w = Number(activeMetrics?.live_direction_accuracy ?? 0);
                  return <>
                    1W live evaluated: <b>{n1w}</b>
                    {n1w === 0
                      ? <span className="muted"> — none yet (each prediction resolves after 1 week)</span>
                      : n1w < 18
                      ? <span className="muted"> — {Math.round(acc1w)}% · too early to validate (need ≥18)</span>
                      : <span> · {Math.round(acc1w)}% hit rate</span>}
                  </>;
                })()}
              </div>
            </>
          ) : (
            <div className="tv-desk-text">1-week is the validated horizon; the active horizon is not statistically reliable.</div>
          )}
        </div>

        {wfIsSignificant === true && wfWinRate && wfProfitFactor && wfMeanPnl && (() => {
          const p = wfWinRate / 100;
          const pf = wfProfitFactor;
          const b = pf * (1 - p) / p;
          const fullKelly = p - (1 - p) / b;
          const halfKelly = fullKelly / 2;
          const avgLoss = Math.abs(wfMeanPnl / ((pf - 1) * (1 - p)));
          const acctPer1 = Math.round(avgLoss / 0.02 / 5000) * 5000;
          return (
            <div className="tv-sizing">
              <div className="tv-desk-label">Position Sizing (Walk-Forward Basis)</div>
              <div className="tv-sizing-row">
                <span>Full Kelly <b>{(fullKelly * 100).toFixed(0)}%</b></span>
                <span className="dot">·</span>
                <span>Half-Kelly <b>{(halfKelly * 100).toFixed(0)}%</b></span>
                <span className="dot">·</span>
                <span>avg loss <b>${Math.round(avgLoss).toLocaleString()}</b>/contract</span>
              </div>
              <div className="tv-sizing-note">
                Conservative 2% risk: 1 CL contract per ~${acctPer1.toLocaleString()} account
                <span className="muted"> · backtest distribution only — no live record yet</span>
              </div>
            </div>
          );
        })()}
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
                <span className="muted">median of {playbookEventCount} events · price moves from EIA daily spot data</span>
              </div>
              <div className="tv-supply-table">
                <div className="tv-supply-row tv-supply-header">
                  <span>Category</span><span>Events</span><span>Peak</span><span>Settled</span>
                </div>
                {playbookDist.supply_lost && (
                  <div className="tv-supply-row">
                    <span className="tv-supply-cat">Physical loss &gt;0.5 mbpd</span>
                    <span className="muted">{playbookDist.supply_lost.n}</span>
                    <span className="up">+{playbookDist.supply_lost.peak?.median}%</span>
                    <span className="up2">+{playbookDist.supply_lost.settle?.median}%</span>
                  </div>
                )}
                {playbookDist.threat_only && (
                  <div className="tv-supply-row">
                    <span className="tv-supply-cat">Threat / no physical loss</span>
                    <span className="muted">{playbookDist.threat_only.n}</span>
                    <span className={playbookDist.threat_only.peak?.median >= 0 ? 'up' : 'down'}>
                      {playbookDist.threat_only.peak?.median >= 0 ? '+' : ''}{playbookDist.threat_only.peak?.median}%
                    </span>
                    <span className={playbookDist.threat_only.settle?.median >= 0 ? 'up2' : 'down'}>
                      {playbookDist.threat_only.settle?.median >= 0 ? '+' : ''}{playbookDist.threat_only.settle?.median}%
                    </span>
                  </div>
                )}
                {playbookDist.strait_risk && (
                  <div className="tv-supply-row">
                    <span className="tv-supply-cat">Hormuz risk events</span>
                    <span className="muted">{playbookDist.strait_risk.n}</span>
                    <span className={playbookDist.strait_risk.peak?.median >= 0 ? 'up' : 'down'}>
                      {playbookDist.strait_risk.peak?.median >= 0 ? '+' : ''}{playbookDist.strait_risk.peak?.median}%
                    </span>
                    <span className={playbookDist.strait_risk.settle?.median >= 0 ? 'up2' : 'down'}>
                      {playbookDist.strait_risk.settle?.median >= 0 ? '+' : ''}{playbookDist.strait_risk.settle?.median}%
                    </span>
                  </div>
                )}
                {playbookDist.iran_driven && (
                  <div className="tv-supply-row">
                    <span className="tv-supply-cat">Iran-driven</span>
                    <span className="muted">{playbookDist.iran_driven.n}</span>
                    <span className={playbookDist.iran_driven.peak?.median >= 0 ? 'up' : 'down'}>
                      {playbookDist.iran_driven.peak?.median >= 0 ? '+' : ''}{playbookDist.iran_driven.peak?.median}%
                    </span>
                    <span className={playbookDist.iran_driven.settle?.median >= 0 ? 'up2' : 'down'}>
                      {playbookDist.iran_driven.settle?.median >= 0 ? '+' : ''}{playbookDist.iran_driven.settle?.median}%
                    </span>
                  </div>
                )}
              </div>
              {playbookPricedIn.strong_day0_n > 0 && (
                <div className="tv-pricedin-note">
                  Priced-in check: strong day-0 reaction (≥+3%) → median eventual peak{' '}
                  <strong className="up">+{playbookPricedIn.strong_day0_median_peak}%</strong>
                  {' '}vs weak day-0 → <strong>+{playbookPricedIn.weak_day0_median_peak}%</strong>
                  {' '}(n={playbookPricedIn.strong_day0_n} / {playbookPricedIn.weak_day0_n})
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
          performanceMetricsByHorizon={data?.performance_metrics?.by_horizon || {}}
          unifiedData={data?.unified_data}
          currentPrice={currentPrice}
          contractInfo={contractInfo}
          priceChange={priceChange}
          priceChangePercent={priceChangePercent}
          feedStatus={data?.feed_status || 'UNKNOWN'}
        />
      </div>

    </div>
  );
}

export default App;
