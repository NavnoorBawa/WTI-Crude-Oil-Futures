#!/usr/bin/env python3
"""Say how a rebuilt data/vol_lab.json compares with the committed one, in one Markdown heading.

Bit for bit; every conclusion identical with floating-point noise only (backend/vol_lab.py
compare_reports: within 1e-4 relative or the last decimal a number is published with); or which
conclusions moved. The Reproduce workflow appends it to the run summary and `make reproduce`
prints it.

Usage:
    python scripts/describe_rebuild.py REBUILT.json [COMMITTED.json]

Exit status 0 when it described a comparison (or that the rebuild did not finish), whatever it
found; the reproduce test itself is the gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def describe(committed: dict[str, Any], rebuilt: dict[str, Any]) -> list[str]:
    from backend.vol_lab import REPRO_REL_TOL, compare_reports

    diff = compare_reports(committed, rebuilt)
    if diff["identical"]:
        return ["### Rebuild: bit for bit identical"]
    if not diff["violations"]:
        return [f"### Rebuild: every conclusion identical; {diff['numbers_differing']} numbers differ "
                f"in the last bits (largest {diff['largest_relative_difference']:.1e} relative, "
                f"tolerance {REPRO_REL_TOL:g} or the published last place)"]
    return ([f"### Rebuild: {len(diff['violations'])} differences beyond floating-point noise"]
            + [f"- `{line}`" for line in diff["violations"][:30]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("rebuilt", help="the rebuilt report (the reproduce test's VOL_LAB_REBUILT)")
    parser.add_argument("committed", nargs="?", default=str(PROJECT_ROOT / "data" / "vol_lab.json"),
                        help="the committed report (default: data/vol_lab.json)")
    args = parser.parse_args(argv)
    rebuilt_path, committed_path = Path(args.rebuilt), Path(args.committed)
    if not rebuilt_path.is_file():
        print("### The rebuild did not finish")
        return 0
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    rebuilt = json.loads(rebuilt_path.read_text(encoding="utf-8"))
    print("\n".join(describe(committed, rebuilt)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
