import { useMemo } from "react";
import { fmtCT, toMs } from "../lib/format";
import useNow from "../lib/useNow";
import { REPO_URL, STALE_SNAPSHOT_MS, STATIC_DATA_MODE } from "./config";
import Dashboard from "./Dashboard";
import { describeQuoteTime, resolveQuote } from "./quote";
import SiteFooter from "./SiteFooter";
import TopBar from "./TopBar";
import useDashboardData from "./useDashboardData";
import useHashLanding from "./useHashLanding";
import useLiveQuote from "./useLiveQuote";
import useResearch from "./useResearch";

/** Where to read about the project while the data cannot be shown (as the app-level error boundary says). */
function GitHubNote() {
  return (
    <p className="status-text">
      The research write-up and its history are on{" "}
      <a href={REPO_URL} target="_blank" rel="noopener noreferrer">GitHub</a>.
    </p>
  );
}

function StaleSnapshotBanner({ frozenAtMs }) {
  const now = useNow(60 * 1000);
  const ageMs = now - frozenAtMs;
  if (ageMs <= STALE_SNAPSHOT_MS) return null;
  const hours = Math.floor(ageMs / (60 * 60 * 1000));
  const age = hours >= 48 ? `${Math.floor(hours / 24)} days` : `${hours} hours`;
  return (
    <div className="notice is-warn" role="status">
      This snapshot is {age} old (frozen {fmtCT(frozenAtMs)}). Scheduled refreshes are running late, so the
      forecast, quote and live record may be out of date.
    </div>
  );
}

function StatusScreen({ title, text, isError = false, onRetry = null, children }) {
  return (
    <div className="status-screen">
      <div className="status-card" role={isError ? "alert" : "status"}>
        {!isError && <div className="status-spinner" aria-hidden="true" />}
        <p className={`status-title${isError ? " is-error" : ""}`}>{title}</p>
        {text && <p className="status-text">{text}</p>}
        {children}
        {onRetry && <button type="button" className="button" onClick={onRetry}>Retry now</button>}
      </div>
    </div>
  );
}

/**
 * Data loading and the page frame. data.json and research.json are fetched in parallel; the
 * dashboard renders as soon as data.json arrives and fills in the research panels when (and if)
 * research.json does.
 */
export default function App() {
  const { data, loading, error, loadingMessage, lastUpdate, retryNow } = useDashboardData();
  const frozenAt = typeof data?.frozen_at === "string" ? data.frozen_at : null;
  const { research, status: researchStatus, retry: retryResearch } = useResearch(frozenAt);
  const liveQuote = useLiveQuote(data);
  const quote = useMemo(() => {
    const resolved = resolveQuote(liveQuote, data);
    return { ...resolved, label: describeQuoteTime(resolved) };
  }, [liveQuote, data]);
  const frozenAtMs = toMs(frozenAt);
  useHashLanding(Boolean(data) && !data.error, researchStatus);

  let body;
  if (loading && !data) {
    body = <StatusScreen title="WTI Volatility Research" text={loadingMessage} />;
  } else if (error && !data) {
    body = (
      <StatusScreen title="Data connection unavailable" text={error} isError onRetry={retryNow}>
        <p className="status-text">Real data only: no placeholder values are shown. Retrying automatically.</p>
        <GitHubNote />
      </StatusScreen>
    );
  } else if (data?.error) {
    body = (
      <StatusScreen title="System error" text={[data.error, data.message].filter(Boolean).map(String).join(" ")} isError onRetry={retryNow}>
        <GitHubNote />
      </StatusScreen>
    );
  } else if (data) {
    body = (
      <Dashboard
        data={data}
        research={research}
        researchStatus={researchStatus}
        quote={quote}
        error={error}
        onRetryResearch={retryResearch}
      />
    );
  }

  return (
    <div className="app" id="top">
      <a className="skip-link" href="#main">Skip to content</a>
      <TopBar frozenAtMs={frozenAtMs} lastUpdate={lastUpdate} hasData={Boolean(data)} data={data} research={research} />
      {STATIC_DATA_MODE && frozenAtMs != null && <StaleSnapshotBanner frozenAtMs={frozenAtMs} />}
      <main id="main" className="main" tabIndex={-1}>{body}</main>
      <SiteFooter data={data} />
    </div>
  );
}
