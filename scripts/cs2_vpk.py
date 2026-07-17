from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from blake3 import blake3


VPK_MAGIC = 0x55AA1234
VPK_VERSION = 2
VPK_HEADER_SIZE = 28
CS2_HASH_BLOCK_SIZE = 1024 * 1024
CS2_HASH_TYPE_BLAKE3 = 1
CS2_SIGNATURE_TYPE_ONLY_FILE_CHECKSUM = 1

HEADER1 = struct.Struct("<III")
HEADER2 = struct.Struct("<IIII")
# Source 2 splits the legacy uint32 archive index into uint16 index + uint16 hash type.
HASH_ENTRY = struct.Struct("<HHII16s")
# CS2 Workshop writes a 20-byte OnlyFileChecksum header with no RSA payload.
CS2_UNSIGNED_SIGNATURE = struct.Struct("<IIIII").pack(
    VPK_MAGIC,
    CS2_SIGNATURE_TYPE_ONLY_FILE_CHECKSUM,
    0,
    0,
    0,
)


@dataclass(frozen=True)
class CS2VPKInfo:
    archive_count: int
    hash_count: int
    archive_bytes: int


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data).digest()


def _directory_basename(directory_vpk: Path) -> str:
    suffix = "_dir.vpk"
    if not directory_vpk.name.lower().endswith(suffix):
        raise ValueError(f"Directory VPK name must end with '{suffix}': {directory_vpk}")
    return directory_vpk.name[: -len(suffix)]


def _ordered_archives(
    directory_vpk: Path, archive_paths: Iterable[Path | str]
) -> list[tuple[int, Path]]:
    basename = _directory_basename(directory_vpk)
    pattern = re.compile(rf"^{re.escape(basename)}_(\d+)\.vpk$", re.IGNORECASE)
    archives: list[tuple[int, Path]] = []
    seen_indexes: set[int] = set()

    for raw_path in archive_paths:
        path = Path(raw_path).resolve()
        match = pattern.fullmatch(path.name)
        if match is None:
            raise ValueError(f"Unexpected archive VPK name: {path.name}")
        archive_index = int(match.group(1))
        if archive_index > 0x7FFE:
            raise ValueError(f"Archive index is too large: {archive_index}")
        if archive_index in seen_indexes:
            raise ValueError(f"Duplicate archive index: {archive_index}")
        if not path.is_file():
            raise FileNotFoundError(f"Archive VPK does not exist: {path}")
        seen_indexes.add(archive_index)
        archives.append((archive_index, path))

    archives.sort(key=lambda item: item[0])
    if not archives:
        raise RuntimeError("No archive VPK chunks were provided.")
    expected_indexes = list(range(len(archives)))
    actual_indexes = [index for index, _ in archives]
    if actual_indexes != expected_indexes:
        raise RuntimeError(
            "Archive VPK indexes must be contiguous from 000: "
            f"{actual_indexes}"
        )
    return archives


def _read_vpk_prefix(directory_vpk: Path) -> tuple[bytes, bytes, int]:
    data = directory_vpk.read_bytes()
    if len(data) < VPK_HEADER_SIZE:
        raise RuntimeError(f"Directory VPK header is truncated: {directory_vpk}")

    magic, version, tree_size = HEADER1.unpack_from(data, 0)
    file_data_size, _, _, _ = HEADER2.unpack_from(data, HEADER1.size)
    if magic != VPK_MAGIC:
        raise RuntimeError(f"Invalid VPK magic: 0x{magic:08X}")
    if version != VPK_VERSION:
        raise RuntimeError(f"CS2 Workshop requires VPK v2, found v{version}.")
    if file_data_size != 0:
        raise RuntimeError(
            "CS2 multichunk output unexpectedly contains embedded file data."
        )

    prefix_end = VPK_HEADER_SIZE + tree_size + file_data_size
    if prefix_end > len(data):
        raise RuntimeError("Directory VPK tree or file-data section is truncated.")
    return (
        data[VPK_HEADER_SIZE:prefix_end],
        data[VPK_HEADER_SIZE : VPK_HEADER_SIZE + tree_size],
        tree_size,
    )


def _build_hash_section(
    archives: list[tuple[int, Path]],
) -> tuple[bytes, int]:
    entries = bytearray()
    archive_bytes = 0

    for archive_index, archive_path in archives:
        offset = 0
        with archive_path.open("rb") as stream:
            while block := stream.read(CS2_HASH_BLOCK_SIZE):
                entries.extend(
                    HASH_ENTRY.pack(
                        archive_index,
                        CS2_HASH_TYPE_BLAKE3,
                        offset,
                        len(block),
                        blake3(block).digest(length=16),
                    )
                )
                offset += len(block)
        if offset == 0:
            raise RuntimeError(f"Archive VPK is empty: {archive_path}")
        archive_bytes += offset

    return bytes(entries), archive_bytes


def _verify_data(
    data: bytes,
    archives: list[tuple[int, Path]],
    *,
    verify_archive_data: bool,
) -> CS2VPKInfo:
    if len(data) < VPK_HEADER_SIZE:
        raise RuntimeError("CS2 directory VPK header is truncated.")

    magic, version, tree_size = HEADER1.unpack_from(data, 0)
    file_data_size, hash_section_size, other_size, signature_size = HEADER2.unpack_from(
        data, HEADER1.size
    )
    if magic != VPK_MAGIC or version != VPK_VERSION:
        raise RuntimeError("CS2 directory VPK header is invalid.")
    if file_data_size != 0:
        raise RuntimeError("CS2 multichunk VPK contains embedded file data.")
    if hash_section_size % HASH_ENTRY.size != 0:
        raise RuntimeError("CS2 archive hash section has an invalid size.")
    if other_size != 48 or signature_size != len(CS2_UNSIGNED_SIGNATURE):
        raise RuntimeError("CS2 checksum or signature footer size is invalid.")

    tree_start = VPK_HEADER_SIZE
    tree_end = tree_start + tree_size
    hash_start = tree_end + file_data_size
    hash_end = hash_start + hash_section_size
    other_end = hash_end + other_size
    signature_end = other_end + signature_size
    if signature_end != len(data):
        raise RuntimeError("CS2 directory VPK section sizes do not match the file size.")

    tree_checksum = data[hash_end : hash_end + 16]
    hashes_checksum = data[hash_end + 16 : hash_end + 32]
    whole_checksum = data[hash_end + 32 : other_end]
    if _md5(data[tree_start:tree_end]) != tree_checksum:
        raise RuntimeError("CS2 VPK tree checksum verification failed.")
    if _md5(data[hash_start:hash_end]) != hashes_checksum:
        raise RuntimeError("CS2 VPK archive hash-section checksum verification failed.")
    if _md5(data[: hash_end + 32]) != whole_checksum:
        raise RuntimeError("CS2 VPK whole-file checksum verification failed.")
    if data[other_end:signature_end] != CS2_UNSIGNED_SIGNATURE:
        raise RuntimeError("CS2 VPK unsigned signature footer is invalid.")

    expected_entries: dict[tuple[int, int], tuple[int, bytes]] = {}
    archive_bytes = 0
    if verify_archive_data:
        for archive_index, archive_path in archives:
            offset = 0
            with archive_path.open("rb") as stream:
                while block := stream.read(CS2_HASH_BLOCK_SIZE):
                    expected_entries[(archive_index, offset)] = (
                        len(block),
                        blake3(block).digest(length=16),
                    )
                    offset += len(block)
            archive_bytes += offset
    else:
        archive_bytes = sum(path.stat().st_size for _, path in archives)

    found_entries: set[tuple[int, int]] = set()
    for entry_offset in range(hash_start, hash_end, HASH_ENTRY.size):
        archive_index, hash_type, offset, length, checksum = HASH_ENTRY.unpack_from(
            data, entry_offset
        )
        if hash_type != CS2_HASH_TYPE_BLAKE3:
            raise RuntimeError(
                f"CS2 VPK hash type must be BLAKE3 (1), found {hash_type}."
            )
        key = (archive_index, offset)
        if key in found_entries:
            raise RuntimeError(f"Duplicate CS2 archive hash entry: {key}")
        found_entries.add(key)
        if verify_archive_data:
            expected = expected_entries.get(key)
            if expected is None:
                raise RuntimeError(f"Unexpected CS2 archive hash entry: {key}")
            if expected != (length, checksum):
                raise RuntimeError(f"CS2 archive BLAKE3 verification failed: {key}")

    if verify_archive_data and found_entries != set(expected_entries):
        missing = sorted(set(expected_entries) - found_entries)[:5]
        raise RuntimeError(f"CS2 archive hash entries are missing: {missing}")

    return CS2VPKInfo(
        archive_count=len(archives),
        hash_count=hash_section_size // HASH_ENTRY.size,
        archive_bytes=archive_bytes,
    )


def finalize_cs2_workshop_vpk(
    directory_vpk: Path | str,
    archive_paths: Iterable[Path | str],
) -> CS2VPKInfo:
    directory_vpk = Path(directory_vpk).resolve()
    archives = _ordered_archives(directory_vpk, archive_paths)
    prefix, tree, tree_size = _read_vpk_prefix(directory_vpk)
    hash_section, archive_bytes = _build_hash_section(archives)

    header = HEADER1.pack(VPK_MAGIC, VPK_VERSION, tree_size) + HEADER2.pack(
        0,
        len(hash_section),
        48,
        len(CS2_UNSIGNED_SIGNATURE),
    )
    without_whole_checksum = (
        header + prefix + hash_section + _md5(tree) + _md5(hash_section)
    )
    output = (
        without_whole_checksum
        + _md5(without_whole_checksum)
        + CS2_UNSIGNED_SIGNATURE
    )
    info = _verify_data(output, archives, verify_archive_data=True)
    if info.archive_bytes != archive_bytes:
        raise RuntimeError("CS2 VPK archive byte count changed during verification.")

    temporary_path = directory_vpk.with_name(directory_vpk.name + ".tmp")
    try:
        temporary_path.write_bytes(output)
        temporary_path.replace(directory_vpk)
    finally:
        temporary_path.unlink(missing_ok=True)
    return info


def verify_cs2_workshop_vpk(
    directory_vpk: Path | str,
    archive_paths: Iterable[Path | str],
) -> CS2VPKInfo:
    directory_vpk = Path(directory_vpk).resolve()
    archives = _ordered_archives(directory_vpk, archive_paths)
    return _verify_data(
        directory_vpk.read_bytes(), archives, verify_archive_data=True
    )
