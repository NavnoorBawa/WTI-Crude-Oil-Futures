import React, { useState, useEffect } from "react";
import Chart from "./Chart";
import ChatInterface from "./ChatInterface";

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isChatVisible, setIsChatVisible] = useState(false);

  useEffect(() => {
    // Set title
    document.title = "Bloomberg Terminal - WTI Crude Oil";

    // Fetch data function
    const fetchData = async () => {
      try {
        const response = await fetch('https://wti-crude-oil-backend.onrender.com/data');
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const result = await response.json();
        setData(result);
        setLoading(false);
        setError(null);
      } catch (err) {
        setError(err.message);
        setLoading(false);
      }
    };

    // Initial fetch
    fetchData();

    // Update every 5 seconds
    const interval = setInterval(fetchData, 5000);

    return () => clearInterval(interval);
  }, []);

  // Loading screen
  if (loading) {
    return (
      <div className="min-h-screen bg-black text-bloomberg-amber font-bloomberg flex items-center justify-center" style={{ flexDirection: 'column' }}>
        <div className="bloomberg-badge mb-4 text-lg px-6 py-3">
          BLOOMBERG TERMINAL
        </div>
        <div className="text-xl mb-3 tracking-wide">
          INITIALIZING MARKET DATA SYSTEMS...
        </div>
        <div className="bloomberg-status-dot bg-bloomberg-green"></div>
        <div className="text-xs text-gray-500 mt-4 uppercase tracking-wider">
          Connecting to Real-Time Data Feeds
        </div>
      </div>
    );
  }

  // Error screen
  if (error) {
    return (
      <div className="min-h-screen bg-black text-bloomberg-amber font-bloomberg flex items-center justify-center" style={{ flexDirection: 'column' }}>
        <div className="bg-bloomberg-red text-white px-6 py-3 mb-4 font-bold text-sm uppercase tracking-wider">
          ⚠ MARKET DATA CONNECTION FAILURE
        </div>
        <div className="text-xl mb-3 text-bloomberg-red">
          REAL-TIME FEED INTERRUPTED
        </div>
        <div className="text-gray-400 text-sm bloomberg-dense">
          ERROR: {error}
        </div>
        <div className="bloomberg-status-dot bg-bloomberg-red mt-4"></div>
        <div className="text-xs text-gray-500 mt-2 uppercase tracking-wider">
          Attempting Reconnection...
        </div>
      </div>
    );
  }

  // Main interface
  const currentPrice = data?.actual && data.actual.length > 0 ? data.actual[data.actual.length - 1] : 0;
  const currentPrediction = data?.predicted && data.predicted.length > 0 ? data.predicted[data.predicted.length - 1] : 0;
  const priceChange = data?.actual && data.actual.length > 1 ? 
    currentPrice - data.actual[data.actual.length - 2] : 0;

  return (
    <div className="min-h-screen bg-black text-bloomberg-amber font-bloomberg p-0">
      {/* BLOOMBERG TERMINAL HEADER - ICONIC $24K LOOK */}
      <div className="bg-gray-900 text-white p-2 flex justify-between items-center border-b-2 border-bloomberg-blue">
        <div className="flex items-center gap-6">
          <div className="bloomberg-badge">
            BLOOMBERG TERMINAL
          </div>
          <div className="font-bold text-lg text-bloomberg-blue uppercase tracking-wider">
            QUANTITATIVE RESEARCH
          </div>
          <div className="text-xs text-gray-400 bloomberg-dense">
            {data?.contract?.symbol || 'CLQ25'} | WTI CRUDE OIL FUTURES | NYMEX
          </div>
        </div>
        <div className="flex items-center gap-4 text-xs bloomberg-ultra-dense">
          <div className="text-status-live">
            <span className="bloomberg-status-dot bg-bloomberg-green"></span>
            LIVE FEED
          </div>
          <div className="text-gray-300">
            {new Date().toLocaleTimeString()} EST
          </div>
          <div className="text-gray-400">
            {new Date().toLocaleDateString('en-US', { month: 'short', day: '2-digit', year: 'numeric' })}
          </div>
          <div className="text-bloomberg-yellow-warm">
            USER: PROFESSIONAL
          </div>
        </div>
      </div>

      {/* BLOOMBERG TERMINAL DATA DASHBOARD - ULTRA HIGH INFORMATION DENSITY */}
      <div className="p-3 border-b border-gray-700 bg-gray-950 grid grid-cols-3 gap-4 bloomberg-grid">
        {/* Market Data Section */}
        <div className="bloomberg-panel p-2">
          <div className="bloomberg-panel-header">
            📊 MARKET DATA - REAL TIME
          </div>
          <div className="grid grid-cols-3 gap-2 pt-2">
            <div className="text-center">
              <div className="text-xxs text-gray-500 uppercase">SPOT</div>
              <div className="text-2xl font-bold text-white bloomberg-highlight">
                ${currentPrice.toFixed(2)}
              </div>
            </div>
            <div className="text-center">
              <div className="text-xxs text-gray-500 uppercase">24H CHG</div>
              <div className={`text-xl font-bold ${priceChange >= 0 ? 'text-bloomberg-green' : 'text-bloomberg-red'}`}>
                {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(3)}
              </div>
            </div>
            <div className="text-center">
              <div className="text-xxs text-gray-500 uppercase">ML PRED</div>
              <div className="text-xl font-bold text-bloomberg-blue">
                ${currentPrediction.toFixed(2)}
              </div>
            </div>
          </div>
        </div>

        {/* Model Performance Section */}
        <div className="bloomberg-panel p-2">
          <div className="bloomberg-panel-header">
            🤖 AI MODEL PERFORMANCE
          </div>
          <div className="grid grid-cols-3 gap-2 pt-2">
            <div className="text-center">
              <div className="text-xxs text-gray-500 uppercase">ACCURACY</div>
              <div className="text-lg font-bold text-bloomberg-green">
                {data?.performance_metrics?.direction_accuracy || 67}%
              </div>
            </div>
            <div className="text-center">
              <div className="text-xxs text-gray-500 uppercase">CORREL</div>
              <div className="text-lg font-bold text-bloomberg-green">
                {data?.performance_metrics?.correlation || 75}%
              </div>
            </div>
            <div className="text-center">
              <div className="text-xxs text-gray-500 uppercase">MAE</div>
              <div className="text-lg font-bold text-bloomberg-yellow-warm">
                ${data?.performance_metrics?.mae || 1.15}
              </div>
            </div>
          </div>
        </div>

        {/* Risk & Analytics Section */}
        <div className="bloomberg-panel p-2">
          <div className="bloomberg-panel-header">
            ⚡ RISK ANALYTICS - MULTI HORIZON
          </div>
          {data?.multi_horizon_predictions && (
            <div className="grid grid-cols-4 gap-1 pt-2 bloomberg-ultra-dense">
              {Object.entries(data.multi_horizon_predictions.predictions || {}).map(([horizon, price]) => {
                const change = ((price - currentPrice) / currentPrice * 100);
                return (
                  <div key={horizon} className="text-center">
                    <div className="text-xxs text-gray-500 uppercase">{horizon}</div>
                    <div className={`font-bold text-sm ${change >= 0 ? 'text-bloomberg-green' : 'text-bloomberg-red'}`}>
                      {change >= 0 ? '+' : ''}{change.toFixed(1)}%
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <div className="mt-2 flex justify-between text-xxs">
            <div>
              <span className="text-gray-500">DATA: </span>
              <span className="text-bloomberg-green">{data?.enterprise_metrics?.data_points || 0} PTS</span>
            </div>
            <div>
              <span className="text-gray-500">STATUS: </span>
              <span className="text-status-live">{data?.ml_status?.status?.toUpperCase() || 'ACTIVE'}</span>
            </div>
          </div>
        </div>
      </div>

      {/* BLOOMBERG TERMINAL MAIN CHART - DOMINATES SCREEN LIKE $24K TERMINAL */}
      <div className="bloomberg-border-glow" style={{
        height: 'calc(100vh - 140px)', // Almost full screen real estate
        borderTop: '2px solid var(--bloomberg-amber)'
      }}>
        <Chart 
          actualArray={data?.actual || []}
          predictedArray={data?.predicted || []}
          performanceMetrics={data?.performance_metrics}
          enterpriseMetrics={data?.enterprise_metrics}
          multiHorizonPredictions={data?.multi_horizon_predictions}
          unifiedData={data?.unified_data}
        />
      </div>

      {/* Chat Interface */}
      <ChatInterface 
        data={data}
        isVisible={isChatVisible}
        onToggle={() => setIsChatVisible(!isChatVisible)}
      />
    </div>
  );
}

export default App;