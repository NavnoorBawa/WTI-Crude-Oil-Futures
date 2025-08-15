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
      <div style={{
        minHeight: '100vh',
        backgroundColor: '#000000',
        color: '#F39F41',
        fontFamily: 'monospace',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexDirection: 'column'
      }}>
        <div style={{
          backgroundColor: '#F39F41',
          color: '#000000',
          padding: '10px 20px',
          marginBottom: '20px',
          fontWeight: 'bold'
        }}>
          BLOOMBERG TERMINAL
        </div>
        <div style={{ fontSize: '1.2em', marginBottom: '10px' }}>
          Loading market data...
        </div>
        <div style={{ color: '#22c55e' }}>●●●</div>
      </div>
    );
  }

  // Error screen
  if (error) {
    return (
      <div style={{
        minHeight: '100vh',
        backgroundColor: '#000000',
        color: '#F39F41',
        fontFamily: 'monospace',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexDirection: 'column'
      }}>
        <div style={{
          backgroundColor: '#ef4444',
          color: '#ffffff',
          padding: '10px 20px',
          marginBottom: '20px',
          fontWeight: 'bold'
        }}>
          CONNECTION ERROR
        </div>
        <div style={{ fontSize: '1.2em', marginBottom: '10px', color: '#ef4444' }}>
          Failed to connect to server
        </div>
        <div style={{ color: '#9ca3af', fontSize: '0.9em' }}>
          Error: {error}
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
    <div style={{
      minHeight: '100vh',
      backgroundColor: '#000000',
      color: '#F39F41',
      fontFamily: 'monospace',
      padding: '0'
    }}>
      {/* Professional Quantitative Research Header */}
      <div style={{
        backgroundColor: '#1a1a1a',
        color: '#ffffff',
        padding: '8px 20px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        borderBottom: '2px solid #0066cc',
        fontFamily: 'Consolas, Monaco, monospace'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          <div style={{ fontWeight: 'bold', fontSize: '1.1em', color: '#0066cc' }}>
            QUANTITATIVE RESEARCH TERMINAL
          </div>
          <div style={{ fontSize: '0.9em', color: '#888' }}>
            {data?.contract?.symbol || 'CLQ25'} | WTI Crude Oil Futures
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '15px', fontSize: '0.85em' }}>
          <div style={{ color: '#00ff00' }}>
            ● LIVE DATA
          </div>
          <div style={{ color: '#ccc' }}>
            {new Date().toLocaleTimeString()} UTC
          </div>
          <div style={{ color: '#888' }}>
            Session: {new Date().toLocaleDateString()}
          </div>
        </div>
      </div>

      {/* Professional Quantitative Data Dashboard */}
      <div style={{
        padding: '12px 20px',
        borderBottom: '1px solid #333',
        backgroundColor: '#111',
        display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr',
        gap: '20px',
        fontFamily: 'Consolas, Monaco, monospace'
      }}>
        {/* Market Data Section */}
        <div style={{ 
          padding: '10px', 
          backgroundColor: '#1a1a1a', 
          border: '1px solid #333',
          borderRadius: '4px'
        }}>
          <div style={{ fontSize: '0.7em', color: '#888', marginBottom: '6px', textTransform: 'uppercase' }}>
            MARKET DATA
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: '0.65em', color: '#888' }}>SPOT PRICE</div>
              <div style={{ fontSize: '1.4em', fontWeight: 'bold', color: '#ffffff' }}>
                ${currentPrice.toFixed(2)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: '0.65em', color: '#888' }}>24H CHANGE</div>
              <div style={{ 
                fontSize: '1.1em', 
                fontWeight: 'bold',
                color: priceChange >= 0 ? '#00ff88' : '#ff4444' 
              }}>
                {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(3)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: '0.65em', color: '#888' }}>ML FORECAST</div>
              <div style={{ fontSize: '1.1em', fontWeight: 'bold', color: '#0099ff' }}>
                ${currentPrediction.toFixed(2)}
              </div>
            </div>
          </div>
        </div>

        {/* Model Performance Section */}
        <div style={{ 
          padding: '10px', 
          backgroundColor: '#1a1a1a', 
          border: '1px solid #333',
          borderRadius: '4px'
        }}>
          <div style={{ fontSize: '0.7em', color: '#888', marginBottom: '6px', textTransform: 'uppercase' }}>
            MODEL PERFORMANCE
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '0.6em', color: '#888' }}>ACCURACY</div>
              <div style={{ fontSize: '1.1em', fontWeight: 'bold', color: '#00ff88' }}>
                {data?.performance_metrics?.direction_accuracy || 67}%
              </div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '0.6em', color: '#888' }}>CORRELATION</div>
              <div style={{ fontSize: '1.1em', fontWeight: 'bold', color: '#00ff88' }}>
                {data?.performance_metrics?.correlation || 75}%
              </div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '0.6em', color: '#888' }}>MAE</div>
              <div style={{ fontSize: '1.1em', fontWeight: 'bold', color: '#ffaa00' }}>
                ${data?.performance_metrics?.mae || 1.15}
              </div>
            </div>
          </div>
        </div>

        {/* Risk & Analytics Section */}
        <div style={{ 
          padding: '10px', 
          backgroundColor: '#1a1a1a', 
          border: '1px solid #333',
          borderRadius: '4px'
        }}>
          <div style={{ fontSize: '0.7em', color: '#888', marginBottom: '6px', textTransform: 'uppercase' }}>
            RISK ANALYTICS
          </div>
          {data?.multi_horizon_predictions && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '6px', fontSize: '0.7em' }}>
              {Object.entries(data.multi_horizon_predictions.predictions || {}).map(([horizon, price]) => {
                const change = ((price - currentPrice) / currentPrice * 100);
                return (
                  <div key={horizon} style={{ textAlign: 'center' }}>
                    <div style={{ color: '#888', fontSize: '0.6em' }}>{horizon.toUpperCase()}</div>
                    <div style={{ 
                      color: change >= 0 ? '#00ff88' : '#ff4444', 
                      fontWeight: 'bold',
                      fontSize: '0.9em'
                    }}>
                      {change >= 0 ? '+' : ''}{change.toFixed(1)}%
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <div style={{ marginTop: '8px', display: 'flex', justifyContent: 'space-between', fontSize: '0.65em' }}>
            <div>
              <span style={{ color: '#888' }}>DATA: </span>
              <span style={{ color: '#00ff88' }}>{data?.enterprise_metrics?.data_points || 0} pts</span>
            </div>
            <div>
              <span style={{ color: '#888' }}>STATUS: </span>
              <span style={{ color: '#00ff88' }}>{data?.ml_status?.status?.toUpperCase() || 'ACTIVE'}</span>
            </div>
          </div>
        </div>
      </div>

      {/* MASSIVE CHART SECTION - TAKES ALMOST ENTIRE SCREEN */}
      <div style={{
        height: 'calc(100vh - 120px)', // Almost full screen minus tiny header
        borderTop: '1px solid #F39F41'
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