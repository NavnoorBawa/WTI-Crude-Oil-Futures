import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import safe_paths


class SafePathTest(unittest.TestCase):
    def test_allowlisted_repository_artifacts_resolve_to_expected_roots(self):
        cases = (
            (
                safe_paths.data_json_path,
                "data/walk_forward_backtest_latest.json",
                safe_paths.DATA_ROOT / "walk_forward_backtest_latest.json",
            ),
            (
                safe_paths.public_json_path,
                "public/data.json",
                safe_paths.PUBLIC_ROOT / "data.json",
            ),
            (
                safe_paths.fixture_json_path,
                "tests/fixtures/valid_frozen_payload.json",
                safe_paths.FIXTURE_ROOT / "valid_frozen_payload.json",
            ),
        )

        for resolver, value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    resolver(value),
                    Path(os.path.realpath(expected)),
                )

    def test_dashboard_validator_accepts_only_production_or_fixture_payload(self):
        self.assertEqual(
            safe_paths.dashboard_payload_path("public/data.json"),
            safe_paths.public_json_path("public/data.json"),
        )
        self.assertEqual(
            safe_paths.dashboard_payload_path(
                "tests/fixtures/valid_frozen_payload.json"
            ),
            safe_paths.fixture_json_path(
                "tests/fixtures/valid_frozen_payload.json"
            ),
        )

    def test_untrusted_path_shapes_are_rejected(self):
        bad_values = (
            "../signal_state.json",
            "/tmp/signal_state.json",
            "data-evil/signal_state.json",
            "data/nested/signal_state.json",
            "data/signal_state.txt",
            "data/unknown.json",
            "data\\..\\signal_state.json",
            "",
        )
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_paths.data_json_path(value)

    def test_public_and_fixture_resolvers_reject_unexpected_names(self):
        for resolver, value in (
            (safe_paths.public_json_path, "public/price.json"),
            (safe_paths.public_json_path, "../public/data.json"),
            (safe_paths.fixture_json_path, "tests/fixtures/other.json"),
            (safe_paths.dashboard_payload_path, "data/signal_state.json"),
            (safe_paths.public_output_directory, "public/subdirectory"),
            (safe_paths.public_output_directory, "/tmp/public"),
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolver(value)

    def test_symlinked_file_cannot_escape_trusted_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            project_root = temporary_root / "project"
            data_root = project_root / "data"
            outside_root = temporary_root / "outside"
            data_root.mkdir(parents=True)
            outside_root.mkdir()
            outside_file = outside_root / "secret.json"
            outside_file.write_text("{}", encoding="utf-8")
            (data_root / "signal_state.json").symlink_to(outside_file)

            with (
                mock.patch.object(safe_paths, "PROJECT_ROOT", project_root),
                mock.patch.object(safe_paths, "DATA_ROOT", data_root),
                self.assertRaises(ValueError),
            ):
                safe_paths.data_json_path("data/signal_state.json")

    def test_symlinked_public_directory_cannot_escape_repository(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            project_root = temporary_root / "project"
            outside_root = temporary_root / "outside"
            project_root.mkdir()
            outside_root.mkdir()
            public_root = project_root / "public"
            public_root.symlink_to(outside_root, target_is_directory=True)

            with (
                mock.patch.object(safe_paths, "PROJECT_ROOT", project_root),
                mock.patch.object(safe_paths, "PUBLIC_ROOT", public_root),
                self.assertRaises(ValueError),
            ):
                safe_paths.public_output_directory("public")


if __name__ == "__main__":
    unittest.main()
