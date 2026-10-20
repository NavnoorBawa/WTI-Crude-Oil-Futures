import { Suspense, useEffect, useRef, useState } from "react";

/**
 * Renders its (lazy) children only once the slot is near the viewport, so chart chunks below the
 * fold are fetched as the reader scrolls. Server rendering and the first client render show the
 * placeholder, which reserves the figure's height so nothing shifts when the chart arrives.
 *
 * @param {object} props
 * @param {number} [props.minHeight]   reserved height in px
 * @param {string} [props.label]       what is loading ("chart")
 * @param {string} [props.rootMargin]  IntersectionObserver margin
 */
export default function LazyMount({ minHeight = 240, label = "chart", rootMargin = "600px 0px", children }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin });
    observer.observe(node);
    return () => observer.disconnect();
  }, [rootMargin]);

  const placeholder = (
    <div className="figure-placeholder" style={{ minHeight }} role="status">
      Loading {label}…
    </div>
  );
  // Only the placeholder reserves height: a loaded chart sizes itself (smaller on a phone).
  return (
    <div ref={ref} className="lazy-mount">
      {visible ? <Suspense fallback={placeholder}>{children}</Suspense> : placeholder}
    </div>
  );
}
