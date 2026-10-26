import { useEffect, useRef, useState } from "react";
import { copyText } from "../lib/clipboard";

const DONE_MS = 2000;

/** 12px glyphs, drawn with the current text colour. */
const GLYPHS = {
  link: (
    <path d="M5 7a2.5 2.5 0 0 0 3.5 0l2-2A2.5 2.5 0 0 0 7 1.5L6 2.5M7 5a2.5 2.5 0 0 0-3.5 0l-2 2A2.5 2.5 0 0 0 5 10.5l1-1" />
  ),
  copy: (
    <>
      <rect x="4" y="4" width="6.5" height="6.5" rx="1" />
      <path d="M8 4V2.5a1 1 0 0 0-1-1H2.5a1 1 0 0 0-1 1V7a1 1 0 0 0 1 1H4" />
    </>
  ),
};

/**
 * A small icon button that copies text and confirms it: the button shows "Copied" for 2 s and a
 * visually hidden live region (an <output>, i.e. role status) says so to screen readers. The text
 * is read at click time, so a link can include the current URL.
 *
 * @param {object} props
 * @param {string|(() => string)} props.text       what to copy
 * @param {string} props.label                     accessible name, e.g. "Copy command: python -m backend.vol_lab"
 * @param {"link"|"copy"} [props.glyph]
 * @param {string} [props.done]                    what the live region says on success
 * @param {string} [props.className]
 */
export default function CopyButton({ text, label, glyph = "copy", done = "Copied to the clipboard", className = "" }) {
  const [state, setState] = useState(null); // null | "done" | "failed"
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const onClick = async () => {
    const ok = await copyText(typeof text === "function" ? text() : text);
    clearTimeout(timer.current);
    setState(ok ? "done" : "failed");
    timer.current = setTimeout(() => setState(null), DONE_MS);
  };

  return (
    <>
      <button
        type="button"
        className={`copy-button${className ? ` ${className}` : ""}${state === "done" ? " is-done" : ""}`}
        aria-label={label}
        onClick={onClick}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false" className="copy-glyph">
          {GLYPHS[glyph] ?? GLYPHS.copy}
        </svg>
        {state === "done" ? "Copied" : null}
      </button>
      <output className="sr-only" role="status">{state === "done" ? done : state === "failed" ? "Could not copy; select the text instead" : ""}</output>
    </>
  );
}
