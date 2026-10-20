import { fmtCT, toMs } from "../lib/format";
import { GIT_SHA, REPO_URL } from "./config";

/** Provenance, the not-advice line, data credits and the chart-library attribution. */
export default function SiteFooter({ data }) {
  const frozenMs = toMs(data?.frozen_at);
  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <p className="footer-provenance">
          <span className="micro">Provenance</span>
          {GIT_SHA
            ? <a href={`${REPO_URL}/commit/${GIT_SHA}`} target="_blank" rel="noopener noreferrer"><code>build {GIT_SHA.slice(0, 7)}</code></a>
            : <code>build local</code>}
          <span>data snapshot {frozenMs != null ? fmtCT(frozenMs) : "not loaded"}</span>
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer">source and history on GitHub</a>
        </p>
        <p className="footer-disclaimer">
          Research project, not investment advice. Forecasts describe volatility, not price direction, and
          past out-of-sample results do not guarantee future accuracy.
        </p>
        <p className="footer-credits">
          Data: Yahoo Finance (CL=F futures, exchange-delayed; ^OVX), FRED (OVXCLS), U.S. EIA (spot and
          contract prices). OVX is a Cboe index. Charts:{" "}
          <a href="https://www.tradingview.com/lightweight-charts/" target="_blank" rel="noopener noreferrer">TradingView Lightweight Charts™</a>
          {" "}(Apache-2.0) and d3. Fonts: Inter and JetBrains Mono (SIL OFL 1.1). All times US Central (CT).
        </p>
      </div>
    </footer>
  );
}
