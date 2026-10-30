"""Print the CHANGELOG section of one released version, for the GitHub Release of its tag.

    python scripts/release_notes.py 2.2.0 > notes.md

The section runs from its "## 2.2.0 — YYYY-MM-DD" heading to the next "## " heading, heading
excluded. Fails when the version has no released section. Standard library only: it runs in the
release job, which holds a write token and installs nothing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(changelog: str, version: str) -> str:
    heading = re.search(rf"^## {re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE)
    if not heading:
        raise ValueError(f"CHANGELOG.md has no released section for {version}")
    following = re.search(r"^## ", changelog[heading.end():], re.MULTILINE)
    end = heading.end() + following.start() if following else len(changelog)
    return changelog[heading.end():end].strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: release_notes.py VERSION", file=sys.stderr)
        return 2
    sys.stdout.write(section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), argv[0].removeprefix("v")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
