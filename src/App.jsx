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
  const latestDataRef = useRef(null);
  const requestInFlightRef = useRef(false);
  const startupRetryPendingRef = useRef(false);

  latestDataRef.current = data;

  const getApiBaseCandidates = () => {
    const hostname = window.location.hostname;
    const isLocalHost = hostname === "localhost" || hostname === "127.0.0.1";

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
    document.title = "Bloomberg Terminal - WTI Crude Oil";
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
            const attempt = await fetch(`${apiBase}/data`, {
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
      <div className="min-h-screen bg-black text-bloomberg-amber font-mono flex items-center justify-center">
        <div className="text-center">
          <div className="text-xl mb-3">BLOOMBERG TERMINAL</div>
          <div className="text-lg mb-4">INITIALIZING MARKET DATA SYSTEMS...</div>
          <div className="text-sm text-gray-500 mt-4">{loadingMessage}</div>
        </div>
      </div>
    );
  }

  // Error screen - System designed to fail rather than show placeholder data
  if (error && !data) {
    return (
      <div className="min-h-screen bg-black text-bloomberg-amber font-mono flex items-center justify-center">
        <div className="text-center">
          <div className="bg-bloomberg-red text-white px-6 py-3 mb-4 text-base">
            REAL DATA CONNECTION FAILURE
          </div>
          <div className="text-xl mb-4 text-bloomberg-red">SYSTEM CANNOT OPERATE WITHOUT REAL DATA</div>
          <div className="text-gray-400 text-base">ERROR: {error}</div>
          <div className="text-sm text-gray-500 mt-3">
            System is configured to fail rather than use placeholder data
          </div>
          <div className="text-sm text-gray-400 mt-2">
            ❌ NO FALLBACK DATA | ✅ REAL DATA ONLY
          </div>
        </div>
      </div>
    );
  }
  
  // Check if data indicates system error
  if (data?.error) {
    return (
      <div className="min-h-screen bg-black text-bloomberg-amber font-mono flex items-center justify-center">
        <div className="text-center">
          <div className="bg-bloomberg-red text-white px-6 py-3 mb-4 text-base">
            SYSTEM ERROR - REAL DATA UNAVAILABLE
          </div>
          <div className="text-xl mb-4 text-bloomberg-red">NO PLACEHOLDER DATA ALLOWED</div>
          <div className="text-gray-400 text-base">ERROR: {data.error}</div>
          <div className="text-sm text-gray-500 mt-3">{data.message}</div>
          <div className="text-sm text-gray-400 mt-2">
            System Status: {data.system_status || 'ERROR'}
          </div>
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
    geoRegime === 'CRITICAL' ? '#ff0000' :
    geoRegime === 'HIGH'     ? '#ff6600' :
    geoRegime === 'ELEVATED' ? '#ffaa00' :
    geoRegime === 'LOW'      ? '#00cc44' : '#666';

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
    <div className="min-h-screen bg-black text-bloomberg-amber font-mono" style={{ display: 'flex', flexDirection: 'column' }}>
      {/* BLOOMBERG TERMINAL HEADER */}
      <div className="bloomberg-titlebar">
        BLOOMBERG PROFESSIONAL
      </div>

      {/* BLOOMBERG STATUS BAR */}
      <div className="bg-black p-2 flex justify-between items-center border-b border-bloomberg-amber">
        <div className="flex items-center gap-6 text-sm">
          <div className="text-bloomberg-amber">LIVE</div>
          <div className="text-white">
            {currentTime.toLocaleTimeString('en-US', { 
              hour12: false, 
              timeZone: 'America/Chicago' 
            })} CT (CME)
          </div>
          <div className="text-gray-400">
            UPDATED {Math.floor((currentTime - lastUpdate) / 1000)}s
          </div>
        </div>
        <div className="text-gray-400 text-sm">
          USER: PROFESSIONAL
        </div>
      </div>

      {/* BLOOMBERG COMMAND LINE */}
      <div className="bg-black border-b border-bloomberg-amber p-2">
        <div className="flex items-center gap-4 text-sm">
          <span className="text-bloomberg-amber">COMMAND:</span>
          <span className="bg-bloomberg-alert text-black px-2 py-1 font-bold">{contractInfo.symbol || 'CLV25'}</span>
          <span className="text-bloomberg-amber">&lt;COMDTY&gt;</span>
          <span className="bg-bloomberg-alert text-black px-2 py-1 font-bold">GP</span>
          <span className="text-bloomberg-amber">&lt;GO&gt;</span>
          <span className="bloomberg-cursor"></span>
          <span className="text-gray-400 ml-4">{contractInfo.description || 'WTI CRUDE OIL FUTURES NYMEX'}</span>
        </div>
      </div>

      {error && data && (
        <div className="bg-black border-b border-bloomberg-red px-3 py-2 text-sm">
          <span className="text-bloomberg-red font-semibold">LIVE SNAPSHOT WARNING:</span>
          <span className="text-gray-300 ml-2">{error}</span>
        </div>
      )}

      {/* BLOOMBERG TERMINAL DATA DASHBOARD */}
      <div className="bg-black border-b border-gray-700 p-2">
        {/* BLOOMBERG DATA TABLE - ALL REAL VALUES */}
        <table className="bloomberg-table w-full text-sm">
          <thead>
            <tr>
              <th className="text-left text-sm">SECURITY</th>
              <th className="text-sm">LAST</th>
              <th className="text-sm">CHG</th>
              <th className="text-sm">%CHG</th>
              <th className="text-sm">VOL</th>
              <th className="text-sm">ML PRED {activeHorizonLabel}</th>
              <th className="text-sm">ACCURACY</th>
              <th className="text-sm">CONFIDENCE</th>
            </tr>
          </thead>
          <tbody>
            <tr className="price-row">
              <td className="text-bloomberg-amber text-left text-sm">
                {contractInfo.security_name || `${contractInfo.symbol} WTI CRUDE`}
              </td>
              <td className="text-white font-bold text-lg">
                {currentPrice > 0 ? currentPrice.toFixed(2) : '--'}
              </td>
              <td className={`text-sm ${priceChange >= 0 ? 'price-up' : 'price-down'}`}>
                {currentPrice > 0 ? `${priceChange >= 0 ? '+' : ''}${priceChange.toFixed(3)}` : '--'}
              </td>
              <td className={`text-sm ${priceChange >= 0 ? 'price-up' : 'price-down'}`}>
                {currentPrice > 0 ? `${priceChangePercent >= 0 ? '+' : ''}${priceChangePercent.toFixed(2)}%` : '--'}
              </td>
              <td className="text-bloomberg-blue text-sm">
                {data?.volume_display || 'N/A'}
              </td>
              <td className="text-bloomberg-cyan text-sm">
                {currentPrediction > 0 ? currentPrediction.toFixed(2) : '--'}
              </td>
              <td className={`text-sm ${accuracyClassName}`}>
                {wfIsSignificant === false
                  ? 'BELOW RND'
                  : displayAccuracy}
                {wfCi95 && wfIsSignificant === true && (
                  <span className="text-gray-500 text-xs ml-1">[{wfCi95[0]}-{wfCi95[1]}%]</span>
                )}
              </td>
              <td className="text-bloomberg-positive text-sm">
                {wfIsSignificant === true && wfMaeImprovement !== null
                  ? `MAE +${wfMaeImprovement.toFixed(1)}%`
                  : displayConfidence}
              </td>
            </tr>
          </tbody>
        </table>

        {/* SYSTEM STATUS BAR - ALL REAL VALUES */}
        <div className="bg-black p-2 mt-2">
          <div className="flex justify-between items-center text-sm">
            <div className="flex gap-6">
              <span className="text-bloomberg-amber font-medium">DATA POINTS:</span>
              <span className="text-white font-medium">
                {data?.enterprise_metrics?.data_points || '--'}
              </span>
              <span className="text-bloomberg-amber font-medium">FEED:</span>
              <span className={`font-medium ${
                data?.feed_status === 'REAL-TIME' ? 'text-bloomberg-positive' : 'text-bloomberg-red'
              }`}>
                {data?.feed_status || 'UNKNOWN'}
              </span>
              <span className="text-bloomberg-amber font-medium">NEXT ML:</span>
              <span className="text-bloomberg-blue font-medium">
                {data?.ml_prediction_timer?.minutes_remaining !== undefined && data?.ml_prediction_timer?.seconds_remaining !== undefined ? 
                  `${data.ml_prediction_timer.minutes_remaining}:${String(data.ml_prediction_timer.seconds_remaining).padStart(2, '0')}` : 
                  '--:--'}
              </span>
              <span className="text-bloomberg-amber font-medium">STATUS:</span>
              <span className={`font-medium ${
                data?.ml_prediction_timer?.currently_processing ? 'text-bloomberg-alert animate-pulse' :
                isFullRealPrediction ? 'text-bloomberg-positive' :
                isRealPrediction ? 'text-bloomberg-alert' :
                'text-bloomberg-red'
              }`}>
                {data?.ml_prediction_timer?.currently_processing ? 'PROCESSING' :
                 isFullRealPrediction ? 'REAL ML' :
                 isRealPrediction ? 'PARTIAL ML' :
                 'NO REAL DATA'}
              </span>
              {wfIsSignificant === true && wfPValue !== null && (
                <>
                  <span className="font-medium text-bloomberg-positive">
                    {activeHorizonLabel} WF: {Math.round(effectiveAccuracy)}% DIR | p={wfPValue < 0.001 ? '<0.001' : wfPValue?.toFixed(4)} | n={wfSamples} ✓
                  </span>
                  {wfSharpe !== null && (
                    <span className="font-medium text-bloomberg-cyan">
                      SHARPE {wfSharpe?.toFixed(2)} | E[PnL] ${wfMeanPnl?.toFixed(0)}/trade | WR {wfWinRate?.toFixed(0)}%
                    </span>
                  )}
                </>
              )}
              {wfIsSignificant === false && (
                <span className="font-medium text-bloomberg-negative">
                  {activeHorizonLabel}: BELOW RANDOM (p={wfPValue?.toFixed(2)}, n={wfSamples}) — not for directional use
                </span>
              )}
              {wfIsSignificant === null && (headlineQualityStatus === 'QUALIFIED' || headlineQualityStatus === 'WATCH') && (
                <span className={`font-medium ${headlineQualityStatus === 'QUALIFIED' ? 'text-bloomberg-positive' : 'text-bloomberg-alert'}`}>
                  QUAL {activeHorizonLabel}: {headlineQualityStatus}
                </span>
              )}
              {geoNoveltySpike && (
                <span className="font-bold text-bloomberg-red animate-pulse">
                  ⚡ BREAKING GEO NEWS
                </span>
              )}
              {(displayAccuracySource !== 'live') && isRealPrediction && (
                <span className="text-bloomberg-blue font-medium">
                  {displayAccuracySource === 'backtest'
                    ? `EVAL: BACKTEST ${activeHorizonLabel}`
                    : `EVAL: WARMUP ${activeHorizonLabel} (${totalEvaluatedPredictions}/${minLiveSamples})`}
                </span>
              )}
              {headlineQualityReasons.length > 0 && (
                <span className="text-gray-400 font-medium">
                  {headlineQualityReasons.join(', ')}
                </span>
              )}
              {fallbackHorizons.length > 0 && (
                <span className="text-bloomberg-orange font-medium">
                  FALLBACKS: {fallbackHorizons.join(', ')}
                </span>
              )}
            </div>
            <div className="flex gap-6">
              {(() => {
                const predictions = data?.multi_horizon_predictions?.predictions;
                const percentChanges = data?.multi_horizon_predictions?.percentage_changes;
                
                // Only show real calculated values, no fallback
                if (!predictions || !percentChanges || !isRealPrediction) {
                  return [
                    { period: '1H', value: '--' },
                    { period: '1D', value: '--' },
                    { period: '1W', value: '--' }
                  ].map(({ period, value }) => (
                    <span key={period}>
                      <span className="text-bloomberg-amber font-medium">{period}:</span>
                      <span className="text-gray-400 font-medium">{value}%</span>
                    </span>
                  ));
                }
                
                const horizons = ['1h', '1d', '1w'];
                const labels = ['1H', '1D', '1W'];
                
                return horizons.map((horizon, i) => {
                  const change = percentChanges[horizon];
                  if (change !== undefined && change !== null) {
                    const isPositive = change >= 0;
                    const colorClass = isPositive ? 'text-bloomberg-positive' : 'text-bloomberg-negative';
                    return (
                      <span key={horizon}>
                        <span className="text-bloomberg-amber font-medium">{labels[i]}:</span>
                        <span className={`${colorClass} font-medium`}>
                          {isPositive ? '+' : ''}{change.toFixed(1)}%
                        </span>
                      </span>
                    );
                  }
                  return (
                    <span key={horizon}>
                      <span className="text-bloomberg-amber font-medium">{labels[i]}:</span>
                      <span className="text-gray-400 font-medium">--%</span>
                    </span>
                  );
                });
              })()}
            </div>
          </div>
        </div>
      </div>

      {/* GEOPOLITICAL RISK + SCENARIO ENGINE PANEL */}
      {data && (
        <div className="bg-black border-b border-gray-700 text-sm">

          {/* ML caveat banner */}
          {mlCaveat && (
            <div className="bg-bloomberg-red text-white text-xs px-3 py-1 font-medium">
              ⚠ {mlCaveat}
            </div>
          )}

          {/* ROW 1: Risk index | Signal | Analogues */}
          <div className="flex border-b border-gray-800" style={{ minHeight: 80 }}>

            {/* GEO RISK INDEX */}
            <div className="flex flex-col justify-center px-3 py-2 border-r border-gray-700" style={{ minWidth: 170 }}>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-bloomberg-amber font-medium text-xs">GEO RISK INDEX</span>
                {geoNoveltySpike && (
                  <span className="text-bloomberg-red text-xs font-bold animate-pulse">⚡ BREAKING</span>
                )}
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-white font-bold text-2xl">{geoScore.toFixed(0)}</span>
                <span className="text-gray-500 text-xs">/100</span>
                <span className="font-bold text-sm" style={{ color: geoScoreBarColor }}>{geoRegime}</span>
              </div>
              <div className="w-full bg-gray-800 h-2 mt-1 rounded">
                <div className="h-2 rounded" style={{ width: `${Math.min(100, geoScore)}%`, backgroundColor: geoScoreBarColor }} />
              </div>
              <div className="flex gap-3 mt-1">
                {Object.entries(geoBreakdown).filter(([,v]) => v > 0).map(([cat, count]) => (
                  <span key={cat} className="text-xs">
                    <span className="text-gray-500 uppercase">{cat[0]}</span>
                    <span className="text-white ml-0.5">{count}</span>
                  </span>
                ))}
                {geoRecent24h > 0 && (
                  <span className="text-xs">
                    <span className="text-gray-500">24H</span>
                    <span className="text-bloomberg-alert ml-0.5 font-bold">{geoRecent24h}</span>
                  </span>
                )}
              </div>
            </div>

            {/* GEO SIGNAL */}
            <div className="flex flex-col justify-center px-3 py-2 border-r border-gray-700" style={{ minWidth: 230 }}>
              <div className="text-bloomberg-amber font-medium text-xs mb-1">GEO TRADE SIGNAL</div>
              <div className="flex items-baseline gap-3">
                <span className={`font-bold text-xl ${
                  geoSignal.signal === 'LONG_BIAS' ? 'text-bloomberg-positive' :
                  geoSignal.signal === 'WATCH'     ? 'text-bloomberg-alert' : 'text-gray-400'
                }`}>
                  {geoSignal.signal || 'NEUTRAL'}
                </span>
                {signalStrength > 0 && (
                  <span className="text-gray-500 text-xs">STR {signalStrength}/100</span>
                )}
              </div>
              {geoSignal.strait_risk && (
                <div className="text-bloomberg-red text-xs font-bold mt-0.5">⚑ HORMUZ RISK ACTIVE</div>
              )}
              <div className="flex gap-4 mt-1.5">
                {evImpactUsd > 0 && (
                  <div className="flex flex-col">
                    <span className="text-gray-500 text-xs">EV IMPACT</span>
                    <span className="text-bloomberg-positive font-bold text-sm">+${evImpactUsd.toFixed(1)}/bbl</span>
                  </div>
                )}
                {edgeUsd !== 0 && (
                  <div className="flex flex-col">
                    <span className="text-gray-500 text-xs">UNPRICED EDGE</span>
                    <span className={`font-bold text-sm ${edgeUsd >= 0 ? 'text-bloomberg-positive' : 'text-bloomberg-negative'}`}>
                      {edgeUsd >= 0 ? '+' : ''}${edgeUsd.toFixed(2)} ({edgePct >= 0 ? '+' : ''}{edgePct.toFixed(1)}%)
                    </span>
                  </div>
                )}
                {expectedResolutionDays > 0 && (
                  <div className="flex flex-col">
                    <span className="text-gray-500 text-xs">AVG DURATION</span>
                    <span className="text-bloomberg-blue font-bold text-sm">{expectedResolutionDays}d</span>
                  </div>
                )}
              </div>
            </div>

            {/* HISTORICAL ANALOGUES */}
            <div className="flex flex-col justify-center px-3 py-2 flex-1 border-r border-gray-700">
              <div className="text-bloomberg-amber font-medium text-xs mb-1">CLOSEST HISTORICAL ANALOGUES</div>
              <div className="flex gap-4">
                {topAnalogues.slice(0, 2).map((a, i) => (
                  <div key={a.id} className="flex flex-col border-l-2 pl-2" style={{ borderColor: i === 0 ? '#ffaa00' : '#555', minWidth: 220 }}>
                    <div className="text-white text-xs font-medium leading-tight">
                      {a.date} · {a.event.length > 45 ? a.event.slice(0, 45) + '…' : a.event}
                    </div>
                    <div className="flex gap-3 text-xs mt-0.5">
                      <span className="text-bloomberg-positive font-bold">Peak +{a.peak_pct}%</span>
                      <span className={`font-medium ${a.settled_pct >= 0 ? 'text-bloomberg-positive' : 'text-bloomberg-negative'}`}>
                        Settled {a.settled_pct >= 0 ? '+' : ''}{a.settled_pct}%
                      </span>
                      <span className="text-gray-500">{a.duration_days}d</span>
                    </div>
                    <div className="text-gray-500 text-xs mt-0.5 leading-tight">
                      {(a.notes || '').slice(0, 55)}{(a.notes || '').length > 55 ? '…' : ''}
                    </div>
                  </div>
                ))}
                {topAnalogues.length === 0 && <div className="text-gray-500 text-xs">Awaiting data</div>}
              </div>
            </div>

            {/* LATEST HEADLINES */}
            {geoHeadlines.length > 0 && (
              <div className="flex flex-col justify-center px-3 py-2" style={{ minWidth: 300 }}>
                <div className="text-bloomberg-amber font-medium text-xs mb-1">
                  LIVE HEADLINES
                  {geoRecent24h > 0 && (
                    <span className="text-bloomberg-alert ml-2">{geoRecent24h} in last 24h</span>
                  )}
                </div>
                {geoHeadlines.slice(0, 3).map((h, i) => (
                  <div key={i} className="flex gap-2 text-xs mb-0.5 items-start">
                    {h.is_breaking ? (
                      <span className="text-bloomberg-red font-bold shrink-0 w-14">BRKNG</span>
                    ) : (
                      <span className="text-gray-600 shrink-0 w-14">
                        {h.published_at ? new Date(h.published_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '--'}
                      </span>
                    )}
                    <span className="text-gray-300 truncate" style={{ maxWidth: 230 }}>{h.headline}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ROW 2: Implied range | Hormuz scenarios */}
          {(impliedRange.high || hormuzScenarios.length > 0) && (
            <div className="flex">

              {/* ANALOGUE-IMPLIED PRICE RANGE */}
              {impliedRange.high && (
                <div className="flex flex-col justify-center px-3 py-2 border-r border-gray-700" style={{ minWidth: 280 }}>
                  <div className="text-bloomberg-amber font-medium text-xs mb-1">ANALOGUE-IMPLIED PRICE RANGE</div>
                  <div className="flex gap-6">
                    <div className="flex flex-col">
                      <span className="text-gray-500 text-xs">BULL CASE</span>
                      <span className="text-bloomberg-positive font-bold text-lg">${impliedRange.high?.toFixed(2)}</span>
                      <span className="text-gray-500 text-xs">+{((impliedRange.high - currentPrice) / currentPrice * 100).toFixed(1)}%</span>
                    </div>
                    <div className="flex flex-col">
                      <span className="text-gray-500 text-xs">BASE CASE</span>
                      <span className="text-white font-bold text-lg">${impliedRange.mid?.toFixed(2)}</span>
                      <span className={`text-xs ${impliedRange.mid >= currentPrice ? 'text-bloomberg-positive' : 'text-bloomberg-negative'}`}>
                        {impliedRange.mid >= currentPrice ? '+' : ''}{((impliedRange.mid - currentPrice) / currentPrice * 100).toFixed(1)}%
                      </span>
                    </div>
                    <div className="flex flex-col">
                      <span className="text-gray-500 text-xs">BEAR CASE</span>
                      <span className="text-bloomberg-negative font-bold text-lg">${impliedRange.low?.toFixed(2)}</span>
                      <span className="text-bloomberg-negative text-xs">{((impliedRange.low - currentPrice) / currentPrice * 100).toFixed(1)}%</span>
                    </div>
                  </div>
                  <div className="text-gray-600 text-xs mt-1">{impliedRange.basis}</div>
                  {edgeUsd !== 0 && (
                    <div className={`text-xs mt-1 font-medium ${edgeUsd >= 0 ? 'text-bloomberg-alert' : 'text-bloomberg-negative'}`}>
                      {edgeUsd >= 0
                        ? `+$${edgeUsd.toFixed(2)} unpriced vs current market`
                        : `−$${Math.abs(edgeUsd).toFixed(2)} premium above analogue fair value`}
                    </div>
                  )}
                </div>
              )}

              {/* STRAIT OF HORMUZ SCENARIOS */}
              {hormuzScenarios.length > 0 && (
                <div className="flex flex-col justify-center px-3 py-2 flex-1">
                  <div className="text-bloomberg-amber font-medium text-xs mb-1">
                    STRAIT OF HORMUZ DISRUPTION SCENARIOS  ·  ~21 mbpd transits daily
                    <span className="text-gray-600 ml-2 font-normal">
                      P = illustrative escalation odds · $ impact from EIA/IEA elasticity refs
                    </span>
                  </div>
                  <div className="flex gap-3">
                    {hormuzScenarios.map((s) => (
                      <div key={s.name} className="flex flex-col border border-gray-700 px-2 py-1.5" style={{ minWidth: 150 }}>
                        <div className="flex items-center justify-between">
                          <span className="text-bloomberg-cyan text-xs font-bold">{s.name}</span>
                          {s.probability > 0 && (
                            <span className="text-bloomberg-alert text-xs font-bold">P={Math.round(s.probability * 100)}%</span>
                          )}
                        </div>
                        <div className="text-gray-500 text-xs">{s.supply_loss_mbpd} mbpd disrupted</div>
                        <div className="flex gap-1 items-baseline mt-0.5">
                          <span className="text-bloomberg-positive font-bold text-sm">${s.price_target_mid?.toFixed(0)}</span>
                          <span className="text-gray-500 text-xs">mid</span>
                        </div>
                        <div className="text-gray-400 text-xs">${s.price_target_low?.toFixed(0)}–${s.price_target_high?.toFixed(0)}</div>
                        <div className="text-bloomberg-positive text-xs">+${s.price_impact_low}–${s.price_impact_high}/bbl</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

            </div>
          )}
        </div>
      )}

      {/* BLOOMBERG MAIN CHART DISPLAY */}
      <div className="bloomberg-window" style={{
        flex: '1 1 auto',
        minHeight: '680px',
        height: 'calc(100vh - 150px)',
        borderTop: '1px solid var(--bloomberg-amber)',
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
