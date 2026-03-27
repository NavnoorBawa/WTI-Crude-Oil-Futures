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

const toNum = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const parseTimestamp = (value) => {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
};

const quantile = (arr, q) => {
  if (!arr || arr.length === 0) return null;
  const sorted = [...arr].sort((a, b) => a - b);
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  if (sorted[base + 1] !== undefined) {
    return sorted[base] + rest * (sorted[base + 1] - sorted[base]);
  }
  return sorted[base];
};

const withGapBreaks = (points, maxGapMs) => {
  if (!Array.isArray(points) || points.length <= 1) return points || [];
  const out = [points[0]];
  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const curr = points[i];
    if ((curr.x - prev.x) > maxGapMs) {
      out.push({ x: new Date(prev.x.getTime() + 1000), y: null });
    }
    out.push(curr);
  }
  return out;
};

const horizonLabelFromIndex = (idx) => {
  if (idx === 0) return "1H";
  if (idx === 1) return "1D";
  if (idx === 2) return "1W";
  return null;
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

    const futurePred = predictedPayload?.future || {};
    const futureValues = Array.isArray(futurePred.values) ? futurePred.values : [];
    const futureTimestamps = Array.isArray(futurePred.timestamps) ? futurePred.timestamps : [];
    const futureUpper = Array.isArray(futurePred.upper_bound) ? futurePred.upper_bound : [];
    const futureLower = Array.isArray(futurePred.lower_bound) ? futurePred.lower_bound : [];

    const now = new Date();

    const actualPointsRaw = [];
    for (let i = 0; i < actualValues.length; i += 1) {
      const y = toNum(actualValues[i]);
      if (y === null || y <= 0) continue;

      const parsedTs = parseTimestamp(actualTimestamps[i]);
      const x = parsedTs || new Date(now.getTime() - (actualValues.length - i) * 5 * 60 * 1000);
      const vol = toNum(actualVolumes[i]);

      actualPointsRaw.push({
        x,
        y: Number(y.toFixed(2)),
        volume: vol !== null && vol > 0 ? Math.round(vol) : null,
      });
    }

    actualPointsRaw.sort((a, b) => a.x - b.x);

    const histPredPointsRaw = [];
    for (let i = 0; i < historicalPredValues.length; i += 1) {
      const y = toNum(historicalPredValues[i]);
      const x = parseTimestamp(historicalPredTimestamps[i]);
      if (y === null || y <= 0 || !x) continue;
      histPredPointsRaw.push({ x, y: Number(y.toFixed(2)) });
    }
    histPredPointsRaw.sort((a, b) => a.x - b.x);

    const futurePointsRaw = [];
    if (futureValues.length > 0 && futureTimestamps.length > 0) {
      for (let i = 0; i < futureValues.length; i += 1) {
        const y = toNum(futureValues[i]);
        const x = parseTimestamp(futureTimestamps[i]);
        if (y === null || y <= 0 || !x) continue;

        const upper = toNum(futureUpper[i]);
        const lower = toNum(futureLower[i]);

        futurePointsRaw.push({
          x,
          y: Number(y.toFixed(2)),
          horizon: horizonLabelFromIndex(i),
          upper,
          lower,
        });
      }
      futurePointsRaw.sort((a, b) => a.x - b.x);
      futurePointsRaw.forEach((p, idx) => {
        if (!p.horizon) p.horizon = horizonLabelFromIndex(idx);
      });
    }

    // Fallback forecast anchors if backend future block is missing.
    if (futurePointsRaw.length === 0) {
      const preds = multiHorizonPredictions?.predictions || {};
      const intervals = multiHorizonPredictions?.prediction_intervals || {};
      const latestActualTime = actualPointsRaw.length > 0 ? actualPointsRaw[actualPointsRaw.length - 1].x : now;

      const anchors = [
        { key: "1h", label: "1H", ms: 60 * 60 * 1000 },
        { key: "1d", label: "1D", ms: 24 * 60 * 60 * 1000 },
        { key: "1w", label: "1W", ms: 7 * 24 * 60 * 60 * 1000 },
      ];

      anchors.forEach((anchor) => {
        const y = toNum(preds[anchor.key]);
        if (y === null || y <= 0) return;

        const interval = intervals[anchor.key] || {};
        futurePointsRaw.push({
          x: new Date(latestActualTime.getTime() + anchor.ms),
          y: Number(y.toFixed(2)),
          horizon: anchor.label,
          upper: toNum(interval.upper),
          lower: toNum(interval.lower),
        });
      });
      futurePointsRaw.sort((a, b) => a.x - b.x);
    }

    // Cap displayed band width so fan doesn't destroy readability.
    const maxBandRatio = 0.14;
    futurePointsRaw.forEach((p) => {
      if (!Number.isFinite(p.upper) || !Number.isFinite(p.lower)) {
        p.upper = null;
        p.lower = null;
        return;
      }
      const hi = Math.max(p.upper, p.lower);
      const lo = Math.min(p.upper, p.lower);
      const center = p.y;
      const width = hi - lo;
      const maxWidth = Math.abs(center) * maxBandRatio;
      if (width > maxWidth) {
        const half = maxWidth / 2;
        p.upper = Number((center + half).toFixed(2));
        p.lower = Number((center - half).toFixed(2));
      } else {
        p.upper = Number(hi.toFixed(2));
        p.lower = Number(lo.toFixed(2));
      }
    });

    const actualGapMs = 90 * 60 * 1000; // break after 90 min gap
    const histGapMs = 3 * 60 * 60 * 1000; // break after 3h gap

    const actualSeries = withGapBreaks(actualPointsRaw.map((p) => ({ x: p.x, y: p.y })), actualGapMs);
    const histSeries = withGapBreaks(histPredPointsRaw.map((p) => ({ x: p.x, y: p.y })), histGapMs);
    const futureSeries = futurePointsRaw.map((p) => ({ x: p.x, y: p.y, horizon: p.horizon }));
    const bandUpper = futurePointsRaw.map((p) => ({ x: p.x, y: p.upper }));
    const bandLower = futurePointsRaw.map((p) => ({ x: p.x, y: p.lower }));

    const volumeSeries = actualPointsRaw
      .filter((p) => Number.isFinite(p.volume) && p.volume > 0)
      .map((p) => ({ x: p.x, y: p.volume }));

    const corePrices = [
      ...actualPointsRaw.map((p) => p.y),
      ...histPredPointsRaw.map((p) => p.y),
      ...futurePointsRaw.map((p) => p.y),
    ].filter((v) => Number.isFinite(v));

    if (corePrices.length === 0 && currentPrice > 0) {
      corePrices.push(Number(currentPrice));
    }

    let yMin = Math.max(0, (currentPrice || 95) - 2);
    let yMax = (currentPrice || 95) + 2;

    if (corePrices.length > 0) {
      const p05 = quantile(corePrices, 0.05);
      const p95 = quantile(corePrices, 0.95);
      const minRaw = Math.min(...corePrices);
      const maxRaw = Math.max(...corePrices);
      const baseMin = Number.isFinite(p05) ? Math.min(minRaw, p05) : minRaw;
      const baseMax = Number.isFinite(p95) ? Math.max(maxRaw, p95) : maxRaw;

      const minVisibleRange = Math.max(1.5, Math.abs((currentPrice || baseMax)) * 0.018);
      const range = Math.max(minVisibleRange, baseMax - baseMin);
      const pad = Math.max(0.45, range * 0.12);

      yMin = Math.max(0, baseMin - pad);
      yMax = baseMax + pad;
    }

    const latestActualPoint = actualPointsRaw.length > 0 ? actualPointsRaw[actualPointsRaw.length - 1] : null;
    const nowMarker = latestActualPoint
      ? [
          { x: latestActualPoint.x, y: yMin },
          { x: latestActualPoint.x, y: yMax },
        ]
      : [];

    const hasBand = futurePointsRaw.filter((p) => Number.isFinite(p.upper) && Number.isFinite(p.lower)).length >= 2;
    const hasRealVolume = volumeSeries.length > 0;
    const volumeMax = hasRealVolume
      ? Math.ceil(Math.max(...volumeSeries.map((p) => p.y)) * 1.15)
      : 1000;

    const rangeByTime = new Map();
    futurePointsRaw.forEach((p) => {
      if (Number.isFinite(p.upper) && Number.isFinite(p.lower)) {
        rangeByTime.set(p.x.getTime(), { upper: p.upper, lower: p.lower, horizon: p.horizon });
      }
    });

    return {
      isEmpty: actualSeries.length === 0 && futureSeries.length === 0,
      actualSeries,
      histSeries,
      futureSeries,
      bandUpper,
      bandLower,
      volumeSeries,
      nowMarker,
      hasBand,
      hasRealVolume,
      rangeByTime,
      yMin,
      yMax,
      volumeMax,
    };
  }, [actualArray, predictedArray, unifiedData, multiHorizonPredictions, currentPrice]);

  const resetZoom = () => {
    if (chartRef.current && chartRef.current.resetZoom) {
      chartRef.current.resetZoom();
    }
  };

  const data = {
    datasets: [
      {
        label: "ACTUAL PRICES",
        data: chartData.actualSeries,
        borderColor: "#FFD700",
        backgroundColor: "transparent",
        borderWidth: 3,
        pointRadius: 2,
        pointHoverRadius: 5,
        pointBackgroundColor: "#FFD700",
        pointBorderColor: "#000000",
        pointBorderWidth: 1,
        tension: 0.08,
        spanGaps: false,
        order: 1,
      },
      {
        label: "HISTORICAL PREDICTIONS",
        data: showHistorical ? chartData.histSeries : [],
        borderColor: "#2DFF9B",
        backgroundColor: "transparent",
        borderWidth: 2,
        borderDash: [7, 4],
        pointRadius: 1.6,
        pointHoverRadius: 4,
        pointBackgroundColor: "#2DFF9B",
        pointBorderWidth: 0,
        tension: 0.1,
        spanGaps: false,
        order: 2,
      },
      {
        label: "FUTURE FORECAST",
        data: chartData.futureSeries,
        borderColor: "#00F5FF",
        backgroundColor: "transparent",
        borderWidth: 2.5,
        borderDash: [2, 4],
        pointRadius: 4,
        pointHoverRadius: 7,
        pointBackgroundColor: "#00F5FF",
        pointBorderColor: "#FFFFFF",
        pointBorderWidth: 1.2,
        tension: 0.04,
        spanGaps: false,
        order: 3,
      },
      {
        label: "INTERVAL LOWER",
        data: showBand && chartData.hasBand ? chartData.bandLower : [],
        borderColor: "rgba(0,245,255,0)",
        backgroundColor: "transparent",
        borderWidth: 0,
        pointRadius: 0,
        fill: false,
        spanGaps: false,
        order: 4,
      },
      {
        label: "CONFIDENCE BAND",
        data: showBand && chartData.hasBand ? chartData.bandUpper : [],
        borderColor: "rgba(0,245,255,0)",
        backgroundColor: "rgba(0,245,255,0.08)",
        borderWidth: 0,
        pointRadius: 0,
        fill: "-1",
        spanGaps: false,
        order: 4,
      },
      {
        label: "NOW MARKER",
        data: chartData.nowMarker,
        borderColor: "rgba(255,165,0,0.75)",
        borderDash: [5, 5],
        borderWidth: 1,
        pointRadius: 0,
        fill: false,
        spanGaps: false,
        order: 5,
      },
      ...(chartData.hasRealVolume
        ? [
            {
              label: "VOLUME (REAL)",
              data: chartData.volumeSeries,
              type: "bar",
              yAxisID: "volume",
              backgroundColor: "rgba(0,128,255,0.16)",
              borderColor: "rgba(0,170,255,0.28)",
              borderWidth: 1,
              barThickness: 3,
              maxBarThickness: 5,
              order: 6,
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
          wheel: { enabled: true, speed: 0.12 },
          pinch: { enabled: true },
          mode: "x",
          drag: {
            enabled: true,
            backgroundColor: "rgba(255, 215, 0, 0.08)",
            borderColor: "#FFD700",
            borderWidth: 1,
          },
        },
        pan: {
          enabled: true,
          mode: "x",
          threshold: 8,
        },
      },
      legend: {
        display: true,
        position: "top",
        labels: {
          filter: (item) => !["INTERVAL LOWER", "NOW MARKER"].includes(item.text),
          color: "#FFA500",
          font: {
            family: "monospace",
            size: 12,
          },
          padding: 14,
          boxWidth: 14,
          boxHeight: 3,
        },
      },
      tooltip: {
        enabled: true,
        mode: "nearest",
        intersect: false,
        filter: (context) => !["INTERVAL LOWER", "NOW MARKER"].includes(context.dataset.label),
        backgroundColor: "rgba(0, 0, 0, 0.92)",
        titleColor: "#FFA500",
        bodyColor: "#FFFFFF",
        borderColor: "#FFA500",
        borderWidth: 1,
        cornerRadius: 0,
        padding: 8,
        callbacks: {
          title: (items) => {
            const x = items?.[0]?.parsed?.x;
            if (!x) return "TIME: --";
            const d = new Date(x);
            return `TIME: ${d.toLocaleString("en-US", { hour12: false })}`;
          },
          label: (context) => {
            if (context.parsed.y === null || context.parsed.y === undefined) return null;
            const value = Number(context.parsed.y);
            const label = context.dataset.label;
            const raw = context.raw || {};

            if (label === "ACTUAL PRICES") return `ACTUAL: $${value.toFixed(2)}`;
            if (label === "HISTORICAL PREDICTIONS") return `HIST: $${value.toFixed(2)}`;
            if (label === "FUTURE FORECAST") {
              const h = raw.horizon ? ` (${raw.horizon})` : "";
              return `FORECAST${h}: $${value.toFixed(2)}`;
            }
            if (label === "CONFIDENCE BAND") return null;
            if (label === "VOLUME (REAL)") {
              if (value >= 1_000_000) return `VOLUME: ${(value / 1_000_000).toFixed(2)}M`;
              if (value >= 1_000) return `VOLUME: ${(value / 1_000).toFixed(1)}K`;
              return `VOLUME: ${Math.round(value)}`;
            }

            return `${label}: ${value.toFixed(2)}`;
          },
          afterBody: (items) => {
            if (!items || items.length === 0) return "";
            const x = items[0]?.parsed?.x;
            if (!x) return "";
            const range = chartData.rangeByTime.get(new Date(x).getTime());
            if (!range) return "";
            const h = range.horizon ? ` ${range.horizon}` : "";
            return `RANGE${h}: $${Number(range.lower).toFixed(2)} - $${Number(range.upper).toFixed(2)}`;
          },
        },
      },
    },
    scales: {
      x: {
        type: "time",
        time: {
          tooltipFormat: "MMM dd, HH:mm",
          displayFormats: {
            minute: "HH:mm",
            hour: "MMM dd HH:mm",
            day: "MMM dd",
          },
        },
        grid: {
          color: "rgba(255, 165, 0, 0.20)",
          lineWidth: 1,
          display: true,
        },
        ticks: {
          color: "#FFA500",
          font: {
            family: "monospace",
            size: 11,
          },
          maxTicksLimit: 10,
          autoSkip: true,
          maxRotation: 0,
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
          color: "rgba(0, 180, 255, 0.72)",
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
          color: "rgba(0, 180, 255, 0.72)",
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
