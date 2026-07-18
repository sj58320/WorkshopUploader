from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import Mock
from urllib.parse import parse_qs

from github_auth import (
    DeviceCode,
    GitHubApiClient,
    GitHubAuthError,
    GitHubAuthManager,
)
from windows_credentials import GitHubCredential


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, *responses: dict) -> None:
        self.responses = [FakeResponse(response) for response in responses]
        self.requests = []

    def __call__(self, request, timeout=30):
        self.requests.append(request)
        return self.responses.pop(0)

    def form(self, index: int) -> dict[str, str]:
        parsed = parse_qs(self.requests[index].data.decode("utf-8"))
        return {key: values[0] for key, values in parsed.items()}


class MemoryStore:
    def __init__(self, credential: GitHubCredential | None = None) -> None:
        self.credential = credential
        self.deleted = False

    def load(self) -> GitHubCredential | None:
        return self.credential

    def save(self, credential: GitHubCredential) -> None:
        self.credential = credential

    def delete(self) -> None:
        self.credential = None
        self.deleted = True


TOKEN_RESPONSE = {
    "access_token": "ghu_test_access",
    "expires_in": 28800,
    "refresh_token": "ghr_test_refresh",
    "refresh_token_expires_in": 15811200,
    "scope": "",
    "token_type": "bearer",
}


class GitHubApiClientTests(unittest.TestCase):
    def test_device_flow_sends_fixed_repository_id(self) -> None:
        opener = FakeOpener(
            {
                "device_code": "device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }
        )
        api = GitHubApiClient("Iv1.test", opener=opener, clock=lambda: 100.0)

        code = api.start_device_flow()

        self.assertEqual(code.user_code, "ABCD-EFGH")
        self.assertEqual(opener.form(0)["repository_id"], "1157838808")

    def test_poll_waits_for_pending_and_honors_slow_down(self) -> None:
        opener = FakeOpener(
            {"error": "authorization_pending"},
            {"error": "slow_down"},
            TOKEN_RESPONSE,
        )
        sleeps: list[float] = []
        api = GitHubApiClient(
            "Iv1.test",
            opener=opener,
            clock=lambda: 100.0,
            sleeper=sleeps.append,
        )
        code = DeviceCode(
            "device-code",
            "ABCD-EFGH",
            "https://github.com/login/device",
            1000.0,
            5,
        )

        credential = api.poll_device_flow(code, cancelled=lambda: False)

        self.assertEqual(credential.access_token, "ghu_test_access")
        self.assertEqual(sleeps, [5, 10])

    def test_refresh_never_sends_client_secret(self) -> None:
        opener = FakeOpener(TOKEN_RESPONSE)
        api = GitHubApiClient("Iv1.test", opener=opener, clock=lambda: 100.0)
        credential = GitHubCredential(
            "expired",
            90.0,
            "ghr_test_refresh",
            1000.0,
            "asset-user",
            12345,
        )

        refreshed = api.refresh(credential)

        form = opener.form(0)
        self.assertNotIn("client_secret", form)
        self.assertEqual(form["refresh_token"], "ghr_test_refresh")
        self.assertEqual(refreshed.login, "asset-user")

    def test_permission_requires_fixed_repository_and_push(self) -> None:
        opener = FakeOpener(
            {
                "id": 1157838808,
                "full_name": "RevenantZE/RSS-ZE-ASSET",
                "private": True,
                "default_branch": "main",
                "permissions": {
                    "admin": False,
                    "maintain": False,
                    "push": True,
                    "triage": False,
                    "pull": True,
                },
            }
        )
        api = GitHubApiClient("Iv1.test", opener=opener)

        permission = api.get_repo_permission("ghu_test_access")

        self.assertEqual(permission.repository_id, 1157838808)
        self.assertTrue(permission.can_push)


class GitHubAuthManagerTests(unittest.TestCase):
    def test_saved_expiring_token_is_refreshed_and_enriched(self) -> None:
        saved = GitHubCredential(
            "expired",
            110.0,
            "refresh",
            1000.0,
            "old-user",
            1,
        )
        store = MemoryStore(saved)
        api = Mock()
        api.clock.return_value = 100.0
        api.refresh.return_value = replace(saved, access_token="fresh", expires_at=1000.0)
        api.get_user.return_value = Mock(
            login="asset-user", user_id=12345, email=None
        )
        api.get_repo_permission.return_value = Mock(
            repository_id=1157838808, can_push=True
        )
        manager = GitHubAuthManager(api, store)

        session = manager.get_valid_session()

        self.assertEqual(session.credential.access_token, "fresh")
        self.assertEqual(session.user.login, "asset-user")
        self.assertEqual(store.credential.login, "asset-user")

    def test_refresh_failure_deletes_saved_credential(self) -> None:
        saved = GitHubCredential("expired", 100.0, "refresh", 1000.0, "user", 1)
        store = MemoryStore(saved)
        api = Mock()
        api.clock.return_value = 100.0
        api.refresh.side_effect = GitHubAuthError("revoked")
        manager = GitHubAuthManager(api, store)

        self.assertIsNone(manager.get_valid_session())
        self.assertTrue(store.deleted)


if __name__ == "__main__":
    unittest.main()
