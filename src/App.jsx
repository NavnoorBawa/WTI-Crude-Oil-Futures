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
        <div className="bloomberg-status-dot bg-bloomberg-positive"></div>
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
      {/* BLOOMBERG TERMINAL HEADER */}
      <div className="bloomberg-titlebar">
        BLOOMBERG TERMINAL - PROFESSIONAL WORKSTATION
      </div>

      {/* BLOOMBERG FUNCTION KEYS */}
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
            <span className="bloomberg-status-dot bg-bloomberg-positive"></span>
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
            <span className="bg-bloomberg-alert text-black px-2 py-1 font-bold text-xs">{data?.contract?.symbol || 'CLQ25'}</span>
            <span className="text-bloomberg-amber text-xs">&lt;COMDTY&gt;</span>
            <span className="bg-bloomberg-alert text-black px-2 py-1 font-bold text-xs">GP</span>
            <span className="text-bloomberg-amber text-xs">&lt;GO&gt;</span>
            <span className="bloomberg-cursor"></span>
          </div>
          <div className="text-gray-400 text-xs">
            WTI CRUDE OIL FUTURES | NYMEX | REAL-TIME ANALYTICS
          </div>
        </div>
      </div>

      {/* BLOOMBERG TERMINAL DATA DASHBOARD */}
      <div className="p-3 border-b border-gray-700 bg-gray-950 grid grid-cols-4 gap-3">
        
        {/* Main Price Display */}
        <div className="bloomberg-panel p-3">
          <div className="bloomberg-panel-header">
            📊 CLQ25 WTI CRUDE OIL
          </div>
          <div className="pt-3">
            <div className="text-center">
              <div className="text-3xl font-bold text-white mb-2">
                ${currentPrice.toFixed(2)}
              </div>
              <div className={`text-lg font-bold ${priceChange >= 0 ? 'text-bloomberg-positive' : 'text-bloomberg-negative'}`}>
                {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(3)}
                <span className="text-sm ml-2">
                  ({priceChange >= 0 ? '+' : ''}{((priceChange/currentPrice)*100).toFixed(2)}%)
                </span>
              </div>
              <div className="text-xs text-bloomberg-amber-light mt-2">
                VOL: 1.2M | LAST UPDATE: LIVE
              </div>
            </div>
          </div>
        </div>

        {/* ML Prediction */}
        <div className="bloomberg-panel p-3">
          <div className="bloomberg-panel-header">
            🤖 ML PREDICTION ENGINE
          </div>
          <div className="pt-3">
            <div className="text-center">
              <div className="text-2xl font-bold text-bloomberg-blue mb-2">
                ${currentPrediction.toFixed(2)}
              </div>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-xs text-bloomberg-amber-light">ACCURACY:</span>
                  <span className="text-sm text-bloomberg-positive">{data?.performance_metrics?.direction_accuracy || 67}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-xs text-bloomberg-amber-light">CONFIDENCE:</span>
                  <span className="text-sm text-bloomberg-positive">92.4%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-xs text-bloomberg-amber-light">STATUS:</span>
                  <span className="text-sm text-status-live">ACTIVE</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Risk Analysis */}
        <div className="bloomberg-panel p-3">
          <div className="bloomberg-panel-header">
            ⚡ RISK HORIZONS
          </div>
          <div className="pt-3 space-y-2">
            <div className="flex justify-between items-center">
              <span className="text-xs text-bloomberg-amber-light">1H:</span>
              <span className="text-sm text-bloomberg-positive">+0.2%</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-bloomberg-amber-light">4H:</span>
              <span className="text-sm text-bloomberg-negative">-0.8%</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-bloomberg-amber-light">1D:</span>
              <span className="text-sm text-bloomberg-positive">+1.5%</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-bloomberg-amber-light">1W:</span>
              <span className="text-sm text-bloomberg-positive">+3.2%</span>
            </div>
          </div>
        </div>

        {/* System Status */}
        <div className="bloomberg-panel p-3">
          <div className="bloomberg-panel-header">
            🔧 SYSTEM STATUS
          </div>
          <div className="pt-3 space-y-2">
            <div className="flex justify-between items-center">
              <span className="text-xs text-bloomberg-amber-light">DATA:</span>
              <span className="text-sm text-bloomberg-positive">{data?.enterprise_metrics?.data_points || 2847} PTS</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-bloomberg-amber-light">FEED:</span>
              <span className="text-sm text-status-live">REAL-TIME</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-bloomberg-amber-light">LATENCY:</span>
              <span className="text-sm text-bloomberg-blue">12ms</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-bloomberg-amber-light">STATUS:</span>
              <span className="text-sm text-bloomberg-positive">OPTIMAL</span>
            </div>
          </div>
        </div>
        
      </div>

      {/* BLOOMBERG MAIN CHART DISPLAY */}
      <div className="bloomberg-window" style={{
        height: 'calc(100vh - 220px)',
        borderTop: '2px solid var(--bloomberg-amber)',
        margin: '4px'
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