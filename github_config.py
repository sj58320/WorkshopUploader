from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


CLIENT_ID_ENVIRONMENT = "WORKSHOP_UPLOADER_GITHUB_CLIENT_ID"


@dataclass(frozen=True)
class GitHubAppConfig:
    client_id: str


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
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise GitHubConfigError("GitHub App 설정 파일이 올바르지 않습니다.") from error
    else:
        client_id = environ.get(CLIENT_ID_ENVIRONMENT, "").strip()
    if not client_id:
        raise GitHubConfigError("GitHub App client ID is missing")
    return GitHubAppConfig(client_id)
