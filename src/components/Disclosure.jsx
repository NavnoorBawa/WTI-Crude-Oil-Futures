import { useEffect, useRef } from "react";

/** The element the URL's #fragment names, or null. */
const hashTarget = () => {
  const id = decodeURIComponentSafe(window.location.hash.slice(1));
  return id ? document.getElementById(id) : null;
};
const decodeURIComponentSafe = (text) => {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
};

/**
 * One level of progressive disclosure on a native <details>: closed by default at every width,
 * its content always in the DOM (server rendering and find-in-page still reach it). It opens for
 * printing (and returns to its previous state afterwards), on the window event "notes:open-all",
 * and when the URL's #fragment names an element inside it, on load and on every hash change.
 * Never put a caveat that qualifies a headline in here: those stay visible.
 *
 * @param {object} props
 * @param {import("react").ReactNode} props.summary   the always-visible label, e.g. "Method notes"
 * @param {string} [props.className]
 * @param {import("react").ReactNode} props.children
 */
export default function Disclosure({ summary, className = "", children }) {
  const ref = useRef(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    let beforePrint = null;
    const open = () => { node.open = true; };
    const onBeforePrint = () => {
      if (beforePrint == null) beforePrint = node.open;
      node.open = true;
    };
    const onAfterPrint = () => {
      if (beforePrint != null) node.open = beforePrint;
      beforePrint = null;
    };
    const onHash = () => {
      const target = hashTarget();
      if (target && node.contains(target)) node.open = true;
    };
    onHash();
    window.addEventListener("beforeprint", onBeforePrint);
    window.addEventListener("afterprint", onAfterPrint);
    window.addEventListener("notes:open-all", open);
    window.addEventListener("hashchange", onHash);
    return () => {
      window.removeEventListener("beforeprint", onBeforePrint);
      window.removeEventListener("afterprint", onAfterPrint);
      window.removeEventListener("notes:open-all", open);
      window.removeEventListener("hashchange", onHash);
    };
  }, []);

  return (
    <details ref={ref} className={`disclosure${className ? ` ${className}` : ""}`}>
      <summary className="disclosure-summary">{summary}</summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}
