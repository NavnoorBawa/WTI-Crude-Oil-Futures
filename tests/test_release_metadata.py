"""The release metadata agrees: package.json, pyproject.toml and CITATION.cff carry one version, the
CHANGELOG's newest released heading is that version, and CITATION.cff's release date is that
heading's date. Editing one file's version alone fails here (docs/ROADMAP.md, Stage 1.1)."""

import json
import re
import tomllib
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})$", re.MULTILINE)


def versions() -> dict[str, str]:
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    return {
        "package.json": json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"],
        "package-lock.json": lock["version"],
        "package-lock.json packages['']": lock["packages"][""]["version"],
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
        datetime.strptime(date, "%Y-%m-%d")      # a real calendar date, as CFF requires
        # Released versions only grow, newest first.
        numbers = [tuple(int(part) for part in v.split(".")) for v, _ in releases]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        # Anything above the newest release is the work in progress, headed "Unreleased".
        above = changelog[: changelog.index(f"## {version} — ")]
        self.assertEqual([line for line in above.splitlines() if line.startswith("## ")], [] if "## Unreleased" not in above else ["## Unreleased"])


class ReleaseToolsTest(unittest.TestCase):
    def test_bump_moves_every_file_together(self):
        import shutil
        import tempfile

        from scripts import bump_version

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("package.json", "package-lock.json", "pyproject.toml", "CITATION.cff", "CHANGELOG.md"):
                shutil.copy(ROOT / name, root / name)
            changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            if "\n## Unreleased\n" not in changelog:      # a released tree: open a work-in-progress section
                first = changelog.index("\n## ")
                (root / "CHANGELOG.md").write_text(changelog[:first] + "\n## Unreleased\n\n- A change.\n" + changelog[first:],
                                                   encoding="utf-8")
            before = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
            version = bump_version.bump(root, "patch", "2030-01-02")
            major, minor, patch = (int(part) for part in before.split("."))
            self.assertEqual(version, f"{major}.{minor}.{patch + 1}")
            lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
            self.assertEqual({lock["version"], lock["packages"][""]["version"],
                              json.loads((root / "package.json").read_text(encoding="utf-8"))["version"],
                              tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]},
                             {version})
            citation = (root / "CITATION.cff").read_text(encoding="utf-8")
            self.assertIn(f"version: {version}\n", citation)
            self.assertIn('date-released: "2030-01-02"', citation)
            self.assertIn(f"\n## {version} — 2030-01-02\n", (root / "CHANGELOG.md").read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                bump_version.bump(root, "patch", "2030-01-03")      # nothing left to release
        with self.assertRaises(ValueError):
            bump_version.next_version("2.2.0", "major")              # would move sealing, scoring or the pair
        for bad in ("2026-10-32", "2026-13-01", "2026-1-5", "20261030"):
            with self.assertRaises(SystemExit, msg=bad):             # rejected before any file is touched
                bump_version.main(["patch", "--date", bad])

    def test_release_notes_are_the_versions_changelog_section(self):
        from scripts import release_notes

        changelog = "# Changelog\n\n## 2.2.0 — 2026-10-29\n\nNew.\n\n### Fixed\n\n- x\n\n## 2.1.0 — 2026-10-27\n\nOld.\n"
        self.assertEqual(release_notes.section(changelog, "2.2.0"), "New.\n\n### Fixed\n\n- x\n")
        self.assertEqual(release_notes.section(changelog, "2.1.0"), "Old.\n")
        with self.assertRaises(ValueError):
            release_notes.section(changelog, "2.3.0")
        version = versions()["package.json"]
        self.assertTrue(release_notes.section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version).strip())

    def test_the_release_workflow_runs_on_version_tags_only_and_installs_nothing(self):
        source = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("tags:\n      - 'v[0-9]+.[0-9]+.[0-9]+'", source)
        self.assertNotRegex(source, r"\n  (pull_request|schedule|workflow_dispatch):")
        self.assertIn("python3 scripts/release_notes.py", source)
        self.assertIn("xargs -0 -r sha256sum > SHA256SUMS", source)
        # The digest step stops on any failed command in a pipeline and checks the digest count.
        step = source[source.index("- name: Write the notes and the artifact digests"):source.index("- name: Publish the release")]
        self.assertIn("shell: bash", step)
        self.assertIn("set -euo pipefail", step)
        self.assertIn('test "$(wc -l < SHA256SUMS)" -eq "${files}"', step)

    def test_artifacts_check_out_exactly_as_committed(self):
        # Without this, Git for Windows (core.autocrlf=true) writes CRLF into the CSVs and
        # `sha256sum -c SHA256SUMS` reports them as changed.
        self.assertIn("\ndata/** -text\n", (ROOT / ".gitattributes").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
