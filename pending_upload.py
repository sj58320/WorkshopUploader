from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from localization import tr

from repository_target import (
    DEFAULT_ASSET_SUBDIR,
    DEFAULT_BRANCH,
    DEFAULT_REPOSITORY,
    RepositoryTarget,
)


PENDING_SCHEMA_VERSION = 4


class PendingPhase(str, Enum):
    STEAM_STARTED = "steam_started"
    STEAM_SUCCEEDED = "steam_succeeded"


@dataclass(frozen=True)
class PendingUpload:
    schema_version: int
    phase: PendingPhase
    workshop_id: int
    note: str
    steam_succeeded_at: str | None
    base_commit: str
    commit_id: str | None
    github_repository: str = DEFAULT_REPOSITORY
    github_branch: str = DEFAULT_BRANCH
    github_asset_subdir: str = DEFAULT_ASSET_SUBDIR
    workshop_profile: str | None = None

    @property
    def target(self) -> RepositoryTarget:
        return RepositoryTarget.parse(
            self.github_repository,
            self.github_branch,
            self.github_asset_subdir,
        )


class PendingUploadError(RuntimeError):
    pass


class PendingUploadStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, pending: PendingUpload) -> None:
        self._validate(pending)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(asdict(pending), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, self.path)

    def load(self) -> PendingUpload | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("Pending upload payload must be an object")
            if payload.get("schema_version") == 2:
                default_target = RepositoryTarget.defaults()
                payload.update(
                    schema_version=PENDING_SCHEMA_VERSION,
                    github_repository=default_target.full_name,
                    github_branch=default_target.branch,
                    github_asset_subdir=default_target.asset_subdir_text,
                    workshop_profile=None,
                )
            elif payload.get("schema_version") == 3:
                payload.update(
                    schema_version=PENDING_SCHEMA_VERSION,
                    workshop_profile=None,
                )
            elif payload.get("schema_version") == PENDING_SCHEMA_VERSION:
                required_target_fields = (
                    "github_repository",
                    "github_branch",
                    "github_asset_subdir",
                )
                if any(field not in payload for field in required_target_fields):
                    raise ValueError("Pending upload target is missing")
            payload["phase"] = PendingPhase(payload.get("phase"))
            pending = PendingUpload(**payload)
            self._validate(pending)
            return pending
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
            raise PendingUploadError(tr("GitHub push 복구 정보가 손상되었습니다.", "The GitHub push recovery data is corrupted.")) from error

    @staticmethod
    def _validate(pending: PendingUpload) -> None:
        common_valid = (
            pending.schema_version == PENDING_SCHEMA_VERSION
            and isinstance(pending.phase, PendingPhase)
            and isinstance(pending.workshop_id, int)
            and isinstance(pending.note, str)
            and bool(pending.note)
            and isinstance(pending.base_commit, str)
            and bool(pending.base_commit)
            and (
                pending.commit_id is None
                or (isinstance(pending.commit_id, str) and bool(pending.commit_id))
            )
        )
        if pending.phase is PendingPhase.STEAM_STARTED:
            phase_valid = (
                pending.workshop_id >= 0
                and pending.steam_succeeded_at is None
                and pending.commit_id is None
            )
        else:
            phase_valid = (
                pending.workshop_id > 0
                and isinstance(pending.steam_succeeded_at, str)
                and bool(pending.steam_succeeded_at)
            )
        try:
            pending.target
            target_valid = (
                pending.workshop_profile is None
                or (
                    isinstance(pending.workshop_profile, str)
                    and bool(pending.workshop_profile)
                )
            )
        except ValueError:
            target_valid = False
        valid = common_valid and phase_valid and target_valid
        if not valid:
            raise PendingUploadError(tr("GitHub push 복구 정보가 올바르지 않습니다.", "The GitHub push recovery data is invalid."))

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
