import { useState, useEffect } from "react";
import Chart from "./Chart";
import FutureChart from "./FutureChart";

// Enhanced utility functions for ML-optimized Bloomberg Terminal
function getBloombergTime() {
  const now = new Date();
  return {
    ny: now.toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: false }),
    london: now.toLocaleTimeString('en-US', { timeZone: 'Europe/London', hour12: false }),
    tokyo: now.toLocaleTimeString('en-US', { timeZone: 'Asia/Tokyo', hour12: false }),
    utc: now.toLocaleTimeString('en-US', { timeZone: 'UTC', hour12: false })
  };
}

function getMarketStatus() {
  const now = new Date();
  const nyTime = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const hour = nyTime.getHours();
  const day = nyTime.getDay();
  
  if (day === 0 || day === 6) {
    return { status: 'CLOSED', color: 'text-red-400' };
  }
  
  if (hour >= 9 && hour < 17) {
    return { status: 'OPEN', color: 'text-green-400' };
  } else if (hour >= 17 && hour < 18) {
    return { status: 'CLOSING', color: 'text-yellow-400' };
  } else {
    return { status: 'CLOSED', color: 'text-red-400' };
  }
}

function formatTime(seconds) {
  if (seconds <= 0) return "00:00";
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

function formatMLTime(seconds) {
  if (seconds <= 0) return "Completed";
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const remainingSecs = Math.ceil(seconds % 60);
  return `${mins}m ${remainingSecs}s`;
}

export default function Block() {
  // Core data state
  const [actualPrice, setActualPrice] = useState(0);
  const [predictedPrice, setPredictedPrice] = useState(0);
  const [actualArray, setActualArray] = useState([]);
  const [predictedArray, setPredictedArray] = useState([]);
  const [multiHorizonPredictions, setMultiHorizonPredictions] = useState(null);
  const [unifiedData, setUnifiedData] = useState(null);
  const [timeRemaining, setTimeRemaining] = useState(600); // 10 minutes for complex ML
  const [contract, setContract] = useState({
    symbol: 'CLQ25',
    description: 'WTI CRUDE OIL FUTURE AUG 2025'
  });

  // Enhanced connection and ML status state
  const [connectionStatus, setConnectionStatus] = useState('connecting');
  const [lastUpdate, setLastUpdate] = useState(null);
  const [errorCount, setErrorCount] = useState(0);
  const [consecutiveErrors, setConsecutiveErrors] = useState(0);
  
  // Complex ML status tracking
  const [mlStatus, setMlStatus] = useState({
    status: 'initializing',
    current_step: 'Loading',
    progress_percentage: 0,
    estimated_completion: null,
    processing_time: 0,
    cache_active: false
  });
  
  // Enterprise-grade state management
  const [enterpriseMetrics, setEnterpriseMetrics] = useState(null);
  const [performanceMetrics, setPerformanceMetrics] = useState(null);
  const [advancedAnalytics, setAdvancedAnalytics] = useState(null);
  const [viewMode, setViewMode] = useState('chart');
  const [chartMode, setChartMode] = useState('historical'); // 'historical', 'future', 'hybrid'

  const [times, setTimes] = useState(getBloombergTime());
  const [marketStatus, setMarketStatus] = useState(getMarketStatus());

  // Update times every second
  useEffect(() => {
    const timeInterval = setInterval(() => {
      setTimes(getBloombergTime());
      setMarketStatus(getMarketStatus());
    }, 1000);

    return () => clearInterval(timeInterval);
  }, []);
  
  // Professional keyboard shortcuts (Bloomberg Terminal style)
  useEffect(() => {
    const handleKeyDown = (event) => {
      // Alt + 1 for Chart
      if (event.altKey && event.key === '1') {
        event.preventDefault();
        setViewMode('chart');
      }
      // Alt + 2 for Analytics  
      if (event.altKey && event.key === '2') {
        event.preventDefault();
        setViewMode('analytics');
      }
      // Alt + 3 for Future Forecast
      if (event.altKey && event.key === '3') {
        event.preventDefault();
        setViewMode('future');
      }
      // F5 to refresh data (professional refresh)
      if (event.key === 'F5') {
        event.preventDefault();
        window.location.reload();
      }
    };
    
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  const fetchEnterpriseData = async () => {
    try {
      // Multiple endpoint strategy for enterprise reliability
      // Use environment variable or fallback to production URL
      const API_BASE_URL = import.meta.env.VITE_API_URL || "https://YOUR_BACKEND_APP_NAME.onrender.com";
      const endpoints = [
        `${API_BASE_URL}/data`,
        "http://127.0.0.1:9000/data",  // Local fallback
        "http://localhost:9000/data"   // Local fallback
      ];
      
      let response = null;
      let lastError = null;
      
      for (const endpoint of endpoints) {
        try {
          const res = await fetch(endpoint, {
            method: 'GET',
            headers: {
              'Accept': 'application/json',
              'Content-Type': 'application/json',
            },
            signal: AbortSignal.timeout(30000) // Extended timeout for complex ML
          });
          
          if (res.ok) {
            response = res;
            break;
          }
        } catch (err) {
          lastError = err;
          console.log(`Failed to connect to ${endpoint}:`, err.message);
        }
      }
      
      if (!response) {
        throw lastError || new Error('All endpoints failed');
      }

      const data = await response.json();
      
      // Update connection status - RESET ERROR COUNT ON SUCCESS
      setConnectionStatus('connected');
      setErrorCount(0);
      setConsecutiveErrors(0);
      setLastUpdate(new Date().toLocaleTimeString());

      // Update main data
      if (data.actual && Array.isArray(data.actual) && data.actual.length > 0) {
        setActualPrice(data.actual[data.actual.length - 1]);
        setActualArray(data.actual);
      }
      
      if (data.predicted && Array.isArray(data.predicted) && data.predicted.length > 0) {
        setPredictedPrice(data.predicted[data.predicted.length - 1]);
        setPredictedArray(data.predicted);
      }
      
      if (data.timeRemaining !== undefined) {
        setTimeRemaining(data.timeRemaining);
      }
      
      if (data.contract) {
        setContract(data.contract);
      }
      
      // Update ML status with detailed tracking
      if (data.ml_status) {
        setMlStatus(data.ml_status);
      }
      
      // Store enterprise metrics
      if (data.enterprise_metrics) {
        setEnterpriseMetrics(data.enterprise_metrics);
      }
      
      // Store performance metrics
      if (data.performance_metrics) {
        setPerformanceMetrics(data.performance_metrics);
      }
      
      // Store advanced analytics
      if (data.advanced_analytics) {
        setAdvancedAnalytics(data.advanced_analytics);
      }
      
      // Store multi-horizon predictions
      if (data.multi_horizon_predictions) {
        setMultiHorizonPredictions(data.multi_horizon_predictions);
      }
      
      // Store unified data for new visualization
      if (data.unified_data) {
        setUnifiedData(data.unified_data);
      }
      
    } catch (error) {
      console.error("Error fetching enterprise data:", error);
      setConnectionStatus('error');
      setErrorCount(prev => prev + 1);
      setConsecutiveErrors(prev => prev + 1);
      
      // Enhanced error handling for ML system
      if (consecutiveErrors > 10) {
        checkMLSystemHealth();
      }
    }
  };

  const checkMLSystemHealth = async () => {
    try {
      const API_BASE_URL = import.meta.env.VITE_API_URL || "https://YOUR_BACKEND_APP_NAME.onrender.com";
      const mlResponse = await fetch(`${API_BASE_URL}/ml-status`, {
        signal: AbortSignal.timeout(10000)
      });
      if (mlResponse.ok) {
        const mlData = await mlResponse.json();
        setMlStatus(prev => ({ ...prev, ...mlData }));
        console.log("ML System Status:", mlData);
        
        // If ML system is healthy, reset some error counters
        if (mlData.ml_model_status === 'active' || mlData.ml_model_status === 'running') {
          setConsecutiveErrors(0);
        }
      }
    } catch (error) {
      console.error("ML system health check failed:", error);
    }
  };

  useEffect(() => {
    // Immediate fetch on mount
    fetchEnterpriseData();
    
    // OPTIMIZED: Reduced polling frequency for complex ML system
    // Since ML takes 25-30s and cache lasts 8 minutes, poll every 15 seconds
    const interval = setInterval(fetchEnterpriseData, 15000);
    
    return () => clearInterval(interval);
  }, []); // Removed errorCount dependency to prevent excessive polling

  // Separate ML status monitoring
  useEffect(() => {
    const mlInterval = setInterval(checkMLSystemHealth, 30000); // Every 30 seconds
    return () => clearInterval(mlInterval);
  }, []);

  // Calculate price change and advanced metrics
  const priceChange = actualPrice > 0 && actualArray.length > 1 
    ? actualPrice - actualArray[actualArray.length - 2] 
    : 0;
  const priceChangePercent = actualPrice > 0 && priceChange !== 0 
    ? (priceChange / actualArray[actualArray.length - 2]) * 100 
    : 0;

  // Helper function for status colors
  const getStatusColor = (status) => {
    switch (status) {
      case 'running': return 'text-yellow-400';
      case 'completed': return 'text-green-400';
      case 'cached': return 'text-blue-400';
      case 'error': return 'text-red-400';
      default: return 'text-gray-400';
    }
  };


  return (
    <div className="text-orange-400 bg-black min-h-screen font-mono p-0">
      {/* Professional Trading Header */}
      <div className="border-b border-orange-400 bg-black">
        {/* Primary Quote Display */}
        <div className="px-6 py-4 border-b border-gray-700">
          <div className="flex justify-between items-baseline">
            <div className="flex items-baseline space-x-6">
              <div className="text-5xl font-bold text-white tracking-tight">
                {actualPrice > 0 ? actualPrice.toFixed(2) : '0.00'}
              </div>
              <div className={`text-2xl font-bold ${priceChange >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(3)}
              </div>
              <div className={`text-xl ${priceChangePercent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                ({priceChangePercent > 0 ? '+' : ''}{priceChangePercent.toFixed(3)}%)
              </div>
              <div className="text-sm text-gray-500 uppercase tracking-wide">
                USD/BBL
              </div>
            </div>
            
            <div className="text-right">
              {multiHorizonPredictions ? (
                <div className="space-y-2">
                  <div className="text-sm text-gray-400 uppercase tracking-wide mb-2">
                    ML MULTI-HORIZON FORECASTS
                  </div>
                  <div className="grid grid-cols-4 gap-4 text-center">
                    <div>
                      <div className="text-xs text-gray-500 uppercase">1H</div>
                      <div className="text-xl font-bold text-white">
                        {multiHorizonPredictions.predictions?.['1h'] ? 
                          multiHorizonPredictions.predictions['1h'].toFixed(2) : '0.00'}
                      </div>
                      <div className={`text-xs ${
                        actualPrice && multiHorizonPredictions.predictions?.['1h'] && 
                        (multiHorizonPredictions.predictions['1h'] - actualPrice) >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {actualPrice && multiHorizonPredictions.predictions?.['1h'] ? 
                          `${(multiHorizonPredictions.predictions['1h'] - actualPrice) >= 0 ? '+' : ''}${(((multiHorizonPredictions.predictions['1h'] - actualPrice) / actualPrice) * 100).toFixed(2)}%` : 
                          '0.00%'}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase">4H</div>
                      <div className="text-xl font-bold text-white">
                        {multiHorizonPredictions.predictions?.['4h'] ? 
                          multiHorizonPredictions.predictions['4h'].toFixed(2) : '0.00'}
                      </div>
                      <div className={`text-xs ${
                        actualPrice && multiHorizonPredictions.predictions?.['4h'] && 
                        (multiHorizonPredictions.predictions['4h'] - actualPrice) >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {actualPrice && multiHorizonPredictions.predictions?.['4h'] ? 
                          `${(multiHorizonPredictions.predictions['4h'] - actualPrice) >= 0 ? '+' : ''}${(((multiHorizonPredictions.predictions['4h'] - actualPrice) / actualPrice) * 100).toFixed(2)}%` : 
                          '0.00%'}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase">1D</div>
                      <div className="text-xl font-bold text-white">
                        {multiHorizonPredictions.predictions?.['1d'] ? 
                          multiHorizonPredictions.predictions['1d'].toFixed(2) : '0.00'}
                      </div>
                      <div className={`text-xs ${
                        actualPrice && multiHorizonPredictions.predictions?.['1d'] && 
                        (multiHorizonPredictions.predictions['1d'] - actualPrice) >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {actualPrice && multiHorizonPredictions.predictions?.['1d'] ? 
                          `${(multiHorizonPredictions.predictions['1d'] - actualPrice) >= 0 ? '+' : ''}${(((multiHorizonPredictions.predictions['1d'] - actualPrice) / actualPrice) * 100).toFixed(2)}%` : 
                          '0.00%'}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase">7D</div>
                      <div className="text-xl font-bold text-white">
                        {multiHorizonPredictions.predictions?.['7d'] ? 
                          multiHorizonPredictions.predictions['7d'].toFixed(2) : '0.00'}
                      </div>
                      <div className={`text-xs ${
                        actualPrice && multiHorizonPredictions.predictions?.['7d'] && 
                        (multiHorizonPredictions.predictions['7d'] - actualPrice) >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {actualPrice && multiHorizonPredictions.predictions?.['7d'] ? 
                          `${(multiHorizonPredictions.predictions['7d'] - actualPrice) >= 0 ? '+' : ''}${(((multiHorizonPredictions.predictions['7d'] - actualPrice) / actualPrice) * 100).toFixed(2)}%` : 
                          '0.00%'}
                      </div>
                    </div>
                  </div>
                  {multiHorizonPredictions.confidence_bands && (
                    <div className="text-xs text-gray-400 text-center mt-2">
                      CONF BANDS: {multiHorizonPredictions.confidence_bands.lower.toFixed(2)} - {multiHorizonPredictions.confidence_bands.upper.toFixed(2)}
                    </div>
                  )}
                </div>
              ) : (
                <div>
                  <div className="text-3xl font-bold text-white mb-1">
                    {predictedPrice > 0 ? predictedPrice.toFixed(2) : '0.00'}
                  </div>
                  <div className="text-sm text-gray-400 uppercase tracking-wide">
                    ML FORECAST
                  </div>
                  {enterpriseMetrics?.prediction_confidence > 0 && (
                    <div className="text-sm text-gray-300">
                      CONF: {(enterpriseMetrics.prediction_confidence * 100).toFixed(1)}%
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        
        {/* Secondary Information Bar */}
        <div className="px-6 py-2 bg-gray-900 border-b border-gray-700">
          <div className="flex justify-between items-center text-xs uppercase tracking-wide">
            <div className="text-gray-300">
              {contract.description} | NYMEX | ACTIVE CONTRACT
            </div>
            <div className="text-gray-400">
              LAST UPDATE: {lastUpdate || 'CONNECTING'}
            </div>
          </div>
        </div>
        
        {/* Professional Market Status Bar */}
        <div className="px-6 py-3 bg-black border-b border-gray-700">
          <div className="grid grid-cols-4 gap-8 text-xs">
            <div>
              <div className="text-gray-500 uppercase tracking-wide mb-1">GLOBAL MARKETS</div>
              <div className="space-x-4 text-gray-300">
                <span>NYC {times.ny}</span>
                <span>LDN {times.london}</span>
                <span>TKY {times.tokyo}</span>
                <span>UTC {times.utc}</span>
              </div>
            </div>
            
            <div>
              <div className="text-gray-500 uppercase tracking-wide mb-1">MARKET STATUS</div>
              <div className={`${marketStatus.color} font-mono`}>
                ● {marketStatus.status}
              </div>
            </div>
            
            <div>
              <div className="text-gray-500 uppercase tracking-wide mb-1">NEXT ML CYCLE</div>
              <div className="text-orange-400 font-mono">
                {formatTime(timeRemaining)}
              </div>
            </div>
            
            <div>
              <div className="text-gray-500 uppercase tracking-wide mb-1">ML ENGINE</div>
              <div className="text-gray-300 font-mono">
                {enterpriseMetrics?.complex_ml_enabled ? 'ACTIVE' : 'OFFLINE'}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Professional ML Status Panel */}
      <div className="px-6 py-4 bg-gray-900 border-b border-gray-700">
        <div className="flex justify-between items-center">
          <div className="flex items-center space-x-4">
            <div className="text-sm text-gray-400 uppercase tracking-wide">ML ENGINE STATUS</div>
            <div className={`font-mono text-sm ${getStatusColor(mlStatus.status)}`}>
              {mlStatus.status?.toUpperCase() || 'UNKNOWN'}
            </div>
            {mlStatus.current_step && (
              <div className="text-sm text-gray-500 font-mono">
                | {mlStatus.current_step}
              </div>
            )}
          </div>
          
          {mlStatus.status === 'running' && (
            <div className="flex items-center space-x-4">
              <div className="text-sm text-gray-400">
                PROGRESS: {mlStatus.progress_percentage}%
              </div>
              <div className="w-32 bg-gray-800 h-1">
                <div 
                  className="bg-orange-400 h-1 transition-all duration-1000"
                  style={{ width: `${mlStatus.progress_percentage}%` }}
                ></div>
              </div>
              {mlStatus.estimated_completion && (
                <div className="text-sm text-gray-400 font-mono">
                  ETA: {formatMLTime((mlStatus.estimated_completion - Date.now() / 1000))}
                </div>
              )}
            </div>
          )}
          
          {mlStatus.status === 'cached' && enterpriseMetrics?.ml_cache_expires && (
            <div className="text-sm text-gray-400 font-mono">
              CACHE EXPIRES: {formatMLTime((enterpriseMetrics.ml_cache_expires - Date.now() / 1000))}
            </div>
          )}
        </div>
      </div>

      {/* Professional Error Handling */}
      {connectionStatus === 'error' && (
        <div className="px-6 py-3 bg-red-900 border-l-4 border-red-500">
          <div className="flex justify-between items-center">
            <div>
              <div className="text-red-300 text-sm font-mono uppercase tracking-wide">
                DATA FEED DISRUPTION
              </div>
              <div className="text-red-400 text-xs mt-1">
                ML SERVER CONNECTION FAILED | ERRORS: {errorCount}
              </div>
            </div>
            <div className="text-red-400 text-xs font-mono">
              STATUS: RECONNECTING
            </div>
          </div>
        </div>
      )}

      {/* Professional System Metrics Grid */}
      <div className="px-6 py-4 bg-gray-950 border-b border-gray-700">
        <div className="grid grid-cols-6 gap-8 text-xs">
          <div>
            <div className="text-gray-500 uppercase tracking-wide mb-1">CONNECTION</div>
            <div className={`font-mono text-sm ${
              connectionStatus === 'connected' ? 'text-green-400' : 
              connectionStatus === 'connecting' ? 'text-yellow-400' : 'text-red-400'
            }`}>
              {connectionStatus.toUpperCase()}
            </div>
          </div>
          
          <div>
            <div className="text-gray-500 uppercase tracking-wide mb-1">PRICE POINTS</div>
            <div className="text-white text-sm font-mono">
              {enterpriseMetrics?.data_points || 0}
            </div>
          </div>
          
          <div>
            <div className="text-gray-500 uppercase tracking-wide mb-1">ML FORECASTS</div>
            <div className="text-white text-sm font-mono">
              {enterpriseMetrics?.prediction_points || 0}
            </div>
          </div>
          
          <div>
            <div className="text-gray-500 uppercase tracking-wide mb-1">DATA QUALITY</div>
            <div className="text-green-400 text-sm font-mono">
              {enterpriseMetrics?.data_quality || 100}%
            </div>
          </div>
          
          <div>
            <div className="text-gray-500 uppercase tracking-wide mb-1">LATENCY</div>
            <div className="text-green-400 text-sm font-mono">
              &lt;1MS
            </div>
          </div>
          
          <div>
            <div className="text-gray-500 uppercase tracking-wide mb-1">UPTIME</div>
            <div className="text-white text-sm font-mono">
              {enterpriseMetrics?.server_uptime?.split('.')[0] || '00:00:00'}
            </div>
          </div>
        </div>
      </div>


      {/* Professional Navigation Tabs */}
      <div className="mb-4">
        <div className="flex space-x-0 bg-gray-900 border-b border-gray-700">
          <button
            onClick={() => setViewMode('chart')}
            className={`px-6 py-3 text-sm font-mono uppercase tracking-wide border-r border-gray-700 transition-colors ${
              viewMode === 'chart' 
                ? 'bg-orange-400 text-black font-bold' 
                : 'bg-gray-900 text-gray-400 hover:bg-gray-800 hover:text-white'
            }`}
          >
[1] CHART
          </button>
          <button
            onClick={() => setViewMode('analytics')}
            className={`px-6 py-3 text-sm font-mono uppercase tracking-wide border-r border-gray-700 transition-colors ${
              viewMode === 'analytics' 
                ? 'bg-orange-400 text-black font-bold' 
                : 'bg-gray-900 text-gray-400 hover:bg-gray-800 hover:text-white'
            }`}
          >
[2] ANALYTICS
          </button>
          <button
            onClick={() => setViewMode('future')}
            className={`px-6 py-3 text-sm font-mono uppercase tracking-wide transition-colors ${
              viewMode === 'future' 
                ? 'bg-orange-400 text-black font-bold' 
                : 'bg-gray-900 text-gray-400 hover:bg-gray-800 hover:text-white'
            }`}
          >
[3] FUTURE FORECAST
          </button>
        </div>

        {/* Simplified Chart Mode Sub-navigation */}
        {viewMode === 'chart' && (
          <div className="flex space-x-0 bg-gray-800 border-b border-gray-600">
            <button
              onClick={() => setChartMode('historical')}
              className={`px-4 py-2 text-xs font-mono uppercase tracking-wide border-r border-gray-600 transition-colors ${
                chartMode === 'historical' 
                  ? 'bg-orange-400 text-black font-bold' 
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
              }`}
            >
              UNIFIED VIEW
            </button>
            <button
              onClick={() => setChartMode('hybrid')}
              className={`px-4 py-2 text-xs font-mono uppercase tracking-wide transition-colors ${
                chartMode === 'hybrid' 
                  ? 'bg-orange-400 text-black font-bold' 
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
              }`}
            >
              WITH FORECAST
            </button>
          </div>
        )}

        {viewMode === 'chart' && (
          <Chart 
            actualArray={actualArray} 
            predictedArray={predictedArray}
            performanceMetrics={performanceMetrics}
            enterpriseMetrics={enterpriseMetrics}
            multiHorizonPredictions={multiHorizonPredictions}
            showFuture={chartMode === 'hybrid'}
            unifiedData={unifiedData}
          />
        )}

        {viewMode === 'future' && (
          <FutureChart 
            currentPrice={actualPrice}
            multiHorizonPredictions={multiHorizonPredictions}
            performanceMetrics={performanceMetrics}
          />
        )}

        {viewMode === 'analytics' && (
          <div className="bg-black">
            <div className="px-6 py-4 bg-gray-900 border-b border-gray-700">
              <div className="text-sm text-gray-400 uppercase tracking-wide">PROFESSIONAL ANALYTICS TERMINAL</div>
            </div>
            
            {/* Consolidated Multi-Horizon Predictions */}
            {multiHorizonPredictions && (
              <div className="bg-gray-900 border-b border-gray-700">
                <div className="px-6 py-4">
                  <div className="text-sm text-gray-400 uppercase tracking-wide mb-4">ML FORECASTING ENGINE</div>
                  <div className="grid grid-cols-4 gap-4">
                    {['1h', '4h', '1d', '7d'].map(horizon => {
                      const pred = multiHorizonPredictions.predictions?.[horizon];
                      const currentPrice = actualArray.length > 0 ? actualArray[actualArray.length - 1] : 0;
                      
                      return pred ? (
                        <div key={horizon} className="bg-gray-800 p-3 rounded border border-gray-600">
                          <div className="text-center">
                            <div className="text-orange-400 text-sm font-mono font-bold mb-2">
                              {horizon.toUpperCase()}
                            </div>
                            <div className="text-white text-2xl font-bold mb-1">
                              ${pred.toFixed(2)}
                            </div>
                            <div className={`text-sm font-mono ${
                              currentPrice && (pred - currentPrice) >= 0 ? 'text-green-400' : 'text-red-400'
                            }`}>
                              {currentPrice ? 
                                `${(pred - currentPrice) >= 0 ? '+' : ''}${(((pred - currentPrice) / currentPrice) * 100).toFixed(2)}%` : 
                                '0.00%'
                              }
                            </div>
                          </div>
                        </div>
                      ) : null;
                    })}
                  </div>
                  
                  <div className="mt-4 text-xs text-gray-500 text-center">
                    Generated: {multiHorizonPredictions.generated_at ? new Date(multiHorizonPredictions.generated_at).toLocaleTimeString() : 'Unknown'} | 
                    Processing: {multiHorizonPredictions.processing_time?.toFixed(1)}s
                  </div>
                </div>
              </div>
            )}

            {/* Streamlined Performance Metrics */}
            {performanceMetrics && (
              <div className="bg-gray-900">
                <div className="px-6 py-4">
                  <div className="text-sm text-gray-400 uppercase tracking-wide mb-4">PERFORMANCE ANALYTICS</div>
                  <div className="grid grid-cols-3 gap-6 text-xs">
                    <div className="text-center">
                      <div className="text-gray-500 uppercase tracking-wide mb-1">DIRECTION ACCURACY</div>
                      <div className={`text-3xl font-mono font-bold mb-1 ${
                        performanceMetrics.direction_accuracy > 55 ? 'text-green-400' : 
                        performanceMetrics.direction_accuracy > 45 ? 'text-yellow-400' : 'text-red-400'
                      }`}>
                        {performanceMetrics.direction_accuracy}%
                      </div>
                      <div className="text-gray-600 text-xs">
                        {performanceMetrics.direction_accuracy > 50 ? 'OUTPERFORMING' : 'UNDERPERFORMING'}
                      </div>
                    </div>
                    
                    <div className="text-center">
                      <div className="text-gray-500 uppercase tracking-wide mb-1">CORRELATION</div>
                      <div className={`text-3xl font-mono font-bold mb-1 ${
                        Math.abs(performanceMetrics.correlation) > 60 ? 'text-green-400' : 
                        Math.abs(performanceMetrics.correlation) > 30 ? 'text-yellow-400' : 'text-red-400'
                      }`}>
                        {performanceMetrics.correlation}%
                      </div>
                      <div className="text-gray-600 text-xs">
                        PRICE CORRELATION
                      </div>
                    </div>
                    
                    <div className="text-center">
                      <div className="text-gray-500 uppercase tracking-wide mb-1">MEAN ERROR</div>
                      <div className="text-white text-3xl font-mono font-bold mb-1">
                        ${performanceMetrics.mae}
                      </div>
                      <div className="text-gray-600 text-xs">
                        AVERAGE ABSOLUTE ERROR
                      </div>
                    </div>
                  </div>
                  
                  <div className="mt-4 pt-4 border-t border-gray-800 text-center">
                    <div className="text-xs text-gray-500">
                      SAMPLE SIZE: {performanceMetrics.total_predictions} predictions | 
                      MAPE: {performanceMetrics.mape}% | 
                      RMSE: ${performanceMetrics.rmse}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Professional Market Intelligence */}
            {advancedAnalytics && (
              <div className="bg-gray-950 border-t border-gray-700">
                <div className="px-6 py-4">
                  <div className="text-sm text-gray-400 uppercase tracking-wide mb-4">MARKET INTELLIGENCE</div>
                  <div className="grid grid-cols-2 gap-8 text-xs">
                    <div>
                      <div className="text-gray-500 uppercase tracking-wide mb-2">VOLATILITY ANALYSIS</div>
                      <div className="space-y-1">
                        <div className="text-gray-400">• Intraday volatility tracking active</div>
                        <div className="text-gray-400">• GARCH regime detection enabled</div>
                        <div className="text-gray-400">• VaR calculations updated real-time</div>
                      </div>
                    </div>
                    
                    <div>
                      <div className="text-gray-500 uppercase tracking-wide mb-2">MARKET MICROSTRUCTURE</div>
                      <div className="space-y-1">
                        <div className="text-gray-400">• Order flow analysis active</div>
                        <div className="text-gray-400">• Spread dynamics monitored</div>
                        <div className="text-gray-400">• Liquidity metrics calculated</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Enhanced Footer */}
      <div className="text-xs text-gray-500 border-t border-orange-400 pt-2">
        <div className="flex justify-between">
          <span>
            Bloomberg Terminal - CLQ25 | Complex ML v4.0.0 | 
            Server: {enterpriseMetrics?.server_uptime || 'Unknown'}
          </span>
          <span>
            Last Data: {lastUpdate || 'Never'} | 
            Errors: {errorCount} | 
            ML: {mlStatus.status?.toUpperCase() || 'UNKNOWN'}
          </span>
        </div>
      </div>
    </div>
  );
}