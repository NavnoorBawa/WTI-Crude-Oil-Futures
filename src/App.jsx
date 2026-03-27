import React, { useState, useEffect } from "react";
import Chart from "./Chart";

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(new Date());
  const [currentTime, setCurrentTime] = useState(new Date());
  const pollIntervalMs = Number(import.meta.env.VITE_POLL_INTERVAL_MS || 15000);
  const configuredApiBase = import.meta.env.VITE_API_BASE_URL;

  const getApiBaseCandidates = () => {
    const hostname = window.location.hostname;
    const isLocalHost = hostname === "localhost" || hostname === "127.0.0.1";

    if (configuredApiBase) {
      return [configuredApiBase];
    }

    if (isLocalHost) {
      return ["http://127.0.0.1:9000"];
    }

    // Production fallbacks if env var is missing on frontend host.
    return [
      "https://wti-crude-oil-backend.onrender.com",
      "https://wti-crude-oil-futures.onrender.com",
      "https://wti-crude-oil-futures-api.onrender.com",
      window.location.origin,
    ];
  };

  useEffect(() => {
    // Set title
    document.title = "Bloomberg Terminal - WTI Crude Oil";

    // Update time every second
    const timeInterval = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);

    // Fetch data function
    const fetchData = async (isInitial = false) => {
      try {
        if (isInitial) {
          setLoading(true);
          setError(null);
        }
        
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

            if (attempt.ok) {
              response = attempt;
              break;
            }

            lastAttemptError = new Error(`HTTP ${attempt.status}: ${attempt.statusText}`);
          } catch (attemptErr) {
            clearTimeout(timeoutId);
            lastAttemptError = attemptErr;
          }
        }

        if (!response) {
          throw lastAttemptError || new Error("No reachable API endpoint found");
        }

        const result = await response.json();
        
        // Update state in correct order
        setData(result);
        setLastUpdate(new Date());
        setError(null);
        setLoading(false);
        
      } catch (err) {
        if (err.name === 'AbortError') {
          setError('Server timeout - Please wait and refresh');
        } else if (err.name === 'TypeError' && err.message.includes('Failed to fetch')) {
          setError('Cannot connect to backend API - verify VITE_API_BASE_URL in frontend environment');
        } else {
          setError(`Network error: ${err.message}`);
        }
        setLoading(false);
      }
    };

    // Initial fetch
    fetchData(true);

    // Backend prediction cadence is minutes, so lower polling frequency cuts load.
    const interval = setInterval(() => fetchData(false), pollIntervalMs);

    return () => {
      clearInterval(interval);
      clearInterval(timeInterval);
    };
  }, [configuredApiBase, pollIntervalMs]);


  // Loading screen
  if (loading && !data) {
    return (
      <div className="min-h-screen bg-black text-bloomberg-amber font-mono flex items-center justify-center">
        <div className="text-center">
          <div className="text-xl mb-3">BLOOMBERG TERMINAL</div>
          <div className="text-lg mb-4">INITIALIZING MARKET DATA SYSTEMS...</div>
          <div className="text-sm text-gray-500 mt-4">Connecting to Real-Time Data Feeds</div>
        </div>
      </div>
    );
  }

  // Error screen - System designed to fail rather than show placeholder data
  if (error) {
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
  const currentPrice = data?.current_price || 0;
  const currentPrediction = data?.multi_horizon_predictions?.predictions?.['1d'] || 0;  // Use 1D ML prediction
  const priceChange = data?.price_change || 0;
  const priceChangePercent = data?.price_change_percent || 0;
  const contractInfo = data?.contract || { symbol: 'CLV25', description: 'WTI CRUDE OIL FUTURES' };

  const totalEvaluatedPredictions = Number(data?.performance_metrics?.total_predictions || 0);
  const liveDirectionAccuracy = Number(data?.performance_metrics?.direction_accuracy || 0);
  const displayDirectionAccuracyRaw = data?.performance_metrics?.display_direction_accuracy;
  const displayAccuracySource = data?.performance_metrics?.display_accuracy_source || 'unavailable';
  const minLiveSamples = Number(data?.performance_metrics?.min_live_accuracy_samples || 18);
  const modelBacktestDirection1d = data?.multi_horizon_predictions?.horizon_backtests?.['1d']?.direction_accuracy;
  const modelConfidence1d = data?.multi_horizon_predictions?.horizon_confidence?.['1d'];
  const isRealPrediction = Boolean(data?.multi_horizon_predictions?.is_real_prediction);
  const isFullRealPrediction = Boolean(data?.multi_horizon_predictions?.is_full_real_prediction);
  const fallbackHorizons = Object.entries(data?.multi_horizon_predictions?.fallbacks || {})
    .filter(([, used]) => Boolean(used))
    .map(([horizon]) => horizon.toUpperCase());

  const effectiveAccuracy = (displayDirectionAccuracyRaw !== undefined && displayDirectionAccuracyRaw !== null)
    ? Number(displayDirectionAccuracyRaw)
    : (totalEvaluatedPredictions > 0
      ? liveDirectionAccuracy
      : (modelBacktestDirection1d !== undefined && modelBacktestDirection1d !== null
        ? Number(modelBacktestDirection1d)
        : null));

  const displayAccuracy = effectiveAccuracy === null
    ? '--'
    : `${Math.round(effectiveAccuracy)}${displayAccuracySource === 'backtest' ? '%*' : (displayAccuracySource === 'blended' ? '%~' : '%')}`;

  const accuracyClassName = (displayAccuracySource === 'live' || displayAccuracySource === 'blended')
    ? 'text-bloomberg-positive'
    : 'text-bloomberg-blue';

  const fallbackConfidence = data?.performance_metrics?.confidence;
  const displayConfidence = (modelConfidence1d !== undefined && modelConfidence1d !== null)
    ? `${Math.round(modelConfidence1d)}%`
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
              <th className="text-sm">ML PRED</th>
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
                {displayAccuracy}
              </td>
              <td className="text-bloomberg-positive text-sm">
                {displayConfidence}
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
              {(displayAccuracySource !== 'live') && isRealPrediction && (
                <span className="text-bloomberg-blue font-medium">
                  {displayAccuracySource === 'blended'
                    ? `EVAL: BLENDED (${totalEvaluatedPredictions}/${minLiveSamples})`
                    : (displayAccuracySource === 'backtest'
                      ? 'EVAL: BACKTEST'
                      : `EVAL: WARMUP (${totalEvaluatedPredictions}/${minLiveSamples})`)}
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
          unifiedData={data?.unified_data}
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
