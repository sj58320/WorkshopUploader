from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


PENDING_SCHEMA_VERSION = 2


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
            raise PendingUploadError("GitHub push 복구 정보가 손상되었습니다.") from error

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
        valid = common_valid and phase_valid
        if not valid:
            raise PendingUploadError("GitHub push 복구 정보가 올바르지 않습니다.")

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
