from __future__ import annotations

import ctypes
import json
import os
from dataclasses import asdict, dataclass
from typing import Protocol

from ctypes import wintypes


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
DEFAULT_TARGET_NAME = "WorkshopUploader:GitHub"


@dataclass(frozen=True)
class GitHubCredential:
    access_token: str
    expires_at: float | None
    refresh_token: str | None
    refresh_expires_at: float | None
    login: str
    user_id: int


class CredentialStoreError(RuntimeError):
    pass


class CredentialApi(Protocol):
    def write_generic(self, target_name: str, blob: bytes) -> None: ...

    def read_generic(self, target_name: str) -> bytes | None: ...

    def delete_generic(self, target_name: str) -> None: ...


class _CREDENTIALW(ctypes.Structure):
    _fields_ = (
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    )


class CtypesCredentialApi:
    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialStoreError("Windows Credential Manager is unavailable")
        self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        credential_pointer = ctypes.POINTER(_CREDENTIALW)
        self._advapi32.CredWriteW.argtypes = (
            ctypes.POINTER(_CREDENTIALW),
            wintypes.DWORD,
        )
        self._advapi32.CredWriteW.restype = wintypes.BOOL
        self._advapi32.CredReadW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(credential_pointer),
        )
        self._advapi32.CredReadW.restype = wintypes.BOOL
        self._advapi32.CredDeleteW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._advapi32.CredDeleteW.restype = wintypes.BOOL
        self._advapi32.CredFree.argtypes = (wintypes.LPVOID,)
        self._advapi32.CredFree.restype = None

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        error_number = ctypes.get_last_error()
        raise CredentialStoreError(
            f"Windows Credential Manager {operation} failed ({error_number})"
        )

    def write_generic(self, target_name: str, blob: bytes) -> None:
        blob_buffer = ctypes.create_string_buffer(blob)
        credential = _CREDENTIALW()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target_name
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(
            blob_buffer, ctypes.POINTER(wintypes.BYTE)
        )
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "GitHub"
        if not self._advapi32.CredWriteW(ctypes.byref(credential), 0):
            self._raise_last_error("write")

    def read_generic(self, target_name: str) -> bytes | None:
        credential_pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self._advapi32.CredReadW(
            target_name,
            CRED_TYPE_GENERIC,
            0,
            ctypes.byref(credential_pointer),
        ):
            if ctypes.get_last_error() == ERROR_NOT_FOUND:
                return None
            self._raise_last_error("read")
        try:
            credential = credential_pointer.contents
            return ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
        finally:
            self._advapi32.CredFree(credential_pointer)

    def delete_generic(self, target_name: str) -> None:
        if self._advapi32.CredDeleteW(target_name, CRED_TYPE_GENERIC, 0):
            return
        if ctypes.get_last_error() != ERROR_NOT_FOUND:
            self._raise_last_error("delete")


class WindowsCredentialStore:
    def __init__(
        self,
        target_name: str = DEFAULT_TARGET_NAME,
        *,
        native: CredentialApi | None = None,
    ) -> None:
        self.target_name = target_name
        self._native = native or CtypesCredentialApi()

    def save(self, credential: GitHubCredential) -> None:
        blob = json.dumps(
            asdict(credential),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._native.write_generic(self.target_name, blob)

    def load(self) -> GitHubCredential | None:
        blob = self._native.read_generic(self.target_name)
        if blob is None:
            return None
        try:
            data = json.loads(blob.decode("utf-8"))
            return GitHubCredential(**data)
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise CredentialStoreError("Saved GitHub credential is invalid") from error

    def delete(self) -> None:
        self._native.delete_generic(self.target_name)
