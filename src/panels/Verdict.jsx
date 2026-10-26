import Chip from "../components/Chip";
import Kpi from "../components/Kpi";
import keepHyphenated from "../components/keepHyphenated";
import { promotionText } from "../lib/assertions";
import { DASH, NBSP, fmtDate, fmtInt, fmtNum, fmtP, fmtPct, num } from "../lib/format";
import { asObject, asText, getVol, liveRecordSummary } from "../lib/research";
import { evidenceTiles, progressGlyph, tilesKey } from "../lib/tiles";
import Hero from "./Hero";
import { EvidenceList, VerdictLede } from "./VerdictLede";

/**
 * The verdict strip: the live forecast as the hero number, and six evidence tiles, each with a
 * named baseline and its sample/test. The first five take their values from one sample, the
 * benchmark lab on every out-of-sample day (research.json), and quote the weekly production
 * validation (data.json) in their footnotes; the key under the tiles names both samples.
 */
export default function Verdict({ data, research, researchStatus }) {
  const { live, liveRecord } = getVol(data);
  const tiles = evidenceTiles(data, research, researchStatus);
  const record = liveRecordSummary(liveRecord);
  const promotion = promotionText(record?.promotion);

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
        {tiles.map(({ id, ...tile }) => <Kpi key={id} {...tile} />)}
        <Kpi
          label="Live forward record"
          href="#live"
          value={record ? fmtInt(record.resolved ?? 0) : DASH}
          unit="resolved"
          glyph={progressGlyph(record?.promotion)}
          baseline={record?.started ? `started ${fmtDate(record.started)}` : "no record in this snapshot"}
          foot={liveFoot}
        />
      </dl>
      <p className="kpi-key">{keepHyphenated(tilesKey(data, research, researchStatus))}</p>
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
