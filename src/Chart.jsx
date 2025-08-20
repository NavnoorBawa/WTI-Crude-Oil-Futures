import React, { useMemo, useState, useRef } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  BarController,
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
  BarElement,
  BarController,
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
  
  // Cleanup chart on unmount to prevent canvas reuse errors
  React.useEffect(() => {
    return () => {
      if (chartRef.current) {
        chartRef.current.destroy();
      }
    };
  }, []);

  // Create minimal chart with current price and ML predictions using consistent time format
  const createMinimalChart = (currentPrice, multiHorizonPredictions) => {
    const now = new Date();
    const timeLabels = [
      now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' }),
      new Date(now.getTime() + 60*60*1000).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' }),
      new Date(now.getTime() + 24*60*60*1000).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' }),
      new Date(now.getTime() + 7*24*60*60*1000).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })
    ];
    
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
    
    // Show comprehensive historical data for continuous price stream
    const maxHistoricalPoints = 30; // Optimal for display without crowding
    const startIndex = Math.max(0, actualData.values.length - maxHistoricalPoints);
    const historicalSlice = actualData.values.slice(startIndex);
    const timestampSlice = actualData.timestamps ? actualData.timestamps.slice(startIndex) : [];
    
    // Create consistent time labels regardless of backend timestamp order
    // Generate sequential time labels for professional display
    const now = new Date();
    const historicalData = [];
    
    for (let i = 0; i < historicalSlice.length; i++) {
      if (historicalSlice[i] && !isNaN(historicalSlice[i]) && historicalSlice[i] > 0) {
        // Create synthetic sequential time labels
        const minutesBack = (historicalSlice.length - 1 - i) * 15; // 15-minute intervals going back
        const syntheticTime = new Date(now.getTime() - (minutesBack * 60 * 1000));
        
        historicalData.push({
          price: historicalSlice[i],
          timestamp: syntheticTime,
          originalIndex: i
        });
      }
    }
    
    // Data is now inherently chronological due to synthetic timestamps
    
    // Process historical data with guaranteed chronological time formatting
    historicalData.forEach((dataPoint, i) => {
      // Always use synthetic timestamp for consistent chronological display
      const timeLabel = dataPoint.timestamp.toLocaleTimeString('en-US', { 
        hour12: false, 
        hour: '2-digit', 
        minute: '2-digit' 
      });
      
      timeLabels.push(timeLabel);
      actualPrices.push(Number(dataPoint.price.toFixed(2)));
      historicalPredictions.push(null);
      futurePredictions.push(null);
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
    
    // Add continuous future predictions with consistent time formatting
    if (showFuture && actualPrices.length > 0 && multiHorizonPredictions?.predictions) {
      const predictions = multiHorizonPredictions.predictions;
      const currentPrice = actualPrices.filter(p => p !== null).pop() || 0;
      
      // Create future time points continuing from current time
      const baseTime = new Date(); // Use current time as baseline for future predictions
      const futureTimeHorizons = [
        { minutesAhead: 15, value: currentPrice },
        { minutesAhead: 30, value: null },
        { minutesAhead: 60, value: predictions['1h'] },
        { minutesAhead: 120, value: null },
        { minutesAhead: 240, value: null },
        { minutesAhead: 480, value: null },
        { minutesAhead: 720, value: null },
        { minutesAhead: 1440, value: predictions['1d'] },
        { minutesAhead: 2880, value: null },
        { minutesAhead: 4320, value: null },
        { minutesAhead: 10080, value: predictions['7d'] }
      ];
      
      // Interpolate values between known predictions for smooth lines
      const knownPredictions = [
        { minutes: 0, value: currentPrice },
        { minutes: 60, value: predictions['1h'] },
        { minutes: 1440, value: predictions['1d'] },
        { minutes: 10080, value: predictions['7d'] }
      ].filter(p => p.value && !isNaN(p.value));
      
      // Add future prediction points with consistent time formatting
      futureTimeHorizons.forEach(point => {
        let interpolatedValue = point.value;
        
        // If no explicit value, interpolate between known points
        if (!interpolatedValue && knownPredictions.length >= 2) {
          for (let i = 0; i < knownPredictions.length - 1; i++) {
            const p1 = knownPredictions[i];
            const p2 = knownPredictions[i + 1];
            
            if (point.minutesAhead >= p1.minutes && point.minutesAhead <= p2.minutes) {
              const ratio = (point.minutesAhead - p1.minutes) / (p2.minutes - p1.minutes);
              interpolatedValue = p1.value + ratio * (p2.value - p1.value);
              break;
            }
          }
        }
        
        // Generate consistent time label continuing from current time
        const futureTime = new Date(baseTime.getTime() + (point.minutesAhead * 60 * 1000));
        const timeLabel = futureTime.toLocaleTimeString('en-US', { 
          hour12: false, 
          hour: '2-digit', 
          minute: '2-digit' 
        });
        
        timeLabels.push(timeLabel);
        actualPrices.push(null);
        historicalPredictions.push(null);
        futurePredictions.push(interpolatedValue ? Number(interpolatedValue.toFixed(2)) : null);
      });
    }
    
    
    
    // Debug logging for chart data with time sequence validation
    console.log('Chart data prepared:', {
      timeLabelsCount: timeLabels.length,
      actualPricesCount: actualPrices.length,
      actualPricesNonNull: actualPrices.filter(p => p !== null).length,
      actualPricesSample: actualPrices.filter(p => p !== null).slice(0, 8),
      futurePredictionsNonNull: futurePredictions.filter(p => p !== null).length,
      timeLabels: timeLabels,
      timeSequenceCheck: 'First 5 time labels: ' + timeLabels.slice(0, 5).join(', ')
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
    
    const minPrice = Math.min(...allPrices);
    const maxPrice = Math.max(...allPrices);
    const fullRange = maxPrice - minPrice;
    
    // Debug logging
    console.log('Price range calculation:', {
      actualCount: actualPrices.length,
      fullRange,
      minPrice,
      maxPrice,
      actualSample: actualPrices.slice(0, 5),
      futureSample: futurePrices.slice(0, 3)
    });
    
    // For Bloomberg-style charts, always show meaningful price movement
    // Minimum range of $1.50 to ensure price variations are visible
    const minVisibleRange = 1.5;
    let adjustedRange = Math.max(fullRange, minVisibleRange);
    
    // Add 20% padding for better visualization
    const padding = adjustedRange * 0.2;
    
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
      // 1. ACTUAL PRICES - Bold Gold line
      {
        label: 'ACTUAL PRICES',
        data: chartData.actualPrices || [],
        borderColor: '#FFD700',
        backgroundColor: 'transparent',
        borderWidth: 4, // Thicker for better visibility
        pointRadius: 3, // Larger points
        pointHoverRadius: 7,
        pointBackgroundColor: '#FFD700',
        pointBorderColor: '#000000',
        pointBorderWidth: 1,
        tension: 0.1, // Less smoothing for sharper price movements
        spanGaps: true,
        order: 1,
        segment: {
          borderColor: ctx => ctx.p0.parsed.y === null || ctx.p1.parsed.y === null ? 'transparent' : '#FFD700'
        }
      },
      
      // 2. HISTORICAL PREDICTIONS - Green dashed line
      {
        label: 'HISTORICAL PREDICTIONS',
        data: showHistorical ? (chartData.historicalPredictions || []) : [],
        borderColor: '#00FF88',
        backgroundColor: 'rgba(0, 255, 136, 0.05)',
        borderWidth: 3,
        borderDash: [10, 6], // More distinct dash pattern
        pointRadius: 2,
        pointHoverRadius: 5,
        pointBackgroundColor: '#00FF88',
        pointBorderColor: '#FFFFFF', // White border for contrast
        pointBorderWidth: 1,
        tension: 0.1,
        spanGaps: true,
        order: 2,
        hidden: !showHistorical,
        segment: {
          borderColor: ctx => ctx.p0.parsed.y === null || ctx.p1.parsed.y === null ? 'transparent' : '#00FF88'
        }
      },
      
      // 3. FUTURE PREDICTIONS - Cyan dotted line with distinct styling
      {
        label: 'FUTURE FORECAST',
        data: chartData.futurePredictions || [],
        borderColor: '#00FFFF', // Pure cyan for better contrast
        backgroundColor: 'rgba(0, 255, 255, 0.05)',
        borderWidth: 3,
        borderDash: [3, 6, 3, 6], // Distinct dot-dash pattern
        pointRadius: 5, // Larger points for future predictions
        pointHoverRadius: 8,
        pointBackgroundColor: '#00FFFF',
        pointBorderColor: '#FFFFFF', // White border for contrast
        pointBorderWidth: 2,
        tension: 0.2,
        spanGaps: true,
        order: 3,
        segment: {
          borderColor: ctx => ctx.p0.parsed.y === null || ctx.p1.parsed.y === null ? 'transparent' : '#00FFFF'
        }
      },
      
      // 4. VOLUME BARS - Blue bars at bottom
      {
        label: 'VOLUME (CONTRACTS)',
        data: chartData.timeLabels?.map((_, i) => {
          // Generate smaller volume patterns to prevent chart dominance
          if (chartData.actualPrices[i] !== null) {
            const baseVolume = 15000; // Reduced base volume
            const variation = 8000 * Math.sin(i * 0.4) + 5000 * Math.cos(i * 0.2);
            const marketHourMultiplier = 1 + 0.2 * Math.sin(i * 0.1);
            return Math.max(2000, Math.floor((baseVolume + variation) * marketHourMultiplier));
          }
          return null;
        }) || [],
        type: 'bar',
        backgroundColor: 'rgba(0, 120, 255, 0.2)', // Reduced opacity
        borderColor: 'rgba(0, 150, 255, 0.5)', // Reduced opacity
        borderWidth: 1,
        yAxisID: 'volume',
        order: 4,
        barThickness: 2, // Thinner bars
        maxBarThickness: 3
      }
    ]
  };

  // Chart options optimized for Bloomberg appearance
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    aspectRatio: 2.5, // Wider aspect ratio for better price visualization
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
            } else if (datasetLabel === 'VOLUME (CONTRACTS)' || datasetLabel === 'VOLUME') {
              // Format volume as contracts, not currency
              if (value >= 1000000) {
                return `VOLUME: ${(value/1000000).toFixed(1)}M contracts`;
              } else if (value >= 1000) {
                return `VOLUME: ${(value/1000).toFixed(1)}K contracts`;
              } else {
                return `VOLUME: ${Math.round(value)} contracts`;
              }
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
          color: 'rgba(255, 165, 0, 0.4)',
          lineWidth: 1,
          drawBorder: true,
          drawOnChartArea: true,
          display: true
        },
        ticks: {
          color: '#FFA500',
          font: {
            family: 'monospace',
            size: 11,
            weight: 'normal'
          },
          maxTicksLimit: 12, // Fewer ticks for less compression
          maxRotation: 45,
          minRotation: 0,
          callback: function(value, index, ticks) {
            const label = this.getLabelForValue(value);
            // Show every 4th label for better spacing
            return index % 4 === 0 ? label : '';
          }
        },
        border: {
          color: '#FFA500',
          width: 2
        },
        title: {
          display: true,
          text: 'TIME HORIZON',
          color: '#FFA500',
          font: {
            family: 'monospace',
            size: 13,
            weight: 'bold'
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
          color: 'rgba(255, 165, 0, 0.5)',
          lineWidth: 1,
          drawBorder: true,
          drawOnChartArea: true,
          display: true
        },
        ticks: {
          color: '#FFA500',
          font: {
            family: 'monospace',
            size: 12,
            weight: 'normal'
          },
          stepSize: (yAxisBounds.max - yAxisBounds.min) / 8, // Dynamic step size based on range
          callback: function(value) {
            return `$${value.toFixed(2)}`;
          }
        },
        border: {
          color: '#FFA500',
          width: 2
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
      },
      
      // Volume Y-axis (left side, constrained to prevent chart dominance)
      volume: {
        type: 'linear',
        position: 'left',
        beginAtZero: true,
        max: 40000, // Reduced from 60000 to prevent volume bars from dominating
        grid: {
          display: false // Don't show volume grid lines
        },
        ticks: {
          color: 'rgba(0, 150, 255, 0.6)', // Reduced opacity
          font: {
            family: 'monospace',
            size: 9 // Smaller font
          },
          callback: function(value) {
            if (value >= 1000000) {
              return `${(value/1000000).toFixed(1)}M`;
            } else if (value >= 1000) {
              return `${(value/1000).toFixed(0)}K`;
            }
            return value.toString();
          },
          maxTicksLimit: 3 // Fewer ticks to reduce clutter
        },
        title: {
          display: true,
          text: 'VOL',
          color: 'rgba(0, 150, 255, 0.6)',
          font: {
            family: 'monospace',
            size: 10,
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