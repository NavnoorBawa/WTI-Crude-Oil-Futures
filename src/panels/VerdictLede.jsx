import keepHyphenated from "../components/keepHyphenated";
import { getLab } from "../lib/research";
import { evidenceRows, verdictLede } from "../lib/verdict";

/** The question the page answers. Static copy: it holds no number. */
const QUESTION = "How well can next-week WTI realized volatility be forecast from free data?";

/**
 * LEDE: the question, then the page's computed answer (the h2 that names the verdict section) with
 * the statistics behind it and their sample. See verdictLede(). While research.json is loading the
 * answer is the weekly production validation's, and the lede is busy and keeps the lab answer's
 * height, so nothing below moves when the lab arrives.
 */
export function VerdictLede({ data, research, researchStatus = "ready" }) {
  const { answer, detail } = verdictLede(getLab(research), data);
  const pending = researchStatus === "loading" && !research;
  return (
    <div className="verdict-lede" aria-busy={pending ? "true" : undefined}>
      <p className="micro">Question</p>
      <p className="verdict-question">{QUESTION}</p>
      {/* Keyed by their text: when the lab's answer replaces the production one it is new text, not
          old text moved (which the browser would count as a layout shift). */}
      <h2 key={answer} id="verdict-title" className="verdict-answer">{keepHyphenated(answer)}</h2>
      {(detail || pending) && <p key={detail ?? ""} className="verdict-answer-detail">{keepHyphenated(detail)}</p>}
    </div>
  );
}

/**
 * EVIDENCE: every section's computed title as an index entry linking to its figure, in page order
 * (evidenceRows()). No new claim or number: each row is the h2 its section shows.
 */
export function EvidenceList({ data, research }) {
  const rows = evidenceRows(data, research);
  if (!rows.length) return null;
  return (
    <section className="evidence" aria-labelledby="evidence-title">
      <h3 id="evidence-title" className="micro evidence-title">What the evidence says</h3>
      <ol className="evidence-list">
        {rows.map((row) => (
          <li key={row.id}>
            <a href={`#${row.id}`} className="evidence-link">
              <span className="chip chip--code">{row.code}</span>
              <span className="evidence-text">{keepHyphenated(row.text)}</span>
            </a>
          </li>
        ))}
      </ol>
    </section>
  );
}
