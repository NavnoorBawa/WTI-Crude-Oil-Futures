import Chip from "../components/Chip";
import Kpi from "../components/Kpi";
import keepHyphenated from "../components/keepHyphenated";
import { liveTileFoot } from "../lib/assertions";
import { DASH, fmtDate, fmtInt, fmtP, fmtPct, num } from "../lib/format";
import { asObject, asText, getVol, liveRecordSummary, snapshotDay } from "../lib/research";
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
  const liveFoot = liveTileFoot(record, snapshotDay(data));
  // While research.json loads, the tiles and their key keep the height their lab text will need.
  const pending = researchStatus === "loading" && !research ? " is-pending" : "";

  return (
    <section className="verdict" aria-labelledby="verdict-title">
      <VerdictLede data={data} research={research} researchStatus={researchStatus} />
      <Hero
        live={live} liveRecord={liveRecord} recentOos={getVol(data).recentOos}
        research={research} researchStatus={researchStatus}
      />
      <dl className={`kpi-grid${pending}`}>
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
      <p className={`kpi-key${pending}`}>{keepHyphenated(tilesKey(data, research, researchStatus))}</p>
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
