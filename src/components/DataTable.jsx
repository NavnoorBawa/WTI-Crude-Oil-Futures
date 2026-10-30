import { useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { csvFileName, downloadText, toCsv } from "../lib/csv";
import { fmtInt } from "../lib/format";
import { SnapshotContext } from "../lib/snapshot";

/**
 * @typedef {object} Column
 * @property {string} key                          unique id; the default sort value is row[key]
 * @property {import("react").ReactNode} label     header text
 * @property {string} [title]                      header tooltip (expands abbreviations)
 * @property {"left"|"right"|"center"} [align]     numbers are right-aligned
 * @property {boolean} [sortable]
 * @property {"asc"|"desc"} [firstDirection]       direction of the first click (desc where higher is better)
 * @property {(row: object) => (number|string|null)} [sortValue]
 * @property {(row: object) => import("react").ReactNode} [render]
 * @property {boolean} [rowHeader]                 render the cell as <th scope="row">
 * @property {string} [className]
 * @property {((row: object) => unknown)|false} [csv]  raw value for the CSV export; false leaves the column out
 * @property {string} [csvLabel]                   CSV header (default: the label when it is text, else the key)
 * @property {640|900|1000|1100} [hideBelow]       hide the column on screens narrower than that (640: 640px
 *                                                  or less); the CSV keeps it
 */

const isMissing = (value) => value == null || (typeof value === "number" && !Number.isFinite(value)) || value === "";

/** Ascending comparison: numbers numerically, text with numeric collation. */
export const compareValues = (a, b) => {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), "en", { numeric: true, sensitivity: "base" });
};

/**
 * Stable sort by an accessor. Missing values (null, NaN, "") always sort last, in either direction,
 * so "no data" never masquerades as the best or worst row.
 */
export const sortRows = (rows, accessor, direction = "asc") => {
  const decorated = rows.map((row, index) => ({ row, index, value: accessor(row) }));
  decorated.sort((x, y) => {
    const xMissing = isMissing(x.value);
    const yMissing = isMissing(y.value);
    if (xMissing || yMissing) return xMissing === yMissing ? x.index - y.index : xMissing ? 1 : -1;
    const order = compareValues(x.value, y.value);
    if (order === 0) return x.index - y.index;
    return direction === "desc" ? -order : order;
  });
  return decorated.map(({ row }) => row);
};

/** The sort state after a header click: same column flips, a new column starts at its first direction. */
export const nextSort = (current, column) =>
  current?.key === column.key
    ? { key: column.key, direction: current.direction === "asc" ? "desc" : "asc" }
    : { key: column.key, direction: column.firstDirection ?? "asc" };

/** Rows in display order: sorted by `sort`, then the `pinLast` rows in input order. Pure. */
export const orderRows = (rows, columns, sort, pinLast = null) => {
  const pinned = pinLast ? rows.filter(pinLast) : [];
  const free = pinned.length ? rows.filter((row) => !pinLast(row)) : rows;
  const column = sort ? columns.find((col) => col.key === sort.key) : null;
  const ordered = column ? sortRows(free, column.sortValue ?? ((row) => row[column.key]), sort.direction) : free;
  return pinned.length ? [...ordered, ...pinned] : ordered;
};

/** A column's header as plain text, when it is text. */
const labelText = (column) => (typeof column.label === "string" ? column.label : null);

/** CSV header and raw value of a column (see Column.csv). */
export const csvColumns = (columns) => columns
  .filter((column) => column.csv !== false)
  .map((column) => ({
    label: column.csvLabel ?? labelText(column) ?? column.key,
    value: (row) => {
      if (typeof column.csv === "function") return column.csv(row);
      if (typeof column.sortValue === "function") return column.sortValue(row);
      const value = row?.[column.key];
      return typeof value === "number" || typeof value === "string" ? value : null;
    },
  }));

const ARIA_SORT = { asc: "ascending", desc: "descending" };
const ANNOUNCE_DELAY_MS = 250;

// A change to the rows this soon after a reader's click, key press or typing is the reader's (a sort,
// a filter, Show all); a later one comes from the data (research.json landing, a refreshed snapshot).
export const READER_WINDOW_MS = 1500;
const READER_EVENTS = ["pointerdown", "keydown", "input"];
let readerInputAt = Number.NEGATIVE_INFINITY;
let watchingReaders = false;
/** Notes the time of every input from the reader, once per page, before any handler runs. */
const watchReaders = () => {
  if (watchingReaders || typeof window === "undefined") return;
  watchingReaders = true;
  const note = (event) => {
    if (event.isTrusted) readerInputAt = performance.now();
  };
  for (const type of READER_EVENTS) window.addEventListener(type, note, { capture: true, passive: true });
};

/**
 * Whether a table's "Showing N of M …, sorted by …" sentence is spoken: only when its text has
 * changed (the same rows re-created by a re-render say nothing new) and a reader's input came at
 * most READER_WINDOW_MS before, so a sort, a filter or Show all is announced, while research.json
 * landing or a refreshed snapshot, which change the rows too, stay silent (on the load of a ?table=
 * link as well). Times in ms on one clock. Pure.
 *
 * @param {{previous: string, next: string, inputAt: number, now: number}} change  the sentence before
 *   and after, the time of the reader's last input and the time of the change
 */
export const shouldAnnounce = ({ previous, next, inputAt, now }) =>
  next !== previous && now - inputAt >= 0 && now - inputAt <= READER_WINDOW_MS;

/**
 * A semantic, optionally sortable table: <caption>, scoped headers and aria-sort on sortable
 * columns. A table that overflows its box (wide, or tall with a capped height) becomes a
 * keyboard-focusable scroll region; one that fits is not a tab stop. While columns are cut off a
 * visible cue says so (a phone shows no scrollbar), a rule marks the right edge while more lies
 * beyond it, and the sticky first column gets an edge once scrolled past.
 *
 * @param {object} props
 * @param {import("react").ReactNode} props.caption
 * @param {boolean} [props.captionHidden]   keep the caption for assistive tech only
 * @param {Column[]} props.columns
 * @param {object[]} props.rows
 * @param {(row: object) => string} props.rowKey
 * @param {(row: object) => string|undefined} [props.rowClass]
 * @param {(row: object) => string|undefined} [props.rowId]   an id per row, for #anchors
 * @param {{key: string, direction: "asc"|"desc"}} [props.initialSort]
 * @param {{key: string, direction: "asc"|"desc"}|null} [props.sort]  controlled sort (with onSortChange)
 * @param {(sort: {key: string, direction: "asc"|"desc"}) => void} [props.onSortChange]
 * @param {(row: object) => boolean} [props.pinLast]  rows kept after the sorted ones, in input order
 * @param {boolean} [props.dense]
 * @param {boolean} [props.tall]           cap the height and scroll vertically, header stuck on top
 * @param {boolean} [props.stickyFirst]    keep the first column in view while scrolling sideways (default on)
 * @param {{label: import("react").ReactNode, span: number}[]} [props.groups]  a header row over column groups
 * @param {640} [props.cardsBelow]         at 640px or less, each row is a card of label/value lines
 * @param {string} [props.exportName]      adds a CSV button; the file is wti-vol_<exportName>_<snapshot date>.csv
 * @param {object[]} [props.exportRows]    rows the CSV holds (default: rows), in the table's current sort
 * @param {boolean|string} [props.announce]  say "Showing N of M <rows>, sorted by …" after a change;
 *   a string names the rows ("hypotheses")
 * @param {number} [props.totalCount]      M in that sentence (default: rows.length)
 * @param {import("react").ReactNode} [props.footnote]
 * @param {import("react").ReactNode} [props.empty]  shown in one full-width row when there are no rows
 */
/** The class that hides a column below its breakpoint (chrome-foundation.css): "col-wide" at 640px or
 * less, "col-hide-<px>" below a mid breakpoint; any other value falls back to "col-wide". */
const MID_BREAKPOINTS = [900, 1000, 1100];
const hideClass = (hideBelow) => {
  if (!hideBelow) return null;
  return MID_BREAKPOINTS.includes(hideBelow) ? `col-hide-${hideBelow}` : "col-wide";
};

export default function DataTable({
  caption,
  captionHidden = false,
  columns,
  rows,
  rowKey,
  rowClass,
  rowId,
  initialSort = null,
  sort: controlledSort,
  onSortChange,
  pinLast = null,
  dense = false,
  tall = false,
  stickyFirst = true,
  groups = null,
  cardsBelow = null,
  exportName = null,
  exportRows = null,
  announce = false,
  totalCount = null,
  footnote = null,
  empty = null,
}) {
  const captionId = useId();
  const { frozenAt = null } = useContext(SnapshotContext) ?? {};
  const [ownSort, setOwnSort] = useState(initialSort);
  const controlled = controlledSort !== undefined;
  const sort = controlled ? controlledSort : ownSort;
  const scrollRef = useRef(null);
  const [overflow, setOverflow] = useState({ wide: false, moreRight: false, scrolled: false, overY: false });
  const sorted = useMemo(() => orderRows(rows, columns, sort, pinLast), [rows, columns, sort, pinLast]);

  const changeSort = (column) => {
    const next = nextSort(sort, column);
    if (!controlled) setOwnSort(next);
    onSortChange?.(next);
  };

  // Layout effect: the "Scroll sideways" line is part of the first paint, so it does not push the
  // rest of the page down after a deep link has landed.
  useLayoutEffect(() => {
    const node = scrollRef.current;
    if (!node) return undefined;
    const update = () => {
      const wide = node.scrollWidth - node.clientWidth > 1;
      const moreRight = wide && node.scrollWidth - node.clientWidth - node.scrollLeft > 1;
      const scrolled = wide && node.scrollLeft > 0;
      const overY = node.scrollHeight - node.clientHeight > 1;
      setOverflow((previous) => (
        previous.wide === wide && previous.moreRight === moreRight && previous.scrolled === scrolled && previous.overY === overY
          ? previous
          : { wide, moreRight, scrolled, overY }
      ));
    };
    update();
    node.addEventListener("scroll", update, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(node);
    if (node.firstElementChild) observer?.observe(node.firstElementChild);
    return () => {
      node.removeEventListener("scroll", update);
      observer?.disconnect();
    };
  }, []);

  // "Showing N of M rows, sorted by …", spoken a moment after a reader's sort, filter or Show all
  // (shouldAnnounce): never on load, nor when the data behind the rows changes.
  const sortColumn = sort ? columns.find((column) => column.key === sort.key) : null;
  const noun = typeof announce === "string" ? announce : "rows";
  const summary = `Showing ${fmtInt(rows.length)} of ${fmtInt(totalCount ?? rows.length)} ${noun}`
    + (sortColumn ? `, sorted by ${sortColumn.csvLabel ?? labelText(sortColumn) ?? sortColumn.key} ${ARIA_SORT[sort.direction]}` : "");
  const [message, setMessage] = useState("");
  const announced = useRef(summary);
  useEffect(() => {
    if (announce) watchReaders();
  }, [announce]);
  useEffect(() => {
    if (!announce) return undefined;
    const speak = shouldAnnounce({ previous: announced.current, next: summary, inputAt: readerInputAt, now: performance.now() });
    announced.current = summary;
    if (!speak) return undefined;
    const timer = setTimeout(() => setMessage(summary), ANNOUNCE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [announce, summary]);

  const download = () => {
    const ordered = orderRows(exportRows ?? rows, columns, sort, pinLast);
    downloadText(toCsv(csvColumns(columns), ordered), csvFileName(exportName, frozenAt));
  };

  // Column-group header row: the first column of each group draws a rule on its left (CSS skips the
  // table's first column).
  const groupStarts = new Set();
  (groups ?? []).reduce((index, group) => {
    groupStarts.add(index);
    return index + (Number(group.span) || 1);
  }, 0);
  const cards = cardsBelow != null;
  const role = (name) => (cards ? name : undefined);
  const cellClass = (column, index) => [
    `align-${column.align ?? "left"}`,
    column.className,
    hideClass(column.hideBelow),
    groupStarts.has(index) ? "group-start" : null,
  ].filter(Boolean).join(" ");
  const scrollable = overflow.wide || (tall && overflow.overY);
  const regionProps = scrollable ? { role: "region", "aria-labelledby": captionId, tabIndex: 0 } : {};

  // The <caption> stays in the table for assistive tech; the visible copy sits outside the scroll
  // region so a wide table on a phone never pushes its title off screen.
  const visibleCaption = !captionHidden ? <p className="table-caption" aria-hidden="true">{caption}</p> : null;
  return (
    <div className="table-wrap">
      {exportName ? (
        <div className="table-caption-row">
          {visibleCaption}
          <button type="button" className="csv-button" onClick={download} aria-describedby={captionId}>
            <span className="sr-only">Download </span>CSV
          </button>
        </div>
      ) : visibleCaption}
      {overflow.wide && <p className="table-more" aria-hidden="true">Scroll sideways for all columns ↔</p>}
      <div
        ref={scrollRef}
        className={`table-scroll${tall ? " is-tall" : ""}${overflow.moreRight ? " has-more" : ""}${overflow.scrolled ? " is-scrolled" : ""}`}
        {...regionProps}
      >
        <table
          className={`data-table${dense ? " is-dense" : ""}${stickyFirst ? " has-sticky" : ""}${cards ? " is-cards" : ""}${groups ? " has-groups" : ""}`}
          role={role("table")}
        >
          <caption id={captionId} className="sr-only">{caption}</caption>
          {groups && groups.map((group, index) => <colgroup key={index} span={Number(group.span) || 1} />)}
          <thead role={role("rowgroup")}>
            {groups && (
              <tr className="group-row" role={role("row")}>
                {/* A group without a label is a blank cell, not an unnamed column-group header. */}
                {groups.map((group, index) => (group.label ? (
                  <th
                    key={index}
                    scope="colgroup"
                    colSpan={Number(group.span) || 1}
                    className="group-start"
                    role={role("columnheader")}
                  >
                    {group.label}
                  </th>
                ) : (
                  <td key={index} colSpan={Number(group.span) || 1} className="group-start" role={role("cell")} aria-hidden="true" />
                )))}
              </tr>
            )}
            <tr role={role("row")}>
              {columns.map((column, index) => {
                const active = sort?.key === column.key;
                return (
                  <th
                    key={column.key}
                    scope="col"
                    className={cellClass(column, index)}
                    aria-sort={column.sortable ? (active ? ARIA_SORT[sort.direction] : "none") : undefined}
                    title={column.title}
                    role={role("columnheader")}
                  >
                    {column.sortable ? (
                      <button type="button" className="sort-button" onClick={() => changeSort(column)}>
                        <span>{column.label}</span>
                        <span className={`sort-glyph${active ? " is-active" : ""}`} aria-hidden="true">
                          {active ? (sort.direction === "asc" ? "↑" : "↓") : "↕"}
                        </span>
                      </button>
                    ) : <span className="th-label">{column.label}</span>}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody role={role("rowgroup")}>
            {sorted.length === 0 && empty && (
              <tr role={role("row")}><td className="empty-cell" colSpan={columns.length} role={role("cell")}>{empty}</td></tr>
            )}
            {sorted.map((row) => (
              <tr key={rowKey(row)} id={rowId?.(row) || undefined} className={rowClass?.(row)} role={role("row")}>
                {columns.map((column, index) => {
                  const content = column.render ? column.render(row) : row[column.key];
                  const label = cards ? (labelText(column) ?? column.csvLabel ?? undefined) : undefined;
                  return column.rowHeader
                    ? <th key={column.key} scope="row" className={cellClass(column, index)} data-label={label} role={role("rowheader")}>{content}</th>
                    : <td key={column.key} className={cellClass(column, index)} data-label={label} role={role("cell")}>{content}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {announce && <p className="sr-only" aria-live="polite">{message}</p>}
      {footnote && <div className="table-footnote">{footnote}</div>}
    </div>
  );
}
