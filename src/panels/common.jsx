// Small pieces shared by the panels.

/**
 * Shown in place of a figure that needs research.json when it is still loading or unavailable.
 * @param {{status: "loading"|"ready"|"unavailable", what?: string}} props
 */
export function ResearchUnavailable({ status, what = "this figure" }) {
  if (status === "loading") {
    return <div className="figure-empty" role="status"><p className="empty-text">Loading research.json…</p></div>;
  }
  return (
    <div className="figure-empty">
      <p className="empty-title">research.json is not available</p>
      <p className="empty-text">
        It holds the reproducible research behind {what}. The live forecast, its validation and the
        live record above come from data.json and are unaffected.
      </p>
    </div>
  );
}

/**
 * An inline legend for an SVG figure (the canvas charts draw their own).
 * @param {{items: {tone: string, label: string, shape?: "dot"|"line"|"dash"|"band"|"ring"|"tick"}[]}} props
 */
export function Legend({ items }) {
  return (
    <ul className="legend" aria-hidden="true">
      {items.map((item) => (
        <li key={item.label} className="legend-item">
          <i className={`swatch swatch-${item.tone}${item.shape ? ` is-${item.shape}` : ""}`} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

/** A compact definition list of label/value facts. */
export function Facts({ items, className = "" }) {
  const shown = items.filter((item) => item && item.value != null);
  if (!shown.length) return null;
  return (
    <dl className={`facts ${className}`}>
      {shown.map((item) => (
        <div key={item.label} className="fact">
          <dt>{item.label}</dt>
          <dd className="num">{item.value}{item.note && <span className="fact-note">{item.note}</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Tones used for the four models compared in stability and calibration figures. */
export const ROLE_TONE = { record: "model", challenger: "alt" };
