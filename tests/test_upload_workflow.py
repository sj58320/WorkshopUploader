from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app_settings import AssetSourceMode
from asset_sync import SyncResult, SyncState
from asset_upload import UploadResult
from github_auth import GitHubSession, GitHubUser
from pending_upload import PendingUpload, PendingPhase
from repository_target import RepositoryTarget
from upload_workflow import UploadOptions, UploadWorkflow, WorkflowBlocked
from windows_credentials import GitHubCredential


SESSION = GitHubSession(
    GitHubCredential("token", None, None, None, "asset-user", 123),
    GitHubUser("asset-user", 123, None),
)


class FakeUploader:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.error: Exception | None = None
        self.on_call = None

    def __call__(self, workshop_id, chunk_size_mb, pack_only, **kwargs):
        self.calls.append(
            {
                "workshop_id": workshop_id,
                "chunk_size_mb": chunk_size_mb,
                "pack_only": pack_only,
                **kwargs,
            }
        )
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return UploadResult(Path(kwargs["output_folder"]), workshop_id, not pack_only)


class FakeRepository:
    def __init__(self, root: Path) -> None:
        self.asset_folder = root / "in" / "additional_files"
        self.asset_folder.mkdir(parents=True)
        self.prepare_result = SyncResult(SyncState.READY, "최신 상태")
        self.commit_calls: list[tuple[str, GitHubUser]] = []
        self.push_errors: list[Exception | None] = []
        self.push_count = 0
        self.current_head = "a" * 40
        self.pending_commits = False
        self.current_head_message = ""

    def prepare(self) -> SyncResult:
        return self.prepare_result

    def head(self) -> str:
        return self.current_head
    def head_message(self) -> str:
        return self.current_head_message

    def has_pending_commits(self) -> bool:
        return self.pending_commits


    def commit(self, note: str, user: GitHubUser) -> str:
        self.commit_calls.append((note, user))
        self.current_head = "b" * 40
        self.current_head_message = note
        self.pending_commits = True
        return self.current_head

    def push(self) -> None:
        self.push_count += 1
        if self.push_errors:
            error = self.push_errors.pop(0)
            if error is not None:
                raise error


class MemoryPendingStore:
    def __init__(self) -> None:
        self.value: PendingUpload | None = None

    def exists(self) -> bool:
        return self.value is not None

    def load(self) -> PendingUpload | None:
        return self.value

    def save(self, pending: PendingUpload) -> None:
        self.value = pending

    def clear(self) -> None:
        self.value = None


class UploadWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.repository = FakeRepository(root / "repo")
        self.uploader = FakeUploader()
        self.pending_store = MemoryPendingStore()
        self.factory_calls = 0
        self.factory_targets = []
        self.target = RepositoryTarget.parse("octo-org/assets", "dev", "custom/assets")

        def repository_factory(session, target):
            self.factory_calls += 1
            self.factory_targets.append(target)
            return self.repository

        self.workflow = UploadWorkflow(
            self.uploader,
            repository_factory,
            self.pending_store,
            clock=lambda: "2026-07-18T12:00:00Z",
        )
        self.options = UploadOptions(
            workshop_id=1234567890,
            chunk_size_mb=100,
            title="Title",
            description="Description",
            local_asset_folder=root / "local-assets",
            output_folder=root / "output",
            preview_path=None,
            github_target=self.target,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_local_mode_never_creates_github_repository(self) -> None:
        self.workflow.build_vpk(AssetSourceMode.LOCAL, self.options, "note")
        self.workflow.upload(AssetSourceMode.LOCAL, self.options, "note")

        self.assertEqual(self.factory_calls, 0)
        self.assertEqual(len(self.uploader.calls), 2)

    def test_github_not_ready_prevents_vpk_and_steam(self) -> None:
        self.repository.prepare_result = SyncResult(SyncState.CONFLICT, "conflict")

        with self.assertRaises(WorkflowBlocked):
            self.workflow.upload(
                AssetSourceMode.GITHUB,
                self.options,
                "note",
                SESSION,
            )

        self.assertEqual(self.uploader.calls, [])

    def test_steam_failure_creates_no_commit_or_pending_record(self) -> None:
        self.uploader.error = RuntimeError("Steam failed")

        with self.assertRaisesRegex(RuntimeError, "Steam failed"):
            self.workflow.upload(
                AssetSourceMode.GITHUB,
                self.options,
                "note",
                SESSION,
            )

        self.assertFalse(self.pending_store.exists())
        self.assertEqual(self.repository.commit_calls, [])

    def test_recovery_intent_is_saved_before_steam_starts(self) -> None:
        observed = []
        self.uploader.on_call = lambda: observed.append(self.pending_store.value)

        self.workflow.upload(
            AssetSourceMode.GITHUB,
            self.options,
            "note",
            SESSION,
        )

        self.assertEqual(observed[0].phase, PendingPhase.STEAM_STARTED)
        self.assertEqual(observed[0].workshop_id, self.options.workshop_id)
        self.assertEqual(observed[0].target, self.target)
        self.assertIsNone(observed[0].steam_succeeded_at)

    def test_github_success_uses_identical_normalized_note(self) -> None:
        self.workflow.upload(
            AssetSourceMode.GITHUB,
            self.options,
            "\nadd models\nfix materials\n",
            SESSION,
        )

        self.assertEqual(
            self.uploader.calls[0]["change_note"],
            "add models\nfix materials",
        )
        self.assertEqual(
            self.repository.commit_calls[0][0],
            "add models\nfix materials",
        )
        self.assertFalse(self.pending_store.exists())

    def test_push_failure_retry_skips_second_steam_upload(self) -> None:
        self.repository.push_errors = [RuntimeError("offline"), None]

        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.workflow.upload(
                AssetSourceMode.GITHUB,
                self.options,
                "",
                SESSION,
            )

        self.assertTrue(self.pending_store.exists())
        self.workflow.retry_pending_push(SESSION)
        self.assertEqual(len(self.uploader.calls), 1)
        self.assertEqual(self.repository.push_count, 2)
        self.assertFalse(self.pending_store.exists())


    def test_retry_after_commit_crash_does_not_create_duplicate_commit(self) -> None:
        self.repository.current_head = "b" * 40
        self.repository.pending_commits = True
        self.repository.current_head_message = "Update asset"
        self.pending_store.value = PendingUpload(
            schema_version=3,
            phase=PendingPhase.STEAM_SUCCEEDED,
            workshop_id=1234567890,
            note="Update asset",
            steam_succeeded_at="2026-07-18T12:00:00Z",
            base_commit="a" * 40,
            commit_id=None,
        )

        self.workflow.retry_pending_push(SESSION)

        self.assertEqual(self.repository.commit_calls, [])
        self.assertEqual(self.repository.push_count, 1)
        self.assertFalse(self.pending_store.exists())

    def test_preexisting_pending_commits_do_not_skip_asset_commit(self) -> None:
        self.repository.current_head = "a" * 40
        self.repository.current_head_message = "older local commit"
        self.repository.pending_commits = True
        self.pending_store.value = PendingUpload(
            schema_version=3,
            phase=PendingPhase.STEAM_SUCCEEDED,
            workshop_id=1234567890,
            note="Update asset",
            steam_succeeded_at="2026-07-18T12:00:00Z",
            base_commit="a" * 40,
            commit_id=None,
        )

        self.workflow.retry_pending_push(SESSION)

        self.assertEqual(len(self.repository.commit_calls), 1)
        self.assertEqual(self.repository.push_count, 1)
        self.assertFalse(self.pending_store.exists())


    def test_ambiguous_steam_result_blocks_automatic_retry(self) -> None:
        self.pending_store.value = PendingUpload(
            schema_version=3,
            phase=PendingPhase.STEAM_STARTED,
            workshop_id=1234567890,
            note="Update asset",
            steam_succeeded_at=None,
            base_commit="a" * 40,
            commit_id=None,
        )

        with self.assertRaisesRegex(WorkflowBlocked, "Steam"):
            self.workflow.retry_pending_push(SESSION)

        self.assertEqual(self.repository.commit_calls, [])
        self.assertEqual(self.repository.push_count, 0)
        self.assertTrue(self.pending_store.exists())

    def test_confirmed_steam_success_continues_with_git_only(self) -> None:
        self.pending_store.value = PendingUpload(
            schema_version=3,
            phase=PendingPhase.STEAM_STARTED,
            workshop_id=0,
            note="Update asset",
            steam_succeeded_at=None,
            base_commit="a" * 40,
            commit_id=None,
        )

        resolved = self.workflow.resolve_ambiguous_steam(True, 987654321)
        self.assertEqual(resolved.phase, PendingPhase.STEAM_SUCCEEDED)
        self.assertEqual(resolved.workshop_id, 987654321)

        self.workflow.retry_pending_push(SESSION)

        self.assertEqual(len(self.uploader.calls), 0)
        self.assertEqual(len(self.repository.commit_calls), 1)
        self.assertEqual(self.repository.push_count, 1)
        self.assertFalse(self.pending_store.exists())

    def test_confirmed_steam_failure_clears_ambiguous_record(self) -> None:
        self.pending_store.value = PendingUpload(
            schema_version=3,
            phase=PendingPhase.STEAM_STARTED,
            workshop_id=1234567890,
            note="Update asset",
            steam_succeeded_at=None,
            base_commit="a" * 40,
            commit_id=None,
        )

        resolved = self.workflow.resolve_ambiguous_steam(False)

        self.assertIsNone(resolved)
        self.assertFalse(self.pending_store.exists())
if __name__ == "__main__":

    unittest.main()
