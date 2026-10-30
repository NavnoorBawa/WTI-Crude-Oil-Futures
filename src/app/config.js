// Build-time configuration (Vite env) and the dashboard's timing constants.

const clampMilliseconds = (rawValue, fallback, minimum, maximum) => {
  const parsed = Number(rawValue);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
};
export { clampMilliseconds };

// Static-snapshot mode (GitHub Pages): the app reads a frozen data.json produced by freeze.py in CI
// instead of polling a live backend. BASE_URL handles the Pages sub-path.
export const STATIC_DATA_MODE = import.meta.env.VITE_STATIC_DATA === "true";
export const CONFIGURED_API_BASE = import.meta.env.VITE_API_BASE_URL;
export const BASE_URL = import.meta.env.BASE_URL ?? "/";
export const STATIC_DATA_URL = `${BASE_URL}data.json`;
// research.json is always a static file next to the page (freeze.py writes it into public/).
export const RESEARCH_URL = `${BASE_URL}research.json`;
export const LIVE_PRICE_URL = import.meta.env.VITE_LIVE_PRICE_URL;
export const REPO_URL = "https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures";
// The commit the bundle was built from: VITE_GIT_SHA, else GITHUB_SHA in Actions (vite.config.js).
export const GIT_SHA = typeof import.meta.env.VITE_GIT_SHA === "string" && /^[0-9a-f]{7,40}$/i.test(import.meta.env.VITE_GIT_SHA)
  ? import.meta.env.VITE_GIT_SHA
  : null;
// The same-origin copy of the live quote that every deploy carries (useLiveQuote reads it too).
export const PRICE_URL = `${BASE_URL}price.json`;

/** A file in the repository on GitHub, at the commit the bundle was built from (else main). */
export const codeUrl = (path) => `${REPO_URL}/blob/${GIT_SHA ?? "main"}/${String(path).replace(/^\/+/, "")}`;
// Commit histories that let a reader check the record and the snapshots from the page: every live
// forecast is committed to runtime-state/vol_track_record.json on the live-data branch, and every
// deploy commits data.json to gh-pages.
export const RECORD_HISTORY_URL = `${REPO_URL}/commits/live-data/runtime-state/vol_track_record.json`;
export const DATA_HISTORY_URL = `${REPO_URL}/commits/gh-pages/data.json`;
// The written documentation, always the current version on main. The full write-up and the
// reviewer's checks come first wherever the page lists them.
export const DOCS = {
  results: `${REPO_URL}/blob/main/docs/RESULTS.md`,
  reviewerGuide: `${REPO_URL}/blob/main/docs/REVIEWER_GUIDE.md`,
  modelCard: `${REPO_URL}/blob/main/docs/MODEL_CARD.md`,
  methodology: `${REPO_URL}/blob/main/docs/METHODOLOGY.md`,
  datasheet: `${REPO_URL}/blob/main/docs/DATASHEET.md`,
  changelog: `${REPO_URL}/blob/main/CHANGELOG.md`,
  citation: `${REPO_URL}/blob/main/CITATION.cff`,
  license: `${REPO_URL}/blob/main/LICENSE`,
};

// A frozen snapshot changes every ~4h, so it is re-checked every 15 min; a live backend polls faster.
export const POLL_INTERVAL_MS = clampMilliseconds(
  import.meta.env.VITE_POLL_INTERVAL_MS,
  STATIC_DATA_MODE ? 15 * 60 * 1000 : 15000,
  5000,
  24 * 60 * 60 * 1000
);
export const STARTUP_RETRY_MS = clampMilliseconds(import.meta.env.VITE_STARTUP_RETRY_MS, 5000, 2000, 60 * 1000);
export const REQUEST_TIMEOUT_MS = 30 * 1000;
export const MAX_INITIAL_FETCH_ATTEMPTS = 5;
export const MAX_BACKGROUND_RETRY_MS = 60 * 1000;   // retry cadence once the startup budget is spent
export const LIVE_PRICE_POLL_MS = 3 * 60 * 1000;
export const LIVE_BADGE_MAX_AGE_MS = 30 * 60 * 1000; // exchange quote time, not fetch time
export const STALE_SNAPSHOT_MS = 12 * 60 * 60 * 1000; // GitHub often runs the 4h schedule 4-9h apart
export const MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;
export const MAX_QUOTE_DEVIATION = 0.25;            // a quote >25% from the snapshot is a bad tick
export const INITIAL_LOADING_MESSAGE = STATIC_DATA_MODE
  ? "Loading the latest research snapshot"
  : "Connecting to the data service";
export const DELAY_NOTE = "Futures quotes come from Yahoo Finance and are exchange-delayed by about 10 minutes.";
