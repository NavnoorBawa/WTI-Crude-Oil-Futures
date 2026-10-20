import { useId, useMemo, useState } from "react";

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

const ARIA_SORT = { asc: "ascending", desc: "descending" };

/**
 * A semantic, optionally sortable table: <caption>, scoped headers, aria-sort on sortable columns,
 * and a keyboard-focusable scroll region so wide tables never widen the page.
 *
 * @param {object} props
 * @param {import("react").ReactNode} props.caption
 * @param {boolean} [props.captionHidden]   keep the caption for assistive tech only
 * @param {Column[]} props.columns
 * @param {object[]} props.rows
 * @param {(row: object) => string} props.rowKey
 * @param {(row: object) => string|undefined} [props.rowClass]
 * @param {{key: string, direction: "asc"|"desc"}} [props.initialSort]
 * @param {boolean} [props.dense]
 * @param {boolean} [props.tall]           cap the height and scroll vertically, header stuck on top
 * @param {import("react").ReactNode} [props.footnote]
 */
export default function DataTable({
  caption,
  captionHidden = false,
  columns,
  rows,
  rowKey,
  rowClass,
  initialSort = null,
  dense = false,
  tall = false,
  footnote = null,
}) {
  const captionId = useId();
  const [sort, setSort] = useState(initialSort);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((col) => col.key === sort.key);
    if (!column) return rows;
    const accessor = column.sortValue ?? ((row) => row[column.key]);
    return sortRows(rows, accessor, sort.direction);
  }, [rows, columns, sort]);

  // The <caption> stays in the table for assistive tech; the visible copy sits outside the scroll
  // region so a wide table on a phone never pushes its title off screen.
  return (
    <div className="table-wrap">
      {!captionHidden && <p className="table-caption" aria-hidden="true">{caption}</p>}
      <div className={`table-scroll${tall ? " is-tall" : ""}`} role="region" aria-labelledby={captionId} tabIndex={0}>
        <table className={`data-table${dense ? " is-dense" : ""}`}>
          <caption id={captionId} className="sr-only">{caption}</caption>
          <thead>
            <tr>
              {columns.map((column) => {
                const active = sort?.key === column.key;
                const align = column.align ?? "left";
                return (
                  <th
                    key={column.key}
                    scope="col"
                    className={`align-${align}${column.className ? ` ${column.className}` : ""}`}
                    aria-sort={column.sortable ? (active ? ARIA_SORT[sort.direction] : "none") : undefined}
                    title={column.title}
                  >
                    {column.sortable ? (
                      <button type="button" className="sort-button" onClick={() => setSort((current) => nextSort(current, column))}>
                        <span>{column.label}</span>
                        <span className={`sort-glyph${active ? " is-active" : ""}`} aria-hidden="true">
                          {active ? (sort.direction === "asc" ? "↑" : "↓") : "↕"}
                        </span>
                      </button>
                    ) : column.label}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={rowKey(row)} className={rowClass?.(row)}>
                {columns.map((column) => {
                  const content = column.render ? column.render(row) : row[column.key];
                  const className = `align-${column.align ?? "left"}${column.className ? ` ${column.className}` : ""}`;
                  return column.rowHeader
                    ? <th key={column.key} scope="row" className={className}>{content}</th>
                    : <td key={column.key} className={className}>{content}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {footnote && <div className="table-footnote">{footnote}</div>}
    </div>
  );
}
