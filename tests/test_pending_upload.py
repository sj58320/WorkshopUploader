from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pending_upload import (
    PENDING_SCHEMA_VERSION,
    PendingUpload,
    PendingUploadError,
    PendingPhase,
    PendingUploadStore,
)


PENDING = PendingUpload(
    schema_version=PENDING_SCHEMA_VERSION,
    phase=PendingPhase.STEAM_SUCCEEDED,
    workshop_id=1234567890,
    note="add models\nfix materials",
    steam_succeeded_at="2026-07-18T12:00:00Z",
    base_commit="a" * 40,
    commit_id=None,
)

STARTED = PendingUpload(
    schema_version=PENDING_SCHEMA_VERSION,
    phase=PendingPhase.STEAM_STARTED,
    workshop_id=0,
    note="Update asset",
    steam_succeeded_at=None,
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

    def test_schema_2_record_migrates_to_default_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "pending_upload.json"
            payload = {
                "schema_version": 2,
                "phase": "steam_succeeded",
                "workshop_id": 1234567890,
                "note": "Update asset",
                "steam_succeeded_at": "2026-07-18T12:00:00Z",
                "base_commit": "a" * 40,
                "commit_id": None,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            pending = PendingUploadStore(path).load()

            self.assertEqual(pending.schema_version, PENDING_SCHEMA_VERSION)
            self.assertEqual(pending.target.full_name, "RevenantZE/RSS-ZE-ASSET")
            self.assertEqual(pending.target.branch, "main")
            self.assertEqual(pending.target.asset_subdir_text, "in/additional_files")

    def test_schema_3_missing_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "pending_upload.json"
            payload = {
                "schema_version": PENDING_SCHEMA_VERSION,
                "phase": "steam_succeeded",
                "workshop_id": 1234567890,
                "note": "Update asset",
                "steam_succeeded_at": "2026-07-18T12:00:00Z",
                "base_commit": "a" * 40,
                "commit_id": None,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(PendingUploadError):
                PendingUploadStore(path).load()

    def test_unknown_schema_is_reported_without_deleting_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "pending_upload.json"
            path.write_text('{"schema_version":99}', encoding="utf-8")
            store = PendingUploadStore(path)

            with self.assertRaises(PendingUploadError):
                store.load()

            self.assertTrue(path.exists())

    def test_steam_started_phase_is_valid_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = PendingUploadStore(Path(temporary_directory) / "pending.json")

            store.save(STARTED)

            self.assertEqual(store.load(), STARTED)

    def test_clear_and_missing_load_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = PendingUploadStore(Path(temporary_directory) / "pending_upload.json")

            self.assertIsNone(store.load())
            store.clear()
            store.clear()
            self.assertFalse(store.exists())


if __name__ == "__main__":
    unittest.main()
