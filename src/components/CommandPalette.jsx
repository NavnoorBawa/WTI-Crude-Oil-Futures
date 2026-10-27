import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  buildCommands, downloadFile, getPrimarySection, groupCommands, jumpTo, MAX_RESULTS, rankCommands, sectionUrl, snapshotReference,
} from "../app/commands";
import { GIT_SHA, REPO_URL, RESEARCH_URL, STATIC_DATA_URL } from "../app/config";
import { SECTIONS } from "../app/sections";
import { PALETTE_KEYS, SHORTCUTS, setSingleKeysEnabled, useSingleKeys } from "../app/useShortcuts";
import { parseViewParams, setViewParam, useViewList } from "../app/viewState";
import { copyText } from "../lib/clipboard";
import { printPage } from "../lib/print";

const FILE_URLS = { "data.json": STATIC_DATA_URL, "research.json": RESEARCH_URL };

/** What the palette reads from the page when it opens: titles, table toggles and the view. */
const readPage = () => {
  const titles = {};
  const tables = [];
  for (const section of SECTIONS) {
    const node = document.getElementById(section.id);
    if (!node) continue;
    const heading = node.querySelector("h2");
    if (heading) titles[section.id] = heading.textContent.replace(/\s+/g, " ").trim();
    if (node.querySelector(`button[aria-controls="${section.id}-body"]`)) tables.push(section.id);
  }
  return { titles, tables, view: parseViewParams(window.location.search) };
};

/** Keys as <kbd>s: "/ or Ctrl K". */
function Keys({ keys }) {
  return keys.map((key, index) => (
    <span key={key} className="palette-key">{index > 0 && <span className="palette-or">or</span>}<kbd>{key}</kbd></span>
  ));
}

/** The shortcuts list and the single-key switch. */
function ShortcutsView({ titleId, onBack, backRef }) {
  const singleKeys = useSingleKeys();
  return (
    <div className="palette-shortcuts">
      <div className="palette-shortcuts-head">
        <h2 id={titleId} className="palette-title">Keyboard shortcuts</h2>
        <button ref={backRef} type="button" className="palette-text-button" onClick={onBack}>Back to search</button>
      </div>
      <div className="palette-switch-row">
        <button
          type="button"
          role="switch"
          aria-checked={singleKeys}
          className="palette-switch"
          onClick={() => setSingleKeysEnabled(!singleKeys)}
        >
          <span className="palette-switch-track" aria-hidden="true"><span className="palette-switch-thumb" /></span>
          Single-key shortcuts
        </button>
        <span className="palette-switch-note">
          {singleKeys ? "On. Turn off if they get in the way of speech input or other keys." : "Off. Ctrl K or ⌘ K still opens the palette."}
        </span>
      </div>
      <h3 className="palette-subtitle">On the page</h3>
      <dl className={`palette-keys${singleKeys ? "" : " is-off"}`}>
        {SHORTCUTS.map((row) => (
          <div key={row.what}>
            <dt><Keys keys={[...row.keys, ...(row.also ?? [])]} /></dt>
            <dd>{row.what}</dd>
          </div>
        ))}
      </dl>
      <h3 className="palette-subtitle">In the palette</h3>
      <dl className="palette-keys">
        {PALETTE_KEYS.map((row) => (
          <div key={row.what}>
            <dt><Keys keys={row.keys} /></dt>
            <dd>{row.what}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** One result row. */
function Option({ item, id, active, onPick, onPoint }) {
  return (
    <div
      id={id}
      role="option"
      aria-selected={active}
      className={`palette-option kind-${item.kind}${active ? " is-active" : ""}`}
      onClick={() => onPick(item)}
      onPointerMove={onPoint}
    >
      <span className="palette-option-main">
        {item.code && <span className="chip chip--code">{item.code}</span>}
        <span className="palette-label">{item.label}</span>
        {item.current && <span className="palette-current">current</span>}
      </span>
      {(item.mono || item.detail) && (
        <span className="palette-detail">
          {item.mono && <code className="palette-mono">{item.mono}</code>}
          {item.mono && item.detail ? " · " : null}
          {item.detail}
        </span>
      )}
    </div>
  );
}

/**
 * The jump palette: a native modal <dialog> (focus stays inside, the page behind is inert, Escape
 * closes) with a combobox over a grouped listbox. Mounted only while open; TopBar opens it on "/",
 * Ctrl/Cmd+K, "?" (the shortcuts view) or its Jump button, and gets `onClose({restore})` once it
 * has closed: restore is false when a jump moved focus to its destination.
 *
 * @param {object} props
 * @param {"search"|"shortcuts"} props.initialView
 * @param {object} props.data
 * @param {object|null} props.research
 * @param {(result: {restore: boolean}) => void} props.onClose
 * @param {(message: string) => void} props.notify   a short confirmation (copied, opened)
 */
export default function CommandPalette({ initialView = "search", data, research, onClose, notify }) {
  const dialogRef = useRef(null);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const backRef = useRef(null);
  const closing = useRef({ restore: true });
  const [page] = useState(readPage);
  const [view, setView] = useState(initialView);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [, toggleTable] = useViewList("table");
  const baseId = useId();
  const listId = `${baseId}-list`;
  const titleId = `${baseId}-title`;
  const optionId = (index) => `${baseId}-option-${index}`;

  const commands = useMemo(() => buildCommands({ data, research, ...page }), [data, research, page]);
  const ranked = useMemo(() => rankCommands(query, commands), [query, commands]);
  const groups = useMemo(() => groupCommands(ranked), [ranked]);
  // Options in the order they are shown (grouped), which is the order the arrow keys walk.
  const flat = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  const active = flat.length ? Math.min(activeIndex, flat.length - 1) : -1;

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  useLayoutEffect(() => {
    if (view === "search") inputRef.current?.focus();
    else backRef.current?.focus();
  }, [view]);

  // Keep the active option in view inside the list (never scrolls the page).
  useLayoutEffect(() => {
    const list = listRef.current;
    const node = active >= 0 ? list?.querySelector(`[id="${optionId(active)}"]`) : null;
    if (!list || !node) return;
    const group = node.parentElement;
    const first = group?.querySelector("[role='option']") === node;
    const top = first ? group.offsetTop : node.offsetTop;
    const bottom = node.offsetTop + node.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  });

  /** Closes the dialog, then runs `then` (synchronously, still inside the reader's gesture). */
  const closeThen = (then = null, restore = true) => {
    closing.current = { restore };
    dialogRef.current?.close();
    then?.();
  };

  const run = (item) => {
    const action = item.run;
    // The section on screen, as the c shortcut copies it: the #hash may name a section (or row)
    // the reader has since scrolled away from.
    const viewUrl = () => sectionUrl(getPrimarySection());
    switch (action.type) {
      case "jump":
        closeThen(() => jumpTo(action.target, { fallback: action.fallback, reveal: action.reveal }), false);
        break;
      case "view":
        setViewParam(action.key, action.value);
        closeThen(() => jumpTo(action.section), false);
        break;
      case "table":
        toggleTable(action.section);
        closeThen(() => jumpTo(action.section), false);
        break;
      case "copy-view":
        closeThen(() => copyText(viewUrl()).then((ok) => notify(ok ? "Link to this view copied" : "Could not copy the link")));
        break;
      case "copy-reference":
        closeThen(() => copyText(snapshotReference(viewUrl(), data?.frozen_at, GIT_SHA))
          .then((ok) => notify(ok ? "Snapshot reference copied" : "Could not copy the reference")));
        break;
      case "notes":
        closeThen(() => {
          window.dispatchEvent(new Event("notes:open-all"));
          notify("Method notes opened");
        });
        break;
      case "print":
        closeThen(() => { printPage(); });
        break;
      case "download":
        closeThen(() => downloadFile(FILE_URLS[action.file], action.file));
        break;
      case "shortcuts":
        setView("shortcuts");
        break;
      case "github":
        closeThen(() => window.open(REPO_URL, "_blank", "noopener,noreferrer"));
        break;
      default:
        break;
    }
  };

  const onKeyDown = (event) => {
    if (event.nativeEvent.isComposing) return;
    const count = flat.length;
    const move = (index) => {
      event.preventDefault();
      if (count) setActiveIndex(index);
    };
    switch (event.key) {
      case "ArrowDown": move(count ? (active + 1) % count : 0); break;
      case "ArrowUp": move(count ? (active - 1 + count) % count : 0); break;
      case "Home": move(0); break;
      case "End": move(count - 1); break;
      case "Enter":
        event.preventDefault();
        if (active >= 0) run(flat[active]);
        break;
      default:
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
          event.preventDefault();
          closeThen();
        }
    }
  };

  // A click on the backdrop (the dialog box itself, outside its content) closes it.
  const onDialogClick = (event) => {
    if (event.target !== dialogRef.current) return;
    const box = dialogRef.current.getBoundingClientRect();
    const outside = event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
    if (outside) closeThen();
  };

  const resultCount = flat.length;
  const status = view !== "search" ? "" : query.trim()
    ? (resultCount ? `${resultCount} result${resultCount === 1 ? "" : "s"}${resultCount === MAX_RESULTS ? `, the ${MAX_RESULTS} best` : ""}` : "No matches")
    : `${SECTIONS.length} sections and the page actions; type to search models, hypotheses and terms`;
  let index = -1;

  return (
    <dialog
      ref={dialogRef}
      className="palette"
      aria-label={view === "search" ? "Jump to a section, model, hypothesis or term" : undefined}
      aria-labelledby={view === "search" ? undefined : titleId}
      onClose={() => onClose(closing.current)}
      onClick={onDialogClick}
    >
      {view === "search" ? (
        <div className="palette-body">
          <div className="palette-search">
            <svg className="palette-glyph" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
              <circle cx="6" cy="6" r="4.5" /><path d="M9.5 9.5 13 13" />
            </svg>
            <input
              ref={inputRef}
              className="palette-input"
              type="text"
              role="combobox"
              aria-label="Jump to"
              aria-autocomplete="list"
              aria-expanded={resultCount > 0}
              aria-controls={listId}
              aria-activedescendant={active >= 0 ? optionId(active) : undefined}
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="go"
              placeholder="Code, model, ledger id or term"
              value={query}
              onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }}
              onKeyDown={onKeyDown}
            />
            {/* The name holds the visible text at every width: "Esc" above the phone layout, "Close" on it. */}
            <button type="button" className="palette-close" onClick={() => closeThen()} aria-label="Close (Esc)">
              <span className="palette-close-key" aria-hidden="true">Esc</span>
              <span className="palette-close-word" aria-hidden="true">Close</span>
            </button>
          </div>
          {/* Not a Tab stop (an overflowing list would be one), and a press on it leaves focus in the
              combobox, the list's only keyboard owner (through aria-activedescendant). */}
          <div
            ref={listRef}
            id={listId}
            role="listbox"
            aria-label="Results"
            className="palette-list"
            tabIndex={-1}
            onMouseDown={(event) => event.preventDefault()}
          >
            {groups.map((group) => (
              <div key={group.kind} role="group" aria-labelledby={`${baseId}-${group.kind}`} className="palette-group">
                <div id={`${baseId}-${group.kind}`} role="presentation" className="palette-group-label">{group.label}</div>
                {group.items.map((item) => {
                  index += 1;
                  const at = index;
                  return (
                    <Option
                      key={item.id}
                      item={item}
                      id={optionId(at)}
                      active={at === active}
                      onPick={run}
                      onPoint={() => { if (at !== active) setActiveIndex(at); }}
                    />
                  );
                })}
              </div>
            ))}
          </div>
          {!resultCount && <p className="palette-empty">Nothing matches “{query.trim()}”. Try a section code such as MODL, a model name, a ledger id or a term such as QLIKE.</p>}
          <div className="palette-foot">
            <p className="palette-status" role="status">{status}</p>
            <button type="button" className="palette-text-button" onClick={() => setView("shortcuts")}>Shortcuts</button>
          </div>
        </div>
      ) : (
        <div className="palette-body">
          <ShortcutsView titleId={titleId} backRef={backRef} onBack={() => setView("search")} />
        </div>
      )}
    </dialog>
  );
}
