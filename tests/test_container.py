"""The reproduce container (Dockerfile, Makefile, .devcontainer/): pinned, and the same environment as CI.

The image must be rebuilt from the repository's own pins with the Reproduce workflow's numeric
variables; these checks keep the three files from drifting apart without building the image (the
Reproduce workflow's container job builds it and runs `make reproduce` in it).
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
REPRODUCE = (ROOT / ".github" / "workflows" / "reproduce.yml").read_text(encoding="utf-8")
NUMERIC = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_CORETYPE",
           "NPY_DISABLE_CPU_FEATURES", "PYTHONHASHSEED")


def _workflow_env() -> dict[str, str]:
    block = REPRODUCE[REPRODUCE.index("    env:\n"):REPRODUCE.index("    steps:\n")]
    return {key: value.strip("'\"") for key, value in re.findall(r"^      (\w+): (.+)$", block, re.M)}


def _assignments(text: str) -> dict[str, str]:
    return {key: value.strip("'\"") for key, value in re.findall(r"\b([A-Z_]+)=(\"[^\"]*\"|'[^']*'|[^\s\\]+)", text)}


class ContainerTest(unittest.TestCase):
    def test_every_base_image_is_pinned_by_digest(self):
        images = re.findall(r"^FROM\s+(\S+)", DOCKERFILE, re.M)
        self.assertEqual(len(images), 2, images)
        for image in images:
            with self.subTest(image=image):
                self.assertRegex(image, r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")

    def test_the_numeric_environment_is_the_reproduce_workflows(self):
        workflow = _workflow_env()
        env_block = DOCKERFILE[DOCKERFILE.index("\nENV "):DOCKERFILE.index("WORKDIR")]
        docker, make = _assignments(env_block), _assignments(MAKEFILE[MAKEFILE.index("REPRODUCE_ENV ="):])
        for key in NUMERIC:
            with self.subTest(key=key):
                self.assertIn(key, workflow)
                self.assertEqual(docker.get(key), workflow[key])
                self.assertEqual(make.get(key), workflow[key])

    def test_dependencies_come_only_from_the_repositorys_pins(self):
        installs = re.findall(r"pip install[^\n]*", DOCKERFILE)
        self.assertEqual(len(installs), 1)
        self.assertEqual(re.findall(r"-r (\S+)", installs[0]), ["requirements.txt", "requirements-dev.txt"])
        npm = re.findall(r"npm (?:ci|install)[^\n]*", DOCKERFILE)
        self.assertEqual(npm, ["npm ci --ignore-scripts --no-audit --no-fund"])
        devcontainer = (ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        config = json.loads(re.sub(r"^\s*//.*$", "", devcontainer, flags=re.M))
        self.assertEqual(config["build"], {"dockerfile": "../Dockerfile", "context": ".."})
        self.assertIn("--ignore-scripts", config["postCreateCommand"])

    def test_dependabot_keeps_the_images_on_ci_s_python_and_node_lines(self):
        # A Python 3.14 or Node 26 image would no longer match the runtimes CI tests and rebuilds on.
        workflows = "\n".join(path.read_text(encoding="utf-8")
                              for path in (ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertEqual(set(re.findall(r"python-version: '([\d.]+)'", workflows)), {"3.12"})
        self.assertEqual(set(re.findall(r"node-version: '(\d+)'", workflows)), {"24"})
        self.assertRegex(DOCKERFILE, r"(?m)^FROM python:3\.12\.\d+-")
        self.assertRegex(DOCKERFILE, r"(?m)^FROM node:24\.\d+\.\d+-")
        config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        docker = config[config.index("package-ecosystem: docker"):]
        self.assertIn('- dependency-name: python\n        versions: [">= 3.13"]', docker)
        self.assertIn('- dependency-name: node\n        versions: [">= 25"]', docker)

    def test_the_makefile_runs_the_reproduce_test_ci_runs(self):
        self.assertIn("tests.test_vol_lab.ArtifactContractTest.test_artifact_reproduces_from_the_snapshot", MAKEFILE)
        self.assertIn("RUN_SLOW=1", MAKEFILE)
        for target in ("lint", "type", "test", "reproduce", "check-frontend", "figures", "docs", "help"):
            with self.subTest(target=target):
                self.assertRegex(MAKEFILE, rf"(?m)^{re.escape(target)}:")
        self.assertIn("docker run --rm wti-vol make reproduce", REPRODUCE)


class DescribeRebuildTest(unittest.TestCase):
    """scripts/describe_rebuild.py, which the Reproduce workflow and `make reproduce` print."""

    def test_it_names_a_bit_for_bit_rebuild_and_a_rebuild_that_did_not_finish(self):
        import io
        from contextlib import redirect_stdout

        from scripts import describe_rebuild

        committed = json.loads((ROOT / "data" / "vol_lab.json").read_text(encoding="utf-8"))
        self.assertEqual(describe_rebuild.describe(committed, json.loads(json.dumps(committed))),
                         ["### Rebuild: bit for bit identical"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = describe_rebuild.main([str(ROOT / "no-such-rebuild.json")])
        self.assertEqual((code, out.getvalue()), (0, "### The rebuild did not finish\n"))


if __name__ == "__main__":
    unittest.main()
