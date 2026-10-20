import { useCallback, useEffect, useRef, useState } from "react";
import {
  CONFIGURED_API_BASE,
  INITIAL_LOADING_MESSAGE,
  MAX_BACKGROUND_RETRY_MS,
  MAX_INITIAL_FETCH_ATTEMPTS,
  POLL_INTERVAL_MS,
  REQUEST_TIMEOUT_MS,
  STARTUP_RETRY_MS,
  STATIC_DATA_MODE,
  STATIC_DATA_URL,
  clampMilliseconds,
} from "./config";

// In static mode the "endpoint" is just the frozen JSON file shipped alongside the site.
const buildRequestUrl = (apiBase) => (apiBase.endsWith(".json") ? apiBase : `${apiBase}/data`);

const getApiBaseCandidates = () => {
  if (STATIC_DATA_MODE) return [STATIC_DATA_URL];
  if (CONFIGURED_API_BASE) return [CONFIGURED_API_BASE];
  // Local dev default only; vite.config.js refuses a production build that would ship it.
  return ["http://127.0.0.1:9000"];
};

/**
 * Loads data.json (or the dev backend) and keeps it fresh.
 *
 * Startup: up to MAX_INITIAL_FETCH_ATTEMPTS tries with the loading screen showing the count; after
 * that the error screen shows but retries continue in the background with capped backoff, so a
 * backend still warming up, or a data.json blip at page load, never needs a reload. Once data is
 * loaded it is re-polled every POLL_INTERVAL_MS; a failed refresh keeps the last good snapshot.
 *
 * @returns {{data: object|null, loading: boolean, error: string|null, loadingMessage: string,
 *   lastUpdate: number|null, retryNow: () => void}}
 */
export default function useDashboardData() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [loadingMessage, setLoadingMessage] = useState(INITIAL_LOADING_MESSAGE);
  const [lastUpdate, setLastUpdate] = useState(null);
  const latestDataRef = useRef(null);
  const retryNowRef = useRef(null);

  latestDataRef.current = data;

  useEffect(() => {
    let isDisposed = false;
    let requestInFlight = false;
    let retryPending = false;
    let initialAttemptCount = 0;
    let retryTimeoutId = null;
    const activeControllers = new Set();

    const clearRetryTimeout = () => {
      if (retryTimeoutId) {
        clearTimeout(retryTimeoutId);
        retryTimeoutId = null;
      }
    };

    const retryDelayMs = (requestedDelayMs) => clampMilliseconds(requestedDelayMs, STARTUP_RETRY_MS, 2000, 60 * 1000);

    // Returns true while the startup budget lasts (the loading screen shows the retry count).
    const scheduleInitialRetry = (requestedDelayMs, message) => {
      if (isDisposed || latestDataRef.current) return false;

      retryPending = true;
      clearRetryTimeout();
      const withinBudget = initialAttemptCount < MAX_INITIAL_FETCH_ATTEMPTS;
      if (withinBudget) {
        setLoading(true);
        setError(null);
        setLoadingMessage(`${message} Retrying (${initialAttemptCount + 1}/${MAX_INITIAL_FETCH_ATTEMPTS})…`);
      }
      const delayMs = withinBudget
        ? retryDelayMs(requestedDelayMs)
        : Math.min(
          MAX_BACKGROUND_RETRY_MS,
          retryDelayMs(requestedDelayMs) * 2 ** (initialAttemptCount - MAX_INITIAL_FETCH_ATTEMPTS + 1)
        );
      retryTimeoutId = setTimeout(() => {
        retryTimeoutId = null;
        retryPending = false;
        if (!isDisposed) fetchData(true, { quiet: !withinBudget });
      }, delayMs);
      return withinBudget;
    };

    // quiet: a background retry keeps the error screen up meanwhile.
    async function fetchData(isInitial = false, { quiet = false } = {}) {
      if (isDisposed || requestInFlight) return;
      if (!isInitial && retryPending && !latestDataRef.current) return;

      requestInFlight = true;
      if (isInitial && !latestDataRef.current) initialAttemptCount += 1;

      try {
        if (isInitial && !quiet && !latestDataRef.current) {
          setLoading(true);
          setError(null);
          setLoadingMessage(INITIAL_LOADING_MESSAGE);
        }

        let result = null;
        let lastAttemptError = null;

        for (const apiBase of getApiBaseCandidates()) {
          if (isDisposed) return;

          const controller = new AbortController();
          activeControllers.add(controller);
          let requestTimedOut = false;
          const timeoutId = setTimeout(() => {
            requestTimedOut = true;
            controller.abort();
          }, REQUEST_TIMEOUT_MS);

          try {
            const attempt = await fetch(buildRequestUrl(apiBase), {
              signal: controller.signal,
              method: "GET",
              // The Pages CDN caches data.json for minutes; revalidate so a re-poll sees a new deploy.
              cache: STATIC_DATA_MODE ? "no-cache" : "default",
              headers: { Accept: "application/json" },
            });

            let responsePayload = null;
            let responseParseError = null;
            try {
              responsePayload = await attempt.json();
            } catch (parseError) {
              responseParseError = parseError;
            }

            const retryAfterHeader = Number(attempt.headers.get("Retry-After"));
            if (responsePayload?.error === "SYSTEM_INITIALIZING") {
              const startupError = new Error(responsePayload.message || "Backend is waking up");
              startupError.code = "SYSTEM_INITIALIZING";
              const retryAfterSeconds = Number(responsePayload.retry_after_seconds);
              startupError.retryAfterMs = Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0
                ? retryAfterSeconds * 1000
                : Number.isFinite(retryAfterHeader) && retryAfterHeader > 0
                  ? retryAfterHeader * 1000
                  : STARTUP_RETRY_MS;
              throw startupError;
            }

            if (!attempt.ok) {
              const errorMessage = responsePayload?.message || responsePayload?.error || attempt.statusText || "Request failed";
              throw new Error(`HTTP ${attempt.status}: ${errorMessage}`);
            }

            if (responseParseError || responsePayload == null || typeof responsePayload !== "object" || Array.isArray(responsePayload)) {
              throw new Error("The data endpoint returned invalid JSON");
            }

            result = responsePayload;
            break;
          } catch (attemptErr) {
            if (isDisposed) return;
            if (requestTimedOut && attemptErr?.name === "AbortError") {
              const timeoutError = new Error("Server timeout - Please wait and refresh");
              timeoutError.code = "REQUEST_TIMEOUT";
              lastAttemptError = timeoutError;
            } else {
              lastAttemptError = attemptErr;
            }
          } finally {
            clearTimeout(timeoutId);
            activeControllers.delete(controller);
          }
        }

        if (isDisposed) return;
        if (!result) throw lastAttemptError || new Error("No reachable API endpoint found");

        clearRetryTimeout();
        retryPending = false;
        initialAttemptCount = 0;
        setData(result);
        setLastUpdate(Date.now());
        setError(null);
        setLoading(false);
        setLoadingMessage(INITIAL_LOADING_MESSAGE);
      } catch (err) {
        if (isDisposed) return;

        const isStarting = err?.code === "SYSTEM_INITIALIZING";
        const userMessage = isStarting
          ? err.message || "Backend is warming up the model."
          : err?.code === "REQUEST_TIMEOUT" || err?.name === "AbortError"
            ? "Server timeout - Please wait and refresh"
            : err?.name === "TypeError" && String(err.message).includes("Failed to fetch")
              ? "Cannot connect to the data service"
              : `Network error: ${err?.message || "Unknown request failure"}`;

        if (!latestDataRef.current) {
          const scheduled = scheduleInitialRetry(isStarting ? err.retryAfterMs : STARTUP_RETRY_MS, userMessage);
          if (scheduled) return;
        }

        setError(
          latestDataRef.current
            ? STATIC_DATA_MODE
              ? `Snapshot refresh failed: ${userMessage}. Showing the snapshot loaded earlier.`
              : `Live data delayed: ${userMessage}. Showing the last good snapshot.`
            : userMessage
        );
        setLoading(false);
      } finally {
        requestInFlight = false;
      }
    }

    fetchData(true);

    const interval = setInterval(() => {
      if (latestDataRef.current) fetchData(false);
    }, POLL_INTERVAL_MS);

    // The error screen's Retry button and a reconnect skip the backoff wait (and, with no data yet,
    // restart the startup budget so the loading screen counts the attempts again).
    const retryNow = () => {
      if (isDisposed || requestInFlight) return;
      if (latestDataRef.current) {
        fetchData(false);
        return;
      }
      clearRetryTimeout();
      retryPending = false;
      initialAttemptCount = 0;
      fetchData(true);
    };
    retryNowRef.current = retryNow;
    window.addEventListener("online", retryNow);

    return () => {
      isDisposed = true;
      retryNowRef.current = null;
      window.removeEventListener("online", retryNow);
      clearRetryTimeout();
      clearInterval(interval);
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
    };
  }, []);

  const retryNow = useCallback(() => retryNowRef.current?.(), []);
  return { data, loading, error, loadingMessage, lastUpdate, retryNow };
}
