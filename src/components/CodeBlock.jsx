import { useEffect, useRef, useState } from "react";

/**
 * A block of commands to copy: its lines never wrap, so on a narrow screen it scrolls sideways. Like
 * DataTable, it becomes a labelled keyboard tab stop (a region) only once it is known to scroll, so the
 * server-rendered page has no tab stop that leads nowhere.
 *
 * @param {object} props
 * @param {string} props.label             the region's accessible name, e.g. "Reproduce commands"
 * @param {import("react").ReactNode} props.children
 */
export default function CodeBlock({ label, children }) {
  const ref = useRef(null);
  const [wide, setWide] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const update = () => setWide(node.scrollWidth - node.clientWidth > 1);
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(node);
    return () => observer?.disconnect();
  }, []);

  const region = wide ? { tabIndex: 0, role: "region", "aria-label": label } : {};
  return <pre ref={ref} className="code-block" {...region}><code>{children}</code></pre>;
}
