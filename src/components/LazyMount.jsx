import { Suspense, startTransition, useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { preloadChunks } from "../app/lazyChunk";

const DEFAULT_HEIGHT = 240;
// Background mounting starts this long after the load event, once the first screen (the verdict,
// the forecast chart, research.json arriving) has finished: each chart mount is a task of up to
// about 130 ms on a mid-range phone, which would otherwise block input while the page loads.
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
// Figures below the fold still mount without a scroll, so find-in-page and print see them later:
// once the page has settled, one at a time, each in an idle period after the previous one has
// mounted. A background mount renders as a transition, so React yields to input while it renders.
// The window events "lazy:mount-all" (src/lib/print.js) and "beforeprint" mount every remaining
// figure at once.
const queue = [];             // {node, mount(urgent)} of figures not mounted yet, in document order
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

// Starts well after the first paint: the load event, SETTLE_MS, then an idle period. The chart
// chunks are fetched at the load event already (in an idle period, rendering nothing), so a
// figure mounted any time after that, a "beforeprint" mount included, renders at once.
const startDraining = () => {
  if (draining || !queue.length) return;
  draining = true;
  const start = () => {
    whenIdle(preloadChunks);
    window.setTimeout(() => whenIdle(drainNext), SETTLE_MS);
  };
  if (document.readyState === "complete") start();
  else window.addEventListener("load", start, { once: true });
};

const mountAll = (event) => {
  mountEverything = true;
  const entries = queue.splice(0);
  if (!entries.length) return;
  // Before printing there is no later frame: render synchronously so the print sees the figures
  // whose chunks are already loaded.
  if (event?.type === "beforeprint") flushSync(() => entries.forEach((entry) => entry.mount(true)));
  else entries.forEach((entry) => entry.mount(true));
};

/**
 * Queues a figure's mount in document order (a figure that renders later, e.g. once research.json
 * arrives, still takes its place top to bottom); returns a function that takes it off the queue.
 *
 * @param {{node: Element, mount: (urgent: boolean) => void}} entry
 */
const enqueue = (entry) => {
  if (!listening) {
    listening = true;
    window.addEventListener("lazy:mount-all", mountAll);
    window.addEventListener("beforeprint", mountAll);
  }
  if (mountEverything) {
    entry.mount(true);
    return () => {};
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
 */
export default function LazyMount({ minHeight = DEFAULT_HEIGHT, label = "chart", rootMargin = "600px 0px", children }) {
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
    const dequeue = enqueue(entry);
    return () => {
      observer.disconnect();
      dequeue();
    };
  }, [rootMargin, visible]);

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
