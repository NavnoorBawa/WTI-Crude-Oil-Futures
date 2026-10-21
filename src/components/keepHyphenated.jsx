/**
 * Renders text so a hyphenated token ("GJR-GARCH(1,1)-t", "look-ahead") never breaks across lines
 * at its hyphens: each such token is wrapped in a nowrap span. Non-string input passes through.
 *
 * @param {import("react").ReactNode} text
 * @returns {import("react").ReactNode}
 */
export default function keepHyphenated(text) {
  if (typeof text !== "string" || !text.includes("-")) return text;
  return text.split(/(\S*\w-\S*)/).map((part, index) => (
    index % 2 === 1 ? <span key={index} className="nowrap">{part}</span> : part
  ));
}
