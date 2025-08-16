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
  showFuture = true,
  currentPrice = 0
}) {
  const [showHistorical, setShowHistorical] = useState(true);
  const chartRef = useRef();

  // Process data with clear separation of actual, historical predictions, and future predictions
  const chartData = useMemo(() => {
    console.log("Processing chart data...", { unifiedData, actualArray, predictedArray });
    
    // Get actual data
    const actualData = unifiedData?.actual || { values: actualArray || [], timestamps: [] };
    const predictedData = unifiedData?.predicted || { 
      historical: { values: predictedArray || [], timestamps: [], upper_bound: [], lower_bound: [] },
      future: { values: [], timestamps: [], upper_bound: [], lower_bound: [] }
    };
    
    if (!actualData.values || actualData.values.length === 0) {
      console.log("No actual data available");
      return { isEmpty: true };
    }
    
    console.log("Data lengths:", {
      actual: actualData.values.length,
      historical: predictedData.historical?.values?.length || 0,
      future: predictedData.future?.values?.length || 0
    });
    
    // Create timeline labels
    const timeLabels = [];
    const actualPrices = [];
    const historicalPredictions = [];
    const futurePredictions = [];
    
    // Process actual historical data (past to present)
    actualData.values.forEach((price, i) => {
      if (price && !isNaN(price) && price > 0) {
        const minutesAgo = actualData.values.length - 1 - i;
        if (minutesAgo === 0) {
          timeLabels.push('NOW');
        } else if (minutesAgo < 60) {
          timeLabels.push(`-${minutesAgo}m`);
        } else {
          timeLabels.push(`-${Math.floor(minutesAgo / 60)}h`);
        }
        actualPrices.push(Number(price.toFixed(2)));
        historicalPredictions.push(null);
        futurePredictions.push(null);
      }
    });
    
    // Process historical predictions (align with actual data)
    if (predictedData.historical?.values && predictedData.historical.values.length > 0) {
      const startIndex = Math.max(0, actualPrices.length - predictedData.historical.values.length);
      predictedData.historical.values.forEach((pred, i) => {
        const index = startIndex + i;
        if (index < historicalPredictions.length && pred && !isNaN(pred)) {
          historicalPredictions[index] = Number(pred.toFixed(2));
        }
      });
    }
    
    // Generate future predictions if needed
    if (showFuture && actualPrices.length > 0) {
      const lastPrice = actualPrices[actualPrices.length - 1];
      
      // Calculate simple momentum from last 5 prices
      let momentum = 0;
      if (actualPrices.length >= 5) {
        const recent = actualPrices.slice(-5);
        const changes = recent.slice(1).map((price, i) => price - recent[i]);
        momentum = changes.reduce((sum, change) => sum + change, 0) / changes.length;
      }
      
      // Create 4 future predictions
      const futurePoints = [
        { hours: 1, label: '+1h' },
        { hours: 4, label: '+4h' },
        { hours: 24, label: '+1d' },
        { hours: 168, label: '+7d' }
      ];
      
      // NO FAKE PREDICTIONS - Only show real ML predictions from backend
      // Remove this fake future prediction generation completely
      // Future predictions should only come from real ML model via API
    }
    
    console.log("Final data arrays:", {
      timeLabels: timeLabels.length,
      actualPrices: actualPrices.filter(p => p !== null).length,
      historicalPredictions: historicalPredictions.filter(p => p !== null).length,
      futurePredictions: futurePredictions.filter(p => p !== null).length
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
  }, [actualArray, predictedArray, unifiedData, showFuture]);

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
        borderColor: '#00FF00',
        backgroundColor: 'rgba(0, 255, 0, 0.1)',
        borderWidth: 2,
        borderDash: [5, 5],
        pointRadius: 1,
        pointHoverRadius: 4,
        pointBackgroundColor: '#00FF00',
        pointBorderColor: '#FFFFFF',
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
        borderWidth: 2,
        borderDash: [3, 6],
        pointRadius: 3,
        pointHoverRadius: 6,
        pointBackgroundColor: '#4AF6C3',
        pointBorderColor: '#000000',
        pointBorderWidth: 1,
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
          color: '#333333',
          lineWidth: 1,
        },
        ticks: {
          color: '#FFA500',
          font: {
            family: 'monospace',
            size: 12,
            weight: 'normal'
          },
          maxTicksLimit: 12,
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
        grid: {
          color: '#333333',
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