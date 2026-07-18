from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from github_config import (
    GitHubConfigError,
    load_github_app_config,
)


class GitHubConfigTests(unittest.TestCase):
    def test_source_run_can_use_environment_client_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = load_github_app_config(
                Path(temporary_directory),
                {"WORKSHOP_UPLOADER_GITHUB_CLIENT_ID": "Iv1.source-test"},
            )

        self.assertEqual(config.client_id, "Iv1.source-test")

    def test_bundle_reads_generated_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory)
            (bundle / "github_app.json").write_text(
                '{"client_id":"Iv1.bundle-test","repository_id":1157838808}',
                encoding="utf-8",
            )

            config = load_github_app_config(bundle, {})

        self.assertEqual(config.client_id, "Iv1.bundle-test")

    def test_missing_client_id_fails_closed_and_legacy_repo_id_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory)
            with self.assertRaises(GitHubConfigError):
                load_github_app_config(bundle, {})
            (bundle / "github_app.json").write_text(
                '{"client_id":"Iv1.bad","repository_id":1}',
                encoding="utf-8",
            )
            config = load_github_app_config(bundle, {})
            self.assertEqual(config.client_id, "Iv1.bad")


if __name__ == "__main__":
    unittest.main()
