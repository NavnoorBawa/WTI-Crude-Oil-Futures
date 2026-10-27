import citationSource from "../../CITATION.cff?raw";
import { DATA_HISTORY_URL, DOCS, GIT_SHA, RECORD_HISTORY_URL, REPO_URL, RESEARCH_URL, STATIC_DATA_URL } from "../app/config";
import { SECTIONS } from "../app/sections";
import { getElasticityPath } from "../charts/lazy";
import CopyButton from "../components/CopyButton";
import DataTable from "../components/DataTable";
import LazyMount from "../components/LazyMount";
import Panel, { REPRODUCE } from "../components/Panel";
import { methodAssertion, methodDetail, promotionText } from "../lib/assertions";
import { DASH, fmtCT, fmtDate, fmtInt, fmtNum, fmtShare, num, tidyProse, toMs } from "../lib/format";
import {
  METH_APPENDIX, citationBibtex, citationText, elasticityAssertion, elasticityPath, ledgerAnchor, liveFit, modelCard,
  parseCitation, retractedRows, timingCorrection, timingCorrectionText, vintageRows,
} from "../lib/reference";
import { asArray, asObject, asText, getLab, getVol, ledgerText, liveRecordSummary, modelLabels, researchLedger, supplyStudy } from "../lib/research";
import { snapshotDate } from "../lib/csv";
import { Facts, ResearchUnavailable } from "./common";

/**
 * The methods behind the page, with the paper each comes from (references, not statistics). Each
 * row's id is the anchor the glossary's "Reference" links point to (src/lib/glossary.js refs).
 */
export const REFERENCES = [
  { id: "stat-har", method: "HAR model of realized volatility", ref: "Corsi (2009), J. Financial Econometrics" },
  { id: "stat-qlike", method: "QLIKE loss: consistent ranking with a noisy volatility proxy", ref: "Patton (2011), J. Econometrics" },
  { id: "stat-mcs", method: "Model Confidence Set", ref: "Hansen, Lunde & Nason (2011), Econometrica" },
  { id: "stat-spa", method: "Superior Predictive Ability test", ref: "Hansen (2005), J. Business & Economic Statistics" },
  { id: "stat-dm", method: "Diebold-Mariano test of equal predictive accuracy", ref: "Diebold & Mariano (1995), JBES" },
  { id: "stat-hln", method: "Small-sample correction of the Diebold-Mariano test (the live promotion test)", ref: "Harvey, Leybourne & Newbold (1997), Int. J. Forecasting" },
  { id: "stat-mz", method: "Mincer-Zarnowitz regression of the realized value on the forecast", ref: "Mincer & Zarnowitz (1969), Economic Forecasts and Expectations, NBER" },
  { id: "stat-conformal", method: "Adaptive conformal inference for the interval", ref: "Gibbs & Candès (2021), NeurIPS" },
  { id: "stat-smearing", method: "Smearing retransformation of log forecasts", ref: "Duan (1983), JASA" },
  { id: "stat-kupiec", method: "VaR unconditional coverage", ref: "Kupiec (1995), J. Derivatives" },
  { id: "stat-christoffersen", method: "VaR independence and conditional coverage", ref: "Christoffersen (1998), Int. Economic Review" },
  { id: "stat-dq", method: "Dynamic quantile test", ref: "Engle & Manganelli (2004), JBES" },
  { id: "stat-utility", method: "Realized utility of volatility forecasts", ref: "Bollerslev, Hood, Huss & Pedersen (2018), RFS" },
  { id: "stat-bh", method: "False discovery rate across the ledger's tests", ref: "Benjamini & Hochberg (1995), J. Royal Statistical Society B" },
  { id: "stat-hlz", method: "The t > 3 hurdle for findings chosen from many", ref: "Harvey, Liu & Zhu (2016), Review of Financial Studies" },
];

// Each artifact on the page and the command that rebuilds it, in the order the README runs them.
const REPRODUCE_STEPS = [
  ["pip install -r requirements.txt", null],
  [REPRODUCE.lab, "data/vol_lab.json, offline"],
  [REPRODUCE.validation, "production, downloads data"],
  [REPRODUCE.walkForward, "post-mortem, downloads data"],
  [REPRODUCE.leakAnatomy, "data/leak_anatomy.json"],
  [REPRODUCE.carry, "offline, committed EIA curve"],
  [REPRODUCE.supply, "offline, committed EIA spot"],
  [REPRODUCE.ledger, "re-reads every artifact"],
  ["python scripts/build_research_bundle.py", "assembles research.json"],
];
const reproduceLines = () => {
  const width = Math.max(...REPRODUCE_STEPS.map(([command]) => command.length)) + 2;
  return REPRODUCE_STEPS.map(([command, note]) => (note ? `${command.padEnd(width)}# ${note}` : command)).join("\n");
};

const APPENDIX = METH_APPENDIX;

const CITATION = parseCitation(citationSource);
const SECTION_CODE = new Map(SECTIONS.map((section) => [section.id, section.code]));

/** A link to a section, as its code chip. */
function SectionLink({ id }) {
  const code = SECTION_CODE.get(id);
  return code ? <a className="chip chip--code chip-link" href={`#${id}`}>{code}</a> : null;
}

/** An external link that opens in a new tab and says so to a screen reader. */
function OutLink({ href, children }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}<span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

/** One label/value row of the model card; a row without a value is left out. */
function CardRow({ label, children }) {
  if (children == null || children === false) return null;
  return (
    <div className="card-row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** The model of record on one card: use, specification, estimation, evaluation, limits, version. */
function ModelCard({ data, research }) {
  const card = modelCard(data, research);
  const { liveRecord } = getVol(data);
  const promotion = liveRecordSummary(liveRecord)?.promotion ?? null;
  const steps = [
    card.minRows != null ? `at least ${fmtInt(card.minRows)} rows before the first out-of-sample forecast` : null,
    card.purge != null ? `the ${fmtInt(card.purge)} rows whose target overlaps the forecast's are dropped (the purge)` : null,
  ].filter(Boolean);
  return (
    <div className="method-block method-block--wide" id="meth-model-card">
      <h3 className="study-title" tabIndex={-1}>Model card · {card.name}{card.version ? ` (${card.version})` : ""}</h3>
      <dl className="model-card">
        <CardRow label="Intended use">
          The level of realized volatility over the next {card.horizon != null ? `${card.horizon} sessions` : "week"}, for risk sizing
        </CardRow>
        <CardRow label="Not intended for">
          Price direction: {card.direction
            ? <>direction accuracy {fmtNum(card.direction.accuracy, 1)}% on every out-of-sample day (benchmark lab), against a {fmtNum(card.direction.floor, 1)}% noise floor on simulated returns with nothing to forecast</>
            : "direction accuracy is not evidence of skill"} (see <SectionLink id="modl" />)
        </CardRow>
        <CardRow label="Specification"><code className="model-equation">{card.equation}</code>, OLS</CardRow>
        <CardRow label="Estimation">
          An expanding window of every matured row{steps.length ? `: ${steps.join("; ")}` : ""}. Refit for every forecast: each live
          call, each out-of-sample day in the benchmark lab{card.step != null ? `, each ${card.step}-session test point in the weekly validation` : ""}.
        </CardRow>
        <CardRow label="Target">
          Close-to-close realized vol over the next {card.horizon != null ? `${card.horizon} sessions` : "week"}: the root mean square of the
          daily log returns, annualized
        </CardRow>
        <CardRow label="Mean forecast">
          Median × √smearing (Duan){card.mean
            ? <>: <span className="num">{fmtNum(card.mean.median, 1)}% × √{fmtNum(card.mean.smearing, 3)} ≈ {fmtNum(card.mean.mean, 1)}%</span> for this call</>
            : ""}. The median decides the direction call and the interval.
        </CardRow>
        <CardRow label="Inputs and timing">
          RV5, RV22 and RV66 from CL=F settlements; OVX{card.lag != null ? ` lagged ${card.lag} session${card.lag === 1 ? "" : "s"}` : ""}, because
          it settles after the CL settlement that opens the target window{card.ovxSource ? ` (source: ${card.ovxSource})` : ""}
        </CardRow>
        {card.evaluation.length > 0 && (
          <CardRow label="Evaluation">
            <span className="card-note">Benchmark lab, every out-of-sample day{card.sampleDays != null ? ` (n = ${fmtInt(card.sampleDays)})` : ""}; DM is positive when the named model has the lower loss</span>
            <ul className="card-lines">
              {card.evaluation.map((item) => (
                <li key={item.key}><span className="card-line-label">{item.label}:</span> {item.text} <SectionLink id={item.href.slice(1)} /></li>
              ))}
            </ul>
          </CardRow>
        )}
        <CardRow label="Known limitations">
          Close-to-close and daily-range measures stand in for intraday realized variance, OVX is an index on options on an ETF rather than on
          CL itself, and the live record is short.
        </CardRow>
        <CardRow label="Version">
          {card.version ?? DASH}; earlier versions and what changed are under <a href={`#${APPENDIX.corrections}`}>Version history</a>
        </CardRow>
        {card.challenger && (
          <CardRow label="Challenger">
            {card.challenger}{promotion ? `; promotion test ${promotionText(promotion)}` : ""} (see <SectionLink id="live" />)
          </CardRow>
        )}
        <CardRow label="Documents">
          <span className="link-list">
            <OutLink href={DOCS.modelCard}>Model card</OutLink>
            <OutLink href={DOCS.methodology}>Methodology</OutLink>
          </span>
        </CardRow>
      </dl>
    </div>
  );
}

/** The live fit beside the lab's full-sample fit, each captioned with its sample. */
function Coefficients({ data, lab }) {
  const { live } = getVol(data);
  const fit = liveFit(live, lab);
  const full = asObject(lab?.har_iv_coefficients);
  const fullRows = asArray(full?.terms).filter((row) => asText(row?.term));
  if (!fit && !fullRows.length) return null;
  const reproduced = fit?.median != null && fit.published != null && fmtNum(fit.median, 1) === fmtNum(fit.published, 1);
  const through = asText(lab?.generated_from?.sessions_through);
  return (
    <div className="method-block method-block--wide">
      <h3 className="study-title">
        {reproduced
          ? `The live coefficients reproduce this call's ${fmtNum(fit.published, 1)}% median`
          : "HAR-IV coefficients: the live fit and the full sample"}
      </h3>
      <div className="split coef-split">
        {fit && (
          <div className="split-figure">
            <DataTable
              caption={`Live fit, refit for the call as of ${fmtDate(fit.asOf)} on every matured row (data.json)`}
              rowKey={(row) => row.term}
              dense
              exportName="live-coefficients"
              columns={[
                { key: "term", label: "Term", rowHeader: true, render: (row) => <code>{row.term}</code> },
                { key: "coef", label: "Coefficient", align: "right", render: (row) => <span className="num">{fmtNum(row.coef, 4)}</span> },
                {
                  key: "input", label: "Input x", title: "This call's input: realized vols as decimals, OVX in index points", align: "right",
                  render: (row) => (row.input == null ? <span className="muted">{DASH}</span>
                    : <span className="num">{fmtNum(row.input, row.inputUnit === "decimal" ? 4 : 2)}</span>),
                },
              ]}
              rows={fit.rows}
              footnote={fit.median != null
                ? `exp(b0 + Σ b·ln x) = ${fmtNum(fit.median, 1)}%${fit.published != null ? `; the published median is ${fmtNum(fit.published, 1)}%` : ""}. Realized vols enter as decimals, OVX in points.`
                : null}
            />
          </div>
        )}
        {fullRows.length > 0 && (
          <div className="split-side">
            <DataTable
              caption={`Full sample, in-sample${through ? ` through ${fmtDate(through)}` : ""}: R² ${fmtNum(full.r2_log, 3)} on log realized vol, ${fmtInt(full.n_rows)} rows, HAC ${fmtInt(full.hac_lags)} lags (research.json)`}
              rowKey={(row) => row.term}
              dense
              exportName="full-sample-coefficients"
              columns={[
                { key: "term", label: "Term", rowHeader: true, render: (row) => <code>{row.term}</code> },
                { key: "coef", label: "Coefficient", align: "right", render: (row) => <span className="num">{fmtNum(row.coef, 4)}</span> },
                { key: "t_hac", label: "t (HAC)", align: "right", render: (row) => <span className="num">{fmtNum(row.t_hac, 2)}</span> },
              ]}
              rows={fullRows}
            />
          </div>
        )}
      </div>
    </div>
  );
}

/** The OVX and realized-vol coefficients at each walk-forward refit. */
function Elasticity({ lab }) {
  const path = elasticityPath(lab);
  if (!path) return null;
  const Chart = getElasticityPath();
  return (
    <div className="method-block method-block--wide">
      <h3 className="study-title">{elasticityAssertion(path)}</h3>
      <LazyMount minHeight={220} label="coefficient path">
        <Chart path={path} />
      </LazyMount>
      <p className="prose-note is-muted">
        The OVX coefficient (b4) and the summed realized-vol coefficients (b1 + b2 + b3) of each weekly walk-forward refit. In a log-log
        model a coefficient is an elasticity: at 1 the forecast moves one-for-one, in percent, with OVX.
      </p>
    </div>
  );
}

/** Every as-of date on the page, in one date format. */
function Vintage({ data, research }) {
  const rows = vintageRows(data, research);
  if (!rows.length) return null;
  const when = (value) => {
    if (!value) return DASH;
    return /T\d{2}:\d{2}/.test(value) ? fmtCT(toMs(value), { year: true }) : fmtDate(value.slice(0, 10));
  };
  return (
    <div className="method-block method-block--wide" id="meth-vintage">
      <h3 className="study-title">Data vintage: every as-of date on the page</h3>
      <DataTable
        caption="Each artifact the page reads, the date its numbers run to, when it was produced, and whether its inputs are pinned by the SHA-256 digests below"
        rowKey={(row) => row.key}
        dense
        exportName="data-vintage"
        cardsBelow={640}
        columns={[
          {
            key: "artifact", label: "Artifact", rowHeader: true,
            render: (row) => <span className="vintage-artifact">{row.artifact}{row.section && <SectionLink id={row.section} />}</span>,
          },
          { key: "asOf", label: "As of", render: (row) => when(row.asOf) },
          { key: "refreshed", label: "Refreshed / issued", render: (row) => when(row.refreshed) },
          { key: "pinned", label: "Pinned", align: "center", csv: (row) => (row.pinned ? "yes" : "no"), render: (row) => (row.pinned ? "yes" : "no") },
        ]}
        rows={rows}
      />
    </div>
  );
}

/** The data manifest: each pinned file with its full SHA-256 digest (truncated on screen, copied whole). */
function Sources({ lab, researchStatus }) {
  const generated = asObject(lab?.generated_from);
  const files = Object.entries(asObject(generated?.files) ?? {}).map(([key, file]) => ({ key, ...asObject(file) }));
  const quality = asObject(generated?.cl_data_quality);
  const agreement = asObject(lab?.ovx_source_agreement);
  return (
    <div className="method-block method-block--wide" id="meth-sources">
      <h3 className="study-title">Data sources</h3>
      {files.length ? (
        <DataTable
          caption="Source files with SHA-256 digests"
          captionHidden
          rowKey={(row) => row.key}
          dense
          cardsBelow={640}
          exportName="data-manifest"
          columns={[
            { key: "file", label: "File", rowHeader: true, render: (row) => <code>{row.file ?? row.key}</code> },
            { key: "source", label: "Source", className: "wrap-cell" },
            { key: "rows", label: "Rows", align: "right", render: (row) => fmtInt(row.rows) },
            { key: "span", label: "Span", csv: (row) => `${row.first ?? ""} – ${row.last ?? ""}`, render: (row) => `${row.first ?? DASH} – ${row.last ?? DASH}` },
            {
              key: "sha256", label: "SHA-256",
              render: (row) => (asText(row.sha256) ? (
                <span className="hash-cell">
                  <code className="hash" title={row.sha256}>{row.sha256}</code>
                  <CopyButton label={`Copy the SHA-256 digest of ${row.file ?? row.key}`} text={row.sha256} done="Digest copied" />
                </span>
              ) : DASH),
            },
          ]}
          rows={files}
        />
      ) : <ResearchUnavailable status={researchStatus} what="the data manifest" />}
      <p className="prose-note is-muted">
        {files.length ? "The digests pin the benchmark lab's inputs. " : ""}The production validation and the direction-model post-mortem are
        committed records of runs on data downloaded at the time; the carry and supply-shock studies read committed EIA files.
      </p>
      <Facts items={[
        quality && { label: "CL sessions", value: fmtInt(quality.sessions), note: [
          num(quality.range_blanked_nonpositive_bars) ? `${quality.range_blanked_nonpositive_bars} non-positive bar blanked` : null,
          num(quality.repaired_high_below_open_or_close) ? `${quality.repaired_high_below_open_or_close} highs repaired` : null,
          num(quality.repaired_low_above_open_or_close) ? `${quality.repaired_low_above_open_or_close} lows repaired` : null,
        ].filter(Boolean).join(", ") || null },
        agreement && { label: "OVX: Yahoo vs FRED", value: `corr ${fmtNum(agreement.correlation, 6)}`, note: `${fmtShare(agreement.share_within_0_05_pts, 2)} of ${fmtInt(agreement.common_days)} days within 0.05 pts; max gap ${fmtNum(agreement.max_abs_diff_pts, 2)}` },
      ]} />
    </div>
  );
}

/** Cite this page: a plain citation and BibTeX from CITATION.cff, with the snapshot and the build. */
function Cite({ data }) {
  const snapshot = typeof data?.frozen_at === "string" ? snapshotDate(data.frozen_at) : null;
  const build = GIT_SHA ? GIT_SHA.slice(0, 7) : null;
  const plain = citationText(CITATION, { snapshot, build });
  const bibtex = citationBibtex(CITATION, { snapshot, build });
  return (
    <div className="method-block appendix-block" id={APPENDIX.cite}>
      <h3 className="study-title" tabIndex={-1}>Cite this page</h3>
      {plain ? (
        <>
          <p className="cite-text">
            {plain}
            <CopyButton label="Copy the citation" text={plain} done="Citation copied" />
          </p>
          <div className="code-copy">
            <pre className="code-block"><code>{bibtex}</code></pre>
            <CopyButton label="Copy the BibTeX entry" text={bibtex} done="BibTeX copied" />
          </div>
        </>
      ) : <p className="prose-note is-muted">The citation file could not be read.</p>}
      <dl className="cite-facts">
        <div>
          <dt>Snapshot</dt>
          <dd>
            {snapshot ?? DASH}
            {snapshot && <CopyButton label="Copy the snapshot date" text={snapshot} done="Snapshot date copied" />}
          </dd>
        </div>
        <div>
          <dt>Build</dt>
          <dd>
            {build ? <OutLink href={`${REPO_URL}/commit/${GIT_SHA}`}><code>{build}</code></OutLink> : <code>local</code>}
            {build && <CopyButton label="Copy the build commit" text={GIT_SHA} done="Build commit copied" />}
          </dd>
        </div>
      </dl>
    </div>
  );
}

/** Version history and corrections: the model's own timing fix, every retraction, and the histories. */
function Corrections({ research }) {
  const lab = getLab(research);
  const timing = timingCorrectionText(timingCorrection(lab));
  const ledger = researchLedger(research);
  const retracted = retractedRows(ledger);
  const superseded = supplyStudy(research)?.superseded ?? [];
  const words = { labels: modelLabels(lab) };
  return (
    <div className="method-block appendix-block" id={APPENDIX.corrections}>
      <h3 className="study-title" tabIndex={-1}>Version history and corrections</h3>
      {timing && (
        <p className="prose-note">
          {timing}. The lower loss came from a close published after the decision, so the leaderboard keeps v1 only as a timing
          control (see <SectionLink id="modl" />).
        </p>
      )}
      {retracted.length > 0 && (
        <>
          <h4 className="figure-title">Retracted claims ({fmtInt(retracted.length)}), kept in the ledger as a record</h4>
          <ul className="corrections-list">
            {retracted.map((row) => (
              <li key={row.id ?? row.hypothesis}>
                <a href={`#${ledgerAnchor(row.id)}`}>{ledgerText(row.hypothesis, words)}</a>
                {row.date && <span className="muted"> recorded {fmtDate(row.date)}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
      {superseded.length > 0 && (
        <p className="prose-note is-muted">
          {fmtInt(superseded.length)} supply-study statement{superseded.length === 1 ? " was" : "s were"} superseded; each is quoted with
          its correction in <SectionLink id="stdy" />.
        </p>
      )}
      <p className="link-list">
        <OutLink href={DOCS.changelog}>Changelog</OutLink>
        <OutLink href={DATA_HISTORY_URL}>data.json history</OutLink>
        <OutLink href={RECORD_HISTORY_URL}>Live record history</OutLink>
      </p>
    </div>
  );
}

/** Reuse: the license, read from CITATION.cff. */
function Reuse() {
  return (
    <div className="method-block appendix-block" id={APPENDIX.reuse}>
      <h3 className="study-title" tabIndex={-1}>Reuse</h3>
      <p className="prose-note">
        Code and data files: {CITATION.license ? `${CITATION.license} license` : "see the license"} (
        <OutLink href={DOCS.license}>LICENSE</OutLink>). The source series keep their providers&apos; terms: Yahoo Finance, FRED and
        the U.S. EIA; OVX is a Cboe index.
      </p>
    </div>
  );
}

/** Docs and the raw payloads. */
function Docs() {
  return (
    <div className="method-block appendix-block" id={APPENDIX.docs}>
      <h3 className="study-title" tabIndex={-1}>Docs</h3>
      <p className="link-list">
        <OutLink href={DOCS.modelCard}>Model card</OutLink>
        <OutLink href={DOCS.methodology}>Methodology</OutLink>
        <OutLink href={DOCS.datasheet}>Datasheet</OutLink>
        <OutLink href={DOCS.changelog}>Changelog</OutLink>
      </p>
      <p className="link-list">
        <a href={STATIC_DATA_URL} download="data.json">Download data.json</a>
        <a href={RESEARCH_URL} download="research.json">Download research.json</a>
      </p>
    </div>
  );
}

/** METH: the model card, its coefficients, every date and file the page reads, the statistics, and how to cite and reuse. */
export default function MethodPanel({ data, research, researchStatus }) {
  const lab = getLab(research);
  const generated = asObject(lab?.generated_from);
  const config = asObject(lab?.config) ?? {};
  const mcs = asObject(config.mcs);
  const conformal = asObject(config.conformal);
  const { validation } = getVol(data);
  const retrievedMs = toMs(generated?.retrieved_at);
  const pinned = Object.keys(asObject(generated?.files) ?? {}).length > 0;

  return (
    <Panel
      id="meth"
      code="METH"
      name="Methodology"
      title={methodAssertion(lab)}
      fallbackTitle="Methodology and reproducibility"
      meta={[
        ...methodDetail(lab),
        retrievedMs != null ? `data retrieved ${fmtCT(retrievedMs)}` : null,
        lab?.sample?.n_days != null ? `${fmtInt(lab.sample.n_days)} out-of-sample days` : null,
      ]}
      notes={[
        asText(config.evaluation) ? `Scoring: ${config.evaluation}.` : null,
        asText(config.forecasts) ?? asText(validation?.forecast_used_for_losses)
          ? `Forecast used: ${tidyProse(asText(config.forecasts) ?? asText(validation?.forecast_used_for_losses))}.` : null,
        mcs ? `Model Confidence Set: ${mcs.statistic ?? DASH} statistic, ${mcs.bootstrap ?? DASH} bootstrap, ${fmtInt(mcs.reps)} reps${mcs.seed != null ? `, seed ${mcs.seed}` : ""}.` : null,
        asText(conformal?.method) ? `Interval: ${tidyProse(conformal.method)}.` : null,
      ]}
      source={{ artifact: ["research.json → vol_lab.generated_from", "vol_lab.config", "vol_lab.series", "data.json → vol_forecast.live"] }}
      resetKey={research}
    >
      <div className="method-grid">
        <ModelCard data={data} research={research} />
        <Coefficients data={data} lab={lab} />
        <Elasticity lab={lab} />
        <Vintage data={data} research={research} />
        <Sources lab={lab} researchStatus={researchStatus} />

        <div className="method-block">
          <h3 className="study-title">Statistics used</h3>
          <ul className="reference-list">
            {REFERENCES.map((item) => (
              <li key={item.id} id={item.id}><span>{item.method}</span><span className="muted">{item.ref}</span></li>
            ))}
          </ul>
        </div>

        <div className="method-block">
          <h3 className="study-title">Reproduce</h3>
          <div className="code-copy">
            <pre className="code-block"><code>{reproduceLines()}</code></pre>
            <CopyButton label="Copy the reproduce commands" text={reproduceLines()} done="Commands copied" />
          </div>
          <p className="prose-note is-muted">
            {pinned
              ? "The benchmark lab's inputs are pinned by the SHA-256 digests above and its bootstrap is seeded, so a rerun on the same files should reproduce its figures on this page; the runs that download data reproduce on the data of the day they run."
              : "The runs that download data reproduce on the data of the day they run."}
          </p>
        </div>

        <Cite data={data} />
        <Corrections research={research} />
        <Reuse />
        <Docs />
      </div>
    </Panel>
  );
}
