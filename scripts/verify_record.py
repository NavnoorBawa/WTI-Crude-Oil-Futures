#!/usr/bin/env python3
"""Check the sealed live record entry by entry, from the record file alone (docs/REVIEWER_GUIDE.md).

The dashboard summarises runtime-state/vol_track_record.json on the live-data branch. This script
re-derives what each entry claims instead of trusting the summary or the site:

- the structural contract and its invariants (vol_record.validate_record);
- the scoring window: base_session is the first CL settlement after the later of the entry's issue
  and record times plus vol_record.COMMIT_MARGIN, and the five scored returns follow it;
- the seal: how long before the settlement that opens the window the entry was sealed, which must
  exceed vol_record.SEAL_MARGIN for every entry that is scored, skipped or expired;
- the roll rule: an entry is skipped_roll exactly when its window spans a contract roll;
- the outcome: a resolved entry's realized vol recomputed from the six closes it stores, and its
  QLIKE;
- the forecast, when the entry stores its fit (entries recorded from 2.2.0 on): median =
  100·exp(b0 + Σ bk·ln xk) and forecast = median·√smearing from its own coefficients and inputs;
- the hash chain: each linked entry's previous_sha256 is the SHA-256 of the sealed part of the entry
  before it, and every sealed entry from vol_record.CHAIN_FROM on carries one (vol_record.chain_breaks),
  so a sealed entry changed, removed or inserted after a later one was sealed fails here unless every
  later link was recomputed too. The chain has no key and nothing links to the newest entry: an edit
  that rewrites the later links, or touches only the newest entry, shows only against a copy or
  timestamp of the record from before it.

Usage:
    git show origin/live-data:runtime-state/vol_track_record.json | python scripts/verify_record.py -
    python scripts/verify_record.py path/to/vol_track_record.json [--latest 10]

Exit status 0 when every check passes, 1 otherwise. What this cannot show on its own is when a
commit reached GitHub: a git date is set by its committer. For that, compare an entry's sealed_at
with the publish job's run log and the push to live-data (GitHub's own timestamps). Standard
library only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import vol_record as vr  # noqa: E402  (standard library only, needs the path above)
from backend.contract_calendar import spans_roll  # noqa: E402

# A recomputed number matches a stored one printed at one decimal when it is within this distance.
PRINT_TOLERANCE = 0.05
# realized_pct is stored rounded to four decimals.
REALIZED_TOLERANCE = 5e-5
# The fit's inputs in coefficient order after the intercept, and their scale (vol points to decimals).
FIT_INPUTS = (("rv5_pct", 100.0), ("rv22_pct", 100.0), ("rv66_pct", 100.0), ("ovx", 1.0))


def _when(stamp: str) -> datetime:
    return vr._aware_utc(stamp)


def recomputed_forecast(entry: dict[str, Any]) -> tuple[float, float] | None:
    """(median, forecast) from the entry's own stored fit and inputs, or None when it stores no usable fit."""
    coefficients, smearing, inputs = entry.get("coefficients"), entry.get("smearing_factor"), entry.get("inputs")
    if not (isinstance(coefficients, list) and len(coefficients) == len(FIT_INPUTS) + 1
            and isinstance(smearing, (int, float)) and smearing > 0 and isinstance(inputs, dict)):
        return None
    values = [inputs.get(key) for key, _ in FIT_INPUTS]
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in values):
        return None
    log_median = coefficients[0] + sum(
        b * math.log(v / scale) for b, v, (_, scale) in zip(coefficients[1:], values, FIT_INPUTS))
    median = 100.0 * math.exp(log_median)
    return median, median * math.sqrt(smearing)


def check_entry(entry: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """One entry's derived facts and the checks it fails (empty when it passes)."""
    problems: list[str] = []
    base = date.fromisoformat(entry["base_session"])
    end = date.fromisoformat(entry["target_end"])
    opens = vr.settlement_time(base)
    moment = max(_when(entry["issued_at"]), _when(entry["recorded_at"])) + vr.COMMIT_MARGIN
    window = vr.target_window(moment)
    if (window["base_session"], window["target_end"]) != (base, end):
        problems.append(f"window {base}..{end} is not the one its issue and record times give "
                        f"({window['base_session']}..{window['target_end']})")
    margin_min = None
    if entry.get("sealed_at") is not None:
        margin_min = (opens - _when(entry["sealed_at"])).total_seconds() / 60
    status = entry["status"]
    scored = status in ("resolved", "skipped_roll", "expired")
    if scored and (margin_min is None or margin_min * 60 <= vr.SEAL_MARGIN.total_seconds()):
        problems.append(f"{status} but not sealed more than {vr.SEAL_MARGIN} before its window opened")
    rolls = spans_roll(base, end)
    if (status == "skipped_roll") != rolls and status in ("resolved", "skipped_roll"):
        problems.append("skipped_roll does not match the roll rule" if status == "skipped_roll"
                        else "resolved although its window spans a contract roll")
    realized = qlike = None
    if status == "resolved":
        closes = [price for _, price in entry["closes"]]
        realized = vr.realized_vol_pct(closes)
        if abs(realized - entry["realized_pct"]) > REALIZED_TOLERANCE:
            problems.append(f"realized_pct {entry['realized_pct']} but its closes give {realized:.4f}")
        qlike = vr.qlike(entry["realized_pct"], entry["forecast_pct"])
    fit = recomputed_forecast(entry)
    if fit is not None:
        median, forecast = fit
        if entry.get("median_pct") is not None and abs(median - entry["median_pct"]) >= PRINT_TOLERANCE:
            problems.append(f"its stored fit gives median {median:.2f}, not {entry['median_pct']}")
        if abs(forecast - entry["forecast_pct"]) >= PRINT_TOLERANCE:
            problems.append(f"its stored fit gives forecast {forecast:.2f}, not {entry['forecast_pct']}")
    facts = {
        "as_of": entry["as_of"], "forecast": entry["forecast_pct"], "window": f"{entry['target_start']}..{end}",
        "opens": opens, "margin_min": margin_min, "status": status, "spans_roll": rolls,
        "realized": entry.get("realized_pct"), "qlike": qlike,
        "in_band": (entry["lo80_pct"] <= entry["realized_pct"] <= entry["hi80_pct"]
                    if status == "resolved" and entry.get("lo80_pct") is not None else None),
        "fit": "reproduces" if fit is not None and not any("stored fit" in p for p in problems)
        else ("does not reproduce" if fit is not None else "not stored"),
    }
    return facts, problems


def _row(facts: dict[str, Any]) -> str:
    margin = f"{facts['margin_min']:,.0f} min" if facts["margin_min"] is not None else "unsealed"
    realized = f"{facts['realized']:.2f}" if facts["realized"] is not None else "—"
    qlike = f"{facts['qlike']:.3f}" if facts["qlike"] is not None else "—"
    band = {True: "in", False: "out", None: "—"}[facts["in_band"]]
    return (f"{facts['as_of']}  {facts['forecast']:>6.1f}  {facts['window']}  {margin:>11}  "
            f"{facts['status']:<12}  {realized:>8}  {qlike:>6}  {band:>4}  {facts['fit']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the sealed live record entry by entry.")
    parser.add_argument("record", help="the record file, or - for standard input")
    parser.add_argument("--latest", type=int, default=0, help="print only the newest N entries (all are checked)")
    args = parser.parse_args(argv)
    text = sys.stdin.read() if args.record == "-" else Path(args.record).read_text(encoding="utf-8")
    try:
        record = vr.validate_record(json.loads(text, parse_constant=vr._reject_constant))
    except ValueError as exc:
        print(f"FAIL: the record does not satisfy its contract: {exc}")
        return 1
    entries = record["entries"]
    results = [check_entry(entry) for entry in entries]
    print(f"Live record started {record['started']}: {len(entries)} entries "
          f"(seal margin required: more than {vr.SEAL_MARGIN.total_seconds() / 60:g} min)")
    print("as_of       forecast  scored window          sealed before  status        realized   QLIKE  band  fit")
    shown = results[-args.latest:] if args.latest > 0 else results
    for facts, _ in shown:
        print(_row(facts))
    failures = [(facts["as_of"], problem) for facts, problems in results for problem in problems]
    # The dashboard's figure: the resolved entries of the pinned model version (summary["champion"]).
    champion = [(entry, facts) for entry, (facts, _) in zip(entries, results)
                if facts["status"] == "resolved" and entry["model_version"] == vr.PROMOTION_MODEL_VERSION]
    if champion:
        mean_qlike = sum(facts["qlike"] for _, facts in champion) / len(champion)
        independent = len(vr.independent_entries([entry for entry, _ in champion]))
        print(f"{vr.PROMOTION_MODEL_VERSION}: {len(champion)} resolved, mean QLIKE {mean_qlike:.4f}, "
              f"{independent} with non-overlapping windows")
    breaks = vr.chain_breaks(entries)
    linked = sum(entry.get("previous_sha256") is not None for entry in entries)
    print(f"Hash chain: {linked} of {len(entries)} entries linked"
          + (f", broken at {', '.join(breaks)}" if breaks else ", no break"))
    by_as_of = {entry["as_of"]: entry for entry in entries}
    failures += [(as_of, vr.chain_break_reason(by_as_of[as_of], first=as_of == entries[0]["as_of"]))
                 for as_of in breaks]
    for as_of, problem in failures:
        print(f"FAIL {as_of}: {problem}")
    print("OK: every entry passes" if not failures else f"{len(failures)} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
