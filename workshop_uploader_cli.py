from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from typing import Sequence

import asset_upload
from app_settings import AssetSourceMode
from asset_sync import AssetRepository, SyncState
from git_client import GitRunner, ensure_askpass
from github_auth import GitHubApiClient, GitHubAuthManager, GitHubSession
from github_config import GitHubConfigError, load_github_app_config
from localization import set_language, tr
from pending_upload import PendingUploadStore
from repository_target import (
    DEFAULT_ASSET_SUBDIR,
    DEFAULT_BRANCH,
    DEFAULT_REPOSITORY,
    RepositoryTarget,
)
from upload_workflow import UploadOptions, UploadWorkflow
from windows_credentials import WindowsCredentialStore


PENDING_PATH = asset_upload.RUNTIME_PATH / "pending_upload.json"
DEFAULT_OUTPUT_FOLDER = asset_upload.RUNTIME_PATH / "output"


def _add_repository_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPOSITORY,
        help="GitHub repository in owner/repo or HTTPS URL form",
    )
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument(
        "--asset-path",
        default=DEFAULT_ASSET_SUBDIR,
        help="Asset folder relative to the repository root; use . for the root",
    )


def _add_upload_arguments(parser: argparse.ArgumentParser) -> None:
    _add_repository_arguments(parser)
    parser.add_argument(
        "--local-folder",
        type=Path,
        help="Use a local asset folder instead of GitHub sync",
    )
    parser.add_argument("--addon-id", type=int)
    parser.add_argument(
        "--profile",
        help="Workshop profile from workshop_targets.json",
    )
    parser.add_argument("--chunk-size-mb", type=int, default=100)
    parser.add_argument("--output-folder", type=Path, default=DEFAULT_OUTPUT_FOLDER)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--title")
    parser.add_argument("--description")
    parser.add_argument(
        "--note",
        default="",
        help="Steam update note and Git commit message; defaults to Update asset",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workshop-uploader",
        description=(
            "Sync CS2 assets, build VPK files, upload to Steam Workshop, "
            "and push GitHub changes."
        ),
    )
    parser.add_argument(
        "--language",
        choices=("ko", "en"),
        default="en",
        help="CLI message language (default: en)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write the final result as one JSON object",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the GitHub device login URL without opening a browser",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sync_parser = commands.add_parser(
        "sync", help="Authenticate and synchronize a GitHub asset repository"
    )
    _add_repository_arguments(sync_parser)

    build_parser_command = commands.add_parser(
        "build", help="Synchronize assets when needed and build VPK files only"
    )
    _add_upload_arguments(build_parser_command)

    upload_parser = commands.add_parser(
        "upload",
        help="Build VPK files, upload to Steam, then commit and push GitHub changes",
    )
    _add_upload_arguments(upload_parser)

    commands.add_parser(
        "retry-push",
        help="Retry only the pending GitHub commit/push after a Steam upload",
    )
    commands.add_parser("logout", help="Remove the saved GitHub credential")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


class CliApplication:
    def __init__(self, *, json_output: bool, no_browser: bool) -> None:
        self.json_output = json_output
        self.no_browser = no_browser
        self.pending_store = PendingUploadStore(PENDING_PATH)
        self.auth_manager: GitHubAuthManager | None = None
        self.repository_factory = None
        self.workflow = UploadWorkflow(
            asset_upload.auto_update,
            None,
            self.pending_store,
        )

    def _write_progress(
        self,
        state: SyncState,
        message: str,
        percent: int | None,
    ) -> None:
        suffix = f" [{percent}%]" if percent is not None else ""
        print(f"[{state.value}] {message}{suffix}", file=sys.stderr, flush=True)

    @staticmethod
    def _git_executable() -> Path:
        if getattr(sys, "frozen", False):
            return asset_upload.BUNDLE_PATH / "mingit" / "cmd" / "git.exe"
        return asset_upload.SOURCE_PATH / ".vendor" / "MinGit" / "cmd" / "git.exe"

    @staticmethod
    def _github_config_root() -> Path:
        if getattr(sys, "frozen", False):
            return asset_upload.BUNDLE_PATH
        vendor_root = asset_upload.SOURCE_PATH / ".vendor"
        if (vendor_root / "github_app.json").is_file():
            return vendor_root
        return asset_upload.BUNDLE_PATH

    def _ensure_github_services(self) -> None:
        if self.auth_manager is not None and self.repository_factory is not None:
            return
        config = load_github_app_config(self._github_config_root())
        git_exe = self._git_executable()
        if not git_exe.is_file():
            raise GitHubConfigError(
                tr(
                    "번들 MinGit을 찾을 수 없습니다: {path}",
                    "Bundled MinGit was not found: {path}",
                    path=git_exe,
                )
            )
        runner = GitRunner(git_exe, ensure_askpass(asset_upload.RUNTIME_PATH))

        def repository_factory(
            session: GitHubSession,
            target: RepositoryTarget,
        ) -> AssetRepository:
            return AssetRepository(
                runner,
                target.clone_root(asset_upload.RUNTIME_PATH),
                session.credential,
                remote_url=target.remote_url,
                branch=target.branch,
                asset_subdir=target.asset_subdir,
                progress=self._write_progress,
            )

        self.repository_factory = repository_factory
        self.auth_manager = GitHubAuthManager(
            GitHubApiClient(config.client_id),
            WindowsCredentialStore(),
        )
        self.workflow = UploadWorkflow(
            asset_upload.auto_update,
            repository_factory,
            self.pending_store,
        )

    def _show_device_code(self, code) -> None:
        print(
            tr(
                "GitHub 로그인: {url} / 코드: {code}",
                "GitHub login: {url} / code: {code}",
                url=code.verification_uri,
                code=code.user_code,
            ),
            file=sys.stderr,
            flush=True,
        )
        if not self.no_browser:
            webbrowser.open(code.verification_uri)

    def _session(self, target: RepositoryTarget) -> GitHubSession:
        self._ensure_github_services()
        session = self.auth_manager.get_valid_session(target)
        if session is not None:
            return session
        return self.auth_manager.login(
            target,
            self._show_device_code,
            lambda: False,
        )

    @staticmethod
    def _target(args: argparse.Namespace) -> RepositoryTarget:
        asset_path = "." if getattr(args, "profile", None) else args.asset_path
        return RepositoryTarget.parse(args.repo, args.branch, asset_path)

    @staticmethod
    def _options(
        args: argparse.Namespace,
        target: RepositoryTarget | None,
    ) -> UploadOptions:
        local_folder = (
            args.local_folder.expanduser().resolve()
            if args.local_folder is not None
            else Path()
        )
        return UploadOptions(
            workshop_id=args.addon_id,
            chunk_size_mb=args.chunk_size_mb,
            title=args.title,
            description=args.description,
            local_asset_folder=local_folder,
            output_folder=args.output_folder.expanduser().resolve(),
            preview_path=(
                args.preview.expanduser().resolve()
                if args.preview is not None
                else None
            ),
            github_target=target,
            workshop_profile=args.profile,
        )

    def _sync(self, args: argparse.Namespace) -> dict[str, object]:
        target = self._target(args)
        session = self._session(target)
        result = self.workflow.prepare_github(session, target)
        return {
            "ok": True,
            "command": "sync",
            "message": result.message,
            "repository": target.full_name,
            "branch": target.branch,
            "asset_path": target.asset_subdir_text,
            "local_path": str(target.asset_folder(target.clone_root(asset_upload.RUNTIME_PATH))),
        }

    def _build_or_upload(
        self,
        args: argparse.Namespace,
        *,
        upload: bool,
    ) -> dict[str, object]:
        if args.profile and args.addon_id is not None:
            raise ValueError("Use either --profile or --addon-id, not both")
        if args.local_folder is not None and args.profile:
            raise ValueError("--profile is available only with GitHub sync")
        if not args.profile and args.addon_id is None:
            raise ValueError("Either --profile or --addon-id is required")
        if args.addon_id is not None and args.addon_id < 0:
            raise ValueError("--addon-id must be zero or a positive integer")
        if args.local_folder is not None:
            mode = AssetSourceMode.LOCAL
            target = None
            session = None
        else:
            mode = AssetSourceMode.GITHUB
            target = self._target(args)
            session = self._session(target)
        options = self._options(args, target)
        result = (
            self.workflow.upload(mode, options, args.note, session)
            if upload
            else self.workflow.build_vpk(mode, options, args.note, session)
        )
        return {
            "ok": True,
            "command": "upload" if upload else "build",
            "message": result.message,
            "workshop_id": result.workshop_id,
            "output": str(result.pack_folder) if result.pack_folder else None,
            "source": mode.value,
            "repository": target.full_name if target is not None else None,
            "branch": target.branch if target is not None else None,
            "profile": args.profile,
        }

    def _retry_push(self) -> dict[str, object]:
        pending = self.pending_store.load()
        if pending is None:
            raise RuntimeError(
                tr(
                    "재시도할 GitHub push가 없습니다.",
                    "There is no GitHub push to retry.",
                )
            )
        session = self._session(pending.target)
        result = self.workflow.retry_pending_push(session)
        return {
            "ok": True,
            "command": "retry-push",
            "message": result.message,
            "workshop_id": result.workshop_id,
            "repository": pending.target.full_name,
            "branch": pending.target.branch,
        }

    def _logout(self) -> dict[str, object]:
        self._ensure_github_services()
        self.auth_manager.logout()
        return {
            "ok": True,
            "command": "logout",
            "message": tr(
                "저장된 GitHub 로그인을 삭제했습니다.",
                "Removed the saved GitHub login.",
            ),
        }

    def run(self, args: argparse.Namespace) -> dict[str, object]:
        if args.command == "sync":
            return self._sync(args)
        if args.command == "build":
            return self._build_or_upload(args, upload=False)
        if args.command == "upload":
            return self._build_or_upload(args, upload=True)
        if args.command == "retry-push":
            return self._retry_push()
        if args.command == "logout":
            return self._logout()
        raise RuntimeError(f"Unsupported command: {args.command}")


def _write_result(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return
    print(payload.get("message", "OK"))
    for key, value in payload.items():
        if key not in {"ok", "message"} and value is not None:
            print(f"{key}: {value}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    set_language(args.language)
    application = CliApplication(
        json_output=args.json,
        no_browser=args.no_browser,
    )
    try:
        payload = application.run(args)
    except KeyboardInterrupt:
        payload = {
            "ok": False,
            "error": tr("사용자가 작업을 취소했습니다.", "Operation cancelled."),
            "type": "KeyboardInterrupt",
        }
        _write_result(payload, json_output=args.json)
        return 130
    except Exception as error:
        payload = {
            "ok": False,
            "error": str(error),
            "type": type(error).__name__,
        }
        if args.json:
            _write_result(payload, json_output=True)
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    _write_result(payload, json_output=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
