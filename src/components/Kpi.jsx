import { Fragment } from "react";
import { NBSP } from "../lib/format";
import KpiGlyph from "./KpiGlyph";
import keepHyphenated from "./keepHyphenated";

const SEPARATOR = " · ";

/** Tile text parts as segments, split wherever a string part holds " · " (a {num} never does). */
export const tileSegments = (content) => {
  const segments = [[]];
  for (const part of Array.isArray(content) ? content : [content]) {
    if (typeof part !== "string") {
      segments[segments.length - 1].push(part);
      continue;
    }
    part.split(SEPARATOR).forEach((piece, index) => {
      if (index > 0) segments.push([]);
      if (piece) segments[segments.length - 1].push(piece);
    });
  }
  return segments.filter((segment) => segment.length);
};

const parts = (segment) => segment.map((part, index) => (typeof part === "string"
  ? <Fragment key={index}>{keepHyphenated(part)}</Fragment>
  : <b key={index} className="num">{part?.num}</b>));

/**
 * Tile text: a string, or a list of strings and {num} figures (set in bold tabular digits). Strings
 * keep hyphenated names whole ("HAR-IV", "OVX-only"), so a name never breaks at its hyphen. Parts
 * joined by " · " render as a .sep-list, so no wrapped line starts or ends with the dot; the dot is
 * text (hidden from screen readers), so a copied footnote keeps it.
 */
const richText = (content) => {
  const segments = tileSegments(content);
  if (segments.length <= 1) return parts(segments[0] ?? []);
  return (
    <span className="sep-list has-dots">
      {segments.map((segment, index) => (
        <span key={index}><span className="sep" aria-hidden="true">{index ? " · " : ""}</span>{parts(segment)}</span>
      ))}
    </span>
  );
};

/**
 * One verdict tile. Rendered as a group inside the parent <dl>, always with the same five children
 * so a row of tiles lines up on a shared grid: the label is the <dt>; the value, its glyph, its
 * named baseline and the sample/test footnote are <dd>s, so a screen reader hears them together.
 * The glyph is decorative (aria-hidden) and its <dd> stays, empty, when there is none. With `href`
 * the label is a link whose hit area covers the whole tile.
 *
 * @param {object} props
 * @param {string} props.label                       what is measured
 * @param {import("react").ReactNode} props.value    the headline number (already formatted)
 * @param {string} [props.unit]                      small unit after the value
 * @param {object|null} [props.glyph]                KpiGlyph props (src/lib/tiles.js)
 * @param {string|Array<string|{num:string}>|null} [props.baseline] the named comparison ("vs 0.952 persistence")
 * @param {string|Array<string|{num:string}>|null} [props.foot]     sample size and test
 * @param {string} [props.href]                      anchor of the panel with the evidence
 */
export default function Kpi({ label, value, unit, glyph, baseline, foot, href }) {
  return (
    <div className="kpi">
      <dt className="kpi-label">
        {href
          ? (
            <a href={href} className="kpi-link">
              {keepHyphenated(label)}
              <span className="kpi-arrow" aria-hidden="true">{NBSP}→</span>
            </a>
          )
          : keepHyphenated(label)}
      </dt>
      <dd className="kpi-value num">
        {value}
        {unit && <span className="kpi-unit">{unit}</span>}
      </dd>
      <dd className="kpi-glyph" aria-hidden="true">{glyph ? <KpiGlyph {...glyph} /> : null}</dd>
      <dd className="kpi-base">{baseline != null ? richText(baseline) : null}</dd>
      <dd className="kpi-foot">{foot != null ? richText(foot) : null}</dd>
    </div>
  );
}
