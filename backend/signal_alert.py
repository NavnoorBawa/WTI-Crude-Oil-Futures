#!/usr/bin/env python3
"""
Signal change detector for the 1W WTI direction model.

Reads the current frozen data.json, compares the 1W stance to the last
known state, and emails navnoorquant@gmail.com if the stance changed.
Updates data/signal_state.json only when the stance changes. The workflow restores
and persists that mutable file on the dedicated live-data branch, keeping automation
off protected main while preserving the state needed for the next comparison.

Required env vars (add as GitHub Secrets):
  GMAIL_APP_PASSWORD  — Gmail App Password for navnoorquant@gmail.com
                        (Generate at myaccount.google.com > Security > App passwords)
Optional:
  GMAIL_USER          — override sender (default: navnoorquant@gmail.com)
  ALERT_EMAIL         — override recipient (default: navnoorquant@gmail.com)
"""

from __future__ import annotations  # PEP 604 (str | None) needs lazy eval on Python <3.10

import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText

try:
    from .live_record import MIN_INDEPENDENT_TO_VALIDATE, one_week_stance
    from .safe_paths import data_json_path, public_json_path
except ImportError:  # Direct invocation: python backend/signal_alert.py
    from live_record import MIN_INDEPENDENT_TO_VALIDATE, one_week_stance
    from safe_paths import data_json_path, public_json_path


STATE_PATH = data_json_path("signal_state.json")
SITE_URL = "https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/"
GMAIL_USER = os.environ.get("GMAIL_USER", "navnoorquant@gmail.com")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "navnoorquant@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def extract_signal(data: dict) -> dict:
    """Pull the current 1W signal from a frozen data.json payload."""
    h1w = (data.get("performance_metrics") or {}).get("by_horizon", {}).get("1w", {})
    # One stance definition for the dashboard record and the alert (strict `is True` significance,
    # unrounded forecast), so the two can never disagree about what was published.
    signal = one_week_stance(data)
    stance = {"LONG": "LONG LEAN", "SHORT": "SHORT LEAN"}.get(signal["stance"], "NEUTRAL")
    live = data.get("live_record") or {}

    contract = data.get("contract") or {}
    return {
        "stance": stance,
        "fc_pct": round(signal["pct"], 3),
        "price": data.get("current_price"),
        "symbol": contract.get("symbol") if isinstance(contract, dict) else str(contract),
        "sharpe": h1w.get("wf_pnl_sharpe"),
        "win_rate": h1w.get("wf_pnl_win_rate"),
        "profit_factor": h1w.get("wf_pnl_profit_factor"),
        "mean_pnl": h1w.get("wf_pnl_mean_per_trade"),
        "accuracy": h1w.get("display_accuracy"),
        "p_value": h1w.get("wf_p_value"),
        "wf_samples": h1w.get("wf_samples"),
        # The git-committed record (live_record.py), not the server's in-memory counter, which is
        # always empty in the one-shot CI job.
        "live_n": int(live.get("n_independent_directional", live.get("n_resolved_directional", 0)) or 0),
        "live_calls": int(live.get("n_calls", 0) or 0),
        "ci": h1w.get("wf_ci_95"),
        "frozen_at": data.get("frozen_at"),
    }


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Refusing to overwrite unreadable signal state: {exc}") from exc
        if not isinstance(state, dict) or "stance" not in state:
            raise ValueError("Refusing to overwrite malformed signal state")
        return state
    return {"stance": None}


def save_state(sig: dict, previous: dict | None = None) -> bool:
    """Persist a semantic stance change; return whether the file changed."""
    previous = previous if previous is not None else load_state()
    if previous.get("stance") == sig["stance"]:
        return False

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({
        "stance": sig["stance"],
        "fc_pct": sig["fc_pct"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    return True


def send_email(prev_stance: str | None, cur: dict) -> bool:
    """Send an alert, returning True only when delivery succeeded or is disabled."""
    if not GMAIL_APP_PASSWORD:
        print("GMAIL_APP_PASSWORD not set — skipping email")
        return True

    arrow = "↑" if cur["stance"] == "LONG LEAN" else "↓" if cur["stance"] == "SHORT LEAN" else "→"
    subject = f"WTI 1W model state: {prev_stance or 'INIT'} → {cur['stance']} {arrow}  |  ${cur['price']:.2f}  (edge retracted)"

    ci_str = f"[{cur['ci'][0]}, {cur['ci'][1]}]" if cur.get("ci") else "n/a"

    live_note = (
        f"Live track record: {cur['live_calls']} calls recorded, {cur['live_n']} independent scored "
        f"directional calls — too few to validate (need >= {MIN_INDEPENDENT_TO_VALIDATE})."
        if cur["live_n"] < MIN_INDEPENDENT_TO_VALIDATE else
        f"Live track record: {cur['live_n']} independent scored directional calls."
    )

    # No position sizing is emitted: the edge is retracted, so Kelly/contract sizing would be
    # meaningless (and the corrected profit factor < 1 makes it undefined anyway).

    # The model is non-significant (NEUTRAL), in which case these metrics may be
    # absent — format defensively so an alert on a NEUTRAL transition can't crash.
    acc_str = f"{cur['accuracy']:.1f}%" if isinstance(cur.get("accuracy"), (int, float)) else "n/a"
    sharpe_str = f"{cur['sharpe']:.2f}" if isinstance(cur.get("sharpe"), (int, float)) else "n/a"
    p_str = f"p = {cur['p_value']:.2f}" if isinstance(cur.get("p_value"), (int, float)) else "p n/a"
    n_str = f"{cur['wf_samples']} OOS" if cur.get("wf_samples") else "OOS"

    body = f"""WTI 1-Week Model State Change (research notification)
{'='*54}

NOTE: The backtested 1W edge was a look-ahead leakage artifact and has been
RETRACTED. Purged, the signal is a coin flip (~48-52% accuracy, negative Sharpe,
p > 0.2) that loses after costs. This email is a pipeline/engineering demo of the
CI alert, NOT a trade recommendation. There is no validated directional signal.

PREVIOUS STATE:  {prev_stance or 'INIT'}
NEW STATE:       {cur['stance']}

Contract:    {cur['symbol']}
Price now:   ${cur['price']:.2f}
1W model output (reference only): {cur['fc_pct']:+.2f}%

Corrected (purged) walk-forward backtest, {n_str}, $100/trade:
  Direction accuracy: {acc_str}  (CI {ci_str}, {p_str})
  Sharpe: {sharpe_str}

{live_note}

Dashboard: {SITE_URL}
Frozen at: {cur['frozen_at']}

---
Walk-forward research demo. Edge retracted. No execution infrastructure.
"""

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ALERT_EMAIL
    try:
        # smtplib does NOT verify certificates by default (PEP 476 covered HTTP clients only), so
        # without an explicit context the app password would go to anyone able to intercept TLS.
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            smtp.sendmail(GMAIL_USER, ALERT_EMAIL, msg.as_string())
        print(f"Email sent: {subject}")
        return True
    except Exception as exc:
        print(
            f"Email failed error_type={type(exc).__name__}",
            file=sys.stderr,
        )
        return False


def process_signal(cur: dict, prev: dict, force: bool = False) -> bool:
    """Deliver a required alert before persisting the new stance."""
    changed = prev.get("stance") != cur["stance"]
    if changed or force:
        print("Signal changed — sending alert" if changed else "Forced alert — sending")
        if not send_email(prev.get("stance"), cur):
            raise RuntimeError("Configured signal alert delivery failed; state not persisted")
    else:
        print("No change")

    if changed:
        save_state(cur, prev)
    return changed


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Detect 1W signal changes and alert.")
    parser.add_argument("--data", default="public/data.json", help="Frozen data.json path")
    parser.add_argument("--force", action="store_true", help="Send alert even if stance unchanged")
    args = parser.parse_args()

    data_path = public_json_path(args.data)
    if not data_path.exists():
        print("Required dashboard payload is unavailable", file=sys.stderr)
        sys.exit(1)

    data = json.loads(data_path.read_text())
    if data.get("error") or not data.get("current_price"):
        # Mirror live_record.py: a payload without a price is not scoreable. Bail cleanly
        # instead of crashing on a None price format — this step runs in the deploy path,
        # so an unhandled exception here would block the whole refresh/deploy.
        print("signal_alert: payload not usable (no current_price) — skipping", file=sys.stderr)
        return
    cur = extract_signal(data)
    prev = load_state()

    print(f"Previous: {prev.get('stance')!r}")
    print(f"Current:  {cur['stance']!r}  fc={cur['fc_pct']:+.3f}%  price=${cur['price']:.2f}")

    try:
        process_signal(cur, prev, force=args.force)
    except RuntimeError as exc:
        # A failed workflow retries the transition because state was not advanced.
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
