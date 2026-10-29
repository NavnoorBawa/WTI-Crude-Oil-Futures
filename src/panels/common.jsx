// Small pieces shared by the panels.
import keepHyphenated from "../components/keepHyphenated";
import { asText } from "../lib/research";
import { fmtMonthYear, fmtSpan } from "../lib/format";

/**
 * Shown in place of a figure that needs research.json when it is still loading, unavailable, or
 * loaded without the block this figure reads ("ready").
 * @param {{status: "loading"|"ready"|"unavailable", what?: string}} props
 */
export function ResearchUnavailable({ status, what = "this figure" }) {
  if (status === "loading") {
    return <div className="figure-empty" role="status"><p className="empty-text">Loading research.json…</p></div>;
  }
  if (status === "ready") {
    return (
      <div className="figure-empty">
        <p className="empty-title">Not in this research.json</p>
        <p className="empty-text">research.json loaded but carries no data for {what}; the other sections are unaffected.</p>
      </div>
    );
  }
  // Unavailable: one line; the page-level notice under the verdict says what is affected and retries.
  return (
    <div className="figure-empty is-line">
      <p className="empty-text">research.json did not load; see the notice under the verdict.</p>
    </div>
  );
}

/**
 * An inline legend for an SVG figure (the canvas charts draw their own).
 * @param {{items: {tone: string, label: string, shape?: "dot"|"line"|"dash"|"ring"|"tick"|"diamond"|"bar"|"bar-open"|"mark-tick"|"mark-tick-open"}[]}} props
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

/** Item counts that get a column count dividing them (facts--3, --4, --6), so no row ends with an orphan. */
const COUNTED_FACTS = new Set([3, 4, 6]);

/** A compact definition list of label/value facts. */
export function Facts({ items, className = "" }) {
  const shown = items.filter((item) => item && item.value != null);
  if (!shown.length) return null;
  const count = COUNTED_FACTS.has(shown.length) ? ` facts--${shown.length}` : "";
  return (
    <dl className={`facts${count} ${className}`}>
      {shown.map((item) => (
        <div key={item.label} className="fact">
          <dt>{item.label}</dt>
          <dd className="num">{item.value}{item.note && <span className="fact-note">{keepHyphenated(item.note)}</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * A sub-study's heading: its one-claim title (h3) and, under it, the statistics behind the claim
 * as a meta line, as a panel's title and meta are laid out.
 * @param {{title: string, detail?: string[]}} props
 */
export function StudyHead({ title, detail = [] }) {
  const items = detail.filter(Boolean);
  return (
    <div className="study-head">
      <h3 className="study-title">{title}</h3>
      {items.length > 0 && (
        <p className="study-meta sep-list">{items.map((item, index) => <span key={index}>{item}</span>)}</p>
      )}
    </div>
  );
}

/**
 * The economic tests' sample (data.json vol_forecast.economic.returns) and why it ends early: they
 * run on same-contract returns rebuilt from the committed EIA futures curve, so they stop where
 * that curve stops, while the forecast panels run to the latest session. Null without a span.
 */
export const economicSpan = (economic) => {
  const returns = economic?.returns;
  const start = asText(returns?.sample_start);
  const end = asText(returns?.sample_end);
  if (!start || !end) return null;
  return {
    text: fmtSpan(start, end),
    short: "same-contract returns end with the committed EIA curve",
    reason: `The sample ends in ${fmtMonthYear(end)} because these tests use same-contract returns, rebuilt from the committed EIA futures curve, which ends there; the forecast panels run to the latest session.`,
  };
};

/** Says why the economic tests are missing from this snapshot (economic.unavailable_reason). */
export function EconomicUnavailable({ economic, what }) {
  const reason = asText(economic?.unavailable_reason);
  if (!reason) return null;
  return (
    <div className="figure-empty is-compact">
      <p className="empty-title">{what} is not in this snapshot</p>
      <p className="empty-text">The economic tests did not run: {reason}.</p>
    </div>
  );
}
