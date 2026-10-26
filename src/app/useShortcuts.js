import { useEffect, useRef, useSyncExternalStore } from "react";

/*
 * Page-wide keyboard shortcuts:
 *   /  or  Ctrl/Cmd+K   open the jump palette
 *   j  /  k             next / previous section (scroll, then focus its heading)
 *   t                   table view of the current section, on or off
 *   c                   copy a link to the current section
 *   ?                   the list of shortcuts (in the palette)
 *
 * A key does nothing while the reader types (an input, select, textarea or editable element has
 * focus), while a dialog is open, or with Ctrl, Alt or Cmd held (Shift only for "?" and "/"). Ctrl/Cmd+K is
 * the one chord. The single-key shortcuts (all but Ctrl/Cmd+K) can be turned off in the
 * shortcuts list (WCAG 2.1.4); the choice is kept in this browser's localStorage when it may be.
 */

const STORAGE_KEY = "wti-vol.single-key-shortcuts";
const CHANGE_EVENT = "shortcuts:setting";

/** The shortcuts as the list in the palette shows them. */
export const SHORTCUTS = [
  { keys: ["/"], also: ["Ctrl K", "⌘ K"], what: "Open the jump palette" },
  { keys: ["j"], what: "Next section" },
  { keys: ["k"], what: "Previous section" },
  { keys: ["t"], what: "Table view of the current section, on or off" },
  { keys: ["c"], what: "Copy a link to the current section" },
  { keys: ["?"], what: "This list" },
];

/** In the palette: how to move through the results. */
export const PALETTE_KEYS = [
  { keys: ["↑", "↓"], what: "Move through the results" },
  { keys: ["Home", "End"], what: "First or last result" },
  { keys: ["Enter"], what: "Go to the result, or run it" },
  { keys: ["Esc"], what: "Close, back to where you were" },
];

const readStored = () => {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "off";
  } catch {
    return true; // storage blocked (private mode, a sandboxed frame): the default stands
  }
};

let singleKeys = null;

/** Whether the single-key shortcuts are on (default on). */
export const singleKeysEnabled = () => {
  if (singleKeys == null) singleKeys = typeof window === "undefined" ? true : readStored();
  return singleKeys;
};

/** Turns the single-key shortcuts on or off, for this page and (when storage works) later visits. */
export function setSingleKeysEnabled(on) {
  singleKeys = Boolean(on);
  try {
    window.localStorage.setItem(STORAGE_KEY, singleKeys ? "on" : "off");
  } catch {
    // Not kept across visits; this page still follows the choice.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

const subscribe = (onChange) => {
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => window.removeEventListener(CHANGE_EVENT, onChange);
};

/** The single-key setting as React state. */
export const useSingleKeys = () => useSyncExternalStore(subscribe, singleKeysEnabled, () => true);

const TEXT_INPUT_ROLES = new Set(["textbox", "searchbox", "combobox", "spinbutton", "slider"]);

/** Whether a key event's target is somewhere the reader types. Works on any element-like object. */
export const isTypingTarget = (target) => {
  if (!target) return false;
  const tag = String(target.tagName ?? "").toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea") return true;
  if (target.isContentEditable === true) return true;
  const role = typeof target.getAttribute === "function" ? target.getAttribute("role") : target.role;
  return TEXT_INPUT_ROLES.has(String(role ?? "").toLowerCase());
};

const SINGLE_KEYS = { "/": "palette", j: "next", k: "previous", t: "table", c: "copy-link" };

/**
 * The action a keydown asks for, or null: "palette", "shortcuts", "next", "previous", "table" or
 * "copy-link". Pure: it reads only the event-like object's key, modifiers, repeat, isComposing,
 * defaultPrevented and target.
 *
 * @param {{key: string, ctrlKey?: boolean, metaKey?: boolean, altKey?: boolean, shiftKey?: boolean,
 *   repeat?: boolean, isComposing?: boolean, defaultPrevented?: boolean, target?: object}} event
 * @param {{singleKeys?: boolean}} [options]
 */
export function keyToAction(event, { singleKeys: enabled = true } = {}) {
  if (!event || typeof event.key !== "string" || event.defaultPrevented || event.isComposing) return null;
  if (isTypingTarget(event.target)) return null;
  const { key } = event;
  const chord = event.ctrlKey || event.metaKey;
  if (chord && !event.altKey && !event.shiftKey && key.toLowerCase() === "k") return event.repeat ? null : "palette";
  if (chord || event.altKey || !enabled) return null;
  if (key === "?") return event.repeat ? null : "shortcuts";
  // Shift is allowed only where a layout needs it to type the character ("/" is Shift+7 in German).
  if (event.shiftKey && key !== "/") return null;
  const action = SINGLE_KEYS[key] ?? null;
  // Holding j or k keeps moving; the other keys act once per press.
  if (action && event.repeat && action !== "next" && action !== "previous") return null;
  return action;
}

/**
 * Listens for the shortcuts on the window and calls onAction(action) for each one it handles.
 *
 * @param {boolean} enabled                     off until the sections exist
 * @param {(action: string) => void} onAction
 */
export default function useShortcuts(enabled, onAction) {
  const handler = useRef(onAction);
  useEffect(() => { handler.current = onAction; }, [onAction]);

  useEffect(() => {
    if (!enabled) return undefined;
    const onKeyDown = (event) => {
      if (document.querySelector("dialog[open]")) return; // a dialog handles its own keys
      const action = keyToAction(event, { singleKeys: singleKeysEnabled() });
      if (!action) return;
      event.preventDefault();
      handler.current(action);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}
