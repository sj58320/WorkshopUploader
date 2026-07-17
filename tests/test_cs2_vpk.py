from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from blake3 import blake3

from scripts.cs2_vpk import (
    CS2_HASH_BLOCK_SIZE,
    CS2_UNSIGNED_SIGNATURE,
    HASH_ENTRY,
    HEADER1,
    HEADER2,
    VPK_MAGIC,
    finalize_cs2_workshop_vpk,
    verify_cs2_workshop_vpk,
)


class CS2VPKTests(unittest.TestCase):
    def test_finalize_generates_cs2_blake3_hash_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            directory_vpk = root / "123_dir.vpk"
            archive_0 = root / "123_000.vpk"
            archive_1 = root / "123_001.vpk"
            tree = b"\0"
            directory_vpk.write_bytes(
                HEADER1.pack(VPK_MAGIC, 2, len(tree))
                + HEADER2.pack(0, 0, 48, 0)
                + tree
                + (b"\0" * 48)
            )
            archive_0.write_bytes(b"A" * (CS2_HASH_BLOCK_SIZE + 17))
            archive_1.write_bytes(b"second archive")

            info = finalize_cs2_workshop_vpk(
                directory_vpk, [archive_1, archive_0]
            )

            self.assertEqual(info.archive_count, 2)
            self.assertEqual(info.hash_count, 3)
            self.assertEqual(
                info,
                verify_cs2_workshop_vpk(directory_vpk, [archive_0, archive_1]),
            )

            data = directory_vpk.read_bytes()
            _, _, tree_size = HEADER1.unpack_from(data)
            _, hash_size, other_size, signature_size = HEADER2.unpack_from(
                data, HEADER1.size
            )
            self.assertEqual(hash_size, HASH_ENTRY.size * 3)
            self.assertEqual(other_size, 48)
            self.assertEqual(signature_size, len(CS2_UNSIGNED_SIGNATURE))
            hash_start = 28 + tree_size
            first = HASH_ENTRY.unpack_from(data, hash_start)
            self.assertEqual(first[:4], (0, 1, 0, CS2_HASH_BLOCK_SIZE))
            self.assertEqual(
                first[4],
                blake3(b"A" * CS2_HASH_BLOCK_SIZE).digest(length=16),
            )
            self.assertEqual(data[-20:], CS2_UNSIGNED_SIGNATURE)
            whole_hash_offset = len(data) - 20 - 16
            self.assertEqual(
                data[whole_hash_offset : whole_hash_offset + 16],
                hashlib.md5(data[:whole_hash_offset]).digest(),
            )

    def test_verify_rejects_changed_archive_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            directory_vpk = root / "456_dir.vpk"
            archive = root / "456_000.vpk"
            tree = b"\0"
            directory_vpk.write_bytes(
                HEADER1.pack(VPK_MAGIC, 2, len(tree))
                + HEADER2.pack(0, 0, 48, 0)
                + tree
                + (b"\0" * 48)
            )
            archive.write_bytes(b"original archive bytes")
            finalize_cs2_workshop_vpk(directory_vpk, [archive])
            archive.write_bytes(b"modified archive bytes")

            with self.assertRaisesRegex(RuntimeError, "BLAKE3"):
                verify_cs2_workshop_vpk(directory_vpk, [archive])


if __name__ == "__main__":
    unittest.main()
