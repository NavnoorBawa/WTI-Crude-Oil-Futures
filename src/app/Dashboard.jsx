import ErrorBoundary from "../components/ErrorBoundary";
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
 */
export default function Dashboard({ data, research, researchStatus, quote, error = null }) {
  const props = { data, research, researchStatus };
  return (
    <>
      {error && <div className="notice" role="status">{error}</div>}
      <ErrorBoundary label="The verdict" resetKey={data}>
        <Verdict {...props} />
      </ErrorBoundary>
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
    </>
  );
}
