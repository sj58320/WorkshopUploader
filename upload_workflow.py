from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app_settings import AssetSourceMode
from asset_sync import AssetRepository, SyncResult, SyncState
from asset_upload import UploadResult, auto_update
from github_auth import GitHubSession
from pending_upload import PendingUpload, PendingUploadStore
from update_notes import normalize_update_note


@dataclass(frozen=True)
class UploadOptions:
    workshop_id: int
    chunk_size_mb: int
    title: str | None
    description: str | None
    local_asset_folder: Path
    output_folder: Path
    preview_path: Path | None
    isolated_output: bool = True

    def uploader_kwargs(self, asset_folder: Path) -> dict:
        return {
            "asset_folder": asset_folder,
            "output_folder": self.output_folder,
            "isolated_output": self.isolated_output,
            "preview_path": self.preview_path,
            "workshop_title": self.title,
            "workshop_description": self.description,
        }


@dataclass(frozen=True)
class WorkflowResult:
    pack_folder: Path | None
    workshop_id: int | None
    sync: SyncResult
    message: str


class WorkflowBlocked(RuntimeError):
    def __init__(self, message: str, sync: SyncResult | None = None) -> None:
        super().__init__(message)
        self.sync = sync


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class UploadWorkflow:
    def __init__(
        self,
        local_uploader: Callable = auto_update,
        repository_factory: Callable[[GitHubSession], AssetRepository] | None = None,
        pending_store: PendingUploadStore | None = None,
        *,
        clock: Callable[[], str] = _utc_timestamp,
    ) -> None:
        self.local_uploader = local_uploader
        self.repository_factory = repository_factory
        self.pending_store = pending_store
        self.clock = clock

    def _repository(self, session: GitHubSession | None) -> AssetRepository:
        if session is None:
            raise WorkflowBlocked("GitHub 로그인이 필요합니다.")
        if self.repository_factory is None:
            raise WorkflowBlocked("GitHub 저장소 동기화가 구성되지 않았습니다.")
        return self.repository_factory(session)

    def _require_no_pending(self) -> None:
        if self.pending_store is not None and self.pending_store.exists():
            raise WorkflowBlocked("먼저 GitHub Push 재시도를 완료하세요.")

    def _ready_repository(
        self,
        session: GitHubSession | None,
    ) -> tuple[AssetRepository, SyncResult]:
        repository = self._repository(session)
        sync = repository.prepare()
        if sync.state is not SyncState.READY:
            raise WorkflowBlocked(sync.message, sync)
        return repository, sync

    def prepare_github(self, session: GitHubSession | None) -> SyncResult:
        _repository, sync = self._ready_repository(session)
        return sync

    def build_vpk(
        self,
        mode: AssetSourceMode,
        options: UploadOptions,
        raw_note: str,
        session: GitHubSession | None = None,
    ) -> WorkflowResult:
        self._require_no_pending()
        note = normalize_update_note(raw_note)
        if mode is AssetSourceMode.LOCAL:
            sync = SyncResult(SyncState.READY, "로컬 모드")
            asset_folder = options.local_asset_folder
        else:
            repository, sync = self._ready_repository(session)
            asset_folder = repository.asset_folder
        result: UploadResult = self.local_uploader(
            options.workshop_id,
            options.chunk_size_mb,
            True,
            **options.uploader_kwargs(asset_folder),
            change_note=note,
        )
        return WorkflowResult(
            result.pack_folder,
            result.workshop_id,
            sync,
            "VPK 생성 완료",
        )

    def upload(
        self,
        mode: AssetSourceMode,
        options: UploadOptions,
        raw_note: str,
        session: GitHubSession | None = None,
    ) -> WorkflowResult:
        self._require_no_pending()
        note = normalize_update_note(raw_note)
        if mode is AssetSourceMode.LOCAL:
            result: UploadResult = self.local_uploader(
                options.workshop_id,
                options.chunk_size_mb,
                False,
                **options.uploader_kwargs(options.local_asset_folder),
                change_note=note,
            )
            return WorkflowResult(
                result.pack_folder,
                result.workshop_id,
                SyncResult(SyncState.READY, "로컬 모드"),
                "Steam 업로드 완료",
            )
        if self.pending_store is None:
            raise WorkflowBlocked("GitHub push 복구 저장소가 구성되지 않았습니다.")
        repository, sync = self._ready_repository(session)
        base_commit = repository.head()
        result = self.local_uploader(
            options.workshop_id,
            options.chunk_size_mb,
            False,
            **options.uploader_kwargs(repository.asset_folder),
            change_note=note,
        )
        pending = PendingUpload(
            schema_version=1,
            workshop_id=result.workshop_id,
            note=note,
            steam_succeeded_at=self.clock(),
            base_commit=base_commit,
            commit_id=None,
        )
        self.pending_store.save(pending)
        commit_id = repository.commit(note, session.user)
        pending = replace(pending, commit_id=commit_id)
        self.pending_store.save(pending)
        repository.push()
        self.pending_store.clear()
        return WorkflowResult(
            result.pack_folder,
            result.workshop_id,
            sync,
            "Steam 업로드 및 GitHub push 완료",
        )

    def retry_pending_push(
        self,
        session: GitHubSession | None,
    ) -> WorkflowResult:
        if self.pending_store is None:
            raise WorkflowBlocked("GitHub push 복구 저장소가 구성되지 않았습니다.")
        pending = self.pending_store.load()
        if pending is None:
            raise WorkflowBlocked("재시도할 GitHub push가 없습니다.")
        repository, sync = self._ready_repository(session)
        if pending.commit_id is None:
            commit_id = repository.commit(pending.note, session.user)
            pending = replace(pending, commit_id=commit_id)
            self.pending_store.save(pending)
        repository.push()
        self.pending_store.clear()
        return WorkflowResult(
            None,
            pending.workshop_id,
            sync,
            "GitHub push 완료",
        )
