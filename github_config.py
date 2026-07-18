from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


REPOSITORY_ID = 1157838808
CLIENT_ID_ENVIRONMENT = "WORKSHOP_UPLOADER_GITHUB_CLIENT_ID"


@dataclass(frozen=True)
class GitHubAppConfig:
    client_id: str
    repository_id: int


class GitHubConfigError(RuntimeError):
    pass


def load_github_app_config(
    bundle_path: Path,
    environ: Mapping[str, str] = os.environ,
) -> GitHubAppConfig:
    config_path = Path(bundle_path) / "github_app.json"
    if config_path.is_file():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            client_id = str(payload.get("client_id", "")).strip()
            repository_id = int(payload.get("repository_id", 0))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise GitHubConfigError("GitHub App 설정 파일이 올바르지 않습니다.") from error
    else:
        client_id = environ.get(CLIENT_ID_ENVIRONMENT, "").strip()
        repository_id = REPOSITORY_ID
    if not client_id or repository_id != REPOSITORY_ID:
        raise GitHubConfigError(
            "GitHub App client ID가 없거나 저장소 ID가 올바르지 않습니다."
        )
    return GitHubAppConfig(client_id, repository_id)
