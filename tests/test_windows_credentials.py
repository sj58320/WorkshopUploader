from __future__ import annotations

import unittest

from windows_credentials import (
    CredentialStoreError,
    GitHubCredential,
    WindowsCredentialStore,
)


CREDENTIAL = GitHubCredential(
    access_token="ghu_test_access",
    expires_at=2_000_000_000.0,
    refresh_token="ghr_test_refresh",
    refresh_expires_at=2_100_000_000.0,
    login="asset-user",
    user_id=12345,
)


class FakeCredentialApi:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.last_target = ""

    def write_generic(self, target_name: str, blob: bytes) -> None:
        self.last_target = target_name
        self.values[target_name] = blob

    def read_generic(self, target_name: str) -> bytes | None:
        return self.values.get(target_name)

    def delete_generic(self, target_name: str) -> None:
        self.values.pop(target_name, None)


class WindowsCredentialStoreTests(unittest.TestCase):
    def test_round_trip_uses_fixed_target_and_utf8_json(self) -> None:
        native = FakeCredentialApi()
        store = WindowsCredentialStore(native=native)

        store.save(CREDENTIAL)

        self.assertEqual(native.last_target, "WorkshopUploader:GitHub")
        self.assertNotIn(CREDENTIAL.access_token, native.last_target)
        self.assertEqual(store.load(), CREDENTIAL)

    def test_missing_credential_returns_none(self) -> None:
        store = WindowsCredentialStore(native=FakeCredentialApi())

        self.assertIsNone(store.load())

    def test_delete_is_idempotent(self) -> None:
        store = WindowsCredentialStore(native=FakeCredentialApi())

        store.delete()
        store.delete()

    def test_corrupt_credential_is_reported_without_token_echo(self) -> None:
        native = FakeCredentialApi()
        native.values["WorkshopUploader:GitHub"] = b"not-json-ghu_hidden"
        store = WindowsCredentialStore(native=native)

        with self.assertRaises(CredentialStoreError) as raised:
            store.load()

        self.assertNotIn("ghu_hidden", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
