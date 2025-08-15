import React, { useMemo } from "react";

export default function PerformanceAnalytics({ actualArray = [], predictedArray = [] }) {
  const analytics = useMemo(() => {
    // Ensure we have arrays and they contain data
    const actual = Array.isArray(actualArray) ? actualArray.filter(v => v !== null && v !== undefined && !isNaN(v)) : [];
    const predicted = Array.isArray(predictedArray) ? predictedArray.filter(v => v !== null && v !== undefined && !isNaN(v)) : [];
    
    if (actual.length < 2 || predicted.length < 2) {
      return null;
    }
    
    const minLength = Math.min(actual.length, predicted.length);
    const actualData = actual.slice(-minLength);
    const predictedData = predicted.slice(-minLength);
    
    try {
      // Basic error metrics
      const errors = actualData.map((a, i) => Math.abs(a - predictedData[i]));
      const squaredErrors = actualData.map((a, i) => Math.pow(a - predictedData[i], 2));
      const percentErrors = actualData.map((a, i) => Math.abs((a - predictedData[i]) / a) * 100);
      
      const mae = errors.reduce((sum, err) => sum + err, 0) / errors.length;
      const rmse = Math.sqrt(squaredErrors.reduce((sum, err) => sum + err, 0) / squaredErrors.length);
      const mape = percentErrors.reduce((sum, err) => sum + err, 0) / percentErrors.length;
      
      // Direction accuracy (trend prediction)
      let correctDirections = 0;
      let totalDirections = 0;
      for (let i = 1; i < minLength; i++) {
        const actualDirection = actualData[i] > actualData[i-1];
        const predictedDirection = predictedData[i] > actualData[i-1];
        if (actualDirection === predictedDirection) correctDirections++;
        totalDirections++;
      }
      const directionAccuracy = totalDirections > 0 ? (correctDirections / totalDirections) * 100 : 0;
      
      // Correlation coefficient
      const meanActual = actualData.reduce((sum, val) => sum + val, 0) / actualData.length;
      const meanPredicted = predictedData.reduce((sum, val) => sum + val, 0) / predictedData.length;
      
      let numerator = 0, denomActual = 0, denomPredicted = 0;
      for (let i = 0; i < actualData.length; i++) {
        numerator += (actualData[i] - meanActual) * (predictedData[i] - meanPredicted);
        denomActual += Math.pow(actualData[i] - meanActual, 2);
        denomPredicted += Math.pow(predictedData[i] - meanPredicted, 2);
      }
      const correlation = (denomActual === 0 || denomPredicted === 0) ? 0 : 
        numerator / Math.sqrt(denomActual * denomPredicted);
      
      // R-squared
      const totalSumSquares = actualData.reduce((sum, val) => sum + Math.pow(val - meanActual, 2), 0);
      const residualSumSquares = squaredErrors.reduce((sum, err) => sum + err, 0);
      const rSquared = totalSumSquares === 0 ? 0 : 1 - (residualSumSquares / totalSumSquares);
      
      // Trading performance simulation
      let tradingReturns = 0;
      let benchmarkReturns = 0;
      let correctTrades = 0;
      let totalTrades = 0;
      
      for (let i = 1; i < minLength; i++) {
        const actualReturn = (actualData[i] - actualData[i-1]) / actualData[i-1];
        const predictedDirection = predictedData[i] > actualData[i-1] ? 1 : -1;
        
        // Simulate trading based on prediction direction
        const tradeReturn = predictedDirection * actualReturn;
        tradingReturns += tradeReturn;
        benchmarkReturns += actualReturn; // Buy and hold
        
        if (tradeReturn > 0) correctTrades++;
        totalTrades++;
      }
      
      const winRate = totalTrades > 0 ? (correctTrades / totalTrades) * 100 : 0;
      const strategyReturn = tradingReturns * 100;
      const benchmarkReturn = benchmarkReturns * 100;
      const alpha = strategyReturn - benchmarkReturn;
      
      // Volatility metrics
      const actualReturns = [];
      for (let i = 1; i < actualData.length; i++) {
        actualReturns.push((actualData[i] - actualData[i-1]) / actualData[i-1]);
      }
      const volatility = actualReturns.length > 0 ? 
        Math.sqrt(actualReturns.reduce((sum, ret) => sum + Math.pow(ret, 2), 0) / actualReturns.length) * Math.sqrt(252) * 100 : 0;
      
      // Prediction consistency
      const predictionChanges = [];
      for (let i = 1; i < predictedData.length; i++) {
        predictionChanges.push(Math.abs(predictedData[i] - predictedData[i-1]));
      }
      const avgPredictionChange = predictionChanges.length > 0 ? 
        predictionChanges.reduce((sum, change) => sum + change, 0) / predictionChanges.length : 0;
      const predictionStability = meanPredicted > 0 ? 
        Math.max(0, 100 - (avgPredictionChange / meanPredicted * 100)) : 0;
      
      return {
        mae: mae.toFixed(2),
        rmse: rmse.toFixed(2),
        mape: mape.toFixed(1),
        directionAccuracy: directionAccuracy.toFixed(1),
        correlation: correlation.toFixed(3),
        rSquared: rSquared.toFixed(3),
        winRate: winRate.toFixed(1),
        strategyReturn: strategyReturn.toFixed(2),
        benchmarkReturn: benchmarkReturn.toFixed(2),
        alpha: alpha.toFixed(2),
        volatility: volatility.toFixed(1),
        predictionStability: predictionStability.toFixed(1),
        sampleSize: minLength,
        correctTrades,
        totalTrades
      };
    } catch (error) {
      console.error('Error calculating analytics:', error);
      return null;
    }
  }, [actualArray, predictedArray]);

  if (!analytics) {
    return (
      <div className="bg-black border border-orange-400 p-6">
        <div className="text-center">
          <div className="text-orange-400 font-mono text-xl font-bold mb-4">
            STRATEGY ANALYTICS
          </div>
          <div className="text-yellow-400 font-mono text-lg mb-2">
            WAITING FOR SUFFICIENT DATA...
          </div>
          <div className="text-gray-400 font-mono text-sm">
            Need at least 2 data points for both actual and predicted prices
          </div>
          <div className="mt-4 text-xs text-gray-500">
            Current data: {Array.isArray(actualArray) ? actualArray.filter(v => v !== null).length : 0} actual, {Array.isArray(predictedArray) ? predictedArray.filter(v => v !== null).length : 0} predicted
          </div>
        </div>
      </div>
    );
  }

  const getPerformanceColor = (metric, value) => {
    const thresholds = {
      directionAccuracy: { excellent: 65, good: 55, poor: 45 },
      correlation: { excellent: 0.8, good: 0.6, poor: 0.4 },
      mape: { excellent: 2, good: 5, poor: 10 }, // Lower is better
      rSquared: { excellent: 0.7, good: 0.5, poor: 0.3 },
      winRate: { excellent: 60, good: 52, poor: 45 },
      alpha: { excellent: 1, good: 0, poor: -1 },
      predictionStability: { excellent: 85, good: 70, poor: 50 }
    };

    const threshold = thresholds[metric];
    if (!threshold) return 'text-white';

    const numValue = parseFloat(value);
    
    if (metric === 'mape') {
      // Lower is better for MAPE
      if (numValue <= threshold.excellent) return 'text-green-400';
      if (numValue <= threshold.good) return 'text-yellow-400';
      return 'text-red-400';
    } else {
      // Higher is better for other metrics
      if (numValue >= threshold.excellent) return 'text-green-400';
      if (numValue >= threshold.good) return 'text-yellow-400';
      return 'text-red-400';
    }
  };

  return (
    <div className="bg-black border border-orange-400 font-mono text-white">
      {/* Header */}
      <div className="bg-orange-400 text-black p-2 font-bold text-center">
        STRATEGY PERFORMANCE ANALYTICS
      </div>
      
      {/* Main metrics grid */}
      <div className="p-4 grid grid-cols-3 gap-6">
        
        {/* Prediction Accuracy */}
        <div className="border border-orange-400 p-3">
          <div className="text-orange-400 text-sm font-bold mb-3 border-b border-orange-400 pb-1">
            PREDICTION ACCURACY
          </div>
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span>Direction Accuracy:</span>
              <span className={`font-bold ${getPerformanceColor('directionAccuracy', analytics.directionAccuracy)}`}>
                {analytics.directionAccuracy}%
              </span>
            </div>
            <div className="flex justify-between">
              <span>Correlation:</span>
              <span className={`font-bold ${getPerformanceColor('correlation', analytics.correlation)}`}>
                {analytics.correlation}
              </span>
            </div>
            <div className="flex justify-between">
              <span>R-Squared:</span>
              <span className={`font-bold ${getPerformanceColor('rSquared', analytics.rSquared)}`}>
                {analytics.rSquared}
              </span>
            </div>
            <div className="flex justify-between">
              <span>MAPE:</span>
              <span className={`font-bold ${getPerformanceColor('mape', analytics.mape)}`}>
                {analytics.mape}%
              </span>
            </div>
          </div>
        </div>

        {/* Error Metrics */}
        <div className="border border-orange-400 p-3">
          <div className="text-orange-400 text-sm font-bold mb-3 border-b border-orange-400 pb-1">
            ERROR METRICS
          </div>
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span>Mean Abs Error:</span>
              <span className="font-bold text-white">${analytics.mae}</span>
            </div>
            <div className="flex justify-between">
              <span>Root Mean Sq Err:</span>
              <span className="font-bold text-white">${analytics.rmse}</span>
            </div>
            <div className="flex justify-between">
              <span>Mean Abs Pct Err:</span>
              <span className="font-bold text-white">{analytics.mape}%</span>
            </div>
            <div className="flex justify-between">
              <span>Sample Size:</span>
              <span className="font-bold text-white">{analytics.sampleSize}</span>
            </div>
          </div>
        </div>

        {/* Trading Performance */}
        <div className="border border-orange-400 p-3">
          <div className="text-orange-400 text-sm font-bold mb-3 border-b border-orange-400 pb-1">
            TRADING SIMULATION
          </div>
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span>Strategy Return:</span>
              <span className={`font-bold ${parseFloat(analytics.strategyReturn) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {parseFloat(analytics.strategyReturn) >= 0 ? '+' : ''}{analytics.strategyReturn}%
              </span>
            </div>
            <div className="flex justify-between">
              <span>Benchmark Return:</span>
              <span className={`font-bold ${parseFloat(analytics.benchmarkReturn) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {parseFloat(analytics.benchmarkReturn) >= 0 ? '+' : ''}{analytics.benchmarkReturn}%
              </span>
            </div>
            <div className="flex justify-between">
              <span>Alpha:</span>
              <span className={`font-bold ${getPerformanceColor('alpha', analytics.alpha)}`}>
                {parseFloat(analytics.alpha) >= 0 ? '+' : ''}{analytics.alpha}%
              </span>
            </div>
            <div className="flex justify-between">
              <span>Win Rate:</span>
              <span className={`font-bold ${getPerformanceColor('winRate', analytics.winRate)}`}>
                {analytics.winRate}%
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Risk Metrics */}
      <div className="p-4 border-t border-orange-400">
        <div className="grid grid-cols-4 gap-6">
          <div className="text-center">
            <div className="text-orange-400 text-xs font-bold">MARKET VOLATILITY</div>
            <div className="text-lg font-bold text-white">{analytics.volatility}%</div>
          </div>
          <div className="text-center">
            <div className="text-orange-400 text-xs font-bold">PREDICTION STABILITY</div>
            <div className={`text-lg font-bold ${getPerformanceColor('predictionStability', analytics.predictionStability)}`}>
              {analytics.predictionStability}%
            </div>
          </div>
          <div className="text-center">
            <div className="text-orange-400 text-xs font-bold">SUCCESSFUL TRADES</div>
            <div className="text-lg font-bold text-white">{analytics.correctTrades}/{analytics.totalTrades}</div>
          </div>
          <div className="text-center">
            <div className="text-orange-400 text-xs font-bold">STRATEGY STATUS</div>
            <div className={`text-lg font-bold ${parseFloat(analytics.alpha) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {parseFloat(analytics.alpha) >= 0 ? 'OUTPERFORM' : 'UNDERPERFORM'}
            </div>
          </div>
        </div>
      </div>

      {/* Strategy insights */}
      <div className="bg-gray-900 border-t border-orange-400 p-3">
        <div className="text-orange-400 text-xs font-bold mb-2">STRATEGY INSIGHTS:</div>
        <div className="text-xs space-y-1">
          {parseFloat(analytics.directionAccuracy) >= 60 && (
            <div className="text-green-400">• Excellent trend prediction capability</div>
          )}
          {parseFloat(analytics.correlation) >= 0.7 && (
            <div className="text-green-400">• Strong correlation with actual prices</div>
          )}
          {parseFloat(analytics.alpha) >= 1 && (
            <div className="text-green-400">• Strategy generating significant alpha</div>
          )}
          {parseFloat(analytics.mape) <= 3 && (
            <div className="text-green-400">• Low prediction error - high precision</div>
          )}
          {parseFloat(analytics.predictionStability) >= 80 && (
            <div className="text-green-400">• Stable and consistent predictions</div>
          )}
          
          {parseFloat(analytics.directionAccuracy) < 50 && (
            <div className="text-red-400">• Direction accuracy below random - review model</div>
          )}
          {parseFloat(analytics.correlation) < 0.4 && (
            <div className="text-red-400">• Low correlation - model may need retraining</div>
          )}
          {parseFloat(analytics.alpha) < -1 && (
            <div className="text-red-400">• Strategy underperforming benchmark significantly</div>
          )}
          
          {/* Show data status */}
          <div className="text-gray-400 mt-2 pt-2 border-t border-gray-700">
            Data quality: {analytics.sampleSize} samples analyzed
          </div>
        </div>
      </div>
    </div>
  );
}