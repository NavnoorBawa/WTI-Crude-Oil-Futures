import { Fragment } from "react";
import { codeUrl, PRICE_URL, RESEARCH_URL, STATIC_DATA_URL } from "../app/config";
import { useViewList } from "../app/viewState";
import CopyButton from "./CopyButton";
import Disclosure from "./Disclosure";
import ErrorBoundary from "./ErrorBoundary";
import keepHyphenated from "./keepHyphenated";
import { withTerms } from "./Term";

/** The command that rebuilds each artifact a panel reads (see README, "Reproduce"). */
export const REPRODUCE = {
  lab: "python -m backend.vol_lab",
  validation: "python -m backend.vol_forecast",
  leakAnatomy: "python -m backend.leak_anatomy",
  walkForward: "python -m backend.backtest_walk_forward [--no-purge]",
  carry: "python -m backend.carry_signal_test",
  supply: "python -m backend.supply_shock_playbook",
  ledger: "python -m backend.research_ledger",
};
export const REPRODUCE_COMMAND = REPRODUCE.lab;

// The payload files a Source footer can name, served next to the page.
const FILE_URLS = { "data.json": STATIC_DATA_URL, "research.json": RESEARCH_URL, "price.json": PRICE_URL };

/**
 * Source footer entries with the file each one reads: "research.json → vol_lab.series" names its
 * file; an entry without a "x.json →" prefix ("vol_lab.leaderboard") reads the previous entry's.
 * Pure.
 *
 * @param {string[]} artifacts
 * @returns {{text: string, file: string|null, href: string|null}[]}
 */
export const sourceLinks = (artifacts) => {
  let file = null;
  return artifacts.map((text) => {
    const match = /^([\w.-]+\.json)\s*→/.exec(text);
    if (match) file = match[1];
    return { text, file, href: file ? FILE_URLS[file] ?? null : null };
  });
};

/**
 * The repository file a command runs: "python -m backend.vol_lab" -> "backend/vol_lab.py",
 * "python scripts/build_research_bundle.py" -> that path; null for anything else. Pure.
 */
export const commandPath = (command) => {
  if (typeof command !== "string") return null;
  const module = /\bpython3?\s+-m\s+(backend(?:\.\w+)+)/.exec(command);
  if (module) return `${module[1].replace(/\./g, "/")}.py`;
  const script = /(?:^|\s)((?:[\w-]+\/)*[\w-]+\.py)(?=\s|$)/.exec(command);
  return script ? script[1] : null;
};

/** Notes as one paragraph per string (glossary terms marked); other nodes pass through. */
const renderNotes = (notes, seen) => {
  if (notes == null || notes === false || notes === "") return null;
  const items = Array.isArray(notes) ? notes.filter((note) => note != null && note !== false && note !== "") : [notes];
  if (!items.length) return null;
  return items.map((note, index) => (typeof note === "string"
    ? <p key={index}>{withTerms(note, seen)}</p>
    : <Fragment key={index}>{note}</Fragment>));
};

/**
 * A reproduce command: the command (linked to its module on GitHub) and its copy button, in one
 * unit so a wrapping footer never leaves the button at the start of the next command's line. A
 * long command breaks at its spaces, never inside a flag such as --no-purge.
 */
function ReproduceCommand({ command }) {
  const path = commandPath(command);
  return (
    <span className="source-command">
      {path ? (
        <a
          className="source-item"
          href={codeUrl(path)}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${command} (code on GitHub, opens in a new tab)`}
        >
          <code>{keepHyphenated(command)}</code>
        </a>
      ) : <code className="source-item">{keepHyphenated(command)}</code>}
      <CopyButton className="copy-command" label={`Copy command: ${command}`} text={command} done="Command copied" />
    </span>
  );
}

/**
 * One research section. Anatomy, top to bottom: a code chip (a link to the section itself), the
 * plain section name and a copy-link button; the assertion title (h2, computed from the payload);
 * meta (sample size, span, test); then the panel's actions (a Table toggle and any filters), which
 * CSS lifts to the top right; the figure, with its view controls; a caption from the data; method
 * notes behind one disclosure; and a source footer whose artifacts link to the payload files and
 * whose commands link to their modules and can be copied. The Table toggle swaps the figure for an
 * accessible table of the same numbers and is held in the URL (?table=fcst,modl).
 *
 * The caption (when a string) and notes mark the first use of each glossary term per panel; the
 * title and meta never do.
 *
 * @param {object} props
 * @param {string} props.id                     anchor id (the lowercase code)
 * @param {string} props.code                   section code chip, e.g. "FCST"
 * @param {string} props.name                   plain section name, e.g. "Forecast vs realized"
 * @param {string|null} props.title             assertion title; null falls back to `fallbackTitle`
 * @param {string} [props.fallbackTitle]        neutral title when the data cannot support an assertion
 * @param {Array<import("react").ReactNode>} [props.meta]   short facts; falsy entries are skipped
 * @param {import("react").ReactNode} [props.actions]       header controls (filters); the Table toggle joins them
 * @param {import("react").ReactNode} [props.controls]      figure controls (range presets), right-aligned
 *   above the figure and hidden with it while the table shows
 * @param {import("react").ReactNode} [props.caption]       how to read the figure (and any caveat that qualifies the title)
 * @param {import("react").ReactNode|string|string[]} [props.notes]  method detail, closed by default;
 *   never a caveat that qualifies the title
 * @param {{artifact: string|string[], reproduce?: string|string[]|null}} [props.source]  reproduce
 *   defaults to the benchmark lab's command; panels built from other artifacts name their own
 * @param {() => import("react").ReactNode} [props.table]  renders the table view (only when shown)
 * @param {string} [props.tableLabel]           visible label of the table toggle (default "Table")
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
  controls = null,
  caption = null,
  notes = null,
  source = null,
  table = null,
  tableLabel = "Table",
  span = "full",
  resetKey,
  children,
}) {
  const [tables, toggleTable] = useViewList("table");
  const showTable = Boolean(table) && tables.has(id);
  const titleId = `${id}-title`;
  const bodyId = `${id}-body`;
  const metaItems = meta.filter(Boolean);
  const artifacts = source ? [].concat(source.artifact).filter(Boolean) : [];
  const reproduce = [].concat(source?.reproduce === undefined ? REPRODUCE_COMMAND : source.reproduce).filter(Boolean);
  // One set per render: each glossary term is explained once in this panel, at its first use.
  const seen = new Set();
  const captionContent = typeof caption === "string" ? withTerms(caption, seen) : caption;
  const notesContent = renderNotes(notes, seen);

  return (
    <section id={id} className={`panel span-${span}`} aria-label={`${code}, ${name}`}>
      <header className="panel-head">
        <div className="panel-eyebrow">
          <a className="chip chip--code chip-link" href={`#${id}`}>{code}</a>
          <span className="micro">{name}</span>
          <CopyButton
            className="copy-link"
            glyph="link"
            label={`Copy link to ${code}, ${name}`}
            done="Link copied"
            text={() => `${window.location.origin}${window.location.pathname}${window.location.search}#${id}`}
          />
        </div>
        <h2 id={titleId} className="panel-title" tabIndex={-1}>{keepHyphenated(title || fallbackTitle || name)}</h2>
        {metaItems.length > 0 && (
          <p className="panel-meta sep-list">
            {metaItems.map((item, index) => <span key={index}>{item}</span>)}
          </p>
        )}
        {(actions || table) && (
          <div className="panel-actions">
            {actions}
            {table && (
              <button
                type="button"
                className="toggle-button"
                aria-pressed={showTable}
                aria-controls={bodyId}
                onClick={() => toggleTable(id)}
              >
                {tableLabel}<span className="sr-only"> view of {name}</span>
              </button>
            )}
          </div>
        )}
      </header>

      <div id={bodyId} className="panel-body">
        <div className="panel-figure" hidden={showTable}>
          {controls && <div className="figure-controls">{controls}</div>}
          <ErrorBoundary label={`The ${name.toLowerCase()} figure`} resetKey={resetKey}>
            {children}
          </ErrorBoundary>
        </div>
        {showTable && <div className="panel-table">{table()}</div>}
      </div>

      {caption && <p className="panel-caption">{captionContent}</p>}
      {notesContent && <Disclosure className="panel-notes" summary="Method notes">{notesContent}</Disclosure>}

      {(artifacts.length > 0 || reproduce.length > 0) && (
        <footer className="panel-source">
          {artifacts.length > 0 && (
            <span className="source-group source-files">
              <span className="micro">Source</span>
              {sourceLinks(artifacts).map(({ text, href }) => (href ? (
                <a
                  key={text}
                  className="source-item"
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`${text} (the data file, opens in a new tab)`}
                >
                  <code>{text}</code>
                </a>
              ) : <code key={text} className="source-item">{text}</code>))}
            </span>
          )}
          {reproduce.length > 0 && (
            <span className="source-group source-commands">
              <span className="micro">Reproduce</span>
              {reproduce.map((command) => <ReproduceCommand key={command} command={command} />)}
            </span>
          )}
        </footer>
      )}
    </section>
  );
}
