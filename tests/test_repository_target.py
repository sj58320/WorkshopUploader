from __future__ import annotations

import unittest
from pathlib import Path

from repository_target import RepositoryTarget, RepositoryTargetError


class RepositoryTargetTests(unittest.TestCase):
    def test_accepts_owner_repo_and_github_https_url(self) -> None:
        short = RepositoryTarget.parse("octo-org/assets", "main", "game/assets")
        url = RepositoryTarget.parse(
            "https://github.com/octo-org/assets.git/", "release/v1", "."
        )

        self.assertEqual(short.full_name, "octo-org/assets")
        self.assertEqual(short.remote_url, "https://github.com/octo-org/assets.git")
        self.assertEqual(short.asset_subdir_text, "game/assets")
        self.assertEqual(url.full_name, "octo-org/assets")
        self.assertEqual(url.asset_subdir_text, ".")

    def test_rejects_non_github_or_ambiguous_repository_urls(self) -> None:
        invalid = (
            "git@github.com:owner/repo.git",
            "https://example.com/owner/repo",
            "https://github.com/owner/repo/issues",
            "owner/repo/extra",
            "owner\\repo",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(RepositoryTargetError):
                RepositoryTarget.parse(value, "main", "assets")

    def test_rejects_unsafe_branches_and_asset_traversal(self) -> None:
        for branch in ("-main", "../main", "feature..one", "bad branch", "x.lock"):
            with self.subTest(branch=branch), self.assertRaises(RepositoryTargetError):
                RepositoryTarget.parse("owner/repo", branch, "assets")
        for subdir in ("../assets", "/assets", "assets/../private", "C:/assets"):
            with self.subTest(subdir=subdir), self.assertRaises(RepositoryTargetError):
                RepositoryTarget.parse("owner/repo", "main", subdir)

    def test_default_target_reuses_v0_1_1_clone_folder(self) -> None:
        target = RepositoryTarget.defaults()

        self.assertEqual(
            target.clone_root(Path("runtime")),
            Path("runtime/repos/RSS-ZE-ASSET"),
        )

    def test_clone_roots_are_stable_and_separate_branches(self) -> None:
        main = RepositoryTarget.parse("Owner/Repo", "main", "assets")
        same = RepositoryTarget.parse("owner/repo", "main", "other")
        dev = RepositoryTarget.parse("Owner/Repo", "dev", "assets")

        self.assertEqual(main.clone_root(Path("runtime")), same.clone_root(Path("runtime")))
        self.assertNotEqual(main.clone_root(Path("runtime")), dev.clone_root(Path("runtime")))
        self.assertEqual(main.asset_folder(Path("clone")), Path("clone/assets"))


if __name__ == "__main__":
    unittest.main()
