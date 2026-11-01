"""refresh.yml's `field` job, run as GitHub runs it, against a local copy of the field-data branch.

The job's two `run` blocks are taken from the workflow file and run with bash --noprofile --norc -eo
pipefail and the step's own env. A `git` wrapper first on PATH sends fetch and push to a local bare
repository (everything else is the real git), and numpy and pandas are unimportable, as on the runner
where nothing is installed. It shows what the job pushes: one commit on top of the branch's tip, the
branch's own files with only the log replaced, never forced; and that it refuses a branch it does not
recognise.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from backend import field_record as fr

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "refresh.yml"
REPO = "owner/repo"

GIT_WRAPPER = r'''#!/bin/sh
# fetch and push go to the local bare repository; everything else is the real git.
for arg in "$@"; do
  case "$arg" in
    fetch|push) exec "$REAL_GIT" -c "url.$FAKE_REMOTE.insteadOf=https://github.com/owner/repo" "$@" ;;
  esac
done
exec "$REAL_GIT" "$@"
'''


def step(name: str) -> tuple[str, dict[str, str]]:
    """A field job step's `run` command (dedented) and its `env:` block, from the workflow."""
    source = WORKFLOW.read_text(encoding="utf-8")
    job = source[source.index("\n  field:\n"):]
    block = job[job.index(f"- name: {name}"):]
    block = block[:block.index("\n      - name:")] if "\n      - name:" in block else block
    env = dict(re.findall(r"^          (\w+): (.+)$", block[:block.index("run:")], re.M))
    run = block[block.index("run:") + len("run:"):]
    if run.startswith(" |\n"):
        lines = []
        for line in run[len(" |\n"):].splitlines():
            if line.strip() and len(line) - len(line.lstrip()) <= 8:
                break
            lines.append(line)
        return textwrap.dedent("\n".join(lines)), env
    return run.strip().splitlines()[0], env


def git(*args, cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def issued(as_of="2026-10-29", key="har_iv", issued_at="2026-10-30T13:00:00Z"):
    return {"kind": "forecast", "as_of": as_of, "issued_at": issued_at, "model_key": key, "forecast_pct": 40.0,
            "median_pct": 35.0, "inputs_sha256": "a" * 64, "ovx_source": "yahoo ^OVX", "code_sha": None,
            "spec_sha256": "b" * 64, "sealed": False}


@unittest.skipUnless(shutil.which("git"), "needs git")
class FieldJobTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.remote = self.dir / "remote.git"
        self.work = self.dir / "work"
        self.temp = self.dir / "runner-temp"
        for path in (self.work, self.temp, self.dir / "bin", self.dir / "block"):
            path.mkdir()
        git("init", "-q", "--bare", str(self.remote), cwd=self.dir)
        git("init", "-q", cwd=self.work)
        git("remote", "add", "origin", f"https://github.com/{REPO}", cwd=self.work)
        (self.work / "backend").symlink_to(ROOT / "backend")
        (self.work / "dist").mkdir()
        self.payload("2026-10-29")
        self.seed({".initialized-v1": "", "README.md": "the field log\n", "vol_field_record.jsonl": ""})
        (self.dir / "bin" / "git").write_text(GIT_WRAPPER, encoding="utf-8")
        (self.dir / "bin" / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.dir / "bin" / "python").symlink_to(sys.executable)
        for tool in ("git", "sleep"):
            (self.dir / "bin" / tool).chmod(0o755)
        (self.dir / "block" / "sitecustomize.py").write_text(
            "import sys\nsys.modules['numpy'] = None\nsys.modules['pandas'] = None\n", encoding="utf-8")
        (self.temp / "field-lines").mkdir()
        self.lines([])
        self.env = {**os.environ, "PATH": f"{self.dir / 'bin'}{os.pathsep}{os.environ['PATH']}",
                    "REAL_GIT": shutil.which("git"), "FAKE_REMOTE": self.remote.as_uri(),
                    "PYTHONPATH": str(self.dir / "block"), "GITHUB_REPOSITORY": REPO,
                    "GITHUB_OUTPUT": str(self.dir / "output"), "GIT_CONFIG_NOSYSTEM": "1",
                    "HOME": str(self.dir)}

    def seed(self, files: dict[str, str]) -> str:
        """Point the remote's field-data branch at a new commit holding exactly these files (on top of it)."""
        seed = self.dir / "seed"
        if seed.exists():
            shutil.rmtree(seed)
        seed.mkdir()
        git("init", "-q", cwd=seed)
        for name, text in files.items():
            (seed / name).parent.mkdir(parents=True, exist_ok=True)
            (seed / name).write_text(text, encoding="utf-8")
        git("add", "-A", cwd=seed)
        git("-c", "user.name=seed", "-c", "user.email=seed@example.com", "commit", "-q", "-m", "seed", cwd=seed)
        git("push", "-q", "--force", str(self.remote), "HEAD:refs/heads/field-data", cwd=seed)
        return git("rev-parse", "HEAD", cwd=seed)

    def payload(self, as_of: str) -> None:
        body = {"frozen_at": "2026-10-30T13:00:00Z",
                "vol_forecast": {"live": {"as_of": as_of, "forecast_next_week_vol_annualized_pct": 47.6},
                                 "recent_closes": {"date": [], "close": []}}}
        (self.work / "dist" / "data.json").write_text(json.dumps(body), encoding="utf-8")

    def lines(self, lines: list[dict]) -> None:
        (self.temp / "field-lines" / "new.jsonl").write_text("".join(fr.canonical(line) + "\n" for line in lines),
                                                               encoding="utf-8")

    def run_step(self, name: str) -> subprocess.CompletedProcess:
        command, env = step(name)
        resolved = {key: value.replace("${{ runner.temp }}", str(self.temp)).replace("${{ github.token }}", "x")
                    for key, value in env.items()}
        self.assertFalse([v for v in resolved.values() if "${{" in v], resolved)
        script = self.dir / "step.sh"
        script.write_text(command, encoding="utf-8")
        (self.dir / "output").write_text("", encoding="utf-8")
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=self.work,
                              env={**self.env, **resolved}, capture_output=True, text=True, timeout=120)

    def append(self) -> subprocess.CompletedProcess:
        return self.run_step("Append to the field log and push it")

    def tip(self) -> str:
        return git("rev-parse", "refs/heads/field-data", cwd=self.remote)

    def test_it_appends_one_commit_on_top_of_the_tip_with_only_the_log_changed(self):
        before = self.tip()
        before_tree = dict(line.split("\t")[::-1] for line in git("ls-tree", before, cwd=self.remote).splitlines())
        self.lines([issued(key=key) for key in fr.FIELD_KEYS])
        result = self.append()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pushed=true", (self.dir / "output").read_text())
        after = self.tip()
        self.assertEqual(git("rev-parse", f"{after}^", cwd=self.remote), before)        # on top of the tip
        after_tree = dict(line.split("\t")[::-1] for line in git("ls-tree", after, cwd=self.remote).splitlines())
        self.assertEqual(set(after_tree), set(before_tree))
        changed = {name for name in after_tree if after_tree[name] != before_tree[name]}
        self.assertEqual(changed, {"vol_field_record.jsonl"})
        log = git("show", f"{after}:vol_field_record.jsonl", cwd=self.remote) + "\n"
        self.assertEqual(fr.validate(log, previous=""), [])
        self.assertEqual([line["model_key"] for line in fr.parse(log)], list(fr.FIELD_KEYS))
        self.assertEqual(git("log", "-1", "--format=%an <%ae>|%cn|%s", after, cwd=self.remote),
                         "WTI Field Log Bot <41898282+github-actions[bot]@users.noreply.github.com>|WTI Field Log Bot|"
                         "chore: append to the field log [skip ci]")
        # The lag check passes on the log just written (it trails the payload's session by none).
        check = self.run_step("Check that the field log keeps up")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

        # A second run with the same lines: refused, nothing pushed, the job succeeds.
        again = self.append()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("The field log is unchanged", again.stdout)
        self.assertNotIn("pushed=true", (self.dir / "output").read_text())
        self.assertEqual(self.tip(), after)

    def test_a_log_that_stopped_growing_fails_the_lag_check(self):
        self.lines([issued()])
        self.assertEqual(self.append().returncode, 0)
        self.payload("2026-11-04")                      # four sessions after the newest logged one
        self.lines([])
        self.assertEqual(self.append().returncode, 0)
        check = self.run_step("Check that the field log keeps up")
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertIn("the log has stopped growing", check.stdout)

    def test_it_refuses_a_branch_it_does_not_recognise_and_pushes_nothing(self):
        self.lines([issued()])
        for name, files in (("no marker", {"vol_field_record.jsonl": ""}),
                            ("no log", {".initialized-v1": ""}),
                            ("a subdirectory", {".initialized-v1": "", "vol_field_record.jsonl": "", "x/y": ""})):
            with self.subTest(name):
                tip = self.seed(files)
                result = self.append()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("does not hold an initialized field log", result.stderr)
                self.assertEqual(self.tip(), tip)

    def test_it_never_rewrites_a_line(self):
        self.lines([issued()])
        self.assertEqual(self.append().returncode, 0)
        log = git("show", "refs/heads/field-data:vol_field_record.jsonl", cwd=self.remote) + "\n"
        tip = self.seed({".initialized-v1": "", "README.md": "x\n",
                         "vol_field_record.jsonl": log.replace('"forecast_pct":40.0', '"forecast_pct":0.0')})
        self.lines([issued(key="gjr")])
        result = self.append()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)     # the old log does not validate
        self.assertEqual(self.tip(), tip)


if __name__ == "__main__":
    unittest.main()
