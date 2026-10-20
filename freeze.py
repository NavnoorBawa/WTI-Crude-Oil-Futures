#!/usr/bin/env python3
"""
Freeze the dashboard payload into static JSON for GitHub Pages hosting.

This is the "frozen Flask" pattern: instead of running a live server, we render the /data endpoint
in-memory with Flask's test client and write the output to static files. A GitHub Actions cron re-runs
this on a schedule, so the static site always serves the most recent snapshot with zero running
infrastructure. The files follow docs/DATA_CONTRACT.md:

  data.json      the live snapshot (schema_version 3). The volatility forecast is REQUIRED: without
                 it the freeze fails, nothing is written, and the previous good site stays live.
  research.json  the reproducible research, copied from committed artifacts
                 (scripts/build_research_bundle.py)
  price.json     a same-origin baseline quote for the live-price overlay

The React frontend (built with VITE_STATIC_DATA=true) fetches these files directly, so no backend
server is needed at runtime.

Usage:
    python freeze.py                 # writes public/data.json, public/research.json, public/price.json
    python freeze.py --out public     # the output directory (it must be public)
"""

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.safe_paths import public_output_directory

RESEARCH_BUILDER = Path(__file__).resolve().parent / "scripts" / "build_research_bundle.py"


def _research_builder():
    """scripts/build_research_bundle.py as a module (standard library only; loaded by path so no
    installed package named `scripts` can stand in for it)."""
    spec = importlib.util.spec_from_file_location("build_research_bundle", RESEARCH_BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _volatility_forecast(data: dict) -> dict:
    """The validated volatility block: the one /data already built, else one more build here.

    Raises SystemExit when there is none: the forecast is the product, so a snapshot without it
    must never replace the published one.
    """
    bundle = data.get("vol_forecast")
    if not bundle:
        try:
            from backend import vol_forecast
            bundle = vol_forecast.forecast_bundle()
        except Exception as exc:
            raise SystemExit(
                f"vol_forecast unavailable (error_type={type(exc).__name__}); "
                "refusing to publish a snapshot without the volatility forecast"
            ) from exc
    if not isinstance(bundle, dict) or not isinstance(bundle.get("live"), dict) \
            or not isinstance(bundle.get("validation"), dict):
        raise SystemExit("vol_forecast is incomplete; refusing to publish it")
    return bundle


def freeze(out_dir: Path) -> dict:
    from backend import server

    # Synchronous init. Unlike the threaded startup_initialization(), initialize_oil_system()
    # RAISES when there is no quote, so a rate-limited / data-unavailable run fails the CI job
    # visibly and the previous good snapshot stays live, rather than publishing an empty page.
    print("Initializing (contract quote + volatility forecast)...", flush=True)
    server.initialize_oil_system()
    server._startup_ready.set()

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

    bundle = _volatility_forecast(data)
    data["vol_forecast"] = bundle
    print(f"   vol_forecast: next-week {bundle['live'].get('direction')} "
          f"@ {bundle['live'].get('forecast_next_week_vol_annualized_pct')}% "
          f"(as of {bundle['live'].get('as_of')}, validation n={bundle['validation'].get('n')})",
          flush=True)

    # Stamp when this snapshot was frozen so the UI can show an honest "data as of" label, and say
    # what it is: a periodic snapshot, not a live feed.
    frozen_at = datetime.now(timezone.utc).isoformat()
    data["frozen_at"] = frozen_at
    data["feed_status"] = "SNAPSHOT"

    # research.json first: write() validates the bundle before writing it, so a broken committed
    # artifact fails the freeze before data.json is touched.
    out_dir.mkdir(parents=True, exist_ok=True)
    research_path = _research_builder().write(out_dir)
    data_path = out_dir / "data.json"
    data_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    # Bake a baseline price.json into the same output dir. Vite copies public/* into
    # dist/, so the dashboard always has a same-origin fallback with this freeze's
    # price + timestamp. The price workflow (scheduled every 15 minutes; GitHub runs it
    # far less often in practice) publishes fresher quotes on the separate live-data
    # branch so those updates do not trigger Pages deployments.
    price = data.get("current_price")
    if isinstance(price, (int, float)) and price > 0:
        pct = data.get("price_change_percent")
        change = data.get("price_change")
        prev_close = round(price - change, 2) if isinstance(change, (int, float)) else None
        contract_block = data.get("contract") if isinstance(data.get("contract"), dict) else {}
        price_payload = {
            "price": round(float(price), 2),
            "prev_close": prev_close,
            "change_pct": round(float(pct), 2) if isinstance(pct, (int, float)) else None,
            # The contract this price is for; the UI only overlays a quote on a snapshot of the
            # same contract (refresh_live_price.py stamps its quotes the same way).
            "contract": contract_block.get("symbol"),
            "fetched_at": frozen_at,
            # The exchange time of the quote itself; the UI keys freshness on this, not on when
            # the job happened to run (a Saturday freeze still carries Friday's close).
            "market_time": contract_block.get("market_time"),
            "source": "freeze snapshot (CL=F)",
        }
        (out_dir / "price.json").write_text(json.dumps(price_payload), encoding="utf-8")

    contract = data.get("contract")
    symbol = contract.get("symbol") if isinstance(contract, dict) else contract
    print(
        f"✅ Froze snapshot @ {frozen_at}\n"
        f"   contract={symbol}  price=${data.get('current_price')}  "
        f"feed={data.get('feed_status')}\n"
        f"   -> {data_path} ({data_path.stat().st_size} bytes)\n"
        f"   -> {research_path} ({research_path.stat().st_size} bytes)",
        flush=True,
    )
    return data


def main():
    parser = argparse.ArgumentParser(description="Freeze API payload to static JSON.")
    parser.add_argument("--out", default="public", help="output directory (default: public)")
    args = parser.parse_args()
    try:
        freeze(public_output_directory(args.out))
    except SystemExit:
        raise
    except Exception as exc:  # surface any failure as a non-zero exit for CI
        print(
            f"❌ Freeze failed error_type={type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
