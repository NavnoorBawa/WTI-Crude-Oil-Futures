import { Component } from "react";

const REPO_URL = "https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures";

// A malformed payload field should cost one section, not blank the page. With a `label` the
// boundary renders an inline notice for that section; without one it is the app-level fallback.
// `resetKey` (e.g. the data object) retries rendering when fresh data arrives.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error(`[${this.props.label || "dashboard"}] render failed`, error, info?.componentStack);
  }

  componentDidUpdate(prevProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const message = error?.message || String(error);

    if (this.props.label) {
      return (
        <div className="tv-section-error" role="alert">
          {this.props.label} could not be displayed ({message}). The rest of the dashboard is unaffected.
        </div>
      );
    }

    return (
      <div className="tv-app tv-center">
        <div className="tv-fatal" role="alert">
          <h1 className="tv-fatal-title">The dashboard failed to render</h1>
          <p className="tv-fatal-text">{message}</p>
          <p className="tv-fatal-text">
            This is usually one malformed field in the data snapshot. Reloading fetches the latest
            snapshot; the research write-up is on{" "}
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer">GitHub</a>.
          </p>
          <button type="button" className="tv-button" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
