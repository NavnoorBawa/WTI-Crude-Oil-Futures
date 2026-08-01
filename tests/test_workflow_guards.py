import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = REPOSITORY_ROOT / ".github" / "workflows"
FULL_COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}")


def _workflow_files():
    return sorted(
        (*WORKFLOW_DIRECTORY.glob("*.yml"), *WORKFLOW_DIRECTORY.glob("*.yaml"))
    )


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
        self.assertIn("load_record(); load_state()", source)
        self.assertIn('if [ -L "$state_directory_path" ]', source)
        self.assertIn("cp --remove-destination --", source)
        self.assertIn("http.followRedirects=false", source)
        self.assertEqual(source.count('if [ "${origin_url%.git}" != "$expected_origin" ]'), 2)

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
        self.assertIn("$STATE_DIRECTORY/signal_state.json", persistence)
        self.assertIn("$STATE_DIRECTORY/live_track_record.json", persistence)
        self.assertNotIn("price.json", persistence)


if __name__ == "__main__":
    unittest.main()
