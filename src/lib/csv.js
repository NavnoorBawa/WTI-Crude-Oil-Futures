// CSV export of a table's rows: RFC 4180 text built in memory and handed to the browser as a file.
// Nothing is fetched, so the page's Content-Security-Policy is unaffected.

const BOM = "﻿";
const CRLF = "\r\n";

/**
 * One RFC 4180 field. Numbers are written raw (unrounded); null, undefined and non-finite numbers
 * are empty; text containing a comma, a double quote, CR or LF (or edge spaces) is quoted, with
 * its quotes doubled.
 */
export const csvField = (value) => {
  if (value == null) return "";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  const text = typeof value === "string" ? value : String(value);
  return /[",\r\n]|^\s|\s$/.test(text) ? `"${text.replace(/"/g, "\"\"")}"` : text;
};

/**
 * CSV text for rows: a UTF-8 byte-order mark (so spreadsheet software reads "−" and "è" correctly),
 * a header line, then one line per row, every line ended by CRLF. Pure.
 *
 * @param {{label: string, value: (row: object) => unknown}[]} columns
 * @param {object[]} rows
 * @returns {string}
 */
export const toCsv = (columns, rows) => {
  const lines = [columns.map((column) => csvField(column.label))];
  for (const row of rows) lines.push(columns.map((column) => csvField(column.value(row))));
  return `${BOM}${lines.map((fields) => fields.join(",")).join(CRLF)}${CRLF}`;
};

/** "2026-10-26T14:05:00Z" -> "2026-10-26": the snapshot's date for file names, else "snapshot". */
export const snapshotDate = (frozenAt) =>
  (typeof frozenAt === "string" && /^\d{4}-\d{2}-\d{2}/.test(frozenAt) ? frozenAt.slice(0, 10) : "snapshot");

/** The file name of a table export, e.g. "wti-vol_ledger_2026-10-26.csv". */
export const csvFileName = (exportName, frozenAt) => `wti-vol_${exportName}_${snapshotDate(frozenAt)}.csv`;

/**
 * Saves text as a file: a Blob behind an object URL, clicked through a temporary <a download>,
 * and revoked once the browser has taken it.
 */
export function downloadText(text, fileName, type = "text/csv;charset=utf-8") {
  if (typeof document === "undefined" || typeof URL?.createObjectURL !== "function") return false;
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return true;
}
