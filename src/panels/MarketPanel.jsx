import { useState } from "react";
import { useViewParam } from "../app/viewState";
import { getPriceChart } from "../charts/lazy";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel from "../components/Panel";
import Segmented from "../components/Segmented";
import { fmtDate, fmtInt, fmtUsd, num } from "../lib/format";
import { asArray, asText } from "../lib/research";

// URL value (?mkt=1y) -> the chart's range key.
const RANGES = [
  { value: "1w", range: "1W", label: "1W", title: "Last week, hourly" },
  { value: "1m", range: "1M", label: "1M", title: "Last month, hourly" },
  { value: "1y", range: "1Y", label: "1Y", title: "Last year, daily closes" },
  { value: "all", range: "ALL", label: "ALL", title: "Full history, daily closes" },
];
const RANGE_PARAMS = RANGES.map((option) => option.value);

/** data.json vol_forecast.recent_closes as rows ({date, close}), oldest first; rows without a close are dropped. */
export const closeRows = (data) => {
  const closes = data?.vol_forecast?.recent_closes;
  const prices = asArray(closes?.close);
  return asArray(closes?.date)
    .map((date, index) => ({ date: typeof date === "string" ? date.slice(0, 10) : null, close: num(prices[index]) }))
    .filter((row) => row.date && row.close != null);
};

/** The CL=F price for context, demoted below the research. No model output is drawn on it. */
export default function MarketPanel({ data }) {
  const [rangeParam, setRangeParam] = useViewParam("mkt", RANGE_PARAMS, "all");
  // Bumped on every range click, the pressed one included, so a click always re-frames the chart.
  const [frameKey, setFrameKey] = useState(0);
  const range = RANGES.find((option) => option.value === rangeParam)?.range ?? "ALL";
  const PriceChart = getPriceChart();
  const actual = data?.unified_data?.actual ?? null;
  const values = asArray(actual?.values).map(num).filter((value) => value != null);
  const timestamps = asArray(actual?.timestamps);
  const first = typeof timestamps[0] === "string" ? timestamps[0].slice(0, 10) : null;
  const historySymbol = asText(data?.contract?.history_symbol) ?? "CL=F";
  const closes = closeRows(data);
  const choose = (value) => {
    setRangeParam(value);
    setFrameKey((key) => key + 1);
  };

  return (
    <Panel
      id="mkt"
      code="MKT"
      name="Market"
      title={`${historySymbol} continuous front month (unadjusted; rolls at expiry)`}
      meta={[
        values.length ? `${values.length.toLocaleString("en-US")} prices` : null,
        first ? `since ${fmtDate(first)}` : null,
        values.length ? `last ${fmtUsd(values[values.length - 1])}` : null,
        "times in CT",
      ]}
      controls={<Segmented label="Price chart range" options={RANGES} value={rangeParam} onChange={choose} />}
      caption="Context only: the continuous series jumps at each contract roll, and no forecast is drawn on price."
      source={{ artifact: ["data.json → unified_data.actual", "vol_forecast.recent_closes"], reproduce: null }}
      resetKey={data}
      tableLabel="Closes"
      table={closes.length ? () => (
        <DataTable
          caption={`Last ${fmtInt(closes.length)} finished-session closes of ${historySymbol}, ${fmtDate(closes[0].date)} to ${fmtDate(closes.at(-1).date)} (USD per barrel)`}
          rowKey={(row) => row.date}
          dense
          exportName="closes"
          columns={[
            { key: "date", label: "Session", rowHeader: true, render: (row) => fmtDate(row.date) },
            { key: "close", label: "Close, USD", align: "right", render: (row) => <span className="num">{fmtUsd(row.close)}</span> },
          ]}
          rows={closes.slice().reverse()}
        />
      ) : null}
    >
      <LazyMount minHeight={{ wide: 368, narrow: 288 }} label="price chart">
        <PriceChart actualPayload={actual} snapshotPrice={data?.current_price} range={range} frameKey={frameKey} />
      </LazyMount>
    </Panel>
  );
}
