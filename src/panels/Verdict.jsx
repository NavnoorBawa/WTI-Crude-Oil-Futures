import Chip from "../components/Chip";
import Kpi from "../components/Kpi";
import { INTERVAL_LEVEL_PCT } from "../lib/assertions";
import { DASH, fmtCT, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, num, toMs } from "../lib/format";
import {
  asObject, asText, getLab, getVol, liveRecordSummary, mcsSummary, recordVar, varRows,
} from "../lib/research";

/**
 * Where the forecast sits: the 80% interval as a band, with the expected forecast, the current
 * 5-session realized vol and OVX as marks. Plain HTML so it needs no chart code above the fold.
 */
export function RangeBar({ lo, hi, forecast, realized, implied }) {
  const values = [lo, hi, forecast, realized, implied].filter((value) => value != null);
  if (lo == null || hi == null || values.length < 3) return null;
  // Anchored at zero so the width and skew of the interval read honestly.
  const max = Math.max(...values) * 1.08 || 1;
  const left = (value) => `${((Math.max(0, value) / max) * 100).toFixed(2)}%`;
  const label = `80% interval ${fmtNum(lo, 1)} to ${fmtNum(hi, 1)} percent; expected ${fmtNum(forecast, 1)}`
    + (realized != null ? `; realized last 5 sessions ${fmtNum(realized, 1)}` : "")
    + (implied != null ? `; OVX ${fmtNum(implied, 1)}` : "");
  return (
    <div className="rangebar" role="img" aria-label={label}>
      <div className="rb-track">
        <div className="rb-band" style={{ left: left(lo), width: `calc(${left(hi)} - ${left(lo)})` }} />
        {realized != null && <div className="rb-mark rb-truth" style={{ left: left(realized) }} />}
        {implied != null && <div className="rb-mark rb-implied" style={{ left: left(implied) }} />}
        {forecast != null && <div className="rb-mark rb-model" style={{ left: left(forecast) }} />}
      </div>
      <div className="rb-ends num" aria-hidden="true">
        <span style={{ left: 0, transform: "none" }}>0</span>
        <span style={{ left: left(lo) }}>{fmtNum(lo, 1)}</span>
        <span style={{ left: left(hi) }}>{fmtNum(hi, 1)}</span>
      </div>
    </div>
  );
}

const arrowFor = (direction) => (direction === "RISING" ? "↑" : direction === "FALLING" ? "↓" : "");

function Hero({ live }) {
  const forecast = num(live?.forecast_next_week_vol_annualized_pct);
  const median = num(live?.forecast_median_pct);
  const lo = num(live?.interval_80?.low_pct);
  const hi = num(live?.interval_80?.high_pct);
  const probRise = num(live?.prob_vol_rises_pct);
  const realized = num(live?.current_realized_vol_5d_annualized_pct);
  const implied = num(live?.implied_vol_ovx_pct);
  const ovxSession = asText(live?.inputs?.ovx_session);
  const challenger = asObject(live?.challenger);
  const direction = typeof live?.direction === "string" ? live.direction.toUpperCase() : null;
  const issuedMs = toMs(live?.issued_at);

  return (
    <div className="verdict-hero">
      <h2 className="micro verdict-label" id="verdict-title">Next-week WTI realized volatility · forecast</h2>
      <div className="hero-row">
        <p className="hero-value num">
          {forecast != null ? fmtNum(forecast, 1) : DASH}
          <span className="hero-unit">%</span>
        </p>
        <div className="hero-side">
          <p className="hero-sub">
            <i className="swatch swatch-model is-dot" />expected, annualized, next 5 sessions
            {median != null && <> · median {fmtPct(median)}</>}
          </p>
          <p className="hero-interval">
            {INTERVAL_LEVEL_PCT}% interval{" "}
            <b className="num">{lo != null && hi != null ? `${fmtNum(lo, 1)}–${fmtNum(hi, 1)}%` : DASH}</b>
          </p>
        </div>
      </div>
      <RangeBar lo={lo} hi={hi} forecast={forecast} realized={realized} implied={implied} />
      <dl className="hero-facts">
        <div className="fact">
          <dt>P(vol rises)</dt>
          <dd className="num accent-model">
            {probRise != null ? <>{arrowFor(direction)} {fmtPct(probRise)}</> : DASH}
          </dd>
        </div>
        <div className="fact">
          <dt><i className="swatch swatch-truth is-tick" />Realized 5d</dt>
          <dd className="num">{fmtPct(realized)}</dd>
        </div>
        <div className="fact">
          <dt><i className="swatch swatch-implied is-diamond" />OVX</dt>
          <dd className="num">
            {fmtNum(implied, 1)}
            {ovxSession && <span className="fact-note">{fmtDate(ovxSession, { year: false })} close</span>}
          </dd>
        </div>
        {challenger && (
          <div className="fact">
            <dt>Challenger</dt>
            <dd className="num">
              {fmtPct(challenger.forecast_next_week_vol_annualized_pct)}
              {asText(challenger.label) && <span className="fact-note">{challenger.label}</span>}
            </dd>
          </div>
        )}
      </dl>
      <p className="hero-model sep-list">
        <span>{asText(live?.model) ?? "Model"}</span>
        {asText(live?.model_version) && <span className="mono">{live.model_version}</span>}
        {asText(live?.as_of) && <span>as of {fmtDate(live.as_of)} close</span>}
        {issuedMs != null && <span>issued {fmtCT(issuedMs)}</span>}
      </p>
    </div>
  );
}

/**
 * The verdict strip: the live forecast as the hero number, and six evidence tiles, each with a
 * named baseline and its sample/test.
 */
export default function Verdict({ data, research, researchStatus }) {
  const { live, validation, liveRecord } = getVol(data);
  const lab = getLab(research);
  const mcs = mcsSummary(lab);
  const var99 = recordVar(varRows(lab), 0.01);
  const record = liveRecordSummary(liveRecord);
  const mz = asObject(validation?.har_mincer_zarnowitz);
  const researchNote = researchStatus === "loading" ? "loading research.json" : "research.json unavailable";
  const n = num(validation?.n);

  return (
    <section className="verdict" aria-labelledby="verdict-title">
      <Hero live={live} />
      <dl className="kpi-grid">
        <Kpi
          label="Forecast loss (QLIKE)"
          href="#modl"
          value={fmtNum(validation?.har_qlike, 3)}
          baseline={<>vs <b className="num">{fmtNum(validation?.persistence_qlike, 3)}</b> persistence · lower is better</>}
          foot={`DM t ${fmtNum(validation?.har_vs_persistence_qlike_dm, 2)} · ${n != null ? `${fmtInt(n)} wk OOS` : "OOS"}`}
        />
        <Kpi
          label="Model Confidence Set"
          href="#modl"
          value={mcs ? `${mcs.survivors}/${mcs.total}` : DASH}
          unit={mcs ? "models" : undefined}
          baseline={mcs ? `survive at ${mcs.level ?? DASH}% on QLIKE` : researchNote}
          foot={mcs?.champion
            ? `${mcs.champion.label} in it on ${mcs.champion.gridsIn ?? DASH}/${mcs.gridCount} weekday grids${mcs.champion.inMcs ? "" : `; out on the full sample${mcs.champion.mcsP != null ? ` (${fmtP(mcs.champion.mcsP)})` : ""}`}`
            : null}
        />
        <Kpi
          label={`${INTERVAL_LEVEL_PCT}% interval coverage`}
          href="#calb"
          value={fmtPct(validation?.interval_80_coverage_pct)}
          baseline={`target ${INTERVAL_LEVEL_PCT}%`}
          foot={`${fmtInt(validation?.interval_80_n)} wk · adaptive conformal`}
        />
        <Kpi
          label="99% VaR breaches"
          href="#risk"
          value={var99?.exceedances != null ? fmtInt(var99.exceedances) : DASH}
          baseline={var99 ? <>vs <b className="num">{fmtNum(var99.expected, 1)}</b> expected</> : researchNote}
          foot={var99 ? `Kupiec ${fmtP(var99.kupiecP) ?? DASH} · ${var99.quantileLabel} · ${fmtInt(var99.n)} wk` : null}
        />
        <Kpi
          label="Mincer-Zarnowitz slope"
          href="#calb"
          value={fmtNum(mz?.slope, 2)}
          baseline="1 = unbiased"
          foot={mz ? `SE ${fmtNum(mz.slope_se, 2)} · unbiased ${fmtP(mz.p_unbiased) ?? DASH} · R² ${fmtNum(mz.r2, 2)}` : null}
        />
        <Kpi
          label="Live forward record"
          href="#live"
          value={record ? fmtInt(record.resolved ?? 0) : DASH}
          unit="resolved"
          baseline={record?.started ? `started ${fmtDate(record.started)}` : "no record in this snapshot"}
          foot={record && (record.resolved ?? 0) === 0
            ? (record.firstResult
              ? `first result ${record.firstResultIsEstimate ? "≈ " : ""}${fmtDate(record.firstResult)}`
              : `${fmtInt(record.recorded ?? 0)} recorded · ${fmtInt(record.skipped ?? 0)} skipped at roll`)
            : record ? `${fmtInt(record.independent)} independent · QLIKE ${fmtNum(record.champion?.qlike, 3)}` : null}
        />
      </dl>
    </section>
  );
}

/** One muted line: the retracted direction model and where its post-mortem is. */
export function RetractedLine({ data, research }) {
  const retracted = asObject(data?.retracted_direction_model);
  const purged = asObject(research?.postmortem?.five_year?.purged);
  const accuracy = num(purged?.direction_acc_pct);
  const p = fmtP(purged?.p_value);
  if (!retracted && !purged) return null;
  return (
    <p className="retracted-line">
      <Chip tone="warn">{asText(retracted?.status) ?? "Retracted"}</Chip>
      <span className="sep-list">
        <span>1W direction model</span>
        <span>look-ahead leak</span>
        {accuracy != null
          ? <span>purged accuracy <b className="num">{fmtPct(accuracy)}</b>{p ? ` (${p})` : ""}</span>
          : asText(retracted?.reason) && <span>{retracted.reason}</span>}
        <span><a href="#leak">post-mortem →</a></span>
      </span>
    </p>
  );
}
