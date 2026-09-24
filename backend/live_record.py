#!/usr/bin/env python3
"""
Git-auditable live track record for the 1W direction signal.

Each CI run records at most one 1W call per CME trading session (entry price + forecast) into
data/live_track_record.json, and resolves every call whose fifth trading session has arrived
against the current price. The workflow persists the mutable file on the dedicated live-data
branch, so every entry and resolution remains timestamped by an auditable git commit without
granting automation a path around main-branch protection. This is the evidence a backtest can
never provide: the record only exists forward.

Rules (conservative by construction; all dates are CME trading sessions, see contract_calendar):
- Calls are recorded only while the market is open. A weekend or holiday run would otherwise
  re-record the previous session's closing price as a "new" call (the pre-2026-09 record holds
  several such duplicates).
- A call resolves in the session exactly RESOLUTION_TRADING_DAYS after its entry session. If no
  run lands in that session or the next one (GitHub schedules are best-effort), the call is marked
  skipped_late rather than scored at an arbitrary later price.
- A call is scored only if CL=F points at the same contract at entry and resolution. The check
  uses the exchange calendar, not the payload's label: CL=F follows the expiring contract through
  its last trade date, so a label-based check alone skipped clean calls and scored spliced ones.
- Only directional calls (LONG/SHORT, |forecast| > 0.6%, significant model) count toward the hit
  rate. NEUTRAL is "no trade" and is recorded but never scored.
- Daily calls with a 5-session horizon overlap, so the summary also reports the number of
  NON-overlapping scored calls; that is the count the ">= 18 to validate" gate uses.

Usage (CI, after freeze.py):
    python backend/live_record.py --data public/data.json
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone

try:
    from .contract_calendar import business_days_between, market_is_open, spans_roll, trading_date
    from .safe_paths import data_json_path, public_json_path
except ImportError:  # Direct invocation: python backend/live_record.py
    from contract_calendar import business_days_between, market_is_open, spans_roll, trading_date
    from safe_paths import data_json_path, public_json_path

RECORD_PATH = data_json_path("live_track_record.json")
RESOLUTION_TRADING_DAYS = 5
MAX_LATE_SESSIONS = 1       # resolve in the target session or the next one, never later
CONVICTION_GATE_PCT = 0.6   # same gate as the dashboard stance
MIN_INDEPENDENT_TO_VALIDATE = 18
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


def one_week_stance(data: dict) -> dict:
    """The dashboard's 1W stance, shared by this recorder and signal_alert.py.

    The gate compares the UNROUNDED forecast when the payload carries it: the display values are
    rounded to one decimal, which silently moved the +-0.6% gate to about +-0.65%.
    """
    mh = data.get("multi_horizon_predictions") or {}
    exact = (mh.get("percentage_changes_exact") or {}).get("1w")
    shown = (mh.get("percentage_changes") or {}).get("1w")
    raw = exact if isinstance(exact, (int, float)) else shown
    pct = float(raw) if isinstance(raw, (int, float)) else 0.0
    h1w = (data.get("performance_metrics") or {}).get("by_horizon", {}).get("1w", {})
    is_significant = h1w.get("wf_is_significant") is True
    if is_significant and pct > CONVICTION_GATE_PCT:
        stance = "LONG"
    elif is_significant and pct < -CONVICTION_GATE_PCT:
        stance = "SHORT"
    else:
        stance = "NEUTRAL"
    return {"pct": pct, "is_significant": is_significant, "stance": stance}


def extract_call(data: dict) -> dict:
    """Pull the current 1W call from a frozen data.json payload."""
    signal = one_week_stance(data)
    contract = data.get("contract") or {}
    entry_at = _parse_utc(str(data.get("frozen_at") or datetime.now(timezone.utc).isoformat()))
    session = trading_date(entry_at).isoformat()
    return {
        "date": session,
        "trading_date": session,
        "entry_at": entry_at.isoformat(),
        "contract": contract.get("symbol") if isinstance(contract, dict) else str(contract),
        "entry_price": float(data.get("current_price") or 0),
        "forecast_pct": round(signal["pct"], 3),
        "stance": signal["stance"],
        "wf_is_significant": signal["is_significant"],
        "eligible_for_validation": signal["is_significant"] and signal["stance"] in ("LONG", "SHORT"),
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


def _entry_session(call: dict) -> date:
    if call.get("trading_date"):
        return date.fromisoformat(call["trading_date"])
    # Legacy rows stored only a UTC timestamp/date; map it to its CME session.
    return trading_date(_parse_utc(call.get("entry_at") or call["date"]))


def resolve_calls(record: dict, now: str, current_symbol: str, current_price: float) -> None:
    """Score every unresolved call whose resolution session has arrived.

    `now` is the moment of the current (frozen) price; the caller only resolves while the market
    is open, so `current_price` belongs to the session `trading_date(now)`.
    """
    resolution_at = _parse_utc(now)
    session = trading_date(resolution_at)
    for call in record["calls"]:
        if call.get("resolved"):
            continue
        entry_session = _entry_session(call)
        elapsed = business_days_between(entry_session, session)
        if elapsed < RESOLUTION_TRADING_DAYS:
            continue
        call["resolved"] = True
        call["resolution_date"] = session.isoformat()
        call["resolution_trading_date"] = session.isoformat()
        call["resolution_at"] = resolution_at.isoformat()
        if elapsed > RESOLUTION_TRADING_DAYS + MAX_LATE_SESSIONS:
            call["skipped_late"] = True
            continue
        if spans_roll(entry_session, session) or call.get("contract") != current_symbol:
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


def _count_non_overlapping(calls: list) -> int:
    """Greedy count of scored calls whose entry-to-resolution windows do not overlap."""
    windows = []
    for c in calls:
        try:
            start = _entry_session(c)
            end = date.fromisoformat(c.get("resolution_trading_date") or c["resolution_date"])
        except (KeyError, TypeError, ValueError):
            continue
        windows.append((start, end))
    count, last_end = 0, None
    for start, end in sorted(windows):
        if last_end is None or start >= last_end:
            count += 1
            last_end = end
    return count


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
        "n_independent_directional": _count_non_overlapping(scored),
        "min_independent_to_validate": MIN_INDEPENDENT_TO_VALIDATE,
        "n_hits": hits,
        "hit_rate_pct": round(hits / len(scored) * 100.0, 1) if scored else None,
        "n_pending": sum(1 for c in calls if not c.get("resolved")),
        "n_skipped_roll": sum(1 for c in calls if c.get("skipped_contract_roll")),
        "n_skipped_late": sum(1 for c in calls if c.get("skipped_late")),
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

    if market_is_open(_parse_utc(call["entry_at"])):
        resolve_calls(record, call["entry_at"], call["contract"], call["entry_price"])
        if not any(c.get("trading_date", c["date"]) == call["trading_date"] for c in record["calls"]):
            record["calls"].append(call)
            print(f"live_record: recorded session {call['trading_date']} {call['stance']} "
                  f"{call['forecast_pct']:+.2f}% @ ${call['entry_price']:.2f} ({call['contract']})")
        else:
            print(f"live_record: call for session {call['trading_date']} already recorded")
    else:
        # The quote is the last close of a finished session; neither a new entry nor a
        # resolution may be priced from it.
        print("live_record: market closed — no call recorded or resolved this run")

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
    print(f"live_record: {summary['n_resolved_directional']} resolved directional "
          f"({summary['n_independent_directional']} non-overlapping), "
          f"hit rate {summary['hit_rate_pct']}%, {summary['n_pending']} pending, "
          f"{summary['n_neutral']} neutral")


if __name__ == "__main__":
    main()
