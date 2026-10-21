import { useId, useMemo, useState } from "react";
import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import Panel, { REPRODUCE } from "../components/Panel";
import { ledgerAssertion } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtPValue, humanizeKey, num } from "../lib/format";
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

/** Filters ledger rows by family and status ("all" passes everything). */
export const filterLedger = (rows, family, status) =>
  rows.filter((row) => (family === "all" || row.family === family) && (status === "all" || row.status === status));

/** How many rows of each value of `key` remain once the other filter is applied. */
const countBy = (rows, key) => rows.reduce((acc, row) => ({ ...acc, [row[key]]: (acc[row[key]] ?? 0) + 1 }), {});

/**
 * A ledger statistic as text: a statistic that is itself a p-value is printed like one (never
 * "0.00"), a percentage to one decimal (as the studies print it), anything else to two decimals
 * (one at 10 or more).
 */
export const fmtLedgerStatistic = (row) => {
  const value = num(row?.statistic);
  if (value == null) return DASH;
  const name = asText(row.statistic_name) ?? "";
  if (/p-value/i.test(name)) return fmtPValue(value);
  if (/%/.test(name)) return fmtNum(value, 1);
  return fmtNum(value, Math.abs(value) >= 10 ? 1 : 2);
};

/** A multiplicity flag cell: yes / no, or why the row has none. */
const flagCell = (row, key) => {
  if (row.selectedAfterSearch) return <span className="muted" title={AFTER_SEARCH_TITLE}>excluded</span>;
  if (row.status === "retracted") return <span className="muted" title="Retracted rows keep their adjusted values but are never counted as surviving">n/a</span>;
  return row[key] === true ? "yes" : row[key] === false ? "no" : DASH;
};

/** LEDG: every hypothesis the project tested, with BH/Holm-adjusted p-values and the t > 3 hurdle. */
export default function LedgerPanel({ research, researchStatus }) {
  const ledger = useMemo(() => researchLedger(research), [research]);
  const [family, setFamily] = useState("all");
  const [status, setStatus] = useState("all");
  const selectId = useId();
  const rows = useMemo(() => (ledger ? filterLedger(ledger.rows, family, status) : []), [ledger, family, status]);
  const words = useMemo(() => {
    const lab = getLab(research);
    return { labels: modelLabels(lab), mcsLevel: mcsLevel(lab) };
  }, [research]);

  if (!ledger) {
    return (
      <Panel id="ledg" code="LEDG" name="Research ledger" title={null} fallbackTitle="Research ledger"
        source={{ artifact: "research.json → ledger", reproduce: REPRODUCE.ledger }}>
        <ResearchUnavailable status={researchStatus} what="the ledger of tested hypotheses" />
      </Panel>
    );
  }

  const families = [...new Set(ledger.rows.map((row) => row.family).filter(Boolean))].sort();
  const statuses = [
    ...STATUS_ORDER.filter((key) => ledger.rows.some((row) => row.status === key)),
    ...[...new Set(ledger.rows.map((row) => row.status))].filter((key) => asText(key) && !STATUS_ORDER.includes(key)),
  ];
  // Each list counts the rows the other filter leaves, so every option says what choosing it shows.
  const familyCounts = countBy(filterLedger(ledger.rows, "all", status), "family");
  const statusCounts = countBy(filterLedger(ledger.rows, family, "all"), "status");
  const counts = statuses.map((key) => `${fmtInt(ledger.byStatus?.[key] ?? 0)} ${ledgerStatusLabel(key)}`);
  const afterSearch = ledger.rows.filter((row) => row.selectedAfterSearch).length;
  const clear = () => { setFamily("all"); setStatus("all"); };

  return (
    <Panel
      id="ledg"
      code="LEDG"
      name="Research ledger"
      title={ledgerAssertion(ledger)}
      fallbackTitle="Every hypothesis tested"
      meta={[
        `${fmtInt(ledger.rows.length)} hypotheses`,
        ...counts,
        ledger.underpowered ? `${fmtInt(ledger.underpowered)} underpowered` : null,
        afterSearch ? `${fmtInt(afterSearch)} selected after a search, outside the adjustment` : null,
        ledger.preSpecified != null ? `${fmtInt(ledger.preSpecified)} pre-specified` : null,
      ]}
      actions={(
        <>
          <label className="select-label" htmlFor={selectId}>
            <span className="sr-only">Family</span>
            <select id={selectId} className="select" value={family} onChange={(event) => setFamily(event.target.value)}>
              <option value="all">All families</option>
              {families.map((name) => <option key={name} value={name}>{humanizeKey(name)} ({fmtInt(familyCounts[name] ?? 0)})</option>)}
            </select>
          </label>
          <label className="select-label" htmlFor={`${selectId}-status`}>
            <span className="sr-only">Status</span>
            <select id={`${selectId}-status`} className="select" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="all">All statuses</option>
              {statuses.map((key) => <option key={key} value={key}>{humanizeKey(ledgerStatusLabel(key))} ({fmtInt(statusCounts[key] ?? 0)})</option>)}
            </select>
          </label>
        </>
      )}
      caption={(
        <>
          Supported: p below 5% with the estimate pointing the way the hypothesis says. Not supported: p of 10% or more, or the
          estimate points the other way; that is a failure to reject, not evidence against the hypothesis, and a row marked
          underpowered had too little power for its null to say much. p is the p-value as the test was run, one-sided where
          the hypothesis is directional. q (Benjamini-Hochberg) and the t &gt; {fmtNum(ledger.hurdle ?? 3, 0)} hurdle use the
          two-sided p (a one-sided p doubled), across every row except those selected after a search. Each row&apos;s test
          line gives its sample and sign convention; DM statistics are positive when the named model has the lower loss, as
          everywhere on the page.
        </>
      )}
      source={{ artifact: "research.json → ledger", reproduce: REPRODUCE.ledger }}
      resetKey={research}
    >
      <DataTable
        caption={`Tested hypotheses: ${fmtInt(rows.length)} of ${fmtInt(ledger.rows.length)} shown`}
        rowKey={(row) => String(row.id ?? row.hypothesis)}
        rowClass={(row) => (row.status === "retracted" ? "is-flagged" : undefined)}
        dense
        tall
        stickyFirst={false}
        initialSort={{ key: "q", direction: "asc" }}
        empty={(
          <span className="table-empty">
            No hypothesis matches both filters.
            <button type="button" className="toggle-button" onClick={clear}>Clear filters</button>
          </span>
        )}
        columns={[
          {
            key: "hypothesis", label: "Hypothesis", rowHeader: true, className: "wrap-cell ledger-hypothesis",
            render: (row) => (
              <>
                <span>{ledgerText(row.hypothesis, words)}</span>
                {asText(row.test) && <span className="ledger-test">{ledgerText(row.test, words)}</span>}
                <code className="ledger-id">{row.id ?? DASH}</code>
              </>
            ),
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
            render: (row) => flagCell(row, "survives_bh"),
          },
          {
            key: "clears_t3", label: `t > ${fmtNum(ledger.hurdle ?? 3, 0)}`, title: "Harvey-Liu-Zhu (2016) hurdle", align: "center", sortable: true,
            sortValue: (row) => (row.clears_t3 ? 1 : 0), firstDirection: "desc",
            render: (row) => flagCell(row, "clears_t3"),
          },
          {
            key: "status", label: "Status", sortable: true, className: "status-cell",
            render: (row) => (asText(row.status) ? (
              <span className="chip-stack">
                <Chip tone={STATUS_TONE[row.status] ?? "muted"} title={STATUS_TITLE[row.status]}>{ledgerStatusLabel(row.status)}</Chip>
                {row.underpowered && <Chip tone="status-warn" title={UNDERPOWERED_TITLE}>underpowered</Chip>}
                {row.selectedAfterSearch && <Chip tone="warn" title={AFTER_SEARCH_TITLE}>selected after search</Chip>}
              </span>
            ) : DASH),
          },
          { key: "date", label: "Date", sortable: true, className: "muted-cell", render: (row) => fmtDate(row.date) },
        ]}
        rows={rows}
      />
    </Panel>
  );
}
