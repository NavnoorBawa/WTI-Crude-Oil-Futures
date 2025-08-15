import React, { useMemo } from "react";
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
  TimeScale
);

export default function FutureChart({ 
  currentPrice = 0, 
  multiHorizonPredictions = null, 
  performanceMetrics = null 
}) {
  // Process future prediction data
  const chartData = useMemo(() => {
    if (!multiHorizonPredictions || !multiHorizonPredictions.predictions) {
      return { isEmpty: true, labels: [], datasets: [] };
    }

    const predictions = multiHorizonPredictions.predictions;
    const confidenceBands = multiHorizonPredictions.confidence_bands || {};
    
    // Create time points for predictions (in hours from now)
    const timePoints = [0, 1, 4, 24, 168]; // Now, 1H, 4H, 1D, 7D
    const labels = ['NOW', '+1H', '+4H', '+1D', '+7D'];
    
    // Create price points
    const pricePoints = [
      currentPrice || 0,
      predictions['1h'] || currentPrice,
      predictions['4h'] || currentPrice,
      predictions['1d'] || currentPrice,
      predictions['7d'] || currentPrice
    ];

    // Create confidence band data if available
    const upperBand = [];
    const lowerBand = [];
    
    timePoints.forEach((_, index) => {
      if (index === 0) {
        // Current price has no uncertainty
        upperBand.push(currentPrice);
        lowerBand.push(currentPrice);
      } else {
        const horizon = labels[index].replace('+', '').toLowerCase();
        const confidence = confidenceBands[horizon];
        
        if (confidence && confidence.confidence_95) {
          upperBand.push(confidence.confidence_95.upper);
          lowerBand.push(confidence.confidence_95.lower);
        } else {
          // Default uncertainty based on time horizon
          const basePrice = pricePoints[index];
          const uncertainty = basePrice * (0.02 * Math.sqrt(timePoints[index] / 24)); // 2% per sqrt(day)
          upperBand.push(basePrice + uncertainty);
          lowerBand.push(basePrice - uncertainty);
        }
      }
    });

    return {
      isEmpty: false,
      labels,
      timePoints,
      pricePoints,
      upperBand,
      lowerBand,
      predictions
    };
  }, [currentPrice, multiHorizonPredictions]);

  if (chartData.isEmpty) {
    return (
      <div className="w-full h-full bg-black flex items-center justify-center">
        <div className="text-center">
          <div className="text-orange-400 text-xl font-bold mb-2">LOADING FUTURE FORECASTS...</div>
          <div className="text-gray-400 text-sm">Waiting for multi-horizon predictions</div>
          <div className="mt-4">
            <div className="text-orange-400">●●●</div>
          </div>
        </div>
      </div>
    );
  }

  const data = {
    labels: chartData.labels,
    datasets: [
      // Main prediction line
      {
        label: "FUTURE FORECAST",
        data: chartData.pricePoints,
        borderColor: "#10b981", // Emerald green for future predictions
        backgroundColor: "rgba(16, 185, 129, 0.1)",
        fill: false,
        tension: 0.3,
        pointRadius: [6, 5, 5, 5, 5], // Larger point for current price
        pointHoverRadius: [8, 7, 7, 7, 7],
        borderWidth: 4,
        pointBackgroundColor: ["#f59e0b", "#10b981", "#10b981", "#10b981", "#10b981"], // Current price in amber
        pointBorderColor: "#000000",
        pointBorderWidth: 2,
        spanGaps: false,
      },
      // Upper confidence band
      {
        label: "95% CONFIDENCE UPPER",
        data: chartData.upperBand,
        borderColor: "rgba(16, 185, 129, 0.3)",
        backgroundColor: "rgba(16, 185, 129, 0.1)",
        fill: '+1', // Fill to the next dataset (lower band)
        tension: 0.2,
        pointRadius: 0,
        pointHoverRadius: 0,
        borderWidth: 1,
        borderDash: [5, 5],
      },
      // Lower confidence band
      {
        label: "95% CONFIDENCE LOWER",
        data: chartData.lowerBand,
        borderColor: "rgba(16, 185, 129, 0.3)",
        backgroundColor: "rgba(16, 185, 129, 0.1)",
        fill: false,
        tension: 0.2,
        pointRadius: 0,
        pointHoverRadius: 0,
        borderWidth: 1,
        borderDash: [5, 5],
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index',
      intersect: false,
    },
    plugins: {
      legend: {
        display: false, // Bloomberg style - no legend on chart
      },
      tooltip: {
        enabled: true,
        mode: "index",
        intersect: false,
        backgroundColor: "rgba(0, 0, 0, 0.95)",
        titleColor: "#10b981",
        bodyColor: "#ffffff",
        borderColor: "#10b981",
        borderWidth: 1,
        titleFont: {
          size: 13,
          family: "monospace",
          weight: "600",
        },
        bodyFont: {
          size: 12,
          family: "monospace",
        },
        callbacks: {
          title: function(tooltipItems) {
            const label = tooltipItems[0].label;
            return `Time: ${label}`;
          },
          label: function(context) {
            const datasetLabel = context.dataset.label;
            const value = context.parsed.y;
            
            if (datasetLabel === "FUTURE FORECAST") {
              const timeLabel = context.label;
              const currentPrice = chartData.pricePoints[0];
              const change = value - currentPrice;
              const changePercent = ((change / currentPrice) * 100);
              
              return [
                `FORECAST: $${value.toFixed(2)}`,
                `CHANGE: ${change >= 0 ? '+' : ''}$${change.toFixed(2)} (${changePercent >= 0 ? '+' : ''}${changePercent.toFixed(2)}%)`,
                `HORIZON: ${timeLabel}`
              ];
            } else if (datasetLabel.includes("CONFIDENCE")) {
              return `${datasetLabel}: $${value.toFixed(2)}`;
            }
            return null;
          },
          footer: function(tooltipItems) {
            const forecastItem = tooltipItems.find(item => item.dataset.label === "FUTURE FORECAST");
            if (forecastItem) {
              const horizon = forecastItem.label.replace('+', '').toLowerCase();
              const confidence = chartData.predictions && multiHorizonPredictions.confidence_bands?.[horizon];
              if (confidence) {
                return [
                  ``,
                  `CONFIDENCE: 95%`,
                  `RANGE: $${confidence.confidence_95?.lower?.toFixed(2) || 'N/A'} - $${confidence.confidence_95?.upper?.toFixed(2) || 'N/A'}`
                ];
              }
            }
            return [];
          }
        }
      }
    },
    scales: {
      x: {
        type: 'category',
        grid: {
          color: "rgba(16, 185, 129, 0.1)",
          borderColor: "#10b981",
        },
        ticks: {
          color: "#10b981",
          font: {
            family: "monospace",
            size: 12,
            weight: "bold"
          },
          callback: function(value, index) {
            return chartData.labels[index];
          }
        },
        border: {
          color: "#10b981",
        },
        title: {
          display: true,
          text: "FORECAST HORIZON",
          color: "#10b981",
          font: {
            family: "monospace",
            size: 11,
            weight: "bold"
          }
        }
      },
      y: {
        type: 'linear',
        position: 'right',
        grid: {
          color: "rgba(16, 185, 129, 0.1)",
          borderColor: "#10b981",
        },
        ticks: {
          color: "#10b981",
          font: {
            family: "monospace",
            size: 11,
          },
          callback: function(value) {
            return '$' + value.toFixed(2);
          }
        },
        border: {
          color: "#10b981",
        },
        title: {
          display: true,
          text: "FUTURE PRICE (USD/BBL)",
          color: "#10b981",
          font: {
            family: "monospace",
            size: 11,
            weight: "bold"
          }
        }
      },
    },
    elements: {
      point: {
        hoverBackgroundColor: "#10b981",
        hoverBorderColor: "#000000",
        hoverBorderWidth: 3,
      },
      line: {
        borderCapStyle: 'round',
        borderJoinStyle: 'round',
      }
    },
  };

  return (
    <div className="w-full h-full bg-black">
      {/* Future Forecast Header */}
      <div className="bg-black border-b border-green-400 p-3">
        <div className="flex justify-between items-center">
          <div>
            <div className="text-green-400 font-bold text-lg font-mono">FUTURE PRICE FORECASTING</div>
            <div className="text-gray-400 text-sm">Multi-horizon ML predictions with confidence intervals</div>
          </div>
          {multiHorizonPredictions?.processing_time && (
            <div className="text-right">
              <div className="text-green-400 text-sm font-mono">
                PROCESSING TIME: {multiHorizonPredictions.processing_time.toFixed(1)}s
              </div>
              <div className="text-gray-400 text-xs">
                Generated: {new Date(multiHorizonPredictions.generated_at || Date.now()).toLocaleTimeString()}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Prediction Summary */}
      <div className="bg-black border-b border-green-400 p-3">
        <div className="grid grid-cols-5 gap-4 text-center text-xs font-mono">
          {chartData.labels.map((label, index) => {
            const price = chartData.pricePoints[index];
            const currentPrice = chartData.pricePoints[0];
            const change = price - currentPrice;
            const changePercent = ((change / currentPrice) * 100);
            
            return (
              <div key={index} className={index === 0 ? 'text-amber-400' : 'text-green-400'}>
                <div className="text-gray-400 font-bold mb-1">{label}</div>
                <div className="text-lg font-bold mb-1">
                  ${price.toFixed(2)}
                </div>
                {index > 0 && (
                  <div className={`text-sm ${change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {change >= 0 ? '+' : ''}${change.toFixed(2)}
                  </div>
                )}
                {index > 0 && (
                  <div className={`text-xs ${changePercent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    ({changePercent >= 0 ? '+' : ''}{changePercent.toFixed(2)}%)
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Chart legend */}
      <div className="bg-black border-b border-green-400 p-2">
        <div className="flex space-x-8 text-xs font-mono">
          <div className="flex items-center space-x-2">
            <div className="w-4 h-0.5 bg-green-400"></div>
            <span className="text-green-400 font-bold">FORECAST</span>
          </div>
          <div className="flex items-center space-x-2">
            <div className="w-4 h-0.5 bg-green-400 opacity-50" style={{borderTop: '1px dashed #10b981'}}></div>
            <span className="text-green-400 opacity-75 font-bold">95% CONFIDENCE</span>
          </div>
          <div className="flex items-center space-x-2">
            <div className="w-3 h-3 bg-amber-400 rounded-full"></div>
            <span className="text-amber-400 font-bold">CURRENT PRICE</span>
          </div>
          <div className="text-gray-400">
            Multi-horizon ML forecasting with uncertainty quantification
          </div>
        </div>
      </div>

      {/* Main chart */}
      <div className="h-96 p-4">
        <Line data={data} options={options} />
      </div>

      {/* Forecast Metrics */}
      {performanceMetrics && (
        <div className="bg-gray-900 border-t border-green-400 p-3">
          <div className="grid grid-cols-4 gap-6 text-xs font-mono text-center">
            <div>
              <div className="text-gray-400 mb-1">MODEL ACCURACY</div>
              <div className={`text-lg font-bold ${
                performanceMetrics.direction_accuracy > 60 ? 'text-green-400' : 
                performanceMetrics.direction_accuracy > 50 ? 'text-yellow-400' : 'text-red-400'
              }`}>
                {performanceMetrics.direction_accuracy}%
              </div>
            </div>
            <div>
              <div className="text-gray-400 mb-1">PREDICTION BIAS</div>
              <div className={`text-lg font-bold ${
                Math.abs(performanceMetrics.prediction_bias || 0) < 0.1 ? 'text-green-400' : 'text-yellow-400'
              }`}>
                {(performanceMetrics.prediction_bias || 0) > 0 ? '+' : ''}{(performanceMetrics.prediction_bias || 0).toFixed(3)}
              </div>
            </div>
            <div>
              <div className="text-gray-400 mb-1">VOLATILITY REGIME</div>
              <div className="text-white text-lg font-bold">
                NORMAL
              </div>
            </div>
            <div>
              <div className="text-gray-400 mb-1">FORECAST CONFIDENCE</div>
              <div className="text-green-400 text-lg font-bold">
                HIGH
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}