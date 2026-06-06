import React, { useState, useEffect, useRef } from "react";
import Chart from "./Chart";

// 1H removed: direction accuracy is definitively below random (not statistically significant).
// 1W is the validated signal: 62.8% direction accuracy, p=0.0002, n=199 OOS samples.
const DISPLAY_HORIZONS = ["1D", "1W"];

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
  const [activeDisplayHorizon, setActiveDisplayHorizon] = useState("1W");
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
  const headlineHorizonKey = String(headlineMetrics?.horizon || "1d").toLowerCase();
  const recommendedDisplayHorizon = headlineMetrics?.quality_status === "QUALIFIED"
    ? headlineHorizonKey.toUpperCase()
    : "1W";
  const resolvedActiveDisplayHorizon = DISPLAY_HORIZONS.includes(activeDisplayHorizon)
    ? activeDisplayHorizon
    : recommendedDisplayHorizon;

  useEffect(() => {
    setActiveDisplayHorizon((previous) => (
      DISPLAY_HORIZONS.includes(previous) ? previous : recommendedDisplayHorizon
    ));
  }, [recommendedDisplayHorizon]);

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
  const scenario = data?.scenario_analysis || {};
  const geoSignal = scenario?.geo_signal || {};
  const topAnalogues = Array.isArray(scenario?.top_analogues) ? scenario.top_analogues : [];
  const impliedRange = scenario?.implied_price_range || {};
  const hormuzScenarios = Array.isArray(scenario?.hormuz_scenarios) ? scenario.hormuz_scenarios : [];
  const mlCaveat = scenario?.ml_caveat || null;
  const evImpactUsd = Number(geoSignal?.ev_impact_usd || 0);
  const edgeUsd = Number(geoSignal?.edge_usd || 0);
  const edgePct = Number(geoSignal?.edge_pct || 0);
  const signalStrength = Number(geoSignal?.signal_strength || 0);
  const expectedResolutionDays = Number(geoSignal?.expected_resolution_days || 0);

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
  const activeHorizonKey = resolvedActiveDisplayHorizon.toLowerCase();
  const activeHorizonLabel = resolvedActiveDisplayHorizon;
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

  return (
    <div className="tv-app">
      {/* Top bar */}
      <div className="tv-topbar">
        <div className="tv-brand">
          <span className="tv-brand-mark">WTI</span>
          <span className="tv-brand-text">
            <span className="tv-brand-title">WTI Crude Oil Futures</span>
            <span className="tv-brand-sub">Quant Forecast · Geopolitical Risk</span>
          </span>
        </div>
        <div className="tv-topbar-right">
          <span className="tv-live"><span className="tv-live-dot" />LIVE</span>
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

      {/* Market hero */}
      <div className="tv-market">
        <div className="tv-market-id">
          <div className="tv-market-symbol">
            <span className="tv-chip">{contractInfo.symbol || 'CLN26'}</span>
            <span className="tv-market-name">{contractInfo.description || 'WTI Crude Oil Futures · NYMEX'}</span>
          </div>
          <div className="tv-market-pricewrap">
            <span className="tv-market-price">{currentPrice > 0 ? `$${currentPrice.toFixed(2)}` : '--'}</span>
            <span className={`tv-market-change ${priceChange >= 0 ? 'is-up' : 'is-down'}`}>
              <span>{currentPrice > 0 ? `${priceChange >= 0 ? '+' : ''}${priceChange.toFixed(2)}` : '--'}</span>
              <span>{currentPrice > 0 ? `${priceChangePercent >= 0 ? '+' : ''}${priceChangePercent.toFixed(2)}%` : '--'}</span>
            </span>
          </div>
          <div className="tv-market-meta">
            Vol {data?.volume_display || 'N/A'} · ML {activeHorizonLabel} {currentPrediction > 0 ? `$${currentPrediction.toFixed(2)}` : '--'} · {data?.feed_status || 'REAL-TIME'}
          </div>
        </div>

        <div className="tv-stats">
          {wfIsSignificant === true ? (
            <>
              <div className="tv-stat up">
                <span className="tv-stat-label">1W Direction</span>
                <span className="tv-stat-value">{Number.isFinite(effectiveAccuracy) ? `${Math.round(effectiveAccuracy)}%` : '--'}</span>
                <span className="tv-stat-sub">p={wfPValue < 0.001 ? '<0.001' : wfPValue?.toFixed(3)} · n={wfSamples}</span>
              </div>
              {wfSharpe !== null && (
                <div className="tv-stat accent">
                  <span className="tv-stat-label">Sharpe (ann.)</span>
                  <span className="tv-stat-value">{wfSharpe.toFixed(2)}</span>
                  <span className="tv-stat-sub">after costs</span>
                </div>
              )}
              {wfMeanPnl !== null && (
                <div className="tv-stat">
                  <span className="tv-stat-label">E[PnL]/trade</span>
                  <span className="tv-stat-value">${Math.round(wfMeanPnl).toLocaleString()}</span>
                  <span className="tv-stat-sub">win rate {wfWinRate?.toFixed(0)}%</span>
                </div>
              )}
            </>
          ) : (
            <div className="tv-stat down">
              <span className="tv-stat-label">{activeHorizonLabel} Signal</span>
              <span className="tv-stat-value">Below random</span>
              <span className="tv-stat-sub">not directional</span>
            </div>
          )}
          <div className="tv-stat">
            <span className="tv-stat-label">Data Points</span>
            <span className="tv-stat-value">{data?.enterprise_metrics?.data_points || '--'}</span>
            <span className="tv-stat-sub">{wfSamples ? `${wfSamples} OOS samples` : 'walk-forward'}</span>
          </div>
        </div>
      </div>

      {/* Geopolitical intelligence */}
      {data && (
        <div className="tv-section">
          <div className="tv-section-head">
            <span className="tv-section-title">Geopolitical Intelligence</span>
            <span className="tv-section-rule" />
          </div>
          <div className="tv-geo-grid">

            {/* Regime */}
            <div className="tv-card">
              <div className="tv-card-label">
                Geo Risk Regime
                {geoNoveltySpike && <span className="tv-flash">⚡ BREAKING</span>}
              </div>
              <div className="tv-geo-score">
                <span className="tv-big">{geoScore.toFixed(0)}</span>
                <span className="tv-geo-max">/100</span>
                <span className="tv-regime-pill" style={{ color: geoScoreBarColor, background: `${geoScoreBarColor}22` }}>{geoRegime}</span>
              </div>
              <div className="tv-bar"><div className="tv-bar-fill" style={{ width: `${Math.min(100, geoScore)}%`, background: geoScoreBarColor }} /></div>
              <div className="tv-geo-breakdown">
                {Object.entries(geoBreakdown).filter(([,v]) => v > 0).map(([cat, count]) => (
                  <span key={cat}>{cat[0].toUpperCase()}<b>{count}</b></span>
                ))}
                {geoRecent24h > 0 && <span>24H<b>{geoRecent24h}</b></span>}
              </div>
            </div>

            {/* Trade signal */}
            <div className="tv-card">
              <div className="tv-card-label">Trade Signal</div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '9px' }}>
                <span className={`tv-signal ${
                  geoSignal.signal === 'LONG_BIAS' ? 'tone-up' :
                  geoSignal.signal === 'WATCH' ? 'tone-watch' : 'tone-neutral'
                }`}>{(geoSignal.signal || 'NEUTRAL').replace('_', ' ')}</span>
                {signalStrength > 0 && <span className="tv-strength">STR {signalStrength}/100</span>}
              </div>
              {geoSignal.strait_risk && <div className="tv-flag">⚑ Hormuz risk active</div>}
              <div className="tv-mini-stats">
                {evImpactUsd > 0 && (
                  <div><span>EV Impact</span><strong className="up">+${evImpactUsd.toFixed(1)}/bbl</strong></div>
                )}
                {edgeUsd !== 0 && (
                  <div><span>Unpriced Edge</span><strong className={edgeUsd >= 0 ? 'up' : 'down'}>{edgeUsd >= 0 ? '+' : ''}${edgeUsd.toFixed(2)} ({edgePct >= 0 ? '+' : ''}{edgePct.toFixed(1)}%)</strong></div>
                )}
                {expectedResolutionDays > 0 && (
                  <div><span>Avg Duration</span><strong className="blue">{expectedResolutionDays}d</strong></div>
                )}
              </div>
            </div>

            {/* Analogues */}
            <div className="tv-card">
              <div className="tv-card-label">Closest Historical Analogues</div>
              {topAnalogues.slice(0, 2).map((a) => (
                <div key={a.id} className="tv-analogue">
                  <div className="tv-analogue-title">{a.date} · {a.event}</div>
                  <div className="tv-analogue-stats">
                    <span className="up">Peak +{a.peak_pct}%</span>
                    <span className={a.settled_pct >= 0 ? 'up2' : 'down'}>Settled {a.settled_pct >= 0 ? '+' : ''}{a.settled_pct}%</span>
                    <span className="muted">{a.duration_days}d</span>
                  </div>
                </div>
              ))}
              {topAnalogues.length === 0 && <div className="tv-headline-text">Awaiting data</div>}
            </div>

            {/* Headlines */}
            {geoHeadlines.length > 0 && (
              <div className="tv-card">
                <div className="tv-card-label">
                  Live Headlines
                  {geoRecent24h > 0 && <span className="muted">{geoRecent24h} in 24h</span>}
                </div>
                {geoHeadlines.slice(0, 3).map((h, i) => (
                  <div key={i} className="tv-headline">
                    <span className={`tv-headline-date ${h.is_breaking ? 'brk' : ''}`}>
                      {h.is_breaking ? 'BRK' : (h.published_at ? new Date(h.published_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '--')}
                    </span>
                    <span className="tv-headline-text">{h.headline}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Implied range */}
            {impliedRange.high && (
              <div className="tv-card">
                <div className="tv-card-label">Analogue-Implied Price Range</div>
                <div className="tv-range">
                  <div>
                    <span>Bull</span>
                    <strong className="up">${impliedRange.high?.toFixed(2)}</strong>
                    <small className="up">+{((impliedRange.high - currentPrice) / currentPrice * 100).toFixed(1)}%</small>
                  </div>
                  <div>
                    <span>Base</span>
                    <strong>${impliedRange.mid?.toFixed(2)}</strong>
                    <small className={impliedRange.mid >= currentPrice ? 'up' : 'down'}>{impliedRange.mid >= currentPrice ? '+' : ''}{((impliedRange.mid - currentPrice) / currentPrice * 100).toFixed(1)}%</small>
                  </div>
                  <div>
                    <span>Bear</span>
                    <strong className="down">${impliedRange.low?.toFixed(2)}</strong>
                    <small className="down">{((impliedRange.low - currentPrice) / currentPrice * 100).toFixed(1)}%</small>
                  </div>
                </div>
                {edgeUsd !== 0 && (
                  <div className={`tv-range-note ${edgeUsd >= 0 ? 'up' : 'down'}`}>
                    {edgeUsd >= 0 ? `+$${edgeUsd.toFixed(2)} unpriced vs market` : `−$${Math.abs(edgeUsd).toFixed(2)} premium above fair value`}
                  </div>
                )}
              </div>
            )}

            {/* Hormuz scenarios */}
            {hormuzScenarios.length > 0 && (
              <div className="tv-card tv-card-wide">
                <div className="tv-card-label">
                  Strait of Hormuz Disruption Scenarios
                  <span className="muted">~21 mbpd/day · P = illustrative odds · $ impact from EIA/IEA refs</span>
                </div>
                <div className="tv-scenarios">
                  {hormuzScenarios.map((s) => (
                    <div key={s.name} className="tv-scenario">
                      <div className="tv-scenario-head">
                        <span className="tv-scenario-name">{s.name}</span>
                        {s.probability > 0 && <span className="tv-prob">P={Math.round(s.probability * 100)}%</span>}
                      </div>
                      <div className="muted">{s.supply_loss_mbpd} mbpd disrupted</div>
                      <div className="tv-scenario-price">
                        <strong>${s.price_target_mid?.toFixed(0)}</strong>
                        <span className="muted">mid · ${s.price_target_low?.toFixed(0)}–${s.price_target_high?.toFixed(0)}</span>
                      </div>
                      <div className="impact">+${s.price_impact_low}–${s.price_impact_high}/bbl</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

          </div>
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
          predictedArray={data?.predicted || []}
          enterpriseMetrics={data?.enterprise_metrics}
          multiHorizonPredictions={data?.multi_horizon_predictions}
          performanceMetricsByHorizon={data?.performance_metrics?.by_horizon || {}}
          unifiedData={data?.unified_data}
          activeHorizon={resolvedActiveDisplayHorizon}
          onActiveHorizonChange={setActiveDisplayHorizon}
          currentPrice={currentPrice}
          contractInfo={contractInfo}
          priceChange={priceChange}
          priceChangePercent={priceChangePercent}
          displayAccuracy={displayAccuracy}
          displayConfidence={displayConfidence}
          feedStatus={data?.feed_status || 'UNKNOWN'}
        />
      </div>

    </div>
  );
}

export default App;
