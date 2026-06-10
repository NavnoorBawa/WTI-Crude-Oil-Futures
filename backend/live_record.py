#!/usr/bin/env python3
"""
Git-auditable live track record for the 1W direction signal.

Each CI run records at most one 1W call per UTC day (entry price + forecast) into
data/live_track_record.json, and resolves any call that is >= 7 calendar days old
against the current price. The workflow commits the file back to main, so every
entry and every resolution is timestamped by a git commit that cannot be back-dated.
This is the evidence a backtest can never provide: the record only exists forward.

Resolution rules (conservative by construction):
- A call is scored only if the front contract is unchanged between entry and
  resolution; calls spanning a contract roll are marked skipped (roll basis would
  contaminate the realized move).
- Only directional calls (LONG/SHORT lean, |forecast| > 0.6%) count toward the hit
  rate. NEUTRAL is "no trade" and is recorded but never scored.
- The resolution price is the frozen price of the first run >= 7 days later —
  within an hour of a true 1-week-later mark, the same cadence as the entry.

Usage (CI, after freeze.py):
    python backend/live_record.py --data public/data.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RECORD_PATH = Path("data/live_track_record.json")
RESOLUTION_DAYS = 7
CONVICTION_GATE_PCT = 0.6  # same gate as the dashboard stance


def extract_call(data: dict) -> dict:
    """Pull the current 1W call from a frozen data.json payload."""
    pct = float(
        data.get("multi_horizon_predictions", {})
            .get("percentage_changes", {})
            .get("1w", 0) or 0
    )
    if pct > CONVICTION_GATE_PCT:
        stance = "LONG"
    elif pct < -CONVICTION_GATE_PCT:
        stance = "SHORT"
    else:
        stance = "NEUTRAL"
    contract = data.get("contract") or {}
    return {
        "date": str(data.get("frozen_at", datetime.now(timezone.utc).isoformat()))[:10],
        "contract": contract.get("symbol") if isinstance(contract, dict) else str(contract),
        "entry_price": float(data.get("current_price") or 0),
        "forecast_pct": round(pct, 3),
        "stance": stance,
        "resolved": False,
    }


def load_record() -> dict:
    if RECORD_PATH.exists():
        try:
            rec = json.loads(RECORD_PATH.read_text())
            if isinstance(rec.get("calls"), list):
                return rec
        except Exception:
            pass
    return {"calls": []}


def resolve_calls(record: dict, today: str, current_symbol: str, current_price: float) -> None:
    """Score every unresolved call that has reached the resolution horizon."""
    for call in record["calls"]:
        if call.get("resolved"):
            continue
        age_days = (datetime.fromisoformat(today) - datetime.fromisoformat(call["date"])).days
        if age_days < RESOLUTION_DAYS:
            continue
        call["resolved"] = True
        call["resolution_date"] = today
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


def summarize(record: dict) -> dict:
    calls = record["calls"]
    scored = [c for c in calls if c.get("resolved") and "hit" in c]
    hits = sum(1 for c in scored if c["hit"])
    summary = {
        "n_calls": len(calls),
        "n_resolved_directional": len(scored),
        "n_hits": hits,
        "hit_rate_pct": round(hits / len(scored) * 100.0, 1) if scored else None,
        "n_pending": sum(1 for c in calls if not c.get("resolved")),
        "n_skipped_roll": sum(1 for c in calls if c.get("skipped_contract_roll")),
        "n_neutral": sum(1 for c in calls if c.get("stance") == "NEUTRAL"),
        "first_call_date": calls[0]["date"] if calls else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    record["summary"] = summary
    return summary


def main():
    parser = argparse.ArgumentParser(description="Record + resolve 1W live calls.")
    parser.add_argument("--data", default="public/data.json", help="frozen payload path")
    args = parser.parse_args()

    payload = json.loads(Path(args.data).read_text())
    if payload.get("error") or not payload.get("current_price"):
        print("live_record: payload not usable — skipping", file=sys.stderr)
        return

    call = extract_call(payload)
    record = load_record()

    resolve_calls(record, call["date"], call["contract"], call["entry_price"])

    if not any(c["date"] == call["date"] for c in record["calls"]):
        record["calls"].append(call)
        print(f"live_record: recorded {call['date']} {call['stance']} "
              f"{call['forecast_pct']:+.2f}% @ ${call['entry_price']:.2f} ({call['contract']})")
    else:
        print(f"live_record: call for {call['date']} already recorded")

    summary = summarize(record)
    RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECORD_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"live_record: {summary['n_resolved_directional']} resolved directional, "
          f"hit rate {summary['hit_rate_pct']}%, {summary['n_pending']} pending, "
          f"{summary['n_neutral']} neutral")


if __name__ == "__main__":
    main()
