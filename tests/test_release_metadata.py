"""The release metadata agrees: package.json, pyproject.toml and CITATION.cff carry one version, the
CHANGELOG's newest released heading is that version, and CITATION.cff's release date is that
heading's date. Editing one file's version alone fails here (docs/ROADMAP.md, Stage 1.1)."""

import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})$", re.MULTILINE)


def versions() -> dict[str, str]:
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    return {
        "package.json": json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"],
        "package-lock.json": json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))["version"],
        "pyproject.toml": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "CITATION.cff": re.search(r"^version: (\S+)$", citation, re.MULTILINE).group(1),
    }


class ReleaseMetadataTest(unittest.TestCase):
    def test_every_file_names_one_version(self):
        found = versions()
        self.assertEqual(len(set(found.values())), 1, found)
        self.assertRegex(found["package.json"], r"^\d+\.\d+\.\d+$")

    def test_the_changelog_and_the_citation_name_the_current_release(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        releases = RELEASE_HEADING.findall(changelog)
        self.assertTrue(releases, "no '## X.Y.Z — YYYY-MM-DD' heading in CHANGELOG.md")
        version, date = releases[0]
        self.assertEqual(version, versions()["package.json"])
        citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^date-released: "(\S+)"$', citation, re.MULTILINE).group(1), date)
        # Released versions only grow, newest first.
        numbers = [tuple(int(part) for part in v.split(".")) for v, _ in releases]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        # Anything above the newest release is the work in progress, headed "Unreleased".
        above = changelog[: changelog.index(f"## {version} — ")]
        self.assertEqual([line for line in above.splitlines() if line.startswith("## ")], [] if "## Unreleased" not in above else ["## Unreleased"])


if __name__ == "__main__":
    unittest.main()
