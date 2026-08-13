from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


MANIFEST_NAME = "workshop_targets.json"
_PROFILE_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


class WorkshopTargetsError(ValueError):
    pass


def _asset_path(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise WorkshopTargetsError("Target asset_path must be a string")
    text = value.strip().replace("\\", "/")
    if (
        not text
        or text == "."
        or text.startswith("/")
        or text.endswith("/")
        or "//" in text
        or ":" in text
        or any(part in {"", ".", ".."} for part in text.split("/"))
    ):
        raise WorkshopTargetsError("Target asset_path must be a safe relative path")
    return PurePosixPath(text)


@dataclass(frozen=True)
class WorkshopTarget:
    profile: str
    workshop_id: int
    title: str
    asset_path: PurePosixPath

    def folder(self, repository_root: Path) -> Path:
        return Path(repository_root).joinpath(*self.asset_path.parts)


class WorkshopTargets:
    def __init__(self, path: Path, payload: dict, targets: dict[str, WorkshopTarget]):
        self.path = Path(path)
        self.payload = payload
        self.targets = targets

    @classmethod
    def load(cls, repository_root: Path) -> "WorkshopTargets":
        path = Path(repository_root) / MANIFEST_NAME
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise WorkshopTargetsError(f"Workshop target manifest was not found: {path}") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise WorkshopTargetsError(f"Could not read Workshop target manifest: {path}") from error
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise WorkshopTargetsError("Workshop target manifest version must be 1")
        raw_targets = payload.get("targets")
        if not isinstance(raw_targets, dict) or not raw_targets:
            raise WorkshopTargetsError("Workshop target manifest must contain targets")
        targets: dict[str, WorkshopTarget] = {}
        for profile, value in raw_targets.items():
            if not isinstance(profile, str) or not _PROFILE_PATTERN.fullmatch(profile):
                raise WorkshopTargetsError(f"Invalid Workshop profile name: {profile!r}")
            if not isinstance(value, dict):
                raise WorkshopTargetsError(f"Workshop profile {profile!r} must be an object")
            workshop_id = value.get("workshop_id")
            title = value.get("title")
            if (
                not isinstance(workshop_id, int)
                or isinstance(workshop_id, bool)
                or workshop_id < 0
            ):
                raise WorkshopTargetsError(f"Workshop profile {profile!r} has an invalid workshop_id")
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 128:
                raise WorkshopTargetsError(f"Workshop profile {profile!r} has an invalid title")
            targets[profile] = WorkshopTarget(
                profile=profile,
                workshop_id=workshop_id,
                title=title.strip(),
                asset_path=_asset_path(value.get("asset_path")),
            )
        return cls(path, payload, targets)

    def get(self, profile: str) -> WorkshopTarget:
        try:
            return self.targets[profile]
        except KeyError as error:
            choices = ", ".join(sorted(self.targets))
            raise WorkshopTargetsError(
                f"Unknown Workshop profile {profile!r}; choose one of: {choices}"
            ) from error

    def set_workshop_id(self, profile: str, workshop_id: int) -> None:
        if not isinstance(workshop_id, int) or isinstance(workshop_id, bool) or workshop_id <= 0:
            raise WorkshopTargetsError("A positive Workshop ID is required")
        target = self.get(profile)
        if target.workshop_id not in {0, workshop_id}:
            raise WorkshopTargetsError(
                f"Workshop profile {profile!r} is already assigned to {target.workshop_id}"
            )
        if target.workshop_id == workshop_id:
            return
        self.payload["targets"][profile]["workshop_id"] = workshop_id
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(self.payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, self.path)
        self.targets[profile] = WorkshopTarget(
            profile=target.profile,
            workshop_id=workshop_id,
            title=target.title,
            asset_path=target.asset_path,
        )
