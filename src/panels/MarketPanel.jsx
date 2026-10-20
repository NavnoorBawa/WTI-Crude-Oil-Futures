import { useState } from "react";
import { getPriceChart } from "../charts/lazy";
import LazyMount from "../components/LazyMount";
import Panel from "../components/Panel";
import Segmented from "../components/Segmented";
import { fmtUsd, num } from "../lib/format";
import { asArray, asText } from "../lib/research";

const RANGES = [
  { value: "1W", label: "1W", title: "Last week, hourly" },
  { value: "1M", label: "1M", title: "Last month, hourly" },
  { value: "1Y", label: "1Y", title: "Last year, daily closes" },
  { value: "ALL", label: "ALL", title: "Full history, daily closes" },
];

/** The CL=F price for context, demoted below the research. No model output is drawn on it. */
export default function MarketPanel({ data }) {
  const [range, setRange] = useState("ALL");
  const PriceChart = getPriceChart();
  const actual = data?.unified_data?.actual ?? null;
  const values = asArray(actual?.values).map(num).filter((value) => value != null);
  const timestamps = asArray(actual?.timestamps);
  const first = typeof timestamps[0] === "string" ? timestamps[0].slice(0, 10) : null;
  const historySymbol = asText(data?.contract?.history_symbol) ?? "CL=F";

  return (
    <Panel
      id="mkt"
      code="MKT"
      name="Market"
      title={`${historySymbol} continuous front month (unadjusted; rolls at expiry)`}
      meta={[
        values.length ? `${values.length.toLocaleString("en-US")} prices` : null,
        first ? `since ${first}` : null,
        values.length ? `last ${fmtUsd(values[values.length - 1])}` : null,
        "times in CT",
      ]}
      actions={<Segmented label="Price chart range" options={RANGES} value={range} onChange={setRange} />}
      caption="Context only: the continuous series jumps at each contract roll, and no forecast is drawn on price."
      source={{ artifact: "data.json → unified_data.actual", reproduce: null }}
      resetKey={data}
    >
      <LazyMount minHeight={340} label="price chart">
        <PriceChart actualPayload={actual} snapshotPrice={data?.current_price} range={range} />
      </LazyMount>
    </Panel>
  );
}
