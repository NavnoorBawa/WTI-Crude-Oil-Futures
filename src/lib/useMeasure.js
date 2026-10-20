import { useEffect, useRef, useState } from "react";

/**
 * Tracks an element's content width (ResizeObserver), so SVG charts draw at real pixel size and
 * their 11px labels stay 11px on a phone instead of being scaled down with a viewBox.
 *
 * @returns {[import("react").RefObject<HTMLElement>, number]} ref and width in px (0 until measured)
 */
export default function useMeasure() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    setWidth(Math.floor(node.getBoundingClientRect().width));
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      const next = Math.floor(entries[0]?.contentRect?.width ?? 0);
      setWidth((previous) => (Math.abs(previous - next) >= 1 ? next : previous));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}
