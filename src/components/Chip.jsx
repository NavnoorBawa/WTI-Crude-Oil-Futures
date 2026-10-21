/**
 * A small inline label. Tones: "code" (section code), "record" (model of record), "challenger",
 * "muted", "warn", "status-pending" | "status-resolved" | "status-skipped_roll".
 *
 * @param {{tone?: string, title?: string, children: import("react").ReactNode}} props
 */
export default function Chip({ tone = "muted", title, children }) {
  return <span className={`chip chip--${tone}`} title={title}>{children}</span>;
}
