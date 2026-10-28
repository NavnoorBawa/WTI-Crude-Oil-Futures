"""Every relative link in the repository's Markdown resolves: the target file exists and, for a link to a
heading (#anchor) in a Markdown file, the heading exists, slugged the way GitHub slugs it.

The README is the short version of docs/RESULTS.md and the docs cross-link section by section, so a
renamed heading or a moved file would otherwise break links silently. Network-free: absolute URLs are
not fetched.
"""

import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = sorted([*ROOT.glob("*.md"), *(ROOT / "docs").glob("*.md")])
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)|\!\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^\s*```")


def prose_lines(text: str) -> list[str]:
    """The lines outside fenced code blocks."""
    lines, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


def slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation other than hyphens and underscores dropped,
    spaces to hyphens (markup such as backticks is dropped with the punctuation)."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    counts: Counter[str] = Counter()
    found = set()
    for line in prose_lines(path.read_text(encoding="utf-8")):
        match = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not match:
            continue
        base = slug(match.group(1))
        found.add(base if counts[base] == 0 else f"{base}-{counts[base]}")
        counts[base] += 1
    return found


class DocsLinksTest(unittest.TestCase):
    def test_the_pages_exist(self):
        names = {page.relative_to(ROOT).as_posix() for page in PAGES}
        self.assertTrue({"README.md", "docs/RESULTS.md", "docs/REVIEWER_GUIDE.md", "docs/METHODOLOGY.md"} <= names)

    def test_every_relative_link_resolves(self):
        broken = []
        checked = 0
        for page in PAGES:
            for number, line in enumerate(prose_lines(page.read_text(encoding="utf-8")), 1):
                for match in LINK.finditer(line):
                    target = match.group(1) or match.group(2)
                    if re.match(r"^[a-z][a-z0-9+.-]*:", target):
                        continue                      # http(s), mailto: not fetched
                    path_part, _, anchor = target.partition("#")
                    resolved = (page.parent / path_part).resolve() if path_part else page
                    checked += 1
                    if not resolved.exists():
                        broken.append(f"{page.relative_to(ROOT)}:{number}: {target} (no such file)")
                    elif anchor and resolved.suffix == ".md" and anchor not in anchors(resolved):
                        broken.append(f"{page.relative_to(ROOT)}:{number}: {target} (no such heading)")
        self.assertGreater(checked, 50)
        self.assertEqual(broken, [])

    def test_slugs_follow_github(self):
        self.assertEqual(slug("4. Post-mortem: the direction model"), "4-post-mortem-the-direction-model")
        self.assertEqual(slug("3. Models (`backend/vol_lab.py`)"), "3-models-backendvol_labpy")
        self.assertEqual(slug("Errata: published numbers that were wrong"), "errata-published-numbers-that-were-wrong")


if __name__ == "__main__":
    unittest.main()
