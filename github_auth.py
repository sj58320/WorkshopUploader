from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable

from repository_target import RepositoryTarget
from windows_credentials import CredentialStore, GitHubCredential


DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_ROOT = "https://api.github.com"
TOKEN_REFRESH_MARGIN_SECONDS = 120.0
TOKEN_PATTERN = re.compile(r"gh[a-z]_[A-Za-z0-9_]+", re.IGNORECASE)


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_at: float
    interval: int


@dataclass(frozen=True)
class GitHubUser:
    login: str
    user_id: int
    email: str | None

    @property
    def commit_email(self) -> str:
        return self.email or f"{self.user_id}+{self.login}@users.noreply.github.com"


@dataclass(frozen=True)
class RepoPermission:
    repository_id: int
    full_name: str
    default_branch: str
    can_push: bool


@dataclass(frozen=True)
class GitHubSession:
    credential: GitHubCredential
    user: GitHubUser


class GitHubAuthError(RuntimeError):
    pass


def _safe_message(value: object) -> str:
    return TOKEN_PATTERN.sub("[REDACTED]", str(value))


class GitHubApiClient:
    def __init__(
        self,
        client_id: str,
        *,
        opener: Callable = urllib.request.urlopen,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        client_id = client_id.strip()
        if not client_id:
            raise GitHubAuthError("GitHub App client ID is missing")
        self.client_id = client_id
        self._opener = opener
        self.clock = clock
        self._sleeper = sleeper

    def _request_json(
        self,
        url: str,
        *,
        form: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict:
        headers = {
            "Accept": "application/json",
            "User-Agent": "WorkshopUploader",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with self._opener(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="replace")
            except OSError:
                detail = str(error.reason)
            raise GitHubAuthError(
                _safe_message(f"GitHub HTTP {error.code}: {detail}")
            ) from error
        except (urllib.error.URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise GitHubAuthError(_safe_message(f"GitHub request failed: {error}")) from error
        if not isinstance(payload, dict):
            raise GitHubAuthError("GitHub returned an invalid response")
        return payload

    def start_device_flow(self) -> DeviceCode:
        payload = self._request_json(
            DEVICE_CODE_URL,
            form={
                "client_id": self.client_id,
            },
        )
        try:
            return DeviceCode(
                device_code=str(payload["device_code"]),
                user_code=str(payload["user_code"]),
                verification_uri=str(payload["verification_uri"]),
                expires_at=self.clock() + int(payload["expires_in"]),
                interval=max(1, int(payload["interval"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GitHubAuthError("GitHub returned an invalid device code") from error

    def _credential_from_token(
        self,
        payload: dict,
        *,
        login: str = "",
        user_id: int = 0,
    ) -> GitHubCredential:
        try:
            access_token = str(payload["access_token"])
            expires_in = payload.get("expires_in")
            refresh_token = payload.get("refresh_token")
            refresh_expires_in = payload.get("refresh_token_expires_in")
            return GitHubCredential(
                access_token=access_token,
                expires_at=(
                    self.clock() + float(expires_in)
                    if expires_in is not None
                    else None
                ),
                refresh_token=(
                    str(refresh_token) if refresh_token is not None else None
                ),
                refresh_expires_at=(
                    self.clock() + float(refresh_expires_in)
                    if refresh_expires_in is not None
                    else None
                ),
                login=login,
                user_id=user_id,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GitHubAuthError("GitHub returned an invalid access token") from error

    def poll_device_flow(
        self,
        code: DeviceCode,
        cancelled: Callable[[], bool],
    ) -> GitHubCredential:
        interval = code.interval
        while self.clock() < code.expires_at and not cancelled():
            payload = self._request_json(
                TOKEN_URL,
                form={
                    "client_id": self.client_id,
                    "device_code": code.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            error_name = payload.get("error")
            if error_name == "authorization_pending":
                self._sleeper(interval)
                continue
            if error_name == "slow_down":
                interval += 5
                self._sleeper(interval)
                continue
            if error_name is not None:
                raise GitHubAuthError(
                    _safe_message(
                        payload.get("error_description") or str(error_name)
                    )
                )
            return self._credential_from_token(payload)
        raise GitHubAuthError("GitHub login expired or was cancelled")

    def refresh(self, credential: GitHubCredential) -> GitHubCredential:
        if not credential.refresh_token:
            raise GitHubAuthError("GitHub refresh token is unavailable")
        payload = self._request_json(
            TOKEN_URL,
            form={
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": credential.refresh_token,
            },
        )
        if "error" in payload:
            raise GitHubAuthError(
                _safe_message(payload.get("error_description") or payload["error"])
            )
        return self._credential_from_token(
            payload,
            login=credential.login,
            user_id=credential.user_id,
        )

    def get_user(self, token: str) -> GitHubUser:
        payload = self._request_json(f"{API_ROOT}/user", token=token)
        try:
            email = payload.get("email")
            return GitHubUser(
                login=str(payload["login"]),
                user_id=int(payload["id"]),
                email=str(email) if email else None,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GitHubAuthError("GitHub returned an invalid user profile") from error

    def get_repo_permission(
        self,
        token: str,
        target: RepositoryTarget,
    ) -> RepoPermission:
        payload = self._request_json(
            f"{API_ROOT}/repos/{target.api_path}",
            token=token,
        )
        try:
            repository_id = int(payload["id"])
            full_name = str(payload["full_name"])
            default_branch = str(payload["default_branch"])
            permissions = payload["permissions"]
            can_push = permissions.get("push") is True
        except (KeyError, TypeError, ValueError) as error:
            raise GitHubAuthError("GitHub returned invalid repository permissions") from error
        if full_name.casefold() != target.full_name.casefold():
            raise GitHubAuthError("GitHub returned an unexpected repository")
        return RepoPermission(repository_id, full_name, default_branch, can_push)


class GitHubAuthManager:
    def __init__(self, api: GitHubApiClient, store: CredentialStore) -> None:
        self.api = api
        self.store = store

    def _enrich(
        self,
        credential: GitHubCredential,
        target: RepositoryTarget,
    ) -> GitHubSession:
        user = self.api.get_user(credential.access_token)
        try:
            permission = self.api.get_repo_permission(
                credential.access_token,
                target,
            )
        except GitHubAuthError as error:
            raise GitHubAuthError(
                f"Cannot access {target.full_name}. Install the GitHub App on that "
                "repository and grant this account access."
            ) from error
        if not permission.can_push:
            raise GitHubAuthError(
                f"The signed-in account cannot push to {target.full_name}"
            )
        enriched = replace(
            credential,
            login=user.login,
            user_id=user.user_id,
        )
        self.store.save(enriched)
        return GitHubSession(enriched, user)

    def login(
        self,
        target: RepositoryTarget,
        on_code: Callable[[DeviceCode], None],
        cancelled: Callable[[], bool],
    ) -> GitHubSession:
        code = self.api.start_device_flow()
        on_code(code)
        credential = self.api.poll_device_flow(code, cancelled)
        return self._enrich(credential, target)

    def get_valid_session(
        self,
        target: RepositoryTarget,
    ) -> GitHubSession | None:
        credential = self.store.load()
        if credential is None:
            return None
        if (
            credential.expires_at is not None
            and credential.expires_at <= self.api.clock() + TOKEN_REFRESH_MARGIN_SECONDS
        ):
            try:
                credential = self.api.refresh(credential)
            except GitHubAuthError:
                self.store.delete()
                return None
        return self._enrich(credential, target)

    def validate_session(
        self,
        session: GitHubSession,
        target: RepositoryTarget,
    ) -> GitHubSession:
        return self._enrich(session.credential, target)

    def logout(self) -> None:
        self.store.delete()
