import { Suspense, startTransition, useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { preloadChunks } from "../app/lazyChunk";

const DEFAULT_HEIGHT = 240;
// Background mounting starts this long after the load event, once the first screen (the verdict,
// research.json arriving) has finished. Measured at 390px with the CPU throttled 4x (a phone-class
// CPU), an SVG or text figure mounts in up to about 200 ms of React work, which a transition can
// split; a canvas chart (lightweight-charts) takes a frame of about 240-400 ms, of which 115-240 ms
// is the library's own requestAnimationFrame paint, which no transition can split. So only SVG and
// text figures mount in the background; canvas charts wait for the reader (`background` false).
const SETTLE_MS = 3000;
// Browsers without requestIdleCallback (Safari) wait this long between background mounts instead.
const IDLE_FALLBACK_MS = 1500;
// A figure whose chunk never arrives (offline, a failed deploy) holds up the queue at most this long.
const STALL_MS = 5000;

/**
 * The height a placeholder reserves, at or above 640px (wide) and below (narrow). A number reserves
 * the same height at every width; a missing or invalid value falls back to DEFAULT_HEIGHT.
 *
 * @param {number|{wide?: number, narrow?: number}} [minHeight]
 * @returns {{wide: number, narrow: number}}
 */
export const reservedHeights = (minHeight) => {
  const valid = (value) => (Number.isFinite(value) && value >= 0 ? Math.round(value) : null);
  if (typeof minHeight === "number") {
    const height = valid(minHeight) ?? DEFAULT_HEIGHT;
    return { wide: height, narrow: height };
  }
  const wide = valid(minHeight?.wide) ?? valid(minHeight?.narrow) ?? DEFAULT_HEIGHT;
  return { wide, narrow: valid(minHeight?.narrow) ?? wide };
};

// ------------------------------------------------------------------------------- mount queue
// SVG and text figures below the fold still mount without a scroll, so find-in-page and print see
// them later: once the page has settled, one at a time, each in an idle period after the previous
// one has mounted. A background mount renders as a transition, so React yields to input while it
// renders. Canvas charts stay out of the queue: their first paint is one long task, and a canvas
// holds no text for find-in-page. They mount near the viewport. The window events
// "lazy:mount-all" (src/lib/print.js) and "beforeprint" mount every remaining figure at once.
const queue = [];             // {node, mount(urgent)} of figures not mounted yet, in document order
const nearOnly = new Set();   // the same for figures that mount only near the viewport or to print
let current = null;           // the entry mounting in the background; the next one waits for it
let mountEverything = false;  // set once everything was asked for: later figures mount at once
let draining = false;
let listening = false;

const whenIdle = (callback) => {
  if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(callback);
  else window.setTimeout(callback, IDLE_FALLBACK_MS);
};

const drainNext = () => {
  const entry = queue.shift() ?? null;
  current = entry;
  if (!entry) {
    draining = false;
    return;
  }
  entry.mount(false);
  window.setTimeout(() => settled(entry), STALL_MS);
};

/** The background figure has mounted (or is gone): the next one follows in an idle period. */
function settled(entry) {
  if (!entry || entry !== current) return;
  current = null;
  if (queue.length) whenIdle(drainNext);
  else draining = false;
}

const afterLoad = (callback) => {
  if (document.readyState === "complete") callback();
  else window.addEventListener("load", callback, { once: true });
};

// The chart chunks are fetched at the load event (in an idle period, rendering nothing), so a
// figure mounted any time after that, a "beforeprint" mount included, renders at once.
const preloadAfterLoad = () => afterLoad(() => whenIdle(preloadChunks));

// Starts well after the first paint: the load event, SETTLE_MS, then an idle period.
const startDraining = () => {
  if (draining || !queue.length) return;
  draining = true;
  preloadAfterLoad();
  afterLoad(() => window.setTimeout(() => whenIdle(drainNext), SETTLE_MS));
};

const mountAll = (event) => {
  mountEverything = true;
  const entries = [...queue.splice(0), ...nearOnly];
  nearOnly.clear();
  if (!entries.length) return;
  // Before printing there is no later frame: render synchronously so the print sees the figures
  // whose chunks are already loaded.
  if (event?.type === "beforeprint") flushSync(() => entries.forEach((entry) => entry.mount(true)));
  else entries.forEach((entry) => entry.mount(true));
};

/**
 * Queues a figure's mount in document order (a figure that renders later, e.g. once research.json
 * arrives, still takes its place top to bottom); returns a function that takes it off the queue.
 * With `background` false the figure is only held for "lazy:mount-all" and "beforeprint".
 *
 * @param {{node: Element, mount: (urgent: boolean) => void}} entry
 * @param {boolean} [background]
 */
const enqueue = (entry, background = true) => {
  if (!listening) {
    listening = true;
    window.addEventListener("lazy:mount-all", mountAll);
    window.addEventListener("beforeprint", mountAll);
  }
  if (mountEverything) {
    entry.mount(true);
    return () => {};
  }
  if (!background) {
    nearOnly.add(entry);
    preloadAfterLoad();
    return () => { nearOnly.delete(entry); };
  }
  const before = queue.findIndex((other) => entry.node.compareDocumentPosition(other.node) & Node.DOCUMENT_POSITION_FOLLOWING);
  queue.splice(before < 0 ? queue.length : before, 0, entry);
  startDraining();
  return () => {
    const index = queue.indexOf(entry);
    if (index >= 0) queue.splice(index, 1);
    // Unmounted while the queue waited for it (on a re-render the wrapper stays in the document).
    if (!entry.node.isConnected) settled(entry);
  };
};

/** Reports, from inside the Suspense boundary, that the lazy children have committed. */
function MountSignal({ onMount }) {
  useEffect(() => { onMount(); }, [onMount]);
  return null;
}

/**
 * Renders its (lazy) children once the slot is near the viewport, or later in the background, so
 * chart chunks below the fold do not compete with the first screen. Server rendering and the first
 * client render show the placeholder, which reserves the figure's height so nothing shifts when
 * the chart arrives. The wrapper is aria-busy until the figure has mounted; the placeholder is
 * not a live region, so a screen reader is not interrupted while the page fills in.
 *
 * @param {object} props
 * @param {number|{wide: number, narrow: number}} [props.minHeight]  reserved height in px; with an
 *   object, `narrow` applies at 640px and below
 * @param {string} [props.label]       what is loading ("chart")
 * @param {string} [props.rootMargin]  IntersectionObserver margin
 * @param {boolean} [props.background] also mount in the background once the page has settled
 *   (default); false for a canvas chart, which then mounts only near the viewport or to print
 */
export default function LazyMount({
  minHeight = DEFAULT_HEIGHT, label = "chart", rootMargin = "600px 0px", background = true, children,
}) {
  const ref = useRef(null);
  const entryRef = useRef(null);
  const [visible, setVisible] = useState(false);
  const [mounted, setMounted] = useState(false);
  const markMounted = useCallback(() => {
    setMounted(true);
    settled(entryRef.current);
  }, []);

  useEffect(() => {
    if (visible) return undefined;
    const node = ref.current;
    if (!node) return undefined;
    // Near the viewport a figure mounts at once; from the background queue, as a transition.
    const mount = (urgent = true) => (urgent ? setVisible(true) : startTransition(() => setVisible(true)));
    if (typeof IntersectionObserver === "undefined") {
      mount();
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) mount();
    }, { rootMargin });
    observer.observe(node);
    const entry = { node, mount };
    entryRef.current = entry;
    const dequeue = enqueue(entry, background);
    return () => {
      observer.disconnect();
      dequeue();
    };
  }, [rootMargin, visible, background]);

  const { wide, narrow } = reservedHeights(minHeight);
  // Only the placeholder reserves height (CSS reads these variables): a loaded chart sizes itself.
  const placeholder = <div className="figure-placeholder">Loading {label}…</div>;
  return (
    <div
      ref={ref}
      className="lazy-mount"
      aria-busy={mounted ? undefined : "true"}
      style={{ "--lazy-h": `${wide}px`, "--lazy-h-narrow": `${narrow}px` }}
    >
      {visible ? (
        <Suspense fallback={placeholder}>
          <MountSignal onMount={markMounted} />
          {children}
        </Suspense>
      ) : placeholder}
    </div>
  );
}
