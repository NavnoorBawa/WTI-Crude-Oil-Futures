import { useEffect, useRef, useState } from "react";
import { REQUEST_TIMEOUT_MS, RESEARCH_URL } from "./config";

const isResearchPayload = (value) =>
  value != null && typeof value === "object" && !Array.isArray(value)
  && (value.vol_lab != null || value.postmortem != null || value.carry != null);

/**
 * research.json is optional. It is fetched once at start-up, in parallel with data.json, and again
 * whenever a newer data snapshot arrives (`snapshotKey` changes after the first one), keeping the
 * previous copy if a refresh fails. Panels that need it render an "unavailable" note instead.
 *
 * @param {string|null} snapshotKey e.g. data.frozen_at
 * @returns {{research: object|null, status: "loading"|"ready"|"unavailable"}}
 */
export default function useResearch(snapshotKey) {
  const [state, setState] = useState({ research: null, status: "loading" });
  const aliveRef = useRef(true);
  const seenKeyRef = useRef(null);
  const loadRef = useRef(null);

  loadRef.current = () => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    const keepOrFail = (previous) => (previous.research ? previous : { research: null, status: "unavailable" });
    fetch(RESEARCH_URL, { signal: controller.signal, cache: "no-cache", headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (!aliveRef.current) return;
        setState(isResearchPayload(payload) ? { research: payload, status: "ready" } : keepOrFail);
      })
      .catch(() => {
        if (aliveRef.current) setState(keepOrFail);
      })
      .finally(() => clearTimeout(timeoutId));
  };

  useEffect(() => {
    aliveRef.current = true;
    loadRef.current();
    return () => { aliveRef.current = false; };
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

  return state;
}
