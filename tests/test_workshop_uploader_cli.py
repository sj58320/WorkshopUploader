from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import workshop_uploader_cli
from repository_target import DEFAULT_ASSET_SUBDIR, DEFAULT_BRANCH, DEFAULT_REPOSITORY


class WorkshopUploaderCliTests(unittest.TestCase):
    def test_sync_defaults_match_gui_repository_defaults(self) -> None:
        args = workshop_uploader_cli.parse_args(["sync"])

        self.assertEqual(args.command, "sync")
        self.assertEqual(args.repo, DEFAULT_REPOSITORY)
        self.assertEqual(args.branch, DEFAULT_BRANCH)
        self.assertEqual(args.asset_path, DEFAULT_ASSET_SUBDIR)

    def test_local_build_arguments_do_not_require_repository_override(self) -> None:
        args = workshop_uploader_cli.parse_args(
            [
                "build",
                "--local-folder",
                "C:/assets",
                "--addon-id",
                "1234567890",
                "--note",
                "update models",
            ]
        )

        self.assertEqual(args.command, "build")
        self.assertEqual(args.local_folder, Path("C:/assets"))
        self.assertEqual(args.addon_id, 1234567890)
        self.assertEqual(args.note, "update models")

    def test_profile_build_does_not_require_addon_id(self) -> None:
        args = workshop_uploader_cli.parse_args(
            ["build", "--profile", "weapon"]
        )

        self.assertEqual(args.profile, "weapon")
        self.assertIsNone(args.addon_id)
        self.assertEqual(
            workshop_uploader_cli.CliApplication._target(args).asset_subdir_text,
            ".",
        )

    def test_json_success_is_one_machine_readable_object(self) -> None:
        payload = {
            "ok": True,
            "command": "sync",
            "message": "ready",
        }
        stdout = io.StringIO()

        with patch.object(
            workshop_uploader_cli.CliApplication,
            "run",
            return_value=payload,
        ), redirect_stdout(stdout):
            exit_code = workshop_uploader_cli.main(["--json", "sync"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        self.assertEqual(stdout.getvalue().count("\n"), 1)

    def test_json_error_returns_nonzero_and_structured_error(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(
            workshop_uploader_cli.CliApplication,
            "run",
            side_effect=RuntimeError("offline"),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = workshop_uploader_cli.main(["--json", "sync"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "ok": False,
                "error": "offline",
                "type": "RuntimeError",
            },
        )
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
