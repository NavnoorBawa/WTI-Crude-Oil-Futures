import { useCallback, useMemo, useSyncExternalStore } from "react";

/*
 * View state in the URL query, so every view of the page is a link that can be shared.
 *
 * Reserved keys (each panel reads only its own; anything else in the query is kept untouched):
 *   fcst    FCST chart range            1y | 3y | all       (default 3y)
 *   risk    RISK chart range            3y | 10y | all      (default 3y)
 *   var     RISK VaR level              99 | 95             (default 99)
 *   leak    LEAK window                 5y | 10y            (default 5y)
 *   mkt     MKT chart range             1w | 1m | 1y | all  (default all)
 *   table   sections in table view      comma list of section ids, e.g. table=fcst,modl
 *   family  LEDG family filter          a family key        (default: all families)
 *   status  LEDG status filter          a status key        (default: all statuses)
 *   q       LEDG search text            free text           (default: empty)
 *   sort    LEDG sort                   <column>-<asc|desc> (default: the ledger's own order)
 *
 * Reading never trusts the URL: a value outside the allowed set reads as the default, so a bad or
 * outdated link can never break a render. Writing uses history.replaceState (no history entry per
 * click), keeps the #hash, and drops a parameter equal to its default, so the bare URL stays the
 * canonical one. Server rendering (tests) reads an empty query, i.e. every default.
 */

const EVENT = "viewstate";
const EMPTY = Object.freeze({});

/** Defaults of the reserved keys, used when a non-React caller writes a key no mounted panel reads. */
export const VIEW_DEFAULTS = Object.freeze({
  fcst: "3y", risk: "3y", var: "99", leak: "5y", mkt: "all", table: "", family: "all", status: "all", q: "", sort: "",
});

const registeredDefaults = new Map();

/** "?fcst=all&table=fcst,modl" -> {fcst: "all", table: "fcst,modl"} (the last of a repeated key wins). Pure. */
export const parseViewParams = (search) => {
  const params = {};
  const text = typeof search === "string" ? search.replace(/^\?/, "") : "";
  if (!text) return params;
  for (const [key, value] of new URLSearchParams(text)) params[key] = value;
  return params;
};

const sameValue = (a, b) => String(a ?? "").toLowerCase() === String(b ?? "").toLowerCase();
// Commas stay readable in a list value; everything else is percent-encoded.
const encode = (text) => encodeURIComponent(text).replace(/%2C/gi, ",");

/**
 * {fcst: "all", risk: "3y"} with defaults {risk: "3y"} -> "?fcst=all". Empty values and values equal
 * to their default (case-insensitively) are dropped; "" when nothing is left. Pure.
 *
 * @param {Record<string, string|null|undefined>} params
 * @param {Record<string, string>} [defaults]
 */
export const serializeViewParams = (params, defaults = {}) => {
  const parts = Object.entries(params ?? {})
    .filter(([key, value]) => value != null && String(value) !== "" && !(key in defaults && sameValue(value, defaults[key])))
    .map(([key, value]) => `${encode(key)}=${encode(String(value))}`);
  return parts.length ? `?${parts.join("&")}` : "";
};

/**
 * The value of `key` as the page should use it: the allowed entry it matches case-insensitively,
 * else the default. With `allowed` null any non-empty text is accepted (a search box), and a
 * function decides for itself. Pure.
 *
 * @param {Record<string, string>} params
 * @param {string} key
 * @param {readonly string[]|((value: string) => boolean)|null} allowed
 * @param {string} defaultValue
 */
export const readViewParam = (params, key, allowed, defaultValue) => {
  const raw = params?.[key];
  if (typeof raw !== "string" || raw === "") return defaultValue;
  if (allowed == null) return raw;
  if (typeof allowed === "function") return allowed(raw) ? raw : defaultValue;
  return allowed.find((option) => sameValue(option, raw)) ?? defaultValue;
};

/** "fcst, modl,,x" -> ["fcst", "modl", "x"]. Pure. */
export const parseViewList = (value) =>
  (typeof value === "string" ? [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))] : []);

// ------------------------------------------------------------------------------------- the store
let cache = { search: null, params: EMPTY };

const getSnapshot = () => {
  const { search } = window.location;
  if (search !== cache.search) cache = { search, params: Object.freeze(parseViewParams(search)) };
  return cache.params;
};
const getServerSnapshot = () => EMPTY;

const subscribe = (onChange) => {
  window.addEventListener("popstate", onChange);
  window.addEventListener(EVENT, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(EVENT, onChange);
  };
};

const defaultFor = (key, explicit) =>
  explicit !== undefined ? explicit : registeredDefaults.has(key) ? registeredDefaults.get(key) : VIEW_DEFAULTS[key];

/**
 * Writes one key into the URL (history.replaceState, hash kept) and tells every reader. For callers
 * outside React too, e.g. a command palette: setViewParam("fcst", "all"). A value equal to the
 * key's default, or empty, removes the key.
 *
 * @param {string} key
 * @param {string|null|undefined} value
 * @param {string} [defaultValue]  defaults to the one a mounted reader registered, else VIEW_DEFAULTS
 */
export function setViewParam(key, value, defaultValue) {
  if (typeof window === "undefined" || typeof window.history?.replaceState !== "function") return;
  const current = parseViewParams(window.location.search);
  const next = { ...current, [key]: value == null ? "" : String(value) };
  const fallback = defaultFor(key, defaultValue);
  const search = serializeViewParams(next, fallback === undefined ? {} : { [key]: fallback });
  if (search === window.location.search) return;
  const url = `${window.location.pathname}${search}${window.location.hash}`;
  try {
    window.history.replaceState(window.history.state, "", url);
  } catch {
    return; // e.g. a sandboxed frame; the view still changes for this render
  }
  window.dispatchEvent(new Event(EVENT));
}

const useParams = () => useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

/**
 * One view setting held in the URL.
 *
 * @param {string} key                  a reserved key (see the header)
 * @param {readonly string[]|((value: string) => boolean)|null} allowed  accepted values (case-insensitive)
 * @param {string} defaultValue
 * @returns {[string, (value: string) => void]}
 */
export function useViewParam(key, allowed, defaultValue) {
  registeredDefaults.set(key, defaultValue);
  const params = useParams();
  const value = readViewParam(params, key, allowed, defaultValue);
  const set = useCallback((next) => setViewParam(key, next, defaultValue), [key, defaultValue]);
  return [value, set];
}

/**
 * A set of ids held in the URL as a comma list (table=fcst,modl).
 *
 * @param {string} key
 * @returns {[Set<string>, (id: string) => void]}
 */
export function useViewList(key) {
  const raw = useParams()[key];
  const items = useMemo(() => new Set(parseViewList(raw)), [raw]);
  const toggle = useCallback((id) => {
    const current = parseViewList(parseViewParams(window.location.search)[key]);
    const next = current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
    setViewParam(key, next.join(","), "");
  }, [key]);
  return [items, toggle];
}
