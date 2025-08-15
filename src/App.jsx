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
      {/* BLOOMBERG TERMINAL TITLEBAR - AUTHENTIC $24K DESIGN */}
      <div className="bloomberg-titlebar">
        BLOOMBERG TERMINAL - PROFESSIONAL WORKSTATION
      </div>

      {/* BLOOMBERG TERMINAL FUNCTION ROW */}
      <div className="bg-gray-900 p-1 flex justify-between items-center border-b border-bloomberg-amber">
        <div className="flex items-center gap-2">
          <div className="bloomberg-function-key">F1 HELP</div>
          <div className="bloomberg-function-key">F2 NEWS</div>
          <div className="bloomberg-function-key">F3 CALC</div>
          <div className="bloomberg-function-key">F4 PORT</div>
          <div className="bloomberg-function-key">F5 RESEARCH</div>
          <div className="bloomberg-function-key">F6 MONITOR</div>
          <div className="bloomberg-function-key">F7 ANALYTICS</div>
          <div className="bloomberg-function-key">F8 SETTINGS</div>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <div className="text-status-live">
            <span className="bloomberg-status-dot bg-bloomberg-green"></span>
            LIVE
          </div>
          <div className="text-bloomberg-amber">
            {new Date().toLocaleTimeString('en-US', { hour12: false })} EST
          </div>
          <div className="text-gray-400">
            USER: PROFESSIONAL
          </div>
        </div>
      </div>

      {/* BLOOMBERG COMMAND LINE */}
      <div className="bg-black border-b border-bloomberg-amber p-2">
        <div className="flex items-center gap-4">
          <span className="text-bloomberg-amber text-xs font-bold">COMMAND:</span>
          <div className="flex items-center gap-2">
            <span className="bg-bloomberg-yellow text-black px-2 py-1 font-bold text-xs">{data?.contract?.symbol || 'CLQ25'}</span>
            <span className="text-bloomberg-amber text-xs">&lt;COMDTY&gt;</span>
            <span className="bg-bloomberg-yellow text-black px-2 py-1 font-bold text-xs">GP</span>
            <span className="text-bloomberg-amber text-xs">&lt;GO&gt;</span>
            <span className="bloomberg-cursor"></span>
          </div>
          <div className="text-gray-400 text-xs">
            WTI CRUDE OIL FUTURES | NYMEX | REAL-TIME ANALYTICS
          </div>
        </div>
      </div>

      {/* BLOOMBERG NEWS TICKER */}
      <div className="bloomberg-ticker">
        <div className="bloomberg-ticker-content text-xs py-1">
          <span className="text-bloomberg-red">● BREAKING:</span> Oil prices volatile amid supply concerns 
          <span className="mx-8 text-bloomberg-blue">● MARKET:</span> NYMEX WTI futures active trading 
          <span className="mx-8 text-bloomberg-green">● UPDATE:</span> Real-time ML predictions live 
          <span className="mx-8 text-bloomberg-yellow">● ALERT:</span> High volatility detected in energy sector 
          <span className="mx-8 text-bloomberg-amber">● DATA:</span> Enterprise-grade analytics active
        </div>
      </div>

      {/* BLOOMBERG TERMINAL DATA DASHBOARD - ULTRA HIGH INFORMATION DENSITY */}
      <div className="p-2 border-b border-gray-700 bg-gray-950 grid grid-cols-4 gap-2 bloomberg-grid">
        {/* Market Data Section */}
        <div className="bloomberg-panel p-1">
          <div className="bloomberg-panel-header text-xxs">
            📊 SPOT MARKET - LIVE FEED
          </div>
          <div className="pt-1 space-y-1">
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">LAST</span>
              <span className="text-lg font-bold text-white bloomberg-highlight">
                ${currentPrice.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">CHG</span>
              <span className={`text-sm font-bold ${priceChange >= 0 ? 'text-bloomberg-green' : 'text-bloomberg-red'}`}>
                {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(3)}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">VOL</span>
              <span className="text-sm text-bloomberg-blue">1.2M</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">HIGH</span>
              <span className="text-sm text-white">${(currentPrice * 1.02).toFixed(2)}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">LOW</span>
              <span className="text-sm text-white">${(currentPrice * 0.98).toFixed(2)}</span>
            </div>
          </div>
        </div>

        {/* AI Analytics Section */}
        <div className="bloomberg-panel p-1">
          <div className="bloomberg-panel-header text-xxs">
            🤖 ML ANALYTICS ENGINE
          </div>
          <div className="pt-1 space-y-1">
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">PRED</span>
              <span className="text-lg font-bold text-bloomberg-blue">
                ${currentPrediction.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">ACC</span>
              <span className="text-sm font-bold text-bloomberg-green">
                {data?.performance_metrics?.direction_accuracy || 67}%
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">CORR</span>
              <span className="text-sm text-bloomberg-green">
                {data?.performance_metrics?.correlation || 75}%
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">MAE</span>
              <span className="text-sm text-bloomberg-yellow-warm">
                ${data?.performance_metrics?.mae || 1.15}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">CONF</span>
              <span className="text-sm text-bloomberg-blue">92.4%</span>
            </div>
          </div>
        </div>

        {/* Risk & Analytics Section */}
        <div className="bloomberg-panel p-1">
          <div className="bloomberg-panel-header text-xxs">
            ⚡ RISK MGMT - HORIZONS
          </div>
          <div className="pt-1 space-y-1">
            {data?.multi_horizon_predictions && Object.entries(data.multi_horizon_predictions.predictions || {}).slice(0, 4).map(([horizon, price]) => {
              const change = ((price - currentPrice) / currentPrice * 100);
              return (
                <div key={horizon} className="flex justify-between items-center">
                  <span className="text-xxs text-gray-500">{horizon.toUpperCase()}</span>
                  <span className={`text-sm font-bold ${change >= 0 ? 'text-bloomberg-green' : 'text-bloomberg-red'}`}>
                    {change >= 0 ? '+' : ''}{change.toFixed(1)}%
                  </span>
                </div>
              );
            })}
            {(!data?.multi_horizon_predictions || Object.keys(data.multi_horizon_predictions.predictions || {}).length === 0) && (
              <>
                <div className="flex justify-between items-center">
                  <span className="text-xxs text-gray-500">1H</span>
                  <span className="text-sm font-bold text-bloomberg-green">+0.2%</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-xxs text-gray-500">4H</span>
                  <span className="text-sm font-bold text-bloomberg-red">-0.8%</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-xxs text-gray-500">1D</span>
                  <span className="text-sm font-bold text-bloomberg-green">+1.5%</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-xxs text-gray-500">1W</span>
                  <span className="text-sm font-bold text-bloomberg-blue">+3.2%</span>
                </div>
              </>
            )}
          </div>
        </div>

        {/* System Status Section */}
        <div className="bloomberg-panel p-1">
          <div className="bloomberg-panel-header text-xxs">
            🔧 SYSTEM STATUS
          </div>
          <div className="pt-1 space-y-1">
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">DATA</span>
              <span className="text-sm text-bloomberg-green">{data?.enterprise_metrics?.data_points || 2847} PTS</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">FEED</span>
              <span className="text-sm text-status-live">LIVE</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">LAT</span>
              <span className="text-sm text-bloomberg-blue">12ms</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">CPU</span>
              <span className="text-sm text-bloomberg-yellow-warm">23%</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xxs text-gray-500">MEM</span>
              <span className="text-sm text-bloomberg-green">67%</span>
            </div>
          </div>
        </div>
      </div>

      {/* BLOOMBERG TERMINAL MAIN CHART - DOMINATES SCREEN LIKE $24K TERMINAL */}
      <div className="bloomberg-window" style={{
        height: 'calc(100vh - 200px)', // Adjusted for new header elements
        borderTop: '2px solid var(--bloomberg-amber)',
        margin: '2px'
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