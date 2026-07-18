from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pending_upload import (
    PendingUpload,
    PendingUploadError,
    PendingUploadStore,
)


PENDING = PendingUpload(
    schema_version=1,
    workshop_id=1234567890,
    note="add models\nfix materials",
    steam_succeeded_at="2026-07-18T12:00:00Z",
    base_commit="a" * 40,
    commit_id=None,
)


class PendingUploadStoreTests(unittest.TestCase):
    def test_round_trip_preserves_multiline_note(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "pending_upload.json"
            store = PendingUploadStore(path)

            store.save(PENDING)

            self.assertEqual(store.load(), PENDING)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_unknown_schema_is_reported_without_deleting_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "pending_upload.json"
            path.write_text('{"schema_version":2}', encoding="utf-8")
            store = PendingUploadStore(path)

            with self.assertRaises(PendingUploadError):
                store.load()

            self.assertTrue(path.exists())

    def test_clear_and_missing_load_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = PendingUploadStore(Path(temporary_directory) / "pending_upload.json")

            self.assertIsNone(store.load())
            store.clear()
            store.clear()
            self.assertFalse(store.exists())


if __name__ == "__main__":
    unittest.main()
