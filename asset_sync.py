from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from git_client import GitCommandError, GitRunner
from github_auth import GitHubUser
from windows_credentials import GitHubCredential


OWNER = "RevenantZE"
REPOSITORY = "RSS-ZE-ASSET"
REMOTE_URL = "https://github.com/RevenantZE/RSS-ZE-ASSET.git"
DEFAULT_BRANCH = "main"
ASSET_SUBDIR = Path("in") / "additional_files"
STASH_MESSAGE = "WorkshopUploader automatic sync"


class SyncState(str, Enum):
    LOGIN_REQUIRED = "login_required"
    DOWNLOADING = "downloading"
    CHECKING = "checking"
    READY = "ready"
    CONFLICT = "conflict"
    PUSH_PENDING = "push_pending"
    ERROR = "error"


@dataclass(frozen=True)
class SyncResult:
    state: SyncState
    message: str
    conflicts: tuple[str, ...] = ()


class AssetRepository:
    def __init__(
        self,
        runner: GitRunner,
        root: Path,
        credential: GitHubCredential,
        *,
        remote_url: str = REMOTE_URL,
        branch: str = DEFAULT_BRANCH,
        progress: Callable[[SyncState, str], None] | None = None,
    ) -> None:
        self.runner = runner
        self.root = Path(root)
        self.credential = credential
        self.remote_url = remote_url
        self.branch = branch
        self.progress = progress or (lambda _state, _message: None)

    @property
    def asset_folder(self) -> Path:
        return self.root / ASSET_SUBDIR

    def _git(self, args, *, credential=False, stdin=None, check=True):
        return self.runner.run(
            args,
            cwd=self.root,
            credential=self.credential if credential else None,
            stdin=stdin,
            check=check,
        )

    @staticmethod
    def _normalized_remote(value: str) -> str:
        normalized = value.strip().rstrip("/")
        if normalized.lower().endswith(".git"):
            normalized = normalized[:-4]
        return normalized

    def _validate_repository(self) -> SyncResult | None:
        if not (self.root / ".git").is_dir():
            return SyncResult(
                SyncState.ERROR,
                f"Git 저장소가 올바르지 않습니다: {self.root}",
            )
        origin = self._git(["remote", "get-url", "origin"], check=False)
        if origin.returncode != 0 or self._normalized_remote(
            origin.stdout
        ) != self._normalized_remote(self.remote_url):
            return SyncResult(SyncState.ERROR, "origin이 지정된 에셋 저장소와 다릅니다.")
        branch = self._git(["branch", "--show-current"], check=False).stdout.strip()
        if branch != self.branch:
            return SyncResult(
                SyncState.ERROR,
                f"현재 브랜치가 {self.branch}이 아닙니다: {branch or 'detached HEAD'}",
            )
        return None

    def _conflicts(self) -> tuple[str, ...]:
        result = self._git(
            ["diff", "--name-only", "--diff-filter=U"],
            check=False,
        )
        return tuple(line for line in result.stdout.splitlines() if line.strip())

    def _error_result(self, error: GitCommandError) -> SyncResult:
        conflicts = self._conflicts()
        if conflicts:
            return SyncResult(
                SyncState.CONFLICT,
                "동일한 파일의 원격/로컬 변경이 충돌했습니다.",
                conflicts,
            )
        return SyncResult(SyncState.ERROR, str(error))

    def _stash_dirty_tree(self) -> str | None:
        status = self._git(
            ["status", "--porcelain", "--untracked-files=all"]
        ).stdout
        if not status.strip():
            return None
        self._git(
            ["stash", "push", "--include-untracked", "--message", STASH_MESSAGE]
        )
        return self._git(["rev-parse", "refs/stash"]).stdout.strip()

    def _restore_stash(self, stash_id: str) -> SyncResult | None:
        try:
            self._git(["stash", "apply", "--index", stash_id])
        except GitCommandError as error:
            return self._error_result(error)
        top_stash = self._git(["rev-parse", "refs/stash"], check=False)
        if top_stash.returncode == 0 and top_stash.stdout.strip() == stash_id:
            self._git(["stash", "drop", "stash@{0}"])
        return None

    def _count(self, revision_range: str) -> int:
        result = self._git(["rev-list", "--count", revision_range])
        return int(result.stdout.strip() or "0")

    def has_pending_commits(self) -> bool:
        return self._count(f"origin/{self.branch}..HEAD") > 0

    def head(self) -> str:
        return self._git(["rev-parse", "HEAD"]).stdout.strip()

    def prepare(self) -> SyncResult:
        if not self.root.exists():
            self.progress(SyncState.DOWNLOADING, "최초 에셋을 다운로드하는 중...")
            self.root.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.runner.run(
                    [
                        "clone",
                        "--depth",
                        "1",
                        "--single-branch",
                        "--branch",
                        self.branch,
                        self.remote_url,
                        str(self.root),
                    ],
                    credential=self.credential,
                )
            except GitCommandError as error:
                return SyncResult(SyncState.ERROR, str(error))
        self.progress(SyncState.CHECKING, "원격 변경사항을 확인하는 중...")
        validation = self._validate_repository()
        if validation is not None:
            return validation
        existing_conflicts = self._conflicts()
        if existing_conflicts:
            return SyncResult(
                SyncState.CONFLICT,
                "먼저 기존 Git 충돌을 해결하세요.",
                existing_conflicts,
            )
        try:
            stash_id = self._stash_dirty_tree()
        except GitCommandError as error:
            return self._error_result(error)
        try:
            self._git(
                ["fetch", "--prune", "origin", self.branch],
                credential=True,
            )
            if self.has_pending_commits():
                self._git(["rebase", f"origin/{self.branch}"])
            elif self._count(f"HEAD..origin/{self.branch}") > 0:
                self._git(["merge", "--ff-only", f"origin/{self.branch}"])
        except GitCommandError as error:
            result = self._error_result(error)
            if result.state is SyncState.ERROR and stash_id is not None:
                restored = self._restore_stash(stash_id)
                return restored or result
            return result
        if stash_id is not None:
            restored = self._restore_stash(stash_id)
            if restored is not None:
                return restored
        if not self.asset_folder.is_dir():
            return SyncResult(
                SyncState.ERROR,
                f"에셋 폴더가 없습니다: {self.asset_folder}",
            )
        return SyncResult(SyncState.READY, "최신 상태")

    def commit(self, note: str, user: GitHubUser) -> str:
        self._git(["config", "user.name", user.login])
        self._git(["config", "user.email", user.commit_email])
        self._git(["add", "-A", "--", ASSET_SUBDIR.as_posix()])
        self._git(["commit", "--allow-empty", "--file", "-"], stdin=note)
        return self.head()

    def push(self) -> None:
        self._git(
            ["fetch", "--prune", "origin", self.branch],
            credential=True,
        )
        if self._count(f"HEAD..origin/{self.branch}") > 0:
            self._git(["rebase", f"origin/{self.branch}"])
        self._git(
            ["push", "origin", f"HEAD:{self.branch}"],
            credential=True,
        )
