from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import sleep, strftime, time

from scripts.cs2_vpk import finalize_cs2_workshop_vpk
from update_notes import normalize_update_note


SOURCE_PATH = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    APPLICATION_PATH = Path(sys.executable).resolve().parent
    BUNDLE_PATH = Path(getattr(sys, "_MEIPASS"))
else:
    APPLICATION_PATH = SOURCE_PATH
    BUNDLE_PATH = SOURCE_PATH

SELF_PATH = APPLICATION_PATH
SCRIPT_PATH = BUNDLE_PATH / "scripts"
INPUT_PATH = APPLICATION_PATH / "in"
OUTPUT_PATH = APPLICATION_PATH / "out"
RUNTIME_PATH = Path(
    os.environ.get("LOCALAPPDATA", tempfile.gettempdir())
) / "WorkshopUploader"

VPKEDIT_EXE = SCRIPT_PATH / "VPKEdit" / "vpkeditcli.exe"
CONTENT_FOLDER = OUTPUT_PATH / "__cache__" / "content"
RUNTIME_CONTENT_FOLDER = RUNTIME_PATH / "cache" / "content"
PACK_FOLDER = OUTPUT_PATH / "pack"
ADDITIONAL_FOLDER = INPUT_PATH / "additional_files"

DEFAULT_CHUNK_SIZE_MB = 100
INVALID_WORKSHOP_IDS = (0, -1)


@dataclass(frozen=True)
class UploadResult:
    pack_folder: Path
    workshop_id: int
    steam_submitted: bool



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pack CS2 assets as a multichunk VPK and upload them to Steam Workshop."
    )
    parser.add_argument(
        "workshop_id",
        type=int,
        help="Workshop item ID. Use 0 to create a new item.",
    )
    parser.add_argument(
        "--pack-only",
        action="store_true",
        help="Create the VPK files without connecting to Steam or uploading.",
    )
    parser.add_argument(
        "--chunk-size-mb",
        type=int,
        default=DEFAULT_CHUNK_SIZE_MB,
        help=f"Maximum archive chunk size in MiB (default: {DEFAULT_CHUNK_SIZE_MB}).",
    )
    parser.add_argument(
        "--title",
        help=(
            "Workshop title. Required for a new item; "
            "omit it to preserve an existing title."
        ),
    )
    parser.add_argument(
        "--description",
        help="Workshop description. Omit it to preserve an existing description.",
    )
    parser.add_argument(
        "--change-note",
        help="Steam Workshop update note. Blank input defaults to 'Update asset'.",
    )
    args = parser.parse_args()
    if args.workshop_id < 0:
        parser.error("workshop_id must be 0 or a positive integer")
    if args.chunk_size_mb < 1:
        parser.error("--chunk-size-mb must be at least 1")
    return args


def ensure_working_directories() -> None:
    ADDITIONAL_FOLDER.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    PACK_FOLDER.mkdir(parents=True, exist_ok=True)


def clean_folder(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def copy_additional_files(source: Path, destination: Path) -> int:
    if not source.is_dir():
        raise FileNotFoundError(f"Asset folder does not exist: {source}")

    source_files = [
        path for path in source.rglob("*") if path.is_file() and path.name != ".gitkeep"
    ]
    if not source_files:
        raise RuntimeError(f"No asset files found in: {source}")

    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".gitkeep", "__pycache__"),
    )
    return len(source_files)


def escape_vdf(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def create_publish_data(title: str, pack_folder: Path = PACK_FOLDER) -> None:
    values = {
        "title": title,
        "source_folder": "script",
        "publish_time": str(int(time())),
        "publish_time_readable": strftime("%m/%d/%Y %H:%M:%S %p"),
    }
    lines = ['"publish_data"', "{"]
    lines.extend(
        f'\t"{key}"\t\t"{escape_vdf(value)}"' for key, value in values.items()
    )
    lines.append("}")
    (pack_folder / "publish_data.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def pack_content(
    workshop_id: int,
    chunk_size_mb: int,
    additional_folder: Path = ADDITIONAL_FOLDER,
    pack_folder: Path = PACK_FOLDER,
    content_folder: Path = CONTENT_FOLDER,
) -> list[Path]:
    if not VPKEDIT_EXE.is_file():
        raise FileNotFoundError(f"VPKEdit CLI does not exist: {VPKEDIT_EXE}")

    additional_folder = Path(additional_folder).resolve()
    pack_folder = Path(pack_folder).resolve()
    content_folder = Path(content_folder).resolve()

    clean_folder(content_folder)
    clean_folder(pack_folder)
    copied_count = copy_additional_files(additional_folder, content_folder)

    directory_vpk = pack_folder / f"{workshop_id}_dir.vpk"
    command = [
        str(VPKEDIT_EXE),
        str(content_folder),
        "-o",
        str(directory_vpk),
        "-v",
        "2",
        "-c",
        str(chunk_size_mb),
        "--no-progress",
    ]

    print(f"Asset folder: {additional_folder}")
    print(f"Output folder: {pack_folder}")
    print(
        f"Packing {copied_count} files as VPK v2 "
        f"with {chunk_size_mb} MiB archive chunks..."
    )
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        command,
        cwd=APPLICATION_PATH,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
        check=False,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout
        )

    archive_prefix = f"{workshop_id}_"
    chunks = sorted(
        (
            path
            for path in pack_folder.glob(f"{archive_prefix}*.vpk")
            if path.stem.removeprefix(archive_prefix).isdigit()
        ),
        key=lambda path: int(path.stem.removeprefix(archive_prefix)),
    )
    if not directory_vpk.is_file() or not chunks:
        raise RuntimeError(
            "VPKEdit did not create the expected multichunk layout "
            f"({workshop_id}_dir.vpk + {workshop_id}_000.vpk ...)."
        )

    print("Generating and verifying CS2-compatible BLAKE3 archive hashes...")
    cs2_info = finalize_cs2_workshop_vpk(directory_vpk, chunks)
    print(
        "CS2 compatibility: generated and verified "
        f"{cs2_info.hash_count} BLAKE3 chunk hash(es) in 1 MiB blocks."
    )

    outputs = [directory_vpk, *chunks]
    total_size = sum(path.stat().st_size for path in outputs)
    print(
        f"Created {directory_vpk.name} and {len(chunks)} archive chunk(s) "
        f"({total_size / 1024 / 1024:.1f} MiB total)."
    )
    return outputs


def load_workshop_module():
    os.chdir(BUNDLE_PATH)
    # Importing this module initializes Steamworks, so keep pack-only mode independent.
    try:
        import scripts.SteamWorks_Workshop_warpper as workshop
    except Exception as error:
        from steamworks.exceptions import (
            SteamConnectionException,
            SteamException,
            SteamNotRunningException,
        )

        if isinstance(error, SteamNotRunningException):
            raise RuntimeError(
                "Steam 클라이언트가 실행되어 있지 않습니다. "
                "Steam을 실행하고 로그인한 뒤 다시 시도하세요."
            ) from error
        if isinstance(error, SteamConnectionException):
            raise RuntimeError(
                "Steam에 로그인되어 있지 않거나 Steam 클라이언트에 연결할 수 "
                "없습니다. 로그인 상태와 네트워크를 확인하세요."
            ) from error
        if isinstance(error, SteamException):
            raise RuntimeError(
                f"Steamworks 초기화에 실패했습니다.\n{error}"
            ) from error
        raise

    return workshop


def auto_update(
    workshop_id: int,
    chunk_size_mb: int,
    pack_only: bool,
    *,
    asset_folder: Path | str | None = None,
    output_folder: Path | str | None = None,
    isolated_output: bool = False,
    preview_path: Path | str | None = None,
    workshop_title: str | None = None,
    workshop_description: str | None = None,
    change_note: str | None = None,
) -> UploadResult:
    logging.info("Start upload asset")
    changelog = normalize_update_note(change_note)

    title = workshop_title.strip() if workshop_title else None
    description = workshop_description.strip() if workshop_description else None
    if title is not None and len(title) > 128:
        raise ValueError("Workshop 제목은 128자 이하여야 합니다.")
    if description is not None and len(description) > 8000:
        raise ValueError("Workshop 설명은 8,000자 이하여야 합니다.")
    if workshop_id == 0 and not pack_only and title is None:
        raise ValueError("새 창작마당 항목은 Workshop 제목을 입력해야 합니다.")

    workshop = None
    if workshop_id == 0 and not pack_only:
        workshop = load_workshop_module()
        workshop_id = workshop.workshop_create()
        if workshop_id in INVALID_WORKSHOP_IDS:
            raise RuntimeError(
                "Steam에서 새 창작마당 항목의 유효한 Addon ID를 받지 못했습니다."
            )

    source_folder = (
        Path(asset_folder).expanduser().resolve()
        if asset_folder is not None
        else ADDITIONAL_FOLDER
    )
    if output_folder is None:
        pack_folder = PACK_FOLDER
        content_folder = CONTENT_FOLDER
    else:
        output_root = Path(output_folder).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        pack_folder = (
            output_root / f"Workshop_{workshop_id}"
            if isolated_output
            else output_root
        )
        content_folder = RUNTIME_CONTENT_FOLDER

    print(f"Preparing Workshop item '{workshop_id}'...")
    pack_content(
        workshop_id,
        chunk_size_mb,
        source_folder,
        pack_folder,
        content_folder,
    )
    create_publish_data(title or f"Workshop {workshop_id}", pack_folder)

    if pack_only:
        print(f"Pack-only mode complete. Output: {pack_folder}")
        return UploadResult(pack_folder, workshop_id, False)

    if workshop is None:
        workshop = load_workshop_module()

    print(f"Uploading Workshop item '{workshop_id}' in 3 seconds...")
    sleep(3.0)

    if preview_path is not None:
        preview_candidate = Path(preview_path).expanduser().resolve()
    elif asset_folder is None:
        preview_candidate = INPUT_PATH / "rss_banner.jpg"
    else:
        preview_candidate = None

    preview = (
        str(preview_candidate)
        if preview_candidate is not None and preview_candidate.is_file()
        else None
    )
    if preview is None:
        print("Preview image not found; the existing Workshop preview will be kept.")

    confirmed_workshop_id = workshop.workshop_update(
        workshop_id,
        changelog,
        title,
        description,
        preview,
        str(pack_folder),
    )
    if confirmed_workshop_id != workshop_id:
        raise RuntimeError(
            "Steam이 확인한 Addon ID가 요청한 Addon ID와 다릅니다. "
            f"요청: {workshop_id}, 응답: {confirmed_workshop_id}"
        )

    logging.info("Complete update Asset")
    print(f"Steam 확인 완료: Workshop item '{workshop_id}' updated successfully.")
    return UploadResult(pack_folder, workshop_id, True)


if getattr(sys, "frozen", False):
    logging.basicConfig(handlers=[logging.NullHandler()], level=logging.INFO)
else:
    logging.basicConfig(
        filename=APPLICATION_PATH / "update.log",
        filemode="a",
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s: %(message)s",
        datefmt="%m/%d/%Y %I:%M:%S %p",
    )


if __name__ == "__main__":
    ensure_working_directories()
    arguments = parse_args()
    try:
        auto_update(
            arguments.workshop_id,
            arguments.chunk_size_mb,
            arguments.pack_only,
            workshop_title=arguments.title,
            workshop_description=arguments.description,
            change_note=arguments.change_note,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        logging.exception("Asset update failed")
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1) from error
