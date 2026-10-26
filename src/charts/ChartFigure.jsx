import { useId } from "react";

/**
 * The frame every lightweight-charts figure shares: a focusable group named by a short label and
 * described by the full text summary; a legend row (hidden from assistive tech, which reads the
 * summary and the live region instead) with the tap hint and the "Reset view" button; the plot
 * host (aria-hidden: the library's own layout table means nothing read aloud); the print image
 * that replaces the canvas on paper; and a polite live region for keyboard reading.
 *
 * @param {object} props
 * @param {ReturnType<import("./lwUtil").useChartReader>} props.reader
 * @param {string} props.label         what, how many, which span (at most 120 characters)
 * @param {string} props.description   the long text summary of what the chart draws
 * @param {import("react").ReactNode} props.legend
 * @param {string} props.hint          e.g. "Tap the chart to read a week"
 * @param {import("react").RefObject<HTMLDivElement>} props.hostRef
 * @param {string} props.hostClassName e.g. "lw-host--forecast"
 */
export default function ChartFigure({ reader, label, description, legend, hint, hostRef, hostClassName }) {
  const descriptionId = useId();
  return (
    <div className="lw-figure" {...reader.figureProps} aria-label={label} aria-describedby={descriptionId}>
      <div className="chart-legend">
        <div className="legend-readout" aria-hidden="true">{legend}</div>
        {(reader.hint || reader.offFrame) && (
          <div className="legend-tools">
            {reader.hint && <span className="chart-hint" aria-hidden="true">{hint}</span>}
            {reader.offFrame && <button type="button" className="reset-view" onClick={reader.reframe}>Reset view</button>}
          </div>
        )}
      </div>
      <div ref={hostRef} className={`lw-host ${hostClassName}`} aria-hidden="true" />
      {reader.printSrc && <img className="print-shot" alt="" src={reader.printSrc} />}
      <p id={descriptionId} className="sr-only">{description}</p>
      <p className="sr-only" aria-live="polite">{reader.announcement}</p>
    </div>
  );
}
