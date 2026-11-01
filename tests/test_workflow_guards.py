import fnmatch
import re
import unittest
from pathlib import Path

from backend.vol_record import SEAL_MARGIN


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = REPOSITORY_ROOT / ".github" / "workflows"
FULL_COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}")
# The structural check of the restored live vol track record (backend/vol_record.py is standard
# library only, so it runs before anything is installed and in the publish job, which installs nothing).
VOL_RECORD_CHECK = "'from backend.vol_record import load_record; load_record()'"
# Stamps sealed_at on the record's new entries immediately before the publish job commits it.
VOL_RECORD_SEAL = "PYTHONDONTWRITEBYTECODE=1 python backend/vol_record.py --seal"

# The only actions a job holding a write token may use: GitHub's own, and only those such a job
# needs (actions/attest only in refresh.yml's anchor and field_anchor jobs, which hold no contents write). Their SHA
# pins are enforced by test_every_remote_action_is_pinned_to_a_full_commit_sha.
WRITE_TOKEN_JOB_ACTIONS = {"actions/checkout", "actions/download-artifact", "actions/setup-python", "actions/attest"}
# Anything that installs, fetches or executes packages or remote code. Next to a write token, a
# compromised dependency could push with it or rewrite a script a later privileged step runs.
PACKAGE_TOOLING = re.compile(
    r"(?<![\w./-])(?:pip[0-9.]*|pipx|uvx?|poetry|pdm|conda|mamba|micromamba|npm|npx|yarn|pnpm"
    r"|bunx?|deno|corepack|gem|cargo|apt|apt-get|brew|curl|wget)(?![\w.-])"
    r"|-m\s+(?:pip|ensurepip|venv)\b"
)


def _workflow_files():
    return sorted(
        (*WORKFLOW_DIRECTORY.glob("*.yml"), *WORKFLOW_DIRECTORY.glob("*.yaml"))
    )


def _jobs(source):
    """Map each job id to its block of text (two-space job keys under top-level `jobs:`)."""
    body = source[source.index("\njobs:\n") + len("\njobs:\n"):]
    jobs, name, lines = {}, None, []
    for line in body.splitlines():
        header = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if header or (line.strip() and not line.startswith(" ")):
            if name:
                jobs[name] = "\n".join(lines)
            if not header:
                break
            name, lines = header.group(1), []
        elif name:
            lines.append(line)
    if name and name not in jobs:
        jobs[name] = "\n".join(lines)
    return jobs


def _permissions(block, indent):
    """The `permissions:` value at this indentation (inline or mapping), or None if absent."""
    match = re.search(rf"^{' ' * indent}permissions:(.*)$", block, re.M)
    if not match:
        return None
    following = block[match.end():].splitlines()[1:]
    mapping = []
    for line in following:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        mapping.append(line)
    return match.group(1) + "\n" + "\n".join(mapping)


def _holds_write_token(job, top_level):
    permissions = _permissions(job, 4)
    if permissions is None:
        permissions = _permissions(top_level, 0)
    if permissions is None:
        return True                     # the repository default may be read/write
    return bool(re.search(r"\bwrite(-all)?\b", re.sub(r"#.*", "", permissions)))


def _shell_run_blocks(source):
    """Yield workflow ``run`` commands without requiring a YAML dependency."""
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(
            r"^(?P<indent>\s*)(?:-\s*)?run:\s*(?P<value>.*)$", lines[index]
        )
        if not match:
            index += 1
            continue

        value = match.group("value").strip()
        if value not in {"|", "|-", "|+", ">", ">-", ">+"}:
            yield value
            index += 1
            continue

        base_indent = len(match.group("indent"))
        block = []
        index += 1
        while index < len(lines):
            line = lines[index]
            if line.strip() and len(line) - len(line.lstrip()) <= base_indent:
                break
            block.append(line)
            index += 1
        yield "\n".join(block)


class WorkflowGuardTest(unittest.TestCase):
    def test_every_remote_action_is_pinned_to_a_full_commit_sha(self):
        workflows = _workflow_files()
        self.assertTrue(workflows, "No GitHub workflow files were found")

        for workflow in workflows:
            source = workflow.read_text(encoding="utf-8")
            for line_number, line in enumerate(source.splitlines(), start=1):
                match = re.match(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", line)
                if not match:
                    continue

                action = match.group(1).strip("'\"")
                if action.startswith("./"):
                    continue

                with self.subTest(workflow=workflow.name, line=line_number):
                    self.assertIn(
                        "@",
                        action,
                        f"{workflow.name}:{line_number} must pin {action!r}",
                    )
                    reference = action.rsplit("@", 1)[-1]
                    self.assertRegex(
                        reference,
                        rf"^{FULL_COMMIT_SHA.pattern}$",
                        (
                            f"{workflow.name}:{line_number} pins {action!r} to a "
                            "mutable ref; use the action's full 40-character commit SHA"
                        ),
                    )

    def test_workflow_shell_commands_never_push_directly_to_main(self):
        main_ref = re.compile(
            r"(?<![\w/-])(?:HEAD:)?(?:refs/heads/)?main(?![\w/-])"
        )

        for workflow in _workflow_files():
            source = workflow.read_text(encoding="utf-8")
            for block_number, block in enumerate(_shell_run_blocks(source), start=1):
                logical_commands = re.sub(r"\\\s*\n\s*", " ", block).splitlines()
                for command in logical_commands:
                    if not re.search(
                        r"(?:^|[;&|])\s*"
                        r"(?:(?:if|elif|while|until|then|!|time)\s+)?"
                        r"git\b.*\bpush\b",
                        command,
                    ):
                        continue
                    with self.subTest(
                        workflow=workflow.name,
                        block=block_number,
                        command=command.strip(),
                    ):
                        self.assertNotRegex(
                            command,
                            main_ref,
                            "Runtime state must never be pushed directly to main",
                        )

    def test_refresh_persists_runtime_state_to_live_data(self):
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")

        self.assertGreaterEqual(source.count("STATE_BRANCH: live-data"), 2)
        self.assertIn(
            "+refs/heads/${STATE_BRANCH}:refs/remotes/origin/${STATE_BRANCH}",
            source,
        )
        self.assertIn('origin/${STATE_BRANCH}', source)
        self.assertIn('push origin "HEAD:${STATE_BRANCH}"', source)
        self.assertNotIn("origin/main", source)
        self.assertNotIn("HEAD:main", source)
        self.assertGreaterEqual(source.count("STATE_MARKER: .initialized-v1"), 2)
        self.assertIn('if [ "$marker_mode" != "100644" ]', source)
        self.assertIn('if [ "$state_mode" != "100644" ]', source)
        self.assertIn('cp "$restore_path" "data/.${state_file}.tmp"', source)
        self.assertIn('mv "data/.${state_file}.tmp" "data/${state_file}"', source)
        # The restored record is validated by its writer's own loader, in build and again in publish.
        self.assertEqual(source.count(VOL_RECORD_CHECK), 2)
        self.assertIn('if [ -L "$state_directory_path" ]', source)
        self.assertIn("cp --remove-destination --", source)
        self.assertIn("http.followRedirects=false", source)
        # The restore, and every step that sends the token, refuses an unexpected origin first.
        origin_check = 'if [ "${origin_url%.git}" != "$expected_origin" ]'
        credentialed = [b for b in _shell_run_blocks(source) if "extraheader" in b]
        self.assertEqual(len(credentialed), 3)            # the site deploy, the state persist, the field log
        for block in credentialed:
            self.assertLess(block.index(origin_check), block.index("extraheader"))
        self.assertEqual(source.count(origin_check), 4)

        restore = source.index("- name: Restore mutable runtime state from live-data")
        freeze = source.index("- name: Freeze data snapshot")
        self.assertLess(restore, freeze, "Runtime state must be restored before freezing")

        persistence_blocks = [
            block
            for block in _shell_run_blocks(source)
            if 'push origin "HEAD:${STATE_BRANCH}"' in block
        ]
        self.assertEqual(len(persistence_blocks), 1)
        persistence = persistence_blocks[0]
        self.assertIn('git -C "$state_worktree" add "$STATE_DIRECTORY/vol_track_record.json"', persistence)
        self.assertEqual(set(re.findall(r"\b\w+\.json\b", persistence)), {"vol_track_record.json"})
        self.assertNotIn("price.json", persistence)

    def test_refresh_restores_and_stages_only_the_vol_track_record(self):
        # The retracted direction model's live record and e-mail alert state are retired: nothing in
        # the workflow restores, runs, stages or persists them any more.
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        for retired in ("signal_state", "live_track_record", "signal_alert", "backend/live_record.py",
                        "backend.live_record", "GMAIL", "ALERT_EMAIL", "@gmail.com"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, source)
        jobs = _jobs(source)
        restore = next(b for b in _shell_run_blocks(jobs["build"]) if "$STATE_RESTORE_DIR" in b)
        self.assertIn("state_file=vol_track_record.json", restore)
        self.assertEqual(re.findall(r'git show "([^"]+)"', restore), ["origin/${STATE_BRANCH}:${branch_path}"])
        self.assertLess(restore.index("python -m json.tool"), restore.index(VOL_RECORD_CHECK))
        self.assertLess(restore.index(VOL_RECORD_CHECK), restore.index('echo "tree='))
        for name in ("Stage runtime state for the publish job", "Place build outputs"):
            step = source[source.index(f"- name: {name}"):]
            step = step[:step.index("\n      - name:")]
            with self.subTest(step=name):
                site_files = {"data.json", "research.json"}          # deployed payloads, not state
                self.assertEqual(set(re.findall(r"\b\w+\.json\b", step)) - site_files, {"vol_track_record.json"})

    def test_both_jobs_validate_the_research_bundle(self):
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        jobs = _jobs(source)
        self.assertIn("--research public/research.json", jobs["build"])
        self.assertIn("cp dist/research.json public/research.json", jobs["publish"])
        self.assertIn("--research public/research.json", jobs["publish"])

    def test_record_step_runs_after_validation_and_before_the_site_build(self):
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        build = _jobs(source)["build"]
        record = build.index("- name: Record + resolve live vol track record")
        self.assertIn("run: python backend/vol_record.py --data public/data.json", build)
        self.assertLess(build.index("- name: Freeze data snapshot"), record)
        self.assertLess(build.index("- name: Validate frozen payload invariants"), record)
        self.assertLess(record, build.index("- name: Build static site"))
        self.assertLess(record, build.index("- name: Stage runtime state for the publish job"))

    def test_push_trigger_covers_the_published_research_artifacts(self):
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        trigger = source[source.index("\n  push:\n"):source.index("\npermissions:")]
        paths = re.findall(r"^\s+- '([^']+)'", trigger, re.M)
        for path in ("src/**", "backend/**", "freeze.py", "data/vol_lab.json", "data/vol_forecast_validation.json",
                     "data/wf_*.json", "data/carry_signal_test.json", "data/supply_shock_study.json",
                     "data/leak_anatomy.json", "data/research_ledger.json", "data/eia_wti_spot_daily.json",
                     "data/market/**", "scripts/build_research_bundle.py", "scripts/validate_frozen_payload.py",
                     ".github/workflows/refresh.yml"):
            with self.subTest(path=path):
                self.assertIn(path, paths)
        # Every committed artifact research.json is built from, and the supply-shock playbook's spot
        # history in data.json, redeploys the site when it changes.
        builder = (REPOSITORY_ROOT / "scripts" / "build_research_bundle.py").read_text(encoding="utf-8")
        inputs = set(re.findall(r'"(\w+\.json)"', builder)) - {"research.json"}
        self.assertGreaterEqual(len(inputs), 9)
        for name in sorted(inputs | {"eia_wti_spot_daily.json"}):
            with self.subTest(artifact=name):
                self.assertTrue(any(fnmatch.fnmatchcase(f"data/{name}", pattern) for pattern in paths))


    def test_privileged_refresh_job_is_split_from_the_build(self):
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        jobs = _jobs(source)
        top_level = source[:source.index("\njobs:")]
        self.assertRegex(top_level, r"permissions:\s*\n\s*contents: read")
        self.assertTrue(_holds_write_token(jobs["publish"], top_level))
        self.assertFalse(_holds_write_token(jobs["build"], top_level))
        self.assertNotIn("secrets.", jobs["publish"])

    def test_only_the_eia_key_and_the_job_token_reach_the_workflows(self):
        # Production reads one credential, the supply-shock study's EIA key, in the read-only build
        # job's freeze step. Pushes use the job's own GITHUB_TOKEN. Nothing else is handed over.
        for workflow in _workflow_files():
            source = workflow.read_text(encoding="utf-8")
            expressions = re.findall(r"\$\{\{(.*?)\}\}", source, re.S)
            secrets = []
            for expression in expressions:
                # Every use of the secrets context must name one secret: no secrets[...], toJSON(secrets).
                for use in re.finditer(r"\bsecrets\b(\.[A-Za-z0-9_]+)?", expression):
                    with self.subTest(workflow=workflow.name, expression=expression.strip()):
                        self.assertIsNotNone(use.group(1), "a secret must be referenced by name")
                    if use.group(1):
                        secrets.append(use.group(1)[1:])
            with self.subTest(workflow=workflow.name):
                self.assertNotRegex(source, r"secrets:\s*inherit")
                if workflow.name == "refresh.yml":
                    self.assertEqual(secrets, ["EIA_API_KEY"])
                else:
                    self.assertEqual(secrets, [])
                for token in re.findall(r"^\s*(\w+):\s*\$\{\{\s*github\.token\s*\}\}", source, re.M):
                    self.assertEqual(token, "GH_TOKEN")
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        jobs = _jobs(source)
        freeze = jobs["build"][jobs["build"].index("- name: Freeze data snapshot"):]
        freeze = freeze[:freeze.index("\n      - name:")]
        self.assertIn("EIA_API_KEY: ${{ secrets.EIA_API_KEY }}", freeze)
        self.assertNotIn("github.token", jobs["build"])
        for retired in ("NEWSAPI_KEY", "FINNHUB_KEY", "ALPHA_VANTAGE_KEY", "USDA_NASS_KEY", "NOAA_CDO_KEY",
                        "MIN_REQUIRED_EXTERNAL_SOURCES", "EAGER_ML_WARMUP", "GMAIL_APP_PASSWORD"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, source)

    def test_publish_job_imports_only_the_vol_record_from_backend(self):
        # Next to the write token, the only repository Python that runs is the standard-library
        # record validator and seal and the payload validator; the retired alert script is gone.
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        publish = _jobs(source)["publish"]
        self.assertEqual(set(re.findall(r"\bbackend[./](\w+)", publish)), {"vol_record"})
        self.assertEqual(re.findall(r"\bfrom backend\.\w+ import [\w, ]+|\bimport backend\b[\w.]*", publish),
                         ["from backend.vol_record import load_record"])
        commands = [re.sub(r"\\\s*\n\s*", " ", b) for b in _shell_run_blocks(publish)]
        scripts = set()
        for command in commands:
            scripts.update(re.findall(r"\bpython3?\s+(?:-[A-Za-z]\s+)*([\w./-]+\.py)\b", command))
        self.assertEqual(scripts, {"scripts/validate_frozen_payload.py", "backend/vol_record.py"})
        record_calls = [line.strip() for command in commands for line in command.splitlines()
                        if "backend/vol_record.py" in line]
        self.assertEqual(record_calls, [VOL_RECORD_SEAL])
        inline = [code for command in commands for code in re.findall(r"\bpython3?\s+-c\s+'([^']*)'", command)]
        self.assertEqual(sorted(inline), sorted([
            "from backend.vol_record import load_record; load_record()",
            'import json, sys; print(json.load(sys.stdin).get("frozen_at") or "")',
        ]))

    def test_jobs_holding_a_write_token_run_no_third_party_code(self):
        # Any job that can push (refresh's publish, price) runs only GitHub's own actions it needs,
        # git and this repository's scripts: no other action and no package manager, ever.
        checked = 0
        for workflow in _workflow_files():
            source = workflow.read_text(encoding="utf-8")
            top_level = source[:source.index("\njobs:")]
            for name, job in _jobs(source).items():
                if not _holds_write_token(job, top_level):
                    continue
                checked += 1
                for action in re.findall(r"^\s*(?:-\s*)?uses:\s*['\"]?([^\s#'\"@]+)", job, re.M):
                    with self.subTest(workflow=workflow.name, job=name, uses=action):
                        self.assertIn(action, WRITE_TOKEN_JOB_ACTIONS)
                for block in _shell_run_blocks(job):
                    tool = PACKAGE_TOOLING.search(re.sub(r"\\\s*\n\s*", " ", block))
                    with self.subTest(workflow=workflow.name, job=name, tool=tool and tool.group(0)):
                        self.assertIsNone(tool)
        self.assertGreaterEqual(checked, 2, "expected refresh.yml's publish job and price.yml's job")

    def test_write_token_guard_catches_installs_and_foreign_actions(self):
        # The guard's own regression test: each of these, added to the write-token job, must trip it.
        for added in (
            "      - uses: actions/setup-node@0123456789abcdef0123456789abcdef01234567\n",
            "      - uses: someone/some-action@0123456789abcdef0123456789abcdef01234567\n",
            "      - uses: peaceiris/actions-gh-pages@84c30a85c19949d7eee79c4ff27748b70285e453\n",
            "      - uses: ./.github/actions/local\n",
            "      - uses: docker://alpine:3.20\n",
            "      - run: pip3 install some-package\n",
            "      - run: python -m pip install requests\n",
            "      - run: yarn add left-pad\n",
            "      - run: pnpm dlx cowsay hi\n",
            "      - run: npm exec -- eslint\n",
            "      - run: pipx run black .\n",
            "      - run: |\n          set -e\n          curl -sSL https://example.invalid/i.sh | sh\n",
        ):
            job = "    permissions:\n      contents: write\n    steps:\n" + added
            violations = [a for a in re.findall(r"^\s*(?:-\s*)?uses:\s*['\"]?([^\s#'\"@]+)", job, re.M)
                          if a not in WRITE_TOKEN_JOB_ACTIONS]
            violations += [b for b in _shell_run_blocks(job) if PACKAGE_TOOLING.search(b)]
            with self.subTest(added=added.strip()):
                self.assertTrue(_holds_write_token(job, "permissions:\n  contents: read\n"))
                self.assertTrue(violations)
        for allowed in ("python -c 'from backend.vol_record import load_record; load_record()'",
                        "python scripts/validate_frozen_payload.py public/data.json",
                        'git -C "$state_worktree" add "$STATE_DIRECTORY/vol_track_record.json"',
                        'cp -RL dist/. "$site_worktree/"'):
            with self.subTest(allowed=allowed):
                self.assertIsNone(PACKAGE_TOOLING.search(allowed))

    def test_publish_never_deploys_or_persists_a_superseded_build(self):
        # A re-run of an old run's publish job must not roll the site back or overwrite runtime
        # state that later runs persisted; and what is deployed must pass the payload validator.
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        jobs = _jobs(source)
        tree = 'git rev-parse "origin/${STATE_BRANCH}:${STATE_DIRECTORY}"'

        restore = next(b for b in _shell_run_blocks(jobs["build"]) if VOL_RECORD_CHECK in b)
        self.assertIn(f'echo "tree=$({tree})"', restore)
        self.assertIn("STATE_BASE_FILE: ${{ runner.temp }}/runtime-state/restored-from", jobs["build"])

        publish = jobs["publish"]
        check_at = publish.index("- name: Refuse a stale or superseded build")
        deploy_at = publish.index("- name: Deploy to GitHub Pages")
        persist_at = publish.index("- name: Persist live vol track record to live-data branch")
        self.assertLess(publish.index("- name: Place build outputs"), check_at)
        self.assertLess(check_at, deploy_at)
        self.assertLess(deploy_at, persist_at)
        # The record is persisted only once the site showing its summary is published.
        self.assertIn("if: ${{ !cancelled() && steps.pages_deploy.outcome == 'success' }}",
                      publish[persist_at:].splitlines()[1])
        self.assertIn("id: pages_deploy", publish[deploy_at:persist_at])
        check = next(b for b in _shell_run_blocks(publish) if "validate_frozen_payload.py" in b)
        self.assertIn("cmp dist/data.json public/data.json", check)
        self.assertIn(f'if [ "$({tree})" != "$base_tree" ]; then', check)
        self.assertIn("python scripts/validate_frozen_payload.py public/data.json", check)
        self.assertIn("--max-age-minutes", check)
        self.assertIn("--not-older-than", check)
        self.assertIn('git show "origin/${PAGES_BRANCH}:data.json"', check)

        persist = next(b for b in _shell_run_blocks(publish) if 'push origin "HEAD:${STATE_BRANCH}"' in b)
        guard = persist.index(f'if [ "$({tree})" != "$base_tree" ]; then')
        self.assertLess(persist.index("diff --staged --quiet"), guard)
        self.assertLess(guard, persist.index("commit -m"))

    def test_the_record_is_sealed_right_before_it_is_committed(self):
        # The seal is what lets the record claim each scored forecast was committed before its
        # window opened: it runs only in the publish job, in the step that commits and pushes, after
        # the origin and restored-tree checks and before the bytes it persists are snapshotted; and
        # that step's time limit keeps the push inside vol_record.SEAL_MARGIN of the stamp.
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        jobs = _jobs(source)
        self.assertNotIn("--seal", "\n".join(_shell_run_blocks(jobs["build"])))
        sealing = [b for b in _shell_run_blocks(source) if "--seal" in b]
        self.assertEqual(len(sealing), 1)
        persist = sealing[0]
        self.assertIn('push origin "HEAD:${STATE_BRANCH}"', persist)
        self.assertEqual(persist.count(VOL_RECORD_SEAL), 1)
        seal = persist.index(VOL_RECORD_SEAL)
        self.assertLess(persist.index('if [ "${origin_url%.git}" != "$expected_origin" ]'), seal)
        self.assertLess(persist.index('if ! [[ "$base_tree" =~'), seal)
        self.assertLess(seal, persist.index('cp data/vol_track_record.json "$STATE_SNAPSHOT_DIR/'))
        self.assertLess(seal, persist.index("commit -m"))

        publish = jobs["publish"]
        step = publish[publish.index("- name: Persist live vol track record to live-data branch"):]
        limit = re.search(r"^        timeout-minutes:\s*(\d+)\s*$", step.split("\n      - name:")[0], re.M)
        self.assertIsNotNone(limit, "the persist step needs its own time limit")
        self.assertLess(int(limit.group(1)) * 60, SEAL_MARGIN.total_seconds())

    def test_site_deploy_mirrors_the_replaced_action(self):
        # What peaceiris/actions-gh-pages did with this workflow's inputs: replace the branch's files
        # with dist/ (keep_files false), add .nojekyll, commit on top of the history (no
        # force_orphan) and push without --force.
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        deploy = next(b for b in _shell_run_blocks(_jobs(source)["publish"])
                      if 'push origin "HEAD:${PAGES_BRANCH}"' in b)
        self.assertIn("PAGES_BRANCH: gh-pages", source)
        self.assertIn("rm -r -q --ignore-unmatch -- .", deploy)
        self.assertIn('cp -RL dist/. "$site_worktree/"', deploy)
        self.assertIn('touch "$site_worktree/.nojekyll"', deploy)
        self.assertIn('git worktree add --detach "$site_worktree" "origin/${PAGES_BRANCH}"', deploy)
        self.assertNotRegex(deploy, r"push\b[^\n]*(--force|\s-f\b|\+HEAD)")
        self.assertIn("http.followRedirects=false", deploy)

    def test_the_anchor_attests_the_persisted_record_after_publish_and_cannot_fail_the_run(self):
        # refresh.yml's anchor job: after publish, never inside it; a failure never fails the run; it
        # can sign and store an attestation but cannot write contents, and runs no shell of its own.
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        jobs = _jobs(source)
        anchor = jobs["anchor"]
        self.assertIn("    needs: publish\n", anchor)
        self.assertIn("    if: ${{ !cancelled() && needs.publish.result == 'success' }}\n", anchor)
        self.assertIn("    continue-on-error: true\n", anchor)
        self.assertIn("    timeout-minutes: 5\n", anchor)          # it holds the concurrency group while it runs
        self.assertEqual(" ".join(_permissions(anchor, 4).split()),
                         "contents: read id-token: write # Sigstore's short-lived signing certificate for this workflow "
                         "attestations: write # keep the attestation with the repository as well")
        self.assertEqual(re.findall(r"uses:\s*([^\s@]+)@", anchor), ["actions/checkout", "actions/attest"])
        self.assertEqual(list(_shell_run_blocks(anchor)), [])
        self.assertIn("ref: live-data", anchor)
        self.assertIn("persist-credentials: false", anchor)
        attest = anchor[anchor.index("uses: actions/attest@"):]
        self.assertEqual(re.findall(r"^\s+(\S+):", attest[attest.index("with:"):], re.M),
                         ["subject-path"])                       # no other input (no private-signing)
        self.assertIn("subject-path: runtime-state/vol_track_record.json", anchor)
        # actions/attest is allowed in the two anchor jobs only, though WRITE_TOKEN_JOB_ACTIONS lists it.
        for workflow in _workflow_files():
            for name, job in _jobs(workflow.read_text(encoding="utf-8")).items():
                if (workflow.name, name) not in (("refresh.yml", "anchor"), ("refresh.yml", "field_anchor")):
                    self.assertNotIn("actions/attest", job, f"{workflow.name} {name}")
        # Nothing about the seal moved: the publish job still neither signs nor attests.
        self.assertNotIn("id-token", _permissions(jobs["publish"], 4))
        self.assertNotIn("actions/attest", re.findall(r"uses:\s*([^\s@]+)@", jobs["publish"]))

    def test_the_field_jobs_only_add_to_the_field_log(self):
        # ROADMAP 1.8: the shadow field log is kept by three best-effort jobs that never touch the sealed
        # record, its branch, the seal or the site, and push only the field-data branch, without force.
        source = (WORKFLOW_DIRECTORY / "refresh.yml").read_text(encoding="utf-8")
        jobs = _jobs(source)
        top_level = source[:source.index("\njobs:")]
        field_jobs = {name: jobs[name] for name in ("field_build", "field", "field_anchor")}
        for name, job in field_jobs.items():
            with self.subTest(name):
                self.assertIn("    continue-on-error: true\n", job)
                code = "\n".join(line for line in job.splitlines() if not line.strip().startswith("#"))
                for word in ("runtime-state", "live-data", "vol_track_record", "vol_record", "--seal", "gh-pages",
                             "STATE_", "PAGES_", "secrets.", "refs/heads/main", "--force", "--force-with-lease"):
                    self.assertNotIn(word, code)
        # Nothing the seal depends on waits on them or names them.
        for name in ("build", "publish", "anchor"):
            self.assertNotIn("field", "\n".join(line for line in jobs[name].splitlines()
                                                if not line.strip().startswith("#")), name)
        self.assertIn("    needs: build\n", jobs["publish"])

        build = field_jobs["field_build"]
        self.assertIn("    needs: build\n", build)
        self.assertFalse(_holds_write_token(build, top_level))
        self.assertNotIn("github.token", build)
        self.assertEqual(" ".join(_permissions(build, 4).split()), "contents: read")
        self.assertEqual([c for c in re.findall(r"backend/field_record\.py (--[\w-]+)", build)], ["--open-keys", "--issue"])

        field = field_jobs["field"]
        self.assertIn("    needs: [publish, field_build]\n", field)
        self.assertIn("    if: ${{ !cancelled() && needs.publish.result == 'success' && "
                      "needs.field_build.result == 'success' }}\n", field)
        self.assertEqual(" ".join(re.sub(r"#.*", "", _permissions(field, 4)).split()), "contents: write")
        self.assertEqual(re.findall(r"uses:\s*([^\s@]+)@", field),
                         ["actions/checkout", "actions/download-artifact", "actions/download-artifact",
                          "actions/setup-python"])
        self.assertNotIn("cache:", field)                      # no package cache next to the write token
        self.assertIn("persist-credentials: false", field)
        commands = [re.sub(r"\\\s*\n\s*", " ", block) for block in _shell_run_blocks(field)]
        calls = [m for command in commands for m in re.findall(r"\bpython3?\s+(?:-[A-Za-z]\s+)*(\S+)", command)]
        self.assertEqual(set(calls), {"backend/field_record.py"})
        self.assertEqual(re.findall(r"backend/field_record\.py (--[\w-]+)", "\n".join(commands)),
                         ["--append", "--validate", "--lag"])
        pushes = [line.strip() for command in commands for line in command.splitlines()
                  if re.match(r"(?:if\s+)?git\b.*\bpush\b", line.strip())]
        self.assertEqual(len(pushes), 1)
        self.assertTrue(pushes[0].endswith('push origin "${commit}:refs/heads/${FIELD_BRANCH}"; then'), pushes)
        self.assertEqual(re.findall(r"^\s+FIELD_BRANCH: (\S+)$", field, re.M), ["field-data"])
        self.assertIn('git commit-tree "$tree" -p "$parent"', field)    # on top of the tip it was built from

        anchor = field_jobs["field_anchor"]
        self.assertIn("    needs: field\n", anchor)
        self.assertIn("    if: ${{ !cancelled() && needs.field.outputs.pushed == 'true' }}\n", anchor)
        self.assertEqual(" ".join(_permissions(anchor, 4).split()),
                         " ".join(_permissions(jobs["anchor"], 4).split()))
        self.assertEqual(re.findall(r"uses:\s*([^\s@]+)@", anchor), ["actions/checkout", "actions/attest"])
        self.assertEqual(list(_shell_run_blocks(anchor)), [])
        self.assertIn("ref: field-data", anchor)
        attest = anchor[anchor.index("uses: actions/attest@"):]
        self.assertEqual(re.findall(r"^\s+(\S+):", attest[attest.index("with:"):], re.M), ["subject-path"])
        self.assertIn("subject-path: vol_field_record.jsonl", anchor)

    def test_health_writes_its_issue_and_nothing_else(self):
        # health.yml can write issues: it reads copies of the record and the payload through gh, runs
        # the standard-library check, and never pushes, seals, or writes anything but its scratch files.
        source = (WORKFLOW_DIRECTORY / "health.yml").read_text(encoding="utf-8")
        top_level = source[:source.index("\njobs:")]
        self.assertEqual(" ".join(_permissions(top_level, 0).split()), "contents: read issues: write actions: read")
        jobs = _jobs(source)
        self.assertEqual(list(jobs), ["health"])
        self.assertIsNone(_permissions(jobs["health"], 4))
        self.assertIn("persist-credentials: false", jobs["health"])
        commands = "\n".join(re.sub(r"\\\s*\n\s*", " ", block) for block in _shell_run_blocks(jobs["health"]))
        self.assertNotRegex(commands, r"\bgit\s+(push|commit)\b|--seal\b|vol_record\.py")
        self.assertEqual(re.findall(r"\bpython3?\s+(\S+\.py)", commands), ["scripts/record_health.py"])
        self.assertEqual(set(re.findall(r">>?\s*\"?([^\s\";]+)", commands)),
                         {"record.json", "data.json", "refresh.json", "reproduce.json", "acknowledged.txt",
                          "anchor-jobs/${id}.json", "${GITHUB_OUTPUT}", "${GITHUB_STEP_SUMMARY}", "/dev/null"})
        # The anchor job lists are read-only API calls, one per recent successful run on main.
        self.assertEqual(re.findall(r"gh api (\S+)", commands)[-1], '"repos/${GH_REPO}/actions/runs/${id}/jobs"')
        # The script leaves pull requests' runs out by their event, so both run lists must carry it.
        self.assertEqual(re.findall(r"--json (\S+) > (refresh|reproduce)\.json", commands),
                         [("conclusion,url,createdAt,event", "refresh"), ("conclusion,url,createdAt,event", "reproduce")])
        # It runs after every Refresh and Reproduce run on main, never for a pull request's run, and a
        # drill (the `now` input) never reaches the step that writes the issue.
        names = {(WORKFLOW_DIRECTORY / f).read_text(encoding="utf-8").split("\n", 1)[0].removeprefix("name: ")
                 for f in ("refresh.yml", "reproduce.yml")}
        listed = re.search(r"workflow_run:\n\s+workflows: \[(.*)\]", source).group(1)
        self.assertEqual({name.strip().strip('"') for name in listed.split(",")}, names)
        self.assertIn("    if: github.event_name != 'workflow_run' || github.event.workflow_run.event != 'pull_request'",
                      jobs["health"])
        issue_step = jobs["health"][jobs["health"].index("- name: Open, update or close the health issue"):]
        self.assertRegex(issue_step[:issue_step.index("run: |")], r"\n\s+if: inputs\.now == ''\n")

    def test_dependabot_groups_leave_majors_to_their_own_prs(self):
        # A major bump (e.g. one half of the upload/download-artifact pair) must not ride along in
        # a routine grouped PR. Only the react group takes majors: its packages move in lockstep.
        source = (REPOSITORY_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        groups = re.findall(r"^      ([\w-]+):\n((?:        .*\n|[ \t]*\n)*)", source, re.M)
        self.assertEqual({name for name, _ in groups}, {"actions", "react", "npm-minor-and-patch", "python"})
        for name, body in groups:
            if name == "react":
                continue
            with self.subTest(group=name):
                types = re.search(r"^        update-types:\n((?:          - .*\n)+)", body, re.M)
                self.assertIsNotNone(types, f"group {name} must restrict update-types")
                self.assertEqual(sorted(re.findall(r"- (\w+)", types.group(1))), ["minor", "patch"])

    def test_dependabot_waits_out_the_two_week_pinning_policy(self):
        # requirements-dev.txt pins only releases at least two weeks old; each ecosystem's cooldown
        # keeps Dependabot from opening PRs for versions that policy would have to hold back.
        source = (REPOSITORY_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        entries = re.split(r"^  - package-ecosystem: ", source, flags=re.M)[1:]
        self.assertEqual(sorted(entry.split("\n", 1)[0].strip() for entry in entries),
                         ["docker", "github-actions", "npm", "pip"])
        for entry in entries:
            with self.subTest(ecosystem=entry.split("\n", 1)[0].strip()):
                days = re.search(r"^    cooldown:\n      default-days: (\d+)$", entry, re.M)
                self.assertIsNotNone(days, "each ecosystem sets a cooldown")
                self.assertGreaterEqual(int(days.group(1)), 14)

    def test_npm_installs_never_run_lifecycle_scripts(self):
        for workflow in _workflow_files():
            for command in _shell_run_blocks(workflow.read_text(encoding="utf-8")):
                for line in command.splitlines():
                    # Command position only (start of line, or after if/then/&&/;), not echo text.
                    if re.search(r"(^|\bif |\bthen |&& |; )\s*npm (ci|install)\b", line.strip()):
                        self.assertIn("--ignore-scripts", line, f"{workflow.name}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
