"""Bump the release version in every file that names it, and date the CHANGELOG's work in progress.

    python scripts/bump_version.py minor              # 2.1.0 -> 2.2.0, dated today (UTC)
    python scripts/bump_version.py patch --date 2026-10-29

The kind follows the project's rule (docs/ROADMAP.md, Stage 1.1): a change to a research result is
MINOR and carries an Errata entry; a dashboard-only change is PATCH. A change to sealing, scoring
or the pinned promotion pair would be MAJOR, and is forbidden, so this script refuses "major".

It edits package.json, package-lock.json (its two root versions), pyproject.toml and CITATION.cff
(version and date-released), and renames CHANGELOG.md's "## Unreleased" heading to
"## X.Y.Z — YYYY-MM-DD". tests/test_release_metadata.py checks that they agree. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def next_version(current: str, kind: str) -> str:
    match = SEMVER.match(current)
    if not match:
        raise ValueError(f"not a version: {current!r}")
    major, minor, patch = (int(part) for part in match.groups())
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError("only 'minor' (a research result changed) or 'patch' (dashboard only) are allowed; "
                     "a major change would move sealing, scoring or the pinned promotion pair")


def _sub_once(pattern: str, replacement: str, text: str, where: str) -> str:
    new, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"{where}: expected one match for {pattern!r}")
    return new


def bump(root: Path, kind: str, date: str) -> str:
    """Write the next version into every file under `root`; returns it."""
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    version = next_version(package["version"], kind)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if "\n## Unreleased\n" not in changelog:
        raise ValueError("CHANGELOG.md has no '## Unreleased' section to release")

    text = (root / "package.json").read_text(encoding="utf-8")
    (root / "package.json").write_text(
        _sub_once(r'^(  "version": )"[^"]+"', rf'\g<1>"{version}"', text, "package.json"), encoding="utf-8")
    text = (root / "package-lock.json").read_text(encoding="utf-8")
    text = _sub_once(r'^(  "version": )"[^"]+"', rf'\g<1>"{version}"', text, "package-lock.json")
    text = _sub_once(r'^(  "packages": \{\n    "": \{\n      "name": "[^"]+",\n      "version": )"[^"]+"',
                     rf'\g<1>"{version}"', text, "package-lock.json packages['']")
    (root / "package-lock.json").write_text(text, encoding="utf-8")
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    (root / "pyproject.toml").write_text(
        _sub_once(r'^version = "[^"]+"', f'version = "{version}"', text, "pyproject.toml"), encoding="utf-8")
    text = (root / "CITATION.cff").read_text(encoding="utf-8")
    text = _sub_once(r"^version: \S+$", f"version: {version}", text, "CITATION.cff")
    text = _sub_once(r'^date-released: "\S+"$', f'date-released: "{date}"', text, "CITATION.cff")
    (root / "CITATION.cff").write_text(text, encoding="utf-8")
    (root / "CHANGELOG.md").write_text(changelog.replace("\n## Unreleased\n", f"\n## {version} — {date}\n", 1),
                                       encoding="utf-8")
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=["minor", "patch"])
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat(), help="release date, YYYY-MM-DD")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        parser.error("--date must be YYYY-MM-DD")
    print(bump(ROOT, args.kind, args.date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
