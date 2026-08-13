from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Callable

from git_client import GitCommandError, GitRunner
from github_auth import GitHubUser
from repository_target import DEFAULT_ASSET_SUBDIR, DEFAULT_BRANCH
from windows_credentials import GitHubCredential
from localization import tr


REMOTE_URL = "https://github.com/RevenantZE/RSS-ZE-ASSET.git"
ASSET_SUBDIR = PurePosixPath(DEFAULT_ASSET_SUBDIR)
STASH_MESSAGE = "WorkshopUploader automatic sync"

_GIT_PROGRESS_PATTERN = re.compile(
    r"(?:remote:\s*)?(Receiving objects|Resolving deltas|Updating files):\s*"
    r"(\d+)%?(?:\s*\((\d+)/(\d+)\))?",
    re.IGNORECASE,
)
_GIT_PROGRESS_RANGES = {
    "receiving objects": (0, 85),
    "resolving deltas": (85, 10),
    "updating files": (95, 5),
}


@dataclass(frozen=True)
class GitTransferProgress:
    phase: str
    phase_percent: int
    overall_percent: int
    current: int | None
    total: int | None


def parse_git_progress(line: str) -> GitTransferProgress | None:
    match = _GIT_PROGRESS_PATTERN.search(line)
    if match is None:
        return None
    phase = match.group(1).lower()
    phase_percent = min(100, max(0, int(match.group(2))))
    start, span = _GIT_PROGRESS_RANGES[phase]
    current = int(match.group(3)) if match.group(3) is not None else None
    total = int(match.group(4)) if match.group(4) is not None else None
    return GitTransferProgress(
        phase,
        phase_percent,
        min(100, start + round(phase_percent * span / 100)),
        current,
        total,
    )


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

class AssetRepositoryError(RuntimeError):
    pass



class AssetRepository:
    def __init__(
        self,
        runner: GitRunner,
        root: Path,
        credential: GitHubCredential,
        *,
        remote_url: str = REMOTE_URL,
        branch: str = DEFAULT_BRANCH,
        asset_subdir: str | PurePosixPath = ASSET_SUBDIR,
        progress: Callable[[SyncState, str, int | None], None] | None = None,
    ) -> None:
        self.runner = runner
        self.root = Path(root)
        self.credential = credential
        self.remote_url = remote_url
        self.branch = branch
        self.asset_subdir = PurePosixPath(str(asset_subdir).replace("\\", "/"))
        self.progress = progress or (lambda _state, _message, _percent: None)

    @property
    def asset_folder(self) -> Path:
        if self.asset_subdir == PurePosixPath("."):
            return self.root
        return self.root.joinpath(*self.asset_subdir.parts)

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
                tr("Git 저장소가 올바르지 않습니다: {path}", "The Git repository is invalid: {path}", path=self.root),
            )
        expected_remote = self._normalized_remote(self.remote_url)
        remote_commands = (
            ["remote", "get-url", "--all", "origin"],
            ["remote", "get-url", "--push", "--all", "origin"],
        )
        for command in remote_commands:
            result = self._git(command, check=False)
            urls = tuple(
                self._normalized_remote(line)
                for line in result.stdout.splitlines()
                if line.strip()
            )
            if result.returncode != 0 or not urls or any(
                url != expected_remote for url in urls
            ):
                return SyncResult(
                    SyncState.ERROR,
                    tr("origin의 fetch/push URL이 지정된 에셋 저장소와 다릅니다.", "The origin fetch/push URL does not match the selected asset repository."),
                )
        rewrites = self._git(
            ["config", "--local", "--get-regexp", "^url\\..*\\.(insteadof|pushinsteadof)$"],
            check=False,
        )
        if rewrites.returncode == 0 and rewrites.stdout.strip():
            return SyncResult(SyncState.ERROR, tr("로컬 Git URL 재작성 설정은 허용되지 않습니다.", "Local Git URL rewrite settings are not allowed."))
        branch = self._git(["branch", "--show-current"], check=False).stdout.strip()
        if branch != self.branch:
            return SyncResult(
                SyncState.ERROR,
                tr("현재 브랜치가 {expected}이 아닙니다: {actual}", "The current branch is not {expected}: {actual}", expected=self.branch, actual=branch or "detached HEAD"),
            )

    def _require_valid_repository(self) -> None:
        validation = self._validate_repository()
        if validation is not None:
            raise AssetRepositoryError(validation.message)

    def _git_progress_callback(self) -> Callable[[str], None]:
        last_percent = 0

        def report(line: str) -> None:
            nonlocal last_percent
            parsed = parse_git_progress(line)
            if parsed is None:
                return
            last_percent = max(last_percent, parsed.overall_percent)
            if parsed.phase == "receiving objects":
                labels = ("파일을 받는 중", "Receiving files")
            elif parsed.phase == "resolving deltas":
                labels = ("변경사항을 정리하는 중", "Resolving changes")
            else:
                labels = ("파일을 적용하는 중", "Updating files")
            detail = f"{parsed.phase_percent}%"
            if parsed.current is not None and parsed.total is not None:
                detail += f" ({parsed.current}/{parsed.total})"
            self.progress(
                SyncState.DOWNLOADING,
                f"{tr(*labels)} {detail}",
                last_percent,
            )

        return report

    def _fetch_fixed_remote(self) -> None:
        self.runner.run_streaming(
            [
                "fetch",
                "--progress",
                "--prune",
                self.remote_url,
                f"+refs/heads/{self.branch}:refs/remotes/origin/{self.branch}",
            ],
            cwd=self.root,
            credential=self.credential,
            progress=self._git_progress_callback(),
        )

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
                tr("동일한 파일의 원격/로컬 변경이 충돌했습니다.", "Remote and local changes conflict in the same file."),
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

    def head_message(self) -> str:
        return self._git(["show", "-s", "--format=%B", "HEAD"]).stdout.rstrip()

    def prepare(self) -> SyncResult:
        if not self.root.exists():
            self.progress(
                SyncState.DOWNLOADING,
                tr("최초 에셋을 다운로드하는 중...", "Downloading assets for the first time..."),
                0,
            )
            self.root.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.runner.run_streaming(
                    [
                        "clone",
                        "--progress",
                        "--depth",
                        "1",
                        "--single-branch",
                        "--branch",
                        self.branch,
                        self.remote_url,
                        str(self.root),
                    ],
                    credential=self.credential,
                    progress=self._git_progress_callback(),
                )
                self.progress(
                    SyncState.DOWNLOADING,
                    tr("다운로드 완료", "Download complete"),
                    100,
                )
            except GitCommandError as error:
                return SyncResult(SyncState.ERROR, str(error))
        self.progress(
            SyncState.CHECKING,
            tr("원격 변경사항을 확인하는 중...", "Checking remote changes..."),
            None,
        )
        validation = self._validate_repository()
        if validation is not None:
            return validation
        existing_conflicts = self._conflicts()
        if existing_conflicts:
            return SyncResult(
                SyncState.CONFLICT,
                tr("먼저 기존 Git 충돌을 해결하세요.", "Resolve the existing Git conflict first."),
                existing_conflicts,
            )
        try:
            stash_id = self._stash_dirty_tree()
        except GitCommandError as error:
            return self._error_result(error)
        try:
            self._fetch_fixed_remote()
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
                tr("에셋 폴더가 없습니다: {path}", "The asset folder does not exist: {path}", path=self.asset_folder),
            )
        return SyncResult(SyncState.READY, tr("최신 상태", "Up to date"))

    def commit(
        self,
        note: str,
        user: GitHubUser,
        paths: tuple[str, ...] | None = None,
    ) -> str:
        self._require_valid_repository()
        self._git(["config", "user.name", user.login])
        self._git(["config", "user.email", user.commit_email])
        selected_paths = paths or (self.asset_subdir.as_posix(),)
        self._git(["add", "-A", "--", *selected_paths])
        self._git(
            ["commit", "--only", "--allow-empty", "--file", "-", "--", *selected_paths],
            stdin=note,
        )
        return self.head()

    def push(self) -> None:
        self._require_valid_repository()
        self._fetch_fixed_remote()
        if self._count(f"HEAD..origin/{self.branch}") > 0:
            self._git(["rebase", f"origin/{self.branch}"])
        self._require_valid_repository()
        self._git(
            ["push", self.remote_url, f"HEAD:refs/heads/{self.branch}"],
            credential=True,
        )
