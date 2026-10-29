import { startTransition, useCallback, useEffect, useRef, useState } from "react";
import { REQUEST_TIMEOUT_MS, RESEARCH_URL } from "./config";

const isResearchPayload = (value) =>
  value != null && typeof value === "object" && !Array.isArray(value)
  && (value.vol_lab != null || value.postmortem != null || value.carry != null);

/**
 * research.json is optional. It is fetched once at start-up, in parallel with data.json, and again
 * whenever a newer data snapshot arrives (`snapshotKey` changes after the first one), keeping the
 * previous copy if a refresh fails. While it is unavailable it is fetched again when the browser
 * comes back online or the tab becomes visible, and `retry()` fetches it on demand (the page-level
 * notice's Retry button). A retry never flips the status back to "loading", so the panels do not
 * flash placeholders while it runs. Panels that need it render a one-line note instead.
 *
 * index.html preloads research.json beside data.json, so it often lands before the first snapshot
 * has rendered and joins that render. Once the page is up, filling in the research panels
 * re-renders all of it, so that update is a transition, which React can split to keep the page
 * responsive.
 *
 * @param {string|null} snapshotKey e.g. data.frozen_at
 * @returns {{research: object|null, status: "loading"|"ready"|"unavailable", retry: () => Promise<boolean>}}
 *   retry resolves to whether research.json is now loaded
 */
export default function useResearch(snapshotKey) {
  const [state, setState] = useState({ research: null, status: "loading" });
  const aliveRef = useRef(true);
  const seenKeyRef = useRef(null);
  const loadRef = useRef(null);
  const inFlightRef = useRef(null);
  const statusRef = useRef(state.status);
  statusRef.current = state.status;
  const snapshotRef = useRef(snapshotKey);
  snapshotRef.current = snapshotKey;

  loadRef.current = () => {
    if (inFlightRef.current) return inFlightRef.current;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    const keepOrFail = (previous) => (previous.research ? previous : { research: null, status: "unavailable" });
    const settle = (update) => {
      if (!aliveRef.current) return;
      if (snapshotRef.current != null) startTransition(() => setState(update));
      else setState(update);
    };
    const request = fetch(RESEARCH_URL, { signal: controller.signal, cache: "no-cache", headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        const ok = isResearchPayload(payload);
        settle(ok ? { research: payload, status: "ready" } : keepOrFail);
        return ok;
      })
      .catch(() => {
        settle(keepOrFail);
        return false;
      })
      .finally(() => {
        clearTimeout(timeoutId);
        inFlightRef.current = null;
      });
    inFlightRef.current = request;
    return request;
  };

  useEffect(() => {
    aliveRef.current = true;
    loadRef.current();
    const retryIfUnavailable = () => {
      if (statusRef.current === "unavailable") loadRef.current();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") retryIfUnavailable();
    };
    window.addEventListener("online", retryIfUnavailable);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      aliveRef.current = false;
      window.removeEventListener("online", retryIfUnavailable);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => {
    if (snapshotKey == null) return;
    if (seenKeyRef.current == null) {
      seenKeyRef.current = snapshotKey; // the start-up fetch already covers the first snapshot
      return;
    }
    if (seenKeyRef.current === snapshotKey) return;
    seenKeyRef.current = snapshotKey;
    loadRef.current();
  }, [snapshotKey]);

  const retry = useCallback(() => loadRef.current(), []);
  return { research: state.research, status: state.status, retry };
}
