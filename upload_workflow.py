from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app_settings import AssetSourceMode
from asset_sync import AssetRepository, SyncResult, SyncState
from asset_upload import UploadResult, auto_update
from github_auth import GitHubSession
from localization import tr
from pending_upload import (
    PENDING_SCHEMA_VERSION,
    PendingPhase,
    PendingUpload,
    PendingUploadStore,
)
from repository_target import RepositoryTarget
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
    github_target: RepositoryTarget | None = None
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
        repository_factory: Callable[[GitHubSession, RepositoryTarget], AssetRepository] | None = None,
        pending_store: PendingUploadStore | None = None,
        *,
        clock: Callable[[], str] = _utc_timestamp,
    ) -> None:
        self.local_uploader = local_uploader
        self.repository_factory = repository_factory
        self.pending_store = pending_store
        self.clock = clock

    def _repository(
        self,
        session: GitHubSession | None,
        target: RepositoryTarget,
    ) -> AssetRepository:
        if session is None:
            raise WorkflowBlocked(tr("GitHub 로그인이 필요합니다.", "GitHub login is required."))
        if self.repository_factory is None:
            raise WorkflowBlocked(tr("GitHub 저장소 동기화가 구성되지 않았습니다.", "GitHub repository sync is not configured."))
        return self.repository_factory(session, target)

    def _require_no_pending(self) -> None:
        if self.pending_store is not None and self.pending_store.exists():
            raise WorkflowBlocked(tr("먼저 GitHub Push 재시도를 완료하세요.", "Complete the GitHub push retry first."))

    def _ready_repository(
        self,
        session: GitHubSession | None,
        target: RepositoryTarget,
    ) -> tuple[AssetRepository, SyncResult]:
        repository = self._repository(session, target)
        sync = repository.prepare()
        if sync.state is not SyncState.READY:
            raise WorkflowBlocked(sync.message, sync)
        return repository, sync

    def prepare_github(
        self,
        session: GitHubSession | None,
        target: RepositoryTarget,
    ) -> SyncResult:
        _repository, sync = self._ready_repository(session, target)
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
            sync = SyncResult(SyncState.READY, tr("로컬 모드", "Local mode"))
            asset_folder = options.local_asset_folder
        else:
            target = options.github_target or RepositoryTarget.defaults()
            repository, sync = self._ready_repository(session, target)
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
            tr("VPK 생성 완료", "VPK creation complete"),
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
                SyncResult(SyncState.READY, tr("로컬 모드", "Local mode")),
                tr("Steam 업로드 완료", "Steam upload complete"),
            )
        if self.pending_store is None:
            raise WorkflowBlocked(tr("GitHub push 복구 저장소가 구성되지 않았습니다.", "GitHub push recovery storage is not configured."))
        target = options.github_target or RepositoryTarget.defaults()
        repository, sync = self._ready_repository(session, target)
        base_commit = repository.head()
        pending = PendingUpload(
            schema_version=PENDING_SCHEMA_VERSION,
            phase=PendingPhase.STEAM_STARTED,
            workshop_id=options.workshop_id,
            note=note,
            steam_succeeded_at=None,
            base_commit=base_commit,
            commit_id=None,
            github_repository=target.full_name,
            github_branch=target.branch,
            github_asset_subdir=target.asset_subdir_text,
        )
        self.pending_store.save(pending)
        try:
            result = self.local_uploader(
                options.workshop_id,
                options.chunk_size_mb,
                False,
                **options.uploader_kwargs(repository.asset_folder),
                change_note=note,
            )
        except Exception:
            self.pending_store.clear()
            raise
        pending = replace(
            pending,
            phase=PendingPhase.STEAM_SUCCEEDED,
            workshop_id=result.workshop_id,
            steam_succeeded_at=self.clock(),
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
            tr("Steam 업로드 및 GitHub push 완료", "Steam upload and GitHub push complete"),
        )


    def resolve_ambiguous_steam(
        self,
        succeeded: bool,
        workshop_id: int | None = None,
    ) -> PendingUpload | None:
        if self.pending_store is None:
            raise WorkflowBlocked(tr("GitHub push 복구 저장소가 구성되지 않았습니다.", "GitHub push recovery storage is not configured."))
        pending = self.pending_store.load()
        if pending is None:
            raise WorkflowBlocked(tr("확인할 Steam 업로드 기록이 없습니다.", "There is no Steam upload record to confirm."))
        if pending.phase is not PendingPhase.STEAM_STARTED:
            raise WorkflowBlocked(tr("Steam 결과 확인이 필요한 기록이 아닙니다.", "This record does not require Steam result confirmation."))
        if not succeeded:
            self.pending_store.clear()
            return None
        confirmed_id = pending.workshop_id if workshop_id is None else workshop_id
        if (
            not isinstance(confirmed_id, int)
            or isinstance(confirmed_id, bool)
            or confirmed_id <= 0
        ):
            raise WorkflowBlocked(tr("성공한 Steam Workshop Addon ID가 필요합니다.", "A successful Steam Workshop Addon ID is required."))
        pending = replace(
            pending,
            phase=PendingPhase.STEAM_SUCCEEDED,
            workshop_id=confirmed_id,
            steam_succeeded_at=self.clock(),
        )
        self.pending_store.save(pending)
        return pending
    def retry_pending_push(
        self,
        session: GitHubSession | None,
    ) -> WorkflowResult:
        if self.pending_store is None:
            raise WorkflowBlocked(tr("GitHub push 복구 저장소가 구성되지 않았습니다.", "GitHub push recovery storage is not configured."))
        pending = self.pending_store.load()
        if pending is None:
            raise WorkflowBlocked(tr("재시도할 GitHub push가 없습니다.", "There is no GitHub push to retry."))
        if pending.phase is PendingPhase.STEAM_STARTED:
            raise WorkflowBlocked(
                tr("Steam 업로드 결과를 확인할 수 없어 자동 재시도를 중단했습니다.", "Automatic retry stopped because the Steam upload result could not be confirmed.")
            )
        repository = self._repository(session, pending.target)
        sync = repository.prepare()
        if sync.state is not SyncState.READY:
            raise WorkflowBlocked(sync.message, sync)
        commit_already_created = (
            pending.commit_id is None
            and repository.head() != pending.base_commit
            and repository.head_message() == pending.note
        )
        if pending.commit_id is None:
            if commit_already_created:
                commit_id = repository.head()
            else:
                commit_id = repository.commit(pending.note, session.user)
            pending = replace(pending, commit_id=commit_id)
            self.pending_store.save(pending)
        repository.push()
        self.pending_store.clear()
        return WorkflowResult(
            None,
            pending.workshop_id,
            sync,
            tr("GitHub push 완료", "GitHub push complete"),
        )
