from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from asset_sync import AssetRepository, SyncState, parse_git_progress
from git_client import GitRunner, ensure_askpass
from github_auth import GitHubUser
from windows_credentials import GitHubCredential


def run_git(cwd: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        [shutil.which("git") or "git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


class AssetRepositoryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.local = self.root / "local"
        self.runtime = self.root / "runtime"
        self.remote.mkdir()
        self.seed.mkdir()
        run_git(self.remote, "init", "--bare")
        run_git(self.seed, "init")
        run_git(self.seed, "config", "user.name", "Test User")
        run_git(self.seed, "config", "user.email", "test@example.com")
        asset_folder = self.seed / "in" / "additional_files"
        asset_folder.mkdir(parents=True)
        (asset_folder / "base.txt").write_text("base", encoding="utf-8")
        run_git(self.seed, "add", "-A")
        run_git(self.seed, "commit", "-m", "initial")
        run_git(self.seed, "branch", "-M", "main")
        run_git(self.seed, "remote", "add", "origin", self.remote.as_uri())
        run_git(self.seed, "push", "-u", "origin", "main")
        run_git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.runner = GitRunner(
            Path(shutil.which("git") or "git"),
            ensure_askpass(self.runtime),
        )
        self.credential = GitHubCredential(
            "token", None, None, None, "asset-user", 123
        )
        self.repository = AssetRepository(
            self.runner,
            self.local,
            self.credential,
            remote_url=self.remote.as_uri(),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def push_remote_change(self, relative_path: str, contents: str) -> None:
        path = self.seed / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        run_git(self.seed, "add", "-A")
        run_git(self.seed, "commit", "-m", f"change {relative_path}")
        run_git(self.seed, "push", "origin", "main")

    def test_initial_clone_is_shallow_single_branch(self) -> None:
        result = self.repository.prepare()

        self.assertEqual(result.state, SyncState.READY)
        self.assertEqual(
            run_git(self.local, "rev-parse", "--is-shallow-repository"),
            "true",
        )
        self.assertEqual(run_git(self.local, "branch", "--show-current"), "main")


    def test_custom_branch_and_asset_subdir_are_used_for_commit_and_push(self) -> None:
        run_git(self.seed, "checkout", "-b", "asset-dev")
        custom_asset = self.seed / "game" / "assets"
        custom_asset.mkdir(parents=True)
        (custom_asset / "custom.txt").write_text("base", encoding="utf-8")
        run_git(self.seed, "add", "-A")
        run_git(self.seed, "commit", "-m", "add custom assets")
        run_git(self.seed, "push", "-u", "origin", "asset-dev")

        custom_local = self.root / "custom-local"
        repository = AssetRepository(
            self.runner,
            custom_local,
            self.credential,
            remote_url=self.remote.as_uri(),
            branch="asset-dev",
            asset_subdir="game/assets",
        )

        result = repository.prepare()
        (repository.asset_folder / "custom.txt").write_text(
            "changed", encoding="utf-8"
        )
        commit_id = repository.commit(
            "custom target", GitHubUser("asset-user", 123, None)
        )
        repository.push()

        self.assertEqual(result.state, SyncState.READY)
        self.assertEqual(run_git(custom_local, "branch", "--show-current"), "asset-dev")
        self.assertEqual(repository.asset_folder, custom_local / "game" / "assets")
        self.assertEqual(
            run_git(self.remote, "show", "-s", "--format=%B", "asset-dev"),
            "custom target",
        )
        committed = run_git(
            custom_local, "show", "--format=", "--name-only", commit_id
        )
        self.assertEqual(committed, "game/assets/custom.txt")

    def test_modified_push_url_is_rejected(self) -> None:
        self.repository.prepare()
        unexpected_remote = self.root / "unexpected.git"
        unexpected_remote.mkdir()
        run_git(unexpected_remote, "init", "--bare")
        run_git(
            self.local, "remote", "set-url", "--push", "origin",
            unexpected_remote.as_uri(),
        )

        result = self.repository.prepare()
        self.assertEqual(result.state, SyncState.ERROR)

    def test_commit_rechecks_main_branch(self) -> None:
        self.repository.prepare()
        run_git(self.local, "checkout", "-b", "unexpected")
        (self.repository.asset_folder / "base.txt").write_text(
            "changed", encoding="utf-8"
        )

        with self.assertRaises(RuntimeError):
            self.repository.commit(
                "must stay on main", GitHubUser("user", 1, None))
    def test_second_sync_fast_forwards_remote_change(self) -> None:
        self.repository.prepare()
        old_head = run_git(self.local, "rev-parse", "HEAD")
        self.push_remote_change("in/additional_files/new.txt", "new")

        result = self.repository.prepare()

        self.assertEqual(result.state, SyncState.READY)
        self.assertNotEqual(run_git(self.local, "rev-parse", "HEAD"), old_head)
        self.assertEqual((self.repository.asset_folder / "new.txt").read_text(), "new")

    def test_dirty_untracked_file_survives_unrelated_sync(self) -> None:
        self.repository.prepare()
        dirty_file = self.repository.asset_folder / "local.txt"
        dirty_file.write_text("local", encoding="utf-8")
        self.push_remote_change("in/additional_files/remote.txt", "remote")

        result = self.repository.prepare()

        self.assertEqual(result.state, SyncState.READY)
        self.assertEqual(dirty_file.read_text(encoding="utf-8"), "local")

    def test_same_file_conflict_is_reported_without_deleting_file(self) -> None:
        self.repository.prepare()
        shared = self.repository.asset_folder / "base.txt"
        shared.write_text("local", encoding="utf-8")
        self.push_remote_change("in/additional_files/base.txt", "remote")

        result = self.repository.prepare()

        self.assertEqual(result.state, SyncState.CONFLICT)
        self.assertIn("in/additional_files/base.txt", result.conflicts)
        self.assertTrue(shared.exists())

    def test_multiline_and_empty_commits_are_created(self) -> None:
        self.repository.prepare()
        (self.repository.asset_folder / "base.txt").write_text("changed", encoding="utf-8")
        user = GitHubUser("asset-user", 123, None)

        first_commit = self.repository.commit("add models\nfix materials", user)
        first_message = run_git(self.local, "show", "-s", "--format=%B", first_commit)
        empty_commit = self.repository.commit("Update asset", user)

        self.assertEqual(first_message, "add models\nfix materials")
        self.assertNotEqual(empty_commit, first_commit)
        self.assertEqual(run_git(self.local, "show", "-s", "--format=%B", empty_commit),
                         "Update asset")

    def test_commit_excludes_staged_files_outside_asset_folder(self) -> None:
        self.repository.prepare()
        outside = self.local / "README.md"
        outside.write_text("do not commit", encoding="utf-8")
        run_git(self.local, "add", "README.md")
        (self.repository.asset_folder / "base.txt").write_text(
            "changed", encoding="utf-8"
        )

        commit_id = self.repository.commit(
            "asset only", GitHubUser("asset-user", 123, None)
        )

        committed = run_git(self.local, "show", "--format=", "--name-only", commit_id)
        self.assertIn("in/additional_files/base.txt", committed.splitlines())
        self.assertNotIn("README.md", committed.splitlines())
        self.assertIn("A  README.md", run_git(self.local, "status", "--short"))


class GitProgressParserTests(unittest.TestCase):
    def test_maps_git_phases_to_monotonic_overall_percent(self) -> None:
        receiving = parse_git_progress("Receiving objects: 42% (42/100), 1.00 MiB")
        resolving = parse_git_progress("Resolving deltas: 50% (5/10)")
        updating = parse_git_progress("Updating files: 100% (10/10)")

        self.assertIsNotNone(receiving)
        self.assertEqual(receiving.phase_percent, 42)
        self.assertEqual(receiving.overall_percent, 36)
        self.assertEqual((receiving.current, receiving.total), (42, 100))
        self.assertEqual(resolving.overall_percent, 90)
        self.assertEqual(updating.overall_percent, 100)

    def test_ignores_unrelated_git_output(self) -> None:
        self.assertIsNone(parse_git_progress("Already up to date."))
if __name__ == "__main__":
    unittest.main()
