#!/usr/bin/env python3
"""
Git-auditable live track record for the 1W direction signal.

Each CI run records at most one 1W call per UTC day (entry price + forecast) into
data/live_track_record.json, and resolves any call that is at least 168 hours old
against the current price. The workflow commits the file back to main, so every
entry and every resolution is timestamped by a git commit that cannot be back-dated.
This is the evidence a backtest can never provide: the record only exists forward.

Resolution rules (conservative by construction):
- A call is scored only if the front contract is unchanged between entry and
  resolution; calls spanning a contract roll are marked skipped (roll basis would
  contaminate the realized move).
- Only directional calls (LONG/SHORT lean, |forecast| > 0.6%) count toward the hit
  rate. NEUTRAL is "no trade" and is recorded but never scored.
- The resolution price is the frozen price of the first scheduled run >= 168 hours
  later (normally within the workflow's four-hour cadence).

Usage (CI, after freeze.py):
    python backend/live_record.py --data public/data.json
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

try:
    from .safe_paths import data_json_path, public_json_path
except ImportError:  # Direct invocation: python backend/live_record.py
    from safe_paths import data_json_path, public_json_path

RECORD_PATH = data_json_path("live_track_record.json")
RESOLUTION_DAYS = 7
CONVICTION_GATE_PCT = 0.6  # same gate as the dashboard stance
# Every record currently in the repository was created after the corrected,
# leakage-free backtest retracted the directional edge. Legacy records did not
# store significance, so this cutoff keeps those audit rows but prevents them from
# being presented as validated directional evidence.
RETRACTION_EFFECTIVE_DATE = "2026-06-06"


def _parse_utc(value: str) -> datetime:
    """Parse an ISO timestamp (or legacy YYYY-MM-DD) as an aware UTC datetime."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def extract_call(data: dict) -> dict:
    """Pull the current 1W call from a frozen data.json payload."""
    pct = float(
        data.get("multi_horizon_predictions", {})
            .get("percentage_changes", {})
            .get("1w", 0) or 0
    )
    h1w = data.get("performance_metrics", {}).get("by_horizon", {}).get("1w", {})
    is_significant = h1w.get("wf_is_significant") is True
    if is_significant and pct > CONVICTION_GATE_PCT:
        stance = "LONG"
    elif is_significant and pct < -CONVICTION_GATE_PCT:
        stance = "SHORT"
    else:
        stance = "NEUTRAL"
    contract = data.get("contract") or {}
    entry_at = str(data.get("frozen_at") or datetime.now(timezone.utc).isoformat())
    entry_at = _parse_utc(entry_at).isoformat()
    return {
        "date": entry_at[:10],
        "entry_at": entry_at,
        "contract": contract.get("symbol") if isinstance(contract, dict) else str(contract),
        "entry_price": float(data.get("current_price") or 0),
        "forecast_pct": round(pct, 3),
        "stance": stance,
        "wf_is_significant": is_significant,
        "eligible_for_validation": is_significant and stance in ("LONG", "SHORT"),
        "resolved": False,
    }


def load_record() -> dict:
    if RECORD_PATH.exists():
        try:
            rec = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
            if isinstance(rec, dict) and isinstance(rec.get("calls"), list):
                return rec
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Refusing to overwrite unreadable live record: {exc}") from exc
        raise ValueError("Refusing to overwrite malformed live record: expected a calls list")
    return {"calls": []}


def resolve_calls(record: dict, today: str, current_symbol: str, current_price: float) -> None:
    """Score every unresolved call that has reached the resolution horizon."""
    resolution_at = _parse_utc(today)
    for call in record["calls"]:
        if call.get("resolved"):
            continue
        entry_at = _parse_utc(call.get("entry_at") or call["date"])
        if resolution_at - entry_at < timedelta(days=RESOLUTION_DAYS):
            continue
        call["resolved"] = True
        call["resolution_date"] = resolution_at.date().isoformat()
        call["resolution_at"] = resolution_at.isoformat()
        if call.get("contract") != current_symbol:
            call["skipped_contract_roll"] = True
            continue
        entry = float(call.get("entry_price") or 0)
        if entry <= 0 or current_price <= 0:
            call["skipped_contract_roll"] = True
            continue
        realized_pct = (current_price - entry) / entry * 100.0
        call["resolution_price"] = round(current_price, 2)
        call["realized_pct"] = round(realized_pct, 3)
        if call.get("stance") in ("LONG", "SHORT"):
            predicted_up = call["stance"] == "LONG"
            call["hit"] = bool(predicted_up == (realized_pct > 0))


def _is_validation_eligible(call: dict) -> bool:
    """Return whether a resolved directional call represents a valid signal."""
    explicit = call.get("eligible_for_validation")
    if explicit is not None:
        return explicit is True
    # Legacy rows before the retraction may remain countable. Rows on/after the
    # retraction are retained as audit evidence but must not inflate performance.
    return str(call.get("date", "")) < RETRACTION_EFFECTIVE_DATE


def summarize(record: dict) -> dict:
    calls = record["calls"]
    scored = [
        c for c in calls
        if c.get("resolved") and "hit" in c and _is_validation_eligible(c)
    ]
    hits = sum(1 for c in scored if c["hit"])
    previous_updated_at = (record.get("summary") or {}).get("updated_at")
    summary = {
        "n_calls": len(calls),
        "n_resolved_directional": len(scored),
        "n_hits": hits,
        "hit_rate_pct": round(hits / len(scored) * 100.0, 1) if scored else None,
        "n_pending": sum(1 for c in calls if not c.get("resolved")),
        "n_skipped_roll": sum(1 for c in calls if c.get("skipped_contract_roll")),
        "n_neutral": sum(1 for c in calls if c.get("stance") == "NEUTRAL"),
        "n_ineligible_directional": sum(
            1 for c in calls
            if c.get("stance") in ("LONG", "SHORT") and not _is_validation_eligible(c)
        ),
        "first_call_date": calls[0]["date"] if calls else None,
        # Preserve this when no call was added/resolved. main() touches it only
        # alongside a semantic record change, preventing timestamp-only commits.
        "updated_at": previous_updated_at or datetime.now(timezone.utc).isoformat(),
    }
    record["summary"] = summary
    return summary


def main():
    parser = argparse.ArgumentParser(description="Record + resolve 1W live calls.")
    parser.add_argument("--data", default="public/data.json", help="frozen payload path")
    args = parser.parse_args()

    data_path = public_json_path(args.data)
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    if payload.get("error") or not payload.get("current_price"):
        print("live_record: payload not usable — skipping", file=sys.stderr)
        return

    call = extract_call(payload)
    record = load_record()
    original_record = json.dumps(record, sort_keys=True)

    resolve_calls(record, call["entry_at"], call["contract"], call["entry_price"])

    if not any(c["date"] == call["date"] for c in record["calls"]):
        record["calls"].append(call)
        print(f"live_record: recorded {call['date']} {call['stance']} "
              f"{call['forecast_pct']:+.2f}% @ ${call['entry_price']:.2f} ({call['contract']})")
    else:
        print(f"live_record: call for {call['date']} already recorded")

    summary = summarize(record)
    record_changed = json.dumps(record, sort_keys=True) != original_record
    if record_changed:
        summary["updated_at"] = datetime.now(timezone.utc).isoformat()
        RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
        RECORD_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    else:
        print("live_record: no record changes — file left untouched")
    # freeze.py runs before this recorder so a call can use the exact frozen quote.
    # Keep the deploy artifact in sync with the newly computed record summary during
    # the same workflow instead of showing the previous cycle for four more hours.
    if payload.get("live_record") != summary:
        payload["live_record"] = summary
        data_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"live_record: {summary['n_resolved_directional']} resolved directional, "
          f"hit rate {summary['hit_rate_pct']}%, {summary['n_pending']} pending, "
          f"{summary['n_neutral']} neutral")


if __name__ == "__main__":
    main()
