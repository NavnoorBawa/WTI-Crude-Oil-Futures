import Chip from "../components/Chip";
import Kpi from "../components/Kpi";
import { INTERVAL_LEVEL_PCT, promotionText, shortModelName } from "../lib/assertions";
import { DASH, NBSP, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, fmtSigned, num } from "../lib/format";
import {
  asObject, asText, getLab, getVol, liveRecordSummary, mcsSummary, ovxOnlyBenchmark, ovxOnlyLabel, persistenceBenchmark,
  recordVar, varRows,
} from "../lib/research";
import Hero from "./Hero";
import { EvidenceList, VerdictLede } from "./VerdictLede";

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
  const promotion = promotionText(record?.promotion);
  // The leaderboard's convention, page-wide: positive = the benchmark has the lower loss.
  const dmParts = [
    persistence?.dm != null ? `persistence ${fmtSigned(persistence.dm, 2)}` : null,
    ovx?.dm != null ? `${ovxName} ${fmtSigned(ovx.dm, 2)}${ovx.p != null ? ` (${fmtP(ovx.p)})` : ""}` : null,
  ].filter(Boolean);
  const mcsAvailable = mcs?.survivors != null;

  let liveFoot = null;
  if (record) {
    const paired = record.paired;
    let status;
    if ((record.resolved ?? 0) === 0) {
      status = record.firstResult
        ? `first result ${fmtDate(record.firstResult)}`
        : `${fmtInt(record.recorded ?? 0)} recorded · ${fmtInt(record.skipped ?? 0)} skipped at roll${record.lateCommit ? ` · ${fmtInt(record.lateCommit)} not sealed in time` : ""}`;
    } else {
      status = paired.n > 0 && paired.record != null && paired.challenger != null
        ? `QLIKE ${fmtNum(paired.record, 3)} record vs ${fmtNum(paired.challenger, 3)} challenger, same ${fmtInt(paired.n)}${NBSP}wk`
        : `${fmtInt(record.independent)} independent · no week with both forecasts yet`;
    }
    liveFoot = promotion ? `${status} · promotion ${promotion}` : status;
  }

  return (
    <section className="verdict" aria-labelledby="verdict-title">
      <VerdictLede data={data} research={research} researchStatus={researchStatus} />
      <Hero
        live={live} liveRecord={liveRecord} recentOos={getVol(data).recentOos}
        research={research} researchStatus={researchStatus}
      />
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
      <EvidenceList data={data} research={research} />
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
