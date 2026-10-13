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
- The session is the one the PRICE belongs to: the quote's exchange time (contract.market_time),
  not the time the job ran. Nothing is recorded or resolved unless that quote is from the session
  trading when the payload was frozen. A weekend or holiday run would otherwise re-record the
  previous session's closing price as a "new" call (the pre-2026-09 record holds several such
  duplicates), and a run just after the 18:00 ET reopen would book the 17:00 close as the next
  session's price.
- A call is recorded only when the 1W forecast was built from the bar of the session before its
  own, as the validated backtest's are. From the 18:00 ET reopen until midnight the session that
  just settled is not in Yahoo's data yet, so those runs leave the session to the first run after
  midnight ET.
- A call resolves in the session exactly RESOLUTION_TRADING_DAYS after its entry session. If no
  run lands in that session or the next one (GitHub schedules are best-effort), the call is marked
  skipped_late rather than scored at an arbitrary later price.
- A call is scored only if the same contract was priced at entry and at resolution. The payload's
  label names the contract actually quoted (oil.get_current_wti_contract checks it against the
  contracts' own quotes near a roll, because Yahoo's live CL=F moves to the next contract a few
  sessions before the last trade date while its daily bars do not). Rows recorded without that
  guarantee (no contract_basis) also keep the calendar check.
- Only directional calls (the dashboard's LONG/SHORT stance) count toward the hit rate. NEUTRAL is
  "no trade" and is recorded but never scored.
- Daily calls with a 5-session horizon overlap, so the summary also reports the number of
  NON-overlapping scored calls and their hit rate; that count is what the ">= 18 to validate" gate
  uses.

Usage (CI, after freeze.py):
    python backend/live_record.py --data public/data.json
"""

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone

try:
    from .contract_calendar import (
        business_days_between, market_is_open, shift_business_days, spans_roll, trading_date,
    )
    from .safe_paths import data_json_path, public_json_path
except ImportError:  # Direct invocation: python backend/live_record.py
    from contract_calendar import (
        business_days_between, market_is_open, shift_business_days, spans_roll, trading_date,
    )
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


def _num(value):
    """The dashboard's num() (src/format.js): a finite number, numeric strings included, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _has_kelly_edge(win_rate_pct, profit_factor) -> bool:
    """The dashboard's computeSizing() edge test: a positive full-Kelly fraction, i.e. profit factor > 1.

    Full Kelly p - (1 - p) / payoff, with payoff = pf (1 - p) / p, is exactly p (pf - 1) / pf. The
    closed form is used so a break-even pf of 1.0 is no edge (the long form leaves a ~1e-17 residue).
    """
    win_rate, pf = _num(win_rate_pct), _num(profit_factor)
    if win_rate is None or pf is None or win_rate <= 0 or win_rate >= 100 or pf <= 0:
        return False
    p = win_rate / 100
    full_kelly = p * (pf - 1) / pf
    return math.isfinite(full_kelly) and full_kelly > 0


def _one_week_model_pct(data: dict):
    """The dashboard's getModelTarget() % move: the 1W model value against the snapshot's price."""
    future = ((((data.get("unified_data") or {}).get("predicted") or {}).get("future") or {})
              .get("by_horizon") or {}).get("1w") or {}
    predictions = data.get("multi_horizon_predictions") or {}
    value = _num(future.get("value"))
    if value is None:
        value = _num((predictions.get("predictions") or {}).get("1w"))
    if value is None or value <= 0:
        return None
    ref = _num(data.get("current_price"))
    if ref is not None and ref > 0:
        return (value - ref) / ref * 100
    return _num((predictions.get("percentage_changes") or {}).get("1w"))


def one_week_stance(data: dict) -> dict:
    """The dashboard's 1W desk stance (src/App.jsx DeskSection), shared by this recorder and signal_alert.py.

    Same inputs and rule as the dashboard, so the three can never disagree about what was published:
    a LONG/SHORT lean needs a significant walk-forward model (`is True`), a positive Kelly edge from
    its 1W P&L stats (win rate and profit factor after costs), and a 1W model move beyond +-0.6%
    against the snapshot's price, computed from the model value rather than the one-decimal display
    percentage (which silently moved the gate to about +-0.65%).
    """
    metrics = (data.get("performance_metrics") or {}).get("by_horizon", {}).get("1w", {}) or {}
    is_significant = metrics.get("wf_is_significant") is True
    has_edge = is_significant and _has_kelly_edge(metrics.get("wf_pnl_win_rate"), metrics.get("wf_pnl_profit_factor"))
    pct = _one_week_model_pct(data)
    if has_edge and pct is not None and pct > CONVICTION_GATE_PCT:
        stance = "LONG"
    elif has_edge and pct is not None and pct < -CONVICTION_GATE_PCT:
        stance = "SHORT"
    else:
        stance = "NEUTRAL"
    return {"pct": pct if pct is not None else 0.0, "is_significant": is_significant,
            "has_edge": has_edge, "stance": stance}


def _model_bar_date(data: dict):
    """Newest completed bar the 1D/1W models forecast from (oil.py publishes it with the daily history source)."""
    sources = (data.get("multi_horizon_predictions") or {}).get("market_data_sources") or {}
    value = (sources.get("daily_history") or {}).get("model_bar_date")
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def extract_call(data: dict) -> dict:
    """Pull the current 1W call from a frozen data.json payload.

    The entry is dated by the quote's own exchange time (contract.market_time) when the payload has
    it, else by the freeze time.
    """
    signal = one_week_stance(data)
    contract = data.get("contract") or {}
    if not isinstance(contract, dict):
        contract = {"symbol": str(contract)}
    frozen_at = _parse_utc(str(data.get("frozen_at") or datetime.now(timezone.utc).isoformat()))
    try:
        entry_at = _parse_utc(contract["market_time"]) if contract.get("market_time") else frozen_at
    except ValueError:
        entry_at = frozen_at
    session = trading_date(entry_at).isoformat()
    model_bar = _model_bar_date(data)
    return {
        "date": session,
        "trading_date": session,
        "entry_at": entry_at.isoformat(),
        "frozen_at": frozen_at.isoformat(),
        "contract": contract.get("symbol"),
        "contract_basis": contract.get("contract_basis"),
        "model_bar_date": model_bar.isoformat() if model_bar else None,
        "entry_price": float(data.get("current_price") or 0),
        "forecast_pct": round(signal["pct"], 3),
        "stance": signal["stance"],
        "wf_is_significant": signal["is_significant"],
        "eligible_for_validation": signal["is_significant"] and signal["stance"] in ("LONG", "SHORT"),
        "resolved": False,
    }


def quote_is_from_open_session(call: dict) -> bool:
    """Whether the call's price is from the session that was trading when the payload was frozen."""
    entry_at, frozen_at = _parse_utc(call["entry_at"]), _parse_utc(call.get("frozen_at") or call["entry_at"])
    return market_is_open(entry_at) and market_is_open(frozen_at) and trading_date(entry_at) == trading_date(frozen_at)


def forecast_uses_decision_bar(call: dict) -> bool:
    """Whether the forecast was built from the bar of the session before the call's own (as in the backtest)."""
    if not call.get("model_bar_date"):
        return False
    session = date.fromisoformat(call["trading_date"])
    return date.fromisoformat(call["model_bar_date"]) >= shift_business_days(session, -1)


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

    `now` is the exchange time of the current price; the caller resolves only when that quote is
    from the open session, so `current_price` belongs to the session `trading_date(now)`.
    `current_symbol` names the contract that price is for.
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
        rolled = call.get("contract") != current_symbol
        if not call.get("contract_basis"):
            # Labelled by the calendar or an older heuristic, not checked against the quote.
            rolled = rolled or spans_roll(entry_session, session)
        if rolled:
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


def _non_overlapping(calls: list) -> list:
    """Greedy subset of scored calls whose entry-to-resolution windows do not overlap."""
    windows = []
    for c in calls:
        try:
            start = _entry_session(c)
            end = date.fromisoformat(c.get("resolution_trading_date") or c["resolution_date"])
        except (KeyError, TypeError, ValueError):
            continue
        windows.append((start, end, c))
    chosen, last_end = [], None
    for start, end, c in sorted(windows, key=lambda w: (w[0], w[1])):
        if last_end is None or start >= last_end:
            chosen.append(c)
            last_end = end
    return chosen


def _hit_rate_pct(calls: list):
    return round(sum(1 for c in calls if c["hit"]) / len(calls) * 100.0, 1) if calls else None


def summarize(record: dict) -> dict:
    calls = record["calls"]
    scored = [
        c for c in calls
        if c.get("resolved") and "hit" in c and _is_validation_eligible(c)
    ]
    independent = _non_overlapping(scored)
    hits = sum(1 for c in scored if c["hit"])
    previous_updated_at = (record.get("summary") or {}).get("updated_at")
    summary = {
        "n_calls": len(calls),
        "n_resolved_directional": len(scored),
        "n_independent_directional": len(independent),
        "min_independent_to_validate": MIN_INDEPENDENT_TO_VALIDATE,
        "n_hits": hits,
        # Over every scored call; those overlap (five-session horizons recorded daily).
        "hit_rate_pct": _hit_rate_pct(scored),
        # Over the non-overlapping calls counted by n_independent_directional: the independent evidence.
        "hit_rate_independent_pct": _hit_rate_pct(independent),
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

    if quote_is_from_open_session(call):
        resolve_calls(record, call["entry_at"], call["contract"], call["entry_price"])
        if any(c.get("trading_date", c["date"]) == call["trading_date"] for c in record["calls"]):
            print(f"live_record: call for session {call['trading_date']} already recorded")
        elif not forecast_uses_decision_bar(call):
            print(f"live_record: the 1W forecast was built from the {call['model_bar_date'] or 'unknown'} bar, "
                  f"not the bar before session {call['trading_date']} — not recorded; a later run records it")
        else:
            record["calls"].append(call)
            print(f"live_record: recorded session {call['trading_date']} {call['stance']} "
                  f"{call['forecast_pct']:+.2f}% @ ${call['entry_price']:.2f} ({call['contract']})")
    else:
        # The quote is the last price of a finished session (or the market is closed); neither a
        # new entry nor a resolution may be priced from it.
        print("live_record: quote not from the open session — no call recorded or resolved this run")

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
