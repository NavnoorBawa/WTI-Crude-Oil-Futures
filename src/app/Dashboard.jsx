import { useMemo, useState } from "react";
import ErrorBoundary from "../components/ErrorBoundary";
import { SnapshotContext } from "../lib/snapshot";
import CalibrationPanel from "../panels/CalibrationPanel";
import ConePanel from "../panels/ConePanel";
import ForecastPanel from "../panels/ForecastPanel";
import LeaderboardPanel from "../panels/LeaderboardPanel";
import LeakPanel from "../panels/LeakPanel";
import LedgerPanel from "../panels/LedgerPanel";
import LiveRecordPanel from "../panels/LiveRecordPanel";
import MarketPanel from "../panels/MarketPanel";
import MarketStrip from "../panels/MarketStrip";
import MethodPanel from "../panels/MethodPanel";
import RiskPanel from "../panels/RiskPanel";
import StabilityPanel from "../panels/StabilityPanel";
import StudiesPanel from "../panels/StudiesPanel";
import Verdict, { RetractedLine } from "../panels/Verdict";
import VrpPanel from "../panels/VrpPanel";
import { SECTIONS } from "./sections";

const PANELS = [
  ["The forecast chart", ForecastPanel],
  ["The model leaderboard", LeaderboardPanel],
  ["The stability section", StabilityPanel],
  ["The calibration section", CalibrationPanel],
  ["The VaR backtest", RiskPanel],
  ["The volatility cone", ConePanel],
  ["The implied-vs-realized section", VrpPanel],
  ["The live record", LiveRecordPanel],
  ["The leak post-mortem", LeakPanel],
  ["The side studies", StudiesPanel],
  ["The research ledger", LedgerPanel],
  ["The methodology", MethodPanel],
  ["The price chart", MarketPanel],
];

/** The sections whose figures come from research.json (the rest read data.json alone). */
export const RESEARCH_SECTIONS = ["modl", "stab", "calb", "risk", "cone", "vrp", "leak", "ledg", "meth"];

/**
 * The one page-level note for a missing research.json, under the verdict: which sections it
 * affects, what still comes from data.json, and a Retry button (with a result the reader can see).
 */
function ResearchNotice({ onRetry = null }) {
  const [attempt, setAttempt] = useState("idle"); // idle | running | failed
  const codes = SECTIONS.filter((section) => RESEARCH_SECTIONS.includes(section.id)).map((section) => section.code);
  const retry = async () => {
    setAttempt("running");
    const ok = await onRetry();
    setAttempt(ok ? "idle" : "failed");
  };
  return (
    <div id="research-notice" className="notice research-notice" role="status">
      <p>
        research.json did not load, so {codes.length} sections below are missing figures: {codes.join(", ")}.
        The forecast, its validation and the live record come from data.json.
        {attempt === "failed" ? " The retry failed too." : ""}
      </p>
      {onRetry && (
        <button type="button" className="button notice-button" onClick={retry} disabled={attempt === "running"}>
          {attempt === "running" ? "Retrying…" : "Retry"}
        </button>
      )}
    </div>
  );
}

/**
 * The whole page body for one data snapshot. Pure function of its props (no fetching), so it can
 * be server-rendered in tests against fixtures.
 *
 * @param {object} props
 * @param {object} props.data              data.json (schema 3)
 * @param {object|null} props.research     research.json, or null when unavailable
 * @param {"loading"|"ready"|"unavailable"} props.researchStatus
 * @param {object} props.quote             resolveQuote() output
 * @param {string|null} [props.error]      a refresh error to show above the verdict
 * @param {(() => Promise<boolean>)|null} [props.onRetryResearch]  fetches research.json again
 */
export default function Dashboard({ data, research, researchStatus, quote, error = null, onRetryResearch = null }) {
  const props = { data, research, researchStatus };
  const frozenAt = typeof data?.frozen_at === "string" ? data.frozen_at : null;
  const snapshot = useMemo(() => ({ frozenAt }), [frozenAt]);
  return (
    <SnapshotContext.Provider value={snapshot}>
      {error && <div className="notice" role="status">{error}</div>}
      <ErrorBoundary label="The verdict" resetKey={data}>
        <Verdict {...props} />
      </ErrorBoundary>
      {researchStatus === "unavailable" && !research && <ResearchNotice onRetry={onRetryResearch} />}
      <div className="strip-row">
        <ErrorBoundary label="The retraction note" resetKey={data}>
          <RetractedLine data={data} research={research} />
        </ErrorBoundary>
        <ErrorBoundary label="The market strip" resetKey={data}>
          <MarketStrip data={data} quote={quote} />
        </ErrorBoundary>
      </div>
      <div className="panel-grid">
        {PANELS.map(([label, PanelComponent]) => (
          <ErrorBoundary key={label} label={label} resetKey={data}>
            <PanelComponent {...props} />
          </ErrorBoundary>
        ))}
      </div>
    </SnapshotContext.Provider>
  );
}
