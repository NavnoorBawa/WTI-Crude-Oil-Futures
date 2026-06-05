#!/usr/bin/env python3
"""
Freeze the live API payload into static JSON for GitHub Pages hosting.

This is the "frozen Flask" pattern: instead of running a live server, we render the
/data and /scenario endpoints in-memory with Flask's test client and write the output
to static files. A GitHub Actions cron re-runs this on a schedule, so the static site
always serves the most recent snapshot with zero running infrastructure.

The React frontend (built with VITE_STATIC_DATA=true) fetches these files directly,
so no backend server is needed at runtime.

Usage:
    python freeze.py                 # writes public/data.json + public/scenario.json
    python freeze.py --out public     # custom output directory
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Must be set BEFORE importing the server so the module-level flag is read as eager.
# Eager warmup makes initialize_oil_system() generate predictions synchronously instead
# of deferring them to a background thread (which a one-shot freeze job cannot wait on).
os.environ.setdefault("EAGER_ML_WARMUP", "true")


def freeze(out_dir: Path) -> dict:
    from backend import server

    # Synchronous init. Unlike the threaded startup_initialization(), initialize_oil_system()
    # RAISES on failure — so a rate-limited / data-unavailable run fails the CI job visibly
    # and the previous good snapshot stays live, rather than publishing an empty page.
    print("Initializing oil system (synchronous warmup)...", flush=True)
    server.initialize_oil_system()
    server._startup_ready.set()
    server.system_state["ml_ready"] = True

    client = server.app.test_client()

    print("Rendering /data ...", flush=True)
    data_resp = client.get("/data")
    if data_resp.status_code != 200:
        raise SystemExit(
            f"/data returned HTTP {data_resp.status_code}: "
            f"{data_resp.get_data(as_text=True)[:400]}"
        )
    data = data_resp.get_json()
    if not data or data.get("error"):
        raise SystemExit(f"/data returned an error payload: {(data or {}).get('error')}")

    print("Rendering /scenario ...", flush=True)
    scenario_resp = client.get("/scenario")
    scenario = scenario_resp.get_json() if scenario_resp.status_code == 200 else {}

    # Stamp when this snapshot was frozen so the UI can show an honest "data as of" label.
    frozen_at = datetime.now(timezone.utc).isoformat()
    data["frozen_at"] = frozen_at
    if isinstance(scenario, dict):
        scenario["frozen_at"] = frozen_at

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (out_dir / "scenario.json").write_text(json.dumps(scenario, indent=2), encoding="utf-8")

    contract = data.get("contract")
    symbol = contract.get("symbol") if isinstance(contract, dict) else contract
    print(
        f"✅ Froze snapshot @ {frozen_at}\n"
        f"   contract={symbol}  price=${data.get('current_price')}  "
        f"feed={data.get('feed_status')}\n"
        f"   -> {out_dir / 'data.json'} ({(out_dir / 'data.json').stat().st_size} bytes)",
        flush=True,
    )
    return data


def main():
    parser = argparse.ArgumentParser(description="Freeze API payload to static JSON.")
    parser.add_argument("--out", default="public", help="output directory (default: public)")
    args = parser.parse_args()
    try:
        freeze(Path(args.out))
    except SystemExit:
        raise
    except Exception as exc:  # surface any failure as a non-zero exit for CI
        print(f"❌ Freeze failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
