import React, { useMemo, useState, useRef } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  TimeScale,
} from "chart.js";
import zoomPlugin from "chartjs-plugin-zoom";
import "chartjs-adapter-date-fns";
import { Line } from "react-chartjs-2";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  TimeScale,
  zoomPlugin
);

export default function Chart({ 
  actualArray = [], 
  predictedArray = [], 
  unifiedData = null,
  multiHorizonPredictions = null,
  showFuture = true,
  currentPrice = 0
}) {
  const [showHistorical, setShowHistorical] = useState(true);
  const chartRef = useRef();

  // Create minimal chart with current price and ML predictions
  const createMinimalChart = (currentPrice, multiHorizonPredictions) => {
    const timeLabels = ['Now', '1H', '1D', '1W'];
    const actualPrices = [currentPrice, null, null, null];
    const futurePredictions = [
      null,
      multiHorizonPredictions.predictions['1h'],
      multiHorizonPredictions.predictions['1d'],
      multiHorizonPredictions.predictions['7d'] || multiHorizonPredictions.predictions['1w']
    ];

    return {
      isEmpty: false,
      timeLabels: timeLabels,
      actualPrices: actualPrices,
      historicalPredictions: [],
      futurePredictions: futurePredictions,
      upperBounds: [],
      lowerBounds: []
    };
  };

  // Process data with clear separation of actual, historical predictions, and future predictions
  const chartData = useMemo(() => {
    
    // Get actual data - handle both unified data structure and legacy arrays
    let actualData;
    if (unifiedData?.actual?.values && unifiedData.actual.values.length > 0) {
      actualData = unifiedData.actual;
    } else if (actualArray && actualArray.length > 0) {
      actualData = { values: actualArray, timestamps: [] };
    } else {
      actualData = { values: [], timestamps: [] };
    }
    
    const predictedData = unifiedData?.predicted || { 
      historical: { values: predictedArray || [], timestamps: [], upper_bound: [], lower_bound: [] },
      future: { values: [], timestamps: [], upper_bound: [], lower_bound: [] }
    };
    
    // If no historical data but we have current price and predictions, create a minimal chart
    if (!actualData.values || actualData.values.length === 0) {
      if (currentPrice > 0 && multiHorizonPredictions?.predictions) {
        return createMinimalChart(currentPrice, multiHorizonPredictions);
      }
      return { isEmpty: true };
    }
    
    // Create timeline labels
    const timeLabels = [];
    const actualPrices = [];
    const historicalPredictions = [];
    const futurePredictions = [];
    
    // LIMIT HISTORICAL DATA to give 3/4 space to future predictions (like btcgpt.info)
    // Show more historical points to display price variation better
    const maxHistoricalPoints = 30;
    const startIndex = Math.max(0, actualData.values.length - maxHistoricalPoints);
    const historicalSlice = actualData.values.slice(startIndex);
    const timestampSlice = actualData.timestamps ? actualData.timestamps.slice(startIndex) : [];
    
    // Process limited actual historical data with REAL timestamps - NO FAKE TIME
    historicalSlice.forEach((price, i) => {
      if (price && !isNaN(price) && price > 0) {
        // Use real timestamps if available, otherwise create readable time labels
        let timeLabel;
        
        if (timestampSlice && timestampSlice[i]) {
          // Use actual timestamp from data - show date and time for market data
          const timestamp = new Date(timestampSlice[i]);
          const now = new Date();
          const daysDiff = Math.floor((now - timestamp) / (1000 * 60 * 60 * 24));
          
          if (daysDiff === 0) {
            // Same day - show time only
            timeLabel = timestamp.toLocaleTimeString('en-US', { 
              hour12: false, 
              hour: '2-digit', 
              minute: '2-digit' 
            });
          } else if (daysDiff <= 7) {
            // Within a week - show day and time
            timeLabel = timestamp.toLocaleDateString('en-US', { 
              weekday: 'short',
              hour: '2-digit', 
              minute: '2-digit',
              hour12: false
            });
          } else {
            // Older - show date
            timeLabel = timestamp.toLocaleDateString('en-US', { 
              month: 'short', 
              day: 'numeric'
            });
          }
        } else {
          // Fallback: Create realistic time intervals 
          const hoursAgo = historicalSlice.length - 1 - i;
          if (hoursAgo === 0) {
            timeLabel = 'NOW';
          } else if (hoursAgo <= 24) {
            timeLabel = `-${hoursAgo}h`;
          } else {
            const days = Math.floor(hoursAgo / 24);
            const hours = hoursAgo % 24;
            timeLabel = hours > 0 ? `-${days}d${hours}h` : `-${days}d`;
          }
        }
        
        timeLabels.push(timeLabel);
        actualPrices.push(Number(price.toFixed(2)));
        historicalPredictions.push(null);
        futurePredictions.push(null);
      }
    });
    
    // Process historical predictions (align with actual data)
    if (predictedData.historical?.values && predictedData.historical.values.length > 0) {
      // Fill historical predictions array with prediction values
      predictedData.historical.values.forEach((pred, i) => {
        if (i < historicalPredictions.length && pred && !isNaN(pred)) {
          historicalPredictions[i] = Number(pred.toFixed(2));
        }
      });
    }
    
    // Add ONLY REAL future predictions that your model actually makes - NO FAKE INTERPOLATION
    if (showFuture && actualPrices.length > 0 && multiHorizonPredictions?.predictions) {
      const predictions = multiHorizonPredictions.predictions;
      
      // ONLY show the 3 REAL predictions your model makes - NO FAKE POINTS
      const realFuturePoints = [
        { label: '+1H', value: predictions['1h'] },  // Real 1H prediction
        { label: '+1D', value: predictions['1d'] },  // Real 1D prediction  
        { label: '+1W', value: predictions['7d'] }   // Real 1W prediction
      ];
      
      // Add spacing points to give future predictions more chart space
      const spacingPoints = [
        { label: '', value: null },  // Empty spacing
        { label: '', value: null },  // Empty spacing
        { label: '', value: null },  // Empty spacing
        { label: '', value: null },  // Empty spacing
        { label: '', value: null },  // Empty spacing
      ];
      
      // Add spacing first to create 3/4 chart space for future
      spacingPoints.forEach(point => {
        timeLabels.push(point.label);
        actualPrices.push(null);
        historicalPredictions.push(null);
        futurePredictions.push(null);
      });
      
      // Add ONLY the 3 REAL prediction points
      realFuturePoints.forEach(point => {
        if (point.value && !isNaN(point.value)) {
          timeLabels.push(point.label);
          actualPrices.push(null);
          historicalPredictions.push(null);
          futurePredictions.push(Number(point.value.toFixed(2)));
        }
      });
    }
    
    
    
    // Debug logging for chart data
    console.log('Chart data prepared:', {
      timeLabelsCount: timeLabels.length,
      actualPricesCount: actualPrices.length,
      actualPricesNonNull: actualPrices.filter(p => p !== null).length,
      actualPricesSample: actualPrices.filter(p => p !== null).slice(0, 8),
      futurePredictionsNonNull: futurePredictions.filter(p => p !== null).length,
      timeLabels: timeLabels.slice(0, 8)
    });
    
    return {
      isEmpty: false,
      timeLabels,
      actualPrices,
      historicalPredictions,
      futurePredictions,
      currentPrice: actualPrices.filter(p => p !== null).pop() || 0,
      totalPoints: actualPrices.filter(p => p !== null).length
    };
  }, [actualArray, predictedArray, unifiedData, multiHorizonPredictions, showFuture, currentPrice]);

  // Reset zoom function
  const resetZoom = () => {
    if (chartRef.current) {
      chartRef.current.resetZoom();
    }
  };

  // Toggle historical data
  const toggleHistoricalData = () => {
    setShowHistorical(!showHistorical);
  };

  // Calculate Y-axis bounds for optimal oil price visualization
  const calculateYAxisBounds = () => {
    const actualPrices = chartData.actualPrices?.filter(p => p !== null && !isNaN(p)) || [];
    const historicalPrices = chartData.historicalPredictions?.filter(p => p !== null && !isNaN(p)) || [];
    const futurePrices = chartData.futurePredictions?.filter(p => p !== null && !isNaN(p)) || [];
    
    const allPrices = [...actualPrices, ...historicalPrices, ...futurePrices];
    if (allPrices.length === 0) return { min: 60, max: 70 };
    
    // Check if actual prices have small variation - if so, optimize for that
    const actualMin = actualPrices.length > 0 ? Math.min(...actualPrices) : 0;
    const actualMax = actualPrices.length > 0 ? Math.max(...actualPrices) : 0;
    const actualRange = actualMax - actualMin;
    
    const minPrice = Math.min(...allPrices);
    const maxPrice = Math.max(...allPrices);
    const fullRange = maxPrice - minPrice;
    
    // Debug logging
    console.log('Price range calculation:', {
      actualCount: actualPrices.length,
      actualRange,
      actualMin,
      actualMax,
      fullRange,
      minPrice,
      maxPrice,
      actualSample: actualPrices.slice(0, 5),
      futureSample: futurePrices.slice(0, 3)
    });
    
    // If actual price variation is small (<$0.50) but total range is large (>$5),
    // focus the chart on actual prices with some room for future predictions
    if (actualRange < 0.5 && fullRange > 5 && actualPrices.length > 0) {
      const padding = Math.max(2.0, fullRange * 0.1); // Ensure we see price movements
      return {
        min: Math.max(0, actualMin - padding),
        max: actualMax + padding
      };
    }
    
    // For small variations, ensure minimum visible range of $2
    if (fullRange < 2 && actualPrices.length > 0) {
      const center = (minPrice + maxPrice) / 2;
      return {
        min: Math.max(0, center - 1.5),
        max: center + 1.5
      };
    }
    
    // Normal calculation for larger variations
    const padding = fullRange < 1 ? 0.5 : Math.max(1.0, fullRange * 0.15);
    
    return {
      min: Math.max(0, minPrice - padding),
      max: maxPrice + padding
    };
  };
  
  const yAxisBounds = calculateYAxisBounds();

  // Chart configuration with professional Bloomberg styling
  const data = {
    labels: chartData.timeLabels || [],
    datasets: [
      // 1. ACTUAL PRICES - Gold line
      {
        label: 'ACTUAL PRICES',
        data: chartData.actualPrices || [],
        borderColor: '#FFD700',
        backgroundColor: 'transparent',
        borderWidth: 3,
        pointRadius: 2,
        pointHoverRadius: 6,
        pointBackgroundColor: '#FFD700',
        pointBorderColor: '#000000',
        pointBorderWidth: 1,
        tension: 0.1,
        spanGaps: false,
        order: 1
      },
      
      // 2. HISTORICAL PREDICTIONS - Green dashed line
      {
        label: 'HISTORICAL PREDICTIONS',
        data: showHistorical ? (chartData.historicalPredictions || []) : [],
        borderColor: '#00FF88',
        backgroundColor: 'rgba(0, 255, 136, 0.1)',
        borderWidth: 3,
        borderDash: [8, 4],
        pointRadius: 2,
        pointHoverRadius: 5,
        pointBackgroundColor: '#00FF88',
        pointBorderColor: '#000000',
        pointBorderWidth: 1,
        tension: 0.1,
        spanGaps: false,
        order: 2,
        hidden: !showHistorical
      },
      
      // 3. FUTURE PREDICTIONS - Cyan dotted line
      {
        label: 'FUTURE FORECAST',
        data: chartData.futurePredictions || [],
        borderColor: '#4AF6C3',
        backgroundColor: 'rgba(74, 246, 195, 0.1)',
        borderWidth: 3,
        borderDash: [4, 8],
        pointRadius: 4,
        pointHoverRadius: 7,
        pointBackgroundColor: '#4AF6C3',
        pointBorderColor: '#000000',
        pointBorderWidth: 2,
        tension: 0.2,
        spanGaps: false,
        order: 3
      }
    ]
  };

  // Chart options optimized for Bloomberg appearance
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: {
      mode: 'index',
      intersect: false,
    },
    plugins: {
      zoom: {
        zoom: {
          wheel: {
            enabled: true,
            speed: 0.1,
          },
          pinch: {
            enabled: true
          },
          mode: 'xy',
          drag: {
            enabled: true,
            backgroundColor: 'rgba(255, 215, 0, 0.1)',
            borderColor: '#FFD700',
            borderWidth: 1,
          }
        },
        pan: {
          enabled: true,
          mode: 'xy',
          threshold: 10,
        }
      },
      legend: {
        display: true,
        position: 'top',
        labels: {
          color: '#FFA500',
          font: {
            family: 'monospace',
            size: 14,
            weight: 'normal'
          },
          padding: 20,
          usePointStyle: false,
          boxWidth: 20,
          boxHeight: 3
        }
      },
      tooltip: {
        enabled: true,
        mode: 'index',
        intersect: false,
        backgroundColor: 'rgba(0, 0, 0, 0.9)',
        titleColor: '#FFA500',
        bodyColor: '#FFFFFF',
        borderColor: '#FFA500',
        borderWidth: 1,
        cornerRadius: 0,
        padding: 8,
        titleFont: {
          size: 14,
          family: 'monospace',
          weight: 'normal'
        },
        bodyFont: {
          size: 13,
          family: 'monospace'
        },
        callbacks: {
          title: function(tooltipItems) {
            return `TIME: ${tooltipItems[0].label}`;
          },
          label: function(context) {
            if (context.parsed.y === null) return null;
            
            const value = context.parsed.y;
            const datasetLabel = context.dataset.label;
            
            if (datasetLabel === 'ACTUAL PRICES') {
              return `ACTUAL: $${value.toFixed(2)}`;
            } else if (datasetLabel === 'HISTORICAL PREDICTIONS') {
              return `HISTORICAL: $${value.toFixed(2)}`;
            } else if (datasetLabel === 'FUTURE FORECAST') {
              return `FORECAST: $${value.toFixed(2)}`;
            }
            
            return `$${value.toFixed(2)}`;
          }
        }
      }
    },
    scales: {
      x: {
        type: 'category',
        grid: {
          color: 'rgba(255, 165, 0, 0.2)',
          lineWidth: 1,
        },
        ticks: {
          color: '#FFA500',
          font: {
            family: 'monospace',
            size: 12,
            weight: 'normal'
          },
          maxTicksLimit: 20,
          maxRotation: 45,
          minRotation: 0,
        },
        border: {
          color: '#666666',
        },
        title: {
          display: true,
          text: 'TIME HORIZON',
          color: '#FFA500',
          font: {
            family: 'monospace',
            size: 13,
            weight: 'normal'
          }
        }
      },
      y: {
        type: 'linear',
        position: 'right',
        beginAtZero: false,
        min: yAxisBounds.min,
        max: yAxisBounds.max,
        grid: {
          color: 'rgba(255, 165, 0, 0.3)',
          lineWidth: 1,
        },
        ticks: {
          color: '#FFA500',
          font: {
            family: 'monospace',
            size: 12,
            weight: 'normal'
          },
          callback: function(value) {
            return `$${value.toFixed(2)}`;
          }
        },
        border: {
          color: '#666666',
        },
        title: {
          display: true,
          text: 'WTI CRUDE OIL (USD/BBL)',
          color: '#FFA500',
          font: {
            family: 'monospace',
            size: 13,
            weight: 'normal'
          }
        }
      }
    }
  };

  // Loading state
  if (chartData.isEmpty) {
    return (
      <div className="w-full h-full bg-black flex items-center justify-center">
        <div className="text-center">
          <div className="text-bloomberg-amber text-lg font-mono mb-2">
            LOADING WTI CRUDE OIL DATA...
          </div>
          <div className="text-gray-400 text-sm">
            Connecting to real-time feeds
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full bg-black text-white font-mono">
      {/* Chart header */}
      <div className="bg-black border-b border-gray-700 px-2 py-1">
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-4">
            <h1 className="text-lg font-normal text-bloomberg-amber">WTI CRUDE OIL</h1>
            <div className="text-2xl font-bold text-white">
              ${currentPrice?.toFixed(2) || '0.00'}
            </div>
            <div className="text-sm text-gray-400">USD/BBL</div>
          </div>
          
          {/* Chart Controls */}
          <div className="flex items-center gap-3">
            <button 
              onClick={toggleHistoricalData}
              className={`px-3 py-1 text-sm font-mono border transition-all ${
                showHistorical 
                  ? 'bg-bloomberg-amber text-black border-bloomberg-amber' 
                  : 'bg-transparent text-bloomberg-amber border-bloomberg-amber hover:bg-bloomberg-amber hover:text-black'
              }`}
            >
              HISTORICAL
            </button>
            <button 
              onClick={resetZoom}
              className="px-3 py-1 text-sm font-mono bg-transparent text-bloomberg-amber border border-bloomberg-amber hover:bg-bloomberg-amber hover:text-black transition-all"
            >
              RESET ZOOM
            </button>
          </div>
        </div>
      </div>

      {/* Chart area */}
      <div className="h-full bg-black p-1">
        <div className="h-full w-full">
          <Line ref={chartRef} data={data} options={options} />
        </div>
      </div>
    </div>
  );
}