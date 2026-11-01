""".github/workflows/health.yml's own shell, run against a fake gh: the issue it opens, edits, keeps or closes.

The two steps' `run` blocks are taken from the workflow file and run as GitHub runs them (bash
--noprofile --norc -eo pipefail), with a fake `gh` and `sleep` first on PATH. The fake keeps the
repository's issues in a JSON file, so a test can play several runs in a row and act as the person
who closes an issue in between. The check step runs with the drill input set, so its clock is fixed."""

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

try:                                     # records made by the real recorder; tests/ is on the path under discover
    from test_record_health import record_with
except ImportError:                      # python -m unittest tests.test_health_workflow
    from tests.test_record_health import record_with

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "health.yml"

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_GH_STATE"]
state = json.load(open(path))
args = sys.argv[1:]
opt = lambda name, default=None: args[args.index(name) + 1] if name in args else default
def done(code=0):
    json.dump(state, open(path, "w"))
    sys.exit(code)
state["calls"].append(" ".join(args[:2]))
if args[0] == "api":
    key = "record" if "vol_track_record" in args[-1] else "payload"
    if state.get("api_fails"):
        sys.stderr.write("HTTP 502\n"); done(1)
    if args[-1].endswith("/jobs"):                 # GET /repos/{repo}/actions/runs/{id}/jobs
        run_id = args[-1].split("/")[-2]
        if run_id in state.get("jobs_fail", []):
            sys.stderr.write("HTTP 502\n"); done(1)
        print(json.dumps(state.get("jobs", {}).get(run_id, {"jobs": []}))); done()
    sys.stdout.write(state[key]); done()
if args[:2] == ["run", "list"]:
    runs = state["runs"].get(opt("--workflow"), [])
    if opt("--status") == "success":
        runs = [run for run in runs if run.get("conclusion") == "success"]
    if opt("--jq") is not None:
        if "databaseId" not in opt("--jq"):
            sys.exit("fake gh: unknown --jq " + opt("--jq"))
        print("\n".join(str(run["databaseId"]) for run in runs
                        if run.get("event") not in ("pull_request", "pull_request_target")))
        done()
    print(json.dumps(runs)); done()
if args[:2] == ["label", "create"]:
    done(1)                                    # the label exists
issues = state["issues"]
if args[:2] == ["issue", "list"]:
    chosen = sorted((i for i in issues if i["state"] == opt("--state")), key=lambda i: -i["number"])
    chosen = chosen[:int(opt("--limit", "30"))]
    if opt("--jq") == ".[0].number // empty":
        print(chosen[0]["number"] if chosen else "")
    elif opt("--jq") == ".[].body":
        print("\n".join(i["body"] for i in chosen))
    else:
        sys.exit("fake gh: unknown --jq " + str(opt("--jq")))
    done()
if args[:2] == ["issue", "create"]:
    issues.append({"number": len(issues) + 1, "state": "open", "assignee": opt("--assignee"),
                   "body": open(opt("--body-file")).read(), "comments": []})
    done()
issue = next(i for i in issues if i["number"] == int(args[2]))
if args[1] == "view":
    print(issue["body"])
elif args[1] == "edit":
    issue["body"] = open(opt("--body-file")).read()
elif args[1] == "comment":
    issue["comments"].append(opt("--body"))
elif args[1] == "close":
    issue["state"] = "closed"
    issue["comments"].append(opt("--comment"))
else:
    sys.exit("fake gh: unhandled " + " ".join(args))
done()
'''


def step(name: str) -> str:
    """A step's `run: |` block from the workflow, dedented."""
    source = WORKFLOW.read_text(encoding="utf-8")
    block = source[source.index(f"- name: {name}"):]
    block = block[block.index("run: |") + len("run: |\n"):]
    lines = []
    for line in block.splitlines():
        if line.strip() and len(line) - len(line.lstrip()) <= 8:
            break
        lines.append(line)
    return textwrap.dedent("\n".join(lines))


class Repository:
    """A scratch working directory, a fake gh with its state, and the data the check fetches."""

    def __init__(self, record: dict, payload: dict):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "scripts").symlink_to(ROOT / "scripts")
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH, encoding="utf-8")
        (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for tool in ("gh", "sleep"):
            (bin_dir / tool).chmod(0o755)
        self.state = self.dir / "gh.json"
        self.write({"record": json.dumps(record), "payload": json.dumps(payload), "runs": {}, "issues": [], "calls": []})
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(self.state),
                    "GH_TOKEN": "x", "GH_REPO": "owner/repo", "GITHUB_REPOSITORY_OWNER": "owner",
                    "GITHUB_OUTPUT": str(self.dir / "output"), "GITHUB_STEP_SUMMARY": str(self.dir / "summary")}

    def read(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def write(self, state: dict) -> None:
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def update(self, **changes) -> None:
        self.write({**self.read(), **changes})

    def _bash(self, name: str, **env) -> int:
        script = self.dir / "step.sh"
        script.write_text(step(name), encoding="utf-8")
        result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=self.dir,
                                env={**self.env, **env}, capture_output=True, text=True, timeout=60)
        self.last = result
        return result.returncode

    def run(self, now: str) -> int:
        """One Health run: the check step, then (if it produced a status) the issue step; the job's exit."""
        (self.dir / "output").write_text("", encoding="utf-8")
        code = self._bash("Check the live record, the site and the last runs", DRILL_NOW=now)
        if code:
            return code
        status = re.search(r"status=(\d)", (self.dir / "output").read_text(encoding="utf-8")).group(1)
        return self._bash("Open, update or close the health issue", STATUS=status)

    def issues(self) -> list[dict]:
        return self.read()["issues"]

    def close(self, number: int) -> None:
        """A person closes the issue."""
        state = self.read()
        next(i for i in state["issues"] if i["number"] == number)["state"] = "closed"
        self.write(state)

    def cleanup(self) -> None:
        self.tmp.cleanup()


class HealthWorkflowTest(unittest.TestCase):
    def repo(self, record, payload):
        repo = Repository(record, payload)
        self.addCleanup(repo.cleanup)
        return repo

    def test_a_problem_that_needs_a_person_opens_one_issue_and_fails_the_run_until_it_clears(self):
        repo = self.repo(record_with(["2026-11-02", "2026-11-03"]), {"frozen_at": "2026-11-03T20:00:00Z"})
        self.assertEqual(repo.run("2026-11-04T18:00:00Z"), 1, repo.last.stderr)            # stale site
        issues = repo.issues()
        self.assertEqual([(i["number"], i["state"], i["assignee"]) for i in issues], [(1, "open", "owner")])
        self.assertIn("**Needs a person**", issues[0]["body"])
        self.assertEqual(repo.run("2026-11-04T20:00:00Z"), 1)                               # same problem, later
        self.assertEqual(repo.issues()[0]["comments"], [])                                  # not edited again
        repo.update(payload=json.dumps({"frozen_at": "2026-11-04T19:00:00Z"}))               # refreshed: healthy
        self.assertEqual(repo.run("2026-11-04T20:00:00Z"), 0, repo.last.stderr)
        self.assertEqual([i["state"] for i in repo.issues()], ["closed"])
        self.assertTrue(repo.issues()[0]["comments"][-1].startswith("Healthy at"))
        # Closed by Health itself, so its body lists no incident keys: only a person's close acknowledges.
        self.assertTrue(repo.issues()[0]["body"].startswith("Healthy at"))
        self.assertNotIn("health-incidents", repo.issues()[0]["body"])

    def test_a_changed_problem_edits_the_issue_once_with_a_comment(self):
        repo = self.repo(record_with(["2026-11-02", "2026-11-03"]), {"frozen_at": "2026-11-03T20:00:00Z"})
        repo.run("2026-11-04T18:00:00Z")
        repo.update(payload=json.dumps({"frozen_at": "2026-11-03T21:00:00Z"}))               # a new, still stale site
        self.assertEqual(repo.run("2026-11-04T18:00:00Z"), 1)
        issue = repo.issues()[0]
        self.assertIn("2026-11-03T21:00:00Z", issue["body"])
        self.assertEqual(issue["comments"], ["The problems changed; the description lists the current ones."])

    def test_closing_the_issue_acknowledges_incidents_even_as_they_age_out_one_by_one(self):
        # Nov 3 and Nov 4 are lost; the record goes on, so nothing needs a person.
        sessions = ["2026-11-02", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
        repo = self.repo(record_with(sessions, started="2026-11-02"), {"frozen_at": "2026-11-06T16:00:00Z"})
        self.assertEqual(repo.run("2026-11-06T18:00:00Z"), 0, repo.last.stderr)            # incidents: the run passes
        self.assertEqual([i["state"] for i in repo.issues()], ["open"])
        self.assertIn("lost:2026-11-03 lost:2026-11-04", repo.issues()[0]["body"])
        repo.close(1)
        for now, frozen in (("2026-11-07T18:00:00Z", "2026-11-07T16:00:00Z"),               # both still in the week
                            ("2026-11-11T12:00:00Z", "2026-11-11T08:00:00Z")):              # Nov 3 has aged out
            repo.update(payload=json.dumps({"frozen_at": frozen}))
            with self.subTest(now=now):
                self.assertEqual(repo.run(now), 0, repo.last.stderr)
                self.assertEqual(len(repo.issues()), 1)                                     # no new issue

    def test_a_failed_anchor_job_is_an_incident_that_opens_the_issue_without_failing_the_run(self):
        repo = self.repo(record_with(["2026-11-02", "2026-11-03"]), {"frozen_at": "2026-11-04T16:00:00Z"})
        anchor = {"name": "Anchor the record in a public log", "conclusion": "failure",
                  "started_at": "2026-11-04T16:05:00Z", "html_url": "https://example.test/job/9"}
        runs = [{"databaseId": n, "conclusion": "success", "url": f"https://example.test/{n}",
                 "createdAt": f"2026-11-04T{n:02d}:00:00Z", "event": event}
                for n, event in ((16, "schedule"), (12, "schedule"), (8, "pull_request"))]
        repo.update(runs={"refresh.yml": runs},
                    jobs={"16": {"jobs": [{"name": "Freeze and build", "conclusion": "success"}, anchor]},
                          "8": {"jobs": [{**anchor, "html_url": "https://example.test/job/pr"}]}},
                    jobs_fail=["12"])                                                  # one list cannot be fetched
        self.assertEqual(repo.run("2026-11-04T18:00:00Z"), 0, repo.last.stderr)
        body = repo.issues()[0]["body"]
        self.assertIn("On 2026-11-04, 1 Refresh run did not anchor the record in Rekor", body)
        self.assertIn("https://example.test/job/9", body)
        self.assertNotIn("job/pr", body)                                               # a pull request's run is not main's
        self.assertIn("<!-- health-incidents: anchor:2026-11-04 -->", body)
        self.assertEqual(sorted(p.name for p in (repo.dir / "anchor-jobs").iterdir()), ["16.json"])

    def test_a_fetch_that_keeps_failing_fails_the_run_and_touches_no_issue(self):
        repo = self.repo(record_with(["2026-11-02"]), {"frozen_at": "2026-11-03T20:00:00Z"})
        repo.update(api_fails=True)
        self.assertEqual(repo.run("2026-11-04T18:00:00Z"), 2)
        self.assertIn("::error title=Health check could not run::", repo.last.stdout)
        self.assertEqual(repo.read()["calls"], ["api -H"] * 3)                              # three tries, nothing else
        self.assertEqual(repo.issues(), [])


if __name__ == "__main__":
    unittest.main()
