import { Fragment } from "react";
import Disclosure from "../components/Disclosure";
import keepHyphenated from "../components/keepHyphenated";
import { INTERVAL_LEVEL_PCT, shortModelName } from "../lib/assertions";
import { DASH, NBSP, fmtCT, fmtDate, fmtInt, fmtNum, fmtPct, fmtSigned, fmtSpan, num, toMs } from "../lib/format";
import { asObject, asText, callScoring, getLab, liveRecordSummary, researchLedger } from "../lib/research";
import {
  bandTrackRecord, callArithmetic, callHistory, challengerStanding, changeAttribution, changeSincePrevious, coneScale,
  elasticities, forecastOfRecord, plainNumber, TOLERANCE, typicalMiss,
} from "../lib/verdict";

/** How many recorded calls the hero lists (the full record is in LIVE). */
const MAX_CALLS = 5;

/** A step's from → to, as the call's inputs print them. */
const attributionSpan = (step) => {
  if (step.id === "refit") return null;
  if (step.id === "smearing") return `${plainNumber(step.from, 4)} → ${plainNumber(step.to, 4)}`;
  if (step.id === "ovx") return `${fmtNum(step.from, 2)} → ${fmtNum(step.to, 2)}`;
  return `${fmtPct(step.from, 2)} → ${fmtPct(step.to, 2)}`;
};

/**
 * What moved the call since the previous one (changeAttribution()): one row per input, the refit and
 * the smearing factor, in vol points of the expected forecast, adding up to the change.
 */
function ChangeAttribution({ attribution, previousAsOf, asOf }) {
  const previousDay = fmtDate(previousAsOf, { year: false });
  const day = fmtDate(asOf, { year: false });
  return (
    <Disclosure summary={`What moved it since the ${previousDay} call`} className="hero-math hero-attribution">
      <table className="attribution-table">
        <caption>
          Change in the expected forecast, {previousDay} call to {day} call, in vol points
        </caption>
        <thead>
          <tr><th scope="col">Step</th><th scope="col">{previousDay} → {day}</th><th scope="col" className="num">Points</th></tr>
        </thead>
        <tbody>
          {attribution.steps.map((step) => (
            <tr key={step.id}>
              <th scope="row">{step.label}</th>
              <td className="num">{step.id === "refit" ? `${previousDay} fit → ${day} fit` : attributionSpan(step)}</td>
              <td className="num">{fmtSigned(step.points, 2)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Expected forecast</th>
            <td className="num">{fmtNum(attribution.from, 2)} → {fmtNum(attribution.to, 2)}</td>
            <td className="num">{fmtSigned(attribution.total, 2)}</td>
          </tr>
        </tfoot>
      </table>
      <p>
        From each call&apos;s own recorded fit. Each input&apos;s move is valued at the {previousDay} coefficients, then
        the refit at the {day} inputs, then the smearing factor; each step multiplies the forecast, so the steps add up to
        the change exactly, and only their split in points depends on that order.
      </p>
    </Disclosure>
  );
}

/** The target window's clause for a recorded call that is never scored (callScoring()); else it is scored. */
const WINDOW_SCORING = {
  pending_roll: " spans a contract roll: recorded, never scored",
  skipped_roll: " spans a contract roll: recorded, never scored",
  late_commit: ", never scored: not sealed before the settlement that opens it",
  expired: ", never scored: its settlements did not arrive in time",
};

/**
 * Where the forecast sits: the 80% interval as a band with the region outside it drawn as possible
 * (hatched), the expected forecast (filled dot), the median (open tick), the current 5-session
 * realized vol and OVX as marks, and the history's 5-day median as a faint tick. Anchored at zero so
 * the width and skew of the interval read honestly. Plain HTML so it needs no chart code above the
 * fold; `track` labels the tails with the counted weeks that landed there. `realizedDigits` is the
 * realized value's own precision (two for a recorded two-decimal input, which is never rounded to one).
 */
export function RangeBar({
  lo, hi, forecast, median = null, realized = null, realizedDigits = 1, implied = null, track = null, history = null,
}) {
  const values = [lo, hi, forecast, median, realized, implied, history?.p50].filter((value) => value != null);
  if (lo == null || hi == null || values.length < 3) return null;
  const max = Math.max(...values) * 1.1 || 1;
  const left = (value) => `${((Math.max(0, value) / max) * 100).toFixed(2)}%`;
  const label = [
    `${INTERVAL_LEVEL_PCT}% interval ${fmtNum(lo, 1)} to ${fmtNum(hi, 1)} percent`,
    forecast != null ? `expected ${fmtNum(forecast, 1)}` : null,
    median != null ? `median ${fmtNum(median, 1)}` : null,
    realized != null ? `realized last 5 sessions ${fmtNum(realized, realizedDigits)}` : null,
    implied != null ? `OVX ${fmtNum(implied, 1)}` : null,
    history ? `5-day realized vol has a historical median of ${fmtNum(history.p50, 1)}` : null,
    track ? `in ${fmtInt(track.inside)} of ${fmtInt(track.n)} production-validation weeks realized vol landed inside that week's band, `
      + `${fmtInt(track.below)} below and ${fmtInt(track.above)} above` : null,
  ].filter(Boolean).join("; ");
  return (
    <div className="rangebar" role="img" aria-label={label}>
      <div className="rb-track">
        <div className="rb-tail" style={{ left: 0, width: left(lo) }} />
        <div className="rb-tail" style={{ left: left(hi), right: 0 }} />
        <div className="rb-band" style={{ left: left(lo), width: `calc(${left(hi)} - ${left(lo)})` }} />
        {history && <div className="rb-mark rb-hist" style={{ left: left(history.p50) }} />}
        {realized != null && <div className="rb-mark rb-truth" style={{ left: left(realized) }} />}
        {implied != null && <div className="rb-mark rb-implied" style={{ left: left(implied) }} />}
        {median != null && <div className="rb-mark rb-median" style={{ left: left(median) }} />}
        {forecast != null && <div className="rb-mark rb-model" style={{ left: left(forecast) }} />}
      </div>
      <div className="rb-ends num" aria-hidden="true">
        <span style={{ left: 0, transform: "none" }}>0</span>
        <span style={{ left: left(lo) }}>{fmtNum(lo, 1)}</span>
        <span style={{ left: left(hi) }}>{fmtNum(hi, 1)}</span>
      </div>
      {track && (
        <div className="rb-tails" aria-hidden="true">
          <span>below: {fmtInt(track.below)}{NBSP}wk</span>
          <span>above: {fmtInt(track.above)}{NBSP}wk</span>
        </div>
      )}
    </div>
  );
}

/** A model probability as a whole percent, never 0% or 100% (it is a model output, not a count). */
const wholePercent = (value) => {
  const rounded = Math.round(value);
  if (rounded >= 100) return `>${NBSP}99%`;
  if (rounded <= 0) return `<${NBSP}1%`;
  return `${rounded}%`;
};

/**
 * The challenger's note: why a model with the lower lab loss is not the model of record. While the
 * live promotion test accrues it is the only route left; once it has decided, the note says what it
 * decided: kept the model of record, or favours the challenger, a swap a maintainer applies (not yet
 * applied while this call is still the pinned record version's).
 */
function ChallengerNote({ standing, label, promotion, version = null, pending = false }) {
  const name = standing.name ?? label;
  const lead = [standing.comparison, standing.search].filter(Boolean);
  const head = lead.length === 2 ? `${lead[0]}, but ${lead[1]}` : lead[0] ?? null;
  const spa = standing.spa;
  const test = standing.test;
  if (!head && !spa && !test) return null;
  const status = test?.status === "keep" || test?.status === "promote" ? test.status : null;
  const sentence = [head, spa?.text].filter(Boolean).join("; ");
  const liveTest = test ? <a href="#live">the {fmtInt(test.required)}-week live test</a> : null;
  let clause = null;
  if (test && status) {
    const opening = sentence ? (spa?.keepsRecord ? ", and " : "; ") : "";
    const outcome = status === "keep"
      ? " kept the model of record"
      : ` favours it; the swap is a maintainer's change${promotion?.modelVersion && promotion.modelVersion === version ? ", not yet applied" : ""}`;
    clause = (
      <>
        {opening}
        {sentence ? liveTest : <>The <a href="#live">{fmtInt(test.required)}-week live test</a></>}
        {outcome}
      </>
    );
  } else if (test) {
    let joiner;
    if (spa) joiner = spa.keepsRecord ? ", so it" : "; it still";
    else joiner = head ? "; it" : "It";
    const progress = test.paired != null ? `${fmtInt(test.paired)}/${fmtInt(test.required)}` : null;
    clause = (
      <>
        {joiner} replaces the model of record only through{" "}
        {liveTest}
        {progress ? ` (${progress})` : ""}
      </>
    );
  }
  return (
    // While research.json loads, the note keeps the height its lab comparison will need.
    <li className={pending ? "is-pending" : undefined}>
      <span className="hero-note-label">Challenger{name ? ` ${name}` : ""}:</span>{" "}
      {keepHyphenated(sentence)}
      {clause}
      .
    </li>
  );
}

/**
 * HERO: this week's call exactly as the live record holds it (forecastOfRecord()), its target
 * window and change since the previous call, the band drawn with the region outside it and the
 * band's counted track record, the typical miss in vol points, and the arithmetic that reproduces
 * the number. A later recomputation on the same close is shown below it, labelled as not scored.
 */
export default function Hero({ live, liveRecord, recentOos, research, researchStatus }) {
  const call = forecastOfRecord(live, liveRecord);
  const lab = getLab(research);
  const model = asText(live?.model);
  const version = asText(call?.entry?.model_version) ?? asText(live?.model_version);
  const label = (
    <p className="micro verdict-label">
      This week&apos;s call{model && ` · ${shortModelName(model)}`}
      {version && <>{" "}<span className="mono">{version}</span></>}
    </p>
  );

  if (!call || call.forecast == null) {
    return (
      <div className="verdict-hero">
        {label}
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

  const summary = liveRecordSummary(liveRecord);
  const calls = callHistory(liveRecord);
  const change = changeSincePrevious(call, calls);
  const attribution = change ? changeAttribution(call.entry, change.previous) : null;
  const track = bandTrackRecord(recentOos);
  const miss = typicalMiss(recentOos);
  const scale = coneScale(lab);
  // research.json still loading: its lines keep their place (empty), so the call does not move when it arrives.
  const pending = researchStatus === "loading" && !lab;
  const ovxError = asText(live?.ovx_error);
  const ovxSource = asText(live?.inputs?.ovx_source);
  const challengerError = asText(live?.challenger_error);
  const challengerLabel = asText(asObject(live?.challenger)?.label);
  const standing = challengerStanding(lab, summary?.promotion, researchLedger(research)?.byId.get("vol_spa_beats_record"));
  const issuedMs = toMs(call.issuedAt);
  const sealedMs = toMs(call.sealedAt);
  const implied = call.ovx.value;
  // The arithmetic: the recorded inputs when they reproduce the call, else the live block's (the
  // model card in METH prints the same chain from the same function).
  const { math, ownFit, coefficients, inputsFrom: mathFrom } = callArithmetic(call, live);
  const weights = elasticities(coefficients);
  const sd = num(live?.residual_sd);
  const realized = call.realized.value;
  // A recorded two-decimal input prints at two decimals, never rounded again to one.
  const realizedText = fmtPct(realized, call.realized.digits);
  const changeText = change && (Math.abs(change.delta) < TOLERANCE
    ? `unchanged since the ${fmtDate(change.previous.as_of, { year: false })} call`
    : `${fmtSigned(change.delta, 1)} pts since the ${fmtDate(change.previous.as_of, { year: false })} call (${fmtNum(change.previous.forecast_pct, 1)})`);
  const recompute = call.recompute;
  const recomputeParts = recompute ? [
    recompute.fields.includes("forecast") ? `forecast ${fmtNum(recompute.values.forecast, 1)}` : null,
    recompute.fields.includes("lo") || recompute.fields.includes("hi")
      ? `band ${fmtNum(recompute.values.lo, 1)}–${fmtNum(recompute.values.hi, 1)}` : null,
    recompute.fields.includes("challenger") ? `challenger ${fmtNum(recompute.values.challenger, 1)}` : null,
  ].filter(Boolean) : [];

  let challengerFact = null;
  if (call.challenger != null) {
    challengerFact = (
      <div className="fact">
        <dt>Challenger</dt>
        <dd className="num">
          {fmtPct(call.challenger)}
          {(challengerLabel ?? standing.name) && <span className="fact-note">{keepHyphenated(challengerLabel ?? standing.name)}</span>}
        </dd>
      </div>
    );
  } else if (challengerError && (call.source === "live" || call.fromLive)) {
    challengerFact = (
      <div className="fact" title={challengerError}>
        <dt>Challenger</dt>
        <dd className="num">{DASH}<span className="fact-note">not published this session</span></dd>
      </div>
    );
  } else if (call.source === "record") {
    challengerFact = (
      <div className="fact">
        <dt>Challenger</dt>
        <dd className="num">{DASH}<span className="fact-note">not in the recorded call</span></dd>
      </div>
    );
  }

  return (
    <div className="verdict-hero">
      <div className="hero-call">
        {label}
        <div className="hero-row">
          <p className="hero-value num">
            {fmtNum(call.forecast, 1)}
            <span className="hero-unit">%</span>
          </p>
          <div className="hero-side">
            <p className="hero-sub">
              <i className="swatch swatch-model is-dot" />expected realized vol, annualized
              {call.median != null && (
                <> · <span className="nowrap"><i className="swatch swatch-model is-open-tick" />median{NBSP}{fmtPct(call.median)}</span></>
              )}
            </p>
            <p className="hero-interval">
              {INTERVAL_LEVEL_PCT}% interval{" "}
              <b className="num nowrap">{call.lo != null && call.hi != null ? `${fmtNum(call.lo, 1)}–${fmtNum(call.hi, 1)}%` : DASH}</b>
            </p>
            {changeText && <p className="hero-change">{changeText}</p>}
          </div>
        </div>
        {call.source === "record" && call.targetStart && call.targetEnd && (
          <p className="hero-window">
            Target window <b>{fmtDate(call.targetStart, { year: false })} – {fmtDate(call.targetEnd)}</b>
            {WINDOW_SCORING[callScoring(call.entry, liveRecord)] ?? ", scored on its settlements"}
          </p>
        )}
        {call.source === "live" && <p className="hero-window">Not in the live record yet</p>}
        {calls.length > 0 && (
          <p className="hero-calls sep-list">
            <span className="hero-calls-label">Recorded</span>
            {calls.slice(0, MAX_CALLS).map((row) => (
              <span key={`${row.as_of} ${row.issued_at}`} className={row === call.entry ? "is-current" : undefined}>
                {fmtDate(row.as_of, { year: false })}{NBSP}<b className="num">{fmtNum(row.forecast_pct, 1)}</b>
              </span>
            ))}
            <span><a href="#live">All recorded calls →</a></span>
          </p>
        )}
        <RangeBar
          lo={call.lo} hi={call.hi} forecast={call.forecast} median={call.median}
          realized={realized} realizedDigits={call.realized.digits} implied={implied} track={track} history={scale}
        />
        {track && (
          <p className="hero-track">
            In {fmtInt(track.inside)} of {fmtInt(track.n)} out-of-sample weeks realized vol landed inside that
            week&apos;s {INTERVAL_LEVEL_PCT}% band; {fmtInt(track.below)} below, {fmtInt(track.above)} above
            (production validation, {fmtSpan(track.first, track.last)}).
          </p>
        )}
        {scale ? (
          <p className="hero-scale">
            <i className="swatch swatch-hist is-tick" />For scale: 5-day realized vol since {fmtDate(scale.since)} has
            median {fmtPct(scale.p50)} and 95th percentile {fmtPct(scale.p95)} (benchmark lab).
          </p>
        ) : pending && <p className="hero-scale is-pending" aria-hidden="true" />}
      </div>
      <div className="hero-detail">
        <dl className="hero-facts">
          <div className="fact">
            <dt><i className="swatch swatch-truth is-tick" />Realized 5d</dt>
            <dd className="num">
              {realizedText}
              {call.asOf && (
                <span className="fact-note">
                  last 5 sessions to {fmtDate(call.asOf, { year: false })}{call.realized.recomputed ? " (recomputation)" : ""}
                </span>
              )}
            </dd>
          </div>
          <div className="fact" title={ovxError ?? undefined}>
            <dt><i className="swatch swatch-implied is-diamond" />OVX</dt>
            <dd className="num">
              {fmtNum(implied, 1)}
              {implied == null && ovxError
                ? <span className="fact-note">no OVX source this session; forecast without OVX</span>
                : call.ovxSession && (
                  <span className="fact-note">
                    {fmtDate(call.ovxSession, { year: false })} close, the model&apos;s input
                    {ovxError && ovxSource ? ` (from ${ovxSource})` : ""}{call.ovx.recomputed ? " (recomputation)" : ""}
                  </span>
                )}
            </dd>
          </div>
          {challengerFact}
          {miss && (
            <div className="fact">
              <dt>Typical miss</dt>
              <dd className="num">
                ±{fmtNum(miss.median, 1)}{NBSP}pts
                <span className="fact-note">
                  median <span className="nowrap">|realized − forecast|</span>, last {fmtInt(miss.n)}{NBSP}wk;{" "}
                  {miss.quantile}th percentile {fmtNum(miss.upper, 1)}
                </span>
              </dd>
            </div>
          )}
        </dl>
        <ul className="hero-notes">
          <ChallengerNote standing={standing} label={challengerLabel} promotion={summary?.promotion} version={version} pending={pending} />
          {call.prob != null && realized != null && call.median != null && (
            // Direction carries no demonstrated skill (LIVE, MODL: accuracy sits at its noise floor), so the
            // probability is one plain sentence: whole percent, its model stated, never as a frequency.
            <li className="hero-prob">
              <span className="hero-note-label">P(vol rises):</span> model-implied chance that next-week realized vol exceeds
              the last 5 sessions&apos; {realizedText}: {wholePercent(call.prob)} (lognormal around the {fmtPct(call.median)} median
              {sd != null ? `, log sd ${fmtNum(sd, 2)} from the fit's residuals` : ""}; not calibrated; direction is not evidence of skill).
            </li>
          )}
        </ul>
      </div>
      <div className="hero-more">
        {weights && (
          <p className="hero-elasticity">
            <span className="hero-note-label">Elasticity of the forecast:</span>{" "}
            {weights.map((item, index) => (
              <Fragment key={item.label}>
                {index > 0 && ", "}<span className="nowrap">{item.label} {fmtNum(item.value, 2)}</span>
              </Fragment>
            ))}{" "}
            (log-log, current fit).
          </p>
        )}
        {math && (
          <Disclosure summary={`How ${fmtNum(call.forecast, 1)} is computed`} className="hero-math">
            {/* Each factor stays on one line; the lines break between factors. */}
            <p className="hero-math-line">
              <span className="nowrap">median = exp({math.intercept})</span>
              {math.factors.map((factor, index) => (
                <Fragment key={index}>{" "}<span className="nowrap">× {factor.base}^{factor.exponent}</span></Fragment>
              ))}
              {" "}<span className="nowrap">= {fmtPct(math.shownMedian, 2)}</span>
            </p>
            <p className="hero-math-line">
              <span className="nowrap">expected = {fmtNum(math.shownMedian, 2)} × √{plainNumber(math.smearing, 4)}</span>{" "}
              <span className="nowrap">(Duan smearing)</span>{" "}<span className="nowrap">= {fmtPct(math.printedMean, 2)}</span>
            </p>
            <p>
              Recorded to one decimal: median {fmtPct(call.median)}, expected {fmtPct(call.forecast)}. RV in decimals, OVX in
              index points; coefficients from the fit as of{" "}
              {fmtDate(ownFit ? call.asOf : asText(live?.as_of) ?? call.asOf)}{ownFit ? ", as recorded with the call" : ""}
              {mathFrom != null ? `; inputs of the ${fmtCT(mathFrom)} computation` : ""}.{" "}
              <a href="#meth">Model card (METH) →</a>
            </p>
          </Disclosure>
        )}
        {attribution && <ChangeAttribution attribution={attribution} previousAsOf={change.previous.as_of} asOf={call.asOf} />}
        {call.olderSnapshot && (
          <p className="hero-recompute">
            This snapshot was computed {fmtCT(toMs(call.olderSnapshot.issuedAt))} from an older close
            ({fmtDate(call.olderSnapshot.asOf, { year: false })}); the call above was recorded from the{" "}
            {fmtDate(call.asOf, { year: false })} close and is the one scored.
          </p>
        )}
        {recompute && recomputeParts.length > 0 && (
          <p className="hero-recompute">
            Recomputed {fmtCT(toMs(recompute.issuedAt))} on the same close: {recomputeParts.join(", ")}. Not scored; the
            recorded call is.
          </p>
        )}
        <p className="hero-model sep-list">
          <span>{model ?? "Model"}</span>
          {version && <span className="mono">{version}</span>}
          {call.asOf && <span>as of {fmtDate(call.asOf)} close</span>}
          {issuedMs != null && <span>{call.source === "record" ? "recorded" : "issued"} {fmtCT(issuedMs)}</span>}
          {sealedMs != null && <span>sealed {fmtCT(sealedMs)}</span>}
        </p>
      </div>
    </div>
  );
}
