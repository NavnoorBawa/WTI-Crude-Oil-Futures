import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { setViewParam, useViewParam } from "../app/viewState";
import { getLedgerStrip } from "../charts/lazy";
import Chip from "../components/Chip";
import CopyButton from "../components/CopyButton";
import DataTable, { orderRows } from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import { ledgerAssertion } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtPValue, humanizeKey, num } from "../lib/format";
import {
  LEDGER_PAGE, filterLedgerRows, ledgerAnchor, ledgerFlagText, ledgerOrderValue, ledgerStrip, ledgerSupportText,
  revealLedgerView, visibleLedgerRows,
} from "../lib/reference";
import { asText, getLab, ledgerStatusLabel, ledgerText, mcsLevel, modelLabels, researchLedger } from "../lib/research";
import { ResearchUnavailable } from "./common";

const STATUS_TONE = { supported: "status-resolved", not_supported: "muted", retracted: "warn", inconclusive: "status-pending" };
const STATUS_ORDER = ["supported", "not_supported", "inconclusive", "retracted"];
const STATUS_TITLE = {
  supported: "p below 5%, with the estimate pointing the way the hypothesis says",
  not_supported: "p of 10% or more, or the estimate points the other way: a failure to reject, not evidence against the hypothesis",
  inconclusive: "p between 5% and 10%, or no p-value with the estimate pointing the right way",
  retracted: "the evidence was invalid when produced (leaked walk-forward, roll-contaminated returns or a wrong null); kept as a record",
};
const UNDERPOWERED_TITLE = "The test's recorded power is below its target, so a failure to reject says little about whether the effect exists";
const AFTER_SEARCH_TITLE = "The model was chosen as the best of a field on this same sample, so this pairwise p-value ignores that search. "
  + "The row is left out of the multiplicity adjustment (no q-value, Holm p-value or t > 3 flag); the Hansen SPA row of this ledger "
  + "tests the same question with the search taken into account.";
const PRE_SPECIFIED_TITLE = "The code documents this test as fixed before the data were seen; most of the project was exploratory";
// The default order (status rank, then q), as a sort the URL leaves out.
const DEFAULT_SORT = Object.freeze({ key: "status", direction: "asc" });
const SORT_KEYS = new Set(["family", "statistic", "p", "q", "survives_bh", "clears_t3", "status", "date"]);
const SORT_PARAM = /^([a-z0-9_]+)-(asc|desc)$/;
const isSortParam = (value) => SORT_KEYS.has(SORT_PARAM.exec(value)?.[1]);
// How long a row reached by a #ledg-<id> link stays marked.
const TARGET_MS = 2000;

/** Filters ledger rows by family and status ("all" passes everything), and by a search, when given. */
export const filterLedger = (rows, family, status, query = "") => filterLedgerRows(rows, { family, status, query });

/** How many rows of each value of `key` remain once the other filters are applied. */
const countBy = (rows, key) => rows.reduce((acc, row) => ({ ...acc, [row[key]]: (acc[row[key]] ?? 0) + 1 }), {});

/**
 * A ledger statistic as text, at the precision its own section prints it: a p-value like one (never
 * "0.00"), a count as a whole number, a monthly return spread (%/month) to two decimals as the
 * carry study does, any other percentage to one decimal, and anything else (t, DM, slopes) to two.
 */
export const fmtLedgerStatistic = (row) => {
  const value = num(row?.statistic);
  if (value == null) return DASH;
  const name = asText(row.statistic_name) ?? "";
  if (/p-value/i.test(name)) return fmtPValue(value);
  if (Number.isInteger(value)) return fmtInt(value);
  if (/\/\s*month/i.test(name)) return fmtNum(value, 2);
  if (/%/.test(name)) return fmtNum(value, 1);
  return fmtNum(value, 2);
};

/** A multiplicity flag cell: yes / no, or why the row has none (see ledgerFlagText). */
const flagCell = (row, key) => {
  const text = ledgerFlagText(row, key);
  if (text === "excluded") return <span className="muted" title={AFTER_SEARCH_TITLE}>excluded</span>;
  if (text === "n/a") return <span className="muted" title="Retracted rows keep their adjusted values but are never counted as surviving">n/a</span>;
  return text ?? DASH;
};

/** The hypothesis cell: the claim, its test, and the row's id as a link to itself with a copy button. */
function HypothesisCell({ row, words }) {
  const id = asText(row.id);
  return (
    <>
      <span>{ledgerText(row.hypothesis, words)}</span>
      {asText(row.test) && <span className="ledger-test">{ledgerText(row.test, words)}</span>}
      {id ? (
        <span className="ledger-id-row">
          <a className="ledger-id" href={`#${ledgerAnchor(id)}`}><code>{id}</code></a>
          <CopyButton
            className="ledger-copy"
            glyph="link"
            label={`Copy link to hypothesis ${id}`}
            done="Link copied"
            text={() => `${window.location.origin}${window.location.pathname}#${ledgerAnchor(id)}`}
          />
        </span>
      ) : <code className="ledger-id">{DASH}</code>}
    </>
  );
}

/** LEDG: every hypothesis the project tested, with BH/Holm-adjusted p-values and the t > 3 hurdle. */
export default function LedgerPanel({ research, researchStatus }) {
  const ledger = useMemo(() => researchLedger(research), [research]);
  const words = useMemo(() => {
    const lab = getLab(research);
    return { labels: modelLabels(lab), mcsLevel: mcsLevel(lab) };
  }, [research]);
  const families = useMemo(() => [...new Set((ledger?.rows ?? []).map((row) => row.family).filter(Boolean))].sort(), [ledger]);
  const statuses = useMemo(() => {
    const present = (ledger?.rows ?? []).map((row) => row.status);
    return [
      ...STATUS_ORDER.filter((key) => present.includes(key)),
      ...[...new Set(present)].filter((key) => asText(key) && !STATUS_ORDER.includes(key)),
    ];
  }, [ledger]);

  // The view lives in the URL (q, family, status, sort, ledg=all), so every view is a link.
  const [query, setQuery] = useViewParam("q", null, "");
  const [family, setFamily] = useViewParam("family", ["all", ...families], "all");
  const [status, setStatus] = useViewParam("status", ["all", ...statuses], "all");
  const [sortParam, setSortParam] = useViewParam("sort", isSortParam, "");
  const [rowsParam, setRowsParam] = useViewParam("ledg", ["all"], "");
  // A printout holds every row, whatever the screen shows.
  const [printing, setPrinting] = useState(false);
  useEffect(() => {
    const before = () => flushSync(() => setPrinting(true));
    const after = () => setPrinting(false);
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);
  const showAll = rowsParam === "all" || printing;
  const sort = useMemo(() => {
    const match = SORT_PARAM.exec(sortParam);
    return match ? { key: match[1], direction: match[2] } : DEFAULT_SORT;
  }, [sortParam]);
  const changeSort = useCallback((next) => {
    const isDefault = next.key === DEFAULT_SORT.key && next.direction === DEFAULT_SORT.direction;
    setSortParam(isDefault ? "" : `${next.key}-${next.direction}`);
  }, [setSortParam]);

  const display = useCallback((text) => ledgerText(text, words), [words]);
  const hurdle = ledger?.hurdle ?? 3;
  const columns = useMemo(() => [
    {
      key: "hypothesis", label: "Hypothesis", rowHeader: true, className: "wrap-cell ledger-hypothesis",
      render: (row) => <HypothesisCell row={row} words={words} />,
    },
    { key: "family", label: "Family", sortable: true, className: "muted-cell", render: (row) => humanizeKey(row.family) },
    {
      key: "statistic", label: "Statistic", align: "right", sortable: true,
      render: (row) => (
        <span className="stat-block">
          <span className="num">{fmtLedgerStatistic(row)}</span>
          {asText(row.statistic_name) && <span className="cell-note">{ledgerText(row.statistic_name, words)}</span>}
        </span>
      ),
    },
    {
      key: "p", label: "p", title: "p-value as the test was run (one-sided where the test is one-sided)", align: "right", sortable: true,
      render: (row) => <span className="num">{fmtPValue(row.p)}</span>,
    },
    {
      key: "q", label: "q (BH)", title: "Benjamini-Hochberg q-value across every row in the multiplicity family, on the two-sided p", align: "right", sortable: true,
      render: (row) => (row.selectedAfterSearch
        ? <span className="muted" title={AFTER_SEARCH_TITLE}>{DASH}</span>
        : <span className="num">{fmtPValue(row.q)}</span>),
    },
    {
      key: "survives_bh", label: "BH 5%", align: "center", sortable: true, sortValue: (row) => (row.survives_bh ? 1 : 0), firstDirection: "desc",
      csv: (row) => ledgerFlagText(row, "survives_bh"),
      render: (row) => flagCell(row, "survives_bh"),
    },
    {
      key: "clears_t3", label: `t > ${fmtNum(hurdle, 0)}`, title: "Harvey-Liu-Zhu (2016) hurdle", align: "center", sortable: true,
      sortValue: (row) => (row.clears_t3 ? 1 : 0), firstDirection: "desc",
      csv: (row) => ledgerFlagText(row, "clears_t3"),
      render: (row) => flagCell(row, "clears_t3"),
    },
    {
      key: "status", label: "Status", title: "Sorts by status, best evidence first, then by q (the default order)", sortable: true, className: "status-cell",
      sortValue: ledgerOrderValue, csv: (row) => asText(row.status),
      render: (row) => (asText(row.status) ? (
        <span className="chip-stack">
          <Chip tone={STATUS_TONE[row.status] ?? "muted"} title={STATUS_TITLE[row.status]}>{ledgerStatusLabel(row.status)}</Chip>
          {row.underpowered && <Chip tone="status-warn" title={UNDERPOWERED_TITLE}>underpowered</Chip>}
          {row.selectedAfterSearch && <Chip tone="warn" title={AFTER_SEARCH_TITLE}>selected after search</Chip>}
          {row.pre_specified === true && <Chip tone="status-pending" title={PRE_SPECIFIED_TITLE}>pre-specified</Chip>}
        </span>
      ) : DASH),
    },
    {
      key: "date", label: "Recorded", title: "The date of the artifact that holds the test", sortable: true, firstDirection: "desc",
      className: "muted-cell", render: (row) => fmtDate(row.date),
    },
    // Export-only columns (hidden on screen; the id and test are shown inside the hypothesis cell).
    { key: "id", label: "ID", className: "csv-only", render: () => null },
    { key: "test", label: "Test", className: "csv-only", render: () => null },
  ], [words, hurdle]);

  const filtered = useMemo(
    () => (ledger ? filterLedgerRows(ledger.rows, { family, status, query }, display) : []),
    [ledger, family, status, query, display],
  );
  const ordered = useMemo(() => orderRows(filtered, columns, sort), [filtered, columns, sort]);
  const visible = useMemo(() => visibleLedgerRows(ordered, showAll), [ordered, showAll]);
  const strip = useMemo(() => (ledger ? ledgerStrip(ledger.rows) : null), [ledger]);

  // A #ledg-<id> link (on load, or any later hash change) clears the search and filters, shows every
  // row, and marks the row once it is on screen. A fresh research.json does not repeat a reveal the
  // reader has already seen (and perhaps filtered away from since).
  const [target, setTarget] = useState(null);
  const revealed = useRef(null);
  useEffect(() => {
    const ids = ledger?.byId;
    if (!ids) return undefined;
    const reveal = (force) => {
      const { hash } = window.location;
      if (!force && revealed.current === hash) return;
      const view = revealLedgerView({}, hash, ids);
      if (!view) return;
      revealed.current = hash;
      setViewParam("q", view.query, "");
      setViewParam("family", view.family, "all");
      setViewParam("status", view.status, "all");
      setViewParam("ledg", view.all ? "all" : "", "");
      setTarget({ id: view.target, at: Date.now() });
    };
    const onHash = () => reveal(true);
    reveal(false);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [ledger]);
  useEffect(() => {
    if (!target) return undefined;
    const frame = requestAnimationFrame(() => {
      const row = document.getElementById(ledgerAnchor(target.id));
      if (!row) return;
      // At once, not smoothly: figures mounting along a long smooth scroll would move the row.
      const root = document.documentElement;
      const behavior = root.style.scrollBehavior;
      root.style.scrollBehavior = "auto";
      row.scrollIntoView({ block: "center" });
      root.style.scrollBehavior = behavior;
    });
    const timer = setTimeout(() => setTarget(null), TARGET_MS);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
    };
  }, [target]);

  // Collapsing back to the first rows keeps the button that did it in view: once the rows are gone
  // and before the frame is painted, the page scrolls the button back under the reader's pointer.
  const moreRef = useRef(null);
  const collapsing = useRef(false);
  const toggleAll = () => {
    collapsing.current = showAll;
    setRowsParam(showAll ? "" : "all");
  };
  useLayoutEffect(() => {
    if (!collapsing.current || showAll) return;
    collapsing.current = false;
    const root = document.documentElement;
    const behavior = root.style.scrollBehavior;
    root.style.scrollBehavior = "auto";
    moreRef.current?.scrollIntoView({ block: "center" });
    root.style.scrollBehavior = behavior;
  }, [showAll]);

  if (!ledger) {
    return (
      <Panel id="ledg" code="LEDG" name="Research ledger" title={null} fallbackTitle="Research ledger"
        source={{ artifact: "research.json → ledger", reproduce: REPRODUCE.ledger }}>
        <ResearchUnavailable status={researchStatus} what="the ledger of tested hypotheses" />
      </Panel>
    );
  }

  // Each list counts the rows the other filters leave, so every option says what choosing it shows.
  const familyCounts = countBy(filterLedgerRows(ledger.rows, { status, query }, display), "family");
  const statusCounts = countBy(filterLedgerRows(ledger.rows, { family, query }, display), "status");
  const others = statuses.filter((key) => key !== "supported").map((key) => `${fmtInt(ledger.byStatus?.[key] ?? 0)} ${ledgerStatusLabel(key)}`);
  const total = ledger.rows.length;
  const familySize = num(research?.ledger?.summary?.rows_in_multiplicity_family);
  const clear = () => { setQuery(""); setFamily("all"); setStatus("all"); };
  let shown = `${fmtInt(visible.length)} of ${fmtInt(total)} shown`;
  if (!filtered.length) shown = `none of ${fmtInt(total)} match`;
  else if (filtered.length < total) {
    shown = visible.length === filtered.length
      ? `${fmtInt(filtered.length)} of ${fmtInt(total)} match`
      : `${fmtInt(visible.length)} of ${fmtInt(filtered.length)} matches shown (${fmtInt(total)} in all)`;
  }
  const LedgerStrip = getLedgerStrip();

  return (
    <Panel
      id="ledg"
      code="LEDG"
      name="Research ledger"
      title={ledgerAssertion(ledger)}
      fallbackTitle="Every hypothesis tested"
      meta={[
        `${fmtInt(total)} hypotheses`,
        ledgerSupportText(ledger),
        ...others,
        ledger.underpowered ? `${fmtInt(ledger.underpowered)} underpowered` : null,
        ledger.preSpecified != null ? `${fmtInt(ledger.preSpecified)} pre-specified` : null,
      ]}
      actions={(
        <>
          <input
            type="search"
            className="search-input"
            aria-label="Filter hypotheses"
            placeholder="Filter hypotheses"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape" && query) {
                event.preventDefault();
                setQuery("");
              }
            }}
          />
          <select className="select" aria-label="Family" value={family} onChange={(event) => setFamily(event.target.value)}>
            <option value="all">All families</option>
            {families.map((name) => <option key={name} value={name}>{humanizeKey(name)} ({fmtInt(familyCounts[name] ?? 0)})</option>)}
          </select>
          <select className="select" aria-label="Status" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">All statuses</option>
            {statuses.map((key) => <option key={key} value={key}>{humanizeKey(ledgerStatusLabel(key))} ({fmtInt(statusCounts[key] ?? 0)})</option>)}
          </select>
        </>
      )}
      caption={`Not supported means a failure to reject, not evidence against the hypothesis; q and the t > ${fmtNum(hurdle, 0)} hurdle adjust across ${familySize != null ? `${fmtInt(familySize)} tests` : "the multiplicity family"}.`}
      notes={[
        "Supported: p below 5% with the estimate pointing the way the hypothesis says. Not supported: p of 10% or more, or the "
          + "estimate points the other way. Inconclusive: p between 5% and 10%, or no p-value with the estimate pointing the right way. "
          + "Retracted: the evidence was invalid when produced (a leaked walk-forward, roll-contaminated returns or a wrong null); "
          + "the row is kept as a record and stays in the multiplicity count. A row marked underpowered had too little power for its "
          + "null to say much.",
        `p is the p-value as the test was run, one-sided where the hypothesis is directional. q (Benjamini-Hochberg) and the t > ${fmtNum(hurdle, 0)} `
          + "hurdle use the two-sided p (a one-sided p doubled), across every row except those selected after a search. Each row's test "
          + "line gives its sample and sign convention; DM statistics are positive when the named model has the lower loss, as everywhere "
          + "on the page. Recorded is the date of the artifact that holds the test.",
      ]}
      source={{ artifact: "research.json → ledger", reproduce: REPRODUCE.ledger }}
      resetKey={research}
    >
      {strip?.cells.length > 0 && (
        <LazyMount minHeight={{ wide: 36, narrow: 64 }} label="ledger summary">
          <LedgerStrip strip={strip} />
        </LazyMount>
      )}
      <DataTable
        caption={`Tested hypotheses: ${shown}`}
        rowKey={(row) => String(row.id ?? row.hypothesis)}
        rowId={(row) => (asText(row.id) ? ledgerAnchor(row.id) : undefined)}
        rowClass={(row) => [row.status === "retracted" ? "is-flagged" : null, target && row.id === target.id ? "is-target" : null]
          .filter(Boolean).join(" ") || undefined}
        dense
        stickyFirst={false}
        cardsBelow={640}
        sort={sort}
        onSortChange={changeSort}
        exportName="ledger"
        exportRows={filtered}
        announce="hypotheses"
        totalCount={total}
        empty={(
          <span className="table-empty">
            No hypothesis matches the search and filters.
            <button type="button" className="toggle-button" onClick={clear}>Clear filters</button>
          </span>
        )}
        columns={columns}
        rows={visible}
      />
      {filtered.length > LEDGER_PAGE && (
        <p className="ledger-more">
          <button ref={moreRef} type="button" className="toggle-button ledger-more-button" onClick={toggleAll}>
            {showAll ? `Show the first ${fmtInt(LEDGER_PAGE)}` : `Show all ${fmtInt(filtered.length)} hypotheses`}
          </button>
        </p>
      )}
    </Panel>
  );
}
