import React, { useMemo, useRef, useState } from "react";
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

const parseTimestamp = (value) => {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
};

const quantile = (arr, q) => {
  if (!Array.isArray(arr) || arr.length === 0) return null;
  const sorted = [...arr].sort((a, b) => a - b);
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  if (sorted[base + 1] !== undefined) {
    return sorted[base] + rest * (sorted[base + 1] - sorted[base]);
  }
  return sorted[base];
};

const formatTimeLabel = (dateObj, firstDateObj) => {
  if (!dateObj) return "--:--";
  const sameDay =
    firstDateObj &&
    dateObj.getFullYear() === firstDateObj.getFullYear() &&
    dateObj.getMonth() === firstDateObj.getMonth() &&
    dateObj.getDate() === firstDateObj.getDate();

  if (sameDay) {
    return dateObj.toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  const dayPart = dateObj.toLocaleDateString("en-US", {
    month: "short",
    day: "2-digit",
  });
  const timePart = dateObj.toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
  return `${dayPart} ${timePart}`;
};

export default function Chart({
  actualArray = [],
  predictedArray = [],
  unifiedData = null,
  multiHorizonPredictions = null,
  currentPrice = 0,
}) {
  const [showHistorical, setShowHistorical] = useState(true);
  const [showBand, setShowBand] = useState(true);
  const chartRef = useRef();

  const chartData = useMemo(() => {
    const actualPayload = unifiedData?.actual || {};
    const predictedPayload = unifiedData?.predicted || {};

    const actualValues = Array.isArray(actualPayload.values) ? actualPayload.values : actualArray;
    const actualTimestamps = Array.isArray(actualPayload.timestamps) ? actualPayload.timestamps : [];
    const actualVolumes = Array.isArray(actualPayload.volumes) ? actualPayload.volumes : [];

    const historicalPred = predictedPayload?.historical || {};
    const historicalPredValues = Array.isArray(historicalPred.values) ? historicalPred.values : predictedArray;
    const historicalPredTimestamps = Array.isArray(historicalPred.timestamps) ? historicalPred.timestamps : [];
    const historicalPredUpper = Array.isArray(historicalPred.upper_bound) ? historicalPred.upper_bound : [];
    const historicalPredLower = Array.isArray(historicalPred.lower_bound) ? historicalPred.lower_bound : [];

    const futurePred = predictedPayload?.future || {};
    const futureValues = Array.isArray(futurePred.values) ? futurePred.values : [];
    const futureTimestamps = Array.isArray(futurePred.timestamps) ? futurePred.timestamps : [];
    const futureUpper = Array.isArray(futurePred.upper_bound) ? futurePred.upper_bound : [];
    const futureLower = Array.isArray(futurePred.lower_bound) ? futurePred.lower_bound : [];

    const pointMap = new Map();

    const ensurePoint = (dateObj) => {
      if (!dateObj) return null;
      const key = Math.floor(dateObj.getTime() / 60000);
      if (!pointMap.has(key)) {
        pointMap.set(key, {
          ts: dateObj,
          actual: null,
          histPred: null,
          futurePred: null,
          upperBand: null,
          lowerBand: null,
          volume: null,
        });
      }
      return pointMap.get(key);
    };

    for (let i = 0; i < actualValues.length; i += 1) {
      const price = Number(actualValues[i]);
      if (!Number.isFinite(price) || price <= 0) continue;

      const ts = parseTimestamp(actualTimestamps[i]) || new Date(Date.now() - (actualValues.length - i) * 15 * 60 * 1000);
      const point = ensurePoint(ts);
      if (!point) continue;

      point.actual = Number(price.toFixed(2));
      const vol = Number(actualVolumes[i]);
      if (Number.isFinite(vol) && vol > 0) {
        point.volume = Math.max(0, Math.round(vol));
      }
    }

    for (let i = 0; i < historicalPredValues.length; i += 1) {
      const pred = Number(historicalPredValues[i]);
      if (!Number.isFinite(pred) || pred <= 0) continue;
      const ts = parseTimestamp(historicalPredTimestamps[i]);
      if (!ts) continue;
      const point = ensurePoint(ts);
      if (!point) continue;

      point.histPred = Number(pred.toFixed(2));

      const upper = Number(historicalPredUpper[i]);
      const lower = Number(historicalPredLower[i]);
      if (Number.isFinite(upper)) point.upperBand = Number(upper.toFixed(2));
      if (Number.isFinite(lower)) point.lowerBand = Number(lower.toFixed(2));
    }

    for (let i = 0; i < futureValues.length; i += 1) {
      const pred = Number(futureValues[i]);
      if (!Number.isFinite(pred) || pred <= 0) continue;

      const ts = parseTimestamp(futureTimestamps[i]);
      if (!ts) continue;
      const point = ensurePoint(ts);
      if (!point) continue;

      point.futurePred = Number(pred.toFixed(2));

      const upper = Number(futureUpper[i]);
      const lower = Number(futureLower[i]);
      if (Number.isFinite(upper)) point.upperBand = Number(upper.toFixed(2));
      if (Number.isFinite(lower)) point.lowerBand = Number(lower.toFixed(2));
    }

    const hasFutureFromPayload = futureValues.length > 0;
    const forecastDict = multiHorizonPredictions?.predictions || {};
    const intervalDict = multiHorizonPredictions?.prediction_intervals || {};

    if (!hasFutureFromPayload && forecastDict && Object.keys(forecastDict).length > 0) {
      const allPointsNow = [...pointMap.values()].sort((a, b) => a.ts - b.ts);
      const baseTime = allPointsNow.length > 0 ? allPointsNow[allPointsNow.length - 1].ts : new Date();

      const syntheticHorizons = [
        { key: "1h", minutes: 60 },
        { key: "1d", minutes: 24 * 60 },
        { key: "1w", minutes: 7 * 24 * 60 },
      ];

      syntheticHorizons.forEach((h) => {
        const pred = Number(forecastDict[h.key]);
        if (!Number.isFinite(pred) || pred <= 0) return;

        const ts = new Date(baseTime.getTime() + h.minutes * 60 * 1000);
        const point = ensurePoint(ts);
        if (!point) return;
        point.futurePred = Number(pred.toFixed(2));

        const intervalObj = intervalDict[h.key] || {};
        const upper = Number(intervalObj.upper);
        const lower = Number(intervalObj.lower);
        if (Number.isFinite(upper)) point.upperBand = Number(upper.toFixed(2));
        if (Number.isFinite(lower)) point.lowerBand = Number(lower.toFixed(2));
      });
    }

    let points = [...pointMap.values()].sort((a, b) => a.ts - b.ts);
    if (points.length > 96) {
      points = points.slice(points.length - 96);
    }

    if (points.length === 0) {
      if (currentPrice > 0) {
        const now = new Date();
        points = [
          {
            ts: now,
            actual: Number(currentPrice.toFixed(2)),
            histPred: null,
            futurePred: null,
            upperBand: null,
            lowerBand: null,
            volume: null,
          },
        ];
      } else {
        return { isEmpty: true };
      }
    }

    const firstDateObj = points[0].ts;
    const labels = points.map((p) => formatTimeLabel(p.ts, firstDateObj));

    const maxBandPct = 0.18;
    points.forEach((point) => {
      const center = point.futurePred ?? point.histPred ?? point.actual;
      const upper = point.upperBand;
      const lower = point.lowerBand;
      if (!Number.isFinite(center) || !Number.isFinite(upper) || !Number.isFinite(lower)) {
        return;
      }

      const hi = Math.max(upper, lower);
      const lo = Math.min(upper, lower);
      const width = hi - lo;
      const widthPct = width / Math.max(1e-6, Math.abs(center));
      if (widthPct > maxBandPct) {
        const half = (maxBandPct * Math.abs(center)) / 2;
        point.upperBand = Number((center + half).toFixed(2));
        point.lowerBand = Number((center - half).toFixed(2));
      } else {
        point.upperBand = Number(hi.toFixed(2));
        point.lowerBand = Number(lo.toFixed(2));
      }
    });

    const actualSeries = points.map((p) => p.actual);
    const histSeries = points.map((p) => p.histPred);
    const futureSeries = points.map((p) => p.futurePred);
    const upperSeries = points.map((p) => p.upperBand);
    const lowerSeries = points.map((p) => p.lowerBand);
    const volumeSeries = points.map((p) => p.volume);

    const corePrices = [...actualSeries, ...histSeries, ...futureSeries]
      .filter((v) => v !== null && Number.isFinite(v));

    const allPrices = corePrices.length > 0
      ? corePrices
      : [...upperSeries, ...lowerSeries].filter((v) => v !== null && Number.isFinite(v));

    const minPriceRaw = allPrices.length > 0 ? Math.min(...allPrices) : Math.max(0, currentPrice - 2);
    const maxPriceRaw = allPrices.length > 0 ? Math.max(...allPrices) : currentPrice + 2;

    let minPrice = minPriceRaw;
    let maxPrice = maxPriceRaw;
    if (allPrices.length >= 12) {
      const q05 = quantile(allPrices, 0.05);
      const q95 = quantile(allPrices, 0.95);
      if (Number.isFinite(q05) && Number.isFinite(q95) && q95 > q05) {
        minPrice = Math.min(minPriceRaw, q05);
        maxPrice = Math.max(maxPriceRaw, q95);
      }
    }

    const anchor = Number.isFinite(currentPrice) && currentPrice > 0
      ? currentPrice
      : (allPrices.length > 0 ? allPrices[allPrices.length - 1] : 100);
    const minVisibleRange = Math.max(1.6, anchor * 0.02);
    const range = Math.max(minVisibleRange, maxPrice - minPrice);
    const pad = Math.max(0.5, range * 0.14);

    const bandPointCount = upperSeries.filter((v, idx) => Number.isFinite(v) && Number.isFinite(lowerSeries[idx])).length;
    const hasBand = bandPointCount >= 2;

    const hasRealVolume = volumeSeries.some((v) => Number.isFinite(v) && v > 0);
    const maxVolume = hasRealVolume
      ? Math.max(...volumeSeries.map((v) => (Number.isFinite(v) ? v : 0)))
      : 0;

    return {
      isEmpty: false,
      labels,
      firstDateObj,
      actualSeries,
      histSeries,
      futureSeries,
      upperSeries,
      lowerSeries,
      volumeSeries,
      hasRealVolume,
      hasBand,
      yMin: Math.max(0, minPrice - pad),
      yMax: maxPrice + pad,
      volumeMax: maxVolume > 0 ? Math.ceil(maxVolume * 1.25) : 1000,
    };
  }, [actualArray, predictedArray, unifiedData, multiHorizonPredictions, currentPrice]);

  const resetZoom = () => {
    if (chartRef.current) {
      chartRef.current.resetZoom();
    }
  };

  const data = {
    labels: chartData.labels || [],
    datasets: [
      {
        label: "ACTUAL PRICES",
        data: chartData.actualSeries || [],
        borderColor: "#FFD700",
        backgroundColor: "transparent",
        borderWidth: 3,
        pointRadius: 2,
        pointHoverRadius: 6,
        pointBackgroundColor: "#FFD700",
        pointBorderColor: "#000000",
        pointBorderWidth: 1,
        tension: 0.08,
        spanGaps: true,
        order: 1,
      },
      {
        label: "HISTORICAL PREDICTIONS",
        data: showHistorical ? (chartData.histSeries || []) : [],
        borderColor: "#2DFF9B",
        backgroundColor: "transparent",
        borderWidth: 2,
        borderDash: [7, 4],
        pointRadius: 1.8,
        pointHoverRadius: 5,
        pointBackgroundColor: "#2DFF9B",
        pointBorderWidth: 0,
        tension: 0.1,
        spanGaps: true,
        order: 2,
      },
      {
        label: "FUTURE FORECAST",
        data: chartData.futureSeries || [],
        borderColor: "#00F5FF",
        backgroundColor: "transparent",
        borderWidth: 2.6,
        borderDash: [2, 4],
        pointRadius: 4,
        pointHoverRadius: 7,
        pointBackgroundColor: "#00F5FF",
        pointBorderColor: "#FFFFFF",
        pointBorderWidth: 1.5,
        tension: 0.05,
        spanGaps: true,
        order: 3,
      },
      {
        label: "INTERVAL LOWER",
        data: (showBand && chartData.hasBand) ? (chartData.lowerSeries || []) : [],
        borderColor: "rgba(0, 245, 255, 0.0)",
        backgroundColor: "transparent",
        borderWidth: 0,
        pointRadius: 0,
        tension: 0.14,
        spanGaps: false,
        order: 4,
      },
      {
        label: "CONFIDENCE BAND",
        data: (showBand && chartData.hasBand) ? (chartData.upperSeries || []) : [],
        borderColor: "rgba(0, 245, 255, 0.0)",
        backgroundColor: "rgba(0, 245, 255, 0.08)",
        borderWidth: 0,
        pointRadius: 0,
        fill: "-1",
        tension: 0.14,
        spanGaps: false,
        order: 4,
      },
      ...(chartData.hasRealVolume
        ? [
            {
              label: "VOLUME (REAL)",
              data: chartData.volumeSeries || [],
              type: "bar",
              yAxisID: "volume",
              backgroundColor: "rgba(0, 128, 255, 0.20)",
              borderColor: "rgba(0, 170, 255, 0.35)",
              borderWidth: 1,
              order: 5,
              barThickness: 3,
              maxBarThickness: 5,
            },
          ]
        : []),
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: {
      mode: "index",
      intersect: false,
    },
    plugins: {
      zoom: {
        zoom: {
          wheel: { enabled: true, speed: 0.1 },
          pinch: { enabled: true },
          mode: "xy",
          drag: {
            enabled: true,
            backgroundColor: "rgba(255, 215, 0, 0.08)",
            borderColor: "#FFD700",
            borderWidth: 1,
          },
        },
        pan: {
          enabled: true,
          mode: "xy",
          threshold: 10,
        },
      },
      legend: {
        display: true,
        position: "top",
        labels: {
          filter: (item) => !["INTERVAL LOWER", "CONFIDENCE BAND"].includes(item.text),
          color: "#FFA500",
          font: {
            family: "monospace",
            size: 12,
          },
          padding: 16,
          boxWidth: 16,
          boxHeight: 3,
        },
      },
      tooltip: {
        enabled: true,
        mode: "index",
        intersect: false,
        filter: (context) => !["INTERVAL LOWER", "CONFIDENCE BAND"].includes(context.dataset.label),
        backgroundColor: "rgba(0, 0, 0, 0.92)",
        titleColor: "#FFA500",
        bodyColor: "#FFFFFF",
        borderColor: "#FFA500",
        borderWidth: 1,
        cornerRadius: 0,
        padding: 8,
        callbacks: {
          title: (items) => `TIME: ${items[0]?.label || "--"}`,
          label: (context) => {
            if (context.parsed.y === null || context.parsed.y === undefined) return null;
            const value = Number(context.parsed.y);
            const label = context.dataset.label;

            if (label === "ACTUAL PRICES") return `ACTUAL: $${value.toFixed(2)}`;
            if (label === "HISTORICAL PREDICTIONS") return `HISTORICAL: $${value.toFixed(2)}`;
            if (label === "FUTURE FORECAST") return `FORECAST: $${value.toFixed(2)}`;
            if (label === "VOLUME (REAL)") {
              if (value >= 1_000_000) return `VOLUME: ${(value / 1_000_000).toFixed(2)}M`;
              if (value >= 1_000) return `VOLUME: ${(value / 1_000).toFixed(1)}K`;
              return `VOLUME: ${Math.round(value)}`;
            }

            return `${label}: ${value.toFixed(2)}`;
          },
          afterBody: (items) => {
            const idx = items?.[0]?.dataIndex;
            if (idx === undefined || idx === null) return "";
            const low = chartData.lowerSeries?.[idx];
            const high = chartData.upperSeries?.[idx];
            if (low !== null && low !== undefined && high !== null && high !== undefined) {
              return `RANGE: $${Number(low).toFixed(2)} - $${Number(high).toFixed(2)}`;
            }
            return "";
          },
        },
      },
    },
    scales: {
      x: {
        type: "category",
        grid: {
          color: "rgba(255, 165, 0, 0.22)",
          lineWidth: 1,
          display: true,
        },
        ticks: {
          color: "#FFA500",
          font: {
            family: "monospace",
            size: 11,
          },
          autoSkip: true,
          maxTicksLimit: 14,
          maxRotation: 45,
          minRotation: 0,
        },
        border: {
          color: "#FFA500",
          width: 1,
        },
      },
      y: {
        type: "linear",
        position: "right",
        beginAtZero: false,
        min: chartData.yMin,
        max: chartData.yMax,
        grid: {
          color: "rgba(255, 165, 0, 0.30)",
          lineWidth: 1,
        },
        ticks: {
          color: "#FFA500",
          font: {
            family: "monospace",
            size: 12,
          },
          callback: (value) => `$${Number(value).toFixed(2)}`,
        },
        border: {
          color: "#FFA500",
          width: 1,
        },
        title: {
          display: true,
          text: "WTI CRUDE OIL (USD/BBL)",
          color: "#FFA500",
          font: {
            family: "monospace",
            size: 12,
          },
        },
      },
      volume: {
        display: chartData.hasRealVolume,
        type: "linear",
        position: "left",
        beginAtZero: true,
        min: 0,
        max: chartData.volumeMax,
        grid: { display: false },
        ticks: {
          color: "rgba(0, 180, 255, 0.75)",
          font: { family: "monospace", size: 10 },
          maxTicksLimit: 4,
          callback: (value) => {
            const v = Number(value);
            if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
            if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
            return `${Math.round(v)}`;
          },
        },
        title: {
          display: chartData.hasRealVolume,
          text: "REAL VOL",
          color: "rgba(0, 180, 255, 0.75)",
          font: { family: "monospace", size: 10 },
        },
      },
    },
  };

  if (chartData.isEmpty) {
    return (
      <div className="w-full h-full bg-black flex items-center justify-center">
        <div className="text-center">
          <div className="text-bloomberg-amber text-lg font-mono mb-2">LOADING WTI CRUDE OIL DATA...</div>
          <div className="text-gray-400 text-sm">Waiting for real-time data and predictions</div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full bg-black text-white font-mono">
      <div className="bg-black border-b border-gray-700 px-2 py-1">
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-4">
            <h1 className="text-lg font-normal text-bloomberg-amber">WTI CRUDE OIL</h1>
            <div className="text-2xl font-bold text-white">${currentPrice?.toFixed(2) || "0.00"}</div>
            <div className="text-sm text-gray-400">USD/BBL</div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowHistorical(!showHistorical)}
              className={`px-3 py-1 text-sm font-mono border transition-all ${
                showHistorical
                  ? "bg-bloomberg-amber text-black border-bloomberg-amber"
                  : "bg-transparent text-bloomberg-amber border-bloomberg-amber hover:bg-bloomberg-amber hover:text-black"
              }`}
            >
              HISTORICAL
            </button>
            <button
              onClick={() => setShowBand(!showBand)}
              className={`px-3 py-1 text-sm font-mono border transition-all ${
                showBand
                  ? "bg-bloomberg-cyan text-black border-bloomberg-cyan"
                  : "bg-transparent text-bloomberg-cyan border-bloomberg-cyan hover:bg-bloomberg-cyan hover:text-black"
              }`}
            >
              BAND
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

      <div className="h-full bg-black p-1">
        <div className="h-full w-full">
          <Line ref={chartRef} data={data} options={options} />
        </div>
      </div>
    </div>
  );
}
