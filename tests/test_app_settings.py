from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app_settings import (
    AssetSourceMode,
    can_change_mode,
    load_settings,
    save_settings,
    selected_mode,
)


class AppSettingsTests(unittest.TestCase):
    def test_legacy_settings_require_mode_choice_and_keep_folder(self) -> None:
        settings = {"asset_folder": r"D:\assets"}

        self.assertIsNone(selected_mode(settings))
        self.assertEqual(settings["asset_folder"], r"D:\assets")

    def test_only_supported_modes_are_selected(self) -> None:
        self.assertEqual(
            selected_mode({"asset_source_mode": "github"}),
            AssetSourceMode.GITHUB,
        )
        self.assertIsNone(selected_mode({"asset_source_mode": "automatic"}))

    def test_update_note_and_unknown_values_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "settings.json"
            save_settings(
                path,
                {
                    "asset_source_mode": "local",
                    "asset_folder": r"D:\assets",
                    "update_note": "do not save",
                    "unknown": "do not save",
                },
            )
            raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            raw,
            {"asset_source_mode": "local", "asset_folder": r"D:\assets"},
        )

    def test_non_string_and_invalid_json_values_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "settings.json"
            path.write_text(
                '{"workshop_id":"123","chunk_size_mb":100}',
                encoding="utf-8",
            )
            self.assertEqual(load_settings(path), {"workshop_id": "123"})
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(load_settings(path), {})

    def test_pending_blocks_mode_change(self) -> None:
        self.assertFalse(can_change_mode(True))
        self.assertTrue(can_change_mode(False))


if __name__ == "__main__":
    unittest.main()
