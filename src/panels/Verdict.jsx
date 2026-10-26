import Chip from "../components/Chip";
import Kpi from "../components/Kpi";
import { INTERVAL_LEVEL_PCT, liveTileFoot, shortModelName } from "../lib/assertions";
import { DASH, fmtCT, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtSigned, num, toMs } from "../lib/format";
import {
  asObject, asText, getLab, getVol, liveRecordSummary, mcsSummary, ovxOnlyBenchmark, ovxOnlyLabel, persistenceBenchmark,
  recordVar, varRows,
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

function Hero({ live }) {
  const forecast = num(live?.forecast_next_week_vol_annualized_pct);
  const median = num(live?.forecast_median_pct);
  const lo = num(live?.interval_80?.low_pct);
  const hi = num(live?.interval_80?.high_pct);
  const probRise = num(live?.prob_vol_rises_pct);
  const realized = num(live?.current_realized_vol_5d_annualized_pct);
  const implied = num(live?.implied_vol_ovx_pct);
  const ovxSession = asText(live?.inputs?.ovx_session);
  // ovx_error is null, or why an OVX source failed: with one source left the forecast still uses
  // OVX (from that source); with none it is the HAR model without OVX.
  const ovxError = asText(live?.ovx_error);
  const ovxSource = asText(live?.inputs?.ovx_source);
  const challenger = asObject(live?.challenger);
  const challengerError = asText(live?.challenger_error);
  const issuedMs = toMs(live?.issued_at);
  const title = <h2 className="micro verdict-label" id="verdict-title">Next-week WTI realized volatility · forecast</h2>;

  if (forecast == null) {
    return (
      <div className="verdict-hero">
        {title}
        <div className="figure-empty is-compact">
          <p className="empty-title">No live forecast in this snapshot</p>
          <p className="empty-text">
            data.json carries no usable vol_forecast.live block, so there is no current forecast or interval
            to show. The out-of-sample evidence below does not depend on it.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="verdict-hero">
      {title}
      <div className="hero-row">
        <p className="hero-value num">
          {fmtNum(forecast, 1)}
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
          <dt><i className="swatch swatch-truth is-tick" />Realized 5d</dt>
          <dd className="num">{fmtPct(realized)}</dd>
        </div>
        <div className="fact" title={ovxError ?? undefined}>
          <dt><i className="swatch swatch-implied is-diamond" />OVX</dt>
          <dd className="num">
            {fmtNum(implied, 1)}
            {implied == null && ovxError
              ? <span className="fact-note">no OVX source this session; forecast without OVX</span>
              : ovxSession && (
                <span className="fact-note">
                  {fmtDate(ovxSession, { year: false })} close, the model&apos;s input{ovxError && ovxSource ? ` (from ${ovxSource})` : ""}
                </span>
              )}
          </dd>
        </div>
        {challenger ? (
          <div className="fact">
            <dt>Challenger</dt>
            <dd className="num">
              {fmtPct(challenger.forecast_next_week_vol_annualized_pct)}
              {asText(challenger.label) && <span className="fact-note">{challenger.label}</span>}
            </dd>
          </div>
        ) : challengerError && (
          <div className="fact" title={challengerError}>
            <dt>Challenger</dt>
            <dd className="num">{DASH}<span className="fact-note">not published this session</span></dd>
          </div>
        )}
        {probRise != null && (
          // Direction carries no demonstrated skill (LIVE, MODL: accuracy sits at its noise floor),
          // so the probability is shown last, unemphasized and labelled as uncalibrated.
          <div className="fact fact--minor">
            <dt>P(vol rises)</dt>
            <dd className="num">
              {fmtPct(probRise)}
              <span className="fact-note">model-implied, uncalibrated</span>
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

/** What a tile says when its research.json figure is absent: loading, no file, or no such block. */
const researchGap = (research, researchStatus, what) => {
  if (researchStatus === "loading") return "loading research.json";
  return research ? `no ${what} in research.json` : "research.json unavailable";
};

/**
 * The verdict strip: the live forecast as the hero number, and six evidence tiles, each with a
 * named baseline and its sample/test. Tiles from the weekly production validation (data.json) and
 * from the every-day benchmark lab (research.json) say which they are, since the two samples give
 * different values of the same statistic.
 */
export default function Verdict({ data, research, researchStatus }) {
  const { live, validation, liveRecord } = getVol(data);
  const lab = getLab(research);
  const mcs = mcsSummary(lab);
  const var99 = recordVar(varRows(lab), 0.01);
  const record = liveRecordSummary(liveRecord);
  const mz = asObject(validation?.har_mincer_zarnowitz);
  const ovx = ovxOnlyBenchmark(validation);
  const persistence = persistenceBenchmark(validation);
  const ovxName = ovxOnlyLabel(lab);
  const n = num(validation?.n);
  const model = shortModelName(live?.model) ?? shortModelName(validation?.model) ?? "the model";
  const weeks = n != null ? `${fmtInt(n)} wk OOS, weekly` : "weekly OOS";
  // The leaderboard's convention, page-wide: positive = the benchmark has the lower loss.
  const dmParts = [
    persistence?.dm != null ? `persistence ${fmtSigned(persistence.dm, 2)}` : null,
    ovx?.dm != null ? `${ovxName} ${fmtSigned(ovx.dm, 2)}${ovx.p != null ? ` (${fmtP(ovx.p)})` : ""}` : null,
  ].filter(Boolean);
  const mcsAvailable = mcs?.survivors != null;

  const liveFoot = liveTileFoot(record);

  return (
    <section className="verdict" aria-labelledby="verdict-title">
      <Hero live={live} />
      <dl className="kpi-grid">
        <Kpi
          label="Forecast loss (QLIKE), weekly"
          href="#fcst"
          value={fmtNum(validation?.har_qlike, 3)}
          baseline={(
            <>
              vs <b className="num">{fmtNum(validation?.persistence_qlike, 3)}</b> persistence
              {ovx && <>, <b className="num">{fmtNum(ovx.qlike, 3)}</b> {ovxName}</>} · lower is better
            </>
          )}
          foot={`${dmParts.length ? `DM t (+ = lower loss than ${model}): ${dmParts.join(", ")} · ` : ""}${weeks}`}
        />
        <Kpi
          label="Model Confidence Set"
          href="#modl"
          value={mcsAvailable ? `${mcs.survivors}/${mcs.eligible}` : DASH}
          unit={mcsAvailable ? "models" : undefined}
          baseline={mcsAvailable
            ? `in the ${mcs.level ?? DASH}% set on QLIKE, every OOS day`
            : researchGap(research, researchStatus, "Model Confidence Set")}
          foot={mcsAvailable && mcs.champion
            ? `${mcs.champion.label} in it on ${mcs.champion.gridsIn ?? DASH}/${mcs.gridCount} sampling grids`
              + `${mcs.champion.inMcs ? "" : `; out on the full sample${mcs.champion.mcsP != null ? ` (${fmtP(mcs.champion.mcsP)})` : ""}`}`
              + `${mcs.excluded.length ? ` · ${mcs.excluded.length === 1 ? "timing control" : `${mcs.excluded.length} timing controls`} excluded` : ""}`
            : null}
        />
        <Kpi
          label={`${INTERVAL_LEVEL_PCT}% interval coverage, weekly`}
          href="#calb"
          value={fmtPct(validation?.interval_80_coverage_pct)}
          baseline={`target ${INTERVAL_LEVEL_PCT}%`}
          foot={`${fmtInt(validation?.interval_80_n)} wk OOS · production validation, adaptive conformal`}
        />
        <Kpi
          label="99% VaR breaches"
          href="#risk"
          value={var99?.exceedances != null ? fmtInt(var99.exceedances) : DASH}
          baseline={var99 ? <>vs <b className="num">{fmtNum(var99.expected, 1)}</b> expected</> : researchGap(research, researchStatus, "VaR backtest")}
          foot={var99
            ? `Kupiec ${fmtP(var99.kupiecP) ?? DASH} · ${var99.quantileLabel} · grid 0, ${fmtInt(var99.n)} wk`
              + `${var99.exceedMin != null ? `; ${fmtInt(var99.exceedMin)}–${fmtInt(var99.exceedMax)} across grids` : ""}`
            : null}
        />
        <Kpi
          label="Mincer-Zarnowitz slope, weekly"
          href="#calb"
          value={fmtNum(mz?.slope, 2)}
          baseline="1 = unbiased"
          foot={mz
            ? `SE ${fmtNum(mz.slope_se, 2)} · unbiased ${fmtP(mz.p_unbiased) ?? DASH} · variance-regression R² ${fmtNum(mz.r2, 2)} · ${weeks}`
            : null}
        />
        <Kpi
          label="Live forward record"
          href="#live"
          value={record ? fmtInt(record.resolved ?? 0) : DASH}
          unit="resolved"
          baseline={record?.started ? `started ${fmtDate(record.started)}` : "no record in this snapshot"}
          foot={liveFoot}
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
        {accuracy != null ? (
          <>
            <span>look-ahead leak</span>
            <span>purged accuracy <b className="num">{fmtPct(accuracy)}</b>{p ? ` (${p})` : ""}</span>
          </>
        ) : <span>{asText(retracted?.reason) ?? "look-ahead leak"}</span>}
        <span><a href="#leak">post-mortem →</a></span>
      </span>
    </p>
  );
}
