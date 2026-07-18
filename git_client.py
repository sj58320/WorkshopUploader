from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from windows_credentials import GitHubCredential


TOKEN_PATTERN = re.compile(r"gh[a-z]_[A-Za-z0-9_]+", re.IGNORECASE)
ASKPASS_FILENAME = "git-askpass.cmd"
ASKPASS_CONTENT = """@echo off
set "prompt=%~1"
echo %prompt% | "%SystemRoot%\\System32\\findstr.exe" /I "username" >nul
if not errorlevel 1 (
  echo %WORKSHOP_UPLOADER_GIT_USERNAME%
) else (
  echo %WORKSHOP_UPLOADER_GIT_PASSWORD%
)
"""


@dataclass(frozen=True)
class GitResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    safe_command: str


class GitCommandError(RuntimeError):
    def __init__(self, result: GitResult) -> None:
        self.result = result
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        super().__init__(f"Git command failed ({result.returncode}): {detail}")


def _redact(value: str, credential: GitHubCredential | None) -> str:
    redacted = value
    if credential is not None and credential.access_token:
        redacted = redacted.replace(credential.access_token, "[REDACTED]")
    return TOKEN_PATTERN.sub("[REDACTED]", redacted)


def ensure_askpass(runtime_path: Path) -> Path:
    runtime_path.mkdir(parents=True, exist_ok=True)
    askpass_path = runtime_path / ASKPASS_FILENAME
    if askpass_path.is_file() and askpass_path.read_text(encoding="utf-8") == ASKPASS_CONTENT:
        return askpass_path
    temporary_path = askpass_path.with_suffix(askpass_path.suffix + ".tmp")
    temporary_path.write_text(ASKPASS_CONTENT, encoding="utf-8")
    os.replace(temporary_path, askpass_path)
    return askpass_path


class GitRunner:
    def __init__(
        self,
        git_exe: Path,
        askpass_path: Path,
        *,
        process_factory: Callable = subprocess.run,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        self.git_exe = Path(git_exe)
        self.askpass_path = Path(askpass_path)
        self._process_factory = process_factory
        self._base_env = dict(os.environ if base_env is None else base_env)

    def run(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        credential: GitHubCredential | None = None,
        stdin: str | None = None,
        check: bool = True,
    ) -> GitResult:
        string_args = tuple(str(argument) for argument in args)
        command = [str(self.git_exe), *string_args]
        environment = dict(self._base_env)
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "credential.helper",
                "GIT_CONFIG_VALUE_0": "",
            }
        )
        if credential is not None:
            environment.update(
                {
                    "GIT_ASKPASS": str(self.askpass_path),
                    "GIT_ASKPASS_REQUIRE": "force",
                    "WORKSHOP_UPLOADER_GIT_USERNAME": (
                        credential.login or "x-access-token"
                    ),
                    "WORKSHOP_UPLOADER_GIT_PASSWORD": credential.access_token,
                }
            )
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        completed = self._process_factory(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env=environment,
            input=stdin,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creation_flags,
            check=False,
        )
        stdout = _redact(completed.stdout or "", credential)
        stderr = _redact(completed.stderr or "", credential)
        safe_command = _redact(subprocess.list2cmdline(command), credential)
        result = GitResult(
            string_args,
            completed.returncode,
            stdout,
            stderr,
            safe_command,
        )
        if check and completed.returncode != 0:
            raise GitCommandError(result)
        return result
