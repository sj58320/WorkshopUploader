from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Mapping


class AssetSourceMode(str, Enum):
    LOCAL = "local"
    GITHUB = "github"


PERSISTED_KEYS = (
    "workshop_id",
    "workshop_title",
    "workshop_description",
    "chunk_size_mb",
    "asset_folder",
    "output_folder",
    "preview_path",
    "asset_source_mode",
)


def load_settings(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key in PERSISTED_KEYS
        if isinstance((value := data.get(key)), str)
    }


def save_settings(path: Path, values: Mapping[str, str]) -> None:
    payload = {
        key: value
        for key in PERSISTED_KEYS
        if isinstance((value := values.get(key)), str)
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def selected_mode(values: Mapping[str, str]) -> AssetSourceMode | None:
    try:
        return AssetSourceMode(values.get("asset_source_mode", ""))
    except ValueError:
        return None


def can_change_mode(pending_exists: bool) -> bool:
    return not pending_exists
