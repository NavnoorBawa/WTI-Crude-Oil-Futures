import { useState } from "react";
import Chip from "./Chip";
import ErrorBoundary from "./ErrorBoundary";
import keepHyphenated from "./keepHyphenated";

export const REPRODUCE_COMMAND = "python -m backend.vol_lab";

/**
 * One research section. Anatomy, top to bottom: a code chip and plain section name; the assertion
 * title (h2, computed from the payload); meta (sample size, span, test); the figure; a one-line
 * caption from the data; and a source footer naming the artifact and how to reproduce it. A
 * "Table" toggle swaps the figure for an accessible table of the same numbers.
 *
 * @param {object} props
 * @param {string} props.id                     anchor id (the lowercase code)
 * @param {string} props.code                   section code chip, e.g. "FCST"
 * @param {string} props.name                   plain section name, e.g. "Forecast vs realized"
 * @param {string|null} props.title             assertion title; null falls back to `fallbackTitle`
 * @param {string} [props.fallbackTitle]        neutral title when the data cannot support an assertion
 * @param {Array<import("react").ReactNode>} [props.meta]   short facts; falsy entries are skipped
 * @param {import("react").ReactNode} [props.actions]       extra controls (range presets, toggles)
 * @param {import("react").ReactNode} [props.caption]
 * @param {{artifact: string|string[], reproduce?: string|null}} [props.source]
 * @param {() => import("react").ReactNode} [props.table]  renders the table view (only when shown)
 * @param {"full"|"half"|"wide"|"narrow"} [props.span]    width in the 12-column grid
 * @param {unknown} [props.resetKey]            retries a failed figure when it changes (fresh data)
 */
export default function Panel({
  id,
  code,
  name,
  title,
  fallbackTitle,
  meta = [],
  actions = null,
  caption = null,
  source = null,
  table = null,
  span = "full",
  resetKey,
  children,
}) {
  const [showTable, setShowTable] = useState(false);
  const titleId = `${id}-title`;
  const bodyId = `${id}-body`;
  const metaItems = meta.filter(Boolean);
  const artifacts = source ? [].concat(source.artifact).filter(Boolean) : [];
  const reproduce = source?.reproduce === undefined ? REPRODUCE_COMMAND : source.reproduce;

  return (
    <section id={id} className={`panel span-${span}`} aria-labelledby={titleId}>
      <header className="panel-head">
        <div className="panel-eyebrow">
          <Chip tone="code">{code}</Chip>
          <span className="micro">{name}</span>
        </div>
        {(actions || table) && (
          <div className="panel-actions">
            {actions}
            {table && (
              <button
                type="button"
                className="toggle-button"
                aria-pressed={showTable}
                aria-controls={bodyId}
                onClick={() => setShowTable((shown) => !shown)}
              >
                Table
              </button>
            )}
          </div>
        )}
        <h2 id={titleId} className="panel-title">{keepHyphenated(title || fallbackTitle || name)}</h2>
        {metaItems.length > 0 && (
          <p className="panel-meta sep-list">
            {metaItems.map((item, index) => <span key={index}>{item}</span>)}
          </p>
        )}
      </header>

      <div id={bodyId} className="panel-body">
        <div className="panel-figure" hidden={showTable}>
          <ErrorBoundary label={`The ${name.toLowerCase()} figure`} resetKey={resetKey}>
            {children}
          </ErrorBoundary>
        </div>
        {showTable && table && <div className="panel-table">{table()}</div>}
      </div>

      {caption && <p className="panel-caption">{caption}</p>}

      {(artifacts.length > 0 || reproduce) && (
        <footer className="panel-source">
          {artifacts.length > 0 && (
            <span>
              <span className="micro">Source</span>
              {artifacts.map((artifact) => <code key={artifact}>{artifact}</code>)}
            </span>
          )}
          {reproduce && (
            <span>
              <span className="micro">Reproduce</span>
              <code>{reproduce}</code>
            </span>
          )}
        </footer>
      )}
    </section>
  );
}
