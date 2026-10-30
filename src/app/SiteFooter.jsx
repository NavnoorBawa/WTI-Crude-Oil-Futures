import { fmtCT, toMs } from "../lib/format";
import { printPage } from "../lib/print";
import { METH_APPENDIX } from "../lib/reference";
import { DOCS, GIT_SHA, REPO_URL } from "./config";

// The full write-up and the reviewer's checks, first: a reader who starts on the page reaches them here.
const DOC_LINKS = [
  { href: DOCS.results, label: "Full results" },
  { href: DOCS.reviewerGuide, label: "Reviewer guide" },
];

// The METH appendix, linked from the footer of every page.
const APPENDIX_LINKS = [
  { href: `#${METH_APPENDIX.cite}`, label: "Cite" },
  { href: `#${METH_APPENDIX.corrections}`, label: "Corrections" },
  { href: `#${METH_APPENDIX.reuse}`, label: "Reuse" },
];

/** Provenance, the appendix links, print, the not-advice line, data credits and the chart-library attribution. */
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
        <nav className="footer-links" aria-label={data ? "Write-up, cite, corrections and reuse" : "Write-up"}>
          <ul className="sep-list">
            {DOC_LINKS.map((link) => (
              <li key={link.href}>
                <a href={link.href} target="_blank" rel="noopener noreferrer">
                  {link.label}<span className="sr-only"> (opens in a new tab)</span>
                </a>
              </li>
            ))}
            {/* The appendix anchors exist only once data.json has rendered the page. */}
            {data && APPENDIX_LINKS.map((link) => <li key={link.href}><a href={link.href}>{link.label}</a></li>)}
          </ul>
          {data && <button type="button" className="footer-print" onClick={() => { printPage(); }}>Print / save PDF</button>}
        </nav>
        <p className="footer-disclaimer">
          Research project, not investment advice. Forecasts describe volatility, not price direction, and
          past out-of-sample results do not guarantee future accuracy.
        </p>
        <p className="footer-credits">
          Data: Yahoo Finance (CL=F futures, exchange-delayed; ^OVX), FRED (OVXCLS), U.S. EIA (spot and
          contract prices). OVX is a Cboe index. Charts: TradingView Lightweight Charts™, Copyright © 2025 TradingView,
          Inc.,{" "}
          <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">tradingview.com</a>
          {" "}(Apache-2.0), and d3. Fonts: Inter and JetBrains Mono (SIL OFL 1.1). Clock times are US Central (CT); where a source quotes ET, the CT time is given beside it.
        </p>
      </div>
    </footer>
  );
}
