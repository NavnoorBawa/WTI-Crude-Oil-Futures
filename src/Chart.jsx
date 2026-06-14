import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  AreaSeries,
  ColorType,
  createChart,
  CrosshairMode,
  LineSeries,
  LineStyle,
} from "lightweight-charts";

// 1W is the only walk-forward validated signal (entry-time-clean: 65.8%, p<0.001 at measured
// ESS, Sharpe 2.44, n=199 OOS). 1D: unstable direction, negative P&L after costs. 1H: never tested. Both removed.
const FORECAST_HORIZONS = ["1W"];

const HORIZON_META = {
  "1W": { key: "1w", color: "#5cb0d6", softFill: "rgba(92, 176, 214, 0.16)", lens: "1W Walk-Forward" },
};

const RANGE_PRESETS = {
  "8H": { lookbackSec: 8 * 60 * 60, rightPaddingSec: 90 * 60, barSpacing: 18 },
  "1D": { lookbackSec: 24 * 60 * 60, rightPaddingSec: 4 * 60 * 60, barSpacing: 12 },
  "1W": { lookbackSec: 7 * 24 * 60 * 60, rightPaddingSec: 18 * 60 * 60, barSpacing: 9 },
  "1M": { lookbackSec: 30 * 24 * 60 * 60, rightPaddingSec: 36 * 60 * 60, barSpacing: 6 },
  ALL: { lookbackSec: null, rightPaddingSec: null, barSpacing: 5 },
};

const HISTORICAL_MIN_SPACING_SEC = {
  "1H": 20 * 60,
  "1D": 6 * 60 * 60,
  "1W": 24 * 60 * 60,
};

const HISTORICAL_GAP_BREAK_SEC = {
  "1H": 8 * 60 * 60,
  "1D": 3 * 24 * 60 * 60,
  "1W": 14 * 24 * 60 * 60,
};

const HISTORY_BRIDGE_MAX_GAP_SEC = {
  "1H": 3 * 60 * 60,
  "1D": 36 * 60 * 60,
  "1W": 10 * 24 * 60 * 60,
};

const DEFAULT_RANGE = "ALL";

const toNum = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const toUnixSeconds = (value) => {
  if (!value) return null;
  const dateValue = new Date(value);
  const timestamp = dateValue.getTime();
  return Number.isFinite(timestamp) ? Math.floor(timestamp / 1000) : null;
};

const round2 = (value) => Number(Number(value).toFixed(2));

const formatSignedPercent = (value) => {
  if (!Number.isFinite(value)) return "--";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
};


const formatSignedPrice = (value) => {
  if (!Number.isFinite(value)) return "--";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
};

const formatCurrency = (value) => {
  if (!Number.isFinite(value)) return "--";
  return `$${Number(value).toFixed(2)}`;
};

const formatCompactVolume = (value) => {
  if (!Number.isFinite(value) || value <= 0) return "--";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return `${Math.round(value)}`;
};

const formatLegendTime = (time) => {
  if (!Number.isFinite(time)) return "--";
  return new Date(time * 1000).toLocaleString("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
};


const rgba = (hex, alpha) => {
  const clean = hex.replace("#", "");
  const value = clean.length === 3
    ? clean.split("").map((char) => char + char).join("")
    : clean;
  const red = parseInt(value.slice(0, 2), 16);
  const green = parseInt(value.slice(2, 4), 16);
  const blue = parseInt(value.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
};

const uniqueSeriesPoints = (points) => {
  const deduped = new Map();
  (points || []).forEach((point) => {
    if (!Number.isFinite(point?.time) || !Number.isFinite(point?.value)) return;
    deduped.set(point.time, point);
  });
  return [...deduped.values()].sort((a, b) => a.time - b.time);
};

const buildActualPoints = (actualPayload, actualArray) => {
  const actualValues = Array.isArray(actualPayload?.values) ? actualPayload.values : actualArray;
  const actualTimestamps = Array.isArray(actualPayload?.timestamps) ? actualPayload.timestamps : [];
  const actualVolumes = Array.isArray(actualPayload?.volumes) ? actualPayload.volumes : [];

  const points = actualValues
    .map((value, index) => {
      const time = toUnixSeconds(actualTimestamps[index]);
      const price = toNum(value);
      const volume = toNum(actualVolumes[index]) || 0;
      if (!Number.isFinite(time) || !Number.isFinite(price) || price <= 0) {
        return null;
      }
      return {
        time,
        value: round2(price),
        volume: Math.max(0, Math.round(volume)),
      };
    })
    .filter(Boolean)
    .sort((a, b) => a.time - b.time);

  const deduped = new Map();
  points.forEach((point) => deduped.set(point.time, point));
  return [...deduped.values()].sort((a, b) => a.time - b.time);
};

const buildHistoricalPredictionModel = (predictedPayload, activeHorizon) => {
  const horizonKey = HORIZON_META[activeHorizon]?.key || "1h";
  const issuedByHorizon = predictedPayload?.issued_by_horizon?.[horizonKey];
  const byHorizon = predictedPayload?.historical_by_horizon?.[horizonKey];
  const fallbackPayload = predictedPayload?.historical || {};
  const source = (Array.isArray(issuedByHorizon?.values) && issuedByHorizon.values.length > 0)
    ? issuedByHorizon
    : (byHorizon || fallbackPayload);

  const values = Array.isArray(source?.values) ? source.values : [];
  const targetTimestamps = Array.isArray(source?.target_timestamps) && source.target_timestamps.length > 0
    ? source.target_timestamps
    : (Array.isArray(source?.timestamps) ? source.timestamps : []);
  const issueTimestamps = Array.isArray(source?.issue_timestamps) ? source.issue_timestamps : [];
  const minSpacingSec = HISTORICAL_MIN_SPACING_SEC[activeHorizon] || 0;
  const gapBreakSec = HISTORICAL_GAP_BREAK_SEC[activeHorizon] || Number.POSITIVE_INFINITY;

  const rawPoints = values
    .map((value, index) => {
      const time = toUnixSeconds(targetTimestamps[index]);
      const numeric = toNum(value);
      const issueTime = toUnixSeconds(issueTimestamps[index]);
      if (!Number.isFinite(time) || !Number.isFinite(numeric) || numeric <= 0) {
        return null;
      }
      return {
        time,
        value: round2(numeric),
        issueTime: Number.isFinite(issueTime) ? issueTime : time,
      };
    })
    .filter(Boolean)
    .sort((a, b) => (a.time - b.time) || (a.issueTime - b.issueTime));

  const collapsedPoints = [];
  rawPoints.forEach((point) => {
    const previous = collapsedPoints[collapsedPoints.length - 1];
    if (previous && (point.time - previous.time) <= minSpacingSec) {
      collapsedPoints[collapsedPoints.length - 1] = point.issueTime >= previous.issueTime ? point : previous;
      return;
    }
    collapsedPoints.push(point);
  });

  const points = collapsedPoints.map(({ time, value }) => ({ time, value }));
  const seriesData = [];
  points.forEach((point, index) => {
    seriesData.push(point);
    const nextPoint = points[index + 1];
    if (!nextPoint) return;

    const gapSeconds = nextPoint.time - point.time;
    if (gapSeconds <= gapBreakSec) return;

    const whitespaceTime = Math.max(
      point.time + 1,
      Math.min(nextPoint.time - 1, point.time + Math.floor(gapSeconds / 2))
    );
    if (whitespaceTime > point.time && whitespaceTime < nextPoint.time) {
      seriesData.push({ time: whitespaceTime });
    }
  });

  return {
    points,
    seriesData,
  };
};

const buildForecastMap = (futurePayload, multiHorizonPredictions, latestTime, latestClose) => {
  const map = {};
  const futureValues = Array.isArray(futurePayload?.values) ? futurePayload.values : [];
  const futureTimestamps = Array.isArray(futurePayload?.timestamps) ? futurePayload.timestamps : [];
  const futureUpper = Array.isArray(futurePayload?.upper_bound) ? futurePayload.upper_bound : [];
  const futureLower = Array.isArray(futurePayload?.lower_bound) ? futurePayload.lower_bound : [];
  const futureByHorizon = futurePayload?.by_horizon || {};

  for (let index = 0; index < FORECAST_HORIZONS.length; index += 1) {
    const horizon = FORECAST_HORIZONS[index];
    const meta = HORIZON_META[horizon];
    const keyedFuture = futureByHorizon?.[meta.key];
    if (keyedFuture) {
      const keyedTime = toUnixSeconds(keyedFuture.timestamp);
      const keyedValue = toNum(keyedFuture.value);
      if (Number.isFinite(keyedTime) && Number.isFinite(keyedValue) && keyedValue > 0) {
        map[horizon] = {
          time: keyedTime,
          value: round2(keyedValue),
          upper: toNum(keyedFuture.upper),
          lower: toNum(keyedFuture.lower),
        };
        continue;
      }
    }

    const time = toUnixSeconds(futureTimestamps[index]);
    const value = toNum(futureValues[index]);
    if (Number.isFinite(time) && Number.isFinite(value) && value > 0) {
      map[horizon] = {
        time,
        value: round2(value),
        upper: toNum(futureUpper[index]),
        lower: toNum(futureLower[index]),
      };
    }
  }

  FORECAST_HORIZONS.forEach((horizon) => {
    if (map[horizon]) return;
    const meta = HORIZON_META[horizon];
    const predictedValue = toNum(multiHorizonPredictions?.predictions?.[meta.key]);
    if (!Number.isFinite(predictedValue) || predictedValue <= 0) return;

    map[horizon] = {
      time: latestTime + (
        horizon === "1H" ? 60 * 60 :
        horizon === "1D" ? 24 * 60 * 60 :
        7 * 24 * 60 * 60
      ),
      value: round2(predictedValue),
      upper: toNum(multiHorizonPredictions?.prediction_intervals?.[meta.key]?.upper),
      lower: toNum(multiHorizonPredictions?.prediction_intervals?.[meta.key]?.lower),
    };
  });

  FORECAST_HORIZONS.forEach((horizon) => {
    if (!map[horizon]) return;
    const minimumFutureTime = latestTime + (
      horizon === "1H" ? 60 * 60 :
      horizon === "1D" ? 24 * 60 * 60 :
      7 * 24 * 60 * 60
    );
    if (!Number.isFinite(map[horizon].time) || map[horizon].time <= latestTime) {
      map[horizon].time = minimumFutureTime;
    }
    map[horizon].changePct = Number.isFinite(latestClose) && latestClose > 0
      ? ((map[horizon].value - latestClose) / latestClose) * 100
      : null;
  });

  return map;
};

const buildProjectionPath = (lastActual, forecast) => {
  if (!lastActual || !forecast) return [];

  const forecastTime = Math.max(lastActual.time + 60, forecast.time);
  const totalTime = Math.max(60, forecastTime - lastActual.time);
  const delta = forecast.value - lastActual.value;
  const mid1Time = Math.max(lastActual.time + 1, lastActual.time + Math.round(totalTime * 0.28));
  const mid2Time = Math.max(mid1Time + 1, lastActual.time + Math.round(totalTime * 0.62));
  const endTime = Math.max(mid2Time + 1, forecastTime);

  return uniqueSeriesPoints([
    { time: lastActual.time, value: lastActual.value },
    {
      time: mid1Time,
      value: round2(lastActual.value + delta * 0.18),
    },
    {
      time: mid2Time,
      value: round2(lastActual.value + delta * 0.58),
    },
    { time: endTime, value: forecast.value },
  ]);
};

const buildPredictionBridge = (historicalPredictions, projectionPoints, lastActual, activeHorizon) => {
  const bridge = [];
  const tail = historicalPredictions.length > 0 ? historicalPredictions[historicalPredictions.length - 1] : null;
  const maxGapSec = HISTORY_BRIDGE_MAX_GAP_SEC[activeHorizon] || 0;

  if (
    tail
    && lastActual
    && tail.time < lastActual.time
    && (lastActual.time - tail.time) <= maxGapSec
  ) {
    bridge.push(tail, { time: lastActual.time, value: lastActual.value });
  }

  return uniqueSeriesPoints([...bridge, ...projectionPoints]);
};

const buildScenarioPath = (lastActual, forecastTime, scenarioValue) => {
  if (!lastActual || !Number.isFinite(forecastTime) || !Number.isFinite(scenarioValue)) return [];
  return buildProjectionPath(lastActual, {
    time: forecastTime,
    value: round2(scenarioValue),
  });
};


export default function Chart({
  actualArray = [],
  unifiedData = null,
  multiHorizonPredictions = null,
  currentPrice = 0,
  contractInfo = null,
  priceChange = 0,
  priceChangePercent = 0,
  livePrice = null,
  livePriceChange = null,
  livePricePct = null,
  feedStatus = "UNKNOWN",
}) {
  const chartHostRef = useRef(null);
  const [selectedRange, setSelectedRange] = useState(DEFAULT_RANGE);
  const [legendSnapshot, setLegendSnapshot] = useState(null);
  const resolvedActiveHorizon = "1W"; // hard-locked: only validated horizon

  const chartModel = useMemo(() => {
    const actualPayload = unifiedData?.actual || {};
    const predictedPayload = unifiedData?.predicted || {};
    const futurePayload = predictedPayload?.future || {};

    const actualPoints = buildActualPoints(actualPayload, actualArray);
    const lastActual = actualPoints.length > 0
      ? actualPoints[actualPoints.length - 1]
      : (Number.isFinite(Number(currentPrice)) && Number(currentPrice) > 0
        ? { time: Math.floor(Date.now() / 1000), value: round2(Number(currentPrice)), volume: 0 }
        : null);
    const resolvedActualPoints = actualPoints.length > 0 || !lastActual
      ? actualPoints
      : [lastActual];

    const historicalPredictionModel = buildHistoricalPredictionModel(predictedPayload, resolvedActiveHorizon);
    const historicalPredictionPoints = historicalPredictionModel.points;
    const forecasts = buildForecastMap(
      futurePayload,
      multiHorizonPredictions,
      lastActual?.time || Math.floor(Date.now() / 1000),
      lastActual?.value || toNum(currentPrice) || 0
    );

    const activeForecast = forecasts[resolvedActiveHorizon] || null;
    const projectionPoints = buildProjectionPath(lastActual, activeForecast);
    const predictionBridge = buildPredictionBridge(historicalPredictionPoints, projectionPoints, lastActual, resolvedActiveHorizon);
    const upperScenarioPoints = buildScenarioPath(lastActual, activeForecast?.time, activeForecast?.upper);
    const lowerScenarioPoints = buildScenarioPath(lastActual, activeForecast?.time, activeForecast?.lower);

    return {
      actualPoints: resolvedActualPoints,
      lastActual,
      historicalPredictionPoints,
      historicalPredictionSeriesData: historicalPredictionModel.seriesData,
      forecasts,
      activeForecast,
      projectionPoints,
      predictionBridge,
      upperScenarioPoints,
      lowerScenarioPoints,
    };
  }, [actualArray, currentPrice, multiHorizonPredictions, resolvedActiveHorizon, unifiedData]);

  const displaySpotPrice = Number.isFinite(Number(currentPrice)) && Number(currentPrice) > 0
    ? Number(currentPrice)
    : (chartModel.lastActual?.value ?? 0);

  // The big toolbar readout prefers the same-origin live quote when present (matching the
  // trading-chart convention: the headline price is live while the plotted candles are
  // history). The chart series/price-line stay anchored to the frozen actuals below.
  const hasLivePrice = Number.isFinite(Number(livePrice)) && Number(livePrice) > 0;
  const toolbarPrice = hasLivePrice ? Number(livePrice) : displaySpotPrice;
  // Change follows the SAME source as the price: with a live price, use the live change
  // or null (renders "--" via formatSigned*) — never the frozen day's change, which is
  // computed against a different reference and would not match the live price shown.
  const toolbarChange = hasLivePrice
    ? (livePriceChange != null ? Number(livePriceChange) : null)
    : (Number(priceChange) || 0);
  const toolbarChangePct = hasLivePrice
    ? (livePricePct != null ? Number(livePricePct) : null)
    : (Number(priceChangePercent) || 0);

  useEffect(() => {
    if (!chartModel.lastActual) {
      setLegendSnapshot(null);
      return;
    }
    setLegendSnapshot({
      time: chartModel.lastActual.time,
      price: chartModel.lastActual.value,
      volume: chartModel.lastActual.volume || 0,
    });
  }, [chartModel.lastActual]);

  useEffect(() => {
    const host = chartHostRef.current;
    if (!host || !chartModel.lastActual || chartModel.actualPoints.length === 0) return undefined;

    const activeMeta = HORIZON_META[resolvedActiveHorizon];
    const chart = createChart(host, {
      width: host.clientWidth || 800,
      height: host.clientHeight || 540,
      attributionLogo: true,
      layout: {
        background: { type: ColorType.Solid, color: "#000000" },
        textColor: "#7d8088",
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.035)" },
        horzLines: { color: "rgba(255,255,255,0.035)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: "rgba(255,255,255,0.16)",
          labelBackgroundColor: "#26262c",
          width: 1,
        },
        horzLine: {
          color: "rgba(255,255,255,0.16)",
          labelBackgroundColor: "#26262c",
          width: 1,
        },
      },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.08)",
        scaleMargins: {
          top: 0.08,
          bottom: 0.06,
        },
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.08)",
        timeVisible: true,
        secondsVisible: false,
        barSpacing: RANGE_PRESETS[selectedRange].barSpacing,
        minBarSpacing: 0.5,
        rightOffset: 8,
      },
      localization: {
        priceFormatter: (price) => `$${Number(price).toFixed(2)}`,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        axisPressedMouseMove: true,
        mouseWheel: true,
        pinch: true,
      },
    });

    const actualSeries = chart.addSeries(AreaSeries, {
      lineColor: "#d4d7dd",
      topColor: "rgba(255, 255, 255, 0.05)",
      bottomColor: "rgba(255, 255, 255, 0.004)",
      lineWidth: 2,
      lastValueVisible: true,
      priceLineVisible: true,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 4,
      crosshairMarkerBorderColor: "#d4d7dd",
      crosshairMarkerBackgroundColor: "#0c0c0e",
    });
    actualSeries.setData(chartModel.actualPoints.map((point) => ({ time: point.time, value: point.value })));

    const historicalPredictionSeries = chart.addSeries(LineSeries, {
      color: rgba(activeMeta.color, 0.92),
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    historicalPredictionSeries.setData(chartModel.historicalPredictionSeriesData);

    const futureAreaSeries = chart.addSeries(AreaSeries, {
      lineColor: activeMeta.color,
      topColor: activeMeta.softFill,
      bottomColor: "rgba(0,0,0,0.01)",
      lineWidth: 2.2,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    futureAreaSeries.setData(chartModel.projectionPoints);

    const bridgeSeries = chart.addSeries(LineSeries, {
      color: activeMeta.color,
      lineWidth: 2.25,
      lineStyle: LineStyle.Solid,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    bridgeSeries.setData(chartModel.predictionBridge);

    const upperScenarioSeries = chart.addSeries(LineSeries, {
      color: rgba(activeMeta.color, 0.36),
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    upperScenarioSeries.setData(chartModel.upperScenarioPoints);

    const lowerScenarioSeries = chart.addSeries(LineSeries, {
      color: rgba(activeMeta.color, 0.36),
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    lowerScenarioSeries.setData(chartModel.lowerScenarioPoints);

    const currentPriceLine = actualSeries.createPriceLine({
      price: chartModel.lastActual.value,
      color: "rgba(212, 215, 221, 0.4)",
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      axisLabelVisible: false,
      title: "",
    });

    const createdLines = [currentPriceLine];
    if (chartModel.activeForecast) {
      createdLines.push(
        actualSeries.createPriceLine({
          price: chartModel.activeForecast.value,
          color: activeMeta.color,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `${resolvedActiveHorizon} FC`,
        })
      );
    }

    const actualLookup = new Map(chartModel.actualPoints.map((point) => [point.time, point]));

    const updateLegendFromTime = (timeValue, fallbackValue) => {
      if (!Number.isFinite(timeValue)) {
        setLegendSnapshot({
          time: chartModel.lastActual.time,
          price: chartModel.lastActual.value,
          volume: chartModel.lastActual.volume || 0,
        });
        return;
      }

      const actualPoint = actualLookup.get(Number(timeValue));
      const predictedPoint = chartModel.historicalPredictionPoints.find((point) => point.time === Number(timeValue));
      const price = actualPoint?.value ?? predictedPoint?.value ?? fallbackValue ?? chartModel.lastActual.value;

      setLegendSnapshot({
        time: Number(timeValue),
        price,
        volume: actualPoint?.volume || 0,
      });
    };

    const handleCrosshairMove = (param) => {
      if (!param?.time || !param?.seriesData) {
        updateLegendFromTime(null, null);
        return;
      }

      const actualData = param.seriesData.get(actualSeries);
      const historicalPredData = param.seriesData.get(historicalPredictionSeries);
      const futureData = param.seriesData.get(futureAreaSeries)
        || param.seriesData.get(bridgeSeries)
        || param.seriesData.get(upperScenarioSeries)
        || param.seriesData.get(lowerScenarioSeries);
      const fallbackValue = actualData?.value ?? historicalPredData?.value ?? futureData?.value ?? null;
      updateLegendFromTime(Number(param.time), fallbackValue);
    };

    chart.subscribeCrosshairMove(handleCrosshairMove);

    const rangeConfig = RANGE_PRESETS[selectedRange];
    if (rangeConfig.lookbackSec && chartModel.lastActual) {
      const forecastEnd = chartModel.activeForecast?.time || chartModel.lastActual.time;
      chart.timeScale().setVisibleRange({
        from: chartModel.lastActual.time - rangeConfig.lookbackSec,
        to: Math.max(
          chartModel.lastActual.time + rangeConfig.rightPaddingSec,
          forecastEnd + Math.round(rangeConfig.rightPaddingSec * 0.35)
        ),
      });
    } else {
      chart.timeScale().fitContent();
    }

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      chart.resize(Math.floor(entry.contentRect.width), Math.floor(entry.contentRect.height));
    });
    resizeObserver.observe(host);

    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      createdLines.forEach((line) => actualSeries.removePriceLine(line));
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [chartModel, resolvedActiveHorizon, selectedRange]);

  if (!chartModel.lastActual) {
    return (
      <div className="tv-chart-shell">
        <div className="tv-chart-empty">
          <div className="tv-empty-title">Waiting for market data</div>
          <div className="tv-empty-subtitle">No real prices are available yet.</div>
        </div>
      </div>
    );
  }

  return (
    <div className="tv-chart-shell">
      <div className="tv-chart-toolbar">
        <div className="tv-toolbar-main">
          <div className="tv-symbol-block">
            <div className="tv-symbol-chip">{contractInfo?.symbol || "WTI"}</div>
            <div className="tv-symbol-copy">
              <div className="tv-symbol-title">{contractInfo?.description || "WTI CRUDE OIL FUTURES"}</div>
              <div className="tv-toolbar-meta">
                <span>{feedStatus}</span>
                {contractInfo?.quote_symbol && <span>{contractInfo.quote_symbol}</span>}
              </div>
            </div>
          </div>

          <div className="tv-price-block">
            <div className="tv-price-main">${toolbarPrice.toFixed(2)}</div>
            <div className={`tv-price-change ${toolbarChange > 0 ? "is-up" : toolbarChange < 0 ? "is-down" : ""}`}>
              <span>{formatSignedPrice(toolbarChange)}</span>
              <span>{formatSignedPercent(toolbarChangePct)}</span>
            </div>
          </div>
        </div>

        <div className="tv-1w-strip">
          <span className="tv-1w-horizon">1W TARGET</span>
          <span className="tv-1w-target">
            {chartModel.forecasts["1W"] ? `$${chartModel.forecasts["1W"].value.toFixed(2)}` : "--"}
          </span>
          {chartModel.forecasts["1W"]?.changePct != null && (
            <span className={`tv-1w-change ${chartModel.forecasts["1W"].changePct >= 0 ? "is-up" : "is-down"}`}>
              {formatSignedPercent(chartModel.forecasts["1W"].changePct)}
            </span>
          )}
          <span className="tv-1w-meta">walk-forward validated signal — full stats above</span>
        </div>
      </div>

      {/* Thesis banner removed — the Desk Call header now carries the verdict. */}

      {/* Scenario rail removed for minimalism — the chart overlay already shows the
          1-week target and scenario band; the Desk Call header carries the verdict. */}

      <div className="tv-chart-stage">
        <div className="tv-chart-overlay tv-overlay-left">
          <div className="tv-legend-card">
            <div className="tv-legend-time">{formatLegendTime(legendSnapshot?.time)}</div>
            <div className="tv-legend-grid">
              <span>PX {legendSnapshot?.price?.toFixed(2) || "--"}</span>
              <span>VOL {formatCompactVolume(legendSnapshot?.volume)}</span>
              <span>{resolvedActiveHorizon} FC {chartModel.activeForecast ? `$${chartModel.activeForecast.value.toFixed(2)}` : "--"}</span>
            </div>
          </div>
        </div>

        <div className="tv-chart-overlay tv-overlay-right">
          <div className="tv-active-forecast-card">
            <div className="tv-active-forecast-label">{resolvedActiveHorizon} Target</div>
            <div className="tv-active-forecast-value">
              {chartModel.activeForecast ? formatCurrency(chartModel.activeForecast.value) : "--"}
            </div>
            <div className="tv-active-forecast-range">
              {chartModel.activeForecast && Number.isFinite(chartModel.activeForecast.lower) && Number.isFinite(chartModel.activeForecast.upper)
                ? `Range ${formatCurrency(chartModel.activeForecast.lower)} - ${formatCurrency(chartModel.activeForecast.upper)}`
                : "Range unavailable"}
            </div>
          </div>
        </div>

        <div className="tv-chart-watermark">
          <span className="tv-watermark-symbol">{contractInfo?.symbol || "WTI"}</span>
          <span className="tv-watermark-caption">Actual + prediction path</span>
        </div>

        <div ref={chartHostRef} className="tv-chart-host" />
      </div>

      <div className="tv-chart-footer">
        <div className="tv-range-strip">
          {Object.keys(RANGE_PRESETS).map((rangeKey) => (
            <button
              key={rangeKey}
              className={`tv-range-button ${selectedRange === rangeKey ? "is-active" : ""}`}
              onClick={() => setSelectedRange(rangeKey)}
            >
              {rangeKey}
            </button>
          ))}
        </div>

        <div className="tv-footer-copy">
          <span><i className="tv-dot actual" />Actual</span>
          <span><i className="tv-dot history" />Past predictions</span>
          <span><i className="tv-dot future" style={{ "--dot-color": HORIZON_META[resolvedActiveHorizon]?.color }} />1W forecast</span>
          <span><i className="tv-dot band" style={{ "--dot-color": rgba(HORIZON_META[resolvedActiveHorizon]?.color, 0.55) }} />Forecast band</span>
        </div>
      </div>
    </div>
  );
}
