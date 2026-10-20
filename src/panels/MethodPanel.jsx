import DataTable from "../components/DataTable";
import Panel, { REPRODUCE_COMMAND } from "../components/Panel";
import { methodAssertion } from "../lib/assertions";
import { DASH, fmtCT, fmtDate, fmtInt, fmtNum, fmtShare, num, shortHash, toMs } from "../lib/format";
import { asArray, asObject, asText, getLab, getVol } from "../lib/research";
import { Facts, ResearchUnavailable } from "./common";

/** The methods behind the page, with the paper each comes from (references, not statistics). */
export const REFERENCES = [
  { method: "HAR model of realized volatility", ref: "Corsi (2009), J. Financial Econometrics" },
  { method: "QLIKE loss: consistent ranking with a noisy volatility proxy", ref: "Patton (2011), J. Econometrics" },
  { method: "Model Confidence Set", ref: "Hansen, Lunde & Nason (2011), Econometrica" },
  { method: "Superior Predictive Ability test", ref: "Hansen (2005), J. Business & Economic Statistics" },
  { method: "Diebold-Mariano test of equal predictive accuracy", ref: "Diebold & Mariano (1995), JBES" },
  { method: "Adaptive conformal inference for the interval", ref: "Gibbs & Candès (2021), NeurIPS" },
  { method: "Smearing retransformation of log forecasts", ref: "Duan (1983), JASA" },
  { method: "VaR unconditional coverage", ref: "Kupiec (1995), J. Derivatives" },
  { method: "VaR independence and conditional coverage", ref: "Christoffersen (1998), Int. Economic Review" },
  { method: "Dynamic quantile test", ref: "Engle & Manganelli (2004), JBES" },
  { method: "Realized utility of volatility forecasts", ref: "Bollerslev, Hood, Huss & Pedersen (2018), RFS" },
];

/** METH: data provenance, timing, the purge, the statistics used, and how to reproduce. */
export default function MethodPanel({ data, research, researchStatus }) {
  const lab = getLab(research);
  const generated = asObject(lab?.generated_from);
  const files = Object.entries(asObject(generated?.files) ?? {}).map(([key, file]) => ({ key, ...asObject(file) }));
  const quality = asObject(generated?.cl_data_quality);
  const agreement = asObject(lab?.ovx_source_agreement);
  const config = asObject(lab?.config) ?? {};
  const mcs = asObject(config.mcs);
  const conformal = asObject(config.conformal);
  const coefficients = asObject(lab?.har_iv_coefficients);
  const { validation, live } = getVol(data);
  const lag = num(config.ovx_lag_sessions) ?? num(validation?.ovx_lag_sessions);
  const retrievedMs = toMs(generated?.retrieved_at);

  const timing = [
    { label: "Target", value: num(config.horizon_sessions) != null ? `realized vol over the next ${config.horizon_sessions} sessions` : null },
    { label: "OVX timing", value: lag != null ? `lagged ${lag} session${lag === 1 ? "" : "s"}` : null, note: asText(live?.inputs?.ovx_source) },
    { label: "Walk-forward step", value: num(config.step) != null ? `${config.step} sessions` : null },
    { label: "Minimum training rows", value: num(config.min_train_rows) != null ? fmtInt(config.min_train_rows) : null },
    { label: "Purge", value: num(config.purge_rows) != null ? `${config.purge_rows} rows before each forecast` : null, note: "labels overlapping the target are dropped" },
    { label: "Scoring", value: asText(config.evaluation) },
    { label: "Forecast used", value: asText(config.forecasts) ?? asText(validation?.forecast_used_for_losses) },
    { label: "MCS", value: mcs ? `${mcs.statistic ?? DASH} statistic, ${mcs.bootstrap ?? DASH} bootstrap, ${fmtInt(mcs.reps)} reps` : null, note: mcs?.seed != null ? `seed ${mcs.seed}` : null },
    { label: "Interval", value: asText(conformal?.method) },
  ];

  return (
    <Panel
      id="meth"
      code="METH"
      name="Methodology"
      title={methodAssertion(lab)}
      fallbackTitle="Methodology and reproducibility"
      meta={[
        retrievedMs != null ? `data retrieved ${fmtCT(retrievedMs)}` : null,
        typeof generated?.sessions_through === "string" ? `sessions through ${fmtDate(generated.sessions_through)}` : null,
        lab?.sample?.n_days != null ? `${fmtInt(lab.sample.n_days)} out-of-sample days` : null,
      ]}
      source={{ artifact: ["research.json → vol_lab.generated_from", "vol_lab.config"] }}
      resetKey={research}
    >
      <div className="method-grid">
        <div className="method-block method-block--wide">
          <h3 className="figure-title">Data sources</h3>
          {files.length ? (
            <DataTable
              caption="Source files with SHA-256 digests"
              captionHidden
              rowKey={(row) => row.key}
              dense
              columns={[
                { key: "file", label: "File", rowHeader: true, render: (row) => <code>{row.file ?? row.key}</code> },
                { key: "source", label: "Source", className: "wrap-cell" },
                { key: "rows", label: "Rows", align: "right", render: (row) => fmtInt(row.rows) },
                { key: "span", label: "Span", render: (row) => `${row.first ?? DASH} – ${row.last ?? DASH}` },
                { key: "sha256", label: "SHA-256", render: (row) => <code className="hash" title={row.sha256}>{shortHash(row.sha256)}…</code> },
              ]}
              rows={files}
            />
          ) : <ResearchUnavailable status={researchStatus} what="the data manifest" />}
          <Facts items={[
            quality && { label: "CL sessions", value: fmtInt(quality.sessions), note: [
              num(quality.range_blanked_nonpositive_bars) ? `${quality.range_blanked_nonpositive_bars} non-positive bar blanked` : null,
              num(quality.repaired_high_below_open_or_close) ? `${quality.repaired_high_below_open_or_close} highs repaired` : null,
              num(quality.repaired_low_above_open_or_close) ? `${quality.repaired_low_above_open_or_close} lows repaired` : null,
            ].filter(Boolean).join(", ") || null },
            agreement && { label: "OVX: Yahoo vs FRED", value: `corr ${fmtNum(agreement.correlation, 6)}`, note: `${fmtShare(agreement.share_within_0_05_pts, 2)} of ${fmtInt(agreement.common_days)} days within 0.05 pts; max gap ${fmtNum(agreement.max_abs_diff_pts, 2)}` },
          ]} />
        </div>

        <div className="method-block">
          <h3 className="figure-title">Timing and evaluation</h3>
          <Facts className="facts--stack" items={timing} />
        </div>

        {asArray(coefficients?.terms).length > 0 && (
          <div className="method-block">
            <h3 className="figure-title">HAR-IV coefficients (log scale)</h3>
            <DataTable
              caption={`HAR-IV regression: R² ${fmtNum(coefficients.r2_log, 3)} on ${fmtInt(coefficients.n_rows)} rows, HAC ${fmtInt(coefficients.hac_lags)} lags`}
              rowKey={(row) => row.term}
              dense
              columns={[
                { key: "term", label: "Term", rowHeader: true, render: (row) => <code>{row.term}</code> },
                { key: "coef", label: "Coefficient", align: "right", render: (row) => <span className="num">{fmtNum(row.coef, 4)}</span> },
                { key: "t_hac", label: "t (HAC)", align: "right", render: (row) => <span className="num">{fmtNum(row.t_hac, 2)}</span> },
              ]}
              rows={asArray(coefficients.terms).filter((row) => asText(row?.term))}
            />
          </div>
        )}

        <div className="method-block">
          <h3 className="figure-title">Statistics used</h3>
          <ul className="reference-list">
            {REFERENCES.map((item) => (
              <li key={item.method}><span>{item.method}</span><span className="muted">{item.ref}</span></li>
            ))}
          </ul>
        </div>

        <div className="method-block">
          <h3 className="figure-title">Reproduce</h3>
          <pre className="code-block"><code>{[
            "pip install -r requirements.txt",
            `${REPRODUCE_COMMAND}            # rebuilds data/vol_lab.json`,
            "python scripts/build_research_bundle.py  # copies committed artifacts into research.json",
          ].join("\n")}</code></pre>
          <p className="prose-note is-muted">
            Inputs are pinned by the SHA-256 digests above and the bootstrap is seeded, so a rerun on the
            same files should reproduce the research figures on this page. Limitations: close-to-close and daily-range measures stand in
            for intraday realized variance, OVX is an index on options on an ETF rather than on CL itself,
            and the live record is short.
          </p>
        </div>
      </div>
    </Panel>
  );
}
