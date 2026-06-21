#!/usr/bin/env python3
"""
Signal change detector for the 1W WTI direction model.

Reads the current frozen data.json, compares the 1W stance to the last
known state, and emails navnoorquant@gmail.com if the stance changed.
Updates data/signal_state.json on every run so the next comparison is
against the current state.

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
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path


STATE_PATH = Path("data/signal_state.json")
SITE_URL = "https://navnoorbawa.github.io/WTI-Crude-Oil-Futures/"
GMAIL_USER = os.environ.get("GMAIL_USER", "navnoorquant@gmail.com")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "navnoorquant@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def extract_signal(data: dict) -> dict:
    """Pull the current 1W signal from a frozen data.json payload."""
    h1w = data.get("performance_metrics", {}).get("by_horizon", {}).get("1w", {})
    pct = float(
        data.get("multi_horizon_predictions", {})
            .get("percentage_changes", {})
            .get("1w", 0) or 0
    )
    is_sig = h1w.get("wf_is_significant", False)
    if is_sig and pct > 0.6:
        stance = "LONG LEAN"
    elif is_sig and pct < -0.6:
        stance = "SHORT LEAN"
    else:
        stance = "NEUTRAL"

    contract = data.get("contract") or {}
    return {
        "stance": stance,
        "fc_pct": round(pct, 3),
        "price": data.get("current_price"),
        "symbol": contract.get("symbol") if isinstance(contract, dict) else str(contract),
        "sharpe": h1w.get("wf_pnl_sharpe"),
        "win_rate": h1w.get("wf_pnl_win_rate"),
        "profit_factor": h1w.get("wf_pnl_profit_factor"),
        "mean_pnl": h1w.get("wf_pnl_mean_per_trade"),
        "accuracy": h1w.get("display_accuracy"),
        "live_n": h1w.get("live_total_predictions", 0),
        "ci": h1w.get("wf_ci_95"),
        "frozen_at": data.get("frozen_at"),
    }


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"stance": None}


def save_state(sig: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({
        "stance": sig["stance"],
        "fc_pct": sig["fc_pct"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))


def send_email(prev_stance: str | None, cur: dict) -> None:
    if not GMAIL_APP_PASSWORD:
        print("GMAIL_APP_PASSWORD not set — skipping email")
        return

    arrow = "↑" if cur["stance"] == "LONG LEAN" else "↓" if cur["stance"] == "SHORT LEAN" else "→"
    subject = f"WTI 1W model state: {prev_stance or 'INIT'} → {cur['stance']} {arrow}  |  ${cur['price']:.2f}  (edge retracted)"

    ci_str = f"[{cur['ci'][0]}, {cur['ci'][1]}]" if cur.get("ci") else "n/a"

    live_note = (
        f"Live track record: {cur['live_n']} evaluated 1W predictions — too early for live validation."
        if cur["live_n"] < 18 else
        f"Live track record: {cur['live_n']} evaluated 1W predictions."
    )

    # No position sizing is emitted: the edge is retracted, so Kelly/contract sizing would be
    # meaningless (and the corrected profit factor < 1 makes it undefined anyway).

    # The model is non-significant (NEUTRAL), in which case these metrics may be
    # absent — format defensively so an alert on a NEUTRAL transition can't crash.
    acc_str = f"{cur['accuracy']:.1f}%" if isinstance(cur.get("accuracy"), (int, float)) else "n/a"
    sharpe_str = f"{cur['sharpe']:.2f}" if isinstance(cur.get("sharpe"), (int, float)) else "n/a"

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

Corrected (purged) backtest, 5y, 199 OOS, $100/trade:
  Direction accuracy: {acc_str}  (CI {ci_str})
  Sharpe: {sharpe_str}   not statistically significant

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
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            smtp.sendmail(GMAIL_USER, ALERT_EMAIL, msg.as_string())
        print(f"Email sent: {subject}")
    except Exception as exc:
        print(f"Email failed: {exc}", file=sys.stderr)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Detect 1W signal changes and alert.")
    parser.add_argument("--data", default="public/data.json", help="Frozen data.json path")
    parser.add_argument("--force", action="store_true", help="Send alert even if stance unchanged")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"Data file not found: {data_path}", file=sys.stderr)
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

    changed = prev.get("stance") != cur["stance"]
    save_state(cur)

    if changed or args.force:
        print("Signal changed — sending alert")
        send_email(prev.get("stance"), cur)
    else:
        print("No change")


if __name__ == "__main__":
    main()
