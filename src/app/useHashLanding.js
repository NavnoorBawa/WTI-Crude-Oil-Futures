import { useEffect, useRef } from "react";

const decode = (text) => {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
};

/** The element the URL's #fragment names, or null (also for an empty or unknown fragment). */
const hashTarget = () => {
  const id = decode(window.location.hash.slice(1));
  return id ? document.getElementById(id) : null;
};

/** The element's own heading (itself or its first heading), when it can take focus (tabindex). */
const headingOf = (element) => {
  const heading = element.matches("h1, h2, h3, h4") ? element : element.querySelector("h1, h2, h3, h4");
  return heading?.hasAttribute("tabindex") ? heading : null;
};

/**
 * Scrolls the element to the top of the view at once (no smooth scroll), then focuses its heading.
 * The reader did not ask for that focus with a key, so the heading is marked (data-landed) and
 * draws no focus ring until it loses focus; a screen reader still announces it.
 */
const land = (element) => {
  const root = document.documentElement;
  const behavior = root.style.scrollBehavior;
  root.style.scrollBehavior = "auto";
  element.scrollIntoView({ block: "start" });
  root.style.scrollBehavior = behavior;
  const heading = headingOf(element);
  if (!heading) return;
  heading.setAttribute("data-landed", "");
  heading.addEventListener("blur", () => heading.removeAttribute("data-landed"), { once: true });
  heading.focus({ preventScroll: true });
};

const INTENT_EVENTS = ["wheel", "touchmove", "keydown", "mousedown"];

/**
 * Deep links that work on a cold load. The sections render only after data.json arrives, so the
 * browser's own jump to the #fragment finds nothing. Once the dashboard has rendered (`ready`),
 * this scrolls the named element to the top of the view (the html scroll-padding clears the top
 * bar) and focuses its heading, so a screen reader announces where the reader landed. When
 * research.json arrives later and lengthens the sections above (or renders the row the link names,
 * which was not there at the first landing), it lands once more, unless the reader has started
 * scrolling or typing. On every later hash change (the code bar, in-page links)
 * the target's heading takes focus; the browser does the scrolling. Unknown fragments are ignored.
 *
 * @param {boolean} ready                     the dashboard (with its sections) is rendered
 * @param {"loading"|"ready"|"unavailable"} [researchStatus]
 */
export default function useHashLanding(ready, researchStatus) {
  // `fragment` is the #hash at the first landing, kept even when it named nothing yet: a model or
  // ledger row (#modl-har_iv) renders only once research.json is in, so it can land only then.
  const state = useRef({ landed: false, fragment: null, repeated: false, moved: false });

  useEffect(() => {
    if (!ready || state.current.landed) return undefined;
    const frame = requestAnimationFrame(() => {
      state.current.landed = true;
      state.current.fragment = window.location.hash;
      const target = hashTarget();
      if (target) land(target);
    });
    return () => cancelAnimationFrame(frame);
  }, [ready]);

  // The reader takes over: no second landing after any scroll, key or click.
  useEffect(() => {
    const onIntent = () => {
      state.current.moved = true;
      INTENT_EVENTS.forEach((name) => window.removeEventListener(name, onIntent, true));
    };
    INTENT_EVENTS.forEach((name) => window.addEventListener(name, onIntent, { capture: true, passive: true }));
    return () => INTENT_EVENTS.forEach((name) => window.removeEventListener(name, onIntent, true));
  }, []);

  useEffect(() => {
    const current = state.current;
    if (researchStatus !== "ready" || !current.landed || !current.fragment || current.repeated || current.moved) return undefined;
    const frame = requestAnimationFrame(() => {
      current.repeated = true;
      // Only the link the page was opened with: a hash the reader has since moved to is theirs.
      const target = window.location.hash === current.fragment ? hashTarget() : null;
      if (target && !current.moved) land(target);
    });
    return () => cancelAnimationFrame(frame);
  }, [researchStatus]);

  useEffect(() => {
    const onHashChange = () => {
      const target = hashTarget();
      if (target) headingOf(target)?.focus({ preventScroll: true });
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
}
