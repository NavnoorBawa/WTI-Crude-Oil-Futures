import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { splitTerms } from "../lib/glossary";

const HOVER_DELAY_MS = 250;
const EDGE_PX = 8;

const canHover = () => typeof window !== "undefined" && Boolean(window.matchMedia?.("(hover: hover)")?.matches);

/** The part of the page on screen, in getBoundingClientRect coordinates (the visual viewport). */
const visibleBox = () => {
  const root = document.documentElement;
  const vv = window.visualViewport;
  if (!vv) return { left: 0, top: 0, right: root.clientWidth, bottom: root.clientHeight };
  return {
    left: vv.offsetLeft,
    top: vv.offsetTop,
    right: Math.min(root.clientWidth, vv.offsetLeft + vv.width),
    bottom: vv.offsetTop + vv.height,
  };
};

/**
 * A glossary toggletip: the term as a button (dotted underline) that opens a one-sentence
 * definition and, where METH lists the method, a link to its reference row. It opens on click, tap,
 * Enter or Space, and on hover only where the device can hover. It closes on Escape (focus returns
 * to the term if it was inside), on a pointer press outside, or when focus leaves term and
 * definition. The definition is rendered only while open, so the page's text (and find-in-page)
 * reads the sentence as written.
 *
 * @param {{entry: import("../lib/glossary").GlossaryEntry, children: import("react").ReactNode}} props
 */
export default function Term({ entry, children }) {
  // null (closed), "hover" (opened by the pointer, closes when it leaves) or "pinned" (clicked).
  const [mode, setMode] = useState(null);
  const wrapRef = useRef(null);
  const buttonRef = useRef(null);
  const popRef = useRef(null);
  const hoverTimer = useRef(null);
  const popId = useId();
  const open = mode != null;

  const clearHoverTimer = () => {
    if (hoverTimer.current != null) clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
  };
  useEffect(() => clearHoverTimer, []);

  const close = useCallback(() => setMode(null), []);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (!wrapRef.current?.contains(event.target)) close();
    };
    const onKeyDown = (event) => {
      if (event.key !== "Escape") return;
      const focusInside = wrapRef.current?.contains(document.activeElement);
      close();
      if (focusInside) buttonRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, close]);

  // Keep the definition on screen: shift it left near the right edge, and above the term near the
  // bottom when there is room there. The shift moves the box itself (CSS `left`), so it never
  // widens the page. The edges are the visual viewport's: while the box is measured unshifted it
  // can overflow, and a phone's layout viewport (innerWidth, innerHeight) then grows to hold it.
  useLayoutEffect(() => {
    const pop = popRef.current;
    if (!open || !pop) return;
    pop.style.setProperty("--term-shift", "0px");
    pop.removeAttribute("data-place");
    const rect = pop.getBoundingClientRect();
    const view = visibleBox();
    let shift = 0;
    if (rect.right > view.right - EDGE_PX) shift = view.right - EDGE_PX - rect.right;
    if (rect.left + shift < view.left + EDGE_PX) shift = view.left + EDGE_PX - rect.left;
    pop.style.setProperty("--term-shift", `${Math.round(shift)}px`);
    const termRect = buttonRef.current?.getBoundingClientRect();
    if (termRect && rect.bottom > view.bottom - EDGE_PX && termRect.top - rect.height > view.top + EDGE_PX * 8) {
      pop.setAttribute("data-place", "above");
    }
  }, [open]);

  const onPointerEnter = (event) => {
    if (event.pointerType !== "mouse" || !canHover()) return;
    clearHoverTimer();
    hoverTimer.current = setTimeout(() => setMode((current) => current ?? "hover"), HOVER_DELAY_MS);
  };
  const onPointerLeave = (event) => {
    if (event.pointerType !== "mouse") return;
    clearHoverTimer();
    setMode((current) => (current === "hover" ? null : current));
  };
  const onBlur = (event) => {
    if (!wrapRef.current?.contains(event.relatedTarget)) setMode((current) => (current === "pinned" ? null : current));
  };
  const onClick = () => {
    clearHoverTimer();
    setMode((current) => (current === "pinned" ? null : "pinned"));
  };

  return (
    <span ref={wrapRef} className="term-wrap" onPointerEnter={onPointerEnter} onPointerLeave={onPointerLeave} onBlur={onBlur}>
      <button
        ref={buttonRef}
        type="button"
        className="term"
        aria-expanded={open}
        aria-controls={open ? popId : undefined}
        onClick={onClick}
      >
        {children}
      </button>
      {open && (
        <span ref={popRef} id={popId} className="term-pop" role="note">
          <strong className="term-pop-name">{entry.term}</strong>{" "}
          {entry.definition}
          {entry.ref && (
            <>
              {" "}
              <a className="term-pop-ref" href={`#${entry.ref}`} onClick={close}>Reference →</a>
            </>
          )}
        </span>
      )}
    </span>
  );
}

/**
 * Text with the first use of each glossary term not yet in `seen` wrapped in a <Term>; `seen` is
 * shared across one panel's caption and notes, so each term is explained once per panel. Anything
 * other than a string passes through unchanged, as does a string with no new term.
 *
 * @param {import("react").ReactNode} text
 * @param {Set<string>} [seen]
 * @returns {import("react").ReactNode}
 */
export function withTerms(text, seen = new Set()) {
  if (typeof text !== "string") return text;
  const parts = splitTerms(text, seen);
  if (parts.length === 1) return parts[0];
  return parts.map((part, index) => (typeof part === "string"
    ? part
    : <Term key={index} entry={part.entry}>{part.text}</Term>));
}
