"""dev.sh must run on stock macOS /bin/bash, which is 3.2."""

import re
import unittest
from pathlib import Path

DEV_SCRIPT = Path(__file__).resolve().parents[1] / "dev.sh"

# Builtins and syntax that bash 3.2 rejects or lacks (introduced in 4.0-5.1).
BASH_4_PLUS = {
    "wait -n (3.2's wait takes no options: it returns at once and the EXIT trap kills both servers)":
        r"\bwait\s+-",
    "mapfile/readarray": r"\b(?:mapfile|readarray)\b",
    "associative arrays": r"\b(?:declare|local|typeset)\s+-[a-zA-Z]*A",
    "case-modifying expansions": r"\$\{[^}]*(?:,,|\^\^)",
    "coproc": r"\bcoproc\b",
    "|& and &>>": r"\|&|&>>",
}


class DevScriptTest(unittest.TestCase):
    def setUp(self):
        lines = DEV_SCRIPT.read_text(encoding="utf-8").splitlines()
        self.code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    def test_uses_nothing_newer_than_bash_3_2(self):
        for feature, pattern in BASH_4_PLUS.items():
            with self.subTest(feature=feature):
                self.assertNotRegex(self.code, pattern)

    def test_waits_for_either_server_then_cleans_up(self):
        self.assertRegex(
            self.code,
            r'while kill -0 "\$BACKEND_PID" 2>/dev/null && kill -0 "\$FRONTEND_PID" 2>/dev/null; do',
        )
        # Traps are installed before either server starts, so Ctrl+C can never orphan one.
        first_start = self.code.index("BACKEND_PID=$!")
        for trap in ("trap 'cleanup; exit 130' INT", "trap 'cleanup; exit 143' TERM", "trap cleanup EXIT"):
            with self.subTest(trap=trap):
                self.assertLess(self.code.index(trap), first_start)
        self.assertIsNotNone(re.search(r"^#!/bin/bash$", DEV_SCRIPT.read_text(encoding="utf-8"), re.M))


if __name__ == "__main__":
    unittest.main()
