/**
 * One verdict tile. Rendered as a group inside the parent <dl>: the label is the <dt>; the value,
 * its named baseline and the sample/test footnote are <dd>s, so a screen reader hears them together.
 *
 * @param {object} props
 * @param {string} props.label                       what is measured
 * @param {import("react").ReactNode} props.value    the headline number (already formatted)
 * @param {string} [props.unit]                      small unit after the value
 * @param {import("react").ReactNode} [props.baseline] the named comparison ("vs 0.952 persistence")
 * @param {import("react").ReactNode} [props.foot]     sample size and test
 * @param {string} [props.href]                      anchor of the panel with the evidence
 */
export default function Kpi({ label, value, unit, baseline, foot, href }) {
  return (
    <div className="kpi">
      <dt className="kpi-label">
        {href ? <a href={href} className="kpi-link">{label}</a> : label}
      </dt>
      <dd className="kpi-value num">
        {value}
        {unit && <span className="kpi-unit">{unit}</span>}
      </dd>
      {baseline != null && <dd className="kpi-base">{baseline}</dd>}
      {foot != null && <dd className="kpi-foot">{foot}</dd>}
    </div>
  );
}
