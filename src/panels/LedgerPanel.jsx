import { useId, useMemo, useState } from "react";
import Chip from "../components/Chip";
import DataTable from "../components/DataTable";
import Panel from "../components/Panel";
import { ledgerAssertion } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtNum, fmtPValue, humanizeKey } from "../lib/format";
import { asText, researchLedger } from "../lib/research";
import { ResearchUnavailable } from "./common";

const STATUS_TONE = { supported: "status-resolved", rejected: "muted", retracted: "warn", inconclusive: "status-pending" };
const STATUS_ORDER = ["supported", "rejected", "retracted", "inconclusive"];

/** Filters ledger rows by family and status ("all" passes everything). */
export const filterLedger = (rows, family, status) =>
  rows.filter((row) => (family === "all" || row.family === family) && (status === "all" || row.status === status));

/** LEDG: every hypothesis the project tested, with BH/Holm-adjusted p-values and the t > 3 hurdle. */
export default function LedgerPanel({ research, researchStatus }) {
  const ledger = useMemo(() => researchLedger(research), [research]);
  const [family, setFamily] = useState("all");
  const [status, setStatus] = useState("all");
  const selectId = useId();
  const rows = useMemo(() => (ledger ? filterLedger(ledger.rows, family, status) : []), [ledger, family, status]);

  if (!ledger) {
    return (
      <Panel id="ledg" code="LEDG" name="Research ledger" title={null} fallbackTitle="Research ledger"
        source={{ artifact: "research.json → ledger", reproduce: null }}>
        <ResearchUnavailable status={researchStatus === "ready" ? "unavailable" : researchStatus} what="the ledger of tested hypotheses" />
      </Panel>
    );
  }

  const families = [...new Set(ledger.rows.map((row) => row.family).filter(Boolean))].sort();
  const statuses = STATUS_ORDER.filter((key) => ledger.rows.some((row) => row.status === key));
  const counts = Object.entries(ledger.byStatus ?? {}).map(([key, value]) => `${fmtInt(value)} ${key}`);

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
        ledger.preSpecified != null ? `${fmtInt(ledger.preSpecified)} pre-specified` : null,
      ]}
      actions={(
        <>
          <label className="select-label" htmlFor={selectId}>
            <span className="sr-only">Family</span>
            <select id={selectId} className="select" value={family} onChange={(event) => setFamily(event.target.value)}>
              <option value="all">All families</option>
              {families.map((name) => <option key={name} value={name}>{humanizeKey(name)}</option>)}
            </select>
          </label>
          <label className="select-label" htmlFor={`${selectId}-status`}>
            <span className="sr-only">Status</span>
            <select id={`${selectId}-status`} className="select" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="all">All statuses</option>
              {statuses.map((key) => <option key={key} value={key}>{key} ({fmtInt(ledger.byStatus?.[key] ?? 0)})</option>)}
            </select>
          </label>
        </>
      )}
      caption={ledger.method ? ledger.method.split(/(?<=\.)\s/).slice(0, 2).join(" ") : null}
      source={{ artifact: "research.json → ledger", reproduce: null }}
      resetKey={research}
    >
      <DataTable
        caption={`Tested hypotheses: ${fmtInt(rows.length)} of ${fmtInt(ledger.rows.length)} shown`}
        rowKey={(row) => String(row.id ?? row.hypothesis)}
        rowClass={(row) => (row.status === "retracted" ? "is-flagged" : undefined)}
        dense
        tall
        initialSort={{ key: "q", direction: "asc" }}
        columns={[
          { key: "id", label: "ID", rowHeader: true, sortable: true, render: (row) => <code className="ledger-id">{row.id ?? DASH}</code> },
          { key: "hypothesis", label: "Hypothesis", className: "wrap-cell ledger-hypothesis", render: (row) => <span title={row.test ?? undefined}>{row.hypothesis}</span> },
          { key: "family", label: "Family", sortable: true, className: "muted-cell" },
          {
            key: "statistic", label: "Statistic", align: "right", sortable: true,
            render: (row) => <span className="num" title={asText(row.statistic_name) ?? undefined}>{fmtNum(row.statistic, Math.abs(row.statistic ?? 0) >= 10 ? 1 : 2)}</span>,
          },
          { key: "p", label: "p", align: "right", sortable: true, render: (row) => <span className="num">{fmtPValue(row.p)}</span> },
          { key: "q", label: "q (BH)", title: "Benjamini-Hochberg q-value across every row", align: "right", sortable: true, render: (row) => <span className="num">{fmtPValue(row.q)}</span> },
          {
            key: "survives_bh", label: "BH 5%", align: "center", sortable: true, sortValue: (row) => (row.survives_bh ? 1 : 0), firstDirection: "desc",
            render: (row) => (row.survives_bh === true ? "yes" : row.survives_bh === false ? "no" : DASH),
          },
          {
            key: "clears_t3", label: `t > ${fmtNum(ledger.hurdle ?? 3, 0)}`, title: "Harvey-Liu-Zhu (2016) hurdle", align: "center", sortable: true,
            sortValue: (row) => (row.clears_t3 ? 1 : 0), firstDirection: "desc",
            render: (row) => (row.clears_t3 === true ? "yes" : row.clears_t3 === false ? "no" : DASH),
          },
          {
            key: "status", label: "Status", sortable: true,
            render: (row) => (asText(row.status) ? <Chip tone={STATUS_TONE[row.status] ?? "muted"}>{row.status}</Chip> : DASH),
          },
          { key: "date", label: "Date", sortable: true, className: "muted-cell", render: (row) => fmtDate(row.date) },
        ]}
        rows={rows}
      />
    </Panel>
  );
}
