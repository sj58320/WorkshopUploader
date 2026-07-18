from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse


DEFAULT_REPOSITORY = "RevenantZE/RSS-ZE-ASSET"
DEFAULT_BRANCH = "main"
DEFAULT_ASSET_SUBDIR = "in/additional_files"

_OWNER_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z"
)
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")
_INVALID_BRANCH_CHARACTER = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


class RepositoryTargetError(ValueError):
    pass


def _parse_repository(value: str) -> tuple[str, str]:
    value = value.strip()
    if not value:
        raise RepositoryTargetError("GitHub repository is required (owner/repo)")
    if "://" in value:
        parsed = urlparse(value)
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname not in {"github.com", "www.github.com"}
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.params
        ):
            raise RepositoryTargetError("Only https://github.com/owner/repo is allowed")
        value = parsed.path.strip("/")
    elif "\\" in value or value.startswith(("git@", "ssh:")):
        raise RepositoryTargetError("Use owner/repo or an HTTPS GitHub URL")
    value = value.rstrip("/")
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise RepositoryTargetError("GitHub repository must be owner/repo")
    owner, repository = parts
    if repository.lower().endswith(".git"):
        repository = repository[:-4]
    if not _OWNER_PATTERN.fullmatch(owner):
        raise RepositoryTargetError("GitHub owner name is invalid")
    if not _REPOSITORY_PATTERN.fullmatch(repository) or repository in {".", ".."}:
        raise RepositoryTargetError("GitHub repository name is invalid")
    return owner, repository


def _validate_branch(value: str) -> str:
    branch = value.strip()
    components = branch.split("/")
    if (
        not branch
        or branch == "@"
        or branch.startswith(("-", "/"))
        or branch.endswith(("/", "."))
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or _INVALID_BRANCH_CHARACTER.search(branch)
        or any(
            not component
            or component.startswith(".")
            or component.lower().endswith(".lock")
            for component in components
        )
    ):
        raise RepositoryTargetError("GitHub branch name is invalid")
    return branch


def _validate_asset_subdir(value: str) -> PurePosixPath:
    text = value.strip().replace("\\", "/")
    if not text:
        raise RepositoryTargetError("Asset path is required; use . for repository root")
    if text == ".":
        return PurePosixPath(".")
    if (
        text.startswith("/")
        or text.endswith("/")
        or "//" in text
        or ":" in text
        or any(ord(character) < 32 for character in text)
    ):
        raise RepositoryTargetError("Asset path must be a relative repository path")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RepositoryTargetError("Asset path cannot contain . or .. components")
    return PurePosixPath(*parts)


@dataclass(frozen=True)
class RepositoryTarget:
    owner: str
    repository: str
    branch: str
    asset_subdir: PurePosixPath

    @classmethod
    def parse(
        cls,
        repository: str,
        branch: str,
        asset_subdir: str,
    ) -> "RepositoryTarget":
        owner, name = _parse_repository(repository)
        return cls(
            owner=owner,
            repository=name,
            branch=_validate_branch(branch),
            asset_subdir=_validate_asset_subdir(asset_subdir),
        )

    @classmethod
    def defaults(cls) -> "RepositoryTarget":
        return cls.parse(DEFAULT_REPOSITORY, DEFAULT_BRANCH, DEFAULT_ASSET_SUBDIR)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repository}"

    @property
    def remote_url(self) -> str:
        return f"https://github.com/{self.full_name}.git"

    @property
    def api_path(self) -> str:
        return "/".join(quote(part, safe="") for part in (self.owner, self.repository))

    @property
    def asset_subdir_text(self) -> str:
        return self.asset_subdir.as_posix()

    def clone_root(self, runtime_path: Path) -> Path:
        runtime_path = Path(runtime_path)
        if (
            self.full_name.casefold() == DEFAULT_REPOSITORY.casefold()
            and self.branch == DEFAULT_BRANCH
        ):
            return runtime_path / "repos" / "RSS-ZE-ASSET"
        identity = f"{self.full_name.casefold()}\n{self.branch}".encode("utf-8")
        suffix = hashlib.sha256(identity).hexdigest()[:12]
        slug = re.sub(
            r"[^A-Za-z0-9._-]+",
            "-",
            f"{self.owner}-{self.repository}",
        ).strip(".-")[:80] or "repository"
        return runtime_path / "repos" / f"{slug}-{suffix}"

    def asset_folder(self, repository_root: Path) -> Path:
        root = Path(repository_root)
        if self.asset_subdir == PurePosixPath("."):
            return root
        return root.joinpath(*self.asset_subdir.parts)
